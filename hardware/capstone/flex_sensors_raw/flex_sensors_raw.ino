#include <Wire.h>
#include <FlexSensors.h>

FlexSensors flex;

const uint8_t SDA_PIN = 21;
const uint8_t SCL_PIN = 22;
const uint8_t ADS_ADDRESS = 0x48;
const uint8_t GPIO_ADC_PIN = 34;  // Onboard ADC input on ESP32 GPIO34 (currently thumb).
const uint8_t SENSOR_COUNT = FlexSensors::SENSOR_COUNT;
const float FLEX_EMA_ALPHA = 0.3f;
const unsigned long SAMPLE_PERIOD_MS = 50;
const uint8_t PRINT_MODE = 1;  // 1: Serial Plotter, 2: CSV (header + data rows).
static_assert(PRINT_MODE == 1 || PRINT_MODE == 2, "PRINT_MODE must be 1 or 2");

const char* const FIELD_NAMES[] = {"A0_raw", "A1_raw", "A2_raw", "A3_raw", "GPIO_raw"};

// Median of three successive samples, followed by EMA; state stays in float
// precision even though the raw sketch prints rounded integer ADC counts.
float filterChannel(uint8_t channel, int sample) {
  static bool initialized[SENSOR_COUNT] = {};
  static int histories[SENSOR_COUNT][3] = {};
  int* history = histories[channel];
  static uint8_t next[SENSOR_COUNT] = {};
  static float smoothed[SENSOR_COUNT] = {};
  if (!initialized[channel]) {
    for (uint8_t i = 0; i < 3; ++i) history[i] = sample;
    smoothed[channel] = sample;
    initialized[channel] = true;
  }
  history[next[channel]] = sample;
  next[channel] = (next[channel] + 1) % 3;
  const int median = history[0] + history[1] + history[2]
      - min(history[0], min(history[1], history[2]))
      - max(history[0], max(history[1], history[2]));
  smoothed[channel] += FLEX_EMA_ALPHA * (median - smoothed[channel]);
  return smoothed[channel];
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);
  if (!flex.begin(Wire, ADS_ADDRESS, GPIO_ADC_PIN)) {
    Serial.println("ADS1015 not found at 0x48. Check power and I2C wiring.");
    while (true) delay(1000);
  }

  if (PRINT_MODE == 2) {
    for (uint8_t channel = 0; channel < SENSOR_COUNT; ++channel) {
      if (channel > 0) Serial.print(',');
      Serial.print(FIELD_NAMES[channel]);
    }
    Serial.println();
  }
}

void loop() {
  const unsigned long sampleStart = millis();
  int raw[SENSOR_COUNT];
  flex.read(raw);

  // Comment out this loop to bypass median/EMA (GPIO averaging remains).
  for (uint8_t channel = 0; channel < SENSOR_COUNT; ++channel) {
    raw[channel] = int(filterChannel(channel, raw[channel]) + 0.5f);
  }

  // if (PRINT_MODE == 1) Serial.print("low:0\thigh:1\t");

  for (uint8_t channel = 0; channel < SENSOR_COUNT; ++channel) {
    if (channel > 0) Serial.print(PRINT_MODE == 1 ? '\t' : ',');
    if (PRINT_MODE == 1) {
      Serial.print(FIELD_NAMES[channel]);
      Serial.print(':');
    }
    Serial.print(raw[channel]);  // ADC counts, without calibration or normalization.
  }
  Serial.println();

  const unsigned long elapsed = millis() - sampleStart;
  if (elapsed < SAMPLE_PERIOD_MS) delay(SAMPLE_PERIOD_MS - elapsed);
}
