"""Diagnostic Pack / ROM Manager for KLineScan."""

import json
import logging
import os
import zipfile
from typing import Dict, List, Optional
from klinescan.core.models import (
    ActuatorDefinition,
    EcuTarget,
    LiveParamDefinition,
    SpecialFunctionDefinition,
    VehicleProfile,
)
from klinescan.core.catalog import CatalogDatabase
from klinescan.core.pack_schema import parse_hex_bytes, parse_int, parse_protocol, validate_pack_dict

log = logging.getLogger(__name__)


class DiagnosticPack:
    def __init__(self, pack_id: str, name: str, version: str, region: str, vehicles: List[VehicleProfile]):
        self.pack_id = pack_id
        self.name = name
        self.version = version
        self.region = region
        self.vehicles = vehicles


class PackManager:
    def __init__(self, packs_dir: str = ""):
        if not packs_dir:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.packs_dir = os.path.join(base_dir, "packs")
        else:
            self.packs_dir = packs_dir
        self.packs: Dict[str, DiagnosticPack] = {}
        self.errors: List[str] = []   # "<file>: <problem>" for every pack that failed to load
        # Runtime index: JSON remains the portable source format, while this
        # in-memory catalog provides normalized search/provenance metadata.
        self.catalog = CatalogDatabase(":memory:")
        self.refresh()

    def refresh(self) -> None:
        self.packs.clear()
        self.errors.clear()
        self.catalog.connection.executescript(
            "DELETE FROM provenance; DELETE FROM applicability; DELETE FROM module_def; "
            "DELETE FROM connection_profile; DELETE FROM vehicle_variant; DELETE FROM pack; DELETE FROM source;"
        )
        self.catalog.connection.commit()
        if not os.path.exists(self.packs_dir):
            return

        for fname in os.listdir(self.packs_dir):
            full_path = os.path.join(self.packs_dir, fname)
            if fname.lower().endswith(".json"):
                self._load_json_pack(full_path)
            elif fname.lower().endswith(".zip"):
                self._load_zip_pack(full_path)

    def _load_json_pack(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            self._fail(os.path.basename(path), f"cannot read JSON: {exc}")
            return
        self._add_pack(data, os.path.basename(path))

    def _load_zip_pack(self, path: str) -> None:
        try:
            with zipfile.ZipFile(path, "r") as z:
                for name in z.namelist():
                    if name.lower().endswith(".json"):
                        try:
                            with z.open(name) as f:
                                data = json.load(f)
                        except ValueError as exc:
                            self._fail(f"{os.path.basename(path)}/{name}", f"cannot read JSON: {exc}")
                            continue
                        self._add_pack(data, f"{os.path.basename(path)}/{name}")
        except (OSError, zipfile.BadZipFile) as exc:
            self._fail(os.path.basename(path), f"cannot open ZIP: {exc}")

    def _fail(self, source: str, problem: str) -> None:
        message = f"{source}: {problem}"
        self.errors.append(message)
        log.error("pack not loaded: %s", message)

    def _add_pack(self, data: dict, source: str) -> Optional[DiagnosticPack]:
        """Validate, parse and register one pack.  Invalid packs are rejected, never half-loaded."""
        ok, problems = validate_pack_dict(data)
        if not ok:
            for problem in problems:
                self._fail(source, problem)
            return None
        try:
            pack = self._parse_pack_data(data)
        except (ValueError, TypeError, KeyError) as exc:
            self._fail(source, str(exc))
            return None
        self.packs[pack.pack_id] = pack
        try:
            self.catalog.import_pack(data, source)
        except Exception as exc:  # catalogue index is secondary: keep the pack usable
            log.warning("catalog index for %s failed: %s", source, exc)
        return pack

    def _parse_pack_data(self, data: dict) -> Optional[DiagnosticPack]:
        pack_id = data.get("pack_id", "custom")
        name = data.get("name", "Custom Pack")
        version = data.get("version", "1.0")
        region = data.get("region", "Global")
        vehicles_raw = data.get("vehicles", [])

        vehicles: List[VehicleProfile] = []
        for v in vehicles_raw:
            targets: List[EcuTarget] = []
            for t in v.get("targets", []):
                proto = parse_protocol(t["protocol"])

                actuators: List[ActuatorDefinition] = []
                for act in t.get("actuators", []):
                    actuators.append(
                        ActuatorDefinition(
                            id=act.get("id", ""),
                            name=act.get("name", ""),
                            description=act.get("description", ""),
                            payload_start=parse_hex_bytes(act.get("payload_start", ""), "payload_start"),
                            payload_stop=parse_hex_bytes(act.get("payload_stop", ""), "payload_stop"),
                            duration_seconds=float(act.get("duration_seconds", 5.0)),
                            warning=act.get("warning", ""),
                        )
                    )

                specials: List[SpecialFunctionDefinition] = []
                for sp in t.get("special_functions", []):
                    specials.append(
                        SpecialFunctionDefinition(
                            id=sp.get("id", ""),
                            name=sp.get("name", ""),
                            description=sp.get("description", ""),
                            security_level=parse_int(sp.get("security_level", 0), "security_level"),
                            command=parse_hex_bytes(sp.get("command", ""), "command"),
                            steps=sp.get("steps", []),
                        )
                    )

                live_params = [
                    LiveParamDefinition(
                        name=lp["name"],
                        request=parse_hex_bytes(lp["request"], "request"),
                        offset=parse_int(lp["offset"], "offset"),
                        length=parse_int(lp.get("length", 1), "length"),
                        formula=str(lp.get("formula", "A")),
                        unit=str(lp.get("unit", "")),
                        category=str(lp.get("category", "Engine")),
                        pid=parse_int(lp.get("pid", 0), "pid"),
                    )
                    for lp in t.get("live_params", []) or []
                ]

                def num(key, default):
                    return parse_int(t[key], key) if key in t else default

                ext = t.get("can_ext_address")
                rx_ext = t.get("can_rx_ext_address")
                target = EcuTarget(
                    name=t.get("name", "Module"),
                    protocol=proto,
                    baudrate=num("baudrate", 10400),
                    slow_init_addr=num("slow_init_addr", 0x33),
                    tester_addr=num("tester_addr", 0xF1),
                    ecu_addr=num("ecu_addr", 0x11),
                    header_format=num("header_format", 0x80),
                    explicit_length=bool(t.get("explicit_length", False)),
                    can_tx_id=num("can_tx_id", 0x7DF),
                    can_rx_id=num("can_rx_id", 0x7E8),
                    use_obd_header=bool(t.get("use_obd_header", False)),
                    pin=num("pin", 7),
                    keep_alive_ms=num("keep_alive_ms", 2000),
                    can_bitrate=num("can_bitrate", 0),
                    can_extended_id=bool(t.get("can_extended_id", False)),
                    can_ext_address=parse_int(ext, "can_ext_address") if ext is not None else None,
                    can_rx_ext_address=parse_int(rx_ext, "can_rx_ext_address") if rx_ext is not None else None,
                    doip_address=str(t.get("doip_address", "")),
                    doip_logical_address=num("doip_logical_address", 0),
                    five_baud_parity=str(t.get("five_baud_parity", "odd")),
                    echo_mode=str(t.get("echo_mode", "")),
                    live_params=live_params,
                    measuring_groups=[parse_int(g, "measuring_groups") for g in t.get("measuring_groups", [])],
                    security_algorithm=str(t.get("security_algorithm", "")),
                    security_level=num("security_level", 0),
                    actuators=actuators,
                    special_functions=specials,
                    confidence=str(t.get("confidence", "imported" if t.get("source_row") else "curated")),
                    source_ref=str(t.get("source_row", t.get("source_ref", ""))),
                )
                targets.append(target)

            vp = VehicleProfile(
                label=v.get("label", "Vehicle"),
                make=v.get("make", ""),
                model=v.get("model", ""),
                year=v.get("year", ""),
                system_type=v.get("system_type", ""),
                targets=targets,
            )
            vehicles.append(vp)

        return DiagnosticPack(pack_id=pack_id, name=name, version=version, region=region, vehicles=vehicles)

    def get_all_profiles(self) -> List[VehicleProfile]:
        profiles: List[VehicleProfile] = []
        for pack in self.packs.values():
            profiles.extend(pack.vehicles)
        return profiles
