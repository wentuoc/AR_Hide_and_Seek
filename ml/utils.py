"""Shared data, model, training, and evaluation utilities."""

import csv
import json
import random
from collections import defaultdict
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset
from tqdm.auto import tqdm


# Runtime and reproducibility helpers

def set_seed(seed):
	"""Set the random seeds used by the training and evaluation scripts."""
	random.seed(seed)
	torch.manual_seed(seed)


def validate_device(device):
	"""Raise an error when CUDA is requested but unavailable."""
	if str(device) == "cuda" and not torch.cuda.is_available():
		raise ValueError("CUDA was requested, but CUDA is not available")


# Dataset and DataLoader helpers

class GestureDataset(Dataset):
	"""Dataset for manifest entries containing serialized 20x11 windows."""

	def __init__(self, manifest_path, label_to_index=None):
		self.manifest_path = Path(manifest_path)
		self.samples = []

		with self.manifest_path.open(newline="", encoding="utf-8") as file:
			rows = list(csv.DictReader(file))

		if not rows:
			raise ValueError(f"Manifest is empty: {self.manifest_path}")

		labels = sorted({row["label"] for row in rows})
		self.label_to_index = label_to_index or {
			label: index for index, label in enumerate(labels)
		}

		for row in rows:
			label = row["label"]
			if label not in self.label_to_index:
				raise ValueError(f"Label {label!r} is missing from label_to_index")
			self.samples.append({
				"path": self._resolve_sample_path(row["path"]),
				"label": label,
			})

	def _resolve_sample_path(self, path):
		candidate = Path(path)
		if candidate.is_absolute() and candidate.is_file():
			return candidate

		candidates = [candidate, self.manifest_path.parent / candidate]
		for resolved_path in candidates:
			if resolved_path.is_file():
				return resolved_path
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


def _labels_from_manifest(manifest_path):
	with Path(manifest_path).open(newline="", encoding="utf-8") as file:
		return {row["label"] for row in csv.DictReader(file)}


def load_label_mapping(label_map_path, manifest_path):
	"""Load a saved label mapping or derive one from a manifest."""
	if label_map_path is not None:
		with Path(label_map_path).open(encoding="utf-8") as file:
			return json.load(file)

	return {
		label: index
		for index, label in enumerate(sorted(_labels_from_manifest(manifest_path)))
	}


def load_source_files(manifest_path, separate_variants=False):
	"""Return source identifiers, optionally treating variants as separate units."""
	with Path(manifest_path).open(newline="", encoding="utf-8") as file:
		rows = csv.DictReader(file)
		if separate_variants:
			return [
				f"{row['source_file']}::{row['variant']}"
				for row in rows
			]
		return [row["source_file"] for row in rows]


def create_dataloaders(
		train_manifest="data/augmented/train/manifest.csv",
		val_manifest="data/augmented/val/manifest.csv",
		test_manifest=None,
		batch_size=32,
		num_workers=0,
		label_to_index=None,
	):
	"""Create loaders for whichever train, validation, and test manifests exist."""
	manifest_paths = {
		"train": train_manifest,
		"val": val_manifest,
		"test": test_manifest,
	}
	manifest_paths = {
		name: Path(path)
		for name, path in manifest_paths.items()
		if path is not None
	}
	if not manifest_paths:
		raise ValueError("At least one manifest must be provided")

	if label_to_index is None:
		if "train" not in manifest_paths:
			raise ValueError(
				"label_to_index is required when no training manifest is provided"
			)
		label_to_index = {
			label: index
			for index, label in enumerate(
				sorted(_labels_from_manifest(manifest_paths["train"]))
			)
		}

	for name, manifest_path in manifest_paths.items():
		unknown_labels = _labels_from_manifest(manifest_path) - set(label_to_index)
		if unknown_labels:
			raise ValueError(
				f"{name} manifest contains labels absent from the label mapping: "
				f"{sorted(unknown_labels)}"
			)

	datasets = {
		name: GestureDataset(manifest_path, label_to_index)
		for name, manifest_path in manifest_paths.items()
	}
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

class GestureCNN(nn.Module):
	"""1D CNN for 20-sample windows with 11 sensor features per sample."""

	def __init__(self, input_features=11, num_classes=5, dropout=0.3):
		super().__init__()
		self.input_features = input_features
		self.num_classes = num_classes

		self.features = nn.Sequential(
			nn.Conv1d(input_features, 32, kernel_size=5, padding=2),
			nn.BatchNorm1d(32),
			nn.ReLU(),
			nn.MaxPool1d(kernel_size=2),
			nn.Conv1d(32, 64, kernel_size=3, padding=1),
			nn.BatchNorm1d(64),
			nn.ReLU(),
			nn.MaxPool1d(kernel_size=2),
			nn.Conv1d(64, 64, kernel_size=3, padding=1),
			nn.BatchNorm1d(64),
			nn.ReLU(),
		)
		self.global_average_pool = nn.AdaptiveAvgPool1d(1)
		self.global_max_pool = nn.AdaptiveMaxPool1d(1)
		self.classifier = nn.Sequential(
			nn.Dropout(dropout),
			nn.Linear(128, 64),
			nn.ReLU(),
			nn.Linear(64, num_classes),
		)

	def forward(self, inputs):
		"""Return class logits for inputs shaped [batch, 20, 11]."""
		if inputs.ndim != 3:
			raise ValueError("inputs must have shape [batch, 20, 11]")
		if inputs.shape[1:] != (20, self.input_features):
			raise ValueError(
				f"inputs must have shape [batch, 20, {self.input_features}]"
			)

		# Conv1d expects [batch, channels, sequence_length].
		features = self.features(inputs.transpose(1, 2))
		average_features = self.global_average_pool(features).flatten(1)
		maximum_features = self.global_max_pool(features).flatten(1)
		pooled_features = torch.cat((average_features, maximum_features), dim=1)
		return self.classifier(pooled_features)

	def predict_proba(self, inputs):
		"""Return softmax probabilities without changing the training output."""
		return torch.softmax(self(inputs), dim=1)


