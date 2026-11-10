r"""Tests for sfes.fontpatch: patched RR_System_Font, supplement font, fslt edits, outputs, CLI and previews.

Game data is read only through PakSet; files are written only to a temporary folder under build\fonts.
Run: C:\StarForceES\.venv\Scripts\python.exe -I -X utf8 C:\StarForceES\tools\run.py tests.test_fontpatch
"""
from __future__ import annotations

import ctypes
import io
import json
import math
import os
import struct
import tempfile
import time
import traceback
from functools import cached_property
from pathlib import Path
from typing import Callable

from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

from sfes import config, fslt, oft
from sfes import fontpatch as fp
from sfes.pak import PakSet

NEW_NAMES = ["exclamdown", "questiondown"]
NEW_CPS = {0x00A1: "exclamdown", 0x00BF: "questiondown"}
UPRIGHT = {"exclamdown": "!", "questiondown": "?"}
COMMERCIAL_PREFIXES = ("natives/stm/gui/ui_font/bo-", "natives/stm/gui/ui_font/dfp", "natives/stm/gui/ui_font/fz")
MAX_IOU = 0.93              # overlap with BO-TBGo's upright mark turned 180 degrees (a copied outline scores ~0.99)
MIN_POINT_DISTANCE = 2.0    # 1024-unit distance from every new outline point to every point of that turned mark


class Ctx:
    """Shared test data, built on first use."""

    @cached_property
    def ps(self) -> PakSet:
        return PakSet()

    @cached_property
    def rr_sfnt(self) -> bytes:
        return oft.decode(self.ps.read(fp.RR_PATH))

    @cached_property
    def patched_raw(self) -> bytes:
        return fp.patched_rr_system(self.ps)

    @cached_property
    def patched_sfnt(self) -> bytes:
        return oft.decode(self.patched_raw)

    @cached_property
    def old(self) -> TTFont:
        return TTFont(io.BytesIO(self.rr_sfnt))

    @cached_property
    def new(self) -> TTFont:
        return TTFont(io.BytesIO(self.patched_sfnt))

    @cached_property
    def supplement_raw(self) -> bytes:
        return fp.supplement_font()

    @cached_property
    def supplement(self) -> TTFont:
        return TTFont(io.BytesIO(oft.decode(self.supplement_raw)))

    @cached_property
    def fslt_raw(self) -> bytes:
        return self.ps.read(fslt.FSLT_EN)

    @cached_property
    def bo(self) -> TTFont:
        return fp.load_font(oft.decode(self.ps.read(fp.BO_PATH)))


CHECKS: list[Callable[[Ctx], None]] = []


def check(fn: Callable[[Ctx], None]) -> Callable[[Ctx], None]:
    CHECKS.append(fn)
    return fn


def _glyph_records(sfnt: bytes) -> list[bytes]:
    """Raw glyf record of every glyph, located through loca."""
    _, tables, _ = fp.read_tables(sfnt)
    n = struct.unpack_from(">H", tables["maxp"], 4)[0]
    short = struct.unpack_from(">h", tables["head"], 50)[0] == 0
    code, size, mult = ("H", 2, 2) if short else ("I", 4, 1)
    loca = [v * mult for v in struct.unpack(f">{n + 1}{code}", tables["loca"][:(n + 1) * size])]
    return [tables["glyf"][a:b] for a, b in zip(loca, loca[1:])]


def _name_records(font: TTFont) -> set[tuple[int, int, int, int, str]]:
    return {(r.platformID, r.platEncID, r.langID, r.nameID, r.toUnicode()) for r in font["name"].names}


def _outline(font: TTFont, name: str) -> tuple:
    coords, ends, flags = font["glyf"][name].getCoordinates(font["glyf"])
    return tuple(coords), tuple(ends), tuple(flags)


def _same_bytes_except(a: bytes, b: bytes, spans: list[tuple[int, int]]) -> bool:
    """True if a and b have the same length and differ only inside the given [start, end) byte spans."""
    if len(a) != len(b):
        return False
    return all(x == y or any(s <= i < e for s, e in spans) for i, (x, y) in enumerate(zip(a, b)))


