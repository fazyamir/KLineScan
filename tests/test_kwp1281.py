from collections import deque

from klinescan.core.models import EcuTarget, ProtocolType
from klinescan.protocols.kwp1281 import Kwp1281Protocol
from klinescan.transport.base import BaseTransport


class FakeKwp1281Transport(BaseTransport):
    """Byte-level ECU that implements KWP1281 complements and counters."""

    def __init__(self):
        super().__init__()
        self.rx = deque()
        self.expected_complements = deque()
        self.tester_block = bytearray()
        self.tester_length = 0
        self.counter = 0
        self.tx_bytes = bytearray()
        self.awaiting_init_ack = False

    def open(self, port, baudrate):
        pass

    def close(self):
        pass

    def set_baudrate(self, baudrate):
        pass

    def flush(self):
        self.rx.clear()

    def slow_init_5baud(self, target_addr):
        self.awaiting_init_ack = True
        self._queue_block(0xF6, b"1J0035180B")
        return bytes([0x55, 0x01, 0x8A])

    def read(self, size, timeout=0.5):
        out = bytearray()
        while self.rx and len(out) < size:
            value, needs_complement = self.rx.popleft()
            out.append(value)
            if needs_complement:
                self.expected_complements.append((~value) & 0xFF)
        return bytes(out)

    def write(self, data):
        if not data:
            return
        value = data[0]
        self.tx_bytes.extend(data)
        if self.awaiting_init_ack:
            # complement of KW2 (0x8A): not answered by the ECU
            self.awaiting_init_ack = False
            assert value == 0x75, f"tester sent 0x{value:02X} instead of ~KW2"
            return
        if self.expected_complements and value == self.expected_complements[0]:
            self.expected_complements.popleft()
            return
        self.tester_block.append(value)
        if len(self.tester_block) == 1:
            self.tester_length = value
        is_final = len(self.tester_block) >= self.tester_length + 1
        if not is_final:
            self.rx.append(((~value) & 0xFF, False))
            return
        self._handle_tester_block(bytes(self.tester_block))
        self.tester_block.clear()
        self.tester_length = 0

    def _handle_tester_block(self, block):
        if len(block) < 3:
            return
        counter, title = block[1], block[2]
        self.counter = counter
        if title == 0x09:
            self._queue_block(0x09, b"")
        elif title == 0x07:
            data = bytes([0x01, 0x15, 0x20, 0x03, 0x00, 0x20])
            self._queue_block(0xFC, data)
        elif title == 0x05:
            self._queue_block(0x09, b"")
        elif title == 0x29:
            self._queue_block(0xE7, bytes([0x01, 0x0D, 0xAC, 0x02, 0x10, 0x20]))
        elif title == 0x00:
            self._queue_block(0xF6, b"1J0035180B")

    def _queue_block(self, title, data):
        self.counter = (self.counter + 1) & 0xFF
        body = bytes([len(data) + 3, self.counter, title]) + data + b"\x03"
        self.rx.extend((byte, True) for byte in body[:-1])
        self.rx.append((body[-1], False))


def test_kwp1281_connect_and_ident():
    transport = FakeKwp1281Transport()
    target = EcuTarget(name="VAG", protocol=ProtocolType.KWP1281, slow_init_addr=0x01)
    protocol = Kwp1281Protocol(transport, target)
    assert protocol.connect()
    ids = protocol.read_ecu_id()
    assert any("1J0035180B" in item.value for item in ids)


def test_kwp1281_fault_and_live_data():
    transport = FakeKwp1281Transport()
    target = EcuTarget(name="VAG", protocol=ProtocolType.KWP1281, slow_init_addr=0x01)
    protocol = Kwp1281Protocol(transport, target)
    protocol.connect()
    dtcs = protocol.read_dtcs()
    assert dtcs[0].raw_hex == "01 15 20"
    assert dtcs[0].code == "VAG 00277"
    live = protocol.read_live_pids()
    assert live
    assert live[0].raw_hex == "0D AC"
    assert live[0].numeric_value == 0xAC * 0x0D * 0.2   # formula 0x01: MW*NW*0.2 /min
    assert dtcs[0].status == "Elaboration 32"


def test_kwp1281_keyword_and_disconnect():
    transport = FakeKwp1281Transport()
    protocol = Kwp1281Protocol(transport, EcuTarget(name="VAG", protocol=ProtocolType.KWP1281, slow_init_addr=0x01))
    assert protocol.connect()
    assert protocol.keyword == 1281
    assert transport.tx_bytes[0] == 0x75          # complement of KW2 sent first
    assert protocol.keep_alive_interval_ms < 500  # ECU drops the link after ~500 ms


def test_kwp1281_measurement_formulas():
    from klinescan.core import kwp1281_formulas as F

    assert F.decode(0x05, 0x0A, 0xB4)[3] == (0xB4 - 100) * 0x0A * 0.1   # temperature
    assert F.decode(0x07, 0x64, 0x32)[3] == 0x32 * 0x64 * 0.01          # speed
    assert F.decode(0x11, 0x4F, 0x4B)[2] == "OK"                        # 2 ASCII chars
    assert F.decode(0xEE, 1, 2)[3] is None                              # unknown -> raw
