#!/usr/bin/env python3

from __future__ import annotations

import sys
from dataclasses import dataclass, field as dc_field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import console  # noqa: E402


VBUS_C = "#1f77b4"
FALLBACK_C = ("#17becf", "#bcbd22", "#e377c2", "#8c564b", "#7f7f7f")


@dataclass(frozen=True)
class Field:
    label: str
    unit: str
    scale: float
    colour: str
    panel: str
    channel: str | None = None
    step: bool = False
    kind: str = "number"
    digits: int = 3


FIELD_INFO: dict[str, Field] = {
    "vbus_mv": Field("vbus", "V", 1000.0, VBUS_C, "volts", None, digits=2),
}
CHANNEL_COLOURS = ("#d55e00", "#0072b2", "#009e73", "#7b4ab5", "#a66b00")
for (channel, names), colour in zip(console.CHANNEL_FIELDS.items(), CHANNEL_COLOURS):
    vin, iin, vout, iout, duty = names
    FIELD_INFO[vin] = Field("vin", "V", 1000.0, colour, "volts", channel)
    FIELD_INFO[iin] = Field("iin", "A", 1000.0, colour, "current", channel)
    FIELD_INFO[vout] = Field("vout", "V", 1000.0, colour, "volts", channel)
    FIELD_INFO[iout] = Field("iout", "A", 1000.0, colour, "current", channel)
    FIELD_INFO[duty] = Field("duty", "%", 10.0, colour, "duty", channel,
                             step=True, digits=1)


@dataclass(frozen=True)
class Derived:
    key: str
    label: str
    unit: str
    colour: str
    panel: str
    inputs: tuple[str, ...]
    expr: str
    channel: str | None = None
    digits: int = 2
    args: tuple = dc_field(default=(), repr=False)


DERIVED: tuple[Derived, ...] = tuple(
    derived
    for (channel, names), colour in zip(console.CHANNEL_FIELDS.items(), CHANNEL_COLOURS)
    for derived in (
        Derived(f"{channel}_pin_w", "pin", "W", colour, "power", names[:2],
                expr="product_milli", channel=channel),
        Derived(f"{channel}_pout_w", "pout", "W", colour, "power", names[2:4],
                expr="product_milli", channel=channel),
        Derived(f"{channel}_efficiency_pct", "efficiency", "%", colour, "efficiency",
                names[:4], expr="efficiency_percent", channel=channel),
    )
)


def efficiency_percent(vin, iin, vout, iout):
    pin = vin * iin
    if pin <= 0:
        return None
    return 100.0 * vout * iout / pin


EXPRESSIONS = {
    "product_milli": lambda a, b: a * b / 1_000_000.0,
    "efficiency_percent": efficiency_percent,
}


PANELS: tuple[tuple[str, str], ...] = (
    ("volts", "voltage (V)"),
    ("current", "current (A)"),
    ("power", "power (W)"),
    ("efficiency", "efficiency (%)"),
    ("duty", "duty (%)"),
)


IV_PAIRS: dict[str, dict[str, str]] = {
    channel: {"x": names[1], "y": names[0], "power": f"{channel}_pin_w"}
    for channel, names in console.CHANNEL_FIELDS.items()
}


def fields() -> list[dict]:
    out: list[dict] = []
    spare = iter(FALLBACK_C)
    for ident in sorted(console.STREAM):
        name, width, signed = console.STREAM[ident]
        info = FIELD_INFO.get(name)
        if info is None:


            info = Field(name, "", 1.0, next(spare, "#888888"), "other", None,
                         digits=0)
        out.append({"key": name, "id": ident, "width": width, "signed": signed,
                    "derived": False, **_as_json(info)})

    have = {f["key"] for f in out}
    for der in DERIVED:
        if not set(der.inputs) <= have:
            continue
        out.append({"key": der.key, "id": None, "derived": True,
                    "inputs": list(der.inputs), "label": der.label,
                    "unit": der.unit, "scale": 1.0, "colour": der.colour,
                    "panel": der.panel, "channel": der.channel, "step": False,
                    "kind": "number", "digits": der.digits})


    order = [name for name, _ in PANELS]
    out.sort(key=lambda f: order.index(f["panel"]) if f["panel"] in order
             else len(order))
    return out


def _as_json(info: Field) -> dict:
    return {"label": info.label, "unit": info.unit, "scale": info.scale,
            "colour": info.colour, "panel": info.panel, "channel": info.channel,
            "step": info.step, "kind": info.kind, "digits": info.digits}


def panels(all_fields: list[dict]) -> list[dict]:
    order = [name for name, _ in PANELS]
    titles = dict(PANELS)
    seen: list[str] = []
    for f in all_fields:
        if f["panel"] not in seen:
            seen.append(f["panel"])
    seen.sort(key=lambda p: order.index(p) if p in order else len(order))
    return [{"id": p, "label": titles.get(p, p),
             "fields": [f["key"] for f in all_fields if f["panel"] == p]}
            for p in seen]


def active_derived() -> tuple[Derived, ...]:
    have = {console.STREAM[i][0] for i in console.STREAM}
    return tuple(d for d in DERIVED if set(d.inputs) <= have)


def build(sequences: list[dict]) -> dict:
    all_fields = fields()
    channels = []
    for f in all_fields:
        if f["channel"] and f["channel"] not in channels:
            channels.append(f["channel"])
    return {
        "fields": all_fields,
        "panels": panels(all_fields),
        "channels": channels or ["a"],
        "iv": {ch: pair for ch, pair in IV_PAIRS.items() if ch in channels},
        "commands": sorted(console.OPCODES),
        "sequences": sequences,
        "stream_period_ms": console.STREAM_PERIOD_MS,
        "baud": console.BAUD,
        "max_payload": console.MAX_PAYLOAD,
    }
