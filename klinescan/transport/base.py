"""
Common transport contract for KLineScan.

The contract separates concerns that a single-file reference implementation keeps
in separate interfaces:

1. device discovery / selection        -> ``list_devices`` / ``open``
2. physical line configuration         -> ``configure`` / ``set_baudrate``
3. bus / session initialisation        -> ``initialize_bus``
4. raw frame read / write              -> ``write`` / ``read`` / ``send_frame``
5. timing and cancellation             -> ``timeout`` / ``cancel``
6. error classification                -> ``klinescan.core.errors``
7. capture and replay                  -> ``klinescan.core.trace`` / ReplayTransport
8. capability reporting                -> ``capabilities``

Protocol code must only depend on this contract, never on adapter specifics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from klinescan.core.errors import (
    KLineScanError,
    ConfigurationError,
    UnsupportedOperationError,
)

# Backwards compatibility: callers historically imported TransportError.
TransportError = KLineScanError


class TransportState(str, Enum):
    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    ERROR = "error"


@dataclass
class TransportCapabilities:
    supports_can: bool = False
    supports_kline: bool = True
    supports_j1850_pwm: bool = False
    supports_j1850_vpw: bool = False
    supports_fast_init: bool = True
    supports_5baud: bool = True
    supports_raw_frames: bool = True
    supports_l_line: bool = False
    supports_custom_baud: bool = False
    supports_obd_passthrough: bool = False
    supports_extended_can: bool = False
    supports_flow_control: bool = True
    max_baudrate: int = 115200
    description: str = ""

    def as_dict(self) -> Dict[str, bool]:
        return {
            "can": self.supports_can,
            "kline": self.supports_kline,
            "fast_init": self.supports_fast_init,
            "slow_init": self.supports_5baud,
            "l_line": self.supports_l_line,
            "custom_baud": self.supports_custom_baud,
            "raw_frames": self.supports_raw_frames,
            "obd_passthrough": self.supports_obd_passthrough,
            "extended_can": self.supports_extended_can,
            "flow_control": self.supports_flow_control,
        }


@dataclass
class CanFrame:
    """One raw CAN frame. Used by the common CAN/ISO-TP layer."""

    arbitration_id: int
    data: bytes
    extended: bool = False
    timestamp: float = 0.0
    channel: str = ""

    def __post_init__(self) -> None:
        max_id = 0x1FFFFFFF if self.extended else 0x7FF
        if self.arbitration_id < 0 or self.arbitration_id > max_id:
            raise ConfigurationError(
                f"CAN identifier 0x{self.arbitration_id:X} does not fit "
                f"{'29' if self.extended else '11'}-bit addressing"
            )
        if len(self.data) > 8 and not self.extended:
            raise ConfigurationError("Classic CAN frames carry at most 8 data bytes")

    def __bytes__(self) -> bytes:
        return bytes(self.data)


@dataclass
class TransportInfo:
    name: str
    port: str = ""
    baudrate: int = 0
    adapter: str = ""
    state: TransportState = TransportState.CLOSED
    capabilities: TransportCapabilities = field(default_factory=TransportCapabilities)
    metadata: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "port": self.port,
            "baudrate": self.baudrate,
            "adapter": self.adapter,
            "state": self.state.value,
            "capabilities": self.capabilities.as_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass
class CancellationToken:
    """Cooperative cancellation shared by service, worker and protocols."""

    _cancelled: bool = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            from klinescan.core.errors import OperationCancelled

            raise OperationCancelled("Operation cancelled by user")


def check_cancelled(transport) -> None:
    """Raise OperationCancelled if the transport's operation was cancelled (safe on any object)."""
    token = getattr(transport, "cancel_token", None)
    if token is not None:
        token.raise_if_cancelled()


