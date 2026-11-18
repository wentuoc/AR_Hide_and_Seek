#include <Wire.h>
#include <FlexSensors.h>
#include <MpuSensor.h>
#include <Motor.h>

FlexSensors flex;
MpuSensor mpu;
Motor motor(17);

const uint8_t SDA_PIN = 21;
const uint8_t SCL_PIN = 22;
const uint8_t SENSOR_COUNT = FlexSensors::SENSOR_COUNT;
const unsigned long SAMPLE_PERIOD_MS = 50;

void setup() {
  motor.begin();  // Keep the motor off throughout data collection.
  Serial.begin(115200);
  delay(1000);
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);

  if (!flex.begin(Wire)) {
    Serial.println("# ADS1015 initialization failed at 0x48.");
    while (true) delay(1000);
  }
  // Use the vendored MPU library accepting WHO_AM_I = 0x70 at address 0x68.
  if (!mpu.begin(Wire)) {
    Serial.println("# MPU initialization failed at 0x68. Check wiring and patched library.");
    while (true) delay(1000);
  }

  Serial.println("# Flex calibration starts in 3 seconds. Prepare to move all fingers.");
  delay(3000);
  flex.calibrate(Serial);
  Serial.println("# Rest your hand and keep the MPU still. Gyro calibration starts in 3 seconds.");
  delay(3000);
  mpu.calibrateGyro();
  Serial.println("# Gyro calibrated. Recording starts now. Reset to recalibrate.");
  Serial.println("seq,time,ax_m_s2,ay_m_s2,az_m_s2,gx_rad_s,gy_rad_s,gz_rad_s,A0_normalized,A1_normalized,A2_normalized,A3_normalized,GPIO_normalized,A0_raw,A1_raw,A2_raw,A3_raw,GPIO_raw");
  // Serial.println("ax_m_s2,ay_m_s2,az_m_s2,gx_rad_s,gy_rad_s,gz_rad_s,A0_normalized,A1_normalized,A2_normalized,A3_normalized,GPIO_normalized");
}

void loop() {
  const unsigned long sampleStart = millis();
  const MpuReading motion = mpu.read(true);
  float normalized[SENSOR_COUNT];
  int raw[SENSOR_COUNT];
  // Same acquisition and processing as capstone; raw values precede flex filters.
  flex.readNormalized(normalized, raw);

  // static uint32_t sequence = 0;
  // Serial.print(sequence++);
  // Serial.print(','); Serial.print(sampleStart);
  // Serial.print(','); 
  Serial.print(motion.ax, 3);
  Serial.print(','); Serial.print(motion.ay, 3);
  Serial.print(','); Serial.print(motion.az, 3);
  Serial.print(','); Serial.print(motion.gx, 3);
  Serial.print(','); Serial.print(motion.gy, 3);
  Serial.print(','); Serial.print(motion.gz, 3);
  for (uint8_t channel = 0; channel < SENSOR_COUNT; ++channel) {
    Serial.print(','); Serial.print(normalized[channel], 3);
  }
  for (uint8_t channel = 0; channel < SENSOR_COUNT; ++channel) {
    Serial.print(','); Serial.print(raw[channel]);
  }
  Serial.println();

  const unsigned long elapsed = millis() - sampleStart;
  if (elapsed < SAMPLE_PERIOD_MS) delay(SAMPLE_PERIOD_MS - elapsed);
}
