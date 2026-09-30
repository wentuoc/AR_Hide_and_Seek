# Machine Learning Pipeline

This subfolder contains scripts for collecting sensor data with the Firebeetle, preprocessing and augmenting the dataset, training the machine learning model, evaluating its performance, and exporting it for use in hardware acceleration.

## Requirements and Installation

- Python 3.11.16
- Required Python packages listed in `requirements.txt`

From this directory, run:

**Windows:**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

I recommend using `uv` for python package management

## record_data.py

This script can be used to record sensor data from the FireBeetle. The data can then be cleaned and pre-processed in subsequent steps for machine learning. 

0. The FireBeetle should be sending the 11 sensor data points through the serial port at a baud rate of 115200 as comma separated values:

   `accelx, accely, accelz, gyrox, gyroy, gyroz, flex1, flex2, flex3, flex4, flex5`

1. Connect the FireBeetle to the laptop and run the sensor code from Arduino. Turn off Serial Monitor/Serial Plotter

2. Find the port that the FireBeetle is connected to

3. `cd` to the `ml` folder and run the script using

   ```bash
   python record_data.py [serial_port]
   ```

   Optionally, use the -n flag to update the number of trials per gesture (default 10):

   ```bash
   python record_data.py [-n NUM_TRIALS] [serial_port]
   ```

4. Follow the instructions in the terminal to record gestures