r"""Pixel widths of game text, measured with the real fonts through the English font-slot chains.

Width model (contract section 5): each character is drawn by the first font of the slot's list in
fontslot_en.fslt.4 whose cmap has it (per-character fallback). Its advance in em is
    hmtx advance / unitsPerEm * x-scale of that font entry (fslt adjust[2]).
No kerning. A line is the sum of its characters; control codes follow codes.Token.prints:
'none' = 0, 'glyph' = 1.0 em, 'insert' = width from an InsertResolver (default: 8 average lowercase
letters of the slot). Pixels = em * font size (+ optional letter spacing per drawn character).
Not modelled (as in the contract): the fslt per-character rules (offsets for full-width letters in slots
0/1, ' /' and ':~' pairs). Metrics.space_override_em (default None) replaces the space advance once the
in-game check of the space width (Phase 1 open question 5) is done: a number applies to every slot, a
mapping slot -> em to the slots it names. Metrics.state_key() changes with it, so caches keyed by it
(sfes.wrap observed limits) never mix measurements made before and after.

Cache: work/cache/metrics_en.json holds the resolved advances for slots 0-7 over a fixed coverage set
(Latin, punctuation, symbol, kana and full-width blocks plus every character of the English and Japanese
slots of the phase1 files). It is keyed by a fingerprint (sha256 of the fslt and of every chain font, plus
the cache schema and coverage ranges), so it rebuilds by itself when an input changes. Characters outside
the coverage set are resolved from the fonts on first use (fonts load lazily).
Font overrides (pak path -> bytes, e.g. a patched rr_system_font.oft.1 or a patched fslt) are measured
instead of the official files and are cached under their own key in the same file.

CLI: run.py metrics [--rebuild] [--slot N] [--size PX] [--text TEXT]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fontTools.ttLib import TTFont

from . import codes, config, fslt, msg23, oft
from . import scope as scopes
from .pak import PakSet

CACHE_PATH = config.CACHE / "metrics_en.json"
SCHEMA = 1
SLOTS = tuple(range(8))
BASE_KEY = "base"
MAX_OVERRIDE_TABLES = 4      # font-override tables kept in the cache (fontpatch has four variants)
COVERAGE_SCOPE = "phase1"
COVERAGE_LANGS = (config.LANG_EN, config.LANG_JA)
# Unicode blocks always resolved into the cache (CJK ideographs and Hangul are left to lazy lookup).
COVERAGE_RANGES = (
    (0x0020, 0x024F),   # Basic Latin .. Latin Extended-B
    (0x2000, 0x206F),   # General Punctuation
    (0x20A0, 0x20CF),   # Currency Symbols
    (0x2100, 0x214F),   # Letterlike Symbols
    (0x2150, 0x218F),   # Number Forms
    (0x2190, 0x21FF),   # Arrows
    (0x2460, 0x24FF),   # Enclosed Alphanumerics
    (0x2500, 0x257F),   # Box Drawing
    (0x25A0, 0x25FF),   # Geometric Shapes
    (0x2600, 0x26FF),   # Miscellaneous Symbols
    (0x3000, 0x303F),   # CJK Symbols and Punctuation
    (0x3040, 0x30FF),   # Hiragana, Katakana
    (0x31F0, 0x31FF),   # Katakana Phonetic Extensions
    (0xFF00, 0xFFEF),   # Halfwidth and Fullwidth Forms
)
LOWERCASE = "abcdefghijklmnopqrstuvwxyz"
DIGITS = "0123456789"
DEFAULT_INSERT_LETTERS = 8
GLYPH_EM = 1.0

Glyph = tuple[float, str]          # (advance in em including x-scale, name of the font that draws it)
SpaceOverride = float | Mapping[int, float] | None
_MISS = object()


class InsertWidths(Protocol):
    """Anything that knows the width of runtime inserts (sfes.inserts.InsertResolver)."""

    def width_em(self, token: codes.Token, slot: int) -> float: ...


@dataclass(frozen=True)
class ChainFont:
    """One font of a slot's fallback list."""
    name: str          # file stem, e.g. 'BO-TBGoStd-Medium'
    path: str          # pak path, e.g. 'natives/stm/gui/ui_font/bo-tbgostd-medium.oft.1'
    x_scale: float     # fslt adjust[2] (float32 as stored)


