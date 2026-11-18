#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cli="${ARDUINO_CLI:-arduino-cli}"
for sketch in capstone mpu6050 flex_sensors flex_sensors_raw wireless motor; do
  "$cli" compile --fqbn "${FQBN:-esp32:esp32:firebeetle32}" \
    --library "$repo_dir/libraries/CapstoneSensors" \
    --library "$repo_dir/libraries/CapstoneWireless" \
    --library "$repo_dir/libraries/Adafruit_MPU6050" \
    "$@" "$repo_dir/$sketch"
done
