"""RE Engine GMSG (.msg.23) reader and writer.

Own implementation. Format knowledge from dtlnor/REMSG_Converter and seifhassine/REasy (MIT, read only).
Verified in Phase 1: rebuilding with the original pool order is byte-identical for all 4,787 game files.

Layout (version 23, as used by this game):
  0   u32 version (23), 'GMSG', u64 16, u32 entry_count, u32 attr_count, u32 lang_count, 4 pad
  40  u64 data_off, unk_off, lang_off, attr_off, attr_name_off
  72  u64 entry offsets[entry_count]; unk_off -> u64 0; lang_off -> i32 languages[lang_count]
      attr_off -> i32 attr types (align 8); attr_name_off -> u64 name ptrs
  entries (40 + 8*lang_count bytes): 16-byte GUID, u32 id, u32 name_hash, u64 name_ptr, u64 attr_ptr,
      u64 text_ptr[lang_count]
  attribute values (8 bytes each per entry), then at data_off the string pool:
      UTF-16LE NUL-terminated strings, deduplicated, encrypted as c[i] = p[i] ^ c[i-1] ^ KEY[i & 15].
"""
from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass, field

from .pak import murmur3_32

KEY = bytes([0xCF, 0xCE, 0xFB, 0xF8, 0xEC, 0x0A, 0x33, 0x66, 0x93, 0xA9, 0x1D, 0x93, 0x50, 0x39, 0x5F, 0x09])
LANG_NAMES = {0: "ja", 1: "en", 2: "fr", 3: "it", 4: "de", 5: "es", 6: "ru", 7: "pl", 8: "nl", 9: "pt",
              10: "ptbr", 11: "ko", 12: "zhtw", 13: "zhcn", 32: "es419", -1: "unused"}


