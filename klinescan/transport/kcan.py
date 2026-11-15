"""
VAG K+CAN Commander 1.4 transport.

The adapter speaks a small binary command protocol at 833333 baud:

  0x50 0x00                 initialise 500 kbit/s (0x51 for 250 kbit/s)
  0x53 0x20 ...             set CAN acceptance filters
  0x60 len hi lo 00 00 ...  transmit one CAN frame (32-bit address field)
  0x70 len hi lo 00 00 ...  received CAN frame

The DTR/RTS pulse and CAN filter layout follow the documented adapter protocol.
"""

import time
from collections import deque
from typing import Deque, Optional

import serial

from klinescan.core.errors import BusError, DeviceNotFoundError
from klinescan.core.registry import register_transport
from klinescan.transport.base import BaseTransport, TransportCapabilities, TransportError


@register_transport("kcan", TransportCapabilities(
    supports_can=True, supports_kline=False, supports_raw_frames=True,
    supports_fast_init=False, supports_5baud=False,
    description="VAG K+CAN Commander 1.4 CAN adapter"))
class KCanTransport(BaseTransport):
    def __init__(self, led_callback=None):
        super().__init__(led_callback)
        self._ser: Optional[serial.Serial] = None
        self._tx_id = 0x7DF
        self._rx_id = 0
        self._rx_ring: Deque[int] = deque()
        self._bitrate = 500000

    def open(self, port: str, baudrate: int = 500000) -> None:
        self._bitrate = baudrate
        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=833333,
                timeout=0.2,
                write_timeout=0.5,
                xonxoff=False,
                rtscts=False,
            )
        except serial.SerialException as exc:
            raise DeviceNotFoundError(
                f"Unable to open K+CAN adapter {port}: {exc}"
            ) from exc

        # Turn off CAN mode, then run the hardware mode-switch pulse.
        self._ser.rts = False
        self._ser.dtr = False
        time.sleep(0.300)
        self._ser.dtr = True
        time.sleep(0.500)
        self._ser.dtr = False
        self._ser.rts = True

        self._send_init()
        self._configure_filters(self._tx_id)
        self.flush()

    def _send_init(self) -> None:
        if not self._ser:
            return
        command = 0x50 if self._bitrate >= 500000 else 0x51
        self._ser.write(bytes([command, 0x00]))
        time.sleep(0.010)

    def _configure_filters(self, can_out_id: int) -> None:
        if not self._ser:
            return
        self._tx_id = can_out_id
        hi = (can_out_id >> 3) & 0xFF
        lo = (can_out_id & 0x07) << 2
        packet = bytearray([0x53, 0x20, hi, lo, 0x00, 0x00])
        packet.extend([0xFF, 0x1C, 0x00, 0x00] * 3)
        packet.extend([0x00, 0x00, 0x00, 0x00])  # accept all incoming IDs
        packet.extend([hi, lo, 0x00, 0x00])
        packet.extend([0xFF, 0x1C, 0x00, 0xFF])
        packet.extend([0xFF, 0x1C, 0x00, 0x00])
        self._ser.write(packet)
        self._read_message(timeout=0.2)

    def initialize_bus(self) -> None:
        self._send_init()

    def set_can_ids(self, tx_id: int, rx_id: int, extended: bool = False) -> None:
        if extended:
            raise BusError("K+CAN Commander supports 11-bit identifiers only")
        self._tx_id, self._rx_id = tx_id, rx_id
        self._configure_filters(tx_id)

    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.rts = False
            self._ser.dtr = False
            time.sleep(0.300)
            self._ser.close()
        self._ser = None
        self._rx_ring.clear()

    def set_baudrate(self, baudrate: int) -> None:
        self._bitrate = baudrate
        self._send_init()

    def flush(self) -> None:
        self._rx_ring.clear()
        if self._ser:
            self._ser.reset_input_buffer()

    def write(self, data: bytes) -> None:
        self._send(self._tx_id, data)

    def _send(self, can_id: int, data: bytes) -> None:
        if not self._ser:
            raise TransportError("K+CAN not open")
        if len(data) > 0xFC:
            raise BusError("K+CAN frame too long")
        self._set_led("tx", True)
        hi = (can_id >> 3) & 0xFF
        lo = (can_id & 0x07) << 2
        packet = bytes([0x60, len(data) + 4, hi, lo, 0x00, 0x00]) + data
        self._ser.write(packet)
        self._read_message(timeout=0.2)  # 0x60 ACK, ignore
        self._set_led("tx", False)

    def send_frame(self, frame) -> None:
        if frame.extended:
            raise BusError("K+CAN Commander supports 11-bit identifiers only")
        self._send(frame.arbitration_id, bytes(frame.data))

    def recv_frame(self, timeout: float = 0.5):
        from klinescan.transport.base import CanFrame

        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline and not self.cancel_token.cancelled:
            message = self._read_message(timeout=max(0.01, deadline - time.perf_counter()))
            if message is None:
                continue
            msg_type, payload = message
            if msg_type != 0x70 or len(payload) <= 4:
                continue
            can_id = (payload[0] << 3) | (payload[1] >> 2)  # same packing as the TX address field
            if self._rx_id and can_id != self._rx_id:
                continue
            self._set_led("rx", True)
            self._set_led("rx", False)
            return CanFrame(can_id, payload[4:])
        return None

    def _read_exact(self, size: int, timeout: float) -> bytes:
        if not self._ser:
            return b""
        out = bytearray()
        while self._rx_ring and len(out) < size:
            out.append(self._rx_ring.popleft())
        deadline = time.perf_counter() + timeout
        while len(out) < size and time.perf_counter() < deadline:
            chunk = self._ser.read(size - len(out))
            if chunk:
                out.extend(chunk)
        return bytes(out)

    def _read_message(self, timeout: float = 0.5):
        header = self._read_exact(2, timeout)
        if len(header) < 2:
            return None
        msg_type, length = header[0], header[1]
        payload = self._read_exact(length, timeout) if length else b""
        return msg_type, payload

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        if not self._ser:
            return b""
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            message = self._read_message(timeout=max(0.01, deadline - time.perf_counter()))
            if message is None:
                continue
            msg_type, payload = message
            if msg_type == 0x70:
                if len(payload) <= 4:
                    continue
                self._set_led("rx", True)
                self._set_led("rx", False)
                return payload[4:]
            # 0x50/0x53/0x60 are command acknowledgements.
        return b""

    def get_capabilities(self) -> dict:
        return {
            "can": True,
            "kline": False,
            "fast_init": False,
            "slow_init": False,
            "l_line": False,
            "custom_baud": False,
        }
