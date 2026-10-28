"""Local, dependency-free browser plotter for the Wi-Fi receiver."""

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time


class Plotter:
    def __init__(self, port=8765):
        self.samples = deque(maxlen=1200)
        self.lock = threading.Lock()
        self.status = "Waiting for ESP32"
        self.stage = None
        self.connection = None
        page = Path(__file__).with_name("wifi_plotter.html").read_bytes()
        plotter = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                # Only same-origin browser requests may control the glove.
                if self.headers.get("Origin") != f"http://127.0.0.1:{plotter.server.server_port}":
                    self.send_error(403)
                    return
                command = {"/calibrate": b"CALIBRATE\n", "/gyro": b"GYRO\n"}.get(self.path)
                with plotter.lock:
                    allowed = (command == b"CALIBRATE\n" and plotter.stage in ("ready", "needs_calibration")) or (command == b"GYRO\n" and plotter.stage == "wait_gyro")
                    if not allowed or plotter.connection is None:
                        self.send_error(409)
                        return
                    try:
                        plotter.connection.sendall(command)
                    except OSError:
                        self.send_error(503)
                        return
                self.send_response(204)
                self.end_headers()

            def do_GET(self):
                if self.path == "/":
                    body, content_type = page, "text/html; charset=utf-8"
                elif self.path == "/data":
                    body = json.dumps(plotter.snapshot()).encode()
                    content_type = "application/json"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"Live plotter: http://127.0.0.1:{self.server.server_port}", flush=True)

    def set_status(self, status):
        with self.lock:
            self.status = status

    def set_connection(self, connection):
        with self.lock:
            self.connection = connection
            self.stage = None

    def set_stage(self, stage):
        with self.lock:
            if stage != self.stage and stage == "flex_countdown":
                self.samples.clear()
            self.stage = stage

    def add(self, fields):
        with self.lock:
            self.samples.append([time.monotonic(), int(fields[0]),
                                 *map(float, fields[2:])])

    def snapshot(self):
        now = time.monotonic()
        with self.lock:
            return {"now": now, "status": self.status, "stage": self.stage,
                    "samples": [sample for sample in self.samples if now - sample[0] <= 30]}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
