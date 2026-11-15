"""
Smart AutoTTY & Hot-plug Hardware Adapter Discovery.
Probes serial baud rates, detects ELM327 chip versions and battery voltages,
classifies FTDI KKL vs K+CAN, and searches Windows J2534 DLLs.
"""

import sys
import threading
import time
from typing import Callable, List, Optional, Tuple
import serial
import serial.tools.list_ports


class DiscoveredInterface:
    def __init__(
        self,
        port: str,
        description: str,
        interface_type: str,
        hardware_id: str = "",
        voltage: str = "",
        firmware: str = "",
    ):
        self.port = port
        self.description = description
        self.interface_type = interface_type  # "FTDI", "ELM327", "SERIAL", "J2534", "CAN", "MOCK"
        self.hardware_id = hardware_id
        self.voltage = voltage
        self.firmware = firmware

    def __str__(self) -> str:
        extra = ""
        if self.firmware:
            extra += f" ({self.firmware})"
        if self.voltage:
            extra += f" [{self.voltage}]"
        return f"{self.port} — {self.interface_type}{extra} - {self.description}"


class AutoDetector:
    def __init__(self, callback: Optional[Callable[[List[DiscoveredInterface]], None]] = None):
        self._cb = callback
        self._is_running = False
        self._thread: Optional[threading.Thread] = None
        self._known_ports: List[str] = []

    def start(self, interval_s: float = 3.0) -> None:
        if self._is_running:
            return
        self._is_running = True
        self._thread = threading.Thread(target=self._poll_loop, args=(interval_s,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._is_running = False

    def _poll_loop(self, interval: float) -> None:
        while self._is_running:
            try:
                ifaces = self.scan_now(probe_elm=False)
                current_ports = [i.port for i in ifaces]
                if current_ports != self._known_ports:
                    self._known_ports = current_ports
                    if self._cb:
                        self._cb(ifaces)
            except Exception:
                pass
            time.sleep(interval)

    # USB-serial bridge chips commonly used in OBD cables (VID:PID).
    USB_CHIPS = {
        "0403:6001": "FTDI FT232R", "0403:6015": "FTDI FT230X", "0403:6010": "FTDI FT2232",
        "1A86:7523": "CH340", "10C4:EA60": "Silicon Labs CP210x", "067B:2303": "Prolific PL2303",
    }

    @classmethod
    def probe_serial_adapter(cls, port: str) -> Tuple[str, str, str]:
        """Identify what is behind a serial port.  Writes to the port: call only on user request.

        1. ELM327/STN: reset with ATZ at the datasheet host rates (38400 or 9600; also
           115200/57600 for modern clones) and look for the ELM/STN banner.
        2. KKL: at 10400 baud a K-line transceiver ties TX and RX together, so a byte we
           send is echoed back when the cable is powered from the vehicle (pin 16).
        Returns (type, firmware, voltage/notes).
        """
        for baud in (38400, 9600, 115200, 57600):
            try:
                with serial.Serial(port=port, baudrate=baud, timeout=0.25, write_timeout=0.3) as ser:
                    ser.reset_input_buffer()
                    ser.write(b"\rATZ\r")
                    time.sleep(1.0)
                    resp = ser.read(256).decode("ascii", errors="ignore").upper()
                    if any(tag in resp for tag in ("ELM", "STN", "OBDLINK", "OBDII")):
                        ser.write(b"ATE0\r"); time.sleep(0.1); ser.read(64)
                        ser.write(b"ATI\r"); time.sleep(0.15)
                        fw = ser.read(64).decode("ascii", errors="ignore").replace(">", "").strip()
                        ser.write(b"ATRV\r"); time.sleep(0.15)
                        volt = ser.read(32).decode("ascii", errors="ignore").replace(">", "").strip()
                        return "ELM327", fw or "ELM327", volt
            except (serial.SerialException, OSError):
                return "UNAVAILABLE", "", "port busy or permission denied"
        try:
            with serial.Serial(port=port, baudrate=10400, timeout=0.2) as ser:
                ser.reset_input_buffer()
                ser.write(b"\x55")
                echo = ser.read(1)
                if echo == b"\x55":
                    return "KKL", "K-line echo detected", "vehicle power present"
                return "KKL?", "no K-line echo", "USB-serial bridge; plug into the car (ignition on) to confirm"
        except (serial.SerialException, OSError):
            return "UNAVAILABLE", "", "port busy or permission denied"

    @classmethod
    def detect(cls, include_network: bool = False) -> List[DiscoveredInterface]:
        """Active detection: probe every candidate port and network adapter."""
        found = []
        for iface in cls.scan_now(probe_elm=False):
            if iface.interface_type in ("MOCK", "J2534", "CAN"):
                found.append(iface)
                continue
            kind, fw, extra = cls.probe_serial_adapter(iface.port)
            iface.interface_type, iface.firmware, iface.voltage = kind, fw or iface.firmware, extra
            found.append(iface)
        if include_network:
            found.extend(cls.detect_network())
        return found

    @staticmethod
    def detect_network(timeout: float = 0.6) -> List[DiscoveredInterface]:
        """Wi-Fi ELM at the common default address, BLE adapters (bleak) and DoIP vehicles (doipclient)."""
        import socket

        from klinescan.transport.links import DEFAULT_WIFI, discover_ble

        out: List[DiscoveredInterface] = []
        try:
            with socket.create_connection(DEFAULT_WIFI, timeout=timeout):
                out.append(DiscoveredInterface(f"tcp://{DEFAULT_WIFI[0]}:{DEFAULT_WIFI[1]}",
                                               "Wi-Fi OBD adapter", "ELM327", firmware="Wi-Fi"))
        except OSError:
            pass
        for dev in discover_ble():
            out.append(DiscoveredInterface(dev["port"], dev["name"], "ELM327", firmware="BLE"))
        try:
            from doipclient import DoIPClient

            address, announcement = DoIPClient.get_entity()
            out.append(DiscoveredInterface(
                f"doip://{address[0]}/{announcement.logical_address:#06x}",
                f"DoIP vehicle VIN {getattr(announcement, 'vin', '')}", "DOIP", firmware="ISO 13400"))
        except Exception:
            pass
        return out

    @classmethod
    def scan_now(cls, probe_elm: bool = False) -> List[DiscoveredInterface]:
        results: List[DiscoveredInterface] = []

        # Always include Mock / Offline Simulator
        results.append(
            DiscoveredInterface(
                port="MOCK_PORT",
                description="Mock ECU (Offline)",
                interface_type="MOCK",
                firmware="Simulator v2.0",
                voltage="14.1V",
            )
        )

        # Serial / USB COM ports
        for p in serial.tools.list_ports.comports():
            if (p.hwid or "n/a") == "n/a" and "rfcomm" not in p.device:
                continue  # legacy UARTs the OS lists without hardware behind them
            desc = p.description or ""
            hwid = p.hwid or ""
            desc_upper = desc.upper()
            hw_upper = hwid.upper()

            interface_type = "SERIAL"
            fw = ""
            volt = ""
            vidpid = f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None and p.pid is not None else ""
            chip = cls.USB_CHIPS.get(vidpid, "")
            if "OBDLINK" in desc_upper or "ELM327" in desc_upper or "STN" in desc_upper:
                interface_type = "ELM327"
            elif "BLUETOOTH" in desc_upper or "RFCOMM" in p.device.upper():
                interface_type = "BLUETOOTH"
                fw = "Bluetooth serial (SPP) - press Detect to identify"
            elif chip:
                interface_type = "USB-SERIAL"
                fw = f"{chip} - KKL or ELM327, press Detect"

            if probe_elm and interface_type in ("SERIAL", "ELM327"):
                probed_type, probed_fw, probed_volt = cls.probe_serial_adapter(p.device)
                if probed_type == "ELM327":
                    interface_type = "ELM327"
                    fw = probed_fw
                    volt = probed_volt

            results.append(
                DiscoveredInterface(
                    port=p.device,
                    description=desc,
                    interface_type=interface_type,
                    hardware_id=hwid,
                    voltage=volt,
                    firmware=fw,
                )
            )

        # Windows J2534 PassThru Registry Scan
        if sys.platform == "win32":
            try:
                import winreg
                reg_paths = [
                    r"SOFTWARE\PassThruSupport.04.04",
                    r"SOFTWARE\WOW6432Node\PassThruSupport.04.04",
                ]
                for root_path in reg_paths:
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root_path) as key:
                            num_subkeys = winreg.QueryInfoKey(key)[0]
                            for i in range(num_subkeys):
                                subkey_name = winreg.EnumKey(key, i)
                                with winreg.OpenKey(key, subkey_name) as subkey:
                                    try:
                                        name = winreg.QueryValueEx(subkey, "Name")[0]
                                        dll = winreg.QueryValueEx(subkey, "FunctionLibrary")[0]
                                        results.append(
                                            DiscoveredInterface(
                                                port=f"J2534:{subkey_name}",
                                                description=f"{name}",
                                                interface_type="J2534",
                                                hardware_id=dll,
                                                firmware="SAE J2534-1",
                                            )
                                        )
                                    except Exception:
                                        pass
                    except OSError:
                        pass
            except Exception:
                pass

        # Linux SocketCAN Scan
        if sys.platform.startswith("linux"):
            try:
                import os
                if os.path.exists("/sys/class/net"):
                    for net_dev in os.listdir("/sys/class/net"):
                        if net_dev.startswith("can") or net_dev.startswith("vcan"):
                            results.append(
                                DiscoveredInterface(
                                    port=net_dev,
                                    description=f"SocketCAN Interface {net_dev}",
                                    interface_type="CAN",
                                    firmware="Linux Kernel CAN",
                                )
                            )
            except Exception:
                pass

        return results
