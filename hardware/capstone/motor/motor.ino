#include <Motor.h>

// ERM motor switched by a 3.3 V-compatible N-channel MOSFET:
// GPIO17 -> gate resistor -> gate; gate -> 10 kOhm -> GND.
// Source -> GND; drain -> motor negative; motor positive -> rated supply.
// Connect supply ground to ESP32 ground. Fit a flyback diode across the
// motor: cathode to motor positive, anode to motor negative.
// The external pulldown holds the motor off before setup() runs.
const uint8_t MOTOR_PIN = 17;
Motor motor(MOTOR_PIN);

void setup() {
  motor.begin();
  Serial.begin(115200);
  Serial.println("ERM motor ready. Send 1 for three pulses, 0 to stop.");
}

void loop() {
  motor.update();
  if (Serial.available() > 0) {
    const char command = Serial.read();
    if (command == '1' || command == '0') {
      if (command == '1') {
        motor.pulse();
        Serial.println("Motor: three pulses");
      } else {
        motor.off();
        Serial.println("Motor OFF");
      }
    }
  }
}
