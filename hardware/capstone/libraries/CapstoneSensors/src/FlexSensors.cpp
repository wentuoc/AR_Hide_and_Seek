#include "FlexSensors.h"
#include "SensorDebug.h"

#include <memory>
#include <new>

namespace {
int readAveragedAdc(uint8_t pin) {
  uint32_t total = 0;
  for (uint8_t sample = 0; sample < 32; ++sample) {
    total += analogRead(pin);
    if (sample < 31) delayMicroseconds(100);
  }
  return (total + 16) / 32;
}
}

bool FlexSensors::begin(TwoWire& wire, uint8_t address, uint8_t gpioPin) {
  wire_ = &wire;
  address_ = address;
  uint16_t config;
  adsAvailable_ = readAdsRegister(ADS1X15_REG_POINTER_CONFIG, config);
  if (!adsAvailable_) lastAdsFailure_ = millis();
  gpioPin_ = gpioPin;
  // The onboard ADC input defaults to ESP32 GPIO34 (currently thumb).
  pinMode(gpioPin_, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(gpioPin_, ADC_11db);
  for (uint8_t finger = 0; finger < SENSOR_COUNT; ++finger) {
    calibrationMin_[finger] = calibrationMax_[finger] = 0;
  }
  for (uint8_t channel = 0; channel < SENSOR_COUNT; ++channel) {
    filterInitialized_[channel] = false;
    next_[channel] = 0;
  }
  return adsAvailable_;
}

bool FlexSensors::readAdsRegister(uint8_t reg, uint16_t& value) {
  wire_->beginTransmission(address_);
  wire_->write(reg);
  if (wire_->endTransmission(false) != 0) return false;
  if (wire_->requestFrom(address_, uint8_t(2)) != 2) return false;
  value = uint16_t(wire_->read()) << 8;
  value |= uint8_t(wire_->read());
  return true;
}

bool FlexSensors::readAdsChannel(uint8_t channel, int& value) {
  // Single shot, +/-4.096 V (2 mV/count), 1600 SPS, comparator disabled.
  const uint16_t config = ADS1X15_REG_CONFIG_OS_SINGLE
      | (ADS1X15_REG_CONFIG_MUX_SINGLE_0 + (channel << 12))
      | GAIN_ONE | ADS1X15_REG_CONFIG_MODE_SINGLE
      | RATE_ADS1015_1600SPS | ADS1X15_REG_CONFIG_CQUE_NONE;
  wire_->beginTransmission(address_);
  wire_->write(ADS1X15_REG_POINTER_CONFIG);
  wire_->write(uint8_t(config >> 8));
  wire_->write(uint8_t(config));
  if (wire_->endTransmission() != 0) return false;
  const unsigned long start = millis();
  do {
    uint16_t status;
    if (!readAdsRegister(ADS1X15_REG_POINTER_CONFIG, status)) return false;
    if (status & ADS1X15_REG_CONFIG_OS_SINGLE) {
      uint16_t result;
      if (!readAdsRegister(ADS1X15_REG_POINTER_CONVERT, result)) return false;
      value = int16_t(result) >> 4;
      return true;
    }
    delay(1);
  } while (millis() - start < 10);
  return false;
}

void FlexSensors::read(int (&raw)[SENSOR_COUNT]) {
  // Back off after a failure so an absent ADS does not delay every MPU sample.
  for (uint8_t channel = 0; channel < 4; ++channel) raw[channel] = -1;
  if (wire_ && (adsAvailable_ || millis() - lastAdsFailure_ >= 1000)) {
    adsAvailable_ = true;
    for (uint8_t channel = 0; channel < 4; ++channel) {
      if (!readAdsChannel(channel, raw[channel])) {
        adsAvailable_ = false;
        lastAdsFailure_ = millis();
        // Reject the entire ADS sample, including channels read before failure.
        for (uint8_t i = 0; i < 4; ++i) raw[i] = -1;
        Serial.println("# ADS1015 unavailable; sending -1 and retrying in 1 second.");
        break;
      }
      SensorDebug::rawValue("acquired", channel, raw[channel]);
    }
  }
  SensorDebug::trace("GPIO read begin");
  raw[4] = readAveragedAdc(gpioPin_);
  SensorDebug::trace("GPIO read end");
}

// Optional below-range dropout handling. Set false to restore normal clamping;
// this does not affect calibration or the existing zero/negative dropout guard.
bool FlexSensors::isBelowRangeDropout(uint8_t channel, int sample) const {
  constexpr bool HOLD_BELOW_CALIBRATION_MIN = false;
  return HOLD_BELOW_CALIBRATION_MIN
      && calibrationMax_[channel] > calibrationMin_[channel]
      && sample < calibrationMin_[channel];
}

float FlexSensors::filterChannel(uint8_t channel, int sample) {
  // Zero/negative counts are dropouts, not a new finger position.
  if (sample <= 0) return filterInitialized_[channel] ? smoothed_[channel] : 0.0f;
  // Skip rejected samples entirely so filter history and output stay unchanged.
  if (isBelowRangeDropout(channel, sample)) {
    return filterInitialized_[channel] ? smoothed_[channel] : 0.0f;
  }
  int* history = history_[channel];
  if (!filterInitialized_[channel]) {
    for (uint8_t i = 0; i < 3; ++i) history[i] = sample;
    smoothed_[channel] = sample;
    filterInitialized_[channel] = true;
  }
  history[next_[channel]] = sample;
  next_[channel] = (next_[channel] + 1) % 3;
  const int median = history[0] + history[1] + history[2]
      - min(history[0], min(history[1], history[2]))
      - max(history[0], max(history[1], history[2]));
  smoothed_[channel] += 0.3f * (median - smoothed_[channel]);
  return smoothed_[channel];
}

void FlexSensors::calibrate(Stream& log, unsigned long durationMs, bool debug) {
  const char* const names[] = {"A0_normalized", "A1_normalized", "A2_normalized", "A3_normalized", "GPIO_normalized"};
  log.print("# Calibration: flex and straighten all fingers through their full range for ");
  log.print(durationMs / 1000.0f, 1);
  log.println(" seconds. Repeat full bends, briefly holding both endpoints.");
  // Exact ADC-count bins preserve small ranges. Four 2048-count ADS
  // histograms plus one 4096-count GPIO histogram use 24 KiB temporarily.
  // Keep this allocation off the ESP32 task stack.
  std::unique_ptr<uint16_t[]> histogram(new (std::nothrow) uint16_t[12288]());
  if (!histogram) {
    log.println("# Calibration failed: insufficient memory; keeping previous ranges.");
    return;
  }
  const unsigned long start = millis();
  unsigned long lastDebug = start;
  if (debug) log.println("# CAL_DEBUG: elapsed_ms; A0,A1,A2,A3,GPIO raw / valid / rejected / observed_max");
  uint32_t valid[SENSOR_COUNT] = {};
  int maximum[SENSOR_COUNT] = {};
  uint32_t rejected[SENSOR_COUNT] = {};
  int raw[SENSOR_COUNT];
  while (millis() - start < durationMs) {
    read(raw);
    for (uint8_t finger = 0; finger < SENSOR_COUNT; ++finger) {
      const int bins = finger < 4 ? 2048 : 4096;
      if (raw[finger] <= 0 || raw[finger] >= bins) {
        ++rejected[finger];
        continue;
      }
      maximum[finger] = max(maximum[finger], raw[finger]);
      uint16_t& count = histogram[finger * 2048 + raw[finger]];
      // Keep counts consistent even for unusually long calibration requests.
      if (count < 65535) {
        ++count;
        ++valid[finger];
      }
    }
    if (debug && millis() - lastDebug >= 250) {
      lastDebug = millis();
      log.print("# CAL_DEBUG,");
      log.print(lastDebug - start);
      for (uint8_t group = 0; group < 4; ++group) {
        log.print(" / ");
        for (uint8_t finger = 0; finger < SENSOR_COUNT; ++finger) {
          if (finger) log.print(',');
          if (group == 0) log.print(raw[finger]);
          else if (group == 1) log.print(valid[finger]);
          else if (group == 2) log.print(rejected[finger]);
          else log.print(maximum[finger]);
        }
      }
      log.println();
    }
    delay(10);
  }

  for (uint8_t finger = 0; finger < SENSOR_COUNT; ++finger) {
    const int bins = finger < 4 ? 2048 : 4096;
    const uint32_t lowRank = (valid[finger] + 19) / 20;
    uint32_t cumulative = 0;
    int low = -1;
    for (int bin = 0; bin < bins; ++bin) {
      cumulative += histogram[finger * 2048 + bin];
      if (cumulative >= lowRank) {
        low = bin;
        break;
      }
    }
    // Accept small movements while rejecting nearly constant signals.
    // Minimum spans are ADC counts; narrow ranges amplify sensor noise.
    const int minimumSpan = finger < 4 ? 4 : 8;
    if (valid[finger] >= 20 && maximum[finger] - low >= minimumSpan) {
      calibrationMin_[finger] = low;
      calibrationMax_[finger] = maximum[finger];
    } else {
      log.print("# Insufficient valid samples or movement on channel ");
      log.print(finger);
      log.print("; observed 5th percentile/max: ");
      log.print(valid[finger] ? low : 0);
      log.print(',');
      log.print(maximum[finger]);
      log.println("; keeping previous range (0 if uncalibrated).");
    }
  }

  for (uint8_t channel = 0; channel < SENSOR_COUNT; ++channel) {
    filterInitialized_[channel] = false;
    next_[channel] = 0;
  }
  log.println("# Calibration complete. 5th percentile / maximum bounds per finger:");
  for (uint8_t finger = 0; finger < SENSOR_COUNT; ++finger) {
    log.print("# ");
    log.print(names[finger]);
    log.print(": ");
    log.print(calibrationMin_[finger]);
    log.print(',');
    log.print(calibrationMax_[finger]);
    log.print("; valid=");
    log.print(valid[finger]);
    log.print("; rejected=");
    log.print(rejected[finger]);
    if (calibrationMin_[finger] == calibrationMax_[finger]) {
      log.print(" (no range detected; output will be 0; reset to recalibrate)");
    }
    log.println();
  }
}

void FlexSensors::readNormalized(float (&normalized)[SENSOR_COUNT]) {
  int raw[SENSOR_COUNT];
  readNormalized(normalized, raw);
}

void FlexSensors::readNormalized(float (&normalized)[SENSOR_COUNT], int (&raw)[SENSOR_COUNT]) {
  read(raw);
  for (uint8_t finger = 0; finger < SENSOR_COUNT; ++finger) {
    if (finger < 4 && raw[finger] == -1) {
      normalized[finger] = -1.0f;
      continue;
    }
    const int range = calibrationMax_[finger] - calibrationMin_[finger];
    const float reading = filterChannel(finger, raw[finger]);
    const float clamped = constrain(reading, float(calibrationMin_[finger]), float(calibrationMax_[finger]));
    normalized[finger] = range > 0 ? (clamped - calibrationMin_[finger]) / range : 0.0f;
  }
}
