#include "Motor.h"

void Motor::begin() {
  digitalWrite(pin_, LOW);
  pinMode(pin_, OUTPUT);
  initialized_ = true;
  off();
}

void Motor::setEnabled(bool enabled) {
  if (!initialized_) return;
  pulsesRemaining_ = 0;
  digitalWrite(pin_, enabled ? HIGH : LOW);
}

void Motor::pulse() {
  if (!initialized_) return;
  pulsesRemaining_ = 3;
  pulseOn_ = true;
  phaseStart_ = millis();
  digitalWrite(pin_, HIGH);
}

void Motor::update() {
  if (!initialized_ || !isPulsing()) return;
  const unsigned long now = millis();
  if (now - phaseStart_ < (pulseOn_ ? 500UL : 200UL)) return;
  phaseStart_ = now;
  if (pulseOn_) {
    digitalWrite(pin_, LOW);
    pulseOn_ = false;
    --pulsesRemaining_;
  } else {
    digitalWrite(pin_, HIGH);
    pulseOn_ = true;
  }
}
