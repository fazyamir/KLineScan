"""Decode user-supplied container files into report dictionaries.

``decode_file`` recognises the supported layouts (multi-pack, legacy
16-byte, legacy 28-byte, sectioned troubleshooting pack and reprogramming
campaign index); ``merge_brands`` combines the per-file brand catalogues.
Both are pure functions over the bytes the caller supplies, so no data is
bundled with the project.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from klinescan.containers import container as C
from klinescan.containers.lha import LhaError, extract_member


def decode_file(path: Path, extract: Path | None) -> dict:
    raw = path.read_bytes()
    report: dict = {"file": path.name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if b"TROUBLE SHOOTING PACK" in raw[:0x60]:
        report.update(container="plain", family="tspack")
        report["catalog"] = C.decode_tspack(raw)
        if extract:
            out = extract / path.stem
            out.mkdir(parents=True, exist_ok=True)
            n = 0
            for section in report["catalog"]["sections"]:
                for rec in section["records"]:
                    if rec.get("kind") != "lha":
                        continue
                    try:
                        member, data = extract_member(raw, rec["offset"])
                    except LhaError as exc:
                        rec["error"] = str(exc)
                        continue
                    target = out / member.name.replace("\\", "/").lstrip("./")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    n += 1
            report["extracted"] = n
        return report

    container, image, info = C.unwrap(raw)
    image_sha = hashlib.sha256(image).hexdigest()
    report.update(container=container, image_sha256=image_sha, image_size=len(image), **info)
    report["family"] = C.classify(image)
    layout = C.layout_for(image_sha, image)
    if layout is None and report["family"] == "repro":
        report["catalog"] = C.decode_repro(image)
        return report
    if layout is None and report["family"] == "c1":
        report["catalog"] = C.decode_c1(image)
        report.update(C.build_brand_catalog("c1", report["catalog"]))
        return report
    if layout is None:
        report["catalog"] = None
        report["note"] = "no verified catalogue layout for this image yet"
        return report
    report["family"] = layout.family
    report["catalog"] = C.decode_catalog(image, layout)
    brand = {"kp": "KIA", "s": "HYUNDAI"}[layout.family]
    report.update(C.build_brand_catalog(layout.family, report["catalog"], brand))
    if extract:
        out = extract / path.stem
        out.mkdir(parents=True, exist_ok=True)
        seen = set()
        for rec in report["catalog"]["records"]:
            for h in rec["handlers"]:
                name = h.get("module") or h.get("name")
                if not name or name in seen or h.get("builtin"):
                    continue
                seen.add(name)
                if layout.family == "s" and h.get("address") is not None:
                    try:
                        _, data = extract_member(image, h["address"])
                        (out / f"{name}.bin").write_bytes(data)
                    except LhaError as exc:
                        h["error"] = str(exc)
                elif layout.family == "kp" and h.get("module_offset") is not None:
                    (out / f"{name}.blk").write_bytes(image[h["module_offset"]: h["module_offset"] + 3000])
        report["extracted"] = len(seen)
    return report


def merge_brands(reports: list[dict]) -> dict:
    """Merge per-file brand catalogues; identical entries collect their source files."""
    merged: dict = {}
    for rep in reports:
        for brand in rep.get("brands", []):
            b = merged.setdefault(brand["name"], {})
            for veh in brand["vehicles"]:
                v = b.setdefault((veh["name"], veh["sub"]), {})
                for sysm in veh["systems"]:
                    s = v.setdefault(sysm["name"], {})
                    for entry in sysm["entries"]:
                        key = (tuple(entry["variant"]), tuple(e.get("id") or "" for e in entry["ecus"]))
                        item = s.setdefault(key, {"variant": entry["variant"], "ecus": entry["ecus"], "files": []})
                        if rep["file"] not in item["files"]:
                            item["files"].append(rep["file"])
    brands = []
    for bname in sorted(merged):
        vehicles = []
        for (vname, sub), systems in sorted(merged[bname].items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            vehicles.append({"name": vname, "sub": sub, "systems": [
                {"name": sname, "entries": list(entries.values())} for sname, entries in systems.items()]})
        brands.append({"name": bname, "vehicles": vehicles})
    return {
        "brands": brands,
        "stats": {"brands": len(brands), "vehicles": sum(len(b["vehicles"]) for b in brands),
                  "entries": sum(len(s["entries"]) for b in brands for v in b["vehicles"] for s in v["systems"])},
        "sources": [{"file": r["file"], "sha256": r["sha256"], "family": r["family"]} for r in reports],
    }
