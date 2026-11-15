"""Byte-stream links for adapters that speak a text/serial protocol (ELM327 / STN).

An ELM327 behaves identically over USB, Bluetooth Classic, Wi-Fi or BLE; only
the pipe differs.  ``open_link(port)`` picks the pipe from the port string:

    COM5, /dev/ttyUSB0, /dev/rfcomm0     serial (USB or OS-bound Bluetooth SPP)
    bt://AA:BB:CC:DD:EE:FF[/channel]     Bluetooth Classic RFCOMM socket (default channel 1)
    tcp://192.168.0.10:35000             Wi-Fi adapters (common factory default; see
                                         https://www.klinescanner.info/wifi/ - not universal)
    ble://AA:BB:CC:DD:EE:FF              Bluetooth Low Energy (needs the optional ``bleak``)

BLE ELM327 clones expose a vendor serial service.  Documented layouts:
service FFF0 with FFF1 = notify and FFF2 = write (OBDLink CX notes:
https://support.scantool.net/support/solutions/articles/43000746707-obdlink-cx-adapter-notes),
and TI CC254x modules with FFE0/FFE1 (notify+write on one characteristic).
Clones vary, so characteristics are discovered at runtime by property.
"""

from __future__ import annotations

import socket
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional

from klinescan.core.errors import AdapterError, ConfigurationError, DeviceNotFoundError

DEFAULT_WIFI = ("192.168.0.10", 35000)
BLE_PREFERRED = (
    ("0000fff0-0000-1000-8000-00805f9b34fb", "0000fff1-0000-1000-8000-00805f9b34fb",
     "0000fff2-0000-1000-8000-00805f9b34fb"),
    ("0000ffe0-0000-1000-8000-00805f9b34fb", "0000ffe1-0000-1000-8000-00805f9b34fb",
     "0000ffe1-0000-1000-8000-00805f9b34fb"),
)


class ByteLink(ABC):
    kind = "link"

    @abstractmethod
    def write(self, data: bytes) -> None: ...

    @abstractmethod
    def read(self, size: int, timeout: float) -> bytes: ...

    @abstractmethod
    def close(self) -> None: ...

    def reset_input(self) -> None:
        while self.read(256, 0.0):
            pass

    def set_baudrate(self, baud: int) -> None:
        """Only meaningful for wired serial links."""


class SerialLink(ByteLink):
    kind = "serial"

    def __init__(self, port: str, baudrate: int):
        import serial  # pyserial

        try:
            self._ser = serial.Serial(port=port, baudrate=baudrate, timeout=0.05, write_timeout=2.0)
        except serial.SerialException as exc:
            raise DeviceNotFoundError(f"cannot open {port}: {exc}") from exc
        self.kind = "bluetooth-spp" if "rfcomm" in port.lower() else "serial"

    def write(self, data: bytes) -> None:
        self._ser.write(data)
        self._ser.flush()

    def read(self, size: int, timeout: float) -> bytes:
        self._ser.timeout = timeout
        return self._ser.read(size)

    def reset_input(self) -> None:
        self._ser.reset_input_buffer()

    def set_baudrate(self, baud: int) -> None:
        self._ser.baudrate = baud

    def close(self) -> None:
        self._ser.close()


