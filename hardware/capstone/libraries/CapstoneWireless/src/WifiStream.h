#pragma once

#include <Arduino.h>

// Singleton ESP32 access point and TCP stream, serviced by a background task.
namespace WifiStream {
struct Record { char line[320]; };
enum Stage { NEEDS_CALIBRATION, FLEX_COUNTDOWN, FLEX, WAIT_GYRO, GYRO, READY };
enum Command { NONE, CALIBRATE, CALIBRATE_GYRO };

// Copies configuration. Returns false on invalid config or task allocation failure.
// AP startup retries in the background. Call from setup, not concurrently.
bool begin(const char* ssid, const char* password, uint16_t port = 5000);
// Queue a null-terminated CSV line; full queues drop new records.
void send(const Record& record);
bool takeTransmissionActivity();
Stage getStage();
void setStage(Stage stage);
Command takeCommand();
void clearPendingRecords();
}  // namespace WifiStream
