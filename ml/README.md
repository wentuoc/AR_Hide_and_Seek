# Machine Learning Pipeline

This subfolder contains scripts for collecting sensor data with the Firebeetle, preprocessing and augmenting the dataset, training the machine learning model, evaluating its performance, and exporting it for use in hardware acceleration.

## Requirements and Installation

- Python 3.11.16
- Required Python packages listed in `requirements.txt`

The commands below assume they are run from the repository root. `uv` is
recommended for Python package management.

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then, create the virtual environment and install the packages

**Windows (PowerShell):**
```powershell
cd .\ml
uv venv --python 3.11.16
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
```

**macOS/Linux:**
```bash
cd ml
uv venv --python 3.11.16
source .venv/bin/activate
uv pip install -r requirements.txt
```

If `uv` is not found after installation, open a new terminal so that the
updated `PATH` is loaded.

## record_data.py

This script can be used to record sensor data from the FireBeetle. The data can then be cleaned and pre-processed in subsequent steps for machine learning. 

This script automatically runs through the list of gestures and prompts the user to perform the required gesture at 3s time intervals. Each trial consists of approximately 1.5s of sampling, and the raw data received is saved as separate csv files to facilitate subsequent steps.

Each CSV file contains the header row, as well as the sample_index and label columns.

### Usage

0. The FireBeetle should be sending the 11 sensor data points over serial at
   115200 baud as comma-separated values:

   `accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z, flex_1, flex_2, flex_3, flex_4, flex_0`

1. Connect the FireBeetle to the laptop, run the sensor code from Arduino, and
   close the Serial Monitor/Serial Plotter.

2. From the repo root, run the script using the port for the connected board:

   **Windows (PowerShell):**

   ```powershell
   python -m ml.record_data COM5 -n 10
   ```

   **macOS/Linux:**

   ```bash
   python -m ml.record_data /dev/cu.usbmodemXXXX -n 10
   ```

   Replace `COM5` or `/dev/cu.usbmodemXXXX` with the actual serial port. The
   default is `COM5`, so the port must be supplied explicitly on macOS/Linux.
   Omit `-n 10` to use the default of 10 trials per gesture.

3. Follow the instructions in the terminal to record gestures.

## record_data_wifi.py

This script allows for the recording of sensor data sent from the FireBeetle over WiFi.

### Usage

1. Join the FireBeetle's Wi-Fi network and run the sensor code from Arduino.

2. From the repo root, run the script using:

   ```bash
   python -m ml.record_data_wifi -n 10 --host 192.168.4.1 --port 5000
   ```

   `--host` and `--port` default to `192.168.4.1` and `5000`, respectively.
   Omit `-n 10` to use the default of 10 trials per gesture.

## visualise_data.py

This script is primarily used for debugging and visualisation. A specified csv file (generated using `record_data.py`) is read and separate subplots show the accelerometer, gyroscope, and flex sensor readings.

### Usage

From the repository root, provide a path relative to the `ml` directory. For
example, a recording saved by `record_data.py` can be visualised with:

```text
python -m ml.visualise_data data/raw/d01/d01_0000.csv
```
