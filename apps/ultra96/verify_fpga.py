"""Execute supplied test windows on Ultra96, independently of live sensors."""
import argparse
import json
import time
from pathlib import Path
import numpy as np
from check_model import ROOT, inspect

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, default=ROOT)
    parser.add_argument('--max-logit-error', type=float, default=0.02,
                        help='Engineering regression threshold, not a gesture accuracy target')
    args = parser.parse_args()
    if not np.isfinite(args.max_logit_error) or args.max_logit_error < 0:
        parser.error('max-logit-error must be finite and nonnegative')
    windows, ref, report = inspect(args.folder)
    from live_ai import FPGA
    engine = FPGA(args.folder / 'gesture_cnn.bit')
    worst, mismatches, timings = 0.0, 0, []
    for i, window in enumerate(windows):
        pred, logits, ms = engine.infer(window)
        error = float(np.max(np.abs(np.asarray(logits) - ref[i])))
        worst = max(worst, error)
        mismatches += int(pred != int(np.argmax(ref[i])))
        timings.append(ms)
        print('[TEST WINDOW] index={} class_id={} reference_class_id={} error={:.6f}'.format(
            i, pred, int(np.argmax(ref[i])), error), flush=True)
    report.update(check='fpga_reference_windows',hardware_executed=True,
                  input_source='provided_test_data_not_live_sensors',
                  windows=len(windows),prediction_mismatches=mismatches,
                  worst_logit_error=worst,max_logit_error=args.max_logit_error,
                  mean_driver_ms=float(np.mean(timings)),
                  passed=mismatches == 0 and worst <= args.max_logit_error)
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    path = logs / ('fpga-check-' + str(time.time_ns()) + '.json')
    path.write_text(json.dumps(report,indent=2) + '\n')
    print(json.dumps(report,indent=2))
    print('Report: ' + str(path))
    if not report['passed']:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
