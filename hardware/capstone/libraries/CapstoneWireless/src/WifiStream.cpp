#include "WifiStream.h"
#include <atomic>
#include <WiFi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

namespace WifiStream {
static std::atomic<Stage> stage{NEEDS_CALIBRATION};
static std::atomic<Command> command{NONE};
static char apSsid[33];
static char apPassword[64];
static uint16_t streamPort;
static const char* stageName(Stage value) {
  switch (value) {
    case FLEX_COUNTDOWN: return "flex_countdown";
    case FLEX: return "flex";
    case WAIT_GYRO: return "wait_gyro";
    case GYRO: return "gyro";
    case READY: return "ready";
    default: return "needs_calibration";
  }
}
static QueueHandle_t queue = nullptr;
static std::atomic<bool> transmissionActivity{false};

// Consumed by the sensor loop; set only after a complete TCP write.
bool takeTransmissionActivity() {
  return transmissionActivity.exchange(false);
}

// Only this task touches networking, so connection retries and slow writes
// cannot stall sensor acquisition. A full queue drops new records.
static void run(void*) {
  const IPAddress apAddress(192, 168, 4, 1);
  WiFi.mode(WIFI_AP);
  while (!WiFi.softAPConfig(apAddress, apAddress, IPAddress(255, 255, 255, 0)) ||
         !WiFi.softAP(apSsid, apPassword)) {
    Serial.println("# Could not start Wi-Fi access point; retrying...");
    vTaskDelay(pdMS_TO_TICKS(2000));
  }
  NetworkServer server(streamPort, 1);
  server.begin();
  while (!server) {
    Serial.println("# Could not start TCP server; retrying...");
    vTaskDelay(pdMS_TO_TICKS(2000));
    server.begin();
  }
  Serial.print("# Wi-Fi access point ready: ");
  Serial.println(apSsid);
  Serial.print("# TCP stream: ");
  Serial.print(WiFi.softAPIP());
  Serial.print(':');
  Serial.println(streamPort);
  NetworkClient client;
  Record record;
  String incoming;
  unsigned long lastStatus = 0;
  Stage lastStage = NEEDS_CALIBRATION;
  for (;;) {
    if (!client.connected()) {
      client.stop();
      xQueueReset(queue);
      client = server.accept();
      if (!client) {
        vTaskDelay(pdMS_TO_TICKS(100));
        continue;
      }
      client.setNoDelay(true);
      incoming = "";
      lastStatus = millis() - 1000;

    }
    while (client.available()) {
      char c = client.read();
      if (c == '\n') {
        Stage current = stage.load();
        if (incoming == "CALIBRATE" && (current == READY || current == NEEDS_CALIBRATION)) command.store(CALIBRATE);
        if (incoming == "GYRO" && current == WAIT_GYRO) command.store(CALIBRATE_GYRO);
        incoming = "";
      } else if (incoming.length() < 32) incoming += c;
      else { client.stop(); break; }
    }
    Stage current = stage.load();
    if (current != lastStage || millis() - lastStatus >= 1000) {
      char status[80];
      snprintf(status, sizeof(status), "#STATUS,%s\n", stageName(current));
      if (client.print(status) != strlen(status)) client.stop();
      lastStage = current;
      lastStatus = millis();
    }
    if (xQueueReceive(queue, &record, pdMS_TO_TICKS(100)) == pdTRUE) {
      const size_t length = strlen(record.line);
      if (client.write(reinterpret_cast<const uint8_t*>(record.line), length) != length) {
        // A partial record is discarded by the receiver on disconnect.
        client.stop();
      } else {
        transmissionActivity.store(true);
      }
    }
  }
}

bool begin(const char* ssid, const char* password, uint16_t port) {
  // One access point/task per board. Repeated calls do not restart Wi-Fi.
  if (queue) return true;
  if (!ssid || !password || !ssid[0] || strlen(ssid) > 32 ||
      strlen(password) < 8 || strlen(password) > 63 || port == 0) {
    Serial.println("# Invalid AP config: SSID must be 1-32 bytes, password 8-63 bytes, port nonzero.");
    return false;
  }
  strcpy(apSsid, ssid);
  strcpy(apPassword, password);
  streamPort = port;
  queue = xQueueCreate(8, sizeof(Record));
  if (!queue) {
    Serial.println("# Wi-Fi queue allocation failed.");
    return false;
  }
  if (xTaskCreate(run, "wifi-stream", 4096, nullptr, 1, nullptr) != pdPASS) {
    vQueueDelete(queue);
    queue = nullptr;
    Serial.println("# Wi-Fi task creation failed.");
    return false;
  } else {
    Serial.println("# Wi-Fi streaming started; starting access point for laptop receiver.");
  }
  return true;
}

void send(const Record& record) {
  if (queue) xQueueSend(queue, &record, 0);
}
Stage getStage() { return stage.load(); }
void setStage(Stage value) { stage.store(value); }
Command takeCommand() { return command.exchange(NONE); }
void clearPendingRecords() { if (queue) xQueueReset(queue); }
}  // namespace WifiStream
