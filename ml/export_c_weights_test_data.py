"""Export a trained PyTorch gesture CNN to HLS headers.

Writes:
  weights.h    - BatchNorm-folded weights in the layout gesture_cnn.h expects
  test_data.h  - real input windows plus PyTorch reference logits/predictions,
                 used by the C++ testbench (C simulation and co-simulation)

Usage:
  python export_weights_pt.py --pt-path model.pt --windows-path windows.npy

windows.npy: float32 array (N, 11, 20) = (batch, channels, time), already normalized
exactly as during training (PyTorch Conv1d layout).
"""
import argparse
from datetime import datetime
from pathlib import Path
import numpy as np
import torch


from ml.utils import GestureCNN 

ML_DIRECTORY = Path(__file__).resolve().parent

NFEATURES = 11
N_TEST = 20  # keep small: co-simulation runs these through RTL

WEIGHTS_FILENAME = "weights.h"
TEST_DATA_FILENAME = "test_data.h"


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Export a trained PyTorch gesture CNN to HLS headers.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--weights", required=True, help="Path to the trained PyTorch checkpoint.")
    parser.add_argument(
        "--test-data",
        required=True,
        help="Path to normalized input windows in (N, 11, 20) NumPy format.",
    )
    parser.add_argument(
        "--export",
        default=ML_DIRECTORY / "exports" / datetime.now().strftime("%Y%m%d_%H%M%S"),
        help="Directory where weights.h and test_data.h will be written.",
    )
    args = parser.parse_args()
    return args


def fold_bn(conv, bn):
    """Fold BatchNorm1d into the preceding Conv1d. Returns (w, b) as numpy."""
    scale = bn.weight / torch.sqrt(bn.running_var + bn.eps)
    w = conv.weight * scale[:, None, None]
    bias = conv.bias if conv.bias is not None else torch.zeros_like(bn.running_mean)
    b = (bias - bn.running_mean) * scale + bn.bias
    return w.detach().numpy(), b.detach().numpy()


def nested(a):
    if a.ndim == 1:
        return "{" + ", ".join(f"{v:.8g}" for v in a) + "}"
    return "{" + ",\n".join(nested(s) for s in a) + "}"


def emit(ctype, name, arr):
    dims = "".join(f"[{d}]" for d in arr.shape)
    return f"const {ctype} {name}{dims} = {nested(arr)};\n"


def main(args):
    weights = args.weights
    test_data = args.test_data
    export_dir = Path(args.export)
    export_dir.mkdir(parents=True, exist_ok=False)
    weights_out = export_dir / WEIGHTS_FILENAME
    test_out = export_dir / TEST_DATA_FILENAME

    checkpoint = torch.load(weights, map_location="cpu")
    label_to_index = checkpoint["label_to_index"]
    model = GestureCNN(NFEATURES, len(label_to_index), 0)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()  # BatchNorm uses running stats, dropout off

    f = model.features
    c = model.classifier
    tensors = []

    # Convolution layers
    # Folds the BatchNorm into the convolution layer
    # Transposes the convolution weights into HLS C++ Memory Layout
    # PyTorch (COUT, CIN, K) -> HLS [K][CIN][COUT]
    for name_w, name_b, conv, bn in [
        ("W1", "B1", f[0], f[1]),
        ("W2", "B2", f[4], f[5]),
        ("W3", "B3", f[8], f[9]),
    ]:
        w, b = fold_bn(conv, bn)
        tensors.append((name_w, np.transpose(w, (2, 1, 0)).copy()))
        tensors.append((name_b, b))

    # Linear layers
    # Transposes the weights
    # PyTorch (OUT, IN) -> HLS [IN][OUT]
    for name_w, name_b, lin in [("WD1", "BD1", c[1]), ("WD2", "BD2", c[3])]:
        tensors.append((name_w, lin.weight.detach().numpy().T.copy()))
        tensors.append((name_b, lin.bias.detach().numpy()))

    # Export folded and transposed weights to gesture_cnn.h
    # Performs a cursory check that the weights do not exceed the fixed point precision
    print("Tensor ranges (wgt_t = ap_fixed<16,4> holds [-8, 8)):")
    text = '#ifndef WEIGHTS_H\n#define WEIGHTS_H\n#include "gesture_cnn.h"\n\n'
    for name, arr in tensors:
        m = float(np.abs(arr).max())
        flag = "  <-- EXCEEDS wgt_t RANGE, widen integer bits" if m >= 8 else ""
        print(f"  {name:4s} {str(arr.shape):16s} max|v|={m:.3f}{flag}")
        text += emit("wgt_t", name, arr)
    text += "\n#endif\n"
    weights_out.write_text(text)

    # Test vectors from the real PyTorch model (catches layout/concat-order mistakes)
    x = torch.from_numpy(np.load(test_data).astype(np.float32))[:N_TEST]  # (N, C, T)
    model_input = x.transpose(1, 2).contiguous()  # GestureCNN expects (N, T, C)
    with torch.no_grad():
        logits = model(model_input).numpy()
    preds = logits.argmax(axis=1)

    # Self-check: a float model rebuilt from the FOLDED weights must reproduce PyTorch's
    # output
    import torch.nn.functional as F

    def folded_forward(xin):
        h = xin
        for i, (conv, bn) in enumerate([(f[0], f[1]), (f[4], f[5]), (f[8], f[9])]):
            w, b = fold_bn(conv, bn)
            h = F.relu(F.conv1d(h, torch.from_numpy(w), torch.from_numpy(b),
                                padding=conv.padding[0]))
            if i < 2:
                h = F.max_pool1d(h, 2)
        avg, mx = h.mean(dim=2), h.amax(dim=2)
        feat = torch.cat([avg, mx], dim=1)
        h = F.relu(F.linear(feat, c[1].weight, c[1].bias))
        return F.linear(h, c[3].weight, c[3].bias)

    print("Self-check (folded weights vs. PyTorch model):")
    with torch.no_grad():
        max_logit_error = (
            folded_forward(x) - torch.from_numpy(logits)
        ).abs().max().item()
    print(f"  max logit error: {max_logit_error:.2e}")
    if max_logit_error >= 1e-4:
        raise RuntimeError(
            "Folded weights do not match the PyTorch model; "
            "check the HLS architecture or folded convolution implementation."
        )

    x_flat = x.transpose(1, 2).reshape(len(x), -1).numpy()  # time-major (T, C) flatten

    t = "#ifndef TEST_DATA_H\n#define TEST_DATA_H\n"
    t += f"constexpr int N_TEST = {len(x)};\n"
    t += emit("float", "TEST_X", x_flat)
    t += emit("float", "TEST_LOGITS", logits)
    t += "const int TEST_PRED[N_TEST] = {" + ", ".join(map(str, preds)) + "};\n"
    t += "#endif\n"
    test_out.write_text(t)
    print(f"Wrote {weights_out} and {test_out} ({len(x)} test windows)")


if __name__ == "__main__":
    args = parse_arguments()
    main(args)
