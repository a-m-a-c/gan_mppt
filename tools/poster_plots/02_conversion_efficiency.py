"""Poster panel 2: measured conversion efficiency for continuous source ramps.

Uses existing 5 s averages without extra smoothing. Frequencies are capture
labels, not telemetry readback; calibration uncertainty is unestablished.
"""
import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_300 = ROOT / "captures/measure_effiency/300kHz/continuous/20261031-193528-702226_measure_mppt_profile/comparison/conversion_efficiency.csv"
DEFAULT_600 = ROOT / "captures/measure_effiency/600kHz/continuous/conversion_efficiency.csv"


def read_curve(path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        points = np.array([[float(row["mean_pin_w"]), float(row["efficiency_pct"])]
                           for row in csv.DictReader(stream)])
    if points.size == 0 or not np.isfinite(points).all():
        raise ValueError(f"Expected finite power and efficiency values: {path}")
    return points


def make_figure(csv_300, csv_600):
    curves = [(300, read_curve(csv_300), "#168b87"),
              (600, read_curve(csv_600), "#b86632")]
    plt.rcParams.update({
        "font.family": "Arial", "font.size": 10,
        "axes.labelsize": 11, "xtick.labelsize": 9, "ytick.labelsize": 9,
        "svg.fonttype": "none", "pdf.fonttype": 42,
    })
    # 2.54 cm per inch; match the square I-V poster figure.
    fig, ax = plt.subplots(figsize=(22 / 2.54, 22 / 2.54))
    fig.subplots_adjust(left=.14, right=.92, bottom=.14, top=.92)
    ax.set_box_aspect(1)
    for frequency, points, color in curves:
        ax.plot(points[:, 0], points[:, 1], color=color, lw=1.3,
                marker="o", ms=2, markeredgewidth=0, label=f"{frequency} kHz")
    peak = curves[0][1][np.argmax(curves[0][1][:, 1])]
    ax.scatter(*peak, s=20, color=curves[0][2], zorder=4)
    ax.annotate(f"{peak[1]:.1f}% at {peak[0]:.0f} W input",
                xy=peak, xytext=(70, 100), fontsize=10, ha="left", va="center",
                arrowprops={"arrowstyle": "-", "color": "#253342", "lw": .8,
                            "connectionstyle": "angle,angleA=0,angleB=90,rad=4"})
    all_points = np.concatenate([points for _, points, _ in curves])
    ax.set(xlabel="PV input power (W)", ylabel="Measured conversion efficiency (%)",
           xlim=(0, max(210, np.ceil(all_points[:, 0].max() / 10) * 10)),
           ylim=(min(85, np.floor(all_points[:, 1].min()) - 1),
                 max(101, np.ceil(all_points[:, 1].max()) + 1)))
    ax.set_xticks([0, 50, 100, 150, 200])
    ax.grid(color="#e6e9ed", lw=.5)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color("black")
        spine.set_linewidth(1)
    ax.set_title("Measured conversion efficiency", fontsize=13,
                 fontweight="bold", pad=14)
    fig.legend(*ax.get_legend_handles_labels(), loc="lower center",
               bbox_to_anchor=(.53, .045), ncol=2, frameon=False, fontsize=10)
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-300", type=Path, default=DEFAULT_300)
    parser.add_argument("--csv-600", type=Path, default=DEFAULT_600)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent / "output/02_conversion_efficiency",
                        help="Output stem (writes .svg and .png)")
    args = parser.parse_args()
    fig = make_figure(args.csv_300, args.csv_600)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for extension in ("svg", "png"):
        path = args.output.with_suffix("." + extension)
        fig.savefig(path, dpi=300, facecolor="white")
        print(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
