"""
SAE J2534 PassThru Transport:
Windows ctypes wrapper for PassThruOpen, PassThruConnect, PassThruIoctl,
PassThruReadMsgs, and PassThruWriteMsgs for Tactrix, Mongoose, Scanmatik, VCX.
"""

import ctypes
from ctypes import byref, c_char_p, c_ulong, c_void_p, POINTER, Structure
import sys
import time
from typing import Dict, Optional
from klinescan.core.errors import DeviceNotFoundError
from klinescan.core.registry import register_transport
from klinescan.transport.base import BaseTransport, CanFrame, TransportCapabilities, TransportError

# J2534 Constants
J2534_ISO9141 = 0x03
J2534_ISO14230 = 0x04
J2534_CAN = 0x05
J2534_ISO15765 = 0x06

FIVE_BAUD_INIT = 0x04
FAST_INIT = 0x05
CLEAR_TX_BUFFER = 0x07
CLEAR_RX_BUFFER = 0x08
CLEAR_MSG_FILTERS = 0x0A
READ_VBATT = 0x0B

# SAE J2534-1 (04.04) flags and filter types
CAN_29BIT_ID = 0x00000100      # TxFlags / Connect flags / RxStatus
CAN_ID_BOTH = 0x00000800       # Connect flag: accept 11- and 29-bit frames
TX_MSG_TYPE = 0x00000001       # RxStatus: loop-back of a message we transmitted
PASS_FILTER = 0x00000001
ERR_TIMEOUT = 0x09
ERR_BUFFER_EMPTY = 0x10


class PASSTHRU_MSG(Structure):
    _fields_ = [
        ("ProtocolID", c_ulong),
        ("RxStatus", c_ulong),
        ("TxFlags", c_ulong),
        ("Timestamp", c_ulong),
        ("DataSize", c_ulong),
        ("ExtraDataIndex", c_ulong),
        ("Data", ctypes.c_ubyte * 4128),
    ]


@register_transport("j2534", TransportCapabilities(
    supports_can=True, supports_kline=True, supports_j1850_pwm=True,
    supports_j1850_vpw=True, supports_raw_frames=True, supports_l_line=False,
    supports_custom_baud=True, description="SAE J2534 PassThru device"))
