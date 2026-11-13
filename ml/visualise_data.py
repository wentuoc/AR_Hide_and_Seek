"""Visualise groups of columns from a CSV file in the data directory."""

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt


CSV_HEADERS = [
	"sample_index", "label",
	"accel_x", "accel_y", "accel_z",
	"gyro_x", "gyro_y", "gyro_z",
	"flex_1", "flex_2", "flex_3", "flex_4", "flex_0",
]
SENSOR_HEADERS = CSV_HEADERS[2:]

RAW_DATA_DIRECTORY = Path(__file__).resolve().parent / "data" / "raw"

def parse_arguments():
    parser = argparse.ArgumentParser(description="Plot values from a CSV file.")
    parser.add_argument("--file", help="CSV filename, relative to the ml/data/raw directory")
    parser.add_argument("--auto", action="store_true", help="Loop through all csv files and visualise them")
    args = parser.parse_args()
    return args


def load_csv(csv_path):
	with csv_path.open("r", newline="") as file:
		rows = list(csv.reader(file))

	if not rows:
		raise ValueError("The CSV file is empty.")

	if len(rows[0]) >= 2 and rows[0][0] == "sample_index" and rows[0][1] == "label":
		header_row = rows[0]
		if len(header_row) < 3:
			raise ValueError("The header row must include at least one sensor column.")
		data_rows = rows[1:]
		if not data_rows:
			raise ValueError("The CSV file contains no data rows.")
		if any(len(row) != len(header_row) for row in data_rows):
			raise ValueError("A headered row has an invalid number of columns.")
		labels = [row[1] for row in data_rows]
		sample_indices = [int(row[0]) for row in data_rows]
		values = [[float(value) for value in row[2:]] for row in data_rows]
		return labels, sample_indices, values, header_row[2:]

	# Legacy recordings contain label followed by the 11 sensor values.
	if any(len(row) != len(CSV_HEADERS) - 1 for row in rows):
		raise ValueError(
			"Expected the new headered format or legacy rows with "
			"one label and 11 sensor values."
		)
	labels = [row[0] for row in rows]
	sample_indices = list(range(len(rows)))
	values = [[float(value) for value in row[1:]] for row in rows]
	return labels, sample_indices, values, SENSOR_HEADERS


def main():
	args = parse_arguments()

	if args.auto:
		data_dir = RAW_DATA_DIRECTORY
		csv_paths = sorted(
			path for path in data_dir.rglob("*.csv") if "archive" not in str(path).lower()
		)
		if not csv_paths:
			raise FileNotFoundError(f"No CSV files found in {data_dir}")
		print("Controls to visualise next csv file (waxd): w = -10, a = -1, x = +10, d = +1 files")
	else:
		csv_path = Path(args.file)
		if not csv_path.is_absolute():
			csv_path = RAW_DATA_DIRECTORY / csv_path
		csv_paths = [csv_path]

	file_index = 0
	figure, axes = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
	action = {"step": None, "closed": False}

	def handle_keypress(event):
		steps = {"d": 1, "a": -1, "x": 10, "w": -10}
		if event.key in steps:
			action["step"] = steps[event.key]
		elif event.key == "q":
			action["closed"] = True

	def handle_close(event):
		action["closed"] = True
		action["step"] = None

	key_connection = figure.canvas.mpl_connect("key_press_event", handle_keypress)
	close_connection = figure.canvas.mpl_connect("close_event", handle_close)
	plt.show(block=False)

	while 0 <= file_index < len(csv_paths) and not action["closed"]:
		csv_path = csv_paths[file_index]
		labels, sample_indices, values, headers = load_csv(csv_path)
		groups = [
			(0, 3, "Accelero"),
			(3, 6, "Gyro"),
			(6, 11, "Flex"),
		]

		for axis in axes:
			axis.clear()
		figure.suptitle(f"{csv_path.name} - Gesture: {labels[0]}")

		x_values = sample_indices
		for axis, (start, end, title) in zip(axes, groups):
			for column in range(start, end):
				axis.plot(
					x_values,
					[row[column] for row in values],
					label=headers[column],
				)
			axis.set_title(title)
			axis.set_ylabel("Value")
			if title == "Flex":
				axis.set_ylim(0,1)
			axis.grid(True, alpha=0.3)
			axis.legend()
		axes[-1].set_xlabel("sample_index")
		figure.tight_layout()
		figure.canvas.draw_idle()
		figure.canvas.flush_events()

		action["step"] = None
		while action["step"] is None and not action["closed"]:
			plt.pause(0.1)
		if action["step"] is None:
			break
		file_index += action["step"]

	figure.canvas.mpl_disconnect(key_connection)
	figure.canvas.mpl_disconnect(close_connection)
	if not action["closed"]:
		plt.close(figure)

if __name__ == "__main__":
	main()
