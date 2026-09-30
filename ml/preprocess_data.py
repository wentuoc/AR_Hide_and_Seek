"""Split recordings and normalize accelerometer and gyroscope data."""

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path

import torch


DATA_DIRECTORY = Path(__file__).resolve().parent / "data"
RAW_DIRECTORY = DATA_DIRECTORY / "raw"
PROCESSED_DIRECTORY = DATA_DIRECTORY / "processed"
SPLITS_DIRECTORY = DATA_DIRECTORY / "splits"
METADATA_DIRECTORY = DATA_DIRECTORY / "metadata"

CSV_HEADERS = [
	"sample_index",
	"label",
	"accel_x", "accel_y", "accel_z",
	"gyro_x", "gyro_y", "gyro_z",
	"flex_1", "flex_2", "flex_3", "flex_4", "flex_5",
]
SENSOR_COLUMNS = CSV_HEADERS[2:]
NORMALIZED_COLUMNS = SENSOR_COLUMNS[:6]
SPLIT_RATIOS = {"train": 0.70, "val": 0.15, "test": 0.15}


def discover_recordings():
	raw_recordings = sorted(RAW_DIRECTORY.rglob("*.csv"))
	if raw_recordings:
		return raw_recordings, RAW_DIRECTORY

	legacy_recordings = sorted(
		path
		for directory in DATA_DIRECTORY.iterdir()
		if directory.is_dir()
		and directory.name not in {"raw", "processed", "splits", "metadata"}
		for path in directory.rglob("*.csv")
	)
	return legacy_recordings, DATA_DIRECTORY


def read_recording(path):
	with path.open("r", newline="", encoding="utf-8") as file:
		rows = list(csv.reader(file))

	if not rows:
		raise ValueError(f"Recording is empty: {path}")

	has_header = rows[0] == CSV_HEADERS
	data_rows = rows[1:] if has_header else rows

	if not has_header:
		expected_width = len(CSV_HEADERS) - 1
		if any(len(row) != expected_width for row in data_rows):
			raise ValueError(
				f"{path} must use the new header or contain {expected_width} "
				"legacy fields per row."
			)

	records = []
	for row_number, row in enumerate(data_rows, start=2 if has_header else 1):
		if len(row) != len(CSV_HEADERS):
			if not has_header and len(row) == len(CSV_HEADERS) - 1:
				sample_index = row_number - 1
				label, sensor_fields = row[0], row[1:]
			else:
				raise ValueError(f"Invalid column count in {path}:{row_number}")
		else:
			try:
				sample_index = int(row[0])
			except ValueError as error:
				raise ValueError(f"Invalid sample_index in {path}:{row_number}") from error
			label, sensor_fields = row[1], row[2:]

		try:
			sensor_values = [float(value) for value in sensor_fields]
		except ValueError as error:
			raise ValueError(f"Non-numeric sensor value in {path}:{row_number}") from error

		if not all(math.isfinite(value) for value in sensor_values):
			raise ValueError(f"Non-finite sensor value in {path}:{row_number}")

		records.append({
			"sample_index": sample_index,
			"label": label,
			"values": sensor_values,
		})

	if not records:
		raise ValueError(f"Recording has no data rows: {path}")

	sample_indices = [record["sample_index"] for record in records]
	if sample_indices != list(range(len(records))):
		raise ValueError(f"Sample indices must be consecutive from zero: {path}")

	return records