class J2534Transport(BaseTransport):
    def __init__(self, led_callback=None):
        super().__init__(led_callback)
        self._dll_path: str = ""
        self._dll = None
        self._dev_id = c_ulong(0)
        self._channel_id = c_ulong(0)
        self._is_open = False
        self._protocol = J2534_CAN
        self._tx_id = 0x7DF
        self._rx_id = 0x7E8
        self._extended = False

    def open(self, port: str, baudrate: int) -> None:
        self.requested_baud = baudrate
        self.actual_baud = baudrate
        self._dll_path = port.replace("J2534:", "") if port.startswith("J2534:") else port

        if sys.platform != "win32":
            raise TransportError("J2534 PassThru is only supported on Windows.")

        # Attempt to find DLL path in registry if a key name was passed
        if not self._dll_path.lower().endswith(".dll"):
            dll_resolved = self._lookup_dll_in_registry(self._dll_path)
            if dll_resolved:
                self._dll_path = dll_resolved

        try:
            self._dll = ctypes.WinDLL(self._dll_path)
        except OSError as exc:
            raise TransportError(f"Failed to load J2534 DLL '{self._dll_path}': {exc}") from exc

        # Open Device
        res = self._dll.PassThruOpen(None, byref(self._dev_id))
        if res != 0:
            raise TransportError(f"PassThruOpen failed with error code 0x{res:08X}")

        # Connect with a conservative default.  Protocols that know their
        # target call set_protocol()/set_can_ids() before opening traffic.
        self._protocol = J2534_CAN if baudrate >= 125000 else J2534_ISO14230
        flags = CAN_ID_BOTH if self._protocol == J2534_CAN else 0
        res = self._dll.PassThruConnect(
            self._dev_id, self._protocol, flags, baudrate, byref(self._channel_id)
        )
        if res != 0:
            self._dll.PassThruClose(self._dev_id)
            raise TransportError(f"PassThruConnect failed with error code 0x{res:08X}")

        self._is_open = True
        self._set_led("iface", True)

    def close(self) -> None:
        if self._is_open and self._dll:
            try:
                self._dll.PassThruDisconnect(self._channel_id)
                self._dll.PassThruClose(self._dev_id)
            except Exception:
                pass
        self._is_open = False
        self._dll = None
        self._set_led("iface", False)
        self._set_led("connected", False)

    def set_baudrate(self, baudrate: int) -> None:
        self.requested_baud = baudrate

    def set_protocol(self, protocol: int, baudrate: Optional[int] = None, flags: int = 0) -> None:
        if not self._is_open or not self._dll:
            raise TransportError("J2534 device not open")
        self._dll.PassThruDisconnect(self._channel_id)
        self._protocol = protocol
        if baudrate is not None:
            self.requested_baud = baudrate
        res = self._dll.PassThruConnect(
            self._dev_id, protocol, flags, self.requested_baud, byref(self._channel_id)
        )
        if res != 0:
            raise TransportError(f"PassThruConnect failed with error code 0x{res:08X}")

    def set_can_ids(self, tx_id: int, rx_id: int, extended: bool = False) -> None:
        """Select the transmit ID and install a PassThru PASS filter for the response ID.

        J2534 devices deliver nothing on a CAN channel until a filter is set
        (J2534-1 section 7.3.4).  ``rx_id == 0`` means the responder is not known
        yet: the filter then passes every identifier and ISO-TP locks on later.
        """
        self._tx_id, self._rx_id, self._extended = tx_id, rx_id, extended
        if not (self._is_open and self._dll) or self._protocol != J2534_CAN:
            return
        self._dll.PassThruIoctl(self._channel_id, CLEAR_MSG_FILTERS, None, None)
        full = 0x1FFFFFFF if extended else 0x7FF
        mask = self._can_msg(full if rx_id else 0, b"")
        pattern = self._can_msg(rx_id if rx_id else 0, b"")
        filter_id = c_ulong(0)
        res = self._dll.PassThruStartMsgFilter(
            self._channel_id, PASS_FILTER, byref(mask), byref(pattern), None, byref(filter_id))
        if res != 0:
            raise TransportError(f"PassThruStartMsgFilter failed with error code 0x{res:08X}")

    def _can_msg(self, can_id: int, data: bytes) -> "PASSTHRU_MSG":
        """J2534 raw CAN message: 4-byte big-endian identifier followed by the data bytes."""
        msg = PASSTHRU_MSG()
        msg.ProtocolID = J2534_CAN
        msg.TxFlags = CAN_29BIT_ID if self._extended else 0
        body = can_id.to_bytes(4, "big") + bytes(data)
        msg.DataSize = len(body)
        for i, b in enumerate(body):
            msg.Data[i] = b
        return msg

    def flush(self) -> None:
        if self._is_open and self._dll:
            self._dll.PassThruIoctl(self._channel_id, CLEAR_TX_BUFFER, None, None)
            self._dll.PassThruIoctl(self._channel_id, CLEAR_RX_BUFFER, None, None)

    def fast_init_pulse(self) -> None:
        if not self._is_open or not self._dll:
            raise TransportError("J2534 device not open")
        self._set_led("break", True)
        # SBYTE_ARRAY for Fast Init
        class SBYTE_ARRAY(Structure):
            _fields_ = [("NumOfBytes", c_ulong), ("BytePtr", POINTER(ctypes.c_ubyte))]

        in_bytes = (ctypes.c_ubyte * 1)(0x81)
        in_arr = SBYTE_ARRAY(1, in_bytes)
        out_bytes = (ctypes.c_ubyte * 32)()
        out_arr = SBYTE_ARRAY(32, out_bytes)

        self._dll.PassThruIoctl(self._channel_id, FAST_INIT, byref(in_arr), byref(out_arr))
        self._set_led("break", False)

    def write(self, data: bytes) -> None:
        if not self._is_open or not self._dll:
            raise TransportError("J2534 device not open")
        if self._protocol == J2534_CAN:
            self.send_frame(CanFrame(self._tx_id, bytes(data), extended=self._extended))
            return
        self._set_led("tx", True)
        msg = PASSTHRU_MSG()
        msg.ProtocolID = self._protocol
        msg.DataSize = len(data)
        for i, b in enumerate(data):
            msg.Data[i] = b
        self._write_msg(msg)
        self._set_led("tx", False)

    def _write_msg(self, msg: "PASSTHRU_MSG") -> None:
        num_msgs = c_ulong(1)
        res = self._dll.PassThruWriteMsgs(self._channel_id, byref(msg), byref(num_msgs), 1000)
        if res != 0:
            raise TransportError(f"PassThruWriteMsgs failed with error code 0x{res:08X}")

    def _read_msg(self, timeout: float) -> Optional["PASSTHRU_MSG"]:
        """Next received message, skipping loop-back copies of our own transmissions."""
        deadline = time.monotonic() + timeout
        while True:
            if self.cancel_token.cancelled:
                return None
            msg = PASSTHRU_MSG()
            num_msgs = c_ulong(1)
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            res = self._dll.PassThruReadMsgs(self._channel_id, byref(msg), byref(num_msgs), remaining)
            if res == 0 and num_msgs.value > 0:
                if msg.RxStatus & TX_MSG_TYPE:
                    continue
                return msg
            if res not in (0, ERR_TIMEOUT, ERR_BUFFER_EMPTY):
                raise TransportError(f"PassThruReadMsgs failed with error code 0x{res:08X}")
            if time.monotonic() >= deadline:
                return None

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        if not self._is_open or not self._dll:
            return b""
        if self._protocol == J2534_CAN:
            frame = self.recv_frame(timeout)
            return frame.data[:size] if frame else b""
        msg = self._read_msg(timeout)
        if msg is None:
            return b""
        self._set_led("rx", True)
        self._set_led("rx", False)
        return bytes(msg.Data[: msg.DataSize])

    def send_frame(self, frame: CanFrame) -> None:
        if not self._is_open or not self._dll:
            raise TransportError("J2534 device not open")
        if self._protocol != J2534_CAN:
            raise TransportError("J2534 channel is not configured for raw CAN frames")
        self._set_led("tx", True)
        saved, self._extended = self._extended, frame.extended
        try:
            self._write_msg(self._can_msg(frame.arbitration_id, frame.data))
        finally:
            self._extended = saved
            self._set_led("tx", False)

    def recv_frame(self, timeout: float = 0.5) -> Optional[CanFrame]:
        if not self._is_open or not self._dll:
            return None
        msg = self._read_msg(timeout)
        if msg is None or msg.DataSize < 4:
            return None
        raw = bytes(msg.Data[: msg.DataSize])
        self._set_led("rx", True)
        self._set_led("rx", False)
        return CanFrame(int.from_bytes(raw[:4], "big"), raw[4:],
                        extended=bool(msg.RxStatus & CAN_29BIT_ID))

    def _lookup_dll_in_registry(self, key_name: str) -> Optional[str]:
        if sys.platform != "win32":
            return None
        try:
            import winreg
            reg_paths = [r"SOFTWARE\PassThruSupport.04.04", r"SOFTWARE\WOW6432Node\PassThruSupport.04.04"]
            for r in reg_paths:
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{r}\\{key_name}") as k:
                        return str(winreg.QueryValueEx(k, "FunctionLibrary")[0])
                except OSError:
                    pass
        except Exception:
            pass
        return None

    def get_capabilities(self) -> Dict[str, bool]:
        return {
            "can": True,
            "kline": True,
            "fast_init": True,
            "slow_init": True,
            "l_line": False,
            "custom_baud": True,
        }
