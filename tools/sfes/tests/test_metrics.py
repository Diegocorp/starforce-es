r"""Tests for sfes.metrics: Phase 1 calibration, font-slot facts, cache, overrides, lazy lookup.

Run: C:\StarForceES\.venv\Scripts\python.exe -I -X utf8 C:\StarForceES\tools\run.py tests.test_metrics
Calibration references: research/fonts/line_width_calibration.json (all SF1 English lines without codes,
p99 886.5 px, max 1120.3 px, 38,918 lines) and research/fonts_verify/linewidth_verify.json, population
rr1_dialogue_st_rt_dm (SF1 dialogue files, wait codes stripped: 33,688 lines, p99 887.55 px, widest line
RR1_DM0210_ENG_23 = 1022.71 px), reproduced with the method of tools/investigate/verify_font_linewidth.py.
Temporary files go to a directory inside work/cache that is removed afterwards.
"""
from __future__ import annotations

import io
import json
import re
import tempfile
import time
from pathlib import Path

from fontTools.ttLib import TTFont

from sfes import codes, config, fslt, metrics, msg23, oft
from sfes import scope as scopes
from sfes.metrics import Metrics
from sfes.pak import PakSet

SIZE = 60.0
SPANISH = "áéíóúüñÁÉÍÓÚÜÑ¿¡"
BASE_LETTER = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u", "ñ": "n",
               "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U", "Ü": "U", "Ñ": "N"}
RR_SYSTEM = "natives/stm/gui/ui_font/rr_system_font.oft.1"
TOLERANCE_PX = 1.0
# verify_font_linewidth.py, population rr1_dialogue_st_rt_dm: files skipped by name prefix, reference values
PHASE1_NON_DIALOGUE = tuple(f"rr1_{x}" for x in (
    "battle", "chip", "emname", "mapname", "manname", "mail", "messtest", "inputscreen", "infoscreen",
    "netscreen", "savescreen", "title", "subscrn", "tuto_subscrn", "transerscreen", "recscrn", "warrock",
    "writemail", "wifi", "staff", "ending", "common", "shop", "powerup", "doubleslot", "quiz",
    "numbertrader"))
PHASE1_DIALOGUE = {"lines": 33688, "p50": 567.33, "p99": 887.55, "max": 1022.71}
WAIT_RX = re.compile(r"_CMD_wait2?\(\d+\)")
ANY_BREAK_RX = re.compile(r"\r\n|\n|\r")


def scratch_dir() -> tempfile.TemporaryDirectory:
    """A temporary directory inside work/cache (derived game material stays in the local work tree)."""
    config.CACHE.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=str(config.CACHE), prefix="layout-test-")


class Checks:
    def __init__(self) -> None:
        self.failed: list[str] = []
        self.count = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.count += 1
        print(f"  [{'ok' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")
        if not ok:
            self.failed.append(name)

    def result(self) -> int:
        print(f"{self.count - len(self.failed)}/{self.count} checks passed")
        for name in self.failed:
            print("  failed:", name)
        return 1 if self.failed else 0


def quantile(sorted_values: list[float], f: float) -> float:
    """Phase 1 convention (font_widths.py): value at index int(f * (n - 1))."""
    return sorted_values[int(f * (len(sorted_values) - 1))]


def sf1_messages(ps: PakSet, specs: list[scopes.FileSpec]) -> list[tuple[str, msg23.Msg]]:
    return [(s.path, msg23.parse(ps.read(s.path))) for s in specs]


