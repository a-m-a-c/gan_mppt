from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import current_profile as profile
from sps5162x import SPS5162X, SupplyError, SupplyReading


RESOURCE = "USB0::0xF4EC::0x1452::TEST::INSTR"


class Instrument:
    resource_name = RESOURCE
    def __init__(self):
        self.commands = []
        self.current, self.voltage, self.output = 8.0, 35.0, False
        self.identity = "Siglent Technologies,SPS5162X,TEST,3.1.1.8R1"
        self.status, self.list_mode, self.off_delay = 0, "0", 0
        self.ignore_current, self.closed = False, False
    def query(self, command):
        self.commands.append(command)
        replies = {"*IDN?": self.identity, "*OPC?": "1", "*ESR?": str(self.status),
                   ":SOURce:CURRent:SET? CH1": str(self.current),
                   ":SOURce:VOLTage:SET? CH1": str(self.voltage),
                   ":SOURce:OUTPut?": str(int(self.output)),
                   ":MEASure:VOLTage? CH1": str(self.voltage if self.output else 0),
                   ":MEASure:CURRent? CH1": "0.00002", ":MEASure:POWer? CH1": "0",
                   ":MEASure:RUN:MODE? CH1": "CV", ":SOURce:LIST:STATe? CH1": self.list_mode,
                   ":SOURce:CTRL:VOLTage:MODE? CH1": "LOCAL",
                   ":SOURce:CTRL:CURRent:MODE? CH1": "LOCAL",
                   ":SOURce:OUTPut:ON:DELay? CH1": "0", ":SOURce:OUTPut:OFF:DELay? CH1": str(self.off_delay)}
        return replies[command]
    def write(self, command):
        self.commands.append(command)
        if command.startswith(":SOURce:CURRent:SET "):
            if not self.ignore_current: self.current = float(command.rsplit(",", 1)[1])
        elif command.startswith(":SOURce:VOLTage:SET "):
            self.voltage = float(command.rsplit(",", 1)[1])
        elif command.startswith(":SOURce:OUTPut "):
            self.output = command.endswith("1")
        else: raise AssertionError(command)
    def close(self): self.closed = True


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.instrument = Instrument()
        self.manager = Mock()
        self.supply = SPS5162X(self.manager, self.instrument, self.instrument.identity, 8, 35)

    def test_discovery_closes_resource_manager_without_context_protocol(self):
        self.manager.list_resources.return_value = (RESOURCE, "USB0::0xF4EC::0xEE38::SCOPE::INSTR")
        with patch("sps5162x.pyvisa.ResourceManager", return_value=self.manager):
            self.assertEqual(SPS5162X.resources(), (RESOURCE,))
        self.manager.close.assert_called_once()

    def test_identity_guard_and_open_close_do_not_enable_output(self):
        self.manager.open_resource.return_value = self.instrument
        with patch("sps5162x.pyvisa.ResourceManager", return_value=self.manager):
            with SPS5162X.open(RESOURCE): pass
            self.assertEqual(self.instrument.commands, ["*IDN?"])
            self.assertTrue(self.instrument.closed)
            self.instrument.identity = "Siglent Technologies,SDL1000X,TEST,1"
            with self.assertRaises(SupplyError): SPS5162X.open(RESOURCE)
        self.assertNotIn(":SOURce:OUTPut 1", self.instrument.commands)

    def test_current_and_voltage_are_verified(self):
        self.assertEqual(self.supply.set_current_limit(0), 0)
        self.assertEqual(self.supply.set_current_limit(8), 8)
        self.assertEqual(self.supply.set_voltage_limit(2), 2)
        self.assertFalse(self.instrument.output)
        self.instrument.ignore_current = True
        with self.assertRaises(SupplyError): self.supply.set_current_limit(1)

    def test_out_of_range_nonfinite_and_sub_milli_values_never_write(self):
        for bad in [-1, 8.001, float("nan"), float("inf"), True, .0001]:
            with self.subTest(value=bad), self.assertRaises(ValueError): self.supply.set_current_limit(bad)
        for bad in [-1, 35.001, float("nan"), .0001]:
            with self.subTest(value=bad), self.assertRaises(ValueError): self.supply.set_voltage_limit(bad)
        self.assertEqual(self.instrument.commands, [])

    def test_scpi_error_rejected(self):
        self.instrument.status = 32
        with self.assertRaisesRegex(SupplyError, "SCPI error"): self.supply.set_current_limit(1)

    def test_enable_requires_bench_limits_manual_control_and_no_delays(self):
        for field, invalid in [("current", 9), ("voltage", 36), ("list_mode", "1"), ("off_delay", 2)]:
            before = getattr(self.instrument, field)
            setattr(self.instrument, field, invalid)
            with self.subTest(field=field), self.assertRaises((ValueError, SupplyError)):
                self.supply.set_output(True)
            self.assertFalse(self.instrument.output)
            setattr(self.instrument, field, before)
        self.supply.set_output(True)
        self.assertTrue(self.supply.readback().output_enabled)
        self.supply.set_output(False)
        self.assertFalse(self.supply.readback().output_enabled)


