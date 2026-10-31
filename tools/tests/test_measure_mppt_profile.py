import csv
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import current_profile
import measure
import measure_mppt_profile as combined
from sps5162x import SPS5162X, SupplyError
from test_measure import packet
from test_sps5162x import Instrument


class Link:
    def __init__(self, instrument, fault=False, channel=0, gated=True, premature=False, ignore_start=False):
        self.instrument, self.fault, self.channel = instrument, fault, channel
        self.running, self.ended = False, False
        self.commands = []
        self.sequence = 0
        self.received_before_enable = False
        self.gated, self.premature, self.ignore_start = gated, premature, ignore_start
        self.armed = False
        self.arm_conditions = None

    def __enter__(self): return self
    def __exit__(self, *args): pass
    @property
    def in_waiting(self): return measure.RECORD.size if self.running or self.ended else 0
    def reset_input_buffer(self): pass
    def write(self, data):
        self.commands.append(data[0])
        if data[0] == measure.console.OPCODES["measure"]:
            self.origin = time.monotonic()
            self.running = True
        elif data[0] == measure.console.OPCODES["measure_start"]:
            self.arm_conditions = (self.instrument.output, self.instrument.current)
            if not self.ignore_start:
                self.armed = True
        elif self.running and data[0] == measure.console.OPCODES["stop"]:
            self.running, self.ended = False, True
    def read(self, size):
        time.sleep(.002)
        if not self.running and not self.ended:
            return b""
        flags = measure.VALID | (self.channel << 8)
        if self.gated:
            flags |= measure.START_GATED
        if self.armed:
            flags |= measure.START_ACCEPTED
        if self.premature or (self.instrument.output and self.armed):
            flags |= measure.SWITCHING
            if self.fault and self.instrument.current >= 4:
                flags |= measure.FAULT
        else:
            self.received_before_enable = True
        if self.ended:
            flags = measure.END | (measure.START_GATED if self.gated else 0)
            self.ended = False
        data = packet(self.sequence, int((time.monotonic() - self.origin) * 1e6), flags, current=1000)
        self.sequence += 1
        return data


