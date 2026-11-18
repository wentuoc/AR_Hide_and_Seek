#include <Arduino.h>

const uint8_t GPIO_ADC_PIN = 34;  // ESP32 onboard ADC input (currently thumb).
const float FLEX_EMA_ALPHA = 0.3f;
const unsigned long SAMPLE_PERIOD_MS = 50;
const uint8_t PRINT_MODE = 1;  // 1: Serial Plotter, 2: CSV (header + data rows).
static_assert(PRINT_MODE == 1 || PRINT_MODE == 2, "PRINT_MODE must be 1 or 2");

int readAveragedAdc() {
  uint32_t total = 0;
  for (uint8_t sample = 0; sample < 32; ++sample) {
    total += analogRead(GPIO_ADC_PIN);
    if (sample < 31) delayMicroseconds(100);
  }
  return (total + 16) / 32;
}

// Median of three samples followed by EMA, matching flex_sensors_raw.
float filterSample(int sample) {
  static bool initialized = false;
  static int history[3] = {};
  static uint8_t next = 0;
  static float smoothed = 0;
  if (!initialized) {
    for (uint8_t i = 0; i < 3; ++i) history[i] = sample;
    smoothed = sample;
    initialized = true;
  }
  history[next] = sample;
  next = (next + 1) % 3;
  const int median = history[0] + history[1] + history[2]
      - min(history[0], min(history[1], history[2]))
      - max(history[0], max(history[1], history[2]));
  smoothed += FLEX_EMA_ALPHA * (median - smoothed);
  return smoothed;
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(GPIO_ADC_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(GPIO_ADC_PIN, ADC_11db);

  if (PRINT_MODE == 2) Serial.println("GPIO_raw");
}

void loop() {
  const unsigned long sampleStart = millis();
  int raw = readAveragedAdc();

  // Comment out this line to bypass median/EMA (GPIO averaging remains).
  raw = int(filterSample(raw) + 0.5f);

  if (PRINT_MODE == 1) Serial.print("GPIO_raw:");
  Serial.println(raw);  // ADC counts, without calibration or normalization.

  const unsigned long elapsed = millis() - sampleStart;
  if (elapsed < SAMPLE_PERIOD_MS) delay(SAMPLE_PERIOD_MS - elapsed);
}
