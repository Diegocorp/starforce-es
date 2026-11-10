r"""Tests for sfes.build: round trip, identity build, applying translations, safety gate (codes, broken code
fragments, DATETIME formats, breaks, blank text, characters), structure checks, font variants, determinism,
pak naming, build names and protected output paths, CLI exit codes.

Game data is read only through PakSet. Everything is written under build\_test\ (removed at the end); the
real work files are only read (a few are copied into build\_test\ and edited there).
Run: C:\StarForceES\.venv\Scripts\python.exe -I -X utf8 C:\StarForceES\tools\run.py tests.test_build
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr
from functools import cached_property
from pathlib import Path

from sfes import build, config, extract, fontpatch, fslt, msg23, wrap
from sfes import scope as scopes
from sfes.inserts import InsertResolver
from sfes.metrics import Metrics
from sfes.pak import Pak, PakSet, path_hash

SCRATCH = config.BUILD / "_test"
SCOPE_FILES = 961
DIALOGUE = "natives/stm/text/rr1/message/eng/rr1_dm0613_eng.msg.23"
MENU = "natives/stm/text/common/common_msggui00040101.msg.23"
CARDS = "natives/stm/text/rr1/message/eng/rr1_chipname_eng.msg.23"
DATETIME_FILE = "natives/stm/text/common/common_msggui00050001.msg.23"
TIMESTAMP = "Common_MsgGUI00040101_Detail_TimeStamp"
ES_FORMAT = '<DATETIME {0} "DD/MM/YYYY HH:mm">'
# entry index -> (es, status, wrap) of the small translation
TRANSLATION = {
    DIALOGUE: {
        3: ("._CMD_wait2(30)._CMD_wait2(30)._CMD_wait2(30)¿Eh? ¡Oh, _CMD_insert_option(0)!", "draft", "auto"),
        4: ("Vaya... Parece que\nme quedé dormido.\n", "draft", "auto"),
        10: ("Oye, _CMD_insert_option(0). Ponte\r\ntu Visualizador.", "done", "manual"),
    },
    MENU: {2: ("Cargar", "draft", "manual"), 10: (ES_FORMAT, "done", "manual")},
    CARDS: {1: ("Cañón", "reviewed", "manual"), 2: ("Cañón Plus", "draft", "manual")},
}
# entry index of DIALOGUE -> (es, reason code the gate must give)
GATE_FAILURES = {
    0: ("¡Señor Boreal! ¡Señor Boreal!_CMD_wait(5)\r\n", "E_CODES"),           # extra code
    3: ("._CMD_wait2(30)._CMD_wait2(30)._CMD_wait2(30)¿Eh? ¡Oh!", "E_CODES"),   # missing code
    5: ("Oh, solo estabas\r\ndurmiendo..._CMD_wait(2\n0)\r\n", "E_BREAK"),     # lone LF inside a code
    6: ("¿Ya está\x00 arreglado?", "E_BREAK"),                                  # NUL
    8: ("¡Encendido,\u00a0ya!", "E_CHARSET"),                                   # U+00A0 in slot 0
    9: ("", "E_EMPTY"),                                                          # empty es
}
BLANK_EN = "natives/stm/text/rr1/message/eng/rr1_brotherviewscreen_eng.msg.23"   # entry 172 is ' '
WAITS = "._CMD_wait2(30)._CMD_wait2(30)._CMD_wait2(30)"
OPT = "_CMD_insert_option(0)"
SLEEPY = f"Oye, {OPT}. Ponte\r\ntu Visualizador."
FMT = '<DATETIME {0} "%s">'
# (path, entry index, es, exactly the reason codes the gate must give; empty = the entry must be applied)
GATE_CASES = [
    (MENU, 10, FMT % "DD/MM/YYYY \ud800 HH:mm", {"E_FORMAT", "E_CHARSET"}),     # lone surrogate in a format
    (MENU, 10, FMT % "DD/MM/YYYY\r\nHH:mm", {"E_FORMAT", "E_BREAK"}),          # CRLF inside the code
    (MENU, 10, FMT % "DD/MM/YYYY {1} HH:mm", {"E_FORMAT"}),                    # placeholder inside a format
    (MENU, 10, FMT % "DD/MM/YYYY{0}HH:mm", {"E_FORMAT"}),
    (MENU, 10, FMT % "DD/MM/YYYY 1 HH:mm", {"E_FORMAT"}),                      # digit
    (MENU, 10, FMT % "DD/MM/YYYY\u200bHH:mm", {"E_FORMAT"}),                   # ZWSP
    (MENU, 10, FMT % "DD/MM/YYYY\u00a0HH:mm", {"E_FORMAT"}),                   # NBSP
    (MENU, 10, FMT % "DD/MM/YYYY \U0001F600 HH:mm", {"E_FORMAT"}),             # emoji
    (MENU, 10, FMT % "DD.MM.YYYY HH:mm", set()),
    (MENU, 10, FMT % "DD-MM-YYYY\u3000HH:mm", set()),
    (MENU, 10, "Fecha: " + FMT % "DD/MM/YYYY HH:mm", set()),
    (DIALOGUE, 3, WAITS.replace("(30)", "(3\r\n0)", 1) + f"¿Eh? ¡Oh, {OPT}!", {"E_CODES", "E_BREAK"}),
    (DIALOGUE, 10, SLEEPY.replace("tu ", "tu _CMD_wait(5 "), {"E_CODES"}),     # unclosed cmd
    (DIALOGUE, 10, SLEEPY.replace("tu ", "tu _CMD_ "), {"E_CODES"}),           # bare _CMD_
    (DIALOGUE, 10, SLEEPY + "_cmd_foo", {"E_CODES"}),                           # unknown cmd, any case
    (DIALOGUE, 10, SLEEPY + " {", {"E_CODES"}),
    (DIALOGUE, 10, SLEEPY + " {a}", {"E_CODES"}),
    (DIALOGUE, 10, SLEEPY.replace("tu ", "tu <ICON KEY_A "), {"E_CODES"}),     # unclosed tag
    (DIALOGUE, 10, SLEEPY.replace("tu ", "tu <icon key_a> "), {"E_CODES"}),    # lowercase tag
    (DIALOGUE, 10, SLEEPY + " >", {"E_CODES"}),
    (DIALOGUE, 10, f"Oye,{OPT}.Ponte\r\ntu Visualizador.", set()),              # codes glued to words
    (DIALOGUE, 6, "   ", {"E_EMPTY"}),                                          # prints nothing
    (DIALOGUE, 6, "\r\n", {"E_EMPTY"}),
    (DIALOGUE, 6, "\u3000 \r\n", {"E_EMPTY"}),
    (BLANK_EN, 172, "Texto", {"E_EMPTY"}),                                      # English prints nothing
    (DIALOGUE, 6, "¿Ya es\u00adtá?", {"E_CHARSET"}),                           # soft hyphen
    (DIALOGUE, 6, "¿Ya esta\u0301?", {"E_CHARSET"}),                           # combining acute
    (DIALOGUE, 6, "¿Ya \ue000está?", {"E_CHARSET"}),                           # private use
    (DIALOGUE, 6, "¿Ya \ud800está?", {"E_CHARSET"}),                           # lone surrogate
    (DIALOGUE, 6, "¿Ya\x7festá?", {"E_BREAK"}),                                # DEL
    (DIALOGUE, 6, "¿Ya\x85está?", {"E_BREAK"}),                                # C1 control
    (DIALOGUE, 6, "¿Ya está?", set()),
]


class Ctx:
    """Shared data, built on first use."""

    @cached_property
    def ps(self) -> PakSet:
        return PakSet()

    @cached_property
    def metrics(self) -> Metrics:
        return Metrics.load(paks=self.ps)

    @cached_property
    def resolver(self) -> InsertResolver:
        return InsertResolver(self.metrics, scope="phase1", paks=self.ps)

    @cached_property
    def specs(self) -> list[scopes.FileSpec]:
        return scopes.files("phase1", self.ps)

    def work_doc(self, path: str) -> dict:
        """A fresh in-memory copy of the real work file of `path` (the file itself is only read)."""
        spec = next(s for s in self.specs if s.path == path)
        return extract.load_json(extract.work_path(config.STRINGS, "phase1", spec.group, spec.stem))

    def source(self, path: str) -> msg23.Msg:
        return msg23.parse(self.ps.read(path))


CHECKS: list[Callable[[Ctx], None]] = []


def check(fn: Callable[[Ctx], None]) -> Callable[[Ctx], None]:
    CHECKS.append(fn)
    return fn


# ------------------------------------------------------------------------------------------- helpers

def _translated(ctx: Ctx, table: dict[str, dict[int, tuple[str, str, str]]]) -> list[dict]:
    docs = []
    for path, entries in table.items():
        doc = ctx.work_doc(path)
        for i, (es, status, mode) in entries.items():
            doc["entries"][i].update(es=es, status=status, wrap=mode)
        docs.append(doc)
    return docs


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _pak_files(pak_path: Path) -> Pak:
    pak = Pak(pak_path)
    assert pak.is_sfes, "marker missing"
    return pak


def _pak_read(pak: Pak, path: str) -> bytes:
    return pak.read(pak.by_hash[path_hash(path)])


def _same_except_english(a: msg23.Msg, b: msg23.Msg, changed: set[int]) -> None:
    """Everything of b equals a, except the English slot of the entries in `changed`."""
    assert (a.version, a.languages, a.attr_types, a.attr_names) == (b.version, b.languages, b.attr_types,
                                                                   b.attr_names)
    en = a.slot(config.LANG_EN)
    assert len(a.entries) == len(b.entries)
    for k, (x, y) in enumerate(zip(a.entries, b.entries)):
        assert (x.guid, x.entry_id, x.name_hash, x.name, x.attributes) == (y.guid, y.entry_id, y.name_hash,
                                                                         y.name, y.attributes), k
        for s, (tx, ty) in enumerate(zip(x.texts, y.texts)):
            if s != en or k not in changed:
                assert tx == ty, (k, s, tx, ty)
        assert (x.texts[en] != y.texts[en]) == (k in changed), k


def _pool_keeps_order(old: msg23.Msg, new: msg23.Msg) -> None:
    """The new pool lists the old strings it still uses in their old order, then the new strings."""
    known, used = set(old.pool_order), set(new.pool_order)
    kept = [s for s in old.pool_order if s in used]
    assert new.pool_order[:len(kept)] == kept
    assert all(s not in known for s in new.pool_order[len(kept):])


def _reasons(report: build.ApplyReport, path: str) -> dict[int, list[str]]:
    f = next(f for f in report.files if f.path == path)
    return {r.i: [x.split(":", 1)[0] for x in r.reasons] for r in f.refused}


@contextmanager
def _patched(obj: object, attr: str, value: object) -> Iterator[None]:
    old = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        setattr(obj, attr, old)


def _raiser(error: Exception) -> Callable[..., None]:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise error
    return fail


# ---------------------------------------------------------------------------------- whole-scope checks

@check
def round_trip_is_identical(ctx: Ctx) -> None:
    res = build.build("roundtrip", out_root=SCRATCH / "rt", all_files=True, no_translations=True)
    assert res.mismatches == (), res.mismatches[:5]
    body = extract.load_json(res.report_path)
    assert body["checks"]["round_trip"] == {"files": SCOPE_FILES, "identical": SCOPE_FILES, "different": []}
    assert body["checks"]["pak_readback"]["identical"] == SCOPE_FILES
    assert len(ctx.specs) == SCOPE_FILES
    pak = _pak_files(res.pak_path)
    assert pak.total == SCOPE_FILES + 1
    loose = res.out_dir
    for spec in ctx.specs:
        raw = ctx.ps.read(spec.path)
        assert _pak_read(pak, spec.path) == raw, spec.path
        assert (loose / Path(*spec.path.split("/"))).read_bytes() == raw, spec.path
    manifest = extract.load_json(res.manifest_path)
    assert len(manifest["files"]) == SCOPE_FILES and not any(f["changed"] for f in manifest["files"])
    assert res.files_changed == 0
    print(f"  round trip {body['checks']['round_trip']['identical']}/{SCOPE_FILES}, pak read-back "
          f"{body['checks']['pak_readback']['identical']}/{SCOPE_FILES}")


@check
def identity_build_is_identical(ctx: Ctx) -> None:
    res = build.build("identity", out_root=SCRATCH / "id", identity=True)
    body = extract.load_json(res.report_path)
    assert res.mismatches == (), res.mismatches[:5]
    assert body["checks"]["identity"] == {"files": SCOPE_FILES, "identical": SCOPE_FILES, "different": []}
    total = sum(len(ctx.source(s.path).entries) for s in ctx.specs)
    assert res.entries_applied + res.entries_refused == total, (res.entries_applied, res.entries_refused, total)
    assert res.files_changed == 0 and _pak_files(res.pak_path).total == 1
    print(f"  identity {body['checks']['identity']['identical']}/{SCOPE_FILES} files; entries applied "
          f"{res.entries_applied}, refused {res.entries_refused} {body['totals']['refused_by_reason']}")


# ------------------------------------------------------------------------------------------- applying

@check
def small_translation_lands_in_english_slot_only(ctx: Ctx) -> None:
    docs = _translated(ctx, TRANSLATION)
    files, report = build.apply_docs(docs, ps=ctx.ps, metrics=ctx.metrics, resolver=ctx.resolver)
    assert sorted(files) == sorted(TRANSLATION), sorted(files)
    assert report.entries_refused == 0 and report.entries_applied == 7, report
    res = build.write_outputs("small", files, ps=ctx.ps, out_root=SCRATCH / "small",
                              options={"test": "small"}, report=report)
    pak = _pak_files(res.pak_path)
    assert pak.total == len(files) + 1
    for path, entries in TRANSLATION.items():
        data = _pak_read(pak, path)
        assert data == files[path] == (res.out_dir / Path(*path.split("/"))).read_bytes()
        old, new = ctx.source(path), msg23.parse(data)
        _same_except_english(old, new, set(entries))
        _pool_keeps_order(old, new)
        doc = ctx.work_doc(path)
        for i, (es, _, mode) in entries.items():
            got = new.get(new.entries[i], config.LANG_EN)
            want = wrap.final_text(es, old.get(old.entries[i], config.LANG_EN), profile=doc["profile"], path=path,
                                   metrics=ctx.metrics, resolver=ctx.resolver, wrap_mode=mode).text
            assert got == want, (got, want)
    menu = msg23.parse(files[MENU])
    assert menu.get(menu.by_name()[TIMESTAMP], config.LANG_EN) == ES_FORMAT
    dialogue = msg23.parse(files[DIALOGUE])
    rewrapped = dialogue.get(dialogue.entries[4], config.LANG_EN)
    assert rewrapped.endswith("dormido.\r\n") and build.control_problems(rewrapped) == [], rewrapped
    assert rewrapped.split() == "Vaya... Parece que me quedé dormido.".split(), rewrapped
    manifest = extract.load_json(res.manifest_path)
    changed = {f["path"]: f["entries_changed"] for f in manifest["files"]}
    assert changed == {p: len(e) for p, e in TRANSLATION.items()}, changed
    assert manifest["options"]["test"] == "small" and all(f["kind"] == "msg" for f in manifest["files"])


@check
def safety_gate_refuses_each_failure(ctx: Ctx) -> None:
    dialogue = ctx.work_doc(DIALOGUE)
    for i, (es, _) in GATE_FAILURES.items():
        dialogue["entries"][i].update(es=es, status="draft")
    dialogue["entries"][2].update(es="¿Y si pedí\r\ndemasiado...?\r\n", status="draft")       # passes
    dialogue["entries"][11].update(es="Vale.", status="Done")                                # unknown status
    dialogue["entries"][13].update(es="¡Vamos!", status="draft", wrap="justify")             # bad wrap mode
    dialogue["entries"][14].update(es="¡Espera!", status="draft", en="Wait!!")               # stale English
    menu = ctx.work_doc(MENU)
    menu["entries"][10].update(es='<DATETIME {0} "DD/MM/YYYY hh:mm">', status="done")       # bad format
    menu["entries"][2].update(es="Cargar", status="done")
    stamp = ctx.work_doc(DATETIME_FILE)
    k = next(i for i, e in enumerate(stamp["entries"]) if "DATETIME" in e["en"])
    stamp["entries"][k].update(es='<DATETIME {0} "DD/MM/YYYY\nHH:mm">', status="done")     # LF in a code
    files, report = build.apply_docs([dialogue, menu, stamp], ps=ctx.ps, metrics=ctx.metrics,
                                     resolver=ctx.resolver)
    got = _reasons(report, DIALOGUE)
    for i, (_, code) in GATE_FAILURES.items():
        assert code in got.get(i, []), (i, code, got.get(i))
    assert got[11] == ["E_STATUS"] and got[13] == ["E_WRAP"] and got[14] == ["E_STALE"], got
    assert _reasons(report, MENU) == {10: ["E_FORMAT"]}
    assert _reasons(report, DATETIME_FILE) == {k: ["E_FORMAT", "E_BREAK"]}      # LF is no format separator
    assert DATETIME_FILE not in files
    for path, refused in ((DIALOGUE, set(got)), (MENU, {10})):
        old, new = ctx.source(path), msg23.parse(files[path])
        for i in refused:
            assert new.get(new.entries[i], config.LANG_EN) == old.get(old.entries[i], config.LANG_EN), (path, i)
        _same_except_english(old, new, {2})


@check
def overflow_is_reported_not_blocked(ctx: Ctx) -> None:
    long_es = " ".join(["Esta frase de prueba es demasiado larga para la ventana de diálogo."] * 4) + "\r\n"
    docs = _translated(ctx, {DIALOGUE: {1: (long_es, "draft", "auto")},
                             CARDS: {0: ("Megacañón Superdefinitivo Extraordinario", "draft", "manual")}})
    files, report = build.apply_docs(docs, ps=ctx.ps, metrics=ctx.metrics, resolver=ctx.resolver)
    assert sorted(files) == sorted([DIALOGUE, CARDS]) and report.entries_refused == 0 and report.overflow == 2
    by_path = {f.path: f.overflow[0] for f in report.files}
    dialogue, card = by_path[DIALOGUE], by_path[CARDS]
    assert dialogue.overflow_lines and dialogue.unit == "px" and dialogue.max_lines == 3 and len(dialogue.lines) > 3
    assert all(w <= dialogue.max_width for w in dialogue.widths) and not dialogue.overflow_width
    assert card.overflow_width and card.unit == "em" and card.lines == ["Megacañón Superdefinitivo Extraordinario"]
    res = build.write_outputs("overflow", files, ps=ctx.ps, out_root=SCRATCH / "overflow", options={},
                              report=report)
    md = (res.out_dir / build.REPORT_MD).read_text(encoding="utf-8")
    assert f"{card.widths[0]:8.2f} em  `'Megacañón Superdefinitivo Extraordinario'`" in md, md
    assert res.overflow == 2 and extract.load_json(res.report_path)["totals"]["overflow"] == 2


@check
def gate_refuses_broken_codes_formats_blank_and_hidden_text(ctx: Ctx) -> None:
    docs = {path: ctx.work_doc(path) for path in {case[0] for case in GATE_CASES}}
    refusals = build.ApplyReport()
    for path, i, es, want in GATE_CASES:
        doc = copy.deepcopy(docs[path])
        doc["entries"][i].update(es=es, status="draft", wrap="manual")
        files, report = build.apply_docs([doc], ps=ctx.ps, metrics=ctx.metrics, resolver=ctx.resolver)
        got = _reasons(report, path)
        assert set(got.get(i, [])) == want and set(got) <= {i}, (es, want, got)
        if want:
            assert files == {} and report.entries_applied == 0, es
            refusals.files += report.files
            continue
        new = msg23.parse(files[path])
        assert new.get(new.entries[i], config.LANG_EN) == es, es
    res = build.write_outputs("gate", {}, ps=ctx.ps, out_root=SCRATCH / "gate", options={}, report=refusals)
    body = extract.load_json(res.report_path)          # lone surrogates in the reasons are escaped
    assert body["totals"]["entries_refused"] == sum(bool(case[3]) for case in GATE_CASES)
    assert "\\ud800" in (res.out_dir / build.REPORT_MD).read_text(encoding="utf-8")


@check
def gate_rules_on_plain_strings(ctx: Ctx) -> None:
    grid = "+-×÷=*@<>«»{|}"                                           # English with code marks as plain text
    assert build.code_problems(grid, grid) == [] and build.code_problems(grid, grid + " <") == []
    assert build.code_problems("Hola", "Hola <")[0].startswith("E_CODES: <")
    assert build.prints_nothing(" \u3000\r\n_CMD_wait(5)") and not build.prints_nothing("_CMD_insert_option(0)")
    assert build.empty_problems("\r\n", "\r\n", "\r\n") == []
    assert build.charset_problems("es\u00adta", "es\u00adta", metrics=ctx.metrics, slot=0) == []
    assert build.charset_problems("esta", "es\u00adta", metrics=ctx.metrics, slot=0) != []
    english = '<DATETIME {0} "hh:mm A MM/DD/YYYY">'
    assert build.format_char_problems(english, english, metrics=ctx.metrics, slot=2) == []
    assert build._escaped("a\u3000\ud800\t\x85b") == "a\\u3000\\ud800\\t\\x85b"


@check
def control_characters_found_everywhere(ctx: Ctx) -> None:
    assert build.control_problems("a\r\nb\r\n\r\n") == []
    assert build.control_problems("a\r\r\n\nb") == ["lone LF x1", "lone CR x1"]
    assert build.control_problems("<ICON\nKEY_A>\x00\t") == ["NUL x1", "U+0009 x1", "lone LF x1"]
    assert build.control_problems("a\x7fb\x85\x9f") == ["U+007F x1", "U+0085 x1", "U+009F x1"]
    assert build.break_problems('<DATETIME {0} "DD/MM/YYYY\r\nHH:mm">') == [
        'E_BREAK: line break inside the code <DATETIME {0} "DD/MM/YYYY\\r\\nHH:mm">']


@check
def wrap_error_refuses_entry(ctx: Ctx) -> None:
    docs = _translated(ctx, {CARDS: {1: ("Cañón", "draft", "manual")}})
    with _patched(wrap, "final_text", _raiser(wrap.CodesChangedError("wrapping changed the codes: 'a' -> 'b'"))):
        files, report = build.apply_docs(docs, ps=ctx.ps, metrics=ctx.metrics, resolver=ctx.resolver)
    assert files == {} and _reasons(report, CARDS) == {1: ["E_WRAP"]}, report


@check
def structure_mismatch_refuses_whole_file(ctx: Ctx) -> None:
    good = _translated(ctx, {CARDS: TRANSLATION[CARDS]})[0]
    wrong_guid, missing, renamed, unknown_path, bad_profile = (copy.deepcopy(good) for _ in range(5))
    wrong_guid["entries"][5]["guid"] = "00000000-0000-0000-0000-000000000000"
    del missing["entries"][-1]
    renamed["entries"][0]["name"] = "RR1_CHIPNAME_ENG_X"
    unknown_path["path"] = CARDS.replace("chipname", "chipnamex")
    bad_profile["profile"] = "wide"
    for doc, code in ((wrong_guid, "E_STRUCTURE"), (missing, "E_STRUCTURE"), (renamed, "E_STRUCTURE"),
                      (unknown_path, "E_STRUCTURE"), (bad_profile, "E_PROFILE")):
        files, report = build.apply_docs([doc], ps=ctx.ps, metrics=ctx.metrics, resolver=ctx.resolver)
        assert files == {} and report.files_refused == 1 and report.entries_applied == 0
        assert report.files[0].refused_file.startswith(code + ":"), report.files[0].refused_file
    files, report = build.apply_docs([good, copy.deepcopy(good)], ps=ctx.ps, metrics=ctx.metrics,
                                     resolver=ctx.resolver)
    assert list(files) == [CARDS] and report.files_refused == 1 and "second document" in report.files[1].refused_file


@check
def odd_documents_are_refused_not_crashing(ctx: Ctx) -> None:
    good = _translated(ctx, {CARDS: TRANSLATION[CARDS]})[0]
    upper, other_case, list_profile, surrogate_path = (copy.deepcopy(good) for _ in range(4))
    upper["path"] = CARDS.upper()
    other_case["path"] = CARDS.replace("rr1_chipname_eng", "RR1_CHIPNAME_ENG")
    list_profile["profile"] = ["observed"]
    surrogate_path["path"] = CARDS + "\ud800"
    for doc, code in ((upper, "E_STRUCTURE"), (list_profile, "E_PROFILE"), (surrogate_path, "E_STRUCTURE")):
        files, report = build.apply_docs([doc], ps=ctx.ps, metrics=ctx.metrics, resolver=ctx.resolver)
        assert files == {} and report.files[0].refused_file.startswith(code + ":"), report.files[0].refused_file
    files, report = build.apply_docs([good, other_case], ps=ctx.ps, metrics=ctx.metrics, resolver=ctx.resolver)
    assert list(files) == [CARDS] and report.files[0].refused_file is None
    assert "spelled as in the pak name list" in report.files[1].refused_file, report.files[1].refused_file
    odd = copy.deepcopy(good)
    odd["entries"][1]["status"] = ["draft"]
    odd["entries"][2]["status"] = {"draft": 1}
    files, report = build.apply_docs([odd], ps=ctx.ps, metrics=ctx.metrics, resolver=ctx.resolver)
    assert _reasons(report, CARDS) == {1: ["E_STATUS"], 2: ["E_STATUS"]} and files == {}, report
    assert {r.status for r in report.files[0].refused} == {"['draft']", "{'draft': 1}"}


# ---------------------------------------------------------------------------------------------- fonts

@check
def fonts_rrsystem_in_pak(ctx: Ctx) -> None:
    res = build.build("frr", out_root=SCRATCH / "frr", fonts="rrsystem", no_translations=True)
    expected = fontpatch.outputs("rrsystem", ctx.ps)
    assert list(expected) == [fontpatch.RR_PATH]
    pak = _pak_files(res.pak_path)
    assert pak.total == 2
    data = _pak_read(pak, fontpatch.RR_PATH)
    shipped = (config.BUILD / "fonts" / "rrsystem" / Path(*fontpatch.RR_PATH.split("/"))).read_bytes()
    assert data == expected[fontpatch.RR_PATH] == shipped
    entry = pak.by_hash[path_hash(fontpatch.RR_PATH)]
    assert entry.attributes == (ctx.ps.entry(fontpatch.RR_PATH).attributes & ~0xF) | 1
    manifest = extract.load_json(res.manifest_path)
    assert [(f["kind"], f["changed"], f["source_pak"]) for f in manifest["files"]] == [("font", True,
                                                                                       config.BASE_PAK)]
    assert manifest["options"]["fonts"] == "rrsystem" and res.files_changed == 1


@check
def fonts_supplement_in_pak(ctx: Ctx) -> None:
    res = build.build("fsup", out_root=SCRATCH / "fsup", fonts="supplement", no_translations=True)
    expected = fontpatch.outputs("supplement", ctx.ps)
    assert sorted(expected) == sorted([fontpatch.SUPPLEMENT_PATH, fslt.FSLT_EN])
    pak = _pak_files(res.pak_path)
    assert pak.total == 3
    for path, data in expected.items():
        shipped = (config.BUILD / "fonts" / "supplement" / Path(*path.split("/"))).read_bytes()
        assert _pak_read(pak, path) == data == shipped, path
    rr_attributes = ctx.ps.entry(fontpatch.RR_PATH).attributes
    assert pak.by_hash[path_hash(fontpatch.SUPPLEMENT_PATH)].attributes == (rr_attributes & ~0xF) | 1
    rows = {f["path"]: f for f in extract.load_json(res.manifest_path)["files"]}
    sup = rows[fontpatch.SUPPLEMENT_PATH]
    assert sup["source_pak"] is None and sup["original_sha256"] is None and sup["kind"] == "font"
    assert sup["pak_entry_template"] == fontpatch.RR_PATH and fontpatch.RR_PATH in sup["note"]
    assert fontpatch.SUPPLEMENT_PATH.rsplit("/", 1)[0] == fontpatch.RR_PATH.rsplit("/", 1)[0]
    assert rows[fslt.FSLT_EN]["kind"] == "fslt" and rows[fslt.FSLT_EN]["changed"]


# ------------------------------------------------------------------- work files, determinism, naming

def _work_copy(ctx: Ctx, root: Path) -> Path:
    """build\\_test\\...\\strings with edited copies of three real work files."""
    strings = root / "strings"
    for doc in _translated(ctx, TRANSLATION):
        spec = next(s for s in ctx.specs if s.path == doc["path"])
        target = extract.work_path(strings, "phase1", spec.group, spec.stem)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(extract.dump_json(doc))
    return strings


def _real_work_hashes(ctx: Ctx) -> dict[str, str]:
    out = {}
    for path in TRANSLATION:
        spec = next(s for s in ctx.specs if s.path == path)
        p = extract.work_path(config.STRINGS, "phase1", spec.group, spec.stem)
        out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@check
def work_file_builds_are_deterministic(ctx: Ctx) -> None:
    before = _real_work_hashes(ctx)
    strings = _work_copy(ctx, SCRATCH / "det")
    stems = [scopes.stem_of(p) for p in TRANSLATION]
    a, b = (build.build("det", stems=stems, fonts="rrsystem", strings_dir=strings, out_root=SCRATCH / "det" / k)
            for k in ("one", "two"))
    tree_a, tree_b = _tree(a.out_dir), _tree(b.out_dir)
    assert tree_a == tree_b, sorted(k for k in tree_a if tree_a[k] != tree_b.get(k))
    assert a.mismatches == () and a.entries_applied == 7 and a.files_changed == 4
    assert ctx.ps.next_free_index() == 3 and a.pak_path.name == config.PATCH_FMT.format(3)
    names = set(tree_a) | {a.pak_path.name}
    manifest = extract.load_json(a.manifest_path)
    outputs = [manifest["pak"]["name"], manifest["options"]["pak"]["name"]] + [f["path"] for f in manifest["files"]]
    assert not any("patch_002" in n for n in [*names, *outputs]), names
    assert sorted(f["path"] for f in manifest["files"]) == sorted([*TRANSLATION, fontpatch.RR_PATH])
    pak = _pak_files(a.pak_path)
    assert pak.fingerprint == ctx.ps.paks[-1].fingerprint == build.newest_official_pak(ctx.ps).fingerprint
    cards = msg23.parse(_pak_read(pak, CARDS))
    assert cards.get(cards.entries[1], config.LANG_EN) == "Cañón"
    assert _real_work_hashes(ctx) == before
    md = (a.out_dir / build.REPORT_MD).read_text(encoding="utf-8")
    assert "## Refused entries" in md and "## Overflow" in md and "| total | count |" in md


@check
def min_status_filters_entries(ctx: Ctx) -> None:
    docs = _translated(ctx, TRANSLATION)
    counts = {}
    for status in build.MIN_STATUSES:
        _, report = build.apply_docs(copy.deepcopy(docs), ps=ctx.ps, metrics=ctx.metrics, resolver=ctx.resolver,
                                     min_status=status)
        counts[status] = report.entries_applied
    assert counts == {"draft": 7, "done": 3, "reviewed": 1}, counts


# -------------------------------------------------------------------------------- protected outputs

def _spellings(folder: Path) -> list[Path]:
    text = str(folder)
    return [folder, Path(text.lower()), Path(text.upper()), Path("\\\\?\\" + text), Path(text.replace("\\", "/"))]


@check
def protected_output_paths_refused(ctx: Ctx) -> None:
    files = {**fontpatch.outputs("supplement", ctx.ps), CARDS: b""}
    for folder in (config.GAME_DIR, config.ORIGINAL):
        for root in _spellings(folder):
            paths = build.output_paths("x", files, ps=ctx.ps, out_root=root)
            assert len(paths) == len(files) + 5
            for path in paths:
                try:
                    fontpatch._check_writable(path)
                except PermissionError:
                    continue
                raise AssertionError(f"{path} must be refused")
    fake = SCRATCH / "fake_game"
    fake.mkdir(parents=True)
    with _patched(config, "GAME_DIR", fake):
        try:
            build.write_outputs("x", files, ps=ctx.ps, out_root=fake, options={}, report=None)
        except PermissionError:
            pass
        else:
            raise AssertionError("write_outputs must refuse the game folder")
    assert list(fake.iterdir()) == [], "nothing may be written into the (fake) game folder"


@check
def output_folder_must_be_inside_build(ctx: Ctx) -> None:
    fake_build = SCRATCH / "fake_build"
    (fake_build / "fonts").mkdir(parents=True)
    elsewhere = SCRATCH / "elsewhere"
    devices = ("con", "CON.x", "nul", "nul.txt", "Aux", "prn", "com1", "COM0", "lpt9.a.b")
    with _patched(config, "BUILD", fake_build):
        for name, root in (("x", elsewhere), ("fonts", fake_build), ("x", fake_build / "fonts"),
                           ("..", fake_build), ("a b", fake_build), ("x.", fake_build),
                           *((d, fake_build) for d in devices)):
            try:
                build.write_outputs(name, {}, ps=ctx.ps, out_root=root, options={}, report=None)
            except ValueError:
                continue
            raise AssertionError(f"{root / name} must be refused")
        with _patched(build, "PakSet", None):          # build() checks the name before it opens any pak
            for name in ("con", "x."):
                try:
                    build.build(name, out_root=fake_build)
                except ValueError:
                    continue
                raise AssertionError(f"build({name!r}) must be refused")
        res = build.write_outputs("ok", {}, ps=ctx.ps, out_root=fake_build, options={}, report=None)
        assert res.out_dir == fake_build / "ok" and _pak_files(res.pak_path).total == 1
        assert build.write_outputs("console", {}, ps=ctx.ps, out_root=fake_build, options={},
                                   report=None).out_dir.is_dir()
    assert not elsewhere.exists() and list((fake_build / "fonts").iterdir()) == []
    assert sorted(p.name for p in fake_build.iterdir()) == ["console", "fonts", "ok"]


# ------------------------------------------------------------------------------------------------ CLI

@check
def cli_exit_codes(ctx: Ctx) -> None:
    cli_build = SCRATCH / "cli"
    cli_build.mkdir(parents=True)
    with _patched(config, "BUILD", cli_build):
        assert build.main([]) == 2
        assert build.main(["--name", "x", "--identity", "--no-translations"]) == 2
        assert build.main(["--name", "x", "--stem", "no_such_stem", "--no-translations"]) == 2
        assert build.main(["--name", "x", "--identity", "--min-status", "reviewed"]) == 2
        assert build.main(["--name", "ok", "--stem", "rr1_chipname_eng", "--all", "--no-translations"]) == 0
        assert (cli_build / "ok" / config.PATCH_FMT.format(3)).exists()
        real_build = msg23.build
        with _patched(msg23, "build", lambda m, pool_order=None: real_build(m, pool_order) + b"\0"):
            assert build.main(["--name", "bad", "--stem", "rr1_chipname_eng", "--all", "--no-translations"]) == 1
        assert build.main(["--name", "id", "--stem", "rr1_dm0613_eng", "--identity"]) == 0
        for error in (TypeError("bad document"), RuntimeError("internal"), NotImplementedError("encrypted")):
            with _patched(build, "build", _raiser(error)), redirect_stderr(io.StringIO()) as err:
                assert build.main(["--name", "err", "--no-translations"]) == 2, error
            assert f"{type(error).__name__}: {error}" in err.getvalue(), err.getvalue()
        assert build.main(["--name", "nul", "--no-translations"]) == 2
    assert sorted(p.name for p in cli_build.iterdir()) == ["bad", "id", "ok"]


@check
def cli_refuses_lone_surrogate_from_a_work_file(ctx: Ctx) -> None:
    """A work file whose JSON escapes a lone surrogate in a DATETIME format: the entry is refused, the rest of
    the file is applied and the reports are written."""
    doc = ctx.work_doc(MENU)
    doc["entries"][2].update(es="Cargar", status="draft")
    doc["entries"][10].update(es=FMT % "DD/MM/YYYY \ud800 HH:mm", status="draft")
    spec = next(s for s in ctx.specs if s.path == MENU)
    strings = SCRATCH / "surrogate" / "strings"
    target = extract.work_path(strings, "phase1", spec.group, spec.stem)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(doc, ensure_ascii=True, indent=1) + "\n", encoding="utf-8")
    out = SCRATCH / "surrogate" / "build"
    out.mkdir()
    with _patched(config, "BUILD", out), _patched(config, "STRINGS", strings):
        assert build.main(["--name", "surrogate", "--stem", spec.stem]) == 0
    body = extract.load_json(out / "surrogate" / build.REPORT_JSON)
    assert body["totals"]["entries_applied"] == 1 and body["totals"]["refused_by_reason"] == {"E_CHARSET": 1,
                                                                                              "E_FORMAT": 1}
    new = msg23.parse((out / "surrogate" / Path(*MENU.split("/"))).read_bytes())
    old = ctx.source(MENU)
    assert new.get(new.entries[2], config.LANG_EN) == "Cargar"
    _same_except_english(old, new, {2})


def main(argv: list[str] | None = None) -> int:
    ctx = Ctx()
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)
    failed = 0
    try:
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
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)
    print(f"{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    return 1 if failed else 0
