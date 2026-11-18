# Ultra96 gesture and visualizer service

The Ultra96 receives encrypted glove samples, maintains a sensor window for each device and delivers gesture state to the matching Unity phone. The laptop reaches the board's loopback services through SSH.

## Start the service

Copy this entire folder to the Ultra96. Use the board's PYNQ Python environment with NumPy, cryptography and ONNX Runtime installed. From an interactive SSH terminal **inside Ultra96_Code**, run:

```bash
sudo /usr/local/share/pynq-venv/bin/python3 pipeline_visualizer.py
```

Keep the terminal open. Once the service reports its listeners, start the [laptop relay](../Laptop_Code/README.md), power on the [glove](../FireBeetle_Code/README.md) and open the compatible Unity app. Each phone connects to the laptop's LAN IP on port 5503 using the corresponding glove's device ID.

## Calibrate and test

| Ultra96 console command | Action |
| --- | --- |
| `status` | Inspect connected devices and calibration state. |
| `c 1` | Start flex calibration for glove 1. Move the fingers through their range until it completes. |
| `g 1` | Calibrate the gyroscope on glove 1. Keep the glove still. |
| `c 2`, then `g 2` | Repeat the two calibration steps for glove 2. |

Perform a gesture with one glove and check the corresponding phone. The service sends `VISUALIZER_STATE` frames and periodically prints `[VISUALIZER ACK]` when a phone acknowledges them. This checks message delivery. Check the displayed gesture separately to verify classification and player routing.

## Model and runtime files

| Files | Role |
| --- | --- |
| `live_ai.py`, `sensor_windows.py`, `model_config.json` | Build a 20-sample × 11-feature window per device. The configured stride is five samples, giving a nominal update every 250 ms at 20 Hz. |
| `pynq_driver.py`, `gesture_cnn.bit`, `gesture_cnn.hwh` | Initialize the PYNQ overlay and buffers at startup. Keep the bitstream and matching hardware description together. |
| `pynq_driver_onnx.py`, `gesture_cnn_seven.onnx`, `gesture_cnn_seven.onnx.data` | In this version, the live inference method calls ONNX Runtime for each window. Keep both model files in this directory. |
| `secure_keys.json` | Device keys for the AES-GCM glove sessions. Match the key to the device ID in each glove's firmware. |
| `phone_cert.pem`, `phone_key.pem`, `phone_server.json` | TLS certificate, server private key and phone authentication configuration. |

The class order in this archive is `click`, `gun`, `paint`, `pinch`, `resting`, `shoot`, `spawn` (IDs 0–6). The service binds to `127.0.0.1` on ports 5602 (gloves), 5603 (phones over TLS) and 5604 (control). Session logs are written under `logs/`.

If the service starts but classification fails on the first gesture window, check that ONNX Runtime is installed in the same Python environment and that the model's external data file is present. Keep live keys and tokens private when sharing the project.
