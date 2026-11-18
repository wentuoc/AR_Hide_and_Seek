#!/usr/bin/env bash
set -euo pipefail
cli="${ARDUINO_CLI:-arduino-cli}"
index_url="https://espressif.github.io/arduino-esp32/package_esp32_index.json"
"$cli" core update-index --additional-urls "$index_url"
"$cli" core install esp32:esp32@3.2.0 --additional-urls "$index_url"
"$cli" lib update-index
# Only dependencies needed by these sketches; MPU6050 itself is vendored.
"$cli" lib install --no-deps 'Adafruit ADS1X15@2.6.2' 'Adafruit BusIO@1.17.4' 'Adafruit Unified Sensor@1.1.15'
