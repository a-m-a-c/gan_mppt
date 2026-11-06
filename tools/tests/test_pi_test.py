from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from contextlib import nullcontext
import binascii
import time
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from measure_pi_response import TargetSequence
import measure_pi_response as script
import measure
from test_measure_mppt_profile import Link
from test_sps5162x import Instrument
from sps5162x import SPS5162X


class PiLink(Link):
    def __init__(self, instrument):
        super().__init__(instrument)
        self.target = 0
        self.changed = False
        self.diag_seq = 0
        self.end_seen = False
        self.drop_end = False

    def write(self, data):
        if data[0] == 0x13:
            self.target = int.from_bytes(data[2:4], "little")
            self.changed = True
        else:
            super().write(bytes([0x0D]) + data[1:] if data[0] == 0x12 else data)

    def read(self, size):
        data = super().read(size)
        if not data:
            return data
        values = list(measure.RECORD.unpack(data))
        if values[9] & measure.END:
            if self.drop_end:
                return b""
            self.end_seen = True
        values[4] = self.target or 30000
        values[9] |= measure.PI_TEST
        packet = measure.RECORD.pack(*values)
        packet = packet[:-2] + binascii.crc_hqx(packet[:-2], 0xFFFF).to_bytes(2, "little")
        if self.changed:
            diag = measure.DIAGNOSTIC_RECORD.pack(measure.DIAGNOSTIC_MAGIC, self.diag_seq,
                int((time.monotonic()-self.origin)*1e6), self.target, 0, 125000, 0,
                self.diag_seq+1, 0, 3, self.target, 0, 0)
            packet += diag[:-2] + binascii.crc_hqx(diag[:-2], 0xFFFF).to_bytes(2, "little")
            self.diag_seq += 1
            self.changed = False
        return packet


class PiTests(unittest.TestCase):
    def test_capture_protocol_and_offline_analysis(self):
        instrument = Instrument()
        supply = SPS5162X(Mock(), instrument, instrument.identity, 8, 35)
        link = PiLink(instrument)
        original = script.runner.capture
        output_setter = supply.set_output
        def checked_output(enabled):
            if not enabled and instrument.output:
                self.assertTrue(link.end_seen, "Source OFF occurred before MCU END")
            return output_setter(enabled)
        supply.set_output = checked_output
        def faster(*args, **kwargs):
            return original(*args, **kwargs, pre_seconds=.02, post_seconds=.01)
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(targets=[25,27,25], hold=1, current=5, voltage=35,
                                   resource=None, port="MOCK", output=Path(directory))
            with patch.object(script.SPS5162X, "open", return_value=nullcontext(supply)), \
                    patch.object(measure.serial, "Serial", return_value=link), \
                    patch.object(script.runner, "capture", side_effect=faster):
                folder = script.capture(args)
            self.assertFalse(instrument.output)
            run = json.loads((folder / "run.json").read_text())
            self.assertLess(run["pwm_stop_confirmed_s"], run["profile"]["output_off_s"])
            self.assertEqual(link.diag_seq, 3)
            self.assertEqual(link.arm_conditions, (True,5))
            result = script.analyse(folder, dwell=.1)
            self.assertTrue(result["complete"])
            self.assertEqual([p["target_v"] for p in result["phases"]], [25,27,25])
            self.assertTrue(all(p["settling_s"] is not None for p in result["phases"]))
            self.assertTrue((folder / "pi_response.svg").exists())

    def test_missing_end_still_turns_source_off_and_fails(self):
        instrument = Instrument()
        supply = SPS5162X(Mock(), instrument, instrument.identity, 8, 35)
        link = PiLink(instrument)
        link.drop_end = True
        original = script.runner.capture
        def faster(*args, **kwargs):
            return original(*args, **kwargs, pre_seconds=.02, post_seconds=.01)
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(targets=[25], hold=1, current=5, voltage=35,
                                   resource=None, port="MOCK", output=Path(directory))
            with patch.object(script.SPS5162X, "open", return_value=nullcontext(supply)), \
                    patch.object(measure.serial, "Serial", return_value=link), \
                    patch.object(script.runner, "capture", side_effect=faster):
                folder = script.capture(args)
            run = json.loads((folder / "run.json").read_text())
            self.assertFalse(run["complete"])
            self.assertFalse(instrument.output)
            self.assertIn("PWM shutdown was not confirmed", run["error"])

    def test_firmware(self):
        compiler = shutil.which("gcc")
        if not compiler:
            self.skipTest("Host GCC unavailable")
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / "main.h").write_text('#include <stdint.h>\nuint32_t HAL_GetTick(void);\n')
            args = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(tmp)]
            for p in ["Inc", "Inc/app", "Inc/app/modes", "Inc/drivers", "Inc/app/control",
                      "Inc/app/control/controllers", "Inc/app/control/control_helpers"]:
                args += ["-I", str(root / "firmware" / p)]
            args += [str(root / p) for p in ["tools/tests/pi_test_harness.c",
                "firmware/Src/app/modes/mode_pi_test.c", "firmware/Src/app/control/control_helpers/pi.c"]]
            binary = tmp / "pi_test.exe"
            args += ["-lm", "-o", str(binary)]
            built = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)

    def sample(self, t, started=False):
        return dict(t_host_s=t, end=False, fault=False, channel="A", valid=True, switching=started,
                    flags=measure.PI_TEST | (measure.START_ACCEPTED if started else 0))

    def test_acknowledgement_and_timed_steps(self):
        sequence = TargetSequence([25,30,25], 3)
        sequence.sample(self.sample(0))
        def ack(t, mv):
            sequence.diagnostic(dict(updated=True, channel="A", target_mv=mv, t_s=t, t_host_s=t))
        ack(.1, 25000)
        gated = self.sample(2)
        gated["flags"] |= measure.START_ACCEPTED
        sequence.sample(gated)
        self.assertIsNone(sequence.start)
        sequence.sample(self.sample(4, True))
        sequence.sample(self.sample(6.99, True))
        self.assertEqual(sequence.sent, 0)
        sequence.sample(self.sample(7, True))
        ack(7.04, 30000)
        sequence.sample(self.sample(10.03, True))
        self.assertEqual(sequence.sent, 1)
        sequence.sample(self.sample(10.05, True))
        ack(10.09, 25000)
        sequence.sample(self.sample(13.08, True))
        self.assertFalse(sequence.complete.is_set())
        sequence.sample(self.sample(13.10, True))
        self.assertTrue(sequence.complete.is_set())
        self.assertEqual(sequence.queue.qsize(), 3)

    def test_missing_ack_and_invalid_profiles(self):
        sequence = TargetSequence([25,27,25], 10)
        sequence.sample(self.sample(0))
        with self.assertRaises(TimeoutError):
            sequence.sample(self.sample(2.1))
        for targets, hold in [([25,25],10), ([float("nan")],10), ([31],10), ([25],200), ([25],0)]:
            with self.assertRaises(ValueError):
                TargetSequence(targets, hold)


if __name__ == "__main__":
    unittest.main()