class CombinedTests(unittest.TestCase):
    def setUp(self):
        self.instrument = Instrument()
        self.supply = SPS5162X(Mock(), self.instrument, self.instrument.identity, 8, 35)
        self.steps = current_profile.ramp_steps(0, 8, .06, .03)

    def run_capture(self, directory, link=None, **kwargs):
        link = link or Link(self.instrument)
        with patch.object(measure.serial, "Serial", return_value=link):
            result = combined.capture(self.supply, "mock", self.steps, output_dir=Path(directory),
                                      pre_seconds=.02, post_seconds=.01, final_hold_s=.03, **kwargs)
        return result, link

    def assert_stopped(self, result, link):
        self.assertFalse(self.instrument.output)
        self.assertTrue(result["output_off_confirmed"])
        self.assertEqual(link.commands, [0x03, 0x0D] + ([0x11] if link.arm_conditions else []) + [0x03])
        self.assertTrue(result["measurement"]["end_received"])

    def test_combined_capture_one_folder_shared_clock_and_all_plots(self):
        with tempfile.TemporaryDirectory() as directory:
            result, link = self.run_capture(directory)
            self.assertTrue(result["complete"], result)
            self.assertTrue(link.received_before_enable)
            self.assertTrue(link.arm_conditions[0])
            self.assertGreater(link.arm_conditions[1], 0)
            self.assertLess(result["start_requested_s"], result["start_accepted_s"])
            self.assert_stopped(result, link)
            folder = Path(result["folder"])
            self.assertEqual(list(Path(directory).iterdir()), [folder])
            for name in ("measure.csv", "measure.bin", "measure.json", "measure.svg", "measure_dots.svg",
                         "measure_mppt.csv", "profile.csv", "summary.json", "profile.svg", "trajectory.svg", "run.json"):
                self.assertTrue((folder / name).is_file(), name)
            with (folder / "profile.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertGreater(float(rows[0]["command_start_s"]), .02)
            self.assertAlmostEqual(float(rows[-1]["scheduled_s"]) - float(rows[0]["scheduled_s"]), .06)
            self.assertEqual(json.loads((folder / "run.json").read_text())["active_samples"], result["active_samples"])
            self.assertNotIn("plot_errors", result)

    def test_mcu_fault_aborts_ramp_preserves_partial_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            result, link = self.run_capture(directory, Link(self.instrument, fault=True), make_plots=False)
            self.assertFalse(result["complete"])
            self.assertTrue(result["measurement"]["fault"])
            self.assertIn("uart_error", result)
            self.assert_stopped(result, link)
            self.assertFalse(result["profile"]["complete"])

    def test_wrong_channel_never_enables_supply(self):
        with tempfile.TemporaryDirectory() as directory:
            result, link = self.run_capture(directory, Link(self.instrument, channel=1), make_plots=False)
            self.assertFalse(result["complete"])
            self.assertNotIn(":SOURce:OUTPut 1", self.instrument.commands)
            self.assert_stopped(result, link)

    def test_uart_connection_failure_never_enables_supply(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(measure.serial, "Serial", side_effect=OSError("port busy")):
            result = combined.capture(self.supply, "mock", self.steps, output_dir=Path(directory), make_plots=False)
            self.assertFalse(result["complete"])
            self.assertIn("port busy", result["uart_error"])
            self.assertNotIn(":SOURce:OUTPut 1", self.instrument.commands)
            self.assertFalse(self.instrument.output)

    def test_supply_failure_and_ctrl_c_stop_uart(self):
        for error in (SupplyError("lost USB"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory, \
                    patch.object(self.supply, "readback", side_effect=error):
                result, link = self.run_capture(directory, make_plots=False)
                self.assertFalse(result["complete"])
                self.assert_stopped(result, link)
                self.assertEqual(result["interrupted"], isinstance(error, KeyboardInterrupt))

    def test_invalid_plan_rejected_before_output_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            for invalid in ([current_profile.CurrentStep(0, 9)], [], [current_profile.CurrentStep(0, 0)]):
                with self.assertRaises(ValueError):
                    combined.capture(self.supply, "mock", invalid, output_dir=Path(directory))
            self.assertEqual(self.instrument.commands, [])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_old_firmware_or_premature_start_never_enables_supply(self):
        for options in ({"gated": False}, {"premature": True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                self.instrument.commands.clear()
                result, link = self.run_capture(directory, Link(self.instrument, **options), make_plots=False)
                self.assertFalse(result["complete"])
                self.assertIn("uart_error", result)
                self.assertNotIn(":SOURce:OUTPut 1", self.instrument.commands)
                self.assertIsNone(link.arm_conditions)
                self.assert_stopped(result, link)

    def test_zero_current_hold_does_not_release_startup(self):
        self.steps = [current_profile.CurrentStep(0, 0), current_profile.CurrentStep(.08, .5)]
        with tempfile.TemporaryDirectory() as directory:
            result, link = self.run_capture(directory, make_plots=False)
            self.assertTrue(result["complete"], result)
            self.assertEqual(link.arm_conditions, (True, .5))
            self.assertGreater(result["start_requested_s"] - result["profile"]["profile_start_s"], .075)
            self.assert_stopped(result, link)

    def test_unverified_nonzero_setpoint_never_releases_startup(self):
        original = self.supply.set_current_limit
        def setter(amps):
            if amps > 0:
                raise SupplyError("current setpoint mismatch")
            return original(amps)
        with tempfile.TemporaryDirectory() as directory, patch.object(self.supply, "set_current_limit", side_effect=setter):
            result, link = self.run_capture(directory, make_plots=False)
            self.assertFalse(result["complete"])
            self.assertIsNone(link.arm_conditions)
            self.assert_stopped(result, link)

    def test_legacy_staircase_remains_available(self):
        steps = current_profile.load_steps(Path(combined.__file__).parent / "profiles/irradiance_staircase.csv")
        self.assertEqual(steps[0], current_profile.CurrentStep(0, .5))
        self.assertTrue(all(s.current_a > 0 for s in steps))
        self.assertEqual(steps[-1], current_profile.CurrentStep(300, 8))
        self.assertTrue(all(b.time_s - a.time_s == 20 and b.current_a - a.current_a == .5
                            for a, b in zip(steps, steps[1:])))

    def test_cli_defaults_to_five_minute_gradual_ramp(self):
        result = dict(measurement={}, complete=True, output_off_confirmed=True,
                      data_quality={}, folder="mock")
        with patch.object(sys, "argv", ["measure_mppt_profile.py", "--port", "MOCK"]), \
                patch.object(combined.SPS5162X, "open") as opened, \
                patch.object(combined, "capture", return_value=result) as capture:
            self.assertEqual(combined.main(), 0)
            opened.assert_called_once()
            steps = capture.call_args.args[2]
            self.assertEqual(len(steps), 601)
            self.assertEqual(steps[0], current_profile.CurrentStep(0, .5))
            self.assertEqual(steps[-1], current_profile.CurrentStep(300, 8))
            for a, b in zip(steps, steps[1:]):
                self.assertEqual(b.time_s - a.time_s, .5)
                self.assertIn(round(b.current_a - a.current_a, 3), (.012, .013))
            self.assertEqual(capture.call_args.kwargs["final_hold_s"], 5)

    def test_cli_explicit_profile_overrides_default_ramp(self):
        profile = Path(combined.__file__).parent / "profiles/irradiance_staircase.csv"
        result = dict(measurement={}, complete=True, output_off_confirmed=True,
                      data_quality={}, folder="mock")
        with patch.object(sys, "argv", ["measure_mppt_profile.py", "--port", "MOCK",
                                        "--profile", str(profile), "--final-hold", "20"]), \
                patch.object(combined.SPS5162X, "open"), \
                patch.object(combined, "capture", return_value=result) as capture:
            self.assertEqual(combined.main(), 0)
            self.assertEqual(len(capture.call_args.args[2]), 16)
            self.assertEqual(capture.call_args.kwargs["final_hold_s"], 20)

    def test_ignored_start_command_times_out_and_stops_both_devices(self):
        self.steps = [current_profile.CurrentStep(0, .5), current_profile.CurrentStep(.2, 1)]
        with tempfile.TemporaryDirectory() as directory, patch.object(combined, "MPPT_START_TIMEOUT_S", .05):
            result, link = self.run_capture(directory, Link(self.instrument, ignore_start=True), make_plots=False)
            self.assertFalse(result["complete"])
            self.assertIn("did not start", result["uart_error"])
            self.assert_stopped(result, link)


if __name__ == "__main__":
    unittest.main()
