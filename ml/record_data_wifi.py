"""Record sensor data from the FireBeetle over Wi-Fi."""

import csv
import math
import os
import queue
import socket
import threading
import time
import re
import argparse
from pathlib import Path

from libs.receive_wifi import receive


parser = argparse.ArgumentParser(description='Record gesture sensor data from FireBeetle over Wi-Fi.')
parser.add_argument('--host', default='192.168.4.1', help='ESP32 access-point address')
parser.add_argument('--port', type=int, default=5000, help='ESP32 sensor TCP port')
parser.add_argument('-n', '--num_trials', type=int, default=10, help='Number of trials per gesture (default: 10)')

ARGS = parser.parse_args()

if not 1 <= ARGS.port <= 65535:
    parser.error("port must be between 1 and 65535")    

# GESTURES = ['pinch', 'release', 'enter_paint', 'select_paint', 'exit_paint', 'gun', 'shoot']
GESTURES = ['spawn', 'pinch', 'paint', 'colour', 'gun', 'shoot', 'click', 'resting'] # for now
EXPECTED_SENSOR_FIELDS = 11
CSV_HEADERS = [
    'sample_index',
    'label',
    'accel_x', 'accel_y', 'accel_z',
    'gyro_x', 'gyro_y', 'gyro_z',
    'flex_1', 'flex_2', 'flex_3', 'flex_4', 'flex_0', # N.B. flex_0 is thumb
]

NUM_TRIALS_PER_GESTURE = ARGS.num_trials
DATA_DIRECTORY = Path(__file__).resolve().parent / "data" / "raw"

GESTURE_DESCRIPTIONS = {
    'spawn': 'From a relaxed state, rapidly curl and uncurl all fingers while flicking forward to produce a pushing motion when the countdown ends', 
    'pinch': 'Hold a pinching/grabbing pose', 
    # 'pinch': 'From a relaxed state, perform a pinching motion when the countdown ends',
    # 'release': 'From a pinching state, perform a release motion when the countdown ends',
    # 'paint': 'From a relaxed state, enter paint mode (3 fingers) when the countdown ends',
    'paint': 'Hold a painting pose (3 fingers)', 
    'colour': 'From paint mode, lift and press index finger down to simulate a button click when the countdown ends',
    'gun': 'Hold a gun pose',
    'shoot': 'From the gun pose, press down the thumb to preform a shooting motion when the countdown ends',
    'click': 'From a relaxed state, perform a left click motion with the index finger',
    'resting': 'Relax your hand and do nothing'
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

def main():
    (DATA_DIRECTORY / f'd{DATASET_NUMBER:02d}').mkdir(parents=True, exist_ok=True)

    records = queue.Queue()
    with socket.create_connection((ARGS.host, ARGS.port), timeout=5) as connection:
        connection.settimeout(10)
        with open(os.devnull, "w", newline="") as receiver_output:
            receiver_writer = csv.writer(receiver_output)
            receiver_thread = threading.Thread(
                target=receive,
                args=(connection, receiver_writer, receiver_output, True),
                kwargs={"on_record": records.put},
            )
            receiver_thread.start()
            time.sleep(2)

            number_of_runs = 0

            try:
                for gesture in GESTURES:
                    print(f"#####################################################")
                    print(f"GESTURE: \x1b[32m{gesture}\x1b[0m")
                    print(f"\x1b[31m{GESTURE_DESCRIPTIONS[gesture]}\x1b[0m")
                    print(f"Please standby...")
                    print(f"#####################################################")
                    time.sleep(5)

                    for i in range(NUM_TRIALS_PER_GESTURE):
                        file = DATA_DIRECTORY / f'd{DATASET_NUMBER:02d}' / f'd{DATASET_NUMBER:02d}_{number_of_runs:04d}.csv'

                        print(f"Recording {gesture} gesture, trial {i+1}/{NUM_TRIALS_PER_GESTURE}... Standby")
                        time.sleep(2)
                        print("3")
                        time.sleep(1)
                        print("2")
                        time.sleep(1)
                        print("1")
                        time.sleep(0.5)

                        while not records.empty():
                            records.get_nowait()

                        start_time = time.perf_counter()
                        is_go_printed = False
                        data = []
                        sample_index = 0

                        while time.perf_counter() - start_time < 2:
                            if time.perf_counter() - start_time >= 0.5 and not is_go_printed:
                                print("Go", flush=True)
                                is_go_printed = True

                            try:
                                fields = records.get(timeout=0.05)
                            except queue.Empty:
                                continue

                            sensor_values = [float(field) for field in fields]
                            if not all(math.isfinite(value) for value in sensor_values):
                                continue

                            data.append([sample_index, gesture, *sensor_values])
                            sample_index += 1

                        write_to_file(data, file)
                        number_of_runs += 1
            finally:
                receiver_thread.join(timeout=1.0)
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        print("End")

if __name__ == "__main__":
    main()