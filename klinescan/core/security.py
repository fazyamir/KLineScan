"""SecurityAccess (service 0x27) for UDS (ISO 14229-1) and KWP2000 (ISO 14230-3).

Flow: ``27 <level>`` (odd level) -> ``67 <level> <seed...>``; the tester computes a
key and sends ``27 <level+1> <key...>`` -> ``67 <level+1>``.  An all-zero seed
means the level is already unlocked.

Seed-to-key algorithms are manufacturer secrets and differ per ECU.  None are
shipped or guessed here.  A user who legitimately holds an algorithm (their own
ECU documentation, a licensed tool's specification) registers it by name and
names it in the pack (``security_algorithm``)::

    from klinescan.core.security import register_algorithm

    @register_algorithm("my_ecu_level1")
    def my_key(seed: bytes, level: int) -> bytes:
        ...
"""

from __future__ import annotations

from typing import Callable, Dict

from klinescan.core.errors import ConfigurationError, NegativeResponseError, ProtocolError

KeyFunction = Callable[[bytes, int], bytes]
_ALGORITHMS: Dict[str, KeyFunction] = {}

NRC_TEXT = {
    0x12: "sub-function (security level) not supported",
    0x22: "conditions not correct (e.g. engine running, wrong session)",
    0x24: "request sequence error",
    0x31: "request out of range",
    0x33: "security access denied",
    0x35: "invalid key",
    0x36: "exceeded number of attempts: the ECU locks access for a while",
    0x37: "required time delay not expired: wait (often 10 s or more) and retry",
}


def register_algorithm(name: str):
    def decorator(fn: KeyFunction) -> KeyFunction:
        _ALGORITHMS[name] = fn
        return fn
    return decorator


def get_algorithm(name: str) -> KeyFunction:
    try:
        return _ALGORITHMS[name]
    except KeyError:
        raise ConfigurationError(
            f"No seed/key algorithm named '{name}' is registered. Register the algorithm "
            "for this ECU with klinescan.core.security.register_algorithm().") from None


def available_algorithms() -> list:
    return sorted(_ALGORITHMS)


def security_access(exchange: Callable[[bytes], bytes], level: int, key_fn: KeyFunction) -> bool:
    """Run seed/key for ``level`` (odd).  ``exchange`` sends a request, returns the response.

    Raises ProtocolError with a readable reason when the ECU refuses.
    """
    if level % 2 == 0 or not 1 <= level <= 0x7D:
        raise ConfigurationError(f"security level must be odd (request seed), got 0x{level:02X}")
    try:
        response = exchange(bytes([0x27, level]))
        if len(response) < 2 or response[0] != 0x67 or response[1] != level:
            raise ProtocolError(f"unexpected seed response {response.hex(' ').upper()}")
        seed = bytes(response[2:])
        if not any(seed):
            return True  # already unlocked
        key = bytes(key_fn(seed, level))
        response = exchange(bytes([0x27, level + 1]) + key)
        if len(response) < 2 or response[0] != 0x67 or response[1] != level + 1:
            raise ProtocolError(f"unexpected key response {response.hex(' ').upper()}")
        return True
    except NegativeResponseError as exc:
        reason = NRC_TEXT.get(exc.nrc, f"NRC 0x{exc.nrc:02X}")
        raise ProtocolError(f"Security access level 0x{level:02X} refused: {reason}") from exc