# ---------------------------------------------------------------------------------------------- reference, sfnt

@check
def reference_metrics_match_game_font(ctx: Ctx) -> None:
    assert fp.measure_reference(ctx.ps) == fp.REF, fp.measure_reference(ctx.ps)


@check
def sfnt_writer_rebuilds_original_byte_exact(ctx: Ctx) -> None:
    assert fp.write_sfnt(*fp.read_tables(ctx.rr_sfnt)) == ctx.rr_sfnt


# ----------------------------------------------------------------------------------------- patched RR font

@check
def patched_font_decompiles_and_checksums(ctx: Ctx) -> None:
    result = fp.verify_font(ctx.patched_raw)
    assert all(result.values()), result


@check
def fbfo_round_trip(ctx: Ctx) -> None:
    for raw in (ctx.patched_raw, ctx.supplement_raw):
        sfnt = oft.decode(raw)
        assert sfnt[:4] == b"\x00\x01\x00\x00", sfnt[:4]
        assert oft.encode(sfnt) == raw


@check
def glyph_count_plus_two(ctx: Ctx) -> None:
    assert ctx.new["maxp"].numGlyphs == ctx.old["maxp"].numGlyphs + 2
    assert ctx.new.getGlyphOrder() == ctx.old.getGlyphOrder() + NEW_NAMES


@check
def old_glyphs_identical(ctx: Ctx) -> None:
    n = len(ctx.old.getGlyphOrder())
    assert _glyph_records(ctx.patched_sfnt)[:n] == _glyph_records(ctx.rr_sfnt)
    for name in ctx.old.getGlyphOrder():
        assert _outline(ctx.new, name) == _outline(ctx.old, name), name
        assert ctx.new["hmtx"][name] == ctx.old["hmtx"][name], name


@check
def old_mappings_identical(ctx: Ctx) -> None:
    new_subs = {(s.platformID, s.platEncID): s for s in ctx.new["cmap"].tables}
    assert len(new_subs) == len(ctx.old["cmap"].tables)
    for sub in ctx.old["cmap"].tables:
        new = new_subs[(sub.platformID, sub.platEncID)].cmap
        added = {cp: g for cp, g in new.items() if cp not in sub.cmap}
        assert {cp: new[cp] for cp in sub.cmap} == sub.cmap, (sub.platformID, sub.platEncID)
        assert added == (NEW_CPS if sub.isUnicode() else {}), added


@check
def new_glyphs_mapped_and_descend(ctx: Ctx) -> None:
    cmap = ctx.new.getBestCmap()
    k = fp.UPM / fp.REF.upm
    advances = {"exclamdown": round(fp.REF.excl_advance * k), "questiondown": round(fp.REF.quest_advance * k)}
    descender = round(fp.REF.descender * k)
    for cp, name in NEW_CPS.items():
        assert cmap[cp] == name, (hex(cp), cmap.get(cp))
        glyph = ctx.new["glyf"][name]
        glyph.recalcBounds(ctx.new["glyf"])
        advance, lsb = ctx.new["hmtx"][name]
        assert descender - 1 <= glyph.yMin <= descender, f"{name} must end on the descender line ({glyph.yMin})"
        assert glyph.yMax == round((fp.REF.x_height + fp.REF.overshoot) * k), f"{name} top {glyph.yMax}"
        assert advance == advances[name] and lsb == glyph.xMin and 0 < glyph.xMin and glyph.xMax < advance
        assert glyph.numberOfContours == 2


@check
def dots_are_round(ctx: Ctx) -> None:
    side = fp.REF.dot * fp.DOT_SCALE * fp.UPM / fp.REF.upm
    for g in fp.new_glyphs():
        dot = max(g.contours, key=lambda c: min(p[1] for p in c))
        xs, ys = [p[0] for p in dot], [p[1] for p in dot]
        assert abs((max(xs) - min(xs)) - side) <= 1 and abs((max(ys) - min(ys)) - side) <= 1, (g.name, side)
        assert sum(not on for _, _, on in dot) == 8 and len(dot) == 12, g.name
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        on_curve = [math.dist((x, y), (cx, cy)) for x, y, on in dot if on]
        assert max(on_curve) - min(on_curve) <= 1, (g.name, on_curve)


