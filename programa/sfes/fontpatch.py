r"""Spanish inverted marks (¡ U+00A1, ¿ U+00BF) for the dialogue font chain, without modified commercial fonts.

Problem: in English mode, fontslot_en slots 0 (dialogue) and 1 (text input) use the chain
RR_System_Font > BO-TBGoStd-Medium > FZLTH > FZLTHB. RR_System_Font maps only the space, 0-9, U+3000 and the
full-width digits, so BO-TBGo draws every letter. BO-TBGo's ¡ stands on the baseline like an 'i' ("¡Hola!" reads
"iHola!") and its ¿ has the same problem. Commercial fonts (BO-*, DFP, FZ*) are never shipped modified, so this
module draws two original glyphs and puts them in a font we may ship:

  rrsystem          (default) RR_System_Font gains the two glyphs. It is first in the slot 0/1 chains, so it now
                    serves ¡ and ¿ and nothing else changes; no fslt edit. Other glyphs, metrics and tables stay
                    as they are, except what two extra glyphs require (glyf, loca, hmtx, hhea.numberOfHMetrics,
                    maxp, cmap, post, head bounding box) plus one name record (ID 10) describing the addition.
  rrsystem_range    rrsystem plus the slot 0/1 range rule ':'..'~' widened to ':'..U+00FF.
  supplement        a separate font with the same two glyphs, SFES_Supplement.oft, inserted right after
                    RR_System_Font in slots 0 and 1 of fontslot_en.
  supplement_range  supplement plus the widened range rule.

Glyph design (own outlines from simple geometry, no outline points taken from any font): ¡ is a round dot above a
tapered stem that hangs below the baseline; ¿ is the same dot above an inverted question hook (a stroked elliptical
bowl, a straight diagonal and a reverse curve into a tapered stem). This is the standard Latin form, an upright
mark turned 180 degrees, built here from a few measured sizes plus own proportions:
  - BO-TBGo metrics (REF, 1000 units/em; measure_reference() re-measures them): x-height, round overshoot,
    descender, the 'i' stem width, the dot size and dot gap of ! and ?, the advances of ! and ?, and the bowl,
    stroke and stem-end sizes of '?'. Everything is scaled to RR_System's 1024 units/em.
  - Own choices: the dots are round (same area as BO-TBGo's square dots) and overshoot the x-height like round
    letters; the ¡ stem tapers from 0.75 to 1.2 'i' stems; both marks end on BO-TBGo's descender line (g, j),
    so they never hang deeper than letters the game already draws.

Vertical model: both fonts have fslt adjust (0, 0, 1, 1) in slots 0 and 1 and the engine is assumed to share one
baseline across the chain. That is unverified: Capcom's own RR digits (y 40..808/1024) do not line up exactly with
BO capitals (10..725/1000) under it (digits_60px.png shows the digits next to the new marks). Slots 0 and 1 also
carry range rules ' '..'/' and ':'..'~' with y adjust -4 (taken as 4 px up); they skip the digits, which suggests
the rules apply per character whichever font serves it. ¡ and ¿ lie outside those ranges, so by default they sit
where BO-TBGo's own Latin-1 glyphs (á é ñ ...) sit; the *_range variants move all of them up together. `rise`
(font units, default 0) lifts the new glyphs if the in-game check shows otherwise.

Descent: RR_System_Font declares descent 0 (hhea and usWinDescent) and keeps it in rrsystem*, because only the two
added glyphs reach below the baseline. If the in-game check shows them clipped at the baseline, supplement* is the
fallback as built: its ascent and line gap are RR_System's, but its declared descent covers its glyphs.

Outputs: build/fonts/<variant>/natives/stm/gui/ui_font/... plus build/fonts/<variant>/report.json;
previews (--preview) in work/fontpreview/*.png, drawn by the small rasterizer at the end of this module.

CLI: run.py fontpatch [--variant rrsystem|rrsystem_range|supplement|supplement_range|all] [--out DIR]
                      [--rise UNITS] [--preview] [--preview-dir DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import itertools
import json
import math
import os
import struct
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, NamedTuple

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.basePen import BasePen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.standardGlyphOrder import standardGlyphOrder
from fontTools.ttLib.tables._g_l_y_f import Glyph

from . import config, fslt, oft
from .pak import PakSet

Point = tuple[float, float]
Contour = tuple[tuple[int, int, bool], ...]          # (x, y, on_curve), starts with an on-curve point

RR_FSLT_PATH = "GUI/ui_font/RR_System_Font.oft"
BO_FSLT_PATH = "GUI/ui_font/BO-TBGoStd-Medium.oft"
SUPPLEMENT_FSLT_PATH = "GUI/ui_font/SFES_Supplement.oft"
RR_PATH = fslt.font_pak_path(RR_FSLT_PATH)
BO_PATH = fslt.font_pak_path(BO_FSLT_PATH)
SUPPLEMENT_PATH = fslt.font_pak_path(SUPPLEMENT_FSLT_PATH)
DIALOGUE_SLOTS = (0, 1)
RANGE_FROM, RANGE_TO, RANGE_EXTENDED_TO = ":", "~", "\u00ff"


class Variant(NamedTuple):
    supplement: bool        # glyphs in a new font after RR_System_Font instead of inside RR_System_Font
    extend_range: bool      # widen the slot 0/1 range rule ':'..'~' to ':'..U+00FF
    summary: str


VARIANTS: dict[str, Variant] = {
    "rrsystem": Variant(False, False, "RR_System_Font with two added glyphs (U+00A1, U+00BF); no fslt change"),
    "rrsystem_range": Variant(False, True, "rrsystem, plus the slot 0/1 range rule ':'..'~' widened to "
                                           "':'..U+00FF"),
    "supplement": Variant(True, False, "new font SFES_Supplement.oft (U+00A1, U+00BF) after RR_System_Font in "
                                       "fontslot_en slots 0 and 1"),
    "supplement_range": Variant(True, True, "supplement, plus the slot 0/1 range rule ':'..'~' widened to "
                                            "':'..U+00FF"),
}
OUT_DIR = config.BUILD / "fonts"
PREVIEW_DIR = config.WORK / "fontpreview"

UPM = 1024                       # RR_System_Font units per em; the supplement font uses the same
SAMPLE = "¡Hola! ¿Qué tal? iHola ¡Sí! ¿Cómo estás, Geo? ¡¿Ñandú?!"
DIGIT_SAMPLE = "¡Gané 4500z! ¿120 HP? H0x1, 2:30 ¡¿Hp9?!"     # RR_System digits next to BO letters

RR_DESCRIPTION = ("Includes U+00A1 and U+00BF glyphs with original outlines, added for a Spanish fan "
                  "translation patch.")
SUPPLEMENT_NAMES = {
    "copyright": "Copyright (c) 2026, SFES Spanish fan translation project.",
    "familyName": "SFES Supplement",
    "styleName": "Regular",
    "uniqueFontIdentifier": "SFES Supplement Regular 1.000",
    "fullName": "SFES Supplement Regular",
    "version": "Version 1.000",
    "psName": "SFESSupplement-Regular",
    "description": "Inverted exclamation and question marks (U+00A1, U+00BF) with original outlines, "
                   "for the dialogue text of a Spanish fan translation patch.",
    "licenseDescription": "Free of charge. Made for and distributed with the SFES Spanish fan translation patch.",
}
SUPPLEMENT_TIMESTAMP = 3_876_336_000      # 2026-11-01 00:00 UTC in head-table seconds; fixed for stable output
NOTDEF_ADVANCE = 512
# RR_System_Font's vertical metrics (tests compare them with the game font). The supplement font copies them,
# except that its declared descent covers its glyphs (see supplement_vmetrics()).
RR_VMETRICS = {"hhea": {"ascent": 813, "descent": 0, "lineGap": 92},
               "OS/2": {"sTypoAscender": 819, "sTypoDescender": -205, "sTypoLineGap": 92,
                        "usWinAscent": 813, "usWinDescent": 0}}
VERTICAL_MODEL = ("Slot 0/1 fslt adjust is (0, 0, 1, 1) for both RR_System_Font and BO-TBGoStd-Medium; the chain "
                  "is assumed to share one baseline (unverified: RR digits y 40..808/1024 vs BO capitals "
                  "10..725/1000, see digits_60px.png). Range rules ' '..'/' and ':'..'~' carry y adjust -4 (taken "
                  "as 4 px up) and skip the digits; U+00A1/U+00BF lie outside them, so by default the new glyphs "
                  "sit like BO-TBGo's own Latin-1 glyphs (accented letters). The *_range variants widen the rule "
                  "so all of them move together. 'rise_units' lifts the new glyphs if the in-game check shows "
                  "otherwise (68 units = 4 px at 60 px). Both marks end on BO-TBGo's descender line. rrsystem* "
                  "keep RR_System's declared descent 0; supplement* declare a descent that covers the glyphs, so "
                  "they are the ready-made fallback if the probe shows the marks clipped at the baseline.")


# --------------------------------------------------------------------------------------------- reference metrics

@dataclass(frozen=True)
class Ref:
    """BO-TBGoStd-Medium metrics (font units) that size the new glyphs; see measure_reference()."""
    upm: int
    base: int                # bottom of flat letters ('x')
    x_height: int            # top of 'x'
    overshoot: int           # how far round letters rise above the x-height ('o')
    descender: int           # bottom of the deepest descender among g j p q y (negative)
    stem: int                # width of the 'i' stem
    dot: int                 # mean side of the square dots of ! ? i
    dot_gap: int             # mean gap between the dot and the rest of ! and ?
    excl_advance: int
    quest_advance: int
    quest_left: int          # '?' bbox
    quest_right: int
    quest_top: int
    quest_stem_end_w: int    # '?' stem width at its end next to the dot
    quest_stroke_v: int      # '?' stroke thickness where the bowl is vertical (widest point, right side)
    quest_stroke_h: int      # '?' stroke thickness where the bowl is horizontal (top)
    quest_bowl_mid: int      # y of the bowl's widest point


REF = Ref(upm=1000, base=10, x_height=510, overshoot=15, descender=-196, stem=100, dot=113, dot_gap=70,
          excl_advance=302, quest_advance=551, quest_left=31, quest_right=500, quest_top=740, quest_stem_end_w=83,
          quest_stroke_v=102, quest_stroke_h=80, quest_bowl_mid=550)

# Own design choices (reference units and proportions).
DOT_SCALE = 2 / math.sqrt(math.pi)  # round dot diameter per side of BO-TBGo's square dot (same area)
EXCL_TAPER = (0.75, 1.2)    # ¡ stem width next to the dot and at the foot, in 'i' stems
HOOK_START_DEG = 190.0      # polar angle of the terminal on the bowl ellipse (180 = leftmost point)
HOOK_TURN_DEG = -50.0       # where the bowl hands over to the straight diagonal
NECK_RADIUS = 120.0         # reverse curve from the diagonal into the vertical stem
ARC_STEP_DEG = 15.0         # one quadratic segment per this much turning


def measure_reference(ps: PakSet) -> Ref:
    """Measure REF on the game's BO-TBGoStd-Medium (outline slices and bounding boxes only)."""
    font = load_font(oft.decode(ps.read(BO_PATH)))
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()

    def outline(ch: str) -> list[list[Point]]:
        return _flatten(glyphs, cmap[ord(ch)])

    def advance(ch: str) -> int:
        return font["hmtx"][cmap[ord(ch)]][0]

    x_box = _bbox(outline("x"))
    excl_dot, excl_stem = _split_dot(outline("!"))
    quest_dot, hook = _split_dot(outline("?"))
    i_dot = max(outline("i"), key=lambda c: _bbox([c])[1])
    dots = [_bbox([c]) for c in (excl_dot, quest_dot, i_dot)]
    gaps = [_bbox([body])[1] - _bbox([dot])[3] for dot, body in ((excl_dot, excl_stem), (quest_dot, hook))]
    q_box, hook_box = _bbox([quest_dot, hook]), _bbox([hook])
    mid = _widest_y(hook, hook_box[1], hook_box[3])
    stem_end_y = hook_box[1] + 8            # just above the flat end of the '?' stem
    return Ref(
        upm=font["head"].unitsPerEm, base=round(x_box[1]), x_height=round(x_box[3]),
        overshoot=round(_bbox(outline("o"))[3] - x_box[3]),
        descender=round(min(_bbox(outline(ch))[1] for ch in "gjpqy")),
        stem=round(_width(_spans(_crossings(outline("i"), (x_box[1] + x_box[3]) / 2, 1))[0])),
        dot=round(sum((b[2] - b[0]) + (b[3] - b[1]) for b in dots) / (2 * len(dots))),
        dot_gap=round(sum(gaps) / len(gaps)), excl_advance=advance("!"),
        quest_advance=advance("?"), quest_left=round(q_box[0]), quest_right=round(q_box[2]),
        quest_top=round(q_box[3]),
        quest_stem_end_w=round(_width(_spans(_crossings([hook], stem_end_y, 1))[0])),
        quest_stroke_v=round(_width(_spans(_crossings([hook], mid, 1))[-1])),
        quest_stroke_h=round(_width(_spans(_crossings([hook], (q_box[0] + q_box[2]) / 2, 0))[-1])),
        quest_bowl_mid=round(mid))


