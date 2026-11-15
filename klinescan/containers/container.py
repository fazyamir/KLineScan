"""Read-only decoders for user-supplied diagnostic container files.

Every function here is read-only and operates on bytes the caller supplies.
No container images, catalogues or other decoded data are bundled with the
project.  Offsets are physical offsets into the *unwrapped* image (after LHA
extraction for archived containers).

Supported layouts
-----------------
``kp``      legacy 16-byte path records (4-character module tags)
``s``       legacy 28-byte path records (6-character LHA modules)
``c1``      multi-pack image (30-byte path records per make)
``tspack``  sectioned text and bitmap pack
``repro``   ECU reprogramming campaign index
"""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass, field
from typing import Iterable

from .lha import LhaError, extract_member, parse_member

BANK = 0x4000
PACK = 0x400000
RECORD_TEXT = 26


# ---------------------------------------------------------------- containers

def unwrap(raw: bytes) -> tuple[str, bytes, dict]:
    """Return (container, image, info) following ``Cpu::loadRom`` rules."""
    if raw[:2] == b"MZ":
        raise NotImplementedError("self-extracting RAR wrapper: extract with unrar first")
    if len(raw) > 7 and raw[2:5] == b"-lh":
        member, image = extract_member(raw, 0)
        return "lha", image, {"member": member.name, "method": member.method,
                              "member_sha256": hashlib.sha256(image).hexdigest()}
    return "plain", raw, {}


def text_at(image: bytes, offset: int, width: int = RECORD_TEXT) -> str:
    chunk = image[offset: offset + width]
    return chunk.split(b"\0", 1)[0].decode("latin-1").strip()


def pool(image: bytes, base: int, index: int, width: int = RECORD_TEXT) -> str | None:
    """1-based fixed-width string pool lookup (index 0 means "none")."""
    if not index:
        return None
    return text_at(image, base + (index - 1) * width, width)


# ------------------------------------------------------------------ profiles

@dataclass(frozen=True)
class CatalogLayout:
    family: str
    name: str
    image_sha256: str
    records: int
    record_size: int
    fields: tuple[str, ...]
    pools: dict = field(default_factory=dict)        # field -> pool base
    handler_names: int = 0
    handler_name_size: int = 4
    module_scan: tuple[int, int] = (0, 0)            # kp: paragraph scan range
    module_pointers: int = 0                         # s: (bank, off) table
    max_model: int = 255


LAYOUT_KP = CatalogLayout(
    family="kp", name="Legacy 16-byte pack",
    image_sha256="19825688d5034724af4a2114660b81a07b5ba60b5ede78984783200e46d0b9af",
    records=0x178928, record_size=16,
    fields=("model", "year", "system", "subvariant", "handler1", "handler2"),
    pools={"model": 0x20C000, "year": 0x20D7A0, "system": 0x20DAE0, "subvariant": 0x20DAE0},
    handler_names=0x178400, handler_name_size=4,
    module_scan=(0x1B0000, 0x300000), max_model=11,
)

LAYOUT_S = CatalogLayout(
    family="s", name="Legacy 28-byte pack",
    image_sha256="",  # filled from inventory at load time; layout verified on the 2007-05-18 build
    records=0x23EE7C, record_size=28,
    fields=("model", "system", "variant", "handler1", "handler2"),
    pools={"model": 0x180000, "system": 0x1818A4, "variant": 0x1822CC},
    handler_names=0x23DF40, handler_name_size=6,
    module_pointers=0x1E8000, max_model=64,
)

LAYOUTS = [LAYOUT_KP, LAYOUT_S]


def iter_records(image: bytes, layout: CatalogLayout) -> Iterable[tuple[int, tuple[int, ...]]]:
    nwords = layout.record_size // 2
    off = layout.records
    while off + layout.record_size <= len(image):
        words = struct.unpack_from(f"<{nwords}H", image, off)
        if not 1 <= words[0] <= layout.max_model:
            return
        yield off, words
        off += layout.record_size


def handler_name(image: bytes, layout: CatalogLayout, handler: int) -> str | None:
    if not handler or not layout.handler_names:
        return None
    size = layout.handler_name_size
    raw = image[layout.handler_names + (handler - 1) * size: layout.handler_names + handler * size]
    return raw.decode("latin-1") if re.fullmatch(rb"[A-Z0-9_]+", raw) else None


