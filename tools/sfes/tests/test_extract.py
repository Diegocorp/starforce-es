r"""Tests for sfes.scope and sfes.extract.

  - scope: phase1 = 961 files (common 70, sf1_eng 850, sf1_base 41), profiles 804/7/150, stable order,
    unique stems; sf2/sf3/all are disjoint and add up; 'all' is a union without work files of its own;
  - status and flag rules: kana marks used as punctuation, format strings, reviewed entry rules and their
    GUID guard;
  - a fresh extraction (temporary folder) has the expected files and entries per group, unique ids, every
    work file matches its source msg exactly (names, guids, en, ja, source pak and sha256), the filter list
    and name-entry grids are skip, every entry records its automatic status; the real work folder is
    checked the same way (read only) when it exists;
  - unreferenced flags agree with the independent Phase 1 GUID scan (research/text_verify);
  - a re-run is idempotent (byte-identical files);
  - merging keeps es/status/note/wrap and custom flags, flags source_changed, lets automatic statuses
    (also keep/skip and legacy files) follow the rules and keeps statuses chosen by a person;
  - structure changes are refused without writing anything unless forced; forced merges match by GUID
    (no translation moves to another line), flag fallback matches relinked and save dropped work;
  - scope_docs refuses stray copies and duplicate ids; a locked file leaves no temp file behind.
Every test that writes uses a temporary folder. Run: run.py tests.test_extract
"""
from __future__ import annotations

import copy
import csv
import hashlib
import os
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path

from sfes import config, extract, scope
from sfes.extract import MergeError, WorkFileError, load_json, work_path
from sfes.pak import PakSet

SCOPE = "phase1"
EXPECTED_FILES = {"common": 70, "sf1_eng": 850, "sf1_base": 41}
EXPECTED_ENTRIES = {"common": 5203, "sf1_eng": 25699, "sf1_base": 314}   # Phase 1 inventory
EXPECTED_PROFILES = {"dialogue_sf1": 804, "observed_text": 7, "observed": 150}
EXPECTED_RULES = {("rr1_inputscreen_eng", "layout_grid"): 24, ("rr1_inputscreen_eng", "filter_list"): 203}
EXPECTED_FLAGS = {"kana_in_en": 4, "format_string": 8}      # 3 language names + 1 test page; 8 DATETIME
GUID_REFS = config.RESEARCH / "text_verify" / "guid_refs_per_file.csv"
INPUT = ("sf1_eng", "rr1_inputscreen_eng")
MERGE = ("sf1_eng", "rr1_dm0000_eng")


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def __call__(self, cond: bool, msg: str) -> bool:
        if cond:
            self.passed += 1
        else:
            self.failed.append(msg)
            print("FAIL:", msg)
        return cond


def raises(fn, kind: type[BaseException]) -> BaseException | None:
    """The exception of type `kind` that fn() raises, or None."""
    try:
        fn()
    except kind as exc:
        return exc
    return None


