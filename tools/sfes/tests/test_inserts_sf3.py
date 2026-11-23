r"""Tests for the SF3 (rr3) insert rules of sfes.inserts.

Run: C:\StarForceES\.venv\Scripts\python.exe -I -X utf8 C:\StarForceES\tools\run.py tests.test_inserts_sf3
  - game selection: SF3 rules only for the rr3 game; SF3 code names are used by no SF1 / SF2 text (any
    language); other resolvers hand SF3 codes to the sf3 resolver and keep their own rules for every other
    code (no phase1 or sf2 English insert is handed over); codes SF3 does not use keep the old sf3 widths;
  - rr3 tables: ids through the English scripts, ids without entry = widest of the table, the player name,
    ability cost, numbers, string slots, player-entered lengths against rr3_subinput_v_eng;
  - every insert of the sf3 English has an SF3 rule (no default) and a positive width;
  - Spanish table texts from sf3 work files (temporary directory), english() ignores them;
  - codes are codes: batch apply refuses and validate reports a Spanish text that drops or adds an SF3
    code (on a scratch copy of one work file);
  - wrap: rr3 name-table limit, a dialogue_sf3 'got' box.
English-only resolvers keep the numbers independent of the live work/strings.
"""
from __future__ import annotations

import json
import shutil
import time
from collections import Counter
from pathlib import Path

from sfes import batch, codes, config, msg23, profiles, wrap
from sfes import scope as scopes
from sfes import validate as validation
from sfes.inserts import (SF3_ID_COMMANDS, SF3_INPUT_LETTERS, SF3_PROFILE_LETTERS, SF3_STRING_TABLES,
                          SF3_TABLES, InsertResolver, script_guids, sf3_rule_name)
from sfes.metrics import Metrics
from sfes.pak import PakSet
from sfes.tests.test_metrics import Checks, scratch_dir

RR3 = "natives/stm/text/rr3/"
SUBINPUT = f"{RR3}message/eng/rr3_subinput_v_eng.msg.23"
PROFILEEDIT = f"{RR3}message/eng/rr3_subprofileedit_v_eng.msg.23"
CARDNAME = f"{RR3}message/eng/rr3_cardname_eng.msg.23"
DIALOGUE = profiles.PROFILES["dialogue_sf3"]
DIALOGUE_FILE = f"{RR3}message/eng/rr3_chk_st000d_eng.msg.23"
RR1_FILE = "natives/stm/text/rr1/message/eng/rr1_dm0000_eng.msg.23"
RR2_FILE = "natives/stm/text/rr2/message/eng/rr2_cardname_eng.msg.23"
SF3_WORK = config.STRINGS / "sf3" / "sf3_eng"
EPS = 1e-9


def tok(raw: str) -> codes.Token:
    (t,) = codes.tokenize(raw)
    return t


def english_of(ps: PakSet, path: str) -> list[str]:
    msg = msg23.parse(ps.read(path))
    return [msg.get(e, config.LANG_EN) for e in msg.entries]


def asterisks(text: str) -> int:
    """Characters of an input field: the asterisks of its second line (rr3_subinput_v_eng)."""
    lines = text.split("\r\n")
    return lines[1].count("*") if len(lines) > 1 else 0


def entry_text(r: InsertResolver, table: str, index: int) -> str:
    return r.table(table).texts[index]


def code_names(ps: PakSet, scope: str, all_languages: bool) -> Counter:
    names: Counter = Counter()
    for spec in scopes.files(scope, ps):
        msg = msg23.parse(ps.read(spec.path))
        langs = msg.languages if all_languages else [config.LANG_EN]
        for e in msg.entries:
            for lang in langs:
                for t in codes.tokenize(msg.get(e, lang)):
                    if t.kind == "cmd":
                        names[t.name] += 1
    return names


# ------------------------------------------------------------------------------------------ games

