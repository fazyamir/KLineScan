"""
Offline Fake ECU transport: a complete vehicle simulator with dynamic engine
physics, multi-frame ISO-TP, UDS, and actuator tests.
"""

import math
import time
from collections import deque
from typing import Deque, Dict, List, Optional
from klinescan.core.registry import register_transport
from klinescan.transport.base import BaseTransport, TransportCapabilities


@register_transport("fake", TransportCapabilities(
    supports_can=True, supports_kline=True, supports_raw_frames=True,
    supports_l_line=True, supports_custom_baud=True,
    description="Offline simulated ECU / captured frame replay"))
class FakeEcuTransport(BaseTransport):
    def __init__(self, led_callback=None):
        super().__init__(led_callback)
        self._is_open = False
        self._rx_queue: Deque[int] = deque()
        self._tick: int = 0
        self._dtcs_cleared = False
        self._sim_start = time.time()
        self._security_unlocked = False
        self._pending_cfs: List[bytes] = []

        # Static captured exact request -> response pairs
        self._obd_map: Dict[bytes, bytes] = {
            # Hyundai ISO 9141-2
            bytes.fromhex("68 6a f1 01 00 c4"): bytes.fromhex("48 6b 12 41 00 be 3e b8 11 cb"),
            bytes.fromhex("68 6a f1 01 01 c5"): bytes.fromhex("48 6b 12 41 01 01 04 00 00 0c"),
            bytes.fromhex("68 6a f1 04 c7"): bytes.fromhex("48 6b 12 44 09"),
            # Toyota Yaris ISO 9141-2
            bytes.fromhex("68 6a f0 01 00 c3"): bytes.fromhex("48 6b 10 41 00 be 1f b8 11 aa"),
            bytes.fromhex("68 6a f0 01 05 c8"): bytes.fromhex("48 6b 10 41 05 64 6d"),       # 60 C
            bytes.fromhex("68 6a f0 01 0c cf"): bytes.fromhex("48 6b 10 41 0c 0d ac c9"),    # ~875 RPM
            bytes.fromhex("68 6a f0 01 0d d0"): bytes.fromhex("48 6b 10 41 0d 00 11"),       # 0 km/h
            bytes.fromhex("68 6a f0 04 c6"): bytes.fromhex("48 6b 10 44 07"),
            # KWP2000 Fast Init & Keep-Alive
            bytes.fromhex("81 11 f1 81 04"): bytes.fromhex("83 f1 11 c1 ef 8f c4"),
            bytes.fromhex("81 11 f1 3e c1"): bytes.fromhex("81 f1 11 7e 01"),
            bytes.fromhex("80 28 f1 01 81 1b"): bytes.fromhex("83 f1 28 c1 ef 8f db"),
            bytes.fromhex("80 28 f1 01 3e d8"): bytes.fromhex("81 f1 28 7e 18"),
            # VAG KWP1281 / KWP2000
            bytes.fromhex("81 01 f1 81 f4"): bytes.fromhex("83 f1 01 c1 ef 8f b4"),
            bytes.fromhex("81 01 f1 3e b1"): bytes.fromhex("81 f1 01 7e f1"),
        }

    def open(self, port: str, baudrate: int) -> None:
        self._is_open = True
        self.port = port
        self.requested_baud = baudrate
        self.actual_baud = baudrate
        self._rx_queue.clear()
        self._sim_start = time.time()
        self._dtcs_cleared = False
        self._mark_open()

    def close(self) -> None:
        self._is_open = False
        self._rx_queue.clear()
        self._mark_closed()

    def set_baudrate(self, baudrate: int) -> None:
        self.requested_baud = baudrate
        self.actual_baud = baudrate

    def flush(self) -> None:
        self._rx_queue.clear()

    def fast_init_pulse(self) -> None:
        self._set_led("break", True)
        time.sleep(0.025)
        self._set_led("break", False)
        time.sleep(0.025)

    def slow_init_5baud(self, target_addr: int, parity: str = "odd") -> bytes:
        self._init_addr = target_addr
        self._set_led("break", True)
        time.sleep(0.12)
        self._set_led("break", False)
        # 0x55 sync byte + Hyundai/ISO9141 keybytes 0x08 0x08 (or 0x8F 0xEA for KWP)
        if target_addr in (0x01, 0x03, 0x11):
            return bytes([0x55, 0x8F, 0xEA])
        return bytes([0x55, 0x08, 0x08])

    def send_break(self, duration_ms: int = 25) -> None:
        self._set_led("break", True)
        time.sleep(duration_ms / 1000.0)
        self._set_led("break", False)

    def set_l_line(self, high: bool) -> None:
        pass

    def write(self, data: bytes) -> None:
        if not self._is_open or not data:
            return
        self._set_led("tx", True)
        self._tick += 1

        reply = self._obd_map.get(data)
        if reply is None:
            reply = self._dynamic_reply(data)

        if reply:
            for b in reply:
                self._rx_queue.append(b)
        self._set_led("tx", False)

    def _dynamic_reply(self, data: bytes) -> bytes:
        now = time.time() - self._sim_start

        # ISO 9141-2 / ISO 14230 slow init: after the tester's ~KW2 the ECU answers
        # with its inverted address.
        if len(data) == 1:
            return bytes([(~getattr(self, "_init_addr", 0x33)) & 0xFF])

        # 1. ISO 9141-2 / CARB Header (68 6A ...)
        if len(data) >= 5 and data[0] in (0x68, 0x48) and data[1] in (0x6A, 0x6B):
            tester = data[2]
            mode = data[3]

            # Mode 01 - Live Telemetry
            if mode == 0x01 and len(data) >= 5:
                pid = data[4]
                return self._build_iso9141_pid(tester, pid, now)

            # Mode 02 - Freeze Frame
            if mode == 0x02 and len(data) >= 5:
                pid = data[4]
                body = [0x48, 0x6B, 0x12, 0x42, pid, 0x00, 0x1F, 0x40]
                csum = sum(body) & 0xFF
                return bytes(body + [csum])

            # Mode 03 - Stored DTCs
            if mode == 0x03:
                if self._dtcs_cleared:
                    body = [0x48, 0x6B, 0x12, 0x43, 0x00, 0x00, 0x00, 0x00]
                else:
                    # P0115 (0x0115) & P0300 (0x0300)
                    body = [0x48, 0x6B, 0x12, 0x43, 0x01, 0x15, 0x03, 0x00, 0x00, 0x00]
                return bytes(body + [sum(body) & 0xFF])

            # Mode 04 - Clear DTCs
            if mode == 0x04:
                self._dtcs_cleared = True
                body = [0x48, 0x6B, 0x12, 0x44]
                return bytes(body + [sum(body) & 0xFF])

            # Mode 07 - Pending DTCs
            if mode == 0x07:
                if self._dtcs_cleared:
                    body = [0x48, 0x6B, 0x12, 0x47, 0x00, 0x00]
                else:
                    # P0420 (0x0420)
                    body = [0x48, 0x6B, 0x12, 0x47, 0x04, 0x20, 0x00, 0x00]
                return bytes(body + [sum(body) & 0xFF])

            # Mode 09 - Vehicle Information (VIN / CalID)
            if mode == 0x09 and len(data) >= 5:
                infotype = data[4]
                if infotype == 0x02:  # VIN: 5 numbered messages of 4 bytes, left-padded with 0x00
                    padded = bytes(3) + b"KMHCN46C17U123456"
                    out = bytearray()
                    for seq in range(5):
                        body = [0x48, 0x6B, 0x12, 0x49, 0x02, seq + 1] + list(padded[seq * 4:seq * 4 + 4])
                        out += bytes(body + [sum(body) & 0xFF])
                    return bytes(out)
                if infotype == 0x0A:  # ECU name: 20 ASCII bytes, right-padded with 0x00
                    name = b"ECM-EngineControl".ljust(20, b"\x00")
                    out = bytearray()
                    for seq in range(5):
                        body = [0x48, 0x6B, 0x12, 0x49, 0x0A, seq + 1] + list(name[seq * 4:seq * 4 + 4])
                        out += bytes(body + [sum(body) & 0xFF])
                    return bytes(out)
                if infotype == 0x04:  # CalID
                    cal_str = b"HYU-ME798-CAL01"
                    out = bytearray()
                    padded = cal_str.ljust(16, b"\x00")
                    for seq in range(4):
                        body = [0x48, 0x6B, 0x12, 0x49, 0x04, seq + 1] + list(padded[seq * 4:seq * 4 + 4])
                        out += bytes(body + [sum(body) & 0xFF])
                    return bytes(out)

        # 2. KWP2000 Frames (0x80..0x8F header format)
        if len(data) >= 4 and (data[0] & 0xC0) == 0x80:
            ecu = data[1]
            tester = data[2]
            sid = data[3] if (data[0] & 0x3F) != 0 else data[4]

            # 0x18 - ReadDTCByStatus
            if sid == 0x18:
                if self._dtcs_cleared:
                    body = [0x82, tester, ecu, 0x58, 0x00]
                    return bytes(body + [sum(body) & 0xFF])
                else:
                    # 2 DTCs: P0115 (0x01 0x15 status 0x20) and P0300 (0x03 0x00 status 0x20)
                    dtc_payload = [0x58, 0x02, 0x01, 0x15, 0x20, 0x03, 0x00, 0x20]
                    body = [0x80 | len(dtc_payload), tester, ecu] + dtc_payload
                    return bytes(body + [sum(body) & 0xFF])

            # 0x14 - ClearDiagnosticInformation
            if sid == 0x14:
                self._dtcs_cleared = True
                body = [0x83, tester, ecu, 0x54, 0xFF, 0x00]
                return bytes(body + [sum(body) & 0xFF])

            # 0x1A - ReadEcuIdentification
            if sid == 0x1A:
                id_type = data[4] if len(data) > 4 else 0x90
                # "BOSCH ME7.9.8 HYUNDAI"
                ecu_id_bytes = list(b"HYU_39110_26820")
                body = [0x80 | (len(ecu_id_bytes) + 3), tester, ecu, 0x5A, id_type] + ecu_id_bytes
                return bytes(body + [sum(body) & 0xFF])

            # 0x01 - SAE J1979 Service 01 on a KWP2000 session (EOBD engine ECUs)
            if sid == 0x01 and len(data) > 5:
                raw = self._pid_raw(data[4], now)
                if raw is None:
                    body = [0x83, tester, ecu, 0x7F, 0x01, 0x12]
                else:
                    payload = [0x41, data[4]] + raw
                    body = [0x80 | len(payload), tester, ecu] + payload
                return bytes(body + [sum(body) & 0xFF])

            # 0x21 - ReadDataByLocalIdentifier
            if sid == 0x21:
                loc_id = data[4] if len(data) > 4 else 0x01
                rpm_raw = int(850 + 200 * math.sin(now * 0.8))
                spd_raw = int(35 + 15 * math.sin(now * 0.4))
                cool_raw = min(92, int(75 + now * 0.5)) + 40
                body = [0x86, tester, ecu, 0x61, loc_id, (rpm_raw >> 8) & 0xFF, rpm_raw & 0xFF, spd_raw, cool_raw]
                return bytes(body + [sum(body) & 0xFF])

            # 0x30 - InputOutputControlByLocalIdentifier (Actuator test)
            if sid == 0x30:
                act_id = data[4] if len(data) > 4 else 0x01
                body = [0x84, tester, ecu, 0x70, act_id, 0x00]
                return bytes(body + [sum(body) & 0xFF])

            # 0x27 - SecurityAccess
            if sid == 0x27:
                sub = data[4] if len(data) > 4 else 0x01
                if sub == 0x01:  # Request Seed
                    body = [0x85, tester, ecu, 0x67, 0x01, 0x12, 0x34]
                    return bytes(body + [sum(body) & 0xFF])
                elif sub == 0x02:  # Send Key
                    self._security_unlocked = True
                    body = [0x83, tester, ecu, 0x67, 0x02]
                    return bytes(body + [sum(body) & 0xFF])

        # 3. ISO-TP / CAN Frames (8 bytes)
        if len(data) == 8:
            pci = (data[0] >> 4) & 0x0F
            sid = data[1]

            # Single frame Mode 01
            if sid == 0x01:
                pid = data[2]
                return self._build_can_pid(pid, now)

            # Single frame Mode 03 - DTCs
            if sid == 0x03:
                if self._dtcs_cleared:
                    return bytes([0x02, 0x43, 0x00, 0, 0, 0, 0, 0])
                return bytes([0x06, 0x43, 0x02, 0x01, 0x15, 0x03, 0x00, 0])

            # Single frame Mode 04 - Clear DTCs
            if sid == 0x04:
                self._dtcs_cleared = True
                return bytes([0x01, 0x44, 0, 0, 0, 0, 0, 0])

            # Single frame Mode 07 - Pending DTCs
            if sid == 0x07:
                if self._dtcs_cleared:
                    return bytes([0x02, 0x47, 0x00, 0, 0, 0, 0, 0])
                return bytes([0x04, 0x47, 0x01, 0x15, 0x03, 0x00, 0, 0])

            # Single frame Mode 0A - Permanent DTCs (none on this simulator)
            if sid == 0x0A:
                return bytes([0x02, 0x4A, 0x00, 0, 0, 0, 0, 0])

            # Mode 09 - vehicle information (multi-frame ISO-TP replies)
            if pci == 0 and sid == 0x09:
                info = {
                    0x02: b"WBA3A5C51CF256651",                 # VIN (17 ASCII)
                    0x04: b"DME-MEVD17.2.4".ljust(16, b"\x00"),  # one 16-byte CALID
                    0x0A: b"ECM-EngineControl".ljust(20, b"\x00"),
                }.get(data[2])
                if info is None:
                    return bytes([0x03, 0x7F, 0x09, 0x12, 0, 0, 0, 0])
                return self._isotp_reply(bytes([0x49, data[2], 0x01]) + info)

            # ISO-TP Flow Control received -> send the queued consecutive frames
            if pci == 0x03:
                for frame in self._pending_cfs:
                    self._rx_queue.extend(frame)
                self._pending_cfs = []
                return b""

            # UDS 0x22 - ReadDataByIdentifier
            if sid == 0x22:
                did_hi, did_lo = data[2], data[3]
                return bytes([0x05, 0x62, did_hi, did_lo, 0x0A, 0x1C, 0, 0])

            # UDS 0x2F - InputOutputControl
            if sid == 0x2F:
                return bytes([0x04, 0x6F, data[2], data[3], 0x03, 0, 0, 0])

        return b""

    def _isotp_reply(self, payload: bytes) -> bytes:
        """Single frame, or first frame now and consecutive frames after the tester's flow control."""
        if len(payload) <= 7:
            return bytes([len(payload)]) + payload + bytes(7 - len(payload))
        first = bytes([0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:6]
        rest, seq, self._pending_cfs = payload[6:], 1, []
        while rest:
            chunk, rest = rest[:7], rest[7:]
            self._pending_cfs.append(bytes([0x20 | (seq & 0x0F)]) + chunk + bytes(7 - len(chunk)))
            seq += 1
        return first

    # -- simulated engine ---------------------------------------------------
    @staticmethod
    def _engine(now: float) -> Dict[str, float]:
        """A small, self-consistent warm-up / drive cycle."""
        rpm = 820 + 1400 * max(0.0, math.sin(now * 0.25)) + 40 * math.cos(now * 2.5)
        speed = max(0.0, 55 * math.sin(now * 0.25))
        load = 18 + 0.022 * (rpm - 800) + 6 * math.sin(now * 0.7)
        coolant = 88 + 2.5 * math.sin(now * 0.05)          # warm engine, thermostat cycling
        return {
            "rpm": rpm, "speed": speed, "load": max(5.0, min(95.0, load)), "coolant": coolant,
            "iat": 28 + 4 * math.sin(now * 0.05), "throttle": 12 + 0.02 * (rpm - 800),
            "maf": 2.5 + rpm * load / 9000, "map": 30 + load * 0.7, "timing": 8 + 10 * math.sin(now * 0.4),
            "stft": 2.5 * math.sin(now * 1.3), "ltft": -1.6, "o2": 0.45 + 0.4 * math.sin(now * 3.0),
            "volts": 14.1 + 0.15 * math.sin(now * 1.5), "baro": 101, "ambient": 24,
            "fuel": 63.0, "oil": 92 + 3 * math.sin(now * 0.04), "runtime": 600 + now,
        }

    @staticmethod
    def _u8(value: float) -> List[int]:
        return [max(0, min(255, int(round(value))))]

    @staticmethod
    def _u16(value: float) -> List[int]:
        raw = max(0, min(0xFFFF, int(round(value))))
        return [raw >> 8, raw & 0xFF]

    def _pid_raw(self, pid: int, now: float) -> Optional[List[int]]:
        """Raw Service 01 data bytes for ``pid`` (inverse of klinescan.core.obd_pids)."""
        from klinescan.core.obd_pids import BITMAP_PIDS, encode_bitmap

        e = self._engine(now)
        pct = lambda v: self._u8(v * 255 / 100)
        trim = lambda v: self._u8((v + 100) * 128 / 100)
        table = {
            0x01: [0x00, 0x07, 0x65, 0x00],                 # MIL off, 0 DTCs, spark ignition
            0x03: [0x02, 0x00],                             # closed loop
            0x04: pct(e["load"]), 0x05: self._u8(e["coolant"] + 40),
            0x06: trim(e["stft"]), 0x07: trim(e["ltft"]),
            0x0B: self._u8(e["map"]), 0x0C: self._u16(e["rpm"] * 4), 0x0D: self._u8(e["speed"]),
            0x0E: self._u8((e["timing"] + 64) * 2), 0x0F: self._u8(e["iat"] + 40),
            0x10: self._u16(e["maf"] * 100), 0x11: pct(e["throttle"]),
            0x13: [0x03], 0x14: self._u8(e["o2"] * 200) + trim(e["stft"]),
            0x15: self._u8(0.62 * 200) + [0xFF], 0x1C: [0x06], 0x1F: self._u16(e["runtime"]),
            0x21: self._u16(0), 0x2F: pct(e["fuel"]), 0x30: [0x12], 0x31: self._u16(1532),
            0x33: self._u8(e["baro"]), 0x3C: self._u16((420 + 40 * math.sin(now * 0.2) + 40) * 10),
            0x42: self._u16(e["volts"] * 1000), 0x43: self._u16(e["load"] * 255 / 100),
            0x44: self._u16(1.0 * 65536 / 2), 0x45: pct(e["throttle"] * 0.8),
            0x46: self._u8(e["ambient"] + 40), 0x47: pct(e["throttle"] + 4),
            0x5A: pct(e["throttle"] * 0.9), 0x5C: self._u8(e["oil"] + 40),
            0x5E: self._u16((0.6 + e["maf"] * 0.09) * 20), 0xA6: list(int(1234567).to_bytes(4, "big")),
        }
        if pid in BITMAP_PIDS:
            supported = set(table) | {b for b in BITMAP_PIDS if b > 0 and b <= max(table)}
            return list(encode_bitmap(pid, supported))
        return table.get(pid)

    def _build_iso9141_pid(self, tester: int, pid: int, now: float) -> bytes:
        raw = self._pid_raw(pid, now)
        if raw is None:
            return b""
        body = [0x48, 0x6B, 0x12, 0x41, pid] + raw
        return bytes(body + [sum(body) & 0xFF])

    def _build_can_pid(self, pid: int, now: float) -> bytes:
        raw = self._pid_raw(pid, now)
        if raw is None:
            return bytes([0x03, 0x7F, 0x01, 0x12, 0, 0, 0, 0])  # subFunctionNotSupported
        payload = [0x41, pid] + raw
        return bytes([len(payload)] + payload + [0] * (7 - len(payload)))

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        if not self._rx_queue:
            time.sleep(min(0.015, timeout))
            return b""
        self._set_led("rx", True)
        out = bytearray()
        while self._rx_queue and len(out) < size:
            out.append(self._rx_queue.popleft())
        self._set_led("rx", False)
        return bytes(out)

    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            supports_can=True,
            supports_kline=True,
            supports_l_line=True,
            supports_custom_baud=True,
            supports_raw_frames=True,
            description="Offline simulated ECU",
        )

    def get_capabilities(self) -> Dict[str, bool]:
        return self.capabilities().as_dict()