def _split_dot(contours: list[list[Point]]) -> tuple[list[Point], list[Point]]:
    """(dot, rest) of a two-contour mark; the dot is the contour with the lowest top."""
    dot = min(contours, key=lambda c: _bbox([c])[3])
    rest = [c for c in contours if c is not dot]
    if len(rest) != 1:
        raise ValueError("expected a mark made of a dot and one other contour")
    return dot, rest[0]


def _widest_y(contour: list[Point], y0: float, y1: float) -> float:
    """Mean y of the rows where the contour reaches furthest right."""
    rows = []
    for y in range(math.ceil(y0), math.floor(y1)):
        xs = _crossings([contour], y + 0.5, 1)
        if xs:
            rows.append((xs[-1], y + 0.5))
    best = max(x for x, _ in rows)
    near = [y for x, y in rows if x >= best - 0.5]
    return sum(near) / len(near)


def _crossings(contours: list[list[Point]], at: float, axis: int) -> list[float]:
    """Sorted crossings of the polylines with the line {axis coordinate == at} (axis 1: horizontal line)."""
    other = 1 - axis
    out = []
    for c in contours:
        for p, q in zip(c, c[1:] + c[:1]):
            if (p[axis] <= at < q[axis]) or (q[axis] <= at < p[axis]):
                out.append(p[other] + (at - p[axis]) * (q[other] - p[other]) / (q[axis] - p[axis]))
    return sorted(out)


