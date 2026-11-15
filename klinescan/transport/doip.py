"""Diagnostics over IP (ISO 13400) for modern vehicles, via the optional ``doipclient``.

Facts used (python-doipclient docs, https://python-doipclient.readthedocs.io/):
TCP/UDP port 13400 (3496 with TLS), protocol version 2 (ISO 13400:2012) by default,
client logical address in 0x0E00..0x0FFF, routing activation sent on connect.
Vehicle side: OBD-II pins 3/11 RX, 12/13 TX, pin 8 activation (+5 V), or the
alternative 1/9 RX pair; BMW ENET cables use the first option.  A plain
100BASE-TX PC adapter is enough with an ENET-style cable; ECUs wired with
100BASE-T1 need a media converter.

Port strings:
    doip://                      discover the vehicle (UDP announcement / identification)
    doip://192.168.0.10          gateway IP, logical address from the target or discovery
    doip://192.168.0.10/0x1001   explicit ECU logical address
"""

from __future__ import annotations

from typing import Dict, Optional

from klinescan.core.errors import ConfigurationError, DeviceNotFoundError, ScanTimeout
from klinescan.core.models import EcuTarget
from klinescan.core.registry import TransportCapabilities, register_transport
from klinescan.transport.base import BaseTransport

DOIP_CAPABILITIES = TransportCapabilities(
    supports_can=False, supports_kline=False, supports_fast_init=False, supports_5baud=False,
    supports_raw_frames=False, supports_obd_passthrough=True, max_baudrate=100_000_000,
    description="DoIP (ISO 13400) over Ethernet - BMW ENET, VAG/Volvo/JLR DoIP gateways",
)
CLIENT_LOGICAL_ADDRESS = 0x0E00


def _parse_port(port: str) -> tuple[str, Optional[int]]:
    rest = port.split("://", 1)[1] if "://" in port else port
    host, _, logical = rest.partition("/")
    return host.strip(), (int(logical, 0) if logical.strip() else None)


@register_transport("doip", DOIP_CAPABILITIES)
class DoIPTransport(BaseTransport):
    name = "doip"
    supports_obd_passthrough = True

    def __init__(self, led_callback=None, client_factory=None):
        super().__init__(led_callback)
        self._client = None
        self._client_factory = client_factory
        self._host = ""
        self._logical: Optional[int] = None
        self.vin = ""

    def _doip(self):
        if self._client_factory is not None:
            return self._client_factory
        try:
            from doipclient import DoIPClient
        except ImportError as exc:
            raise ConfigurationError("DoIP needs the optional 'doipclient' package: pip install doipclient") from exc
        return DoIPClient

    def open(self, port: str, baudrate: int = 0) -> None:
        self.port = port
        self._host, self._logical = _parse_port(port)
        self._mark_open()

    def configure_target(self, target: EcuTarget) -> None:
        DoIPClient = self._doip()
        host = target.doip_address or self._host
        logical = target.doip_logical_address or self._logical
        if not host or logical is None:
            try:
                address, announcement = DoIPClient.get_entity()
            except Exception as exc:
                raise DeviceNotFoundError(f"no DoIP vehicle announced itself on the network: {exc}") from exc
            host = host or address[0]
            logical = logical if logical is not None else announcement.logical_address
            self.vin = getattr(announcement, "vin", "") or ""
        try:
            self._client = DoIPClient(host, logical, client_logical_address=CLIENT_LOGICAL_ADDRESS)
        except Exception as exc:
            raise DeviceNotFoundError(f"DoIP connect/routing activation to {host} failed: {exc}") from exc
        self._host, self._logical = host, logical

    def send_obd_payload(self, payload: bytes, timeout: float = 2.0) -> bytes:
        if self._client is None:
            raise ConfigurationError("DoIP session not configured")
        self._set_led("tx", True)
        try:
            self._client.send_diagnostic(bytearray(payload), timeout=timeout)
            response = self._client.receive_diagnostic(timeout=timeout)
        except TimeoutError as exc:
            raise ScanTimeout("no DoIP diagnostic response") from exc
        finally:
            self._set_led("tx", False)
        # UDS 0x78 "response pending": keep waiting for the final answer
        while response and len(response) >= 3 and response[0] == 0x7F and response[2] == 0x78:
            response = self._client.receive_diagnostic(timeout=max(timeout, 5.0))
        return bytes(response or b"")

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None
        self._mark_closed()

    def set_baudrate(self, baudrate: int) -> None:
        pass

    def flush(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self._pending = self.send_obd_payload(data)

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        data, self._pending = getattr(self, "_pending", b"")[:size], getattr(self, "_pending", b"")[size:]
        return data

    def capabilities(self) -> TransportCapabilities:
        return DOIP_CAPABILITIES

    def get_capabilities(self) -> Dict[str, bool]:
        return DOIP_CAPABILITIES.as_dict()
