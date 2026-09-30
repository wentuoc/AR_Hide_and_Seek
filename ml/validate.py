"""Evaluate gesture model predictions at window and recording level."""

import argparse
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from utils import (
	GestureDataset,
	load_label_mapping,
	load_model,
	load_source_files,
	print_metrics,
	evaluate_recordings,
	set_seed,
	validate_device,
)


ML_DIRECTORY = Path(__file__).resolve().parent


def main():
	args = parse_arguments()
	validate_arguments(args)
	set_seed(args.seed)
	device = torch.device(args.device)
	label_to_index = load_label_mapping(args.label_map, args.manifest)
	model, label_to_index = load_model(args.weights, label_to_index, device)
	# TODO: remove separate_variants=True once enough independent recordings exist.
	loader, source_files = load_data(args, label_to_index)

	# Future work: explore stride, noise standard deviation, and augmentation copies with Optuna.
	metrics = evaluate_recordings(
		model, loader, source_files, label_to_index, device
	)
	print_metrics(metrics)


def parse_arguments():
	parser = argparse.ArgumentParser(
		description="Evaluate a trained gesture model on a validation or test manifest.",
		formatter_class=argparse.ArgumentDefaultsHelpFormatter,
	)
	parser.add_argument(
		"--manifest",
		type=Path,
		default=ML_DIRECTORY / "data" / "augmented" / "test" / "manifest.csv",
		help="Manifest containing serialized evaluation windows.",
	)
	parser.add_argument(
		"--weights",
		type=Path,
		default=ML_DIRECTORY / "outputs" / "final_weights.pt",
		help="Final weights or a full training checkpoint.",
	)
	parser.add_argument(
		"--label-map",
		type=Path,
		help="Optional JSON label-to-index mapping when weights do not contain one.",
	)
	parser.add_argument("--batch-size", type=int, default=32, help="Samples per batch.")
	parser.add_argument(
		"--num-workers",
		type=int,
		default=0,
		help="DataLoader worker processes; use 0 on Windows for simplest execution.",
	)
	parser.add_argument("--seed", type=int, default=42, help="Random seed.")
	parser.add_argument(
		"--device",
		choices=["cpu", "cuda"],
		default="cpu",
		help="Torch device used for evaluation.",
	)
	return parser.parse_args()


def validate_arguments(args):
	if args.batch_size <= 0:
		raise ValueError("batch-size must be positive")
	if args.num_workers < 0:
		raise ValueError("num-workers cannot be negative")
	if not args.manifest.is_file():
		raise FileNotFoundError(f"Missing validation manifest: {args.manifest}")
	if not args.weights.is_file():
		raise FileNotFoundError(f"Missing model weights: {args.weights}")
	validate_device(args.device)


def load_data(args, label_to_index):
	dataset = GestureDataset(args.manifest, label_to_index)
	loader = DataLoader(
		dataset,
		batch_size=args.batch_size,
		shuffle=False,
		num_workers=args.num_workers,
	)
	return loader, load_source_files(args.manifest, separate_variants=True)


if __name__ == "__main__":
	main()