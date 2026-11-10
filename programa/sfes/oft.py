"""RE Engine .oft.1 font wrapper (magic FBFO) codec. Own implementation; format learned from REasy
file_handlers/oft (MIT, read-only reference).

Layout: b"FBFO" + payload, payload = SFNT bytes XOR an 8-byte repeating key,
key = ror64(SEED, len(payload) & 63) as little-endian bytes. An edited font must be encoded with its own length.
"""
from __future__ import annotations

SEED = 0xAE6E39B58A355F45
MAGIC = b"FBFO"
SFNT_MAGICS = (b"OTTO", b"\x00\x01\x00\x00", b"ttcf", b"true")
_M64 = (1 << 64) - 1


def _ror64(v: int, n: int) -> int:
    n &= 63
    v &= _M64
    return v if n == 0 else ((v >> n) | (v << (64 - n))) & _M64


def key_for(payload_len: int) -> bytes:
    return _ror64(SEED, payload_len & 63).to_bytes(8, "little")


def _xor(payload: bytes) -> bytes:
    key = key_for(len(payload))
    # fast path: xor with a repeated key via big ints
    n = len(payload)
    if n == 0:
        return b""
    full = (key * (n // 8 + 1))[:n]
    return (int.from_bytes(payload, "little") ^ int.from_bytes(full, "little")).to_bytes(n, "little")


def decode(data: bytes) -> bytes:
    if data[:4] != MAGIC:
        raise ValueError("not an FBFO wrapper")
    sfnt = _xor(data[4:])
    if sfnt[:4] not in SFNT_MAGICS:
        raise ValueError(f"decoded payload has unknown SFNT magic {sfnt[:4]!r}")
    return sfnt


def encode(sfnt: bytes) -> bytes:
    if sfnt[:4] not in SFNT_MAGICS:
        raise ValueError("input is not SFNT")
    return MAGIC + _xor(sfnt)
