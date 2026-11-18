"""Run live inference on the Wi-Fi data stream."""

import argparse
import csv
import json
import os
import socket
import time
from collections import deque
from pathlib import Path

import matplotlib.pyplot as plt
import torch

from libs.receive_wifi import receive
from ml.utils import load_model

ML_DIRECTORY = Path(__file__).resolve().parent
NORMALISATION_FILE = ML_DIRECTORY / "data" / "metadata" / "normalisation.json"

WINDOW_SIZE = 20
STRIDE = 5


def main():
    args = parse_arguments()

    weights = args.weights
    device = torch.device("cpu")
    model, label_to_index, _ = load_model(weights, device)
    normalisation = load_normalisation_values()
    print(f"Using normalisation values {normalisation}")
    print(f"Using model at {weights}")

    samples = deque(maxlen=WINDOW_SIZE)
    print(f"Listening on {args.host}:{args.port}; using {weights}")
    figure, axes, lines, current_class = initialise_plot()

    def update_plot():
        values = list(samples)
        if not values:
            return
        x_values = list(range(len(values)))
        for axis, group_lines, feature_indexes in (
            (axes[0], lines[0], range(3)),
            (axes[1], lines[1], range(3, 6)),
            (axes[2], lines[2], range(6, 11)),
        ):
            for index, line in zip(feature_indexes, group_lines):
                line.set_data(x_values, [sample[index] for sample in values])
            axis.set_xlim(0, max(WINDOW_SIZE - 1, len(values) - 1))
            axis.relim()
            if axis == axes[2]:
                axis.autoscale_view(scalex=False, scaley=False)
                axis.set_ylim(0, 1)
                axis.legend(loc='right')
            else:
                axis.autoscale_view(scalex=False, scaley=True)
            axis.set_title(current_class)
        figure.canvas.draw_idle()
        figure.canvas.flush_events()
        plt.pause(0.001)

    def on_record(fields):
        nonlocal current_class
        samples.append([float(field) for field in fields[:11]])
        if len(samples) < WINDOW_SIZE:
            return
        try:
            prediction = infer(model, list(samples), normalisation)
            scores = torch.as_tensor(prediction, dtype=torch.float32).flatten()
            argmax = int(torch.argmax(scores).item())
            confidence = float(torch.softmax(scores, dim=0)[argmax].item())
            class_name = next(
                (label for label, index in label_to_index.items() if int(index) == argmax),
                str(argmax),
            )
            current_class = class_name
            if confidence < 0.9: 
                class_name = 'Ignore'
                current_class = 'Ignore'
            update_plot()
            print(
                f"argmax={argmax} confidence={confidence:.4f} class={class_name}",
                flush=True,
            )
        except (RuntimeError, ValueError) as error:
            print(f"Inference error: {error}", flush=True)
        for _ in range(min(STRIDE, len(samples))):
            samples.popleft()

    with open(os.devnull, "w", newline="") as output:
        writer = csv.writer(output)
        while True:
            try:
                connection = socket.create_connection((args.host, args.port), timeout=5)
            except OSError as error:
                print(f"Connection unavailable: {error}; retrying in 2 seconds.", flush=True)
                time.sleep(2)
                continue
            print(f"Connected to {args.host}:{args.port}", flush=True)
            with connection:
                connection.settimeout(10)
                receive(connection, writer, output, True, on_record=on_record)
            print("Disconnected; reconnecting in 2 seconds.", flush=True)
            time.sleep(2)


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default='192.168.4.1', help='ESP32 access-point address')
    parser.add_argument("--port", type=int, default=5000, help='ESP32 sensor TCP port')
    parser.add_argument("--weights", type=Path, required=True, help='File path of the weights')
    return parser.parse_args()


def load_normalisation_values():
    with NORMALISATION_FILE.open(encoding="utf-8") as normalisation_file:
         normalisation_values = json.load(normalisation_file)
    mean = torch.zeros(11, dtype=torch.float32)
    std = torch.ones(11, dtype=torch.float32)
    json_mean = torch.tensor(list(normalisation_values["mean"].values()), dtype=torch.float32)
    json_std = torch.tensor(list(normalisation_values["std"].values()), dtype=torch.float32)
    mean[:6] = json_mean[:6]
    std[:6] = torch.where(json_std[:6] == 0, torch.ones_like(json_std[:6]), json_std[:6])
    normalisation = (mean, std)
    return normalisation



def infer(model, window, normalisation):
    # [batch, time, features]
    x = torch.tensor([window], dtype=torch.float32)
    mean, std = normalisation
    x = (x - mean) / std
    with torch.inference_mode():
        output = model(x)
    return output.squeeze().tolist()


def initialise_plot():
    plt.ion()
    figure, axes = plt.subplots(3, 1, sharex=True)
    plot_groups = (
        ("Accelerometer", axes[0], range(3)),
        ("Gyroscope", axes[1], range(3, 6)),
        ("Flex sensor", axes[2], range(6, 11)),
    )
    lines = []
    for title, axis, feature_indexes in plot_groups:
     axis.set_ylabel(title)
     axis.legend(loc="upper right")
     lines.append(
            [axis.plot([], [], label=f"feature {index}")[0] for index in feature_indexes]
        )
    axes[-1].set_xlabel("Sample")
    current_class = "Waiting for prediction"
    return figure,axes,lines,current_class


if __name__ == "__main__":
    main()
