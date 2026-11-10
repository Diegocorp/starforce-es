r"""Work-file extraction and merge (contract section 3).

Creates or merges work/strings/<scope>/<group>/<stem>.json, one per msg.23 file of a base scope (phase1,
sf2, sf3), from the winning (newest) copy of each file in the official paks. Language slot 1 (English) is
the source text, slot 0 (Japanese) is copied for context. Scope 'all' has no work files of its own: it is
the union of the base scopes' folders (scope_docs), so extraction refuses it.

Automatic status of an entry (first rule that applies):
  empty  English is empty
  skip   the file category is in profiles.SKIP_CATEGORIES
  skip   a reviewed block of ENTRY_RULES: profanity filter list (flag filter_list) or name-entry character
         grid (flag layout_grid), the layout data of Phase 1 report section 4
  skip   English contains kana/kanji (test strings, language names) - flag kana_in_en. The marks U+30A0,
         U+30FB, U+30FC and U+FF70, which the English text uses as ellipsis and dashes, do not count
  keep   English has no letters once codes are removed ("......", "<ICON INGAME_KEY_A>", digits, "ーー";
         the kana marks above are not letters here)
  todo   everything else
The automatic status is recorded as flag auto:<status>. A status that differs from it was chosen by a
person (CSV import or JSON edit).

Flags recomputed from the source on every run: auto:<status>, kana_in_en, filter_list, layout_grid;
unreferenced (an entry of an *_eng file whose GUID no *.user.3.en script contains; base-only files are
loaded by code and never flagged); check_inverted (English without letters but with ? or !, e.g. "...?",
kept as English by default although Spanish may want the opening ¿ or ¡); format_string (a
<DATETIME ... "format"> tag: the US date/time format inside the tag may need a Spanish decision).

Merging into an existing work file:
  - es, note and wrap are always kept;
  - status: an automatic status follows the current rules, so English changes and rule changes reach
    untouched entries; a status chosen by a person is kept. Entries written before the auto: flag existed
    count as automatic when es and note are empty and the status is todo, keep, skip or empty;
  - English changed: en is updated and flag source_changed is added (a person removes it after review);
  - ja and the file-level fields (source_pak, source_sha256, category, profile) follow the source;
  - entry structure (count, order, names, guids) changed: error and nothing is written, unless
    --force-structure. Then entries are re-matched by GUID (the game finds lines by GUID), else by the same
    name with the same English, else by identical English that is unique on both sides; the two fallbacks
    add flag relinked. Old entries left without a match that hold work (es, note, a translated or chosen
    status) are appended to work/strings/_dropped/<scope>/<group>/<stem>.json: nothing is discarded.
Files are written only when their bytes change, so a re-run without source changes leaves every file
byte-identical.

CLI: run.py extract --scope phase1 [--force-structure]
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import codes, config, msg23, profiles
from . import scope as scopes
from .pak import PakSet, name_list
from .scope import FileSpec

SCHEMA = 1
STATUSES = ("todo", "draft", "done", "reviewed", "keep", "skip", "empty")
TRANSLATED = frozenset({"draft", "done", "reviewed"})
AUTO_STATUSES = frozenset({"todo", "keep", "skip", "empty"})     # the statuses the rules assign
AUTO_PREFIX = "auto:"
FLAG_SOURCE_CHANGED = "source_changed"
FLAG_RELINKED = "relinked"
FLAG_KANA = "kana_in_en"
FLAG_UNREFERENCED = "unreferenced"
FLAG_INVERTED = "check_inverted"
FLAG_FORMAT = "format_string"
FLAG_FILTER = "filter_list"
FLAG_GRID = "layout_grid"
SOURCE_FLAGS = frozenset({FLAG_KANA, FLAG_UNREFERENCED, FLAG_INVERTED, FLAG_FORMAT, FLAG_FILTER, FLAG_GRID})
DROPPED_DIR = "_dropped"

SCRIPT_SUFFIX = ".user.3.en"
BREAK_RX = re.compile(r"\r\n|\r|\n")
GUID_ALIGN = 8       # RSZ stores Guid fields 8-aligned (verified: same hits as a 4-aligned scan)
# Kana marks that the English text uses as ellipsis ("Was known as・・・") and dashes ("ーー" placeholders):
# U+30A0, U+30FB middle dot, U+30FC and U+FF70 prolonged sound marks. They are punctuation here, although
# str.isalpha() is True for U+30FC and U+FF70.
KANA_MARKS = frozenset("\u30a0\u30fb\u30fc\uff70")
# Hiragana, Katakana, CJK ext. A, CJK unified, CJK compatibility, half-width katakana; without KANA_MARKS.
KANA_RX = re.compile("[\u3040-\u309f\u30a1-\u30fa\u30fd-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
                     "\uff66-\uff6f\uff71-\uff9f]")


@dataclass(frozen=True)
class EntryRule:
    """A reviewed block of entries (indexes first..last) that must not be translated."""
    first: int
    last: int
    flag: str
    guid_digest: str     # guid_digest() of the block: the rule applies only to exactly these entries


# Layout data and filter lists inside translatable files (Phase 1 report section 4; reviewed entry by entry).
ENTRY_RULES: dict[str, tuple[EntryRule, ...]] = {
    "rr1_inputscreen_eng": (EntryRule(0, 23, FLAG_GRID, "f0c502d85db1972d"),
                            EntryRule(105, 307, FLAG_FILTER, "e6337cdd220d48d8")),
    "rr2_subinput_eng": (EntryRule(21, 66, FLAG_GRID, "45d95f7ea187fbe4"),),
    "rr3_subinput_v_eng": (EntryRule(18, 28, FLAG_GRID, "99ead86d7104bfa2"),),
}


class MergeError(Exception):
    """A work file cannot be merged with the source (structure changed, unreadable, other schema)."""


class WorkFileError(ValueError):
    """The work folder of a scope is inconsistent (stray copy, wrong folder, duplicate ids)."""


# ---------------------------------------------------------------- work file I/O (shared by sfes tools)

def work_path(strings_dir: Path, scope: str, group: str, stem: str) -> Path:
    return Path(strings_dir) / scope / group / f"{stem}.json"


def dropped_path(strings_dir: Path, scope: str, group: str, stem: str) -> Path:
    """Side file for entries that left the source (one level deeper than work files, never read as one)."""
    return Path(strings_dir) / DROPPED_DIR / scope / group / f"{stem}.json"


def dump_json(obj: object) -> bytes:
    """Canonical JSON bytes used for every file the toolkit writes (contract section 0.7)."""
    return (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def load_json(path: Path) -> dict:
    """A JSON file (a UTF-8 BOM, which some Windows editors add, is accepted). Raises WorkFileError naming the
    file when it is not valid UTF-8 JSON."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkFileError(f"{path}: not valid UTF-8 JSON ({exc})") from exc


