"""Deterministic pack normalization, overrides and reviewable diffs."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Iterable, List

from klinescan.core.ids import stable_id


def vehicle_id(pack_id: str, vehicle: Dict[str, Any]) -> str:
    return stable_id(pack_id, "variant", vehicle.get("make"), vehicle.get("model"), vehicle.get("year"), vehicle.get("label"))


def target_id(pack_id: str, vehicle: Dict[str, Any], target: Dict[str, Any]) -> str:
    return stable_id(vehicle_id(pack_id, vehicle), "module", target.get("name"), target.get("protocol"), target.get("ecu_addr", ""))


def canonical_pack(pack: Dict[str, Any]) -> Dict[str, Any]:
    """Return a stable representation suitable for hashing and golden tests."""
    result = copy.deepcopy(pack)
    source = result.get("source")
    if isinstance(source, dict):
        source.pop("imported_at", None)
        source.pop("path", None)
    return json.loads(json.dumps(result, sort_keys=True, separators=(",", ":")))


def apply_overrides(pack: Dict[str, Any], overrides: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply explicit field patches without mutating generated importer output.

    Override format::

        {"target_id": "...", "set": {"confidence": "curated"}}
        {"vehicle_id": "...", "set": {"make": "Toyota"}}
    """
    result = copy.deepcopy(pack)
    for override in overrides:
        fields = override.get("set", {})
        if not isinstance(fields, dict):
            continue
        wanted_vehicle = override.get("vehicle_id")
        wanted_target = override.get("target_id")
        for vehicle in result.get("vehicles", []):
            if wanted_vehicle and vehicle_id(result.get("pack_id", "custom"), vehicle) != wanted_vehicle:
                continue
            if wanted_vehicle:
                vehicle.update(fields)
            for target in vehicle.get("targets", []):
                if wanted_target and target_id(result.get("pack_id", "custom"), vehicle, target) != wanted_target:
                    continue
                if wanted_target:
                    target.update(fields)
    return result


def diff_packs(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    def records(pack: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        output: Dict[str, Dict[str, Any]] = {}
        for vehicle in pack.get("vehicles", []):
            output[vehicle_id(pack.get("pack_id", "custom"), vehicle)] = vehicle
            for target in vehicle.get("targets", []):
                output[target_id(pack.get("pack_id", "custom"), vehicle, target)] = target
        return output

    before, after = records(old), records(new)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(key for key in set(before) & set(after) if before[key] != after[key])
    return {"added": added, "changed": changed, "removed": removed,
            "summary": {"added": len(added), "changed": len(changed), "removed": len(removed)}}
