#pragma once
#include <Wire.h>
#include <atomic>
#include <FlexSensors.h>
#include <MpuSensor.h>
#include <Motor.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>

// Requires the teammate's original libraries, including patched MPU dependency.
FlexSensors flex;
MpuSensor mpu;
Motor motor(17);
static_assert(FlexSensors::SENSOR_COUNT == 5, "Expected five flex channels");
struct SensorSample {
    uint32_t sampleSeq, at;
    float imu[6], flex[5];
};
QueueHandle_t sensorQueue = nullptr;
QueueHandle_t calibrationQueue = nullptr;
enum CalibrationState { WAIT_FLEX, FLEX_COUNTDOWN, FLEX_RUNNING, WAIT_GYRO,
                        GYRO_RUNNING, SENSOR_READY, SENSOR_ERROR };
std::atomic<int> calibrationState{WAIT_FLEX};
const char* calibrationName() {
    switch (calibrationState.load()) {
        case WAIT_FLEX: return "WAIT_FLEX";
        case FLEX_COUNTDOWN: return "FLEX_COUNTDOWN";
        case FLEX_RUNNING: return "FLEX_RUNNING";
        case WAIT_GYRO: return "WAIT_GYRO";
        case GYRO_RUNNING: return "GYRO_RUNNING";
        case SENSOR_READY: return "READY";
        default: return "SENSOR_ERROR";
    }
}
bool requestCalibration(char action) {
    int state = calibrationState.load();
    if (!calibrationQueue || state == SENSOR_ERROR || state == FLEX_COUNTDOWN ||
        state == FLEX_RUNNING || state == GYRO_RUNNING) return false;
    if (action == 'g' && state != WAIT_GYRO && state != SENSOR_READY) return false;
    if (action != 'c' && action != 'g') return false;
    calibrationState.store(action == 'c' ? FLEX_COUNTDOWN : GYRO_RUNNING);
    if (xQueueSend(calibrationQueue, &action, 0) != pdTRUE) {
        calibrationState.store(state); return false;
    }
    return true;
}
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
        motor.on();
        state = VIBRATING;
        stateStart = now;
      }
      break;
    case VIBRATING:
      if (now - stateStart >= 2000) {
        motor.off();
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

void sensorTask(void*) {
    uint32_t seq = 0;
    uint32_t reportAt = millis(), peakMs = 0, full = 0;
    for (;;) {
        char action;
        if (xQueueReceive(calibrationQueue, &action, 0) == pdTRUE) {
            motor.off();
            xQueueReset(sensorQueue);
            if (action == 'c') {
                vTaskDelay(pdMS_TO_TICKS(3000));
                calibrationState.store(FLEX_RUNNING);
                flex.calibrate(Serial);
                calibrationState.store(WAIT_GYRO);
            } else {
                vTaskDelay(pdMS_TO_TICKS(1000));
                mpu.calibrateGyro();
                calibrationState.store(SENSOR_READY);
            }
        }
        if (calibrationState.load() != SENSOR_READY) {
            updateGestureMotor(0, 0, 0, false);
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        SensorSample sample = {};
        sample.sampleSeq = seq++;
        sample.at = millis();
        MpuReading m = mpu.read(true);
        float values[] = {m.ax, m.ay, m.az, m.gx, m.gy, m.gz};
        memcpy(sample.imu, values, sizeof(values));
        int raw[5];
        flex.readNormalized(sample.flex, raw);
        updateGestureMotor(sample.flex[1], sample.flex[2], m.gx, false); // Disabled in teammate revision.
        bool valid = true;
        for (float v : sample.imu) valid = valid && isfinite(v);
        // Preserve the hardware library's -1 flex sentinel and valid IMU.
        for (float v : sample.flex) valid = valid && isfinite(v);
        if (valid && xQueueSend(sensorQueue, &sample, 0) != pdTRUE) {
            ++full;
        }
        const uint32_t elapsed = millis() - sample.at;
        if (elapsed > peakMs) peakMs = elapsed;
        if (millis() - reportAt >= 5000) {
            Serial.printf("[SAMPLE] peak_ms=%lu queue_full=%lu\n",
                          (unsigned long)peakMs, (unsigned long)full);
            reportAt = millis();
            peakMs = full = 0;
        }
        const uint32_t used = millis() - sample.at;
        if (used < 50) delay(50 - used);
        else vTaskDelay(1);
    }
}
void beginSensors() {
    motor.begin();
    Wire.begin(21,22);
    Wire.setClock(100000);
    Wire.setTimeOut(5);
    const bool adsReady = flex.begin(Wire);
    if (!adsReady) Serial.println("[ADS] Startup probe failed; ADS reads will retry.");
    if (!mpu.begin(Wire)) {
        calibrationState.store(SENSOR_ERROR);
        Serial.println("[SENSOR ERROR] Check wiring; network remains available");
        return;
    }
    sensorQueue = xQueueCreate(4, sizeof(SensorSample));
    calibrationQueue = xQueueCreate(1, sizeof(char));
    if (!sensorQueue || !calibrationQueue ||
        xTaskCreatePinnedToCore(sensorTask, "sensor", 6144, nullptr, 2, nullptr, xPortGetCoreID()) != pdPASS) {
        calibrationState.store(SENSOR_ERROR);
        return;
    }
    Serial.println("[CAL] Wireless calibration: send c then g from Ultra96 console");
}