def _spans(crossings: list[float]) -> list[Point]:
    return list(zip(crossings[::2], crossings[1::2]))


def _width(span: Point) -> float:
    return span[1] - span[0]


def _bbox(contours: list[list[Point]]) -> tuple[float, float, float, float]:
    xs = [p[0] for c in contours for p in c]
    ys = [p[1] for c in contours for p in c]
    return min(xs), min(ys), max(xs), max(ys)


# ------------------------------------------------------------------------------------------------ glyph design

@dataclass(frozen=True)
class NewGlyph:
    """An added glyph in 1024-unit coordinates."""
    name: str
    codepoint: int
    advance: int
    contours: tuple[Contour, ...]

    def bounds(self) -> tuple[int, int, int, int]:
        xs = [p[0] for c in self.contours for p in c]
        ys = [p[1] for c in self.contours for p in c]
        return min(xs), min(ys), max(xs), max(ys)

    def glyph(self) -> Glyph:
        pen = TTGlyphPen(None)
        for contour in self.contours:
            pen.moveTo(contour[0][:2])
            pending: list[tuple[int, int]] = []
            for x, y, on in contour[1:]:
                if not on:
                    pending.append((x, y))
                elif pending:
                    pen.qCurveTo(*pending, (x, y))
                    pending = []
                else:
                    pen.lineTo((x, y))
            if pending:
                pen.qCurveTo(*pending, contour[0][:2])
            pen.closePath()
        return pen.glyph()


@dataclass(frozen=True)
class _Piece:
    """Part of a stroke's centerline: u in [0, 1] -> (point, unit direction); `steps` quadratic segments per
    side, 0 for a straight piece whose sides stay straight lines."""
    at: Callable[[float], tuple[Point, Point]]
    length: float
    steps: int


def new_glyphs(rise: int = 0, ref: Ref = REF) -> list[NewGlyph]:
    """The two added glyphs, exclamdown then questiondown, raised by `rise` font units."""
    return [_exclamdown(ref, rise), _questiondown(ref, rise)]


def _exclamdown(r: Ref, rise: int) -> NewGlyph:
    cx = r.excl_advance / 2
    dot, y0 = _round_dot(cx, r)
    y1 = _foot_y(r)
    near, foot = (k * r.stem / 2 for k in EXCL_TAPER)
    stem = [(cx - near, y0, True), (cx - foot, y1, True), (cx + foot, y1, True), (cx + near, y0, True)]
    return _turn("exclamdown", 0x00A1, r.excl_advance, [dot, stem], r, rise)


def _questiondown(r: Ref, rise: int) -> NewGlyph:
    stem_x = (r.quest_left + r.quest_right) / 2
    dot, stem_end_y = _round_dot(stem_x, r)
    pieces = _hook_centerline(r, (stem_x, stem_end_y))
    full, end = r.quest_stroke_v / 2, r.quest_stem_end_w / 2
    taper_from = sum(p.length for p in pieces[:2])              # the neck curve and the stem narrow together
    taper_len = sum(p.length for p in pieces[2:])

    def half_w(s: float) -> float:
        return full + (end - full) * min(1.0, max(0.0, (s - taper_from) / taper_len))

    hook = _stroke(pieces, half_w, r.quest_stroke_h / 2)
    return _turn("questiondown", 0x00BF, r.quest_advance, [dot, hook], r, rise)


def _round_dot(cx: float, r: Ref) -> tuple[list[tuple[float, float, bool]], float]:
    """Round dot of an upright mark (dipping below the baseline by the round overshoot, so the turned dot rises
    above the x-height like 'o') and the y where the rest of the mark starts above it."""
    d = r.dot * DOT_SCALE
    bottom = r.base - r.overshoot
    return _circle(cx, bottom + d / 2, d / 2), bottom + d + r.dot_gap


def _foot_y(r: Ref) -> float:
    """Upright y that the turn maps onto BO-TBGo's descender line."""
    return r.x_height + r.base - r.descender


def _circle(cx: float, cy: float, radius: float) -> list[tuple[float, float, bool]]:
    """Quadratic circle: on-curve points at the four extremes, two controls per quarter (eight 45-degree arcs
    whose shared ends are the implied midpoints of the controls)."""
    far = radius / math.cos(math.radians(22.5))
    pts = []
    for quarter in range(4):
        a = math.radians(90 * quarter)
        pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a), True))
        for step in (22.5, 67.5):
            b = a + math.radians(step)
            pts.append((cx + far * math.cos(b), cy + far * math.sin(b), False))
    return pts


def _turn(name: str, cp: int, advance: int, contours: list[list[tuple[float, float, bool]]], r: Ref,
          rise: int) -> NewGlyph:
    """Turn an upright mark 180 degrees about the point that maps the baseline onto the x-height, then scale to
    UPM units."""
    k = UPM / r.upm
    pivot_y = r.x_height + r.base
    out = []
    for contour in contours:
        pts = [(round((advance - x) * k), round((pivot_y - y) * k) + rise, on) for x, y, on in contour]
        out.append(_clockwise(_dedupe(pts)))
    return NewGlyph(name, cp, round(advance * k), tuple(out))


def _dedupe(pts: list[tuple[int, int, bool]]) -> list[tuple[int, int, bool]]:
    out = [p for i, p in enumerate(pts) if i == 0 or p[:2] != pts[i - 1][:2]]
    while len(out) > 1 and out[-1][:2] == out[0][:2]:
        out.pop()
    return out


def _clockwise(pts: list[tuple[int, int, bool]]) -> Contour:
    """TrueType outer contours run clockwise; reverse (keeping the first point) if needed."""
    area = sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(pts, pts[1:] + pts[:1]))
    return tuple(pts if area <= 0 else [pts[0]] + pts[:0:-1])


def _hook_centerline(r: Ref, stem_end: Point) -> list[_Piece]:
    """Centerline of an upright '?' hook, terminal to stem end: elliptical bowl (clockwise), straight diagonal,
    reverse curve (counter-clockwise) into a vertical stem at stem_end's x. The bowl keeps the measured size but
    moves down if needed so its top turns onto the descender line."""
    a, b = r.quest_stroke_v / 2, r.quest_stroke_h / 2
    xc = stem_end[0]
    rx, ry = xc - r.quest_left - a, r.quest_top - b - r.quest_bowl_mid
    yc = r.quest_bowl_mid - max(0.0, r.quest_top - _foot_y(r))
    bowl = _ellipse_arc((xc, yc), rx, ry, HOOK_START_DEG, HOOK_TURN_DEG)
    p1, t1 = bowl.at(1.0)
    h1 = math.atan2(t1[1], t1[0]) % math.tau
    run = (xc + NECK_RADIUS * (1 + math.sin(h1)) - p1[0]) / math.cos(h1)
    if run <= 0:
        raise ValueError("hook design constants leave no room for the diagonal")
    q = (p1[0] + run * t1[0], p1[1] + run * t1[1])
    neck = _circle_arc((q[0] - NECK_RADIUS * math.sin(h1), q[1] + NECK_RADIUS * math.cos(h1)), NECK_RADIUS,
                       math.degrees(h1), 270.0)
    neck_end = neck.at(1.0)[0]
    if stem_end[1] >= neck_end[1]:
        raise ValueError("hook design constants leave no room for the stem")
    return [bowl, _line(p1, q), neck, _line(neck_end, stem_end)]


