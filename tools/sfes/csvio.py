r"""CSV export and import of work files for spreadsheet work (contract section 4).

export  work/export/<scope>.csv: UTF-8 with BOM, every cell quoted, CRLF row ends, real line breaks
        inside cells, one row per entry. Columns:
          id, file, entry, category, profile, status, en, ja, ref_es, es, note, en_lines, limit, base
        ref_es    local DS Spanish reference (work/ref/ds_es); non-exact or whole-script references start
                  with a marker such as "[fuzzy 0.893] " or "[script] "
        en_lines  number of lines of the English body (trailing breaks not counted)
        limit     "3 lines x 1026 px" for hard dialogue profiles, or for observed profiles
                  "<= English (file max: 2 lines, 17 chars/line)" (exceeding it is a warning)
        base      fingerprint of es/status/note at export time (do not edit). It lets import tell an edit
                  made in this CSV from a newer change in the work file.
        An existing CSV that holds edits not yet imported is never overwritten: export stops and names the
        rows; --force first keeps the old file as <scope>.bak-NNN.csv.
import  reads a CSV saved by a spreadsheet or a text editor (UTF-8 with or without BOM, or UTF-16;
        comma, semicolon or tab separated) and updates only es, status and note, matched by id. Per row:
          - refused: unknown or repeated id, a cell count other than the header's, a damaged base cell;
          - en differs from the work file (line-break style ignored): refused if the row is edited,
            otherwise only reported;
          - row not edited since the export (values match base): nothing is applied, so a newer work file
            (another import, a JSON edit) is never reverted;
          - row edited while the work file also changed since the export: conflict, refused
            (--overwrite-newer applies the CSV row anyway, after a person checked it);
          - the result must be consistent, else refused: a valid status; English empty -> status empty and
            no es; status empty only for empty English; draft/done/reviewed need es;
          - es filled while the status stays todo/keep/skip: applied with a warning (the build ignores es);
          - line breaks in es and note are normalized to CRLF (spreadsheets save LF-only cells).
        A CSV without the base column (hand-made, or exported by an older version) needs --no-base, which
        compares against the work files only. Only work files whose content changes are rewritten.
        The summary is printed and written to work/export/<scope>_import.txt.
stats   entry counts by group, category and status.
Scope 'all' reads and writes the phase1, sf2 and sf3 work files together.

CLI: run.py csvio export --scope phase1 [--force]
     run.py csvio import --scope phase1 [--file PATH] [--dry-run] [--overwrite-newer] [--no-base]
     run.py csvio stats --scope phase1
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import codes, config, profiles, refds
from . import scope as scopes
from .extract import (AUTO_PREFIX, STATUSES, TRANSLATED, dump_json, normalize_breaks, scope_docs,
                      status_is_auto, write_if_changed)

COLUMNS = ["id", "file", "entry", "category", "profile", "status", "en", "ja", "ref_es", "es", "note",
           "en_lines", "limit", "base"]
REQUIRED = ("id", "en", "es", "status", "note")
FIELDS = ("es", "status", "note")
BASE_RX = re.compile(r"h[a-z2-7]{15}")     # starts with a letter so spreadsheets never read it as a number
DELIMITERS = ",;\t"
PRINT_LIMIT = 40
PREVIEW = 40


class ExportBlocked(ValueError):
    """The export target holds edits that are not in the work files yet."""


def export_path(scope: str, export_dir: Path = config.EXPORT) -> Path:
    return Path(export_dir) / f"{scope}.csv"


def body_lines(en: str) -> int:
    body, _ = codes.body_and_trailing(en)
    return len(codes.split_lines(body)) if body else 0


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def file_limit(doc: dict) -> str:
    """Human-readable size limit shared by every entry of a work file."""
    prof = profiles.PROFILES[doc["profile"]]
    if prof.hard:
        return f"{plural(prof.max_lines, 'line')} x {prof.max_px:g} px"
    entries = doc["entries"]
    lines = max((body_lines(e["en"]) for e in entries), default=0)
    chars = max((len(codes.strip_markup(line)) for e in entries for line in codes.split_lines(e["en"])),
                default=0)
    return f"<= English (file max: {plural(lines, 'line')}, {chars} chars/line)"


def ref_text(ref: dict | None) -> str:
    if not ref:
        return ""
    marks = [] if ref["match"] == "exact" else [f"{ref['match']} {ref['score']}"]
    if ref.get("level") == "script":
        marks.append("script")
    return "".join(f"[{m}] " for m in marks) + ref["es"]


# ---------------------------------------------------------------- the translator fields

def entry_fields(entry: dict) -> dict[str, str]:
    return {"es": normalize_breaks(entry.get("es", "")), "status": entry.get("status", ""),
            "note": normalize_breaks(entry.get("note", ""))}


def row_fields(row: dict[str, str]) -> dict[str, str]:
    return {"es": normalize_breaks(row.get("es", "")), "status": row.get("status", "").strip().lower(),
            "note": normalize_breaks(row.get("note", ""))}


def fingerprint(fields: dict[str, str]) -> str:
    """Short, spreadsheet-safe fingerprint of es/status/note (the base column)."""
    raw = json.dumps([fields[k] for k in FIELDS], ensure_ascii=False).encode("utf-8")
    return "h" + base64.b32encode(hashlib.sha256(raw).digest()[:10]).decode("ascii").lower()[:15]


def preview(text: str) -> str:
    flat = text.replace("\r\n", " / ")
    return repr(flat if len(flat) <= PREVIEW else flat[:PREVIEW] + "...")


# ---------------------------------------------------------------- reading CSV files

@dataclass
class Record:
    n: int                   # record number in the file (header = 1), i.e. the spreadsheet row
    width: int               # number of cells
    row: dict[str, str]


@dataclass
class Table:
    names: list[str]
    records: list[Record]


def decode_table(raw: bytes, path: Path) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: not UTF-8 ({exc}); save it as 'CSV UTF-8'") from None


def read_table(path: Path) -> Table:
    """Header names and non-empty records of a CSV. Separator detected from the header line."""
    text = decode_table(Path(path).read_bytes(), Path(path)).lstrip("\ufeff")   # editors may add a 2nd BOM
    header = text.split("\n", 1)[0]
    delimiter = max(DELIMITERS, key=header.count)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    names = [h.strip() for h in next(reader, [])]
    missing = [c for c in REQUIRED if c not in names]
    if missing:
        raise ValueError(f"{path}: missing columns {', '.join(missing)}")
    records = [Record(n, len(cells), dict(zip(names, cells))) for n, cells in enumerate(reader, start=2)
               if any(cells)]
    return Table(names, records)


def entry_index(docs: list[tuple[Path, dict]]) -> dict[str, tuple[int, dict]]:
    """id -> (document number, entry)."""
    return {e["id"]: (k, e) for k, (_, doc) in enumerate(docs) for e in doc["entries"]}


# ---------------------------------------------------------------- export

def export_rows(docs: list[tuple[Path, dict]], ref_dir: Path) -> list[list[str]]:
    rows = []
    for _, doc in docs:
        stem = scopes.stem_of(doc["path"])
        refs = refds.load(doc["scope"], stem, ref_dir)
        limit = file_limit(doc)
        for e in doc["entries"]:
            rows.append([e["id"], stem, e["name"], doc["category"], doc["profile"], e["status"], e["en"],
                         e["ja"], ref_text(refs.get(e["name"])), e["es"], e["note"],
                         str(body_lines(e["en"])), limit, fingerprint(entry_fields(e))])
    return rows


def csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def pending_edits(path: Path, docs: list[tuple[Path, dict]]) -> list[str]:
    """Rows of an existing CSV holding edits that are not in the work files (empty: safe to overwrite)."""
    try:
        table = read_table(path)
    except ValueError as exc:
        return [f"the file cannot be checked: {exc}"]
    index = entry_index(docs)
    has_base = "base" in table.names
    out = []
    for rec in table.records:
        rid = rec.row.get("id", "").strip()
        _, entry = index.get(rid, (None, None))
        if entry is None or rec.width != len(table.names):
            out.append(f"row {rec.n} {rid or '(no id)'} (not a complete row of this scope)")
            continue
        new = row_fields(rec.row)
        unedited = has_base and fingerprint(new) == rec.row.get("base", "").strip()
        if new != entry_fields(entry) and not unedited:
            out.append(f"row {rec.n} {rid}")
    return out


def backup_path(out: Path) -> Path:
    """First free <stem>.bak-NNN<suffix> next to `out`."""
    n = 1
    while (p := out.with_name(f"{out.stem}.bak-{n:03d}{out.suffix}")).exists():
        n += 1
    return p


@dataclass(frozen=True)
class Exported:
    path: Path
    rows: int
    backup: Path | None = None        # where a CSV with un-imported edits was kept (--force)


def export(scope: str, strings_dir: Path = config.STRINGS, ref_dir: Path = refds.DS_REF,
           export_dir: Path = config.EXPORT, force: bool = False) -> Exported:
    """Write work/export/<scope>.csv. Raises ExportBlocked if the existing file holds un-imported edits."""
    docs = scope_docs(scope, strings_dir)
    rows = export_rows(docs, ref_dir)
    data = csv_bytes(COLUMNS, rows)
    out = export_path(scope, export_dir)
    backup = None
    if out.exists() and out.read_bytes() != data:
        pending = pending_edits(out, docs)
        if pending and not force:
            more = f" and {len(pending) - 5} more" if len(pending) > 5 else ""
            raise ExportBlocked(f"{out} holds {plural(len(pending), 'row')} with edits that are not in the work "
                                f"files ({'; '.join(pending[:5])}{more}). Import it first (csvio import --scope "
                                f"{scope}), or export with --force to keep it as a .bak-NNN.csv copy.")
        if pending:
            backup = backup_path(out)
            os.replace(out, backup)
    write_if_changed(out, data)
    return Exported(out, len(rows), backup)


# ---------------------------------------------------------------- import

@dataclass
class Problem:
    """A reported row. Kinds: unknown_id, duplicate_id, bad_row, bad_base, en_mismatch, conflict,
    bad_status (refused) and es_unused (warning only)."""
    row: int          # spreadsheet row number (header = 1)
    id: str
    kind: str
    detail: str
    refused: bool = True


@dataclass
class Verdict:
    """What one CSV row does to its entry."""
    edits: dict[str, str] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)
    kept_newer: bool = False          # row unedited, the work file changed after the export

    @property
    def refused(self) -> bool:
        return any(p.refused for p in self.problems)


@dataclass(frozen=True)
class Options:
    width: int                        # cells per row (header)
    has_base: bool
    overwrite_newer: bool


@dataclass
class ImportResult:
    source: Path
    rows: int = 0
    applied: int = 0
    kept_newer: int = 0
    changes: Counter = field(default_factory=Counter)      # es / status / note -> entries changed
    files_written: list[Path] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    @property
    def refused(self) -> int:
        return sum(p.refused for p in self.problems)


def state_problem(fields: dict[str, str], en: str) -> str | None:
    """Why es/status would be inconsistent for an entry with English `en`, or None."""
    status, es = fields["status"], fields["es"]
    if status not in STATUSES:
        return f"status {status!r} is not one of {', '.join(STATUSES)}"
    if en == "":
        return None if status == "empty" and es == "" else "English is empty: status stays 'empty', es empty"
    if status == "empty":
        return "status 'empty' is only for entries whose English is empty"
    if status in TRANSLATED and es == "":
        return f"status {status!r} needs the Spanish text in es"
    return None


def unused_es(n: int, rid: str, fields: dict[str, str]) -> Problem | None:
    if fields["es"] and fields["status"] not in TRANSLATED:
        return Problem(n, rid, "es_unused", f"es is filled but status is {fields['status']!r}: the build "
                       "uses es only for draft, done and reviewed", refused=False)
    return None


def judge_row(rec: Record, rid: str, entry: dict | None, repeated: bool, opt: Options) -> Verdict:
    """Decide what a CSV row may change (see the module docstring for the rules)."""
    def refuse(kind: str, detail: str) -> Verdict:
        return Verdict(problems=[Problem(rec.n, rid, kind, detail)])

    if repeated:
        return refuse("duplicate_id", "id already imported from an earlier row")
    if entry is None:
        return refuse("unknown_id", "no such entry in the work files")
    if rec.width != opt.width:
        return refuse("bad_row", f"{rec.width} cells, the header has {opt.width}; not applied")
    new, cur = row_fields(rec.row), entry_fields(entry)
    base = rec.row.get("base", "").strip() if opt.has_base else None
    if base is not None and not BASE_RX.fullmatch(base):
        return refuse("bad_base", f"base cell {base!r} is not an export fingerprint; not applied")
    edited = new != cur if base is None else fingerprint(new) != base
    newer = base is not None and fingerprint(cur) != base
    if normalize_breaks(rec.row.get("en", "")) != normalize_breaks(entry["en"]):
        detail = "English differs from the work file" + ("; row has edits, not applied" if edited else "")
        return Verdict(problems=[Problem(rec.n, rid, "en_mismatch", detail, refused=edited)])
    if not edited or new == cur:
        return Verdict(kept_newer=newer and new != cur)
    if newer and not opt.overwrite_newer:
        return refuse("conflict", f"edited here, and the work file changed after the export (now: status "
                                  f"{cur['status']}, es {preview(cur['es'])}); re-export and redo the row, or "
                                  "import with --overwrite-newer")
    bad = state_problem(new, entry["en"])
    if bad:
        return refuse("bad_status", bad)
    warning = unused_es(rec.n, rid, new)
    return Verdict(edits={k: v for k, v in new.items() if v != cur[k]}, problems=[warning] if warning else [])


def import_csv(path: Path, scope: str, strings_dir: Path = config.STRINGS, export_dir: Path = config.EXPORT,
               dry_run: bool = False, overwrite_newer: bool = False, no_base: bool = False) -> ImportResult:
    """Apply es/status/note from a CSV to the work files of `scope`."""
    docs = scope_docs(scope, strings_dir)
    table = read_table(path)
    if "base" not in table.names and not no_base:
        raise ValueError(f"{path}: no 'base' column (hand-made, or exported by an older version); re-export, "
                         "or import with --no-base to compare against the work files only")
    opt = Options(len(table.names), "base" in table.names and not no_base, overwrite_newer)
    index = entry_index(docs)
    result = ImportResult(Path(path), rows=len(table.records))
    seen: set[str] = set()
    dirty: set[int] = set()
    for rec in table.records:
        rid = rec.row.get("id", "").strip()
        k, entry = index.get(rid, (None, None))
        verdict = judge_row(rec, rid, entry, rid in seen, opt)
        seen.add(rid)
        result.problems += verdict.problems
        if verdict.refused:
            continue
        result.applied += 1
        result.kept_newer += verdict.kept_newer
        for key, value in verdict.edits.items():
            entry[key] = value
            result.changes[key] += 1
            dirty.add(k)
    for k in sorted(dirty):
        p, doc = docs[k]
        if not dry_run and write_if_changed(p, dump_json(doc)):
            result.files_written.append(p)
    report = "\n".join(import_lines(result, scope, dry_run, limit=None)) + "\n"
    write_if_changed(Path(export_dir) / f"{scope}_import.txt", report.encode("utf-8"))
    return result


def import_lines(res: ImportResult, scope: str, dry_run: bool, limit: int | None = PRINT_LIMIT) -> list[str]:
    changed = ", ".join(f"{k} {res.changes[k]}" for k in FIELDS)
    kinds = Counter(p.kind for p in res.problems)
    lines = [f"import {res.source} -> scope {scope}{' (dry run, nothing written)' if dry_run else ''}",
             f"  rows {res.rows}: accepted {res.applied}, refused {res.refused}, "
             f"warnings {len(res.problems) - res.refused}",
             f"  fields changed: {changed}; work files written {len(res.files_written)}"]
    if res.kept_newer:
        lines.append(f"  unedited rows left alone (the work file is newer than this CSV): {res.kept_newer}")
    if kinds:
        lines.append("  problems: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    shown = res.problems if limit is None else res.problems[:limit]
    for p in shown:
        lines.append(f"    row {p.row:6d} {p.id}  {p.kind}{' (refused)' if p.refused else ''}: {p.detail}")
    if len(shown) < len(res.problems):
        lines.append(f"    ... {len(res.problems) - len(shown)} more in the report file")
    if res.changes or res.kept_newer or kinds["conflict"]:
        lines.append("  re-export (csvio export) before editing further, so that base matches the work files")
    return lines


# ---------------------------------------------------------------- stats

def stats(scope: str, strings_dir: Path = config.STRINGS) -> dict[str, Counter]:
    """Counters of (key, status) for key kinds 'group' and 'category'; 'flags' (automatic-status flags
    left out) and 'chosen' (statuses set by a person, per group)."""
    out = {"group": Counter(), "category": Counter(), "flags": Counter(), "chosen": Counter()}
    for _, doc in scope_docs(scope, strings_dir):
        for e in doc["entries"]:
            out["group"][(doc["group"], e["status"])] += 1
            out["category"][(doc["category"], e["status"])] += 1
            out["flags"].update(f for f in e["flags"] if not f.startswith(AUTO_PREFIX))
            out["chosen"][doc["group"]] += not status_is_auto(e)
    return out


def table_row(label: str, values: list[int]) -> str:
    return f"{label:22s}{sum(values):8d}" + "".join(f"{v:9d}" for v in values)


def table_lines(title: str, counts: Counter, keys: list[str]) -> list[str]:
    """One row per key with entry counts per status, then a total row."""
    lines = [f"{title:22s}{'entries':>8s}" + "".join(f"{s:>9s}" for s in STATUSES)]
    totals: Counter = Counter()
    for key in keys:
        values = [counts[(key, s)] for s in STATUSES]
        totals.update(dict(zip(STATUSES, values)))
        lines.append(table_row(key, values))
    lines.append(table_row("total", [totals[s] for s in STATUSES]))
    return lines


def stats_lines(scope: str, strings_dir: Path = config.STRINGS) -> list[str]:
    st = stats(scope, strings_dir)
    groups = [g for g in scopes.SCOPE_GROUPS[scope] if any(k[0] == g for k in st["group"])]
    cats = sorted({k[0] for k in st["category"]})
    lines = table_lines("group", st["group"], groups) + [""] + table_lines("category", st["category"], cats)
    lines.append("")
    lines.append("flags: " + (", ".join(f"{k} {v}" for k, v in sorted(st["flags"].items())) or "none"))
    lines.append(f"statuses chosen by a person: {sum(st['chosen'].values())}")
    return lines


# ---------------------------------------------------------------- CLI

def run(args: argparse.Namespace) -> int:
    if args.cmd == "export":
        res = export(args.scope, force=args.force)
        if res.backup:
            print(f"kept the previous CSV, which held edits not yet imported, as {res.backup}")
        print(f"exported {res.rows} rows -> {res.path}")
        return 0
    if args.cmd == "stats":
        print("\n".join(stats_lines(args.scope)))
        return 0
    src = args.file or export_path(args.scope)
    res = import_csv(src, args.scope, dry_run=args.dry_run, overwrite_newer=args.overwrite_newer,
                     no_base=args.no_base)
    print("\n".join(import_lines(res, args.scope, args.dry_run)))
    print(f"  report: {Path(config.EXPORT) / (args.scope + '_import.txt')}")
    return 1 if res.refused else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="csvio", description="CSV export/import of translation work files.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("export", "import", "stats"):
        p = sub.add_parser(name)
        p.add_argument("--scope", default="phase1", choices=scopes.SCOPES)
        if name == "export":
            p.add_argument("--force", action="store_true",
                           help="overwrite a CSV holding edits not yet imported (kept as <scope>.bak-NNN.csv)")
        if name == "import":
            p.add_argument("--file", type=Path, help="CSV to import (default: work/export/<scope>.csv)")
            p.add_argument("--dry-run", action="store_true", help="check and report, write nothing")
            p.add_argument("--overwrite-newer", action="store_true",
                           help="apply edited rows even where the work file changed after the export")
            p.add_argument("--no-base", action="store_true",
                           help="accept a CSV without the base column (no conflict check)")
    args = ap.parse_args(argv)
    try:
        return run(args)
    except PermissionError as exc:
        print(f"csvio {args.cmd}: {exc} (is the file open in a spreadsheet program? close it and retry)")
    except (ValueError, OSError) as exc:      # missing/inconsistent work files, bad CSV, blocked export
        print(f"csvio {args.cmd}: {exc}")
    return 2
