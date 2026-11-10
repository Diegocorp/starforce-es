r"""Tests for sfes.wrap and sfes.inserts (the layout engine).

Run: C:\StarForceES\.venv\Scripts\python.exe -I -X utf8 C:\StarForceES\tools\run.py tests.test_wrap [-v]
  - inserts: player name, item/card ids through the English scripts, numbers, REF, PLATMSG, DATETIME,
    Spanish from work files (absent, broken, minimum status, english()), every insert of the phase1 English;
  - wrap: every SF1 dialogue English entry in auto mode against 3 lines x 1026 px at 60 px (exceptions are
    listed), code signatures, trailing CRLF counts, kept ':' / indented layouts, manual identity, observed
    limits (from the paks, English-only inserts, cache keys), edge cases, timing.
English-only resolvers make the numbers independent of the live work/strings. Temporary work files go to a
directory inside work/cache that is removed afterwards.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from sfes import codes, config, msg23, profiles, wrap
from sfes import scope as scopes
from sfes.inserts import InsertResolver, load_ref_index, script_guids
from sfes.metrics import Metrics
from sfes.pak import PakSet
from sfes.tests.test_metrics import Checks, scratch_dir

DIALOGUE = profiles.PROFILES["dialogue_sf1"]
DM0000 = "natives/stm/text/rr1/message/eng/rr1_dm0000_eng.msg.23"
ITEM_SCRIPT = "natives/stm/text/rr1/userdata/itemname.user.3.en"
ITEM_TABLE = "natives/stm/text/rr1/message/eng/rr1_itemname_eng.msg.23"
CARD_TABLE = "natives/stm/text/rr1/message/eng/rr1_chipname_eng.msg.23"
IBOXDATA = "natives/stm/text/rr1/message/eng/rr1_iboxdata_eng.msg.23"
ITEMINF = "natives/stm/text/rr1/message/eng/rr1_iteminf_eng.msg.23"
MAILSCRN = "natives/stm/text/rr1/message/eng/rr1_mailscrn_eng.msg.23"
GOT_BOX = '_CMD_insert_option(0) got:\r\n"_CMD_insert_itemname(112)"!!\r\n'
TIME_BUDGET_S = 60.0
Auto = Callable[..., wrap.WrapResult]       # auto(es, en="") on the SF1 dialogue profile


def tok(raw: str) -> codes.Token:
    (t,) = codes.tokenize(raw)
    return t


def entry_name(r: InsertResolver, table: str, game_id: int) -> str | None:
    t = r.table(table)
    idx = t.index_of(game_id)
    return None if idx is None else t.names[idx]


def entry_text(r: InsertResolver, table: str, name: str) -> str:
    t = r.table(table)
    return t.texts[t.names.index(name)]


def has_one_glyph(m: Metrics, text: str) -> bool:
    """`text` is plain text plus exactly one glyph code (1.0 em)."""
    return abs(m.line_em(text, 0) - m.text_em(codes.strip_markup(text), 0) - 1.0) < 1e-12


def english_of(ps: PakSet, path: str) -> list[str]:
    msg = msg23.parse(ps.read(path))
    return [msg.get(e, config.LANG_EN) for e in msg.entries]


def english_entry(ps: PakSet, path: str, name: str) -> str:
    msg = msg23.parse(ps.read(path))
    return next(msg.get(e, config.LANG_EN) for e in msg.entries if e.name == name)


def write_work_file(work: Path, group: str, stem: str, entries: list[dict]) -> None:
    p = work / "phase1" / group / f"{stem}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema": 1, "entries": entries}
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------------------------- inserts

def test_inserts(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("inserts (English tables)")
    r = InsertResolver(m, paks=ps, english_only=True)

    def em(raw: str) -> float:
        return r.width_em(tok(raw), 0)

    player = r.table("player")
    c.check("player_name slot 1: entry 0 'Geo' (given), entry 1 'Stelar' (family)",
            player.texts[:2] == ["Geo", "Stelar"], str(player.texts))
    c.check("option(0) = width of 'Geo'", em("_CMD_insert_option(0)") == m.text_em("Geo", 0))
    c.check("option(1) = width of 'Stelar'", em("_CMD_insert_option(1)") == m.text_em("Stelar", 0))
    c.check("option(2) = default", em("_CMD_insert_option(2)") == m.default_insert_em(0))
    c.check("item script covers ids 0-255", len(script_guids(ps.read(ITEM_SCRIPT))) == 256)
    c.check("item ids 0-68 = entry index",
            all(entry_name(r, "item", i) == f"RR1_ITEMNAME_ENG_{i}" for i in range(69)))
    items = {i: entry_name(r, "item", i) for i in (69, 70, 79, 80, 99, 111, 112, 113, 300)}
    c.check("item ids through the script", items == {
        69: None, 70: "RR1_ITEMNAME_ENG_69", 79: "RR1_ITEMNAME_ENG_78", 80: "RR1_ITEMNAME_ENG_79",
        99: "RR1_ITEMNAME_ENG_83", 111: None, 112: "RR1_ITEMNAME_ENG_95", 113: "RR1_ITEMNAME_ENG_96",
        300: None}, str(items))
    cards = {i: entry_name(r, "card", i) for i in (150, 151, 168, 169, 181, 208, 231, 247, 255)}
    c.check("card ids through the script", cards == {
        150: "RR1_CHIPNAME_ENG_150", 151: "RR1_CHIPNAME_ENG_206", 168: "RR1_CHIPNAME_ENG_223", 169: None,
        181: "RR1_CHIPNAME_ENG_151", 208: "RR1_CHIPNAME_ENG_178", 231: "RR1_CHIPNAME_ENG_187",
        247: "RR1_CHIPNAME_ENG_202", 255: None}, str(cards))
    c.check("itemname(112) = width of entry 95",
            em("_CMD_insert_itemname(112)") == m.line_em(entry_text(r, "item", "RR1_ITEMNAME_ENG_95"), 0),
            r.describe(tok("_CMD_insert_itemname(112)")))
    widest_item = max(m.line_em(t, 0) for t in r.table("item").texts)
    c.check("unknown item id and itemnamefree = widest item",
            em("_CMD_insert_itemname(69)") == em("_CMD_insert_itemnamefree(0x01)") == widest_item)
    card187 = entry_text(r, "card", "RR1_CHIPNAME_ENG_187")
    c.check("btlwarename(231) = entry 187 including its glyph code",
            em("_CMD_insert_btlwarename(231)") == m.line_em(card187, 0) and has_one_glyph(m, card187))
    test_insert_rules(c, m, r)
    test_all_inserts(c, r, ps)


def test_insert_rules(c: Checks, m: Metrics, r: InsertResolver) -> None:
    def em(raw: str) -> float:
        return r.width_em(tok(raw), 0)

    c.check("numdisp low nibble digits", em("_CMD_insert_numdisp(0x16,0)") == 6 * m.digit_em(0)
            and em("_CMD_insert_numdisp(0x08,0x01)") == 8 * m.digit_em(0))
    c.check("numdisp without a digit count (low nibble 0) = default",
            em("_CMD_insert_numdisp(0x30,1)") == m.default_insert_em(0),
            r.describe(tok("_CMD_insert_numdisp(0x30,1)")))
    c.check("{0} = 3 digits", em("{0}") == 3 * m.digit_em(0))
    card152 = entry_text(r, "card", "RR1_CHIPNAME_ENG_152")
    c.check("REF to a card name with a glyph code",
            em("<REF RR1_CHIPNAME_ENG_152>") == m.line_em(card152, 0) and has_one_glyph(m, card152))
    loc = r.table("localize")
    variants = [t for n, t in zip(loc.names, loc.texts) if n.startswith("Common_Localize_2_")]
    c.check("PLATMSG = widest variant", len(variants) == 2
            and em("<PLATMSG Common_Localize_2>") == max(m.line_em(v, 0) for v in variants))
    dt = '<DATETIME {0} "hh:mm A MM/DD/YYYY">'
    literal = max(m.text_em(": AM //", 0), m.text_em(": PM //", 0))
    c.check("DATETIME = 12 digits + literals with the wider of AM/PM",
            abs(em(dt) - (literal + 12 * m.digit_em(0))) < 1e-12, r.describe(tok(dt)))
    c.check("unknown insert = default", em("_CMD_insert_brother(1,0)") == m.default_insert_em(0))


def test_all_inserts(c: Checks, r: InsertResolver, ps: PakSet) -> None:
    rules: Counter = Counter()
    bad = []
    for spec in scopes.files("phase1", ps):
        msg = msg23.parse(ps.read(spec.path))
        for e in msg.entries:
            for t in codes.tokenize(msg.get(e, config.LANG_EN)):
                if t.prints != "insert":
                    continue
                a = r.assumption(t)
                rules[a.rule] += 1
                if r.width_em(t, 0) <= 0 or "not found" in a.note:
                    bad.append(r.describe(t))
    print("  info: phase1 English inserts by rule:", dict(sorted(rules.items())))
    c.check("every phase1 English insert has a positive width and REF targets exist", not bad, str(bad[:5]))


def test_spanish_tables(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("inserts (Spanish from work files)")
    english = InsertResolver(m, paks=ps, english_only=True)
    with scratch_dir() as td:
        work = Path(td)
        empty = InsertResolver(m, work_dir=work, paks=ps)
        c.check("work dir without files = English",
                empty.width_em(tok("_CMD_insert_itemname(112)"), 0)
                == english.width_em(tok("_CMD_insert_itemname(112)"), 0))
        write_work_file(work, "sf1_eng", "rr1_itemname_eng", [
            {"name": "RR1_ITEMNAME_ENG_95", "es": "Memoria PS 10", "status": "draft"},
            {"name": "RR1_ITEMNAME_ENG_94", "es": "Aleación", "status": "todo"}])
        write_work_file(work, "sf1_eng", "rr1_chipname_eng", [
            {"name": "RR1_CHIPNAME_ENG_152", "es": "Fuego Tauro<REPLACE  RR1_EX>", "status": "reviewed"}])
        broken = work / "phase1" / "sf1_base" / "rr1_player_name.json"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("{ not json", encoding="utf-8")
        r = InsertResolver(m, work_dir=work, paks=ps)
        strict = InsertResolver(m, work_dir=work, paks=ps, min_status="reviewed")

        def em(res: InsertResolver, raw: str) -> float:
            return res.width_em(tok(raw), 0)

        item112, ref152 = "_CMD_insert_itemname(112)", "<REF RR1_CHIPNAME_ENG_152>"
        c.check("translated item name used", em(r, item112) == m.text_em("Memoria PS 10", 0),
                r.describe(tok(item112)))
        c.check("status todo keeps English", em(r, "_CMD_insert_itemname(110)")
                == m.line_em(entry_text(english, "item", "RR1_ITEMNAME_ENG_94"), 0))
        c.check("REF uses the translated target",
                em(r, ref152) == m.line_em("Fuego Tauro<REPLACE  RR1_EX>", 0))
        c.check("broken work file falls back to English",
                em(r, "_CMD_insert_option(0)") == m.text_em("Geo", 0))
        c.check("min_status reviewed: draft item stays English, reviewed card is Spanish",
                em(strict, item112) == em(english, item112) and em(strict, ref152) == em(r, ref152))
        c.check("english() ignores the work files and is reused",
                em(r.english(), item112) == em(english, item112) and r.english() is r.english()
                and r.english().english() is r.english())
        try:
            InsertResolver(m, paks=ps, min_status="keep")
            rejected = False
        except ValueError:
            rejected = True
        c.check("unknown min_status rejected", rejected)


# ------------------------------------------------------------------------------------------------ wrap

def colon_layout_kept(en: str, out: str) -> bool:
    """Every English line that ends with ':' (and is not the last) still ends a line of the result (earlier
    soft breaks may join, e.g. 'Geo' / 'gave away:' -> 'Geo gave away:')."""
    ends = [line.rstrip(" ") for line in wrap.visible_lines(out)[:-1]]
    return all(any(e.endswith(line.rstrip(" ")) for e in ends)
               for line in wrap.visible_lines(en)[:-1] if wrap.ends_with_colon(line))


def test_dialogue(c: Checks, m: Metrics, r: InsertResolver, ps: PakSet, verbose: bool) -> None:
    print("wrap: every SF1 dialogue English entry (auto, 3 lines x 1026 px at 60 px)")
    t0 = time.time()
    total = fit = manual_same = trailing_ok = changed = colon = colon_kept = 0
    exceptions = []
    for spec in scopes.files("phase1", ps):
        if spec.profile != DIALOGUE.name:
            continue
        msg = msg23.parse(ps.read(spec.path))
        for e in msg.entries:
            en = msg.get(e, config.LANG_EN)
            res = wrap.final_text(en, en, profile=DIALOGUE, path=spec.path, metrics=m, resolver=r,
                                  wrap_mode="auto")
            man = wrap.final_text(en, en, profile=DIALOGUE, path=spec.path, metrics=m, resolver=r,
                                  wrap_mode="manual")
            total += 1
            ok = not (res.overflow_lines or res.overflow_width)
            fit += ok
            changed += res.text != en
            manual_same += man.text == en
            trailing_ok += codes.trailing_breaks(res.text) == codes.trailing_breaks(en)
            if any(wrap.ends_with_colon(line) for line in wrap.visible_lines(en)[:-1]):
                colon += 1
                colon_kept += colon_layout_kept(en, res.text)
            if codes.code_signature(res.text) != codes.code_signature(en):
                exceptions.append((spec.stem, e.name, "CODES CHANGED"))
            elif not ok:
                exceptions.append((spec.stem, e.name, f"{len(res.lines)} lines, {max(res.widths_px):.1f} px"))
            if verbose and res.text != en:
                print("    ", e.name, repr(en), "->", repr(res.text))
    elapsed = time.time() - t0
    print(f"  info: {fit}/{total} entries fit after auto wrap ({changed} re-broken); "
          f"exceptions: {len(exceptions)}")
    for x in exceptions:
        print("    exception:", *x)
    c.check("nearly all dialogue entries fit (>= 99.5%)", fit >= 0.995 * total, f"{fit}/{total}")
    c.check("code signatures unchanged for all", not any(x[2] == "CODES CHANGED" for x in exceptions))
    c.check("trailing CRLF count = English for all", trailing_ok == total, f"{trailing_ok}/{total}")
    c.check("manual mode is identity on CRLF text", manual_same == total, f"{manual_same}/{total}")
    c.check("lines ending with ':' keep their break (get/use/give boxes)", colon_kept == colon > 150,
            f"{colon_kept}/{colon} entries")
    c.check("performance: auto + manual for all dialogue well under a minute", elapsed < TIME_BUDGET_S,
            f"{elapsed:.1f}s")


def observed_specs(ps: PakSet) -> list[scopes.FileSpec]:
    return [s for s in scopes.files("phase1", ps) if not profiles.PROFILES[s.profile].hard]


def test_observed(c: Checks, m: Metrics, r: InsertResolver, ps: PakSet) -> None:
    print("wrap: observed profiles (limits from the English of each file)")
    wrap.clear_observed_cache()
    checked, over, same = 0, [], 0
    specs = observed_specs(ps)
    for spec in specs:
        profile = profiles.PROFILES[spec.profile]
        ens = english_of(ps, spec.path)
        limits = wrap.observed_limits(spec.path, ens, m, r)
        same += wrap.file_limits(spec.path, m, r) is limits
        for en in ens:
            res = wrap.final_text(en, en, profile=profile, path=spec.path, metrics=m, resolver=r,
                                  wrap_mode=profile.wrap)
            checked += 1
            if res.overflow_lines or res.overflow_width or res.unit != "em" or res.max_px != limits.max_px:
                over.append((spec.stem, en[:60], res.widths_px, res.max_px, res.max_lines))
    for x in over:
        print("    over:", *x)
    c.check("English of observed files fits its own file limits (auto for observed_text)", not over,
            f"{checked - len(over)}/{checked} entries, {len(specs)} files")
    c.check("final_text takes the file limits from the paks (same cached object)", same == len(specs))
    box = english_entry(ps, IBOXDATA, "RR1_IBOXDATA_ENG_29")
    ibox = wrap.final_text(box, box, profile="observed_text", path=IBOXDATA, metrics=m, resolver=r,
                           wrap_mode="auto")
    c.check("observed_text get box keeps its two lines", ibox.text == box, repr(ibox.text))
    test_observed_cache(c, m, r, ps)


def test_observed_cache(c: Checks, m: Metrics, r: InsertResolver, ps: PakSet) -> None:
    ens = english_of(ps, MAILSCRN)
    first = wrap.observed_limits(MAILSCRN, ens, m, r)
    c.check("observed limits cached for the same file, English and state",
            first is wrap.observed_limits(MAILSCRN, list(ens), m, r))
    other = wrap.observed_limits(MAILSCRN, ens[:1], m, r)
    m.space_override_em = 0.27
    spaced = wrap.observed_limits(MAILSCRN, ens, m, r)
    m.space_override_em = None
    c.check("other English texts or another metrics state give fresh limits",
            other is not first and spaced.max_px != first.max_px
            and wrap.observed_limits(MAILSCRN, ens, m, r) is first,
            f"{first.max_px:.4f} em, with space 0.27 em: {spaced.max_px:.4f} em")
    long_es = "Una descripción bastante más larga que la original"
    res = wrap.final_text(long_es, "Short text", profile="observed_text", path=ITEMINF, metrics=m, resolver=r,
                          wrap_mode="auto")
    c.check("observed entry without observed_limits() is wrapped against the file limit",
            res.max_px == wrap.file_limits(ITEMINF, m, r).max_px and len(res.lines) == 2 and not res.notes,
            " | ".join(res.lines))
    missing = "natives/stm/text/rr1/message/eng/x.msg.23"
    lone = wrap.final_text("Uno\nDos", "Hi", profile="observed", path=missing, metrics=m, resolver=r,
                           wrap_mode="manual")
    c.check("file not in the paks: the entry's English is the limit (with a note)",
            any("not in the paks" in n for n in lone.notes) and lone.overflow_lines)
    given = wrap.Limits(0, 60.0, 300.0, 9, True, "px", "test")
    res = wrap.final_text("palabra " * 6, "", profile=DIALOGUE, path=DM0000, metrics=m, resolver=r,
                          wrap_mode="auto", limits=given)
    c.check("limits= replaces the profile limits", res.max_px == 300.0 and max(res.widths_px) <= 300.0
            and len(res.lines) > 1)


def fake_spanish_work(work: Path, ps: PakSet) -> None:
    """Every SF1 item and card name and every REF target 'translated' to its English + 10 x 'W' (draft)."""
    rows: dict[str, list[tuple[str, str]]] = {}
    for name, ref in load_ref_index(ps).items():
        rows.setdefault(ref["path"], []).append((name, ref["en"]))
    for path in (ITEM_TABLE, CARD_TABLE):
        msg = msg23.parse(ps.read(path))
        rows.setdefault(path, []).extend((e.name, msg.get(e, config.LANG_EN)) for e in msg.entries)
    for path, pairs in sorted(rows.items()):
        write_work_file(work, "fake", scopes.stem_of(path), [
            {"name": n, "es": en + "W" * 10, "status": "draft"} for n, en in sorted(set(pairs)) if en])


def naive_max_em(path: str, ens: list[str], m: Metrics, r: InsertResolver) -> float:
    slot = profiles.observed_slot(path)
    return max([0.0] + [m.line_em(line, slot, r) for en in ens for line in wrap.visible_lines(en)])


def test_observed_spanish(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("wrap: observed limits do not move with Spanish insert texts")
    with scratch_dir() as td:
        work = Path(td)
        fake_spanish_work(work, ps)
        r_es = InsertResolver(m, work_dir=work, paks=ps)
        r_en = r_es.english()
        moved = wrong = 0
        for spec in observed_specs(ps):
            ens = english_of(ps, spec.path)
            wrap.clear_observed_cache()
            limit = wrap.file_limits(spec.path, m, r_es).max_px
            english = naive_max_em(spec.path, ens, m, r_en)
            moved += naive_max_em(spec.path, ens, m, r_es) > english + wrap.EPS
            wrong += abs(limit - english) > 1e-12
        wrap.clear_observed_cache()
    c.check("limits equal the English-only measurement in every observed file", wrong == 0, f"{wrong} differ")
    c.check("the fake Spanish names would have moved limits (test has teeth)", moved > 0,
            f"{moved} files would move if measured with Spanish inserts")


def greedy_ok(res: wrap.WrapResult, m: Metrics, r: InsertResolver) -> bool:
    """No line could also take the first word of the next line."""
    for a, b in zip(res.lines, res.lines[1:]):
        nxt = b.split(" ", 1)[0]
        if m.line_px(a + " " + nxt, 0, DIALOGUE.size, r) <= DIALOGUE.max_px:
            return False
    return True


def test_samples(c: Checks, m: Metrics, r: InsertResolver) -> None:
    print("wrap: samples")

    def auto(es: str, en: str = "") -> wrap.WrapResult:
        return wrap.final_text(es, en, profile=DIALOGUE, path=DM0000, metrics=m, resolver=r, wrap_mode="auto")

    es = ("¿Qué pasó con el Transador? ¡No lo encuentro por\nningún lado! ¿Me ayudas, "
          "_CMD_insert_option(0)?_CMD_wait(30) ¡Ñandú!")
    res = auto(es, "A sample line\r\nfor the wrap\r\ntest!\r\n")
    c.check("Spanish sample: lines fit, at most 3, greedy",
            not res.overflow_width and not res.overflow_lines and greedy_ok(res, m, r),
            " | ".join(f"{w:.0f}px {line}" for line, w in zip(res.lines, res.widths_px)))
    c.check("Spanish sample: words, accents and codes preserved, one trailing CRLF",
            " ".join(res.lines).split() == es.replace("\n", " ").split() and res.text.endswith("¡Ñandú!\r\n")
            and not m.unsupported_chars(res.text, 0))
    long = auto("A" * 120 + " fin")
    c.check("over-long word reported as overflow",
            long.overflow_width and any("wider" in n for n in long.notes), f"{long.widths_px[0]:.0f} px")
    many = auto(" ".join(["palabra"] * 60))
    c.check("too many lines reported as overflow", many.overflow_lines and len(many.lines) > 3,
            f"{len(many.lines)} lines")
    c.check("non-printing code after a space joins the previous word",
            auto("Hola, _CMD_wait(5)mundo").text == "Hola,_CMD_wait(5) mundo")
    c.check("glyph and insert codes stay with the word they touch",
            auto("Pulsa <ICON INGAME_KEY_A>.\nVale, _CMD_insert_option(0)!").text
            == "Pulsa <ICON INGAME_KEY_A>. Vale, _CMD_insert_option(0)!")
    c.check("REPLACE keeps its two spaces, other runs collapse",
            auto("Carta  Tauro<REPLACE  RR1_EX>   ya").text == "Carta Tauro<REPLACE  RR1_EX> ya")
    c.check("hard paragraph break kept", auto("Uno\r\n\r\nDos\ntres").text == "Uno\r\n\r\nDos tres")
    test_layout_samples(c, auto)
    test_edge_samples(c, m, r, auto)


def test_layout_samples(c: Checks, auto: Auto) -> None:
    c.check("get box: break after ':' kept (English)", auto(GOT_BOX, GOT_BOX).text == GOT_BOX)
    got_es = auto('_CMD_insert_option(0) obtuvo:\n"_CMD_insert_itemname(112)"!!', GOT_BOX)
    c.check("get box: break after ':' kept (Spanish), with a note",
            got_es.text == '_CMD_insert_option(0) obtuvo:\r\n"_CMD_insert_itemname(112)"!!\r\n'
            and any("':'" in n for n in got_es.notes), repr(got_es.text))
    c.check("':' followed by a wait code still ends the line",
            auto("Lee esto:_CMD_wait(10)\nHola", "x").text == "Lee esto:_CMD_wait(10)\r\nHola")
    c.check("':' inside a line does not break",
            auto("Nota: esto\nsigue", "x").text == "Nota: esto sigue")
    ding = "*din don\r\n      din don*_CMD_wait(80)"
    c.check("indented line keeps its break and indentation", auto(ding, ding).text == ding)
    c.check("one leading space is not an indent", auto("Hola,\n mundo", "x").text == "Hola, mundo")
    c.check("lines of only spaces are dropped", auto("Uno:\r\n   \r\nDos", "x").text == "Uno:\r\nDos")
    c.check("padding of choice labels kept",
            auto(" Sí   ", " Yes  ").text == " Sí   " and auto("   No   ", "   No   ").text == "   No   ")
    c.check("padding kept only on the sides the English pads",
            auto("  Sí  ", "  Yes").text == "  Sí" and auto("  Sí  ", "Yes  ").text == "Sí  ")
    c.check("stray spaces at line ends removed when the English has none",
            auto("Hola  \r\n\r\nmundo  ", "Hi").text == "Hola\r\n\r\nmundo"
            and auto("Hola  \r\n\r\nmundo  ", "Hi  \r\n\r\nyou  ").text == "Hola\r\n\r\nmundo  ")
    c.check("U+3000 padding is text, never a break point",
            auto("\u3000Hola\u3000", "x").text == "\u3000Hola\u3000")


def test_edge_samples(c: Checks, m: Metrics, r: InsertResolver, auto: Auto) -> None:
    c.check("trailing breaks follow English", auto("Hola\r\n\r\n", "Hi\r\n").text == "Hola\r\n")
    c.check("only spaces or breaks: kept, with the English trailing breaks",
            auto("   ", "Hi\r\n").text == "   \r\n" and auto("\r\n", "Hi\r\n\r\n").text == "\r\n\r\n")
    c.check("empty Spanish stays empty", auto("", "Hi\r\n").text == "")
    c.check("normalize_breaks leaves breaks inside codes alone",
            wrap.normalize_breaks("a\n<ICON\nX>b\r") == "a\r\n<ICON\nX>b\r\n")
    typo = "Pulsa <ICON\r\nINGAME_KEY_A> ya"
    res = auto(typo)
    c.check("a code with a line break: no exception, kept as written, note",
            res.text == typo and any("code contains a line break" in n for n in res.notes))
    man = wrap.final_text("Hola\nmundo\radiós\r\n", "x\r\n", profile=DIALOGUE, path=DM0000, metrics=m,
                          resolver=r, wrap_mode="manual")
    c.check("manual mode only normalizes breaks",
            man.text == "Hola\r\nmundo\r\nadiós\r\n" and man.lines == ["Hola", "mundo", "adiós"])
    typo_lf = "Pulsa <ICON\nKEY> ya"
    c.check("manual mode with a broken code keeps the code",
            wrap.final_text(typo_lf, "", profile=DIALOGUE, path=DM0000, metrics=m, resolver=r,
                            wrap_mode="manual").text == typo_lf)
    try:
        wrap.final_text("x", "", profile=DIALOGUE, path=DM0000, metrics=m, resolver=r, wrap_mode="fancy")
        rejected = False
    except ValueError:
        rejected = True
    c.check("invalid wrap mode rejected", rejected)


def main(argv: list[str] | None = None) -> int:
    verbose = "-v" in (argv or [])
    c = Checks()
    t0 = time.time()
    ps = PakSet()
    m = Metrics.load(paks=ps)
    test_inserts(c, m, ps)
    test_spanish_tables(c, m, ps)
    r = InsertResolver(m, paks=ps, english_only=True)
    test_samples(c, m, r)
    test_dialogue(c, m, r, ps, verbose)
    test_observed(c, m, r, ps)
    test_observed_spanish(c, m, ps)
    print(f"total {time.time() - t0:.1f}s")
    return c.result()