def kp_module_index(image: bytes, layout: CatalogLayout) -> dict[str, int]:
    """Paragraph-aligned 4-char module tags, first occurrence wins (firmware scan order)."""
    lo, hi = layout.module_scan
    found: dict[str, int] = {}
    for off in range(lo, min(hi, len(image)), 16):
        tag = image[off: off + 4]
        if re.fullmatch(rb"[A-Z0-9]{4}", tag):
            found.setdefault(tag.decode(), off)
    return found


def s_module_location(image: bytes, layout: CatalogLayout, handler: int) -> dict | None:
    """Resolve a legacy handler to its LHA module (firmware first-match rule)."""
    name = handler_name(image, layout, handler)
    if name is None:
        return None
    size = layout.handler_name_size
    first = handler - 1
    for i in range(handler - 1):
        if image[layout.handler_names + i * size: layout.handler_names + (i + 1) * size] == name.encode():
            first = i
            break
    bank, off = struct.unpack_from("<2H", image, layout.module_pointers + 4 * first)
    info = {"name": name, "name_index": first}
    if bank == 0xFFFF or off >= BANK:
        info["builtin"] = True  # no overlay module: handled by resident firmware
        return info
    address = layout.module_pointers + bank * BANK + off
    info["address"] = address
    try:
        member = parse_member(image, address)
        info.update(method=member.method, compressed=member.compressed_size, size=member.original_size)
    except LhaError:
        info["method"] = None
    return info


def decode_catalog(image: bytes, layout: CatalogLayout) -> dict:
    modules = kp_module_index(image, layout) if layout.family == "kp" else {}
    entries = []
    for off, words in iter_records(image, layout):
        rec = dict(zip(layout.fields, words))
        handlers = [h for h in words[len(layout.fields) - 2:] if h]
        row = {"offset": off, "raw": list(words[: len(layout.fields)])}
        for fname, base in layout.pools.items():
            row[fname] = pool(image, base, rec.get(fname, 0))
        row["handlers"] = []
        for h in handlers:
            item = {"handler": h, "module": handler_name(image, layout, h)}
            if layout.family == "kp" and item["module"]:
                item["module_offset"] = modules.get(item["module"])
                if item["module_offset"] is None:
                    item["builtin"] = True  # not in the overlay directory
            elif layout.family == "s":
                item.update(s_module_location(image, layout, h) or {})
            row["handlers"].append(item)
        entries.append(row)
    return {"layout": layout.name, "family": layout.family, "records": entries,
            "record_count": len(entries)}


# ------------------------------------------------------- troubleshooting pack

def decode_tspack(raw: bytes) -> dict:
    """Decode a sectioned troubleshooting pack (extended image)."""
    title = raw[0x30:0x50].split(b"\xff", 1)[0].decode("latin-1").strip()
    ptrs = [struct.unpack_from("<2H", raw, 4 * i) for i in range(12)]
    phys = [b * BANK + o for b, o in ptrs]
    sections = []
    for s in range(6):
        index, data = phys[2 * s], phys[2 * s + 1]
        end = phys[2 * s + 2] if s < 5 else len(raw)
        count = (data - index) // 4
        items = []
        for i in range(count):
            b, o = struct.unpack_from("<2H", raw, index + 4 * i)
            addr = data + b * BANK + o
            if not (data <= addr < end) or (i and b == 0 and o == 0):
                continue
            items.append(addr)
        records = []
        for addr in sorted(set(items)):
            rec = {"offset": addr}
            if raw[addr + 2: addr + 5] == b"-lh":
                try:
                    member = parse_member(raw, addr)
                    rec.update(kind="lha", name=member.name, method=member.method,
                               size=member.original_size)
                except LhaError:
                    rec["kind"] = "binary"
            else:
                text_start = raw.find(b"\xff\xff\xff\xff", addr, addr + 64)
                start = text_start + 4 if text_start >= 0 else addr
                stop = raw.find(b"\0", start, start + 8192)
                body = raw[start: stop if stop > 0 else start + 512]
                rec.update(kind="text", title=body.split(b"\n", 1)[0].decode("latin-1").strip("$# \n"))
            records.append(rec)
        sections.append({"section": s, "index": index, "data": data, "end": end,
                         "records": records})
    return {"family": "tspack", "title": title, "sections": sections}


# ------------------------------------------------------------------- C1 packs