class Clock:
    now = 0.0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.supply = Mock(identity="Siglent Technologies,SPS5162X,TEST,1", resource=RESOURCE,
                           max_current_a=8, max_voltage_v=35)
        self.supply.readback.return_value = SupplyReading("2026-10-30T00:00:00+00:00", 35, 1, True, 30, 1, 30, "CC")
        self.steps = [profile.CurrentStep(0, 1), profile.CurrentStep(1, 8)]

    def test_ramp_and_csv(self):
        steps = profile.ramp_steps(0, 8, 3, .25)
        self.assertEqual(steps[0], profile.CurrentStep(0, 0))
        self.assertEqual(steps[-1], profile.CurrentStep(3, 8))
        self.assertEqual(len(steps), 13)
        slow = profile.ramp_steps(0, 8, 120)
        self.assertEqual(len(slow), 481)
        self.assertEqual(slow[-1], profile.CurrentStep(120, 8))
        self.assertTrue(all(0.0159 <= b.current_a - a.current_a <= 0.0171
                            for a, b in zip(slow, slow[1:])))
        self.assertEqual(profile.load_steps(Path(__file__).resolve().parents[1] / "profiles/irradiance_steps.csv")[-1].time_s, 30)

    def test_invalid_profile_is_rejected_before_output_changes(self):
        for steps in [[], [profile.CurrentStep(1, 1)], [profile.CurrentStep(0, 9)],
                      [profile.CurrentStep(0, 1), profile.CurrentStep(0, 2)],
                      [profile.CurrentStep(0, 1), profile.CurrentStep(float("nan"), 2)]]:
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                profile.run_current_profile(self.supply, steps)
        self.supply.set_output.assert_not_called()

    def run_profile(self, directory):
        clock = Clock()
        with patch.object(profile.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(profile.time, "sleep", side_effect=clock.sleep), patch.object(profile, "plot_profile"):
            return profile.run_current_profile(self.supply, self.steps, output_dir=Path(directory))

    def test_complete_and_timestamped_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_profile(directory)
            self.assertTrue(result["complete"] and result["output_off_confirmed"])
            self.assertEqual(result["samples"], 2)
            self.assertEqual(self.supply.set_output.call_args_list[-1].args, (False,))
            text = (Path(result["folder"]) / "profile.csv").read_text()
            self.assertIn("scheduled_s,command_start_s,setpoint_verified_s,readback_end_s", text)

    def test_failure_and_keyboard_interrupt_preserve_partial_files_and_stop(self):
        for error in [SupplyError("read failed"), KeyboardInterrupt()]:
            self.supply.readback.side_effect = error
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(type(error)): self.run_profile(directory)
                summary = json.loads(next(Path(directory).glob("*/summary.json")).read_text())
                self.assertFalse(summary["complete"])
                self.assertTrue(summary["output_off_confirmed"])
                self.assertEqual(self.supply.set_output.call_args_list[-1].args, (False,))

    def test_unexpected_output_stop_fails(self):
        self.supply.readback.return_value = replace(self.supply.readback.return_value, output_enabled=False)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SupplyError): self.run_profile(directory)
            self.assertEqual(self.supply.set_output.call_args_list[-1].args, (False,))

    def test_shutdown_failure_never_reports_complete(self):
        self.supply.set_output.side_effect = [None, None, SupplyError("lost USB")]
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_profile(directory)
            self.assertFalse(result["complete"] or result["output_off_confirmed"])
            self.assertIn("shutdown_error", result)

    def test_late_commands_abort_instead_of_catching_up(self):
        clock = Clock()
        self.supply.set_output.side_effect = lambda enabled: clock.sleep(2) if enabled else None
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(profile.time, "monotonic", side_effect=clock.monotonic), patch.object(profile, "plot_profile"):
                with self.assertRaisesRegex(SupplyError, "deadline"):
                    profile.run_current_profile(self.supply, self.steps, output_dir=Path(directory))
            self.assertEqual(self.supply.set_output.call_args_list[-1].args, (False,))

    def test_cancelled_profile_never_enables_output(self):
        stop = Event()
        stop.set()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SupplyError, "cancelled"):
                profile.run_current_profile(self.supply, self.steps, output_dir=Path(directory),
                                             stop_event=stop, make_plots=False)
            self.assertFalse(any(call.args == (True,) for call in self.supply.set_output.call_args_list))

    def test_cancellation_interrupts_wait_and_confirms_shutdown(self):
        stop = Event()
        self.supply.readback.side_effect = lambda: (stop.set() or SupplyReading(
            "2026-10-30", 35, 1, True, 30, 1, 30, "CC"))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SupplyError, "cancelled"):
                profile.run_current_profile(self.supply, self.steps, output_dir=Path(directory),
                                             stop_event=stop, make_plots=False)
            self.assertEqual(self.supply.set_output.call_args_list[-1].args, (False,))


if __name__ == "__main__":
    unittest.main()
