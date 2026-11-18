# Capstone sensor sketches

The sensor sketches use the same `CapstoneSensors` library in
`libraries/CapstoneSensors/src`. Edit `FlexSensors.cpp` for flex sensor behavior
and `MpuSensor.cpp` for MPU initialization, calibration, and acquisition.
The `.ino` files control calibration order, sampling timing, and Serial output.
`FlexSensors::calibrate(Serial)` owns flex range calibration and its prompts;
`readNormalized()` applies those ranges and per-channel filtering.
`MpuSensor::calibrateGyro()` owns gyro offset calibration.
`WifiStream.h` / `WifiStream.cpp` in `libraries/CapstoneWireless/src` own the access point, TCP server, background
queue, status messages, and incoming calibration commands. Sketches include
`<WifiStream.h>` and pass their local Wi-Fi configuration to
`WifiStream::begin(AP_SSID, AP_PASSWORD, STREAM_PORT)`. Calibration sequencing
stays in `capstone.ino`; the transport exposes `getStage()`, `setStage()`,
`takeCommand()`, `send()`, `clearPendingRecords()`, and transmission activity
without exposing its FreeRTOS queue or atomic state.

The flex readers acquire five inputs: the four non-thumb fingers on
ADS1015 A0–A3 (address `0x48`), and the thumb on ESP32 GPIO34.
Array and output order is always A0, A1, A2, A3, GPIO. Labels are channel-based:
`A0_raw`–`A3_raw` and `GPIO_raw`, or `A0_normalized`–`A3_normalized`
and `GPIO_normalized`, so rewiring does not require renaming fingers. The ADS readings and
12-bit ESP32 reading are raw counts with different scales.
I2C uses SDA 21 and SCL 22.

## Pin assignments

Numbers below are ESP32 GPIO numbers, not physical header positions.
ADS1015 A0–A3 are channels on the external ADC, not ESP32 pins.

| Connection | Pin / channel | Use |
| --- | --- | --- |
| Shared I2C SDA | GPIO21 | ADS1015 (`0x48`) and MPU6050 (`0x68`) SDA |
| Shared I2C SCL | GPIO22 | ADS1015 and MPU6050 SCL |
| Index flex sensor | ADS1015 A0 | First flex channel |
| Middle flex sensor | ADS1015 A1 | Second flex channel |
| Ring flex sensor | ADS1015 A2 | Third flex channel |
| Pinky flex sensor | ADS1015 A3 | Fourth flex channel |
| Thumb flex sensor | GPIO34 | Onboard ADC input; fifth flex channel |
| ERM motor MOSFET gate | GPIO17 | Active-high output; delayed vibration after fist + wrist turn |
| Onboard status LED | GPIO2 (`LED_BUILTIN`) | Combined sketch's sampling/transmission indicator |

The standalone flex and MPU sketches use the same sensor pins as the combined
sketch. `hall_effect/hall_effect.ino` instead reads Hall1–Hall4 on ADS1015 A0–A3
at `0x48`, using GPIO21/22 for I2C; it is a separate test using those ADC channels.
`i2c_scanner/i2c_scanner.ino` uses GPIO21/22 and currently reads MPU identification
registers at `0x68`. The standalone wireless test requires no sensor or motor pins.
Connect sensor and motor-driver grounds to ESP32 GND; motor wiring is detailed below.

## Sensor acquisition

`flex_sensors.ino` calibrates all five fingers for five seconds at startup.
When the Serial prompt appears, repeatedly flex and straighten each finger through
its full sensing range, briefly holding both endpoints. Calibration ignores zero/negative
readings and uses each input's 5th percentile as the lower bound and maximum valid
reading as the upper bound (lower-bound histogram resolution: 16 ADS1015 counts,
32 GPIO counts),
then outputs `(raw - min) / (max - min)` clamped to 0–1. These values represent
calibration minimum to maximum, not bend angles. An input with no measured range
outputs 0 to avoid division by zero. Calibration requires at least 20 valid samples
and a span of two histogram bins; otherwise it keeps the previous bounds, or 0
if uncalibrated. During sampling, zero/negative dropouts hold the last filtered
value; raw output still includes them. The separate `isBelowRangeDropout()`
function also holds the last filtered value for samples below the calibrated
minimum. Set `HOLD_BELOW_CALIBRATION_MIN` to `false` in `FlexSensors.cpp` to
disable only this behavior without changing percentile calibration. Reset the board to recalibrate; bounds are
held in RAM. Startup instructions and measured bounds are printed as `#` comment
lines before the data (skip these when parsing CSV).
It samples approximately every 50 ms. In `flex_sensors.ino`, `PRINT_MODE` selects
Serial Plotter (`1`) or CSV with a header (`2`). `DATA_MODE` selects normalized
(`1`, default), raw (`2`), or both (`3`), acquired together. Raw values precede
median/EMA filtering; GPIO retains its 32-read average. `CHANNEL_MASK` selects
channels with bits 0–4 for A0, A1, A2, A3, GPIO: `0x1F` selects all five, while
`0x0F` selects A0–A3. Both mode emits two values per selected channel, so use
at most four channels for Arduino IDE 2's eight-trace plotter limit. Normalized-only
plotter mode also emits `low`/`high` scale anchors; other modes omit them.
Raw counts and normalized values share one plot axis, so raw counts dominate
when both are displayed.
`FlexSensors::read()` remains available for raw ADC counts.

