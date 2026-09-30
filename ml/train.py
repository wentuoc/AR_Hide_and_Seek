"""Train the gesture CNN with TensorBoard logging and checkpoints."""

import argparse
from pathlib import Path

import torch
from torch import nn
from torch.utils.tensorboard import SummaryWriter

from utils import (
	create_cnn_model,
	create_dataloaders,
	print_epoch_metrics,
	run_epoch,
	save_checkpoint,
	set_seed,
	validate_device,
)


ML_DIRECTORY = Path(__file__).resolve().parent


def main():
	args = parse_arguments()
	validate_arguments(args)
	set_seed(args.seed)
	device = torch.device(args.device)
	loaders, label_to_index = load_data(args)
	model, loss_function, optimizer = create_training_components(
		label_to_index, args, device
	)

	args.output_directory.mkdir(parents=True, exist_ok=True)
	args.log_directory.mkdir(parents=True, exist_ok=True)
	writer = SummaryWriter(log_dir=args.log_directory)
	try:
		train_model(
			model,
			loaders,
			label_to_index,
			loss_function,
			optimizer,
			device,
			args,
			writer,
		)
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
	parser.add_argument(
		"--val-manifest",
		type=Path,
		default=ML_DIRECTORY / "data" / "augmented" / "val" / "manifest.csv",
		help="Manifest containing validation windows.",
	)
	parser.add_argument("--epochs", type=int, default=50, help="Number of training epochs.")
	parser.add_argument("--batch-size", type=int, default=32, help="Samples per batch.")
	parser.add_argument("--learning-rate", type=float, default=1e-3, help="Adam learning rate.")
	parser.add_argument("--weight-decay", type=float, default=1e-4, help="Adam weight decay.")
	parser.add_argument(
		"--checkpoint-every",
		type=int,
		default=5,
		help="Save a resumable checkpoint after this many epochs.",
	)
	parser.add_argument("--seed", type=int, default=42, help="Random seed.")
	parser.add_argument(
		"--num-workers",
		type=int,
		default=0,
		help="DataLoader worker processes; use 0 on Windows for simplest execution.",
	)
	parser.add_argument(
		"--device",
		default="cpu",
		choices=["cpu", "cuda"],
		help="Torch device used for training.",
	)
	parser.add_argument(
		"--output-directory",
		type=Path,
		default=ML_DIRECTORY / "outputs",
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
	if args.epochs <= 0 or args.batch_size <= 0:
		raise ValueError("epochs and batch-size must be positive")
	if args.checkpoint_every <= 0:
		raise ValueError("checkpoint-every must be positive")
	validate_device(args.device)


def load_data(args):
	for manifest in (args.train_manifest, args.val_manifest):
		if not manifest.is_file():
			raise FileNotFoundError(f"Missing dataset manifest: {manifest}")

	return create_dataloaders(
		train_manifest=args.train_manifest,
		val_manifest=args.val_manifest,
		batch_size=args.batch_size,
		num_workers=args.num_workers,
	)


def create_training_components(label_to_index, args, device):
	model = create_cnn_model(num_classes=len(label_to_index)).to(device)
	loss_function = nn.CrossEntropyLoss()
	optimizer = torch.optim.Adam(
		model.parameters(),
		lr=args.learning_rate,
		weight_decay=args.weight_decay,
	)
	return model, loss_function, optimizer


def train_model(
		model,
		loaders,
		label_to_index,
		loss_function,
		optimizer,
		device,
		args,
		writer,
	):
	for epoch in range(1, args.epochs + 1):
		train_loss, train_accuracy = run_epoch(
			model,
			loaders["train"],
			loss_function,
			device,
			optimizer,
			description=f"Epoch {epoch}/{args.epochs} train",
		)
		val_loss, val_accuracy = run_epoch(
			model,
			loaders["val"],
			loss_function,
			device,
			description=f"Epoch {epoch}/{args.epochs} val",
		)
		log_epoch_metrics(
			writer,
			epoch,
			args.learning_rate,
			train_loss,
			train_accuracy,
			val_loss,
			val_accuracy,
		)
		print_epoch_metrics(
			epoch,
			args.epochs,
			train_loss,
			train_accuracy,
			val_loss,
			val_accuracy,
		)
		if epoch % args.checkpoint_every == 0:
			save_checkpoint(
				args.output_directory / f"checkpoint_epoch_{epoch:03d}.pt",
				epoch,
				model,
				optimizer,
				label_to_index,
				train_loss,
				val_loss,
			)

	final_weights_path = args.output_directory / "final_weights.pt"
	torch.save({
		"model_state_dict": model.state_dict(),
		"label_to_index": label_to_index,
	}, final_weights_path)
	print(f"Final weights saved to {final_weights_path}")


def log_epoch_metrics(writer, epoch, learning_rate, train_loss, train_accuracy, val_loss, val_accuracy):
	writer.add_scalar("loss/train", train_loss, epoch)
	writer.add_scalar("loss/val", val_loss, epoch)
	writer.add_scalar("accuracy/train", train_accuracy, epoch)
	writer.add_scalar("accuracy/val", val_accuracy, epoch)
	writer.add_scalar("learning_rate", learning_rate, epoch)


if __name__ == "__main__":
	main()
