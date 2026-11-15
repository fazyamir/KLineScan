"""Unit tests for ISO 15765-2 (ISO-TP) Single-Frame and Multi-Frame Reassembly."""

from collections import deque
import pytest
from klinescan.core.models import EcuTarget, ProtocolType
from klinescan.protocols.isotp_obd import IsoTpObdProtocol
from klinescan.transport.base import BaseTransport


class MockIsoTpTransport(BaseTransport):
    def __init__(self):
        super().__init__()
        self.tx_frames = []
        self.rx_queue = deque()

    def open(self, port: str, baudrate: int) -> None:
        pass

    def close(self) -> None:
        pass

    def set_baudrate(self, baudrate: int) -> None:
        pass

    def flush(self) -> None:
        self.rx_queue.clear()

    def write(self, data: bytes) -> None:
        self.tx_frames.append(data)

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        if self.rx_queue:
            return self.rx_queue.popleft()
        return b""


def test_isotp_single_frame_padding_and_exchange():
    transport = MockIsoTpTransport()
    target = EcuTarget(name="CAN Engine", protocol=ProtocolType.CAN_OBD)
    proto = IsoTpObdProtocol(transport, target)

    # Pad 2 bytes (0x01, 0x00) -> 8 bytes [0x02, 0x01, 0x00, 0, 0, 0, 0, 0]
    padded = proto._pad_sf(bytes([0x01, 0x00]))
    assert len(padded) == 8
    assert padded[0] == 0x02
    assert padded[1:3] == bytes([0x01, 0x00])

    # Simulate single frame response from ECU: [0x06, 0x41, 0x00, 0xBE, 0x3E, 0xB8, 0x11, 0x00]
    transport.rx_queue.append(bytes([0x06, 0x41, 0x00, 0xBE, 0x3E, 0xB8, 0x11, 0x00]))
    resp = proto._exchange(bytes([0x01, 0x00]))
    assert len(resp) == 6
    assert resp == bytes([0x41, 0x00, 0xBE, 0x3E, 0xB8, 0x11])


def test_isotp_multi_frame_reassembly():
    transport = MockIsoTpTransport()
    target = EcuTarget(name="CAN Engine", protocol=ProtocolType.CAN_OBD)
    proto = IsoTpObdProtocol(transport, target)

    # Multi-frame response: total length 14 bytes
    # FF: PCI 0x10, len 0x0E (14 bytes), data: [0x49, 0x02, 0x01, 'V', 'I', 'N'] (6 bytes)
    ff = bytes([0x10, 0x0E, 0x49, 0x02, 0x01, ord("V"), ord("I"), ord("N")])
    # CF1: PCI 0x21, data: ['1', '2', '3', '4', '5', '6', '7'] (7 bytes)
    cf1 = bytes([0x21, ord("1"), ord("2"), ord("3"), ord("4"), ord("5"), ord("6"), ord("7")])
    # CF2: PCI 0x22, data: ['8', 0, 0, 0, 0, 0, 0] (1 byte needed to complete 14 bytes)
    cf2 = bytes([0x22, ord("8"), 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

    transport.rx_queue.append(ff)
    transport.rx_queue.append(cf1)
    transport.rx_queue.append(cf2)

    # Request VIN (0x09, 0x02)
    payload = proto._exchange(bytes([0x09, 0x02]))

    # Verify flow control was sent: [0x30, 0x00, 0x00, 0, 0, 0, 0, 0]
    fc_sent = any(f[0] == 0x30 for f in transport.tx_frames)
    assert fc_sent, "Flow Control (CTS) should have been transmitted"

    # Verify reassembled length and content
    assert len(payload) == 14
    assert payload[:3] == bytes([0x49, 0x02, 0x01])
    assert payload[3:] == b"VIN12345678"
