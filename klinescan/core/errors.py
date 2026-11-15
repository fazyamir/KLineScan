"""
Structured KLineScan error model.

Every failure that can reach the UI or the headless API is classified here so
callers can react consistently instead of matching ad-hoc strings:

    missing device, permission failure, electrical/bus fault, timeout,
    malformed frame, ECU negative response, unsupported service, cancellation.

Nothing in this module fabricates diagnostic data.  A failure is always a
failure, never a synthetic success.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class ErrorClass(str, Enum):
    DEVICE_MISSING = "device_missing"
    PERMISSION = "permission"
    BUS_FAULT = "bus_fault"
    TIMEOUT = "timeout"
    MALFORMED_FRAME = "malformed_frame"
    NEGATIVE_RESPONSE = "negative_response"
    UNSUPPORTED = "unsupported"
    CANCELLED = "cancelled"
    PROTOCOL = "protocol"
    CONFIGURATION = "configuration"
    ADAPTER = "adapter"
    SAFETY = "safety_gate"
    INTERNAL = "internal"


@dataclass
class ErrorContext:
    transport: str = ""
    protocol: str = ""
    adapter: str = ""
    port: str = ""
    service: Optional[int] = None
    raw: bytes = b""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "transport": self.transport,
            "protocol": self.protocol,
            "adapter": self.adapter,
            "port": self.port,
            "service": self.service,
            "raw_hex": self.raw.hex(),
        }


class KLineScanError(Exception):
    """Base class for all classified KLineScan failures."""

    error_class: ErrorClass = ErrorClass.INTERNAL
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        context: Optional[ErrorContext] = None,
        cause: Optional[BaseException] = None,
    ):
        super().__init__(message)
        self.message = message
        self.context = context or ErrorContext()
        self.cause = cause

    def __str__(self) -> str:
        if self.context.transport or self.context.port:
            where = " / ".join(
                part for part in (self.context.transport, self.context.port) if part
            )
            return f"{self.message} ({where})"
        return self.message

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.__class__.__name__,
            "class": self.error_class.value,
            "message": self.message,
            "retryable": self.retryable,
            "context": self.context.as_dict(),
        }


class ConfigurationError(KLineScanError):
    error_class = ErrorClass.CONFIGURATION


class DeviceNotFoundError(KLineScanError):
    error_class = ErrorClass.DEVICE_MISSING


class PermissionDeniedError(KLineScanError):
    error_class = ErrorClass.PERMISSION


class BusError(KLineScanError):
    error_class = ErrorClass.BUS_FAULT
    retryable = True


class ScanTimeout(KLineScanError):
    error_class = ErrorClass.TIMEOUT
    retryable = True


class MalformedFrameError(KLineScanError):
    error_class = ErrorClass.MALFORMED_FRAME


class NegativeResponseError(KLineScanError):
    error_class = ErrorClass.NEGATIVE_RESPONSE

    def __init__(
        self,
        service: int,
        nrc: int,
        message: str = "",
        *,
        context: Optional[ErrorContext] = None,
    ):
        self.service = service
        self.nrc = nrc
        ctx = context or ErrorContext()
        ctx.service = service
        text = message or f"ECU rejected service 0x{service:02X} with NRC 0x{nrc:02X}"
        super().__init__(text, context=ctx)

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["nrc"] = self.nrc
        return data


class UnsupportedOperationError(KLineScanError):
    error_class = ErrorClass.UNSUPPORTED


class OperationCancelled(KLineScanError):
    error_class = ErrorClass.CANCELLED


class ProtocolError(KLineScanError):
    error_class = ErrorClass.PROTOCOL


class AdapterError(KLineScanError):
    error_class = ErrorClass.ADAPTER
    retryable = True


def classify_exception(exc: BaseException) -> KLineScanError:
    """Best-effort conversion of third-party exceptions to KLineScanError."""
    if isinstance(exc, KLineScanError):
        return exc

    text = str(exc).lower()
    if isinstance(exc, PermissionError) or "permission denied" in text:
        return PermissionDeniedError(str(exc), cause=exc)
    if isinstance(exc, FileNotFoundError) or "no such" in text or "not found" in text:
        return DeviceNotFoundError(str(exc), cause=exc)
    if isinstance(exc, TimeoutError) or "timed out" in text or "timeout" in text:
        return ScanTimeout(str(exc), cause=exc)
    return KLineScanError(str(exc), cause=exc)


class SafetyGateError(KLineScanError):
    """A write operation was refused because the target is not verified for it."""

    error_class = ErrorClass.SAFETY