def write_if_changed(path: Path, data: bytes) -> bool:
    """Write `data` atomically unless the file already holds exactly these bytes. True if written.
    On failure (e.g. the file is open in a spreadsheet program) the old file stays and no temp file is left."""
    path = Path(path)
    try:
        if path.read_bytes() == data:
            return False
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return True


def normalize_breaks(text: str) -> str:
    """Any line-break style (CRLF, LF, CR) as CRLF, the game's only line break."""
    return BREAK_RX.sub("\r\n", text)


def entry_id(stem: str, name: str) -> str:
    return f"{stem}#{name}"


def doc_problem(path: Path, doc: dict, scope: str, group: str) -> str | None:
    """Why a JSON file in a group folder is not that folder's work file for its msg, or None."""
    if not isinstance(doc, dict) or not isinstance(doc.get("path"), str) or "entries" not in doc:
        return "not a work file"
    if not isinstance(doc["entries"], list) or not all(isinstance(e, dict) for e in doc["entries"]):
        return "entries is not a list of objects"
    if doc.get("scope") != scope or doc.get("group") != group:
        return f"belongs to {doc.get('scope')}/{doc.get('group')}, not {scope}/{group}"
    if scopes.stem_of(doc["path"]) != path.stem:
        return f"file name does not match its msg {doc['path']} (stray copy?)"
    return None


