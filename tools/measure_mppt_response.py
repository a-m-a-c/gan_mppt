#!/usr/bin/env python3
"""Capture measurement 24, or analyse a saved run without opening hardware.

Capture enables the SPS5162X and channel A MPPT using the existing gated runner.
Use --replay FOLDER for offline analysis. Run with uv run --project tools --locked python.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import statistics

from current_profile import CurrentStep, validate_profile
import measure_mppt_profile as runner
from sps5162x import SPS5162X


def build_profile(high_a: float, low_a: float, hold_s: float = 30) -> list[CurrentStep]:
    if not all(math.isfinite(x) for x in (high_a, low_a, hold_s)):
        raise ValueError("Profile values must be finite")
    if not 0 < low_a < high_a or hold_s <= 0:
        raise ValueError("Require 0 < low current < high current and a positive hold")
    steps = [CurrentStep(0, high_a), CurrentStep(hold_s, low_a), CurrentStep(2 * hold_s, high_a)]
    validate_profile(steps, 35, 35, 8, hold_s, 0.5)
    if 3 * hold_s + 19 > 3600:
        raise ValueError("Capture including baseline, tail and runner margin must fit within one hour")
    return steps


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def load_reference(path: Path | None) -> dict[float, float]:
    if path is None:
        return {}
    reference = {}
    for row in read_csv(path):
        if row.get("reference_usable", "True") != "True":
            continue
        current, power = float(row["source_current_a"]), float(row["pin_w"])
        if not math.isfinite(current) or not math.isfinite(power) or power <= 0:
            raise ValueError("Reference currents/powers must be finite with positive power")
        if current in reference:
            raise ValueError(f"Duplicate reference current: {current}")
        reference[current] = power
    return reference


def analyse_response(folder: Path, reference: Path | None = None, *, tolerance_pct: float = 2,
                     dwell_s: float = 1, max_gap_s: float = 0.05,
                     steady_window_s: float = 5) -> dict:
    for value in (tolerance_pct, dwell_s, max_gap_s, steady_window_s):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Analysis tolerance, dwell, gap and steady window must be positive and finite")
    refs = load_reference(reference)
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    profile = read_csv(folder / "profile.csv")
    if not profile:
        raise ValueError("No source events available")
    rows = read_csv(folder / "measure.csv")
    samples = []
    for row in rows:
        valid = (row["valid"] == "True" and row["fault"] == "False"
                 and row["restore_failed"] == "False" and row["channel"] == "A")
        power = float(row["vin_mv"]) * float(row["iin_ma"]) / 1e6 if valid else None
        samples.append({"host_s": float(row["t_host_s"]), "mcu_s": float(row["t_s"]),
                        "power_w": power, "active": valid and row["switching"] == "True",
                        "gap": int(row["sequence_gap"])})
    if not samples or any(b["host_s"] < a["host_s"] for a, b in zip(samples, samples[1:])):
        raise ValueError("Need samples in nondecreasing host timestamp order")
    stop = run.get("profile", {}).get("output_off_s", samples[-1]["host_s"])
    result = {"complete_capture": bool(run.get("complete")), "reference_file": str(reference) if reference else None,
              "tolerance_pct": tolerance_pct, "dwell_s": dwell_s, "max_gap_s": max_gap_s,
              "steady_window_s": steady_window_s, "data_quality": run.get("data_quality", {}),
              "timing_note": "Approximate host-command-to-UART-arrival timing, not ADC-synchronised latency. "
                             "MCU sample timestamps are retained separately in response.csv.",
              "reference_note": "Separate-run MPP reference; source/temperature drift applies. No interpolation.",
              "settling_definition": "First continuous in-band dwell of raw valid switching samples; "
                                     "later departures are allowed. Gaps/invalid samples reset the dwell.",
              "phases": []}
    for index, event in enumerate(profile):
        start = float(event["command_start_s"])
        if index == 0:
            start = run.get("measurement", {}).get("start_command_s")
        end = min(float(profile[index + 1]["command_start_s"]), stop) if index + 1 < len(profile) else stop
        current = float(event["requested_current_a"])
        target = refs.get(current)
        phase = {"name": "acquisition" if index == 0 else f"step_{index}", "source_current_a": current,
                 "source_command_s": float(event["command_start_s"]),
                 "setpoint_verified_s": float(event["setpoint_verified_s"]),
                 "start_s": start, "end_s": end, "reference_w": target,
                 "settling_s": None, "status": "missing_start_event" if start is None else "missing_reference"}
        if start is not None:
            segment = [s for s in samples if start <= s["host_s"] < end]
            since = previous = None
            if target is not None:
                phase["status"] = "not_settled"
                for sample in segment:
                    t = sample["host_s"]
                    broken = sample["gap"] or (previous is not None and t - previous > max_gap_s)
                    if broken:
                        since = None
                    inside = (sample["active"] and math.isfinite(sample["power_w"])
                              and abs(sample["power_w"] - target) <= target * tolerance_pct / 100)
                    since = (t if since is None else since) if inside else None
                    if since is not None and t - since >= dwell_s:
                        phase.update(settling_s=since - start, dwell_confirmed_s=t, status="settled")
                        break
                    previous = t
            tail = [s["power_w"] for s in segment if s["active"] and
                    s["host_s"] >= max(start, end - steady_window_s) and math.isfinite(s["power_w"])]
            phase["tail_samples"] = len(tail)
            if tail:
                phase.update(tail_mean_w=statistics.mean(tail), tail_pp_w=max(tail) - min(tail),
                             tail_std_w=statistics.pstdev(tail))
        result["phases"].append(phase)
    with (folder / "response.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=samples[0].keys())
        writer.writeheader()
        writer.writerows(samples)
    (folder / "response.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def plot_response(folder: Path, result: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = read_csv(folder / "response.csv")
    fig, ax = plt.subplots(figsize=(11, 5), layout="constrained")
    try:
        ax.plot([float(r["host_s"]) for r in rows],
                [float(r["power_w"]) if r["power_w"] else math.nan for r in rows],
                linewidth=0.6, label="Measured input power (UART arrival time)", rasterized=True)
        for i, phase in enumerate(result["phases"]):
            command = phase["source_command_s"]
            ax.axvspan(command, phase["setpoint_verified_s"], color="gray", alpha=0.15)
            ax.axvline(command, color="gray", linestyle=":", linewidth=0.8)
            ax.text(command, 0.98, f" {phase['source_current_a']:g} A", transform=ax.get_xaxis_transform(), va="top")
            if phase["reference_w"] is not None:
                ax.hlines(phase["reference_w"], command, phase["end_s"], color="black", linestyle="--",
                          label="Separate-run reference MPP" if i == 0 else None)
        start = result["phases"][0]["start_s"]
        if start is not None:
            ax.axvline(start, color="green", linestyle="--", label="MPPT start command")
        ax.set(xlabel="Host time since capture command (s)", ylabel="Input power (W)",
               title="MPPT acquisition and disturbance response" +
                     (" — PARTIAL CAPTURE" if not result["complete_capture"] else ""))
        ax.grid(alpha=0.25)
        ax.legend(loc="best")
        fig.savefig(folder / "response.svg")
        fig.savefig(folder / "response.png", dpi=180)
    finally:
        plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--port", help="Capture from MCU UART; disconnect bench GUI first")
    mode.add_argument("--replay", type=Path, help="Existing capture folder; no hardware access")
    parser.add_argument("--high", type=float, help="High source current in A (required for capture)")
    parser.add_argument("--low", type=float, help="Low source current in A (required for capture)")
    parser.add_argument("--hold", type=float, default=30, help="Seconds per plateau (default 30)")
    parser.add_argument("--resource", help="SPS5162X VISA resource")
    parser.add_argument("--voltage", type=float, default=35, help="Source compliance voltage (default 35)")
    parser.add_argument("--reference", type=Path, help="Reference CSV with source_current_a,pin_w; optional reference_usable")
    parser.add_argument("--tolerance-pct", type=float, default=2)
    parser.add_argument("--dwell", type=float, default=1)
    parser.add_argument("--max-gap", type=float, default=0.05)
    parser.add_argument("--steady-window", type=float, default=5)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "captures")
    args = parser.parse_args()
    try:
        load_reference(args.reference)
        for value in (args.tolerance_pct, args.dwell, args.max_gap, args.steady_window):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Analysis settings must be positive and finite")
        folder = args.replay
        if folder is None:
            if args.high is None or args.low is None:
                parser.error("Capture requires --high and --low")
            steps = build_profile(args.high, args.low, args.hold)
            validate_profile(steps, args.voltage, 35, 8, args.hold, 0.5)
            with SPS5162X.open(args.resource) as supply:
                run = runner.capture(supply, args.port, steps, output_dir=args.output,
                                     voltage_limit_v=args.voltage, final_hold_s=args.hold, make_plots=False)
            folder = Path(run["folder"])
            print(f"Capture complete: {run['complete']}; supply OFF confirmed: {run['output_off_confirmed']}")
        result = analyse_response(folder, args.reference, tolerance_pct=args.tolerance_pct,
                                  dwell_s=args.dwell, max_gap_s=args.max_gap, steady_window_s=args.steady_window)
        plot_response(folder, result)
        print(result["timing_note"])
        for phase in result["phases"]:
            print(f"{phase['name']}: {phase['status']}; settling={phase['settling_s']} s")
        print(f"Saved response.csv/json/svg/png in {folder}")
        return int(not result["complete_capture"])
    except Exception as error:
        print(f"Response capture/analysis failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
