r"""Build: apply translations to the msg files and write the patch outputs (contract section 7).

apply_docs() reads each document's msg file from the official paks, checks that the document matches it,
and writes wrap.final_text(...).text into the English slot (config.LANG_EN) of every entry whose status
reaches min_status (inserts.statuses_from) and passes the safety gate. Everything else stays as in the
source: other slots, other entries, attributes, and the pool order of unchanged strings (msg23.build keeps
the original order and appends new strings). Only files whose bytes changed are returned.

Whole-file refusals (the file is left out, every entry stays English):
  E_STRUCTURE  path missing, not in the official paks or not spelled as in the pak name list (case
               included), not a readable msg.23 file, a second document for the same file, or entries
               (count, order, names, guids) that differ from the source
  E_PROFILE    the document's profile is not a key of profiles.PROFILES
Safety gate per entry (always on, independent of validate); a refused entry stays English and is listed:
  E_STATUS     unknown status (never translated)
  E_STALE      the entry's 'en' is not the English in the paks (the game changed since extraction)
  E_WRAP       wrap is neither 'auto' nor 'manual', or wrapping changed the codes (wrap.CodesChangedError)
  E_EMPTY      English, Spanish or the final text is empty (or es is not a string), or exactly one of
               English and final text prints nothing (only spaces, U+3000, line breaks and non-printing
               codes: the 'blank' of validate's E_EMPTY)
  E_CODES      textrules.code_diff(en, final) is not empty, or the plain text (outside complete codes) of
               the final text holds a code mark (_CMD_ in any case, {, }, <, >) that the plain text of the
               English entry does not: a broken or unknown code the game's parser may misread
  E_FORMAT     textrules.format_problems(en, final) is not empty, or a DATETIME format that is not one of
               the entry's English formats has a character between its fields other than FORMAT_SEPARATORS
               ('/', ':', '.', '-', space, U+3000; the game's own formats use '/', ':', space and U+3000)
               or one that no font of the slot draws
  E_BREAK      lone CR or LF (inside a code too), a line break inside a code (the English never has one),
               NUL or another control character (C0, DEL, C1) in the final text
  E_CHARSET    a lone surrogate anywhere (it cannot be stored as UTF-16); Metrics.unsupported_chars(final,
               slot) (slot = the profile's slot, else observed_slot(path)); or a visible character that
               draws nothing or attaches to its neighbour (HIDDEN_CATEGORIES: format characters such as the
               soft hyphen, combining marks, private use, unassigned) unless the English entry has it
Overflow (lines or width beyond the limits of wrap.final_text) never blocks; it is reported with the final
lines and widths. Lines that are unchanged English are not copied into the reports (game text stays local).
Text from the documents is written into reports with control and other non-printable characters escaped.

build() modes (they combine):
  default          the scope's work files (extract.scope_docs(scope, strings_dir));
  identity         in-memory documents with es = en, status done, wrap manual for every entry: the output
                   must equal the originals byte for byte (proves the whole apply path is lossless);
  no_translations  no documents at all (with fonts: a font-only pak);
  all_files        every scope file is written, changed or not, and every file rebuilt without changes must
                   equal its original bytes (all_files + no_translations = the round trip);
  fonts            fontpatch.outputs(variant, ps) join the outputs; text is measured with
                   Metrics.load(overrides=those outputs);
  stems            only these files of the scope.

write_outputs() writes build/<name>/:
  natives/...                       loose copies of the output files (mod-manager layout)
  re_chunk_000.pak.patch_NNN.pak    NNN = PakSet.next_free_index(); the output files plus the marker entry;
                                    fingerprint of the official patch pak with the highest index; checksums
                                    zero; entry attributes of the official entry being overridden, for a new
                                    file those of NEW_FILE_TEMPLATES (supplement font: RR_System_Font)
  manifest.json                     options, toolkit hashes, pak, and per file: path, kind, source pak,
                                    original and new sha256, size, entries changed, pak template
  build_report.json, .md            totals, checks, per-file table, refused files and entries with their
                                    reasons, overflow entries with final lines and widths, notes
Checks: round_trip, identity (above) and pak_readback (the pak re-read with sfes.pak.Pak: every file
identical, marker present, entry count and fingerprint). Differences are listed in BuildResult.mismatches.
Every output path goes through fontpatch._check_writable before anything is written; then build/<name>
must lie inside config.BUILD and outside build/fonts, and only that folder is cleared. The name is letters,
digits, '_', '-' and inner dots, and its part before the first dot is not a Windows device name (CON, PRN,
AUX, NUL, COM0-9, LPT0-9); build() checks the name and folder before applying anything. No timestamps are
written: the same inputs give byte-identical paks, loose files, manifests and reports.

CLI: run.py build --scope phase1 --name NAME [--min-status draft|done|reviewed] [--fonts VARIANT]
                  [--stem STEM ...] [--all] [--no-translations] [--identity]
     exit 0 success, 1 a round-trip / identity / read-back difference, 2 usage or input error, or an
     internal error (printed with its traceback), so 1 always means a check difference.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import struct
import traceback
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from . import codes, config, extract, fontpatch, msg23, profiles, textrules, wrap
from . import scope as scopes
from .inserts import TRANSLATED_ORDER, InsertResolver, statuses_from
from .metrics import Metrics
from .pak import Entry, Pak, PakSet, PakWriter, name_list, path_hash
from .profiles import Profile
from .scope import FileSpec

MIN_STATUSES = TRANSLATED_ORDER        # draft < done < reviewed
IDENTITY_STATUS = "done"
CHECKSUM_MODE = "zero"
MANIFEST_SCHEMA = 1
MANIFEST_NAME = "manifest.json"
REPORT_JSON = "build_report.json"
REPORT_MD = "build_report.md"
FONTS_DIR = "fonts"                    # build/fonts holds fontpatch outputs: read-only input here
NAME_RX = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")
DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"{d}{n}" for d in ("COM", "LPT") for n in range(10))})
CONTROL_RX = re.compile(r"\r\n|[\x00-\x1f\x7f-\x9f]")
CONTROL_NAMES = {"\r": "lone CR", "\n": "lone LF", "\x00": "NUL"}
SURROGATE_RX = re.compile("[\ud800-\udfff]")
# Characters a changed DATETIME format may have between its fields.
FORMAT_SEPARATORS = frozenset("/:.- \u3000")
# Marks that only codes use (cmd, placeholder, tag); in plain text they are a broken or unknown code.
CODE_MARKS = ("_CMD_", "{", "}", "<", ">")
# Unicode categories that draw nothing or attach to a neighbour: format, combining marks, private use, unassigned.
HIDDEN_CATEGORIES = frozenset({"Cf", "Mn", "Mc", "Me", "Co", "Cn"})
BLANK_CHARS = " \u3000"
KINDS = ((".msg.23", "msg"), (".oft.1", "font"), (".fslt.4", "fslt"))
# New files (not in the official paks) and the official file in the same folder whose pak entry they copy.
NEW_FILE_TEMPLATES = {fontpatch.SUPPLEMENT_PATH: fontpatch.RR_PATH}
TOOLS_DIR = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------------------------------- reports

@dataclass
class Refusal:
    """An entry the safety gate kept in English."""
    id: str
    i: int
    name: str
    status: str
    reasons: list[str]


@dataclass
class Overflow:
    """An applied entry with more lines than allowed or a line wider than the limit (in `unit`).
    `lines` is None when the final text is the unchanged English (not copied into reports)."""
    id: str
    i: int
    name: str
    unit: str
    max_width: float
    max_lines: int
    widths: list[float]
    lines: list[str] | None
    overflow_lines: bool
    overflow_width: bool


@dataclass
class FileReport:
    """What apply_docs did with one document."""
    path: str
    source_pak: str | None = None
    refused_file: str | None = None
    entries_applied: int = 0
    entries_changed: int = 0
    file_changed: bool = False
    refused: list[Refusal] = field(default_factory=list)
    overflow: list[Overflow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ApplyReport:
    """Per-file results of apply_docs, plus the checks of a build (name -> {files, identical, different})."""
    files: list[FileReport] = field(default_factory=list)
    checks: dict[str, dict] = field(default_factory=dict)

    @property
    def entries_applied(self) -> int:
        return sum(f.entries_applied for f in self.files)

    @property
    def entries_changed(self) -> int:
        return sum(f.entries_changed for f in self.files)

    @property
    def entries_refused(self) -> int:
        return sum(len(f.refused) for f in self.files)

    @property
    def overflow(self) -> int:
        return sum(len(f.overflow) for f in self.files)

    @property
    def files_refused(self) -> int:
        return sum(f.refused_file is not None for f in self.files)

    def mismatches(self) -> list[str]:
        return [f"{name}: {item}" for name, check in sorted(self.checks.items()) for item in check["different"]]

    def refusal_counts(self) -> dict[str, int]:
        """Refused entries per reason code (an entry with several reasons counts once per code)."""
        codes = Counter(code for f in self.files for r in f.refused
                        for code in {reason.split(":", 1)[0] for reason in r.reasons})
        return dict(sorted(codes.items()))


@dataclass(frozen=True)
class BuildResult:
    """Where a build wrote its outputs (report_path is build_report.json; the .md lies next to it), how many
    output files differ from the official ones, entry totals, and every check difference (empty = success)."""
    out_dir: Path
    pak_path: Path
    manifest_path: Path
    report_path: Path
    files_changed: int
    entries_applied: int
    entries_refused: int
    overflow: int
    mismatches: tuple[str, ...] = ()


# ------------------------------------------------------------------------------------- safety gate

def _escaped(text: str) -> str:
    """text with every non-printable character (controls, lone surrogates, U+3000 ...) written as an escape,
    so a report item stays on one line and can be stored as UTF-8."""
    return "".join(ch if ch.isprintable() else repr(ch)[1:-1] for ch in text)


def _names(chars: Iterable[str]) -> str:
    return ", ".join(f"U+{ord(c):04X}" for c in chars)


@lru_cache(maxsize=1024)
def _plain(text: str) -> str:
    """The plain text of text: everything outside complete codes and line breaks."""
    return "".join(t.raw for t in codes.tokenize(text) if t.kind == "text")


def prints_nothing(text: str) -> bool:
    """Only spaces (ASCII or U+3000), line breaks and codes that print nothing (validate's 'blank')."""
    return all(t.prints in ("none", "break") or (t.kind == "text" and not t.raw.strip(BLANK_CHARS))
               for t in codes.tokenize(text))


def control_problems(text: str) -> list[str]:
    """Lone CR / LF, NUL and other control characters (C0, DEL, C1) anywhere in text (CRLF is the game's
    break)."""
    found = Counter(m.group() for m in CONTROL_RX.finditer(text) if m.group() != "\r\n")
    return [f"{CONTROL_NAMES.get(ch, f'U+{ord(ch):04X}')} x{n}" for ch, n in sorted(found.items())]


def empty_problems(en: str, es: str, final: str) -> list[str]:
    """English or Spanish empty, or exactly one of English and final text printing nothing."""
    out = []
    if not en:
        out.append("E_EMPTY: English is empty")
    if not es or not final:
        out.append("E_EMPTY: Spanish is empty")
    if out:
        return out
    en_blank, es_blank = prints_nothing(en), prints_nothing(final)
    if en_blank == es_blank:
        return []
    if es_blank:
        return ["E_EMPTY: the Spanish prints nothing (only spaces, line breaks or non-printing codes)"]
    return ["E_EMPTY: the English prints nothing but the Spanish has text"]


def _code_marks(text: str) -> set[str]:
    plain = _plain(text).upper()
    return {mark for mark in CODE_MARKS if mark in plain}


def code_problems(en: str, final: str) -> list[str]:
    """Codes missing or in excess (textrules), and code marks in the plain text that the English lacks."""
    out = []
    missing, extra = textrules.code_diff(en, final)
    if missing or extra:
        parts = [f"{label} {', '.join(map(_escaped, items))}" for label, items in (("missing", missing),
                                                                                    ("extra", extra)) if items]
        out.append("E_CODES: " + "; ".join(parts))
    marks = sorted(_code_marks(final) - _code_marks(en))
    if marks:
        out.append(f"E_CODES: {' '.join(marks)} outside a complete code (a broken or unknown code)")
    return out


def format_problems(en: str, final: str, *, metrics: Metrics, slot: int) -> list[str]:
    """textrules.format_problems (fields, letters, separators); when it finds none, format_char_problems
    (a separator the slot cannot draw). Only a text with a DATETIME tag has formats."""
    if "<DATETIME" not in final:
        return []
    shared = [f"E_FORMAT: {_escaped(p)}" for p in textrules.format_problems(en, final)]
    return shared or format_char_problems(en, final, metrics=metrics, slot=slot)


def format_char_problems(en: str, final: str, *, metrics: Metrics, slot: int) -> list[str]:
    """Characters between the fields of each DATETIME format of `final` that is not an English format of the
    entry: only FORMAT_SEPARATORS the slot draws (letters are textrules.format_problem's)."""
    english = {fmt for _, fmt in textrules.datetime_formats(en)}
    out = []
    for arg, fmt in textrules.datetime_formats(final):
        if fmt in english:
            continue
        bad = sorted({ch for ch in textrules.FIELD_RX.sub("", fmt) if not ch.isalpha()
                      and (ch not in FORMAT_SEPARATORS or not metrics.supported(ch, slot))})
        if bad:
            out.append(f'E_FORMAT: <DATETIME {arg} "{_escaped(fmt)}">: {_names(bad)} between the date fields '
                       "(allowed: / : . - space U+3000)")
    return out


def break_problems(final: str) -> list[str]:
    """Control characters, and a line break inside a code."""
    found = control_problems(final)
    code = wrap.code_with_break(final)
    if code is not None:
        found.append(f"line break inside the code {_escaped(code)}")
    return ["E_BREAK: " + ", ".join(found)] if found else []


def charset_problems(en: str, final: str, *, metrics: Metrics, slot: int) -> list[str]:
    """Lone surrogates, visible characters no font of the slot draws, and visible characters that draw nothing
    or attach to a neighbour (HIDDEN_CATEGORIES) unless the English has them. Controls are E_BREAK's."""
    out = []
    surrogates = sorted(set(SURROGATE_RX.findall(final)))
    if surrogates:
        out.append(f"E_CHARSET: lone surrogate {_names(surrogates)} (cannot be stored as UTF-16)")
    missing = [ch for ch in metrics.unsupported_chars(final, slot)
               if ch not in surrogates and unicodedata.category(ch) != "Cc"]
    if missing:
        out.append(f"E_CHARSET: no font of slot {slot} draws {_names(missing)}")
    hidden = sorted({ch for ch in _plain(final) if unicodedata.category(ch) in HIDDEN_CATEGORIES}
                    - set(_plain(en)) - set(missing))
    if hidden:
        out.append(f"E_CHARSET: {_names(hidden)} draws nothing or attaches to a neighbour (format character, "
                   "combining mark, private use or unassigned)")
    return out


def gate_problems(en: str, es: str, final: str, *, metrics: Metrics, slot: int) -> list[str]:
    """Reasons why `final` (the text the build would write for `es`) must not replace `en`; [] if none."""
    return [*empty_problems(en, es, final), *code_problems(en, final),
            *format_problems(en, final, metrics=metrics, slot=slot), *break_problems(final),
            *charset_problems(en, final, metrics=metrics, slot=slot)]


# ------------------------------------------------------------------------------------------ applying

@dataclass(frozen=True)
class _FileContext:
    path: str
    profile: Profile
    slot: int
    metrics: Metrics
    resolver: InsertResolver | None


def entry_structure(entries: list) -> list[tuple]:
    """(name, guid) of every work-file entry; (None, None) for an entry that is not a dict."""
    return [(e.get("name"), e.get("guid")) if isinstance(e, dict) else (None, None) for e in entries]


def _doc_problem(doc: object, ps: PakSet, seen: set[int]) -> str | None:
    """Why a document is refused as a whole; `seen` holds the path hashes of the documents accepted so far."""
    if not isinstance(doc, dict) or not isinstance(doc.get("path"), str):
        return "E_STRUCTURE: not a work-file document with a path"
    path = doc["path"]
    if not path.isascii() or not ps.has(path):
        return "E_STRUCTURE: path is not in the official paks"
    canonical = name_list().get(path_hash(path))
    if path != canonical:
        return ("E_STRUCTURE: path is not spelled as in the pak name list"
                + (f" ({canonical})" if canonical else ""))
    if path_hash(path) in seen:
        return "E_STRUCTURE: a second document for this path"
    if not isinstance(doc.get("entries"), list):
        return "E_STRUCTURE: entries missing"
    profile = doc.get("profile")
    if not isinstance(profile, str) or profile not in profiles.PROFILES:
        return f"E_PROFILE: unknown profile {profile!r}"
    return None


def _parse_source(raw: bytes) -> tuple[msg23.Msg | None, str | None]:
    try:
        return msg23.parse(raw), None
    except (ValueError, KeyError, IndexError, struct.error) as exc:
        return None, f"E_STRUCTURE: not a readable msg.23 file ({exc})"


def _final(entry: dict, en: str, ctx: _FileContext) -> tuple[wrap.WrapResult | None, list[str]]:
    """The final text of a translated entry and the gate's reasons to refuse it."""
    es = entry.get("es")
    mode = entry.get("wrap", ctx.profile.wrap)
    if "en" in entry and entry["en"] != en:
        return None, ["E_STALE: the work file's English differs from the paks; re-run extract"]
    if mode not in wrap.MODES:
        return None, [f"E_WRAP: unknown wrap mode {mode!r}"]
    if not isinstance(es, str):
        return None, ["E_EMPTY: es is not a text"]
    try:
        res = wrap.final_text(es, en, profile=ctx.profile, path=ctx.path, metrics=ctx.metrics,
                              resolver=ctx.resolver, wrap_mode=mode)
    except wrap.CodesChangedError as exc:
        return None, [f"E_WRAP: {_escaped(str(exc))}"]
    return res, gate_problems(en, es, res.text, metrics=ctx.metrics, slot=ctx.slot)


def _overflow(entry_id: str, i: int, name: str, res: wrap.WrapResult, en: str) -> Overflow:
    return Overflow(entry_id, i, name, res.unit, round(res.max_px, 2), res.max_lines,
                    [round(w, 2) for w in res.widths_px], None if res.text == en else list(res.lines),
                    res.overflow_lines, res.overflow_width)


def _apply_entries(doc: dict, msg: msg23.Msg, ctx: _FileContext, statuses: frozenset[str],
                   report: FileReport) -> None:
    stem = scopes.stem_of(ctx.path)
    for k, (entry, src) in enumerate(zip(doc["entries"], msg.entries)):
        status = entry.get("status")
        entry_id = extract.entry_id(stem, src.name)
        if not isinstance(status, str) or status not in extract.STATUSES:
            report.refused.append(Refusal(entry_id, k, src.name, _escaped(str(status)),
                                          [f"E_STATUS: unknown status {status!r}"]))
            continue
        if status not in statuses:
            continue
        en = msg.get(src, config.LANG_EN)
        res, problems = _final(entry, en, ctx)
        if problems:
            report.refused.append(Refusal(entry_id, k, src.name, status, problems))
            continue
        msg.set(src, config.LANG_EN, res.text)
        report.entries_applied += 1
        report.entries_changed += res.text != en
        report.notes += [f"{src.name}: {_escaped(n)}" for n in res.notes]
        if res.overflow_lines or res.overflow_width:
            report.overflow.append(_overflow(entry_id, k, src.name, res, en))


def _apply_doc(doc: dict, ps: PakSet, metrics: Metrics, resolver: InsertResolver | None,
               statuses: frozenset[str], seen: set[int]) -> tuple[FileReport, bytes | None]:
    """Report and new bytes (None when unchanged or refused) of one document."""
    path = doc.get("path") if isinstance(doc, dict) else None
    report = FileReport(_escaped(path) if isinstance(path, str) else repr(path))
    report.refused_file = _doc_problem(doc, ps, seen)
    if report.refused_file:
        return report, None
    seen.add(path_hash(path))
    source = ps.entry(path)
    report.source_pak = source.pak.name
    raw = ps.read(source)
    msg, report.refused_file = _parse_source(raw)
    if msg is None:
        return report, None
    want = [(e.name, e.guid) for e in msg.entries]
    if entry_structure(doc["entries"]) != want:
        report.refused_file = "E_STRUCTURE: " + extract.describe_change(entry_structure(doc["entries"]), want)
        return report, None
    if doc.get("source_sha256") not in (None, hashlib.sha256(raw).hexdigest()):
        report.notes.append("source_sha256 differs: the work file was extracted from another version of the file")
    profile = profiles.PROFILES[doc["profile"]]
    slot = profile.slot if profile.slot is not None else profiles.observed_slot(path)
    _apply_entries(doc, msg, _FileContext(path, profile, slot, metrics, resolver), statuses, report)
    if not report.entries_applied:
        return report, None
    data = msg23.build(msg)
    report.file_changed = data != raw
    return report, data if report.file_changed else None


def apply_docs(docs: list[dict], *, ps: PakSet, metrics: Metrics, resolver: InsertResolver | None,
               min_status: str = "draft") -> tuple[dict[str, bytes], ApplyReport]:
    """Apply work-file documents to their msg files: {pak path: new bytes} of the files whose bytes changed,
    and the report (applied, refused and overflowing entries, notes) of every document."""
    statuses = statuses_from(min_status)
    report = ApplyReport()
    files: dict[str, bytes] = {}
    seen: set[int] = set()
    for doc in docs:
        file_report, data = _apply_doc(doc, ps, metrics, resolver, statuses, seen)
        report.files.append(file_report)
        if data is not None:
            files[file_report.path] = data
    return files, report


# ---------------------------------------------------------------------------- identity and round trip

def identity_docs(specs: Iterable[FileSpec], ps: PakSet) -> list[dict]:
    """In-memory documents with es = en, status done and wrap manual for every entry of each file."""
    docs = []
    for spec in specs:
        m = msg23.parse(ps.read(spec.path))
        entries = [{"id": extract.entry_id(spec.stem, e.name), "i": k, "name": e.name, "guid": e.guid,
                    "en": m.get(e, config.LANG_EN), "es": m.get(e, config.LANG_EN), "status": IDENTITY_STATUS,
                    "wrap": "manual"} for k, e in enumerate(m.entries)]
        docs.append({"schema": extract.SCHEMA, "scope": scopes.home_scope(spec.group), "group": spec.group,
                     "path": spec.path, "category": spec.category, "profile": spec.profile, "entries": entries})
    return docs


def _check(total: int, different: list[str]) -> dict:
    return {"files": total, "identical": total - len(different), "different": different}


def round_trip(specs: Iterable[FileSpec], ps: PakSet) -> tuple[dict[str, bytes], dict]:
    """Every file parsed and rebuilt without changes, and the check that each equals its original bytes."""
    files, different = {}, []
    for spec in specs:
        raw = ps.read(spec.path)
        files[spec.path] = msg23.build(msg23.parse(raw))
        if files[spec.path] != raw:
            different.append(spec.path)
    return files, _check(len(files), different)


def identity_check(changed: dict[str, bytes], report: ApplyReport) -> dict:
    """An identity build changes no file and refuses no file."""
    different = sorted(changed) + [f"{f.path} ({f.refused_file})" for f in report.files if f.refused_file]
    return _check(len(report.files), different)


# ------------------------------------------------------------------------------------------- outputs

def kind_of(path: str) -> str:
    """'msg', 'font' or 'fslt'; any other file is refused."""
    for suffix, kind in KINDS:
        if path.endswith(suffix):
            return kind
    raise ValueError(f"{path}: the build ships only msg, font and fslt files")


def pak_name(ps: PakSet) -> str:
    return config.PATCH_FMT.format(ps.next_free_index())


def output_paths(name: str, files: Iterable[str], *, ps: PakSet, out_root: Path = config.BUILD) -> list[Path]:
    """Every path write_outputs writes: the build folder, loose files, pak, manifest and reports."""
    out_dir = Path(out_root) / name
    loose = [out_dir / Path(*p.split("/")) for p in sorted(files)]
    return [out_dir, *loose, out_dir / pak_name(ps), out_dir / MANIFEST_NAME, out_dir / REPORT_JSON,
            out_dir / REPORT_MD]


def _check_out_dir(name: str, out_dir: Path) -> None:
    """build/<name> must be a plain folder name (no Windows device name) inside config.BUILD and outside
    build/fonts."""
    if not NAME_RX.fullmatch(name):
        raise ValueError(f"build name {name!r}: use letters, digits, '_', '-' and inner dots")
    if name.split(".", 1)[0].upper() in DEVICE_NAMES:
        raise ValueError(f"build name {name!r}: {name.split('.', 1)[0]} is a Windows device name")
    target, root = out_dir.resolve(), config.BUILD.resolve()
    fonts = root / FONTS_DIR
    if root not in target.parents:
        raise ValueError(f"{out_dir} is not inside {config.BUILD}")
    if target == fonts or fonts in target.parents:
        raise ValueError(f"{out_dir}: {fonts} holds the font inputs and is never written by the build")


def newest_official_pak(ps: PakSet) -> Pak:
    """The official patch pak with the highest index (the base pak when there is none)."""
    by_index = dict(zip(ps.official_patch_indices(), ps.paks[1:]))
    return by_index[max(by_index)] if by_index else ps.paks[0]


def pak_template(path: str, ps: PakSet) -> tuple[Entry, str]:
    """Official entry whose attributes the pak entry of `path` copies, and its path."""
    entry = ps.entry(path)
    if entry is not None:
        return entry, path
    template = NEW_FILE_TEMPLATES.get(path)
    if template is None or not ps.has(template):
        raise ValueError(f"{path} is a new file without a template entry (NEW_FILE_TEMPLATES)")
    return ps.entry(template), template


def pak_templates(files: Iterable[str], ps: PakSet) -> dict[str, tuple[Entry, str]]:
    """pak_template() of every output file; files the build does not ship are refused (kind_of)."""
    for path in files:
        kind_of(path)
    return {path: pak_template(path, ps) for path in files}


def toolkit_hashes() -> dict[str, str]:
    """sha256 of the launcher and every sfes module (tests excluded)."""
    files = [TOOLS_DIR / "run.py", *sorted((TOOLS_DIR / "sfes").glob("*.py"))]
    return {p.relative_to(TOOLS_DIR).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clear(out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)


def _write_loose(out_dir: Path, files: dict[str, bytes]) -> None:
    for path, data in files.items():
        dest = out_dir / Path(*path.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def _write_pak(pak_path: Path, files: dict[str, bytes], templates: dict[str, tuple[Entry, str]],
               fingerprint: int) -> None:
    writer = PakWriter(fingerprint=fingerprint, checksum_mode=CHECKSUM_MODE)
    for path, data in files.items():
        writer.add(path, data, template=templates[path][0])
    writer.add_marker()
    writer.write(pak_path)


def pak_readback(pak_path: Path, files: dict[str, bytes], fingerprint: int) -> dict:
    """Re-read the written pak: each file's bytes, the marker, the entry count and the fingerprint."""
    pak = Pak(pak_path)
    different = [p for p, data in files.items()
                 if (e := pak.by_hash.get(path_hash(p))) is None or pak.read(e) != data]
    identical = len(files) - len(different)
    if not pak.is_sfes:
        different.append(f"marker {config.MARKER_PATH} missing")
    if pak.total != len(files) + 1:
        different.append(f"pak holds {pak.total} entries, expected {len(files) + 1}")
    if pak.fingerprint != fingerprint:
        different.append(f"fingerprint 0x{pak.fingerprint:08x}, expected 0x{fingerprint:08x}")
    return {"files": len(files), "identical": identical, "different": different, "marker": pak.is_sfes,
            "entries": pak.total}


def _manifest_file(path: str, data: bytes, ps: PakSet, template: tuple[Entry, str],
                   changed_entries: dict[str, int]) -> dict:
    official = ps.entry(path)
    original = ps.read(official) if official else None
    row = {"path": path, "kind": kind_of(path), "source_pak": official.pak.name if official else None,
           "original_sha256": _sha(original) if original is not None else None, "new_sha256": _sha(data),
           "size": len(data), "changed": data != original,
           "entries_changed": changed_entries.get(path, 0) if kind_of(path) == "msg" else None,
           "pak_entry_template": template[1], "pak_attributes": f"0x{template[0].attributes:x}"}
    if official is None:
        row["note"] = (f"new file: its pak entry copies the attributes of the official {template[1]} "
                       "(same folder, closest kind)")
    return row


def build_manifest(name: str, files: dict[str, bytes], ps: PakSet, options: dict, report: ApplyReport,
                   templates: dict[str, tuple[Entry, str]], pak_path: Path, newest: Pak) -> dict:
    changed_entries = {f.path: f.entries_changed for f in report.files}
    pak_bytes = pak_path.read_bytes()
    pak_info = {"index": ps.next_free_index(), "name": pak_path.name,
                "fingerprint": f"0x{newest.fingerprint:08x}",
                "fingerprint_of": "the official patch pak with the highest index"
                                  f" ({max(ps.official_patch_indices(), default=0)})",
                "checksum_mode": CHECKSUM_MODE}
    return {"schema": MANIFEST_SCHEMA, "name": name,
            "options": {**options, "pak": pak_info, "toolkit": toolkit_hashes()},
            "pak": {"name": pak_path.name, "sha256": _sha(pak_bytes), "size": len(pak_bytes),
                    "entries": len(files) + 1, "marker": config.MARKER_PATH},
            "loose_root": "natives",
            "files": [_manifest_file(p, d, ps, templates[p], changed_entries) for p, d in files.items()]}


def totals(report: ApplyReport, manifest: dict) -> dict:
    kinds = Counter(f["kind"] for f in manifest["files"])
    return {"documents": len(report.files), "files_refused": report.files_refused,
            "entries_applied": report.entries_applied, "entries_changed": report.entries_changed,
            "entries_refused": report.entries_refused, "refused_by_reason": report.refusal_counts(),
            "overflow": report.overflow, "files_written": len(manifest["files"]),
            "files_written_by_kind": dict(sorted(kinds.items())),
            "files_changed": sum(f["changed"] for f in manifest["files"])}


def write_outputs(name: str, files: dict[str, bytes], *, ps: PakSet, out_root: Path = config.BUILD,
                  options: dict, report: ApplyReport | None) -> BuildResult:
    """Write build/<name>/ (loose files, patch pak, manifest, reports) for `files` ({pak path: bytes})."""
    report = ApplyReport() if report is None else report
    files = dict(sorted(files.items()))
    paths = output_paths(name, files, ps=ps, out_root=out_root)
    for path in paths:
        fontpatch._check_writable(path)
    out_dir = paths[0]
    _check_out_dir(name, out_dir)
    templates = pak_templates(files, ps)
    newest = newest_official_pak(ps)
    pak_path, manifest_path, report_path, md_path = paths[-4:]
    _clear(out_dir)
    _write_loose(out_dir, files)
    _write_pak(pak_path, files, templates, newest.fingerprint)
    report.checks["pak_readback"] = pak_readback(pak_path, files, newest.fingerprint)
    manifest = build_manifest(name, files, ps, options, report, templates, pak_path, newest)
    manifest_path.write_bytes(extract.dump_json(manifest))
    summary = totals(report, manifest)
    body = {"name": name, "options": manifest["options"], "totals": summary, "checks": report.checks,
            "files": [asdict(f) for f in report.files]}
    report_path.write_bytes(extract.dump_json(body))
    md_path.write_bytes(markdown(name, manifest, summary, report).encode("utf-8"))
    return BuildResult(out_dir, pak_path, manifest_path, report_path, summary["files_changed"],
                       report.entries_applied, report.entries_refused, report.overflow,
                       tuple(report.mismatches()))


# ------------------------------------------------------------------------------------ markdown report

def _cell(text: object) -> str:
    return str(text).replace("|", "\\|")


def _code(text: str) -> str:
    """Markdown code span holding the Python literal of text (padding and escapes stay visible)."""
    shown = repr(text)
    return f"`` {shown} ``" if "`" in shown else f"`{shown}`"


def _md_options(manifest: dict) -> list[str]:
    o = manifest["options"]
    mode = [m for m in ("identity", "all_files", "no_translations") if o.get(m)] or ["translations"]
    stems = ", ".join(o["stems"]) if o.get("stems") else "all"
    pak = o["pak"]
    return [f"- scope `{o.get('scope')}`, minimum status `{o.get('min_status')}`, fonts: "
            f"{o.get('fonts') or 'none'}, stems: {stems}, mode: {', '.join(mode)}",
            f"- pak `{pak['name']}` (index {pak['index']}, fingerprint {pak['fingerprint']}, checksums "
            f"{pak['checksum_mode']}), pak entries: {manifest['pak']['entries']} (marker included)"]


def _md_totals(summary: dict) -> list[str]:
    reasons = ", ".join(f"{k} {v}" for k, v in summary["refused_by_reason"].items()) or "none"
    kinds = ", ".join(f"{k} {v}" for k, v in summary["files_written_by_kind"].items()) or "none"
    rows = [("documents processed", summary["documents"]), ("files refused", summary["files_refused"]),
            ("entries applied", summary["entries_applied"]), ("entries changed", summary["entries_changed"]),
            ("entries refused by the safety gate", f"{summary['entries_refused']} ({reasons})"),
            ("entries with overflow", summary["overflow"]),
            ("files written", f"{summary['files_written']} ({kinds})"),
            ("files changed", summary["files_changed"])]
    return ["| total | count |", "|---|---|"] + [f"| {a} | {b} |" for a, b in rows]


def _md_checks(report: ApplyReport) -> list[str]:
    out = []
    for name, check in sorted(report.checks.items()):
        out.append(f"- {name}: {check['identical']}/{check['files']} identical"
                   + (f"; {len(check['different'])} difference(s)" if check["different"] else ""))
        out += [f"  - {_cell(d)}" for d in check["different"]]
    return out


def _md_files(manifest: dict, report: ApplyReport) -> list[str]:
    by_path = {f.path: f for f in report.files}
    written = {f["path"]: f for f in manifest["files"]}
    listed = sorted(set(written) | {f.path for f in report.files if f.refused_file or f.refused or f.overflow})
    out = ["| file | kind | source pak | applied | changed | refused | overflow | written sha256 |",
           "|---|---|---|---|---|---|---|---|"]
    for path in listed:
        f, w = by_path.get(path, FileReport(path)), written.get(path)
        source = w["source_pak"] if w else f.source_pak
        sha = w["new_sha256"][:16] if w else ("refused" if f.refused_file else "not written")
        kind = w["kind"] if w else "msg"
        out.append(f"| {_cell(path)} | {kind} | {source or 'new'} | {f.entries_applied} | {f.entries_changed} | "
                   f"{len(f.refused)} | {len(f.overflow)} | {sha} |")
    return out


def _md_refused(report: ApplyReport) -> list[str]:
    files = [f"- {_cell(f.path)}: {_cell(f.refused_file)}" for f in report.files if f.refused_file]
    entries = [f"| {_cell(r.id)} | {r.status} | {_cell('; '.join(r.reasons))} |"
               for f in report.files for r in f.refused]
    out = ["## Refused files", ""] + (files or ["none"]) + ["", "## Refused entries", ""]
    return out + (["| entry | status | reasons |", "|---|---|---|"] + entries if entries else ["none"])


def _md_overflow(report: ApplyReport) -> list[str]:
    out = []
    for f in report.files:
        for o in f.overflow:
            what = [w for w, on in (("lines", o.overflow_lines), ("width", o.overflow_width)) if on]
            out.append(f"- {_cell(o.id)}: {len(o.widths)} lines, widest {max(o.widths, default=0):.2f} {o.unit} "
                       f"(limit {o.max_width:.2f} {o.unit} x {o.max_lines} lines; over: {', '.join(what)})")
            lines = o.lines if o.lines is not None else [None] * len(o.widths)
            out += [f"  - {w:8.2f} {o.unit}  " + ("(unchanged English, not copied)" if line is None else _code(line))
                    for w, line in zip(o.widths, lines)]
    return out or ["none"]


def markdown(name: str, manifest: dict, summary: dict, report: ApplyReport) -> str:
    notes = [f"- {_cell(f.path)}: {_cell(n)}" for f in report.files for n in f.notes]
    lines = [f"# Build report: {name}", "", *_md_options(manifest), "", "## Totals", "", *_md_totals(summary), "",
             "## Checks", "", *(_md_checks(report) or ["none"]), "", "## Files", "", *_md_files(manifest, report),
             "", *_md_refused(report), "", "## Overflow", "", *_md_overflow(report), "", "## Notes", "",
             *(notes or ["none"]), ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------- build

def select_specs(specs: list[FileSpec], stems: Iterable[str] | None) -> list[FileSpec]:
    """The specs of the given stems (all when None or empty); unknown stems are an error."""
    wanted = set(stems or ())
    if not wanted:
        return specs
    unknown = sorted(wanted - {s.stem for s in specs})
    if unknown:
        raise ValueError(f"not files of this scope: {', '.join(unknown)}")
    return [s for s in specs if s.stem in wanted]


def _check_modes(min_status: str, fonts: str | None, no_translations: bool, identity: bool) -> None:
    if min_status not in MIN_STATUSES:
        raise ValueError(f"min_status must be one of {MIN_STATUSES}, not {min_status!r}")
    if fonts is not None and fonts not in fontpatch.VARIANTS:
        raise ValueError(f"unknown font variant {fonts!r}; choose from {', '.join(fontpatch.VARIANTS)}")
    if identity and no_translations:
        raise ValueError("identity and no_translations exclude each other")
    if identity and IDENTITY_STATUS not in statuses_from(min_status):
        raise ValueError(f"identity documents have status {IDENTITY_STATUS!r}, below min_status {min_status!r}")


def _work_docs(scope: str, strings_dir: Path, specs: list[FileSpec]) -> list[dict]:
    stems = {s.stem for s in specs}
    return [d for _, d in extract.scope_docs(scope, strings_dir) if scopes.stem_of(d["path"]) in stems]


def _apply(docs: list[dict], ps: PakSet, font_files: dict[str, bytes], scope: str, strings_dir: Path,
           min_status: str, english_only: bool) -> tuple[dict[str, bytes], ApplyReport]:
    if not docs:
        return {}, ApplyReport()
    metrics = Metrics.load(overrides=font_files or None, paks=ps)
    resolver = InsertResolver(metrics, scope=scope, work_dir=strings_dir, paks=ps, min_status=min_status,
                              english_only=english_only)
    return apply_docs(docs, ps=ps, metrics=metrics, resolver=resolver, min_status=min_status)


def build(name: str, scope: str = "phase1", *, min_status: str = "draft", fonts: str | None = None,
          stems: Iterable[str] | None = None, no_translations: bool = False, all_files: bool = False,
          identity: bool = False, out_root: Path = config.BUILD, strings_dir: Path = config.STRINGS) -> BuildResult:
    """Apply the scope's translations (or identity / no documents) and write build/<name>/."""
    _check_modes(min_status, fonts, no_translations, identity)
    out_dir = Path(out_root) / name
    fontpatch._check_writable(out_dir)
    _check_out_dir(name, out_dir)
    ps = PakSet()
    specs = select_specs(scopes.files(scope, ps), stems)
    font_files = fontpatch.outputs(fonts, ps) if fonts else {}
    if identity:
        docs = identity_docs(specs, ps)
    else:
        docs = [] if no_translations else _work_docs(scope, Path(strings_dir), specs)
    changed, report = _apply(docs, ps, font_files, scope, Path(strings_dir), min_status, identity)
    files: dict[str, bytes] = {}
    if all_files:
        files, report.checks["round_trip"] = round_trip(specs, ps)
    if identity:
        report.checks["identity"] = identity_check(changed, report)
    files.update(changed)
    files.update(font_files)
    options = {"scope": scope, "min_status": min_status, "fonts": fonts,
               "stems": sorted(s.stem for s in specs) if stems else None,
               "identity": identity, "all_files": all_files, "no_translations": no_translations}
    return write_outputs(name, files, ps=ps, out_root=out_root, options=options, report=report)


# ----------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="build", description="Apply translations and write the patch outputs.")
    ap.add_argument("--scope", default="phase1", choices=scopes.SCOPES)
    ap.add_argument("--name", required=True, help="output folder build/<NAME>")
    ap.add_argument("--min-status", default="draft", choices=MIN_STATUSES)
    ap.add_argument("--fonts", choices=list(fontpatch.VARIANTS), help="add a fontpatch variant")
    ap.add_argument("--stem", dest="stems", action="extend", nargs="+", metavar="STEM",
                    help="only these files (file name without .msg.23)")
    ap.add_argument("--all", dest="all_files", action="store_true", help="write every scope file")
    ap.add_argument("--no-translations", action="store_true", help="apply no work files")
    ap.add_argument("--identity", action="store_true", help="es = en for every entry (must be byte-identical)")
    return ap


def _print_result(name: str, res: BuildResult) -> None:
    print(f"build {name}: {res.pak_path.name}, {res.files_changed} file(s) changed; entries applied "
          f"{res.entries_applied}, refused {res.entries_refused}, overflow {res.overflow}")
    body = extract.load_json(res.report_path)
    for check, info in sorted(body["checks"].items()):
        print(f"  check {check}: {info['identical']}/{info['files']} identical")
    for item in res.mismatches[:50]:
        print("  DIFFERENT:", item)
    print(f"  output: {res.out_dir}")


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    try:
        res = build(args.name, args.scope, min_status=args.min_status, fonts=args.fonts, stems=args.stems,
                    no_translations=args.no_translations, all_files=args.all_files, identity=args.identity,
                    out_root=config.BUILD, strings_dir=config.STRINGS)
        _print_result(args.name, res)
    except (ValueError, OSError) as exc:
        print(f"build: {exc}")
        return 2
    except Exception:          # any other failure is internal: never exit 1, which means a check difference
        traceback.print_exc()
        print("build: internal error (traceback above)")
        return 2
    return 1 if res.mismatches else 0
