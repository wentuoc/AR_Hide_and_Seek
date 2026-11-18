"""Train the gesture CNN with TensorBoard logging and checkpoints."""

import argparse
import json
from datetime import datetime
from pathlib import Path

import torch
from torch import nn
from torch.utils.tensorboard import SummaryWriter

from ml.utils import (
	create_cnn_model,
	create_dataloaders,
	print_epoch_metrics,
	run_epoch,
	save_checkpoint,
	set_seed,
)


ML_DIRECTORY = Path(__file__).resolve().parent
MODEL_CONFIG_PATH = ML_DIRECTORY / "data" / "metadata" / "model_config.json"

# Hard-coded, final label_to_index
# label_to_index = {'back': 0, 'click': 1, 'drop': 2, 'grab': 3, 'gun': 4, 'paint': 5, 'shoot': 6, 'resting': 7}
label_to_index = {'back': 0, 'click': 1, 'drop': 2, 'grab': 3, 'gun': 4, 'paint': 5, 'shoot': 6}


def main():
	global label_to_index
	args = parse_arguments()
	validate_arguments(args)
	set_seed(args.seed)
	device = torch.device("cpu")
	training_parameters = load_training_parameters(args.optuna_study)
	loaders, label_to_index = create_dataloaders(
		train_manifest=args.train_manifest,
		batch_size=training_parameters["batch_size"],
		num_workers=0,
		label_to_index=label_to_index
	)
	model, loss_function, optimiser = create_training_components(label_to_index, training_parameters, device)

	args.output_directory.mkdir(parents=True, exist_ok=True)
	args.log_directory.mkdir(parents=True, exist_ok=True)
	save_training_parameters(
		args.output_directory / "training_parameters.json",
		args,
		training_parameters,
		label_to_index,
	)
	writer = SummaryWriter(log_dir=args.log_directory)
	try:
		train_model(
			model,
			training_parameters["model_type"],
			loaders,
			label_to_index,
			loss_function,
			optimiser,
			device,
			args,
			training_parameters["learning_rate"],
			writer,
		)
		write_model_config(label_to_index)
	finally:
		writer.close()


def parse_arguments():
	parser = argparse.ArgumentParser(
		description="Train the gesture CNN and save checkpoints and TensorBoard logs.",
		formatter_class=argparse.ArgumentDefaultsHelpFormatter,
	)
	parser.add_argument(
		"--train-manifest",
		type=Path,
		default=ML_DIRECTORY / "data" / "augmented" / "train" / "manifest.csv",
		help="Manifest containing training windows.",
	)
	parser.add_argument("--epochs", type=int, default=50, help="Number of training epochs.")
	parser.add_argument(
		"--optuna-study",
		type=Path,
		default=None,
		help="Optional Optuna study JSON containing best training parameters.",
	)
	parser.add_argument(
		"--checkpoint-every",
		type=int,
		default=5,
		help="Save a resumable checkpoint after this many epochs.",
	)
	parser.add_argument("--seed", type=int, default=42, help="Random seed.")
	parser.add_argument(
		"--output-directory",
		type=Path,
		default=ML_DIRECTORY / "outputs" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
		help="Directory for checkpoints and final weights.",
	)
	parser.add_argument(
		"--log-directory",
		type=Path,
		default=ML_DIRECTORY / "runs" / "gesture_cnn",
		help="TensorBoard event-log directory.",
	)
	return parser.parse_args()


def validate_arguments(args):
	if args.epochs <= 0:
		raise ValueError("epochs must be positive")
	if args.checkpoint_every <= 0:
		raise ValueError("checkpoint-every must be positive")


def load_training_parameters(study_path):
	parameters = {
		"batch_size": 32,
		"learning_rate": 1e-3,
		"weight_decay": 1e-4,
		"optimiser": "Adam",
		"model_type": "standard",
	}
	if study_path is None:
		return parameters
	if not study_path.is_file():
		raise FileNotFoundError(f"Missing Optuna study: {study_path}")

	with study_path.open(encoding="utf-8") as file:
		study = json.load(file)
	best_parameters = study.get("best_params")
	if not isinstance(best_parameters, dict):
		raise ValueError(
			f"Optuna study does not contain a valid best_params object: {study_path}"
		)
	parameters.update({
		key: best_parameters[key]
		for key in parameters
		if key in best_parameters
	})
	if parameters["batch_size"] <= 0:
		raise ValueError("Optuna batch_size must be positive")
	if parameters["learning_rate"] <= 0 or parameters["weight_decay"] < 0:
		raise ValueError("Optuna learning_rate must be positive and weight_decay non-negative")
	if parameters["optimiser"] not in {"Adam", "SGD"}:
		raise ValueError(f"Unsupported optimiser: {parameters['optimiser']}")
	return parameters


