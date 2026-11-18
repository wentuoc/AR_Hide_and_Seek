#include "MpuSensor.h"

bool MpuSensor::begin(TwoWire& wire, uint8_t address) {
  // This project's device uses the vendored Adafruit library accepting
  // WHO_AM_I = 0x70. Its I2C address remains 0x68.
  if (!mpu_.begin(address, &wire)) return false;
  mpu_.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu_.setGyroRange(MPU6050_RANGE_1000_DEG);
  mpu_.setFilterBandwidth(MPU6050_BAND_10_HZ);
  offsets_[0] = offsets_[1] = offsets_[2] = 0;
  resetFilters();
  return true;
}

void MpuSensor::resetFilters() {
  for (auto& filter : filters_) filter = LowPassFilter{};
}

void MpuSensor::calibrateGyro() {
  const int samples = 500;
  offsets_[0] = offsets_[1] = offsets_[2] = 0;
  for (int i = 0; i < samples; ++i) {
    sensors_event_t a, g, temp;
    mpu_.getEvent(&a, &g, &temp);
    offsets_[0] += g.gyro.x;
    offsets_[1] += g.gyro.y;
    offsets_[2] += g.gyro.z;
    delay(5);
  }
  for (uint8_t axis = 0; axis < 3; ++axis) offsets_[axis] /= samples;
  resetFilters();
}

float MpuSensor::deadband(float value) {
  return abs(value) < 0.02f ? 0 : value;
}

MpuReading MpuSensor::read(bool processed) {
  sensors_event_t a, g, temp;
  mpu_.getEvent(&a, &g, &temp);
  MpuReading reading = {a.acceleration.x, a.acceleration.y, a.acceleration.z,
                        g.gyro.x, g.gyro.y, g.gyro.z};
  if (processed) {
    reading.ax = filters_[0].update(reading.ax);
    reading.ay = filters_[1].update(reading.ay);
    reading.az = filters_[2].update(reading.az);
    // Remove offsets before smoothing; deadband must not modify filter state.
    reading.gx = deadband(filters_[3].update(reading.gx - offsets_[0]));
    reading.gy = deadband(filters_[4].update(reading.gy - offsets_[1]));
    reading.gz = deadband(filters_[5].update(reading.gz - offsets_[2]));
  }
  return reading;
}
