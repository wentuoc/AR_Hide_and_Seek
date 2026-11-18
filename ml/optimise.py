"""
Optimise gesture CNN architectures and training hyperparameters with Optuna.
Performs k-fold cross validation on the training set.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path

import optuna
import torch
from torch import nn

from ml.models import (
    GestureCNN,
    GestureCNN_Large,
    GestureCNN_Prelim,
    GestureCNN_Small,
)
from ml.utils import create_dataloaders, run_epoch, set_seed


ML_DIRECTORY = Path(__file__).resolve().parent
MODEL_TYPES = {
    "standard": GestureCNN,
    "prelim": GestureCNN_Prelim,
    "small": GestureCNN_Small,
    "large": GestureCNN_Large,
}

# Hard-coded, final label_to_index (without resting)
label_to_index = {'back': 0, 'click': 1, 'drop': 2, 'grab': 3, 'gun': 4, 'paint': 5, 'shoot': 6}

def main():
    args = parse_arguments()
    validate_arguments(args)
    set_seed(args.seed)
    device = torch.device("cpu")

    study = optuna.create_study(
        direction="maximize",
        pruner=optuna.pruners.MedianPruner(
            n_startup_trials=5,
            n_warmup_steps=5,
        ),
    )

    study.optimize(
        lambda trial: objective(trial, args, device),
        n_trials=args.trials,
        show_progress_bar=True
    )
    
    save_study(study, args.output)
    print(f"Best validation accuracy: {study.best_value:.2%}")
    print(f"Best parameters: {study.best_params}")
    print(f"Study saved to {args.output}")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Optimise gesture CNN architecture and training hyperparameters.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--train-manifest",
        type=Path,
        default=ML_DIRECTORY / "data" / "augmented" / "train" / "manifest.csv",
        help="Path to the train manifest"
    )
    # parser.add_argument(
    #     "--val-manifest",
    #     type=Path,
    #     default=ML_DIRECTORY / "data" / "augmented" / "val" / "manifest.csv",
    # )
    parser.add_argument(
        "--output",
        type=Path,
        default=ML_DIRECTORY / "outputs" / "optuna" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
        help="Output directory"
    )
    parser.add_argument("--trials", type=int, default=30, help="Number of Optuna trials.")
    parser.add_argument("--epochs", type=int, default=30, help="Maximum training epochs per trial.")
    parser.add_argument("--folds", type=int, default=4, help="Number of grouped cross-validation folds.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    return parser.parse_args()


def validate_arguments(args):
    if args.trials <= 0 or args.epochs <= 0 or args.folds < 2:
        raise ValueError("trials and epochs must be positive, and folds must be at least 2")
    if not args.train_manifest.is_file():
        raise FileNotFoundError(f"Missing dataset manifest: {args.train_manifest}")
    # for manifest in (args.train_manifest, args.val_manifest):
    #     if not manifest.is_file():
    #         raise FileNotFoundError(f"Missing dataset manifest: {manifest}")



def objective(trial, args, device):
    global label_to_index

    # Hyperparameters for dataloaders
    batch_size = trial.suggest_categorical("batch_size", [16, 32, 64])
    fold_loaders = []
    for fold_index in range(args.folds):
        loaders, label_to_index = create_dataloaders(
            train_manifest=args.train_manifest,
            batch_size=batch_size,
            k_folds=args.folds,
            fold_index=fold_index,
            seed=args.seed,
            label_to_index=label_to_index
        )
        fold_loaders.append(loaders)

    # Hyperparameters for model
    model_name = trial.suggest_categorical("model_type", list(MODEL_TYPES))
    model_class = MODEL_TYPES[model_name]
    models = [
        model_class(num_input_features=11, num_classes=len(label_to_index)).to(device)
        for _ in fold_loaders
    ]
    loss_function = nn.CrossEntropyLoss()

    # Hyperparameters for optimiser
    optimiser_name = trial.suggest_categorical("optimiser", ["Adam", "SGD"])
    learning_rate = trial.suggest_float("learning_rate", 1e-4, 1e-2, log=True)
    weight_decay = trial.suggest_float("weight_decay", 1e-6, 1e-2, log=True)
    optimisers = []
    for model in models:
        if optimiser_name == "Adam":
            optimiser = torch.optim.Adam(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
        else:
            optimiser = torch.optim.SGD(model.parameters(), lr=learning_rate, weight_decay=weight_decay, momentum=0.9)
        optimisers.append(optimiser)

    best_val_accuracy = 0.0
    for epoch in range(args.epochs):
        fold_accuracies = []
        for fold_index, (loaders, model, optimiser) in enumerate(
            zip(fold_loaders, models, optimisers)
        ):
            run_epoch(
                model,
                loaders["train"],
                loss_function,
                device,
                optimiser,
                description=(
                    f"Trial {trial.number} fold {fold_index + 1}/{args.folds} "
                    f"epoch {epoch + 1}/{args.epochs}"
                ),
            )
            _, val_accuracy = run_epoch(
                model,
                loaders["val"],
                loss_function,
                device,
                description=(
                    f"Trial {trial.number} fold {fold_index + 1}/{args.folds} "
                    "validation"
                ),
            )
            fold_accuracies.append(val_accuracy)

        mean_val_accuracy = sum(fold_accuracies) / len(fold_accuracies)
        best_val_accuracy = max(best_val_accuracy, mean_val_accuracy)
        trial.report(mean_val_accuracy, epoch)
        if trial.should_prune():
            raise optuna.TrialPruned()

    return best_val_accuracy


def save_study(study, output_path):
    output_path = output_path.with_suffix(".json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "best_value": study.best_value,
        "best_params": study.best_params,
        "trials": [
            {
                "number": trial.number,
                "state": trial.state.name,
                "value": trial.value,
                "params": trial.params,
            }
            for trial in study.trials
        ],
    }
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()