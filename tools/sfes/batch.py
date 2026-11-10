r"""Translation batches (Phase 4): give translators their entries, take their Spanish back safely, import the
approved name tables, lint what the glossary cannot express, and report progress.

dump          run.py batch dump --stem STEM [--stem ...] [--status todo,draft] [--out FILE]
              JSON {"files": [{stem, path, category, profile, limit, entries: [{id, en, ja, ref, speaker,
              status, es, note, flags, wrap}]}]}, entries in file order (dialogue context).
              ref      local DS PAL Spanish (Spain Spanish, 2007): a terminology hint only; never copy its
                       regional forms or its 10-character abbreviations.
              speaker  from work/glossary/cache/speakers_sf1.json when its confidence is high or medium.
              ja       the entry's own Japanese; for SF1 *_eng files (English only) the Japanese of the same
                       game message from work/glossary/cache/ja_align_sf1.json, marked "[mensaje] ": the
                       Japanese splits boxes differently, so it may hold neighbouring boxes too.
apply         run.py batch apply --in FILE [--status draft|done|reviewed] [--dry-run]
              FILE: a JSON list (or {"rows": [...]}) of {"id", "es", optional "note", optional "status",
              optional "wrap" ("auto" or "manual": e.g. "manual" keeps a deliberate line layout such as the
              transformation shout)}.
              A row with "status": "keep" and no es marks an entry as intentionally English (es emptied);
              a row with "status": "todo", no es and a "note" only records a note on an entry left todo
              (e.g. "SF2/SF3 scope").
              Each row is refused (and reported) unless: the id exists; the entry is not skip/empty; for a
              translated status es has text, the same codes as the English (textrules, DATETIME formats
              checked), and no NUL/lone surrogate/C0 control besides line breaks. Line breaks are stored
              as CRLF. Accepted rows are written under a per-file lock (<work file>.lock), so translators
              working on different files never collide; exit code 1 when any row is refused.
import-names  run.py batch import-names [--dry-run] [--force]
              Applies work/glossary/names_proposed.json: status_hint "draft" -> es + status draft, "keep" ->
              status keep, only on entries still todo (or keep for a draft hint); --force also replaces a
              different existing translation. Partial copies are left for the translators.
lint          run.py batch lint [--stem STEM ...] [--min-status draft]
              L_VOSOTROS (vosotros/vuestro/os, -áis/-éis verbs), L_USTED (singular "usted"), L_SI ("Si"
              before ! . , : probably "Sí"), L_THOUSANDS (1.000 / 1,000), L_ARTICLE_INSERT (article before an
              inserted item/card name or <REF>), L_ENGLISH (frequent English words left in the Spanish).
              Warnings only; exit code 1 with --strict when any is found.
progress      run.py batch progress [--by category|group|file]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

from . import codes, config, csvio, extract, refds, textrules
from . import scope as scopes

SCOPE = "phase1"
SPEAKERS_PATH = config.WORK / "glossary" / "cache" / "speakers_sf1.json"
JA_ALIGN_PATH = config.WORK / "glossary" / "cache" / "ja_align_sf1.json"   # work/glossary/scripts/ja_align.py
JA_MESSAGE_MARK = "[mensaje] "
NAMES_PATH = config.WORK / "glossary" / "names_proposed.json"
ROW_STATUSES = ("draft", "done", "reviewed", "keep", "todo")
TRANSLATED = ("draft", "done", "reviewed")
CLOSED = ("skip", "empty")
LOCK_TIMEOUT = 60.0
BAD_CHARS = re.compile(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")
# A literal escape sequence (backslash + n, r, t, quote or uXXXX) left by a converter: the game would print it.
LITERAL_ESCAPE = re.compile(r"\\(?:[nrt\"]|u[0-9a-fA-F]{4})")

LINT_RULES = [
    ("L_VOSOTROS", re.compile(r"\b(?:vosotros|vosotras|vuestr[oa]s?|os)\b|\b\w+(?:áis|éis)\b")),
    ("L_USTED", re.compile(r"\busted\b", re.I)),
    ("L_SI", re.compile(r"(?:^|[\s¡¿\"'(])[Ss]i(?=[!.,:]|\s*\r?\n|$)")),
    ("L_THOUSANDS", re.compile(r"(?<![\d.,])\d{1,3}(?:[.,]\d{3})+(?![\d.,])")),
    ("L_ARTICLE_INSERT", re.compile(r"\b(?:el|la|los|las|un|una|unos|unas|del|al)\s+"
                                    r"(?:_CMD_insert_(?:itemname|btlwarename|itemnamefree|btlwarenamefree)|<REF )",
                                    re.I)),
    ("L_ENGLISH", re.compile(r"\b(?:the|and|you|your|with|this|that|what|don't|it's|I'm)\b", re.I)),
]


# ------------------------------------------------------------------------------------------- files

def work_files(scope: str = SCOPE, strings_dir: Path = config.STRINGS) -> dict[str, Path]:
    """stem -> work file path for every group folder of the scope."""
    out: dict[str, Path] = {}
    for group in scopes.SCOPE_GROUPS[scope]:
        for p in sorted((Path(strings_dir) / scopes.home_scope(group) / group).glob("*.json")):
            out[p.stem] = p
    return out


@contextmanager
def file_lock(path: Path, timeout: float = LOCK_TIMEOUT):
    lock = path.with_name(path.name + ".lock")
    t0 = time.monotonic()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() - t0 > timeout:
                raise TimeoutError(f"{lock} is held by another process; remove it if no translator is running")
            time.sleep(0.2)
    try:
        yield
    finally:
        try:
            os.remove(lock)
        except FileNotFoundError:
            pass


def _speakers() -> dict[str, list]:
    try:
        with open(SPEAKERS_PATH, encoding="utf-8") as f:
            return json.load(f).get("entries", {})
    except (OSError, ValueError):
        return {}


def _ja_aligned() -> dict[str, str]:
    try:
        with open(JA_ALIGN_PATH, encoding="utf-8") as f:
            return json.load(f).get("entries", {})
    except (OSError, ValueError):
        return {}


# -------------------------------------------------------------------------------------------- dump

def dump(stems: list[str], statuses: set[str] | None = None, scope: str = SCOPE,
         strings_dir: Path = config.STRINGS) -> dict:
    files = work_files(scope, strings_dir)
    missing = [s for s in stems if s not in files]
    if missing:
        raise KeyError(f"no work file for: {', '.join(missing)}")
    speakers = _speakers()
    ja_aligned = _ja_aligned()
    out = []
    for stem in stems:
        doc = extract.load_json(files[stem])
        refs = refds.load(doc["scope"], stem)
        rows = []
        for e in doc["entries"]:
            if statuses and e["status"] not in statuses:
                continue
            sp = speakers.get(e["id"])
            ja = e["ja"] or (JA_MESSAGE_MARK + ja_aligned[e["id"]] if e["id"] in ja_aligned else "")
            rows.append({"id": e["id"], "en": e["en"], "ja": ja, "ref": csvio.ref_text(refs.get(e["name"])),
                         "speaker": sp[0] if sp and sp[1] in ("high", "medium") else "",
                         "status": e["status"], "es": e["es"], "note": e["note"], "flags": e["flags"],
                         "wrap": e["wrap"]})
        out.append({"stem": stem, "path": doc["path"], "category": doc["category"], "profile": doc["profile"],
                    "limit": csvio.file_limit(doc), "entries": rows})
    return {"files": out}


# ------------------------------------------------------------------------------------------- apply

def row_problem(entry: dict, row: dict, status: str) -> str | None:
    """Why `row` cannot be written to `entry` (None when it can)."""
    if entry["status"] in CLOSED:
        return f"entry status is {entry['status']}"
    if status not in ROW_STATUSES:
        return f"unknown status {status!r}"
    es = row.get("es", "")
    if not isinstance(es, str):
        return "es must be a text"
    if "wrap" in row and row["wrap"] not in ("auto", "manual"):
        return f"wrap must be auto or manual, not {row['wrap']!r}"
    if status in ("keep", "todo"):
        if es:
            return f"a {status} row must not carry es"
        if status == "todo" and not isinstance(row.get("note"), str):
            return "a todo row only sets a note: give \"note\""
        if status == "todo" and entry["status"] != "todo":
            return f"a todo row only annotates an untranslated entry (this one is {entry['status']})"
        return None
    if not es.strip():
        return "empty es for a translated status"
    if not entry["en"].strip():
        return "the English is empty"
    if BAD_CHARS.search(es):
        return "NUL, control character or lone surrogate in es"
    m = LITERAL_ESCAPE.search(es)
    if m and not LITERAL_ESCAPE.search(entry["en"]):
        return f"literal escape sequence {m.group(0)!r} in es (write a real line break or character)"
    missing, extra = textrules.code_diff(entry["en"], es)
    if missing or extra:
        return f"codes differ: missing {missing} extra {extra}"
    fmt = textrules.format_problems(entry["en"], es)
    if fmt:
        return "; ".join(fmt)
    return None


def apply_rows(rows: list[dict], default_status: str = "draft", scope: str = SCOPE,
               strings_dir: Path = config.STRINGS, dry_run: bool = False) -> dict:
    files = work_files(scope, strings_dir)
    by_stem: dict[str, list[dict]] = defaultdict(list)
    refused: list[dict] = []
    for k, row in enumerate(rows):
        rid = row.get("id") if isinstance(row, dict) else None
        stem = rid.split("#", 1)[0] if isinstance(rid, str) else None
        if stem not in files:
            refused.append({"row": k, "id": rid, "reason": "unknown id"})
            continue
        by_stem[stem].append(row)
    accepted = 0
    changed_files = 0
    for stem, srows in sorted(by_stem.items()):
        path = files[stem]
        with file_lock(path):
            doc = extract.load_json(path)
            index = {e["id"]: e for e in doc["entries"]}
            changed = False
            for row in srows:
                e = index.get(row["id"])
                status = row.get("status", default_status)
                why = "unknown id" if e is None else row_problem(e, row, status)
                if why:
                    refused.append({"id": row["id"], "reason": why})
                    continue
                es = extract.normalize_breaks(row.get("es", "")) if status in TRANSLATED else ""
                new = dict(e, es=es, status=status)
                if "note" in row and isinstance(row["note"], str):
                    new["note"] = row["note"]
                if "wrap" in row:
                    new["wrap"] = row["wrap"]
                if new != e:
                    e.clear()
                    e.update(new)
                    changed = True
                accepted += 1
            if changed and not dry_run:
                extract.write_if_changed(path, extract.dump_json(doc))
                changed_files += 1
    return {"accepted": accepted, "refused": refused, "files_changed": changed_files, "dry_run": dry_run}


# ------------------------------------------------------------------------------------------ names

def import_names(names_path: Path = NAMES_PATH, scope: str = SCOPE, strings_dir: Path = config.STRINGS,
                 dry_run: bool = False, force: bool = False) -> dict:
    with open(names_path, encoding="utf-8") as f:
        records = json.load(f)["records"]
    plan: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("status_hint") in ("draft", "keep"):
            plan[r["id"].split("#", 1)[0]].append(r)
    files = work_files(scope, strings_dir)
    stats = Counter()
    problems = []
    for stem, recs in sorted(plan.items()):
        if stem not in files:
            stats["file_missing"] += len(recs)
            continue
        with file_lock(files[stem]):
            doc = extract.load_json(files[stem])
            index = {e["id"]: e for e in doc["entries"]}
            changed = False
            for r in recs:
                e = index.get(r["id"])
                if e is None:
                    stats["id_missing"] += 1
                    problems.append(f"{r['id']}: not in the work file")
                    continue
                if e["en"] != r["en"]:
                    stats["en_changed"] += 1
                    problems.append(f"{r['id']}: English differs from the proposal")
                    continue
                if r["status_hint"] == "keep":
                    if e["status"] == "todo":
                        e.update(status="keep", es="")
                        stats["keep"] += 1
                        changed = True
                    else:
                        stats["unchanged"] += 1
                    continue
                if e["status"] in TRANSLATED and e["es"] and e["es"] != r["es"] and not force:
                    stats["conflict"] += 1
                    problems.append(f"{r['id']}: already translated differently ({e['es']!r})")
                    continue
                if e["status"] not in ("todo", "keep") and not force and e["es"] == r["es"]:
                    stats["unchanged"] += 1
                    continue
                why = row_problem(e, {"es": r["es"]}, "draft")
                if why:
                    stats["refused"] += 1
                    problems.append(f"{r['id']}: {why}")
                    continue
                e.update(es=extract.normalize_breaks(r["es"]), status="draft")
                stats["draft"] += 1
                changed = True
            if changed and not dry_run:
                extract.write_if_changed(files[stem], extract.dump_json(doc))
                stats["files_changed"] += 1
    return {"stats": dict(sorted(stats.items())), "problems": problems, "dry_run": dry_run}


# ------------------------------------------------------------------------------------------- lint

def _plain(text: str) -> str:
    """Text with non-insert codes removed (inserts kept so article checks can see them)."""
    out = []
    for tok in codes.tokenize(text):
        if tok.kind == "text" or tok.kind == "nl":
            out.append(tok.raw)
        elif tok.prints == "insert":
            out.append(tok.raw)
        else:
            out.append(" ")
    return "".join(out)


def lint_text(es: str) -> list[tuple[str, str]]:
    plain = _plain(es)
    found = []
    for code, rx in LINT_RULES:
        m = rx.search(plain)
        if m:
            found.append((code, m.group(0).strip()))
    return found


def lint(stems: list[str] | None = None, min_status: str = "draft", scope: str = SCOPE,
         strings_dir: Path = config.STRINGS) -> list[dict]:
    wanted = set(TRANSLATED[TRANSLATED.index(min_status):])
    files = work_files(scope, strings_dir)
    out = []
    for stem in (stems or sorted(files)):
        doc = extract.load_json(files[stem])
        for e in doc["entries"]:
            if e["status"] not in wanted or not e["es"]:
                continue
            for code, hit in lint_text(e["es"]):
                out.append({"id": e["id"], "code": code, "hit": hit, "es": e["es"][:120]})
    return out


# --------------------------------------------------------------------------------------- progress

def progress(by: str = "category", scope: str = SCOPE, strings_dir: Path = config.STRINGS) -> dict:
    table: dict[str, Counter] = defaultdict(Counter)
    for stem, path in work_files(scope, strings_dir).items():
        doc = extract.load_json(path)
        key = {"category": doc["category"], "group": doc["group"], "file": stem}[by]
        for e in doc["entries"]:
            table[key][e["status"]] += 1
            if e["status"] in TRANSLATED:
                table[key]["chars_en_translated"] += len(codes.strip_markup(e["en"]))
            elif e["status"] == "todo":
                table[key]["chars_en_todo"] += len(codes.strip_markup(e["en"]))
    return {k: dict(v) for k, v in sorted(table.items())}


# -------------------------------------------------------------------------------------------- CLI

def _read_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    rows = data.get("rows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected a JSON list of rows or {{\"rows\": [...]}}")
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="run.py batch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--stem", action="append", required=True)
    d.add_argument("--status", default="")
    d.add_argument("--out")
    a = sub.add_parser("apply")
    a.add_argument("--in", dest="infile", required=True)
    a.add_argument("--status", default="draft", choices=ROW_STATUSES)
    a.add_argument("--dry-run", action="store_true")
    n = sub.add_parser("import-names")
    n.add_argument("--dry-run", action="store_true")
    n.add_argument("--force", action="store_true")
    li = sub.add_parser("lint")
    li.add_argument("--stem", action="append")
    li.add_argument("--min-status", default="draft", choices=TRANSLATED)
    li.add_argument("--strict", action="store_true")
    p = sub.add_parser("progress")
    p.add_argument("--by", default="category", choices=("category", "group", "file"))
    args = ap.parse_args(argv)

    if args.cmd == "dump":
        stems = [s for item in args.stem for s in item.split(",") if s]
        statuses = {s for s in args.status.split(",") if s} or None
        data = json.dumps(dump(stems, statuses), ensure_ascii=False, indent=1)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(data + "\n", encoding="utf-8")
            n_entries = sum(len(f["entries"]) for f in json.loads(data)["files"])
            print(f"wrote {args.out}: {len(stems)} file(s), {n_entries} entries")
        else:
            print(data)
        return 0
    if args.cmd == "apply":
        res = apply_rows(_read_rows(Path(args.infile)), args.status, dry_run=args.dry_run)
        print(f"accepted {res['accepted']}, refused {len(res['refused'])}, files changed {res['files_changed']}"
              + (" (dry run)" if args.dry_run else ""))
        for r in res["refused"]:
            print(f"  REFUSED {r.get('id')}: {r['reason']}")
        return 1 if res["refused"] else 0
    if args.cmd == "import-names":
        res = import_names(dry_run=args.dry_run, force=args.force)
        print("import-names", "(dry run)" if args.dry_run else "", res["stats"])
        for line in res["problems"][:60]:
            print("  " + line)
        if len(res["problems"]) > 60:
            print(f"  ... {len(res['problems']) - 60} more")
        return 0
    if args.cmd == "lint":
        stems = [s for item in (args.stem or []) for s in item.split(",") if s] or None
        found = lint(stems, args.min_status)
        counts = Counter(f["code"] for f in found)
        for f in found:
            print(f"{f['code']:17s} {f['id']}  [{f['hit']}]  {f['es']!r}")
        print(f"lint: {len(found)} finding(s) {dict(sorted(counts.items()))}")
        return 1 if (found and args.strict) else 0
    if args.cmd == "progress":
        table = progress(args.by)
        cols = ["todo", "draft", "done", "reviewed", "keep", "skip", "empty"]
        print(f"{'':28s} " + " ".join(f"{c:>8s}" for c in cols) + "   chars_todo chars_done")
        tot = Counter()
        for k, v in table.items():
            tot.update(v)
            print(f"{k[:28]:28s} " + " ".join(f"{v.get(c, 0):8d}" for c in cols)
                  + f"   {v.get('chars_en_todo', 0):10d} {v.get('chars_en_translated', 0):10d}")
        print(f"{'TOTAL':28s} " + " ".join(f"{tot.get(c, 0):8d}" for c in cols)
              + f"   {tot.get('chars_en_todo', 0):10d} {tot.get('chars_en_translated', 0):10d}")
        return 0
    return 2
