"""Shared data, model, training, and evaluation utilities."""

import ast
import csv
import random
from collections import defaultdict
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset
from pytorch_ood.loss import EnergyRegularizedLoss
from tqdm.auto import tqdm

from ml.models import (
	GestureCNN,
	GestureCNN_Large,
	GestureCNN_Prelim,
	GestureCNN_Small,
)

MODEL_TYPES = {
	"standard": GestureCNN,
	"prelim": GestureCNN_Prelim,
	"small": GestureCNN_Small,
	"large": GestureCNN_Large,
}

# Runtime and reproducibility helpers

def set_seed(seed):
	"""Set the random seeds used by the training and evaluation scripts."""
	random.seed(seed)
	torch.manual_seed(seed)


# Dataset and DataLoader helpers

class GestureDataset(Dataset):
	"""Dataset for manifest entries containing serialized 20x11 windows."""

	def __init__(self, manifest_path, label_to_index=None, rows=None):
		self.manifest_path = Path(manifest_path)
		self.samples = [] # A list of (augmented) data .pt paths and their labels

		if rows is None:
			rows = read_manifest(self.manifest_path)

		labels = sorted({row["label"] for row in rows}) # Default label to class index mapping is by alphabetical order
		self.label_to_index = label_to_index or {
			label: index for index, label in enumerate(labels)
		}

		for row in rows:
			label = row["label"]
			if label not in self.label_to_index:
				print(
					f"Skipping label {label!r}: not present in label_to_index "
					f"(manifest: {self.manifest_path})"
				)
				print(
					f"Skipping label {label!r}: not present in label_to_index "
					f"(manifest: {self.manifest_path})"
				)
				continue
			self.samples.append({
				"path": self._resolve_sample_path(row["path"]),
				"label": label,
			})

	def _resolve_sample_path(self, path):
		candidates = [
			Path(path),
			self.manifest_path.parent / path,
		]
		for candidate in candidates:
			if candidate.is_file():
				return candidate
		raise FileNotFoundError(f"Could not find dataset sample: {path}")

	def __len__(self):
		return len(self.samples)

	def __getitem__(self, index):
		sample = torch.load(self.samples[index]["path"], weights_only=True)
		data = sample["data"].float()
		if data.shape != (20, 11):
			raise ValueError(
				f"Expected sample shape (20, 11), got {tuple(data.shape)}"
			)
		label = torch.tensor(
			self.label_to_index[self.samples[index]["label"]],
			dtype=torch.long,
		)
		return data, label


def read_manifest(manifest_path):
	"""Read and validate a non-empty manifest."""
	with Path(manifest_path).open(newline="", encoding="utf-8") as file:
		rows = list(csv.DictReader(file))
	if not rows:
		raise ValueError(f"Manifest is empty: {manifest_path}")
	for row in rows:
		if "label" not in row:
			raise ValueError(f"Manifest is missing a label column: {manifest_path}")
		raw_label = row["label"].strip()
		if raw_label.startswith("{") and raw_label.endswith("}"):
			try:
				labels = ast.literal_eval(raw_label)
			except (SyntaxError, ValueError):
				continue
			if isinstance(labels, (set, frozenset)):
				if len(labels) != 1:
					raise ValueError(
						f"Manifest row contains multiple labels: {manifest_path}"
					)
				row["label"] = next(iter(labels))
	return rows


def _labels_from_rows(rows):
	"""Get all the labels in rows"""
	return {row["label"] for row in rows}


def _grouped_kfold_rows(manifest_path, k_folds, seed):
	"""Partition manifest rows by source recording into deterministic folds."""
	rows = read_manifest(manifest_path)
	groups_by_label = defaultdict(lambda: defaultdict(list))
	labels_by_source = defaultdict(set)
	for row in rows:
		source_file = row.get("source_file")
		if not source_file:
			raise ValueError(
				"Grouped K-fold validation requires a source_file column"
			)
		labels_by_source[source_file].add(row["label"])
		groups_by_label[row["label"]][source_file].append(row)

	if any(len(labels) > 1 for labels in labels_by_source.values()):
		raise ValueError(
			"Each source recording must have one label for grouped K-fold validation"
		)

	groups = [
		source_file
		for groups_by_source in groups_by_label.values()
		for source_file in groups_by_source
	]
	if len(groups) < k_folds:
		raise ValueError(
			f"Cannot create {k_folds} folds from {len(groups)} source recordings"
		)

	generator = random.Random(seed)
	fold_rows = [[] for _ in range(k_folds)]
	for label in sorted(groups_by_label):
		label_groups = list(groups_by_label[label].items())
		generator.shuffle(label_groups)
		for index, (_, group_rows) in enumerate(label_groups):
			fold_rows[index % k_folds].extend(group_rows)

	return fold_rows


