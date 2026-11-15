"""
Linux SocketCAN transport (python-can).

Exposes both the byte-stream contract used by legacy protocols and the raw
frame contract used by the ISO-TP channel.
"""

from typing import Optional

from klinescan.core.errors import BusError, DeviceNotFoundError
from klinescan.core.registry import register_transport
from klinescan.transport.base import BaseTransport, CanFrame, TransportCapabilities, TransportError


@register_transport("socketcan", TransportCapabilities(
    supports_can=True, supports_kline=False, supports_raw_frames=True,
    supports_fast_init=False, supports_5baud=False,
    description="Linux SocketCAN interface"))
class SocketCanTransport(BaseTransport):
    def __init__(self, led_callback=None):
        super().__init__(led_callback)
        self._bus = None
        self._tx_id = 0x7DF
        self._rx_id = 0x7E8
        self._extended = False
        self._is_open = False

    def open(self, port: str, baudrate: int = 500000) -> None:
        self.requested_baud = baudrate
        self.actual_baud = baudrate
        if not port:
            raise DeviceNotFoundError("SocketCAN requires an interface name such as can0")

        interface = "virtual" if port.startswith("virtual:") else "socketcan"
        channel = port.split(":", 1)[1] if interface == "virtual" else port
        try:
            import can

            self._bus = can.interface.Bus(channel=channel, interface=interface)
        except Exception as exc:
            raise DeviceNotFoundError(
                f"Failed to open CAN interface '{channel}' ({interface}): {exc}"
            ) from exc

        self._is_open = True
        self._set_led("iface", True)
        self._set_led("can", True)

    def close(self) -> None:
        if self._bus:
            try:
                self._bus.shutdown()
            except Exception:
                pass
        self._bus = None
        self._is_open = False
        self._set_led("iface", False)
        self._set_led("can", False)
        self._set_led("connected", False)

    def set_baudrate(self, baudrate: int) -> None:
        # Bitrate is configured on the netdev (ip link set can0 bitrate ...).
        self.requested_baud = baudrate

    def flush(self) -> None:
        if not self._bus:
            return
        try:
            while self._bus.recv(timeout=0) is not None:
                pass
        except Exception:
            pass

    def set_can_ids(self, tx_id: int, rx_id: int, extended: bool = False) -> None:
        self._tx_id = tx_id
        self._rx_id = rx_id
        self._extended = extended
        if self._bus is not None:
            # Kernel-side acceptance filter, so frames from other ECUs never reach us.
            filters = None
            if rx_id:
                filters = [{"can_id": rx_id, "can_mask": 0x1FFFFFFF if extended else 0x7FF,
                            "extended": extended}]
            try:
                self._bus.set_filters(filters)
            except Exception as exc:
                raise BusError(f"could not set CAN filter: {exc}") from exc

    def send_frame(self, frame: CanFrame) -> None:
        if not self._bus or not self._is_open:
            raise TransportError("CAN interface not open")
        import can

        message = can.Message(
            arbitration_id=frame.arbitration_id,
            data=frame.data,
            is_extended_id=frame.extended,
        )
        self._set_led("tx", True)
        try:
            self._bus.send(message, timeout=0.5)
        except Exception as exc:
            raise BusError(f"CAN send failed: {exc}") from exc
        finally:
            self._set_led("tx", False)

    def recv_frame(self, timeout: float = 0.5) -> Optional[CanFrame]:
        if not self._bus or not self._is_open:
            return None
        import time

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.cancel_token.cancelled:
                return None
            msg = self._bus.recv(timeout=remaining)
            if msg is None:
                return None
            if msg.arbitration_id == self._tx_id and bool(msg.is_extended_id) == self._extended:
                continue  # our own frame, echoed by the controller
            break
        return CanFrame(
            arbitration_id=msg.arbitration_id,
            data=bytes(msg.data),
            extended=bool(msg.is_extended_id),
            timestamp=float(msg.timestamp),
            channel=str(getattr(msg, "channel", "") or ""),
        )

    def write(self, data: bytes) -> None:
        self.send_frame(CanFrame(self._tx_id, data, extended=self._extended))

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        frame = self.recv_frame(timeout)
        while frame is not None and self._rx_id and frame.arbitration_id != self._rx_id:
            frame = self.recv_frame(timeout)
        if frame is None:
            return b""
        self._set_led("rx", True)
        self._set_led("rx", False)
        return frame.data[:size]

    def get_capabilities(self) -> dict:
        return {
            "can": True,
            "kline": False,
            "fast_init": False,
            "slow_init": False,
            "l_line": False,
            "custom_baud": False,
        }
