"""Zoom a complete 2-8-2 A cycle from the supplied averaged MPPT capture.

Host command timing includes supply response; it is not controller latency.
Retains the supplied 10-sample means and gaps without additional smoothing.
Reference MPP powers are from separate I-V sweeps, subject to source/thermal drift.
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
DEFAULT_CSV = ROOT / "captures/measure_mppt_dips/20261031-204803-390966_measure_mppt_profile/comparison/mppt_timescale_average_10samples.csv"


def read_rows(path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def crossing_time(times, values, threshold, rising):
    """Linearly interpolate the first threshold crossing between adjacent means."""
    for t0, t1, y0, y1 in zip(times[:-1], times[1:], values[:-1], values[1:]):
        crossed = y0 < threshold <= y1 if rising else y0 > threshold >= y1
        if crossed and np.isfinite([t0, t1, y0, y1]).all():
            return t0 + (threshold - y0) * (t1 - t0) / (y1 - y0)
    raise ValueError(f"No crossing found for {threshold:g} W")


def make_figure(csv_path, cycle, before, after):
    metadata = json.loads(csv_path.with_suffix(".json").read_text())
    steps = metadata["reference_steps"]
    candidates = [i for i in range(1, len(steps) - 1)
                  if [steps[j]["source_current_a"] for j in (i-1, i, i+1)] == [2, 8, 2]]
    if not 1 <= cycle <= len(candidates):
        raise ValueError(f"Choose a cycle from 1 to {len(candidates)}")
    index = candidates[cycle - 1]
    origin = steps[index]["start_s"]
    fall = steps[index]["end_s"] - origin
    left, right = -before, fall + after
    if origin + left < steps[index-1]["start_s"] or origin + right > steps[index+1]["end_s"]:
        raise ValueError("Window extends beyond this 2-8-2 cycle; reduce --before or --after")
    profile = read_rows(csv_path.parent.parent / "profile.csv")
    host_origin = float(profile[0]["command_start_s"]) + origin
    rows = read_rows(csv_path)
    times = np.array([float(row["mean_host_s"]) - host_origin for row in rows])
    power = np.array([float(row["mean_pin_w"]) for row in rows])
    keep = (times >= left) & (times <= right)
    if not np.isfinite(power[keep]).any():
        raise ValueError("No finite input power samples in the selected window")
    edges = [left, 0, fall, right]
    reference = [steps[j]["pmpp_w"] for j in (index-1, index, index+1)]
    # Use the final half of each visible plateau, away from the transition.
    low_before = np.nanmedian(power[(times >= left) & (times <= left / 2)])
    high = np.nanmedian(power[(times >= fall / 2) & (times < fall)])
    low_after = np.nanmedian(power[(times >= fall + after / 2) & (times <= right)])
    transitions = []
    for rising, start, end, low in ((True, 0, fall, low_before),
                                     (False, fall, right, low_after)):
        # Include one sample before the command to bracket the first crossing.
        first = max(0, np.searchsorted(times, start) - 1)
        last = np.searchsorted(times, end)
        fractions = (.1, .9) if rising else (.9, .1)
        crossings = [crossing_time(times[first:last], power[first:last],
                                   low + fraction * (high - low), rising)
                     for fraction in fractions]
        transitions.append((rising, *crossings))

    plt.rcParams.update({"font.family": "Arial", "font.size": 10,
                         "axes.labelsize": 11, "xtick.labelsize": 9, "ytick.labelsize": 9})
    fig, (source_ax, power_ax) = plt.subplots(
        2, 1, sharex=True, figsize=(22 / 2.54, 22 / 2.54),
        gridspec_kw={"height_ratios": [1, 4], "hspace": .07})
    fig.subplots_adjust(left=.13, right=.95, bottom=.16, top=.90)
    source_ax.step(edges, [2, 8, 2, 2], where="post", color="#596b80", lw=1.2)
    source_ax.set(ylabel="Source current\nsetting (A)", ylim=(0, 10), yticks=[2, 8])
    power_ax.plot(times[keep], power[keep], color="#168b87", lw=1,
                  marker="o", ms=1.5, markeredgewidth=0, label="Measured PV input power")
    power_ax.step(edges, reference + [reference[-1]], where="post", ls="--",
                  color="#253342", lw=1.2, label="Reference MPP power")
    power_ax.set(xlabel="Time from 2 to 8 A source command (s)",
                 ylabel="PV input power (W)", xlim=(left, right),
                 ylim=(0, max(220, np.nanmax(power[keep]) * 1.08)),
                 yticks=[0, 50, 100, 150, 200])
    for rising, t0, t1 in transitions:
        for crossing in (t0, t1):
            power_ax.axvline(crossing, color="#c62828", lw=.9, zorder=4)
        power_ax.annotate("", xy=(t0, 208), xytext=(t1, 208),
                          arrowprops={"arrowstyle": "<->", "color": "#c62828", "lw": .9,
                                      "shrinkA": 0, "shrinkB": 0, "mutation_scale": 6})
        direction = "10–90% rise" if rising else "90–10% fall"
        power_ax.text(t1 + .015, 207,
                      f"{direction}: {(t1 - t0) * 1000:.0f} ms",
                      ha="left", va="bottom", color="#c62828", fontsize=9,
                      bbox={"facecolor": "white", "edgecolor": "none", "pad": 2})
        print(f"{direction}: {(t1 - t0) * 1000:.2f} ms "
              f"(crossings {t0:.6f}, {t1:.6f} s)")
    for ax in (source_ax, power_ax):
        ax.grid(color="#e6e9ed", lw=.5)
        ax.set_axisbelow(True)
        for transition in (0, fall):
            ax.axvline(transition, color="#9aa4ae", ls=":", lw=.8)
        for spine in ax.spines.values():
            spine.set_color("black")
            spine.set_linewidth(1)
    source_ax.set_title("MPPT response to a 2-8-2 A source transition",
                        fontsize=13, fontweight="bold", pad=14)
    fig.legend(*power_ax.get_legend_handles_labels(), loc="lower center",
               bbox_to_anchor=(.54, .065), ncol=2, frameon=False, fontsize=10)
    print(f"Cycle {cycle}: rising command {origin:.6f} s, falling command {origin + fall:.6f} s "
          "after first source command; supplied 10-sample averages.")
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--cycle", type=int, default=3, help="Complete 2-8-2 cycle, numbered from 1")
    parser.add_argument("--before", type=float, default=.35, help="Seconds before rising command")
    parser.add_argument("--after", type=float, default=.4, help="Seconds after falling command")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent / "output/06_transition_zoom.png")
    args = parser.parse_args()
    if args.before <= 0 or args.after <= 0:
        parser.error("--before and --after must be positive")
    fig = make_figure(args.csv, args.cycle, args.before, args.after)
    output = args.output.with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=300, facecolor="white")
    plt.close(fig)
    print(output)


if __name__ == "__main__":
    main()
