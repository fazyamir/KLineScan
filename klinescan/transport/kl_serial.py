import ctypes
import os
import sys
import time
import serial
from klinescan.core import kline_timing as KT
from klinescan.core.registry import register_transport
from klinescan.transport.base import BaseTransport, TransportCapabilities, TransportError, check_cancelled
from klinescan.transport.kline_echo import EchoCanceller

KL_CAPABILITIES = TransportCapabilities(
    supports_can=False,
    supports_kline=True,
    supports_raw_frames=False,
    supports_l_line=True,
    supports_custom_baud=True,
    max_baudrate=115200,
    description="KKL / FTDI virtual COM K-line adapter",
)


def _enable_windows_high_res_timer(enable: bool) -> None:
    if sys.platform == "win32":
        try:
            winmm = ctypes.WinDLL("winmm")
            if enable:
                winmm.timeBeginPeriod(1)
            else:
                winmm.timeEndPeriod(1)
        except OSError:
            pass


def _set_ftdi_latency(port: str, value_ms: int = 1) -> bool:
    """FTDI USB-serial chips buffer reads for 16 ms by default; K-line needs ~1 ms.

    Linux exposes the latency timer at /sys/bus/usb-serial/devices/<tty>/latency_timer
    (valid 1..255 ms; https://granitedevices.com/wiki/FTDI_Linux_USB_latency).  Writing it
    needs permission (udev rule or root); failure is reported, not fatal.  CH340/CP210x/PL2303
    adapters have no equivalent setting.
    """
    if not sys.platform.startswith("linux"):
        return False
    tty = os.path.basename(os.path.realpath(port))
    path = f"/sys/bus/usb-serial/devices/{tty}/latency_timer"
    try:
        with open(path, "w") as fh:
            fh.write(str(value_ms))
        return True
    except OSError:
        return False


def _precise_sleep(duration: float) -> None:
    """Hybrid sleep + busy-wait for accurate 25ms fast-init timing."""
    deadline = time.perf_counter() + duration
    if duration > 0.004:
        time.sleep(duration - 0.003)
    while time.perf_counter() < deadline:
        pass


