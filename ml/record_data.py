"""Record sensor data from the FireBeetle connected to the laptop via USB."""

import serial
import time
import csv
import math
import re
import argparse
from pathlib import Path


parser = argparse.ArgumentParser(description='Record gesture sensor data from FireBeetle connected to a system port.')
parser.add_argument('serial_port', nargs='?', default='COM5', help='serial port to use (default: COM5)')
parser.add_argument('-n', '--num_trials', type=int, default=10, help='Number of trials per gesture (default: 10)')

ARGS = parser.parse_args()
SERIAL_PORT = ARGS.serial_port

# GESTURES = ['pinch', 'release', 'enter_paint', 'select_paint', 'exit_paint', 'gun', 'shoot']
GESTURES = ['pinch', 'release', 'enter_paint', 'gun', 'shoot'] # for now
EXPECTED_SENSOR_FIELDS = 11
CSV_HEADERS = [
    'sample_index',
    'label',
    'accel_x', 'accel_y', 'accel_z',
    'gyro_x', 'gyro_y', 'gyro_z',
    'flex_1', 'flex_2', 'flex_3', 'flex_4', 'flex_5',
]

NUM_TRIALS_PER_GESTURE = ARGS.num_trials
DATA_DIRECTORY = Path(__file__).resolve().parent / "data" / "raw"

GESTURE_DESCRIPTIONS = {
    'pinch': 'From a relaxed state, perform a pinching motion when the countdown ends',
    'release': 'From a pinching state, perform a release motion when the countdown ends',
    'enter_paint': 'From a relaxed state, enter paint mode (3 fingers) when the countdown ends',
    'gun': 'Hold the gun pose when the countdown ends',
    'shoot': 'From the gun pose, perform a shooting motion when the countdown ends'
}

assert len(CSV_HEADERS) - 2 == EXPECTED_SENSOR_FIELDS, "Headers do not match number of sensor fields"

def get_dataset_number():
    highest_number = 0
    if DATA_DIRECTORY.is_dir():
        for path in DATA_DIRECTORY.iterdir():
            if re.fullmatch(r'd\d+', path.name) and path.is_dir():
                highest_number = max(highest_number, int(path.name[1:]))
    else:
        DATA_DIRECTORY.mkdir()
    return highest_number + 1

DATASET_NUMBER = get_dataset_number()

def write_to_file(data, file):
    with open(file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        writer.writerows(data)

## Main code starts here ##

(DATA_DIRECTORY / f'{DATASET_NUMBER:02d}').mkdir(parents=True, exist_ok=True)

with serial.Serial(SERIAL_PORT, baudrate=115200, timeout=0.1) as ser:
    time.sleep(2)
    number_of_runs = 0

    for gesture in GESTURES:
        # os.makedirs(os.path.join(DATA_DIRECTORY, gesture), exist_ok=True) # No need to create subfolders for now
        print(f"#####################################################")
        print(f"GESTURE: \x1b[32m{gesture}\x1b[0m")
        print(f"Description {GESTURE_DESCRIPTIONS[gesture]}")
        print(f"\x1b[31mPlease standby...\x1b[0m")
        print(f"#####################################################")
        time.sleep(5)

        for i in range(NUM_TRIALS_PER_GESTURE):
            file = DATA_DIRECTORY / f'd{DATASET_NUMBER:02d}_{number_of_runs:04d}.csv'

            print(f"Recording {gesture} gesture, trial {i}/{NUM_TRIALS_PER_GESTURE}... Standby")
            time.sleep(2)
            print("3")
            time.sleep(1)
            print("2")
            time.sleep(1)
            print("1")
            time.sleep(0.5)

            ser.reset_input_buffer()
            ser.reset_output_buffer()
            # ser.write(b'S') # Ignore start command sent to FireBeetle for now
            start_time = time.perf_counter()
            is_go_printed = False
            data = []
            sample_index = 0

            while time.perf_counter() - start_time < 1.5:
                if time.perf_counter() - start_time >= 0.5 and not is_go_printed:
                    print("Go", flush=True)
                    is_go_printed = True
                
                line = ser.readline() # Blocking for {timeout} seconds

                if line:
                    decoded_line = line.decode("utf-8", errors="replace").strip()
                    fields = [field.strip() for field in decoded_line.split(",")]

                    if len(fields) == EXPECTED_SENSOR_FIELDS:
                        try:
                            sensor_values = [float(field) for field in fields]
                        except ValueError:
                            print(f"Skipping non-numeric reading: {decoded_line!r}")
                            continue

                        if not all(math.isfinite(value) for value in sensor_values):
                            print(f"Skipping non-finite reading: {decoded_line!r}")
                            continue

                        data.append([sample_index, gesture, *sensor_values])
                        sample_index += 1
                    else:
                        print(f"Skipping malformed reading: {decoded_line!r}")

            write_to_file(data, file)
            number_of_runs += 1

    print("End")
