import binascii
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure


def packet(sequence=0, tick=1000, flags=measure.VALID, current=-125):
    data = measure.RECORD.pack(measure.MAGIC, sequence, tick, 800,
                               12000, current, 40000, 250, 500, flags, 0, 0, 0)
    return data[:-2] + binascii.crc_hqx(data[:-2], 0xFFFF).to_bytes(2, "little")


class ProtocolTests(unittest.TestCase):
    def test_split_frames_noise_and_signed_current(self):
        parser = measure.Parser()
        out = []
        for byte in b"normal telemetry" + packet() + packet(1, 2000):
            out.extend(parser.feed(bytes([byte])))
        self.assertEqual([row["sequence"] for row in out], [0, 1])
        self.assertEqual(out[0]["iin_ma"], -125)
        self.assertEqual(parser.crc_errors, 0)

    def test_crc_recovery_and_gap(self):
        corrupt = bytearray(packet(1, 2000))
        corrupt[18] ^= 0x80
        parser = measure.Parser()
        samples = parser.feed(packet() + corrupt + packet(2, 3000))
        timeline = measure.Timeline()
        rows = [timeline.row(s, 0.0) for s in samples]
        self.assertEqual(parser.crc_errors, 1)
        self.assertEqual(rows[-1]["sequence_gap"], 1)

    def test_timestamp_and_sequence_wrap(self):
        samples = measure.Parser().feed(packet(0xFFFFFFFF, 0xFFFFFF00) + packet(0, 0x100))
        timeline = measure.Timeline()
        first, second = [timeline.row(s, 0.0) for s in samples]
        self.assertAlmostEqual(second["t_s"] - first["t_s"], 512e-6)
        self.assertEqual(second["sequence_gap"], 0)

    def test_end_is_not_a_zero_voltage_sample(self):
        sample = measure.Parser().feed(packet(flags=measure.END | measure.RESTORE_FAILED | measure.FAULT))[0]
        row = measure.Timeline().row(sample, 0.0)
        self.assertEqual(row["vin_mv"], "")
        self.assertTrue(row["end"] and row["fault"] and row["restore_failed"])

    def test_reboot_rejected(self):
        timeline = measure.Timeline()
        samples = measure.Parser().feed(packet(10, 1000000) + packet(0, 1000))
        timeline.row(samples[0], 0.0)
        with self.assertRaises(ValueError):
            timeline.row(samples[1], 0.0)

    def test_recorder_requests_stop_and_writes_files(self):
        class Link:
            def __init__(self):
                self.data = bytearray()
                self.commands = []
                self.running = False

            def __enter__(self): return self
            def __exit__(self, *args): pass
            @property
            def in_waiting(self): return len(self.data)
            def reset_input_buffer(self): self.data.clear()
            def write(self, data):
                self.commands.append(data[0])
                if data[0] == measure.console.OPCODES["measure"]:
                    self.running = True
                    self.data.extend(packet())
                elif self.running and data[0] == measure.console.OPCODES["stop"]:
                    self.data.extend(packet(1, 2000, measure.END))
            def read(self, size):
                data = self.data[:size]
                del self.data[:size]
                return bytes(data)

        link = Link()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture.csv"
            with patch.object(measure.serial, "Serial", return_value=link):
                summary = measure.record("mock", 0.01, output)
            self.assertEqual(link.commands, [0x03, 0x0D, 0x03])
            self.assertEqual(summary["samples"], 1)
            self.assertTrue(summary["end_received"])
            with output.open() as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["iin_ma"], "-125")
            self.assertTrue(json.loads(output.with_suffix(".json").read_text())["end_received"])
            self.assertNotIn("plot_error", summary)
            self.assertIn("<svg", output.with_suffix(".svg").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