class SocketLink(ByteLink):
    """TCP (Wi-Fi adapters) or Bluetooth RFCOMM socket."""

    def __init__(self, sock: socket.socket, kind: str):
        self._sock = sock
        self.kind = kind

    @classmethod
    def tcp(cls, host: str, port: int, timeout: float = 5.0) -> "SocketLink":
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise DeviceNotFoundError(f"cannot reach Wi-Fi adapter {host}:{port}: {exc}") from exc
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return cls(sock, "wifi")

    @classmethod
    def rfcomm(cls, address: str, channel: int = 1, timeout: float = 10.0) -> "SocketLink":
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise ConfigurationError(
                "this Python build has no native Bluetooth sockets; pair the adapter and use "
                "its serial port (COMx or /dev/rfcomm0) instead")
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(timeout)
        try:
            sock.connect((address, channel))
        except OSError as exc:
            sock.close()
            raise DeviceNotFoundError(
                f"cannot connect Bluetooth {address} channel {channel}: {exc} "
                "(is the adapter paired and powered?)") from exc
        return cls(sock, "bluetooth-rfcomm")

    def write(self, data: bytes) -> None:
        self._sock.sendall(data)

    def read(self, size: int, timeout: float) -> bytes:
        self._sock.settimeout(max(timeout, 0.001))
        try:
            return self._sock.recv(size)
        except (socket.timeout, BlockingIOError):
            return b""

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class BleLink(ByteLink):
    """BLE GATT serial bridge via ``bleak`` running on a private asyncio loop."""

    kind = "ble"

    def __init__(self, address: str, timeout: float = 15.0):
        try:
            import asyncio

            from bleak import BleakClient  # optional dependency
        except ImportError as exc:
            raise ConfigurationError(
                "BLE adapters need the optional 'bleak' package: pip install bleak") from exc
        self._asyncio = asyncio
        self._rx = bytearray()
        self._cond = threading.Condition()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="ble-link")
        self._thread.start()
        self._client = BleakClient(address, timeout=timeout)
        self._write_char = None
        self._write_response = False
        try:
            self._call(self._connect(), timeout + 5)
        except Exception as exc:
            self.close()
            raise DeviceNotFoundError(f"BLE connect to {address} failed: {exc}") from exc

    def _call(self, coro, timeout: float):
        return self._asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _connect(self) -> None:
        await self._client.connect()
        services = self._client.services
        notify = write = None
        for svc_uuid, n_uuid, w_uuid in BLE_PREFERRED:
            if services.get_service(svc_uuid):
                notify = services.get_characteristic(n_uuid)
                write = services.get_characteristic(w_uuid)
                if notify and write:
                    break
        if not (notify and write):  # fall back to discovery by properties
            for svc in services:
                for ch in svc.characteristics:
                    props = set(ch.properties)
                    if notify is None and props & {"notify", "indicate"}:
                        notify = ch
                    if write is None and props & {"write", "write-without-response"}:
                        write = ch
        if not (notify and write):
            raise AdapterError("no notify/write characteristic pair found on BLE device")
        self._write_char = write
        self._write_response = "write" in write.properties and "write-without-response" not in write.properties
        await self._client.start_notify(notify, self._on_notify)

    def _on_notify(self, _handle, data: bytearray) -> None:
        with self._cond:
            self._rx.extend(data)
            self._cond.notify_all()

    def write(self, data: bytes) -> None:
        # BLE writes are limited by the ATT MTU; 20 bytes is the guaranteed minimum.
        for i in range(0, len(data), 20):
            self._call(self._client.write_gatt_char(self._write_char, data[i:i + 20],
                                                    response=self._write_response), 5)

    def read(self, size: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        with self._cond:
            while not self._rx:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b""
                self._cond.wait(remaining)
            out = bytes(self._rx[:size])
            del self._rx[:size]
            return out

    def reset_input(self) -> None:
        with self._cond:
            self._rx.clear()

    def close(self) -> None:
        try:
            if self._client and self._client.is_connected:
                self._call(self._client.disconnect(), 5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


def open_link(port: str, baudrate: int = 38400) -> ByteLink:
    """Open the byte pipe described by ``port`` (see module docstring)."""
    port = (port or "").strip()
    lower = port.lower()
    if lower.startswith("tcp://") or lower.startswith("wifi://"):
        rest = port.split("://", 1)[1] or f"{DEFAULT_WIFI[0]}:{DEFAULT_WIFI[1]}"
        host, _, p = rest.partition(":")
        return SocketLink.tcp(host or DEFAULT_WIFI[0], int(p or DEFAULT_WIFI[1]))
    if lower.startswith("bt://") or lower.startswith("rfcomm://"):
        rest = port.split("://", 1)[1]
        address, _, channel = rest.partition("/")
        return SocketLink.rfcomm(address, int(channel or 1))
    if lower.startswith("ble://"):
        return BleLink(port.split("://", 1)[1])
    if not port:
        raise ConfigurationError("no adapter port selected")
    return SerialLink(port, baudrate)


def discover_ble(timeout: float = 5.0) -> list[dict]:
    """Scan for BLE devices advertising an ELM-style serial service (needs bleak)."""
    try:
        import asyncio

        from bleak import BleakScanner
    except ImportError:
        return []
    wanted = {svc for svc, _, _ in BLE_PREFERRED}

    async def scan():
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        out = []
        for device, adv in found.values():
            uuids = {u.lower() for u in (adv.service_uuids or [])}
            name = device.name or adv.local_name or ""
            if uuids & wanted or any(k in name.upper() for k in ("OBD", "ELM", "VLINK", "VGATE", "OBDLINK", "ICAR")):
                out.append({"port": f"ble://{device.address}", "name": name or device.address,
                            "rssi": adv.rssi})
        return out

    try:
        return asyncio.run(scan())
    except Exception:
        return []
