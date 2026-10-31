import binascii
import csv
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import characterise_iv as characterise
import measure
from sps5162x import SupplyReading, SupplyError


class Supply:
    identity, resource = "MOCK SPS5162X", "MOCK"
    def __init__(self):
        self.output, self.current, self.voltage = False, 8, 35
        self.events = []
    def set_output(self, enabled):
        self.output = enabled
        self.events.append(("output", enabled))
    def set_current_limit(self, current):
        assert not self.output
        self.current = current
        self.events.append(("current", current))
    def set_voltage_limit(self, voltage): self.voltage = voltage
    def readback(self):
        return SupplyReading("mock", self.voltage, self.current, self.output,
                             28, self.current, 28 * self.current, "CC")


class Link:
    def __init__(self, supply, *, fault=False, corrupt=False, endpoint_peak=False, return_ramp=False):
        self.supply = supply
        self.fault, self.corrupt, self.endpoint_peak = fault, corrupt, endpoint_peak
        self.return_ramp = return_ramp
        self.data, self.commands = bytearray(), []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    @property
    def in_waiting(self): return len(self.data)
    def flush(self): pass
    def reset_input_buffer(self): self.data.clear()
    def read(self, count):
        result = bytes(self.data[:count])
        del self.data[:count]
        return result
    def write(self, data):
        self.commands.append(data[0])
        if data[0] != measure.console.OPCODES["measure_iv_sweep"]:
            return
        assert self.supply.output
        base = measure.IV_SWEEP | measure.IV_DUTY_RAMP
        if self.return_ramp:
            base |= measure.IV_RETURN_RAMP
        seq = 0
        for down in ((False, True) if self.return_ramp else (False,)):
            levels = [(300, 28000, .1), (500, 24000, .85), (700, 15000, 1)]
            if self.endpoint_peak:
                levels = [(300, 28000, 1), (500, 24000, .8), (700, 15000, .7)]
            for duty, volts, fraction in (reversed(levels) if down else levels):
                for n in range(100):
                    flags = base | measure.VALID | measure.SWITCHING | (measure.IV_DESCENDING if down else 0)
                    # Large transition spike must not become the reference MPP.
                    current = 9500 if n < 20 else int(self.supply.current * fraction * 1000)
                    self.add(seq, volts, current, duty, flags)
                    seq += 1
        self.add(seq, 0, 0, 0, base | measure.END | (measure.FAULT if self.fault else measure.IV_COMPLETE))
        if self.corrupt:
            self.data[20] ^= 1
    def add(self, seq, volts, current, duty, flags):
        packet = measure.RECORD.pack(measure.MAGIC, seq, seq * 1000, 100,
                                     volts, current, 45000, 100, duty, flags, 0, 0, 0)
        self.data.extend(packet[:-2] + binascii.crc_hqx(packet[:-2], 0xFFFF).to_bytes(2, "little"))


