#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyserial>=3.5", "matplotlib>=3.8"]
# ///
"""Record mode_measure packets. This process must own the serial port."""

from __future__ import annotations

import argparse
import binascii
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
from threading import Event
import time
from typing import Callable

import serial
import console

# firmware/Src/drivers/measure_io.c; little endian, CRC-16/CCITT-FALSE.
MAGIC = b"MPM1"
RECORD = struct.Struct("<4sIIIiiiiHHIIH")
DIAGNOSTIC_MAGIC = b"MPD1"
DIAGNOSTIC_RECORD = struct.Struct("<4sIIIiiIIHHIIH")
DIAGNOSTIC_FIELDS = ("sequence", "time_us", "target_mv", "error_mv", "power_mw",
                     "age_ms", "decisions", "window_samples", "flags", "compared_target_mv", "dropped")
DIAGNOSTIC_CSV_FIELDS = ("t_s", "t_host_s", *DIAGNOSTIC_FIELDS, "channel", "updated",
                         "power_valid", "settled", "pi_tracking", "direction", "sequence_gap")
VALID, SWITCHING, FAULT, END, RESTORE_FAILED = 1, 2, 4, 8, 16
IV_SWEEP, IV_DESCENDING, IV_COMPLETE = 32, 64, 128
# Bits 6/7 have separate meanings when IV_SWEEP is clear.
START_GATED, START_ACCEPTED = 64, 128
IV_VOLTAGE, IV_LIMITED = 2048, 4096
IV_CURRENT_RAMP = 8192
IV_DUTY_RAMP = 16384
IV_RETURN_RAMP = 32768
FIELDS = ("sequence", "time_us", "span_us", "vin_mv", "iin_ma", "vout_mv",
          "iout_ma", "duty", "flags", "dropped", "errors")
CSV_FIELDS = ("t_s", "t_host_s", *FIELDS, "channel", "valid", "switching",
              "fault", "end", "restore_failed", "sequence_gap")


class Parser:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.crc_errors = 0
        self.discarded = 0

    def feed(self, data: bytes) -> list[dict]:
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= len(MAGIC):
            indices = [self.buffer.find(magic) for magic in (MAGIC, DIAGNOSTIC_MAGIC)]
            index = min((i for i in indices if i >= 0), default=-1)
            if index < 0:
                discard = len(self.buffer) - len(MAGIC) + 1
                self.discarded += discard
                del self.buffer[:discard]
                break
            self.discarded += index
            del self.buffer[:index]
            if len(self.buffer) < RECORD.size:
                break
            packet = self.buffer[:RECORD.size]
            if binascii.crc_hqx(packet[:-2], 0xFFFF) != int.from_bytes(packet[-2:], "little"):
                self.crc_errors += 1
                self.discarded += 1
                del self.buffer[0]
                continue
            if packet[:4] == DIAGNOSTIC_MAGIC:
                values = DIAGNOSTIC_RECORD.unpack(packet)
                result.append(dict(zip(DIAGNOSTIC_FIELDS, values[1:-1]), _diagnostic=True))
            else:
                values = RECORD.unpack(packet)
                result.append(dict(zip(FIELDS, values[1:-1])))
            del self.buffer[:RECORD.size]
        return result


