"""RE Engine PAK (KPKA v4) reading and patch-pak writing.

Reading: official paks are opened read-only ("rb"). Writing: PakWriter only ever writes to a path the
caller gives (inside the project folder); it refuses to write into the game folder.

Format (own implementation; references read: eigeen/ree-pak-rs, seifhassine/REasy, both MIT):
  header  <4sBBHII  magic 'KPKA', major 4, minor 2, feature flags, file count, fingerprint
  entry   <IIQQQqQ  hash_lower, hash_upper, offset, compressed size, size, attributes, checksum
  attributes: bits 0-3 compression (0 none, 1 deflate raw, 2 zstd), bits 16-23 encryption,
              other bits (0x800, 0x1000, ...) are copied from the official entry being overridden.
  path hash: murmur3_32(seed 0xFFFFFFFF) of the UTF-16LE lower-case path (lo) and upper-case path (hi).
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import config

try:
    from compression import zstd as _zstd  # Python 3.14+
except ImportError:  # pragma: no cover
    _zstd = None

HEADER = struct.Struct("<4sBBHII")
ENTRY = struct.Struct("<IIQQQqQ")
COMP_MASK = 0xF
ENC_MASK = 0xFF0000


def murmur3_32(data: bytes, seed: int = 0xFFFFFFFF) -> int:
    c1, c2 = 0xCC9E2D51, 0x1B873593
    h = seed & 0xFFFFFFFF
    n = len(data)
    nblocks = n // 4
    for (k,) in struct.iter_unpack("<I", data[: nblocks * 4]):
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    tail = data[nblocks * 4:]
    k = 0
    if len(tail) >= 3:
        k ^= tail[2] << 16
    if len(tail) >= 2:
        k ^= tail[1] << 8
    if len(tail) >= 1:
        k ^= tail[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
    h ^= n
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


@lru_cache(maxsize=None)
def path_hash(path: str) -> int:
    """64-bit pak hash of a pak path such as 'natives/stm/text/common/x.msg.23'."""
    lo = murmur3_32(path.lower().encode("utf-16le"))
    hi = murmur3_32(path.upper().encode("utf-16le"))
    return (hi << 32) | lo


@dataclass
class Entry:
    pak: Path
    index: int
    hash_lo: int
    hash_hi: int
    offset: int
    csize: int
    usize: int
    attributes: int
    checksum: int

    @property
    def hash(self) -> int:
        return (self.hash_hi << 32) | self.hash_lo

    @property
    def compression(self) -> int:
        return self.attributes & COMP_MASK

    @property
    def encryption(self) -> int:
        return (self.attributes & ENC_MASK) >> 16


class Pak:
    def __init__(self, path: Path):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            raw = f.read(HEADER.size)
            if len(raw) < HEADER.size:
                raise ValueError(f"{path}: too small for a pak header")
            magic, self.major, self.minor, self.feature, self.total, self.fingerprint = HEADER.unpack(raw)
            if magic != b"KPKA":
                raise ValueError(f"{path}: bad magic {magic!r}")
            if self.major != 4:
                raise ValueError(f"{path}: unsupported major version {self.major}")
            if self.feature != 0:
                raise ValueError(f"{path}: feature flags 0x{self.feature:04x} not supported")
            toc = f.read(ENTRY.size * self.total)
        self.entries = [Entry(self.path, i, *e) for i, e in enumerate(ENTRY.iter_unpack(toc))]
        self.by_hash = {e.hash: e for e in self.entries}

    def read_raw(self, e: Entry) -> bytes:
        with open(self.path, "rb") as f:
            f.seek(e.offset)
            return f.read(e.csize)

    def read(self, e: Entry) -> bytes:
        data = self.read_raw(e)
        if e.encryption:
            raise NotImplementedError(f"resource encryption type {e.encryption}")
        c = e.compression
        if c == 0:
            out = data
        elif c == 1:
            try:
                out = zlib.decompress(data, -zlib.MAX_WBITS)
            except zlib.error:
                out = zlib.decompress(data)
        elif c == 2:
            if _zstd is None:
                raise RuntimeError("zstd not available")
            out = _zstd.decompress(data)
        else:
            raise NotImplementedError(f"compression type {c}")
        if len(out) != e.usize:
            raise ValueError(f"size mismatch for entry {e.index}: {len(out)} != {e.usize}")
        return out

    @property
    def is_sfes(self) -> bool:
        return path_hash(config.MARKER_PATH) in self.by_hash


class PakSet:
    """Official base pak plus official numbered patch paks; later paks override earlier ones.

    Paks built by this toolkit (they contain MARKER_PATH) are skipped, so extraction always reads
    Capcom's data even when the Spanish patch is installed.
    """

    def __init__(self, game_dir: Path | None = None, include_sfes: bool = False):
        game_dir = Path(game_dir or config.GAME_DIR)
        self.game_dir = game_dir
        self.paks: list[Pak] = [Pak(game_dir / config.BASE_PAK)]
        self.skipped: list[Path] = []
        n = 1
        while True:
            p = game_dir / config.PATCH_FMT.format(n)
            if not p.exists():
                break
            pak = Pak(p)
            if pak.is_sfes and not include_sfes:
                self.skipped.append(p)
            else:
                self.paks.append(pak)
            n += 1
        self.highest_index = n - 1  # highest patch number present on disk (official or not)
        self.winner: dict[int, Entry] = {}
        self._pak_of: dict[Path, Pak] = {}
        for pak in self.paks:
            self._pak_of[pak.path] = pak
            for e in pak.entries:
                self.winner[e.hash] = e

    def official_patch_indices(self) -> list[int]:
        out = []
        for pak in self.paks[1:]:
            out.append(int(pak.path.name.rsplit("patch_", 1)[1].split(".")[0]))
        return out

    def next_free_index(self) -> int:
        """Patch number for our pak: one above the highest official patch on disk."""
        idx = self.official_patch_indices()
        return (max(idx) if idx else 0) + 1

    def entry(self, path: str) -> Entry | None:
        return self.winner.get(path_hash(path))

    def has(self, path: str) -> bool:
        return path_hash(path) in self.winner

    def read(self, e_or_path) -> bytes:
        e = self.entry(e_or_path) if isinstance(e_or_path, str) else e_or_path
        if e is None:
            raise KeyError(f"not in paks: {e_or_path}")
        return self._pak_of[e.pak].read(e)

    def source_name(self, path: str) -> str | None:
        e = self.entry(path)
        return e.pak.name if e else None


@lru_cache(maxsize=1)
def name_list() -> dict[int, str]:
    """hash -> path for the community name list (local reference file, used for listing only)."""
    out = {}
    with open(config.NAMELIST, encoding="utf-8") as f:
        for line in f:
            p = line.strip()
            if p:
                out[path_hash(p)] = p
    return out


@dataclass
class PendingFile:
    path: str
    data: bytes
    attributes: int


class PakWriter:
    """Collects files and writes a KPKA v4.2 patch pak with only those files."""

    def __init__(self, fingerprint: int = 0, checksum_mode: str = "zero"):
        if checksum_mode not in ("zero", "copy"):
            raise ValueError("checksum_mode must be 'zero' or 'copy'")
        self.fingerprint = fingerprint
        self.checksum_mode = checksum_mode
        self.files: dict[int, PendingFile] = {}
        self.templates: dict[int, Entry] = {}

    def add(self, path: str, data: bytes, template: Entry | None = None, attributes: int | None = None,
            compress: bool = True) -> None:
        """Add a file. Attribute bits other than compression come from `template` (the official entry
        being overridden) or from `attributes`; compression is set from `compress`."""
        path = path.replace("\\", "/")
        if attributes is None:
            attributes = template.attributes if template is not None else 0
        attributes = (attributes & ~COMP_MASK & ~ENC_MASK) | (1 if compress else 0)
        h = path_hash(path)
        self.files[h] = PendingFile(path, bytes(data), attributes)
        if template is not None:
            self.templates[h] = template

    def add_marker(self, text: str = "Spanish fan translation patch (sfes)\n") -> None:
        self.add(config.MARKER_PATH, text.encode("utf-8"), attributes=0, compress=False)

    def write(self, out_path: Path) -> list[dict]:
        out_path = Path(out_path)
        game = config.GAME_DIR.resolve()
        try:
            out_path.resolve().relative_to(game)
        except ValueError:
            pass
        else:
            raise PermissionError("PakWriter refuses to write inside the game folder")
        order = sorted(self.files)
        blobs = []
        for h in order:
            f = self.files[h]
            if f.attributes & COMP_MASK == 1:
                co = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
                blob = co.compress(f.data) + co.flush()
            else:
                blob = f.data
            blobs.append(blob)
        toc_end = HEADER.size + ENTRY.size * len(order)
        off = toc_end
        toc = bytearray()
        report = []
        for h, blob in zip(order, blobs):
            f = self.files[h]
            ck = 0
            if self.checksum_mode == "copy" and h in self.templates:
                ck = self.templates[h].checksum
            toc += ENTRY.pack(h & 0xFFFFFFFF, h >> 32, off, len(blob), len(f.data), f.attributes, ck)
            report.append({"path": f.path, "hash": f"{h:016x}", "offset": off, "csize": len(blob),
                           "usize": len(f.data), "attributes": f.attributes, "checksum": ck})
            off += len(blob)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as fo:
            fo.write(HEADER.pack(b"KPKA", 4, 2, 0, len(order), self.fingerprint))
            fo.write(toc)
            for blob in blobs:
                fo.write(blob)
        return report
