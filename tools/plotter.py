#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyserial>=3.5", "matplotlib>=3.8"]
# ///
"""Scripted bench run from a terminal: drive the board and plot the result."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "gui"))

import console                                    # noqa: E402
import capture                                    # noqa: E402
from link import LinkError, SerialLink            # noqa: E402

MODES = ("auto", "chmppt", "ch5mppt", "dualmppt", "ivsweep")


def ad_hoc(mode: str, start: float, end: float, length: float,
           plot_start: float, iv: bool) -> capture.Sequence:
    return capture.Sequence(
        name=mode,
        label=f"{mode} {start:g}-{end:g} s",
        steps=((start, mode), (end, "stop")),
        length=length,
        plot_start=plot_start,
        renders=("timeseries", "iv") if iv or mode == "ivsweep" else ("timeseries",),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sequence", help="run a predefined sequence instead of --mode")
    ap.add_argument("--list", action="store_true", help="list predefined sequences")
    ap.add_argument("--mode", choices=MODES, default="auto", help="mode command to start")
    ap.add_argument("--start", type=float, default=2.0, help="seconds until the mode command")
    ap.add_argument("--end", type=float, default=5.0, help="seconds until STOP")
    ap.add_argument("--plot_start", type=float, default=0.0,
                    help="seconds at which the plot x-axis starts; capture still begins at 0")
    ap.add_argument("--length", type=float, default=8.0,
                    help="capture seconds; may be less than --end to stop after the plot")
    ap.add_argument("--port", help="serial port; auto-detected if omitted")
    ap.add_argument("--iv", action="store_true",
                    help="also write the I-V curve; implied by --mode ivsweep")
    ap.add_argument("--rload", type=float,
                    help="output load in ohms; adds stage efficiency to the I-V curve")
    args = ap.parse_args()

    if args.list:
        for seq in capture.SEQUENCES:
            steps = ", ".join(f"{w:g}s {v}" for w, v in seq.steps)
            print(f"  {seq.name:10s} {seq.length:5g}s  {steps}")
        return 0

    if args.sequence:
        seq = capture.SEQUENCES_BY_NAME.get(args.sequence)
        if seq is None:
            print(f"unknown sequence: {args.sequence}  (try --list)", file=sys.stderr)
            return 2
    else:
        if not (0 <= args.start < args.end and args.start < args.length):
            print("need 0 <= start < end and start < length", file=sys.stderr)
            return 2
        if not 0 <= args.plot_start < args.length:
            print("need 0 <= plot_start < length", file=sys.stderr)
            return 2
        seq = ad_hoc(args.mode, args.start, args.end, args.length,
                     args.plot_start, args.iv)

    link = SerialLink()
    try:
        port = link.open(args.port)
    except LinkError as exc:
        print(f"{exc}; try: uv run tools/console.py --list", file=sys.stderr)
        return 1

    recorder = capture.Recorder()

    def send(verb: str) -> None:
        frame = link.send(verb)
        print(f"  {link.clock():6.3f} s  sent {verb:10s} {frame.hex(' ')}")

    print(f"capturing {seq.length:g} s on {port} at {console.BAUD}")
    run = capture.SequenceRun(seq, send, recorder, link.clock)
    link.subscribe(recorder.feed)
    run.start()
    try:
        while not run.finished.wait(0.25):
            pass
    except KeyboardInterrupt:
        print("\ninterrupted - stopping the board and writing what was captured")
        run.cancel()
        run.join(timeout=2.0)
    finally:
        link.unsubscribe(recorder.feed)
        link.close()

    if run.error:
        print(f"sequence error: {run.error}", file=sys.stderr)

    paths = capture.capture_paths(seq.name)
    summary, written = capture.render(seq, recorder, paths, args.rload)
    print()
    for line in summary:
        print(f"  {line}")
    print("\nwrote " + ", ".join(str(p.relative_to(capture.REPO_ROOT))
                                 for p in written.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
