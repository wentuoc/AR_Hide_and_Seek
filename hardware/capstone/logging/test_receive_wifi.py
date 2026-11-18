"""Protocol tests: python3 -m unittest discover -s logging -p 'test_*.py'."""

import contextlib
import csv
import io
import socket
import unittest

from receive_wifi import CSV_FIELDS as FIELDS, parse_record, receive


def row(ax):
    return f"{ax},2,3,0,0,0,0.1,0.2,0.3,0.4,0.5,688,631,984,469,1564\n".encode()


class Connection:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def recv(self, _size):
        item = next(self.chunks, b"")
        if isinstance(item, Exception):
            raise item
        return item


class ReceiverTests(unittest.TestCase):
    def capture(self, chunks):
        output = io.StringIO()
        messages = io.StringIO()
        with contextlib.redirect_stdout(messages):
            receive(Connection(chunks), csv.writer(output), output, True)
        return list(csv.reader(io.StringIO(output.getvalue()))), messages.getvalue()

    def test_split_and_coalesced_records_discard_partial_tail(self):
        first = row(0)
        rows, _ = self.capture([first[:7], first[7:] + row(1) + b"2,50"])
        self.assertEqual([r[1] for r in rows], ["0", "1"])
        self.assertEqual(len(rows[0]), len(FIELDS) + 1)

    def test_invalid_record(self):
        rows, messages = self.capture([row(0) + b"invalid\n" + row(3)])
        self.assertEqual(len(rows), 2)
        self.assertIn("Skipping invalid", messages)

    def test_plotter_receives_only_valid_complete_records(self):
        class PlotSink:
            def __init__(self):
                self.records = []

            def add(self, fields):
                self.records.append(fields)

        sink = PlotSink()
        output = io.StringIO()
        first = row(0)
        with contextlib.redirect_stdout(io.StringIO()):
            receive(Connection([first[:8], first[8:] + b"bad\n" + row(1) + b"2,50"]),
                    csv.writer(output), output, True, sink)
        self.assertEqual([record[0] for record in sink.records], ["0", "1"])
        self.assertEqual(list(map(float, sink.records[0][:6])), [0, 2, 3, 0, 0, 0])
        self.assertEqual(list(map(float, sink.records[0][6:11])), [.1, .2, .3, .4, .5])
        self.assertEqual(list(map(int, sink.records[0][11:])), [688, 631, 984, 469, 1564])

    def test_raw_counts_saved_in_matching_columns(self):
        rows, _ = self.capture([row(0)])
        saved = dict(zip(["received_at_utc", *FIELDS], rows[0]))
        self.assertEqual(saved["A0_normalized"], "0.1")
        self.assertEqual(saved["A0_raw"], "688")
        self.assertEqual(saved["GPIO_raw"], "1564")

    def test_raw_count_validation(self):
        self.assertEqual(parse_record(row(0).replace(b"688", b"-1"))[11], "-1")
        for raw in [row(0).replace(b"688", b"0.5"),
                    row(0).replace(b"688", b"2048"),
                    row(0).replace(b"688", b"-2049"),
                    row(0).replace(b"1564", b"4096"),
                    row(0).replace(b"1564", b"-1")]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_record(raw)

    def test_without_raw_counts(self):
        raw = b"-4.908,2,3,0,0,0,0.1,0.2,0.3,0.4,0.5\n"
        self.assertEqual(len(parse_record(raw)), 11)
        rows, _ = self.capture([raw + row(0)])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], "-4.908")
        self.assertEqual(rows[0][-5:], [""] * 5)
        self.assertEqual(len(rows[0]), len(rows[1]))

    def test_status_heartbeats_are_not_csv_records(self):
        class Sink:
            def __init__(self):
                self.stages = []
            def set_stage(self, stage):
                self.stages.append(stage)
            def add(self, fields):
                pass
        sink = Sink()
        output = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()):
            receive(Connection([b"#STAT", b"US,wait_gyro\n",
                                b"#STATUS,gyro\n#STATUS,ready\n" + row(0)]),
                    csv.writer(output), output, True, sink)
        self.assertEqual(sink.stages, ["wait_gyro", "gyro", "ready"])
        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_invalid_values(self):
        for raw in [row(0).replace(b",2,", b",nan,", 1),
                    row(0).replace(b"0.5", b"1.5"), b"\xff\n"]:
            with self.subTest(raw=raw), self.assertRaises((ValueError, UnicodeError)):
                parse_record(raw)

    def test_oversized_record_and_timeout(self):
        for chunks in [[b"x" * 320], [b"x" * 320 + b"\n"], [socket.timeout()]]:
            rows, messages = self.capture(chunks)
            self.assertEqual(rows, [])
            self.assertTrue(messages)


if __name__ == "__main__":
    unittest.main()
