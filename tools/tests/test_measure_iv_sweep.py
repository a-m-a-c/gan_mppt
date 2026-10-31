import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure
import measure_iv_sweep as iv
from test_measure import packet


def hold(start=0.0, duty=400, down=False, current=2000):
    flags = measure.VALID | measure.SWITCHING | measure.IV_SWEEP
    if down:
        flags |= measure.IV_DESCENDING
    return [{"t_s": start + n / 1000, "flags": flags, "channel": "A", "duty": duty,
             "sequence_gap": 0, "vin_mv": 25000, "iin_ma": 9000 if n < 20 else current,
             "vout_mv": 47000, "iout_ma": 1000} for n in range(100)]


class SweepTests(unittest.TestCase):
    def test_holds_exclude_transition_and_keep_directions_separate(self):
        rows = hold() + hold(.1, 405) + hold(.2, 400, down=True, current=1800)
        points = iv.extract_points(rows)
        self.assertEqual([p["branch"] for p in points], ["up", "up", "down"])
        for point, expected in zip(points, (2, 2, 1.8)):
            self.assertAlmostEqual(point["iin_a"], expected)
        self.assertEqual(points[0]["pin_w"], 50)
        self.assertGreaterEqual(points[0]["samples"], 79)

    def test_gap_and_ramp_are_not_one_settled_hold(self):
        rows = hold()[:50]
        rows[25]["sequence_gap"] = 1
        self.assertEqual(iv.extract_points(rows), [])
        self.assertEqual(iv.extract_points(hold()[:5]), [])

    def test_fifty_ms_hold_retains_settled_samples(self):
        points = iv.extract_points(hold()[:50] + hold(.05, duty=405)[:50])
        self.assertEqual(len(points), 2)
        for point in points:
            self.assertGreaterEqual(point["samples"], 29)
            self.assertAlmostEqual(point["pin_w"], 50)
        self.assertEqual(iv.extract_points(hold()[:39]), [])

    def test_fault_non_sweep_and_other_channels_excluded(self):
        for field, value in [("flags", measure.VALID | measure.SWITCHING),
                             ("flags", measure.VALID | measure.SWITCHING | measure.IV_SWEEP | measure.FAULT),
                             ("channel", "B")]:
            rows = hold()
            for row in rows:
                row[field] = value
            self.assertEqual(iv.extract_points(rows), [])

    def test_stop_does_not_count_as_completed_sweep(self):
        summary = {"end_received": True, "samples": 100, "final_flags": measure.END | measure.IV_SWEEP}
        self.assertFalse(iv.sweep_complete(summary))
        summary["final_flags"] |= measure.IV_COMPLETE
        self.assertTrue(iv.sweep_complete(summary))
        for key in ("fault", "restore_failed", "interrupted", "sequence_gaps", "firmware_dropped"):
            self.assertFalse(iv.sweep_complete({**summary, key: 1}))

    def test_iv_command_and_automatic_end(self):
        class Link:
            def __init__(self): self.data, self.commands = bytearray(), []
            def __enter__(self): return self
            def __exit__(self, *args): pass
            @property
            def in_waiting(self): return len(self.data)
            def reset_input_buffer(self): self.data.clear()
            def read(self, count):
                result = bytes(self.data[:count]); del self.data[:count]
                return result
            def write(self, data):
                self.commands.append(data[0])
                if data[0] == 0x0E:
                    self.data.extend(packet(flags=measure.VALID | measure.IV_SWEEP | measure.IV_DUTY_RAMP))
                    self.data.extend(packet(1, 2000, measure.END | measure.IV_SWEEP |
                                            measure.IV_DUTY_RAMP | measure.IV_COMPLETE))
        link = Link()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(measure.serial, "Serial", return_value=link), patch.object(measure, "plot_capture"):
                summary = measure.record("mock", 1, Path(directory) / "iv.csv",
                                         command="measure_iv_sweep",
                                         required_flags=measure.IV_SWEEP | measure.IV_DUTY_RAMP)
            self.assertEqual(link.commands, [0x03, 0x0E, 0x03])
            self.assertTrue(iv.sweep_complete(summary))

    def test_partial_plot_and_points_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "iv.csv"
            rows = hold() + hold(.1, down=True)
            with path.open("w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
                writer.writeheader(); writer.writerows(rows)
            path.with_suffix(".json").write_text(json.dumps({"end_received": False}))
            summary = iv.render(path)
            self.assertFalse(summary["iv_sweep"]["complete"])
            self.assertEqual(summary["iv_sweep"]["points"], 2)
            self.assertIn("INCOMPLETE", path.with_name("iv_iv.svg").read_text(encoding="utf-8"))
            self.assertTrue(path.with_name("iv_points.csv").exists())

    def test_firmware_lifecycle_and_protection(self):
        compiler = shutil.which("gcc")
        if not compiler:
            self.skipTest("Host GCC unavailable")
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            (temp / "main.h").write_text('#include <stdint.h>\nuint32_t HAL_GetTick(void);\n')
            binary = temp / "iv_sweep_test.exe"
            includes = [temp, root / "firmware/Inc", root / "firmware/Inc/app",
                        root / "firmware/Inc/app/modes", root / "firmware/Inc/drivers"]
            args = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror"]
            for include in includes:
                args += ["-I", str(include)]
            args += [str(root / "tools/tests/measure_iv_sweep_harness.c"),
                     str(root / "firmware/Src/app/modes/mode_measure_iv_sweep.c"),
                     "-lm", "-o", str(binary)]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("IV sweep lifecycle checks passed", result.stdout)

    def test_voltage_sweep_keeps_samples_while_duty_changes(self):
        flags = measure.VALID | measure.IV_SWEEP | measure.IV_VOLTAGE | measure.IV_LIMITED
        parsed = measure.Parser().feed(packet(flags=flags))[0]
        self.assertEqual(measure.Timeline().row(parsed, 0)["channel"], "A")
        rows = hold()
        for n, row in enumerate(rows):
            row["flags"] |= measure.IV_VOLTAGE
            row["duty"] = 400 + n
        points = iv.extract_raw_points(rows)
        self.assertEqual(len(points), len(rows))
        self.assertTrue(all(p["branch"] == "voltage_down" and p["samples"] == 1 for p in points))
        rows[0]["flags"] |= measure.FAULT
        self.assertEqual(len(iv.extract_raw_points(rows)), len(rows) - 1)

    def test_voltage_limit_renders_partial_single_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "iv.csv"
            rows = hold()
            for n, row in enumerate(rows):
                row["flags"] |= measure.IV_VOLTAGE
                row["duty"] = 400 + n
            with path.open("w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
                writer.writeheader(); writer.writerows(rows)
            path.with_suffix(".json").write_text(json.dumps({"end_received": True, "samples": 100,
                "final_flags": measure.END | measure.IV_SWEEP | measure.IV_VOLTAGE | measure.IV_LIMITED}))
            summary = iv.render(path)
            self.assertTrue(summary["iv_sweep"]["limited"])
            self.assertFalse(summary["iv_sweep"]["complete"])
            self.assertEqual(summary["iv_sweep"]["points"], 100)
            self.assertIn("LIMITED / partial", path.with_name("iv_iv.svg").read_text(encoding="utf-8"))

    def test_current_ramp_completes_without_return_branch_and_keeps_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "iv.csv"
            rows = hold()
            for row in rows:
                row["flags"] |= measure.IV_CURRENT_RAMP
                row["iin_ma"] = 7000
            rows[-1]["iin_ma"] = 7500
            with path.open("w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
                writer.writeheader(); writer.writerows(rows)
            path.with_suffix(".json").write_text(json.dumps({"end_received": True, "samples": 100,
                "final_flags": measure.END | measure.IV_SWEEP | measure.IV_CURRENT_RAMP | measure.IV_COMPLETE}))
            summary = iv.render(path)
            self.assertTrue(summary["iv_sweep"]["complete"])
            self.assertEqual(summary["iv_sweep"]["method"], "Direct PWM ramp to 7.5 A input")
            self.assertEqual(summary["iv_sweep"]["points"], 100)
            with path.with_name("iv_points.csv").open(newline="") as fh:
                points = list(csv.DictReader(fh))
            self.assertEqual(float(points[-1]["iin_a"]), 7.5)
            self.assertEqual(points[-1]["branch"], "current_ramp")

    def test_legacy_duty_ramp_replots_without_assuming_new_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "iv.csv"
            rows = hold(duty=499) + hold(.1, duty=500)
            for row in rows:
                row["flags"] |= measure.IV_DUTY_RAMP
            with path.open("w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
                writer.writeheader(); writer.writerows(rows)
            path.with_suffix(".json").write_text(json.dumps({"end_received": True, "samples": 200,
                "final_flags": measure.END | measure.IV_SWEEP | measure.IV_DUTY_RAMP | measure.IV_COMPLETE}))
            summary = iv.render(path)
            self.assertTrue(summary["iv_sweep"]["complete"])
            self.assertEqual(summary["iv_sweep"]["method"], "Direct PWM duty ramp")
            self.assertIn("Duty ramp: raw samples", path.with_name("iv_iv.svg").read_text(encoding="utf-8"))

    def test_return_ramp_has_separate_raw_branches_and_requires_both(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "iv.csv"
            def render(rows):
                for row in rows:
                    row["flags"] |= measure.IV_DUTY_RAMP | measure.IV_RETURN_RAMP
                with path.open("w", newline="") as fh:
                    writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
                    writer.writeheader(); writer.writerows(rows)
                path.with_suffix(".json").write_text(json.dumps({"end_received": True, "samples": len(rows),
                    "iv_sweep": {"requested_setup": {"frequency_hz": 600000}},
                    "final_flags": measure.END | measure.IV_SWEEP | measure.IV_DUTY_RAMP |
                                   measure.IV_RETURN_RAMP | measure.IV_DESCENDING | measure.IV_COMPLETE}))
                return iv.render(path)
            summary = render(hold(duty=750) + hold(.1, duty=749, down=True))
            self.assertTrue(summary["iv_sweep"]["complete"])
            self.assertEqual(set(summary["iv_sweep"]["measured_peaks"]), {"up", "down"})
            self.assertEqual(summary["iv_sweep"]["requested_setup"]["frequency_hz"], 600000)
            with path.with_name("iv_points.csv").open(newline="") as fh:
                points = list(csv.DictReader(fh))
            self.assertEqual([p["branch"] for p in points], ["up"] * 100 + ["down"] * 100)
            svg = path.with_name("iv_iv.svg").read_text(encoding="utf-8")
            self.assertIn("Duty up: raw samples", svg)
            self.assertIn("Duty down: raw samples", svg)
            self.assertFalse(render(hold(duty=750))["iv_sweep"]["complete"])

    def test_capture_defaults_use_upward_only_seventy_percent_sweep(self):
        with tempfile.TemporaryDirectory() as directory:
            def record(port, timeout, path, **kwargs):
                self.assertEqual(port, "mock")
                self.assertEqual(timeout, 95)
                self.assertEqual(kwargs["required_flags"], measure.IV_SWEEP | measure.IV_DUTY_RAMP)
                return {}
            def render(path):
                metadata = json.loads(path.with_suffix(".json").read_text())
                self.assertEqual(metadata["requested_setup"]["frequency_hz"], 600000)
                self.assertEqual(metadata["requested_setup"]["step_period_ms"], 100)
                self.assertEqual(metadata["requested_setup"]["maximum_duty_pct"], 70)
                self.assertEqual(metadata["requested_setup"]["direction"], "up")
                return {"iv_sweep": {"complete": True}}
            with patch.object(sys, "argv", ["measure_iv_sweep.py", "--port", "mock", "--output", directory]), \
                    patch.object(iv.measure, "record", side_effect=record), \
                    patch.object(iv, "render", side_effect=render):
                self.assertEqual(iv.main(), 0)


if __name__ == "__main__":
    unittest.main()
