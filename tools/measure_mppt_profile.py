#!/usr/bin/env python3
"""Capture measurement 5 while stepping the SPS5162X current limit.

  uv run --project tools --locked python tools/measure_mppt_profile.py --port COM6

Defaults: 2 s PWM-off baseline; 0.5 -> 8 A over 300 s; 5 s final hold; 2 s tail.
This command enables the supply output and starts channel A active MPPT.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from threading import Event, Thread
import time

import current_profile
import measure
from sps5162x import SPS5162X, SupplyError

MPPT_START_TIMEOUT_S = 30.0 # 2 s settling window plus time for a rising source/profile to stabilize.

def plot_trajectory(path: Path, *, complete: bool) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with path.open(newline="", encoding="utf-8") as handle:
        rows = [r for r in csv.DictReader(handle) if r["valid"] == "True" and
                r["switching"] == "True" and r["fault"] == "False" and r["channel"] == "A"]
    fig, ax = plt.subplots(figsize=(9, 6), layout="constrained")
    details = {"active_samples": len(rows)}
    try:
        if rows:
            # Limit SVG display size; all samples remain in measure.csv and peak search.
            stride = max(1, math.ceil(len(rows) / 20000))
            shown = rows[::stride]
            points = ax.scatter([float(r["iin_ma"]) / 1000 for r in shown],
                                [float(r["vin_mv"]) / 1000 for r in shown],
                                c=[float(r["t_s"]) for r in shown], s=2, linewidths=0, cmap="viridis")
            fig.colorbar(points, ax=ax, label="MCU time since capture entry (s)")
            peak = max(rows, key=lambda r: float(r["iin_ma"]) * float(r["vin_mv"]))
            amps, volts = float(peak["iin_ma"]) / 1000, float(peak["vin_mv"]) / 1000
            ax.annotate(f"Maximum sampled input power: {amps * volts:.2f} W\n"
                        f"{volts:.3f} V, {amps:.3f} A", xy=(amps, volts),
                        xytext=(0.02, 0.02), textcoords="axes fraction", fontsize=9,
                        bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "none"})
            details.update(display_stride=stride, peak_sampled_input_w=amps * volts,
                           peak_sample_time_s=float(peak["t_s"]))
        else:
            ax.text(0.5, 0.5, "No valid samples with PWM switching", transform=ax.transAxes, ha="center")
        ax.set(xlabel="Input current (A)", ylabel="Input voltage (V)",
               title=f"Measurement 5: active MPPT V–I trajectory — {'complete' if complete else 'partial'}\n"
                     "Reference MPP path requires separate source characterization")
        ax.grid(alpha=0.25)
        fig.savefig(path.with_name("trajectory.svg"))
    finally:
        plt.close(fig)
    return details


def capture(supply: SPS5162X, port: str, steps: list[current_profile.CurrentStep], *,
            output_dir: Path, voltage_limit_v: float = 35.0, pre_seconds: float = 2.0,
            final_hold_s: float = 5.0, post_seconds: float = 2.0,
            max_lateness_s: float = 0.5, make_plots: bool = True,
            capture_command: str = "measure", command_queue=None,
            on_diagnostic=None, on_measurement_sample=None, startup_ready=None,
            completion_ready=None) -> dict:
    steps = list(steps)
    if capture_command not in ("measure", "pi_test"):
        raise ValueError("Unsupported gated capture command")
    current_profile.validate_profile(steps, voltage_limit_v, supply.max_voltage_v,
                                    supply.max_current_a, final_hold_s, max_lateness_s)
    if not any(step.current_a > 0 for step in steps):
        raise ValueError("Active MPPT capture needs at least one nonzero current setting")
    if not all(math.isfinite(t) and t >= 0 for t in (pre_seconds, post_seconds)):
        raise ValueError("Baseline and tail times must be finite and nonnegative")
    # 5 s startup timeout + setup/USB margin; reaching this cap is a failed capture.
    capture_seconds = pre_seconds + steps[-1].time_s + final_hold_s + post_seconds + 15.0
    if capture_seconds > 3600:
        raise ValueError("Combined capture must fit within one hour")
    suffix = "measure_pi_response" if capture_command == "pi_test" else "measure_mppt_profile"
    folder = Path(output_dir) / f"{datetime.now().astimezone():%Y%m%d-%H%M%S-%f}_{suffix}"
    folder.mkdir(parents=True, exist_ok=False)
    print(f"Measurement folder: {folder}", flush=True)
    stop, abort, ready, start = Event(), Event(), Event(), Event()
    uart: dict = {}
    result = {"folder": str(folder), "complete": False, "started_utc": datetime.now(timezone.utc).isoformat(),
              "port": port, "capture_command": capture_command,
              "supply_identity": supply.identity, "supply_resource": supply.resource,
              "pre_seconds": pre_seconds, "post_seconds": post_seconds,
              "active_samples": 0, "output_off_confirmed": False,
              "timebase_note": "profile.csv times and measure.csv t_host_s share the MCU capture-command "
                               "host monotonic origin. UART arrival and sequential VISA operations are "
                               "not simultaneous ADC samples; measure.csv t_s is MCU time.",
              "reference_mpp_path": "Not characterized; trajectory does not establish tracking efficiency."}

    def on_started(origin: float) -> None:
        uart["origin"] = origin

    def on_sample(row: dict) -> None:
        if not row["flags"] & measure.START_GATED:
            uart.setdefault("error", "Firmware lacks gated measurement startup; flash the updated firmware before running")
            abort.set()
        if (row["switching"] or row["flags"] & measure.START_ACCEPTED) and not start.is_set():
            uart.setdefault("error", "Measurement startup released before the first nonzero source setting was verified")
            abort.set()
        unexpected_mode = (row["channel"] != "A" or bool(row["flags"] & measure.IV_SWEEP)
                           or bool(row["flags"] & measure.PI_TEST) != (capture_command == "pi_test"))
        if row["fault"] or row["restore_failed"] or unexpected_mode or (row["end"] and not stop.is_set()):
            uart.setdefault("error", f"Unexpected MCU record: channel={row['channel']}, flags=0x{row['flags']:04X}")
            abort.set()
        if row["valid"]:
            uart["last_valid_s"] = row["t_host_s"]
            if row["flags"] & measure.START_ACCEPTED:
                result.setdefault("start_accepted_s", row["t_host_s"])
            if not abort.is_set():
                ready.set()
            if row["switching"]:
                result.setdefault("first_switching_s", row["t_s"])
            result["active_samples"] += int(row["switching"])
            if (start.is_set() and result["active_samples"] == 0
                    and row["t_host_s"] - result["start_requested_s"] > MPPT_START_TIMEOUT_S):
                uart.setdefault("error", f"MPPT did not start within {MPPT_START_TIMEOUT_S:g} s of the first nonzero source setting")
                abort.set()
        elif row["t_host_s"] - uart.get("last_valid_s", 0) > 3.0:
            uart.setdefault("error", "No valid sensor samples for 3 s")
            abort.set()
        if on_measurement_sample is not None:
            on_measurement_sample(row)

    def record_uart() -> None:
        try:
            uart["summary"] = measure.record(port, capture_seconds, folder / "measure.csv",
                                             stop_event=stop, on_started=on_started,
                                             on_sample=on_sample, make_plots=False, start_event=start,
                                             command=capture_command, command_queue=command_queue,
                                             on_diagnostic=on_diagnostic)
        except BaseException as error:
            uart["error"] = f"{type(error).__name__}: {error}"
        finally:
            if not stop.is_set():
                uart.setdefault("error", "MCU recorder ended before the profile finished")
                abort.set()

    def wait(seconds: float) -> None:
        if abort.wait(seconds):
            raise SupplyError(uart.get("error", "MCU capture stopped"))

    def on_setpoint(step: current_profile.CurrentStep) -> None:
        if step.current_a > 0 and not start.is_set():
            result["start_requested_s"] = time.monotonic() - uart["origin"]
            start.set()

    worker = Thread(target=record_uart, name="measurement-uart", daemon=True)

    def stop_before_source_off() -> None:
        if completion_ready is not None:
            deadline = time.monotonic() + 15.0
            while not completion_ready.is_set():
                wait(0.01)
                if time.monotonic() >= deadline:
                    raise TimeoutError("PI target holds did not complete within the capture margin")
        result["stop_requested_s"] = time.monotonic() - uart["origin"]
        stop.set()
        # Recorder sends STOP and waits up to 1 s for END; allow thread/file cleanup.
        worker.join(timeout=3.0)
        summary = uart.get("summary", {})
        if (worker.is_alive() or "error" in uart or not summary.get("end_received")
                or summary.get("fault") or summary.get("restore_failed")
                or summary.get("final_flags", 0) & measure.SWITCHING):
            raise SupplyError("PWM shutdown was not confirmed by a clean END; switching source off anyway")
        result["pwm_stop_confirmed_s"] = time.monotonic() - uart["origin"]

    try:
        supply.set_output(False)
        worker.start()
        deadline = time.monotonic() + 5.0
        while not ready.is_set() or (startup_ready is not None and not startup_ready.is_set()):
            wait(0.05)
            if time.monotonic() >= deadline:
                raise TimeoutError("No valid channel A UART data within 5 s; supply remains off")
        wait(pre_seconds)
        print(f"Recording; starting {steps[0].current_a:g} -> {steps[-1].current_a:g} A "
              f"profile; final step at {steps[-1].time_s:g} s, then hold {final_hold_s:g} s", flush=True)
        result["profile"] = current_profile.run_current_profile(
            supply, steps, voltage_limit_v=voltage_limit_v, final_hold_s=final_hold_s,
            max_lateness_s=max_lateness_s, capture_folder=folder, clock_origin=uart["origin"],
            stop_event=abort, make_plots=False, on_setpoint=on_setpoint,
            on_complete=stop_before_source_off if capture_command == "pi_test" else None)
        if not result["profile"]["complete"]:
            raise SupplyError("Supply profile failed; see summary.json")
        if capture_command != "pi_test":
            wait(post_seconds)
    except BaseException as error:
        result["error"] = f"{type(error).__name__}: {error}"
        result["interrupted"] = isinstance(error, KeyboardInterrupt)
    finally:
        stop.set()
        abort.set()
        # Request MCU STOP concurrently with the supply OFF command.
        try:
            supply.set_output(False)
            result["output_off_confirmed"] = True
        except Exception as error:
            result["shutdown_error"] = str(error)
        if worker.ident is not None:
            worker.join(timeout=5.0)
        if worker.is_alive():
            uart["error"] = "UART worker did not stop within 5 s"
        meta_path = folder / "measure.json"
        uart_summary = uart.get("summary", {})
        if not uart_summary and meta_path.exists() and not worker.is_alive():
            uart_summary = json.loads(meta_path.read_text(encoding="utf-8"))
        result["measurement"] = uart_summary
        if "error" in uart:
            result["uart_error"] = uart["error"]
        profile_path = folder / "summary.json"
        if "profile" not in result and profile_path.exists():
            result["profile"] = json.loads(profile_path.read_text(encoding="utf-8"))
        result["complete"] = bool(
            "error" not in result and "uart_error" not in result and
            result["output_off_confirmed"] and result.get("profile", {}).get("complete") and
            "start_accepted_s" in result and "start_command_s" in uart_summary and
            uart_summary.get("end_received") and uart_summary.get("samples", 0) > 0 and
            result["active_samples"] > 1 and not any(uart_summary.get(k) for k in
                ("fault", "restore_failed", "error", "interrupted")))
        result["data_quality"] = {key: uart_summary.get(key, 0) for key in
                                   ("sequence_gaps", "crc_errors", "firmware_dropped", "firmware_errors",
                                    "mppt_sequence_gaps", "mppt_firmware_dropped")}
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        manifest = folder / "run.json"
        manifest.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        # Plot only after hardware shutdown and UART file closure.
        if make_plots:
            tasks = []
            if (folder / "profile.csv").exists():
                tasks.append(("profile", lambda: current_profile.plot_profile(folder / "profile.csv", shared_clock=True)))
            if (folder / "measure.csv").exists() and not worker.is_alive():
                tasks.extend((("measurement", lambda: measure.plot_capture(folder / "measure.csv")),
                              ("trajectory", lambda: result.update(trajectory=plot_trajectory(
                                  folder / "measure.csv", complete=result["complete"])))))
            for name, task in tasks:
                try:
                    task()
                except Exception as error:
                    result.setdefault("plot_errors", {})[name] = str(error)
            manifest.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="MCU UART port, e.g. COM6; disconnect the bench website first")
    parser.add_argument("--resource", help="SPS5162X VISA resource; defaults to the sole USB supply")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--ramp", type=float, nargs=2, metavar=("START_A", "END_A"),
                        help="Ramp endpoints in A (default 0.5 8)")
    source.add_argument("--profile", type=Path,
                        help="CSV: time_s,current_a; overrides the default gradual ramp")
    # (8 - 0.5) A / 300 s = 0.025 A/s; 0.5 s commands give 12/13 mA quantized steps.
    parser.add_argument("--duration", type=float, default=300.0, help="Ramp seconds (default 300)")
    parser.add_argument("--interval", type=float, default=0.5, help="Ramp command interval in seconds (default 0.5)")
    parser.add_argument("--voltage", type=float, default=35.0)
    parser.add_argument("--pre-seconds", type=float, default=2.0)
    parser.add_argument("--final-hold", type=float, default=5.0,
                        help="Hold last current before switching off (default 5 s)")
    parser.add_argument("--post-seconds", type=float, default=2.0)
    parser.add_argument("--max-lateness", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "captures")
    args = parser.parse_args()
    try:
        steps = (current_profile.load_steps(args.profile) if args.profile else
                 current_profile.ramp_steps(*(args.ramp or (0.5, 8.0)), args.duration, args.interval))
        with SPS5162X.open(args.resource) as supply:
            result = capture(supply, args.port, steps, output_dir=args.output,
                             voltage_limit_v=args.voltage, pre_seconds=args.pre_seconds,
                             final_hold_s=args.final_hold, post_seconds=args.post_seconds,
                             max_lateness_s=args.max_lateness)
        measurement = result["measurement"]
        rate = measurement.get("received_rate_hz")
        rate_text = f" at {rate:.1f} Hz" if rate is not None else ""
        print(f"{'Complete' if result['complete'] else 'Partial/failed'}: "
              f"{measurement.get('samples', 0)} MCU samples{rate_text}")
        print(f"Supply OFF confirmed: {result['output_off_confirmed']}; "
              f"MCU END received: {measurement.get('end_received', False)}")
        print(f"MPPT diagnostics: {measurement.get('mppt_records', 0)} records in measure_mppt.csv")
        if not measurement.get('mppt_records', 0):
            print("No MPPT diagnostics received; check that the new firmware was flashed.")
        for key in ("error", "uart_error", "shutdown_error", "plot_errors"):
            if key in result:
                print(f"{key}: {result[key]}")
        if any(result["data_quality"].values()):
            print(f"Data quality: {result['data_quality']}")
        print(f"Saved: {result['folder']}")
        return 130 if result.get("interrupted") else int(not result["complete"] or bool(result.get("plot_errors")))
    except Exception as error:
        print(f"Measurement failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
