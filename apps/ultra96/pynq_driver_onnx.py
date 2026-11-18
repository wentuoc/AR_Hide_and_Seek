"""ONNX Runtime replacement for the PYNQ driver's ``self_test`` interface."""
import importlib
import os
import time
from pathlib import Path

import numpy as np


# MODEL_FILE = os.environ.get("GESTURE_MODEL", "gesture_cnn.onnx")
MODEL_FILE = "gesture_cnn_seven.onnx"
WINDOWS_FILE = "test_data.npy"
REF_FILE = "ref_logits.npy"
T0, C0 = 20, 11
NCLASS = 7
_SESSION = None
_MODEL_PATH = None


def _onnxruntime():
    try:
        return importlib.import_module("onnxruntime")
    except ImportError as error:
        raise RuntimeError(
            "ONNX Runtime is required; install it with: "
            "python3 -m pip install onnxruntime"
        ) from error


def load_model(path=MODEL_FILE):
    """Load and cache an ONNX Runtime CPU session."""
    global _SESSION, _MODEL_PATH
    model_path = Path(path).expanduser().resolve()
    if not model_path.is_file():
        raise FileNotFoundError(f"ONNX model not found: {model_path}")
    if _SESSION is not None and _MODEL_PATH == model_path:
        return _SESSION

    ort = _onnxruntime()
    session_options = ort.SessionOptions()
    session_options.intra_op_num_threads = 1
    session_options.inter_op_num_threads = 1
    _SESSION = ort.InferenceSession(
        str(model_path),
        sess_options=session_options,
        providers=["CPUExecutionProvider"],
    )
    _MODEL_PATH = model_path
    return _SESSION


def _logits(outputs):
    """Extract one finite five-class logits vector from ONNX outputs."""
    if not outputs:
        raise ValueError("ONNX model returned no outputs")
    logits = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
    if logits.size != NCLASS:
        raise ValueError(f"model must return {NCLASS} logits, got shape {logits.shape}")
    if not np.isfinite(logits).all():
        raise ValueError("model returned non-finite logits")
    return logits


def load_windows():
    """Load test windows as ``(batch, time, channels)`` float32 arrays."""
    windows = np.load(WINDOWS_FILE, allow_pickle=False)
    if windows.ndim != 3:
        raise ValueError(
            f"{WINDOWS_FILE}: expected 3 dimensions (N, {T0}, {C0}), "
            f"got {windows.shape}"
        )
    if windows.shape[1:] == (C0, T0):
        print(f"{WINDOWS_FILE} is channels-first {windows.shape}; transposing")
        windows = windows.transpose(0, 2, 1)
    if windows.shape[1:] != (T0, C0):
        raise ValueError(
            f"{WINDOWS_FILE}: expected windows of shape ({T0}, {C0}), "
            f"got {windows.shape[1:]}"
        )
    return np.ascontiguousarray(windows, dtype=np.float32)


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

    # Load lazily once; subsequent self_test calls reuse the in-memory session.
    session = _SESSION
    if session is None:
        session = load_model()
    input_name = session.get_inputs()[0].name
    tensor = np.ascontiguousarray(window[None, :, :], dtype=np.float32)
    logits = _logits(session.run(None, {input_name: tensor}))
    return int(np.argmax(logits)), logits


def main():
    session = load_model()
    print("ONNX providers:", session.get_providers())
    windows = load_windows()

    pred, logits = self_test(None, None, None, windows[0])
    print("window 0 -> pred:", pred, "logits:", np.round(logits, 3))

    if os.path.exists(REF_FILE):
        ref = np.load(REF_FILE, allow_pickle=False)
        if ref.ndim != 2 or ref.shape[1] != NCLASS:
            raise ValueError(f"{REF_FILE}: expected shape (N, {NCLASS}), got {ref.shape}")
        n = min(len(ref), len(windows))
        worst, mism = 0.0, 0
        for index in range(n):
            prediction, model_logits = self_test(None, None, None, windows[index])
            worst = max(worst, float(np.abs(model_logits - ref[index]).max()))
            mism += int(prediction != int(np.argmax(ref[index])))
        print(
            f"compared {n} windows: worst logit error {worst:.4f}, "
            f"prediction mismatches vs reference {mism}"
        )
    else:
        print(f"{REF_FILE} not found: skipping reference comparison")

    started = time.perf_counter()
    for _ in range(100):
        self_test(None, None, None, windows[0])
    average_us = (time.perf_counter() - started) / 100 * 1e6
    print(f"average {average_us:.0f} us per inference (including Python overhead)")


if __name__ == "__main__":
    main()
