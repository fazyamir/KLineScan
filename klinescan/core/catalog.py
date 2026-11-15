"""Normalized SQLite catalog for vehicle/module pack metadata.

The catalog is an index, not a replacement for the existing diagnostic model.
Legacy JSON packs remain importable and the worker continues to receive
``VehicleProfile``/``EcuTarget`` instances.  This layer makes search,
deduplication and provenance explicit as the number of packs grows.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from klinescan.core.ids import stable_id


SCHEMA_VERSION = 1


class CatalogDatabase:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.initialize()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS catalog_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT '',
                file_sha256 TEXT NOT NULL DEFAULT '',
                imported_at TEXT NOT NULL DEFAULT '',
                license_note TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS pack (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                region TEXT NOT NULL DEFAULT '',
                source_id TEXT REFERENCES source(id)
            );
            CREATE TABLE IF NOT EXISTS vehicle_variant (
                id TEXT PRIMARY KEY,
                make TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                system_type TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL,
                pack_id TEXT NOT NULL REFERENCES pack(id)
            );
            CREATE TABLE IF NOT EXISTS connection_profile (
                id TEXT PRIMARY KEY,
                protocol TEXT NOT NULL,
                pin INTEGER NOT NULL,
                baudrate INTEGER NOT NULL,
                init_method TEXT NOT NULL,
                ecu_addr INTEGER NOT NULL,
                tester_addr INTEGER NOT NULL,
                settings_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS module_def (
                id TEXT PRIMARY KEY,
                system_key TEXT NOT NULL,
                name TEXT NOT NULL,
                connection_profile_id TEXT NOT NULL REFERENCES connection_profile(id),
                capabilities_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS applicability (
                variant_id TEXT NOT NULL REFERENCES vehicle_variant(id),
                module_id TEXT NOT NULL REFERENCES module_def(id),
                confidence TEXT NOT NULL DEFAULT 'imported',
                notes TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (variant_id, module_id)
            );
            CREATE TABLE IF NOT EXISTS provenance (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                source_id TEXT NOT NULL REFERENCES source(id),
                source_ref TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'imported',
                PRIMARY KEY (entity_type, entity_id, source_id, source_ref)
            );
            CREATE INDEX IF NOT EXISTS idx_variant_make ON vehicle_variant(make);
            CREATE INDEX IF NOT EXISTS idx_variant_model ON vehicle_variant(model);
            CREATE INDEX IF NOT EXISTS idx_variant_year ON vehicle_variant(year);
            CREATE INDEX IF NOT EXISTS idx_module_system ON module_def(system_key);
            """
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _profile_settings(target: Dict[str, Any]) -> Dict[str, Any]:
        excluded = {"name", "actuators", "special_functions", "source_row", "source_softw", "source_serline", "source_baud", "source_data"}
        return {key: target[key] for key in sorted(target) if key not in excluded}

    def import_pack(self, data: Dict[str, Any], source_name: str = "") -> int:
        """Import one legacy or normalized JSON pack idempotently.

        Returns the number of vehicle variants imported.
        """
        pack_id = str(data.get("pack_id", "custom"))
        source_data = data.get("source", {}) if isinstance(data.get("source"), dict) else {}
        source_id = stable_id("source", pack_id, source_data.get("sha256", source_name))
        self.connection.execute(
            """INSERT OR REPLACE INTO source(id, kind, name, version, file_sha256, imported_at, license_note)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (source_id, "diagnostic_pack", source_name or pack_id,
             str(data.get("version", "")), str(source_data.get("sha256", "")),
             str(source_data.get("imported_at", "")), str(data.get("license_note", ""))),
        )
        self.connection.execute(
            """INSERT OR REPLACE INTO pack(id, name, version, region, source_id)
               VALUES(?, ?, ?, ?, ?)""",
            (pack_id, str(data.get("name", pack_id)), str(data.get("version", "1.0")),
             str(data.get("region", "")), source_id),
        )

        count = 0
        for vehicle in data.get("vehicles", []):
            if not isinstance(vehicle, dict):
                continue
            make = str(vehicle.get("make", ""))
            model = str(vehicle.get("model", ""))
            year = str(vehicle.get("year", ""))
            label = str(vehicle.get("label", "Vehicle"))
            variant_id = stable_id(pack_id, "variant", make, model, year, label)
            self.connection.execute(
                """INSERT OR REPLACE INTO vehicle_variant
                   (id, make, model, year, system_type, display_name, pack_id)
                   VALUES(?, ?, ?, ?, ?, ?, ?)""",
                (variant_id, make, model, year, str(vehicle.get("system_type", "")), label, pack_id),
            )
            self.connection.execute(
                """INSERT OR REPLACE INTO provenance(entity_type, entity_id, source_id, source_ref, confidence)
                   VALUES('vehicle_variant', ?, ?, ?, 'imported')""",
                (variant_id, source_id, str(vehicle.get("source_row", ""))),
            )
            count += 1

            for target in vehicle.get("targets", []):
                if not isinstance(target, dict):
                    continue
                settings = self._profile_settings(target)
                settings_json = json.dumps(settings, sort_keys=True, separators=(",", ":"))
                profile_id = "profile-" + hashlib.sha256(settings_json.encode()).hexdigest()[:20]
                self.connection.execute(
                    """INSERT OR REPLACE INTO connection_profile
                       (id, protocol, pin, baudrate, init_method, ecu_addr, tester_addr, settings_json)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                    (profile_id, str(target.get("protocol", "")), int(target.get("pin", 7)),
                     int(target.get("baudrate", 10400)),
                     "slow" if "slow" in str(target.get("protocol", "")).lower() else "fast",
                     int(target.get("ecu_addr", 0x11)), int(target.get("tester_addr", 0xF1)), settings_json),
                )
                system_key = stable_id(str(target.get("name", "module")).split(" ")[0])
                module_id = stable_id(pack_id, "module", system_key, target.get("name", "Module"), profile_id)
                capabilities = {
                    "actuators": target.get("actuators", []),
                    "special_functions": target.get("special_functions", []),
                }
                self.connection.execute(
                    """INSERT OR REPLACE INTO module_def
                       (id, system_key, name, connection_profile_id, capabilities_json)
                       VALUES(?, ?, ?, ?, ?)""",
                    (module_id, system_key, str(target.get("name", "Module")), profile_id,
                     json.dumps(capabilities, sort_keys=True)),
                )
                self.connection.execute(
                    """INSERT OR REPLACE INTO applicability(variant_id, module_id, confidence, notes)
                       VALUES(?, ?, ?, ?)""",
                    (variant_id, module_id, "imported", ""),
                )
                self.connection.execute(
                    """INSERT OR REPLACE INTO provenance(entity_type, entity_id, source_id, source_ref, confidence)
                       VALUES('module_def', ?, ?, ?, 'imported')""",
                    (module_id, source_id, str(target.get("source_row", ""))),
                )
        self.connection.commit()
        return count

    def search_variants(self, query: str = "", make: str = "", model: str = "", year: str = "") -> List[sqlite3.Row]:
        terms = [f"%{query.strip().lower()}%", make, model, year]
        return list(self.connection.execute(
            """SELECT DISTINCT v.* FROM vehicle_variant v
               LEFT JOIN applicability a ON a.variant_id = v.id
               LEFT JOIN module_def m ON m.id = a.module_id
               LEFT JOIN connection_profile c ON c.id = m.connection_profile_id
               WHERE (? = '' OR lower(v.display_name || ' ' || v.make || ' ' || v.model || ' ' || v.year || ' ' || m.name || ' ' || c.protocol) LIKE ?)
                 AND (? = '' OR v.make = ?) AND (? = '' OR v.model = ?) AND (? = '' OR v.year = ?)
               ORDER BY v.make, v.model, v.year, v.display_name""",
            (terms[0], terms[0], make, make, model, model, year, year),
        ))
