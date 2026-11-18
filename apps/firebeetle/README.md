# FireBeetle glove firmware

Each glove measures wrist motion and finger bend, then sends the readings to the Ultra96 through the laptop. The firmware targets 20 samples per second. Each sample contains six MPU6050 values (acceleration and angular velocity on three axes) and five flex readings. Calibration commands travel back over the same connection.

## Configure the glove

| File | What to configure |
| --- | --- |
| `src/esp32_secure_ultra.cpp` | Set `SSID` and `PASSWORD` for the local Wi-Fi. Set `PC_IP` to the laptop's address on that network. The glove connects to TCP port 5500. |
| `src/crypto_key.h` | Set `SECURE_DEVICE_ID` to 1 or 2. Use the matching device key from the Ultra96 configuration. Give each glove a different ID. |
| `platformio.ini` | Defines the `firebeetle32` build environment, library dependencies and 115200-baud serial monitor. |

## Flash and check the connection

1. Connect the glove to the computer by USB. Open this folder (the one containing `platformio.ini`) in PlatformIO.
2. Run the following commands in the PlatformIO terminal.

   ```sh
   pio run -e firebeetle32 -t upload
   pio device monitor -b 115200
   ```

3. Start the [Ultra96 service](../Ultra96_Code/README.md) and the [laptop relay](../Laptop_Code/README.md). On the glove monitor, check that `[BOOT]` shows the expected device ID, followed by `[TCP] Connected` and `[SESSION] Keys derived`.
4. In the **Ultra96 service terminal**, enter `c 1` and move the fingers through their flex range. After flex calibration finishes, hold the glove still and enter `g 1`. Enter `status` to check readiness. Use `c 2` and `g 2` for the second glove.

The glove can run on battery after flashing. Calibration uses the wireless link. If the serial output repeats `[WIFI] Retrying...`, check the Wi-Fi credentials. If it repeatedly attempts TCP connections, check the laptop IP and whether the relay is listening on port 5500.