def _keystream(n: int) -> int:
    return int.from_bytes((KEY * (n // 16 + 1))[:n], "little")


def decrypt(raw: bytes) -> bytes:
    """p[i] = c[i] ^ c[i-1] ^ KEY[i & 15] (c[-1] = 0), computed with big-int arithmetic."""
    n = len(raw)
    if n == 0:
        return b""
    mask = (1 << (8 * n)) - 1
    c = int.from_bytes(raw, "little")
    p = (c ^ ((c << 8) & mask) ^ _keystream(n)) & mask
    return p.to_bytes(n, "little")


def encrypt(plain: bytes) -> bytes:
    """Inverse of decrypt: c is the running XOR (prefix XOR) of q = p ^ KEY."""
    n = len(plain)
    if n == 0:
        return b""
    mask = (1 << (8 * n)) - 1
    c = int.from_bytes(plain, "little") ^ _keystream(n)
    shift = 8
    while shift < 8 * n:
        c ^= (c << shift) & mask
        shift <<= 1
    return c.to_bytes(n, "little")


def name_hash(name: str) -> int:
    return murmur3_32(name.encode("utf-16le"))


def align8(n: int) -> int:
    return n + (-n) % 8


@dataclass
class MsgEntry:
    guid: str
    entry_id: int          # opaque 32-bit id (called sound_id in some tools)
    name_hash: int
    name: str
    attributes: list
    texts: list[str]       # one per language slot, in file order


@dataclass
class Msg:
    version: int
    languages: list[int]
    attr_types: list[int]
    attr_names: list[str]
    entries: list[MsgEntry] = field(default_factory=list)
    pool_order: list[str] = field(default_factory=list)   # unique strings in original pool order

    def slot(self, lang_id: int) -> int:
        return self.languages.index(lang_id)

    def get(self, entry: MsgEntry, lang_id: int) -> str:
        return entry.texts[self.slot(lang_id)]

    def set(self, entry: MsgEntry, lang_id: int, text: str) -> None:
        entry.texts[self.slot(lang_id)] = text

    def by_name(self) -> dict[str, MsgEntry]:
        return {e.name: e for e in self.entries}


def parse(data: bytes) -> Msg:
    if data[4:8] != b"GMSG":
        raise ValueError("not a GMSG file")
    version = struct.unpack_from("<I", data, 0)[0]
    entry_count, attr_count, lang_count = struct.unpack_from("<III", data, 16)
    data_off, unk_off, lang_off, attr_off, attr_name_off = struct.unpack_from("<5Q", data, 32)
    entry_offs = struct.unpack_from(f"<{entry_count}Q", data, 72)
    languages = list(struct.unpack_from(f"<{lang_count}i", data, lang_off))
    attr_types = list(struct.unpack_from(f"<{attr_count}i", data, attr_off))
    attr_name_ptrs = struct.unpack_from(f"<{attr_count}Q", data, attr_name_off)
    pool = decrypt(data[data_off:])
    if len(pool) % 2:
        raise ValueError("odd-sized string pool")
    text = pool.decode("utf-16-le")
    strings: dict[int, str] = {}
    order: list[str] = []
    start = 0
    for i, ch in enumerate(text):
        if ch == "\x00":
            s = text[start:i]
            strings[data_off + start * 2] = s
            order.append(s)
            start = i + 1
    if start != len(text):
        raise ValueError("string pool does not end with NUL")
    msg = Msg(version, languages, attr_types, [strings[x] for x in attr_name_ptrs], pool_order=order)
    for off in entry_offs:
        guid = str(uuid.UUID(bytes_le=bytes(data[off:off + 16])))
        entry_id, nhash = struct.unpack_from("<II", data, off + 16)
        name_ptr, attr_ptr = struct.unpack_from("<QQ", data, off + 24)
        text_ptrs = struct.unpack_from(f"<{lang_count}Q", data, off + 40)
        attrs = []
        for k, t in enumerate(attr_types):
            if t == 0:
                attrs.append(struct.unpack_from("<q", data, attr_ptr + 8 * k)[0])
            elif t == 1:
                attrs.append(struct.unpack_from("<d", data, attr_ptr + 8 * k)[0])
            else:
                attrs.append(strings[struct.unpack_from("<Q", data, attr_ptr + 8 * k)[0]])
        msg.entries.append(MsgEntry(guid, entry_id, nhash, strings[name_ptr], attrs,
                                    [strings[t] for t in text_ptrs]))
    return msg


def _needed_strings(m: Msg) -> list[str]:
    out = list(m.attr_names)
    for e in m.entries:
        out.append(e.name)
        out.extend(e.texts)
        for t, v in zip(m.attr_types, e.attributes):
            if t not in (0, 1):
                out.append(v if v is not None else "")
    return out


def build(m: Msg, pool_order: list[str] | None = None) -> bytes:
    """Serialize. Pool order: strings of `pool_order` (default: m.pool_order) that are still used, in that
    order, then any new strings in first-use order. Unchanged files therefore rebuild byte-identically."""
    for e in m.entries:
        if name_hash(e.name) != e.name_hash:
            raise ValueError(f"name hash mismatch for {e.name}")
        if len(e.texts) != len(m.languages):
            raise ValueError(f"{e.name}: {len(e.texts)} texts for {len(m.languages)} languages")
        for t in e.texts:
            if "\x00" in t:
                raise ValueError(f"{e.name}: NUL inside text")
    needed = _needed_strings(m)
    needed_set = set(needed)
    base = m.pool_order if pool_order is None else pool_order
    order = [s for s in dict.fromkeys(base) if s in needed_set]
    have = set(order)
    order += [s for s in dict.fromkeys(needed) if s not in have]

    n, na, nl = len(m.entries), len(m.attr_types), len(m.languages)
    unk_off = 72 + 8 * n
    lang_off = unk_off + 8
    attr_off = align8(lang_off + 4 * nl)
    attr_name_off = attr_off + align8(4 * na)
    entry0 = attr_name_off + 8 * na
    esz = 40 + 8 * nl
    ents_end = entry0 + n * esz
    data_off = ents_end + 8 * n * na

    pool = bytearray()
    soff: dict[str, int] = {}
    for s in order:
        soff[s] = data_off + len(pool)
        pool += (s + "\0").encode("utf-16-le")

    out = bytearray()
    out += struct.pack("<I4sQIII4x", m.version, b"GMSG", 16, n, na, nl)
    out += struct.pack("<5Q", data_off, unk_off, lang_off, attr_off, attr_name_off)
    out += struct.pack(f"<{n}Q", *[entry0 + i * esz for i in range(n)])
    out += struct.pack("<Q", 0)
    out += struct.pack(f"<{nl}i", *m.languages)
    out += b"\0" * (attr_off - len(out))
    out += struct.pack(f"<{na}i", *m.attr_types)
    out += b"\0" * (attr_name_off - len(out))
    out += struct.pack(f"<{na}Q", *[soff[x] for x in m.attr_names])
    if len(out) != entry0:
        raise AssertionError("layout error before entries")
    for i, e in enumerate(m.entries):
        out += uuid.UUID(e.guid).bytes_le
        out += struct.pack("<IIQQ", e.entry_id, e.name_hash, soff[e.name], ents_end + i * 8 * na)
        out += struct.pack(f"<{nl}Q", *[soff[t] for t in e.texts])
    for e in m.entries:
        for t, v in zip(m.attr_types, e.attributes):
            if t == 0:
                out += struct.pack("<q", v)
            elif t == 1:
                out += struct.pack("<d", v)
            else:
                out += struct.pack("<Q", soff[v if v is not None else ""])
    if len(out) != data_off:
        raise AssertionError("layout error before string pool")
    out += encrypt(bytes(pool))
    return bytes(out)
