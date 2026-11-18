#include <Wire.h>
#include <FlexSensors.h>

FlexSensors flex;

const uint8_t SDA_PIN = 21;
const uint8_t SCL_PIN = 22;
const uint8_t SENSOR_COUNT = FlexSensors::SENSOR_COUNT;
const unsigned long SAMPLE_PERIOD_MS = 50;  // Approximately 20 samples/second.
const uint8_t PRINT_MODE = 1;  // 1: Serial Plotter, 2: CSV (header + data rows).
static_assert(PRINT_MODE == 1 || PRINT_MODE == 2, "PRINT_MODE must be 1 or 2");

const uint8_t DATA_MODE = 1;  // 1: normalized, 2: raw, 3: both.
// Bits 0-4 select A0, A1, A2, A3, GPIO. Use 0x0F for four channels
// (eight traces in both mode), or 0x1F for all five (ten traces in both mode).
const uint8_t CHANNEL_MASK = 0x1F;
static_assert(DATA_MODE >= 1 && DATA_MODE <= 3, "DATA_MODE must be 1, 2, or 3");
static_assert(CHANNEL_MASK > 0 && CHANNEL_MASK <= 0x1F, "Select at least one flex channel");
const bool SHOW_NORMALIZED = DATA_MODE != 2;
const bool SHOW_RAW = DATA_MODE != 1;

const char* const NORMALIZED_NAMES[] = {
  "A0_normalized", "A1_normalized", "A2_normalized",
  "A3_normalized", "GPIO_normalized"
};
const char* const RAW_NAMES[] = {"A0_raw", "A1_raw", "A2_raw", "A3_raw", "GPIO_raw"};

void printFieldPrefix(uint8_t field, const char* name) {
  if (field > 0) Serial.print(PRINT_MODE == 1 ? '\t' : ',');
  if (PRINT_MODE == 1) {
    Serial.print(name);
    Serial.print(':');
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);

  if (!flex.begin(Wire)) {
    Serial.println("ADS1015 not found at 0x48. Check power and I2C wiring.");
    while (true) delay(1000);
  }

  delay(500);
  flex.calibrate(Serial);
  if (PRINT_MODE == 2) {
    uint8_t field = 0;
    for (uint8_t channel = 0; channel < SENSOR_COUNT; ++channel) {
      if (!(CHANNEL_MASK & (1 << channel))) continue;
      if (SHOW_NORMALIZED) {
        if (field++ > 0) Serial.print(',');
        Serial.print(NORMALIZED_NAMES[channel]);
      }
      if (SHOW_RAW) {
        if (field++ > 0) Serial.print(',');
        Serial.print(RAW_NAMES[channel]);
      }
    }
    Serial.println();
  }
}

void loop() {
  const unsigned long sampleStart = millis();

  float normalized[SENSOR_COUNT];
  int raw[SENSOR_COUNT];
  // One acquisition: normalized values are median/EMA filtered; raw values
  // are ADC counts before those filters (GPIO retains its 32-read average).
  flex.readNormalized(normalized, raw);

  uint8_t field = 0;
  // Only normalized-only mode uses fixed 0-1 scale anchors (two traces).
  if (PRINT_MODE == 1 && DATA_MODE == 1) {
    Serial.print("low:0\thigh:1");
    field = 2;
  }
  for (uint8_t channel = 0; channel < SENSOR_COUNT; ++channel) {
    if (!(CHANNEL_MASK & (1 << channel))) continue;
    if (SHOW_NORMALIZED) {
      printFieldPrefix(field++, NORMALIZED_NAMES[channel]);
      Serial.print(normalized[channel], 3);
    }
    if (SHOW_RAW) {
      printFieldPrefix(field++, RAW_NAMES[channel]);
      Serial.print(raw[channel]);
    }
  }

  Serial.println();
  const unsigned long elapsed = millis() - sampleStart;
  if (elapsed < SAMPLE_PERIOD_MS) delay(SAMPLE_PERIOD_MS - elapsed);
}
