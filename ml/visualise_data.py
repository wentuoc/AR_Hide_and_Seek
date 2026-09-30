"""Visualise groups of columns from a CSV file in the data directory."""

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt


def load_csv(csv_path):
	with csv_path.open("r", newline="") as file:
		reader = csv.DictReader(file)
		if reader.fieldnames is None:
			raise ValueError("The CSV file is missing a header.")

		# expected_headers = [
		# 	"sample_index", "label",
		# 	"accel_x", "accel_y", "accel_z",
		# 	"gyro_x", "gyro_y", "gyro_z",
		# 	"flex_1", "flex_2", "flex_3", "flex_4", "flex_5",
		# ]
		# if reader.fieldnames != expected_headers:
		# 	raise ValueError(
		# 		f"Expected CSV headers {expected_headers}, "
		# 		f"but found {reader.fieldnames}."
		# 	)

		rows = list(reader)

	if not rows:
		raise ValueError("The CSV file contains no data rows.")

	labels = [row["label"] for row in rows]
	sample_indices = [int(row["sample_index"]) for row in rows]
	sensor_headers = reader.fieldnames[2:]
	values = [
		[float(row[header]) for header in sensor_headers]
		for row in rows
	]
	return labels, sample_indices, values, sensor_headers


def main():
	parser = argparse.ArgumentParser(description="Plot values from a CSV file.")
	parser.add_argument("csv_file", help="CSV filename, relative to the data directory")
	args = parser.parse_args()

	csv_path = Path(args.csv_file)
	if not csv_path.is_absolute():
		csv_path = Path(__file__).resolve().parent / "data" / csv_path

	labels, sample_indices, values, headers = load_csv(csv_path)
	groups = [
		(0, 3, "Accelero"),
		(3, 6, "Gyro"),
		(6, 11, "Flex"),
	]

	figure, axes = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
	figure.suptitle(f"Gesture: {labels[0]}")
	
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
		axis.grid(True, alpha=0.3)
		axis.legend()

	axes[-1].set_xlabel("Row")
	figure.tight_layout()
	plt.show()


if __name__ == "__main__":
	main()