C1_RECORD = 30
C1_PROFILE = 0x54
_C1_TEXT = re.compile(rb"[\x21-\x7e][\x20-\x7e]*[\x00\x20]*")
def c1_make_table(image: bytes) -> int | None:
    """Manufacturer table: 26-byte, 1-based; code 1 is generic OBD-II."""
    m = re.search(rb"CARB OBD-II\0{6,}HYUNDAI", image)
    return m.start() if m else None


def _valid_text(image: bytes, off: int) -> bool:
    chunk = image[off: off + RECORD_TEXT]
    return bool(_C1_TEXT.fullmatch(chunk)) and chunk[:1] != b" "


def c1_path_count(image: bytes, offset: int) -> int:
    off = offset
    while off + C1_RECORD <= len(image):
        w = struct.unpack_from("<15H", image, off)
        if w[0] == 0 or w[0] > 120 or w[1] == 0:
            break
        off += C1_RECORD
    return (off - offset) // C1_RECORD


# Directory pair order (index into the 100-pair block after the base bank).
C1_DIR_FIELDS = ("path", "field1", "field2", "field3", "field4", "field5", "profiles")


def c1_directory(image: bytes) -> list[dict]:
    """Per-segment table directory found at ``pack + 0x1FC000``.

    Each 0x190-byte block holds 100 ``(bank, offset)`` word pairs.  Pair 0 is
    the segment base bank (relative to the pack); every other pair is relative
    to that base, ``0xFFFF`` meaning "absent".  Pairs 1..7 are the path table,
    the five name lists referenced by record fields 1..5, and the 84-byte ECU
    profile table.  The block whose base is ``pack+0x200000`` describes the
    shared area (manufacturer table etc.).
    """
    blocks = []
    for pack in range(PACK, len(image), PACK):
        for off in range(pack + 0x1FC000, pack + 0x200000, 0x190):
            if off + 0x190 > len(image):
                break
            base_bank, zero = struct.unpack_from("<2H", image, off)
            if zero or base_bank in (0, 0xFFFF):
                continue
            base = pack + base_bank * BANK
            pairs = [struct.unpack_from("<2H", image, off + 4 * i) for i in range(1, 100)]
            addrs = [None if b == 0xFFFF else base + b * BANK + o for b, o in pairs]
            entry = {"directory": off, "base": base, "tables": addrs}
            entry.update({name: addrs[i] for i, name in enumerate(C1_DIR_FIELDS)})
            if (entry["path"] == base and c1_path_count(image, base) > 0 and entry["profiles"]
                    and c1_profile_count(image, entry["profiles"]) > 0):
                blocks.append(entry)
    return blocks


def c1_profile_count(image: bytes, start: int) -> int:
    n = 0
    while True:
        entry = image[start + n * C1_PROFILE: start + n * C1_PROFILE + 8]
        if re.fullmatch(rb"[A-Z0-9_]{8}", entry) or entry == b"0\0      ":  # placeholder slot
            n += 1
            continue
        return n


CPU_CLOCK_HZ = 20_000_000  # emulator Cpu::oclock; baud = clock / 2 / 8 / divisor


def hsio_baud(register: int) -> int:
    return CPU_CLOCK_HZ // 2 // 8 // (register & 0x7FFF)