def load_source_files(
		manifest_path,
		separate_variants=False,
		label_to_index=None,
	):
	"""Return source identifiers, optionally treating variants as separate units."""
	rows = read_manifest(manifest_path)
	if label_to_index is not None:
		rows = [
			row for row in rows
			if row["label"] in label_to_index
		]
	if separate_variants:
		return [
			f"{row['source_file']}::{row['variant']}"
			for row in rows
		]
	return [row["source_file"] for row in rows]


def _create_manifest_datasets(manifest_paths, label_to_index):
	manifest_rows = {
		name: read_manifest(manifest_path)
		for name, manifest_path in manifest_paths.items()
	}
	if label_to_index is None:
		if "train" not in manifest_rows:
			raise ValueError(
				"label_to_index is required when no training manifest is provided"
			)
		label_to_index = {
			label: index
			for index, label in enumerate(
				sorted(_labels_from_rows(manifest_rows["train"]))
			)
		}

	for name, rows in manifest_rows.items():
		unknown_labels = _labels_from_rows(rows) - set(label_to_index)
		if unknown_labels:
			print(
				f"{name} manifest labels absent from the label mapping "
				f"(skipping them): {sorted(unknown_labels)}"
			)
			manifest_rows[name] = [
				row for row in rows
				if row["label"] in label_to_index
			]

	datasets = {
		name: GestureDataset(
			manifest_paths[name], label_to_index, rows=rows
		)
		for name, rows in manifest_rows.items()
	}
	return datasets, label_to_index


def _create_kfold_datasets(
		train_manifest,
		label_to_index,
		k_folds,
		fold_index,
		seed,
	):
	fold_rows = _grouped_kfold_rows(train_manifest, k_folds, seed)
	if label_to_index is None:
		labels = _labels_from_rows(
			[row for rows in fold_rows for row in rows]
		)
		label_to_index = {
			label: index for index, label in enumerate(sorted(labels))
		}
	all_rows = [row for rows in fold_rows for row in rows]
	unknown_labels = _labels_from_rows(all_rows) - set(label_to_index)
	if unknown_labels:
		print(
			f"K-fold manifest labels absent from the label mapping "
			f"(skipping them): {sorted(unknown_labels)}"
		)
		fold_rows = [
			[row for row in rows if row["label"] in label_to_index]
			for rows in fold_rows
		]
	train_rows = [
		row
		for index, rows in enumerate(fold_rows)
		if index != fold_index
		for row in rows
	]
	datasets = {
		"train": GestureDataset(
			train_manifest, label_to_index, rows=train_rows
		),
		"val": GestureDataset(
			train_manifest, label_to_index, rows=fold_rows[fold_index]
		),
	}
	return datasets, label_to_index


def create_dataloaders(
		train_manifest="ml/data/augmented/train/manifest.csv",
		test_manifest=None,
		batch_size=32,
		num_workers=0,
		label_to_index=None,
		k_folds=None,
		fold_index=0,
		seed=42,
	):
	"""Create loaders from manifests or one grouped K-fold train/val split."""
	if k_folds is not None:
		if k_folds < 2:
			raise ValueError("k_folds must be at least 2")
		if not 0 <= fold_index < k_folds:
			raise ValueError("fold_index must be between 0 and k_folds - 1")
		datasets, label_to_index = _create_kfold_datasets(
			train_manifest,
			label_to_index,
			k_folds,
			fold_index,
			seed,
		)
	else:
		manifest_paths = {
			"train": train_manifest,
			"test": test_manifest,
		}
		manifest_paths = {
			name: Path(path)
			for name, path in manifest_paths.items()
			if path is not None
		}
		if not manifest_paths:
			raise ValueError("At least one manifest must be provided")
		datasets, label_to_index = _create_manifest_datasets(
			manifest_paths, label_to_index
		)

	return {
		name: DataLoader(
			dataset,
			batch_size=batch_size,
			shuffle=name == "train",
			num_workers=num_workers,
		)
		for name, dataset in datasets.items()
	}, label_to_index


