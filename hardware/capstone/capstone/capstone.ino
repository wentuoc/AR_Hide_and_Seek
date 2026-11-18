#include <Wire.h>
#include <FlexSensors.h>
#include <SensorDebug.h>
#include <MpuSensor.h>
#include <Motor.h>
#include <WifiStream.h>

#if __has_include("wifi_config.h")
#include "wifi_config.h"
#else
#include "wifi_config.example.h"
#endif

FlexSensors flex;
MpuSensor mpu;

const uint8_t SDA_PIN = 21;
const uint8_t SCL_PIN = 22;
const uint8_t MOTOR_PIN = 17;
Motor motor(MOTOR_PIN);
const unsigned long SAMPLE_PERIOD_MS = 50;  // Approximately 20 samples/second.
const uint8_t STATUS_LED_PIN = LED_BUILTIN;  // GPIO2 on FireBeetle ESP32.
const uint8_t STATUS_LED_ON = HIGH;  // Change to LOW for an active-low LED.
const uint8_t SENSOR_COUNT = FlexSensors::SENSOR_COUNT;
const bool CALIBRATION_DEBUG = false;  // Serial Monitor at 115200 baud.
// Uncomment to include raw flex counts in Wi-Fi and Serial output. Re-upload after changing.
#define INCLUDE_RAW_FLEX

const uint8_t PRINT_MODE = 2;  // 1: Serial Plotter, 2: CSV (header + data rows).
static_assert(PRINT_MODE == 1 || PRINT_MODE == 2, "PRINT_MODE must be 1 or 2");

const char* const FIELD_NAMES[] = {
  "ax_m_s2", "ay_m_s2", "az_m_s2", "gx_rad_s", "gy_rad_s", "gz_rad_s",
  "A0_normalized", "A1_normalized", "A2_normalized", "A3_normalized", "GPIO_normalized"
#ifdef INCLUDE_RAW_FLEX
  , "A0_raw", "A1_raw", "A2_raw", "A3_raw", "GPIO_raw"
#endif
};

void printFieldPrefix(uint8_t field) {
  if (field > 0) Serial.print(PRINT_MODE == 1 ? '\t' : ',');
  if (PRINT_MODE == 1) {
    Serial.print(FIELD_NAMES[field]);
    Serial.print(':');
  }
}

void updateStatusLed() {
  static bool ledOn = false;
  static bool transmitting = false;
  static unsigned long lastTransmission = 0;
  static unsigned long lastToggle = 0;
  const unsigned long now = millis();
  if (WifiStream::takeTransmissionActivity()) {
    lastTransmission = now;
    transmitting = true;
  }
  if (transmitting && now - lastTransmission >= 500) transmitting = false;

  // Slow heartbeat while sampling; fast blink while TCP writes succeed.
  const unsigned long interval = transmitting ? 100 : 500;
  if (now - lastToggle >= interval) {
    lastToggle = now;
    ledOn = !ledOn;
    digitalWrite(STATUS_LED_PIN, ledOn ? STATUS_LED_ON : !STATUS_LED_ON);
  }
}

// Call every sample. Passing enabled=false cancels pending/active vibration.
void updateGestureMotor(float middle, float ring, float gx, bool enabled) {
  enum State { ARMED, WAITING, VIBRATING, WAIT_OPEN };
  static State state = ARMED;
  static unsigned long stateStart = 0;
  static unsigned long conditionStart = 0;
  static bool timingCondition = false;
  const unsigned long now = millis();

  if (!enabled) {
    motor.off();
    state = ARMED;
    timingCondition = false;
    return;
  }

  motor.update();
  const float fingerAverage = (middle + ring) / 2.0f;
  switch (state) {
    case ARMED: {
      const bool gesture = fingerAverage < 0.50f && abs(gx) > 2.0f;
      if (!gesture) {
        timingCondition = false;
      } else if (!timingCondition) {
        conditionStart = now;
        timingCondition = true;
      } else if (now - conditionStart >= 100) {
        state = WAITING;
        stateStart = now;
        timingCondition = false;
      }
      break;
    }
    case WAITING:
      if (now - stateStart >= 1500) {
        motor.pulse();
        state = VIBRATING;
      }
      break;
    case VIBRATING:
      if (!motor.isPulsing()) {
        state = WAIT_OPEN;
        timingCondition = false;
      }
      break;
    case WAIT_OPEN:
      // Require an open hand after vibration before accepting another gesture.
      if (!(fingerAverage > 0.65f)) {
        timingCondition = false;
      } else if (!timingCondition) {
        conditionStart = now;
        timingCondition = true;
      } else if (now - conditionStart >= 300) {
        state = ARMED;
        timingCondition = false;
      }
      break;
  }
}