class Timeline:
    def __init__(self) -> None:
        self.previous_time: int | None = None
        self.previous_sequence: int | None = None
        self.elapsed_us = 0

    def stamp(self, sample: dict, host_time: float) -> dict:
        tick = sample["time_us"]
        seq = sample["sequence"]
        gap = seq if self.previous_sequence is None else (seq - self.previous_sequence - 1) & 0xFFFFFFFF
        if self.previous_time is None:
            self.elapsed_us = tick
        else:
            delta = (tick - self.previous_time) & 0xFFFFFFFF
            if delta > 0x7FFFFFFF:
                raise ValueError("MCU timestamp moved backwards; possible reset/new session")
            self.elapsed_us += delta
        self.previous_time, self.previous_sequence = tick, seq
        return {"t_s": self.elapsed_us / 1_000_000, "t_host_s": host_time, "sequence_gap": gap}

    def row(self, sample: dict, host_time: float) -> dict:
        stamp = self.stamp(sample, host_time)
        flags = sample["flags"]
        result = {**stamp,
                  **sample, "channel": "ABCDE"[(flags >> 8) & 7] if ((flags >> 8) & 7) < 5 else "?",
                  "valid": bool(flags & VALID), "switching": bool(flags & SWITCHING),
                  "fault": bool(flags & FAULT), "end": bool(flags & END),
                  "restore_failed": bool(flags & RESTORE_FAILED)}
        if not flags & VALID:
            for field in ("vin_mv", "iin_ma", "vout_mv", "iout_ma"):
                result[field] = ""
        return result


