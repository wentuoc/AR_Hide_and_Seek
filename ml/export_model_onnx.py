"""Export the GestureCNN checkpoint from ``utils.py`` to ONNX.

Run from this directory, for example:
	python export_model_onnx.py --weights weights.pt --output ml/exports/gesture_cnn

The exported contract is float32 ``[batch, 20, 11]`` to float32
``[batch, number_of_classes]`` logits.
"""
import argparse
from datetime import datetime
import importlib
import os
from pathlib import Path

import numpy as np
import torch

from ml.utils import load_model

ML_DIRECTORY = Path(__file__).resolve().parent

OUTPUT_ONNX_FILENAME = "gesture_cnn.onnx"

def export_model(weights_path, output_dir, opset):
	"""Load a project checkpoint and export its inference graph."""
	model, label_to_index = load_model(weights_path, "cpu")
	if not label_to_index:
		raise ValueError("Checkpoint does not contain a non-empty label_to_index mapping")

	output_dir.mkdir(parents=True, exist_ok=True)
	example = torch.zeros((1, 20, 11), dtype=torch.float32)
	torch.onnx.export(
		model,
		example,
		output_dir / OUTPUT_ONNX_FILENAME,
		input_names=["window"],
		output_names=["logits"],
		dynamic_axes={
			"window": {0: "batch"},
			"logits": {0: "batch"},
		},
		opset_version=opset,
		do_constant_folding=True,
	)
	return output_dir, label_to_index


def verify_export(weights_path, output_dir, label_to_index):
	"""Check the graph and compare one ONNX result with PyTorch."""
	model_path = output_dir / OUTPUT_ONNX_FILENAME
	try:
		onnx = importlib.import_module("onnx")
	except ImportError as error:
		raise RuntimeError(
			"ONNX export verification requires onnx; install it with: "
			"python3 -m pip install onnx"
		) from error

	graph = onnx.load(str(model_path))
	onnx.checker.check_model(graph)

	try:
		ort = importlib.import_module("onnxruntime")
	except ImportError:
		print("ONNX Runtime not installed; skipped numerical verification")
		return

	model, _ = load_model(weights_path, "cpu")
	sample = np.random.default_rng(0).standard_normal((1, 20, 11)).astype(np.float32)
	with torch.no_grad():
		expected = model(torch.from_numpy(sample)).numpy()
	session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
	actual = session.run(["logits"], {"window": sample})[0]
	np.testing.assert_allclose(actual, expected, rtol=1e-4, atol=1e-5)
	print(f"Runtime verification passed for {len(label_to_index)} classes")


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"--weights",
		type=Path,
		default=ML_DIRECTORY / "outputs" / "weights.pt",
		help="Path to the GestureCNN checkpoint",
	)
	parser.add_argument(
		"--output",
		type=Path,
		default=ML_DIRECTORY / "exports" / datetime.now().strftime("%Y%m%d_%H%M%S"),
		help="Directory where the ONNX model will be exported",
	)
	parser.add_argument(
		"--opset",
		type=int,
		default=17,
		help="ONNX opset version to use for export",
	)
	parser.add_argument(
		"--skip-verify",
		action="store_true",
		help="Skip ONNX graph and numerical verification",
	)
	args = parser.parse_args()

	output_path, label_to_index = export_model(args.weights, args.output, args.opset)
	if not args.skip_verify:
		verify_export(args.weights, output_path, label_to_index)
	print(f"Exported {args.weights} -> {output_path} / {OUTPUT_ONNX_FILENAME}")
	print(f"Labels: {label_to_index}")


if __name__ == "__main__":
	main()
