"""DAT container reader tests using only synthetic fixtures.

These exercise the LHA member reader, container unwrapping and the layout
decoders without shipping or reading any vendor image.
"""

import json
import struct

from klinescan.containers import container as C
from klinescan.containers.export import decode_file
from klinescan.containers.lha import decode_lh5, extract_member


class _Bits:
    def __init__(self):
        self.bits = []

    def put(self, value, width):
        self.bits.extend((value >> (width - 1 - i)) & 1 for i in range(width))

    def bytes(self):
        bits = self.bits + [0] * (-len(self.bits) % 8 + 16)
        return bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


def test_lh5_constant_trees_decode_literal_run():
    b = _Bits()
    b.put(5, 16)        # block size
    b.put(0, 5); b.put(0, 5)      # pt tree: constant
    b.put(0, 9); b.put(0x41, 9)   # c tree: constant literal 'A'
    b.put(0, 4); b.put(0, 4)      # p tree: constant
    assert decode_lh5(b.bytes(), 5) == b"AAAAA"


def _lh0_member(name: bytes, payload: bytes) -> bytes:
    header = bytearray([0, 0]) + b"-lh0-"
    header += struct.pack("<II", len(payload), len(payload)) + b"\0\0\0\0" + b"\x20\x00"
    header += bytes([len(name)]) + name + b"\0\0"
    header[0] = len(header) - 2
    return bytes(header) + payload


def test_extract_stored_member():
    member, data = extract_member(_lh0_member(b"X.BIN", b"hello"), 0)
    assert member.name == "X.BIN" and data == b"hello"


def test_unwrap_lha_container():
    container, image, info = C.unwrap(_lh0_member(b"IMAGE.BIN", b"\xfa" * 4))
    assert container == "lha" and image == b"\xfa" * 4 and info["member"] == "IMAGE.BIN"


def test_pool_is_one_based_and_fixed_width():
    image = b"ALPHA".ljust(26, b" ") + b"BETA".ljust(26, b"\0")
    assert C.pool(image, 0, 1) == "ALPHA"
    assert C.pool(image, 0, 2) == "BETA"
    assert C.pool(image, 0, 0) is None


def test_decode_catalog_with_synthetic_kp_layout():
    image = bytearray(0x400)
    image[0x000:0x01A] = b"RIO".ljust(26, b" ")
    image[0x040:0x05A] = b"2007MY".ljust(26, b" ")
    image[0x080:0x09A] = b"ENGINE".ljust(26, b" ")
    image[0x100:0x104] = b"CE02"
    image[0x200:0x210] = struct.pack("<8H", 1, 1, 1, 0, 1, 0, 0, 0)
    image[0x300:0x304] = b"CE02"
    layout = C.CatalogLayout(
        family="kp", name="synthetic", image_sha256="", records=0x200, record_size=16,
        fields=("model", "year", "system", "subvariant", "handler1", "handler2"),
        pools={"model": 0, "year": 0x40, "system": 0x80, "subvariant": 0x80},
        handler_names=0x100, module_scan=(0x300, 0x400), max_model=1)
    cat = C.decode_catalog(bytes(image), layout)
    assert cat["record_count"] == 1
    row = cat["records"][0]
    assert (row["model"], row["year"], row["system"]) == ("RIO", "2007MY", "ENGINE")
    assert row["handlers"][0]["module"] == "CE02"
    assert row["handlers"][0]["module_offset"] == 0x300


def test_tspack_section_pointers_are_bank_relative():
    raw = bytearray(b"\xff" * 0x200)
    ptrs = [(0, 0x60), (0, 0x70)] + [(0, 0x80), (0, 0x84)] * 5
    for i, (b, o) in enumerate(ptrs):
        struct.pack_into("<2H", raw, 4 * i, b, o)
    raw[0x30:0x45] = b"TROUBLE SHOOTING PACK"
    struct.pack_into("<2H", raw, 0x60, 0, 0)
    struct.pack_into("<2H", raw, 0x64, 0, 0x08)
    raw[0x70:0x78] = b"\0" * 4 + b"\xff" * 4
    raw[0x78:0x80] = b"\xff\xff\xff\xffTitle\n"[:8]
    report = C.decode_tspack(bytes(raw))
    assert report["title"] == "TROUBLE SHOOTING PACK"
    assert [r["offset"] for r in report["sections"][0]["records"]] == [0x70, 0x78]


def test_repro_table_layout_synthetic():
    image = bytearray(0x2000)
    head = b"VER. : 011-80.1".ljust(40, b"\0") + b"DATE : 2006. 09. 06".ljust(40, b"\0") + b"1".ljust(40, b"\0")
    image[0x1000:0x1078] = head
    image[0x1078:0x10A0] = b"GK 2.0 TCU".ljust(40, b"\0")
    image[0x10A0:0x10C8] = b"0012".ljust(40, b"\0")
    report = C.decode_repro(bytes(image))
    assert report["layout"] == "table" and report["date"] == "2006. 09. 06"
    assert report["campaigns"] == [{"offset": 0x1078, "title": "GK 2.0 TCU", "bulletin": "0012"}]


def test_c1_can_profile_fields():
    rec = b"TLCSC613" + bytes.fromhex("19 00 07 50 3b 00") + b"\0" * (C.C1_PROFILE - 14)
    info = C.c1_profile(rec, 0, 1)
    assert info["family"] == "CS" and info["can_id"] == 0x750 and info["can_ext_address"] == 0x3B


def test_decode_file_on_synthetic_plain_image(tmp_path):
    image = bytearray(0x400)
    image[0x30:0x45] = b"TROUBLE SHOOTING PACK"
    path = tmp_path / "sample.bin"
    path.write_bytes(image)
    report = decode_file(path, None)
    assert report["container"] == "plain"
    assert report["family"] == "tspack"
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
