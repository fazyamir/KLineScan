"""Diagnostic protocol state machines and the single protocol registry."""

from typing import Dict, Type

from klinescan.core.errors import ConfigurationError
from klinescan.core.models import EcuTarget, ProtocolType
from klinescan.protocols.base import BaseProtocol
from klinescan.protocols.consult import ConsultProtocol
from klinescan.protocols.isotp_obd import IsoTpObdProtocol
from klinescan.protocols.kwp1281 import Kwp1281Protocol
from klinescan.protocols.kwp2000 import Kwp2000Protocol
from klinescan.protocols.uds import UdsProtocol

PROTOCOL_CLASSES: Dict[ProtocolType, Type[BaseProtocol]] = {
    ProtocolType.CAN_OBD: IsoTpObdProtocol,
    ProtocolType.UDS_CAN: UdsProtocol,
    ProtocolType.KWP1281: Kwp1281Protocol,
    ProtocolType.ISO9141: Kwp2000Protocol,
    ProtocolType.KWP2000_FAST: Kwp2000Protocol,
    ProtocolType.NISSAN_CONSULT: ConsultProtocol,
}


def protocol_class(protocol: ProtocolType) -> Type[BaseProtocol]:
    try:
        return PROTOCOL_CLASSES[protocol]
    except KeyError:
        raise ConfigurationError(f"No protocol implementation for {protocol}") from None


def make_protocol(transport, target: EcuTarget, frame_cb=None) -> BaseProtocol:
    return protocol_class(target.protocol)(transport, target, frame_cb)


__all__ = [
    "BaseProtocol", "ConsultProtocol", "IsoTpObdProtocol", "Kwp1281Protocol",
    "Kwp2000Protocol", "UdsProtocol", "PROTOCOL_CLASSES", "make_protocol", "protocol_class",
]
