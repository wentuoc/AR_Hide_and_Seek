#pragma once

#include <Arduino.h>

// Active-high, low-side MOSFET control for an ERM vibration motor.
// Requires an external gate pulldown and a flyback diode across the motor.
class Motor {
 public:
  explicit Motor(uint8_t pin = 17) : pin_(pin) {}
  // Configure the gate output and start with the motor off.
  void begin();
  // Call begin() first. Commands before initialization are ignored.
  void setEnabled(bool enabled);
  void on() { setEnabled(true); }
  void off() { setEnabled(false); }
  // Start three 500 ms pulses separated by 200 ms gaps. Call update() often.
  void pulse();
  void update();
  bool isPulsing() const { return pulsesRemaining_ > 0; }

 private:
  uint8_t pin_;
  bool initialized_ = false;
  uint8_t pulsesRemaining_ = 0;
  bool pulseOn_ = false;
  unsigned long phaseStart_ = 0;
};