def _turned_bo_mark(ctx: Ctx, ch: str) -> tuple[list[list[fp.Point]], list[fp.Point]]:
    """BO-TBGo's upright mark turned 180 degrees exactly as fontpatch turns its own upright designs, in 1024
    units: (flattened contours, every outline point including control points)."""
    glyph_set, name = ctx.bo.getGlyphSet(), ctx.bo.getBestCmap()[ord(ch)]
    advance, k = ctx.bo["hmtx"][name][0], fp.UPM / fp.REF.upm
    pivot = fp.REF.x_height + fp.REF.base

    def turn(p: fp.Point) -> fp.Point:
        return (advance - p[0]) * k, (pivot - p[1]) * k

    pen, recording = fp._SegmentPen(glyph_set, turn, 2.0), RecordingPen()
    glyph_set[name].draw(pen)
    glyph_set[name].draw(recording)
    return pen.contours, [turn(p) for _, args in recording.value for p in args]


def _ink(contours: list[list[fp.Point]]) -> list[bool]:
    """Pixels more than half covered, at 2 font units per pixel."""
    canvas = fp._Canvas(400, 600)
    canvas.fill([[(20 + x / 2, 280 - y / 2) for x, y in c] for c in contours])
    return [v > 0.5 for v in canvas.coverage()]


@check
def new_marks_are_own_outlines(ctx: Ctx) -> None:
    """The new glyphs must not reproduce BO-TBGo's upright marks turned 180 degrees (which is what a copied
    outline would be): limited overlap, and no outline point on or next to one of BO-TBGo's points."""
    glyph_set = ctx.new.getGlyphSet()
    for g in fp.new_glyphs():
        bo_contours, bo_points = _turned_bo_mark(ctx, UPRIGHT[g.name])
        pen = fp._SegmentPen(glyph_set, lambda p: (float(p[0]), float(p[1])), 2.0)
        glyph_set[g.name].draw(pen)
        ours, theirs = _ink(pen.contours), _ink(bo_contours)
        iou = sum(a and b for a, b in zip(ours, theirs)) / sum(a or b for a, b in zip(ours, theirs))
        nearest = min(math.dist(p[:2], q) for c in g.contours for p in c for q in bo_points)
        assert iou < MAX_IOU, f"{g.name}: overlap {iou:.3f} with the turned BO-TBGo '{UPRIGHT[g.name]}'"
        assert nearest >= MIN_POINT_DISTANCE, f"{g.name}: a point lies {nearest:.1f} units from a BO-TBGo point"


@check
def untouched_tables_and_fields_kept(ctx: Ctx) -> None:
    _, old, _ = fp.read_tables(ctx.rr_sfnt)
    _, new, _ = fp.read_tables(ctx.patched_sfnt)
    assert set(old) == set(new)
    for tag in ("OS/2", "cvt ", "gasp", "FFTM"):
        assert old[tag] == new[tag], tag
    assert _same_bytes_except(old["head"], new["head"], [(8, 12), (36, 44)]), "head"
    assert _same_bytes_except(old["hhea"], new["hhea"], [(34, 36)]), "hhea"
    assert _same_bytes_except(old["maxp"], new["maxp"], [(4, 10)]), "maxp"
    assert old["post"][:32] == new["post"][:32], "post header"
    added = _name_records(ctx.new) - _name_records(ctx.old)
    assert _name_records(ctx.old) <= _name_records(ctx.new)
    assert added == {(1, 0, 0, 10, fp.RR_DESCRIPTION), (3, 1, 0x409, 10, fp.RR_DESCRIPTION)}, added


