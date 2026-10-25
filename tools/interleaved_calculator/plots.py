"""Comparison plots using the same exact switching events as the ripple model."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .bus_ripple import calculate_bus_waveform


def plot_phase_comparison(
    names, duty_cycles, output_currents_a, switching_frequency_hz,
    bus_capacitance_f, cases, output_dir: Path, show: bool = True,
) -> None:
    """Save comparison/timing figures, then display both until closed."""
    if not show:
        plt.switch_backend("Agg")
    colours = ("#64748b", "#2171b5", "#d65f23")
    channel_colours = ("#3579b1", "#299d8f", "#c89426", "#9567b3", "#db6b55")
    period_us = 1e6 / switching_frequency_hz
    conditions = (
        f"{switching_frequency_hz / 1000:g} kHz  |  "
        f"{bus_capacitance_f * 1e6:g} uF shared capacitance  |  "
        f"{sum(output_currents_a):.3f} A mean load"
    )
    note = "Rectangular CCM output pulses; excludes inductor ripple, ESR/ESL and battery impedance."
    style = {
        "font.family": "DejaVu Sans", "font.size": 10,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.titleweight": "bold", "axes.labelcolor": "#334155",
        "text.color": "#172b4d", "axes.edgecolor": "#cbd5e1",
        "grid.color": "#dbe3eb", "grid.alpha": 0.7,
        "figure.facecolor": "white", "savefig.facecolor": "white",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with plt.rc_context(style):
        fig, axes = plt.subplots(2, 2, figsize=(13, 8.5))
        fig.canvas.manager.set_window_title("Bus ripple: aligned, evenly spaced and optimised")
        fig.suptitle("Switching timing changes the charge imbalance", fontsize=18, fontweight="bold")
        fig.text(0.5, 0.925, conditions, ha="center", color="#52647a")
        fig.subplots_adjust(top=0.84, bottom=0.12, hspace=0.43, wspace=0.25)
        ripple_values = []
        for index, (label, phases) in enumerate(cases):
            times, currents, voltages = calculate_bus_waveform(
                duty_cycles, output_currents_a, switching_frequency_hz,
                bus_capacitance_f, phases,
            )
            times = np.asarray(times)
            voltages = np.asarray(voltages)
            # Exact time-weighted mean of the piecewise-linear waveform.
            mean_voltage = np.sum(np.diff(times) * (voltages[:-1] + voltages[1:]) / 2) / times[-1]
            centred_mv = (voltages - mean_voltage) * 1000
            ripple_mv = float(np.ptp(voltages) * 1000)
            ripple_values.append(ripple_mv)
            plot_times = np.concatenate((times, times[1:] + times[-1])) * 1e6
            plot_voltage = np.concatenate((centred_mv, centred_mv[1:]))
            caption = f"{label}: {ripple_mv:.3f} mV pp"
            axes[0, 0].plot(plot_times, plot_voltage, color=colours[index], lw=2, label=caption)
            if index > 0:
                axes[0, 1].plot(plot_times, plot_voltage, color=colours[index], lw=2, label=caption)
            axes[1, 0].stairs(
                list(currents) * 2, plot_times, baseline=None,
                color=colours[index], lw=1.8, label=label,
            )

        axes[0, 0].set_title("Bus voltage: all cases, same scale")
        axes[0, 1].set_title("Detail: evenly spaced vs optimised")
        axes[1, 0].set_title("Capacitor current: surplus or deficit")
        for ax in (axes[0, 0], axes[0, 1], axes[1, 0]):
            ax.axhline(0, color="#94a3b8", lw=0.8, zorder=0)
            ax.set_xlim(0, 2 * period_us)
            ax.set_xlabel("Time (us) — two switching periods")
            ax.grid(True)
            ax.legend(loc="best", fontsize=8, framealpha=0.95)
        axes[0, 0].set_ylabel("Bus voltage relative to mean (mV)")
        axes[0, 1].set_ylabel("Bus voltage relative to mean (mV)")
        axes[1, 0].set_ylabel("Capacitor current (A)")

        bars = axes[1, 1].bar([label for label, _ in cases], ripple_values, color=colours, width=0.6)
        axes[1, 1].bar_label(bars, fmt="%.3f", padding=5, fontweight="bold")
        axes[1, 1].set_title("Peak-to-peak ripple at fixed power and C")
        axes[1, 1].set_ylabel("Bus voltage ripple (mV pp)")
        axes[1, 1].set_ylim(0, max(max(ripple_values) * 1.22, 1e-6))
        axes[1, 1].grid(axis="y")
        axes[1, 1].set_axisbelow(True)
        fig.text(0.5, 0.035, note + "\nOptimised = best numerical result found, not a global-optimality proof.",
                 ha="center", fontsize=9, color="#52647a")

        timing, timing_axes = plt.subplots(3, 1, figsize=(13, 9))
        timing.canvas.manager.set_window_title("Channel output-current pulse timing")
        timing.suptitle("When each converter supplies the bus", fontsize=18, fontweight="bold")
        timing.text(0.5, 0.925, "Coloured bars = output-current delivery; gaps = low-side switch on",
                    ha="center", color="#52647a")
        timing.subplots_adjust(top=0.85, bottom=0.12, left=0.15, hspace=0.55)
        for ax, (label, phases), ripple_mv in zip(timing_axes, cases, ripple_values):
            for row, (duty, phase) in enumerate(zip(duty_cycles, phases)):
                start = ((phase / 360) + duty) % 1.0
                width = 1 - duty
                segments = [(start, min(width, 1 - start))]
                if start + width > 1:
                    segments.append((0.0, start + width - 1))
                ax.broken_barh(
                    [(left * period_us, length * period_us) for left, length in segments],
                    (row - 0.3, 0.6), facecolors=channel_colours[row % len(channel_colours)],
                )
            ax.set_yticks(range(len(names)), [f"{name}   {phase:.3f}°" for name, phase in zip(names, phases)])
            ax.set_ylim(len(names) - 0.5, -0.5)
            ax.set_xlim(0, period_us)
            ax.set_xlabel("Time (us) — one switching period")
            ax.set_title(f"{label}  |  {ripple_mv:.3f} mV pp", loc="left", fontsize=11)
            ax.grid(axis="x")
            ax.set_axisbelow(True)
        timing.text(0.5, 0.035, "Phases label the LOW-SIDE TURN-ON delays, relative to F1.\n" + note,
                    ha="center", fontsize=9, color="#52647a")

        for figure, filename in ((fig, "bus_ripple_comparison"), (timing, "channel_timing")):
            for extension in ("png", "svg"):
                figure.savefig(output_dir / f"{filename}.{extension}", dpi=180)
        if show:
            plt.show()
        else:
            plt.close(fig)
            plt.close(timing)