def record(port: str, seconds: float, output: Path, *, command: str = "measure",
           required_flags: int = 0, stop_event: Event | None = None,
           on_started: Callable[[float], None] | None = None,
           on_sample: Callable[[dict], None] | None = None, make_plots: bool = True,
           start_event: Event | None = None) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_path = output.with_suffix(".bin")
    meta_path = output.with_suffix(".json")
    diagnostic_path = output.with_name(f"{output.stem}_mppt.csv")
    if any(path.exists() for path in (output, raw_path, meta_path, diagnostic_path, output.with_suffix(".svg"),
                                       output.with_name(f"{output.stem}_dots.svg"))):
        raise FileExistsError("Capture paths already exist; choose another --output")
    parser, timeline = Parser(), Timeline()
    diagnostic_timeline = Timeline()
    summary = {"port": port, "baud": console.BAUD, "protocol": "MPM1",
               "command": command,
               "started_utc": datetime.now(timezone.utc).isoformat(),
               "requested_seconds": seconds, "samples": 0, "sequence_gaps": 0,
               "end_received": False, "fault": False, "restore_failed": False,
               "mppt_records": 0, "mppt_sequence_gaps": 0, "mppt_firmware_dropped": 0}
    interrupted = False
    try:
        with serial.Serial(port, console.BAUD, timeout=0.05, write_timeout=1.0) as link, \
                output.open("x", newline="", encoding="utf-8") as csv_file, raw_path.open("xb") as raw_file, \
                diagnostic_path.open("x", newline="", encoding="utf-8") as diagnostic_file:
            writer = csv.DictWriter(csv_file, CSV_FIELDS)
            writer.writeheader()
            diagnostic_writer = csv.DictWriter(diagnostic_file, DIAGNOSTIC_CSV_FIELDS)
            diagnostic_writer.writeheader()

            def receive() -> None:
                data = link.read(max(1, min(link.in_waiting, 65536)))
                raw_file.write(data)
                host_time = time.monotonic() - started
                for sample in parser.feed(data):
                    if sample.pop("_diagnostic", False):
                        flags = sample["flags"]
                        stamp = diagnostic_timeline.stamp(sample, host_time)
                        row = {**stamp, **sample,
                               "channel": "ABCDE"[(flags >> 8) & 7] if ((flags >> 8) & 7) < 5 else "?",
                               "updated": bool(flags & 1), "power_valid": bool(flags & 2),
                               "settled": bool(flags & 4), "pi_tracking": bool(flags & 8),
                               "direction": 1 if flags & 16 else -1}
                        if not row["power_valid"]:
                            row["power_mw"] = ""
                            row["compared_target_mv"] = ""
                        diagnostic_writer.writerow(row)
                        summary["mppt_records"] += 1
                        summary["mppt_sequence_gaps"] += row["sequence_gap"]
                        summary["mppt_firmware_dropped"] = sample["dropped"]
                        continue
                    if sample["flags"] & required_flags != required_flags:
                        raise ValueError("Unexpected measurement mode in received record")
                    row = timeline.row(sample, host_time)
                    writer.writerow(row)
                    if on_sample is not None:
                        on_sample(row)
                    summary["samples"] += int(row["valid"])
                    summary["sequence_gaps"] += row["sequence_gap"]
                    summary["end_received"] |= row["end"]
                    summary["fault"] |= row["fault"]
                    summary["restore_failed"] |= row["restore_failed"]
                    if row["end"]:
                        summary["final_flags"] = sample["flags"]
                    summary["firmware_dropped"] = sample["dropped"]
                    summary["firmware_errors"] = sample["errors"]
                    summary["last_mcu_time_s"] = row["t_s"]
                    summary["last_host_time_s"] = host_time
                    if row["valid"]:
                        summary.setdefault("first_sample_s", row["t_s"])
                        summary["last_sample_s"] = row["t_s"]
                        summary["max_span_us"] = max(summary.get("max_span_us", 0), sample["span_us"])
                        duration = row["t_s"] - summary["first_sample_s"]
                        if duration > 0:
                            summary["received_rate_hz"] = (summary["samples"] - 1) / duration

            try:
                link.write(console.encode(console.OPCODES["stop"]))
                drain_until = time.monotonic() + 0.5
                while time.monotonic() < drain_until:
                    link.read(max(1, min(link.in_waiting, 65536)))
                link.reset_input_buffer()
                started = time.monotonic()
                summary["host_origin_monotonic_s"] = started
                summary["host_origin_utc"] = datetime.now(timezone.utc).isoformat()
                link.write(console.encode(console.OPCODES[command]))
                if on_started is not None:
                    on_started(started)
                while time.monotonic() - started < seconds and not (stop_event and stop_event.is_set()):
                    receive()
                    elapsed = time.monotonic() - started
                    if summary["end_received"]:
                        break
                    if (command == "measure" and summary["samples"] and "start_command_s" not in summary
                            and not summary["fault"] and not (stop_event and stop_event.is_set())
                            and (start_event is None or start_event.is_set())):
                        link.write(console.encode(console.OPCODES["measure_start"]))
                        summary["start_command_s"] = time.monotonic() - started
                    if elapsed - summary.get("last_host_time_s", 0.0) > 3.0:
                        raise TimeoutError("No measurement records for 3 s; mode may have been refused")
            except KeyboardInterrupt:
                interrupted = True
            finally:
                link.write(console.encode(console.OPCODES["stop"]))
                if "started" in locals():
                    deadline = time.monotonic() + 1.0
                    while not summary["end_received"] and time.monotonic() < deadline:
                        receive()
                csv_file.flush()
                diagnostic_file.flush()
    except Exception as exc:
        summary["error"] = str(exc)
        raise
    finally:
        summary.update(crc_errors=parser.crc_errors, discarded_bytes=parser.discarded,
                       interrupted=interrupted)
        meta_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        if output.exists() and make_plots:
            try:
                plot_capture(output)
            except Exception as exc:
                summary["plot_error"] = str(exc)
                meta_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
                print(f"Plot failed (raw data preserved): {exc}")
    return summary


