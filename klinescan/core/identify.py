"""Automatic vehicle identification: generic OBD connect, VIN/ECU IDs, catalogue match.

Steps (each reported in ``IdentifyResult.log``):
1. Find a working OBD link.  ELM327-class adapters run their own protocol search
   (AT SP 0 + AT DPN); raw K-line adapters try ISO 9141-2 5-baud to 0x33 and then
   KWP2000 fast init with the OBD functional header (C1 33 F1); raw CAN adapters
   use the functional ID 0x7DF (responder discovered).
2. Read Service 09 VIN / calibration IDs / ECU name and the supported PIDs.
3. Decode the VIN (``klinescan.core.vin``) and rank catalogue vehicles of that brand
   whose names carry a compatible model year.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterable, List, Optional, Tuple

from klinescan.core.models import EcuIdItem, EcuTarget, ProtocolType
from klinescan.core.vin import VinInfo, decode_vin

GENERIC_CAN = [
    EcuTarget("OBD-II CAN 11-bit", ProtocolType.CAN_OBD, baudrate=500000, can_tx_id=0x7DF, can_rx_id=0,
              pin=6, can_bitrate=500000, confidence="curated", source_ref="obd:generic"),
    EcuTarget("OBD-II CAN 11-bit 250k", ProtocolType.CAN_OBD, baudrate=250000, can_tx_id=0x7DF, can_rx_id=0,
              pin=6, can_bitrate=250000, confidence="curated", source_ref="obd:generic"),
]
GENERIC_KLINE = [
    EcuTarget("OBD-II ISO 9141-2", ProtocolType.ISO9141, slow_init_addr=0x33, use_obd_header=True,
              tester_addr=0xF1, confidence="curated", source_ref="obd:generic"),
    EcuTarget("OBD-II KWP2000 fast init", ProtocolType.KWP2000_FAST, ecu_addr=0x33, tester_addr=0xF1,
              header_format=0xC0, confidence="curated", source_ref="obd:generic"),
]


@dataclass
class IdentifyResult:
    connected: bool = False
    protocol: str = ""
    target: Optional[EcuTarget] = None
    ids: List[EcuIdItem] = field(default_factory=list)
    vin: Optional[VinInfo] = None
    supported_pids: List[int] = field(default_factory=list)
    candidates: List[dict] = field(default_factory=list)
    log: List[str] = field(default_factory=list)

    @property
    def ecu_name(self) -> str:
        return next((i.value for i in self.ids if i.label == "ECU name"), "")


_YEAR4 = re.compile(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)")
_RANGE2 = re.compile(r"(?<!\d)(\d{2})\s*[-~]\s*(\d{2})?(?!\d)")
_MY2 = re.compile(r"(?<!\d)(\d{2})MY")


def _yy(value: str) -> int:
    n = int(value)
    return 1900 + n if n >= 80 else 2000 + n


def year_span(text: str) -> Optional[Tuple[int, int]]:
    """Model-year span written in a catalogue name ('2007MY', '03-05', '(1998~ )', '06-', '1996')."""
    text = text or ""
    years = [int(y) for y in _YEAR4.findall(text)]
    if years:
        lo, hi = min(years), max(years)
        if re.search(r"(19|20)\d\d\s*[~-]\s*\)?\s*$|~\s*\)", text) and len(years) == 1:
            hi = date.today().year + 1
        if re.search(r"\(\s*~\s*(19|20)\d\d", text) and len(years) == 1:
            lo = 1980
        return lo, hi
    m = _RANGE2.search(text)
    if m:
        lo = _yy(m.group(1))
        hi = _yy(m.group(2)) if m.group(2) else date.today().year + 1
        return (lo, hi) if lo <= hi else None
    m = _MY2.search(text)
    if m:
        y = _yy(m.group(1))
        return y, y
    return None


def match_catalog(vin: VinInfo, catalog, limit: int = 20) -> List[dict]:
    """Rank catalogue vehicles for the VIN's brand by model-year compatibility."""
    if catalog is None or not vin or not vin.manufacturer:
        return []
    wanted = {b.upper() for b in vin.catalog_brands}
    this_year = date.today().year + 1
    years = [y for y in vin.model_years if y <= this_year] or vin.model_years
    out = []
    for brand in catalog.brands():
        if brand["name"].upper() not in wanted:
            continue
        for vehicle in brand["vehicles"]:
            span = year_span(f"{vehicle['name']} {vehicle.get('sub') or ''}")
            if span and years:
                if not any(span[0] <= y <= span[1] for y in years):
                    continue
                score, reason = 2.0, f"model year {'/'.join(map(str, years))} within {span[0]}-{span[1]}"
            else:
                score, reason = 1.0, "brand match (no model year in the catalogue name)"
            out.append({"brand": brand["name"], "vehicle": vehicle["name"], "sub": vehicle.get("sub"),
                        "systems": len(vehicle["systems"]), "score": score, "reason": reason})
    out.sort(key=lambda c: (-c["score"], c["brand"], str(c["vehicle"])))
    return out[:limit]


def identify(transport, protocol_factory: Callable[[object, EcuTarget], object],
             catalog=None, kinds: Iterable[str] = ("can", "kline")) -> IdentifyResult:
    """Run the identification flow on an *opened* transport."""
    result = IdentifyResult()
    protocol = None
    if hasattr(transport, "auto_protocol"):
        try:
            result.protocol = transport.auto_protocol()
            result.log.append(f"adapter protocol search: {result.protocol}")
            target = EcuTarget("OBD-II (adapter auto)", ProtocolType.CAN_OBD, can_tx_id=0x7DF, can_rx_id=0,
                               confidence="curated", source_ref=transport.AUTO_OBD)
            protocol = protocol_factory(transport, target)
            if protocol.connect():
                result.connected, result.target = True, target
        except Exception as exc:
            result.log.append(f"adapter protocol search failed: {exc}")
    if not result.connected:
        candidates = (GENERIC_CAN if "can" in kinds else []) + (GENERIC_KLINE if "kline" in kinds else [])
        for target in candidates:
            try:
                protocol = protocol_factory(transport, target)
                if protocol.connect():
                    result.connected, result.target, result.protocol = True, target, target.name
                    result.log.append(f"connected via {target.name}")
                    break
                result.log.append(f"{target.name}: no answer")
            except Exception as exc:
                result.log.append(f"{target.name}: {exc}")
    if not result.connected or protocol is None:
        result.log.append("no OBD-II response: check ignition, cable and adapter")
        return result
    try:
        result.ids = protocol.read_ecu_id()
    except Exception as exc:
        result.log.append(f"vehicle information (Service 09) unavailable: {exc}")
    try:
        result.supported_pids = protocol.discover_supported_pids(force=True)
    except Exception as exc:
        result.log.append(f"supported PID discovery failed: {exc}")
    vin_text = next((i.value for i in result.ids if "VIN" in i.label), "")
    if vin_text:
        result.vin = decode_vin(vin_text)
        result.log.append(f"VIN {result.vin.vin}: {result.vin.manufacturer or 'unknown maker'}, "
                          f"model year {result.vin.likely_year or '?'}"
                          + (f" (code also fits {', '.join(str(y) for y in result.vin.model_years if y != result.vin.likely_year)})"
                             if len(result.vin.model_years) > 1 else ""))
        result.candidates = match_catalog(result.vin, catalog)
    else:
        result.log.append("ECU did not report a VIN (common before ~2005 on K-line)")
    return result
