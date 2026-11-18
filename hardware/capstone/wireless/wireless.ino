#include <WifiStream.h>

#if __has_include("wifi_config.h")
#include "wifi_config.h"
#else
#include "wifi_config.example.h"
#endif

// Wireless-only smoke test: no MPU or flex hardware needed.
// Emits synthetic, clearly labeled test data in the receiver's CSV format.
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("# Wireless test: all transmitted sensor values are synthetic zeros.");
  WifiStream::setStage(WifiStream::READY);
  if (!WifiStream::begin(AP_SSID, AP_PASSWORD, STREAM_PORT)) {
    Serial.println("# Wireless initialization failed.");
    while (true) delay(1000);
  }
}

void loop() {
  // This sketch has no sensors to calibrate; acknowledge requests on Serial.
  if (WifiStream::takeCommand() != WifiStream::NONE) {
    Serial.println("# Wireless-only test: calibration is not applicable.");
  }
  static uint32_t sequence = 0;
  WifiStream::Record record = {};
  snprintf(record.line, sizeof(record.line),
           "%lu,%lu,0,0,0,0,0,0,0,0,0,0,0\n",
           static_cast<unsigned long>(sequence++), millis());
  WifiStream::send(record);
  delay(50);
}