`flex_sensors_raw/flex_sensors_raw.ino` reads ADS1015 A0–A3 at `0x48`
plus the thumb sensor on ESP32 GPIO34 as a 12-bit analog input,
and prints raw integer ADC counts without calibration or normalization. It uses
SDA 21, SCL 22, and the same `GAIN_ONE` setting as `FlexSensors`. Open Serial
Plotter at 115200 baud to see `A0_raw` through `A3_raw` and `GPIO_raw` approximately every 50 ms.
Set `PRINT_MODE` to `2` for CSV output. This sketch uses `FlexSensors::read()`,
including its rounded average of 32 onboard GPIO ADC readings spaced 100 µs apart.
The sketch then applies a three-sample median and exponential smoothing (alpha 0.3)
to each channel independently before rounding to integer counts.
The GPIO input's raw counts use
the ESP32 ADC scale, which differs from the ADS1015 scale.

`mpu6050.ino` calibrates the stationary gyro, smooths all six axes with an
exponential low-pass filter (alpha 0.2), and applies a 0.02 rad/s gyro deadband.
`capstone.ino` starts Wi-Fi and waits for calibration commands from the live
plotter. Click **Start calibration**, prepare for three seconds, then flex and
straighten all fingers for five seconds. Rest your hand and click **I'm ready**
to calibrate the stationary gyro. Readings and CSV recording begin afterward.
**Recalibrate** repeats this sequence without restarting Wi-Fi; recording pauses
during calibration and resumes in the same CSV. Calibration bounds remain in RAM.
It requests processed MPU readings with `read(true)`, enabling the low-pass filter
and gyro deadband. Flex readings use the measured ranges, with a three-sample
median and exponential smoothing (alpha 0.3) on each flex input independently before normalization.
Inputs with no measured range output 0; measured bounds and warnings appear on Serial.
It streams six processed MPU values and five normalized flex values to three
decimal places, followed by five raw integer flex counts, targeting a 50 ms loop
period. Optional Serial sample output is commented out in the sketch; if enabled,
set `PRINT_MODE` to `2` for CSV. Startup prompts and bounds use `#` comment lines.
Historical logs and plots retain their original finger labels, which describe
the wiring when they were captured.
The MPU at `0x68` uses the vendored Adafruit MPU6050 library in
`libraries/Adafruit_MPU6050`. It accepts standard ID `0x68` and the project's
observed ID `0x70`. See [the patch notes](libraries/Adafruit_MPU6050/PATCHES.md).

## ERM motor

`motor/motor.ino` is a standalone ERM vibration motor sketch using GPIO17
to drive a 3.3 V-compatible N-channel MOSFET. The motor starts off; open
Serial Monitor at 115200 baud and send `1` for three 500 ms pulses with
200 ms gaps, or `0` to stop immediately. Sending `1` again restarts the sequence.

Connect GPIO17 through a gate resistor to the MOSFET gate, with a 10 kOhm
gate-to-ground pulldown. Connect source to ground, drain to motor negative,
and motor positive to a supply matching the motor's rated voltage. Share
ground with the ESP32. Add a flyback diode across the motor (cathode to
positive, anode to negative). Select the MOSFET and diode for the motor's
stall current; do not power the motor from a GPIO.

The external pulldown keeps the MOSFET off before the sketch starts.

