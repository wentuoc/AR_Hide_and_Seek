"""Train the gesture CNN with TensorBoard logging and checkpoints."""

import argparse
import json
from datetime import datetime
from pathlib import Path

import torch
from torch.utils.tensorboard import SummaryWriter
from pytorch_ood.loss import EnergyRegularizedLoss

from ml.utils import (
    create_dataloaders,
    print_epoch_metrics,
    run_epoch_energy,
    save_checkpoint,
    set_seed,
    load_model
)


ML_DIRECTORY = Path(__file__).resolve().parent
MODEL_CONFIG_PATH = ML_DIRECTORY / "data" / "metadata" / "model_config.json"

def main():
    args = parse_arguments()
    validate_arguments(args)
    set_seed(args.seed)
    device = torch.device("cpu")
    training_parameters = load_training_parameters(args.optuna_study)
    model, label_to_index, model_type = load_model(args.weights, device)
    label_to_index_model = label_to_index.copy() # saving the model requres the original label_to_index (no OOD) 
    label_to_index_data = label_to_index.copy() # loading data requires OOD class

    if 'resting' in label_to_index_data.items():
        raise ValueError("Model is trained on resting class already")
    label_to_index_data["resting"] = -1

    print("Using labels: ", label_to_index_data)
    loaders, _ = create_dataloaders(
        train_manifest=args.train_manifest,
        batch_size=training_parameters["batch_size"],
        num_workers=0,
        label_to_index=label_to_index_data
    )
    loss_function = EnergyRegularizedLoss()
    optimiser = torch.optim.SGD(
        model.parameters(), 
        lr=training_parameters["learning_rate"], 
        weight_decay=training_parameters["weight_decay"], 
        momentum=0.9
    )

    args.output_directory.mkdir(parents=True, exist_ok=True)
    args.log_directory.mkdir(parents=True, exist_ok=True)
    writer = SummaryWriter(log_dir=args.log_directory)
    try:
        train_model(
            model,
            model_type,
            loaders,
            label_to_index_model,
            loss_function,
            optimiser,
            device,
            args,
            training_parameters["learning_rate"],
            training_parameters["energy_threshold"],
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
        "--weights",
        type=Path,
        default=ML_DIRECTORY / "outputs" / "final_weights.pt",
        help="Final weights from previous training stage."
    )
    parser.add_argument("--epochs", type=int, default=20, help="Number of training epochs.")
    parser.add_argument(
        "--optuna-study",
        type=Path,
        default=ML_DIRECTORY / "outputs" / "optuna_study.json",
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
        default=ML_DIRECTORY / "outputs" / "energy" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
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
    if not args.weights.is_file():
        raise FileNotFoundError(f"Missing model weights: {args.weights}")


def load_training_parameters(study_path):
    parameters = {
        "batch_size": 16,
        "learning_rate": 1e-6,
        "weight_decay": 1e-6,
        "optimiser": "SGD",
        "energy_threshold": -7
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
        energy_threshold,
        writer,
    ):
    for epoch in range(1, args.epochs + 1):
        train_loss, train_accuracy = run_epoch_energy(
            model,
            loaders["train"],
            loss_function,
            device,
            optimiser,
            energy_threshold,
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
