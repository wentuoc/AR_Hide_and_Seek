"""Evaluate final gesture model weights and generate test statistics."""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import torch

from ml.utils import (
	create_dataloaders,
	load_model,
	load_source_files,
	print_metrics,
	evaluate_recordings_energy,
	set_seed,
)


ML_DIRECTORY = Path(__file__).resolve().parent


def main():
	args = parse_arguments()
	validate_arguments(args)
	set_seed(args.seed)
	device = torch.device("cpu")
	model, label_to_index, _ = load_model(args.weights, device)

	label_to_index_data = label_to_index.copy() # loading data requires OOD class
	
	if "resting" in label_to_index_data:
		raise ValueError("Model is trained on resting class already")
	label_to_index_data["resting"] = -1

	print("Using labels: ", label_to_index_data)
	loader, source_files = load_data(args, label_to_index_data)
	metrics = evaluate_recordings_energy(
		model,
		loader,
		source_files,
		label_to_index_data,
		device,
		energy_threshold=args.energy_threshold,
	)
	print_metrics(metrics)
	print_test_statistics(metrics, label_to_index_data)
	plot_path = args.weights.resolve().parent / "test_statistics.png"
	plot_test_statistics(metrics, label_to_index_data, plot_path)


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
	parser.add_argument("--batch-size", type=int, default=32, help="Samples per batch.")
	parser.add_argument("--seed", type=int, default=42, help="Random seed.")
	parser.add_argument(
		"--energy-threshold",
		type=float,
		default=-17,
		help="Energy score threshold used to classify out-of-distribution recordings.",
	)
	return parser.parse_args()


def validate_arguments(args):
	if args.batch_size <= 0:
		raise ValueError("batch-size must be positive")
	if not args.manifest.is_file():
		raise FileNotFoundError(f"Missing test manifest: {args.manifest}")
	if not args.weights.is_file():
		raise FileNotFoundError(f"Missing model weights: {args.weights}")


def load_data(args, label_to_index):
	loaders, _ = create_dataloaders(
		train_manifest=None,
		test_manifest=args.manifest,
		batch_size=args.batch_size,
		num_workers=0,
		label_to_index=label_to_index,
	)
	return loaders["test"], load_source_files(
		args.manifest,
		separate_variants=True,
		label_to_index=label_to_index,
	)


def print_test_statistics(metrics, label_to_index):
	index_to_label = invert_label_mapping(label_to_index)
	model_indices = sorted(index for index in index_to_label if index >= 0)
	has_ood = -1 in index_to_label
	class_indices = model_indices + ([len(model_indices)] if has_ood else [])
	class_names = [index_to_label[index] for index in model_indices]
	if has_ood:
		class_names.append(index_to_label[-1])
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
	for class_index, class_name in zip(class_indices, class_names):
		true_label = -1 if class_name == index_to_label.get(-1) else class_index
		class_results = [
			result for result in metrics["results"]
			if result["true_label"] == true_label
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


def plot_test_statistics(metrics, label_to_index, plot_path):
	index_to_label = invert_label_mapping(label_to_index)
	results = metrics["results"]
	model_indices = sorted(index for index in index_to_label if index >= 0)
	has_ood = -1 in index_to_label
	class_indices = model_indices + ([len(model_indices)] if has_ood else [])
	class_names = [index_to_label[index] for index in model_indices]
	if has_ood:
		class_names.append(index_to_label[-1])
	correct_counts = []
	incorrect_counts = []
	for class_index, class_name in zip(class_indices, class_names):
		true_label = -1 if class_name == index_to_label.get(-1) else class_index
		class_results = [
			result for result in results
			if result["true_label"] == true_label
		]
		correct_counts.append(sum(result["correct"] for result in class_results))
		incorrect_counts.append(sum(not result["correct"] for result in class_results))

	figure, axes = plt.subplots(1, 4, figsize=(24, 5))
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

	axis = axes[3]
	id_energy_scores = [
		result["energy_score"]
		for result in results
		if result["true_label"] != -1
	]
	ood_energy_scores = [
		result["energy_score"]
		for result in results
		if result["true_label"] == -1
	]
	axis.hist(
		id_energy_scores,
		bins=10,
		alpha=0.7,
		label="In-distribution",
	)
	axis.hist(
		ood_energy_scores,
		bins=10,
		alpha=0.7,
		label="Out-of-distribution",
	)
	axis.axvline(
		metrics["energy_threshold"],
		color="black",
		linestyle="--",
		label="Threshold",
	)
	axis.set_title("Energy score distribution")
	axis.set_xlabel("Energy score")
	axis.set_ylabel("Recordings")
	axis.legend()

	figure.tight_layout()
	plot_path.parent.mkdir(parents=True, exist_ok=True)
	figure.savefig(plot_path, dpi=150)
	plt.show()
	plt.close(figure)
	print(f"Test statistics plot saved to {plot_path}")


if __name__ == "__main__":
	main()
