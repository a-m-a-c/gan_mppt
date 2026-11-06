"""Poster panel 1: measured trajectory and separate-run I–V references."""
import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = ROOT / "captures/measure_5_redo/continuous/20261031-181652-777808_measure_mppt_profile/comparison/mppt_continuous_average_10samples.csv"


def read_rows(path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def make_figure(csv_path, reference):
    """Use supplied averages unchanged; scatter does not bridge missing samples."""
    rows = read_rows(csv_path)
    data = np.array([[float(row[key]) for key in
                      ("mean_mcu_s", "mean_iin_a", "mean_vin_v")] for row in rows])
    data = data[np.isfinite(data).all(axis=1)]
    curves = [row for row in read_rows(reference / "settled_points.csv")
              if row["branch"] == "up"]
    peaks = sorted((row for row in read_rows(reference / "reference_mpp.csv")
                    if row["branch"] == "up" and row["reference_usable"] == "True"),
                   key=lambda row: float(row["source_current_a"]))
    if not len(data) or not curves or not peaks:
        raise ValueError("Expected finite trajectory points, up-branch curves and usable MPPs")

    plt.rcParams.update({
        "font.family": "Arial", "font.size": 10,
        "axes.labelsize": 11, "axes.titlesize": 11,
        "axes.edgecolor": "#66717d", "axes.linewidth": 0.7,
        "xtick.labelsize": 9, "ytick.labelsize": 9,
        "svg.fonttype": "none", "pdf.fonttype": 42,
    })
    # 2.54 cm per inch; preserve the exact poster slot (no tight bounding box).
    fig, ax = plt.subplots(figsize=(22 / 2.54, 22 / 2.54))
    fig.subplots_adjust(left=.10, right=.84, bottom=.16, top=.90)
    ax.set_box_aspect(1)
    # Clip only the reference curves; retain the full trajectory and axis range.
    curve_clip = Rectangle((0, 15), 1, 18, transform=ax.get_yaxis_transform())
    for sweep in sorted({row["sweep_index"] for row in curves}, key=int):
        points = [row for row in curves if row["sweep_index"] == sweep]
        line, = ax.plot([float(row["iin_a"]) for row in points],
                [float(row["vin_v"]) for row in points],
                color="#98a5b3", lw=.65, zorder=1)
        line.set_clip_path(curve_clip)
    dots = ax.scatter(data[:, 1], data[:, 2], c=data[:, 0], cmap="viridis",
                      s=.4, linewidths=0, alpha=.7, rasterized=True, zorder=2)
    ax.plot([float(row["iin_a"]) for row in peaks],
            [float(row["vin_v"]) for row in peaks],
            "o--", color="#253342", ms=3, lw=1.1, zorder=3)
    ax.set(xlim=(-.1, 8.3), xlabel="PV input current (A)")
    ax.set_xticks([0, 2, 4, 6, 8])
    ax.grid(color="#e6e9ed", lw=.5)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(1.0)
    ax.set(ylabel="PV input voltage (V)", ylim=(13, 33))
    ax.set_yticks([15, 20, 25, 30])
    color_ax = fig.add_axes([.88, .23, .015, .60])
    fig.colorbar(dots, cax=color_ax, label="Capture time (s)", ticks=[0, 200, 400, 600])
    fig.suptitle(r"MPPT tracking along a 0.5-8 A $I_{sc}$ ramp", x=.5, ha="center",
                 y=.94, fontsize=13, fontweight="bold")
    handles = [Line2D([], [], color="#98a5b3", lw=1.2, label="Measured I–V curves"),
               Line2D([], [], marker="o", ls="none", color="#238a8d", ms=4,
                      label="MPPT trajectory"),
               Line2D([], [], marker="o", ls="--", color="#253342", ms=3,
                      label="Reference MPP")]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(.49, .065),
               ncol=3, frameon=False, fontsize=9, handlelength=2, columnspacing=1.8)
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--reference", type=Path, default=ROOT / "captures/measure_iv_curves")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent / "output/01_iv_tracking",
                        help="Output stem (writes .svg and .png)")
    args = parser.parse_args()
    fig = make_figure(args.csv, args.reference)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for extension in ("svg", "png"):
        path = args.output.with_suffix("." + extension)
        fig.savefig(path, dpi=300, facecolor="white")
        print(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
