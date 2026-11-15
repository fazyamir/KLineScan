"""Session recorder and player using plain CSV (one event per row).

CSV opens in any spreadsheet or analysis tool and replays in KLineScan.
Columns::

    timestamp, elapsed_s, event, direction, code, name, value, unit, numeric, raw_hex, note

* ``META``   - name/value pairs describing the session (first rows)
* ``FRAME``  - a bus frame: direction (TX/RX/INFO/ERR), raw_hex, note
* ``PID``    - one live value per row; rows of one poll share ``elapsed_s``
* ``DTC``    - one trouble code per row: code, value = status, name = description,
               unit = severity
* ``DTCS_EMPTY`` - a DTC read that returned no codes
* ``STATE``  - name/value state change
"""

from __future__ import annotations

import csv
import io
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from klinescan.core.models import BusFrame, DiagnosticTroubleCode, LivePid

COLUMNS = ["timestamp", "elapsed_s", "event", "direction", "code", "name", "value",
           "unit", "numeric", "raw_hex", "note"]


class SessionRecorder:
    def __init__(self):
        self._fh: Optional[io.TextIOBase] = None
        self._writer: Optional[csv.DictWriter] = None
        self._lock = threading.Lock()
        self._start_time = 0.0
        self._event_count = 0
        self._file_path = ""

    @property
    def is_recording(self) -> bool:
        return self._writer is not None

    @property
    def event_count(self) -> int:
        return self._event_count

    def start_recording(self, file_path: str, meta: Optional[Dict[str, Any]] = None) -> None:
        self.stop_recording()
        if not file_path.lower().endswith(".csv"):
            file_path += ".csv"
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        self._file_path = file_path
        self._fh = open(file_path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=COLUMNS)
        self._writer.writeheader()
        self._start_time = time.time()
        self._event_count = 0
        for key, value in {"format": "klinescan-session-csv/1", **(meta or {})}.items():
            self._row("META", name=str(key), value=str(value))

    def _row(self, event: str, **fields: Any) -> None:
        now = time.time()
        row = {column: "" for column in COLUMNS}
        row.update(timestamp=datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="milliseconds"),
                   elapsed_s=f"{now - self._start_time:.3f}", event=event)
        row.update({k: ("" if v is None else v) for k, v in fields.items()})
        with self._lock:
            if self._writer is not None:
                self._writer.writerow(row)
                self._event_count += 1

    def record_frame(self, frame: BusFrame) -> None:
        if self.is_recording:
            self._row("FRAME", direction=frame.direction, raw_hex=frame.data.hex(" ").upper(), note=frame.note)

    def record_pids(self, pids: List[LivePid]) -> None:
        if not self.is_recording:
            return
        elapsed = f"{time.time() - self._start_time:.3f}"
        for p in pids:
            self._row("PID", code=f"0x{p.pid:02X}", name=p.name, value=p.value, unit=p.unit,
                      numeric="" if p.numeric_value is None else repr(p.numeric_value),
                      raw_hex=p.raw_hex, note=p.category, elapsed_s=elapsed)

    def record_dtcs(self, dtcs: List[DiagnosticTroubleCode]) -> None:
        if not self.is_recording:
            return
        if not dtcs:
            self._row("DTCS_EMPTY")
        for d in dtcs:
            self._row("DTC", code=d.code, value=d.status, name=d.description, unit=d.severity,
                      raw_hex=d.raw_hex)

    def record_state(self, key: str, value: Any) -> None:
        if self.is_recording:
            self._row("STATE", name=key, value=str(value))

    def stop_recording(self) -> Optional[str]:
        with self._lock:
            if self._fh is None:
                return None
            try:
                self._fh.flush()
                self._fh.close()
            finally:
                self._fh = None
                self._writer = None
            return self._file_path