# Model construction and weight loading

def create_cnn_model(
		input_features=11,
		num_classes=5,
		dropout=0.3,
		model_type="standard",
	):
	"""Create a named gesture-classification 1D CNN."""
	try:
		model_class = MODEL_TYPES[model_type]
	except KeyError as error:
		supported_models = ", ".join(sorted(MODEL_TYPES))
		raise ValueError(
			f"Unknown model type {model_type!r}; "
			f"choose one of: {supported_models}"
		) from error

	print(f"Creating model of type {model_type} with {input_features} input features and {num_classes} output classes")
	return model_class(
		num_input_features=input_features,
		num_classes=num_classes,
		dropout=dropout,
	)


def load_model(weights_path, device):
	"""Load either a training checkpoint or standalone model weights."""
	checkpoint = torch.load(weights_path, map_location=device, weights_only=True)
	if "model_state_dict" in checkpoint:
		state_dict = checkpoint["model_state_dict"]
		label_to_index = checkpoint.get("label_to_index")
		model_type = checkpoint.get("model_type", "standard")
	else:
		state_dict = checkpoint
		label_to_index = None
		model_type = "standard"

	if label_to_index is None:
		raise ValueError(
			"Checkpoint does not contain label_to_index; provide it separately"
		)
	model = create_cnn_model(
		num_classes=len(label_to_index),
		model_type=model_type,
	).to(device)
	model.load_state_dict(state_dict)
	model.eval()
	return model, label_to_index, model_type


# Training and recording-level evaluation

def run_epoch(model, loader, loss_function, device, optimiser=None, description=None):
	"""Run one training or evaluation epoch and return loss and accuracy."""
	training = optimiser is not None
	model.train(training)
	total_loss = 0.0
	correct = 0
	total_samples = 0

	gradient_context = torch.enable_grad() if training else torch.no_grad()
	with gradient_context:
		batches = tqdm(loader, desc=description, leave=False, unit="batch")
		for inputs, labels in batches:
			inputs = inputs.to(device)
			labels = labels.to(device)

			if training:
				optimiser.zero_grad(set_to_none=True)

			logits = model(inputs)
			loss = loss_function(logits, labels)

			if training:
				loss.backward()
				optimiser.step()

			batch_size = labels.size(0)
			total_loss += loss.item() * batch_size
			correct += (logits.argmax(dim=1) == labels).sum().item()
			total_samples += batch_size

	if total_samples == 0:
		raise ValueError("DataLoader contains no samples")

	return total_loss / total_samples, correct / total_samples


def run_epoch_energy(
        model,
        loader,
        loss_function,
        device,
        optimiser=None,
        energy_threshold=None,
        description=None,
):
	"""Run one training or evaluation epoch and return loss and accuracy."""
	if energy_threshold is None:
		energy_threshold = -7
	training = optimiser is not None
	model.train(training)
	total_loss = 0.0
	correct = 0
	total_samples = 0

	gradient_context = torch.enable_grad() if training else torch.no_grad()
	with gradient_context:
		batches = tqdm(loader, desc=description, leave=False, unit="batch")
		for inputs, labels in batches:
			inputs = inputs.to(device)
			labels = labels.to(device)

			if training:
				optimiser.zero_grad(set_to_none=True)

			logits = model(inputs)
			loss = loss_function(logits, labels)

			if training:
				loss.backward()
				optimiser.step()

			batch_size = labels.size(0)
			total_loss += loss.item() * batch_size
			predicted_classes = logits.argmax(dim=1)
			energy_scores = -torch.logsumexp(logits, dim=1)
			predicted_classes = torch.where(
				energy_scores > energy_threshold,
				torch.full_like(predicted_classes, -1),
				predicted_classes,
			)
			correct += (predicted_classes == labels).sum().item()
			total_samples += batch_size

	if total_samples == 0:
		raise ValueError("DataLoader contains no samples")

	return total_loss / total_samples, correct / total_samples


