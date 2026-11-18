#pragma once

#include <Arduino.h>
#include <Adafruit_ADS1X15.h>
#include <Wire.h>

class FlexSensors {
 public:
  static constexpr uint8_t SENSOR_COUNT = 5;
  // False means ADS unavailable; GPIO remains usable and reads retry the ADS.
  bool begin(TwoWire& wire, uint8_t address = 0x48, uint8_t gpioPin = 34);
  // Array order: ADS1015 A0-A3, then ESP32 GPIO ADC (currently thumb).
  // Unavailable ADS channels return -1 in raw and normalized output.
  void read(int (&raw)[SENSOR_COUNT]);
  // Move every finger repeatedly through its full range; bounds are held in RAM.
  // Uses the exact nonzero 5th percentile and observed maximum. Requires 20 valid
  // samples and a span of 4 ADS / 8 GPIO counts; inadequate data preserves prior bounds.
  // Debug emits raw snapshots and cumulative valid/rejected counts every 250 ms.
  void calibrate(Stream& log, unsigned long durationMs = 5000, bool debug = false);
  // Call calibrate first. Uncalibrated or zero-range channels output 0.
  // ADS communication failures output -1; other zero/negative dropouts hold
  // the last filtered value (0 before a valid sample).
  // Optional below-calibration-min handling also holds the last filtered value.
  // Each channel is median/EMA filtered at the caller's approximately 20 Hz cadence.
  // Clamped calibration range: 0 = minimum, 1 = maximum, not bend angle.
  void readNormalized(float (&normalized)[SENSOR_COUNT]);
  // Acquire once and return both representations of the same sample.
  // Raw GPIO is the existing 32-read average, before median/EMA filtering.
  void readNormalized(float (&normalized)[SENSOR_COUNT], int (&raw)[SENSOR_COUNT]);

 private:
  int calibrationMin_[SENSOR_COUNT] = {};
  int calibrationMax_[SENSOR_COUNT] = {};
  bool filterInitialized_[SENSOR_COUNT] = {};
  int history_[SENSOR_COUNT][3] = {};
  uint8_t next_[SENSOR_COUNT] = {};
  float smoothed_[SENSOR_COUNT] = {};
  bool isBelowRangeDropout(uint8_t channel, int sample) const;
  float filterChannel(uint8_t channel, int sample);
  bool readAdsRegister(uint8_t reg, uint16_t& value);
  bool readAdsChannel(uint8_t channel, int& value);
  TwoWire* wire_ = nullptr;
  uint8_t address_ = 0x48;
  bool adsAvailable_ = false;
  unsigned long lastAdsFailure_ = 0;
  uint8_t gpioPin_ = 34;
};
