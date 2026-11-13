"""
Record sensor data from the FireBeetle over Wi-Fi, using
the receive function in receive_wifi.py to handle incoming data.
Save one csv per gesture per trial in a new dataset directory, 
logging the recording session to data_record_log.txt.
"""

import csv
import math
import os
import queue
import socket
import threading
import time
import re
import argparse
from datetime import datetime
from pathlib import Path

from libs.receive_wifi import receive
  

# GESTURES = ['pinch', 'release', 'enter_paint', 'select_paint', 'exit_paint', 'gun', 'shoot']
# GESTURES = ['spawn', 'pinch', 'paint', 'colour', 'gun', 'shoot', 'click', 'resting'] # for now
GESTURES = ['drop', 'grab', 'paint', 'gun', 'shoot', 'click', 'back', 'resting'] # for now
EXPECTED_SENSOR_FIELDS = 11
CSV_HEADERS = [
    'sample_index',
    'label',
    'accel_x', 'accel_y', 'accel_z',
    'gyro_x', 'gyro_y', 'gyro_z',
    'flex_1', 'flex_2', 'flex_3', 'flex_4', 'flex_0', # N.B. flex_0 is thumb
]

RAW_DATA_DIRECTORY = Path(__file__).resolve().parent / "data" / "raw"

GESTURE_DESCRIPTIONS = {
    'drop': 'Flick the hand forward while rapidly uncurling all fingers',
    'grab': 'Rapidly curl all fingers while flicking the hand up',
    'paint': 'Hold the index finger out and shake the hand left to right as if to draw',
    'gun': 'Hold a gun pose (index, middle finger and thumb)',
    'shoot': 'Flick the wrist up while holding a gun pose',
    'click': 'Double click the index and middle fingers',
    'back': 'Swipe the hand left',
    'resting': 'Relax the hand and do nothing'
    # 'spawn': 'From a relaxed state, rapidly curl and uncurl all fingers while flicking forward to produce a pushing motion when the countdown ends', 
    # 'pinch': 'Hold a pinching/grabbing pose', 
    # 'pinch': 'From a relaxed state, perform a pinching motion when the countdown ends',
    # 'release': 'From a pinching state, perform a release motion when the countdown ends',
    # 'paint': 'From a relaxed state, enter paint mode (3 fingers) when the countdown ends',
    # 'paint': 'Hold a painting pose (index finger up)', 
    # 'colour': 'From paint mode, lift and press index finger down to simulate a button click when the countdown ends',
    # 'gun': 'Hold a gun pose (thumb, index and middle finger)',
    # 'shoot': 'From the gun pose, press down the thumb to preform a shooting motion when the countdown ends',
    # 'click': 'From a relaxed state, perform a double left click motion with the index finger',
    # 'resting': 'Relax your hand and do nothing'
}

assert len(CSV_HEADERS) - 2 == EXPECTED_SENSOR_FIELDS, "Headers do not match number of sensor fields"

def parse_arguments():
    parser = argparse.ArgumentParser(description='Record gesture sensor data from FireBeetle over Wi-Fi.')
    parser.add_argument('--host', default='192.168.4.1', help='ESP32 access-point address')
    parser.add_argument('--port', type=int, default=5000, help='ESP32 sensor TCP port')
    parser.add_argument('-n', '--num_trials', type=int, default=10, help='Number of trials per gesture (default: 10)')
    parser.add_argument('--name', type=str, default='default_user', help='Name of user performing trials')

    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")  

    return args

ARGS = parse_arguments()
NUM_TRIALS_PER_GESTURE = ARGS.num_trials

def get_dataset_number():
    highest_number = 0
    if RAW_DATA_DIRECTORY.is_dir():
        for path in RAW_DATA_DIRECTORY.iterdir():
            if re.fullmatch(r'd\d+', path.name) and path.is_dir():
                highest_number = max(highest_number, int(path.name[1:]))
    else:
        RAW_DATA_DIRECTORY.mkdir()
    return highest_number + 1

def get_csv_file_count(dataset_directory):
    return sum(
        1 for path in dataset_directory.iterdir()
        if path.is_file() and path.suffix.lower() == '.csv'
    )

DATASET_NUMBER = get_dataset_number()

def write_to_file(data, file):
    with open(file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        writer.writerows(data)

def write_data_record_log(dataset_directory):
    with open(RAW_DATA_DIRECTORY / "data_record_log.txt", 'a', newline='') as f:
        f.write(f"d{DATASET_NUMBER:02d} - {datetime.now().strftime('%d%m%y-%H:%M')}, "
                f"{ARGS.name}, {NUM_TRIALS_PER_GESTURE} trials, "
                f"{get_csv_file_count(dataset_directory)} files saved, "
                f"{len(GESTURES)} classes: {GESTURES}\n")

def receive_with_reconnect(records, receiver_writer, receiver_output, stop_event, connection_holder):
    while not stop_event.is_set():
        connection = None
        try:
            connection = socket.create_connection((ARGS.host, ARGS.port), timeout=5)
            connection.settimeout(10)
            connection_holder[0] = connection
            print(f"Connected to {ARGS.host}:{ARGS.port}", flush=True)
            receive(
                connection,
                receiver_writer,
                receiver_output,
                True,
                on_record=records.put,
                stop_event=stop_event,
            )
        except OSError as exc:
            if not stop_event.is_set():
                print(f"Connection unavailable: {exc}; reconnecting in 2 seconds.", flush=True)
        finally:
            connection_holder[0] = None
            if connection is not None:
                connection.close()

        if not stop_event.is_set():
            stop_event.wait(2)

## Main code starts here ##

def main():
    dataset_directory = RAW_DATA_DIRECTORY / f'd{DATASET_NUMBER:02d}'

    records = queue.Queue()
    (dataset_directory).mkdir(parents=True, exist_ok=True)

    # Receive records in the background and queue them while discarding raw output.
    with open(os.devnull, "w", newline="") as receiver_output:
        receiver_writer = csv.writer(receiver_output)
        stop_event = threading.Event()
        connection_holder = [None]
        receiver_thread = threading.Thread(
            target=receive_with_reconnect,
            args=(records, receiver_writer, receiver_output, stop_event, connection_holder),
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
                    file = RAW_DATA_DIRECTORY / f'd{DATASET_NUMBER:02d}' / f'd{DATASET_NUMBER:02d}_{number_of_runs:04d}.csv'

                    print(f"Recording {gesture} gesture, trial {i+1}/{NUM_TRIALS_PER_GESTURE}... Standby")
                    time.sleep(1)
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

                        sensor_values = [float(field) for field in fields[:EXPECTED_SENSOR_FIELDS]] # Data stream might have more than 11 fields for debug purposes
                        if not all(math.isfinite(value) for value in sensor_values):
                            continue

                        data.append([sample_index, gesture, *sensor_values])
                        sample_index += 1

                    write_to_file(data, file)
                    number_of_runs += 1
        finally:
            write_data_record_log(dataset_directory)
            stop_event.set()
            connection = connection_holder[0]
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            receiver_thread.join()

    print("End")

if __name__ == "__main__":
    main()