def test_games(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("game selection")
    sf3 = InsertResolver(m, "sf3", paks=ps, english_only=True)
    c.check("sf3 resolver: game rr3, every SF3 table present",
            sf3.game == "rr3" and all(sf3.table(n) is not None for n in SF3_TABLES),
            str([n for n in SF3_TABLES if sf3.table(n) is None]))
    for scope in ("phase1", "sf2"):
        clash = sorted(n for n in code_names(ps, scope, True) if sf3_rule_name(n))
        c.check(f"no {scope} text (any language) uses an SF3 code name", not clash, str(clash))
    for scope in ("phase1", "sf2", "all"):
        r = InsertResolver(m, scope, paks=ps, english_only=True)
        moved = [raw for raw in ("_CMD_keyname(36)", "_CMD_str1()", "_CMD_insert_pl_name()", "_CMD_num_l2(6)",
                                 "_CMD_pro_text_05()", "_CMD_abliname(42)")
                 if r.width_em(tok(raw), 0) != sf3.width_em(tok(raw), 0)
                 or r.assumption(tok(raw)) != sf3.assumption(tok(raw))]
        c.check(f"{scope}: SF3 codes measured by the sf3 resolver", not moved, str(moved))
    r1 = InsertResolver(m, paks=ps, english_only=True)
    r2 = r1.for_scope("sf2")
    handed = []
    for scope, r in (("phase1", r1), ("sf2", r2)):
        for spec in scopes.files(scope, ps):
            msg = msg23.parse(ps.read(spec.path))
            for e in msg.entries:
                for t in codes.tokenize(msg.get(e, config.LANG_EN)):
                    if t.prints == "insert" and r._delegate(t) is r.for_scope("sf3"):
                        handed.append(t.raw)
    c.check("no phase1 or sf2 English insert is handed to sf3", not handed, str(handed[:5]))
    shared = [raw for raw in ("_CMD_insert_itemname(152)", "_CMD_insert_option(0)", "_CMD_insert_btlwarename(2)",
                              "_CMD_insert_brother(1)")
              if sf3.width_em(tok(raw), 0) != m.default_insert_em(0)]
    c.check("sf3: codes SF3 does not use keep the old widths (default, no name tables)", not shared, str(shared))
    c.check("sf3: numdisp and {0} as before",
            sf3.width_em(tok("_CMD_insert_numdisp(0x13,1)"), 0) == 3 * m.digit_em(0)
            and sf3.width_em(tok("{0}"), 0) == 3 * m.digit_em(0))
    c.check("for_path: rr3 file -> the sf3 resolver, rr1 / rr2 files -> their own",
            r1.for_path(DIALOGUE_FILE).scope == "sf3" and r1.for_path(DIALOGUE_FILE) is r1.for_scope("sf3")
            and r1.for_path(RR1_FILE) is r1 and r1.for_path(RR2_FILE) is r2)
    c.check("SF2 codes from the sf3 resolver go to sf2",
            sf3.width_em(tok("_CMD_insert_attackstar()"), 0) == 1.0)


# ------------------------------------------------------------------------------------------ tables

def test_tables(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("rr3 tables (English)")
    r = InsertResolver(m, "sf3", paks=ps, english_only=True)

    def em(raw: str) -> float:
        return r.width_em(tok(raw), 0)

    sizes = {}
    for name, spec in SF3_TABLES.items():
        n_entries = len(msg23.parse(ps.read(spec.msg)).entries)
        n_ids = len(script_guids(ps.read(spec.script)))
        sizes[name] = (n_entries, n_ids)
    c.check("every SF3 table has an English script covering its entries",
            all(ids >= n for n, ids in sizes.values()), str(sizes))
    straight = [n for n in SF3_TABLES if n != "teamname"
                and any(r.table(n).index_of(k) != k for k in range(len(r.table(n).names)))]
    c.check("name tables used by id codes: game id = entry index (checked through the scripts)",
            not straight, str(straight))
    picks = {"_CMD_keyname(36)": ("keyitemname", 36), "_CMD_abliname(42)": ("abilityname", 42),
             "_CMD_popname(4)": ("popupname", 4), "_CMD_cardname(1)": ("cardname", 1),
             "_CMD_emname(144)": ("emname", 144), "_CMD_areaname(0)": ("areaname", 0),
             "_CMD_nickname(6)": ("nickname", 6), "_CMD_mannname(64)": ("manname", 64),
             "_CMD_subname(3)": ("subcardname", 3), "_CMD_weaponname(8)": ("weaponname", 8),
             "_CMD_fldrname(2)": ("foldername", 2), "_CMD_pwrname(1)": ("powerupname", 1),
             "_CMD_keyword(9)": ("keyword", 9)}
    wrong = [raw for raw, (table, k) in picks.items()
             if abs(em(raw) - m.line_em(entry_text(r, table, k), 0, r)) > EPS
             or r.assumption(tok(raw)).rule != "table_id"]
    c.check("id codes = width of that table entry", not wrong, str(wrong))
    c.check("every id command has a table", set(SF3_ID_COMMANDS.values()) <= set(SF3_TABLES))
    widest_card = max(m.line_em(t, 0, r) for t in r.table("cardname").texts)
    c.check("cardname id without entry = widest card name",
            abs(em("_CMD_cardname(9999)") - widest_card) < EPS
            and "has no entry" in r.assumption(tok("_CMD_cardname(9999)")).note)
    nick = r.table("nickname")
    c.check("insert_pl_name = the wider of rr3_nickname 0 and 1 (Geo / Mega Man)",
            em("_CMD_insert_pl_name()") == max(m.text_em(nick.texts[0], 0), m.text_em(nick.texts[1], 0))
            and nick.names[:2] == ["RR3_NICKNAME_ENG_0", "RR3_NICKNAME_ENG_1"])
    c.check("ablity_lv(n) = '/' + 3 digits",
            abs(em("_CMD_ablity_lv(2)") - (m.text_em("/", 0) + 3 * m.digit_em(0))) < EPS)
    digits = {raw: round(em(raw) / m.digit_em(0), 9) for raw in (
        "_CMD_num_l1(2)", "_CMD_num_l2(6)", "_CMD_num_r4(3)", "_CMD_num_0r1(1)", "_CMD_num_0r3(0x2)",
        "_CMD_key_item_num()")}
    c.check("numbers: the argument is the digit count; key_item_num 2 digits", digits == {
        "_CMD_num_l1(2)": 2, "_CMD_num_l2(6)": 6, "_CMD_num_r4(3)": 3, "_CMD_num_0r1(1)": 1,
        "_CMD_num_0r3(0x2)": 2, "_CMD_key_item_num()": 2}, str(digits))
    c.check("a number without digit count = default", em("_CMD_num_l1(x)") == m.default_insert_em(0))
    for raw, tables in (("_CMD_sservername()", ("sservername",)), ("_CMD_noisename_r1()", ("noise_chg_name",)),
                        ("_CMD_wout_area_name()", ("areaname", "g_areaname"))):
        want = max(m.line_em(t, 0, r) for n in tables for t in r.table(n).texts)
        c.check(f"{raw} = widest of {'/'.join(tables)}", abs(em(raw) - want) < EPS, f"{em(raw):.2f} em")
    names = max(m.line_em(t, 0, r) for n in SF3_STRING_TABLES for t in r.table(n).texts)
    slot = max(names, 10 * m.avg_lowercase_em(0), 12 * m.digit_em(0))
    c.check("str1-str4 = the widest of the string tables, a Codename (10) and a Friend Code (12 digits)",
            all(abs(em(f"_CMD_str{k}()") - slot) < EPS for k in (1, 2, 3, 4))
            and r.assumption(tok("_CMD_str1()")).rule == "slot", f"{slot:.2f} em")


def test_input_lengths(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("player-entered texts")
    en = english_of(ps, SUBINPUT)
    fields = {"codename": 0, "message": 2, "folder name": 3, "friend code": 4, "team name": 6}
    found = {k: asterisks(en[i]) for k, i in fields.items()}
    c.check("input lengths = asterisks of rr3_subinput_v_eng", found == SF3_INPUT_LETTERS, str(found))
    profile = [asterisks(en[i]) for i in range(8, 16)]
    c.check("profile word lengths = input fields 8-15", tuple(profile) == SF3_PROFILE_LETTERS, str(profile))
    labels = [t.strip() for t in english_of(ps, PROFILEEDIT)[1:9]]
    c.check("profile word order = rr3_subprofileedit_v_eng 1-8 (8 fields)", len(labels) == 8 and all(labels),
            str(labels))
    r = InsertResolver(m, "sf3", paks=ps, english_only=True)
    low = m.avg_lowercase_em(0)
    got = {raw: round(r.width_em(tok(raw), 0) / low, 9) for raw in (
        "_CMD_pro_text_00()", "_CMD_pro_text_02()", "_CMD_pro_text_03()", "_CMD_pro_text_04()",
        "_CMD_pro_text_05()", "_CMD_pro_text_06()", "_CMD_pro_text_07()", "_CMD_pro_text_my_01()",
        "_CMD_pro_text_my_name()", "_CMD_pro_name_12()", "_CMD_pro_res_r1()", "_CMD_pro_res_r2()",
        "_CMD_pro_text_09()", "_CMD_pro_res_r7()")}
    want = {"_CMD_pro_text_00()": 10, "_CMD_pro_text_02()": 11, "_CMD_pro_text_03()": 8, "_CMD_pro_text_04()": 8,
            "_CMD_pro_text_05()": 16, "_CMD_pro_text_06()": 8, "_CMD_pro_text_07()": 15,
            "_CMD_pro_text_my_01()": 11, "_CMD_pro_text_my_name()": 10, "_CMD_pro_name_12()": 10,
            "_CMD_pro_res_r1()": 8, "_CMD_pro_res_r2()": 8, "_CMD_pro_text_09()": 16, "_CMD_pro_res_r7()": 16}
    c.check("player-entered inserts = N average lowercase letters", got == want, str(got))


def test_all_sf3_inserts(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("every insert of the sf3 English")
    r = InsertResolver(m, "sf3", paks=ps, english_only=True)
    rules: Counter = Counter()
    names: Counter = Counter()
    bad = []
    for spec in scopes.files("sf3", ps):
        msg = msg23.parse(ps.read(spec.path))
        for e in msg.entries:
            for t in codes.tokenize(msg.get(e, config.LANG_EN)):
                if t.prints != "insert":
                    continue
                a = r.assumption(t)
                rules[a.rule] += 1
                names[t.name] += 1
                if a.default or r.width_em(t, 0) <= 0 or "not found" in a.note or "no entry" in a.note:
                    bad.append(r.describe(t))
    print("  info: sf3 English inserts by rule:", dict(sorted(rules.items())))
    c.check("every sf3 English insert has an SF3 rule (no default, no unresolved id), a positive width",
            not bad, str(bad[:5]))
    commands = sorted(n for n in names if n not in ("REF", "{0}"))
    c.check("47 command codes seen, each with an SF3 rule",
            len(commands) == 47 and all(sf3_rule_name(n) for n in commands), f"{len(commands)}: {commands}")
    c.check("counts of the task list", (names["areaname"], names["insert_pl_name"], names["str1"],
                                        names["nickname"]) == (521, 89, 71, 79),
            str((names["areaname"], names["insert_pl_name"], names["str1"], names["nickname"])))
    nonprint = code_names(ps, "sf3", False)
    c.check("SF3 pacing / portrait codes print nothing",
            all(tok(f"_CMD_{n}()").prints == "none" for n in ("face_wink", "face_none", "rwait1", "rwait4",
                                                               "page_wait_key"))
            and tok("_CMD_wait(10)").prints == "none" and nonprint["face_wink"] > 0)


# ------------------------------------------------------------------------------------- work files

def write_work_file(work: Path, stem: str, entries: list[dict]) -> None:
    p = work / "sf3" / "sf3_eng" / f"{stem}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": 1, "entries": entries}, ensure_ascii=False, indent=1), encoding="utf-8")


def test_spanish(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("rr3 tables (Spanish from work files)")
    english = InsertResolver(m, "sf3", paks=ps, english_only=True)
    with scratch_dir() as td:
        work = Path(td)
        key, nick, card = "Objeto clave de prueba", "Cazador ficticio", "Carta ñandú"
        write_work_file(work, "rr3_keyitemname_eng", [
            {"name": "RR3_KEYITEMNAME_ENG_36", "es": key, "status": "draft"},
            {"name": "RR3_KEYITEMNAME_ENG_37", "es": "Coco de prueba", "status": "todo"}])
        write_work_file(work, "rr3_nickname_eng", [
            {"name": "RR3_NICKNAME_ENG_6", "es": nick, "status": "reviewed"},
            {"name": "RR3_NICKNAME_ENG_1", "es": "Mega Man Mega Man Mega Man", "status": "done"}])
        write_work_file(work, "rr3_cardname_eng", [
            {"name": "RR3_CARDNAME_ENG_1", "es": card, "status": "done"}])
        r = InsertResolver(m, "sf3", work_dir=work, paks=ps)
        strict = InsertResolver(m, "sf3", work_dir=work, paks=ps, min_status="reviewed")
        phase1 = InsertResolver(m, "phase1", work_dir=work, paks=ps)

        def em(res: InsertResolver, raw: str) -> float:
            return res.width_em(tok(raw), 0)

        c.check("translated key item name used", em(r, "_CMD_keyname(36)") == m.text_em(key, 0))
        c.check("status todo keeps English",
                em(r, "_CMD_keyname(37)") == m.text_em(entry_text(english, "keyitemname", 37), 0))
        c.check("insert_pl_name follows the translated nickname 1",
                em(r, "_CMD_insert_pl_name()") == m.text_em("Mega Man Mega Man Mega Man", 0))
        c.check("string slots grow with a translated table name when it is the widest",
                em(r, "_CMD_str1()") >= em(english, "_CMD_str1()"))
        c.check("min_status reviewed: draft key item stays English, reviewed nickname is Spanish",
                em(strict, "_CMD_keyname(36)") == em(english, "_CMD_keyname(36)")
                and em(strict, "_CMD_nickname(6)") == m.text_em(nick, 0))
        c.check("english() ignores the work files",
                em(r.english(), "_CMD_cardname(1)") == em(english, "_CMD_cardname(1)") != m.text_em(card, 0))
        c.check("a phase1 resolver's sf3 delegate reads the same work files",
                em(phase1, "_CMD_cardname(1)") == m.text_em(card, 0)
                and em(phase1.for_path(DIALOGUE_FILE), "_CMD_nickname(6)") == m.text_em(nick, 0))


def test_codes_kept(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("SF3 codes must be kept (apply and validate, scratch copy)")
    samples = {"rr3_field_ibox_eng": ("_CMD_subname(3)", "_CMD_insert_pl_name()", "_CMD_num_l2(6)", "_CMD_str2()"),
               "rr3_field_humor_word_eng": ("_CMD_pro_text_my_01()", "_CMD_pro_text_06()", "_CMD_pro_name_12()"),
               "rr3_brothernetapply_w_eng": ("_CMD_rwait2()", "_CMD_noisename_r1()", "_CMD_num_l1(2)")}
    with scratch_dir() as td:
        strings = Path(td)
        rows, targets = [], {}
        for stem, raws in samples.items():
            dst = strings / "sf3" / "sf3_eng" / f"{stem}.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SF3_WORK / f"{stem}.json", dst)
            doc = json.loads(dst.read_text(encoding="utf-8"))
            # the scratch copy starts untranslated, so the check does not depend on translation progress
            for x in doc["entries"]:
                if x["status"] in ("draft", "done"):
                    x.update(es="", status="todo")
            dst.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            for raw in raws:
                e = next(x for x in doc["entries"] if raw in x["en"] and x["status"] == "todo")
                targets[raw] = e["id"]
                rows.append({"id": e["id"], "es": e["en"].replace(raw, "", 1), "status": "draft"})
                rows.append({"id": e["id"], "es": e["en"].replace(raw, raw + raw, 1), "status": "draft"})
        before = {p: p.read_bytes() for p in strings.rglob("*.json")}
        res = batch.apply_rows(rows, scope="sf3", strings_dir=strings)
        refused = Counter(x["id"] for x in res["refused"] if "codes differ" in x["reason"])
        c.check(f"apply refuses all {len(rows)} rows that drop or repeat an SF3 code (nothing written)",
                res["accepted"] == 0 and sum(refused.values()) == len(rows)
                and before == {p: p.read_bytes() for p in strings.rglob("*.json")}, str(res["refused"][:2]))
        good = dict(rows[0], es=json.loads((strings / "sf3" / "sf3_eng" / "rr3_field_ibox_eng.json")
                                           .read_text(encoding="utf-8"))["entries"][1]["en"])
        good["id"] = json.loads((strings / "sf3" / "sf3_eng" / "rr3_field_ibox_eng.json")
                                .read_text(encoding="utf-8"))["entries"][1]["id"]
        ok = batch.apply_rows([good], scope="sf3", strings_dir=strings, dry_run=True)
        c.check("a row with the same codes is accepted (dry run)", ok["accepted"] == 1 and not ok["refused"])
        # write a dropped code straight into the copy: the validator must report it
        p = strings / "sf3" / "sf3_eng" / "rr3_field_ibox_eng.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        e = next(x for x in doc["entries"] if x["id"] == targets["_CMD_subname(3)"])
        e.update(es=e["en"].replace("_CMD_subname(3)", "", 1), status="draft")
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        rep = validation.validate("sf3", strings_dir=strings, metrics=m, ps=ps, stems=["rr3_field_ibox_eng"])
        found = [i for i in rep.issues if i.code == "E_CODES" and i.id == e["id"]]
        c.check("validate reports E_CODES for a dropped SF3 code", len(found) == 1,
                str([(i.code, i.id) for i in rep.issues][:5]))


# ------------------------------------------------------------------------------------------- wrap

def test_wrap(c: Checks, m: Metrics, ps: PakSet) -> None:
    print("wrap with SF3 inserts")
    r = InsertResolver(m, "sf3", paks=ps, english_only=True)
    lim = wrap.file_limits(CARDNAME, m, r)
    want = max(m.line_em(t, lim.slot, r) for t in english_of(ps, CARDNAME))
    c.check("rr3_cardname limit = widest English card name",
            abs(lim.max_px - want) < EPS
            and abs(wrap.file_limits(CARDNAME, m, InsertResolver(m, paks=ps, english_only=True).for_path(CARDNAME))
                    .max_px - lim.max_px) < EPS, f"{lim.max_px:.2f} em")
    es = '¡_CMD_insert_pl_name() obtuvo una carta de batalla:\r\n"_CMD_cardname(12)"!'
    res = wrap.final_text(es, "x", profile=DIALOGUE, path=DIALOGUE_FILE, metrics=m, resolver=r, wrap_mode="auto")
    nick = r.table("nickname").texts
    pl = max(m.text_em(nick[0], 0), m.text_em(nick[1], 0))
    # with 'Mega Man' the first line is wider than the box (1055 px): auto wrap moves "batalla:" down
    first = (m.text_em("¡", 0) + pl + m.text_em(" obtuvo una carta de", 0)) * DIALOGUE.size
    second = m.text_em(f'"{entry_text(r, "cardname", 12)}"!', 0) * DIALOGUE.size
    c.check("dialogue_sf3 'got' box: widths use the SF3 rules (player name = 'Mega Man')",
            res.lines == ["¡_CMD_insert_pl_name() obtuvo una carta de", "batalla:", '"_CMD_cardname(12)"!']
            and abs(res.widths_px[0] - first) < 1e-6 and abs(res.widths_px[-1] - second) < 1e-6
            and not res.overflow_lines and not res.overflow_width, f"{res.lines} {res.widths_px}")
    short = '¡_CMD_insert_pl_name() obtuvo la carta:\r\n"_CMD_cardname(12)"!'
    res = wrap.final_text(short, "x", profile=DIALOGUE, path=DIALOGUE_FILE, metrics=m, resolver=r,
                          wrap_mode="auto")
    c.check("a shorter 'got' line keeps the break after ':'", res.lines == short.split("\r\n"), str(res.lines))


def main(argv: list[str] | None = None) -> int:
    c = Checks()
    t0 = time.time()
    ps = PakSet()
    m = Metrics.load(paks=ps)
    test_games(c, m, ps)
    test_tables(c, m, ps)
    test_input_lengths(c, m, ps)
    test_all_sf3_inserts(c, m, ps)
    test_spanish(c, m, ps)
    test_codes_kept(c, m, ps)
    test_wrap(c, m, ps)
    print(f"total {time.time() - t0:.1f}s")
    return c.result()