class BaseTransport(ABC):
    name: str = "base"

    def __init__(self, led_callback: Optional[Callable[[str, bool], None]] = None):
        self._led_cb = led_callback
        self.pin: int = 7
        self.requested_baud: int = 10400
        self.actual_baud: int = 10400
        self.echo_enabled: bool = True
        self.state: TransportState = TransportState.CLOSED
        self.port: str = ""
        self.cancel_token = CancellationToken()

    # -- lifecycle ---------------------------------------------------------
    def _set_led(self, name: str, state: bool) -> None:
        if self._led_cb:
            self._led_cb(name, state)

    def _mark_open(self) -> None:
        self.state = TransportState.OPEN
        self._set_led("iface", True)

    def _mark_closed(self) -> None:
        self.state = TransportState.CLOSED
        self._set_led("iface", False)
        self._set_led("connected", False)

    def __enter__(self) -> "BaseTransport":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @abstractmethod
    def open(self, port: str, baudrate: int) -> None:
        pass

    @abstractmethod
    def close(self) -> None:
        pass

    @abstractmethod
    def set_baudrate(self, baudrate: int) -> None:
        pass

    @abstractmethod
    def flush(self) -> None:
        pass

    @abstractmethod
    def write(self, data: bytes) -> None:
        pass

    @abstractmethod
    def read(self, size: int, timeout: float = 0.5) -> bytes:
        pass

    # -- optional contract -------------------------------------------------
    def configure(self, **kwargs) -> None:
        """Apply adapter/line settings (parity, stopbits, latency, ids...)."""
        if "baudrate" in kwargs:
            self.set_baudrate(int(kwargs["baudrate"]))
        if "l_line" in kwargs:
            self.set_l_line(bool(kwargs["l_line"]))

    def initialize_bus(self) -> None:
        """Run bus/session initialisation required by the adapter."""
        return None

    def send_frame(self, frame: CanFrame) -> None:
        """Write one raw CAN frame. Adapters without CAN must reject this."""
        raise UnsupportedOperationError(
            f"{self.name} does not expose raw CAN frames",
            context=self._error_context(),
        )

    def recv_frame(self, timeout: float = 0.5) -> Optional[CanFrame]:
        raise UnsupportedOperationError(
            f"{self.name} does not expose raw CAN frames",
            context=self._error_context(),
        )

    def set_can_ids(self, tx_id: int, rx_id: int, extended: bool = False) -> None:
        raise UnsupportedOperationError(
            f"{self.name} does not support CAN identifier configuration",
            context=self._error_context(),
        )

    def cancel(self) -> None:
        """Abort blocking reads/waits; the next I/O call raises OperationCancelled."""
        self.cancel_token.cancel()

    def reset_cancel(self) -> None:
        self.cancel_token._cancelled = False  # same object: wrapped transports share it

    def fast_init_pulse(self) -> None:
        """25 ms low / 25 ms high K-line wake-up pulse."""
        raise UnsupportedOperationError(
            "Fast init not supported on this transport", context=self._error_context()
        )

    def slow_init_5baud(self, target_addr: int) -> bytes:
        """5-baud address byte send; returns sync + keybytes [0x55, KW1, KW2]."""
        raise UnsupportedOperationError(
            "5-baud slow init not supported on this transport",
            context=self._error_context(),
        )

    def set_l_line(self, high: bool) -> None:
        """Optional L-line control via RTS or Pin 15."""
        return None

    def send_break(self, duration_ms: int = 25) -> None:
        """Send manual break condition on the K-line."""
        return None

    # -- introspection -----------------------------------------------------
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities()

    def get_capabilities(self) -> Dict[str, bool]:
        return self.capabilities().as_dict()

    def info(self) -> TransportInfo:
        return TransportInfo(
            name=self.name,
            port=self.port,
            baudrate=self.actual_baud,
            state=self.state,
            capabilities=self.capabilities(),
        )

    @classmethod
    def list_devices(cls) -> List[Dict[str, str]]:
        """Return discoverable devices without opening them."""
        return []

    def _error_context(self):
        from klinescan.core.errors import ErrorContext

        return ErrorContext(
            transport=self.name, port=self.port, adapter=self.info().adapter
        )
