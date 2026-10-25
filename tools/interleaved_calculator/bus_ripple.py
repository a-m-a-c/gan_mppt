"""Shared-capacitor ripple estimates with constant load current.

Model each boost output as zero current during switch-on and a constant
Iout / (1 - duty) during switch-off. Integrate their sum minus the load
current using dV = Icap * dt / C. This neglects inductor-current ripple,
ESR/ESL, dead time, output isolation and battery impedance.

For one converter this reduces to Vpp = Iout * duty / (frequency * C):
https://www.ti.com/lit/an/slva372d/slva372d.pdf
"""

from collections.abc import Sequence
from math import isfinite


def calculate_bus_waveform(
    duty_cycles: Sequence[float],
    output_currents_a: Sequence[float],
    switching_frequency_hz: float,
    bus_capacitance_f: float,
    phase_offsets_deg: Sequence[float],
) -> tuple[list[float], list[float], list[float]]:
    """Return edge times (s), interval capacitor currents (A), and voltages (V).

    Phase specifies each low-side switch's turn-on delay, modulo 360 degrees.
    All converters use the same frequency. Currents
    are cycle averages; capacitance is the effective total at the common bus.
    There is one current per interval, and one voltage per edge. Voltage is
    relative to the start of the cycle, not relative to its mean.
    """
    if not duty_cycles or len(duty_cycles) != len(output_currents_a):
        raise ValueError("Provide one duty and output current per converter.")
    if len(phase_offsets_deg) != len(duty_cycles) or any(
        not isfinite(phase) for phase in phase_offsets_deg
    ):
        raise ValueError("Provide one finite phase offset per converter.")
    if not isfinite(switching_frequency_hz) or switching_frequency_hz <= 0:
        raise ValueError("Switching frequency must be positive and finite.")
    if not isfinite(bus_capacitance_f) or bus_capacitance_f <= 0:
        raise ValueError("Bus capacitance must be positive and finite.")
    if any(not isfinite(duty) or not 0 <= duty < 1 for duty in duty_cycles):
        raise ValueError("Duty cycles must be finite fractions from 0 to below 1.")
    if any(not isfinite(current) or current < 0 for current in output_currents_a):
        raise ValueError("Output currents must be nonnegative and finite.")

    events = [(1.0, 0.0)]
    capacitor_current = -sum(output_currents_a)
    for duty, current, phase in zip(duty_cycles, output_currents_a, phase_offsets_deg):
        if duty == 0:
            capacitor_current += current
            continue
        pulse_current = current / (1 - duty)
        switch_on = (phase % 360) / 360
        output_start = (switch_on + duty) % 1.0
        # Wrapped output pulses are already active just before phase zero.
        if output_start > switch_on:
            capacitor_current += pulse_current
        events.append((output_start, pulse_current))
        events.append((switch_on, -pulse_current))

    previous_phase = 0.0
    times = [0.0]
    currents = []
    voltages = [0.0]
    for phase, current_step in sorted(events):
        if phase > previous_phase:
            duration = (phase - previous_phase) / switching_frequency_hz
            voltages.append(voltages[-1] + capacitor_current * duration / bus_capacitance_f)
            times.append(phase / switching_frequency_hz)
            currents.append(capacitor_current)
        capacitor_current += current_step
        previous_phase = phase

    return times, currents, voltages


def calculate_bus_ripple(
    duty_cycles: Sequence[float],
    output_currents_a: Sequence[float],
    switching_frequency_hz: float,
    bus_capacitance_f: float,
    phase_offsets_deg: Sequence[float],
) -> float:
    """Return exact peak-to-peak voltage for the rectangular-pulse model."""
    _, _, voltages = calculate_bus_waveform(
        duty_cycles, output_currents_a, switching_frequency_hz,
        bus_capacitance_f, phase_offsets_deg,
    )
    return max(voltages) - min(voltages)


def calculate_in_phase_bus_ripple(
    duty_cycles: Sequence[float],
    output_currents_a: Sequence[float],
    switching_frequency_hz: float,
    bus_capacitance_f: float,
) -> float:
    """Return ripple with every converter at zero phase offset."""
    return calculate_bus_ripple(
        duty_cycles, output_currents_a, switching_frequency_hz,
        bus_capacitance_f, [0.0] * len(duty_cycles),
    )
