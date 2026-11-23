r"""Tests for the SF2 (rr2) insert rules of sfes.inserts and for the game selection by scope.

Run: C:\StarForceES\.venv\Scripts\python.exe -I -X utf8 C:\StarForceES\tools\run.py tests.test_inserts_sf2
  - game selection: scope -> game, scope_of_path, sf3 / all keep the old (rr1, no tables) code path for
    shared codes; for_path / for_scope give the resolver of a file's game with the same settings;
  - SF1 untouched: no phase1 English text uses an SF2-only code, other resolvers hand only those codes
    to the sf2 resolver, shared codes keep the rr1 rules;
  - rr2 tables: item / card / ability / folder ids through the English scripts (card id // 256 -> table,
    ability cost variants), star marks, Geo / Mega Man, player-entered lengths against rr2_subinput_eng;
  - every insert of the sf2 English has a rule (no default) and a positive width;
  - Spanish table texts from sf2 work files (temporary directory), english() ignores them;
  - wrap: name-table limits with the star mark, a dialogue_sf2 'got' box.
English-only resolvers keep the numbers independent of the live work/strings.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from sfes import codes, config, msg23, profiles, wrap
from sfes import scope as scopes
from sfes.inserts import (GAME_OF_SCOPE, SF2_INPUT_LETTERS, SF2_ONLY_COMMANDS, SF2_OPTIONS, SF2_PROFILE_LETTERS,
                          SF2_RULES, InsertResolver, scope_of_path, script_guids)
from sfes.metrics import Metrics
from sfes.pak import PakSet
from sfes.tests.test_metrics import Checks, scratch_dir

RR2 = "natives/stm/text/rr2/"
SUBINPUT = f"{RR2}message/eng/rr2_subinput_eng.msg.23"
CARDNAME = f"{RR2}message/eng/rr2_cardname_eng.msg.23"
DIALOGUE = profiles.PROFILES["dialogue_sf2"]
DIALOGUE_FILE = f"{RR2}message/eng/rr2_st0002_x_eng.msg.23"
STAR = "_CMD_insert_attackstar()"
EPS = 1e-9


def tok(raw: str) -> codes.Token:
    (t,) = codes.tokenize(raw)
    return t


def entry_name(r: InsertResolver, table: str, script_id: int) -> str | None:
    t = r.table(table)
    idx = t.index_of(script_id)
    return None if idx is None else t.names[idx]


def entry_text(r: InsertResolver, table: str, name: str) -> str:
    t = r.table(table)
    return t.texts[t.names.index(name)]


def english_of(ps: PakSet, path: str) -> list[str]:
    msg = msg23.parse(ps.read(path))
    return [msg.get(e, config.LANG_EN) for e in msg.entries]


def asterisks(text: str) -> int:
    """Characters of an input field: the asterisks of its second line (rr2_subinput_eng)."""
    lines = text.split("\r\n")
    return lines[1].count("*") if len(lines) > 1 else 0


# ------------------------------------------------------------------------------------------ games

def test_games(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("game selection")
    games = {s: InsertResolver(m, s, paks=ps, english_only=True).game for s in ("phase1", "sf2", "sf3", "all")}
    c.check("scope -> game", games == {"phase1": "rr1", "sf2": "rr2", "sf3": "rr3", "all": None}, str(games))
    c.check("GAME_OF_SCOPE covers the base scopes", set(GAME_OF_SCOPE) == set(scopes.BASE_SCOPES))
    paths = {p: scope_of_path(p) for p in (CARDNAME, "natives/stm/text/rr1/message/eng/rr1_dm0000_eng.msg.23",
                                           "natives/stm/text/rr3/message/eng/rr3_x_eng.msg.23",
                                           "natives/stm/text/common/localize.msg.23")}
    c.check("scope_of_path: rr2 -> sf2, rr3 -> sf3, rr1 and common -> phase1",
            list(paths.values()) == ["sf2", "phase1", "sf3", "phase1"], str(paths))
    sf2 = InsertResolver(m, "sf2", paks=ps, english_only=True)
    for scope in ("sf3", "all"):
        r = InsertResolver(m, scope, paks=ps, english_only=True)
        defaults = [raw for raw in ("_CMD_insert_itemname(152)", "_CMD_insert_option(0)",
                                    "_CMD_insert_btlwarename(2)", "_CMD_insert_numdisp(0x13,1)")
                    if r.width_em(tok(raw), 0) != (3 * m.digit_em(0) if "numdisp" in raw
                                                   else m.default_insert_em(0))]
        c.check(f"{scope}: shared codes as before (no name tables: default widths)", not defaults, str(defaults))
        moved = [raw for raw in ("_CMD_insert_attackstar()", "_CMD_insert_subaru_name()",
                                 "_CMD_insert_abilityname(9)", "_CMD_insert_subaru_profile(7)")
                 if r.width_em(tok(raw), 0) != sf2.width_em(tok(raw), 0)]
        c.check(f"{scope}: SF2-only codes measured by the sf2 resolver", not moved, str(moved))


def test_for_path(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("rules by file (for_path / for_scope)")
    with scratch_dir() as td:
        work = Path(td)
        r1 = InsertResolver(m, paks=ps, work_dir=work, min_status="done")
        r2 = r1.for_path(DIALOGUE_FILE)
        c.check("for_path: rr2 file -> the sf2 resolver, created once, same settings",
                r2.scope == "sf2" and r2.game == "rr2" and r2 is r1.for_scope("sf2") is r1.for_path(CARDNAME)
                and r2.work.work_dir == work and r2.min_status == "done" and not r2.english_only
                and r2.paks is r1.paks and r2.metrics is r1.metrics)
        c.check("for_path: rr1 and common files -> the phase1 resolver itself (also from its sf2 resolver)",
                r1.for_path("natives/stm/text/rr1/message/eng/rr1_dm0000_eng.msg.23") is r1
                and r1.for_path("natives/stm/text/common/localize.msg.23") is r1 and r2.for_scope("sf2") is r2
                and r2.for_path("natives/stm/text/rr1/message/eng/rr1_dm0000_eng.msg.23") is r1)
        everything = InsertResolver(m, "all", paks=ps, english_only=True)
        per_game = [everything.for_path(p).scope for p in
                    ("natives/stm/text/rr1/message/eng/rr1_dm0000_eng.msg.23", CARDNAME,
                     "natives/stm/text/rr3/message/eng/rr3_x_eng.msg.23")]
        c.check("for_path of an 'all' resolver picks each game's resolver", per_game == ["phase1", "sf2", "sf3"]
                and everything.for_scope("sf2").english_only, str(per_game))
    r1 = InsertResolver(m, paks=ps, english_only=True)
    shared = ("_CMD_insert_option(0)", "_CMD_insert_itemname(152)", "_CMD_insert_btlwarename(3)")
    by_file = [r1.for_path(DIALOGUE_FILE).width_em(tok(raw), 0) for raw in shared]
    own = [InsertResolver(m, "sf2", paks=ps, english_only=True).width_em(tok(raw), 0) for raw in shared]
    c.check("shared codes of an rr2 file: SF2 rules through for_path, rr1 rules in the phase1 resolver",
            by_file == own and by_file[0] == 10 * m.avg_lowercase_em(0)
            and r1.width_em(tok(shared[0]), 0) == m.text_em("Geo", 0), str(by_file))


def test_sf1_untouched(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("SF1 untouched")
    used = Counter()
    for spec in scopes.files("phase1", ps):
        msg = msg23.parse(ps.read(spec.path))
        for e in msg.entries:
            for t in codes.tokenize(msg.get(e, config.LANG_EN)):
                if t.kind == "cmd" and t.prints == "insert":
                    used[t.name] += 1
    clash = sorted(set(used) & SF2_ONLY_COMMANDS)
    c.check("no phase1 English text uses an SF2-only code", not clash, str(clash))
    c.check("SF2-only codes all have an SF2 rule", SF2_ONLY_COMMANDS <= set(SF2_RULES),
            str(sorted(SF2_ONLY_COMMANDS - set(SF2_RULES))))
    r1 = InsertResolver(m, paks=ps, english_only=True)
    r2 = InsertResolver(m, "sf2", paks=ps, english_only=True)
    star = STAR
    c.check("phase1 resolver hands SF2-only codes to an sf2 resolver",
            r1.width_em(tok(star), 0) == r2.width_em(tok(star), 0) == 1.0
            and r1.assumption(tok(star)) == r1.for_scope("sf2").assumption(tok(star))
            and r1.for_scope("sf2").english_only and r2.for_scope("sf2") is r2)
    c.check("phase1 resolver keeps the rr1 rules of shared codes",
            r1.width_em(tok("_CMD_insert_option(0)"), 0) == m.text_em("Geo", 0)
            and r1.assumption(tok("_CMD_insert_btlwarename(2)")).note.startswith("card id 2 -> RR1_CHIPNAME")
            and r1.width_em(tok("_CMD_insert_brother(1)"), 0) == m.default_insert_em(0))
    c.check("english() of a phase1 resolver delegates to an English-only sf2 resolver",
            InsertResolver(m, paks=ps).english().for_scope("sf2").english_only)
    phase1_codes: dict[str, codes.Token] = {}
    for spec in scopes.files("phase1", ps):
        msg = msg23.parse(ps.read(spec.path))
        for e in msg.entries:
            for t in codes.tokenize(msg.get(e, config.LANG_EN)):
                if t.prints == "insert":
                    phase1_codes[t.raw] = t
    moved = [raw for raw, t in phase1_codes.items() if r1._delegate(t) is not None]
    c.check(f"none of the {len(phase1_codes)} distinct phase1 English inserts is handed to sf2", not moved,
            str(moved[:5]))


# ------------------------------------------------------------------------------------------ tables

def test_tables(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("rr2 tables (English)")
    r = InsertResolver(m, "sf2", paks=ps, english_only=True)

    def em(raw: str) -> float:
        return r.width_em(tok(raw), 0)

    c.check("item script covers ids 0-255, 118 entries",
            len(script_guids(ps.read(f"{RR2}userdata/itemname.user.3.en"))) == 256
            and len(r.table("item").names) == 118)
    items = {i: entry_name(r, "item", i) for i in (0, 31, 32, 42, 81, 103, 152, 153, 163)}
    c.check("item ids through the script", items == {
        0: "RR2_ITEMNAME_ENG_0", 31: "RR2_ITEMNAME_ENG_31", 32: None, 42: "RR2_ITEMNAME_ENG_32",
        81: "RR2_ITEMNAME_ENG_59", 103: "RR2_ITEMNAME_ENG_81", 152: "RR2_ITEMNAME_ENG_113",
        153: "RR2_ITEMNAME_ENG_114", 163: None}, str(items))
    c.check("itemname(152) = width of entry 113",
            em("_CMD_insert_itemname(152)") == m.line_em(entry_text(r, "item", "RR2_ITEMNAME_ENG_113"), 0),
            r.describe(tok("_CMD_insert_itemname(152)")))
    widest_item = max(m.line_em(t, 0) for t in r.table("item").texts)
    c.check("itemnamefree and an id without entry = widest item",
            em("_CMD_insert_itemnamefree(0x01)") == em("_CMD_insert_itemname(32)") == widest_item)

    cards = {}
    for raw in ("_CMD_insert_btlwarename(2)", "_CMD_insert_btlwarename(245)", "_CMD_insert_btlwarename(370)",
                "_CMD_insert_btlwarename(380)", "_CMD_insert_btlwarename(512)"):
        cards[raw[24:-1]] = r.assumption(tok(raw)).note.split(" = ")[0]
    c.check("card id // 256 selects the table, % 256 the script id", cards == {
        "2": "card id 2 -> card 2 -> RR2_CARDNAME_ENG_2",
        "245": "card id 245 -> card 245 -> RR2_CARDNAME_ENG_207",
        "370": "card id 370 -> card2 114 -> RR2_CARDNAME2_ENG_50",
        "380": "card id 380 -> card2 124 -> RR2_CARDNAME2_ENG_60",
        "512": "card id 512 -> card3 0 -> RR2_CARDNAME3_ENG_0"}, str(cards))
    c.check("Legend Card entries of cardname2 are legendcombo_name(380-386)",
            [entry_text(r, "card2", f"RR2_CARDNAME2_ENG_{60 + k}") for k in range(7)]
            == [f"_CMD_insert_legendcombo_name({380 + k})" for k in range(7)])
    star = em("_CMD_insert_attackstar()")
    stars = [entry_text(r, "card3", f"RR2_CARDNAME3_ENG_{k}") for k in (27, 28, 29)]
    c.check("attackstar = one star glyph (rr2_cardname3 ids 251-253)",
            star == 1.0 and stars == [f"<REPLACE  RR2_STAR{k}>" for k in (1, 2, 3)], str(stars))
    card2 = entry_text(r, "card", "RR2_CARDNAME_ENG_2")
    c.check("btlwarename(2) = entry 2: its name + the star mark (1 em)",
            card2.endswith(STAR) and STAR not in card2[:-len(STAR)]
            and abs(em("_CMD_insert_btlwarename(2)") - (m.text_em(card2[:-len(STAR)], 0) + 1.0)) < EPS)
    c.check("btlwarename(512) = Brother card: a handle name (nested insert_brother)",
            abs(em("_CMD_insert_btlwarename(512)") - 10 * m.avg_lowercase_em(0)) < EPS)
    widest_card = max(m.line_em(t, 0, r) for n in ("card", "card2", "card3") for t in r.table(n).texts)
    c.check("btlwarenamefree and card 510 (no entry) = widest of the three card tables",
            em("_CMD_insert_btlwarenamefree(0)") == em("_CMD_insert_btlwarename(510)") == widest_card)
    download = [entry_text(r, "card", f"RR2_CARDNAME_ENG_{k}") for k in (208, 209)]
    c.check("downloaded Mega / Giga cards 246 / 247 (entry '????') = widest card name",
            download == ["????", "????"]
            and em("_CMD_insert_btlwarename(246)") == em("_CMD_insert_btlwarename(247)") == widest_card
            and "downloaded card" in r.assumption(tok("_CMD_insert_btlwarename(246)")).note, str(download))
    c.check("subaru_name = the wider of 'Geo' and 'Mega Man'",
            em("_CMD_insert_subaru_name()") == max(m.text_em("Geo", 0), m.text_em("Mega Man", 0)))

    abilities = {k: r.ability_index(k) for k in (1, 2, 4, 5, 9, 48, 49, 53, 58, 64, 65, 66, 70, 73, 74, 75, 79,
                                                 101, 102, 104, 105)}
    c.check("ability ids: own name, else the closest lower id of the same cost run", abilities == {
        1: 0, 2: 0, 4: 0, 5: 1, 9: 2, 48: 11, 49: 12, 53: 16, 58: 21, 64: 27, 65: None, 66: None, 70: 28,
        73: 28, 74: 29, 75: 29, 79: 30, 101: 35, 102: 36, 104: 38, 105: None}, str(abilities))
    cost = 3 * m.digit_em(0)
    ability2 = entry_text(r, "ability", "RR2_ABILITYNAME_ENG_2")
    c.check("abilityname(9) = entry 2 + '/' + 3 digits; nocost = the name",
            abs(em("_CMD_insert_abilityname(9)") - (m.text_em(ability2 + "/", 0) + cost)) < EPS
            and em("_CMD_insert_abilityname_nocost(9)") == m.text_em(ability2, 0))
    widest_ability = max(m.text_em(t, 0) for t in r.table("ability").texts)
    widest_cost = max(m.text_em(t + "/", 0) for t in r.table("ability").texts) + cost
    c.check("abilityname_free = widest name + '/' + cost; nocost_free = widest name",
            abs(em("_CMD_insert_abilityname_free(0)") - widest_cost) < EPS
            and em("_CMD_insert_abilityname_nocost_free(0)") == widest_ability)
    c.check("ability id without entry = widest name + cost",
            abs(em("_CMD_insert_abilityname(66)") - widest_cost) < EPS)
    c.check("folder_name(65) = script id 1 (id & 0x3F) = entry 1, 132 (no entry) = widest folder",
            entry_name(r, "folder", 1) == "RR2_DECKNAME_ENG_1"
            and em("_CMD_insert_folder_name(65)") == m.text_em(entry_text(r, "folder", "RR2_DECKNAME_ENG_1"), 0)
            and em("_CMD_insert_folder_name(132)") == max(m.text_em(t, 0) for t in r.table("folder").texts))
    c.check("emnamefree = widest virus name (rr2_emname1)",
            em("_CMD_insert_emnamefree(0)") == max(m.line_em(t, 0) for t in r.table("enemy").texts))


def test_input_lengths(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("player-entered texts")
    en = english_of(ps, SUBINPUT)
    fields = {"handle": 0, "secret": 1, "message": 2, "combo": 3, "subject": 6}
    found = {k: asterisks(en[i]) for k, i in fields.items()}
    c.check("input lengths = asterisks of rr2_subinput_eng", found == SF2_INPUT_LETTERS, str(found))
    c.check("Auto Brother handle and Folder Name fields", asterisks(en[20]) == 10 and asterisks(en[5]) == 8)
    # subinput order: food, celebrity, TV, hometown, interests, dislikes, words, treasure, feature (10-18)
    by_profile = {0: 10, 1: 13, 2: 14, 3: 12, 5: 11, 6: 15, 7: 16, 8: 17, 9: 18}
    lengths = {n: asterisks(en[i]) for n, i in by_profile.items()}
    c.check("profile word lengths = their input fields",
            all(SF2_PROFILE_LETTERS[n] == k for n, k in lengths.items()), str(lengths))
    c.check("no English input field for the catchphrase (entry 9 blank); 3 = Japanese limit",
            asterisks(en[9]) == 0 and SF2_PROFILE_LETTERS[4] == 3)
    r = InsertResolver(m, "sf2", paks=ps, english_only=True)
    low = m.avg_lowercase_em(0)

    def letters(raw: str) -> float:
        return r.width_em(tok(raw), 0) / low

    got = {raw: round(letters(raw), 9) for raw in (
        "_CMD_insert_option(0)", "_CMD_insert_option(1)", "_CMD_insert_option(2)", "_CMD_insert_option(3)",
        "_CMD_insert_brother(0)", "_CMD_insert_subplayer_plname()", "_CMD_insert_subaru_profile_name()",
        "_CMD_insert_autobrother_name()", "_CMD_insert_brother_profile_name(10)",
        "_CMD_insert_brother_profile_name(5)", "_CMD_insert_brother_profile_name(7)", "_CMD_insert_bestcombo()",
        "_CMD_insert_legendcombo_name(380)", "_CMD_insert_subaru_profile(7)", "_CMD_insert_brother_profile(4)",
        "_CMD_insert_wordbox_profile(2)", "_CMD_insert_brother_kotoba_free(4,0,0)",
        "_CMD_insert_option_profile(0)", "_CMD_insert_subaru_profile(12)")}
    want = {"_CMD_insert_option(0)": 10, "_CMD_insert_option(1)": 12, "_CMD_insert_option(2)": 17,
            "_CMD_insert_option(3)": 17, "_CMD_insert_brother(0)": 10, "_CMD_insert_subplayer_plname()": 10,
            "_CMD_insert_subaru_profile_name()": 10, "_CMD_insert_autobrother_name()": 10,
            "_CMD_insert_brother_profile_name(10)": 10, "_CMD_insert_brother_profile_name(5)": 10,
            "_CMD_insert_brother_profile_name(7)": 17, "_CMD_insert_bestcombo()": 8,
            "_CMD_insert_legendcombo_name(380)": 8, "_CMD_insert_subaru_profile(7)": 17,
            "_CMD_insert_brother_profile(4)": 3, "_CMD_insert_wordbox_profile(2)": 12,
            "_CMD_insert_brother_kotoba_free(4,0,0)": 3, "_CMD_insert_option_profile(0)": 17,
            "_CMD_insert_subaru_profile(12)": 17}
    c.check("player-entered inserts = N average lowercase letters", got == want, str(got))
    c.check("SF2_OPTIONS letters", {n: k for n, (_, k) in SF2_OPTIONS.items()} == {0: 10, 1: 12, 2: 17, 3: 17})
    c.check("option(4) and up = default", r.width_em(tok("_CMD_insert_option(4)"), 0) == m.default_insert_em(0))
    c.check("numdisp as in SF1 (low nibble = minLength)",
            r.width_em(tok("_CMD_insert_numdisp(0x13,1)"), 0) == 3 * m.digit_em(0)
            and r.width_em(tok("_CMD_insert_numdisp(2,1)"), 0) == 2 * m.digit_em(0))


def test_all_sf2_inserts(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("every insert of the sf2 English")
    r = InsertResolver(m, "sf2", paks=ps, english_only=True)
    rules: Counter = Counter()
    names: Counter = Counter()
    bad = []
    for spec in scopes.files("sf2", ps):
        msg = msg23.parse(ps.read(spec.path))
        for e in msg.entries:
            for t in codes.tokenize(msg.get(e, config.LANG_EN)):
                if t.prints != "insert":
                    continue
                a = r.assumption(t)
                rules[a.rule] += 1
                names[t.name] += 1
                if a.default or r.width_em(t, 0) <= 0 or "not found" in a.note:
                    bad.append(r.describe(t))
    print("  info: sf2 English inserts by rule:", dict(sorted(rules.items())))
    c.check("every sf2 English insert has an SF2 rule (no default), a positive width, REF targets exist",
            not bad, str(bad[:5]))
    c.check("26 command codes seen, each with a rule",
            len([n for n in names if n.startswith("insert_")]) == 26
            and all(n in SF2_RULES or n == "insert_numdisp" for n in names if n.startswith("insert_")),
            str(sorted(names)))
    c.check("counts of the task list", (names["insert_itemname"], names["insert_attackstar"],
                                        names["insert_subaru_name"], names["insert_option"]) == (534, 125, 182, 81))


# ------------------------------------------------------------------------------------- work files

def write_work_file(work: Path, stem: str, entries: list[dict]) -> None:
    p = work / "sf2" / "sf2_eng" / f"{stem}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": 1, "entries": entries}, ensure_ascii=False, indent=1), encoding="utf-8")


def test_spanish(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("rr2 tables (Spanish from work files)")
    english = InsertResolver(m, "sf2", paks=ps, english_only=True)
    with scratch_dir() as td:
        work = Path(td)
        item, card, ability = "Objeto de prueba 10", "Carta ñandú", "Habilidad ficticia"
        write_work_file(work, "rr2_itemname_eng", [
            {"name": "RR2_ITEMNAME_ENG_113", "es": item, "status": "draft"},
            {"name": "RR2_ITEMNAME_ENG_114", "es": "Objeto de prueba 20", "status": "todo"}])
        write_work_file(work, "rr2_cardname_eng", [
            {"name": "RR2_CARDNAME_ENG_2", "es": card + STAR, "status": "done"}])
        write_work_file(work, "rr2_abilityname_eng", [
            {"name": "RR2_ABILITYNAME_ENG_2", "es": ability, "status": "reviewed"}])
        r = InsertResolver(m, "sf2", work_dir=work, paks=ps)
        strict = InsertResolver(m, "sf2", work_dir=work, paks=ps, min_status="reviewed")
        phase1 = InsertResolver(m, "phase1", work_dir=work, paks=ps)

        def em(res: InsertResolver, raw: str) -> float:
            return res.width_em(tok(raw), 0)

        item114 = entry_text(english, "item", "RR2_ITEMNAME_ENG_114")
        c.check("translated item name used", em(r, "_CMD_insert_itemname(152)") == m.text_em(item, 0))
        c.check("status todo keeps English", em(r, "_CMD_insert_itemname(153)") == m.text_em(item114, 0))
        c.check("translated card name keeps its star mark",
                abs(em(r, "_CMD_insert_btlwarename(2)") - (m.text_em(card, 0) + 1.0)) < EPS)
        c.check("ability: Spanish name + cost, nocost = Spanish name",
                abs(em(r, "_CMD_insert_abilityname(10)") - (m.text_em(ability + "/", 0) + 3 * m.digit_em(0))) < EPS
                and em(r, "_CMD_insert_abilityname_nocost(9)") == m.text_em(ability, 0))
        c.check("min_status reviewed: draft item stays English, reviewed ability is Spanish",
                em(strict, "_CMD_insert_itemname(152)") == em(english, "_CMD_insert_itemname(152)")
                and em(strict, "_CMD_insert_abilityname_nocost(9)") == m.text_em(ability, 0))
        c.check("english() ignores the work files",
                em(r.english(), "_CMD_insert_itemname(152)") == em(english, "_CMD_insert_itemname(152)")
                != m.text_em(item, 0))
        c.check("a phase1 resolver's sf2 delegate and for_path read the same work files",
                em(phase1, "_CMD_insert_abilityname_nocost(9)") == m.text_em(ability, 0)
                and em(phase1.for_path(CARDNAME), "_CMD_insert_itemname(152)") == m.text_em(item, 0))


# ------------------------------------------------------------------------------------------- wrap

def test_wrap(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("wrap with SF2 inserts")
    r = InsertResolver(m, "sf2", paks=ps, english_only=True)
    lim = wrap.file_limits(CARDNAME, m, r)
    slot = lim.slot
    want = max(m.line_em(t, slot, r) for t in english_of(ps, CARDNAME))
    with_default = max(m.line_em(t.replace(STAR, ""), slot) + m.default_insert_em(slot)
                       if STAR in t else m.line_em(t, slot) for t in english_of(ps, CARDNAME))
    c.check("rr2_cardname limit = widest English card name with its star mark (1 em)",
            abs(lim.max_px - want) < EPS and lim.max_px < with_default,
            f"{lim.max_px:.2f} em (default star would give {with_default:.2f} em)")
    c.check("run.py wrap (phase1 resolver) gets the same rr2_cardname limit",
            abs(wrap.file_limits(CARDNAME, m, InsertResolver(m, paks=ps, english_only=True)).max_px - lim.max_px)
            < EPS)
    es = '¡_CMD_insert_subaru_name() obtuvo la habilidad:\r\n"_CMD_insert_abilityname(73)"!'
    res = wrap.final_text(es, "x", profile=DIALOGUE, path=DIALOGUE_FILE, metrics=m, resolver=r, wrap_mode="auto")
    first = (m.text_em("¡", 0) + m.text_em("Mega Man", 0) + m.text_em(" obtuvo la habilidad:", 0)) * DIALOGUE.size
    ability28 = entry_text(r, "ability", "RR2_ABILITYNAME_ENG_28")      # ability ids 70-73
    second = (m.text_em(f'"{ability28}/"!', 0) + 3 * m.digit_em(0)) * DIALOGUE.size
    c.check("dialogue_sf2 'got' box: the line after ':' is kept, widths use the SF2 rules",
            res.lines == ['¡_CMD_insert_subaru_name() obtuvo la habilidad:', '"_CMD_insert_abilityname(73)"!']
            and abs(res.widths_px[0] - first) < 1e-6 and abs(res.widths_px[1] - second) < 1e-6
            and not res.overflow_width and not res.overflow_lines, f"{res.lines} {res.widths_px}")


def main(argv: list[str] | None = None) -> int:
    c = Checks()
    t0 = time.time()
    ps = PakSet()
    m = Metrics.load(paks=ps)
    test_games(c, m, ps)
    test_for_path(c, m, ps)
    test_sf1_untouched(c, m, ps)
    test_tables(c, m, ps)
    test_input_lengths(c, m, ps)
    test_all_sf2_inserts(c, m, ps)
    test_spanish(c, m, ps)
    test_wrap(c, m, ps)
    print(f"total {time.time() - t0:.1f}s")
    return c.result()
