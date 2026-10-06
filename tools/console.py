#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyserial>=3.5"]
# ///
"""Serial console for the GaN MPPT board."""

from __future__ import annotations

import argparse
import sys
import threading
import time

import serial
from serial.tools import list_ports

BAUD = 921600


# Opcodes: Src/app/command.c.

OPCODES = {
    "reset": 0x01,
    "clearfault": 0x02,
    "stop": 0x03,
    "chmppt": 0x06,
    "ivsweep": 0x07,
    "bypass_on": 0x08,
    "bypass_off": 0x09,
    "dualmppt": 0x0A,
    "ch5mppt": 0x0B,
    "auto": 0x0C,
}


MAX_PAYLOAD = 8


CRC_STUB = 0xCC


# IDs and widths: Src/app/stream.c.


STREAM_PERIOD_MS = 5

# Keep channel A's existing names for saved I-V tooling.
CHANNEL_FIELDS = {
    "a": ("vin_mv", "iin_ma", "vout_mv", "iout_ma", "duty"),
    "b": ("b_vin_mv", "b_iin_ma", "b_vout_mv", "b_iout_ma", "b_duty"),
    "c": ("c_vin_mv", "c_iin_ma", "c_vout_mv", "c_iout_ma", "c_duty"),
    "d": ("d_vin_mv", "d_iin_ma", "d_vout_mv", "d_iout_ma", "d_duty"),
    "e": ("e_vin_mv", "e_iin_ma", "e_vout_mv", "e_iout_ma", "e_duty"),
}
CHANNEL_IDS = {
    "a": (0x63, 0x64, 0x66, 0x67, 0x61),
    "b": (0x70, 0x71, 0x72, 0x73, 0x74),
    "c": (0x80, 0x81, 0x82, 0x83, 0x84),
    "d": (0x90, 0x91, 0x92, 0x93, 0x94),
    "e": (0xA0, 0xA1, 0xA2, 0xA3, 0xA4),
}
STREAM = {0x60: ("vbus_mv", 4, False)}
for channel, names in CHANNEL_FIELDS.items():
    for index, (ident, name) in enumerate(zip(CHANNEL_IDS[channel], names)):
        width = 4
        if index == 4:
            width = 2
        STREAM[ident] = (name, width, index in (1, 3))

STREAM_NAMES = tuple(spec[0] for spec in STREAM.values())
STREAM_FIRST = STREAM_NAMES[0]
STREAM_LAST = STREAM_NAMES[-1]


class StreamSet:
    """Publish complete ordered sets without mixing snapshots."""

    def __init__(self) -> None:
        self.pending = {}

    def feed(self, name: str, value: int | None) -> dict | None:
        if name == STREAM_FIRST:
            self.pending = {}
        index = len(self.pending)
        if index >= len(STREAM_NAMES) or name != STREAM_NAMES[index]:
            self.pending = {}
            return None
        self.pending[name] = value
        if name == STREAM_LAST:
            result = self.pending
            self.pending = {}
            return result
        return None


