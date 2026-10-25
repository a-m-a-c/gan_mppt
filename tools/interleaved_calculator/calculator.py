import argparse
import json
from pathlib import Path

from .solar_panels import SolarPanel
from .converter import BoostConverter
from .bus_ripple import calculate_bus_ripple
from .phase_optimisation import optimise_phases
from .plots import plot_phase_comparison


def main(show_plots: bool = True) -> None:
    battery_voltage = 48.0
    irradiance = 1000.0  # W/m²
    efficiency = 0.95
    switching_frequency_hz = 300_000.0
    inductance_h = 33e-6  # 33 uH
    output_capacitance_f = 48.84e-6  # 48.84 uF
    bus_capacitance_f = 5 * output_capacitance_f  # Assumes five capacitors directly in parallel.

    f1 = SolarPanel(voc=30.50, isc=8.66, vmpp=27.59, impp=8.22, irradiance=irradiance)
    bl = SolarPanel(voc=17.86, isc=8.66, vmpp=16.15, impp=8.22, irradiance=irradiance)
    br = SolarPanel(voc=17.86, isc=8.66, vmpp=16.15, impp=8.22, irradiance=irradiance)
    r1 = SolarPanel(voc=23.81, isc=8.66, vmpp=21.54, impp=8.22, irradiance=irradiance)
    r2 = SolarPanel(voc=13.39, isc=8.66, vmpp=12.11, impp=8.22, irradiance=irradiance)

    def print_converter(name: str, panel: SolarPanel) -> BoostConverter:
        input_current = panel.get_impp()
        converter = BoostConverter(
            vin=panel.vmpp,
            iin=input_current,
            vout=battery_voltage,
            efficiency=efficiency,
            switching_frequency_hz=switching_frequency_hz,
            inductance_h=inductance_h,
            output_capacitance_f=output_capacitance_f,
        )
        print(
            f"{name:<6}"
            f"{converter.get_duty_cycle():>10.2%}"
            f"{input_current:>12.3f}"
            f"{converter.get_inductor_ripple_current():>18.3f}"
            f"{converter.get_peak_inductor_current():>16.3f}"
            f"{converter.get_output_ripple_voltage() * 1000:>20.2f}"
        )
        return converter

    print("\nBoost converter results")
    print(
        f"Bus: {battery_voltage:g} V | Irradiance: {irradiance:g} W/m^2 | "
        f"Efficiency: {efficiency:.1%}"
    )
    print(
        f"Switching: {switching_frequency_hz / 1000:g} kHz | "
        f"L: {inductance_h * 1e6:g} uH | Cout: {output_capacitance_f * 1e6:g} uF\n"
    )
    header = (
        f"{'Panel':<6}{'Duty':>10}{'Input (A)':>12}"
        f"{'IL ripple (A pp)':>18}{'IL peak (A)':>16}{'Vout ripple (mV pp)':>20}"
    )
    print(header)
    print("-" * len(header))
    converter_f1 = print_converter("F1", f1)
    converter_bl = print_converter("BL", bl)
    converter_br = print_converter("BR", br)
    converter_r1 = print_converter("R1", r1)
    converter_r2 = print_converter("R2", r2)
    print("\nPer-converter CCM estimates; capacitor ESR and interleaving excluded.")

    converters = (converter_f1, converter_bl, converter_br, converter_r1, converter_r2)
    duty_cycles = [converter.get_duty_cycle() for converter in converters]
    output_currents_a = [converter.get_output_current() for converter in converters]
    zero_phase_ripple = calculate_bus_ripple(
        duty_cycles, output_currents_a, switching_frequency_hz,
        bus_capacitance_f, phase_offsets_deg=(0, 0, 0, 0, 0),
    )
    interleaved_ripple = calculate_bus_ripple(
        duty_cycles, output_currents_a, switching_frequency_hz,
        bus_capacitance_f, phase_offsets_deg=(0, 72, 144, 216, 288),
    )
    print("\nSearching phases with four reproducible optimisation runs...", flush=True)
    optimal_phases, search_ripples = optimise_phases(
        duty_cycles, output_currents_a, switching_frequency_hz, bus_capacitance_f,
    )
    optimal_ripple = calculate_bus_ripple(
        duty_cycles, output_currents_a, switching_frequency_hz,
        bus_capacitance_f, optimal_phases,
    )
    print("\nCombined bus comparison (same power and capacitance)")
    print(f"Effective bus capacitance: {bus_capacitance_f * 1e6:g} uF")
    print("Phase order: F1, BL, BR, R1, R2")
    print(f"{'Case':<20}{'Bus ripple (mV pp)':>20}")
    print("-" * 40)
    print(f"{'All in phase':<20}{zero_phase_ripple * 1000:>20.3f}")
    print(f"{'72-degree spacing':<20}{interleaved_ripple * 1000:>20.3f}")
    print(f"{'Optimised (best)':<20}{optimal_ripple * 1000:>20.3f}")
    print("\nLow-side turn-on phases (degrees)")
    print(f"{'Panel':<8}{'Aligned':>12}{'72-deg':>12}{'Optimised':>14}")
    names = ("F1", "BL", "BR", "R1", "R2")
    for index, (name, phase) in enumerate(zip(names, optimal_phases)):
        print(f"{name:<8}{0:>12.3f}{72 * index:>12.3f}{phase:>14.3f}")
    if zero_phase_ripple > 1e-12:
        print(f"Optimised reduction vs aligned: {100 * (1 - optimal_ripple / zero_phase_ripple):.2f}%")
    if interleaved_ripple > 1e-12:
        print(f"Optimised reduction vs 72-degree spacing: {100 * (1 - optimal_ripple / interleaved_ripple):.2f}%")
    print("Search results (mV pp): " + ", ".join(f"{value * 1000:.6f}" for value in search_ripples))
    print("Best numerical solution found; global optimality is not proven. F1 is fixed at 0 degrees.")
    print("Constant output-pulse approximation; inductor ripple, ESR and battery impedance excluded.")

    cases = [
        ("Aligned", [0.0] * 5),
        ("72-degree spacing", [0, 72, 144, 216, 288]),
        ("Optimised", optimal_phases),
    ]
    output_dir = Path(__file__).parent / "out"
    output_dir.mkdir(exist_ok=True)
    results = {
        "channel_order": names, "frequency_hz": switching_frequency_hz,
        "bus_voltage_v": battery_voltage, "bus_capacitance_f": bus_capacitance_f,
        "duty_cycles": duty_cycles, "output_currents_a": output_currents_a,
        "search_seeds": [7, 23, 61, 109], "search_ripple_v_pp": search_ripples,
        "model": "Rectangular output pulses; constant load; ideal shared capacitance",
        "optimality": "Best numerical result found; no global proof",
        "cases": [{"name": label, "phases_deg": phases,
                   "ripple_v_pp": calculate_bus_ripple(duty_cycles, output_currents_a,
                                                       switching_frequency_hz, bus_capacitance_f, phases)}
                  for label, phases in cases],
    }
    (output_dir / "phase_results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"\nSaving plots and phase_results.json to {output_dir}", flush=True)
    plot_phase_comparison(
        names, duty_cycles, output_currents_a, switching_frequency_hz,
        bus_capacitance_f, cases, output_dir, show=show_plots,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare and optimise five boost-converter phases.")
    parser.add_argument("--no-show", action="store_true", help="Save plots without opening windows.")
    main(show_plots=not parser.parse_args().no_show)
