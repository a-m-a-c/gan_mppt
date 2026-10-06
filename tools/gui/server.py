#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pyserial>=3.5",
#   "fastapi>=0.115",
#   "uvicorn>=0.30",
#   "websockets>=12",   # uvicorn has no WebSocket support without one
#   "matplotlib>=3.8",
# ]
# ///
"""Browser front end for the board: live plots, a command line, scripted runs."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import sys
import threading
import webbrowser
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import capture  # noqa: E402
import console  # noqa: E402
import link as link_mod  # noqa: E402
import schema  # noqa: E402

STATIC_DIR = HERE / "static"


TIMESERIES_DECIMATE = 4


PUMP_HZ = 25
MAX_BATCH = 4000


class LiveState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.derived = schema.active_derived()
        self.assembler = console.StreamSet()
        self.latest: dict[str, float | None] = {}
        self.ts: deque[tuple[float, dict]] = deque(maxlen=MAX_BATCH)
        self.iv: dict[str, deque] = {ch: deque(maxlen=MAX_BATCH)
                                     for ch in schema.IV_PAIRS}
        self.last_point: dict[str, tuple] = {}
        self.sets = 0
        self.dropped = 0
        self.now = 0.0

    def feed(self, name: str, value: int | None, t: float) -> None:
        with self.lock:
            snapshot = self.assembler.feed(name, value)
            if snapshot is None:
                if name == console.STREAM_LAST:
                    self.dropped += 1
                return
            self.latest = snapshot
            self.now = t
            self.sets += 1
            for der in self.derived:
                values = [self.latest.get(k) for k in der.inputs]
                self.latest[der.key] = None
                if None not in values:
                    self.latest[der.key] = schema.EXPRESSIONS[der.expr](*values)

            if self.sets % TIMESERIES_DECIMATE == 0:
                self.ts.append((t, dict(self.latest)))


            for ch, pair in schema.IV_PAIRS.items():
                x, y = self.latest.get(pair["x"]), self.latest.get(pair["y"])
                if x is None or y is None:
                    continue
                if self.last_point.get(ch) == (x, y):
                    continue
                self.last_point[ch] = (x, y)
                self.iv[ch].append((t, x, y, self.latest.get(pair["power"], 0.0)))

    def drain(self) -> dict | None:
        with self.lock:
            if not self.ts and not any(self.iv.values()):
                return None
            payload = {
                "type": "data",
                "now": self.now,
                "ts": [t for t, _ in self.ts],
                "series": {key: [row.get(key) for _, row in self.ts]
                           for key in _series_keys(self.ts)},
                "iv": {ch: list(points) for ch, points in self.iv.items() if points},
                "latest": dict(self.latest),
            }
            self.ts.clear()
            for points in self.iv.values():
                points.clear()
            return payload

    def reset(self) -> None:
        with self.lock:
            self.assembler = console.StreamSet()
            self.latest.clear()
            self.ts.clear()
            for points in self.iv.values():
                points.clear()
            self.last_point.clear()
            self.sets = 0
            self.dropped = 0
            self.now = 0.0

    def counters(self) -> dict:
        with self.lock:
            return {"sets": self.sets, "dropped": self.dropped}


def _series_keys(rows) -> list[str]:
    keys: list[str] = []
    for _, row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    return keys


class Hub:
    def __init__(self) -> None:
        self.link = link_mod.SerialLink()
        self.live = LiveState()
        self.logs: deque[dict] = deque(maxlen=400)
        self.sockets: set[WebSocket] = set()
        self.run: capture.SequenceRun | None = None
        self.recorder: capture.Recorder | None = None
        self.run_state: dict = {"state": "idle"}
        self.link.subscribe(self.live.feed)

    def set_link(self, new_link) -> None:
        self.link.unsubscribe(self.live.feed)
        self.link.close()
        self.link = new_link
        self.link.subscribe(self.live.feed)


    def log(self, text: str, level: str = "info") -> None:
        self.logs.append({"t": self.link.clock(), "level": level, "text": text})

    def drain_logs(self) -> list[dict]:
        out, self.logs = list(self.logs), deque(maxlen=self.logs.maxlen)
        return out


    def connect(self, port: str | None) -> str:
        chosen = self.link.open(port)
        self.live.reset()
        self.log(f"connected to {chosen} at {console.BAUD} baud", "ok")
        return chosen

    def disconnect(self) -> None:
        self.cancel_sequence()
        if self.link.connected and self.link.kind == "serial":
            try:
                self.link.send("stop")
                self.log("sent stop before disconnecting", "ok")
            except link_mod.LinkError as exc:
                self.log(f"could not stop before disconnect: {exc}", "warn")
        self.link.close()
        self.log("disconnected", "warn")

    def state(self) -> dict:
        return {"type": "status", "connected": self.link.connected,
                "kind": self.link.kind,
                "port": self.link.port, "error": self.link.error,
                "stats": self.link.stats(), **self.live.counters(),
                "sequence": self.run_state}


    def send(self, verb: str) -> None:
        frame = self.link.send(verb)
        self.log(f"sent {verb}  {frame.hex(' ')}", "tx")

    def send_raw(self, op: int, payload: bytes) -> None:
        frame = self.link.send_raw(op, payload)
        self.log(f"sent raw  {frame.hex(' ')}", "tx")


    def start_sequence(self, name: str) -> None:
        if self.run is not None and not self.run.finished.is_set():
            raise link_mod.LinkError(f"{self.run_state.get('name')} is still running")
        if not self.link.connected:
            raise link_mod.LinkError("not connected")
        seq = capture.SEQUENCES_BY_NAME.get(name)
        if seq is None:
            raise link_mod.LinkError(f"unknown sequence: {name}")

        recorder = capture.Recorder()
        self.recorder = recorder
        self.run_state = {"state": "running", "name": seq.name, "label": seq.label,
                          "length": seq.length, "started": self.link.clock(),
                          "summary": [], "files": {}}
        self.log(f"sequence {seq.label} started ({seq.length:g} s)", "ok")
        self.run = capture.SequenceRun(seq, self.send, recorder, self.link.clock,
                                       on_event=lambda kind, arg:
                                       self._on_sequence(seq, recorder, kind, arg))
        self.link.subscribe(recorder.feed)
        self.run.start()

    def cancel_sequence(self) -> None:
        if self.run is not None and not self.run.finished.is_set():
            self.run.cancel()
            self.log("sequence cancelled", "warn")

    def _on_sequence(self, seq, recorder, kind: str, arg) -> None:
        if kind == "step":
            return


        self.link.unsubscribe(recorder.feed)
        self.run_state["state"] = "rendering"
        try:
            paths = capture.capture_paths(seq.name)
            summary, files = capture.render(seq, recorder, paths)
            self.run_state.update(
                state="done", summary=summary,
                files={key: path.name for key, path in files.items()})
            for line in summary:
                self.log(line)
            self.log(f"wrote {', '.join(p.name for p in files.values())}", "ok")
        except Exception as exc:                 # noqa: BLE001
            self.run_state.update(state="error", summary=[str(exc)])
            self.log(f"render failed: {exc}", "error")


hub = Hub()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    pump = asyncio.create_task(pump_loop())
    try:
        yield
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        hub.link.close()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def prevent_gui_caching(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


async def broadcast(message: dict) -> None:
    for socket in list(hub.sockets):
        try:
            await socket.send_json(message)
        except (WebSocketDisconnect, RuntimeError):
            hub.sockets.discard(socket)


async def pump_loop() -> None:
    tick = 0
    while True:
        await asyncio.sleep(1.0 / PUMP_HZ)
        tick += 1
        if not hub.sockets:
            continue
        data = hub.live.drain()
        if data is not None:
            await broadcast(data)
        logs = hub.drain_logs()
        if logs:
            await broadcast({"type": "log", "lines": logs})


        if tick % (PUMP_HZ // 4) == 0:
            await broadcast(hub.state())


@app.get("/")
async def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("style.css", "plots.js", "app.js"):
        version = hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:16]
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={version}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/ports")
async def api_ports() -> dict:
    return {"ports": link_mod.available_ports(), "suggested": console.pick_port()}


@app.get("/api/state")
async def api_state() -> dict:
    return hub.state()


@app.post("/api/connect")
async def api_connect(body: dict | None = None) -> JSONResponse:
    port = (body or {}).get("port") or None
    try:
        chosen = hub.connect(port)
    except link_mod.LinkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast({"type": "reset"})
    return JSONResponse({"port": chosen})


@app.post("/api/disconnect")
async def api_disconnect() -> dict:
    hub.disconnect()
    return {"ok": True}


@app.get("/api/captures")
async def api_captures() -> dict:
    return {"captures": capture.list_captures()}


@app.get("/captures/{name}")
async def api_capture_file(name: str) -> FileResponse:
    path = (capture.CAPTURE_DIR / name).resolve()
    if capture.CAPTURE_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="no such capture")
    return FileResponse(path)


@app.websocket("/ws")
async def websocket(socket: WebSocket) -> None:
    await socket.accept()
    hub.sockets.add(socket)
    sequences = [s.as_json() for s in capture.SEQUENCES]
    await socket.send_json({"type": "schema", **schema.build(sequences)})
    await socket.send_json(hub.state())
    try:
        while True:
            message = await socket.receive_json()
            await handle(socket, message)
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        hub.sockets.discard(socket)


async def handle(socket: WebSocket, message: dict) -> None:
    kind = message.get("type")
    try:
        if kind == "command":
            hub.send(str(message.get("verb", "")))
        elif kind == "raw":
            op = int(str(message.get("op", "")), 16)
            payload = bytes(int(b, 16) for b in str(message.get("payload", "")).split())
            hub.send_raw(op, payload)
        elif kind == "sequence":
            hub.start_sequence(str(message.get("name", "")))
        elif kind == "sequence_cancel":
            hub.cancel_sequence()
        elif kind == "clear_log":
            hub.logs.clear()
        else:
            hub.log(f"unknown message: {kind}", "error")
    except (link_mod.LinkError, ValueError) as exc:
        hub.log(str(exc), "error")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address; anything but localhost exposes the board")
    ap.add_argument("--port", type=int, default=8000, help="HTTP port")
    ap.add_argument("--serial", help="connect to this serial port at startup")
    ap.add_argument("--replay", type=Path,
                    help="drive the GUI from a capture CSV instead of the port")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="replay speed multiplier; 0 is as fast as possible")
    ap.add_argument("--loop", action="store_true", help="restart the replay at the end")
    ap.add_argument("--open", action="store_true", help="open a browser tab")
    args = ap.parse_args()

    import uvicorn

    if args.replay:
        hub.set_link(link_mod.ReplayLink(args.replay, args.speed, args.loop))
        print(f"replaying {args.replay} at x{args.speed:g}"
              f" - press Connect to start it")
    if args.serial and not args.replay:
        try:
            hub.connect(args.serial)
        except link_mod.LinkError as exc:
            print(f"{exc}", file=sys.stderr)

    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}/"
    if args.host not in ("127.0.0.1", "localhost"):
        print(f"WARNING bound to {args.host} - anyone on this network can drive the board")
    print(f"\n  GaN MPPT GUI  ->  {url}\n")
    if args.open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