@dataclass(frozen=True)
class _Font:
    upm: int
    cmap: dict[int, str]
    advances: dict[str, int]

    def advance_em(self, cp: int, x_scale: float) -> float | None:
        g = self.cmap.get(cp)
        return None if g is None else self.advances[g] / self.upm * x_scale


class _FontSource:
    """fslt and font bytes from the official paks, or from overrides (pak path -> bytes)."""

    def __init__(self, overrides: Mapping[str, bytes] | None, paks: PakSet | None):
        self.overrides = dict(overrides or {})
        self._paks = paks
        self._fonts: dict[str, _Font] = {}

    @property
    def paks(self) -> PakSet:
        if self._paks is None:
            self._paks = PakSet()
        return self._paks

    def read(self, path: str) -> bytes:
        return self.overrides[path] if path in self.overrides else self.paks.read(path)

    def font(self, path: str) -> _Font:
        if path not in self._fonts:
            self._fonts[path] = _parse_font(self.read(path))
        return self._fonts[path]


def _parse_font(raw: bytes) -> _Font:
    logging.getLogger("fontTools").setLevel(logging.ERROR)   # silence 'created timestamp' warnings
    tt = TTFont(io.BytesIO(oft.decode(raw)), lazy=True)
    hmtx = tt["hmtx"].metrics
    return _Font(tt["head"].unitsPerEm, dict(tt.getBestCmap()), {g: adv for g, (adv, _lsb) in hmtx.items()})


def font_name(fslt_path: str) -> str:
    """'GUI/ui_font/RR_System_Font.oft' -> 'RR_System_Font'."""
    return fslt_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def chains_from_fslt(raw: bytes) -> dict[int, list[ChainFont]]:
    model = fslt.parse(raw)
    return {s: [ChainFont(font_name(f["path"]), fslt.font_pak_path(f["path"]), f["adjust"][2])
                for f in model["slots"][s]["fonts"]] for s in SLOTS}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _input_hashes(src: _FontSource, chains: dict[int, list[ChainFont]], fslt_raw: bytes) -> dict[str, str]:
    paths = sorted({f.path for chain in chains.values() for f in chain})
    out = {fslt.FSLT_EN: _sha256(fslt_raw)}
    out.update({p: _sha256(src.read(p)) for p in paths})
    return out


def fingerprint(inputs: Mapping[str, str]) -> str:
    material = {"schema": SCHEMA, "ranges": COVERAGE_RANGES, "inputs": dict(sorted(inputs.items()))}
    return _sha256(json.dumps(material, sort_keys=True).encode("utf-8"))


def cache_key(overrides: Mapping[str, bytes] | None) -> str:
    """'base' for the official files; 'ovr-<hash>' for a set of overrides."""
    if not overrides:
        return BASE_KEY
    material = "\n".join(f"{p}:{_sha256(b)}" for p, b in sorted(overrides.items()))
    return "ovr-" + _sha256(material.encode("utf-8"))[:16]


# ------------------------------------------------------------------------------------------- building

def _range_chars() -> set[str]:
    return {chr(cp) for lo, hi in COVERAGE_RANGES for cp in range(lo, hi + 1)}


def _visible_chars(text: str) -> set[str]:
    return {ch for tok in codes.tokenize(text) if tok.kind == "text" for ch in tok.raw}


def scope_chars(ps: PakSet, scope: str = COVERAGE_SCOPE) -> set[str]:
    """Every character of the visible text (codes and line breaks removed) of the English and Japanese
    slots of a scope's msg files."""
    out: set[str] = set()
    for spec in scopes.files(scope, ps):
        m = msg23.parse(ps.read(spec.path))
        for e in m.entries:
            for lang in COVERAGE_LANGS:
                out.update(_visible_chars(m.get(e, lang)))
    return out


