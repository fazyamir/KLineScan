"""
Direct FTDI D2XX transport with precise line handling.

Matches the reference implementation where it matters:
  * FTDI VID/PID 0x0403/0x6001 selection
  * latency timer 2 ms, USB read timeout 100 ms
  * no flow control, DTR/RTS asserted
  * async bitbang slow-init (mask 0x15, TX bit 0, RTS bit 2, DTR bit 4)
    at 8192 baud, one line state per 5-baud bit slot
  * ring-buffered echo cancellation so ECU replies are never discarded

Falls back to the pyserial K-line transport when D2XX is unavailable.
"""

import ctypes
import sys
import time
from typing import Deque, Dict, List, Optional

from collections import deque

from klinescan.core.errors import ConfigurationError, DeviceNotFoundError
from klinescan.core.registry import register_transport
from klinescan.transport.base import BaseTransport, TransportCapabilities, TransportError
from klinescan.transport.kl_serial import KLSerialTransport, _enable_windows_high_res_timer
from klinescan.transport.kline_echo import EchoCanceller

FT_OPEN_BY_SERIAL_NUMBER = 1
FT_OPEN_BY_DESCRIPTION = 2
FT_BITMODE_RESET = 0x00
FT_BITMODE_ASYNC = 0x01
BITBANG_MASK = 0x15
SLOW_INIT_BAUD = 8192


class _FTDeviceInfoNode(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_ulong),
        ("type", ctypes.c_ulong),
        ("id", ctypes.c_ulong),
        ("locid", ctypes.c_ulong),
        ("serial", ctypes.c_char * 16),
        ("description", ctypes.c_char * 64),
        ("ftHandle", ctypes.c_void_p),
    ]


def _load_d2xx():
    candidates = []
    if sys.platform == "win32":
        candidates = ["ftd2xx.dll", "ftd2xx64.dll"]
    elif sys.platform == "darwin":
        candidates = ["libftd2xx.dylib", "/usr/local/lib/libftd2xx.dylib"]
    else:
        candidates = ["libftd2xx.so", "libftd2xx.so.1"]
    for name in candidates:
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


def _five_baud_bits(address: int, parity: str = "odd") -> List[int]:
    """Start bit, 8 bits LSB-first, stop bit.

    ``parity="odd"``: 7 data bits + odd parity (ISO 9141-2, see
    klinescan.core.kline_timing); ``"none"``: the raw 8-bit address.
    """
    from klinescan.core.kline_timing import odd_parity_address

    wire = odd_parity_address(address) if parity == "odd" else address & 0xFF
    return [0] + [(wire >> index) & 1 for index in range(8)] + [1]


@register_transport("ftdi", TransportCapabilities(
    supports_can=False, supports_kline=True, supports_raw_frames=False,
    supports_l_line=True, supports_custom_baud=True,
    description="Direct FTDI D2XX K-line adapter"))
@register_transport("ftdi_direct", TransportCapabilities(
    supports_can=False, supports_kline=True, supports_raw_frames=False,
    supports_l_line=True, supports_custom_baud=True,
    description="Direct FTDI D2XX K-line adapter"))