# Behaviour of the common driver indices (profile byte 8), assigned by
# matching profiles to the vehicle/system/variant they describe.  Each driver
# showed one consistent behaviour in public reverse-engineering notes.
DRIVERS = {
    0x01: {"bus": "K-line", "protocol": "KWP2000", "init": "fast", "header": "80 tt F1 len", "hsio": 0x8077},
    0x05: {"bus": "K-line", "protocol": "KWP2000", "init": "fast", "header": "80 tt F1 len", "hsio": 0x8077},
    0x02: {"bus": "K-line", "protocol": "KWP2000", "init": "fast", "header": "81 tt F1", "hsio": 0x8077},
    0x03: {"bus": "K-line", "protocol": "KWP2000", "init": "fast", "header": "81 tt F1", "hsio": 0x8077},
    0x0B: {"bus": "K-line", "protocol": "KWP2000", "init": "fast", "header": "81 tt F1", "hsio": 0x8077},
    0x0E: {"bus": "K-line", "protocol": "KWP2000", "init": "fast", "header": "81 tt F1", "hsio": 0x8077},
    0x1B: {"bus": "K-line", "protocol": "KWP2000", "init": "fast", "header": "81 tt F1", "hsio": 0x8077},
    0x1C: {"bus": "K-line", "protocol": "ISO9141/KWP", "init": "5-baud", "header": "68 6A F1", "hsio": 0x8077},
    0x1D: {"bus": "K-line", "protocol": "ISO9141/KWP", "init": "5-baud", "header": "68 6A F1", "hsio": 0x8077},
    0x20: {"bus": "K-line", "protocol": "ISO9141/KWP", "init": "5-baud", "header": "68 6A F1", "hsio": 0x8077},
    0x32: {"bus": "K-line", "protocol": "MG single-byte", "init": "5-baud", "header": "1-byte request", "hsio": 0x8081},
    0x4A: {"bus": "K-line", "protocol": "MG single-byte", "init": "5-baud", "header": "1-byte request", "hsio": 0x8081},
    0x33: {"bus": "K-line", "protocol": "immobilizer/code saving", "init": "none", "header": "05 EA ..", "hsio": 0x8077},
    0x1A: {"bus": "CAN", "protocol": "CAN via CAN interface module", "init": "none", "header": "CAN", "hsio": 0x801F},
}


def _u16(rec: bytes, off: int) -> int:
    return rec[off] | (rec[off + 1] << 8)


def c1_profile(image: bytes, table: int, handler: int) -> dict:
    """Decode one 84-byte C1 ECU profile.

    Verified fields: byte 8 driver index (selects baud/init/frame format, see
    ``DRIVERS``), byte 9 link flags (0x11 serial driver, 0x00 CAN/other),
    bytes 10-13 addressing, bytes 24-25 / 29-30 / 38-39 / 44-45 / 54-55
    16-bit references into the segment's other tables (for common profiles
    bytes 38-39 equal the profile number).  The remaining parameter bytes are
    kept as raw words; their meaning was not observable.
    """
    rec = image[table + (handler - 1) * C1_PROFILE: table + handler * C1_PROFILE]
    pid = rec[:8].decode("latin-1")
    family = pid[2:4]
    info = {"handler": handler, "id": pid, "family": family, "class": pid[4:5],
            "protocol_code": rec[8], "driver": rec[8], "link_flags": rec[9],
            "address": rec[10:12].hex(), "raw": rec[8:].hex()}
    if family == "CS":
        info["can_id"] = (rec[10] << 8) | rec[11]
        if rec[12]:
            info["can_ext_address"] = rec[12]
    elif family == "SI":
        info.update(init_address=rec[10], target=rec[11], tester=rec[13])
    elif family == "SK":
        info.update(target=rec[11], tester=rec[13])
        if rec[10]:
            info["header_format"] = rec[10]
    else:
        info["addressing"] = rec[10:14].hex()
    info["table_refs"] = {name: _u16(rec, off) for name, off in
                          (("ref24", 24), ("ref29", 29), ("ref38", 38), ("ref44", 44), ("ref54", 54))
                          if _u16(rec, off)}
    info["params"] = [rec[o:o + 2].hex() for o in range(14, 84, 2)]
    drv = DRIVERS.get(rec[8]) if pid.startswith("HK") else None
    if drv:
        info["observed"] = dict(drv, baud=hsio_baud(drv["hsio"]), hsio=f"{drv['hsio']:04X}",
                                source="public reverse-engineering notes")
    return info