@check
def bbox_maxp_hmtx_consistent(ctx: Ctx) -> None:
    font = TTFont(io.BytesIO(ctx.patched_sfnt))
    glyf = font["glyf"]
    # stored glyph headers (the original font's head box follows them, not a recalculation from points)
    boxes = [(g.xMin, g.yMin, g.xMax, g.yMax) for g in (glyf[n] for n in font.getGlyphOrder()) if g.numberOfContours]
    head = font["head"]
    assert (head.xMin, head.yMin, head.xMax, head.yMax) == (
        min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    stored = (font["maxp"].numGlyphs, font["maxp"].maxPoints, font["maxp"].maxContours)
    font["maxp"].recalc(font)
    assert stored == (font["maxp"].numGlyphs, font["maxp"].maxPoints, font["maxp"].maxContours), stored
    assert font["hhea"].advanceWidthMax == max(adv for adv, _ in font["hmtx"].metrics.values())


@check
def contours_clockwise_and_rise(ctx: Ctx) -> None:
    for g in fp.new_glyphs():
        for contour in g.contours:
            area = sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(contour, contour[1:] + contour[:1]))
            assert area < 0, f"{g.name}: contour is not clockwise"
            assert contour[0][2], f"{g.name}: contour must start on-curve"
    for low, high in zip(fp.new_glyphs(0), fp.new_glyphs(68)):
        assert low.advance == high.advance
        assert [[(x, y + 68, on) for x, y, on in c] for c in low.contours] == [list(c) for c in high.contours]


# ------------------------------------------------------------------------------------------- supplement font

@check
def supplement_has_only_notdef_and_two_glyphs(ctx: Ctx) -> None:
    font = ctx.supplement
    assert font.getGlyphOrder() == [".notdef"] + NEW_NAMES, font.getGlyphOrder()
    for sub in font["cmap"].tables:
        assert sub.cmap == NEW_CPS, (sub.platformID, sub.platEncID, sub.cmap)
    assert 0x20 not in font.getBestCmap(), "the supplement font must not map the space"
    assert font["glyf"][".notdef"].numberOfContours == 0
    for name in NEW_NAMES:
        assert _outline(font, name) == _outline(ctx.new, name), name
        assert font["hmtx"][name] == ctx.new["hmtx"][name], name
    result = fp.verify_font(ctx.supplement_raw)
    assert all(result.values()), result


@check
def supplement_vertical_metrics(ctx: Ctx) -> None:
    """Ascent and line gaps copy RR_System_Font; the declared descent covers the glyphs."""
    descent_fields = {("hhea", "descent"), ("OS/2", "sTypoDescender"), ("OS/2", "usWinDescent")}
    for tag, fields in fp.RR_VMETRICS.items():
        for field, value in fields.items():
            assert getattr(ctx.old[tag], field) == value, (tag, field, "differs from the game's RR_System_Font")
            if (tag, field) not in descent_fields:
                assert getattr(ctx.supplement[tag], field) == value, (tag, field)
    sup, low = ctx.supplement, ctx.supplement["head"].yMin
    assert low < 0 and sup["hhea"].descent == low == -sup["OS/2"].usWinDescent, (low, sup["hhea"].descent)
    assert sup["OS/2"].sTypoDescender <= low
    assert sup["head"].unitsPerEm == ctx.old["head"].unitsPerEm
    for rise in (0, 68):
        depth = min(g.bounds()[1] for g in fp.new_glyphs(rise))
        assert fp.supplement_vmetrics(fp.new_glyphs(rise))["hhea"]["descent"] == min(0, depth), rise


@check
def supplement_name_table_exact(ctx: Ctx) -> None:
    ids = {"copyright": 0, "familyName": 1, "styleName": 2, "uniqueFontIdentifier": 3, "fullName": 4, "version": 5,
           "psName": 6, "description": 10, "licenseDescription": 13}
    expected = {(3, 1, 0x409, ids[key], text) for key, text in fp.SUPPLEMENT_NAMES.items()}
    assert _name_records(ctx.supplement) == expected, _name_records(ctx.supplement) ^ expected


# ----------------------------------------------------------------------------------------------------- fslt

def _slots(raw: bytes) -> list[dict]:
    return fslt.parse(raw)["slots"]


@check
def fslt_without_options_is_byte_identical(ctx: Ctx) -> None:
    assert fp.patch_fslt(ctx.fslt_raw) == ctx.fslt_raw