Motor control is provided by `Motor.h` and `Motor.cpp` in the existing
`CapstoneSensors` library. The standalone sketch handles Serial commands;
the library has no Serial output or delays. `capstone/capstone.ino` declares
`Motor motor(MOTOR_PIN)` with `MOTOR_PIN = 17` and calls `motor.begin()` first
in `setup()`, before sensor initialization. Once calibrated, `updateGestureMotor()`
recognizes a fist + wrist turn when the average of middle/ring flex channels
(`A1_normalized` and `A2_normalized`) is below 0.50 and absolute processed X-axis
gyro speed exceeds 2.0 rad/s continuously for 100 ms. It waits 1.5 seconds, drives
GPIO17 HIGH for three 500 ms pulses with 200 ms off gaps, then leaves it LOW.
Timing uses `millis()` and is checked
each sample (approximately every 50 ms), so sampling and Wi-Fi streaming continue.
Gestures during the wait or vibration are ignored. After vibration, open the hand
(middle/ring average above 0.65 for 300 ms) to rearm. For each demonstration,
close your fist, turn your wrist firmly, wait for vibration to finish, then open
your hand before repeating. Keep the sensor mounting and calibration consistent.
Starting recalibration cancels pending vibration and immediately turns the motor
off before the calibration delays. The motor stays off while uncalibrated.
Serial on/off commands are supported only by the standalone motor sketch.
The library owns the three-pulse timing through `motor.pulse()`; call
`motor.update()` regularly and use `motor.isPulsing()` to check completion.
It also provides continuous `motor.on()`, `motor.off()`, and `motor.setEnabled(bool)`;
these commands cancel any pulse sequence, with `off()` stopping it immediately.
`begin()` always turns the motor off;
commands before `begin()` are ignored. To choose another output-capable pin,
pass it to the constructor, for example `Motor motor(25);`.

## Reproducible setup and build