def _resolve_chain(src: _FontSource, chain: list[ChainFont], chars: list[str]) -> dict:
    served: list[list[str]] = [[] for _ in chain]
    advances: list[list[str]] = [[] for _ in chain]
    fonts = [src.font(f.path) for f in chain]
    for ch in chars:
        for i, font in enumerate(fonts):
            g = font.cmap.get(ord(ch))
            if g is not None:
                served[i].append(ch)
                advances[i].append(str(font.advances[g]))
                break
    return {"fonts": [{"name": f.name, "path": f.path, "x_scale": f.x_scale, "upm": font.upm}
                      for f, font in zip(chain, fonts)],
            "chars": ["".join(s) for s in served],
            "advances": [" ".join(a) for a in advances]}


def build_table(src: _FontSource, chains: dict[int, list[ChainFont]], inputs: dict[str, str],
                extra: set[str]) -> dict:
    """Cache document: coverage definition plus, per distinct chain, the characters each font serves and
    their advances in font units (em = units / upm * x_scale)."""
    ranged = _range_chars()
    covered = sorted(ranged | extra)
    groups: dict[tuple[ChainFont, ...], list[int]] = {}
    for s in SLOTS:
        groups.setdefault(tuple(chains[s]), []).append(s)
    coverage = {"ranges": [list(r) for r in COVERAGE_RANGES], "extra": "".join(sorted(extra - ranged))}
    return {"schema": SCHEMA, "fingerprint": fingerprint(inputs), "inputs": inputs, "coverage": coverage,
            "chains": [{"slots": slots, **_resolve_chain(src, list(chain), covered)}
                       for chain, slots in groups.items()]}


# ---------------------------------------------------------------------------------------------- cache

def _read_cache(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) and doc.get("schema") == SCHEMA else {}


def _cached_table(path: Path, key: str, fp: str) -> dict | None:
    table = _read_cache(path).get("tables", {}).get(key)
    return table if isinstance(table, dict) and table.get("fingerprint") == fp else None


def _write_cache(path: Path, key: str, table: dict) -> None:
    """Store `table` under `key`; keep the base table and the MAX_OVERRIDE_TABLES most recently written
    override tables (one per font variant in practice), so switching variants does not rebuild them.
    Stale tables are harmless: every read checks the table's fingerprint."""
    old = _read_cache(path)
    tables = old.get("tables", {})
    recent = [k for k in old.get("recent", []) if k in tables and k not in (key, BASE_KEY)]
    if key != BASE_KEY:
        recent = (recent + [key])[-MAX_OVERRIDE_TABLES:]
    kept = {k: tables[k] for k in recent if k != key}
    if key != BASE_KEY and BASE_KEY in tables:
        kept[BASE_KEY] = tables[BASE_KEY]
    kept[key] = table
    doc = {"schema": SCHEMA, "recent": recent, "tables": dict(sorted(kept.items()))}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# -------------------------------------------------------------------------------------------- metrics

def _slot_tables(table: dict) -> dict[int, dict[str, Glyph | None]]:
    cov = table["coverage"]
    covered = {chr(cp) for lo, hi in cov["ranges"] for cp in range(lo, hi + 1)} | set(cov["extra"])
    out: dict[int, dict[str, Glyph | None]] = {}
    for chain in table["chains"]:
        t: dict[str, Glyph | None] = dict.fromkeys(covered)
        for f, chars, advs in zip(chain["fonts"], chain["chars"], chain["advances"]):
            for ch, adv in zip(chars, advs.split()):
                t[ch] = (int(adv) / f["upm"] * f["x_scale"], f["name"])
        for s in chain["slots"]:
            out[s] = t
    return out


