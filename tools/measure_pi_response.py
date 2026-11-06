#!/usr/bin/env python3
"""PI-only input-voltage step capture. Capture enables channel A and the supply.

uv run --project tools --locked python tools/measure_pi_response.py --port COM6 --current 5 --targets 25 30 25 --hold 3
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from queue import Queue
from threading import Event
import statistics

import console
import measure
import measure_mppt_profile as runner
from current_profile import CurrentStep, validate_profile
from sps5162x import SPS5162X


class TargetSequence:
    def __init__(self, targets: list[float], hold: float):
        if (not targets or not math.isfinite(hold) or hold < 1 or len(targets) * hold > 150
                or any(not math.isfinite(v) or not 2 <= v <= 30.5 or
                       abs(v * 1000 - round(v * 1000)) > 1e-6 for v in targets)):
            raise ValueError("Targets must be 2..30.5 V in 1 mV increments; hold >=1 s; total <=150 s")
        if any(a == b for a, b in zip(targets, targets[1:])):
            raise ValueError("Adjacent targets must differ for acknowledgement matching")
        self.targets = [round(v * 1000) for v in targets]
        self.hold = hold
        self.queue = Queue()
        self.ready = Event()
        self.complete = Event()
        self.sent = -1
        self.acknowledged = -1
        self.start = None
        self.pending_since = None
        self.events = []

    def sample(self, row: dict) -> None:
        if row["end"] or row["fault"]:
            return
        if row["channel"] != "A" or not row["flags"] & measure.PI_TEST:
            raise ValueError("PI-test firmware identifier missing; flash the new build")
        now = row["t_host_s"]
        if self.pending_since is not None and now - self.pending_since > 2:
            raise TimeoutError("PI reference was not acknowledged within 2 s")
        if row["valid"] and row["switching"] and self.start is None:
            self.start = now
        wanted = max(0, self.sent)
        if self.start is not None and self.acknowledged == self.sent and self.sent >= 0:
            phase_start = max(self.start, self.events[-1]["acknowledgement_host_s"])
            if now - phase_start >= self.hold:
                if self.sent == len(self.targets) - 1:
                    self.complete.set()
                else:
                    wanted = self.sent + 1
        if row["valid"] and wanted > self.sent:
            if self.sent != self.acknowledged:
                raise TimeoutError("Previous reference still unacknowledged")
            self.queue.put(console.encode(console.OPCODES["pi_target"], self.targets[wanted].to_bytes(2, "little")))
            self.sent = wanted
            self.pending_since = now
            self.events.append({"index": wanted, "target_mv": self.targets[wanted], "queued_host_s": now})

    def diagnostic(self, row: dict) -> None:
        if self.sent < 0 or self.acknowledged == self.sent or row["channel"] != "A":
            return
        if row["updated"] and row["target_mv"] == self.targets[self.sent]:
            self.events[-1].update(applied_mcu_s=row["t_s"], acknowledgement_host_s=row["t_host_s"])
            self.acknowledged = self.sent
            self.pending_since = None
            if self.sent == 0:
                self.ready.set()


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def analyse(folder: Path, band_v: float = .1, dwell: float = .5) -> dict:
    if not all(math.isfinite(x) and x > 0 for x in (band_v, dwell)):
        raise ValueError("Voltage band and dwell must be positive and finite")
    metadata = json.loads((folder / "pi_test.json").read_text())
    run = json.loads((folder / "run.json").read_text())
    samples = read_csv(folder / "measure.csv")
    diagnostics = read_csv(folder / "measure_mppt.csv")
    events = [r for r in diagnostics if r["updated"] == "True" and int(r["target_mv"]) > 0]
    active = [r for r in samples if r["valid"] == "True" and r["switching"] == "True" and r["fault"] == "False"]
    if not active or not events:
        raise ValueError("No active PI samples or applied reference events; raw capture is retained")
    # End before source OFF command, not after its verification/readback delay.
    stop_host = metadata.get("source_off_requested_host_s", 0)
    active = [r for r in active if float(r["t_host_s"]) < stop_host]
    if not active:
        raise ValueError("No samples before the recorded source-off request")
    end = float(active[-1]["t_s"])
    result = {"complete": run["complete"] and metadata["targets_acknowledged"],
              "band_v": band_v, "dwell_s": dwell, "data_quality": run.get("data_quality", {}),
              "definition": "First raw-sample dwell in absolute voltage band; invalid samples or >10 ms gaps reset dwell.",
              "timing": "MCU acquisition and reference-application timestamps; startup begins at first switching sample.",
              "phases": []}
    for i, event in enumerate(events):
        start = max(float(event["t_s"]), float(active[0]["t_s"])) if i == 0 else float(event["t_s"])
        finish = float(events[i+1]["t_s"]) if i+1 < len(events) else end
        finish = min(finish, end)
        target = float(event["target_mv"]) / 1000
        segment = [r for r in samples if start <= float(r["t_s"]) < finish]
        since = previous = None
        settled = None
        values = []
        tail = []
        for r in segment:
            t = float(r["t_s"])
            valid = r["valid"] == "True" and r["switching"] == "True" and r["fault"] == "False"
            if not valid or int(r["sequence_gap"]) or (previous is not None and t - previous > .01):
                since = None
            previous = t
            if not valid:
                continue
            voltage = float(r["vin_mv"]) / 1000
            values.append(voltage)
            if t >= max(start, finish - 2):
                tail.append(voltage)
            if abs(voltage - target) <= band_v:
                since = t if since is None else since
                if settled is None and t - since >= dwell:
                    settled = since - start
            else:
                since = None
        rising = i > 0 and target > float(events[i-1]["target_mv"]) / 1000
        overshoot = (max(0, max(values)-target) if rising else max(0, target-min(values))) if values and i else None
        result["phases"].append({"target_v": target, "start_mcu_s": start, "end_mcu_s": finish,
            "settling_s": settled, "overshoot_v": overshoot,
            "tail_mean_error_v": statistics.mean(tail)-target if tail else None,
            "tail_std_v": statistics.pstdev(tail) if tail else None})
    (folder / "pi_response.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 1, sharex=True, figsize=(11, 8), layout="constrained")
    try:
        t = [float(r["t_s"]) for r in active]
        axes[0].plot(t, [float(r["vin_mv"])/1000 for r in active], lw=.6, label="Measured Vin", rasterized=True)
        for i, phase in enumerate(result["phases"]):
            axes[0].hlines(phase["target_v"], phase["start_mcu_s"], phase["end_mcu_s"],
                           color="black", linestyle="--", label="Applied target" if i == 0 else None)
        axes[1].plot(t, [float(r["duty"])/10 for r in active], lw=.6, rasterized=True)
        axes[2].plot(t, [float(r["vin_mv"])*float(r["iin_ma"])/1e6 for r in active], lw=.6, rasterized=True)
        for ax, label in zip(axes, ["Input voltage (V)", "Duty (%)", "Input power (W)"]):
            ax.set_ylabel(label)
            ax.grid(alpha=.25)
        axes[0].legend()
        axes[2].set_xlabel("MCU capture time (s)")
        fig.suptitle("PI-only voltage response" + (" — PARTIAL" if not result["complete"] else ""))
        for suffix in ("svg", "png"):
            fig.savefig(folder / f"pi_response.{suffix}", dpi=160)
    finally:
        plt.close(fig)
    return result


def capture(args) -> Path:
    sequence = TargetSequence(args.targets, args.hold)
    if not math.isfinite(args.current) or args.current <= 0:
        raise ValueError("Current must be positive and finite")
    steps = [CurrentStep(0, args.current)]
    duration = len(args.targets) * args.hold
    validate_profile(steps, args.voltage, 35, 8, duration, .5)
    # The runner switches off before analysing. Record command-start time separately.
    import time
    metadata = {"targets_v": args.targets, "hold_s": args.hold, "source_current_a": args.current,
                "gains_note": "Firmware defaults Kp=.002 Ki=.3; record flashed build if changed.",
                "events": sequence.events, "targets_acknowledged": False}
    with SPS5162X.open(args.resource) as supply:
        original = supply.set_output
        origin = None
        def sample(row):
            nonlocal origin
            if origin is None:
                origin = time.monotonic() - row["t_host_s"]
            sequence.sample(row)
        def set_output(enabled):
            if not enabled and sequence.start is not None and origin is not None:
                metadata.setdefault("source_off_requested_host_s", time.monotonic() - origin)
            return original(enabled)
        supply.set_output = set_output
        run = runner.capture(supply, args.port, steps, output_dir=args.output,
            voltage_limit_v=args.voltage, final_hold_s=duration, make_plots=False,
            capture_command="pi_test", command_queue=sequence.queue,
            on_diagnostic=sequence.diagnostic, on_measurement_sample=sample,
            startup_ready=sequence.ready, completion_ready=sequence.complete)
    folder = Path(run["folder"])
    metadata["targets_acknowledged"] = sequence.acknowledged == len(sequence.targets)-1
    metadata["source_off_requested_host_s"] = metadata.get("source_off_requested_host_s", 0)
    (folder / "pi_test.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Capture complete: {run['complete']}; supply OFF confirmed: {run['output_off_confirmed']}")
    return folder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--port")
    mode.add_argument("--replay", type=Path)
    parser.add_argument("--current", type=float, default=5)
    parser.add_argument("--targets", type=float, nargs="+", default=[25, 30, 25])
    parser.add_argument("--hold", type=float, default=3)
    parser.add_argument("--voltage", type=float, default=35)
    parser.add_argument("--resource")
    parser.add_argument("--band", type=float, default=.1)
    parser.add_argument("--dwell", type=float, default=.5)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1]/"captures")
    args = parser.parse_args()
    try:
        if not all(math.isfinite(v) and v > 0 for v in (args.band, args.dwell)):
            raise ValueError("Band and dwell must be positive and finite")
        folder = args.replay if args.replay is not None else capture(args)
        result = analyse(folder, args.band, args.dwell)
        print(json.dumps(result, indent=2))
        print(f"Saved: {folder}")
        return int(not result["complete"])
    except Exception as error:
        print(f"PI test failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
