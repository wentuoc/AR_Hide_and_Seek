#include "Motor.h"

void Motor::begin() {
  digitalWrite(pin_, LOW);
  pinMode(pin_, OUTPUT);
  initialized_ = true;
  off();
}

void Motor::setEnabled(bool enabled) {
  if (!initialized_) return;
  digitalWrite(pin_, enabled ? HIGH : LOW);
}
