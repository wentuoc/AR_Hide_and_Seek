"""Run the gesture_cnn IP on the board using the normal PYNQ Overlay flow.

Needs gesture_cnn.bit AND gesture_cnn.hwh (same base name, same folder).

Register names come from the .hwh (they are printed when this script starts):
  x_in_1 / x_in_2       low / high 32 bits of the input-buffer address   (write-only)
  logits_1 / logits_2   low / high 32 bits of the logits-buffer address  (write-only)
  pred_r                predicted class computed in hardware
  CTRL (offset 0x00)    bit0 ap_start, bit1 ap_done, bit2 ap_idle

Run on the board as root:  sudo -i ; cd <folder with the files> ; python3 pynq_driver.py
"""
import os
import time
import numpy as np
from pynq import Overlay, allocate

# ----------------------------- EDIT IF NEEDED --------------------------------
BITFILE = "gesture_cnn.bit"
X_IN_REGS = ("x_in_1", "x_in_2")
LOGITS_REGS = ("logits_1", "logits_2")
PRED_REG_CANDIDATES = ("pred_r", "pred")
WINDOWS_FILE = "test_data.npy"      # your test windows (renamed files: change this)
REF_FILE = "ref_logits.npy"       # PyTorch logits for the same windows
# -----------------------------------------------------------------------------

T0, C0, NCLASS = 20, 11, 5
FRAC_BITS = 10           # act_t = ap_fixed<16,6>  ->  16 - 6 = 10 fractional bits
SCALE = 1 << FRAC_BITS

CTRL = 0x00
AP_START, AP_DONE, AP_IDLE = 0x1, 0x2, 0x4


def load_overlay():
    # ignore_version: PYNQ 3.0.1 was validated with Vivado 2022.1, this design used 2023.2.
    ol = Overlay(os.path.abspath(BITFILE), ignore_version=True)
    names = [n for n in ol.ip_dict if "gesture_cnn" in n.lower()]
    if not names:
        raise RuntimeError(f"no gesture_cnn IP found; IPs in this overlay: {list(ol.ip_dict)}")
    name = names[0]
    info = ol.ip_dict[name]
    print(f"IP '{name}' at 0x{info['phys_addr']:08x}, range 0x{info['addr_range']:x}")
    return ol, getattr(ol, name)


def reg_address(ip, name):
    """Byte offset of a named register, taken from the register map parsed from the .hwh."""
    try:
        return getattr(ip.register_map, name).address
    except AttributeError:
        raise RuntimeError(f"register '{name}' not found or has no address; "
                           f"print(ip.register_map) shows the names available")


def set_buffer_address(ip, reg_names, buf):
    addr = buf.physical_address
    ip.write(reg_address(ip, reg_names[0]), addr & 0xFFFFFFFF)
    ip.write(reg_address(ip, reg_names[1]), addr >> 32)


def read_hw_pred(ip):
    for name in PRED_REG_CANDIDATES:
        if hasattr(ip.register_map, name):
            return ip.read(reg_address(ip, name)) & 0xFF
    return None


def load_windows():
    """Load the test windows as (N, T0, C0) = (batch, time, channels).

    Accepts the channels-first layout (N, C0, T0) too and transposes it. The two shapes
    (20, 11) and (11, 20) cannot be confused, so this is safe.
    """
    w = np.load(WINDOWS_FILE)
    if w.ndim != 3:
        raise ValueError(f"{WINDOWS_FILE}: expected 3 dimensions (N, {T0}, {C0}), got {w.shape}")
    if w.shape[1:] == (C0, T0):
        print(f"{WINDOWS_FILE} is channels-first {w.shape}; transposing to (N, {T0}, {C0})")
        w = w.transpose(0, 2, 1)
    if w.shape[1:] != (T0, C0):
        raise ValueError(f"{WINDOWS_FILE}: expected windows of shape ({T0}, {C0}), got {w.shape[1:]}")
    return np.ascontiguousarray(w, dtype=np.float32)


def to_fixed(window):
    """window: float array (T0, C0), time-major, normalized exactly as in training."""
    w = np.asarray(window, dtype=np.float32)
    assert w.shape == (T0, C0), f"expected ({T0}, {C0}), got {w.shape}"
    q = np.round(w * SCALE)
    return np.clip(q, -32768, 32767).astype(np.int16).reshape(-1)


def classify(ip, in_buf, out_buf, window, timeout_s=1.0):
    in_buf[:] = to_fixed(window)
    in_buf.flush()
    ip.write(CTRL, AP_START)
    t0 = time.perf_counter()
    while not (ip.read(CTRL) & AP_DONE):
        if time.perf_counter() - t0 > timeout_s:
            raise TimeoutError("IP did not finish; check clock, reset and buffer addresses")
    out_buf.invalidate()
    logits = out_buf.astype(np.float32) / SCALE
    return int(np.argmax(logits)), logits


# def self_test(ip, in_buf, out_buf, window):
#     """Run once with a sentinel in the output buffer to catch address-register mix-ups."""
#     sentinel = 0x4321
#     out_buf[:] = sentinel
#     out_buf.flush()
#     pred, logits = classify(ip, in_buf, out_buf, window)
#     if np.all(out_buf == sentinel):
#         raise RuntimeError("logits buffer was never written. The IP ran but did not use the logits "
#                            "address: check the register names / that logits_1 and logits_2 are set.")
#     return pred, logits