def decode_c1(image: bytes) -> dict:
    makes_base = c1_make_table(image)
    segments = []
    for seg in c1_directory(image):
        count = c1_path_count(image, seg["path"])
        nprof = c1_profile_count(image, seg["profiles"])
        recs = [struct.unpack_from("<15H", image, seg["path"] + i * C1_RECORD) for i in range(count)]
        lists = {f"field{k}": seg[f"field{k}"] for k in range(1, 6)}
        # A level resolves to text only if every value it uses lands on a name;
        # otherwise the firmware uses it as a plain option number.
        textual = {}
        for k in range(1, 6):
            base = lists[f"field{k}"]
            vals = {r[k] for r in recs if r[k]}
            textual[k] = bool(base) and all(_valid_text(image, base + (v - 1) * RECORD_TEXT) for v in vals)
        rows = []
        for i, r in enumerate(recs):
            row = {"offset": seg["path"] + i * C1_RECORD, "raw": list(r[:7]), "kind": r[6],
                   "make": pool(image, makes_base, r[0]) if makes_base else None}
            for k in range(1, 6):
                if not r[k]:
                    row[f"field{k}"] = None
                elif textual[k]:
                    row[f"field{k}"] = pool(image, lists[f"field{k}"], r[k])
                else:
                    row[f"field{k}"] = r[k]
            row["profiles"] = [c1_profile(image, seg["profiles"], h) for h in r[7:] if h and h <= nprof]
            rows.append(row)
        hmax = max((h for r in recs for h in r[7:]), default=0)
        segments.append({
            "directory": seg["directory"], "offset": seg["path"], "count": count,
            "makes": sorted({row["make"] for row in rows if row["make"]}),
            "lists": lists, "textual_levels": [k for k in range(1, 6) if textual[k]],
            "profile_table": seg["profiles"], "profile_count": nprof, "max_handler": hmax,
            "profiles_consistent": hmax <= nprof,
            "records": rows,
        })
    return {"family": "c1", "make_table": makes_base, "segments": segments,
            "record_count": sum(s["count"] for s in segments)}


# ------------------------------------------------------------ re-programming

def _cstrings(image: bytes, start: int, stop: int) -> list[tuple[int, str]]:
    out, off = [], start
    for chunk in image[start:stop].split(b"\0"):
        if len(chunk) > 2 and all(32 <= c < 127 for c in chunk):
            out.append((off, chunk.decode("latin-1").strip()))
        off += len(chunk) + 1
    return out


def decode_repro(image: bytes) -> dict:
    """Campaign index of an ECU re-programming pack (shown before the password).

    Two layouts are known: a table headed ``VER. :`` / ``DATE :`` with
    3 x 40-byte rows per campaign (title, bulletin number, blank) as in
    R4-Repro, and firmware-embedded NUL-separated menu strings anchored by
    ``CAMPAIGN SELECTION`` as in R-Repro.  Flash payloads are not decoded.
    """
    m = re.search(rb"VER\. : ([^\0]+)\0+DATE : ([^\0]+)\0+(\d+)\0", image)
    if m and m.start() % 0x1000 == 0:
        base, count = m.start(), int(m.group(3))
        campaigns = []
        for i in range(count):
            row = base + 0x78 + i * 120
            campaigns.append({"offset": row, "title": text_at(image, row, 40),
                              "bulletin": text_at(image, row + 40, 40)})
        return {"family": "repro", "layout": "table", "version": m.group(1).decode(),
                "date": m.group(2).decode(), "campaigns": campaigns}
    menus = []
    for anchor in re.finditer(rb"\0CAMPAIGN SELECTION\0", image):
        items = []
        for off, s in _cstrings(image, anchor.start() + 20, anchor.start() + 0x400):
            if s in ("SOFTWARE DOWNLOAD", "CAMPAIGN SELECTION") or s.startswith("F1 "):
                break
            items.append({"offset": off, "title": s})
        menus.append({"anchor": anchor.start() + 1, "campaigns": items})
    return {"family": "repro", "layout": "firmware-strings", "menus": menus}


# ------------------------------------------------------------- classification

def classify(image: bytes) -> str:
    if b"TROUBLE SHOOTING PACK" in image[:0x60]:
        return "tspack"
    if len(image) > 0x2084 and image[0x2080] == 0xFA:
        if re.search(rb"[A-Z]{2}(SK|CS|MM|SM|SI)[PBC]\d{3}", image):
            return "c1"
        if re.search(rb"RE-PROGRAMMING|CAMPAIGN SELECTION", image[:0x400000]):
            return "repro"
        return "legacy"
    return "unknown"


def layout_for(image_sha256: str, image: bytes) -> CatalogLayout | None:
    for layout in LAYOUTS:
        if layout.image_sha256 and layout.image_sha256 == image_sha256:
            return layout
    # The legacy 28-byte layout is identified by its stable anchors.
    if image[0x180000:0x18000A] == b"ACCENT(MC)" and image[0x1818A4:0x1818AA] == b"ENGINE":
        return LAYOUT_S
    return None


# ------------------------------------------------------------- brand catalogue

# Profile-ID family codes.  ``CS`` is confirmed CAN by the decoded CAN IDs
# (0x7E0/0x7E1 engine/TCM, 0x6F1 BMW tester, 0x750 Toyota gateway); the others
# are kept as codes because the firmware semantics were not observed directly.
PROTOCOL_HINTS = {"CS": "CAN"}


