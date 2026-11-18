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

T0, C0, NCLASS = 20, 11, 7
INPUT_PRENORMALIZED = True
FRAC_BITS = 10           # act_t = ap_fixed<16,6>  ->  16 - 6 = 10 fractional bits
SCALE = 1 << FRAC_BITS

CTRL = 0x00
AP_START, AP_DONE, AP_IDLE = 0x1, 0x2, 0x4

CONFIDENT_ONLY = True

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
    if w.shape != (T0, C0) or not np.isfinite(w).all():
        raise ValueError("Expected a finite normalized 20x11 input window")
    q = np.round(w * SCALE)
    if np.any(q < -32768) or np.any(q > 32767):
        raise ValueError("Fixed-point input overflow; check preprocessing")
    return q.astype(np.int16).reshape(-1)


def softmax(logits):
    exp_logits = np.exp(logits - np.max(logits))
    return exp_logits / np.sum(exp_logits)

def classify(ip, in_buf, out_buf, window, timeout_s=1.0):
    if in_buf.shape != (T0*C0,) or out_buf.shape != (NCLASS,):
        raise ValueError("Buffer shape must match 220 inputs and 8 outputs")
    in_buf[:] = to_fixed(window)
    in_buf.flush()
    ip.write(CTRL, AP_START)
    t0 = time.perf_counter()
    while not (ip.read(CTRL) & AP_DONE):
        if time.perf_counter() - t0 > timeout_s:
            raise TimeoutError("IP did not finish; check clock, reset and buffer addresses")
    out_buf.invalidate()
    logits = out_buf.astype(np.float32) / SCALE
    class_index = int(np.argmax(logits))

    if CONFIDENT_ONLY:
        confidence = np.max(softmax(logits))
        if confidence < 0.9:
            class_index = 5 # Resting
            logits = np.array([0,0,0,0,0,1,0,0])

    return class_index, logits


def self_test(ip, in_buf, out_buf, window):
    """Run once with a sentinel in the output buffer to catch address-register mix-ups."""
    sentinel = 0x4321
    out_buf[:] = sentinel
    out_buf.flush()
    pred, logits = classify(ip, in_buf, out_buf, window)
    if np.all(out_buf == sentinel):
        raise RuntimeError("logits buffer was never written. The IP ran but did not use the logits "
                           "address: check the register names / that logits_1 and logits_2 are set.")
    return pred, logits


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
        if ref.ndim != 2 or ref.shape[1] != NCLASS or not np.isfinite(ref).all():
            raise ValueError("ref_logits.npy must contain finite (N,8) logits from this same model")
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
    main()