def evaluate_recordings(model, loader, source_files, label_to_index, device):
	"""Evaluate windows and aggregate their predictions by source recording."""
	loss_function = nn.CrossEntropyLoss()
	if len(source_files) != len(loader.dataset):
		raise ValueError("Source-file metadata does not match the DataLoader")

	recordings = defaultdict(lambda: {"probabilities": [], "label": None})
	total_loss = 0.0
	correct = 0
	total_samples = 0
	with torch.no_grad():
		model.eval()
		batches = tqdm(loader, desc="Evaluating recordings", leave=False, unit="batch")
		for inputs, labels in batches:
			inputs = inputs.to(device)
			labels = labels.to(device)
			logits = model(inputs)
			loss = loss_function(logits, labels)
			probabilities = torch.softmax(logits, dim=1)

			batch_size = labels.size(0)
			total_loss += loss.item() * batch_size
			correct += (logits.argmax(dim=1) == labels).sum().item()
			total_samples += batch_size
			batch_source_files = source_files[
				total_samples - batch_size:total_samples
			]

			for index, source_file in enumerate(batch_source_files):
				recording = recordings[source_file]
				recording["probabilities"].append(probabilities[index].cpu())
				recording["label"] = labels[index].item()

	if not recordings:
		raise ValueError("Evaluation manifest contains no samples")
	if total_samples == 0:
		raise ValueError("DataLoader contains no samples")

	class_count = len(label_to_index)
	confusion_matrix = torch.zeros(
		(class_count, class_count), dtype=torch.long
	)
	results = []
	for source_file, recording in recordings.items():
		average_probabilities = torch.stack(recording["probabilities"]).mean(dim=0)
		true_label = recording["label"]
		predicted_label = average_probabilities.argmax().item()
		confidence = average_probabilities[predicted_label].item()
		confusion_matrix[true_label, predicted_label] += 1
		results.append({
			"source_file": source_file,
			"true_label": true_label,
			"predicted_label": predicted_label,
			"confidence": confidence,
			"correct": predicted_label == true_label,
		})

	correct_count = sum(result["correct"] for result in results)
	recording_loss = sum(
		-torch.log(
			torch.stack(recording["probabilities"]).mean(dim=0)[recording["label"]]
				.clamp_min(1e-8)
		).item()
		for recording in recordings.values()
	) / len(recordings)
	
	return {
		"window_loss": total_loss / total_samples,
		"window_accuracy": correct / total_samples,
		"window_count": total_samples,
		"recording_count": len(results),
		"recording_loss": recording_loss,
		"recording_accuracy": correct_count / len(results),
		"correct_count": correct_count,
		"incorrect_count": len(results) - correct_count,
		"results": results,
		"confusion_matrix": confusion_matrix
	}


