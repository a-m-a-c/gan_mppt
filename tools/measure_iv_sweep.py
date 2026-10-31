#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyserial>=3.5", "matplotlib>=3.8"]
# ///
"""Capture one channel-A reference-panel IV sweep, or replot its raw CSV."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
from statistics import fmean

import measure

REQUESTED_SETUP = {"frequency_hz": 600000, "maximum_duty_pct": 70.0,
                   "direction": "up", "step_pct": 0.1, "step_period_ms": 100}


def sweep_finished(summary: dict) -> bool:
    """Firmware completed normally; transport quality is assessed separately."""
    return bool(summary.get("end_received") and summary.get("samples")
                and not summary.get("final_flags", 0) & measure.IV_LIMITED
                and summary.get("final_flags", 0) & (measure.IV_SWEEP | measure.IV_COMPLETE)
                == (measure.IV_SWEEP | measure.IV_COMPLETE)
                and not any(summary.get(k) for k in
                            ("fault", "restore_failed", "interrupted", "error", "firmware_errors")))


def sweep_complete(summary: dict) -> bool:
    return sweep_finished(summary) and not any(summary.get(k) for k in
        ("plot_error", "sequence_gaps", "crc_errors", "firmware_dropped"))


def extract_points(rows: list[dict], settle_s: float = 0.02,
                   min_hold_s: float = 0.04) -> list[dict]:
    """Average each contiguous duty hold separately, excluding the transition."""
    # Keep the 40 ms minimum for older 50 ms captures; current 100 ms holds retain ~80 ms after settling.
    points, group = [], []
    key = None

    def flush() -> None:
        if len(group) < 2 or group[-1]["t"] - group[0]["t"] < min_hold_s:
            return
        kept = [r for r in group if r["t"] - group[0]["t"] >= settle_s]
        if len(kept) < 10:
            return
        point = {"branch": key[0], "duty_pct": key[1] / 10,
                 "t_s": fmean(r["t"] for r in kept), "samples": len(kept)}
        for name in ("vin_v", "iin_a", "vout_v", "iout_a", "pin_w", "pout_w"):
            point[name] = fmean(r[name] for r in kept)
        point["vin_pp_v"] = max(r["vin_v"] for r in kept) - min(r["vin_v"] for r in kept)
        point["iin_pp_a"] = max(r["iin_a"] for r in kept) - min(r["iin_a"] for r in kept)
        points.append(point)

    for row in rows:
        flags = int(row["flags"])
        good = (flags & (measure.VALID | measure.SWITCHING | measure.IV_SWEEP)
                == (measure.VALID | measure.SWITCHING | measure.IV_SWEEP)
                and not flags & (measure.FAULT | measure.END | measure.RESTORE_FAILED)
                and row["channel"] == "A")
        if not good:
            flush()
            group, key = [], None
            continue
        next_key = ("down" if flags & measure.IV_DESCENDING else "up", int(row["duty"]))
        if key != next_key or int(row["sequence_gap"]):
            flush()
            group, key = [], next_key
        sample = {"t": float(row["t_s"]), **{name: float(row[field]) / 1000
                  for name, field in (("vin_v", "vin_mv"), ("iin_a", "iin_ma"),
                                      ("vout_v", "vout_mv"), ("iout_a", "iout_ma"))}}
        if not all(math.isfinite(value) for value in sample.values()):
            flush()
            group, key = [], None
            continue
        if group and sample["t"] <= group[-1]["t"]:
            raise ValueError("Sweep timestamps must increase")
        sample["pin_w"] = sample["vin_v"] * sample["iin_a"]
        sample["pout_w"] = sample["vout_v"] * sample["iout_a"]
        group.append(sample)
    flush()
    return points


def extract_raw_points(rows: list[dict], sweep_flag: int = measure.IV_VOLTAGE,
                       branch: str = "voltage_down") -> list[dict]:
    """Keep measured sweep samples, including the final current crossing."""
    points = []
    required = measure.VALID | measure.SWITCHING | measure.IV_SWEEP | sweep_flag
    for row in rows:
        flags = int(row["flags"])
        if (flags & required != required or row["channel"] != "A" or
                flags & (measure.FAULT | measure.END | measure.RESTORE_FAILED | measure.IV_LIMITED)):
            continue
        direction = ("down" if flags & measure.IV_DESCENDING else "up") if flags & measure.IV_RETURN_RAMP else branch
        p = {"branch": direction, "t_s": float(row["t_s"]),
             "duty_pct": int(row["duty"]) / 10, "samples": 1,
             "vin_pp_v": "", "iin_pp_a": ""}
        for name, field in (("vin_v", "vin_mv"), ("iin_a", "iin_ma"),
                            ("vout_v", "vout_mv"), ("iout_a", "iout_ma")):
            p[name] = float(row[field]) / 1000
        if not all(math.isfinite(p[k]) for k in ("t_s", "vin_v", "iin_a", "vout_v", "iout_a")):
            continue
        p["pin_w"] = p["vin_v"] * p["iin_a"]
        p["pout_w"] = p["vout_v"] * p["iout_a"]
        points.append(p)
    return points


def render(csv_path: Path) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows or not any(int(r["flags"]) & measure.IV_SWEEP for r in rows):
        raise ValueError("This CSV is not a mode_measure_iv_sweep capture")
    voltage_sweep = any(int(r["flags"]) & measure.IV_VOLTAGE for r in rows)
    current_ramp = any(int(r["flags"]) & measure.IV_CURRENT_RAMP for r in rows)
    duty_ramp = any(int(r["flags"]) & measure.IV_DUTY_RAMP for r in rows)
    return_ramp = any(int(r["flags"]) & measure.IV_RETURN_RAMP for r in rows)
    raw_sweep = voltage_sweep or current_ramp or duty_ramp
    if duty_ramp:
        points = extract_raw_points(rows, measure.IV_DUTY_RAMP, "duty_ramp")
        branches = ("up", "down") if return_ramp else ("duty_ramp",)
    elif current_ramp:
        points = extract_raw_points(rows, measure.IV_CURRENT_RAMP, "current_ramp")
        branches = ("current_ramp",)
    else:
        points = extract_raw_points(rows) if voltage_sweep else extract_points(rows)
        branches = ("voltage_down",) if voltage_sweep else ("up", "down")
    with csv_path.with_name(f"{csv_path.stem}_points.csv").open("w", newline="", encoding="utf-8") as fh:
        fields = ("branch", "duty_pct", "t_s", "samples", "vin_v", "iin_a", "pin_w",
                  "vout_v", "iout_a", "pout_w", "vin_pp_v", "iin_pp_a")
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(points)
    meta_path = csv_path.with_suffix(".json")
    summary = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    requested_setup = summary.get("requested_setup") or summary.get("iv_sweep", {}).get("requested_setup")
    complete = sweep_complete(summary) and all(
        sum(p["branch"] == branch for p in points) >= 2 for branch in branches)
    limited = bool(summary.get("final_flags", 0) & measure.IV_LIMITED)
    peaks = {}
    for branch in branches:
        branch_points = [p for p in points if p["branch"] == branch]
        if branch_points:
            best = max(branch_points, key=lambda p: p["pin_w"])
            peaks[branch] = {k: best[k] for k in ("vin_v", "iin_a", "pin_w", "duty_pct")}
    summary["iv_sweep"] = {
        "complete": complete, "limited": limited, "points": len(points), "measured_peaks": peaks,
        "method": ("Direct PWM duty up/down sweep" if return_ramp else "Direct PWM duty ramp" if duty_ramp else
                   "Direct PWM ramp to 7.5 A input" if current_ramp else
                   "PI input-voltage ramp" if voltage_sweep else "Legacy duty up/down sweep"),
        "averaging": ("None: raw valid switching samples." if raw_sweep else
                      "Each duty visit independently; first 20 ms excluded; minimum hold 40 ms and 10 retained samples."),
        "scope": "Measured range only; no extrapolated Isc or inferred full-panel efficiency.",
        "test_reference": {"panel_series_half_cells": 39, "stc_voc_v": 29.02,
                           "stc_isc_a": 8.66, "stc_vmpp_v": 26.25, "stc_impp_a": 8.22,
                           "stc_pmpp_w": 215.75, "battery": "12S4P 10 Ah",
                           "battery_max_v": 50.4, "battery_max_charge_a": 10},
    }
    if duty_ramp and requested_setup:
        summary["iv_sweep"]["requested_setup"] = requested_setup
    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(9, 7), layout="constrained")
    try:
        for branch, color in zip(branches, ("tab:blue", "tab:orange")):
            ps = [p for p in points if p["branch"] == branch]
            if not ps:
                continue
            for ax, field in zip(axes, ("iin_a", "pin_w")):
                ax.plot([p["vin_v"] for p in ps], [p[field] for p in ps],
                        linestyle="None", marker=".", markersize=1.5 if raw_sweep else 3, color=color,
                        label=(f"Duty {branch}: raw samples" if return_ramp else "Duty ramp: raw samples" if duty_ramp else
                               "Duty ramp to 7.5 A: raw samples" if current_ramp else
                               "Voltage ramp: raw samples" if voltage_sweep else f"Duty {branch}: dwell averages"))
        axes[0].set_ylabel("Panel current (A)")
        axes[1].set_ylabel("Panel power (W)")
        axes[1].set_xlabel("Panel voltage (V)")
        for ax in axes:
            ax.grid(alpha=0.25)
            if ax.lines:
                ax.legend()
        if not points:
            axes[0].text(0.5, 0.5, "No usable sweep points", ha="center", transform=axes[0].transAxes)
        status = "complete" if complete else "LIMITED / partial" if limited else "INCOMPLETE"
        fig.suptitle(f"39-cell reference panel — channel A — {status}\n"
                     "Measured range only; short-circuit endpoint not established")
        path = csv_path.with_name(f"{csv_path.stem}_iv.svg")
        fig.savefig(path, format="svg")
    finally:
        plt.close(fig)
    meta_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", help="board serial port; starts the active sweep")
    source.add_argument("--csv", type=Path, help="replot an existing IV capture without connecting")
    parser.add_argument("--timeout", type=float, default=95.0, help="host stop deadline in seconds (default 95; firmware deadline 85)")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "captures")
    args = parser.parse_args()
    if not 0 < args.timeout <= 300:
        parser.error("--timeout must be greater than zero and at most 300 seconds")
    if args.output.suffix.lower() == ".csv":
        parser.error("--output must be a parent directory")
    path = args.csv
    capture_error = None
    try:
        if path is None:
            folder = args.output / f"{datetime.now().astimezone():%Y%m%d-%H%M%S-%f}_measure_iv_sweep"
            folder.mkdir(parents=True, exist_ok=False)
            path = folder / "measure_iv_sweep.csv"
            print(f"Channel A only; active sweep; capture folder: {folder}")
            print("Firmware must be built with mode_measure_iv_sweep. Battery must be below 50.0 V.")
            print(f"Direct PWM at {REQUESTED_SETUP['frequency_hz']} Hz: 0% -> 70%, then PWM off, 0.1 percentage point/100 ms; about 73 s. Reflash for 100 ms timing.")
            try:
                measure.record(args.port, args.timeout, path, command="measure_iv_sweep",
                               required_flags=measure.IV_SWEEP | measure.IV_DUTY_RAMP)
            except (OSError, ValueError, measure.serial.SerialException, TimeoutError) as exc:
                capture_error = str(exc)
            meta_path = path.with_suffix(".json")
            metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            metadata["requested_setup"] = REQUESTED_SETUP
            meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        summary = render(path)
        print(json.dumps(summary["iv_sweep"], indent=2))
        if capture_error:
            print(f"Capture failed: {capture_error}")
        return int(bool(capture_error) or not summary["iv_sweep"]["complete"])
    except (OSError, ValueError, measure.serial.SerialException, TimeoutError) as exc:
        print(f"IV sweep failed: {capture_error or exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
