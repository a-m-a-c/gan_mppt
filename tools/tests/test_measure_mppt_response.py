import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure_mppt_response as response


class ResponseTests(unittest.TestCase):
    def fixture(self, folder, gap=False):
        (folder / "run.json").write_text(json.dumps({"complete": True,
            "measurement": {"start_command_s": 0}, "profile": {"output_off_s": 6}}))
        with (folder / "profile.csv").open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["command_start_s", "setpoint_verified_s", "requested_current_a"])
            writer.writerows([(0, .01, 4), (2, 2.01, 2), (4, 4.01, 4)])
        with (folder / "measure.csv").open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["t_host_s", "t_s", "vin_mv", "iin_ma", "valid", "switching",
                             "fault", "restore_failed", "channel", "sequence_gap"])
            for n in range(60):
                t = n / 10
                target = 2000 if 2 <= t < 4 else 4000
                amps = target if n % 20 >= 5 else 1000
                writer.writerow([t, t + .02, 10000, amps, True, True, False, False, "A",
                                 int(gap and n % 3 == 0)])
        reference = folder / "ref.csv"
        reference.write_text("source_current_a,pin_w,reference_usable\n4,40,True\n2,20,True\n")
        return reference

    def test_profile_validation(self):
        self.assertEqual([(s.time_s, s.current_a) for s in response.build_profile(4, 2)],
                         [(0, 4), (30, 2), (60, 4)])
        for high, low, hold in [(2, 4, 30), (4, 0, 30), (9, 2, 30), (4, 2, -1),
                                (float("nan"), 2, 30), (4, 2, 2000)]:
            with self.assertRaises(ValueError):
                response.build_profile(high, low, hold)

    def test_known_response_and_plots(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            ref = self.fixture(folder)
            result = response.analyse_response(folder, ref, dwell_s=.4, max_gap_s=.11)
            for phase in result["phases"]:
                self.assertEqual(phase["status"], "settled")
                self.assertAlmostEqual(phase["settling_s"], .5)
            response.plot_response(folder, result)
            for suffix in ("csv", "json", "svg", "png"):
                self.assertGreater((folder / f"response.{suffix}").stat().st_size, 0)

    def test_gaps_prevent_false_settling(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            ref = self.fixture(folder, gap=True)
            result = response.analyse_response(folder, ref, dwell_s=.4, max_gap_s=.11)
            self.assertTrue(all(p["status"] == "not_settled" for p in result["phases"]))

    def test_missing_reference_and_start_are_not_invented(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.fixture(folder)
            result = response.analyse_response(folder)
            self.assertTrue(all(p["status"] == "missing_reference" for p in result["phases"]))
            (folder / "run.json").write_text('{"complete": false}')
            result = response.analyse_response(folder)
            self.assertEqual(result["phases"][0]["status"], "missing_start_event")
            self.assertFalse(result["complete_capture"])

    def test_replay_never_opens_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            self.fixture(Path(directory))
            with patch.object(sys, "argv", ["response", "--replay", directory]), \
                    patch.object(response.SPS5162X, "open") as open_supply:
                self.assertEqual(response.main(), 0)
                open_supply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
