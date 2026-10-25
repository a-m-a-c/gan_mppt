import unittest

import numpy as np

from tools.interleaved_calculator.bus_ripple import calculate_bus_ripple, calculate_bus_waveform
from tools.interleaved_calculator.phase_optimisation import optimise_phases


class BusRippleTests(unittest.TestCase):
    def test_single_converter_matches_capacitor_discharge(self):
        for phase in (0, 100, 359, -360):
            self.assertAlmostEqual(calculate_bus_ripple([0.4], [2], 100_000, 100e-6, [phase]), 0.08)

    def test_equal_pulses_cancel_at_even_spacing(self):
        for duties, phases in (([0.5] * 2, [0, 180]), ([0.8] * 5, [0, 72, 144, 216, 288])):
            ripple = calculate_bus_ripple(duties, [1] * len(duties), 100_000, 100e-6, phases)
            self.assertAlmostEqual(ripple, 0.0)

    def test_aligned_parallel_scaling(self):
        single = calculate_bus_ripple([0.4], [2], 100_000, 100e-6, [0])
        combined = calculate_bus_ripple([0.4] * 5, [2] * 5, 100_000, 500e-6, [0] * 5)
        self.assertAlmostEqual(single, combined)

    def test_waveform_agrees_with_independent_time_sampling(self):
        duties = [0.4252083333, 0.6635416667, 0.6635416667, 0.55125, 0.7477083333]
        currents = [4.488548125, 2.627403125, 2.627403125, 3.50428875, 1.970145625]
        # Include wraparound, coincident edges, and the numerical search result.
        phase_sets = ([0] * 5, [0, 72, 144, 216, 288], [359, 5, 203, -30, 720],
                      [0, 348.776, 222.155, 151.392, 96.650])
        count = 200_000
        x = (np.arange(count) + 0.5) / count
        for phases in phase_sets:
            with self.subTest(phases=phases):
                capacitor_current = np.full(count, -sum(currents))
                for duty, current, phase in zip(duties, currents, phases):
                    capacitor_current += (((x - phase / 360) % 1) >= duty) * current / (1 - duty)
                sampled_voltage = np.concatenate(([0], np.cumsum(capacitor_current) / (count * 300_000 * 244.2e-6)))
                times, interval_currents, voltages = calculate_bus_waveform(duties, currents, 300_000, 244.2e-6, phases)
                self.assertEqual(len(times), len(interval_currents) + 1)
                self.assertEqual(len(times), len(voltages))
                self.assertAlmostEqual(voltages[-1], 0.0, places=12)
                self.assertAlmostEqual(times[-1], 1 / 300_000)
                self.assertLess(abs(np.ptp(voltages) - np.ptp(sampled_voltage)), 5e-6)
                rotated = calculate_bus_ripple(duties, currents, 300_000, 244.2e-6, [p + 53 for p in phases])
                self.assertAlmostEqual(rotated, np.ptp(voltages), places=12)

    def test_zero_duty_and_zero_load(self):
        self.assertAlmostEqual(calculate_bus_ripple([0, 0], [1, 2], 100_000, 100e-6, [15, 270]), 0)
        self.assertAlmostEqual(calculate_bus_ripple([0.4, 0.7], [0, 0], 100_000, 100e-6, [15, 270]), 0)

    def test_invalid_phases_and_parameters(self):
        for phases in ([0], [0, float('nan')]):
            with self.assertRaises(ValueError):
                calculate_bus_ripple([0.5, 0.5], [1, 1], 100_000, 100e-6, phases)
        for frequency, capacitance in ((0, 100e-6), (100_000, 0)):
            with self.assertRaises(ValueError):
                calculate_bus_ripple([0.5], [1], frequency, capacitance, [0])

    def test_search_can_find_known_zero_ripple(self):
        phases, _ = optimise_phases([0.5, 0.5], [1, 1], 100_000, 100e-6, seeds=(7,))
        self.assertEqual(phases[0], 0)
        self.assertLess(calculate_bus_ripple([0.5, 0.5], [1, 1], 100_000, 100e-6, phases), 1e-9)

    def test_search_retains_baselines_for_unequal_channels(self):
        duties, currents = [0.3, 0.5, 0.7], [1, 2, 1]
        phases, _ = optimise_phases(duties, currents, 100_000, 100e-6, seeds=(7,))
        best = calculate_bus_ripple(duties, currents, 100_000, 100e-6, phases)
        for baseline in ([0, 0, 0], [0, 120, 240]):
            self.assertLessEqual(best, calculate_bus_ripple(duties, currents, 100_000, 100e-6, baseline))
        self.assertEqual(phases[0], 0)
        self.assertTrue(all(0 <= phase < 360 for phase in phases))


if __name__ == '__main__':
    unittest.main()