def _ecu_from_c1(profile: dict) -> dict:
    ecu = {"id": profile["id"], "family": profile["family"], "class": profile["class"],
           "protocol_code": profile["protocol_code"], "address": profile["address"],
           "handler": profile["handler"], "driver": profile["driver"]}
    for key in ("init_address", "target", "tester", "header_format", "addressing", "observed"):
        if key in profile:
            ecu[key] = profile[key]
    if profile["family"] in PROTOCOL_HINTS:
        ecu["bus"] = PROTOCOL_HINTS[profile["family"]]
    for key in ("can_id", "can_ext_address"):
        if key in profile:
            ecu[key] = profile[key]
    return ecu


def _ecu_from_handler(item: dict) -> dict:
    ecu = {"handler": item["handler"], "id": item.get("module") or item.get("name")}
    if item.get("builtin"):
        ecu["builtin"] = True
    for key in ("module_offset", "address", "method", "size"):
        if item.get(key) is not None:
            ecu[key] = item[key]
    return ecu


_YEAR_LEVEL = re.compile(r"(?:19|20)\d\d\b.*|.*\b\d\dMY\b.*|UP TO .*|FROM .*|~.*\d{4}.*", re.I)


def _add(tree: dict, brand: str, vehicle: str, sub, system, variant: list, ecus: list, source: dict):
    b = tree.setdefault(brand, {})
    v = b.setdefault((vehicle, sub), {})
    s = v.setdefault(system, [])
    s.append({"variant": [x for x in variant if x not in (None, "")], "ecus": ecus, "source": source})


def _render(tree: dict) -> list[dict]:
    brands = []
    for brand, vehicles in tree.items():
        out_vehicles = []
        for (name, sub), systems in vehicles.items():
            out_vehicles.append({
                "name": name, "sub": sub,
                "systems": [{"name": sname, "entries": entries} for sname, entries in systems.items()],
            })
        brands.append({"name": brand, "vehicles": out_vehicles})
    return brands


def build_brand_catalog(family: str, catalog: dict, default_brand: str | None = None) -> dict:
    """Uniform brand -> vehicle -> system -> variant -> ECU view of a decoded catalogue.

    ``vehicle`` is the first menu level after the brand and ``sub`` the
    second (model year, chassis or series, depending on the manufacturer);
    the device menu order is preserved, and every entry keeps its source
    record offset and raw field values.
    """
    tree: dict = {}
    if family == "c1":
        for seg in catalog["segments"]:
            for r in seg["records"]:
                if not r["make"] or r["field1"] is None:
                    continue
                name, sub = r["field1"], r["field2"]
                source = {"offset": r["offset"], "raw": r["raw"], "segment": seg["offset"],
                          "menu_order": "vehicle,sub"}
                # Year/era-first menus (GM, Ford, Mitsubishi...): show the model as the vehicle.
                if isinstance(sub, str) and isinstance(name, str) and _YEAR_LEVEL.fullmatch(name):
                    name, sub = sub, name
                    source["menu_order"] = "sub,vehicle"
                _add(tree, r["make"], name, sub, r["field3"],
                     [r["field4"], r["field5"]], [_ecu_from_c1(p) for p in r["profiles"]], source)
    elif family == "kp":
        for r in catalog["records"]:
            _add(tree, default_brand, r["model"], r["year"], r["system"], [r["subvariant"]],
                 [_ecu_from_handler(h) for h in r["handlers"]], {"offset": r["offset"], "raw": r["raw"]})
    elif family == "s":
        for r in catalog["records"]:
            _add(tree, default_brand, r["model"], None, r["system"], [r["variant"]],
                 [_ecu_from_handler(h) for h in r["handlers"]], {"offset": r["offset"], "raw": r["raw"]})
    brands = _render(tree)
    stats = {
        "brands": len(brands),
        "vehicles": sum(len(b["vehicles"]) for b in brands),
        "entries": sum(len(s["entries"]) for b in brands for v in b["vehicles"] for s in v["systems"]),
        "ecu_references": sum(len(e["ecus"]) for b in brands for v in b["vehicles"]
                              for s in v["systems"] for e in s["entries"]),
    }
    return {"brands": brands, "stats": stats}
