"""
Export normalized test windows and reference model logits as NumPy arrays.
These npy files will be used for final validation of the bitstream on the FPGA. 
"""

import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import torch

from ml.utils import GestureCNN, GestureDataset


ML_DIRECTORY = Path(__file__).resolve().parent

NFEATURES = 11
TEST_DATA_FILENAME = "test_data.npy"
REF_LOGITS_FILENAME = "ref_logits.npy"


def export_manifest_to_npy(manifest_path, output_dir):
	"""Export normalized windows from a manifest as an [N, 11, 20] array."""
	dataset = GestureDataset(manifest_path)
	windows = torch.stack([
		dataset[index][0]
		for index in range(len(dataset))
	])

	# Dataset samples are [N, 20, 11]; transpose to [N, 11, 20].
	data = windows.permute(0, 2, 1).contiguous().numpy().astype(np.float32)

	if data.ndim != 3 or data.shape[1:] != (NFEATURES, 20):
		raise ValueError(
			f"Test data should have shape (N, {NFEATURES}, 20); it has shape {data.shape} instead"
		)
	
	output_dir.mkdir(parents=True, exist_ok=True)
	output_path = Path(output_dir / TEST_DATA_FILENAME)
	np.save(output_path, data)
	return data


def create_reference_logits(weights_path, data, export_path):
	"""Create reference logits for exported test data."""
	try:
		checkpoint = torch.load(
			weights_path,
			map_location="cpu",
			weights_only=True,
		)
	except FileNotFoundError as error:
		raise FileNotFoundError(
			f"Weights file not found: {weights_path}"
		) from error

	label_to_index = checkpoint['label_to_index']
	model = GestureCNN(NFEATURES, len(label_to_index), dropout=0)
	model.load_state_dict(checkpoint["model_state_dict"])
	model.eval()

	# NumPy/HLS export is channel-first; GestureCNN expects time-first input.
	inputs = torch.from_numpy(data).float().permute(0, 2, 1).contiguous()
	with torch.no_grad():
		logits = model(inputs).numpy()

	save_location = export_path / REF_LOGITS_FILENAME
	np.save(save_location, logits)
	return save_location


def parse_arguments():
	parser = argparse.ArgumentParser(
		description="Export normalized test data and create PyTorch reference logits.",
		formatter_class=argparse.ArgumentDefaultsHelpFormatter,
	)
	parser.add_argument(
		"--manifest",
		type=Path,
		default=ML_DIRECTORY / "data" / "augmented" / "test" / "manifest.csv",
		help="Manifest containing normalized serialized windows.",
	)
	parser.add_argument(
		"--output",
		type=Path,
		default=ML_DIRECTORY / "exports" / datetime.now().strftime("%Y%m%d_%H%M%S"),
		help="Output location.",
	)
	parser.add_argument(
		"--weights",
		type=Path,
		default=ML_DIRECTORY / "outputs" / "final_weights.pt",
		help="Path to the trained model checkpoint.",
	)
	return parser.parse_args()


def main():
	args = parse_arguments()
	if not args.manifest.is_file():
		raise FileNotFoundError(f"Missing manifest: {args.manifest}")

	data = export_manifest_to_npy(args.manifest, args.output)
	print(f"Exported data with shape {data.shape} to {args.output}")
	logits_path = create_reference_logits(args.weights, data, args.output)
	print(f"Saved reference logits to {logits_path}")


if __name__ == "__main__":
	main()
