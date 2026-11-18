"""Offline asset checks. Does not load or emulate the FPGA."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np

ROOT = Path(__file__).resolve().parent
EXPECTED = {'CTRL': 0, 'x_in_1': 0x10, 'x_in_2': 0x14,
            'logits_1': 0x2c, 'logits_2': 0x30, 'pred_r': 0x1c}

def inspect(folder):
    folder = Path(folder)
    paths = [folder / n for n in ('gesture_cnn.bit', 'gesture_cnn.hwh',
                                  'test_data.npy', 'ref_logits.npy')]
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError('Missing/empty asset: ' + str(path))
    modules = [m for m in ET.parse(paths[1]).getroot().iter('MODULE')
               if m.get('MODTYPE') == 'gesture_cnn']
    if len(modules) != 1:
        raise ValueError('Expected one gesture_cnn module')
    regs = {}
    for reg in modules[0].iter('REGISTER'):
        props = {p.get('NAME'): p.get('VALUE') for p in reg.findall('./PROPERTY')}
        # Vivado exports properties inside a PROPERTIES element.
        props.update({p.get('NAME'): p.get('VALUE') for p in reg.findall('./PROPERTIES/PROPERTY')})
        if 'ADDRESS_OFFSET' in props:
            regs[reg.get('NAME')] = int(props['ADDRESS_OFFSET'], 0)
    if any(regs.get(k) != v for k, v in EXPECTED.items()):
        raise ValueError('Unexpected register layout: ' + repr(regs))
    windows = np.load(paths[2], allow_pickle=False)
    if windows.ndim == 3 and windows.shape[1:] == (11, 20):
        windows = windows.transpose(0, 2, 1)
    if windows.ndim != 3 or windows.shape[1:] != (20, 11) or len(windows) == 0:
        raise ValueError('Expected nonempty (N,20,11) or (N,11,20) test data')
    ref = np.load(paths[3], allow_pickle=False)
    if ref.shape != (len(windows), 5):
        raise ValueError('Reference logits must match every window')
    if not np.isfinite(windows).all() or not np.isfinite(ref).all():
        raise ValueError('Nonfinite test data/reference')
    q = np.round(windows.astype(np.float32) * 1024)
    if np.any(q < -32768) or np.any(q > 32767):
        raise ValueError('Test input would saturate ap_fixed<16,6>')
    report = dict(check='offline_assets',hardware_executed=False,
                  module=modules[0].get('INSTANCE'),registers=regs,
                  windows_shape=list(windows.shape),reference_shape=list(ref.shape),
                  hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    return np.ascontiguousarray(windows, dtype=np.float32), ref, report

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        _, _, report = inspect(args.folder)
        print(json.dumps(report, indent=2))
        print('[PASS] Offline assets only; real sensor preprocessing still needs confirmation.')
    except (OSError, ValueError, ET.ParseError) as e:
        parser.exit(1, '[FAIL] ' + str(e) + '\n')

if __name__ == '__main__':
    main()