@check
def fslt_insert_changes_only_slot_0_1_font_lists(ctx: Ctx) -> None:
    patched = fp.patch_fslt(ctx.fslt_raw, insert_supplement=True)
    assert fslt.encode(fslt.parse(patched)) == patched
    for i, (old, new) in enumerate(zip(_slots(ctx.fslt_raw), _slots(patched))):
        if i not in fp.DIALOGUE_SLOTS:
            assert old == new, i
            continue
        assert old["rules"] == new["rules"] and old["tail_rules"] == new["tail_rules"], i
        expected = list(old["fonts"])
        expected.insert(1, {"adjust": old["fonts"][0]["adjust"], "path": fp.SUPPLEMENT_FSLT_PATH})
        assert old["fonts"][0]["path"] == fp.RR_FSLT_PATH and new["fonts"] == expected, (i, new["fonts"])
    try:
        fp.patch_fslt(patched, insert_supplement=True)
    except ValueError:
        pass
    else:
        raise AssertionError("inserting the supplement twice must fail")


@check
def fslt_extend_range_changes_only_one_rule_per_slot(ctx: Ctx) -> None:
    patched = fp.patch_fslt(ctx.fslt_raw, extend_range=True)
    changed = []
    for i, (old, new) in enumerate(zip(_slots(ctx.fslt_raw), _slots(patched))):
        assert old["fonts"] == new["fonts"] and old["tail_rules"] == new["tail_rules"], i
        assert len(old["rules"]) == len(new["rules"]), i
        changed += [(i, a, b) for a, b in zip(old["rules"], new["rules"]) if a != b]
    assert [i for i, _, _ in changed] == list(fp.DIALOGUE_SLOTS), changed
    for _, a, b in changed:
        assert a["texts"] == [":", "~", ""] and b["texts"] == [":", "\u00ff", ""], (a, b)
        assert a["adjust"] == b["adjust"] and a["params"] == b["params"] == [1, 2, 2, 2]


@check
def fslt_both_options(ctx: Ctx) -> None:
    both = fp.patch_fslt(ctx.fslt_raw, insert_supplement=True, extend_range=True)
    step = fp.patch_fslt(fp.patch_fslt(ctx.fslt_raw, insert_supplement=True), extend_range=True)
    assert both == step


# ------------------------------------------------------------------------------------------ outputs and CLI

@check
def outputs_per_variant_are_deterministic(ctx: Ctx) -> None:
    keys = {"rrsystem": [fp.RR_PATH], "rrsystem_range": [fslt.FSLT_EN, fp.RR_PATH],
            "supplement": [fslt.FSLT_EN, fp.SUPPLEMENT_PATH], "supplement_range": [fslt.FSLT_EN, fp.SUPPLEMENT_PATH]}
    assert fp.SUPPLEMENT_PATH == "natives/stm/gui/ui_font/sfes_supplement.oft.1"
    assert list(fp.VARIANTS) == list(keys)
    built = {}
    for variant in fp.VARIANTS:
        built[variant] = fp.outputs(variant, ctx.ps)
        assert list(built[variant]) == keys[variant], (variant, list(built[variant]))
        assert fp.outputs(variant, ctx.ps) == built[variant], f"{variant} is not deterministic"
        assert not any(p.startswith(COMMERCIAL_PREFIXES) for p in built[variant]), variant
    assert built["rrsystem"][fp.RR_PATH] == built["rrsystem_range"][fp.RR_PATH] == ctx.patched_raw
    assert built["rrsystem_range"][fslt.FSLT_EN] == fp.patch_fslt(ctx.fslt_raw, extend_range=True)
    assert built["supplement"][fp.SUPPLEMENT_PATH] == built["supplement_range"][fp.SUPPLEMENT_PATH]
    assert built["supplement"][fslt.FSLT_EN] == fp.patch_fslt(ctx.fslt_raw, insert_supplement=True)
    assert built["supplement_range"][fslt.FSLT_EN] == fp.patch_fslt(ctx.fslt_raw, True, True)


