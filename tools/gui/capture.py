#!/usr/bin/env python3

from __future__ import annotations

import csv
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import console   # noqa: E402
import iv_curve  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPTURE_DIR = REPO_ROOT / "captures"


CSV_HEADER = ["t_s", "t_host_s", *console.STREAM_NAMES, "event"]
T, T_HOST = 0, 1
VBUS_MV = CSV_HEADER.index("vbus_mv")
VIN_MV = CSV_HEADER.index("vin_mv")
IIN_MA = CSV_HEADER.index("iin_ma")
DUTY = CSV_HEADER.index("duty")


class Recorder:
    def __init__(self) -> None:
        self.rows: list[tuple] = []
        self.events: list[tuple[float, str]] = []
        self.assembler = console.StreamSet()
        self.start_time = 0.0
        self.ticks = -1
        self.partial = 0
        self.complete = True
        self.recording = True
        self.lock = threading.Lock()

    def feed(self, name: str, value: int | None, t_host: float) -> None:
        with self.lock:
            if not self.recording:
                return
            if name == console.STREAM_FIRST:
                if self.ticks >= 0 and not self.complete:
                    self.partial += 1
                self.ticks += 1
                self.complete = False
            snapshot = self.assembler.feed(name, value)
            if snapshot is not None:
                self.complete = True
                self.rows.append((t_host - self.start_time, t_host,
                                  *(snapshot[name] for name in console.STREAM_NAMES)))

    def mark(self, t_host: float, label: str) -> None:
        with self.lock:
            self.events.append((t_host - self.start_time, label))

    def stop(self) -> None:
        with self.lock:
            self.recording = False
            if self.ticks >= 0 and not self.complete:
                self.partial += 1
                self.complete = True

    def write_csv(self, path: Path) -> None:
        with self.lock:
            rows, events = list(self.rows), sorted(self.events)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(CSV_HEADER)
            pending = list(events)
            for row in rows:
                label = ""
                while pending and pending[0][0] <= row[T]:
                    label = pending.pop(0)[1]
                w.writerow([f"{row[T]:.4f}", f"{row[T_HOST]:.4f}", *row[VBUS_MV:], label])


@dataclass(frozen=True)
class Sequence:
    name: str
    label: str
    steps: tuple[tuple[float, str], ...]
    length: float
    renders: tuple[str, ...] = ("timeseries",)
    plot_start: float = 0.0
    description: str = ""

    def validate(self) -> None:
        for when, verb in self.steps:
            if verb not in console.OPCODES:
                raise ValueError(f"{self.name}: {verb} is not in console.OPCODES")
            if when < 0:
                raise ValueError(f"{self.name}: step at {when} s is before the start")
        if self.length <= 0 or not 0 <= self.plot_start < self.length:
            raise ValueError(f"{self.name}: need 0 <= plot_start < length")

    def as_json(self) -> dict:
        return {"name": self.name, "label": self.label, "length": self.length,
                "steps": [[w, v] for w, v in self.steps],
                "renders": list(self.renders), "description": self.description}


SEQUENCES: tuple[Sequence, ...] = (
    Sequence(
        name="auto",
        label="Auto mode",
        steps=((2.0, "auto"), (5.0, "stop")),
        length=8.0,
        description="Auto stub for 3 s; no channel control implemented yet.",
    ),
    Sequence(
        name="ivsweep",
        label="I-V sweep",
        steps=((2.0, "ivsweep"), (30.0, "stop")),
        length=32.0,
        renders=("timeseries", "iv"),
        description="Full duty sweep; also renders the I-V curve, one line per pass.",
    ),
    Sequence(
        name="chmppt",
        label="Single-channel MPPT (1)",
        steps=((2.0, "chmppt"), (30.0, "stop")),
        length=32.0,
        renders=("timeseries", "iv"),
        description="Channel-A MPPT for 28 s.",
    ),
    Sequence(
        name="ch5mppt",
        label="Single-channel MPPT (5)",
        steps=((2.0, "ch5mppt"), (30.0, "stop")),
        length=32.0,
        description="Channel-5 MPPT for 28 s.",
    ),
    Sequence(
        name="dualmppt",
        label="Dual-channel MPPT (1 + 5)",
        steps=((2.0, "dualmppt"), (30.0, "stop")),
        length=32.0,
        description="Independent MPPT on channels 1 and 5 for 28 s.",
    ),
)

SEQUENCES_BY_NAME = {s.name: s for s in SEQUENCES}
for _seq in SEQUENCES:
    _seq.validate()


def capture_paths(name: str, when: datetime | None = None) -> dict[str, Path]:
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    stem = CAPTURE_DIR / f"{stamp}_{name}"
    return {"csv": Path(f"{stem}.csv"),
            "timeseries": Path(f"{stem}.svg"),
            "iv": Path(f"{stem}_iv.svg")}


