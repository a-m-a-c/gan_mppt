#!/usr/bin/env python3
"""Characterise the dark-panel/current-source combination for measurement 5.

Live use enables the supply and runs channel A's existing IV sweep repeatedly.
--replot only reads saved files; no instruments are opened.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime
import json
import math
from pathlib import Path
from threading import Event, Thread
import time

import measure
import measure_iv_sweep as sweep
from sps5162x import SPS5162X, SupplyError, setpoint


def save_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def data_quality(summary: dict) -> dict:
    counts = {k: summary.get(k, 0) for k in
              ("sequence_gaps", "crc_errors", "firmware_dropped", "discarded_bytes")}
    return dict(status="warning" if any(counts.values()) else "clean", **counts)


def intact_points(rows: list[dict]) -> tuple[list[dict], int]:
    """Exclude entire duty visits on either side of a missing-record interval."""
    visits, damaged = [], set()
    previous = None
    for row in rows:
        key = (int(row["flags"]) & measure.IV_DESCENDING, int(row["duty"]))
        if key != previous:
            visits.append([])
            previous = key
        index = len(visits) - 1
        if int(row["sequence_gap"]):
            damaged.add(index)
            if not visits[index] and index:
                damaged.add(index - 1)
        visits[index].append(row)
    points = []
    for index, visit in enumerate(visits):
        if index not in damaged:
            points.extend(sweep.extract_points(visit))
    return points, len(damaged)


def stop_board(port: str) -> None:
    with measure.serial.Serial(port, measure.console.BAUD, timeout=.05, write_timeout=1) as link:
        link.write(measure.console.encode(measure.console.OPCODES["stop"]))
        link.flush()
        time.sleep(.5)


def validate(currents: list[float], timeout: float, settle: float, off_seconds: float = 5) -> None:
    if not currents or len(set(currents)) != len(currents):
        raise ValueError("Provide distinct, nonzero current levels")
    for current in currents:
        if setpoint(current, 8, "source current") == 0:
            raise ValueError("Zero current has no useful MPP reference")
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("Timeout must be in (0, 300] seconds")
    if not math.isfinite(settle) or not 0 <= settle <= 60:
        raise ValueError("Settle time must be in [0, 60] seconds")
    if not math.isfinite(off_seconds) or not 0 <= off_seconds <= 60:
        raise ValueError("Supply-off pause must be in [0, 60] seconds")


def capture(supply: SPS5162X, port: str, currents: list[float], *, output: Path,
            timeout: float = 95, settle: float = 2, off_seconds: float = 5,
            notes: str = "") -> Path:
    """Stop on any failed sweep/source check; preserve partial captures and request OFF."""
    validate(currents, timeout, settle, off_seconds)
    folder = output / f"{datetime.now().astimezone():%Y%m%d-%H%M%S-%f}_characterise_iv"
    folder.mkdir(parents=True, exist_ok=False)
    run = dict(complete=False, output_off_confirmed=False, currents_a=currents,
               supply_identity=supply.identity, supply_resource=supply.resource,
               voltage_limit_v=35, settle_s=settle, off_seconds=off_seconds,
               timeout_s=timeout, notes=notes,
               requested_setup=sweep.REQUESTED_SETUP, sweeps=[])
    save_json(folder / "run.json", run)
    print(f"Capture folder: {folder}", flush=True)
    try:
        supply.set_output(False)
        stop_board(port)
        supply.set_voltage_limit(35)
        for index, current in enumerate(currents, 1):
            item = dict(index=index, source_current_a=current, complete=False,
                        csv=f"sweep_{index:02d}/measure_iv_sweep.csv")
            run["sweeps"].append(item)
            path = folder / item["csv"]
            path.parent.mkdir()
            save_json(folder / "run.json", run)
            print(f"Sweep {index}/{len(currents)}: {current:g} A", flush=True)
            # Output OFF was confirmed at setup or the preceding sweep's cleanup.
            print(f"Supply OFF pause: {off_seconds:g} s", flush=True)
            item["off_pause_start_monotonic_s"] = time.monotonic()
            time.sleep(off_seconds)
            item["off_pause_end_monotonic_s"] = time.monotonic()
            supply.set_current_limit(current)
            supply.set_output(True)
            done, abort = Event(), Event()
            errors = []

            def monitor() -> None:
                try:
                    with (path.parent / "source.csv").open("w", newline="", encoding="utf-8") as handle:
                        writer = None
                        while not done.is_set():
                            start = time.monotonic()
                            reading = supply.readback()
                            row = dict(host_start_monotonic_s=start,
                                       host_end_monotonic_s=time.monotonic(), **asdict(reading))
                            if writer is None:
                                writer = csv.DictWriter(handle, fieldnames=list(row))
                                writer.writeheader()
                            writer.writerow(row)
                            handle.flush()
                            if (not reading.output_enabled or reading.mode.upper() != "CC"
                                    or abs(reading.current_limit_a - current) > .0005
                                    or abs(reading.voltage_limit_v - 35) > .0005):
                                raise SupplyError("Source left CC, changed settings, or switched off")
                            if done.wait(1):
                                break
                except Exception as exc:
                    errors.append(str(exc))
                    abort.set()

            worker = None
            try:
                time.sleep(settle)
                reading = supply.readback()
                item["before"] = asdict(reading)
                if (not reading.output_enabled or reading.mode.upper() != "CC"
                        or abs(reading.current_limit_a - current) > .0005
                        or abs(reading.voltage_limit_v - 35) > .0005):
                    raise SupplyError("Source not ready in CC at the requested settings")
                worker = Thread(target=monitor, daemon=True)
                worker.start()
                summary = measure.record(port, timeout, path, command="measure_iv_sweep",
                                         required_flags=measure.IV_SWEEP | measure.IV_DUTY_RAMP,
                                         stop_event=abort, make_plots=False)
                item["complete"] = sweep.sweep_finished(summary) and not errors
                item["data_quality"] = data_quality(summary)
                if item["data_quality"]["status"] == "warning":
                    print(f"Data quality warning at {current:g} A: {item['data_quality']}", flush=True)
                if not item["complete"]:
                    raise SupplyError("Sweep did not complete normally or source failed; see individual JSON and source log")
            finally:
                done.set()
                if worker is not None:
                    worker.join()
                item["source_errors"] = errors
                if errors:
                    item["complete"] = False
                supply.set_output(False)
            if errors:
                raise SupplyError("; ".join(errors))
            save_json(folder / "run.json", run)
        run["complete"] = True
    except (Exception, KeyboardInterrupt) as exc:
        run["error"] = str(exc) or "Interrupted by user"
        run["complete"] = False
    finally:
        try:
            supply.set_output(False)
            run["output_off_confirmed"] = True
        except Exception as exc:
            run["shutdown_error"] = str(exc)
            run["complete"] = False
        save_json(folder / "run.json", run)
    return folder


def render(folder: Path, trajectory: Path | None = None) -> dict:
    """Combine raw records, settled curves and bracketed per-branch MPP candidates."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    points, peaks, assessments = [], [], []
    with (folder / "all_samples.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = None
        for item in run["sweeps"]:
            path = folder / item["csv"]
            if not path.exists():
                continue
            metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
            # Reassess the former transport-only rejection without rewriting acquisition history.
            legacy_rejection = run.get("error") == "Sweep incomplete or data quality failed; see individual JSON and source log"
            item["complete"] = bool((item["complete"] or legacy_rejection)
                                    and sweep.sweep_finished(metadata) and not item.get("source_errors"))
            quality = data_quality(metadata)
            with path.open(newline="", encoding="utf-8") as source:
                reader = csv.DictReader(source)
                rows = list(reader)
                if writer is None:
                    writer = csv.DictWriter(handle, fieldnames=["sweep_index", "source_current_a", "sweep_complete", *reader.fieldnames])
                    writer.writeheader()
                for row in rows:
                    writer.writerow(dict(sweep_index=item["index"], source_current_a=item["source_current_a"],
                                         sweep_complete=item["complete"], **row))
            ps, excluded = intact_points(rows)
            assessments.append(dict(sweep_index=item["index"], complete=item["complete"],
                                    data_quality=quality, excluded_duty_visits=excluded))
            for p in ps:
                p.update(sweep_index=item["index"], source_current_a=item["source_current_a"],
                         sweep_complete=item["complete"], data_quality=quality["status"])
            points.extend(ps)
            for branch in ("up", "down"):
                branch_points = [p for p in ps if p["branch"] == branch]
                if not branch_points:
                    continue
                best = max(branch_points, key=lambda p: p["pin_w"])
                bracketed = (best["pin_w"] > 0
                             and any(p["vin_v"] < best["vin_v"] and p["pin_w"] < best["pin_w"] for p in branch_points)
                             and any(p["vin_v"] > best["vin_v"] and p["pin_w"] < best["pin_w"] for p in branch_points))
                peaks.append(dict(best, peak_bracketed=bracketed,
                                  reference_usable=bool(item["complete"] and bracketed)))
    for name, values in (("settled_points.csv", points), ("reference_mpp.csv", peaks)):
        with (folder / name).open("w", newline="", encoding="utf-8") as handle:
            if values:
                writer = csv.DictWriter(handle, fieldnames=list(values[0]))
                writer.writeheader()
                writer.writerows(values)
    fig, axes = plt.subplots(1, 2, figsize=(13, 6), layout="constrained")
    try:
        colors = plt.get_cmap("viridis")
        for n, item in enumerate(run["sweeps"]):
            color = colors(n / max(1, len(run["sweeps"]) - 1))
            for branch, style in (("up", "-"), ("down", "--")):
                ps = [p for p in points if p["sweep_index"] == item["index"] and p["branch"] == branch]
                if not ps:
                    continue
                quality_warning = any(a["sweep_index"] == item["index"] and a["data_quality"]["status"] == "warning" for a in assessments)
                label = f"{item['source_current_a']:g} A" + (" (partial)" if not item["complete"] else "") + (" [data warning]" if quality_warning else "")
                axes[0].plot([p["iin_a"] for p in ps], [p["vin_v"] for p in ps], style,
                             color=color, linewidth=.8, label=label if branch == "up" else None)
                axes[1].plot([p["vin_v"] for p in ps], [p["pin_w"] for p in ps], style, color=color, linewidth=.8)
        for branch, style in (("up", "ko-"), ("down", "ks--")):
            ps = sorted([p for p in peaks if p["reference_usable"] and p["branch"] == branch],
                        key=lambda p: p["source_current_a"])
            if ps:
                axes[0].plot([p["iin_a"] for p in ps], [p["vin_v"] for p in ps], style,
                             markersize=3, label=f"Reference MPP ({branch})")
                axes[1].plot([p["vin_v"] for p in ps], [p["pin_w"] for p in ps], style, markersize=3)
        if trajectory:
            with trajectory.open(newline="", encoding="utf-8") as handle:
                rows = [r for r in csv.DictReader(handle) if r["valid"] == "True"
                        and r["switching"] == "True" and r["fault"] == "False" and r["channel"] == "A"]
            shown = rows[::max(1, math.ceil(len(rows) / 20000))]
            axes[0].scatter([float(r["iin_ma"]) / 1000 for r in shown],
                            [float(r["vin_mv"]) / 1000 for r in shown], s=1, c="tomato",
                            alpha=.3, label="Measurement 5 (display subsampled)")
        axes[0].set(xlabel="Converter input current (A)", ylabel="Converter input voltage (V)")
        axes[1].set(xlabel="Converter input voltage (V)", ylabel="Input power (W)")
        for ax in axes:
            ax.grid(alpha=.25)
        if axes[0].lines or axes[0].collections:
            axes[0].legend(fontsize=7, ncol=2)
        directions = "solid: duty up; dashed: duty down" if any(p["branch"] == "down" for p in points) else "upward duty sweep"
        fig.suptitle(f"IV characterization — {'complete' if run['complete'] else 'PARTIAL'}\n"
                     f"Settled duty holds; {directions}; labels: supply setting")
        fig.savefig(folder / "iv_curves.svg")
        fig.savefig(folder / "iv_curves.png", dpi=160)
    finally:
        plt.close(fig)
    result = dict(settled_points=len(points), usable_reference_points=sum(p["reference_usable"] for p in peaks),
                  sweeps=assessments,
                  trajectory=str(trajectory) if trajectory else None,
                  averaging="Exclude whole duty visits bordering gaps; discard first 20 ms; require 40 ms span and 10 retained samples; mean(V*I).",
                  qualification="Bracketed measured maxima, not calibrated ground truth. Check temperature drift, settling and repeatability.",
                  timebase="Raw MCU t_s and host t_host_s restart each sweep. Source logs use host monotonic clock; per-sweep JSON stores recorder origin.")
    save_json(folder / "analysis.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--port", help="Live capture: enables supply and channel A sweep")
    mode.add_argument("--replot", type=Path, help="Existing characterization folder; no hardware")
    parser.add_argument("--currents", type=float, nargs="+", default=[n / 2 for n in range(1, 17)])
    parser.add_argument("--resource", help="Optional SPS5162X VISA resource")
    parser.add_argument("--timeout", type=float, default=95, help="Per-sweep host deadline (seconds; default 95, firmware 85)")
    parser.add_argument("--settle", type=float, default=2, help="Supply settling before each sweep (seconds)")
    parser.add_argument("--off-seconds", type=float, default=5,
                        help="Supply-OFF pause before each sweep, including the first (default 5 seconds)")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "captures")
    parser.add_argument("--trajectory", type=Path, help="Measurement 5 measure.csv to overlay")
    parser.add_argument("--notes", default="", help="Panel temperature, setup, board/firmware revision, etc.")
    args = parser.parse_args()
    try:
        validate(args.currents, args.timeout, args.settle, args.off_seconds)
        if args.trajectory:
            with args.trajectory.open(newline="", encoding="utf-8") as handle:
                fields = csv.DictReader(handle).fieldnames or []
            if not {"valid", "switching", "fault", "channel", "iin_ma", "vin_mv"} <= set(fields):
                raise ValueError("--trajectory must be a measurement-5 measure.csv")
        folder = args.replot
        if folder is None:
            with SPS5162X.open(args.resource) as supply:
                folder = capture(supply, args.port, args.currents, output=args.output,
                                 timeout=args.timeout, settle=args.settle,
                                 off_seconds=args.off_seconds, notes=args.notes)
        print(json.dumps(render(folder, args.trajectory), indent=2))
        run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        if not run["complete"]:
            print(json.dumps(run, indent=2))
        print(f"Saved CSVs and plots: {folder}")
        return int(not run["complete"])
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Characterization failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