@register_transport("kl_serial", KL_CAPABILITIES)
@register_transport("kline", KL_CAPABILITIES)
class KLSerialTransport(BaseTransport):
    """
    Dumb K-Line (KKL / FTDI) serial transport.
    Handles TX-RX hardware echo cancellation, 5-baud slow init, and 25ms fast init.
    """

    def __init__(self, led_callback=None, echo_enabled: bool = True, echo_mode: str = ""):
        super().__init__(led_callback)
        self._ser: serial.Serial | None = None
        self.echo = EchoCanceller(echo_mode or ("auto" if echo_enabled else "off"))
        self._rx_ring = bytearray()
        self.inter_byte_ms = KT.P4_DEFAULT_FAST_INIT
        self.latency_tuned = False

    # The UI's echo toggle sets ``echo_enabled``: on = auto-detect, off = cable cancels echo.
    @property
    def echo_enabled(self) -> bool:
        return self.echo.mode != "off"

    @echo_enabled.setter
    def echo_enabled(self, value: bool) -> None:
        if hasattr(self, "echo"):
            self.echo.set_mode("auto" if value else "off")

    def open(self, port: str, baudrate: int) -> None:
        self.requested_baud = self.actual_baud = baudrate
        try:
            _enable_windows_high_res_timer(True)
            self._ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.3,
                write_timeout=0.5,
            )
            self._ser.dtr = False
            self._ser.rts = False
            self.port = port
            self.latency_tuned = _set_ftdi_latency(port)
            self.flush()
        except serial.SerialException as exc:
            raise TransportError(f"Failed to open {port}: {exc}") from exc

    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()
        self._ser = None
        self._rx_ring.clear()
        _enable_windows_high_res_timer(False)

    def set_baudrate(self, baudrate: int) -> None:
        if not self._ser:
            return
        self._ser.baudrate = baudrate
        self.requested_baud = self.actual_baud = baudrate
        self.flush()

    def flush(self) -> None:
        self._rx_ring.clear()
        if self._ser:
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()

    def set_l_line(self, high: bool) -> None:
        if self._ser:
            self._ser.rts = high

    def fast_init_pulse(self) -> None:
        if not self._ser:
            raise TransportError("Port not open")
        self.flush()
        # Idle >= W5 (300 ms), then TiniL low (25 ms) and the rest of TWup (50 ms) high.
        self._ser.break_condition = False
        _precise_sleep(KT.T_IDLE / 1000)
        self._set_led("break", True)
        self._ser.break_condition = True
        _precise_sleep(KT.T_INIL / 1000)
        self._ser.break_condition = False
        self._set_led("break", False)
        _precise_sleep((KT.T_WUP - KT.T_INIL) / 1000)
        # Flush break-induced 0x00 glitch byte
        self._ser.reset_input_buffer()

    def slow_init_5baud(self, target_addr: int, parity: str = "odd") -> bytes:
        """Bit-bang the 5-baud address byte and return ``[0x55, KW1, KW2]``.

        ISO 9141-2 frames the address as start bit, 7 data bits + odd parity
        (LSB first), stop bit; some manufacturer protocols send 8 data bits
        without parity (``parity="none"``).  See ``klinescan.core.kline_timing``.
        """
        if not self._ser:
            raise TransportError("Port not open")
        wire = KT.odd_parity_address(target_addr) if parity == "odd" else target_addr & 0xFF
        bit = KT.FIVE_BAUD_BIT_MS / 1000
        self.flush()
        self._ser.break_condition = False
        _precise_sleep(KT.W5_MIN / 1000)

        self._set_led("break", True)
        self._ser.break_condition = True          # start bit (0)
        _precise_sleep(bit)
        for bit_idx in range(8):                  # LSB first
            level = (wire >> bit_idx) & 1
            self._ser.break_condition = (level == 0)
            self._set_led("break", level == 0)
            _precise_sleep(bit)
        self._ser.break_condition = False          # stop bit (1)
        self._set_led("break", False)
        _precise_sleep(bit)
        # The ECU answers 0x55 after W1 (60..300 ms), KW1 after W2, KW2 after W3.
        # Do not flush: the sync byte may already be in the buffer.
        timeout = (KT.W1_MAX + KT.W2_MAX + KT.W3_MAX) / 1000 + 0.3
        self.inter_byte_ms = KT.P4_DEFAULT_SLOW_INIT
        return self.read(3, timeout=timeout)

    def write(self, data: bytes) -> None:
        if not self._ser:
            raise TransportError("Port not open")
        self._set_led("tx", True)
        if self.inter_byte_ms > 0 and len(data) > 1:
            for index, byte in enumerate(data):   # tester inter-byte time P4
                self._ser.write(bytes([byte]))
                self._ser.flush()
                if index < len(data) - 1:
                    _precise_sleep(self.inter_byte_ms / 1000)
        else:
            self._ser.write(data)
            self._ser.flush()

        # K-line ties TX and RX together: drop the echo, keep any ECU bytes.
        if data:
            self._rx_ring.extend(self.echo.consume(bytes(data), self._read_one, self._ser.baudrate))
        self._set_led("tx", False)

    def _read_one(self, timeout: float) -> bytes:
        old = self._ser.timeout
        self._ser.timeout = timeout
        try:
            return self._ser.read(1)
        finally:
            self._ser.timeout = old

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        if not self._ser:
            raise TransportError("Port not open")
        out = bytearray()
        while self._rx_ring and len(out) < size:
            out.append(self._rx_ring.pop(0))
        if len(out) < size:
            # Read in short slices so a cancel request (window close, Stop) is honoured quickly.
            deadline = time.perf_counter() + timeout
            old_timeout = self._ser.timeout
            try:
                while len(out) < size:
                    check_cancelled(self)
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    self._ser.timeout = min(remaining, 0.1)
                    out.extend(self._ser.read(size - len(out)))
            finally:
                self._ser.timeout = old_timeout
        if out:
            self._set_led("rx", True)
            self._set_led("rx", False)
        return bytes(out)

    def _read_exact(self, size: int, timeout: float) -> bytes:
        deadline = time.perf_counter() + timeout
        buf = bytearray()
        while len(buf) < size and time.perf_counter() < deadline:
            chunk = self._ser.read(size - len(buf))
            if chunk:
                buf.extend(chunk)
        return bytes(buf)
