"""
Versioned raw trace capture / replay and redaction.

The roadmap requires every diagnostic result to be backed by raw evidence and
requires reproducible logs.  This module is the single on-disk format used by
the recorder, the conformance runner and the replay transport.

Format: the session CSV layout (``klinescan.core.session_recorder.COLUMNS``),
``.trace.csv``.  ``META`` rows carry version/redaction/metadata (JSON values),
``FRAME`` rows carry ``elapsed_s``, ``direction``, ``raw_hex`` and ``note``.

Serial debug logs (``SERIAL write data ..`` / ``SERIAL read data ..``) can be
imported with :func:`load_serial_log`.
"""

from __future__ import annotations

import csv
import json
import os
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from klinescan.core.models import BusFrame
from klinescan.core.session_recorder import COLUMNS

TRACE_VERSION = 1

_DEFAULT_REDACT_KEYS = {
    "vin",
    "license_plate",
    "plate",
    "owner",
    "customer",
    "address",
    "phone",
    "email",
}


@dataclass
class RedactionPolicy:
    """Minimal, explicit redaction for metadata before a trace is shared."""

    enabled: bool = True
    keys: set = field(default_factory=lambda: set(_DEFAULT_REDACT_KEYS))

    def scrub(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return dict(metadata)
        out: Dict[str, Any] = {}
        for key, value in metadata.items():
            if key.lower() in self.keys:
                out[key] = "[redacted]"
            elif isinstance(value, dict):
                out[key] = self.scrub(value)
            else:
                out[key] = value
        return out

    @property
    def label(self) -> str:
        return "default" if self.enabled else "none"


@dataclass
class TraceEvent:
    rel_time: float
    direction: str
    data: bytes
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "type": "FRAME",
            "rel_time": round(self.rel_time, 6),
            "direction": self.direction,
            "hex": self.data.hex().upper(),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TraceEvent":
        return cls(
            rel_time=float(data.get("rel_time", 0.0)),
            direction=str(data.get("direction", "RX")),
            data=bytes.fromhex(data.get("hex", "")),
            note=str(data.get("note", "")),
        )

    def to_bus_frame(self) -> BusFrame:
        return BusFrame(
            direction=self.direction,
            data=self.data,
            note=self.note,
            timestamp=self.rel_time,
        )


def reproducibility_metadata(
    *,
    transport: str = "",
    protocol: str = "",
    adapter: str = "",
    port: str = "",
    pack_versions: Optional[Dict[str, str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Metadata that makes a capture reproducible and auditable."""
    try:
        from klinescan import __version__ as app_version
    except Exception:  # pragma: no cover - defensive
        app_version = "unknown"

    meta: Dict[str, Any] = {
        "app": "KLineScan",
        "app_version": app_version,
        "transport": transport,
        "protocol": protocol,
        "adapter": adapter,
        "port": port,
        "os": platform.platform(),
        "python": platform.python_version(),
        "executable": sys.executable,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pack_versions": pack_versions or {},
    }
    if extra:
        meta.update(extra)
    return meta


class TraceRecorder:
    """Writes a versioned, redacted trace file."""

    def __init__(self, redaction: Optional[RedactionPolicy] = None):
        self.redaction = redaction or RedactionPolicy()
        self._file = None
        self._path = ""
        self._start = 0.0

    @property
    def is_recording(self) -> bool:
        return self._file is not None

    def start(self, path: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        self.stop()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._path = path
        self._file = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=COLUMNS)
        self._writer.writeheader()
        self._start = time.time()
        for row in _meta_rows(TRACE_VERSION, self.redaction.label, self.redaction.scrub(metadata or {})):
            self._writer.writerow(row)

    def record(self, direction: str, data: bytes, note: str = "") -> None:
        if not self._file:
            return
        event = TraceEvent(time.time() - self._start, direction, data, note)
        self._writer.writerow(_frame_row(event))

    def record_frame(self, frame: BusFrame) -> None:
        self.record(frame.direction, frame.data, frame.note)

    def stop(self) -> Optional[str]:
        if self._file:
            try:
                self._file.flush()
                self._file.close()
            finally:
                self._file = None
                return self._path
        return None


@dataclass
class Trace:
    events: List[TraceEvent] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    version: int = TRACE_VERSION
    redaction: str = "default"

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(_meta_rows(self.version, self.redaction, self.metadata))
            writer.writerows(_frame_row(event) for event in self.events)

    @classmethod
    def load(cls, path: str) -> "Trace":
        trace = cls()
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["event"] == "META":
                    value = json.loads(row["value"]) if row["value"] else ""
                    if row["name"] == "version":
                        trace.version = int(value)
                    elif row["name"] == "redaction":
                        trace.redaction = str(value)
                    elif row["name"] == "metadata":
                        trace.metadata = value or {}
                elif row["event"] == "FRAME":
                    trace.events.append(TraceEvent(float(row["elapsed_s"] or 0.0), row["direction"] or "RX",
                                                   bytes.fromhex(row["raw_hex"]), row["note"]))
        return trace

    def rx_events(self) -> List[TraceEvent]:
        return [e for e in self.events if e.direction.upper() == "RX"]


def _meta_rows(version: int, redaction: str, metadata: Dict[str, Any]) -> List[Dict[str, str]]:
    rows = []
    for name, value in (("format", "klinescan-trace-csv/1"), ("version", version),
                        ("redaction", redaction), ("metadata", metadata)):
        row = {c: "" for c in COLUMNS}
        row.update(event="META", name=name, value=json.dumps(value, sort_keys=True))
        rows.append(row)
    return rows


def _frame_row(event: "TraceEvent") -> Dict[str, str]:
    row = {c: "" for c in COLUMNS}
    row.update(elapsed_s=f"{event.rel_time:.6f}", event="FRAME", direction=event.direction,
               raw_hex=event.data.hex(" ").upper(), note=event.note)
    return row


_SERIAL_RE = re.compile(
    r"(SERIAL write data|SERIAL read data)\s+([0-9A-Fa-f]{2})"
)


def load_serial_log(path: str) -> Trace:
    """Import a serial debug/dump log into the common trace format."""
    events: List[TraceEvent] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            match = _SERIAL_RE.search(line)
            if not match:
                continue
            direction = "TX" if "write" in match.group(1) else "RX"
            events.append(
                TraceEvent(float(len(events)), direction, bytes([int(match.group(2), 16)]))
            )
    return Trace(events=events, metadata={"source": os.path.basename(path)})


def iter_frames(trace: Trace) -> Iterable[BusFrame]:
    for event in trace.events:
        yield event.to_bus_frame()
