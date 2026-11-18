#include <Wire.h>
#include <MpuSensor.h>
#include <math.h>

MpuSensor mpu;
const uint8_t SDA_PIN = 21, SCL_PIN = 22;
const float GRAVITY = 9.80665f;

// Hamilton quaternion (w,x,y,z): sensor to world, right handed.
struct Quaternion { float w, x, y, z; };
// Explicit declarations keep Arduino's prototype generator after the type.
Quaternion multiply(Quaternion a, Quaternion b);
void updateOrientation(const MpuReading& m, float dt);
Quaternion orientation = {1, 0, 0, 0};
Quaternion reference = {1, 0, 0, 0};
float biasX = 0, biasY = 0, biasZ = 0;
uint32_t previousSampleUs, previousOutputMs;

Quaternion multiply(Quaternion a, Quaternion b) {
  return {a.w*b.w-a.x*b.x-a.y*b.y-a.z*b.z,
          a.w*b.x+a.x*b.w+a.y*b.z-a.z*b.y,
          a.w*b.y-a.x*b.z+a.y*b.w+a.z*b.x,
          a.w*b.z+a.x*b.y-a.y*b.x+a.z*b.w};
}

void updateOrientation(const MpuReading& m, float dt) {
  float gx = m.gx-biasX, gy = m.gy-biasY, gz = m.gz-biasZ;
  float norm = sqrtf(m.ax*m.ax+m.ay*m.ay+m.az*m.az);
  // Accelerometer corrects tilt only near 1 g to reduce motion artifacts.
  if (fabsf(norm-GRAVITY) < 0.15f*GRAVITY) {
    float ax = m.ax/norm, ay = m.ay/norm, az = m.az/norm;
    const Quaternion& q = orientation;
    float vx = 2*(q.x*q.z-q.w*q.y);
    float vy = 2*(q.w*q.x+q.y*q.z);
    float vz = 1-2*(q.x*q.x+q.y*q.y);
    const float gain = 2.0f;
    gx += gain*(ay*vz-az*vy);
    gy += gain*(az*vx-ax*vz);
    gz += gain*(ax*vy-ay*vx);
  }
  // q_dot = 0.5*q*angular_velocity; gyro units are rad/s.
  Quaternion d = multiply(orientation, {0, gx, gy, gz});
  Quaternion& q = orientation;
  q.w += 0.5f*d.w*dt; q.x += 0.5f*d.x*dt;
  q.y += 0.5f*d.y*dt; q.z += 0.5f*d.z*dt;
  float length = sqrtf(q.w*q.w+q.x*q.x+q.y*q.y+q.z*q.z);
  q.w /= length; q.x /= length; q.y /= length; q.z /= length;
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);
  if (!mpu.begin(Wire)) {
    Serial.println("# MPU initialization failed!");
    while (1) delay(10);
  }
  delay(500);
  Serial.println("# Keep glove completely still for calibration...");
  // Use raw readings: processed readings smooth and discard small rotations.
  float ax = 0, ay = 0, az = 0;
  const int samples = 500;
  for (int i = 0; i < samples; ++i) {
    MpuReading m = mpu.read(false);
    biasX += m.gx; biasY += m.gy; biasZ += m.gz;
    ax += m.ax; ay += m.ay; az += m.az;
    delay(5);
  }
  biasX /= samples; biasY /= samples; biasZ /= samples;
  float roll = atan2f(ay, az);
  float pitch = atan2f(-ax, sqrtf(ay*ay+az*az));
  float cr = cosf(roll/2), sr = sinf(roll/2);
  float cp = cosf(pitch/2), sp = sinf(pitch/2);
  orientation = {cr*cp, sr*cp, cr*sp, -sr*sp};
  reference = orientation;
  Serial.println("# Ready. Send r to recenter. Yaw drifts over time.");
  Serial.println("qw,qx,qy,qz,roll_deg,pitch_deg,yaw_deg");
  previousSampleUs = micros();
  previousOutputMs = millis();
}

void loop() {
  MpuReading m = mpu.read(false);
  uint32_t now = micros();
  float dt = (uint32_t)(now-previousSampleUs)*1e-6f;
  previousSampleUs = now;
  // Skip long pauses instead of integrating a stale sample across the gap.
  if (dt > 0 && dt < 0.1f) updateOrientation(m, dt);
  while (Serial.available()) {
    char command = Serial.read();
    if (command == 'r' || command == 'R') reference = orientation;
  }
  if ((uint32_t)(millis()-previousOutputMs) >= 20) {
    previousOutputMs = millis();
    Quaternion inverse = {reference.w, -reference.x, -reference.y, -reference.z};
    Quaternion q = multiply(inverse, orientation);
    float roll = atan2f(2*(q.w*q.x+q.y*q.z), 1-2*(q.x*q.x+q.y*q.y));
    float sinPitch = 2*(q.w*q.y-q.z*q.x);
    float pitch = asinf(fmaxf(-1.0f, fminf(1.0f, sinPitch)));
    float yaw = atan2f(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z));
    Serial.print(q.w, 6); Serial.print(',');
    Serial.print(q.x, 6); Serial.print(',');
    Serial.print(q.y, 6); Serial.print(',');
    Serial.print(q.z, 6); Serial.print(',');
    Serial.print(roll*RAD_TO_DEG, 2); Serial.print(',');
    Serial.print(pitch*RAD_TO_DEG, 2); Serial.print(',');
    Serial.println(yaw*RAD_TO_DEG, 2);
  }
  delay(5);
}