def create_training_components(label_to_index, training_parameters, device):
	model = create_cnn_model(
		num_classes=len(label_to_index),
		model_type=training_parameters["model_type"],
	).to(device)
	loss_function = nn.CrossEntropyLoss()
	if training_parameters["optimiser"] == "Adam":
		optimiser = torch.optim.Adam(
			model.parameters(),
			lr=training_parameters["learning_rate"],
			weight_decay=training_parameters["weight_decay"],
		)
	else:
		optimiser = torch.optim.SGD(
			model.parameters(),
			lr=training_parameters["learning_rate"],
			weight_decay=training_parameters["weight_decay"],
			momentum=0.9,
		)
	return model, loss_function, optimiser


def save_training_parameters(output_path, args, training_parameters, label_to_index):
	"""Save the resolved model and training configuration as JSON."""
	parameters = {
		"seed": args.seed,
		"epochs": args.epochs,
		"checkpoint_every": args.checkpoint_every,
		"train_manifest": str(args.train_manifest),
		"optuna_study": str(args.optuna_study),
		"log_directory": str(args.log_directory),
		"output_directory": str(args.output_directory),
		"label_to_index": label_to_index,
		"loss_function": "CrossEntropyLoss",
		**training_parameters,
	}
	output_path.parent.mkdir(parents=True, exist_ok=True)
	with output_path.open("w", encoding="utf-8") as file:
		json.dump(parameters, file, indent=2)
	print(f"Training parameters saved to {output_path}")


def write_model_config(label_to_index):
	"""Write runtime configuration using the trained labels and normalization."""

	normalisation_path = ML_DIRECTORY / "data" / "metadata" / "normalisation.json"
	if not normalisation_path.is_file():
		raise FileNotFoundError(f"Missing normalization metadata: {normalisation_path}")
	with normalisation_path.open(encoding="utf-8") as file:
		normalization = json.load(file)

	mean = normalization["mean"]
	std = normalization["std"]
	channel_names = {
		"accel_x": "ax", "accel_y": "ay", "accel_z": "az",
		"gyro_x": "gx", "gyro_y": "gy", "gyro_z": "gz",
	}
	statistics = {
		channel_names[column]: (mean[column], std[column])
		for column in channel_names
	}
	ultra96_feature_order = ["ax", "ay", "az", "gx", "gy", "gz", "flex0", "flex1", "flex2", "flex3", "flex4"] # different from training logs!
	model_config = {
		"confirmed": True,
		"sample_period_ms": 50,
		"tolerance_ms": 15,
		"stride": 5,
		"feature_order": ultra96_feature_order,
		"offset": [statistics.get(feature, (0, 1))[0] for feature in ultra96_feature_order],
		"divisor": [statistics.get(feature, (0, 1))[1] for feature in ultra96_feature_order],
		"labels": [
			label
			for label, _ in sorted(label_to_index.items(), key=lambda item: item[1])
		],
		"gesture_ids": [
			index
			for _, index in sorted(label_to_index.items(), key=lambda item: item[1])
		],
		"notes": (
			"AI owner must confirm feature order, training sample rate and "
			"normalization. Current offset/divisor are placeholders. For "
			"non-affine preprocessing, update prepare() in live_ai.py."
		),
	}
	MODEL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
	with MODEL_CONFIG_PATH.open("w", encoding="utf-8") as file:
		json.dump(model_config, file, indent=2)
	print(f"Model configuration saved to {MODEL_CONFIG_PATH}")


def train_model(
		model,
		model_type,
		loaders,
		label_to_index,
		loss_function,
		optimiser,
		device,
		args,
		learning_rate,
		writer,
	):
	for epoch in range(1, args.epochs + 1):
		train_loss, train_accuracy = run_epoch(
			model,
			loaders["train"],
			loss_function,
			device,
			optimiser,
			description=f"Epoch {epoch}/{args.epochs} train",
		)
		log_epoch_metrics(
			writer,
			epoch,
			learning_rate,
			train_loss,
			train_accuracy,
		)
		print_epoch_metrics(
			epoch,
			args.epochs,
			train_loss,
			train_accuracy,
		)
		if epoch % args.checkpoint_every == 0:
			save_checkpoint(
				args.output_directory / f"checkpoint_epoch_{epoch:03d}.pt",
				epoch,
				model_type,
				model,
				optimiser,
				label_to_index,
				train_loss,
			)

	final_weights_path = args.output_directory / "final_weights.pt"
	torch.save({
		"model_state_dict": model.state_dict(),
		"label_to_index": label_to_index,
		"model_type": model_type
	}, final_weights_path)
	
	print(f"Final weights saved to {final_weights_path}")


def log_epoch_metrics(writer, epoch, learning_rate, train_loss, train_accuracy):
	writer.add_scalar("loss/train", train_loss, epoch)
	writer.add_scalar("accuracy/train", train_accuracy, epoch)
	writer.add_scalar("learning_rate", learning_rate, epoch)


if __name__ == "__main__":
	main()