void setup() {
  motor.begin();  // Initialize GPIO17 LOW before sensors and Wi-Fi.
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, !STATUS_LED_ON);
  Serial.begin(115200);
  delay(1000);
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);
  Wire.setTimeOut(5);  // Bound I2C waits so ADS failures cannot stall streaming.

  if (!flex.begin(Wire)) {
    Serial.println("# ADS1015 unavailable at 0x48; continuing with -1 for ADS flex values.");
  }

  // Use the vendored Adafruit library accepting WHO_AM_I = 0x70.
  // The device's I2C address remains 0x68.
  if (!mpu.begin(Wire)) {
    Serial.println("MPU initialization failed at 0x68. Check wiring and patched library.");
    while (true) delay(1000);   
  }
  WifiStream::begin(AP_SSID, AP_PASSWORD, STREAM_PORT);
  Serial.println("# Connect to Wi-Fi and open the plotter to start calibration.");
  if (PRINT_MODE == 2) {
    for (uint8_t field = 0; field < sizeof(FIELD_NAMES) / sizeof(FIELD_NAMES[0]); ++field) {
      if (field > 0) Serial.print(',');
      Serial.print(FIELD_NAMES[field]);
    }
    Serial.println();
  }
}

void loop() {
  const WifiStream::Command command = WifiStream::takeCommand();
  if (command == WifiStream::CALIBRATE && (WifiStream::getStage() == WifiStream::READY ||
                       WifiStream::getStage() == WifiStream::NEEDS_CALIBRATION)) {
    WifiStream::setStage(WifiStream::FLEX_COUNTDOWN);
    // updateGestureMotor(0, 0, 0, false);
    WifiStream::clearPendingRecords();
    delay(3000);
    WifiStream::setStage(WifiStream::FLEX);
    flex.calibrate(Serial, 5000, CALIBRATION_DEBUG);
    WifiStream::setStage(WifiStream::WAIT_GYRO);
  } else if (command == WifiStream::CALIBRATE_GYRO && WifiStream::getStage() == WifiStream::WAIT_GYRO) {
    WifiStream::setStage(WifiStream::GYRO);
    delay(1000);  // Settle after clicking before measuring offsets.
    mpu.calibrateGyro();
    WifiStream::setStage(WifiStream::READY);
  }
  if (WifiStream::getStage() != WifiStream::READY) {
    // updateGestureMotor(0, 0, 0, false);
    digitalWrite(STATUS_LED_PIN, !STATUS_LED_ON);
    delay(20);
    return;
  }
  const unsigned long sampleStart = millis();
  const MpuReading motion = mpu.read(true);  // Offset correction, low-pass filter, gyro deadband.
  float flexNormalized[SENSOR_COUNT];
  int flexRaw[SENSOR_COUNT];
  flex.readNormalized(flexNormalized, flexRaw);
  SensorDebug::rawValue("after normalization", 1, flexRaw[1]);
  // updateGestureMotor(flexNormalized[1], flexNormalized[2], motion.gx, true);

  WifiStream::Record record = {};
  const int length = snprintf(
      record.line, sizeof(record.line),
      "%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f\n",
      motion.ax, motion.ay, motion.az, motion.gx, motion.gy, motion.gz,
      flexNormalized[0], flexNormalized[1], flexNormalized[2],
      flexNormalized[3], flexNormalized[4]);
#ifdef INCLUDE_RAW_FLEX
  // Replace the final newline with the optional raw columns.
  if (length > 0 && static_cast<size_t>(length) < sizeof(record.line)) {
    SensorDebug::rawValue("before raw snprintf", 1, flexRaw[1]);
    const int extra = snprintf(record.line + length - 1, sizeof(record.line) - length + 1,
        ",%d,%d,%d,%d,%d\n", flexRaw[0], flexRaw[1], flexRaw[2], flexRaw[3], flexRaw[4]);
    if (extra < 0 || static_cast<size_t>(extra) >= sizeof(record.line) - length + 1) return;
    if (SensorDebug::ENABLED) {
      Serial.print("# RAW_DEBUG,formatted record,");
      Serial.print(record.line);
    }
  }
#endif
  if (length > 0 && static_cast<size_t>(length) < sizeof(record.line)) {
    WifiStream::send(record);
  }

  // Six processed MPU readings followed by five normalized flex values.
  // MPU getEvent() returns m/s^2 and rad/s, not raw register counts.
  
  printFieldPrefix(0); Serial.print(motion.ax, 3);
  printFieldPrefix(1); Serial.print(motion.ay, 3);
  printFieldPrefix(2); Serial.print(motion.az, 3);
  printFieldPrefix(3); Serial.print(motion.gx, 3);
  printFieldPrefix(4); Serial.print(motion.gy, 3);
  printFieldPrefix(5); Serial.print(motion.gz, 3);

  for (uint8_t finger = 0; finger < FlexSensors::SENSOR_COUNT; ++finger) {
    printFieldPrefix(6 + finger);
    Serial.print(flexNormalized[finger], 3);
  }
#ifdef INCLUDE_RAW_FLEX
  for (uint8_t finger = 0; finger < FlexSensors::SENSOR_COUNT; ++finger) {
    printFieldPrefix(11 + finger);
    Serial.print(flexRaw[finger]);
  }
#endif
  Serial.println();

  updateStatusLed();
  const unsigned long elapsed = millis() - sampleStart;
  if (elapsed < SAMPLE_PERIOD_MS) delay(SAMPLE_PERIOD_MS - elapsed);
}