@check
def main_writes_variants_reports_and_previews(ctx: Ctx) -> None:
    fp.OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=fp.OUT_DIR, prefix="_test_") as tmp:
        out, previews = Path(tmp) / "fonts", Path(tmp) / "preview"
        assert fp.main(["--out", str(out), "--preview", "--preview-dir", str(previews)]) == 0
        for variant in fp.VARIANTS:
            info = json.loads((out / variant / "report.json").read_text(encoding="utf-8"))
            assert info["variant"] == variant and set(info["glyphs"]) == {"U+00A1", "U+00BF"}
            for path, data in fp.outputs(variant, ctx.ps).items():
                assert (out / variant / Path(*path.split("/"))).read_bytes() == data, path
            assert all(c for f in info["files"] for c in f.get("checks", {}).values()), variant
        pngs = sorted(p.name for p in previews.glob("*.png"))
        assert pngs == ["closeup_200px.png", "digits_60px.png", "slot0_60px.png", "slot0_60px_rules.png"], pngs
        assert all((previews / p).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for p in pngs)
        assert fp.main(["--variant", "rrsystem_range", "--out", str(out)]) == 0


def _spellings(folder: Path) -> list[str]:
    """Other ways to write a path inside folder that Windows accepts for writing."""
    inside = str(folder / "natives")
    out = [inside.lower(), inside.upper(), inside.replace("\\", "/"), str(folder / "x" / ".." / "natives"),
           inside + "\\", "\\\\?\\" + inside, "\\\\.\\" + inside, "//?/" + inside.replace("\\", "/"),
           "\\??\\" + inside]
    short = ctypes.create_unicode_buffer(1024)
    if folder.exists() and ctypes.windll.kernel32.GetShortPathNameW(str(folder), short, 1024):
        out.append(short.value + "\\natives")
    volume = ctypes.create_unicode_buffer(64)
    if ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(folder.drive + "\\", volume, 64):
        out.append(volume.value + inside[len(folder.drive) + 1:])
    return out


@check
def protected_folders_refused(ctx: Ctx) -> None:
    for folder in (config.GAME_DIR, config.ORIGINAL):
        for spelling in [str(folder), str(folder / "natives")] + _spellings(folder):
            try:
                fp._check_writable(Path(spelling))
            except PermissionError:
                continue
            raise AssertionError(f"writing to {spelling!r} must be refused")
    for allowed in (fp.OUT_DIR / "rrsystem", fp.PREVIEW_DIR, config.ROOT / "originals"):
        fp._check_writable(allowed)


# ------------------------------------------------------------------------------------------------ previews

@check
def rasterizer_covers_exact_area(ctx: Ctx) -> None:
    square = [(3.25, 4.5), (3.25, 14.5), (13.25, 14.5), (13.25, 4.5)]
    hole = [(5.5, 6.5), (9.5, 6.5), (9.5, 10.5), (5.5, 10.5)]
    for contours, area in (([square], 100.0), ([square[::-1]], 100.0), ([square, hole], 84.0)):
        canvas = fp._Canvas(24, 24)
        canvas.fill(contours)
        assert abs(sum(canvas.coverage()) - area) < 1e-6, (area, sum(canvas.coverage()))


@check
def chain_draws_new_marks_below_baseline(ctx: Ctx) -> None:
    slot = fslt.parse(ctx.fslt_raw)["slots"][0]
    before = fp._Fonts(ctx.ps, {})
    after = fp._Fonts(ctx.ps, {fp.RR_PATH: ctx.patched_sfnt})
    for ch in "¡¿":
        ink_below = []
        for fonts in (before, after):
            canvas = fp._Canvas(80, 110)
            fp._draw_text(canvas, fonts, slot, ch, 10, 70, 60, False)
            ink_below.append(sum(canvas.coverage()[72 * 80:]))
        assert ink_below[0] < 1e-6 and ink_below[1] > 30, (ch, ink_below)
        assert after.resolve(slot, ord(ch))[0]["path"] == fp.RR_FSLT_PATH
        assert before.resolve(slot, ord(ch))[0]["path"] == fp.BO_FSLT_PATH


def main(argv: list[str] | None = None) -> int:
    ctx = Ctx()
    failed = 0
    for fn in CHECKS:
        t0 = time.time()
        try:
            fn(ctx)
        except Exception:          # report every failing check, then fail the run
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
        else:
            print(f"PASS {fn.__name__} ({time.time() - t0:.2f}s)")
    print(f"{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    return 1 if failed else 0
