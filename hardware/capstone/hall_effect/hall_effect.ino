#include <Wire.h>
#include <Adafruit_ADS1X15.h>

Adafruit_ADS1015 ads;

#define SDA_PIN 21
#define SCL_PIN 22

void setup() {
  Serial.begin(115200);

  Wire.begin(SDA_PIN, SCL_PIN);

  if (!ads.begin(0x48, &Wire)) {
    Serial.println("ADS1015 not detected!");
    while (1);
  }

  ads.setGain(GAIN_ONE);

  delay(1000);
}

void loop() {
  int16_t hall1 = ads.readADC_SingleEnded(0);
  int16_t hall2 = ads.readADC_SingleEnded(1);
  int16_t hall3 = ads.readADC_SingleEnded(2);
  int16_t hall4 = ads.readADC_SingleEnded(3);

  // Arduino Serial Plotter format
  Serial.print("Hall1:");
  Serial.print(hall1);

  Serial.print("\tHall2:");
  Serial.print(hall2);

  Serial.print("\tHall3:");
  Serial.print(hall3);

  Serial.print("\tHall4:");
  Serial.println(hall4);

  delay(20);
}