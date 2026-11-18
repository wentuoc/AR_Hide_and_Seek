"""Exercise calibration commands over the local HTTP server."""
import contextlib
import io
import json
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from wifi_plotter import Plotter


class PlotterTests(unittest.TestCase):
    def test_sensor_formats_preserve_all_channels(self):
        import threading
        from collections import deque
        plotter = Plotter.__new__(Plotter)
        plotter.lock = threading.Lock()
        plotter.samples = deque()
        plotter.status = "Connected"
        plotter.stage = "ready"
        values = [-4.908, 2, 3, 0, 0, 0, .1, .2, .3, .4, .5]
        for fields in (values, values + [688, 631, 984, 469, 1564]):
            plotter.add(list(map(str, fields)))
            self.assertEqual(plotter.snapshot()["samples"][-1][1:], fields)

    def test_commands_require_connection_stage_and_origin(self):
        class Connection:
            def __init__(self):
                self.commands = []
            def sendall(self, data):
                self.commands.append(data)
        with contextlib.redirect_stdout(io.StringIO()):
            plotter = Plotter(0)
        base = f"http://127.0.0.1:{plotter.server.server_port}"
        connection = Connection()
        def post(path, origin=base):
            return urlopen(Request(base + path, data=b"", headers={"Origin": origin}), timeout=2)
        try:
            with self.assertRaises(HTTPError) as error:
                post('/calibrate')
            self.assertEqual(error.exception.code, 409)
            plotter.set_connection(connection)
            plotter.set_stage('needs_calibration')
            with self.assertRaises(HTTPError) as error:
                post('/calibrate', 'https://example.com')
            self.assertEqual(error.exception.code, 403)
            with post('/calibrate') as response:
                self.assertEqual(response.status, 204)
            plotter.set_stage('flex')
            with self.assertRaises(HTTPError):
                post('/gyro')
            plotter.set_stage('wait_gyro')
            with post('/gyro') as response:
                self.assertEqual(response.status, 204)
            self.assertEqual(connection.commands, [b'CALIBRATE\n', b'GYRO\n'])
            with urlopen(base + '/data') as response:
                self.assertEqual(json.load(response)['stage'], 'wait_gyro')
            plotter.set_connection(None)
            self.assertIsNone(plotter.snapshot()['stage'])
        finally:
            plotter.close()