def test_calibration(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("calibration (slot 0, 60 px)")
    specs = scopes.files("phase1", ps)
    eng = sf1_messages(ps, [s for s in specs if s.group == "sf1_eng"])
    plain = sorted(m.line_px(line, 0, SIZE) for _, msg in eng for e in msg.entries
                   for line in re.split(r"\r?\n", msg.get(e, config.LANG_EN))
                   if line and "<" not in line and "_CMD_" not in line)
    p99 = quantile(plain, 0.99)
    c.check("Phase 1 population: all SF1 English lines without codes", len(plain) == 38918,
            f"{len(plain)} lines")
    c.check("Phase 1 p99 886.5 px +-1", abs(p99 - 886.5) <= TOLERANCE_PX, f"{p99:.1f} px")
    c.check("Phase 1 max 1120.3 px +-1", abs(plain[-1] - 1120.3) <= TOLERANCE_PX, f"{plain[-1]:.1f} px")

    dialogue = sf1_messages(ps, [s for s in specs if s.profile == "dialogue_sf1"])
    rows = []
    for _, msg in dialogue:
        for e in msg.entries:
            for line in codes.split_lines(msg.get(e, config.LANG_EN)):
                if line and not any(t.prints in ("insert", "glyph") for t in codes.tokenize(line)):
                    rows.append((m.line_px(line, 0, SIZE), e.name, line))
    rows.sort()
    widest = rows[-1]
    d99 = quantile([r[0] for r in rows], 0.99)
    c.check("dialogue_sf1 files: 804", len(dialogue) == 804, str(len(dialogue)))
    c.check("widest dialogue_sf1 line is RR1_DM0210_ENG_23", widest[1] == "RR1_DM0210_ENG_23", widest[1])
    c.check("RR1_DM0210_ENG_23 = 1022.7 px +-1 (_CMD_wait2 = 0)", abs(widest[0] - 1022.7) <= TOLERANCE_PX,
            f"{widest[0]:.1f} px {widest[2]!r}")
    print(f"  info: dialogue_sf1 lines without inserts/glyphs: {len(rows)}, p99 {d99:.1f} px, "
          f"p50 {quantile([r[0] for r in rows], 0.5):.1f} px (the 886.5 px figure is the all-SF1 population)")
    c.check("dialogue_sf1 p99 below the 1026 px box", d99 < 1026.0, f"{d99:.1f} px")
    test_phase1_dialogue(c, m, [s for s in specs if s.group == "sf1_eng"], eng)


def test_phase1_dialogue(c: Checks, m: Metrics, specs: list[scopes.FileSpec],
                         messages: list[tuple[str, msg23.Msg]]) -> None:
    """Phase 1 population rr1_dialogue_st_rt_dm with its own method: wait codes stripped, any break splits,
    lines with other codes skipped, quantile a[min(n - 1, int(f * n))]."""
    widths = []
    for spec, (_, msg) in zip(specs, messages):
        if spec.stem.startswith(PHASE1_NON_DIALOGUE):
            continue
        for e in msg.entries:
            for line in ANY_BREAK_RX.split(WAIT_RX.sub("", msg.get(e, config.LANG_EN))):
                if line.strip() and "_CMD_" not in line and "<" not in line:
                    widths.append(m.line_px(line, 0, SIZE))
    widths.sort()
    n = len(widths)
    got = {"lines": n, "max": widths[-1], "p50": widths[min(n - 1, int(0.5 * n))],
           "p99": widths[min(n - 1, int(0.99 * n))]}
    detail = f"lines {got['lines']}/{PHASE1_DIALOGUE['lines']} " + " ".join(
        f"{k} {got[k]:.2f}/{PHASE1_DIALOGUE[k]}" for k in ("p50", "p99", "max"))
    c.check("Phase 1 dialogue population reproduced (lines exact, p50/p99/max +-1 px)",
            got["lines"] == PHASE1_DIALOGUE["lines"]
            and all(abs(got[k] - PHASE1_DIALOGUE[k]) <= TOLERANCE_PX for k in ("p50", "p99", "max")), detail)


def test_charset(c: Checks, m: Metrics) -> None:
    print("character set")
    c.check("slot 0 lacks U+00A0", not m.supported("\u00a0", 0))
    c.check("slot 0 has all Spanish letters and marks", all(m.supported(ch, 0) for ch in SPANISH),
            " ".join(f"{ch}:{m.font_of(ch, 0)}" for ch in "á¿¡"))
    c.check("slot 2 has all Spanish letters and marks", all(m.supported(ch, 2) for ch in SPANISH),
            " ".join(f"{ch}:{m.font_of(ch, 2)}" for ch in "á¿¡"))
    same = [ch for ch, b in BASE_LETTER.items() if m.font_of(ch, 0) == m.font_of(b, 0) == "BO-TBGoStd-Medium"
            and m.advance_em(ch, 0) == m.advance_em(b, 0)]
    c.check("accented letters = base letter advance in BO-TBGo", len(same) == len(BASE_LETTER),
            f"{len(same)}/{len(BASE_LETTER)}")
    c.check("space and digits come from RR_System_Font in slot 0",
            m.font_of(" ", 0) == m.font_of("7", 0) == "RR_System_Font")
    c.check("unsupported_chars finds NBSP, ignores codes",
            m.unsupported_chars("a\u00a0b<ICON INGAME_KEY_A>_CMD_wait(5)", 0) == ["\u00a0"])
    cache = json.loads(metrics.CACHE_PATH.read_text(encoding="utf-8"))
    extra = set(cache["tables"]["base"]["coverage"]["extra"])
    outside = next(chr(cp) for cp in range(0x4E00, 0xA000) if chr(cp) not in extra)
    c.check("character outside the cache resolves lazily from the fonts", m.supported(outside, 0),
            f"U+{ord(outside):04X} -> {m.font_of(outside, 0)}")
    c.check("noncharacter unsupported everywhere",
            not any(m.supported("\U0010ffff", s) for s in metrics.SLOTS))


def test_tokens(c: Checks, m: Metrics) -> None:
    print("codes and lines")
    c.check("glyph tag = 1.0 em", m.line_em("<ICON INGAME_KEY_A>", 0) == 1.0)
    c.check("REPLACE glyph = 1.0 em", m.line_em("<REPLACE  RR1_EX>", 0) == 1.0)
    c.check("wait codes = 0", m.line_em("_CMD_wait(5)_CMD_wait2(30)", 0) == 0.0)
    c.check("insert without resolver = 8 x average lowercase",
            abs(m.line_em("_CMD_insert_option(0)", 0) - 8 * m.avg_lowercase_em(0)) < 1e-12)
    px = m.lines_px("ab\r\ncd\r\n", 0, SIZE)
    c.check("lines_px splits on CRLF only",
            len(px) == 3 and px[2] == 0.0 and len(m.lines_px("a\nb", 0, SIZE)) == 1)
    c.check("line_px = line_em * size", abs(m.line_px("Hola", 0, SIZE) - m.line_em("Hola", 0) * SIZE) < 1e-9)
    c.check("letter spacing counts characters and glyphs",
            abs(m.line_px("ab<ICON X>_CMD_wait(1)", 0, SIZE, letter_space=2.0)
                - m.line_px("ab<ICON X>", 0, SIZE) - 6.0) < 1e-9)
    test_space_override(c, m)


def test_space_override(c: Checks, m: Metrics) -> None:
    before, base_key = m.line_em("a b c", 0), m.state_key()
    space0, space1 = m.advance_em(" ", 0), m.advance_em(" ", 1)
    m.space_override_em = {0: 0.27}
    per_slot = m.line_em("a b c", 0), m.advance_em(" ", 1), m.state_key()
    m.space_override_em = 0.27
    every = m.line_em("a b c", 0), m.advance_em(" ", 1), m.advance_em(" ", 0), m.state_key()
    m.space_override_em = None
    expected = before + 2 * (0.27 - space0)
    c.check("space_override_em per slot (mapping)",
            abs(per_slot[0] - expected) < 1e-12 and per_slot[1] == space1, f"{before:.4f} -> {per_slot[0]:.4f} em")
    c.check("space_override_em for every slot (number)",
            abs(every[0] - expected) < 1e-12 and every[1] == every[2] == 0.27)
    c.check("state_key follows the override", len({base_key, per_slot[2], every[3]}) == 3
            and m.state_key() == base_key and m.line_em("a b c", 0) == before)


def patched_rr_system_font(ps: PakSet) -> tuple[bytes, float]:
    """RR_System_Font with U+00A1 and U+00BF mapped to its '1' glyph (a stand-in for the fonts team's
    added glyphs); returns the .oft.1 bytes and that glyph's advance in em."""
    tt = TTFont(io.BytesIO(oft.decode(ps.read(RR_SYSTEM))))
    glyph = tt.getBestCmap()[ord("1")]
    for table in tt["cmap"].tables:
        if table.isUnicode():
            table.cmap[0xA1] = glyph
            table.cmap[0xBF] = glyph
    out = io.BytesIO()
    tt.save(out)
    return oft.encode(out.getvalue()), tt["hmtx"][glyph][0] / tt["head"].unitsPerEm


def test_cache_and_overrides(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("cache, rebuild and overrides")
    size = metrics.CACHE_PATH.stat().st_size
    c.check("cache file exists and is small", size < 1_000_000, f"{size:,} bytes")
    with scratch_dir() as td:
        path = Path(td) / "metrics_en.json"
        t0 = time.time()
        fresh = Metrics.load(rebuild=True, cache_path=path, paks=ps)
        build_s = time.time() - t0
        probe = "Hola, ¿qué tal? ¡Ñandú! 0123 ＡＢ「」…—"
        c.check("rebuilt table equals the shared cache", fresh.fingerprint == m.fingerprint and all(
            fresh.line_em(probe, s) == m.line_em(probe, s) for s in metrics.SLOTS), f"build {build_s:.1f}s")
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["tables"]["base"]["fingerprint"] = "stale"
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        Metrics.load(cache_path=path, paks=ps)
        rebuilt = json.loads(path.read_text(encoding="utf-8"))["tables"]["base"]["fingerprint"]
        c.check("a stale cache entry is rebuilt automatically", rebuilt == fresh.fingerprint)
        raw, em = patched_rr_system_font(ps)
        ovr = Metrics.load(overrides={RR_SYSTEM: raw}, cache_path=path, paks=ps)
        c.check("override key differs from base",
                ovr.key.startswith("ovr-") and ovr.fingerprint != m.fingerprint, ovr.key)
        c.check("patched font serves ¡ and ¿ in slot 0",
                ovr.font_of("¡", 0) == ovr.font_of("¿", 0) == "RR_System_Font"
                and ovr.advance_em("¡", 0) == em, f"{ovr.advance_em('¡', 0):.4f} em")
        c.check("base metrics unchanged by the override", m.font_of("¡", 0) == "BO-TBGoStd-Medium")
        tables = json.loads(path.read_text(encoding="utf-8"))["tables"]
        c.check("cache keeps base and override tables", sorted(tables) == sorted(["base", ovr.key]))
        stamp = path.stat().st_mtime_ns
        again = Metrics.load(overrides={RR_SYSTEM: raw}, cache_path=path, paks=ps)
        c.check("override table reused from the cache (no rewrite)",
                again.font_of("¡", 0) == "RR_System_Font" and path.stat().st_mtime_ns == stamp)


def main(argv: list[str] | None = None) -> int:
    c = Checks()
    t0 = time.time()
    ps = PakSet()
    m = Metrics.load(paks=ps)
    load_s = time.time() - t0
    c.check("Metrics.load from cache", load_s < 5.0, f"{load_s:.2f}s, key {m.key}")
    names = {s: [f.name for f in m.chain(s)] for s in metrics.SLOTS}
    c.check("chains follow the contract", names[0][:2] == ["RR_System_Font", "BO-TBGoStd-Medium"]
            and names[2][:2] == ["RR_Design_Font_W7", "BO-GSanSrfStdN-Bd"]
            and names[3][:2] == names[5][:2] == ["RR_Design_Font_W9", "BO-GSanSrfStdN-UB"]
            and names[4][0] == "DFP-ReiSho-W6" and names[7] == ["BO-GSanSrfStdN-Bd"])
    official = metrics.chains_from_fslt(ps.read(fslt.FSLT_EN))
    c.check("chains match the official fslt", all(official[s] == m.chain(s) for s in metrics.SLOTS))
    test_charset(c, m)
    test_tokens(c, m)
    t1 = time.time()
    test_calibration(c, m, ps)
    print(f"  calibration time {time.time() - t1:.1f}s")
    test_cache_and_overrides(c, m, ps)
    print(f"total {time.time() - t0:.1f}s")
    return c.result()
