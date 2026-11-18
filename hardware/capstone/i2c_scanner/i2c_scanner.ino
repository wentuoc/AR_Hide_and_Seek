// #include <Wire.h>

// void setup() {
//   Serial.begin(115200);

//   // FireBeetle ESP32 I²C pins
//   Wire.begin(21, 22);  // SDA, SCL

//   Serial.println("Scanning I2C...");
// }

// void loop() {
//   int found = 0;

//   for (byte address = 1; address < 127; address++) {
//     Wire.beginTransmission(address);

//     if (Wire.endTransmission() == 0) {
//       Serial.print("Found device at 0x");
//       if (address < 16) Serial.print("0");
//       Serial.println(address, HEX);
//       found++;
//     }
//   }

//   if (found == 0) Serial.println("No I2C devices found");
//   Serial.println();

//   delay(3000);
// }

// ==============

// #include <Wire.h>

// #define SDA_PIN 21
// #define SCL_PIN 22
// #define MPU_ADDR 0x68

// void setup() {
//   Serial.begin(115200);
//   delay(1000);

//   Wire.begin(SDA_PIN, SCL_PIN);
//   Wire.setClock(100000);

//   Serial.println("Reading MPU6050 WHO_AM_I...");

//   // WHO_AM_I register
//   Wire.beginTransmission(MPU_ADDR);
//   Wire.write(0x75);
//   uint8_t err = Wire.endTransmission(false);

//   Serial.print("I2C write result: ");
//   Serial.println(err);

//   if (err == 0) {
//     Wire.requestFrom(MPU_ADDR, 1);

//     if (Wire.available()) {
//       uint8_t whoami = Wire.read();

//       Serial.print("WHO_AM_I = 0x");
//       Serial.println(whoami, HEX);
//     }
//   }
// }

// void loop() {}

// ==============

#include <Wire.h>

#define MPU_ADDR 0x68

uint8_t readReg(uint8_t reg) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);

  if (Wire.endTransmission(false) != 0)
    return 0xFF;

  Wire.requestFrom(MPU_ADDR, 1);

  return Wire.available() ? Wire.read() : 0xFF;
}

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);

  Serial.println("Identification registers:");

  Serial.print("WHO_AM_I  0x75 = 0x");
  Serial.println(readReg(0x75), HEX);

  Serial.print("PWR_MGMT1 0x6B = 0x");
  Serial.println(readReg(0x6B), HEX);

  Serial.print("ACCEL_CFG 0x1C = 0x");
  Serial.println(readReg(0x1C), HEX);

  Serial.print("GYRO_CFG  0x1B = 0x");
  Serial.println(readReg(0x1B), HEX);
}

void loop() {}