def encode(op: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload {len(payload)} exceeds TRANSPORT_MAX_PAYLOAD ({MAX_PAYLOAD})")
    return bytes([op, len(payload)]) + payload + bytes([CRC_STUB])


class StreamParser:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.resyncs = 0
        self.frames = 0

    def feed(self, data: bytes) -> list[tuple[str, int | None]]:
        self.buf += data
        out: list[tuple[str, int | None]] = []
        while True:
            if len(self.buf) < 2:
                return out
            ident, size = self.buf[0], self.buf[1]
            known = STREAM.get(ident)
            if known is None or known[1] != size:
                del self.buf[0]
                self.resyncs += 1
                continue
            if len(self.buf) < 2 + size:
                return out
            name, _, signed = known
            value = int.from_bytes(self.buf[2 : 2 + size], "little", signed=signed)
            if size == 4 and value in (0xFFFFFFFF, -0x80000000):
                value = None
            del self.buf[: 2 + size]
            self.frames += 1
            out.append((name, value))


class Board:
    def __init__(self, port: str) -> None:
        self.ser = serial.Serial(port, BAUD, timeout=0.05)
        self.parser = StreamParser()
        self.latest: dict[str, int | None] = {}
        self.lock = threading.Lock()
        self.running = True
        self.watch = False
        self.watch_interval = 0.5
        self._last_print = 0.0
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()

    def _read_loop(self) -> None:
        while self.running:
            try:
                data = self.ser.read(max(1, self.ser.in_waiting))
            except serial.SerialException as exc:
                print(f"\n[port closed: {exc}]")
                self.running = False
                return
            if not data:
                continue
            for name, value in self.parser.feed(data):
                with self.lock:
                    self.latest[name] = value
            if self.watch and (time.monotonic() - self._last_print) >= self.watch_interval:
                self._last_print = time.monotonic()
                print("\r" + self.summary())
                print("> ", end="", flush=True)

    def summary(self) -> str:
        with self.lock:
            latest = dict(self.latest)
        if "vbus_mv" not in latest:
            return "no telemetry yet"
        def display(name: str, divisor: float) -> str:
            value = latest.get(name)
            if value is None:
                return "--"
            return f"{value / divisor:.3f}"

        parts = [f"vbus {display('vbus_mv', 1000)} V"]
        for channel, names in CHANNEL_FIELDS.items():
            vin, iin, vout, iout, duty = names
            parts.append(
                f"{channel.upper()}: vin {display(vin, 1000)} V "
                f"iin {display(iin, 1000)} A vout {display(vout, 1000)} V "
                f"iout {display(iout, 1000)} A duty {display(duty, 10)} %")
        parts.append(f"[{self.parser.frames} frames, {self.parser.resyncs} resynced]")
        return " | ".join(parts)

    def send(self, op: int, payload: bytes = b"") -> None:
        frame = encode(op, payload)
        self.ser.write(frame)
        print(f"  sent {frame.hex(' ')}")

    def close(self) -> None:
        self.running = False
        self.reader.join(timeout=1.0)
        self.ser.close()


def pick_port() -> str | None:
    ports = list(list_ports.comports())
    if not ports:
        return None
    for p in ports:
        blurb = f"{p.description} {p.manufacturer or ''}".lower()
        if any(k in blurb for k in ("usb", "cp210", "ch340", "ftdi", "st-link", "stlink")):
            return p.device
    return ports[0].device


HELP = """commands
  reset | clearfault | stop                        send a system command
  auto | chmppt | ch5mppt | dualmppt | ivsweep        run a mode
  bypass_on | bypass_off                          control the diode bypass
  raw <op-hex> [byte-hex ...]                      send an arbitrary frame
  watch                                            toggle telemetry printing
  rate <interval_ms>                               set the watch print interval
  status                                           print the latest values once
  ports                                            list serial ports
  help | quit
"""


def repl(board: Board) -> None:
    print(HELP)
    while board.running:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        parts = line.split()
        verb, args = parts[0].lower(), parts[1:]

        if verb in ("quit", "exit", "q"):
            return
        if verb == "help":
            print(HELP)
        elif verb == "watch":
            board.watch = not board.watch
            print(f"  telemetry {'on' if board.watch else 'off'}"
                  f" at {board.watch_interval * 1000:.0f} ms")
        elif verb == "rate":
            try:
                ms = int(args[0])
            except (IndexError, ValueError):
                print(f"  usage: rate <interval_ms>   (now {board.watch_interval * 1000:.0f} ms)")
                continue
            if ms < STREAM_PERIOD_MS:
                print(f"  floor is {STREAM_PERIOD_MS} ms - the board only sends that often")
                ms = STREAM_PERIOD_MS
            board.watch_interval = ms / 1000.0
            print(f"  watch interval {ms} ms")
        elif verb == "status":
            print("  " + board.summary())
        elif verb == "ports":
            for p in list_ports.comports():
                print(f"  {p.device:10s} {p.description}")
        elif verb == "raw":
            try:
                op = int(args[0], 16)
                payload = bytes(int(a, 16) for a in args[1:])
                board.send(op, payload)
            except (IndexError, ValueError) as exc:
                print(f"  usage: raw <op-hex> [byte-hex ...]  ({exc})")
        elif verb in OPCODES:
            board.send(OPCODES[verb])
        else:
            print(f"  unknown: {verb}  (try help)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port; auto-detected if omitted")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    ap.add_argument("--watch", action="store_true", help="start with telemetry printing on")
    args = ap.parse_args()

    if args.list:
        found = list(list_ports.comports())
        if not found:
            print("no serial ports found")
        for p in found:
            print(f"{p.device:10s} {p.description}")
        return 0

    port = args.port or pick_port()
    if port is None:
        print("no serial port found; try --list", file=sys.stderr)
        return 1

    try:
        board = Board(port)
    except serial.SerialException as exc:
        print(f"could not open {port}: {exc}", file=sys.stderr)
        return 1

    print(f"connected to {port} at {BAUD}")
    board.watch = args.watch
    try:
        repl(board)
    finally:
        board.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