def load_scope_files(scope: str, strings_dir: Path) -> list[tuple[Path, dict]]:
    """(path, document) of the JSON files in the group folders of a scope, groups in scope order."""
    out = []
    for group in scopes.SCOPE_GROUPS[scope]:
        folder = Path(strings_dir) / scopes.home_scope(group) / group
        out += [(p, load_json(p)) for p in sorted(folder.glob("*.json"))]
    return out


def scope_docs(scope: str, strings_dir: Path = config.STRINGS) -> list[tuple[Path, dict]]:
    """(path, document) of every work file of a scope ('all' = the phase1, sf2 and sf3 folders):
    groups in scope order, then by msg path. Raises FileNotFoundError if there are none, and
    WorkFileError for stray files (e.g. 'x - Copy.json') or ids that occur twice."""
    docs = load_scope_files(scope, strings_dir)
    if not docs:
        raise FileNotFoundError(f"no work files for scope {scope} under {strings_dir}; run: extract --scope "
                                + " / ".join(scopes.members(scope)))
    bad = [f"{p}: {why}" for p, d in docs
           if (why := doc_problem(p, d, scopes.home_scope(p.parent.name), p.parent.name))]
    ids = Counter(e.get("id") for _, d in docs if isinstance(d, dict) and isinstance(d.get("entries"), list)
                  for e in d["entries"] if isinstance(e, dict))
    bad += [f"id {i!r} occurs {n} times" for i, n in sorted(ids.items(), key=str) if n > 1][:20]
    if bad:
        raise WorkFileError("inconsistent work files (move stray copies out of the work folder):\n  "
                            + "\n  ".join(bad))
    order = {g: k for k, g in enumerate(scopes.SCOPE_GROUPS[scope])}
    docs.sort(key=lambda pd: (order[pd[1]["group"]], pd[1]["path"]))
    return docs


# ---------------------------------------------------------------- source side

def guid_digest(guids: list[str]) -> str:
    return hashlib.sha256("\n".join(guids).encode("ascii")).hexdigest()[:16]


def entry_rule_flags(stem: str, guids: list[str]) -> tuple[dict[int, str], list[str]]:
    """Rule flag per entry index, and a warning for every reviewed rule that no longer fits the source."""
    flags: dict[int, str] = {}
    warnings = []
    for rule in ENTRY_RULES.get(stem, ()):
        block = guids[rule.first:rule.last + 1]
        if len(block) == rule.last - rule.first + 1 and guid_digest(block) == rule.guid_digest:
            flags.update(dict.fromkeys(range(rule.first, rule.last + 1), rule.flag))
        else:
            warnings.append(f"{stem}: reviewed {rule.flag} rule for entries {rule.first}-{rule.last} no longer "
                            "matches the source GUIDs; not applied (review the entries, update ENTRY_RULES)")
    return flags, warnings


