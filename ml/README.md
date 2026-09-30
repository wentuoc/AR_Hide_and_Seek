# Machine Learning Pipeline

This subfolder contains scripts for collecting sensor data with the Firebeetle, preprocessing and augmenting the dataset, training the machine learning model, evaluating its performance, and exporting it for use in hardware acceleration.

## Requirements and Installation

- Python 3.11.16
- Required Python packages listed in `requirements.txt`

From this directory (`ml\`), run:

**Windows:**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

I recommend using `uv` for python package management

## record_data.py

This script can be used to record sensor data from the FireBeetle. The data can then be cleaned and pre-processed in subsequent steps for machine learning. 

This script automatically runs through the list of gestures and prompts the user to perform the required gesture at 3s time intervals. Each trial consists of approximately 1.5s of sampling, and the raw data received is saved as separate csv files to facilitate subsequent steps.

Each CSV file contains the header row, as well as the sample_index and label columns.

### Usage

0. The FireBeetle should be sending the 11 sensor data points through the serial port at a baud rate of 115200 as comma separated values:

   `accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z, flex_1, flex_2, flex_3, flex_4, flex_5`

1. Connect the FireBeetle to the laptop and run the sensor code from Arduino. Turn off Serial Monitor/Serial Plotter

2. Find the port that the FireBeetle is connected to

3. `cd` to the `ml` folder and run the script using

   ```bash
   cd .\ml
   python record_data.py [serial_port]
   ```

   Optionally, use the -n flag to update the number of trials per gesture (default 10):

   ```bash
   python record_data.py [-n NUM_TRIALS] [serial_port]
   ```

4. Follow the instructions in the terminal to record gestures

## visualise_data.py

This script is primarily used for debugging and visualisation. A specified csv file (generated using `record_data.py`) is read and separate subplots show the accelerometer, gyroscope, and flex sensor readings.

### Usage

Provide the path to the csv file as a relative path from `ml\data\`.

```bash
python visualise_data.py [csv_file]
```