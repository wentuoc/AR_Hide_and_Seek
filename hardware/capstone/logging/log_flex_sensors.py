import argparse
import serial
from datetime import datetime

parser = argparse.ArgumentParser(description="Log serial output to a text file.")
parser.add_argument("--port", default="/dev/cu.usbserial-110",
                    help="Serial port, e.g. /dev/cu.usbserial-110 or COM3")
parser.add_argument("--baud", type=int, default=115200,
                    help="Baud rate (default: 115200)")
parser.add_argument("--output", required=True,
                    help="Output filename, e.g. flex_test.txt")

args = parser.parse_args()

with serial.Serial(args.port, args.baud, timeout=1) as ser, open(
    args.output, "a", encoding="utf-8"
) as file:
    print(f"Logging from {args.port} to {args.output}. Press Ctrl+C to stop.")

    try:
        while True:
            line = ser.readline().decode("utf-8", errors="replace").strip()

            if line:
                timestamp = datetime.now().isoformat()
                entry = f"{timestamp}, {line}"

                print(entry)
                file.write(entry + "\n")
                file.flush()

    except KeyboardInterrupt:
        print(f"\nSaved to {args.output}")