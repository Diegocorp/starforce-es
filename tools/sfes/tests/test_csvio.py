r"""Tests for sfes.csvio and sfes.refds (the DS reference column the CSV carries).

  - refds: a rebuild in a temporary folder equals the real reference (when it exists), covers at least 95%
    of the SF1 text entries, and a scope without alignment or without work files writes nothing;
  - export: UTF-8 BOM, contract header plus base, one row per entry in work-file order, cells equal the
    work files (real CRLF line breaks inside cells), ref_es filled, limits per profile, deterministic;
  - export never overwrites a CSV holding edits that are not imported (an edited cell, an added row);
    --force keeps it as a backup; a CSV open in a spreadsheet program gives an error and leaves no temp file;
  - export -> import with no edits changes nothing (also a spreadsheet-style re-save: LF-only cells,
    semicolon separator, minimal quoting, and UTF-16 tab-separated text);
  - edited es/status/note are imported, LF-only cells become CRLF, only the edited file is rewritten;
  - refused and reported: en mismatch with edits, unknown/repeated id, invalid status, short row, damaged
    base, English-empty entry given a translation, translated status without es; warning for es left
    with status todo; an en mismatch without edits is only reported; --dry-run writes nothing;
  - lost update: an unedited row of an older CSV never reverts a newer work file; a row edited on both
    sides is a conflict unless --overwrite-newer; a CSV without base needs --no-base;
  - stray work files are refused; scope 'all' exports and imports through the phase1 files; stats add up.
Work files are copied to a temporary folder first; the real work folder is only read.
Run: run.py tests.test_csvio
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import shutil
import tempfile
import time
from pathlib import Path

from sfes import config, csvio, extract, refds
from sfes.extract import load_json

SCOPE = "phase1"
COL = {c: k for k, c in enumerate(csvio.COLUMNS)}
MIN_COVERAGE = 95.0


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


def read_rows(path: Path) -> list[list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def write_rows(path: Path, rows: list[list[str]], delimiter: str = ",", lf_cells: bool = False,
               encoding: str = "utf-8-sig") -> Path:
    """Write rows the way a spreadsheet would: optional LF-only cells and minimal quoting."""
    if lf_cells:
        rows = [[c.replace("\r\n", "\n") for c in r] for r in rows]
    buf = io.StringIO(newline="")
    csv.writer(buf, delimiter=delimiter, lineterminator="\r\n").writerows(rows)
    path.write_bytes(buf.getvalue().encode(encoding))
    return path


def entry_by_id(strings: Path, rid: str) -> dict:
    return next(e for _, d in extract.scope_docs(SCOPE, strings) for e in d["entries"] if e["id"] == rid)


def set_entry(strings: Path, rid: str, **kw) -> None:
    """Change an entry directly in its work file (a JSON edit or another tool)."""
    stem, _ = rid.split("#", 1)
    path = next(p for p, d in extract.scope_docs(SCOPE, strings) if p.stem == stem)
    doc = load_json(path)
    next(e for e in doc["entries"] if e["id"] == rid).update(kw)
    path.write_bytes(extract.dump_json(doc))


def todo_rows(rows: list[list[str]], profile: str = "dialogue_sf1") -> list[int]:
    return [k for k, r in enumerate(rows) if k and r[COL["status"]] == "todo" and r[COL["profile"]] == profile]


def setup(td: Path) -> tuple[Path, Path, Path]:
    """Temporary copies of the work files and the DS reference; an empty export folder."""
    strings, ref, export = td / "strings", td / "ref", td / "export"
    real = config.STRINGS / SCOPE
    if real.exists():
        shutil.copytree(real, strings / SCOPE)
    else:
        extract.extract(SCOPE, strings)
    if (refds.DS_REF / SCOPE).exists():
        shutil.copytree(refds.DS_REF / SCOPE, ref / SCOPE)
    else:
        refds.build(SCOPE, strings, ref)
    return strings, ref, export


# ---------------------------------------------------------------- refds

def test_refds(strings: Path, td: Path, ck: Checker) -> None:
    out = td / "ref_rebuilt"
    result = refds.build(SCOPE, strings, out)
    text = sum(c.text_entries for c in result)
    covered = sum(c.covered for c in result)
    ck(len(result) == 850 and text and 100.0 * covered / text >= MIN_COVERAGE,
       f"DS reference: {len(result)} files, {covered}/{text} text entries")
    if (refds.DS_REF / SCOPE).exists():
        ck(tree_hashes(out / SCOPE) == tree_hashes(refds.DS_REF / SCOPE),
           "rebuilt reference differs from the real one")
    ck(sum(c.counts["fuzzy"] for c in result) > 0, "no fuzzy references accepted")
    rows = [{"lc_en": "Get 10 Zenny!", "ds_en": "Get 100 Zenny!", "score": 0.9},
            {"lc_en": "Yes!", "ds_en": "Yes.", "score": 0.95},
            {"lc_en": "Go to the lab", "ds_en": "Go to lab", "score": 0.9}]
    ck([refds.fuzzy_ok(r) for r in rows] == [True, False, True], "fuzzy filter (length, digit set)")
    rows[0]["ds_en"] = "Get 20 Zenny!"
    ck(not refds.fuzzy_ok(rows[0]), "fuzzy match with other digits accepted")
    empty = td / "ref_empty"
    ck(isinstance(raises(lambda: refds.build("sf3", strings, empty), refds.NoAlignment), refds.NoAlignment)
       and not empty.exists(), "scope without alignment wrote files")
    ck(raises(lambda: refds.build("sf2", strings, empty), FileNotFoundError) is not None and not empty.exists(),
       "scope without work files wrote files")


# ---------------------------------------------------------------- export

def test_export(strings: Path, ref: Path, export: Path, ck: Checker) -> Path:
    res = csvio.export(SCOPE, strings, ref, export)
    out, n = res.path, res.rows
    raw = out.read_bytes()
    ck(raw.startswith(b"\xef\xbb\xbf"), "export has no UTF-8 BOM")
    rows = read_rows(out)
    ck(rows[0] == csvio.COLUMNS and csvio.COLUMNS[:13] == ["id", "file", "entry", "category", "profile", "status",
                                                          "en", "ja", "ref_es", "es", "note", "en_lines", "limit"],
       f"header {rows[0]}")
    docs = extract.scope_docs(SCOPE, strings)
    entries = [(doc, e) for _, doc in docs for e in doc["entries"]]
    ck(n == len(entries) == len(rows) - 1, f"rows {n} vs entries {len(entries)}")
    same = all(r[COL["id"]] == e["id"] and r[COL["en"]] == e["en"] and r[COL["ja"]] == e["ja"]
               and r[COL["es"]] == e["es"] and r[COL["status"]] == e["status"]
               and r[COL["profile"]] == doc["profile"] for r, (doc, e) in zip(rows[1:], entries))
    ck(same, "exported cells differ from the work files")
    ck(len({r[0] for r in rows[1:]}) == n, "exported ids are not unique")
    ck(any("\r\n" in r[COL["en"]] for r in rows[1:]), "no real line breaks inside cells")
    ck(all(csvio.BASE_RX.fullmatch(r[COL["base"]]) for r in rows[1:]), "base cells malformed")
    with_ref = sum(1 for r in rows[1:] if r[COL["ref_es"]])
    ck(with_ref > 20000, f"only {with_ref} rows have ref_es")
    dialogue = {r[COL["limit"]] for r in rows[1:] if r[COL["profile"]] == "dialogue_sf1"}
    ck(dialogue == {"3 lines x 1026 px"}, f"dialogue limits {dialogue}")
    observed = {r[COL["limit"]] for r in rows[1:] if r[COL["profile"]] == "observed"}
    ck(all(x.startswith("<= English") for x in observed), f"observed limits {sorted(observed)[:3]}")
    ck(csvio.body_lines("a\r\nb\r\n\r\n") == 2 and csvio.body_lines("") == 0, "body_lines")
    again = csvio.export(SCOPE, strings, ref, export)
    ck(again.path.read_bytes() == raw and again.backup is None, "export is not deterministic")
    return out


def test_export_guard(path: Path, strings: Path, ref: Path, export: Path, ck: Checker) -> None:
    original = path.read_bytes()
    rows = read_rows(path)
    k = todo_rows(rows)[0]
    rows[k][COL["es"]] = "Traducción sin importar"
    rows[k][COL["status"]] = "draft"
    write_rows(path, rows)
    edited = path.read_bytes()
    set_entry(strings, rows[k + 1][COL["id"]], note="otro cambio")      # the work files changed too
    exc = raises(lambda: csvio.export(SCOPE, strings, ref, export), csvio.ExportBlocked)
    ck(exc is not None and rows[k][COL["id"]] in str(exc) and "holds 1 row " in str(exc),
       f"export not blocked: {exc}")
    ck(path.read_bytes() == edited, "blocked export changed the CSV")
    imported = csvio.import_csv(path, SCOPE, strings, export)
    ck(dict(imported.changes) == {"es": 1, "status": 1} and not imported.refused, f"import {imported.problems}")
    res = csvio.export(SCOPE, strings, ref, export)
    ck(res.backup is None and path.read_bytes() != edited, "export after import was blocked")
    with open(path, "ab") as f:
        f.write('"edited-by-translator"\r\n'.encode("utf-8"))
    exc = raises(lambda: csvio.export(SCOPE, strings, ref, export), csvio.ExportBlocked)
    ck(exc is not None and "edited-by-translator" in str(exc), f"added row not protected: {exc}")
    kept = path.read_bytes()
    forced = csvio.export(SCOPE, strings, ref, export, force=True)
    ck(forced.backup is not None and forced.backup.read_bytes() == kept
       and forced.backup.name == "phase1.bak-001.csv", f"forced export backup {forced.backup}")
    forced.backup.unlink()
    set_entry(strings, rows[k + 2][COL["id"]], note="cambio en JSON")
    if os.name == "nt":
        with open(path, "rb"):
            exc = raises(lambda: csvio.export(SCOPE, strings, ref, export), PermissionError)
        ck(exc is not None and not path.with_name(path.name + ".tmp").exists(), "export onto an open CSV")
    set_entry(strings, rows[k][COL["id"]], es="", status="todo")
    for rid in (rows[k + 1][COL["id"]], rows[k + 2][COL["id"]]):
        set_entry(strings, rid, note="")
    csvio.export(SCOPE, strings, ref, export)
    ck(path.read_bytes() == original, "CSV not back to the original export after undoing the edits")


# ---------------------------------------------------------------- import

def test_roundtrip(path: Path, strings: Path, export: Path, ck: Checker) -> None:
    before = tree_hashes(strings)
    rows = read_rows(path)
    variants = {
        "as exported": path,
        "LF cells, semicolons": write_rows(export / "excel.csv", rows, ";", lf_cells=True),
        "UTF-16 tabs": write_rows(export / "unicode.txt", rows, "\t", lf_cells=True, encoding="utf-16"),
        "double BOM": export / "bom2.csv",
    }
    variants["double BOM"].write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    for label, p in variants.items():
        res = csvio.import_csv(p, SCOPE, strings, export)
        ck(res.applied == len(rows) - 1 and not res.problems and not res.changes and not res.files_written,
           f"{label}: unedited import changed something ({res.applied} applied, {res.problems[:3]}, "
           f"{dict(res.changes)})")
    ck(tree_hashes(strings) == before, "unedited imports modified work files")


def test_edits(path: Path, strings: Path, export: Path, ck: Checker) -> None:
    rows = read_rows(path)
    a, b, c, f, g, h, s, t, u = todo_rows(rows)[:9]
    empty = next(k for k, r in enumerate(rows) if k and r[COL["status"]] == "empty")
    rows[a][COL["es"]] = "¡Hola!\r\nSegunda línea"
    rows[a][COL["status"]] = "Draft"
    rows[a][COL["note"]] = "revisar\r\ntono"
    rows[b][COL["en"]] = "Tampered English"
    rows[b][COL["es"]] = "No debe entrar"
    rows[c][COL["status"]] = "finished"
    rows[f][COL["status"]] = "empty"
    rows[g][COL["en"]] = "Tampered, no edits"
    rows[h][COL["status"]] = "done"                                        # done without Spanish
    rows[s][COL["es"]] = "Escrito, pero sin estado"                        # warning only
    rows[t][COL["base"]] = "12345"                                         # damaged base
    rows[t][COL["es"]] = "Base rota"
    rows[u] = rows[u][:COL["note"]]                                        # short row
    rows[empty][COL["es"]] = "texto"
    rows[empty][COL["status"]] = "done"
    unknown = list(rows[a])
    unknown[COL["id"]] = "no_such_file#NOPE"
    repeat = list(rows[a])
    repeat[COL["es"]] = "Otra vez"
    rows += [unknown, repeat]
    set_entry(strings, rows[u][COL["id"]], note="nota importante")         # what a short row could erase
    src = write_rows(export / "edited.csv", rows, lf_cells=True)
    before = tree_hashes(strings)
    dry = csvio.import_csv(src, SCOPE, strings, export, dry_run=True)
    ck(dry.changes and tree_hashes(strings) == before, "dry run wrote files or found no edits")
    res = csvio.import_csv(src, SCOPE, strings, export)
    kinds = sorted((p.kind, p.refused) for p in res.problems)
    want = sorted([("en_mismatch", True), ("bad_status", True), ("bad_status", True), ("en_mismatch", False),
                   ("unknown_id", True), ("duplicate_id", True), ("bad_status", True), ("bad_status", True),
                   ("es_unused", False), ("bad_base", True), ("bad_row", True)])
    ck(kinds == want, f"problems {kinds}")
    ck(res.refused == 9, f"refused {res.refused}")
    ck(dict(res.changes) == {"es": 2, "status": 1, "note": 1}, f"changes {dict(res.changes)}")
    ck(len(res.files_written) == len({rows[a][COL["file"]], rows[s][COL["file"]]}), f"written {res.files_written}")
    e = entry_by_id(strings, rows[a][COL["id"]])
    ck(e["es"] == "¡Hola!\r\nSegunda línea" and e["status"] == "draft" and e["note"] == "revisar\r\ntono",
       f"imported entry {e}")
    e = entry_by_id(strings, rows[s][COL["id"]])
    ck(e["es"] == "Escrito, pero sin estado" and e["status"] == "todo", f"es with status todo {e}")
    for k in (b, c, f, h, t, empty):
        rid = rows[k][COL["id"]]
        e = entry_by_id(strings, rid)
        ck(e["es"] == "" and e["status"] in ("todo", "empty"), f"refused row {rid} was applied: {e}")
    ck(entry_by_id(strings, rows[u][COL["id"]])["note"] == "nota importante", "short row erased the note")
    report = (export / f"{SCOPE}_import.txt").read_text(encoding="utf-8")
    ck(all(k in report for k in ("en_mismatch", "unknown_id", "bad_row", "es_unused")), "report lacks problems")
    again = csvio.import_csv(src, SCOPE, strings, export)
    ck(not again.changes and not again.files_written, "re-importing the same CSV changed files again")


def test_lost_update(strings: Path, ref: Path, export: Path, ck: Checker) -> None:
    out = csvio.export(SCOPE, strings, ref, export / "lost").path
    rows = read_rows(out)
    x, y, z = todo_rows(rows)[:3]
    newer = rows[x][COL["id"]]
    set_entry(strings, newer, es="Es el año 220X...", status="done")      # translated after the export
    set_entry(strings, rows[z][COL["id"]], note="nota nueva")
    rows[y][COL["es"]] = "Otra línea"
    rows[y][COL["status"]] = "draft"
    rows[z][COL["es"]] = "Choque"
    rows[z][COL["status"]] = "draft"
    src = write_rows(export / "old.csv", rows)
    res = csvio.import_csv(src, SCOPE, strings, export)
    e = entry_by_id(strings, newer)
    ck((e["es"], e["status"]) == ("Es el año 220X...", "done"), f"older CSV reverted a newer translation: {e}")
    ck(res.kept_newer == 1 and dict(res.changes) == {"es": 1, "status": 1}, f"kept {res.kept_newer} {res.changes}")
    ck([(p.kind, p.refused) for p in res.problems] == [("conflict", True)], f"conflict {res.problems}")
    ck(entry_by_id(strings, rows[z][COL["id"]])["es"] == "", "conflicting row applied")
    forced = csvio.import_csv(src, SCOPE, strings, export, overwrite_newer=True)
    ez = entry_by_id(strings, rows[z][COL["id"]])
    ck(not forced.problems and (ez["es"], ez["status"], ez["note"]) == ("Choque", "draft", ""),
       f"--overwrite-newer {forced.problems} {ez}")
    ck(entry_by_id(strings, newer)["es"] == "Es el año 220X...", "--overwrite-newer reverted an unedited row")
    plain = write_rows(export / "nobase.csv", [r[:COL["base"]] for r in rows])
    ck(raises(lambda: csvio.import_csv(plain, SCOPE, strings, export), ValueError) is not None,
       "CSV without base accepted")
    res = csvio.import_csv(plain, SCOPE, strings, export, no_base=True)
    ck(not res.refused, f"--no-base import refused rows {res.problems[:3]}")
    for rid in (newer, rows[y][COL["id"]], rows[z][COL["id"]]):
        set_entry(strings, rid, es="", status="todo", note="")


def test_bad_encoding(strings: Path, export: Path, ck: Checker) -> None:
    p = export / "ansi.csv"
    p.write_bytes("id,en,es,status,note\r\nx,Señal,,todo,\r\n".encode("cp1252"))
    exc = raises(lambda: csvio.import_csv(p, SCOPE, strings, export), ValueError)
    ck(exc is not None and "UTF-8" in str(exc), f"non-UTF-8 CSV: {exc}")


def test_stray_and_all(strings: Path, ref: Path, export: Path, ck: Checker) -> None:
    phase1 = read_rows(csvio.export(SCOPE, strings, ref, export / "p1").path)
    every = csvio.export("all", strings, ref, export / "all")
    ck(read_rows(every.path) == phase1, "scope 'all' export differs from phase1 when only phase1 exists")
    rows = read_rows(every.path)
    k = todo_rows(rows)[0]
    rows[k][COL["es"]] = "Vía all"
    rows[k][COL["status"]] = "draft"
    res = csvio.import_csv(write_rows(export / "all_edit.csv", rows), "all", strings, export)
    ck(len(res.files_written) == 1 and SCOPE in res.files_written[0].parts,
       f"'all' import wrote {res.files_written}")
    set_entry(strings, rows[k][COL["id"]], es="", status="todo")
    folder = strings / SCOPE / "sf1_eng"
    stray = folder / "rr1_dm0000_eng - Copy.json"
    shutil.copy(folder / "rr1_dm0000_eng.json", stray)
    ck(raises(lambda: csvio.export(SCOPE, strings, ref, export), extract.WorkFileError) is not None,
       "export accepted a stray work file copy")
    stray.unlink()


def test_stats(strings: Path, ck: Checker) -> None:
    st = csvio.stats(SCOPE, strings)
    total = sum(len(d["entries"]) for _, d in extract.scope_docs(SCOPE, strings))
    ck(sum(st["group"].values()) == total == sum(st["category"].values()), "stats do not add up")
    ck(not any(f.startswith("auto:") for f in st["flags"]) and st["flags"]["filter_list"] == 203, "stats flags")
    lines = csvio.stats_lines(SCOPE, strings)
    ck(any(line.startswith("total") and str(total) in line for line in lines), "stats total line")


def main(argv: list[str] | None = None) -> int:
    t0 = time.time()
    ck = Checker()
    with tempfile.TemporaryDirectory(prefix="sfes_test_csvio_") as td:
        strings, ref, export = setup(Path(td))
        test_refds(strings, Path(td), ck)
        out = test_export(strings, ref, export, ck)
        before = tree_hashes(strings)
        test_export_guard(out, strings, ref, export, ck)
        ck(tree_hashes(strings) == before, "export guard test left work-file changes")
        test_roundtrip(out, strings, export, ck)
        test_edits(out, strings, export, ck)
        test_lost_update(strings, ref, export, ck)
        test_bad_encoding(strings, export, ck)
        test_stray_and_all(strings, ref, export, ck)
        test_stats(strings, ck)
    print(f"test_csvio: {ck.passed} checks passed, {len(ck.failed)} failed ({time.time() - t0:.1f}s)")
    return 1 if ck.failed else 0