def main():
    ol, ip = load_overlay()
    print(ip.register_map)

    ctrl = ip.read(CTRL)
    print(f"control register = 0x{ctrl:08x} (idle bit 0x4 should be set)")
    for n in X_IN_REGS + LOGITS_REGS + PRED_REG_CANDIDATES:
        if hasattr(ip.register_map, n):
            print(f"  {n:10s} offset 0x{reg_address(ip, n):02x}")

    in_buf = allocate(shape=(T0 * C0,), dtype=np.int16)
    out_buf = allocate(shape=(NCLASS,), dtype=np.int16)
    set_buffer_address(ip, X_IN_REGS, in_buf)
    set_buffer_address(ip, LOGITS_REGS, out_buf)

    windows = load_windows()                   # (N, 20, 11) = (batch, time, channels)
    pred, logits = self_test(ip, in_buf, out_buf, windows[0])
    hw_pred = read_hw_pred(ip)
    print("window 0 -> pred:", pred, "(hardware pred register:", hw_pred, ") logits:", np.round(logits, 3))

    # Compare with PyTorch on the same windows (ref_logits.npy is created on the PC).
    if os.path.exists(REF_FILE):
        ref = np.load(REF_FILE)
        n = min(len(ref), len(windows))
        worst, mism, reg_mism = 0.0, 0, 0
        for i in range(n):
            p, lg = classify(ip, in_buf, out_buf, windows[i])
            worst = max(worst, float(np.abs(lg - ref[i]).max()))
            mism += int(p != int(np.argmax(ref[i])))
            hp = read_hw_pred(ip)
            reg_mism += int(hp is not None and hp != p)
        print(f"compared {n} windows: worst logit error {worst:.4f}, "
              f"prediction mismatches vs PyTorch {mism}, pred-register vs logits mismatches {reg_mism}")
    else:
        print(f"{REF_FILE} not found: skipping the PyTorch comparison")

    t0 = time.perf_counter()
    for _ in range(100):
        classify(ip, in_buf, out_buf, windows[0])
    print(f"average {(time.perf_counter() - t0) / 100 * 1e6:.0f} us per inference (including Python overhead)")


if __name__ == "__main__":
    #main()

"""PyTorch replacement for the PYNQ driver's ``self_test`` interface.

The model file must contain a TorchScript module or a pickled ``torch.nn.Module``.
The hardware arguments are accepted for drop-in compatibility and are not used.
"""
import os
from pathlib import Path
import torch
from torch import nn

import numpy as np


MODEL_FILE = os.environ.get("weights.pt")
NCLASS = 7
_MODEL = None
_MODEL_PATH = None

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

def load_model(path=MODEL_FILE):
    """Load a CPU PyTorch model from ``path`` and cache it for later calls."""
    global _MODEL, _MODEL_PATH
    model_path = Path(path).expanduser().resolve()
    if not model_path.is_file():
        raise FileNotFoundError(f"PyTorch model not found: {model_path}")
    if _MODEL is not None and _MODEL_PATH == model_path:
        return _MODEL
    
    checkpoint = torch.load(model_path, map_location="cpu", weights_only=True)
    
    if "model_state_dict" in checkpoint:
        state_dict = checkpoint["model_state_dict"]
        label_to_index = checkpoint.get("label_to_index")
    else:
        state_dict = checkpoint

    model = create_cnn_model(num_classes=len(label_to_index)).to("cpu")
    model.load_state_dict(state_dict)
    model.eval()

    _MODEL = model
    _MODEL_PATH = model_path

    return model


def _logits(output):
    """Extract one finite five-class logits vector from common model outputs."""

    if isinstance(output, dict):
        for key in ("logits", "output", "predictions"):
            if key in output:
                output = output[key]
                break
        else:
            raise ValueError("model output dictionary has no logits/output/predictions entry")
    elif isinstance(output, (tuple, list)):
        if not output:
            raise ValueError("model returned an empty tuple/list")
        output = output[0]
    if not isinstance(output, torch.Tensor):
        raise TypeError(f"model output must be a torch.Tensor, got {type(output).__name__}")

    output = output.detach().to("cpu").reshape(-1)
    if output.numel() != NCLASS:
        raise ValueError(f"model must return {NCLASS} logits, got shape {tuple(output.shape)}")
    logits = output.numpy().astype(np.float32, copy=False)
    if not np.isfinite(logits).all():
        raise ValueError("model returned non-finite logits")
    return logits


def self_test(ip, in_buf, out_buf, window):
    """Run one window and return ``(predicted_class, logits)``.

    ``ip``, ``in_buf`` and ``out_buf`` are retained solely for compatibility with
    :func:`pynq_driver.self_test`.
    """

    window = np.asarray(window, dtype=np.float32)
    if window.shape != (20, 11):
        raise ValueError(f"expected window shape (20, 11), got {window.shape}")
    if not np.isfinite(window).all():
        raise ValueError("window contains non-finite values")

    tensor = torch.from_numpy(np.ascontiguousarray(window)).unsqueeze(0)
    with torch.no_grad():
        logits = _logits(_MODEL(tensor))
    return int(np.argmax(logits)), logits


if __name__ == "__main__":
    print(f"PyTorch driver ready; set GESTURE_MODEL to select the .pt file (default: {MODEL_FILE})")
