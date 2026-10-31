#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyvisa>=1.15,<2", "matplotlib>=3.8"]
# ///
"""Run an SPS5162X current-limit profile for changing available panel current.

Examples:
  uv run --project tools --locked python tools/current_profile.py --ramp 0 8 --duration 120
  uv run --project tools --locked python tools/current_profile.py --profile tools/profiles/irradiance_steps.csv
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from threading import Event
import time
from typing import Callable

import pyvisa
from sps5162x import SPS5162X, SupplyError, setpoint


@dataclass(frozen=True)
class CurrentStep:
    time_s: float
    current_a: float


def ramp_steps(start_a: float, end_a: float, duration_s: float,
               interval_s: float = 0.25) -> list[CurrentStep]:
    if not all(math.isfinite(x) for x in (start_a, end_a, duration_s, interval_s)):
        raise ValueError("Ramp parameters must be finite")
    if duration_s <= 0 or interval_s <= 0 or duration_s / interval_s > 100000:
        raise ValueError("Duration/interval must be positive, with at most 100000 steps")
    times = [n * interval_s for n in range(math.ceil(duration_s / interval_s))] + [duration_s]
    return [CurrentStep(t, round(start_a + (end_a - start_a) * t / duration_s, 3)) for t in times]


def load_steps(path: Path) -> list[CurrentStep]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        if not rows.fieldnames or not {"time_s", "current_a"} <= set(rows.fieldnames):
            raise ValueError("Profile CSV needs time_s,current_a columns")
        return [CurrentStep(float(row["time_s"]), float(row["current_a"])) for row in rows]


def plot_profile(path: Path, *, shared_clock: bool = False) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return
    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(9, 6), layout="constrained")
    try:
        t = [float(row["command_start_s"]) for row in rows]
        requested = [float(r["requested_current_a"]) for r in rows]
        meta = path.with_name("summary.json")
        if meta.exists():
            end_s = json.loads(meta.read_text(encoding="utf-8")).get("output_off_s")
            if end_s is not None and math.isfinite(end_s) and end_s > t[-1]:
                t.append(end_s)
                requested.append(requested[-1])
        axes[0].step(t, requested, where="post", label="Commanded current limit")
        axes[0].plot([float(r["readback_end_s"]) for r in rows], [float(r["current_a"]) for r in rows],
                     ".", markersize=3, label="Supply measured current")
        axes[1].plot([float(r["readback_end_s"]) for r in rows], [float(r["voltage_v"]) for r in rows],
                     ".", markersize=3, label="Supply measured voltage")
        axes[0].set_ylabel("Current (A)")
        axes[1].set_ylabel("Voltage (V)")
        axes[1].set_xlabel("Host time since MCU capture command (s)" if shared_clock else
                           "Host time since output-enable command (s)")
        for ax in axes:
            ax.grid(alpha=0.25); ax.legend()
        fig.suptitle("SPS5162X current-limit profile — measured current depends on load")
        fig.savefig(path.with_suffix(".svg"))
    finally:
        plt.close(fig)


def validate_profile(steps: list[CurrentStep], voltage_limit_v: float, max_voltage_v: float,
                     max_current_a: float, final_hold_s: float, max_lateness_s: float) -> float:
    voltage = setpoint(voltage_limit_v, max_voltage_v, "voltage limit (V)")
    if not steps or steps[0].time_s != 0:
        raise ValueError("Profile must start at time_s = 0")
    previous = -1.0
    for step in steps:
        if not math.isfinite(step.time_s) or step.time_s <= previous:
            raise ValueError("Profile times must be finite and strictly increasing")
        setpoint(step.current_a, max_current_a, "current limit (A)")
        previous = step.time_s
    if not math.isfinite(final_hold_s) or final_hold_s < 0 or not math.isfinite(max_lateness_s) or max_lateness_s < 0:
        raise ValueError("Final hold and maximum lateness must be finite and nonnegative")
    return voltage


def run_current_profile(supply: SPS5162X, steps: list[CurrentStep], *,
                        voltage_limit_v: float = 35.0, output_dir: Path = Path("captures"),
                        final_hold_s: float = 1.0, max_lateness_s: float = 0.5,
                        capture_folder: Path | None = None, clock_origin: float | None = None,
                        stop_event: Event | None = None, make_plots: bool = True,
                        on_setpoint: Callable[[CurrentStep], None] | None = None) -> dict:
    steps = list(steps)
    voltage = validate_profile(steps, voltage_limit_v, supply.max_voltage_v,
                               supply.max_current_a, final_hold_s, max_lateness_s)
    folder = Path(capture_folder) if capture_folder is not None else Path(output_dir) / (
        datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f") + "_current_profile")
    folder.mkdir(parents=True, exist_ok=capture_folder is not None)
    if any((folder / name).exists() for name in ("profile.csv", "summary.json", "profile.svg")):
        raise FileExistsError("Profile capture files already exist")
    print(f"Current profile capture: {folder}")
    summary = {"identity": supply.identity, "resource": supply.resource,
               "voltage_limit_v": voltage, "steps": [asdict(s) for s in steps],
               "final_hold_s": final_hold_s, "max_lateness_s": max_lateness_s,
               "limits": {"current_a": supply.max_current_a, "voltage_v": supply.max_voltage_v},
               "samples": 0, "complete": False, "output_off_confirmed": False,
               "timestamp_note": "Host timestamps; sequential supply queries, not synchronized ADC samples."}
    path = folder / "profile.csv"

    def check_stop() -> None:
        if stop_event is not None and stop_event.is_set():
            raise SupplyError("Profile cancelled: MCU capture stopped or reported a fault")

    def wait_until(deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            if stop_event is None:
                time.sleep(remaining)
            else:
                stop_event.wait(remaining)
        check_stop()

    try:
        with path.open("w", newline="") as handle:
            writer = None
            supply.set_output(False)
            supply.set_voltage_limit(voltage)
            supply.set_current_limit(steps[0].current_a)
            check_stop()
            summary["started_utc"] = datetime.now(timezone.utc).isoformat()
            origin = time.monotonic()
            log_origin = origin if clock_origin is None else clock_origin
            offset = origin - log_origin
            summary["profile_start_s"] = offset
            summary["shared_capture_clock"] = clock_origin is not None
            supply.set_output(True)
            summary["output_enabled_s"] = time.monotonic() - log_origin
            for step in steps:
                wait_until(origin + step.time_s)
                command_start = time.monotonic() - log_origin
                lateness = command_start - offset - step.time_s
                if lateness > max_lateness_s:
                    raise SupplyError(f"Profile missed {step.time_s:.3f} s deadline by {lateness:.3f} s")
                supply.set_current_limit(step.current_a)
                verified = time.monotonic() - log_origin
                check_stop()
                if on_setpoint is not None:
                    on_setpoint(step)
                reading = supply.readback()
                if not reading.output_enabled:
                    raise SupplyError("Supply output stopped during profile")
                row = {"scheduled_s": offset + step.time_s, "command_start_s": command_start,
                       "setpoint_verified_s": verified, "readback_end_s": time.monotonic() - log_origin,
                       "lateness_s": lateness,
                       "requested_current_a": step.current_a, **asdict(reading)}
                if writer is None:
                    writer = csv.DictWriter(handle, fieldnames=row.keys()); writer.writeheader()
                writer.writerow(row); handle.flush()
                summary["samples"] += 1
            wait_until(origin + steps[-1].time_s + final_hold_s)
            summary["complete"] = True
    except BaseException as error:
        summary["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        try:
            supply.set_output(False)
            summary["output_off_confirmed"] = True
            if "log_origin" in locals():
                summary["output_off_s"] = time.monotonic() - log_origin
        except Exception as error:
            summary["complete"] = False
            summary["shutdown_error"] = str(error)
        summary["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        if make_plots:
            try:
                plot_profile(path, shared_clock=clock_origin is not None)
            except Exception as error:
                summary["plot_error"] = str(error)
                (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return {**summary, "folder": str(folder)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--resource", help="VISA resource; defaults to the sole SPS USB device")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ramp", type=float, nargs=2, metavar=("START_A", "END_A"))
    source.add_argument("--profile", type=Path, help="CSV columns: time_s,current_a")
    # 8 A / 120 s = 0.0667 A/s; 250 ms updates are about 16.7 mA each.
    parser.add_argument("--duration", type=float, default=120.0, help="ramp duration in seconds (default 120)")
    parser.add_argument("--interval", type=float, default=0.25, help="ramp command spacing in seconds (default 0.25)")
    parser.add_argument("--voltage", type=float, default=35.0, help="voltage compliance (default/max 35 V)")
    parser.add_argument("--final-hold", type=float, default=1.0)
    parser.add_argument("--max-lateness", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "captures")
    args = parser.parse_args()
    try:
        steps = load_steps(args.profile) if args.profile else ramp_steps(*args.ramp, args.duration, args.interval)
        with SPS5162X.open(args.resource) as supply:
            result = run_current_profile(supply, steps, voltage_limit_v=args.voltage, output_dir=args.output,
                                         final_hold_s=args.final_hold, max_lateness_s=args.max_lateness)
        print(json.dumps(result, indent=2))
        return int(not result["complete"])
    except KeyboardInterrupt:
        print("Profile interrupted; see saved summary for output shutdown status")
        return 130
    except (SupplyError, ValueError, OSError, pyvisa.errors.VisaIOError) as error:
        print(f"Profile failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
