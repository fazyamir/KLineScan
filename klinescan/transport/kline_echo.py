"""K-line echo cancellation shared by the KKL serial and direct FTDI transports.

A K-line interface ties the transmit and receive lines together, so every byte the
tester sends is read back.  Some cables cancel the echo in hardware, so the echo
is never seen.

The echo of a byte arrives while the byte is still on the wire.  An ECU cannot
answer a byte before it has received it, and it waits P2 (>= 25 ms on ISO 14230)
or its own handshake delay (KWP1281) before replying.  So the bytes read right
after a write are the echo, *in order*.  This module therefore:

* matches the echo as an exact prefix of the sent sequence (never "any byte equal
  to the next expected value"), inside a short window derived from the baud
  rate plus USB latency;
* hands every byte that is not part of that prefix back to the receive buffer;
* in ``auto`` mode decides once, on the first write, whether the cable echoes,
  then keeps that decision (``on`` / ``off``) for the session.  Users with an
  echo-cancelling cable can force ``off`` so no time is spent waiting.
"""

from __future__ import annotations

from typing import Callable, List, Optional

ECHO_MODES = ("auto", "on", "off")

# USB-serial bridges deliver received bytes in USB packets: FTDI after its latency
# timer (16 ms default, 1-2 ms when tuned), CH340/PL2303 similar.  Allow for it.
USB_LATENCY_MARGIN_S = 0.030


class EchoCanceller:
    def __init__(self, mode: str = "auto"):
        self.mode = mode if mode in ECHO_MODES else "auto"
        self.detected: Optional[bool] = None if self.mode == "auto" else (self.mode == "on")
        self.mismatches = 0

    @property
    def expect_echo(self) -> bool:
        return self.detected is not False

    def set_mode(self, mode: str) -> None:
        self.__init__(mode)

    def window(self, baudrate: int, nbytes: int) -> float:
        byte_time = 10.0 / max(1, baudrate)          # start + 8 data + stop
        return nbytes * byte_time + USB_LATENCY_MARGIN_S

    def consume(
        self,
        sent: bytes,
        read_byte: Callable[[float], bytes],
        baudrate: int,
    ) -> List[int]:
        """Read and drop the echo of ``sent``; return bytes that are *not* echo.

        ``read_byte(timeout)`` returns one byte or ``b""`` on timeout.
        """
        if not sent or not self.expect_echo:
            return []
        leftovers: List[int] = []
        matched = 0
        per_byte = self.window(baudrate, 1)
        first_timeout = self.window(baudrate, len(sent))
        while matched < len(sent):
            chunk = read_byte(first_timeout if matched == 0 else per_byte)
            if not chunk:
                break
            if chunk[0] == sent[matched]:
                matched += 1
                continue
            leftovers.append(chunk[0])
            break  # the echo is a strict prefix; anything else is ECU data

        if self.detected is None:  # auto: decide once
            self.detected = matched > 0
        elif matched != len(sent):
            self.mismatches += 1
        return leftovers
