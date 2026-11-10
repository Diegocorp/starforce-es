"""RE Engine font-slot file (.fslt.4) parser and encoder. Own implementation, layout cross-checked against
REasy file_handlers/fslt (MIT, read-only reference). Verified byte-exact on all 4 game fslt files.

v4 layout (little-endian):
  0x00 u32 version (4), 'FSLT'; 0x08 u64 slot_entry_offset[16]
  slot entry (40 B): u32 nFonts, u32 nRules, u32 nTail, u32 pad, u64 pFontPtrArr, u64 pRulePtrArr, u64 pTailPtrArr
  pointer arrays: u64[max(n,1)] (a single 0 when n == 0)
  font object (24 B): f32 adjust[4] (x-offset, y-offset, x-scale, y-scale), u64 pPath (UTF-16)
  rule object (48 B): u64 pText[3], f32 adjust[2], u32 params[4]
  strings: UTF-16LE NUL-terminated, deduplicated, after all objects

Model: {"version": 4, "slots": [ {"fonts": [{"adjust": [4 floats], "path": str}],
                                   "rules": [{"texts": [3 str], "adjust": [2 floats], "params": [4 ints]}],
                                   "tail_rules": [...] } x16 ]}
Font paths look like 'GUI/ui_font/RR_System_Font.oft' -> pak path 'natives/stm/gui/ui_font/rr_system_font.oft.1'.
"""
from __future__ import annotations

import copy
import struct

FSLT_EN = "natives/stm/gui/ui_font/fontslot/fontslot_en.fslt.4"
FSLT_JA = "natives/stm/gui/ui_font/fontslot/fontslot_ja.fslt.4"


def font_pak_path(font_path: str) -> str:
    return "natives/stm/" + font_path.lower() + ".1"


def _string(d: bytes, off: int) -> str:
    e = off
    while d[e:e + 2] != b"\0\0":
        e += 2
    return d[off:e].decode("utf-16-le")


def parse(data: bytes) -> dict:
    ver, magic = struct.unpack_from("<I4s", data, 0)
    if magic != b"FSLT" or ver != 4:
        raise ValueError(f"not an FSLT v4 file: {ver} {magic!r}")
    slot_offs = struct.unpack_from("<16Q", data, 8)
    slots = []
    for so in slot_offs:
        nf, nr, nt, _pad = struct.unpack_from("<4I", data, so)
        pf, pr, pt = struct.unpack_from("<3Q", data, so + 16)

        def ptrs(base, n):
            return [struct.unpack_from("<Q", data, base + 8 * j)[0] for j in range(n)] if base else []

        fonts = []
        for p in ptrs(pf, nf):
            adj = list(struct.unpack_from("<4f", data, p))
            (sp,) = struct.unpack_from("<Q", data, p + 16)
            fonts.append({"adjust": adj, "path": _string(data, sp)})

        def rules(base, n):
            out = []
            for p in ptrs(base, n):
                tp = struct.unpack_from("<3Q", data, p)
                adj = list(struct.unpack_from("<2f", data, p + 24))
                prm = list(struct.unpack_from("<4I", data, p + 32))
                out.append({"texts": [_string(data, t) for t in tp], "adjust": adj, "params": prm})
            return out

        slots.append({"fonts": fonts, "rules": rules(pr, nr), "tail_rules": rules(pt, nt)})
    return {"version": ver, "slots": slots}


def encode(model: dict) -> bytes:
    slots = model["slots"]
    if len(slots) != 16:
        raise ValueError("FSLT needs exactly 16 slots")
    head = 0x88
    out = bytearray(head + 40 * 16)
    patches = []
    arrays = []
    pos = len(out)
    for s in slots:
        row = []
        for items in (s["fonts"], s["rules"], s["tail_rules"]):
            row.append(pos)
            pos += 8 * max(len(items), 1)
        arrays.append(row)
    obj = pos
    for s in slots:
        pos += 24 * len(s["fonts"]) + 48 * len(s["rules"]) + 48 * len(s["tail_rules"])
    out += b"\0" * (pos - len(out))
    for i, (s, row) in enumerate(zip(slots, arrays)):
        struct.pack_into("<III4xQQQ", out, head + 40 * i, len(s["fonts"]), len(s["rules"]),
                         len(s["tail_rules"]), *row)
    for s, row in zip(slots, arrays):
        for (items, size), arr in zip(((s["fonts"], 24), (s["rules"], 48), (s["tail_rules"], 48)), row):
            for j in range(max(len(items), 1)):
                struct.pack_into("<Q", out, arr + 8 * j, obj + size * j if j < len(items) else 0)
            for j, it in enumerate(items):
                at = obj + size * j
                if size == 24:
                    struct.pack_into("<4f", out, at, *it["adjust"])
                    patches.append((at + 16, it["path"]))
                else:
                    for k, tx in enumerate(it["texts"]):
                        patches.append((at + 8 * k, tx))
                    struct.pack_into("<2f4I", out, at + 24, *it["adjust"], *it["params"])
            obj += size * len(items)
    texts = [f["path"] for s in slots for f in s["fonts"]]
    for grp in ("rules", "tail_rules"):
        for s in slots:
            for r in s[grp]:
                texts += r["texts"]
    pool: dict[str, int] = {}
    for t in texts:
        if t not in pool:
            pool[t] = len(out)
            out += t.encode("utf-16-le") + b"\0\0"
    for at, t in patches:
        struct.pack_into("<Q", out, at, pool[t])
    struct.pack_into("<I4s", out, 0, 4, b"FSLT")
    struct.pack_into("<16Q", out, 8, *[head + 40 * i for i in range(16)])
    return bytes(out)


def clone(model: dict) -> dict:
    return copy.deepcopy(model)