def tree_hashes(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def save(path: Path, doc: dict) -> None:
    path.write_bytes(extract.dump_json(doc))


def edit(root: Path, group: str, stem: str, fn) -> Path:
    path = work_path(root, SCOPE, group, stem)
    doc = load_json(path)
    fn(doc["entries"])
    save(path, doc)
    return path


def entries_of(root: Path, group: str, stem: str) -> list[dict]:
    return load_json(work_path(root, SCOPE, group, stem))["entries"]


# ---------------------------------------------------------------- scope and rules

def test_scope(ps: PakSet, ck: Checker) -> list[scope.FileSpec]:
    specs = scope.files(SCOPE, ps)
    ck(len(specs) == 961, f"phase1 has {len(specs)} files, expected 961")
    ck(dict(scope.group_counts(specs)) == EXPECTED_FILES, f"group counts {dict(scope.group_counts(specs))}")
    profs = dict(Counter(s.profile for s in specs))
    ck(profs == EXPECTED_PROFILES, f"profile counts {profs}")
    order = {g: k for k, g in enumerate(scope.SCOPE_GROUPS[SCOPE])}
    ck(specs == sorted(specs, key=lambda s: (order[s.group], s.path)), "scope order is not group, path")
    ck(len({s.stem for s in specs}) == len(specs), "stems are not unique in phase1")
    ck(all(ps.has(s.path) for s in specs), "a scope path is missing from the paks")
    ck(scope.files(SCOPE, ps) == specs, "scope listing is not deterministic")
    later = {k: {s.path for s in scope.files(k, ps)} for k in ("sf2", "sf3", "all")}
    mine = {s.path for s in specs}
    ck(not (mine & later["sf2"]) and not (mine & later["sf3"]) and not (later["sf2"] & later["sf3"]),
       "scopes overlap")
    ck(later["all"] == mine | later["sf2"] | later["sf3"], "scope 'all' is not the union of the others")
    ck(scope.members("all") == scope.BASE_SCOPES == ("phase1", "sf2", "sf3")
       and scope.members("sf2") == ("sf2",), "scope members")
    ck(scope.home_scope("sf1_base") == "phase1" and scope.home_scope("sf3_eng") == "sf3", "home_scope")
    avail = {"natives/stm/text/rr1/message/rr1_x.msg.23", "natives/stm/text/rr1/message/eng/rr1_x_eng.msg.23",
             "natives/stm/text/rr1/message/rr1_y.msg.23"}
    ck(scope.group_of("natives/stm/text/rr1/message/rr1_x.msg.23", avail) is None, "base file with twin")
    ck(scope.group_of("natives/stm/text/rr1/message/rr1_y.msg.23", avail) == "sf1_base", "base-only file")
    ck(scope.group_of("natives/stm/text/rr1/message/eng/rr1_x_eng.msg.23", avail) == "sf1_eng", "eng file")
    ck(scope.group_of("natives/stm/text/common/message/a.msg.23", avail) == "common", "common/message")
    ck(scope.group_of("natives/stm/text/common/sub/dir/a.msg.23", avail) is None, "deep common path")
    return specs


def test_rules(ck: Checker) -> None:
    lore = "This Tribe of hot-blooded warriors...\r\nWas known as・・・ Zerker!"
    cases = [("", "cutscene", None, "empty"), ("Hello", "debug_test", None, "skip"),
             ("テスト", "cutscene", None, "skip"), ("日本語", "ui_settings", None, "skip"),
             ("......", "cutscene", None, "keep"), ("<ICON INGAME_KEY_A>", "menu_screen", None, "keep"),
             ("123", "menu_screen", None, "keep"), ("_CMD_insert_option(0)!!", "cutscene", None, "keep"),
             ("\u3000", "info_text", None, "keep"), ("Hello\r\n", "cutscene", None, "todo"),
             ("Ñandú", "cutscene", None, "todo"), (lore, "cutscene", None, "todo"),
             ("ーー", "battle_ui", None, "keep"), ("ｰ・゠", "battle_ui", None, "keep"),
             ("FUCK", "menu_screen", "filter_list", "skip"),
             ("abcdefghijk", "menu_screen", "layout_grid", "skip")]
    for en, cat, rule, want in cases:
        got = extract.initial_status(en, cat, rule)
        ck(got == want, f"initial_status({en!r}, {cat}, {rule}) = {got}, expected {want}")
    spec = scope.FileSpec("natives/stm/text/rr1/message/eng/a_eng.msg.23", "sf1_eng", "a_eng", "cutscene",
                          "dialogue_sf1")
    flag_cases = [("テスト", set(), None, ["kana_in_en", "unreferenced"]),
                  ("...?", {"g"}, None, ["check_inverted"]),
                  ("Hi!", {"g"}, None, []), (lore, {"g"}, None, []), ("ーー!", {"g"}, None, ["check_inverted"]),
                  ('<DATETIME {0} "MM/DD/YYYY">', {"g"}, None, ["format_string"]),
                  ("Saved <DATETIME {0}>", {"g"}, None, []), ('!"#$%&()?', {"g"}, "layout_grid", ["layout_grid"])]
    for en, refs, rule, want in flag_cases:
        got = extract.source_flags(en, "g", spec, refs, rule)
        ck(got == want, f"source_flags({en!r}, rule {rule}) = {got}, expected {want}")
    flags, warnings = extract.entry_rule_flags("rr1_inputscreen_eng", [f"guid{k}" for k in range(400)])
    ck(flags == {} and len(warnings) == 2 and "ENTRY_RULES" in warnings[0], f"stale rules applied: {warnings}")
    ck(extract.entry_rule_flags("rr1_inputscreen_eng", ["g"] * 10)[0] == {}, "rule applied to a short file")
    ck(extract.entry_rule_flags("other_eng", ["g"]) == ({}, []), "rule for a file without rules")


# ---------------------------------------------------------------- extraction against the source

def check_against_source(root: Path, sources: list[extract.Source], ps: PakSet, ck: Checker, label: str) -> None:
    ids: Counter = Counter()
    bad = []
    for src in sources:
        spec = src.spec
        path = work_path(root, SCOPE, spec.group, spec.stem)
        if not path.exists():
            bad.append(f"missing {path}")
            continue
        doc = load_json(path)
        head = (doc["schema"], doc["scope"], doc["group"], doc["path"], doc["source_pak"], doc["source_sha256"],
                doc["category"], doc["profile"])
        want = (1, SCOPE, spec.group, spec.path, ps.entry(spec.path).pak.name, src.sha256, spec.category,
                spec.profile)
        if head != want:
            bad.append(f"{spec.stem}: header {head} != {want}")
        m = src.msg
        got = [(e["i"], e["id"], e["name"], e["guid"], e["en"], e["ja"]) for e in doc["entries"]]
        exp = [(k, f"{spec.stem}#{e.name}", e.name, e.guid, m.get(e, config.LANG_EN), m.get(e, config.LANG_JA))
               for k, e in enumerate(m.entries)]
        if got != exp:
            bad.append(f"{spec.stem}: entries differ from the source msg")
        if any(e["status"] not in extract.STATUSES for e in doc["entries"]):
            bad.append(f"{spec.stem}: invalid status")
        ids.update(e["id"] for e in doc["entries"])
    for b in bad[:10]:
        print("   ", b)
    ck(not bad, f"{label}: {len(bad)} work files differ from their source")
    dup = [i for i, n in ids.items() if n > 1]
    ck(not dup, f"{label}: duplicate ids {dup[:5]}")


def phase1_unreferenced() -> dict[str, int]:
    """Unreferenced entries per rr1 _eng file from the independent Phase 1 GUID scan."""
    out = {}
    with open(GUID_REFS, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["group"] == "rr1_eng":
                stem = row["file"].rsplit("/", 1)[1][: -len(".msg.23")]
                out[stem] = int(row["entries"]) - int(row["entries_referenced"])
    return out


def check_rule_entries(outcomes: list[extract.FileOutcome], ck: Checker) -> None:
    got = Counter()
    for o in outcomes:
        for e in o.doc["entries"]:
            for f in set(e["flags"]) & {extract.FLAG_FILTER, extract.FLAG_GRID}:
                got[(o.spec.stem, f)] += 1
                ck(e["status"] == "skip", f"{e['id']} flagged {f} but status {e['status']}")
    ck(dict(got) == EXPECTED_RULES, f"reviewed rule entries {dict(got)}")
    doc = next(o.doc for o in outcomes if o.spec.stem == INPUT[1])
    for k in (24, 104, 308):
        e = doc["entries"][k]
        ck(e["status"] == "todo" and not set(e["flags"]) & {"filter_list", "layout_grid"},
           f"neighbour {e['id']}: {e}")
    ck(not [w for o in outcomes for w in o.warnings], "stale reviewed rules in phase1")


def test_fresh(root: Path, ps: PakSet, sources: list[extract.Source], ck: Checker) -> None:
    outcomes = extract.extract(SCOPE, root, ps=ps)
    ck(len(outcomes) == 961 and all(o.action == "new" for o in outcomes), "fresh extraction: 961 new files")
    per_group = Counter()
    for o in outcomes:
        per_group[o.spec.group] += len(o.doc["entries"])
    ck(dict(per_group) == EXPECTED_ENTRIES, f"entries per group {dict(per_group)}")
    check_against_source(root, sources, ps, ck, "fresh extraction")
    entries = [(o.spec.group, e) for o in outcomes for e in o.doc["entries"]]
    flags = Counter((g, f) for g, e in entries for f in e["flags"])
    ck(flags[("common", "unreferenced")] == 0 and flags[("sf1_base", "unreferenced")] == 0,
       "unreferenced flag outside _eng files")
    for name, n in EXPECTED_FLAGS.items():
        got = sum(v for (_, f), v in flags.items() if f == name)
        ck(got == n, f"flag {name}: {got}, expected {n}")
    autos = [e for _, e in entries if [f for f in e["flags"] if f.startswith("auto:")] != ["auto:" + e["status"]]]
    ck(not autos, f"entries without exactly one auto flag matching the status: {autos[:2]}")
    check_rule_entries(outcomes, ck)
    ours = {o.spec.stem: sum("unreferenced" in e["flags"] for e in o.doc["entries"])
            for o in outcomes if o.spec.group == "sf1_eng"}
    if GUID_REFS.exists():
        theirs = phase1_unreferenced()
        diff = {s: (ours.get(s), theirs.get(s)) for s in set(ours) | set(theirs) if ours.get(s) != theirs.get(s)}
        ck(not diff, f"unreferenced per file differs from Phase 1 scan: {sorted(diff.items())[:5]}")
    ck(sum(ours.values()) > 0, "no unreferenced entries found at all")
    patched = sum(o.doc["source_pak"] != config.BASE_PAK for o in outcomes)
    ck(patched > 0, "no file comes from a patch pak (expected several from patch_001)")
    statuses = Counter(e["status"] for _, e in entries)
    print(f"  fresh extraction: {sum(per_group.values())} entries {dict(statuses)}, {patched} files from patch "
          f"paks, {sum(ours.values())} unreferenced")


def test_idempotent(root: Path, ps: PakSet, ck: Checker, label: str) -> None:
    before = tree_hashes(root)
    outcomes = extract.extract(SCOPE, root, ps=ps)
    ck(all(o.action == "unchanged" for o in outcomes), f"{label}: re-run rewrote files")
    ck(tree_hashes(root) == before, f"{label}: re-run changed bytes")


# ---------------------------------------------------------------- merge (unit)

def unit_entry(name: str, guid: str, en: str, status: str, **kw) -> dict:
    e = {"id": f"a#{name}", "i": 0, "name": name, "guid": guid, "en": en, "ja": "", "es": "", "status": status,
         "wrap": "auto", "note": "", "flags": [f"auto:{status}"]}
    e.update(kw)
    return e


def test_merge_unit(ck: Checker) -> None:
    new = {"schema": 1, "entries": [
        unit_entry("A", "g0", "New text", "todo"), unit_entry("B", "g1", "", "empty"),
        unit_entry("C", "g2", "Real text", "todo"), unit_entry("D", "g3", "Geo", "todo"),
        unit_entry("E", "g4", "Hi", "todo"), unit_entry("F", "g5", "Hey", "todo")]}
    old = copy.deepcopy(new)
    o = old["entries"]
    o[0].update(en="Old text", es="Texto", status="done", wrap="manual", note="n",
                flags=["auto:todo", "mine", "unreferenced"])
    o[1].update(en="Was here", status="todo", flags=["auto:todo"])
    o[2].update(en="......", status="keep", flags=["auto:keep"])          # automatic keep, English now real
    o[3].update(status="keep")                                           # chosen by a person (auto says todo)
    o[4].update(status="skip", flags=[])                                 # legacy file, untouched entry
    o[5].update(status="keep", es="Hola", flags=[])                      # legacy file, translator work
    res = extract.merge(old, new)
    a, b, c, d, e, f = res.doc["entries"]
    ck((a["en"], a["es"], a["status"], a["wrap"], a["note"]) == ("New text", "Texto", "done", "manual", "n"),
       f"merge kept translator fields: {a}")
    ck(a["flags"] == ["auto:todo", "mine", "source_changed"], f"merge flags {a['flags']}")
    ck(b["status"] == "empty" and b["flags"] == ["auto:empty", "source_changed"], f"automatic status: {b}")
    ck(c["status"] == "todo" and "source_changed" in c["flags"], f"automatic keep not recomputed: {c}")
    ck(d["status"] == "keep" and d["flags"] == ["auto:todo"], f"chosen status lost: {d}")
    ck(e["status"] == "todo" and f["status"] == "keep", f"legacy entries: {e['status']}, {f['status']}")
    ck(res.source_changed == 3, f"merge counted {res.source_changed} source changes")
    ck(not extract.status_is_auto(d) and extract.status_is_auto(e) and not extract.status_is_auto(f),
       "status_is_auto")
    broken = copy.deepcopy(old)
    broken["entries"][1]["guid"] = "other"
    exc = raises(lambda: extract.merge(broken, new), MergeError)
    ck(exc is not None and "index 1" in str(exc), f"structure error message: {exc}")
    forced = extract.merge(broken, new, force_structure=True)
    ck(forced.doc["entries"][1]["guid"] == "g1" and forced.relinked == 0 and forced.added == 1
       and [x["name"] for x in forced.dropped] == ["B"], f"forced merge: {forced}")


def test_pairing(ck: Checker) -> None:
    old = [unit_entry("N_0", "g1", "one", "done", es="uno"), unit_entry("N_1", "g2", "two", "done", es="dos"),
           unit_entry("N_2", "g3", "three", "done", es="tres"), unit_entry("N_3", "g9", "gone", "done", es="ido"),
           unit_entry("N_4", "gA", "same", "todo"), unit_entry("N_5", "gB", "dup", "todo"),
           unit_entry("N_6", "gC", "dup", "todo")]
    new = [unit_entry("N_0", "g0", "zero", "todo"), unit_entry("N_1", "g1", "one", "todo"),
           unit_entry("N_2", "g2", "two", "todo"), unit_entry("N_3", "gX", "three", "todo"),
           unit_entry("N_4", "gZ", "same", "todo"), unit_entry("N_5", "gD", "dup", "todo"),
           unit_entry("N_6", "gE", "dup", "todo")]
    pairs, left = extract.pair_entries(old, new)
    want = {1: (0, "guid"), 2: (1, "guid"), 3: (2, "en"), 4: (4, "name"), 5: (5, "name"), 6: (6, "name")}
    ck(pairs == want and left == [3], f"pairing {pairs}, unmatched {left}")
    res = extract.merge({"schema": 1, "source_sha256": "old", "entries": old}, {"schema": 1, "entries": new},
                        force_structure=True)
    es = [x["es"] for x in res.doc["entries"]]
    ck(es == ["", "uno", "dos", "tres", "", "", ""], f"forced merge moved translations: {es}")
    relinked = [x["name"] for x in res.doc["entries"] if "relinked" in x["flags"]]
    ck(relinked == ["N_3", "N_4", "N_5", "N_6"] and res.relinked == 4 and res.added == 1, f"relinked {relinked}")
    ck(len(res.dropped) == 1 and res.dropped[0]["es"] == "ido" and res.dropped[0]["dropped_from"] == "old",
       f"dropped {res.dropped}")
    ck(extract.holds_work(res.dropped[0]) and not extract.holds_work(unit_entry("X", "g", "x", "todo")),
       "holds_work")


def test_save_dropped(td: Path, ck: Checker) -> None:
    path = extract.dropped_path(td, SCOPE, "sf1_eng", "x_eng")
    doc = {"scope": SCOPE, "group": "sf1_eng", "path": "natives/stm/text/rr1/message/eng/x_eng.msg.23"}
    a, b = unit_entry("A", "g1", "a", "done", es="A"), unit_entry("B", "g2", "b", "done", es="B")
    extract.save_dropped(path, doc, [a])
    extract.save_dropped(path, doc, [a, b])
    ck([e["name"] for e in load_json(path)["entries"]] == ["A", "B"], "dropped side file does not dedupe")
    ck(path.relative_to(td).parts[0] == extract.DROPPED_DIR and len(path.relative_to(td).parts) == 4,
       f"dropped side file location {path}")


# ---------------------------------------------------------------- merge against real data

def find_plain_file(root: Path, exclude: set[str]) -> str:
    """An sf1_eng stem with at least 6 entries, all todo, with distinct English (for structure tests)."""
    for p in sorted((root / SCOPE / "sf1_eng").glob("*.json")):
        es = load_json(p)["entries"]
        if p.stem not in exclude and len(es) >= 6 and all(e["status"] == "todo" for e in es) \
                and len({e["en"] for e in es}) == len(es):
            return p.stem
    raise AssertionError("no plain sf1_eng file found")


def tweak_merge(entries: list[dict]) -> None:
    entries[0].update(es="Hola.\nMundo", status="draft", note="nota", wrap="manual",
                      flags=entries[0]["flags"] + ["reviewer"])
    entries[1].update(en="OLD ENGLISH", es="Viejo", status="done")
    entries[2].update(en="OLD", status="empty", flags=["auto:empty"])
    entries[3].update(en="......", status="keep", flags=["auto:keep"])
    entries[4].update(status="keep")


def make_legacy(entries: list[dict]) -> None:
    """The file as the previous extractor wrote it: no auto/rule flags, filter and grid rows todo or keep."""
    for e in entries:
        e["flags"] = [f for f in e["flags"]
                      if not f.startswith("auto:") and f not in ("filter_list", "layout_grid")]
        if e["status"] == "skip":
            e["status"] = "keep" if not extract.has_letters(e["en"]) else "todo"


def test_merge_run(root: Path, ps: PakSet, ck: Checker) -> None:
    src = entries_of(root, *MERGE)
    ck([e["status"] for e in src[:5]] == ["todo"] * 5, "merge test needs 5 todo entries")
    edit(root, *MERGE, tweak_merge)
    edit(root, *INPUT, make_legacy)
    others = {k: v for k, v in tree_hashes(root).items() if MERGE[1] not in k and INPUT[1] not in k}
    outcomes = extract.extract(SCOPE, root, ps=ps)
    actions = Counter(o.action for o in outcomes)
    ck(actions == Counter({"unchanged": 959, "updated": 2}), f"merge actions {dict(actions)}")
    e0, e1, e2, e3, e4 = entries_of(root, *MERGE)[:5]
    ck((e0["es"], e0["status"], e0["note"], e0["wrap"]) == ("Hola.\nMundo", "draft", "nota", "manual"),
       f"entry 0 lost translator fields: {e0}")
    ck("reviewer" in e0["flags"] and "source_changed" not in e0["flags"], f"entry 0 flags {e0['flags']}")
    ck(e1["en"] == src[1]["en"] and e1["es"] == "Viejo" and e1["status"] == "done"
       and "source_changed" in e1["flags"], f"entry 1 after source change: {e1}")
    ck(e2["en"] == src[2]["en"] and e2["status"] == "todo" and "source_changed" in e2["flags"],
       f"entry 2 automatic status: {e2}")
    ck(e3["status"] == "todo" and "source_changed" in e3["flags"] and "auto:todo" in e3["flags"],
       f"entry 3 automatic keep did not follow the new English: {e3}")
    ck(e4["status"] == "keep" and "source_changed" not in e4["flags"], f"entry 4 chosen status lost: {e4}")
    fresh = {e["id"]: (e["status"], e["flags"]) for e in load_json(work_path(root, SCOPE, *INPUT))["entries"]}
    migrated = Counter(s for s, f in fresh.values() if {"filter_list", "layout_grid"} & set(f))
    ck(migrated == Counter({"skip": 227}), f"legacy file did not migrate to the reviewed rules: {migrated}")
    ck({k: v for k, v in tree_hashes(root).items() if MERGE[1] not in k and INPUT[1] not in k} == others,
       "merge touched other files")
    test_idempotent(root, ps, ck, "after merge")


def shift_file(entries: list[dict]) -> None:
    """The file before an entry was inserted at the top: names renumbered, every line translated."""
    stem = entries[0]["id"].split("#")[0]
    prefix = entries[0]["name"].rsplit("_", 1)[0]
    old = []
    for k, e in enumerate(entries[1:]):
        old.append(dict(e, i=k, name=f"{prefix}_{k}", id=f"{stem}#{prefix}_{k}", es=f"ES {e['guid']}",
                        status="done"))
    entries[:] = old


def add_extra(entries: list[dict]) -> None:
    last = entries[-1]
    entries.append(dict(last, name=last["name"] + "99", id=last["id"] + "99", es="Traducción valiosa",
                        guid="11111111-2222-3333-4444-555555555555", status="reviewed"))


def test_structure(root: Path, ps: PakSet, ck: Checker) -> None:
    group, stem = "common", "common_msggui00030000"
    shifted = find_plain_file(root, {MERGE[1], INPUT[1]})
    dropped = find_plain_file(root, {MERGE[1], INPUT[1], shifted})
    edit(root, group, stem, lambda entries: entries[0].update(es="Idioma"))
    edit(root, group, stem, lambda entries: entries[3].update(guid="00000000-0000-0000-0000-000000000000"))
    edit(root, "sf1_base", "rr1_msggui01000000", lambda entries: entries.pop())
    edit(root, "sf1_eng", shifted, shift_file)
    edit(root, "sf1_eng", dropped, add_extra)
    before = tree_hashes(root)
    text = str(raises(lambda: extract.extract(SCOPE, root, ps=ps), MergeError))
    ck(all(s in text for s in (stem, "rr1_msggui01000000", shifted, dropped)),
       f"structure change not refused, or the error does not name every file: {text[:300]}")
    ck(tree_hashes(root) == before, "refused extraction still wrote files")
    outcomes = {o.spec.stem: o for o in extract.extract(SCOPE, root, force_structure=True, ps=ps)}
    changed = sorted(s for s, o in outcomes.items() if o.action != "unchanged")
    ck(changed == sorted([stem, "rr1_msggui01000000", shifted, dropped]), f"forced extraction rewrote {changed}")
    doc = load_json(work_path(root, SCOPE, group, stem))
    ck(doc["entries"][0]["es"] == "Idioma", "forced extraction lost es of a matched entry")
    e3 = doc["entries"][3]
    ck(e3["guid"] != "00000000-0000-0000-0000-000000000000" and "relinked" in e3["flags"], f"relinked entry {e3}")
    es = entries_of(root, "sf1_eng", shifted)
    wrong = [e["id"] for e in es if e["es"] and e["es"] != f"ES {e['guid']}"]
    ck(not wrong and sum(bool(e["es"]) for e in es) == len(es) - 1,
       f"translations moved to other lines: {wrong[:3]}")
    ck(es[0]["es"] == "" and es[0]["status"] == "todo" and outcomes[shifted].added == 1, f"inserted entry {es[0]}")
    ck(not any("relinked" in e["flags"] for e in es), "GUID matches flagged relinked")
    side = extract.dropped_path(root, SCOPE, "sf1_eng", dropped)
    kept = load_json(side)["entries"] if side.exists() else []
    ck([e["es"] for e in kept] == ["Traducción valiosa"] and outcomes[dropped].saved_to == side,
       f"dropped translation not saved: {kept}")
    ck(all("Traducción valiosa" != e["es"] for e in entries_of(root, "sf1_eng", dropped)), "dropped entry kept")
    lines = "\n".join(extract.summary_lines(SCOPE, list(outcomes.values()), root))
    ck("structure re-matched" in lines and str(side) in lines, "summary does not report the forced merge")
    test_idempotent(root, ps, ck, "after forced merge")


# ---------------------------------------------------------------- work folder checks

def test_scope_docs(root: Path, ck: Checker) -> None:
    docs = extract.scope_docs(SCOPE, root)
    ck(len(docs) == 961 and extract.scope_docs("all", root) == docs, "scope 'all' is not the union of folders")
    folder = root / SCOPE / MERGE[0]
    stray = folder / f"{MERGE[1]} - Copy.json"
    shutil.copy(folder / f"{MERGE[1]}.json", stray)
    exc = raises(lambda: extract.scope_docs(SCOPE, root), WorkFileError)
    ck(exc is not None and "Copy" in str(exc) and "occurs 2 times" in str(exc), f"stray copy accepted: {exc}")
    ck(stray in extract.orphans(SCOPE, root, []), "stray copy not listed as orphan")
    stray.unlink()
    exc = raises(lambda: extract.extract("all", root), ValueError)
    ck(exc is not None and "phase1" in str(exc), f"extract accepted scope 'all': {exc}")
    ck(raises(lambda: extract.scope_docs("sf3", root), FileNotFoundError) is not None,
       "scope_docs without work files did not raise FileNotFoundError")


def test_locked_write(td: Path, ck: Checker) -> None:
    """A file held open (as a spreadsheet program does) cannot be replaced: the old bytes and no temp stay."""
    if os.name != "nt":
        return
    path = td / "locked.csv"
    path.write_bytes(b"old")
    with open(path, "rb"):
        exc = raises(lambda: extract.write_if_changed(path, b"new"), PermissionError)
    ck(exc is not None, "replaced a file that is open")
    ck(path.read_bytes() == b"old" and not path.with_name("locked.csv.tmp").exists(), "temp file left behind")


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    t0 = time.time()
    ck = Checker()
    ps = PakSet()
    specs = test_scope(ps, ck)
    test_rules(ck)
    test_merge_unit(ck)
    test_pairing(ck)
    sources = extract.read_sources(specs, ps)
    real = config.STRINGS / SCOPE
    if real.exists():
        check_against_source(config.STRINGS, sources, ps, ck, "real work folder")
    else:
        print(f"  note: {real} does not exist yet; only temporary extractions were checked")
    with tempfile.TemporaryDirectory(prefix="sfes_test_extract_") as td:
        test_save_dropped(Path(td) / "side", ck)
        test_locked_write(Path(td), ck)
        fresh = Path(td) / "fresh"
        test_fresh(fresh, ps, sources, ck)
        test_idempotent(fresh, ps, ck, "fresh")
        work = Path(td) / "copy"
        shutil.copytree(real if real.exists() else fresh / SCOPE, work / SCOPE)
        test_idempotent(work, ps, ck, "copy of the work folder")
        test_scope_docs(work, ck)
        test_merge_run(work, ps, ck)
        test_structure(work, ps, ck)
    print(f"test_extract: {ck.passed} checks passed, {len(ck.failed)} failed ({time.time() - t0:.1f}s)")
    return 1 if ck.failed else 0
