#!/usr/bin/env python3
"""Receive newline-delimited ESP32 sensor CSV over TCP (standard library only)."""

import argparse
import csv
from datetime import datetime, timezone
import math
from pathlib import Path
import socket
import time
from typing import Callable

FIELDS = [
    "ax_m_s2", "ay_m_s2", "az_m_s2",
    "gx_rad_s", "gy_rad_s", "gz_rad_s", "A0_normalized",
    "A1_normalized", "A2_normalized", "A3_normalized", "GPIO_normalized"
]
MAX_LINE = 320


def parse_record(raw):
    fields = raw.decode("ascii").strip().split(",")
    if len(fields) != len(FIELDS):
        raise ValueError(f"expected {len(FIELDS)} sensor fields")
    if not all(math.isfinite(float(field)) for field in fields):
        raise ValueError("non-finite sensor value")
    if not all(0 <= float(field) <= 1 for field in fields[6:]):
        raise ValueError("flex value outside 0–1")
    return fields


def receive(connection, writer, output, quiet, plotter=None, on_record: Callable | None = None):
    pending = bytearray()
    last_stage = None
    while True:
        try:
            data = connection.recv(4096)
        except socket.timeout:
            print("No data for 10 seconds; reconnecting.", flush=True)
            return
        except OSError as exc:
            print(f"Connection lost: {exc}", flush=True)
            return
        if not data:
            return  # Discard any incomplete final line.
        pending.extend(data)
        while b"\n" in pending:
            raw, _, remainder = pending.partition(b"\n")
            pending = bytearray(remainder)
            if len(raw) >= MAX_LINE:
                print("Oversized record; closing connection.", flush=True)
                return
            if raw.startswith(b"#STATUS,"):
                stage = raw[8:].decode("ascii", errors="replace")
                if stage in {"needs_calibration", "flex_countdown", "flex", "wait_gyro", "gyro", "ready"}:
                    if plotter is not None:
                        plotter.set_stage(stage)
                    if stage != last_stage:
                        print(f"Glove: {stage}. Use --plot for calibration controls.", flush=True)
                        last_stage = stage
                continue
            try:
                fields = parse_record(raw)
            except (ValueError, UnicodeError) as exc:
                print(f"Skipping invalid record: {exc}", flush=True)
                continue
            if on_record is not None:
                on_record(fields)
            writer.writerow([datetime.now(timezone.utc).isoformat(), *fields])
            output.flush()
            if plotter is not None:
                plotter.add(fields)
            if not quiet:
                print(",".join(fields), flush=True)
        if len(pending) >= MAX_LINE:
            print("Oversized incomplete record; closing connection.", flush=True)
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.4.1", help="ESP32 access-point address")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--output", type=Path, default=Path(
        "captures/wifi_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"))
    parser.add_argument("--quiet", action="store_true", help="Hide individual readings")
    parser.add_argument("--plot", action="store_true", help="Plot IMU and normalized flex readings locally")
    parser.add_argument("--plot-port", type=int, default=8765, help="Local plotter HTTP port (default: 8765)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not 1 <= args.plot_port <= 65535:
        parser.error("plot port must be between 1 and 65535")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    plotter = None
    try:
        # Exclusive creation prevents accidental replacement of a recording.
        with args.output.open("x", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(["received_at_utc", *FIELDS])
            output.flush()
            if args.plot:
                from tools.wifi_plotter import Plotter
                plotter = Plotter(args.plot_port)
            print(f"Connecting to ESP32 at {args.host}:{args.port}; saving {args.output}", flush=True)
            print("Join the ESP32 Wi-Fi network first. Press Ctrl+C to stop.", flush=True)
            while True:
                try:
                    connection = socket.create_connection((args.host, args.port), timeout=5)
                except OSError as exc:
                    if plotter is not None:
                        plotter.set_status("ESP32 unavailable · reconnecting")
                    print(f"Connection unavailable: {exc}; retrying in 2 seconds.", flush=True)
                    time.sleep(2)
                    continue
                print(f"Connected: {args.host}:{args.port}", flush=True)
                if plotter is not None:
                    plotter.set_status("Connected to ESP32")
                    plotter.set_connection(connection)
                with connection:
                    connection.settimeout(10)
                    try:
                        receive(connection, writer, output, args.quiet, plotter)
                    finally:
                        if plotter is not None:
                            plotter.set_connection(None)
                if plotter is not None:
                    plotter.set_status("Disconnected · reconnecting")
                print("Disconnected; reconnecting in 2 seconds.", flush=True)
                time.sleep(2)
    except KeyboardInterrupt:
        print(f"\nStopped. CSV: {args.output}")
    except OSError as exc:
        parser.exit(1, f"Receiver error: {exc}\n")
    finally:
        if plotter is not None:
            plotter.close()


if __name__ == "__main__":
    main()
