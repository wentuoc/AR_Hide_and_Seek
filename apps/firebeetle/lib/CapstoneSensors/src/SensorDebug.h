#pragma once

#include <Arduino.h>

// Temporary Serial diagnostics. Set false after isolating the stalled call.
// Verbose output can reduce the sampling rate.
namespace SensorDebug {
constexpr bool ENABLED = true;
inline void rawValue(const char* point, int channel, int value) {
  if (!ENABLED) return;
  Serial.print("# RAW_DEBUG,");
  Serial.print(millis());
  Serial.print(',');
  Serial.print(point);
  Serial.print(",A");
  Serial.print(channel);
  Serial.print('=');
  Serial.println(value);
}
inline void trace(const char* operation, int channel = -1) {
  if (!ENABLED) return;
  Serial.print("# I2C_DEBUG,");
  Serial.print(millis());
  Serial.print(',');
  Serial.print(operation);
  if (channel >= 0) {
    Serial.print(",A");
    Serial.print(channel);
  }
  Serial.println();
}
}