def _ellipse_arc(c: Point, rx: float, ry: float, start_deg: float, end_deg: float) -> _Piece:
    """Clockwise arc (start_deg > end_deg) of an axis-aligned ellipse."""
    a0, a1 = math.radians(start_deg), math.radians(end_deg)

    def at(u: float) -> tuple[Point, Point]:
        phi = a0 + (a1 - a0) * u
        return ((c[0] + rx * math.cos(phi), c[1] + ry * math.sin(phi)),
                _unit((rx * math.sin(phi), -ry * math.cos(phi))))

    return _Piece(at, _length(at), _arc_steps(start_deg - end_deg))


def _circle_arc(c: Point, radius: float, start_heading: float, end_heading: float) -> _Piece:
    """Counter-clockwise arc that turns the direction of travel from start_heading to end_heading (degrees)."""
    h0, h1 = math.radians(start_heading), math.radians(end_heading)

    def at(u: float) -> tuple[Point, Point]:
        h = h0 + (h1 - h0) * u
        return (c[0] + radius * math.sin(h), c[1] - radius * math.cos(h)), (math.cos(h), math.sin(h))

    return _Piece(at, radius * abs(h1 - h0), _arc_steps(end_heading - start_heading))


def _line(p: Point, q: Point) -> _Piece:
    t = _unit((q[0] - p[0], q[1] - p[1]))
    return _Piece(lambda u: ((p[0] + (q[0] - p[0]) * u, p[1] + (q[1] - p[1]) * u), t), math.dist(p, q), 0)


def _arc_steps(sweep_deg: float) -> int:
    return max(1, math.ceil(abs(sweep_deg) / ARC_STEP_DEG))


def _length(at: Callable[[float], tuple[Point, Point]], n: int = 256) -> float:
    pts = [at(i / n)[0] for i in range(n + 1)]
    return sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))


def _stroke(pieces: list[_Piece], half_w: Callable[[float], float], half_h: float
            ) -> list[tuple[float, float, bool]]:
    """Outline of a stroke drawn with an elliptical pen (half-widths half_w(arc length) x half_h), butt ends.

    Each curved segment of a side becomes one quadratic whose control point is where the side's tangents at
    the segment ends meet; tangents are taken numerically, so a changing pen width is followed exactly."""
    left: list[tuple[float, float, bool]] = []
    right: list[tuple[float, float, bool]] = []
    start = 0.0
    for piece in pieces:
        for side, sign in ((left, 1.0), (right, -1.0)):
            side += _side_points(piece, start, half_w, half_h, sign, first=not side)
        start += piece.length
    return left + right[::-1]


def _side_points(piece: _Piece, start: float, half_w: Callable[[float], float], half_h: float, sign: float,
                 first: bool) -> list[tuple[float, float, bool]]:
    """One side of a piece: on-curve points (the first only for the first piece) and quadratic controls.
    Points come in travel order; the right side (sign -1) is reversed by the caller."""
    def offset(u: float) -> Point:
        p, t = piece.at(u)
        return _add(p, _pen_offset((-t[1] * sign, t[0] * sign), half_w(start + u * piece.length), half_h))

    def tangent(u: float, toward: float) -> Point:
        """Direction of increasing u at u, measured on the side of u that faces `toward`."""
        h = 1e-4 if toward > u else -1e-4
        p, q = offset(u), offset(u + h)
        return _unit(((q[0] - p[0]) / h, (q[1] - p[1]) / h))

    out = [(*offset(0.0), True)] if first else []
    n = max(piece.steps, 1)
    for i in range(1, n + 1):
        u0, u1 = (i - 1) / n, i / n
        if piece.steps:
            ctrl = _meet(offset(u0), tangent(u0, u1), offset(u1), tangent(u1, u0))
            if ctrl is not None:
                out.append((*ctrl, False))
        out.append((*offset(u1), True))
    return out


def _pen_offset(n: Point, a: float, b: float) -> Point:
    """Point of the ellipse x²/a² + y²/b² = 1 that lies furthest along the unit direction n."""
    k = math.hypot(a * n[0], b * n[1])
    return a * a * n[0] / k, b * b * n[1] / k


def _meet(p: Point, t: Point, q: Point, s: Point) -> Point | None:
    """Intersection of the lines p + u*t and q + v*s (None if parallel)."""
    den = t[0] * s[1] - t[1] * s[0]
    if abs(den) < 1e-9:
        return None
    u = ((q[0] - p[0]) * s[1] - (q[1] - p[1]) * s[0]) / den
    return p[0] + u * t[0], p[1] + u * t[1]


def _unit(v: Point) -> Point:
    n = math.hypot(*v)
    return v[0] / n, v[1] / n


def _add(p: Point, q: Point) -> Point:
    return p[0] + q[0], p[1] + q[1]


def _glyph_data(g: NewGlyph) -> tuple[bytes, Glyph]:
    """Compiled glyf record (padded to 4 bytes) and the compiled Glyph object."""
    glyph = g.glyph()
    data = glyph.compile(None)
    return data + b"\0" * (-len(data) % 4), glyph


# ------------------------------------------------------------------------------------------ sfnt read and write

def load_font(sfnt: bytes) -> TTFont:
    return TTFont(io.BytesIO(sfnt), lazy=True)


def read_tables(sfnt: bytes) -> tuple[bytes, dict[str, bytes], list[str]]:
    """(sfnt version, raw tables by tag, tags in file order)."""
    num = struct.unpack_from(">H", sfnt, 4)[0]
    entries = [struct.unpack_from(">4sIII", sfnt, 12 + 16 * i) for i in range(num)]
    tables = {tag.decode("latin-1"): sfnt[off:off + length] for tag, _, off, length in entries}
    order = [tag.decode("latin-1") for tag, _, off, _ in sorted(entries, key=lambda e: e[2])]
    return sfnt[:4], tables, order


def table_checksum(data: bytes) -> int:
    padded = data + b"\0" * (-len(data) % 4)
    return sum(struct.unpack(f">{len(padded) // 4}I", padded)) & 0xFFFFFFFF


def write_sfnt(version: bytes, tables: dict[str, bytes], order: list[str]) -> bytes:
    """Assemble an sfnt: directory sorted by tag, data in `order`, 4-byte padding, fresh checksums."""
    head = bytearray(tables["head"])
    struct.pack_into(">I", head, 8, 0)
    tables = {**tables, "head": bytes(head)}
    num = len(tables)
    selector = int(math.log2(num))
    out = bytearray(struct.pack(">4sHHHH", version, num, 16 << selector, selector, 16 * num - (16 << selector)))
    offset = 12 + 16 * num
    offsets = {}
    for tag in order:
        offsets[tag] = offset
        offset += len(tables[tag]) + (-len(tables[tag]) % 4)
    for tag in sorted(tables):
        out += struct.pack(">4sIII", tag.encode("latin-1"), table_checksum(tables[tag]), offsets[tag],
                           len(tables[tag]))
    for tag in order:
        out += tables[tag] + b"\0" * (-len(tables[tag]) % 4)
    struct.pack_into(">I", out, offsets["head"] + 8, (0xB1B0AFBA - table_checksum(bytes(out))) & 0xFFFFFFFF)
    return bytes(out)


