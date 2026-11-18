"""
Create fixed-length training windows and sensor augmentations, and manifests required for training.
By default, only augments the training set. To create manifests for testing without augmenting the test
set, set --copies 0.
To produce only one window per sample, use --window-start 5 --stride 20
"""

import argparse
import csv
import random
from pathlib import Path

import torch

DATA_DIRECTORY = Path(__file__).resolve().parent / "data"
PROCESSED_DIRECTORY = DATA_DIRECTORY / "processed"
AUGMENTED_DIRECTORY = DATA_DIRECTORY / "augmented"

CSV_HEADERS = [
    "sample_index",
    "label",
    "accel_x", "accel_y", "accel_z",
    "gyro_x", "gyro_y", "gyro_z",
    "flex_1", "flex_2", "flex_3", "flex_4", "flex_0",
]
SENSOR_COUNT = len(CSV_HEADERS) - 2
MOTION_COLUMN_COUNT = 6


def parse_arguments():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter,)
    parser.add_argument("--input", type=Path, default=PROCESSED_DIRECTORY / "train", help="Input path")
    parser.add_argument("--output", type=Path, default=AUGMENTED_DIRECTORY / "train", help="Output path")
    parser.add_argument("--window-start", type=int, default=0, help="The starting sample number of the window")
    parser.add_argument("--stride", type=int, default=10, help="Stride to use when windowing")
    parser.add_argument("--copies", type=int, default=2, help="Number of augmented copies produced from one window")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if args.copies < 0:
        raise ValueError("The number of copies cannot be negative")

    return args


def window_sequence(sequence, window_size=20, stride=10, start=0):
    """
    Return overlapping windows from a [samples, sensors] tensor.
    
    The sample indices of the windows will be:
    start           : start + window_size
    start + stride  : start + stride + window_size
    etc.
    """
    if window_size <= 0 or stride <= 0:
        raise ValueError("window_size and stride must be positive")
    if sequence.shape[0] < window_size:
        return torch.empty(
            (0, window_size, sequence.shape[1]),
            dtype=sequence.dtype,
            device=sequence.device,
        )

    starts = range(start, sequence.shape[0] - window_size + 1, stride)
    return torch.stack([sequence[start:start + window_size] for start in starts])


def add_random_noise(window, noise_std=0.02, flex_offset_std=0.01, generator=None):
    """Add motion noise and fixed per-channel offsets to flex channels."""
    if noise_std < 0:
        raise ValueError("noise_std must be non-negative")
    if flex_offset_std < 0:
        raise ValueError("flex_offset_std must be non-negative")
    augmented = window.clone()
    noise = torch.randn(
        augmented[:, :MOTION_COLUMN_COUNT].shape,
        generator=generator,
        dtype=augmented.dtype,
        device=augmented.device,
    ) * noise_std
    augmented[:, :MOTION_COLUMN_COUNT] += noise

    flex_offsets = torch.randn(
        augmented[:, MOTION_COLUMN_COUNT:].shape[1:],
        generator=generator,
        dtype=augmented.dtype,
        device=augmented.device,
    ) * flex_offset_std
    augmented[:, MOTION_COLUMN_COUNT:] += flex_offsets
    augmented[:, MOTION_COLUMN_COUNT:] = augmented[:, MOTION_COLUMN_COUNT:].clamp(0.0, 1.0)
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


def randomly_mask_sensor(window, probability=0.05, generator=None):
    """Set each motion channel to its normalized mean with a given probability."""
    if not 0 <= probability <= 1:
        raise ValueError("probability must be between 0 and 1")
    augmented = window.clone()
    mask = torch.rand(
        SENSOR_COUNT,
        generator=generator,
        dtype=augmented.dtype,
        device=augmented.device,
    ) < probability
    augmented[:, :][:, mask] = 0.0
    return augmented


def augment_window(window, generator=None):
    """Create one conservative augmented version of a normalized window."""
    augmented = add_random_noise(window, generator=generator)
    augmented = apply_random_scaling(augmented, generator=generator)
    return randomly_mask_sensor(augmented, generator=generator)


def read_processed_recording(path):
    with path.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    labels = {row["label"] for row in rows}
    if len(labels) != 1:
        raise ValueError(f"Recording must contain exactly one label: {path}")
    label = next(iter(labels))
    sequence = torch.tensor(
        [[float(row[column]) for column in CSV_HEADERS[2:]] for row in rows],
        dtype=torch.float32,
    )
    return label, sequence


def discover_recordings(input_directory):
    """Returns a list of all csv file paths in the raw directory that is not in archival"""
    input_paths = sorted(input_directory.rglob("*.csv"))
    if input_paths:
        return input_paths
    else:
        raise ValueError(f"No csv files found in {input_directory}")
    

def save_example(input_path, label, window_index, variant, example, output_path):
    torch.save({
                    "data": example,
                    "label": label,
                    "source_file": str(input_path),
                    "window_index": window_index,
                    "variant": variant,
                }, output_path)


def save_manifest(output_directory, manifest_rows):
    with (output_directory / "manifest.csv").open(
        "w", newline="", encoding="utf-8"
    ) as file:
     writer = csv.writer(file)
     writer.writerow(["path", "source_file", "label", "window_index", "variant"])
     writer.writerows(manifest_rows)


def main():
    args = parse_arguments()

    random.seed(args.seed)

    input_paths = discover_recordings(args.input)

    args.output.mkdir(parents=True, exist_ok=True)
    generator = torch.Generator().manual_seed(args.seed)
    manifest_rows = []

    count = 0

    for input_path in input_paths:
        label, sequence = read_processed_recording(input_path)
        windows = window_sequence(sequence, stride=args.stride, start=args.window_start)
        
        source_name = input_path.stem
        for window_index, window in enumerate(windows):
            examples = [("original", window)]
            examples.extend(
                (f"augmented_{copy_index:02d}", augment_window(window, generator))
                for copy_index in range(args.copies)
            )
            for variant, example in examples:
                output_name = f"{source_name}_w{window_index:03d}_{variant}.pt"
                output_path = args.output / output_name
                save_example(input_path, label, window_index, variant, example, output_path)
                manifest_rows.append([
                    str(output_path), str(input_path), label, window_index, variant,
                ])
                count += 1

    save_manifest(args.output, manifest_rows)
    
    print(f"Saved {count} window examples to {args.output}")


if __name__ == "__main__":
    main()