def _chains_of(table: dict) -> dict[int, list[ChainFont]]:
    return {s: [ChainFont(f["name"], f["path"], f["x_scale"]) for f in chain["fonts"]]
            for chain in table["chains"] for s in chain["slots"]}


class Metrics:
    """Advance widths per font slot (0-7). Build with Metrics.load()."""

    def __init__(self, table: dict, src: _FontSource, key: str):
        self.key = key
        self.fingerprint: str = table["fingerprint"]
        self.inputs: dict[str, str] = table["inputs"]
        self.space_override_em: SpaceOverride = None   # em for all slots, or slot -> em (in-game calibration)
        self._src = src
        self._chains = _chains_of(table)
        self._tables = _slot_tables(table)

    @classmethod
    def load(cls, rebuild: bool = False, overrides: Mapping[str, bytes] | None = None,
             cache_path: Path = CACHE_PATH, paks: PakSet | None = None) -> Metrics:
        """Metrics from the cache, rebuilt from the paks when missing, stale or `rebuild` is set.
        `overrides` maps pak paths (fonts or the fslt) to replacement bytes."""
        src = _FontSource(overrides, paks)
        fslt_raw = src.read(fslt.FSLT_EN)
        chains = chains_from_fslt(fslt_raw)
        inputs = _input_hashes(src, chains, fslt_raw)
        key = cache_key(overrides)
        table = None if rebuild else _cached_table(Path(cache_path), key, fingerprint(inputs))
        if table is None:
            table = build_table(src, chains, inputs, scope_chars(src.paks))
            _write_cache(Path(cache_path), key, table)
        return cls(table, src, key)

    @property
    def paks(self) -> PakSet:
        """The official paks the fonts come from (also used to read a file's English for its limits)."""
        return self._src.paks

    def state_key(self) -> tuple:
        """Everything a measurement depends on: font set (cache key, fingerprint) and the space override."""
        ov = self.space_override_em
        space = tuple(sorted(ov.items())) if isinstance(ov, Mapping) else ov
        return self.key, self.fingerprint, space

    # ---- per character

    def chain(self, slot: int) -> list[ChainFont]:
        return list(self._chains[slot])

    def _glyph(self, ch: str, slot: int) -> Glyph | None:
        table = self._tables[slot]
        g = table.get(ch, _MISS)
        if g is _MISS:
            g = self._resolve_live(ch, slot)
        return g

    def _resolve_live(self, ch: str, slot: int) -> Glyph | None:
        found: Glyph | None = None
        for f in self._chains[slot]:
            em = self._src.font(f.path).advance_em(ord(ch), f.x_scale)
            if em is not None:
                found = (em, f.name)
                break
        self._tables[slot][ch] = found
        return found

    def _space_override(self, slot: int) -> float | None:
        ov = self.space_override_em
        if isinstance(ov, Mapping):
            ov = ov.get(slot)
        return None if ov is None else float(ov)

    def advance_em(self, ch: str, slot: int) -> float | None:
        """Advance of one character in em, or None if no font of the slot has it."""
        g = self._glyph(ch, slot)
        if g is None:
            return None
        override = self._space_override(slot) if ch == " " else None
        return g[0] if override is None else override

    def font_of(self, ch: str, slot: int) -> str | None:
        g = self._glyph(ch, slot)
        return None if g is None else g[1]

    def supported(self, ch: str, slot: int) -> bool:
        return self._glyph(ch, slot) is not None

    def unsupported_chars(self, text: str, slot: int) -> list[str]:
        """Sorted distinct characters of the visible text that no font of the slot can draw."""
        return sorted(ch for ch in _visible_chars(text) if not self.supported(ch, slot))

    def avg_lowercase_em(self, slot: int) -> float:
        return sum(self.advance_em(c, slot) or 0.0 for c in LOWERCASE) / len(LOWERCASE)

    def default_insert_em(self, slot: int) -> float:
        """Width assumed for a runtime insert nobody knows more about: 8 average lowercase letters."""
        return DEFAULT_INSERT_LETTERS * self.avg_lowercase_em(slot)

    def digit_em(self, slot: int) -> float:
        """Widest digit of the slot (numbers are inserted at run time)."""
        return max(self.advance_em(c, slot) or 0.0 for c in DIGITS)

    # ---- strings

    def text_em(self, text: str, slot: int) -> float:
        """Sum of advances of plain characters (no code handling); unsupported characters count 0."""
        total = 0.0
        for ch in text:
            g = self._glyph(ch, slot)
            if g is not None:
                total += g[0]
        override = self._space_override(slot)
        space = self._glyph(" ", slot) if override is not None and " " in text else None
        if space is not None:
            total += text.count(" ") * (override - space[0])
        return total

    def token_em(self, tok: codes.Token, slot: int, resolver: InsertWidths | None = None) -> float:
        kind = tok.prints
        if kind == "text":
            return self.text_em(tok.raw, slot)
        if kind == "glyph":
            return GLYPH_EM
        if kind == "insert":
            return resolver.width_em(tok, slot) if resolver is not None else self.default_insert_em(slot)
        return 0.0                      # 'none' (pacing, portraits) and 'break'

    def line_em(self, line: str, slot: int, resolver: InsertWidths | None = None) -> float:
        """Width of one line in em. Line breaks inside `line` count 0 (use lines_px for multi-line text)."""
        return sum(self.token_em(tok, slot, resolver) for tok in codes.tokenize(line))

    def line_px(self, line: str, slot: int, size: float, resolver: InsertWidths | None = None,
                letter_space: float = 0.0) -> float:
        """Width in px at font size `size`; `letter_space` px is added per drawn character (plain characters
        and glyph codes; inserted runtime text gets none)."""
        px = self.line_em(line, slot, resolver) * size
        if letter_space:
            px += letter_space * _drawn_count(line)
        return px

    def lines_px(self, text: str, slot: int, size: float,
                 resolver: InsertWidths | None = None) -> list[float]:
        """One width per CRLF-separated line (codes.split_lines), trailing empty lines included."""
        return [self.line_px(line, slot, size, resolver) for line in codes.split_lines(text)]


