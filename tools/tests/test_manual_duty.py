import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools/gui"))
import console
import server
from test_telemetry import snapshot, wire


class ManualDutyTests(unittest.TestCase):
    def test_firmware_commands_ramp_and_protection(self):
        compiler = shutil.which("gcc")
        if not compiler:
            self.skipTest("Host GCC unavailable")
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            (temp / "main.h").write_text('#include <stdint.h>\nuint32_t HAL_GetTick(void);\n')
            binary = temp / "manual_duty.exe"
            args = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror"]
            for include in [temp, *(ROOT / "firmware" / p for p in
                                      ["Inc", "Inc/app", "Inc/app/modes", "Inc/drivers"])]:
                args += ["-I", str(include)]
            args += [str(ROOT / p) for p in ["tools/tests/manual_duty_harness.c",
                     "firmware/Src/app/modes/mode_manual_duty.c", "firmware/Src/app/command.c"]]
            args += ["-lm", "-o", str(binary)]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_websocket_payload_and_rejections(self):
        hub = server.Hub()
        hub.link = Mock()
        with patch.object(server, "hub", hub):
            asyncio.run(server.handle(None, {"type": "manual_duty_set", "duty": 589}))
            hub.link.send_raw.assert_called_once_with(0x10, b"\x4d\x02")
            for bad in [-1, 851, 1000, 65536, 1.2, True, "500", None]:
                hub.link.reset_mock()
                asyncio.run(server.handle(None, {"type": "manual_duty_set", "duty": bad}))
                hub.link.send_raw.assert_not_called()
            hub.run = Mock()
            hub.run.finished.is_set.return_value = False
            asyncio.run(server.handle(None, {"type": "manual_duty_set", "duty": 500}))
            hub.link.send_raw.assert_not_called()
        self.assertEqual(console.manual_duty_payload(0), b"\x00\x00")
        self.assertEqual(console.manual_duty_payload(850), b"\x52\x03")

    def test_status_packet_does_not_break_old_telemetry(self):
        parser = console.StreamParser()
        assembler = console.StreamSet()
        result = []
        data = wire(snapshot()) + b"\xb0\x02\x4d\x02" + wire(snapshot()) + b"\xb0\x02\xff\xff"
        statuses = []
        for byte in data:
            for name, value in parser.feed(bytes([byte])):
                if name == "manual_duty_target": statuses.append(value)
                row = assembler.feed(name, value)
                if row is not None: result.append(row)
        self.assertEqual(result, [snapshot(), snapshot()])
        self.assertEqual(statuses, [589, 65535])
        self.assertEqual(parser.resyncs, 0)

    def test_live_status_expiry_and_inactive(self):
        live = server.LiveState()
        live.feed("manual_duty_target", 589, 0)
        def feed(t):
            for _ in range(4):
                for name, value in snapshot().items(): live.feed(name, value, t)
            return live.drain()["manual_duty"]["target"]
        self.assertEqual(feed(.1), 589)
        live.feed("manual_duty_target", 500, .101)
        self.assertEqual(feed(.1), 500)  # Status follows the completed telemetry set.
        self.assertIsNone(feed(.7))
        live.feed("manual_duty_target", 65535, .7)
        self.assertIsNone(feed(.7))
        live.reset()
        self.assertIsNone(feed(.1))

    def test_fault_packet_signed_reading_and_preserved_history(self):
        legacy = console.decode_manual_fault(17 | (654 << 16) | (3 << 32))
        self.assertIn("FAULTED", legacy["message"])
        self.assertNotIn("stopped unexpectedly", legacy["message"])
        raw = 11 | (615 << 16) | ((-10001 & 0xFFFFFFFF) << 32)
        parsed = console.StreamParser().feed(b"\xb1\x08" + raw.to_bytes(8, "little"))
        self.assertEqual(parsed, [("manual_duty_fault", raw)])
        self.assertIsNone(console.StreamSet().feed(*parsed[0]))
        fault = console.decode_manual_fault(raw)
        self.assertEqual(fault["value"], -10001)
        self.assertEqual(fault["duty"], 615)
        self.assertIn("-10.001 A", fault["message"])
        hub = server.Hub()
        for _ in range(4):
            for name, value in snapshot().items(): hub.live.feed(name, value, 1)
        hub.live.feed(*parsed[0], 1.001)
        hub.live.feed(*parsed[0], 1.006)
        data = hub.live.drain()
        self.assertEqual(len(data["manual_events"]), 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(server.capture, "CAPTURE_DIR", root / "captures"), patch.object(server.capture, "REPO_ROOT", root):
                hub.save_manual_faults(data)
                folders = list((root / "captures").iterdir())
                self.assertEqual(len(folders), 1)
                saved = json.loads((folders[0] / "fault.json").read_text())
                self.assertEqual(saved["fault"], fault)
                self.assertEqual(len((folders[0] / "history.csv").read_text().splitlines()), 5)
                self.assertNotIn("manual_events", data)
        hub.live.feed("manual_duty_fault", 0, 2)
        hub.live.feed(*parsed[0], 3)
        self.assertEqual(len(hub.live.drain()["manual_events"]), 1)


if __name__ == "__main__":
    unittest.main()
