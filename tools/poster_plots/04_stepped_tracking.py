"""Poster panel 4: 1-8 A stepped MPPT tracking with nominal 10 s plateaus.

Retains supplied 10-sample means and NaN gaps. Host timing is approximate;
reference MPP powers are from separate I-V sweeps, so source/thermal drift applies.
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
DEFAULT_CAPTURE = ROOT / "captures/measure_5_redo/timescale/20261031-180349-346540_measure_mppt_profile"
STEM = "mppt_timescale_average_10samples"


def read_rows(path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def make_figure(capture):
    comparison = capture / "comparison"
    rows = read_rows(comparison / f"{STEM}.csv")
    metadata = json.loads((comparison / f"{STEM}.json").read_text())
    steps = metadata["reference_steps"]
    events = read_rows(capture / "profile.csv")
    origin = float(events[0]["command_start_s"])
    times = np.array([float(row["mean_host_s"]) - origin for row in rows])
    power = np.array([float(row["mean_pin_w"]) for row in rows])
    if not steps or not np.isfinite(power).any():
        raise ValueError("Expected reference steps and finite input power samples")
    boundaries = [step["start_s"] for step in steps] + [steps[-1]["end_s"]]
    currents = [step["source_current_a"] for step in steps]
    reference_power = [step["pmpp_w"] for step in steps]

    plt.rcParams.update({
        "font.family": "Arial", "font.size": 10,
        "axes.labelsize": 11, "xtick.labelsize": 9, "ytick.labelsize": 9,
        "svg.fonttype": "none", "pdf.fonttype": 42,
    })
    # 2.54 cm per inch; retain a square poster canvas.
    fig, (source_ax, power_ax) = plt.subplots(
        2, 1, sharex=True, figsize=(22 / 2.54, 22 / 2.54),
        gridspec_kw={"height_ratios": [1, 4], "hspace": .07})
    fig.subplots_adjust(left=.13, right=.95, bottom=.16, top=.90)
    source_ax.step(boundaries, currents + [currents[-1]], where="post",
                   color="#596b80", lw=1.2)
    source_ax.set(ylabel="Source current\nsetting (A)", ylim=(0, 9), yticks=[1, 4, 8])
    power_ax.plot(times, power, color="#168b87", lw=.75,
                  label="Measured PV input power")
    power_ax.step(boundaries, reference_power + [reference_power[-1]],
                  where="post", color="#253342", ls="--", lw=1.2,
                  label="Reference MPP power")
    power_ax.set(xlabel="Time from first source command (s)",
                 ylabel="PV input power (W)", xlim=(0, boundaries[-1]),
                 ylim=(0, 220), yticks=[0, 50, 100, 150, 200])
    power_ax.set_xticks(np.arange(0, 81, 10))
    source_ax.tick_params(axis="x", labelbottom=False)
    for ax in (source_ax, power_ax):
        ax.grid(color="#e6e9ed", lw=.5)
        ax.set_axisbelow(True)
        for spine in ax.spines.values():
            spine.set_color("black")
            spine.set_linewidth(1)
    source_ax.set_title("MPPT tracking through 1-8 A source steps",
                        fontsize=13, fontweight="bold", pad=14)
    fig.legend(*power_ax.get_legend_handles_labels(), loc="lower center",
               bbox_to_anchor=(.54, .065), ncol=2, frameon=False, fontsize=10)
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent / "output/04_stepped_tracking",
                        help="Output stem (writes .png)")
    args = parser.parse_args()
    fig = make_figure(args.capture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    path = args.output.with_suffix(".png")
    fig.savefig(path, dpi=300, facecolor="white")
    print(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