@dataclass
class Source:
    spec: FileSpec
    pak: str
    sha256: str
    msg: msg23.Msg
    rule_flags: dict[int, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def read_sources(specs: list[FileSpec], ps: PakSet) -> list[Source]:
    out = []
    for spec in specs:
        entry = ps.entry(spec.path)
        raw = ps.read(entry)
        m = msg23.parse(raw)
        rule_flags, warnings = entry_rule_flags(spec.stem, [e.guid for e in m.entries])
        out.append(Source(spec, entry.pak.name, hashlib.sha256(raw).hexdigest(), m, rule_flags, warnings))
    return out


def script_paths(ps: PakSet) -> list[str]:
    """Every English message script (*.user.3.en) present in the official paks."""
    return sorted(p for h, p in name_list().items() if p.endswith(SCRIPT_SUFFIX) and h in ps.winner)


def referenced_guids(ps: PakSet, wanted: set[str]) -> set[str]:
    """The GUIDs of `wanted` that occur as 16-byte Guid fields in any English script."""
    if not wanted:
        return set()
    table = {uuid.UUID(g).bytes_le: g for g in wanted}
    found: set[str] = set()
    for path in script_paths(ps):
        data = ps.read(path)
        for off in range(0, len(data) - 15, GUID_ALIGN):
            g = table.get(data[off:off + 16])
            if g is not None:
                found.add(g)
    return found


def eng_guids(sources: list[Source]) -> set[str]:
    return {e.guid for s in sources if s.spec.is_eng for e in s.msg.entries}


def has_kana(text: str) -> bool:
    return KANA_RX.search(text) is not None


def has_letters(text: str) -> bool:
    """codes.has_letters, with KANA_MARKS counted as punctuation."""
    return any(ch.isalpha() and ch not in KANA_MARKS for ch in codes.strip_markup(text))


def has_format_string(text: str) -> bool:
    """True if a <DATETIME ...> tag carries a quoted date/time format."""
    return any(t.kind == "tag" and t.name == "DATETIME" and '"' in (t.args or "") for t in codes.tokenize(text))


def initial_status(en: str, category: str, rule_flag: str | None = None) -> str:
    if en == "":
        return "empty"
    if category in profiles.SKIP_CATEGORIES or rule_flag or has_kana(en):
        return "skip"
    if not has_letters(en):
        return "keep"
    return "todo"


def source_flags(en: str, guid: str, spec: FileSpec, referenced: set[str],
                 rule_flag: str | None = None) -> list[str]:
    flags = []
    if rule_flag:
        flags.append(rule_flag)
    elif has_kana(en):
        flags.append(FLAG_KANA)
    elif not has_letters(en) and ("?" in en or "!" in en):
        flags.append(FLAG_INVERTED)
    if has_format_string(en):
        flags.append(FLAG_FORMAT)
    if spec.is_eng and guid not in referenced:
        flags.append(FLAG_UNREFERENCED)
    return sorted(flags)


def auto_flag(status: str) -> str:
    return AUTO_PREFIX + status


def new_entry(spec: FileSpec, index: int, e: msg23.MsgEntry, m: msg23.Msg, referenced: set[str],
              rule_flag: str | None = None) -> dict:
    en = m.get(e, config.LANG_EN)
    status = initial_status(en, spec.category, rule_flag)
    return {
        "id": entry_id(spec.stem, e.name),
        "i": index,
        "name": e.name,
        "guid": e.guid,
        "en": en,
        "ja": m.get(e, config.LANG_JA),
        "es": "",
        "status": status,
        "wrap": spec.layout.wrap,
        "note": "",
        "flags": sorted(source_flags(en, e.guid, spec, referenced, rule_flag) + [auto_flag(status)]),
    }


def build_doc(scope: str, src: Source, referenced: set[str]) -> dict:
    """Fresh work-file document for one source file (no translations)."""
    spec = src.spec
    return {
        "schema": SCHEMA,
        "scope": scope,
        "group": spec.group,
        "path": spec.path,
        "source_pak": src.pak,
        "source_sha256": src.sha256,
        "category": spec.category,
        "profile": spec.profile,
        "entries": [new_entry(spec, k, e, src.msg, referenced, src.rule_flags.get(k))
                    for k, e in enumerate(src.msg.entries)],
    }


# ---------------------------------------------------------------- merge

def is_source_flag(flag: str) -> bool:
    return flag in SOURCE_FLAGS or flag.startswith(AUTO_PREFIX)


def recorded_auto(entry: dict) -> str | None:
    """The automatic status recorded in the entry's flags, or None (written before the auto: flag)."""
    return next((f[len(AUTO_PREFIX):] for f in entry.get("flags", []) if f.startswith(AUTO_PREFIX)), None)


def status_is_auto(entry: dict) -> bool:
    """True if no person has chosen the entry's status, so it may follow the current rules."""
    status, recorded = entry.get("status"), recorded_auto(entry)
    if status not in AUTO_STATUSES:
        return False
    if recorded is not None:
        return status == recorded
    return not entry.get("es") and not entry.get("note")


def holds_work(entry: dict) -> bool:
    """True if an entry carries something a person made: Spanish, a note or a chosen status."""
    return bool(entry.get("es") or entry.get("note")) or not status_is_auto(entry)


@dataclass
class MergeResult:
    doc: dict
    source_changed: int = 0
    added: int = 0
    relinked: int = 0
    dropped: list[dict] = field(default_factory=list)    # old entries without a match in the source


def structure(doc: dict) -> list[tuple[str, str]]:
    return [(e.get("name"), e.get("guid")) for e in doc.get("entries", [])]


def describe_change(old: list[tuple[str, str]], new: list[tuple[str, str]]) -> str:
    k = next((i for i, (a, b) in enumerate(zip(old, new)) if a != b), min(len(old), len(new)))
    was = "name={} guid={}".format(*old[k]) if k < len(old) else "(none)"
    now = "name={} guid={}".format(*new[k]) if k < len(new) else "(none)"
    return f"{len(old)} -> {len(new)} entries; first difference at index {k}: work file {was}, source {now}"


def merge_entry(old: dict, new: dict, relinked: bool = False) -> tuple[dict, bool]:
    """Source fields from `new`, translator fields from `old`. Returns (entry, en_changed)."""
    changed = old.get("en") != new["en"]
    flags = {f for f in old.get("flags", []) if not is_source_flag(f)}
    if changed:
        flags.add(FLAG_SOURCE_CHANGED)
    if relinked:
        flags.add(FLAG_RELINKED)
    out = dict(new)
    out["es"] = old.get("es", "")
    out["status"] = new["status"] if status_is_auto(old) else old.get("status", new["status"])
    out["wrap"] = old.get("wrap", new["wrap"])
    out["note"] = old.get("note", "")
    out["flags"] = sorted(flags | set(new["flags"]))
    return out, changed


def pair_entries(old: list[dict], new: list[dict]) -> tuple[dict[int, tuple[int, str]], list[int]]:
    """Pair source entries with old work-file entries after a structure change.

    Returns ({new index: (old index, how)}, unmatched old indexes); how = guid | name | en."""
    pairs: dict[int, tuple[int, str]] = {}
    used: set[int] = set()

    def take(j: int, k: int | None, how: str) -> None:
        if k is not None and k not in used and j not in pairs:
            pairs[j] = (k, how)
            used.add(k)

    by_guid = {e.get("guid"): k for k, e in enumerate(old)}
    for j, ne in enumerate(new):
        take(j, by_guid.get(ne["guid"]), "guid")
    by_name = {e.get("name"): k for k, e in enumerate(old)}
    for j, ne in enumerate(new):
        k = by_name.get(ne["name"])
        if k is not None and old[k].get("en") == ne["en"]:
            take(j, k, "name")
    old_en = Counter(e.get("en") for k, e in enumerate(old) if k not in used)
    new_en = Counter(ne["en"] for j, ne in enumerate(new) if j not in pairs)
    by_en = {e.get("en"): k for k, e in enumerate(old) if k not in used}
    for j, ne in enumerate(new):
        if old_en[ne["en"]] == 1 and new_en[ne["en"]] == 1:
            take(j, by_en[ne["en"]], "en")
    return pairs, [k for k in range(len(old)) if k not in used]


def merge(old: dict, new: dict, force_structure: bool = False) -> MergeResult:
    """Merge an existing work file `old` into the fresh source document `new`."""
    if old.get("schema") != SCHEMA:
        raise MergeError(f"schema {old.get('schema')!r} is not {SCHEMA}")
    old_entries = old.get("entries", [])
    before, after = structure(old), structure(new)
    if before == after:
        pairs, leftover = {j: (j, "same") for j in range(len(after))}, []
    elif force_structure:
        pairs, leftover = pair_entries(old_entries, new["entries"])
    else:
        raise MergeError("entry structure changed: " + describe_change(before, after))
    result = MergeResult(doc={})
    entries = []
    for j, ne in enumerate(new["entries"]):
        if j not in pairs:
            entries.append(ne)
            result.added += 1
            continue
        k, how = pairs[j]
        merged, changed = merge_entry(old_entries[k], ne, relinked=how in ("name", "en"))
        entries.append(merged)
        result.source_changed += changed
        result.relinked += how in ("name", "en")
    result.dropped = [dict(old_entries[k], dropped_from=old.get("source_sha256", "")) for k in leftover]
    result.doc = {**new, "entries": entries}
    return result


def save_dropped(path: Path, doc: dict, entries: list[dict]) -> None:
    """Append dropped entries to their side file, each record once."""
    kept = load_json(path)["entries"] if path.exists() else []
    known = {json.dumps(e, sort_keys=True, ensure_ascii=False) for e in kept}
    kept += [e for e in entries if json.dumps(e, sort_keys=True, ensure_ascii=False) not in known]
    side = {"schema": SCHEMA, "scope": doc["scope"], "group": doc["group"], "path": doc["path"],
            "entries": kept}
    write_if_changed(path, dump_json(side))


# ---------------------------------------------------------------- run

@dataclass
class FileOutcome:
    spec: FileSpec
    path: Path
    doc: dict
    action: str                                            # new | updated | unchanged
    source_changed: int = 0
    added: int = 0
    relinked: int = 0
    dropped: list[str] = field(default_factory=list)       # names of old entries no longer in the source
    saved_to: Path | None = None                           # side file that keeps dropped entries with work
    warnings: list[str] = field(default_factory=list)


def plan(scope: str, sources: list[Source], referenced: set[str], strings_dir: Path,
         force_structure: bool) -> list[tuple[Path, Source, MergeResult, bool]]:
    """Merge every file in memory. Raises MergeError listing every failing file; nothing is written."""
    planned, errors = [], []
    for src in sources:
        new = build_doc(scope, src, referenced)
        path = work_path(strings_dir, scope, src.spec.group, src.spec.stem)
        if not path.exists():
            planned.append((path, src, MergeResult(new), False))
            continue
        try:
            planned.append((path, src, merge(load_json(path), new, force_structure), True))
        except (MergeError, ValueError, OSError) as exc:
            errors.append(f"{path}: {exc}")
    if errors:
        hint = ("\nNothing was written. Check the source, or re-run with --force-structure: entries are then "
                "re-matched by GUID (fallbacks get flag relinked) and translated entries that left the source "
                f"are saved under {Path(strings_dir) / DROPPED_DIR}.")
        raise MergeError("\n".join(errors) + hint)
    return planned


def apply(scope: str, strings_dir: Path, path: Path, src: Source, res: MergeResult,
          existed: bool) -> FileOutcome:
    """Write one merged work file, after saving its dropped entries that hold work."""
    spec = src.spec
    keep = [e for e in res.dropped if holds_work(e)]
    saved = dropped_path(strings_dir, scope, spec.group, spec.stem) if keep else None
    if saved:
        save_dropped(saved, res.doc, keep)
    written = write_if_changed(path, dump_json(res.doc))
    action = ("updated" if existed else "new") if written else "unchanged"
    return FileOutcome(spec, path, res.doc, action, res.source_changed, res.added, res.relinked,
                       [e.get("name") for e in res.dropped], saved, src.warnings)


def extract(scope: str, strings_dir: Path = config.STRINGS, force_structure: bool = False,
            ps: PakSet | None = None) -> list[FileOutcome]:
    """Create or merge the work files of base scope `scope` under `strings_dir`."""
    if scope not in scopes.BASE_SCOPES:
        raise ValueError(f"scope {scope!r} has no work files of its own; extract "
                         f"{', '.join(scopes.members(scope))} instead")
    ps = ps or PakSet()
    sources = read_sources(scopes.files(scope, ps), ps)
    referenced = referenced_guids(ps, eng_guids(sources))
    strings_dir = Path(strings_dir)
    return [apply(scope, strings_dir, *item) for item in plan(scope, sources, referenced, strings_dir,
                                                             force_structure)]


def orphans(scope: str, strings_dir: Path, outcomes: list[FileOutcome]) -> list[Path]:
    """JSON files in the scope's group folders that are not a scope file's work file (left untouched)."""
    known = {o.path.resolve() for o in outcomes}
    root = Path(strings_dir) / scope
    return sorted(p for g in scopes.SCOPE_GROUPS[scope] for p in (root / g).glob("*.json")
                  if p.resolve() not in known)


def group_lines(group: str, part: list[FileOutcome]) -> list[str]:
    actions = Counter(o.action for o in part)
    status = Counter(e["status"] for o in part for e in o.doc["entries"])
    flags = Counter(f for o in part for e in o.doc["entries"] for f in e["flags"]
                    if not f.startswith(AUTO_PREFIX))
    chosen = sum(not status_is_auto(e) for o in part for e in o.doc["entries"])
    lines = [f"  {group:9s} files {len(part):4d} (new {actions['new']}, updated {actions['updated']}, "
             f"unchanged {actions['unchanged']})  entries {sum(status.values()):6d}",
             "            status: " + ", ".join(f"{s} {status[s]}" for s in STATUSES if status[s])
             + (f" ({chosen} chosen by a person)" if chosen else "")]
    if flags:
        lines.append("            flags:  " + ", ".join(f"{k} {v}" for k, v in sorted(flags.items())))
    return lines


def structure_lines(outcomes: list[FileOutcome]) -> list[str]:
    lines = []
    for o in outcomes:
        if o.added or o.relinked or o.dropped:
            lines.append(f"  {o.spec.stem}: structure re-matched: {o.added} added, {o.relinked} relinked "
                         f"(flag {FLAG_RELINKED}), {len(o.dropped)} no longer in the source: "
                         + ", ".join(o.dropped))
        if o.saved_to:
            lines.append(f"    dropped entries holding work saved to {o.saved_to}")
    return lines


def summary_lines(scope: str, outcomes: list[FileOutcome], strings_dir: Path) -> list[str]:
    lines = [f"scope {scope}: {len(outcomes)} files -> {Path(strings_dir) / scope}"]
    for group in scopes.SCOPE_GROUPS[scope]:
        lines += group_lines(group, [o for o in outcomes if o.spec.group == group])
    total = Counter(e["status"] for o in outcomes for e in o.doc["entries"])
    lines.append(f"  total entries {sum(total.values())}: "
                 + ", ".join(f"{s} {total[s]}" for s in STATUSES if total[s]))
    changed = [(o.spec.stem, o.source_changed) for o in outcomes if o.source_changed]
    if changed:
        lines.append(f"  English changed in {len(changed)} files (flag {FLAG_SOURCE_CHANGED}): "
                     + ", ".join(f"{s} ({n})" for s, n in changed))
    lines += structure_lines(outcomes)
    lines += [f"  warning: {w}" for o in outcomes for w in o.warnings]
    lines += [f"  orphan work file (not in scope, left untouched): {p}"
              for p in orphans(scope, strings_dir, outcomes)]
    return lines


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="extract", description="Create or merge translation work files.")
    ap.add_argument("--scope", default="phase1", choices=scopes.BASE_SCOPES,
                    help="base scope ('all' has no work files of its own)")
    ap.add_argument("--force-structure", action="store_true",
                    help="rebuild files whose entry structure changed: entries re-matched by GUID, then by "
                         "name and English; translated entries that left the source are saved under "
                         f"work/strings/{DROPPED_DIR}")
    args = ap.parse_args(argv)
    try:
        outcomes = extract(args.scope, config.STRINGS, args.force_structure)
    except MergeError as exc:
        print(f"extract: stopped.\n{exc}")
        return 2
    except OSError as exc:
        print(f"extract: {exc}")
        return 2
    print("\n".join(summary_lines(args.scope, outcomes, config.STRINGS)))
    return 0