class CharacterisationTests(unittest.TestCase):
    def setUp(self):
        # All tests fail if any real instrument-opening path is reached.
        self.visa = patch("sps5162x.pyvisa.ResourceManager", side_effect=AssertionError("Hardware forbidden"))
        self.visa.start()
        self.addCleanup(self.visa.stop)
        self.supply = Supply()

    def capture(self, directory, **link_options):
        links = []
        def serial(*args, **kwargs):
            link = Link(self.supply, **link_options)
            links.append(link)
            return link
        with patch.object(measure.serial, "Serial", side_effect=serial):
            folder = characterise.capture(self.supply, "MOCK", [.5, 1], output=Path(directory), settle=0, off_seconds=0)
        return folder, links

    def test_real_recorder_mock_instruments_combines_and_replots(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, links = self.capture(directory)
            run = json.loads((folder / "run.json").read_text())
            self.assertTrue(run["complete"], run)
            self.assertTrue(run["output_off_confirmed"])
            self.assertFalse(self.supply.output)
            self.assertEqual(links[0].commands, [3])
            for link in links[1:]: self.assertEqual(link.commands, [3, 14, 3])
            with patch.object(measure.serial, "Serial", side_effect=AssertionError("Offline only")):
                result = characterise.render(folder, folder / run["sweeps"][0]["csv"])
            self.assertEqual(result["usable_reference_points"], 2)
            with (folder / "reference_mpp.csv").open() as handle:
                peaks = list(csv.DictReader(handle))
            self.assertEqual([float(p["vin_v"]) for p in peaks], [24] * 2)
            self.assertAlmostEqual(float(peaks[0]["pin_w"]), 10.2)
            with (folder / "all_samples.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 602)
            self.assertEqual({r["source_current_a"] for r in rows}, {"0.5", "1"})
            for name in ("iv_curves.svg", "iv_curves.png", "settled_points.csv", "analysis.json"):
                self.assertTrue((folder / name).stat().st_size)

    def test_legacy_return_capture_still_replots_both_branches(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, _ = self.capture(directory, return_ramp=True)
            self.assertEqual(characterise.render(folder)["usable_reference_points"], 4)

    def test_fault_aborts_remaining_levels_and_excludes_reference(self):
        for options in ({"fault": True}, {"fault": True, "corrupt": True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                folder, links = self.capture(directory, **options)
                run = json.loads((folder / "run.json").read_text())
                self.assertFalse(run["complete"])
                self.assertEqual(len(run["sweeps"]), 1)
                self.assertFalse(self.supply.output)
                self.assertEqual(characterise.render(folder)["usable_reference_points"], 0)

    def test_corruption_warns_but_continues_to_next_level(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, _ = self.capture(directory, corrupt=True)
            run = json.loads((folder / "run.json").read_text())
            self.assertTrue(run["complete"])
            self.assertEqual(len(run["sweeps"]), 2)
            self.assertEqual(run["sweeps"][0]["data_quality"]["crc_errors"], 1)
            result = characterise.render(folder)
            self.assertEqual(result["sweeps"][0]["excluded_duty_visits"], 1)
            self.assertEqual(result["sweeps"][0]["data_quality"]["status"], "warning")
            with (folder / "settled_points.csv").open() as handle:
                points = list(csv.DictReader(handle))
            self.assertFalse(any(p["branch"] == "up" and p["duty_pct"] == "30.0" for p in points))
            # Offline old-run reclassification preserves acquisition history.
            run["complete"] = False
            run["error"] = "Sweep incomplete or data quality failed; see individual JSON and source log"
            for item in run["sweeps"]: item["complete"] = False
            characterise.save_json(folder / "run.json", run)
            original = (folder / "run.json").read_bytes()
            self.assertTrue(characterise.render(folder)["sweeps"][0]["complete"])
            self.assertEqual(original, (folder / "run.json").read_bytes())

    def test_entire_hold_excluded_for_early_late_and_boundary_gaps(self):
        from test_measure_iv_sweep import hold
        for gap in (5, 95, 100):
            rows = hold() + hold(.1, 405) + hold(.2, 410)
            rows[gap]["sequence_gap"] = 23
            points, excluded = characterise.intact_points(rows)
            self.assertEqual(excluded, 2 if gap == 100 else 1)
            self.assertNotIn(40, [p["duty_pct"] for p in points])
            if gap == 100:
                self.assertNotIn(40.5, [p["duty_pct"] for p in points])
            self.assertIn(41, [p["duty_pct"] for p in points])

    def test_completion_still_rejects_missing_end_and_acquisition_errors(self):
        summary = dict(end_received=True, samples=100, final_flags=measure.END | measure.IV_SWEEP | measure.IV_COMPLETE,
                       crc_errors=1, sequence_gaps=23, firmware_dropped=2)
        self.assertTrue(characterise.sweep.sweep_finished(summary))
        for field in ("fault", "restore_failed", "interrupted", "error", "firmware_errors"):
            self.assertFalse(characterise.sweep.sweep_finished(dict(summary, **{field: 1})))
        self.assertFalse(characterise.sweep.sweep_finished(dict(summary, end_received=False)))

    def test_boundary_maximum_is_not_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, _ = self.capture(directory, endpoint_peak=True)
            self.assertEqual(characterise.render(folder)["usable_reference_points"], 0)

    def test_serial_failure_switches_source_off(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(characterise, "stop_board"), \
                patch.object(measure.serial, "Serial", side_effect=OSError("serial disconnected")):
            folder = characterise.capture(self.supply, "MOCK", [.5, 1], output=Path(directory), settle=0, off_seconds=0)
            run = json.loads((folder / "run.json").read_text())
            self.assertFalse(run["complete"])
            self.assertTrue(run["output_off_confirmed"])
            self.assertFalse(self.supply.output)

    def test_wrong_source_mode_refuses_sweep(self):
        reading = self.supply.readback
        self.supply.readback = lambda: replace(reading(), mode="CV")
        with tempfile.TemporaryDirectory() as directory, patch.object(characterise, "stop_board"), \
                patch.object(measure, "record") as record:
            folder = characterise.capture(self.supply, "MOCK", [.5], output=Path(directory), settle=0, off_seconds=0)
            record.assert_not_called()
            self.assertFalse(json.loads((folder / "run.json").read_text())["complete"])
            self.assertFalse(self.supply.output)

    def test_source_monitor_failure_requests_recorder_stop(self):
        reading = self.supply.readback
        self.supply.readback = Mock(side_effect=[reading(), SupplyError("lost USB")])
        # The first reading must describe the source after enable.
        self.supply.readback.side_effect = [SupplyReading("mock", 35, .5, True, 28, .5, 14, "CC"), SupplyError("lost USB")]
        def recorder(*args, stop_event, **kwargs):
            self.assertTrue(stop_event.wait(2))
            return {"end_received": False}
        with tempfile.TemporaryDirectory() as directory, patch.object(characterise, "stop_board"), \
                patch.object(measure, "record", side_effect=recorder):
            folder = characterise.capture(self.supply, "MOCK", [.5, 1], output=Path(directory), settle=0, off_seconds=0)
            run = json.loads((folder / "run.json").read_text())
            self.assertEqual(run["sweeps"][0]["source_errors"], ["lost USB"])
            self.assertEqual(len(run["sweeps"]), 1)
            self.assertFalse(self.supply.output)

    def test_interrupt_keeps_partial_manifest_and_switches_off(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(characterise, "stop_board"), \
                patch.object(measure, "record", side_effect=KeyboardInterrupt):
            folder = characterise.capture(self.supply, "MOCK", [.5], output=Path(directory), settle=0, off_seconds=0)
            run = json.loads((folder / "run.json").read_text())
            self.assertFalse(run["complete"])
            self.assertTrue(run["output_off_confirmed"])

    def test_shutdown_failure_is_not_success(self):
        def output(enabled):
            if self.supply.output and not enabled:
                raise SupplyError("OFF failed")
            self.supply.output = enabled
        self.supply.set_output = output
        with tempfile.TemporaryDirectory() as directory, patch.object(characterise, "stop_board"), \
                patch.object(measure, "record", side_effect=RuntimeError("capture failed")):
            folder = characterise.capture(self.supply, "MOCK", [.5], output=Path(directory), settle=0, off_seconds=0)
            run = json.loads((folder / "run.json").read_text())
            self.assertFalse(run["complete"])
            self.assertFalse(run["output_off_confirmed"])
            self.assertIn("OFF failed", run["shutdown_error"])

    def test_bad_arguments_do_not_touch_hardware(self):
        for currents, timeout, settle in (([0], 175, 2), ([9], 175, 2), ([.5, .5], 175, 2),
                                          ([float("nan")], 175, 2), ([.5], 0, 2), ([.5], 175, -1)):
            with self.subTest(currents=currents), self.assertRaises(ValueError):
                characterise.validate(currents, timeout, settle)

    def test_default_off_pause_precedes_every_enable_including_first(self):
        sleeps = []
        def sleep(seconds):
            sleeps.append((seconds, self.supply.output))
        summary = dict(end_received=True, samples=100,
                       final_flags=measure.END | measure.IV_SWEEP | measure.IV_COMPLETE)
        with tempfile.TemporaryDirectory() as directory, patch.object(characterise, "stop_board"), \
                patch.object(characterise.time, "sleep", side_effect=sleep), \
                patch.object(measure, "record", return_value=summary):
            folder = characterise.capture(self.supply, "MOCK", [5, 5.5], output=Path(directory))
            run = json.loads((folder / "run.json").read_text())
            self.assertTrue(run["complete"])
            self.assertEqual(run["off_seconds"], 5)
            self.assertEqual(sleeps, [(5, False), (2, True), (5, False), (2, True)])
            self.assertTrue(all("off_pause_end_monotonic_s" in item for item in run["sweeps"]))
            self.assertFalse(self.supply.output)

    def test_invalid_off_pause_is_rejected_before_capture(self):
        for seconds in (-1, 61, float("nan"), float("inf")):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                characterise.validate([5], 55, 2, seconds)

    def test_interrupt_during_off_pause_never_enables_supply(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(characterise, "stop_board"), \
                patch.object(characterise.time, "sleep", side_effect=KeyboardInterrupt):
            folder = characterise.capture(self.supply, "MOCK", [5], output=Path(directory))
            run = json.loads((folder / "run.json").read_text())
            self.assertFalse(run["complete"])
            self.assertTrue(run["output_off_confirmed"])
            self.assertNotIn(("output", True), self.supply.events)


if __name__ == "__main__":
    unittest.main()
