"""
ISO 15765-2 (ISO-TP) transport channel.

Implements classic 8-byte ISO-TP with:
  * single frames (SF)
  * first/consecutive frames (FF/CF) in both directions
  * flow control (FC) with block size and separation time
  * optional padding
  * receive filtering by CAN identifier
  * strict sequence-number checking and classified timeouts

Both the OBD-II/CAN and UDS protocol modules share this implementation.  The
channel never invents a response: missing or malformed frames raise a
classified error.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from klinescan.core.errors import (
    MalformedFrameError,
    NegativeResponseError,
    ScanTimeout,
    UnsupportedOperationError,
)
from klinescan.core.models import EcuTarget
from klinescan.transport.base import BaseTransport, CanFrame, check_cancelled

PCI_SF = 0
PCI_FF = 1
PCI_CF = 2
PCI_FC = 3

P2_STAR_MAX_S = 5.0  # ISO 14229-2 P2*server_max after a response-pending NRC

FC_CTS = 0
FC_WAIT = 1
FC_OVERFLOW = 2


@dataclass
class IsoTpConfig:
    pad_byte: int = 0x00
    pad_to: int = 8
    block_size: int = 0
    stmin_ms: float = 0.0
    timeout: float = 0.6
    max_retries: int = 2
    # Only frames from ``rx_id`` are accepted.  With rx_id == 0 (responder not known yet)
    # the channel locks onto the first frame that is a plausible answer to the request.
    accept_any_rx_id: bool = False
    # ISO 15765-2 N_WFTmax: how many FC.WAIT frames are tolerated before aborting.
    max_wait_frames: int = 10


class IsoTpChannel:
    def __init__(
        self,
        transport: BaseTransport,
        target: EcuTarget,
        config: Optional[IsoTpConfig] = None,
        *,
        tx_id: Optional[int] = None,
        rx_id: Optional[int] = None,
        extended: bool = False,
    ):
        self.transport = transport
        self.target = target
        self.config = config or IsoTpConfig()
        self.tx_id = tx_id if tx_id is not None else target.can_tx_id
        self.rx_id = rx_id if rx_id is not None else target.can_rx_id
        self.extended = extended or bool(getattr(target, "can_extended_id", False))
        # ISO 15765-2 extended addressing: one address byte precedes every PCI byte.
        self.ext_tx = getattr(target, "can_ext_address", None)
        self.ext_rx = getattr(target, "can_rx_ext_address", None)
        self._request_sid: Optional[int] = None
        self._program_transport_ids()

    def _program_transport_ids(self) -> None:
        """Tell raw-CAN adapters which identifier to send on and which to accept."""
        if self._uses_passthrough():
            return  # ELM327/DoIP adapters are configured by the protocol's _prepare_adapter
        setter = getattr(self.transport, "set_can_ids", None)
        if setter is None:
            return
        try:
            setter(self.tx_id, self.rx_id, extended=self.extended)
        except UnsupportedOperationError:
            pass

    # -- public ------------------------------------------------------------
    def send(self, payload: bytes, timeout: Optional[float] = None) -> bytes:
        """Send an application payload and return the reassembled response."""
        if not payload:
            raise MalformedFrameError("empty ISO-TP payload")
        timeout = self.config.timeout if timeout is None else timeout

        if self._uses_passthrough():
            return self._passthrough(payload, timeout)

        self._request_sid = payload[0]
        if len(payload) <= self._sf_capacity():
            self._send_frame(self._single_frame(payload))
        else:
            self._send_multiframe(payload, timeout)
        response = self._receive_payload(timeout)
        # NRC 0x78 requestCorrectlyReceived-ResponsePending: the ECU needs more time.
        # Keep waiting up to P2*server max (5 s, ISO 14229-2) after each pending reply.
        while len(response) >= 3 and response[0] == 0x7F and response[2] == 0x78:
            response = self._receive_payload(P2_STAR_MAX_S)
        return self._raise_negative(response)

    def receive(self, timeout: Optional[float] = None) -> bytes:
        return self._raise_negative(
            self._receive_payload(self.config.timeout if timeout is None else timeout)
        )

    @staticmethod
    def _raise_negative(response: bytes) -> bytes:
        if len(response) >= 3 and response[0] == 0x7F:
            raise NegativeResponseError(response[1], response[2])
        return response

    # -- frame helpers -----------------------------------------------------
    def _sf_capacity(self) -> int:
        return 6 if self.ext_tx is not None else 7

    def _needs_padding(self) -> bool:
        return bool(self.config.pad_to and self.config.pad_to > 0)

    def _single_frame(self, payload: bytes) -> bytes:
        frame = bytes([len(payload) & 0x0F]) + payload
        if self._needs_padding() and len(frame) < self.config.pad_to:
            frame += bytes([self.config.pad_byte]) * (self.config.pad_to - len(frame))
        return frame

    def _send_multiframe(self, payload: bytes, timeout: float) -> None:
        length = len(payload)
        if length > 0xFFF:
            ff = bytes([0x10, 0x00]) + length.to_bytes(4, "big") + payload[:2]
            offset = 2
        else:
            room = 5 if self.ext_tx is not None else 6
            ff = bytes([0x10 | ((length >> 8) & 0x0F), length & 0xFF]) + payload[:room]
            offset = room
        self._send_frame(ff)

        block_size = self.config.block_size
        stmin = self.config.stmin_ms / 1000.0
        sequence = 1
        sent_in_block = 0
        waits = 0
        while offset < length:
            fc = self._receive_frame(timeout)
            if not fc.data or ((fc.data[0] >> 4) & 0x0F) != PCI_FC:
                raise MalformedFrameError(
                    f"expected flow control, got PCI 0x{fc.data[0]:02X}" if fc.data
                    else "empty flow control frame"
                )
            status = fc.data[0] & 0x0F
            if status == FC_WAIT:
                waits += 1
                if waits > self.config.max_wait_frames:
                    raise ScanTimeout(
                        f"ECU sent more than {self.config.max_wait_frames} flow-control WAIT frames"
                    )
                continue
            waits = 0
            if status == FC_OVERFLOW:
                raise MalformedFrameError("ECU reported ISO-TP receive overflow")
            if status != FC_CTS:
                raise MalformedFrameError(
                    f"expected flow control, got PCI 0x{fc.data[0]:02X}"
                    if fc.data
                    else "empty flow control frame"
                )
            fs_block = fc.data[1] if len(fc.data) > 1 else 0
            fs_stmin = self._decode_stmin(fc.data[2] if len(fc.data) > 2 else 0)
            if fs_stmin is not None:
                stmin = fs_stmin
            allowed = fs_block if fs_block else 0xFFFFFFFF
            sent_in_block = 0
            while offset < length and sent_in_block < allowed:
                chunk = payload[offset : offset + (6 if self.ext_tx is not None else 7)]
                offset += len(chunk)
                frame = bytes([0x20 | (sequence & 0x0F)]) + chunk
                self._send_frame(frame)
                sequence = (sequence + 1) & 0x0F
                sent_in_block += 1
                if stmin > 0 and offset < length:
                    time.sleep(stmin)
            block_size = fs_block

    def _receive_payload(self, timeout: float) -> bytes:
        frame = self._receive_frame(timeout)
        pci = (frame.data[0] >> 4) & 0x0F if frame.data else -1

        if pci == PCI_SF:
            length = frame.data[0] & 0x0F
            if length > len(frame.data) - 1:
                raise MalformedFrameError("ISO-TP single frame shorter than length")
            return frame.data[1 : 1 + length]

        if pci == PCI_FF:
            if len(frame.data) < 8:
                raise MalformedFrameError("ISO-TP first frame too short")
            total = ((frame.data[0] & 0x0F) << 8) | frame.data[1]
            if total == 0 and len(frame.data) >= 6:
                total = int.from_bytes(frame.data[2:6], "big")
                data = bytearray(frame.data[6:])
            else:
                data = bytearray(frame.data[2:])
            self._send_flow_control()
            expected_sequence = 1
            deadline = time.time() + timeout
            while len(data) < total:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise ScanTimeout(
                        f"ISO-TP reassembly timed out ({len(data)}/{total} bytes)"
                    )
                cf = self._receive_frame(remaining)
                if not cf.data or ((cf.data[0] >> 4) & 0x0F) != PCI_CF:
                    raise MalformedFrameError("expected ISO-TP consecutive frame")
                sequence = cf.data[0] & 0x0F
                if sequence != expected_sequence:
                    raise MalformedFrameError(
                        f"ISO-TP sequence error: expected {expected_sequence}, got {sequence}"
                    )
                expected_sequence = (expected_sequence + 1) & 0x0F
                data.extend(cf.data[1:])
            return bytes(data[:total])

        if pci == PCI_FC:
            return b""

        raise MalformedFrameError(
            f"unexpected ISO-TP PCI 0x{frame.data[0]:02X}" if frame.data else "empty frame"
        )

    def _send_flow_control(self) -> None:
        fc = bytes(
            [
                0x30 | (self.config.block_size & 0x0F),
                self._encode_stmin(self.config.stmin_ms),
                self.config.pad_byte,
                self.config.pad_byte,
                self.config.pad_byte,
                self.config.pad_byte,
                self.config.pad_byte,
                self.config.pad_byte,
            ]
        )
        self._send_frame(fc[:8])

    def _send_frame(self, data: bytes) -> None:
        data = bytes(data)
        if self.ext_tx is not None:
            data = (bytes([self.ext_tx]) + data)[:8]
        try:
            self.transport.send_frame(
                CanFrame(self.tx_id, data, extended=self.extended)
            )
        except UnsupportedOperationError:
            self.transport.write(data)

    def _receive_frame(self, timeout: float) -> CanFrame:
        deadline = time.monotonic() + timeout
        raw_frames = True
        while True:
            check_cancelled(self.transport)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ScanTimeout("no ISO-TP response from ECU")
            try:
                frame = self.transport.recv_frame(remaining)
            except UnsupportedOperationError:
                raw_frames = False
                break
            if frame is None:
                continue
            if self.rx_id and frame.arbitration_id != self.rx_id and not self.config.accept_any_rx_id:
                continue  # another ECU, or our own frame echoed by the controller
            data = bytes(frame.data)
            if self.ext_tx is not None:
                if self.ext_rx is not None and data[:1] != bytes([self.ext_rx]):
                    continue
                data = data[1:]
            if self.rx_id == 0:
                if frame.arbitration_id == self.tx_id or not self._plausible_response(data):
                    continue
                self.rx_id = frame.arbitration_id  # validated responder: lock onto it
                self._program_transport_ids()
            return CanFrame(frame.arbitration_id, data, extended=frame.extended)

        if not raw_frames:
            raw = self.transport.read(8, timeout=max(0.0, deadline - time.monotonic()))
            if not raw:
                raise ScanTimeout("no ISO-TP response from ECU")
            return CanFrame(self.rx_id, raw)
        raise ScanTimeout("no ISO-TP response from ECU")  # pragma: no cover

    def _plausible_response(self, data: bytes) -> bool:
        """Is this the first frame of an answer to the request we sent?"""
        if not data:
            return False
        pci = (data[0] >> 4) & 0x0F
        if pci == PCI_SF:
            sid = data[1] if len(data) > 1 else None
        elif pci == PCI_FF:
            sid = data[2] if len(data) > 2 else None
        elif pci == PCI_FC:
            return True  # flow control for our first frame
        else:
            return False
        if self._request_sid is None or sid is None:
            return sid is not None
        return sid in (0x7F, (self._request_sid + 0x40) & 0xFF)

    def _uses_passthrough(self) -> bool:
        return bool(
            getattr(self.transport, "supports_obd_passthrough", False)
            or self.transport.capabilities().supports_obd_passthrough
        )

    def _passthrough(self, payload: bytes, timeout: float) -> bytes:
        sender = getattr(self.transport, "send_obd_payload", None)
        if sender is None:
            raise UnsupportedOperationError(
                "transport advertises OBD passthrough but implements no sender"
            )
        response = sender(payload, timeout=timeout)
        if not response:
            raise ScanTimeout("ELM327/STN adapter returned no data")
        if response[0] == 0x7F and len(response) >= 3:
            raise NegativeResponseError(response[1], response[2])
        return response

    @staticmethod
    def _decode_stmin(value: int) -> Optional[float]:
        if value <= 0x7F:
            return value / 1000.0
        if 0xF1 <= value <= 0xF9:
            return (value - 0xF0) / 10000.0
        return None

    @staticmethod
    def _encode_stmin(ms: float) -> int:
        if ms <= 0:
            return 0
        if ms < 0.127:
            return int(round(ms * 1000)) & 0x7F
        return 0x7F
