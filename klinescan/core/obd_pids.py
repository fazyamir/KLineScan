"""SAE J1979 Service 01 PID definitions shared by every protocol and the mock ECU.

Formulas, ranges and units are taken from the OBD-II PIDs reference
(https://en.wikipedia.org/wiki/OBD-II_PIDs, oldid 1374011962): only PIDs with a
published numeric formula are decoded; the rest are shown as raw hex.
Supported-PID bitmaps (PIDs 00, 20, 40 ... C0): bit A7..D0 = the next 32 PIDs.

OBD on UDS (SAE J1979-2) maps Service 01 PID ``xx`` to DID ``0xF4xx``
(Softing / SAE J1979-2 overview), so the same table decodes both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Set

BITMAP_PIDS = (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0)


def _u16(d: bytes, i: int = 0) -> int:
    return (d[i] << 8) | d[i + 1]


@dataclass(frozen=True)
class PidDef:
    pid: int
    name: str
    unit: str
    length: int
    decode: Callable[[bytes], float]
    category: str = "Engine"
    minimum: Optional[float] = None
    maximum: Optional[float] = None


def _pct(d):
    return d[0] * 100 / 255


def _temp(d):
    return d[0] - 40


def _trim(d):
    return d[0] * 100 / 128 - 100


_DEFS: List[PidDef] = [
    PidDef(0x04, "Calculated engine load", "%", 1, _pct, "Engine", 0, 100),
    PidDef(0x05, "Engine coolant temperature", "°C", 1, _temp, "Temperature", -40, 215),
    PidDef(0x06, "Short term fuel trim B1", "%", 1, _trim, "Fuel", -100, 99.2),
    PidDef(0x07, "Long term fuel trim B1", "%", 1, _trim, "Fuel", -100, 99.2),
    PidDef(0x08, "Short term fuel trim B2", "%", 1, _trim, "Fuel", -100, 99.2),
    PidDef(0x09, "Long term fuel trim B2", "%", 1, _trim, "Fuel", -100, 99.2),
    PidDef(0x0A, "Fuel pressure", "kPa", 1, lambda d: 3 * d[0], "Fuel", 0, 765),
    PidDef(0x0B, "Intake manifold pressure", "kPa", 1, lambda d: d[0], "Air", 0, 255),
    PidDef(0x0C, "Engine speed", "rpm", 2, lambda d: _u16(d) / 4, "Engine", 0, 16383.75),
    PidDef(0x0D, "Vehicle speed", "km/h", 1, lambda d: d[0], "Vehicle", 0, 255),
    PidDef(0x0E, "Timing advance", "°", 1, lambda d: d[0] / 2 - 64, "Engine", -64, 63.5),
    PidDef(0x0F, "Intake air temperature", "°C", 1, _temp, "Temperature", -40, 215),
    PidDef(0x10, "MAF air flow rate", "g/s", 2, lambda d: _u16(d) / 100, "Air", 0, 655.35),
    PidDef(0x11, "Throttle position", "%", 1, _pct, "Engine", 0, 100),
    *[PidDef(0x14 + i, f"O2 sensor {i + 1} voltage", "V", 2, (lambda d: d[0] / 200), "Oxygen", 0, 1.275)
      for i in range(8)],
    PidDef(0x1F, "Run time since engine start", "s", 2, _u16, "Engine", 0, 65535),
    PidDef(0x21, "Distance with MIL on", "km", 2, _u16, "Vehicle", 0, 65535),
    PidDef(0x22, "Fuel rail pressure (vacuum)", "kPa", 2, lambda d: 0.079 * _u16(d), "Fuel", 0, 5177.265),
    PidDef(0x23, "Fuel rail gauge pressure", "kPa", 2, lambda d: 10 * _u16(d), "Fuel", 0, 655350),
    *[PidDef(0x24 + i, f"O2 sensor {i + 1} lambda", "λ", 4, (lambda d: 2 / 65536 * _u16(d)), "Oxygen", 0, 2)
      for i in range(8)],
    PidDef(0x2C, "Commanded EGR", "%", 1, _pct, "Emissions", 0, 100),
    PidDef(0x2D, "EGR error", "%", 1, _trim, "Emissions", -100, 99.2),
    PidDef(0x2E, "Commanded evaporative purge", "%", 1, _pct, "Emissions", 0, 100),
    PidDef(0x2F, "Fuel tank level", "%", 1, _pct, "Fuel", 0, 100),
    PidDef(0x30, "Warm-ups since codes cleared", "", 1, lambda d: d[0], "Vehicle", 0, 255),
    PidDef(0x31, "Distance since codes cleared", "km", 2, _u16, "Vehicle", 0, 65535),
    PidDef(0x32, "Evap system vapor pressure", "Pa", 2,
           lambda d: (int.from_bytes(d[:2], "big", signed=True)) / 4, "Emissions", -8192, 8191.75),
    PidDef(0x33, "Barometric pressure", "kPa", 1, lambda d: d[0], "Air", 0, 255),
    *[PidDef(0x34 + i, f"O2 sensor {i + 1} current", "mA", 4, (lambda d: _u16(d, 2) / 256 - 128), "Oxygen", -128, 128)
      for i in range(8)],
    PidDef(0x3C, "Catalyst temperature B1S1", "°C", 2, lambda d: _u16(d) / 10 - 40, "Temperature", -40, 6513.5),
    PidDef(0x3D, "Catalyst temperature B2S1", "°C", 2, lambda d: _u16(d) / 10 - 40, "Temperature", -40, 6513.5),
    PidDef(0x3E, "Catalyst temperature B1S2", "°C", 2, lambda d: _u16(d) / 10 - 40, "Temperature", -40, 6513.5),
    PidDef(0x3F, "Catalyst temperature B2S2", "°C", 2, lambda d: _u16(d) / 10 - 40, "Temperature", -40, 6513.5),
    PidDef(0x42, "Control module voltage", "V", 2, lambda d: _u16(d) / 1000, "Electrical", 0, 65.535),
    PidDef(0x43, "Absolute load value", "%", 2, lambda d: 100 / 255 * _u16(d), "Engine", 0, 25700),
    PidDef(0x44, "Commanded air-fuel ratio", "λ", 2, lambda d: 2 / 65536 * _u16(d), "Fuel", 0, 2),
    PidDef(0x45, "Relative throttle position", "%", 1, _pct, "Engine", 0, 100),
    PidDef(0x46, "Ambient air temperature", "°C", 1, _temp, "Temperature", -40, 215),
    PidDef(0x47, "Absolute throttle position B", "%", 1, _pct, "Engine", 0, 100),
    PidDef(0x4D, "Time run with MIL on", "min", 2, _u16, "Vehicle", 0, 65535),
    PidDef(0x52, "Ethanol fuel", "%", 1, _pct, "Fuel", 0, 100),
    PidDef(0x53, "Absolute evap vapor pressure", "kPa", 2, lambda d: _u16(d) / 200, "Emissions", 0, 327.675),
    PidDef(0x54, "Evap vapor pressure", "Pa", 2,
           lambda d: int.from_bytes(d[:2], "big", signed=True), "Emissions", -32768, 32767),
    PidDef(0x59, "Fuel rail absolute pressure", "kPa", 2, lambda d: 10 * _u16(d), "Fuel", 0, 655350),
    PidDef(0x5A, "Relative accelerator pedal", "%", 1, _pct, "Engine", 0, 100),
    PidDef(0x5B, "Hybrid battery remaining life", "%", 1, _pct, "Electrical", 0, 100),
    PidDef(0x5C, "Engine oil temperature", "°C", 1, _temp, "Temperature", -40, 210),
    PidDef(0x5D, "Fuel injection timing", "°", 2, lambda d: _u16(d) / 128 - 210, "Fuel", -210, 301.992),
    PidDef(0x5E, "Engine fuel rate", "L/h", 2, lambda d: _u16(d) / 20, "Fuel", 0, 3212.75),
    PidDef(0x61, "Driver demand torque", "%", 1, lambda d: d[0] - 125, "Engine", -125, 130),
    PidDef(0x62, "Actual engine torque", "%", 1, lambda d: d[0] - 125, "Engine", -125, 130),
    PidDef(0x63, "Engine reference torque", "N·m", 2, _u16, "Engine", 0, 65535),
    PidDef(0x8E, "Engine friction torque", "%", 1, lambda d: d[0] - 125, "Engine", -125, 130),
    PidDef(0xA2, "Cylinder fuel rate", "mg/stroke", 2, lambda d: _u16(d) / 32, "Fuel", 0, 2047.96875),
    PidDef(0xA6, "Odometer", "km", 4, lambda d: int.from_bytes(d[:4], "big") / 10, "Vehicle", 0, 429496729.5),
]

PIDS: Dict[int, PidDef] = {d.pid: d for d in _DEFS}


def decode_bitmap(base: int, data: bytes) -> Set[int]:
    """PIDs flagged in a supported-PID bitmap reply (A7 = base+1 ... D0 = base+32)."""
    value = int.from_bytes(data[:4].ljust(4, b"\0"), "big")
    return {base + 1 + bit for bit in range(32) if value & (1 << (31 - bit))}


def encode_bitmap(base: int, supported: Iterable[int]) -> bytes:
    value = 0
    for pid in supported:
        if base < pid <= base + 32:
            value |= 1 << (31 - (pid - base - 1))
    return value.to_bytes(4, "big")


def discover_supported(query: Callable[[int], Optional[bytes]]) -> Set[int]:
    """Walk the bitmap chain.  ``query(pid)`` returns the data bytes after ``41 pid``."""
    supported: Set[int] = set()
    for base in BITMAP_PIDS:
        data = query(base)
        if not data or len(data) < 4:
            break
        flags = decode_bitmap(base, data)
        supported |= flags
        if base + 0x20 not in flags:  # next bitmap not supported -> chain ends
            break
    return supported


def pollable(supported: Iterable[int]) -> List[int]:
    return sorted(p for p in supported if p in PIDS)


def format_value(definition: PidDef, value: float) -> str:
    if abs(value) >= 1000 or float(value).is_integer():
        return f"{value:.0f}"
    return f"{value:.2f}" if abs(value) < 10 else f"{value:.1f}"