def evaluate_recordings_energy(
		model,
		loader,
		source_files,
		label_to_index,
		device,
		energy_threshold=-7,
):
	"""Evaluate windows and aggregate their predictions by source recording."""
	loss_function = EnergyRegularizedLoss()
	if len(source_files) != len(loader.dataset):
		raise ValueError("Source-file metadata does not match the DataLoader")

	recordings = defaultdict(lambda: {
		"probabilities": [],
		"energy_scores": [],
		"label": None,
	})
	total_loss = 0.0
	correct = 0
	total_samples = 0
	with torch.no_grad():
		model.eval()
		batches = tqdm(loader, desc="Evaluating recordings", leave=False, unit="batch")
		for inputs, labels in batches:
			inputs = inputs.to(device)
			labels = labels.to(device)
			logits = model(inputs)
			loss = loss_function(logits, labels)
			probabilities = torch.softmax(logits, dim=1)
			predicted_classes = logits.argmax(dim=1)
			energy_scores = -torch.logsumexp(logits, dim=1)
			predicted_classes = torch.where(
				energy_scores > energy_threshold,
				torch.full_like(predicted_classes, -1),
				predicted_classes,
			)

			batch_size = labels.size(0)
			total_loss += loss.item() * batch_size
			correct += (predicted_classes == labels).sum().item()
			total_samples += batch_size
			batch_source_files = source_files[
				total_samples - batch_size:total_samples
			]

			for index, source_file in enumerate(batch_source_files):
				recording = recordings[source_file]
				recording["probabilities"].append(probabilities[index].cpu())
				recording["energy_scores"].append(energy_scores[index].cpu())
				recording["label"] = labels[index].item()

	if not recordings:
		raise ValueError("Evaluation manifest contains no samples")
	if total_samples == 0:
		raise ValueError("DataLoader contains no samples")

	class_count = len(label_to_index)
	confusion_matrix = torch.zeros(
		(class_count, class_count), dtype=torch.long
	)
	results = []
	ood_index = class_count - 1
	for source_file, recording in recordings.items():
		average_probabilities = torch.stack(recording["probabilities"]).mean(dim=0)
		average_energy = torch.stack(recording["energy_scores"]).mean().item()
		true_label = recording["label"]
		predicted_label = (
			-1
			if average_energy > energy_threshold
			else average_probabilities.argmax().item()
		)
		confidence = average_probabilities.max().item()
		true_index = ood_index if true_label == -1 else true_label
		predicted_index = ood_index if predicted_label == -1 else predicted_label
		confusion_matrix[true_index, predicted_index] += 1
		results.append({
			"source_file": source_file,
			"true_label": true_label,
			"predicted_label": predicted_label,
			"confidence": confidence,
			"energy_score": average_energy,
			"correct": predicted_label == true_label,
		})

	correct_count = sum(result["correct"] for result in results)
	id_recordings = [
		recording
		for recording in recordings.values()
		if recording["label"] != -1
	]
	recording_loss = sum(
		-torch.log(
			torch.stack(recording["probabilities"]).mean(dim=0)[recording["label"]]
				.clamp_min(1e-8)
		).item()
		for recording in id_recordings
	) / len(id_recordings) if id_recordings else 0.0

	energy_groups = {
		"id_energy": [
			result["energy_score"]
			for result in results
			if result["true_label"] != -1
		],
		"ood_energy": [
			result["energy_score"]
			for result in results
			if result["true_label"] == -1
		],
	}
	energy_statistics = {}
	for name, scores in energy_groups.items():
		if scores:
			score_tensor = torch.tensor(scores)
			energy_statistics[name] = {
				"count": len(scores),
				"mean": score_tensor.mean().item(),
				"min": score_tensor.min().item(),
				"max": score_tensor.max().item(),
				"std": score_tensor.std(unbiased=False).item(),
			}
		else:
			energy_statistics[name] = {"count": 0}

	return {
		"window_loss": total_loss / total_samples,
		"window_accuracy": correct / total_samples,
		"window_count": total_samples,
		"recording_count": len(results),
		"recording_loss": recording_loss,
		"recording_accuracy": correct_count / len(results),
		"correct_count": correct_count,
		"incorrect_count": len(results) - correct_count,
		"results": results,
		"confusion_matrix": confusion_matrix,
		"energy_threshold": energy_threshold,
		**energy_statistics,
	}


def save_checkpoint(path, epoch, model_type, model, optimiser, label_to_index, train_loss):
	"""Save model and optimiser state for resuming or later evaluation."""
	path = Path(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	torch.save({
		"epoch": epoch,
		"model_type": model_type,
		"model_state_dict": model.state_dict(),
		"optimiser_state_dict": optimiser.state_dict(),
		"label_to_index": label_to_index,
		"train_loss": train_loss,
	}, path)


# Console reporting helpers

def print_epoch_metrics(epoch, total_epochs, train_loss, train_accuracy):
	print(
		f"Epoch {epoch:03d}/{total_epochs:03d} | "
		f"train loss: {train_loss:.4f}, "
		f"train accuracy: {train_accuracy:.2%}, "
	)


def print_metrics(metrics):
	"""Print window-level and recording-level evaluation metrics."""
	print(f"Windows evaluated: {metrics['window_count']}")
	print(f"Recordings evaluated: {metrics['recording_count']}")
	print(f"Window loss: {metrics['window_loss']:.4f}")
	print(f"Window accuracy: {metrics['window_accuracy']:.2%}")
	print(f"Recording loss: {metrics['recording_loss']:.4f}")
	print(f"Recording accuracy: {metrics['recording_accuracy']:.2%}")
	if "id_energy" in metrics and "ood_energy" in metrics:
		print(f"Energy threshold: {metrics['energy_threshold']:.4f}")
		for name, key in (("In-distribution", "id_energy"), ("Out-of-distribution", "ood_energy")):
			statistics = metrics[key]
			if statistics["count"] == 0:
				print(f"{name} energy: no recordings")
				continue
			print(
				f"{name} energy: count={statistics['count']}, "
				f"mean={statistics['mean']:.4f}, "
				f"min={statistics['min']:.4f}, "
				f"max={statistics['max']:.4f}, "
				f"std={statistics['std']:.4f}"
			)