class SequenceRun:
    def __init__(self, seq: Sequence, send, recorder: Recorder, clock,
                 on_event=None) -> None:
        self.seq = seq
        self.send = send
        self.recorder = recorder
        self.clock = clock
        self.on_event = on_event or (lambda *a: None)
        self.t0 = clock()
        self.recorder.start_time = self.t0
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.error: str | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "SequenceRun":
        self.thread.start()
        return self

    @property
    def elapsed(self) -> float:
        return self.clock() - self.t0

    def _wait_until(self, offset: float) -> bool:
        remaining = offset - self.elapsed
        if remaining > 0:
            return not self.cancelled.wait(remaining)
        return not self.cancelled.is_set()

    def _run(self) -> None:
        stopped = False
        try:
            # Finish the sequence even after recording ends, so STOP is still sent.

            timeline = sorted([*self.seq.steps, (self.seq.length, None)],
                              key=lambda step: step[0])
            for when, verb in timeline:
                if not self._wait_until(when):
                    break
                if verb is None:
                    self.recorder.stop()
                    continue
                self.send(verb)
                self.recorder.mark(self.clock(), verb)
                stopped = stopped or verb == "stop"
                self.on_event("step", verb)
        except Exception as exc:                 # noqa: BLE001
            self.error = str(exc)
        finally:
            self.recorder.stop()


            if not stopped:
                try:
                    self.send("stop")
                except Exception:                # noqa: BLE001
                    pass
            self.finished.set()
            self.on_event("finished", None)

    def cancel(self) -> None:
        self.cancelled.set()

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)


def render(seq: Sequence, recorder: Recorder, paths: dict[str, Path],
           rload: float | None = None) -> tuple[list[str], dict[str, Path]]:

    recorder.write_csv(paths["csv"])
    written = {"csv": paths["csv"]}
    lines = summarise(recorder)

    if "timeseries" in seq.renders:
        render_timeseries(recorder, paths["timeseries"], seq)
        written["timeseries"] = paths["timeseries"]
    if "iv" in seq.renders:
        result = iv_curve.render(paths["csv"], paths["iv"], rload, True,
                                 f"channel A input V-I - {paths['csv'].name}")
        if result is not None:
            written["iv"] = paths["iv"]
            duty, volts, amps, pin = result
            lines.append(f"MPP {volts:.3f} V  {amps:.3f} A  {pin:.2f} W  at duty {duty}")
    return lines, written


def summarise(recorder: Recorder) -> list[str]:
    rows = recorder.rows
    lines = [f"{len(rows)} sets, {recorder.partial} partial"]
    if not rows:
        lines.append("no telemetry received - is the board powered and streaming?")
        return lines

    def span(index: int, divisor: float, unit: str, name: str, fmt: str = "6.2f") -> None:
        vals = [r[index] for r in rows if r[index] is not None]
        if not vals:
            return
        lo, hi, last = min(vals) / divisor, max(vals) / divisor, vals[-1] / divisor
        lines.append(f"{name:4s} min {lo:{fmt}} {unit}  max {hi:{fmt}} {unit}"
                     f"  final {last:{fmt}} {unit}")

    span(VIN_MV, 1000.0, "V", "vin", "6.3f")
    span(IIN_MA, 1000.0, "A", "iin", "6.3f")
    span(VBUS_MV, 1000.0, "V", "vbus")
    span(DUTY, 1.0, " ", "duty", "6.0f")


    return lines


def render_timeseries(recorder: Recorder, path: Path, seq: Sequence) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = recorder.rows
    if not rows:
        return
    plot_start, length = seq.plot_start, seq.length
    t = [r[T] for r in rows]

    def scaled(index: int, divisor: float) -> list:
        return [(r[index] / divisor) if r[index] is not None else None for r in rows]

    fig, axes = plt.subplots(3, 1, sharex=True, figsize=(11, 9))
    window = f"{plot_start:g}-{length:g} s" if plot_start else f"{length:g} s"
    fig.suptitle(f"{seq.label} - {window}")
    axes[0].plot(t, scaled(VBUS_MV, 1000.0), lw=1.2, label="vbus")
    for channel, names in console.CHANNEL_FIELDS.items():
        for name in names[:4]:
            axis = axes[0]
            if name.endswith("_ma"):
                axis = axes[1]
            axis.plot(t, scaled(CSV_HEADER.index(name), 1000.0), lw=1,
                      label=f"{channel.upper()} {name.removeprefix(channel + '_')}")
        axes[2].plot(t, scaled(CSV_HEADER.index(names[4]), 10.0), lw=1,
                     drawstyle="steps-post", label=channel.upper())
    axes[0].set_ylabel("voltage (V)")
    axes[1].set_ylabel("current (A)")
    axes[2].set_ylabel("duty (%)")
    axes[2].set_xlabel("time (s)")
    for axis in axes:
        axis.legend(loc="upper left", fontsize=7, ncol=3)

    colours = {"stop": "#333333"}
    for when, verb in recorder.events:
        if not (plot_start <= when <= length):
            continue
        colour = colours.get(verb, "#2ca02c")
        for ax in axes:
            ax.axvline(when, color=colour, ls="--", lw=1.0)
        axes[0].annotate(verb, xy=(when, 1.01), xycoords=("data", "axes fraction"),
                         ha="center", va="bottom", fontsize=8, color=colour)

    for ax in axes:
        ax.grid(alpha=0.3)
        ax.set_xlim(plot_start, length)

    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def list_captures(limit: int = 40) -> list[dict]:
    if not CAPTURE_DIR.is_dir():
        return []
    out = []
    for csv_path in sorted(CAPTURE_DIR.glob("*.csv"), reverse=True)[:limit]:
        if csv_path.stem.endswith("_iv"):
            continue
        stem = csv_path.with_suffix("")
        files = {"csv": csv_path.name}
        for key, candidate in (("timeseries", Path(f"{stem}.svg")),
                               ("iv", Path(f"{stem}_iv.svg"))):
            if candidate.exists():
                files[key] = candidate.name
        out.append({"name": csv_path.stem, "files": files,
                    "size": csv_path.stat().st_size,
                    "mtime": csv_path.stat().st_mtime})
    return out
