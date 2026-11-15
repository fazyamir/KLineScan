"""KWP1281 measuring-block value formulas.

A group reading (request 0x29, response block 0xE7) carries 3-byte measurements
``formula, NW, MW``.  The formula byte selects how NW/MW are scaled.  The
formulas below are re-implemented from the formula table of the open-source
KLineKWP1281Lib (https://github.com/domnulvlad/KLineKWP1281Lib,
``getMeasurementValue``), which credits blafusel.de for the protocol
documentation.  Formulas that produce text, need a lookup table, or carry a
variable-length payload are not decoded: they are shown raw rather than
guessed.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple


def _signed8(v: int) -> int:
    return v if v <= 127 else v - 256


def _signed16(nw: int, mw: int) -> int:
    value = nw * 256 + mw
    return value if value <= 32767 else value - 65536


def _u16(nw: int, mw: int) -> int:
    return nw * 256 + mw


# formula -> (name, unit, f(NW, MW))
FORMULAS: Dict[int, Tuple[str, str, Callable[[int, int], float]]] = {
    0x01: ("Engine speed", "/min", lambda nw, mw: mw * nw * 0.2),
    0x02: ("Value", "%", lambda nw, mw: mw * nw * 0.002),
    0x03: ("Angle", "°", lambda nw, mw: mw * nw * 0.002),
    0x04: ("Ignition angle", "° ATDC", lambda nw, mw: (mw - 127) * nw * -0.01),
    0x05: ("Temperature", "°C", lambda nw, mw: (mw - 100) * nw * 0.1),
    0x06: ("Voltage", "V", lambda nw, mw: mw * nw * 0.001),
    0x07: ("Speed", "km/h", lambda nw, mw: mw * nw * 0.01),
    0x08: ("Value", "", lambda nw, mw: mw * nw * 0.1),
    0x09: ("Steering angle", "°", lambda nw, mw: (mw - 127) * nw * 0.02),
    0x0B: ("Lambda", "", lambda nw, mw: 1 + (mw - 128) * nw * 0.0001),
    0x0C: ("Resistance", "Ω", lambda nw, mw: mw * nw * 0.001),
    0x0D: ("Distance", "mm", lambda nw, mw: (mw - 127) * nw * 0.001),
    0x0E: ("Pressure", "bar", lambda nw, mw: mw * nw * 0.005),
    0x0F: ("Time", "ms", lambda nw, mw: mw * nw * 0.01),
    0x12: ("Pressure", "mbar", lambda nw, mw: mw * nw * 0.04),
    0x13: ("Volume", "l", lambda nw, mw: mw * nw * 0.01),
    0x14: ("Value", "%", lambda nw, mw: (mw - 128) * nw / 128),
    0x15: ("Voltage", "V", lambda nw, mw: mw * nw * 0.001),
    0x16: ("Time", "ms", lambda nw, mw: mw * nw * 0.001),
    0x17: ("Value", "%", lambda nw, mw: mw * nw / 256),
    0x18: ("Current", "A", lambda nw, mw: mw * nw * 0.001),
    0x19: ("Air mass", "g/s", lambda nw, mw: (256 * mw + nw) / 180),
    0x1A: ("Temperature", "°C", lambda nw, mw: mw - nw),
    0x1B: ("Angle", "°", lambda nw, mw: (mw - 128) * nw * 0.01),
    0x1C: ("Value", "", lambda nw, mw: mw - nw),
    0x1E: ("Angle", "°", lambda nw, mw: mw * nw / 12),
    0x1F: ("Value", "", lambda nw, mw: mw * nw / 2560),
    0x20: ("Value", "", lambda nw, mw: _signed8(mw)),
    0x21: ("Value", "%", lambda nw, mw: (mw / nw) * 100 if nw else 0.0),
    0x22: ("Correction", "KW", lambda nw, mw: (mw - 128) * nw * 0.01),
    0x23: ("Consumption", "l/h", lambda nw, mw: mw * nw * 0.01),
    0x24: ("Distance", "km", lambda nw, mw: _u16(nw, mw) * 10),
    0x26: ("Correction", "KW", lambda nw, mw: (mw - 128) * nw * 0.001),
    0x27: ("Injected quantity", "mg/h", lambda nw, mw: nw * mw / 255),
    0x28: ("Current", "A", lambda nw, mw: ((nw * 255 + mw) - 4000) * 0.1),
    0x29: ("Charge", "Ah", lambda nw, mw: nw * 255 + mw),
    0x2A: ("Power", "kW", lambda nw, mw: ((nw * 255 + mw) - 4000) * 0.1),
    0x2B: ("Voltage", "V", lambda nw, mw: (nw * 255 + mw) * 0.1),
    0x2D: ("Consumption", "l/km", lambda nw, mw: mw * nw * 0.1),
    0x2E: ("Correction", "KW", lambda nw, mw: ((nw * 256) + mw - 32768) * 0.00275),
    0x2F: ("Time", "ms", lambda nw, mw: (mw - 128) * nw),
    0x30: ("Value", "", lambda nw, mw: nw * 255 + mw),
    0x31: ("Injected quantity", "mg/h", lambda nw, mw: nw * mw * 0.025),
    0x32: ("Pressure", "mbar", lambda nw, mw: (mw - 128) * 100 / nw if nw else 0.0),
    0x33: ("Injected quantity", "mg/h", lambda nw, mw: (mw - 128) * nw / 255),
    0x34: ("Torque", "Nm", lambda nw, mw: (mw - 50) * nw * 0.02),
    0x35: ("Air mass", "g/s", lambda nw, mw: ((mw - 128) * 256 + nw) / 180),
    0x36: ("Count", "", lambda nw, mw: nw * 256 + mw),
    0x37: ("Time", "s", lambda nw, mw: mw * nw * 0.005),
    0x3A: ("Rate", "/s", lambda nw, mw: _signed8(mw) * 1.023),
    0x3B: ("Value", "", lambda nw, mw: _u16(nw, mw) / 32768),
    0x3C: ("Time", "s", lambda nw, mw: _u16(nw, mw) / 100),
    0x3D: ("Value", "", lambda nw, mw: (mw - 128) / nw if nw else 0.0),
    0x3E: ("Time", "s", lambda nw, mw: mw * nw * 256 / 1000),
    0x40: ("Resistance", "Ω", lambda nw, mw: mw + nw),
    0x41: ("Distance", "mm", lambda nw, mw: (mw - 127) * nw * 0.01),
    0x42: ("Voltage", "V", lambda nw, mw: mw * nw / 512),
    0x43: ("Angle", "°", lambda nw, mw: _signed16(nw, mw) * 2.5),
    0x44: ("Yaw rate", "°/s", lambda nw, mw: _signed16(nw, mw) * 0.1358),
    0x45: ("Pressure", "bar", lambda nw, mw: _signed16(nw, mw) * 0.3255),
    0x46: ("Acceleration", "m/s²", lambda nw, mw: _signed16(nw, mw) * 0.192),
    0x47: ("Distance", "cm", lambda nw, mw: mw * nw),
    0x48: ("Voltage", "V", lambda nw, mw: (nw * 255 + mw * (211 - nw)) / 4080),
    0x49: ("Resistance", "Ω", lambda nw, mw: nw * mw * 0.01),
    0x4A: ("Time", "months", lambda nw, mw: 0.1 * nw * mw),
    0x4C: ("Resistance", "kΩ", lambda nw, mw: nw * 255 + mw),
    0x4D: ("Voltage", "V", lambda nw, mw: (255 * nw + mw * 60) / 4080),
    0x4E: ("Rate", "/s", lambda nw, mw: _signed8(mw) * 1.819),
    0x4F: ("Channel", "", lambda nw, mw: mw),
    0x50: ("Resistance", "kΩ", lambda nw, mw: _u16(nw, mw) / 100),
    0x51: ("Angle", "°", lambda nw, mw: _signed16(nw, mw) * 0.04375),
    0x52: ("Acceleration", "m/s²", lambda nw, mw: _signed16(nw, mw) * 0.00981),
    0x53: ("Pressure", "bar", lambda nw, mw: _signed16(nw, mw) * 0.01),
    0x54: ("Acceleration", "m/s²", lambda nw, mw: _signed16(nw, mw) * 0.0973),
    0x55: ("Yaw rate", "°/s", lambda nw, mw: _signed16(nw, mw) * 0.002865),
    0x56: ("Current", "A", lambda nw, mw: mw * nw * 0.1),
    0x57: ("Rate", "°/s", lambda nw, mw: nw * (mw - 128) * 0.1),
    0x58: ("Resistance", "kΩ", lambda nw, mw: mw * nw * 0.01),
    0x59: ("Time", "h", lambda nw, mw: nw * 256 + mw),
    0x5A: ("Mass", "kg", lambda nw, mw: nw * mw * 0.1),
    0x5B: ("Angle", "°", lambda nw, mw: nw * (mw - 128) * 0.1),
    0x5C: ("Distance", "km", lambda nw, mw: nw * mw),
    0x5D: ("Torque", "Nm", lambda nw, mw: (mw - 128) * nw * 0.001),
    0x5E: ("Torque", "Nm", lambda nw, mw: nw * (mw - 128) * 0.1),
    0x60: ("Pressure", "mbar", lambda nw, mw: nw * mw * 0.1),
    0x61: ("Temperature", "°C", lambda nw, mw: (mw - nw) * 5),
    0x62: ("Value", "/km", lambda nw, mw: nw * mw * 0.1),
    0x63: ("Value", "", lambda nw, mw: _signed16(nw, mw)),
    0x64: ("Pressure", "bar", lambda nw, mw: nw * mw * 0.1),
    0x65: ("Value", "l/mm", lambda nw, mw: mw * nw * 0.001),
    0x66: ("Distance", "mm", lambda nw, mw: mw * nw * 0.1),
    0x67: ("Voltage", "V", lambda nw, mw: nw + mw * 0.05),
    0x68: ("Volume", "ml", lambda nw, mw: (mw - 128) * nw * 0.2),
    0x69: ("Distance", "m", lambda nw, mw: (mw - 128) * nw * 0.01),
    0x6A: ("Speed", "km/h", lambda nw, mw: (mw - 128) * nw * 0.1),
    0x70: ("Angle", "°", lambda nw, mw: (mw - 128) * nw * 0.001),
    0x71: ("Value", "", lambda nw, mw: (mw - 128) * nw * 0.01),
    0x72: ("Distance", "m", lambda nw, mw: (mw - 128) * nw),
    0x73: ("Power", "W", lambda nw, mw: _signed16(nw, mw)),
    0x74: ("Engine speed", "/min", lambda nw, mw: _signed16(nw, mw)),
    0x75: ("Temperature", "°C", lambda nw, mw: (mw - 64) * nw * 0.01),
    0x77: ("Value", "%", lambda nw, mw: mw * nw * 0.01),
    0x78: ("Angle", "°", lambda nw, mw: mw * nw * 1.41),
    0x79: ("Value", "", lambda nw, mw: ((mw * 256) + nw) * 0.5),
    0x7A: ("Value", "", lambda nw, mw: ((mw * 256) + nw - 32768) * 0.01),
    0x7C: ("Current", "mA", lambda nw, mw: mw * nw * 0.1),
    0x7D: ("Level", "dB", lambda nw, mw: nw - mw),
    0x7E: ("Value", "", lambda nw, mw: nw * mw * 0.1),
    0x80: ("Engine speed", "/min", lambda nw, mw: mw * nw),
    0x81: ("Value", "%", lambda nw, mw: mw * nw / 256),
    0x82: ("Current", "A", lambda nw, mw: mw * nw / 2560),
    0x83: ("Angle", "°", lambda nw, mw: (mw * nw * 0.5) - 30),
    0x84: ("Angle", "°", lambda nw, mw: mw * nw * 0.5),
    0x85: ("Voltage", "V", lambda nw, mw: mw * nw / 256),
    0x86: ("Speed", "km/h", lambda nw, mw: mw * nw),
    0x87: ("Value", "", lambda nw, mw: mw * nw),
    0x89: ("Time", "ms", lambda nw, mw: mw * nw * 0.01),
    0x8A: ("Voltage", "V", lambda nw, mw: mw * nw * 0.001),
    0x8F: ("Angle", "°", lambda nw, mw: (mw - 128) * nw * 0.01),
    0x90: ("Consumption", "l/h", lambda nw, mw: mw * nw * 0.01),
    0x91: ("Value", "", lambda nw, mw: mw * nw * 0.01),
    0x92: ("Value", "", lambda nw, mw: 1 + (mw - 128) * nw * 0.0001),
    0x94: ("Value", "", lambda nw, mw: (mw - 128) * nw * 0.25),
    0x95: ("Temperature", "°C", lambda nw, mw: (mw - 100) * nw * 0.1),
    0x96: ("Air mass", "g/s", lambda nw, mw: (256 * mw + nw) / 180),
    0x97: ("Correction", "KW", lambda nw, mw: (mw - 128) * nw * 0.01),
    0x98: ("Injected quantity", "mg/h", lambda nw, mw: nw * mw * 0.025),
    0x99: ("Injected quantity", "mg/h", lambda nw, mw: (mw - 128) * nw / 255),
    0x9A: ("Angle", "°", lambda nw, mw: mw * nw),
    0x9B: ("Angle", "°", lambda nw, mw: (nw * mw * 0.01) - 90),
    0x9C: ("Distance", "cm", lambda nw, mw: _u16(nw, mw)),
    0x9D: ("Distance", "cm", lambda nw, mw: _signed16(nw, mw)),
    0x9E: ("Speed", "km/h", lambda nw, mw: _u16(nw, mw) * 0.01),
    0x9F: ("Temperature", "°C", lambda nw, mw: ((mw - 127) * 256 + nw) * 0.1),
    0xA2: ("Angle", "°", lambda nw, mw: nw * mw * 0.448),
    0xA5: ("Current", "mA", lambda nw, mw: (mw * 256) + nw - 32768),
    0xA6: ("Rate", "°/s", lambda nw, mw: _signed16(nw, mw) * 2.5),
    0xA7: ("Resistance", "mΩ", lambda nw, mw: mw * nw * 0.001),
    0xA8: ("Value", "%", lambda nw, mw: (256 * nw + mw) * 0.01),
    0xA9: ("Voltage", "mV", lambda nw, mw: mw * nw + 200),
    0xAA: ("Mass", "g", lambda nw, mw: mw * nw / 2560),
    0xAB: ("Mass", "g", lambda nw, mw: mw * nw / 2560),
    0xAC: ("Emission", "mg/km", lambda nw, mw: mw * nw),
    0xAD: ("Mass flow", "mg/s", lambda nw, mw: mw * nw * 0.1),
    0xAE: ("Distance", "km", lambda nw, mw: mw * nw * 0.01),
    0xAF: ("Pressure", "bar", lambda nw, mw: mw * nw * 0.005),
    0xB0: ("Concentration", "ppm", lambda nw, mw: mw * nw * 0.1),
    0xB1: ("Torque", "Nm", lambda nw, mw: (nw - 128) * 256 + mw),
    0xB2: ("Rate", "°/s", lambda nw, mw: ((nw - 128) * 256 + mw) * (6 / 51)),
    0xB3: ("Value", "", lambda nw, mw: (nw * 256 + mw) * 10),
    0xB4: ("Mass flow", "kg/h", lambda nw, mw: mw * nw / 256),
    0xB5: ("Mass flow", "mg/s", lambda nw, mw: mw * nw * 0.001),
}

# Formula bytes whose measurement is not 3 bytes long, or whose meaning needs a
# table (text, switch states, header+body groups).  Shown raw.
VARIABLE_LENGTH = {0x3F, 0x5F, 0x76, 0xA0}
TEXT_FORMULAS = {0x0A: "Warm/Cold", 0x10: "Switch bits", 0x11: "ASCII", 0x1D: "Map",
                 0x25: "Text table", 0x2C: "Time hh:mm", 0x38: "Code", 0x39: "Code",
                 0x6B: "Hex", 0x7B: "Text table", 0x7F: "Date", 0x88: "Switch bits",
                 0x8B: "Table", 0x8C: "Table", 0x8D: "Text table", 0x8E: "ASCII",
                 0x93: "Table", 0xA1: "Binary", 0xA3: "Time", 0xA4: "Value"}


def decode(formula: int, nw: int, mw: int) -> Tuple[str, str, str, Optional[float]]:
    """Return ``(name, unit, display, numeric)`` for one measurement."""
    entry = FORMULAS.get(formula)
    if entry is not None:
        name, unit, fn = entry
        try:
            value = float(fn(nw, mw))
        except ZeroDivisionError:
            value = 0.0
        display = f"{value:.0f}" if abs(value) >= 100 else f"{value:.3g}" if value else "0"
        return name, unit, display, value
    if formula == 0x11 or formula == 0x8E:
        text = bytes([nw, mw]).decode("ascii", errors="replace")
        return "Text", "", text, None
    if formula in (0x10, 0x88):
        return "Switch bits", "", f"{mw & nw:08b}", None
    label = TEXT_FORMULAS.get(formula, f"Type 0x{formula:02X}")
    return label, "", f"0x{nw:02X} 0x{mw:02X}", None