def split_recordings(recordings, seed):
	grouped_recordings = defaultdict(list)
	for path, records in recordings.items():
		grouped_recordings[records[0]["label"]].append(path)

	generator = random.Random(seed)
	split_recordings_by_name = {name: [] for name in SPLIT_RATIOS}

	for label in sorted(grouped_recordings):
		paths = grouped_recordings[label]
		generator.shuffle(paths)
		train_count = int(len(paths) * SPLIT_RATIOS["train"])
		val_count = int(len(paths) * SPLIT_RATIOS["val"])
		train_count = max(1, train_count)
		val_count = max(1, val_count) if len(paths) >= 3 else val_count
		if train_count + val_count >= len(paths):
			val_count = max(0, len(paths) - train_count - 1)

		split_recordings_by_name["train"].extend(paths[:train_count])
		split_recordings_by_name["val"].extend(paths[train_count:train_count + val_count])
		split_recordings_by_name["test"].extend(paths[train_count + val_count:])

	return split_recordings_by_name


def fit_statistics(recordings):
	training_values = torch.tensor(
		[record["values"] for path in recordings for record in recordings[path]],
		dtype=torch.float32,
	)
	normalized_values = training_values[:, :len(NORMALIZED_COLUMNS)]
	means = normalized_values.mean(dim=0)
	standard_deviations = normalized_values.std(dim=0, unbiased=False).clamp_min(1e-8)
	return means, standard_deviations


def normalize_records(records, means, standard_deviations):
	values = torch.tensor([record["values"] for record in records], dtype=torch.float32)
	values[:, :len(NORMALIZED_COLUMNS)] = (
		values[:, :len(NORMALIZED_COLUMNS)] - means
	) / standard_deviations

	normalized_records = []
	for record, values_row in zip(records, values.tolist()):
		normalized_records.append({
			"sample_index": record["sample_index"],
			"label": record["label"],
			"values": values_row,
		})
	return normalized_records


def write_recording(path, records):
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("w", newline="", encoding="utf-8") as file:
		writer = csv.writer(file)
		writer.writerow(CSV_HEADERS)
		for record in records:
			writer.writerow([
				record["sample_index"],
				record["label"],
				*record["values"],
			])


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--seed", type=int, default=42, help="Split random seed")
	args = parser.parse_args()

	recording_paths, source_directory = discover_recordings()
	if not recording_paths:
		raise SystemExit(f"No CSV recordings found under {source_directory}")

	recordings = {path: read_recording(path) for path in recording_paths}
	split_paths = split_recordings(recordings, args.seed)
	means, standard_deviations = fit_statistics(
		{path: recordings[path] for path in split_paths["train"]}
	)

	PROCESSED_DIRECTORY.mkdir(parents=True, exist_ok=True)
	for split_name, paths in split_paths.items():
		for path in paths:
			relative_path = path.relative_to(source_directory)
			output_path = PROCESSED_DIRECTORY / split_name / relative_path
			write_recording(
				output_path,
				normalize_records(recordings[path], means, standard_deviations),
			)

	SPLITS_DIRECTORY.mkdir(parents=True, exist_ok=True)
	for split_name, paths in split_paths.items():
		manifest_path = SPLITS_DIRECTORY / f"{split_name}.csv"
		with manifest_path.open("w", newline="", encoding="utf-8") as file:
			writer = csv.writer(file)
			writer.writerow(["path", "label", "split"])
			for path in sorted(paths):
				writer.writerow([
					path.relative_to(DATA_DIRECTORY),
					recordings[path][0]["label"],
					split_name,
				])

	METADATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
	statistics = {
		"normalized_columns": NORMALIZED_COLUMNS,
		"unchanged_columns": SENSOR_COLUMNS[len(NORMALIZED_COLUMNS):],
		"mean": dict(zip(NORMALIZED_COLUMNS, means.tolist())),
		"std": dict(zip(NORMALIZED_COLUMNS, standard_deviations.tolist())),
		"split_ratios": SPLIT_RATIOS,
		"seed": args.seed,
	}
	with (METADATA_DIRECTORY / "normalization.json").open("w", encoding="utf-8") as file:
		json.dump(statistics, file, indent=2)

	print(f"Processed {len(recording_paths)} recordings")
	for split_name, paths in split_paths.items():
		print(f"{split_name}: {len(paths)} recordings")


if __name__ == "__main__":
	main()