def create_cnn_model(input_features=11, num_classes=5, dropout=0.3):
	"""Create the gesture-classification 1D CNN."""
	return GestureCNN(
		input_features=input_features,
		num_classes=num_classes,
		dropout=dropout,
	)


def load_model(weights_path, label_to_index, device):
	"""Load either a training checkpoint or standalone model weights."""
	checkpoint = torch.load(weights_path, map_location=device, weights_only=True)
	if "model_state_dict" in checkpoint:
		state_dict = checkpoint["model_state_dict"]
		checkpoint_labels = checkpoint.get("label_to_index")
		if checkpoint_labels is not None:
			label_to_index = checkpoint_labels
	else:
		state_dict = checkpoint

	model = create_cnn_model(num_classes=len(label_to_index)).to(device)
	model.load_state_dict(state_dict)
	model.eval()
	return model, label_to_index


# Training and recording-level evaluation

def run_epoch(model, loader, loss_function, device, optimizer=None, description=None):
	"""Run one training or evaluation epoch and return loss and accuracy."""
	training = optimizer is not None
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
				optimizer.zero_grad(set_to_none=True)

			logits = model(inputs)
			loss = loss_function(logits, labels)

			if training:
				loss.backward()
				optimizer.step()

			batch_size = labels.size(0)
			total_loss += loss.item() * batch_size
			correct += (logits.argmax(dim=1) == labels).sum().item()
			total_samples += batch_size

	if total_samples == 0:
		raise ValueError("DataLoader contains no samples")

	return total_loss / total_samples, correct / total_samples


def evaluate_recordings(model, loader, source_files, label_to_index, device):
	"""Evaluate windows and aggregate their predictions by source recording."""
	loss_function = nn.CrossEntropyLoss()
	window_loss, window_accuracy = run_epoch(
		model,
		loader,
		loss_function,
		device,
		description="Evaluating windows",
	)
	recordings = defaultdict(lambda: {"probabilities": [], "label": None})
	source_index = 0

	# Make a second, deterministic pass to retain per-recording probabilities.
	with torch.no_grad():
		batches = tqdm(loader, desc="Aggregating recordings", leave=False, unit="batch")
		for inputs, labels in batches:
			inputs = inputs.to(device)
			labels = labels.to(device)
			probabilities = torch.softmax(model(inputs), dim=1)
			batch_source_files = source_files[
				source_index:source_index + labels.size(0)
			]
			source_index += labels.size(0)

			for index, source_file in enumerate(batch_source_files):
				recording = recordings[source_file]
				recording["probabilities"].append(probabilities[index].cpu())
				recording["label"] = labels[index].item()

	if not recordings:
		raise ValueError("Evaluation manifest contains no samples")
	if source_index != len(source_files):
		raise ValueError("Source-file metadata does not match the DataLoader")

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
		"window_loss": window_loss,
		"window_accuracy": window_accuracy,
		"window_count": len(source_files),
		"recording_count": len(results),
		"recording_loss": recording_loss,
		"recording_accuracy": correct_count / len(results),
		"correct_count": correct_count,
		"incorrect_count": len(results) - correct_count,
		"results": results,
		"confusion_matrix": confusion_matrix,
	}


def save_checkpoint(path, epoch, model, optimizer, label_to_index, train_loss, val_loss):
	"""Save model and optimizer state for resuming or later evaluation."""
	path = Path(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	torch.save({
		"epoch": epoch,
		"model_state_dict": model.state_dict(),
		"optimizer_state_dict": optimizer.state_dict(),
		"label_to_index": label_to_index,
		"train_loss": train_loss,
		"val_loss": val_loss,
	}, path)


# Console reporting helpers

def print_epoch_metrics(
		epoch,
		total_epochs,
		train_loss,
		train_accuracy,
		val_loss,
		val_accuracy,
	):
	print(
		f"Epoch {epoch:03d}/{total_epochs:03d} | "
		f"train loss: {train_loss:.4f}, "
		f"val loss: {val_loss:.4f}, "
		f"train accuracy: {train_accuracy:.2%}, "
		f"val accuracy: {val_accuracy:.2%}"
	)


def print_metrics(metrics):
	"""Print window-level and recording-level evaluation metrics."""
	print(f"Windows evaluated: {metrics['window_count']}")
	print(f"Recordings evaluated: {metrics['recording_count']}")
	print(f"Window loss: {metrics['window_loss']:.4f}")
	print(f"Window accuracy: {metrics['window_accuracy']:.2%}")
	print(f"Recording loss: {metrics['recording_loss']:.4f}")
	print(f"Recording accuracy: {metrics['recording_accuracy']:.2%}")