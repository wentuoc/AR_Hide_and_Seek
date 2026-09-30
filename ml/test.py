"""Evaluate final gesture model weights and generate test statistics."""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
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
	metrics = evaluate_recordings(
		model, loader, source_files, label_to_index, device
	)
	print_metrics(metrics)
	print_test_statistics(metrics, label_to_index)
	plot_test_statistics(metrics, label_to_index, args.plot_path, args.show)


def parse_arguments():
	parser = argparse.ArgumentParser(
		description="Evaluate final gesture weights and generate test statistics and plots.",
		formatter_class=argparse.ArgumentDefaultsHelpFormatter,
	)
	parser.add_argument(
		"--manifest",
		type=Path,
		default=ML_DIRECTORY / "data" / "augmented" / "test" / "manifest.csv",
		help="Manifest containing serialized test windows.",
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
	parser.add_argument(
		"--plot-path",
		type=Path,
		default=ML_DIRECTORY / "outputs" / "test_statistics.png",
		help="Path for the generated statistics plot.",
	)
	parser.add_argument(
		"--show",
		action="store_true",
		help="Display the plot after saving it.",
	)
	return parser.parse_args()


def validate_arguments(args):
	if args.batch_size <= 0:
		raise ValueError("batch-size must be positive")
	if args.num_workers < 0:
		raise ValueError("num-workers cannot be negative")
	if not args.manifest.is_file():
		raise FileNotFoundError(f"Missing test manifest: {args.manifest}")
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


def print_test_statistics(metrics, label_to_index):
	index_to_label = invert_label_mapping(label_to_index)
	correct_results = [result for result in metrics["results"] if result["correct"]]
	incorrect_results = [result for result in metrics["results"] if not result["correct"]]
	all_confidences = [result["confidence"] for result in metrics["results"]]
	correct_confidences = [result["confidence"] for result in correct_results]
	incorrect_confidences = [result["confidence"] for result in incorrect_results]

	print("\n" + "=" * 72)
	print("TEST RESULTS")
	print("=" * 72)
	print(f"{'Metric':<32}{'Value':>20}")
	print("-" * 52)
	print(f"{'Windows evaluated':<32}{metrics['window_count']:>20}")
	print(f"{'Recordings evaluated':<32}{metrics['recording_count']:>20}")
	print(f"{'Window loss':<32}{metrics['window_loss']:>20.4f}")
	print(f"{'Window accuracy':<32}{metrics['window_accuracy']:>19.2%}")
	print(f"{'Recording loss':<32}{metrics['recording_loss']:>20.4f}")
	print(f"{'Recording accuracy':<32}{metrics['recording_accuracy']:>19.2%}")

	print("\n" + "-" * 72)
	print("INFERENCE SUMMARY")
	print("-" * 72)
	print(f"{'Metric':<32}{'Value':>20}")
	print("-" * 52)
	print(f"{'Correct inferences':<32}{metrics['correct_count']:>20}")
	print(f"{'Incorrect inferences':<32}{metrics['incorrect_count']:>20}")
	print(f"{'Overall confidence':<32}{mean_or_zero(all_confidences):>19.2%}")
	print(f"{'Correct confidence':<32}{mean_or_zero(correct_confidences):>19.2%}")
	print(f"{'Incorrect confidence':<32}{mean_or_zero(incorrect_confidences):>19.2%}")

	print("\n" + "-" * 72)
	print("PER-CLASS RESULTS")
	print("-" * 72)
	print(f"{'Class':<24}{'Correct':>12}{'Incorrect':>12}{'Accuracy':>12}")
	print("-" * 60)
	for class_index, class_name in index_to_label.items():
		class_results = [
			result for result in metrics["results"]
			if result["true_label"] == class_index
		]
		correct_count = sum(result["correct"] for result in class_results)
		incorrect_count = len(class_results) - correct_count
		accuracy = correct_count / len(class_results) if class_results else 0.0
		print(
			f"{class_name:<24}{correct_count:>12}"
			f"{incorrect_count:>12}{accuracy:>11.2%}"
		)

	print("\n" + "-" * 72)
	print("CONFUSION MATRIX (rows: true, columns: predicted)")
	print("-" * 72)
	class_names = [index_to_label[index] for index in sorted(index_to_label)]
	column_width = max(12, max(len(name) for name in class_names) + 2)
	matrix_header = "True / Pred"
	print(f"{matrix_header:<{column_width}}" + "".join(
		f"{name:>{column_width}}" for name in class_names
	))
	print("-" * (column_width * (len(class_names) + 1)))
	for row_index, class_name in enumerate(class_names):
		row_values = metrics["confusion_matrix"][row_index].tolist()
		print(f"{class_name:<{column_width}}" + "".join(
			f"{value:>{column_width}}" for value in row_values
		))


def invert_label_mapping(label_to_index):
	return {
		index: label
		for label, index in label_to_index.items()
	}


def mean_or_zero(values):
	return sum(values) / len(values) if values else 0.0


def plot_test_statistics(metrics, label_to_index, plot_path, show):
	index_to_label = invert_label_mapping(label_to_index)
	results = metrics["results"]
	class_indices = list(range(len(index_to_label)))
	class_names = [index_to_label[index] for index in class_indices]
	correct_counts = []
	incorrect_counts = []
	for class_index in class_indices:
		class_results = [
			result for result in results
			if result["true_label"] == class_index
		]
		correct_counts.append(sum(result["correct"] for result in class_results))
		incorrect_counts.append(sum(not result["correct"] for result in class_results))

	figure, axes = plt.subplots(1, 3, figsize=(18, 5))
	axis = axes[0]
	positions = torch.arange(len(class_names)).numpy()
	axis.bar(positions - 0.2, correct_counts, width=0.4, label="Correct")
	axis.bar(positions + 0.2, incorrect_counts, width=0.4, label="Incorrect")
	axis.set_xticks(positions)
	axis.set_xticklabels(class_names, rotation=45, ha="right")
	axis.set_title("Inference distribution")
	axis.set_ylabel("Recordings")
	axis.legend()

	axis = axes[1]
	correct_confidences = [result["confidence"] for result in results if result["correct"]]
	incorrect_confidences = [result["confidence"] for result in results if not result["correct"]]
	axis.hist(correct_confidences, bins=10, alpha=0.7, label="Correct")
	axis.hist(incorrect_confidences, bins=10, alpha=0.7, label="Incorrect")
	axis.set_title("Confidence distribution")
	axis.set_xlabel("Predicted confidence")
	axis.set_ylabel("Recordings")
	axis.legend()

	axis = axes[2]
	matrix = metrics["confusion_matrix"].numpy()
	image = axis.imshow(matrix, cmap="Blues")
	figure.colorbar(image, ax=axis)
	axis.set_xticks(class_indices)
	axis.set_yticks(class_indices)
	axis.set_xticklabels(class_names, rotation=45, ha="right")
	axis.set_yticklabels(class_names)
	axis.set_xlabel("Predicted label")
	axis.set_ylabel("True label")
	axis.set_title("Confusion matrix")
	for row in class_indices:
		for column in class_indices:
			axis.text(column, row, matrix[row, column], ha="center", va="center")

	figure.tight_layout()
	plot_path.parent.mkdir(parents=True, exist_ok=True)
	figure.savefig(plot_path, dpi=150)
	if show:
		plt.show()
	plt.close(figure)
	print(f"Test statistics plot saved to {plot_path}")


if __name__ == "__main__":
	main()
