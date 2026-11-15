"""Raw trace replay transport and scripted golden-transcript transport."""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, List, Optional, Tuple

from klinescan.core.errors import ConfigurationError, MalformedFrameError
from klinescan.core.trace import Trace, TraceEvent
from klinescan.transport.base import BaseTransport, TransportCapabilities


class ReplayTransport(BaseTransport):
    """Replays captured RX frames; TX frames are recorded for comparison.

    This transport performs no interpretation.  It is the deterministic
    substrate for conformance tests and offline reproduction of a session.
    """

    name = "replay"

    def __init__(self, trace: Trace, *, realtime: bool = False, led_callback=None):
        super().__init__(led_callback)
        self._trace = trace
        self._realtime = realtime
        self._start = 0.0
        self._rx: Deque[TraceEvent] = deque(trace.rx_events())
        self.tx_events: List[TraceEvent] = []
        self._write_pos = 0
        self._write_events = [e for e in trace.events if e.direction.upper() == "TX"]

    @property
    def trace(self) -> Trace:
        return self._trace

    def open(self, port: str = "", baudrate: int = 10400) -> None:
        self.port = port or "replay"
        self.requested_baud = baudrate
        self.actual_baud = baudrate
        self._start = time.time()
        self._mark_open()

    def close(self) -> None:
        self._mark_closed()

    def set_baudrate(self, baudrate: int) -> None:
        self.requested_baud = baudrate
        self.actual_baud = baudrate

    def flush(self) -> None:
        return None

    def write(self, data: bytes) -> None:
        self.tx_events.append(TraceEvent(time.time() - self._start, "TX", data))
        self._write_pos += 1
        self._set_led("tx", True)
        self._set_led("tx", False)

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        if not self._rx:
            return b""
        event = self._rx[0]
        if self._realtime:
            target = event.rel_time - (time.time() - self._start)
            if target > 0:
                time.sleep(min(target, timeout))
        chunk = event.data[:size]
        if len(chunk) == len(event.data):
            self._rx.popleft()
        else:
            event.data = event.data[size:]
        self._set_led("rx", True)
        self._set_led("rx", False)
        return chunk

    def expected_tx(self) -> List[bytes]:
        return [e.data for e in self._write_events]

    def unmatched_tx(self) -> List[Tuple[bytes, bytes]]:
        """Return (expected, actual) pairs that differ, for conformance checks."""
        mismatches = []
        for index, actual in enumerate(self.tx_events):
            if index >= len(self._write_events):
                mismatches.append((b"", actual.data))
                continue
            expected = self._write_events[index].data
            if expected != actual.data:
                mismatches.append((expected, actual.data))
        return mismatches

    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            supports_can=True,
            supports_kline=True,
            supports_raw_frames=True,
            supports_5baud=True,
            supports_fast_init=True,
            description="Deterministic trace replay",
        )


class ScriptedTransport(BaseTransport):
    """A strict exchange-based transport for golden protocol transcripts.

    Each ``exchange`` pairs one expected TX frame with zero or more RX frames.
    A mismatch is a test failure, not a warning.
    """

    name = "scripted"

    def __init__(
        self,
        exchanges: List[Tuple[bytes, List[bytes]]],
        initial_rx: Optional[List[bytes]] = None,
    ):
        super().__init__()
        self._exchanges = [
            (bytes(tx), [bytes(rx) for rx in rxs]) for tx, rxs in exchanges
        ]
        self._index = 0
        self._rx: Deque[bytes] = deque(bytes(rx) for rx in (initial_rx or []))
        self.tx_frames: List[bytes] = []
        self.mismatches: List[Tuple[bytes, bytes]] = []
        self._open = False

    def open(self, port: str = "scripted", baudrate: int = 10400) -> None:
        self.port = port
        self._open = True
        self._mark_open()

    def close(self) -> None:
        self._open = False
        self._mark_closed()

    def set_baudrate(self, baudrate: int) -> None:
        self.actual_baud = baudrate

    def flush(self) -> None:
        self._rx.clear()

    def write(self, data: bytes) -> None:
        if not self._open:
            raise ConfigurationError("scripted transport is not open")
        self.tx_frames.append(bytes(data))
        if self._index >= len(self._exchanges):
            self.mismatches.append((b"", bytes(data)))
            return
        expected, responses = self._exchanges[self._index]
        if expected != bytes(data):
            self.mismatches.append((expected, bytes(data)))
        for response in responses:
            self._rx.append(response)
        self._index += 1

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        if not self._rx:
            return b""
        chunk = self._rx[0][:size]
        if len(chunk) == len(self._rx[0]):
            self._rx.popleft()
        else:
            self._rx[0] = self._rx[0][size:]
        return chunk

    @property
    def complete(self) -> bool:
        return self._index >= len(self._exchanges) and not self.mismatches

    def fast_init_pulse(self) -> None:
        return None

    def send_frame(self, frame) -> None:
        self.write(frame.data)

    def slow_init_5baud(self, target_addr: int) -> bytes:
        # Scripted slow-init response is supplied as the first RX frame of the
        # next exchange through ``write``; here we return pending RX directly.
        return self.read(3, timeout=0.1)
