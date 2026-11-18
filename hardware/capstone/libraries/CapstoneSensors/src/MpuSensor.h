#pragma once

#include <Arduino.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Wire.h>

struct MpuReading {
  float ax, ay, az;  // m/s^2
  float gx, gy, gz;  // rad/s
};

class MpuSensor {
 public:
  bool begin(TwoWire& wire, uint8_t address = 0x68);
  // Keep the sensor still. Call after initialization and settling.
  void calibrateGyro();
  // Processing subtracts gyro offsets, smooths all axes, then applies gyro deadband.
  MpuReading read(bool processed = true);

 private:
  struct LowPassFilter {
    float value = 0;
    bool initialized = false;

    float update(float sample) {
      // Alpha 0.2 is tuned for approximately 20 Hz sampling.
      if (!initialized) {
        value = sample;
        initialized = true;
      } else {
        value += 0.2f * (sample - value);
      }
      return value;
    }
  };

  LowPassFilter filters_[6];
  void resetFilters();
  Adafruit_MPU6050 mpu_;
  float offsets_[3] = {0, 0, 0};
  static float deadband(float value);
};