def _drawn_count(line: str) -> int:
    """Characters that get letter spacing: plain characters plus one per glyph code."""
    n = 0
    for t in codes.tokenize(line):
        if t.prints == "text":
            n += len(t.raw)
        elif t.prints == "glyph":
            n += 1
    return n


# ------------------------------------------------------------------------------------------------ CLI

def _describe(m: Metrics, slots: Iterable[int]) -> list[str]:
    out = [f"cache key {m.key}  fingerprint {m.fingerprint[:16]}  inputs {len(m.inputs)}"]
    for s in slots:
        names = " > ".join(f"{f.name} x{f.x_scale:.3g}" for f in m.chain(s))
        out.append(f"slot {s}: {names}  | avg lowercase {m.avg_lowercase_em(s):.4f} em, "
                   f"space {m.advance_em(' ', s)} em ({m.font_of(' ', s)})")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="metrics", description="Font-slot metrics (English chains).")
    ap.add_argument("--rebuild", action="store_true", help="rebuild the cache from the paks")
    ap.add_argument("--slot", type=int, default=0, choices=SLOTS)
    ap.add_argument("--size", type=float, default=60.0, help="font size in px for --text")
    ap.add_argument("--text", help="measure this text (\\n separates lines); codes use default widths")
    args = ap.parse_args(argv)
    m = Metrics.load(rebuild=args.rebuild)
    for line in _describe(m, SLOTS if args.text is None else (args.slot,)):
        print(line)
    if args.text is not None:
        text = args.text.replace("\\n", "\r\n")
        for line, px in zip(codes.split_lines(text), m.lines_px(text, args.slot, args.size)):
            print(f"{px:8.1f} px  {line!r}")
        missing = m.unsupported_chars(text, args.slot)
        if missing:
            print("unsupported:", " ".join(f"U+{ord(c):04X}" for c in missing))
    return 0
