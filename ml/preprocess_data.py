"""
Check that all csv file names matches their parent directory
and that values are valid (matches number of columns, finite, etc.).
Then, split recordings and normalize accelerometer and gyroscope data.
"""

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
	"flex_1", "flex_2", "flex_3", "flex_4", "flex_0",
]
SENSOR_COLUMNS = CSV_HEADERS[2:]
NORMALISED_COLUMNS = SENSOR_COLUMNS[:6]
# SPLIT_RATIOS = {"train": 0.70, "val": 0.15, "test": 0.15}
SPLIT_RATIOS = {"train": 0.85, "test": 0.15}


def parse_arguments():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--seed", type=int, default=42, help="Split random seed")
	parser.add_argument(
		"--test-set",
		type=lambda value: [item.strip() for item in value.split(",") if item.strip()],
		default=None,
		help="The datasets to be used as test set, passed as a comma-separated list",
	)
	return parser.parse_args()


def discover_recordings():
	"""Returns a list of all csv file paths in the raw directory that is not in archival"""
	raw_recordings = sorted(
		path for path in RAW_DIRECTORY.rglob("*.csv")
		if RAW_DIRECTORY / "archive" not in path.parents
	)
	if raw_recordings:
		return raw_recordings
	else:
		raise ValueError(f"No csv files found in ml/data/raw")


def rename_mismatched_recordings(recording_paths):
	"""
	Checks through each csv file path and ensures that it is of the format
	./ml/data/raw/d[xx]/d[xx]_[yyyy].csv, where xx is the dataset number
	and yyyy is the sample number
	"""
	renamed_paths = []
	for path in recording_paths:
		prefix, separator, recording_number = path.stem.partition("_")
		directory_name = path.parent.name
		if not separator or prefix == directory_name:
			renamed_paths.append(path)
			continue

		target = path.with_name(
			f"{directory_name}_{recording_number}{path.suffix}"
		)
		if target.exists() and target != path:
			raise ValueError(
				f"Cannot rename {path}: target already exists: {target}"
			)
		path.rename(target)
		renamed_paths.append(target)

	return sorted(renamed_paths)


def read_and_check_recording(path):
	"""
	Opens each csv file with the specified path and checks that the data is valid:
	- It is non-empty
	- It contains the header row
	- The number of columns matches the header row
	- The first column, sample_index is an integer
	- The sensor values are numbers
	- The sensor values are not NaN
	- The sample_indices are consecutive 

	Args:
		path: The path of a csv file
	Returns:
		records: The contents of the csv file as a list, with each element being a dict 
			containing sample_index, label, and sensor_values
	"""

	with path.open("r", newline="", encoding="utf-8") as file:
		rows = list(csv.reader(file))

	if not rows:
		raise ValueError(f"Recording is empty: {path}")

	has_header = rows[0] == CSV_HEADERS

	if not has_header:
		raise ValueError(f"Incorrect format: Recording does not contain header row")

	data_rows = rows[1:]
	records = []
	for row_number, row in enumerate(data_rows, start=2):
		if len(row) != len(CSV_HEADERS):
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

	if len(records) < 20:
		raise ValueError(f"Number of samples is less than 20: {path}")

	return records


def split_recordings(recordings, seed):
	"""
	Splits the recordings into train/val/test set according to the hardcoded ratio
	
	Returns:
		split_recordings_by_name: A dictionary consisting of paths of csv files grouped by train/val/test
	"""

	# Groups the records by their labels
	grouped_recordings = defaultdict(list)
	for path, records in recordings.items():
		grouped_recordings[records[0]["label"]].append(path)

	generator = random.Random(seed)
	split_recordings_by_name = {name: [] for name in SPLIT_RATIOS}

	for label in sorted(grouped_recordings):
		paths = grouped_recordings[label]
		generator.shuffle(paths)
		train_count = int(len(paths) * SPLIT_RATIOS["train"])
		# val_count = int(len(paths) * SPLIT_RATIOS["val"])

		split_recordings_by_name["train"].extend(paths[:train_count])
		split_recordings_by_name["test"].extend(paths[train_count:])
		# split_recordings_by_name["val"].extend(paths[train_count:train_count + val_count])
		# split_recordings_by_name["test"].extend(paths[train_count + val_count:])

	return split_recordings_by_name


