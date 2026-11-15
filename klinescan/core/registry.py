"""
Registry, Plugin Decorators, and Capability Negotiation for KLineScan.
Enables new transports, protocols, and decoders to self-register declaratively.
"""

import importlib
import pkgutil
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from klinescan.transport.base import TransportCapabilities


_TRANSPORT_REGISTRY: Dict[str, Type[Any]] = {}
_TRANSPORT_CAPABILITIES: Dict[str, TransportCapabilities] = {}
_PROTOCOL_REGISTRY: Dict[str, Type[Any]] = {}
_DECODER_REGISTRY: Dict[str, Callable[..., Any]] = {}


def register_transport(name: str, capabilities: Optional[TransportCapabilities] = None):
    """Decorator to register a hardware transport class."""
    def decorator(cls: Type[Any]):
        canonical = name.strip().lower()
        _TRANSPORT_REGISTRY[canonical] = cls
        if capabilities:
            _TRANSPORT_CAPABILITIES[canonical] = capabilities
        elif hasattr(cls, "get_capabilities"):
            # Provide default capabilities if class exposes them
            _TRANSPORT_CAPABILITIES[canonical] = TransportCapabilities(
                supports_can=getattr(cls, "SUPPORTS_CAN", False),
                supports_kline=getattr(cls, "SUPPORTS_KLINE", True),
                supports_fast_init=getattr(cls, "SUPPORTS_FAST_INIT", True),
                supports_5baud=getattr(cls, "SUPPORTS_5BAUD", True),
            )
        else:
            _TRANSPORT_CAPABILITIES[canonical] = TransportCapabilities()
        return cls
    return decorator


def register_protocol(name: str):
    """Decorator to register a diagnostic protocol class."""
    def decorator(cls: Type[Any]):
        canonical = name.strip().lower()
        _PROTOCOL_REGISTRY[canonical] = cls
        return cls
    return decorator


def register_decoder(name: str):
    """Decorator to register a custom PID / frame decoder function."""
    def decorator(fn: Callable[..., Any]):
        canonical = name.strip().lower()
        _DECODER_REGISTRY[canonical] = fn
        return fn
    return decorator


def get_transport_class(name: str) -> Optional[Type[Any]]:
    return _TRANSPORT_REGISTRY.get(name.strip().lower())


def get_transport_capabilities(name: str) -> TransportCapabilities:
    return _TRANSPORT_CAPABILITIES.get(name.strip().lower(), TransportCapabilities())


def get_protocol_class(name: str) -> Optional[Type[Any]]:
    return _PROTOCOL_REGISTRY.get(name.strip().lower())


def get_decoder(name: str) -> Optional[Callable[..., Any]]:
    return _DECODER_REGISTRY.get(name.strip().lower())


def list_registered_transports() -> List[str]:
    return sorted(list(_TRANSPORT_REGISTRY.keys()))


def list_registered_protocols() -> List[str]:
    return sorted(list(_PROTOCOL_REGISTRY.keys()))


def validate_transport_for_target(transport_name: str, target: Any) -> Tuple[bool, str]:
    """
    Validates whether the selected hardware transport can physically communicate
    with the target ECU protocol (CAN vs K-Line vs J1850).
    """
    caps = get_transport_capabilities(transport_name)
    proto_str = getattr(target.protocol, "value", str(target.protocol)).lower()

    if "can" in proto_str and not caps.supports_can:
        return (
            False,
            f"The target ECU protocol ({target.protocol}) requires CAN bus hardware (pins 6/14). "
            f"The selected transport '{transport_name}' does not support CAN.",
        )

    if ("iso 9141" in proto_str or "kwp2000" in proto_str) and not caps.supports_kline:
        return (
            False,
            f"The target ECU protocol ({target.protocol}) requires K-Line hardware (pin 7). "
            f"The selected transport '{transport_name}' does not support K-Line.",
        )

    if "j1850" in proto_str:
        if "vpw" in proto_str and not caps.supports_j1850_vpw:
            return False, f"SAE J1850 VPW protocol is not supported by '{transport_name}'."
        if "pwm" in proto_str and not caps.supports_j1850_pwm:
            return False, f"SAE J1850 PWM protocol is not supported by '{transport_name}'."

    return True, "Compatible"


def discover_plugins() -> None:
    """Discovers and imports modules in transport and protocol packages."""
    for pkg_name in ("klinescan.transport", "klinescan.protocols"):
        try:
            pkg = importlib.import_module(pkg_name)
            if hasattr(pkg, "__path__"):
                for _, mod_name, _ in pkgutil.iter_modules(pkg.__path__):
                    try:
                        importlib.import_module(f"{pkg_name}.{mod_name}")
                    except Exception:
                        pass
        except Exception:
            pass
