"""Pure-Python LHA ``-lh5-`` decoder and level-0 member reader.

Some diagnostic container images embed LHA members whose headers common LHA
tools reject (for example empty or NUL-padded file names), so the container
reader decodes them directly.  Only the ``-lh5-`` (8 KiB dictionary) and
``-lh0-`` (stored) methods are needed by the observed layouts.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

DICBIT = 13
DICSIZ = 1 << DICBIT
MAXMATCH = 256
THRESHOLD = 3
NC = 255 + MAXMATCH + 2 - THRESHOLD  # 510
NP = DICBIT + 1
NT = 19
PBIT = 4
TBIT = 5
CBIT = 9


class LhaError(ValueError):
    pass


class _BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.bitbuf = 0
        self.bitcount = 0

    def _fill(self, n: int) -> None:
        while self.bitcount < n:
            byte = self.data[self.pos] if self.pos < len(self.data) else 0
            self.pos += 1
            self.bitbuf = (self.bitbuf << 8) | byte
            self.bitcount += 8

    def peek(self, n: int) -> int:
        self._fill(n)
        return (self.bitbuf >> (self.bitcount - n)) & ((1 << n) - 1)

    def get(self, n: int) -> int:
        if n == 0:
            return 0
        value = self.peek(n)
        self.bitcount -= n
        self.bitbuf &= (1 << self.bitcount) - 1
        return value


def _make_table(lengths: list[int]) -> dict[tuple[int, int], int]:
    """Canonical Huffman decode map keyed by (length, code)."""
    table: dict[tuple[int, int], int] = {}
    max_len = max(lengths) if lengths else 0
    code = 0
    for length in range(1, max_len + 1):
        for symbol, sym_len in enumerate(lengths):
            if sym_len == length:
                table[(length, code)] = symbol
                code += 1
        code <<= 1
    return table


def _decode_symbol(br: _BitReader, table, max_len: int) -> int:
    code = 0
    for length in range(1, max_len + 1):
        code = (code << 1) | br.get(1)
        symbol = table.get((length, code))
        if symbol is not None:
            return symbol
    raise LhaError("invalid Huffman code")


class _Huffman:
    def __init__(self, lengths: list[int] | None = None, constant: int | None = None):
        self.constant = constant
        if lengths is not None:
            self.table = _make_table(lengths)
            self.max_len = max(lengths) if lengths else 0

    def decode(self, br: _BitReader) -> int:
        if self.constant is not None:
            return self.constant
        return _decode_symbol(br, self.table, self.max_len)


def _read_pt_len(br: _BitReader, nn: int, nbit: int, i_special: int) -> _Huffman:
    n = br.get(nbit)
    if n == 0:
        return _Huffman(constant=br.get(nbit))
    lengths = [0] * nn
    i = 0
    while i < n:
        c = br.peek(3)
        if c == 7:
            br.get(3)
            while br.get(1):
                c += 1
        else:
            br.get(3)
        lengths[i] = c
        i += 1
        if i == i_special:
            skip = br.get(2)
            for _ in range(skip):
                lengths[i] = 0
                i += 1
    return _Huffman(lengths)


def _read_c_len(br: _BitReader, pt: _Huffman) -> _Huffman:
    n = br.get(CBIT)
    if n == 0:
        return _Huffman(constant=br.get(CBIT))
    lengths = [0] * NC
    i = 0
    while i < n:
        c = pt.decode(br)
        if c <= 2:
            if c == 0:
                count = 1
            elif c == 1:
                count = br.get(4) + 3
            else:
                count = br.get(CBIT) + 20
            for _ in range(count):
                lengths[i] = 0
                i += 1
        else:
            lengths[i] = c - 2
            i += 1
    return _Huffman(lengths)


def decode_lh5(data: bytes, original_size: int) -> bytes:
    br = _BitReader(data)
    out = bytearray()
    blocksize = 0
    c_tree = p_tree = None
    while len(out) < original_size:
        if blocksize == 0:
            blocksize = br.get(16)
            pt = _read_pt_len(br, NT, TBIT, 3)
            c_tree = _read_c_len(br, pt)
            p_tree = _read_pt_len(br, NP, PBIT, -1)
        blocksize -= 1
        c = c_tree.decode(br)
        if c < 256:
            out.append(c)
            continue
        length = c - 256 + THRESHOLD
        p = p_tree.decode(br)
        if p > 1:
            p = (1 << (p - 1)) + br.get(p - 1)
        start = len(out) - p - 1
        if start < 0:
            raise LhaError("back-reference before start of output")
        for k in range(length):
            out.append(out[start + k])
    return bytes(out[:original_size])


@dataclass
class LhaMember:
    offset: int
    header_size: int
    method: str
    compressed_size: int
    original_size: int
    name: str
    level: int

    @property
    def data_offset(self) -> int:
        return self.offset + self.header_size

    @property
    def end(self) -> int:
        return self.data_offset + self.compressed_size


def parse_member(buf: bytes, offset: int) -> LhaMember:
    """Parse an LHA level-0/1 member header at ``offset``."""
    if offset + 22 > len(buf) or buf[offset + 2 : offset + 4] != b"-l" or buf[offset + 6] != 0x2D:
        raise LhaError(f"no LHA header at {offset:#x}")
    hsize = buf[offset]
    method = buf[offset + 2 : offset + 7].decode("ascii")
    comp, orig = struct.unpack_from("<II", buf, offset + 7)
    level = buf[offset + 20]
    nlen = buf[offset + 21]
    name = buf[offset + 22 : offset + 22 + nlen].split(b"\0", 1)[0].decode("latin-1")
    total_header = hsize + 2
    if level == 1:
        # Extended headers follow the base header and are counted in comp.
        pos = offset + total_header
        ext = struct.unpack_from("<H", buf, pos - 2)[0]
        while ext:
            pos += ext
            comp -= ext
            ext = struct.unpack_from("<H", buf, pos - 2)[0]
        total_header = pos - offset
    return LhaMember(offset, total_header, method, comp, orig, name, level)


def extract_member(buf: bytes, offset: int) -> tuple[LhaMember, bytes]:
    member = parse_member(buf, offset)
    payload = buf[member.data_offset : member.end]
    if member.method == "-lh0-":
        return member, payload[: member.original_size]
    if member.method == "-lh5-":
        return member, decode_lh5(payload, member.original_size)
    raise LhaError(f"unsupported LHA method {member.method}")
