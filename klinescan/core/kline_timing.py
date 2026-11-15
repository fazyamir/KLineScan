"""K-line (ISO 9141-2 / ISO 14230-2) timing constants with their sources.

Values are in milliseconds.  Where sources disagree the conservative value is
used and the disagreement is noted.

Sources
-------
[ISO14230-2:1999]  ISO 14230-2:1999 sample, normal timing table
    https://cdn.standards.iteh.ai/samples/23920/3f24042985114fba806b87d9e1edad8e/ISO-14230-2-1999.pdf
[KWP-RP]           Keyword Protocol 2000 Data Link Layer Recommended Practice v1.5
    https://www.internetsomething.com/kwp/kwp2000_recommended_guidlines.pdf
[EU2016/799]       Commission Implementing Regulation (EU) 2016/799, Annex IC App. 8
    (K-line fast initialisation timing for tachographs, ISO 14230 based)
    https://www.legislation.gov.uk/eur/2016/799/annex/1/appendix/8/2020-02-26/data.html
[ISO9141-2 guide]  ISO 9141-2:1994 standard guide (5-baud address framing)
    https://standards.iteh.ai/catalog/standards/iso/aa1fa48b-f92f-4965-be9e-0066aed09d3f/iso-9141-2-1994
[colinbourassa]    Automotive diagnostic protocols comparison (slow-init framing per variant)
    https://colinbourassa.github.io/car_stuff/diagnostic_protocols
"""

from __future__ import annotations

from dataclasses import dataclass

# --- 5-baud (slow) initialisation -------------------------------------------
FIVE_BAUD_BIT_MS = 200          # 5 bit/s -> 200 ms per bit [ISO9141-2 guide]
W1_MIN, W1_MAX = 60, 300        # end of address byte -> 0x55 sync [ISO14230-2:1999], [KWP-RP]
W2_MIN, W2_MAX = 5, 20          # sync -> KW1
W3_MIN, W3_MAX = 0, 20          # KW1 -> KW2
W4_MIN, W4_MAX = 25, 50         # KW2 -> tester's inverted KW2, and -> ECU's inverted address
W5_MIN = 300                    # bus idle before any (re)initialisation

# --- fast initialisation -----------------------------------------------------
T_INIL = 25                     # wake-up pattern low time, 25 +/- 1 ms [EU2016/799]
T_WUP = 50                      # whole wake-up pattern, 50 +/- 1 ms [EU2016/799]
T_IDLE = W5_MIN                 # idle before wake-up (uses W5) [ISO14230-2:1999]

# --- message timing (normal timing set) --------------------------------------
P1_MAX = 20                     # ECU inter-byte time 0..20 ms
P2_MIN, P2_MAX = 25, 50         # request end -> response start (normal timing) [ISO14230-2:1999]
P3_MIN, P3_MAX = 55, 5000       # response end -> next request; link drops after P3max [KWP-RP]
P4_MIN, P4_MAX = 0, 20          # tester inter-byte time [ISO14230-2:1999]
# ISO 9141-2 testers conventionally space request bytes; EU2016/799 requires
# P4 >= 5 ms for its K-line units.  5 ms is inside every cited range.
P4_DEFAULT_SLOW_INIT = 5
P4_DEFAULT_FAST_INIT = 0


@dataclass(frozen=True)
class KLineTiming:
    """Per-session timing, defaulting to the normal ISO 14230-2 set."""

    p2_max: int = P2_MAX
    p3_min: int = P3_MIN
    p4: int = P4_DEFAULT_FAST_INIT
    keepalive_ms: int = 2000      # well inside P3max (5000 ms)

    @property
    def response_timeout_s(self) -> float:
        # wait at least P2max, plus margin for USB/serial latency
        return (self.p2_max + 100) / 1000.0


def odd_parity_address(address: int) -> int:
    """ISO 9141-2 address byte: 7 data bits + odd parity in bit 7.

    The ISO 9141-2 guide describes the address as 8 bits LSB first where the
    eighth bit makes the count of ones odd.  ``0x33`` becomes ``0xB3``.
    """
    seven = address & 0x7F
    ones = bin(seven).count("1")
    return seven | (0x80 if ones % 2 == 0 else 0x00)