class FtdiDirectTransport(BaseTransport):
    def __init__(self, led_callback=None, echo_enabled: bool = True, echo_mode: str = ""):
        super().__init__(led_callback)
        self.echo = EchoCanceller(echo_mode or ("auto" if echo_enabled else "off"))
        self._ft_handle = None
        self._d2xx = _load_d2xx()
        self._fallback_serial: Optional[KLSerialTransport] = None
        self._is_d2xx_active = False
        self._rx_ring: Deque[int] = deque()
        self._port = ""

    @property
    def echo_enabled(self) -> bool:
        return self.echo.mode != "off"

    @echo_enabled.setter
    def echo_enabled(self, value: bool) -> None:
        if hasattr(self, "echo"):
            self.echo.set_mode("auto" if value else "off")
        if getattr(self, "_fallback_serial", None):
            self._fallback_serial.echo_enabled = value

    def _open_d2xx(self, port: str) -> bool:
        if not self._d2xx:
            return False
        handle = ctypes.c_void_p()
        key = port.strip()

        if key.lower().startswith("index:"):
            result = self._d2xx.FT_Open(int(key.split(":", 1)[1]), ctypes.byref(handle))
        elif key.isdigit():
            result = self._d2xx.FT_Open(int(key), ctypes.byref(handle))
        else:
            result = 1
            serial, description = self._find_device(key)
            if serial:
                result = self._d2xx.FT_OpenEx(
                    serial.encode(), FT_OPEN_BY_SERIAL_NUMBER, ctypes.byref(handle)
                )
            elif description:
                result = self._d2xx.FT_OpenEx(
                    description.encode(), FT_OPEN_BY_DESCRIPTION, ctypes.byref(handle)
                )

        if result != 0:
            return False

        self._ft_handle = handle
        try:
            self._d2xx.FT_SetLatencyTimer(self._ft_handle, 2)
            self._d2xx.FT_SetFlowControl(self._ft_handle, 0, 0, 0)
            self._d2xx.FT_SetDataCharacteristics(self._ft_handle, 8, 0, 0)
            self._d2xx.FT_SetTimeouts(self._ft_handle, 100, 100)
            self._d2xx.FT_SetDtr(self._ft_handle)
            self._d2xx.FT_SetRts(self._ft_handle)
            self._d2xx.FT_SetBaudRate(self._ft_handle, self.requested_baud)
        except Exception:
            try:
                self._d2xx.FT_Close(self._ft_handle)
            except Exception:
                pass
            self._ft_handle = None
            return False

        self._is_d2xx_active = True
        return True

    def _find_device(self, text: str):
        try:
            count = ctypes.c_ulong(0)
            if self._d2xx.FT_CreateDeviceInfoList(ctypes.byref(count)) != 0:
                return "", ""
            nodes = (_FTDeviceInfoNode * count.value)()
            if self._d2xx.FT_GetDeviceInfoList(nodes, ctypes.byref(count)) != 0:
                return "", ""
        except Exception:
            return "", ""

        needle = text.upper().replace("COM", "")
        for node in nodes:
            serial = node.serial.decode(errors="ignore").strip("\x00 ")
            description = node.description.decode(errors="ignore").strip("\x00 ")
            if needle and (needle in serial.upper() or needle in description.upper()):
                return serial, description
        return "", ""

    def open(self, port: str, baudrate: int) -> None:
        self.requested_baud = baudrate
        self.actual_baud = baudrate
        self._port = port

        if self._open_d2xx(port):
            _enable_windows_high_res_timer(True)  # 1 ms sleep resolution for init timing
            self._set_led("iface", True)
            return

        # Fallback to a high-precision pyserial K-line transport.
        self._fallback_serial = KLSerialTransport(self._led_cb, echo_mode=self.echo.mode)
        self._fallback_serial.cancel_token = self.cancel_token
        try:
            self._fallback_serial.open(port, baudrate)
        except TransportError as exc:
            raise DeviceNotFoundError(
                f"No FTDI device matching '{port}' and serial fallback failed: {exc}"
            ) from exc
        self._set_led("iface", True)

    def close(self) -> None:
        if self._is_d2xx_active and self._d2xx and self._ft_handle:
            try:
                self._d2xx.FT_SetBitMode(self._ft_handle, 0xFF, FT_BITMODE_RESET)
            except Exception:
                pass
            try:
                self._d2xx.FT_Close(self._ft_handle)
            except Exception:
                pass
            self._ft_handle = None
            self._is_d2xx_active = False
            _enable_windows_high_res_timer(False)

        if self._fallback_serial:
            self._fallback_serial.close()
            self._fallback_serial = None

        self._rx_ring.clear()
        self._set_led("iface", False)
        self._set_led("connected", False)

    def set_baudrate(self, baudrate: int) -> None:
        self.requested_baud = baudrate
        self.actual_baud = baudrate
        if self._is_d2xx_active and self._d2xx and self._ft_handle:
            self._d2xx.FT_SetBaudRate(self._ft_handle, baudrate)
        elif self._fallback_serial:
            self._fallback_serial.set_baudrate(baudrate)

    def flush(self) -> None:
        self._rx_ring.clear()
        if self._is_d2xx_active and self._d2xx and self._ft_handle:
            self._d2xx.FT_Purge(self._ft_handle, 3)
        elif self._fallback_serial:
            self._fallback_serial.flush()

    def fast_init_pulse(self) -> None:
        if not (self._is_d2xx_active and self._d2xx and self._ft_handle):
            if self._fallback_serial:
                self._fallback_serial.fast_init_pulse()
            return
        from klinescan.core import kline_timing as KT

        time.sleep(KT.T_IDLE / 1000)              # bus idle >= W5
        self._set_led("break", True)
        self._d2xx.FT_SetBreakOn(self._ft_handle)
        time.sleep(KT.T_INIL / 1000)              # TiniL 25 ms low
        self._d2xx.FT_SetBreakOff(self._ft_handle)
        self._set_led("break", False)
        time.sleep((KT.T_WUP - KT.T_INIL) / 1000)  # rest of TWup (50 ms)
        self.flush()

    def slow_init_5baud(self, target_addr: int, parity: str = "odd") -> bytes:
        if not (self._is_d2xx_active and self._d2xx and self._ft_handle):
            if self._fallback_serial:
                return self._fallback_serial.slow_init_5baud(target_addr, parity=parity)
            return b""

        self.flush()
        self._d2xx.FT_SetBitMode(self._ft_handle, BITBANG_MASK, FT_BITMODE_ASYNC)
        self._d2xx.FT_SetBaudRate(self._ft_handle, SLOW_INIT_BAUD)
        self._set_led("break", True)
        for bit in _five_baud_bits(target_addr, parity):
            value = ctypes.c_ubyte(0x01 if bit else 0x00)
            written = ctypes.c_ulong(0)
            self._d2xx.FT_Write(self._ft_handle, ctypes.byref(value), 1, ctypes.byref(written))
            time.sleep(0.200)
        self._set_led("break", False)

        self._d2xx.FT_SetBitMode(self._ft_handle, 0xFF, FT_BITMODE_RESET)
        self._d2xx.FT_SetBaudRate(self._ft_handle, self.requested_baud)
        # No purge here: the ECU sends 0x55 within W1 (60..300 ms) and it must not be lost.
        return self.read(3, timeout=0.8)

    def write(self, data: bytes) -> None:
        if self._is_d2xx_active and self._d2xx and self._ft_handle:
            self._set_led("tx", True)
            written = ctypes.c_ulong(0)
            self._d2xx.FT_Write(self._ft_handle, data, len(data), ctypes.byref(written))
            if data:
                self._rx_ring.extend(self.echo.consume(
                    bytes(data), lambda t: self._read_serial_bytes(1, t), self.requested_baud))
            self._set_led("tx", False)
        elif self._fallback_serial:
            self._fallback_serial.write(data)

    def _read_serial_bytes(self, size: int, timeout: float) -> bytes:
        buf = (ctypes.c_char * size)()
        read_bytes = ctypes.c_ulong(0)
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline and not self.cancel_token.cancelled:
            rx_q = ctypes.c_ulong(0)
            self._d2xx.FT_GetQueueStatus(self._ft_handle, ctypes.byref(rx_q))
            if rx_q.value:
                to_read = min(size, rx_q.value)
                self._d2xx.FT_Read(
                    self._ft_handle, buf, to_read, ctypes.byref(read_bytes)
                )
                return buf.raw[: read_bytes.value]
            time.sleep(0.002)
        return b""

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        if self._is_d2xx_active and self._d2xx and self._ft_handle:
            out = bytearray()
            while self._rx_ring and len(out) < size:
                out.append(self._rx_ring.popleft())
            if len(out) < size:
                out.extend(self._read_serial_bytes(size - len(out), timeout))
            if out:
                self._set_led("rx", True)
                self._set_led("rx", False)
            return bytes(out)
        elif self._fallback_serial:
            return self._fallback_serial.read(size, timeout)
        return b""

    def get_capabilities(self) -> Dict[str, bool]:
        return {
            "can": False,
            "kline": True,
            "fast_init": True,
            "slow_init": True,
            "l_line": True,
            "custom_baud": True,
        }
