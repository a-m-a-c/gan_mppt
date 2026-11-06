"""Poster panel 3: 8 A startup, -0.2 to +1.0 s from observed PWM activity.

Voltage/current use the capture's existing 10-sample means. Duty uses raw telemetry.
Zero is the first valid PWM-active sample, not a scope-observed switching edge.
Dashed MPP references are from a separate run, subject to source/thermal drift.
"""
import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAPTURE = ROOT / "captures/measure_startup/20261031-190115-751624_measure_mppt_profile"


def read_rows(path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def make_figure(capture):
    metadata = json.loads((capture / "startup_transient.json").read_text())
    rows = read_rows(capture / "measure.csv")
    valid = lambda row: (row["valid"] == "True" and row["fault"] == "False"
                         and row["restore_failed"] == "False" and row["channel"] == "A")
    start = next((float(row["t_s"]) for row in rows
                  if valid(row) and row["switching"] == "True"), None)
    if start is None or not np.isclose(start, metadata["first_switching_mcu_s"], atol=1e-6, rtol=0):
        raise ValueError("Startup origin is missing or differs from the averaged capture")
    profile = read_rows(capture / "profile.csv")
    if any(float(row["requested_current_a"]) != 8 for row in profile):
        raise ValueError("Expected a fixed 8 A source setting")
    averages = read_rows(capture / "startup_transient_average_10samples.csv")
    times = np.array([float(row["time_from_pwm_s"]) for row in averages])
    voltage = np.array([float(row["vin_v"]) for row in averages])
    current = np.array([float(row["iin_a"]) for row in averages])
    duty_times, duty, bus_voltage = [], [], []
    previous = None
    for row in rows:
        t = float(row["t_s"]) - start
        if not -.2 <= t <= 1:
            continue
        gap = int(row["sequence_gap"]) != 0 or (previous is not None and not 0 < t - previous <= .01)
        previous = t
        if gap or not valid(row):
            duty_times.append(t)
            duty.append(np.nan)
            bus_voltage.append(np.nan)
        if valid(row):
            duty_times.append(t)
            duty.append(float(row["duty"]) / 10)
            bus_voltage.append(float(row["vout_mv"]) / 1000)
    plt.rcParams.update({"font.family": "Arial", "font.size": 10,
                         "axes.labelsize": 11, "xtick.labelsize": 9, "ytick.labelsize": 9})
    fig, (voltage_ax, current_ax, duty_ax) = plt.subplots(
        3, 1, sharex=True, figsize=(22 / 2.54, 22 / 2.54),
        gridspec_kw={"hspace": .10})
    fig.subplots_adjust(left=.13, right=.95, bottom=.17, top=.90)
    for ax, values, key in ((voltage_ax, voltage, "vin_v"),
                             (current_ax, current, "iin_a")):
        ax.plot(times, values, color="#168b87", lw=1, label="Measured PV input")
        ax.axhline(metadata["reference_mpp"][key], color="#253342",
                   ls="--", lw=1.2, label="Separate-run MPP reference")
    voltage_ax.plot(duty_times, bus_voltage, color="#b86632", lw=1,
                    label="Bus voltage (measured output)")
    voltage_ax.set(ylabel="Voltage (V)", ylim=(0, max(35, np.nanmax(bus_voltage) * 1.1)))
    current_ax.set(ylabel="PV input current (A)", ylim=(-.2, 9))
    duty_ax.step(duty_times, duty, where="post", color="#596b80", lw=1.1)
    duty_ax.set(ylabel="PWM duty (%)", ylim=(0, max(60, np.nanmax(duty) * 1.1)),
                xlabel="Time from first PWM-active telemetry sample (s)", xlim=(-.2, 1))
    duty_ax.set_xticks(np.arange(-.2, 1.01, .2))
    for ax in (voltage_ax, current_ax, duty_ax):
        ax.axvline(0, color="#9aa4ae", ls=":", lw=.9)
        ax.grid(color="#e6e9ed", lw=.5)
        ax.set_axisbelow(True)
        for spine in ax.spines.values():
            spine.set_color("black")
            spine.set_linewidth(1)
    voltage_ax.set_title("MPPT startup at an 8 A source setting", fontsize=13,
                       fontweight="bold", pad=14)
    fig.legend(*voltage_ax.get_legend_handles_labels(), loc="lower center",
               bbox_to_anchor=(.54, .035), ncol=2, frameon=False, fontsize=10)
    print(f"First valid PWM-active sample: {start:.6f} s MCU time")
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent / "output/03_startup.png")
    args = parser.parse_args()
    fig = make_figure(args.capture)
    output = args.output.with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=300, facecolor="white")
    plt.close(fig)
    print(output)


if __name__ == "__main__":
    main()
