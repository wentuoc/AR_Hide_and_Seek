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

 private:
  uint8_t pin_;
  bool initialized_ = false;
};
