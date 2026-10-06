import csv
import re
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "gui"))

import console
import capture
import link
import server


def snapshot():
    values = {"vbus_mv": 48000}
    for names in console.CHANNEL_FIELDS.values():
        values.update(zip(names, (24000, -1250, 48100, -600, 500)))
    return values


def wire(values):
    data = bytearray()
    for ident, (name, size, signed) in console.STREAM.items():
        value = values[name]
        if value is None:
            value = 0xFFFFFFFF
            if signed:
                value = -0x80000000
        data.extend((ident, size))
        data.extend(value.to_bytes(size, "little", signed=signed))
    return data


class TelemetryTests(unittest.TestCase):
    def test_firmware_wire_contract(self):
        source = (TOOLS.parent / "firmware/Src/app/stream.c").read_text()
        ids = re.findall(r"\{((?:0x[0-9A-F]+U,?\s*){5})\}", source)
        parsed = [tuple(int(v, 16) for v in re.findall(r"0x([0-9A-F]+)U", row))
                  for row in ids]
        self.assertEqual(parsed, list(console.CHANNEL_IDS.values()))
        self.assertEqual(len(console.STREAM), 26)
        self.assertEqual(len(wire(snapshot())), 146)
        self.assertIn(f"#define STREAM_PERIOD_MS {console.STREAM_PERIOD_MS}U", source)
        self.assertNotIn("flags", console.STREAM_NAMES)
        self.assertNotIn("vin_target_mv", console.STREAM_NAMES)

    def test_fragmented_signed_and_invalid_samples(self):
        values = snapshot()
        values["b_iout_ma"] = None
        values["c_vin_mv"] = None
        parser = console.StreamParser()
        assembler = console.StreamSet()
        result = None
        for byte in b"\xff\x00" + wire(values):
            for name, value in parser.feed(bytes([byte])):
                result = assembler.feed(name, value)
        self.assertEqual(result, values)
        self.assertEqual(parser.resyncs, 2)

    def test_partial_set_never_reuses_previous_values(self):
        assembler = console.StreamSet()
        values = snapshot()
        for name, value in list(values.items())[3:]:
            self.assertIsNone(assembler.feed(name, value))
        for name, value in values.items():
            if name == "b_iin_ma":
                continue
            self.assertIsNone(assembler.feed(name, value))
        for name, value in values.items():
            result = assembler.feed(name, value)
        self.assertEqual(result, values)

    def test_record_replay_and_live_invalid_channel(self):
        recorder = capture.Recorder()
        recorder.start_time = 10.0
        values = snapshot()
        for index in range(8):
            if index == 4:
                values["c_vin_mv"] = None
            for name, value in values.items():
                recorder.feed(name, value, 10 + index * 0.005)
        recorder.mark(10.01, "chmppt")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.csv"
            recorder.write_csv(path)
            with path.open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["t_s"], "0.0000")
            self.assertEqual(rows[2]["event"], "chmppt")
            self.assertEqual(rows[-1]["c_vin_mv"], "")
            self.assertEqual(rows[-1]["e_iout_ma"], "-600")
            replay = link.ReplayLink(path, speed=0)
            live = server.LiveState()
            replay.subscribe(live.feed)
            replay.open()
            replay._thread.join(timeout=5)
            replay.close()
            self.assertEqual(live.sets, 8)
            self.assertEqual(live.dropped, 0)
            self.assertIsNone(live.latest["c_pin_w"])
            self.assertEqual(live.latest["e_iout_ma"], -600)
            self.assertEqual(len(live.ts), 2)
            capture.render_timeseries(recorder, Path(directory) / "capture.svg",
                                      capture.SEQUENCES[0])
            self.assertTrue((Path(directory) / "capture.svg").is_file())


if __name__ == "__main__":
    unittest.main()