def split_train_val(recordings, seed, test_set: list):
	"""
	Reserves the datasets in test_set for testing and splits the remaining datasets for training and validation
	
	Returns:
		split_recordings_by_name: A dictionary consisting of paths of csv files grouped by train/val/test
	"""

	datasets = defaultdict(list)
	for path in recordings:
		datasets[path.parent.name].append(path)

	test_datasets = set(test_set)
	unknown_datasets = test_datasets - datasets.keys()
	if unknown_datasets:
		raise ValueError(
			f"Unknown test dataset(s): {', '.join(sorted(unknown_datasets))}"
		)

	grouped_recordings = defaultdict(list)
	for path, records in recordings.items():
		if path.parent.name in test_set:
			continue
		grouped_recordings[records[0]["label"]].append(path)

	generator = random.Random(seed)
	split_recordings_by_name = {name: [] for name in SPLIT_RATIOS}

	for label in sorted(grouped_recordings):
		paths = grouped_recordings[label]
		generator.shuffle(paths)
		train_count = int(len(paths))
		# train_count = int(len(paths) * SPLIT_RATIOS["train"] / (SPLIT_RATIOS["train"] + SPLIT_RATIOS["val"]))
		# val_count = int(len(paths) * SPLIT_RATIOS["val"] / (SPLIT_RATIOS["train"] + SPLIT_RATIOS["val"]))

		split_recordings_by_name["train"].extend(paths[:train_count])
		# split_recordings_by_name["val"].extend(paths[train_count:train_count + val_count])

	for dataset_name in sorted(test_datasets):
		split_recordings_by_name["test"].extend(datasets[dataset_name])

	return split_recordings_by_name


def calculate_statistics(recordings):
	training_values = torch.tensor(
		[
			record["values"]
			for recording in recordings.values()
			for record in recording
		],
		dtype=torch.float32,
	)
	normalised_values = training_values[:, :len(NORMALISED_COLUMNS)]
	means = normalised_values.mean(dim=0)
	standard_deviations = normalised_values.std(dim=0, unbiased=False).clamp_min(1e-8)
	return means, standard_deviations


def normalise_records(records, means, standard_deviations):
	values = torch.tensor([record["values"] for record in records], dtype=torch.float32)
	values[:, :len(NORMALISED_COLUMNS)] = (
		values[:, :len(NORMALISED_COLUMNS)] - means
	) / standard_deviations

	normalised_records = []
	for record, values_row in zip(records, values.tolist()):
		normalised_records.append({
			"sample_index": record["sample_index"],
			"label": record["label"],
			"values": values_row,
		})
	return normalised_records


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
			

def write_meatadata(args, means, standard_deviations, test_set):
	statistics = {
		"normalized_columns": NORMALISED_COLUMNS,
		"unchanged_columns": SENSOR_COLUMNS[len(NORMALISED_COLUMNS):],
		"mean": dict(zip(NORMALISED_COLUMNS, means.tolist())),
		"std": dict(zip(NORMALISED_COLUMNS, standard_deviations.tolist())),
		"split_ratios": SPLIT_RATIOS,
		"seed": args.seed,
	}
	if test_set:
		statistics["split_ratios"] = f"Fixed test set: {test_set}"
	with (METADATA_DIRECTORY / "normalisation.json").open("w", encoding="utf-8") as file:
	 json.dump(statistics, file, indent=2)


def main():
	args = parse_arguments()

	recording_paths = discover_recordings()
	recording_paths = rename_mismatched_recordings(recording_paths)

	recordings = {}
	for path in recording_paths:
		try:
			recordings[path] = read_and_check_recording(path)
		except ValueError as e:
			print(e)
			
	if args.test_set is None:
		split_paths = split_recordings(recordings, args.seed)
	else:
		split_paths = split_train_val(recordings, args.seed, args.test_set)
	means, standard_deviations = calculate_statistics(
		{path: recordings[path] for path in split_paths["train"]}
	)

	PROCESSED_DIRECTORY.mkdir(parents=True, exist_ok=True)
	for split_name, paths in split_paths.items():
		for path in paths:
			relative_path = path.relative_to(RAW_DIRECTORY)
			output_path = PROCESSED_DIRECTORY / split_name / relative_path
			write_recording(
				output_path,
				normalise_records(recordings[path], means, standard_deviations),
			)

	for split_name, paths in split_paths.items():
		manifest_path = PROCESSED_DIRECTORY / f"{split_name}.csv"
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
	write_meatadata(args, means, standard_deviations, args.test_set)

	print(f"Processed {len(recordings)} recordings")
	for split_name, paths in split_paths.items():
		print(f"{split_name}: {len(paths)} recordings")

if __name__ == "__main__":
	main()
