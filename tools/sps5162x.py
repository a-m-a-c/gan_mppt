#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyvisa>=1.15,<2"]
# ///
"""Siglent SPS5162X USB/LAN SCPI interface for the 35 V / 8 A MPPT bench.

SCPI reference: https://www.siglenteu.com/download/10688/ (pages 80-91).
Opening/closing a connection does not change output state. Profiles explicitly
own output enable/disable; set_current_limit changes a limit, not measured current.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import threading

import pyvisa


class SupplyError(RuntimeError):
    pass


def setpoint(value: float, maximum: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= maximum:
        raise ValueError(f"{name} must be finite and between 0 and {maximum}")
    rounded = round(number, 3) # Supply setting resolution: 1 mV / 1 mA.
    if abs(number - rounded) > 1e-9:
        raise ValueError(f"{name} must use 0.001-unit increments")
    return rounded


@dataclass(frozen=True)
class SupplyReading:
    timestamp_utc: str
    voltage_limit_v: float
    current_limit_a: float
    output_enabled: bool
    voltage_v: float
    current_a: float
    power_w: float
    mode: str


class SPS5162X:
    def __init__(self, manager, instrument, identity: str, max_current_a: float,
                 max_voltage_v: float):
        self._manager, self._instrument = manager, instrument
        self.identity = identity
        self.resource = instrument.resource_name
        self.max_current_a, self.max_voltage_v = max_current_a, max_voltage_v
        self._lock = threading.RLock()
        self._closed = False

    @staticmethod
    def resources(visa_library: str | None = None) -> tuple[str, ...]:
        manager = pyvisa.ResourceManager(visa_library or "")
        try:
            return tuple(r for r in manager.list_resources("USB?*::INSTR")
                         if "::0XF4EC::0X1452::" in r.upper())
        finally:
            manager.close()

    @classmethod
    def open(cls, resource: str | None = None, *, max_current_a: float = 8.0,
             max_voltage_v: float = 35.0, timeout_ms: int = 3000,
             visa_library: str | None = None) -> SPS5162X:
        # SPS5162X nameplate: 160 V, 15 A, 720 W. Bench defaults are narrower.
        current = setpoint(max_current_a, 15.0, "maximum current")
        voltage = setpoint(max_voltage_v, 160.0, "maximum voltage")
        if current == 0 or voltage == 0 or type(timeout_ms) is not int or timeout_ms <= 0:
            raise ValueError("Limits and timeout must be positive")
        if resource is None:
            candidates = cls.resources(visa_library)
            if len(candidates) != 1:
                raise SupplyError(f"Expected one SPS USB resource; found {candidates}. Specify --resource.")
            resource = candidates[0]
        manager = pyvisa.ResourceManager(visa_library or "")
        instrument = None
        try:
            instrument = manager.open_resource(resource, timeout=timeout_ms,
                                               read_termination="\n", write_termination="\n")
            identity = instrument.query("*IDN?").strip()
            parts = identity.split(",")
            if len(parts) < 4 or "siglent" not in parts[0].lower() or parts[1].strip() != "SPS5162X":
                raise SupplyError(f"Expected Siglent SPS5162X, got {identity!r}")
            return cls(manager, instrument, identity, current, voltage)
        except BaseException:
            if instrument is not None:
                instrument.close()
            manager.close()
            raise

    def __enter__(self) -> SPS5162X:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _query(self, command: str) -> str:
        if self._closed:
            raise SupplyError("Connection is closed")
        try:
            return self._instrument.query(command).strip()
        except pyvisa.errors.VisaIOError as error:
            raise SupplyError(f"VISA query failed: {command}: {error}") from error

    def _number(self, command: str) -> float:
        response = self._query(command)
        try:
            value = float(response)
        except ValueError as error:
            raise SupplyError(f"Invalid numeric response to {command}: {response!r}") from error
        if not math.isfinite(value):
            raise SupplyError(f"Nonfinite response to {command}: {response!r}")
        return value

    def _output(self) -> bool:
        reply = self._query(":SOURce:OUTPut?").upper()
        if reply not in ("0", "1", "ON", "OFF"):
            raise SupplyError(f"Invalid output state: {reply!r}")
        return reply in ("1", "ON")

    def _write_checked(self, command: str) -> None:
        if self._closed:
            raise SupplyError("Connection is closed")
        try:
            self._instrument.write(command)
        except pyvisa.errors.VisaIOError as error:
            raise SupplyError(f"VISA write failed: {command}: {error}") from error
        if self._query("*OPC?") != "1":
            raise SupplyError(f"Instrument did not complete {command}")
        # IEEE 488.2 ESR: query/device/execution/command error bits 2..5.
        status = int(self._number("*ESR?"))
        if status & 0x3C:
            raise SupplyError(f"SCPI error status 0x{status:02x} after {command}")

    def set_current_limit(self, amps: float) -> float:
        target = setpoint(amps, self.max_current_a, "current limit (A)")
        with self._lock:
            self._write_checked(f":SOURce:CURRent:SET CH1,{target:.3f}")
            actual = self._number(":SOURce:CURRent:SET? CH1")
            if abs(actual - target) > 0.0005:
                raise SupplyError(f"Current limit readback {actual} A != requested {target} A")
            return actual

    def set_voltage_limit(self, volts: float) -> float:
        target = setpoint(volts, self.max_voltage_v, "voltage limit (V)")
        with self._lock:
            self._write_checked(f":SOURce:VOLTage:SET CH1,{target:.3f}")
            actual = self._number(":SOURce:VOLTage:SET? CH1")
            if abs(actual - target) > 0.0005:
                raise SupplyError(f"Voltage limit readback {actual} V != requested {target} V")
            return actual

    def _check_enable_configuration(self) -> None:
        setpoint(self._number(":SOURce:VOLTage:SET? CH1"), self.max_voltage_v, "programmed voltage")
        setpoint(self._number(":SOURce:CURRent:SET? CH1"), self.max_current_a, "programmed current")
        if self._query(":SOURce:LIST:STATe? CH1") != "0":
            raise SupplyError("Disable instrument LIST mode before enabling this profile")
        for quantity in ("VOLTage", "CURRent"):
            if self._query(f":SOURce:CTRL:{quantity}:MODE? CH1").upper() != "LOCAL":
                raise SupplyError(f"{quantity} is under external analog control")
        for edge in ("ON", "OFF"):
            if self._number(f":SOURce:OUTPut:{edge}:DELay? CH1") != 0:
                raise SupplyError("Set instrument output ON/OFF delays to zero before enabling")

    def set_output(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("enabled must be True or False")
        with self._lock:
            if enabled:
                self._check_enable_configuration()
            self._write_checked(f":SOURce:OUTPut {int(enabled)}")
            if self._output() != enabled:
                raise SupplyError(f"Output did not become {'ON' if enabled else 'OFF'}")

    def readback(self) -> SupplyReading:
        # Queries are sequential; timestamp is host time, not an instrument trigger.
        with self._lock:
            return SupplyReading(datetime.now(timezone.utc).isoformat(),
                                 self._number(":SOURce:VOLTage:SET? CH1"),
                                 self._number(":SOURce:CURRent:SET? CH1"), self._output(),
                                 self._number(":MEASure:VOLTage? CH1"),
                                 self._number(":MEASure:CURRent? CH1"),
                                 self._number(":MEASure:POWer? CH1"),
                                 self._query(":MEASure:RUN:MODE? CH1"))

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._instrument.close()
                finally:
                    self._manager.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource", help="VISA USB or TCPIP resource; defaults to the sole SPS USB device")
    parser.add_argument("--list", action="store_true", help="list SPS USB resources without changing output")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("status")
    commands.add_parser("set-current").add_argument("amps", type=float)
    commands.add_parser("set-voltage").add_argument("volts", type=float)
    commands.add_parser("output").add_argument("state", choices=("on", "off"))
    args = parser.parse_args()
    try:
        if args.list:
            print(json.dumps(SPS5162X.resources(), indent=2))
            return 0
        with SPS5162X.open(args.resource) as supply:
            if args.command == "set-current": supply.set_current_limit(args.amps)
            elif args.command == "set-voltage": supply.set_voltage_limit(args.volts)
            elif args.command == "output": supply.set_output(args.state == "on")
            print(json.dumps({"identity": supply.identity, "resource": supply.resource,
                              **asdict(supply.readback())}, indent=2))
        return 0
    except (SupplyError, ValueError, pyvisa.errors.VisaIOError) as error:
        print(f"Supply error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