Install [Arduino CLI](https://arduino.github.io/arduino-cli/latest/installation/),
then run from the repository root:

```sh
bash scripts/setup-arduino.sh
bash scripts/build-arduino.sh
```

Setup installs ESP32 core 3.2.0, ADS1X15 2.6.2, BusIO 1.17.4, and Unified
Sensor 1.1.15 into the CLI's configured Arduino directories. It requires network
access and may change installed dependency versions. The complete MPU6050 2.2.9
library is included here with its license and compatibility patch; do not install
an unpatched replacement. Display libraries used by its upstream examples are
not needed by our sketches.

The build script compiles six sketches (`capstone`, `mpu6050`, `flex_sensors`,
`flex_sensors_raw`, `wireless`, and `motor`) for `esp32:esp32:firebeetle32`.
The separate `hall_effect` and `i2c_scanner` diagnostic sketches are not in this build loop.
Set `FQBN` for another board or `ARDUINO_CLI` to your CLI executable path.
For the macOS Arduino IDE's bundled CLI, for example:

```sh
export ARDUINO_CLI='/Applications/Arduino IDE.app/Contents/Resources/app/node_modules/arduino-ide-extension/build/arduino-cli'
bash scripts/build-arduino.sh
```

The script explicitly selects all three repository libraries with `--library`.
Use `bash scripts/build-arduino.sh --verbose` to inspect the selected paths:
Adafruit MPU6050 must resolve to this checkout's `libraries/Adafruit_MPU6050`.
[Arduino CLI compile options](https://arduino.github.io/arduino-cli/latest/commands/arduino-cli_compile/)
document explicit library selection.

## Arduino IDE setup

Install the same ESP32 core and three dependency versions through Boards Manager
and Library Manager. Copy or link `libraries/CapstoneSensors`, `libraries/CapstoneWireless`, and
`libraries/Adafruit_MPU6050` into your configured sketchbook's `libraries` folder.
If an MPU6050 copy already exists there, back it up outside that folder first,
then replace it with the repository copy. Restart the IDE and select your board.
Enable verbose compilation and verify that the patched repository copy is used.
Prefer links during development so edits in this repository are reflected directly.

## Sharing changes

Include `libraries/Adafruit_MPU6050` (including license and PATCHES.md),
`libraries/CapstoneSensors`, `libraries/CapstoneWireless`, the sketches, scripts, and README in your pull request.
Teammates can clone, run setup, and build without editing their installed driver.
Keep the vendored library's source version and patch notes updated together.

## Stream the combined sketch over Wi-Fi

The combined sketch sends all six processed MPU values, five normalized flex
values, and optionally five raw flex counts over TCP at its existing target of 20 samples/second.
Optional Serial output remains available. No additional Python packages are needed.

The onboard LED (`LED_BUILTIN`, GPIO2 on FireBeetle ESP32) stays off during
startup/calibration or an initialization failure. Once sampling starts, it blinks
slowly (500 ms on/off). Successful TCP writes switch it to a fast blink (100 ms
on/off); it returns to the slow heartbeat after 500 ms without a successful write.
This indicates local transmission activity, not confirmation that the laptop saved
the data. LED timing uses `millis()` and adds no sampling delays. For a board with
an active-low LED, set `STATUS_LED_ON` to `LOW` in the combined sketch.

1. Build and upload `capstone/capstone.ino` using the setup above. The ESP32 creates
   its own 2.4 GHz Wi-Fi network and starts a TCP server at `192.168.4.1:5000`,
   then waits for you to start calibration from the plotter.
2. On your laptop, join **Capstone-Glove** with password **capstone123**.
   Stay connected if the laptop warns that this network has no internet access.
   No router, internet connection, or laptop IP configuration is required.
3. From the repository root, start the receiver:

   ```sh
   python3 logging/receive_wifi.py --plot
   ```

   The receiver connects to the ESP32, prints readings, and saves a timestamped
   CSV under `logging/captures/`. Use `--quiet` to hide readings,
   `--host 192.168.4.1 --port 5000` to select the ESP32 endpoint, or
   `--output path/to/new.csv` to select a new file. Existing files are never
   overwritten. Keep the laptop awake while recording.
4. Open **http://127.0.0.1:8765** and follow **Start calibration**, then **I'm ready**.
   Readings appear and are saved once calibration finishes. Use **Recalibrate**
   whenever needed; Wi-Fi remains connected. It retries
   every two seconds after connection failures/disconnections. If the ESP32
   restarts, rejoin its Wi-Fi network if your laptop does not do so automatically.
   One streaming receiver is supported at a time.

To customize the ESP32 network, copy `capstone/wifi_config.example.h` to
`capstone/wifi_config.h`, edit `AP_SSID`, `AP_PASSWORD` (8–63 characters), and
optionally `STREAM_PORT`, then upload again. The local file is ignored by Git;
without it, the example defaults work directly. These credentials belong to the
ESP32 access point, not an existing router. `LAPTOP_HOST`, `LAPTOP_PORT`, and the
old router credentials are no longer used. The receiver initiates an outgoing
connection; no incoming laptop server/firewall rule is needed.

The wire format is one newline-terminated CSV record per sample:

```text
ax_m_s2,ay_m_s2,az_m_s2,gx_rad_s,gy_rad_s,gz_rad_s,A0_normalized,A1_normalized,A2_normalized,A3_normalized,GPIO_normalized
```

By default each record has 11 fields. To append five raw ADC counts, uncomment
`// #define INCLUDE_RAW_FLEX` near the top of `capstone/capstone.ino` and upload
again. Comment it out and upload again to disable raw output. This switch applies
to both Wi-Fi and Serial, including the Serial CSV header. Raw counts come from
the same sensor acquisition as the normalized values.

The receiver and plotter automatically accept either 11 or 16 fields. Captures
always include five raw columns (`A0_raw` through `GPIO_raw`); these are empty
when raw output is disabled. The receiver adds `received_at_utc`, handles
split/coalesced TCP records, skips malformed rows, and reconnects automatically.
There are no board sequence or uptime columns in this format.

Networking runs in a separate FreeRTOS task with an eight-record queue. Slow
connections drop new records when the queue fills; reconnecting clears queued
records. This keeps sensor acquisition independent of network waits, but does
not provide lossless recording during outages. TCP data is unencrypted; use a
private AP credentials when recording sensitive data.
For USB-free operation, supply suitable board power and use the plotter's guided
calibration controls. No hardware button is required.

The TCP connection also carries `#STATUS,<stage>` lines once per second (and on
stage changes), keeping it alive while waiting or calibrating. These lines are
excluded from CSV files. Commands from the receiver are `CALIBRATE\n` and `GYRO\n`;
the firmware accepts each only in its appropriate stage. Update the receiver
alongside the firmware. The CLI without `--plot` can record an already calibrated
glove, but the plotter is required to start or repeat calibration.

### Live Wi-Fi plotter

After joining the ESP32 network, run:

```sh
python3 logging/receive_wifi.py --plot --quiet
```

Open **http://127.0.0.1:8765** in your browser. No extra packages or internet
connection are required. The page shows the following graphs:

- **IMU:** all six acceleration/gyro channels, with an automatically scaled shared
  numeric Y-axis. Acceleration is in m/s²; angular velocity is in rad/s.
- **Flex:** all five normalized channels, with the Y-axis fixed at **0–1**.
- **Raw flex:** shown automatically when raw counts are received, with an automatically scaled Y-axis.

Select a 10, 20, or 30 second scrolling window. **Pause view** freezes all graphs
while CSV recording continues. Receipt gaps of half a second or more break the traces. Connection status
is shown above the graphs; the page keeps responding while the receiver retries.
Use `--plot-port 8766` if the default local port is occupied. Stop the receiver
with Ctrl+C to end recording and the local plot server.

### Standalone wireless test

Upload `wireless/wireless.ino` to test the access point and TCP stream without
MPU or flex hardware. Join **Capstone-Glove** and run the same receiver:

```sh
python3 logging/receive_wifi.py --plot
```

This sketch immediately streams **synthetic zero sensor values** at approximately
20 Hz, with increasing sequence numbers and board timestamps. No calibration is
needed; calibration requests only print a notice on Serial. Captured CSVs are test
data, not measurements. Upload the combined sketch again to use real sensors.

To customize this sketch's network, copy `wireless/wifi_config.example.h` to
`wireless/wifi_config.h` (ignored by Git). Each sketch owns its configuration;
the shared library does not include sketch-local credential files.
