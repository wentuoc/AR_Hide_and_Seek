"""Create fixed-length training windows and sensor augmentations."""

import argparse
import csv
import random
from pathlib import Path

import torch


CSV_HEADERS = [
	"sample_index",
	"label",
	"accel_x", "accel_y", "accel_z",
	"gyro_x", "gyro_y", "gyro_z",
	"flex_1", "flex_2", "flex_3", "flex_4", "flex_5",
]
SENSOR_COUNT = len(CSV_HEADERS) - 2
MOTION_COLUMN_COUNT = 6


def window_sequence(sequence, window_size=20, stride=10):
	"""Return overlapping windows from a [samples, sensors] tensor."""
	if window_size <= 0 or stride <= 0:
		raise ValueError("window_size and stride must be positive")
	if sequence.shape[0] < window_size:
		return torch.empty(
			(0, window_size, sequence.shape[1]),
			dtype=sequence.dtype,
			device=sequence.device,
		)

	starts = range(0, sequence.shape[0] - window_size + 1, stride)
	return torch.stack([sequence[start:start + window_size] for start in starts])


def add_random_noise(window, noise_std=0.02, generator=None):
	"""Add Gaussian noise to motion channels; leave flex channels unchanged."""
	if noise_std < 0:
		raise ValueError("noise_std must be non-negative")
	augmented = window.clone()
	noise = torch.randn(
		augmented[:, :MOTION_COLUMN_COUNT].shape,
		generator=generator,
		dtype=augmented.dtype,
		device=augmented.device,
	) * noise_std
	augmented[:, :MOTION_COLUMN_COUNT] += noise
	# augmented[:, MOTION_COLUMN_COUNT:] = augmented[:, MOTION_COLUMN_COUNT:].clamp(0.0, 1.0)
	return augmented


def apply_random_scaling(window, minimum=0.9, maximum=1.1, generator=None):
	"""Apply one random amplitude factor to the motion channels."""
	if minimum < 0 or maximum < minimum:
		raise ValueError("scaling bounds must satisfy 0 <= minimum <= maximum")
	scale = torch.empty(1, dtype=window.dtype, device=window.device).uniform_(
		minimum, maximum, generator=generator
	)
	augmented = window.clone()
	augmented[:, :MOTION_COLUMN_COUNT] *= scale
	return augmented


def randomly_mask_sensor(window, probability=0.1, generator=None):
	"""Set each motion channel to its normalized mean with a given probability."""
	if not 0 <= probability <= 1:
		raise ValueError("probability must be between 0 and 1")
	augmented = window.clone()
	mask = torch.rand(
		MOTION_COLUMN_COUNT,
		generator=generator,
		dtype=augmented.dtype,
		device=augmented.device,
	) < probability
	augmented[:, :MOTION_COLUMN_COUNT][:, mask] = 0.0
	return augmented


def augment_window(window, generator=None):
	"""Create one conservative augmented version of a normalized window."""
	augmented = add_random_noise(window, generator=generator)
	augmented = apply_random_scaling(augmented, generator=generator)
	return randomly_mask_sensor(augmented, generator=generator)


def read_processed_recording(path):
	with path.open("r", newline="", encoding="utf-8") as file:
		reader = csv.DictReader(file)
		if reader.fieldnames != CSV_HEADERS:
			raise ValueError(f"Unexpected CSV header in {path}")

		rows = list(reader)

	if not rows:
		raise ValueError(f"Recording is empty: {path}")
	labels = {row["label"] for row in rows}
	if len(labels) != 1:
		raise ValueError(f"Recording contains multiple labels: {path}")

	sequence = torch.tensor(
		[[float(row[column]) for column in CSV_HEADERS[2:]] for row in rows],
		dtype=torch.float32,
	)
	return next(iter(labels)), sequence


def save_windows(input_directory, output_directory, window_size, stride, copies, seed):
	"""Window all recordings in a split and save original/augmented tensors."""
	input_paths = sorted(input_directory.rglob("*.csv"))
	if not input_paths:
		raise ValueError(f"No processed CSV files found in {input_directory}")

	output_directory.mkdir(parents=True, exist_ok=True)
	generator = torch.Generator().manual_seed(seed)
	manifest_rows = []

	for input_path in input_paths:
		label, sequence = read_processed_recording(input_path)
		windows = window_sequence(sequence, window_size, stride)
		if len(windows) == 0:
			raise ValueError(
				f"Recording {input_path} has fewer than {window_size} samples"
			)

		source_name = input_path.stem
		for window_index, window in enumerate(windows):
			examples = [("original", window)]
			examples.extend(
				(f"augmented_{copy_index:02d}", augment_window(window, generator))
				for copy_index in range(copies)
			)
			for variant, example in examples:
				output_name = f"{source_name}_w{window_index:03d}_{variant}.pt"
				output_path = output_directory / output_name
				torch.save({
					"data": example,
					"label": label,
					"source_file": str(input_path),
					"window_index": window_index,
					"variant": variant,
				}, output_path)
				manifest_rows.append([
					str(output_path), str(input_path), label, window_index, variant,
				])

	with (output_directory / "manifest.csv").open(
		"w", newline="", encoding="utf-8"
	) as file:
		writer = csv.writer(file)
		writer.writerow(["path", "source_file", "label", "window_index", "variant"])
		writer.writerows(manifest_rows)

	return len(manifest_rows)


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--input", type=Path, default=Path("data/processed/train"), help="Input path")
	parser.add_argument("--output", type=Path, default=Path("data/augmented/train"), help="Output path")
	parser.add_argument("--window-size", type=int, default=20, help="Size of window after augmentation. Should match with model's expected input size")
	parser.add_argument("--stride", type=int, default=10, help="Stride to use when windowing")
	parser.add_argument("--copies", type=int, default=2, help="Number of augmented copies produced from one window")
	parser.add_argument("--seed", type=int, default=42)
	args = parser.parse_args()

	if args.copies < 0:
		parser.error("--copies must be non-negative")

	random.seed(args.seed)
	count = save_windows(
		args.input,
		args.output,
		args.window_size,
		args.stride,
		args.copies,
		args.seed,
	)
	print(f"Saved {count} window examples to {args.output}")


if __name__ == "__main__":
	main()