def plot_capture(csv_path: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    summary = json.loads(csv_path.with_suffix(".json").read_text(encoding="utf-8"))
    series = {key: [] for key in ("t", "vin", "vout", "iin", "iout", "pin", "pout", "duty")}
    for row in rows:
        if row["valid"] != "True":
            continue
        if int(row["sequence_gap"]) and series["t"]:
            for values in series.values():
                values.append(float("nan"))
        vin, iin, vout, iout = (float(row[key]) / 1000 for key in
                               ("vin_mv", "iin_ma", "vout_mv", "iout_ma"))
        values = (float(row["t_s"]), vin, vout, iin, iout, vin * iin, vout * iout,
                  float(row["duty"]) / 10)
        for key, value in zip(series, values):
            series[key].append(value)

    fig, axes = plt.subplots(4, 1, sharex=True, figsize=(11, 10), layout="constrained")
    try:
        status = "complete" if summary.get("end_received") else "incomplete"
        if summary.get("fault") or summary.get("error"):
            status = "fault / incomplete"
        elif summary.get("interrupted"):
            status = "interrupted"
        elif summary.get("final_flags", 0) & IV_LIMITED:
            status = "sweep limited / partial"
        elif summary.get("command") == "measure_iv_sweep" and not summary.get("final_flags", 0) & IV_COMPLETE:
            status = "incomplete sweep"
        channels = ", ".join(sorted({r["channel"] for r in rows if r["valid"] == "True"}))
        fig.suptitle(f"Measurement capture — channel {channels or '?'} — {status}\n{csv_path.parent.name}")
        for ax, fields, ylabel in zip(axes,
                                     (("vin", "vout"), ("iin", "iout"), ("pin", "pout"), ("duty",)),
                                     ("Voltage (V)", "Current (A)", "Power (W)", "Duty (%)")):
            for key in fields:
                ax.plot(series["t"], series[key], label=key, linewidth=1,
                        marker="." if summary.get("samples", 0) < 2 else None,
                        drawstyle="steps-post" if key == "duty" else "default")
            ax.set_ylabel(ylabel)
            ax.grid(alpha=0.25)
            ax.legend(loc="upper right", ncols=len(fields))
        axes[-1].set_xlabel("MCU time since capture entry (s)")
        if not series["t"]:
            axes[0].text(0.5, 0.5, "No valid measurement samples", transform=axes[0].transAxes,
                         ha="center", va="center")
        rate = summary.get("received_rate_hz")
        rate_text = f"{rate:.1f} sets/s" if rate is not None else "rate unavailable"
        axes[0].set_title(f"{summary.get('samples', 0)} samples · {rate_text} · "
                          f"{summary.get('sequence_gaps', 0)} missing records", fontsize=10, loc="left")
        svg_path = csv_path.with_suffix(".svg")
        fig.savefig(svg_path, format="svg")
        for ax in axes:
            for line in ax.get_lines():
                line.set_linestyle("None")
                line.set_drawstyle("default")
                line.set_marker("o")
                line.set_markersize(1.8)
                line.set_markeredgewidth(0)
            ax.legend(loc="upper right", ncols=len(ax.get_lines()))
        fig.savefig(csv_path.with_name(f"{csv_path.stem}_dots.svg"), format="svg")
        return svg_path
    finally:
        plt.close(fig)


def main() -> int:
    args_parser = argparse.ArgumentParser(description=__doc__)
    args_parser.add_argument("--port", required=True)
    args_parser.add_argument("--seconds", type=float, default=10.0)
    args_parser.add_argument("--output", type=Path,
                             default=Path(__file__).resolve().parents[1] / "captures",
                             help="parent directory for a new timestamped capture folder")
    args = args_parser.parse_args()
    if not 0 < args.seconds <= 3600:
        args_parser.error("--seconds must be greater than zero and at most 3600")
    if args.output.suffix.lower() == ".csv":
        args_parser.error("--output now selects a parent directory, not a CSV filename")
    folder = args.output / f"{datetime.now().astimezone():%Y%m%d-%H%M%S-%f}_measure"
    output = folder / "measure.csv"
    try:
        folder.mkdir(parents=True, exist_ok=False)
        print(f"Capture folder: {folder}")
        summary = record(args.port, args.seconds, output)
    except (OSError, ValueError, serial.SerialException, TimeoutError) as exc:
        print(f"Capture failed: {exc}")
        return 1
    print(f"Saved capture with {summary['samples']} samples to {folder}")
    print(json.dumps(summary, indent=2))
    return int(not summary["end_received"] or summary["fault"] or not summary["samples"]
               or "plot_error" in summary)


if __name__ == "__main__":
    raise SystemExit(main())