# ------------------------------------------------------------------------------------------ RR_System_Font patch

def patched_rr_system(ps: PakSet, rise: int = 0) -> bytes:
    """RR_System_Font.oft.1 with the two added glyphs (FBFO-wrapped, ready for the pak)."""
    return oft.encode(patch_rr_sfnt(oft.decode(ps.read(RR_PATH)), new_glyphs(rise)))


def patch_rr_sfnt(sfnt: bytes, glyphs: list[NewGlyph]) -> bytes:
    """Append `glyphs` to a TrueType font, keeping every existing glyph, metric and untouched table as is."""
    version, tables, order = read_tables(sfnt)
    font = load_font(sfnt)
    old_order = font.getGlyphOrder()
    if any(g.name in old_order for g in glyphs):
        raise ValueError("font already has the glyphs to add")
    compiled = [_glyph_data(g) for g in glyphs]
    n_old = len(old_order)
    tables["glyf"], tables["loca"] = _append_glyf(tables, n_old, [data for data, _ in compiled])
    tables["hmtx"], tables["hhea"] = _append_hmtx(tables, n_old, glyphs)
    tables["maxp"] = _patch_maxp(tables["maxp"], n_old + len(glyphs), [glyph for _, glyph in compiled])
    tables["head"] = _patch_head(tables["head"], glyphs)
    tables["post"] = _append_post(tables["post"], n_old, glyphs)
    font.setGlyphOrder(old_order + [g.name for g in glyphs])
    tables["cmap"] = _patch_cmap(font, glyphs)
    tables["name"] = _patch_name(font)
    return write_sfnt(version, tables, order)


