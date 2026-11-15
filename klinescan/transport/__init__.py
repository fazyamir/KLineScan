"""Hardware transport abstraction layer.

Adapters are imported lazily so importing ``klinescan.transport.base`` (for
example from the registry) does not execute every adapter module and create an
import cycle while the package is still initialising.
"""

import importlib

from klinescan.transport.base import BaseTransport, TransportError

_LAZY_EXPORTS = {
    "AutoDetector": "klinescan.transport.auto_detect",
    "DiscoveredInterface": "klinescan.transport.auto_detect",
    "FakeEcuTransport": "klinescan.transport.fake_ecu",
    "KLSerialTransport": "klinescan.transport.kl_serial",
    "FtdiDirectTransport": "klinescan.transport.ftdi_direct",
    "Elm327Transport": "klinescan.transport.elm327",
    "KCanTransport": "klinescan.transport.kcan",
    "J2534Transport": "klinescan.transport.j2534",
    "SocketCanTransport": "klinescan.transport.socketcan",
}

__all__ = ["BaseTransport", "TransportError", *_LAZY_EXPORTS]


def __getattr__(name):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value
