# Meccha applications

Meccha is a two-player augmented reality hide-and-seek game. Each player wears a sensor glove and uses a phone running the Unity AR app. The four folders here contain the software that carries a player's hand movement from the glove to the game.

| Folder | Runs on | Role |
| --- | --- | --- |
| [firebeetle/](./firebeetle/) | FireBeetle ESP32 | Samples six motion values and five finger-bend values at a target of 20 Hz. Sends readings over Wi-Fi and accepts wireless calibration commands. |
| [laptop_host/](./laptop_host/) | Windows laptop | Provides a local address for the gloves and phones. Relays their TCP connections to the Ultra96 through an SSH tunnel. |
| [mobile_ar/](./mobile_ar/) | Unity AR phone | Connects to the laptop, receives gesture updates for its device ID and uses them as input to the Hider or Seeker game. |
| [ultra96/](./ultra96/) | Ultra96 board | Receives glove samples, classifies a sliding window for each player and sends the latest gesture state to the corresponding phone. |

## How a gesture reaches the game

The glove connects to the laptop on TCP port `5500`. The laptop forwards the AES-GCM-protected glove messages to the Ultra96. Each phone opens a separate TLS connection through laptop port `5503` and registers its device ID. When the Ultra96 classifies a gesture, the matching phone receives the updated state over that connection.

## Run an end-to-end check

1. Configure each glove with the local Wi-Fi credentials, the laptop's LAN IP and a distinct device ID. Flash the firmware from `firebeetle/`.
2. On the Ultra96, open a terminal in `ultra96/` and run `sudo /usr/local/share/pynq-venv/bin/python3 pipeline_visualizer.py`.
3. On the laptop, open PowerShell in `laptop_host/` and run `powershell -ExecutionPolicy Bypass -File .\Start-Live.ps1`. Complete SSH login and keep the relay running.
4. Power on the gloves. In the Ultra96 service terminal, enter `c 1` and flex the fingers. Once flex calibration finishes, hold the glove still and enter `g 1`. Repeat with `c 2` and `g 2` for the second glove. Enter `status` to check readiness.
5. Open the Unity app on each phone. Use the laptop's LAN IP, port `5503` and the device ID of the paired glove. Perform a gesture with one glove at a time and check that the corresponding phone responds.