def _append_glyf(tables: dict[str, bytes], n_old: int, blobs: list[bytes]) -> tuple[bytes, bytes]:
    """Existing glyph records stay byte-identical; new records go after them."""
    short = struct.unpack_from(">h", tables["head"], 50)[0] == 0
    code, size, mult = ("H", 2, 2) if short else ("I", 4, 1)
    loca = [v * mult for v in struct.unpack(f">{n_old + 1}{code}", tables["loca"][:(n_old + 1) * size])]
    glyf = bytearray(tables["glyf"][:loca[-1]])
    for blob in blobs:
        glyf += blob
        loca.append(len(glyf))
    if short and loca[-1] // 2 > 0xFFFF:
        raise ValueError("glyf too large for short loca offsets")
    return bytes(glyf), struct.pack(f">{len(loca)}{code}", *(v // mult for v in loca))


def _append_hmtx(tables: dict[str, bytes], n_old: int, glyphs: list[NewGlyph]) -> tuple[bytes, bytes]:
    hhea = bytearray(tables["hhea"])
    n_long = struct.unpack_from(">H", hhea, 34)[0]
    raw = tables["hmtx"]
    metrics = [struct.unpack_from(">Hh", raw, 4 * i) for i in range(n_long)]
    metrics += [(metrics[-1][0], struct.unpack_from(">h", raw, 4 * n_long + 2 * i)[0])
                for i in range(n_old - n_long)]
    adv_max, lsb_min, rsb_min, ext_max = struct.unpack_from(">Hhhh", hhea, 10)
    for g in glyphs:
        x0, _, x1, _ = g.bounds()
        metrics.append((g.advance, x0))
        adv_max, lsb_min = max(adv_max, g.advance), min(lsb_min, x0)
        rsb_min, ext_max = min(rsb_min, g.advance - x1), max(ext_max, x1)
    n_long = len(metrics)
    while n_long > 1 and metrics[n_long - 1][0] == metrics[n_long - 2][0]:
        n_long -= 1
    out = b"".join(struct.pack(">Hh", *m) for m in metrics[:n_long])
    out += b"".join(struct.pack(">h", lsb) for _, lsb in metrics[n_long:])
    struct.pack_into(">Hhhh", hhea, 10, adv_max, lsb_min, rsb_min, ext_max)
    struct.pack_into(">H", hhea, 34, n_long)
    return out, bytes(hhea)


def _patch_maxp(raw: bytes, num_glyphs: int, glyphs: list[Glyph]) -> bytes:
    out = bytearray(raw)
    points, contours = struct.unpack_from(">HH", out, 6)
    points = max([points] + [len(g.coordinates) for g in glyphs])
    contours = max([contours] + [g.numberOfContours for g in glyphs])
    struct.pack_into(">HHH", out, 4, num_glyphs, points, contours)
    return bytes(out)


def _patch_head(raw: bytes, glyphs: list[NewGlyph]) -> bytes:
    out = bytearray(raw)
    x0, y0, x1, y1 = struct.unpack_from(">hhhh", out, 36)
    for g in glyphs:
        gx0, gy0, gx1, gy1 = g.bounds()
        x0, y0, x1, y1 = min(x0, gx0), min(y0, gy0), max(x1, gx1), max(y1, gy1)
    struct.pack_into(">hhhh", out, 36, x0, y0, x1, y1)
    return bytes(out)


def _append_post(raw: bytes, n_old: int, glyphs: list[NewGlyph]) -> bytes:
    if struct.unpack_from(">I", raw, 0)[0] != 0x00020000:
        raise ValueError("expected a post table version 2.0")
    indices = list(struct.unpack_from(f">{n_old}H", raw, 34))
    for g in glyphs:
        indices.append(standardGlyphOrder.index(g.name))
    names = raw[34 + 2 * n_old:]
    return raw[:32] + struct.pack(f">H{len(indices)}H", len(indices), *indices) + names


def _patch_cmap(font: TTFont, glyphs: list[NewGlyph]) -> bytes:
    cmap = font["cmap"]
    for sub in cmap.tables:
        if sub.isUnicode():
            sub.cmap.update({g.codepoint: g.name for g in glyphs})
    return cmap.compile(font)


def _patch_name(font: TTFont) -> bytes:
    name = font["name"]
    name.setName(RR_DESCRIPTION, 10, 1, 0, 0)
    name.setName(RR_DESCRIPTION, 10, 3, 1, 0x409)
    return name.compile(font)


# -------------------------------------------------------------------------------------------- supplement font

def supplement_font(rise: int = 0) -> bytes:
    """SFES_Supplement.oft.1: .notdef plus the two added glyphs (FBFO-wrapped)."""
    return oft.encode(supplement_sfnt(rise))


def supplement_vmetrics(glyphs: list[NewGlyph]) -> dict[str, dict[str, int]]:
    """RR_System_Font's vertical metrics with the declared descent lowered to the deepest glyph point. The ascent
    is RR_System's, so an ascent-based baseline places the glyphs as in rrsystem; the descent keeps a renderer
    that clips at the declared descent from cutting off the parts below the baseline."""
    depth = min([0] + [g.bounds()[1] for g in glyphs])
    os2 = RR_VMETRICS["OS/2"]
    return {"hhea": {**RR_VMETRICS["hhea"], "descent": depth},
            "OS/2": {**os2, "sTypoDescender": min(os2["sTypoDescender"], depth), "usWinDescent": -depth}}


def supplement_sfnt(rise: int = 0) -> bytes:
    """The supplement font as TrueType (vertical metrics: supplement_vmetrics())."""
    glyphs = new_glyphs(rise)
    vmetrics = supplement_vmetrics(glyphs)
    fb = FontBuilder(UPM, isTTF=True)
    fb.updateHead(created=SUPPLEMENT_TIMESTAMP, modified=SUPPLEMENT_TIMESTAMP, fontRevision=1.0)
    fb.setupGlyphOrder([".notdef"] + [g.name for g in glyphs])
    fb.setupCharacterMap({g.codepoint: g.name for g in glyphs})
    fb.setupGlyf({".notdef": TTGlyphPen(None).glyph(), **{g.name: g.glyph() for g in glyphs}})
    fb.setupHorizontalMetrics({".notdef": (NOTDEF_ADVANCE, 0), **{g.name: (g.advance, g.bounds()[0])
                                                                   for g in glyphs}})
    fb.setupHorizontalHeader(**vmetrics["hhea"])
    fb.setupNameTable(SUPPLEMENT_NAMES, mac=False)
    fb.setupOS2(version=4, usWeightClass=400, fsType=0, fsSelection=0xC0, achVendID="SFES", **vmetrics["OS/2"],
                ulUnicodeRange1=1 << 1, ulCodePageRange1=1, usFirstCharIndex=min(g.codepoint for g in glyphs),
                usLastCharIndex=max(g.codepoint for g in glyphs), usDefaultChar=0, usBreakChar=32,
                usMaxContext=0, sxHeight=0, sCapHeight=0)
    fb.setupPost()
    gasp = newTable("gasp")
    gasp.gaspRange = {0xFFFF: 0x0002}
    fb.font["gasp"] = gasp
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------ fslt patch

def patch_fslt(raw: bytes, insert_supplement: bool = False, extend_range: bool = False) -> bytes:
    """fontslot_en with the supplement font after RR_System_Font in slots 0 and 1 and/or the slot 0/1 range rule
    ':'..'~' widened to ':'..U+00FF. With no option the result is byte-identical to `raw`."""
    model = fslt.parse(raw)
    for i in DIALOGUE_SLOTS:
        slot = model["slots"][i]
        if insert_supplement:
            _insert_supplement(slot)
        if extend_range:
            _extend_range(slot)
    return fslt.encode(model)


def _insert_supplement(slot: dict) -> None:
    paths = [f["path"] for f in slot["fonts"]]
    if SUPPLEMENT_FSLT_PATH in paths or paths.count(RR_FSLT_PATH) != 1:
        raise ValueError(f"unexpected slot font list: {paths}")
    i = paths.index(RR_FSLT_PATH)
    slot["fonts"].insert(i + 1, {"adjust": list(slot["fonts"][i]["adjust"]), "path": SUPPLEMENT_FSLT_PATH})


def _extend_range(slot: dict) -> None:
    rules = [r for r in slot["rules"] if r["params"][0] == 1 and r["texts"] == [RANGE_FROM, RANGE_TO, ""]]
    if len(rules) != 1:
        raise ValueError(f"expected one ':'..'~' range rule, found {len(rules)}")
    rules[0]["texts"][1] = RANGE_EXTENDED_TO


# ----------------------------------------------------------------------------------------------- outputs, report

def outputs(variant: str, ps: PakSet | None = None, rise: int = 0) -> dict[str, bytes]:
    """pak path -> file bytes for one variant, sorted by path."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; choose from {list(VARIANTS)}")
    if ps is None:
        ps = PakSet()
    spec = VARIANTS[variant]
    files = {SUPPLEMENT_PATH: supplement_font(rise)} if spec.supplement else {RR_PATH: patched_rr_system(ps, rise)}
    if spec.supplement or spec.extend_range:
        files[fslt.FSLT_EN] = patch_fslt(ps.read(fslt.FSLT_EN), spec.supplement, spec.extend_range)
    return dict(sorted(files.items()))


def verify_font(raw: bytes) -> dict[str, bool]:
    """FBFO round trip, full fontTools decompile, and table and whole-file checksums of an .oft.1 file."""
    sfnt = oft.decode(raw)
    _, tables, _ = read_tables(sfnt)
    num = struct.unpack_from(">H", sfnt, 4)[0]
    stored = {sfnt[12 + 16 * i:16 + 16 * i].decode("latin-1"): struct.unpack_from(">I", sfnt, 16 + 16 * i)[0]
              for i in range(num)}
    head = bytearray(tables["head"])
    struct.pack_into(">I", head, 8, 0)
    sums = {tag: table_checksum(bytes(head) if tag == "head" else data) for tag, data in tables.items()}
    return {"fbfo_round_trip": oft.encode(sfnt) == raw,
            "fonttools_decompile": _decompiles(sfnt),
            "table_checksums": sums == stored,
            "file_checksum": table_checksum(sfnt) == 0xB1B0AFBA}


def _decompiles(sfnt: bytes) -> bool:
    """True if fontTools decompiles every table and expands every glyph."""
    try:
        font = TTFont(io.BytesIO(sfnt))
        font.ensureDecompiled()
        if "glyf" in font:
            for name in font.getGlyphOrder():
                font["glyf"][name].expand(font["glyf"])
    except Exception:          # any parser error means the font is not usable
        return False
    return True


def report(variant: str, files: dict[str, bytes], ps: PakSet, rise: int) -> dict:
    """JSON-ready description of one variant's files and glyphs."""
    entries = []
    for path, data in files.items():
        old = ps.entry(path)
        entry = {"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                 "replaces": {"pak": old.pak.name, "sha256": hashlib.sha256(ps.read(old)).hexdigest()} if old else None,
                 "pak_entry_template": path if old else RR_PATH}
        if path.endswith(".oft.1"):
            entry["checks"] = verify_font(data)
            entry["declared_vmetrics"] = _declared_vmetrics(data)
        else:
            entry["fslt"] = _fslt_summary(data)
        entries.append(entry)
    return {"variant": variant, "summary": VARIANTS[variant].summary, "rise_units": rise, "files": entries,
            "glyphs": _glyph_report(new_glyphs(rise)), "reference_metrics": {"font": BO_PATH, **asdict(REF)},
            "vertical_model": VERTICAL_MODEL}


def _declared_vmetrics(raw: bytes) -> dict[str, int]:
    """The vertical metrics a renderer may use for the baseline or for clipping, and the real glyph extent."""
    font = load_font(oft.decode(raw))
    hhea, os2, head = font["hhea"], font["OS/2"], font["head"]
    return {"hhea_ascent": hhea.ascent, "hhea_descent": hhea.descent, "win_ascent": os2.usWinAscent,
            "win_descent": os2.usWinDescent, "head_y_min": head.yMin, "head_y_max": head.yMax}


def _glyph_report(glyphs: list[NewGlyph]) -> dict:
    out = {}
    for g in glyphs:
        x0, y0, x1, y1 = g.bounds()
        out[f"U+{g.codepoint:04X}"] = {
            "char": chr(g.codepoint), "glyph": g.name, "advance": g.advance, "advance_em": round(g.advance / UPM, 5),
            "bbox": [x0, y0, x1, y1], "contours": len(g.contours),
            "px_at_60": {"advance": round(g.advance * 60 / UPM, 2), "top": round(y1 * 60 / UPM, 2),
                         "bottom": round(y0 * 60 / UPM, 2)}}
    return out


def _fslt_summary(raw: bytes) -> dict:
    model = fslt.parse(raw)
    out = {}
    for i in DIALOGUE_SLOTS:
        slot = model["slots"][i]
        out[f"slot{i}_fonts"] = [f["path"] for f in slot["fonts"]]
        out[f"slot{i}_ranges"] = [r["texts"][:2] for r in slot["rules"] if r["params"][0] == 1]
    return out


def write_variant(root: Path, files: dict[str, bytes], info: dict) -> list[Path]:
    """Write the natives tree and report.json under root (refuses the game folder and original/)."""
    _check_writable(root)
    written = []
    for path, data in files.items():
        dest = root / Path(*path.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        written.append(dest)
    rep = root / "report.json"
    rep.write_text(json.dumps(info, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return written + [rep]


_DEVICE_PREFIXES = (("\\\\?\\UNC\\", "\\\\"), ("\\\\.\\UNC\\", "\\\\"), ("\\\\?\\", ""), ("\\\\.\\", ""),
                    ("\\??\\", ""))


def _check_writable(path: Path) -> None:
    """Refuse paths in or under the game folder or original/, however they are spelled."""
    target = _canonical(path)
    for protected in (config.GAME_DIR, config.ORIGINAL):
        root = _canonical(protected)
        if target == root or target.startswith(root.rstrip("\\") + "\\") or _under_by_identity(path, protected):
            raise PermissionError(f"refusing to write under {protected}")


def _canonical(path: Path) -> str:
    """Absolute, case-folded Windows path with links and short names resolved and any device prefix
    (\\\\?\\, \\\\.\\, \\??\\) removed."""
    text = os.fspath(path).replace("/", "\\")
    for prefix, replacement in _DEVICE_PREFIXES:
        if text.upper().startswith(prefix):
            text = replacement + text[len(prefix):]
            break
    return os.path.normcase(os.path.realpath(text))


def _under_by_identity(path: Path, protected: Path) -> bool:
    """True if path or one of its existing parent folders is the protected folder itself (same volume and file
    id), which also catches spellings that string checks miss (substituted drives, volume GUID paths)."""
    try:
        wanted = os.stat(protected)
    except OSError:
        return False
    folder = Path(os.path.abspath(path))
    for candidate in (folder, *folder.parents):
        try:
            if os.path.samestat(os.stat(candidate), wanted):
                return True
        except OSError:
            continue
    return False


# ------------------------------------------------------------------------------------------------------ previews

class _SegmentPen(BasePen):
    """Flattens outlines into closed polylines after mapping points with `transform`."""

    def __init__(self, glyph_set, transform: Callable[[Point], Point], tolerance: float):
        super().__init__(glyph_set)
        self.transform, self.tolerance = transform, tolerance
        self.contours: list[list[Point]] = []

    def _moveTo(self, pt):
        self.contours.append([self.transform(pt)])

    def _lineTo(self, pt):
        self.contours[-1].append(self.transform(pt))

    def _curveToOne(self, p1, p2, p3):
        self._bezier([self.contours[-1][-1]] + [self.transform(p) for p in (p1, p2, p3)])

    def _qCurveToOne(self, p1, p2):
        self._bezier([self.contours[-1][-1]] + [self.transform(p) for p in (p1, p2)])

    def _bezier(self, ctrl: list[Point]) -> None:
        length = sum(math.dist(p, q) for p, q in zip(ctrl, ctrl[1:]))
        n = max(2, min(64, math.ceil(length / self.tolerance)))
        for i in range(1, n + 1):
            pts = ctrl
            t = i / n
            while len(pts) > 1:
                pts = [(p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t) for p, q in zip(pts, pts[1:])]
            self.contours[-1].append(pts[0])

    def _closePath(self):
        if len(self.contours[-1]) > 1 and self.contours[-1][-1] == self.contours[-1][0]:
            self.contours[-1].pop()

    _endPath = _closePath


def _flatten(glyph_set, name: str) -> list[list[Point]]:
    pen = _SegmentPen(glyph_set, lambda p: (float(p[0]), float(p[1])), tolerance=4.0)
    glyph_set[name].draw(pen)
    return pen.contours


class _Canvas:
    """Grayscale canvas with exact-area anti-aliasing (signed coverage accumulation, nonzero per glyph)."""

    def __init__(self, width: int, height: int):
        self.w, self.h = width, height
        self.acc = [0.0] * (width * height + 2)
        self.guides: dict[int, float] = {}

    def fill(self, contours: list[list[Point]]) -> None:
        segments = [(p, q) for c in contours for p, q in zip(c, c[1:] + c[:1])]
        area = sum(p[0] * q[1] - q[0] * p[1] for p, q in segments)
        sign = 1.0 if area >= 0 else -1.0
        for p, q in segments:
            self._line(self._clamp(p), self._clamp(q), sign)

    def guide(self, y: float, level: float) -> None:
        row = int(y)
        if 0 <= row < self.h:
            self.guides[row] = max(level, self.guides.get(row, 0.0))

    def _clamp(self, p: Point) -> Point:
        return min(max(p[0], 0.0), self.w - 1.001), min(max(p[1], 0.0), float(self.h))

    def _line(self, p0: Point, p1: Point, sign: float) -> None:
        if p0[1] == p1[1]:
            return
        direction = sign if p0[1] < p1[1] else -sign
        if p0[1] > p1[1]:
            p0, p1 = p1, p0
        dxdy = (p1[0] - p0[0]) / (p1[1] - p0[1])
        x = p0[0]
        for y in range(int(p0[1]), min(self.h, math.ceil(p1[1]))):
            dy = min(y + 1.0, p1[1]) - max(float(y), p0[1])
            x_next = x + dxdy * dy
            self._cover_row(y * self.w, x, x_next, dy * direction)
            x = x_next

    def _cover_row(self, row: int, xa: float, xb: float, d: float) -> None:
        acc = self.acc
        x0, x1 = min(xa, xb), max(xa, xb)
        x0f = math.floor(x0)
        i0, i1 = int(x0f), int(math.ceil(x1))
        if i1 <= i0 + 1:
            mid = 0.5 * (xa + xb) - x0f
            acc[row + i0] += d - d * mid
            acc[row + i0 + 1] += d * mid
            return
        s = 1.0 / (x1 - x0)
        frac0 = x0 - x0f
        a0 = 0.5 * s * (1.0 - frac0) ** 2
        frac1 = x1 - i1 + 1.0
        am = 0.5 * s * frac1 * frac1
        acc[row + i0] += d * a0
        if i1 == i0 + 2:
            acc[row + i0 + 1] += d * (1.0 - a0 - am)
        else:
            a1 = s * (1.5 - frac0)
            acc[row + i0 + 1] += d * (a1 - a0)
            for i in range(i0 + 2, i1 - 1):
                acc[row + i] += d * s
            a2 = a1 + (i1 - i0 - 3) * s
            acc[row + i1 - 1] += d * (1.0 - a2 - am)
        acc[row + i1] += d * am

    def coverage(self) -> list[float]:
        """Ink coverage 0..1 per pixel, row by row."""
        return [min(1.0, abs(v)) for v in itertools.accumulate(self.acc[:self.w * self.h])]

    def png(self) -> bytes:
        gray = bytearray(255 - int(v * 255 + 0.5) for v in self.coverage())
        for row, level in self.guides.items():
            line = gray[row * self.w:(row + 1) * self.w]
            gray[row * self.w:(row + 1) * self.w] = bytes(min(v, 255 - int(level * 255)) for v in line)
        raw = b"".join(b"\0" + gray[y * self.w:(y + 1) * self.w] for y in range(self.h))

        def chunk(tag: bytes, body: bytes) -> bytes:
            return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

        header = struct.pack(">IIBBBBB", self.w, self.h, 8, 0, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9))
                + chunk(b"IEND", b""))


@dataclass(frozen=True)
class _Loaded:
    font: TTFont
    cmap: dict[int, str]
    glyph_set: object


class _Fonts:
    """fslt font path -> loaded font, from the paks unless replaced by a patched sfnt; loaded on first use."""

    def __init__(self, ps: PakSet, replaced: dict[str, bytes]):
        self.ps, self.replaced = ps, replaced
        self.cache: dict[str, _Loaded] = {}

    def get(self, fslt_path: str) -> _Loaded:
        if fslt_path not in self.cache:
            pak_path = fslt.font_pak_path(fslt_path)
            font = load_font(self.replaced.get(pak_path) or oft.decode(self.ps.read(pak_path)))
            self.cache[fslt_path] = _Loaded(font, font.getBestCmap(), font.getGlyphSet())
        return self.cache[fslt_path]

    def resolve(self, slot: dict, cp: int) -> tuple[dict, _Loaded] | None:
        """First font of the slot's chain that maps cp, with its fslt entry."""
        for entry in slot["fonts"]:
            loaded = self.get(entry["path"])
            if cp in loaded.cmap:
                return entry, loaded
        return None


@dataclass(frozen=True)
class _Row:
    label: str
    fonts: _Fonts
    slot: dict
    rules: bool         # apply the slot's range-rule y adjust


def _draw_text(canvas: _Canvas, fonts: _Fonts, slot: dict, text: str, x: float, baseline: float, size: float,
               rules: bool) -> None:
    """Lay out text through a slot's font chain (fslt font adjust, optional range-rule y adjust)."""
    ranges = [(ord(r["texts"][0]), ord(r["texts"][1]), r["adjust"][1]) for r in slot["rules"]
              if rules and r["params"][0] == 1]
    for ch in text:
        hit = fonts.resolve(slot, ord(ch))
        if hit is None:
            x += size / 2
            continue
        entry, loaded = hit
        name = loaded.cmap[ord(ch)]
        ox, oy, xs, ys = entry["adjust"]
        sx = size / loaded.font["head"].unitsPerEm * xs
        sy = size / loaded.font["head"].unitsPerEm * ys
        x0, y0 = x + ox, baseline + oy + sum(adj for lo, hi, adj in ranges if lo <= ord(ch) <= hi)
        pen = _SegmentPen(loaded.glyph_set, lambda p: (x0 + p[0] * sx, y0 - p[1] * sy), 1.0)
        loaded.glyph_set[name].draw(pen)                 # drawn now, so the lambda sees this glyph's values
        canvas.fill(pen.contours)
        x += loaded.font["hmtx"][name][0] * sx


def _sheet(rows: list[_Row], text: str, size: float, width: int) -> bytes:
    """One labelled line of `text` per row, with faint baseline, x-height and descender guides."""
    label_size = 22.0
    row_h = label_size * 1.7 + size * 1.45
    canvas = _Canvas(width, int(row_h * len(rows) + label_size))
    for k, row in enumerate(rows):
        top = k * row_h + label_size / 2
        _draw_text(canvas, rows[0].fonts, rows[0].slot, row.label, 12, top + label_size, label_size, False)
        baseline = top + label_size * 1.7 + size * 0.95
        canvas.guide(baseline, 0.18)
        for y in (REF.x_height, REF.descender):
            canvas.guide(baseline - y * size / REF.upm, 0.12)
        _draw_text(canvas, row.fonts, row.slot, text, 12, baseline, size, row.rules)
    return canvas.png()


def render_previews(ps: PakSet, out_dir: Path, rise: int = 0) -> list[Path]:
    """Before/after PNGs of the slot 0 chain at 60 px (fslt rules ignored and modeled), the same with RR_System
    digits, and a close-up."""
    _check_writable(out_dir)
    raw = ps.read(fslt.FSLT_EN)
    slot0 = {(supplement, extend): fslt.parse(patch_fslt(raw, supplement, extend))["slots"][0]
             for supplement in (False, True) for extend in (False, True)}
    original = slot0[False, False]
    plain = _Fonts(ps, {})
    rr = _Fonts(ps, {RR_PATH: oft.decode(patched_rr_system(ps, rise))})
    sup = _Fonts(ps, {SUPPLEMENT_PATH: supplement_sfnt(rise)})
    sheets = {
        "slot0_60px.png": ([
            _Row("before: original slot 0 chain", plain, original, False),
            _Row("rrsystem: RR_System_Font + 2 glyphs", rr, original, False),
            _Row("supplement: SFES_Supplement after RR_System_Font", sup, slot0[True, False], False)], SAMPLE, 60),
        "slot0_60px_rules.png": ([
            _Row("before, fslt range rules modeled (y -4 px for ' '..'/' and ':'..'~')", plain, original, True),
            _Row("rrsystem, rules modeled", rr, original, True),
            _Row("rrsystem_range: rule widened to ':'..U+00FF, modeled", rr, slot0[False, True], True),
            _Row("supplement_range: same rule, modeled", sup, slot0[True, True], True)], SAMPLE, 60),
        "digits_60px.png": ([
            _Row("before, rules off (RR_System digits, BO-TBGo letters)", plain, original, False),
            _Row("rrsystem, rules off (shared baseline)", rr, original, False),
            _Row("rrsystem, rules modeled (-4 px on ASCII except digits)", rr, original, True),
            _Row("rrsystem_range, rules modeled", rr, slot0[False, True], True)], DIGIT_SAMPLE, 60),
        "closeup_200px.png": ([
            _Row("before", plain, original, False),
            _Row("after (rrsystem)", rr, original, False)], "¡i! ¿? ¡¿Hxg", 200),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (rows, text, size) in sheets.items():
        path = out_dir / name
        path.write_bytes(_sheet(rows, text, size, 1700 if size < 100 else 1300))
        written.append(path)
    return written


# ------------------------------------------------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fontpatch", description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--variant", choices=[*VARIANTS, "all"], default="all")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="output root (default build/fonts)")
    ap.add_argument("--rise", type=int, default=0, help="lift the new glyphs by this many font units (1024/em)")
    ap.add_argument("--preview", action="store_true", help="also render PNG previews")
    ap.add_argument("--preview-dir", type=Path, default=PREVIEW_DIR)
    args = ap.parse_args(argv)
    ps = PakSet()
    ok = True
    for variant in VARIANTS if args.variant == "all" else (args.variant,):
        files = outputs(variant, ps, args.rise)
        info = report(variant, files, ps, args.rise)
        written = write_variant(args.out / variant, files, info)
        checks = [c for f in info["files"] for c in f.get("checks", {}).values()]
        ok &= all(checks)
        print(f"{variant}: {len(files)} file(s), checks {'OK' if all(checks) else 'FAILED'}")
        for path in written:
            print("  ", path)
    if args.preview:
        for path in render_previews(ps, args.preview_dir, args.rise):
            print("preview:", path)
    return 0 if ok else 1