def load_session(file_path: str) -> tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Read a session CSV into (meta, grouped events) for replay or analysis."""
    meta: Dict[str, str] = {}
    events: List[Dict[str, Any]] = []
    with open(file_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            event = row.get("event", "")
            t = float(row.get("elapsed_s") or 0.0)
            if event == "META":
                meta[row["name"]] = row["value"]
            elif event == "FRAME":
                events.append({"type": "FRAME", "rel_time": t, "direction": row["direction"],
                               "data": bytes.fromhex(row["raw_hex"]), "note": row["note"]})
            elif event == "PID":
                pid = LivePid(pid=int(row["code"], 16), name=row["name"], value=row["value"],
                              unit=row["unit"], raw_hex=row["raw_hex"],
                              numeric_value=float(row["numeric"]) if row["numeric"] else None,
                              category=row["note"] or "Engine")
                if events and events[-1]["type"] == "PIDS" and events[-1]["rel_time"] == t:
                    events[-1]["pids"].append(pid)
                else:
                    events.append({"type": "PIDS", "rel_time": t, "pids": [pid]})
            elif event in ("DTC", "DTCS_EMPTY"):
                if not (events and events[-1]["type"] == "DTCS" and abs(events[-1]["rel_time"] - t) < 0.05):
                    events.append({"type": "DTCS", "rel_time": t, "dtcs": []})
                if event == "DTC":
                    events[-1]["dtcs"].append(DiagnosticTroubleCode(
                        code=row["code"], raw_hex=row["raw_hex"], status=row["value"] or "Stored",
                        description=row["name"], severity=row["unit"] or "Warning"))
            elif event == "STATE":
                events.append({"type": "STATE", "rel_time": t, "key": row["name"], "value": row["value"]})
    return meta, events


class SessionPlayer:
    def __init__(
        self,
        frame_cb: Optional[Callable[[BusFrame], None]] = None,
        pid_cb: Optional[Callable[[List[LivePid]], None]] = None,
        dtc_cb: Optional[Callable[[List[DiagnosticTroubleCode]], None]] = None,
        progress_cb: Optional[Callable[[float, float, float], None]] = None,
        state_cb: Optional[Callable[[str, Any], None]] = None,
    ):
        self.frame_cb = frame_cb
        self.pid_cb = pid_cb
        self.dtc_cb = dtc_cb
        self.progress_cb = progress_cb
        self.state_cb = state_cb
        self._events: List[Dict[str, Any]] = []
        self._meta: Dict[str, str] = {}
        self._total_duration = 0.0
        self._speed = 1.0
        self._is_playing = False
        self._is_paused = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._current_index = 0

    @property
    def meta(self) -> Dict[str, str]:
        return dict(self._meta)

    @property
    def is_playing(self) -> bool:
        return self._is_playing and not self._is_paused

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def total_duration(self) -> float:
        return self._total_duration

    def load(self, file_path: str) -> bool:
        self.stop()
        try:
            self._meta, self._events = load_session(file_path)
        except (OSError, ValueError, KeyError, csv.Error):
            self._events = []
            return False
        self._total_duration = self._events[-1]["rel_time"] if self._events else 0.0
        self._current_index = 0
        return True

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.1, speed)

    def play(self) -> None:
        if self._is_paused:
            self._is_paused = False
            self._pause_event.set()
            return
        if self._is_playing or not self._events:
            return
        self._is_playing = True
        self._stop_event.clear()
        self._pause_event.set()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if self._is_playing and not self._is_paused:
            self._is_paused = True
            self._pause_event.clear()

    def stop(self) -> None:
        self._is_playing = False
        self._is_paused = False
        self._stop_event.set()
        self._pause_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._current_index = 0

    def seek_percent(self, pct: float) -> None:
        if not self._events or self._total_duration <= 0:
            return
        target = max(0.0, min(1.0, pct)) * self._total_duration
        self._current_index = next((i for i, ev in enumerate(self._events) if ev["rel_time"] >= target), 0)

    def _run_loop(self) -> None:
        last = self._events[self._current_index]["rel_time"] if self._current_index < len(self._events) else 0.0
        while self._current_index < len(self._events) and not self._stop_event.is_set():
            self._pause_event.wait()
            if self._stop_event.is_set():
                break
            ev = self._events[self._current_index]
            delta = ev["rel_time"] - last
            if delta > 0:
                time.sleep(min(delta / self._speed, 1.0))
            last = ev["rel_time"]
            self._dispatch_event(ev)
            if self.progress_cb:
                pct = ev["rel_time"] / self._total_duration if self._total_duration > 0 else 1.0
                self.progress_cb(pct, ev["rel_time"], self._total_duration)
            self._current_index += 1
        self._is_playing = False
        self._is_paused = False
        if self.progress_cb and self._total_duration > 0:
            self.progress_cb(1.0, self._total_duration, self._total_duration)

    def _dispatch_event(self, ev: Dict[str, Any]) -> None:
        kind = ev["type"]
        if kind == "FRAME" and self.frame_cb:
            self.frame_cb(BusFrame(direction=ev["direction"], data=ev["data"], note=ev["note"],
                                   timestamp=ev["rel_time"]))
        elif kind == "PIDS" and self.pid_cb:
            self.pid_cb(ev["pids"])
        elif kind == "DTCS" and self.dtc_cb:
            self.dtc_cb(ev["dtcs"])
        elif kind == "STATE" and self.state_cb:
            self.state_cb(ev["key"], ev["value"])
