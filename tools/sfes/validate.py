r"""Translation validator: checks the work files of a scope before a build (contract sections 6 and 6a).

Every file of the scope gets the file checks; the text checks apply to the entries whose status reaches the
minimum (inserts.statuses_from: draft < done < reviewed; default draft) and look at the final text the build
would write: wrap.final_text(es, en, profile, path, metrics, resolver, wrap_mode=entry wrap), with en = the
entry's English in the official paks (what the build compares with). Every reason for which the build's
safety gate keeps an entry in English is an error here, so 0 errors means that the build applies every
checked entry.

Issue codes (level; one issue per entry and code unless noted):
  E_STRUCTURE error  [file] work file entries (count, order, name, guid; also i and id) or path/group differ
                     from the source msg, the work file is missing, or its profile is unknown (text checks
                     skipped); [entry] en differs from the English in the paks (the build keeps the entry in
                     English; the text checks use the paks' English), en / es / flags of the wrong type, or an
                     unknown wrap mode (then checked with the profile's default)
  E_STATUS    error  unknown status (any entry), or a translated status with an empty es
  E_CODES     error  textrules.code_diff(en, es) not empty (the detail names the missing and extra codes as
                     written), or a code mark (_CMD_ in any case, {, }, <, >) outside complete codes that the
                     English plain text does not have: a broken or unknown code
  E_FORMAT    error  textrules.format_problems(en, es), or a changed DATETIME format with a character between
                     its fields other than FORMAT_SEPARATORS (/ : . - space U+3000) or one the slot cannot draw
  E_REPLACE   error  a <REPLACE tag whose name does not follow exactly two spaces
  E_BREAK     error  a lone CR or LF (final_text turns those outside codes into CRLF, so they survive only
                     inside codes), a line break inside a code, NUL or any other control character: C0 except
                     CRLF (tab included), DEL and C1 (U+0080-U+009F), that is every character of category Cc
  E_LINES     error  hard profile: more visible lines than max_lines
  E_WIDTH     error  hard profile: a line wider than max_px (px)
  E_CHARSET   error  a visible character (codes and inserted text excluded) that no font of the slot chain
                     draws (Metrics.unsupported_chars; control characters are E_BREAK's) or a lone surrogate,
                     or one that draws nothing or attaches to its neighbour (HIDDEN_CATEGORIES: soft hyphen and
                     other format characters, combining marks, private use, unassigned) unless the English
                     has it; slot = the profile's slot or profiles.observed_slot(path)
  E_EMPTY     error  English empty (the Spanish must stay empty: status empty), English blank but Spanish
                     not, or the reverse (blank: nothing but spaces, U+3000, line breaks and codes that print
                     nothing); an empty es is E_STATUS instead
  W_WIDTH     warn   observed profile: a line wider than the widest English line of the file (em)
  W_LINES     warn   observed profile: more lines than the English maximum of the file
  W_STYLE     warn   slot 0 only: STYLE_CHARS (“ ” ‘ ’ … — ° and ´ ¨ ′ ″ ‐ ―, which the chain draws 1.0 em
                     wide from BO-TBGoStd), or 2+ spaces in a row inside a line (codes that print nothing are
                     transparent); a run at a line edge is allowed when some English line of the entry is
                     padded on that side too
  W_PADDING   warn   the English body starts / ends with ASCII spaces or U+3000 and the final Spanish body does
                     not start / end with the same kind of space (both sides in one issue); in hard (dialogue)
                     profiles only padding that lays something out counts: U+3000, 2+ spaces on a side, or
                     both sides padded (a single stray space at one end of running dialogue is ignored)
  W_SAME      warn   es equals en once runs of spaces and line breaks are ignored (an English copy, also one
                     without its trailing breaks or with moved breaks) and the English has letters
                     (extract.has_letters)
  W_SOURCE    warn   flagged source_changed or relinked: the English moved under the translation
  W_INVERTED  info   a sentence ends in ? or ! without its opening ¿ / ¡ (InvertedMarks)
  W_GLOSSARY  warn   a glossary term of the English not rendered as agreed, or a forbidden form [one per term]
  W_STALE     warn   [file] source_sha256 differs from the current winning file in the paks, or entries' en
                     differ from the paks' English (re-run extract)
  I_FORMAT    info   a DATETIME format was changed (allowed; listed for review)
  I_TODO      info   [file] the file has untranslated (todo) entries

Inverted marks: per file, the translated entries are read in order. An opening ¿ or ¡ (or a run such as ¡¿)
waits for its closing mark. It carries over to the next entry when that entry is the next box (index + 1),
since one sentence may span several boxes, and is dropped at the end of a box that ends with a single '.'
(a finished sentence; '...' goes on). A box with status keep (shown in English, e.g. '......') is read for
its marks without being reported, so it does not break the chain; any other box that is not checked does.
A closing run (?, !, ?!, !?, !!, ...) closes the newest waiting opener of its kind and every newer one; a
mixed run such as ?! then goes on to the newest older opener of its other kind ('¿Sabes que ¡ganamos!?'
closes both). An unmatched closing run is reported only when the sentence before it has a letter, a digit
or a printing code, so '...?' or '???' alone are left alone. Marks inside codes are ignored.

Glossary (work/glossary.json; written in Phase 3, the check is inactive while the file does not exist):
  {"schema": 1, "terms": [{"en": "Battle Card", "es": "carta de batalla", "variants": ["cartas de batalla"],
    "forbidden": ["tarjeta de batalla"], "case_sensitive": false, "note": ""}]}
  en, es           required; texts with at least one word
  variants         optional; other accepted Spanish forms (plural, gender, capitalization if case-sensitive)
  forbidden        optional; Spanish forms that must not be used
  case_sensitive   optional, default false (false compares casefolded words); note optional, shown in reports
  No other keys; en must be unique (casefolded unless case-sensitive). Texts are compared as words (letters
  and digits) and single punctuation marks, so 'Card' never matches inside 'Cards' and spacing and line
  breaks do not matter; codes count as word breaks. W_GLOSSARY when the English contains en but the Spanish
  contains neither es nor a variant, and when the Spanish contains a forbidden form; both problems of one
  term are one issue.

Reports (write_reports): validate_<scope>.csv and .md for a run over the whole scope, validate_<scope>_
selected.csv and .md for a run over some stems (a partial run never replaces the full report). CSV: UTF-8
with BOM, every cell quoted; columns level, code, id, file, entry, line, detail, es_preview; line = 1-based
visible line of the final text or empty. MD: settings, counts per code and level, the worst files, the
first examples of each code with the final lines and their widths. Both are deterministic: no times or
dates, issues in file order. Work-file text is written on one line: CRLF as ' / ', every other character
that is not printable (controls, format characters, U+00A0, U+2028, U+3000 ...) as <U+XXXX>. The reports
hold game text, so write_reports refuses the game folder and original/ (fontpatch._check_writable) and
build/ and dist/ (contract rule 0.3).

CLI: run.py validate --scope phase1 [--min-status draft|done|reviewed] [--fonts rrsystem|rrsystem_range|
     supplement|supplement_range] [--stem STEM ...] [--quiet] [--strings DIR] [--glossary PATH] [--out DIR]
     Exit code 1 when any error exists, else 0; 2 when the work files, the glossary, the arguments or the
     report folder are unusable, or on an internal error (printed with its traceback), so 1 always means
     that errors were found.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import time
import traceback
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from . import codes, config, extract, fontpatch, inserts, msg23, profiles, textrules, wrap
from . import scope as scopes
from .inserts import InsertResolver
from .metrics import CACHE_PATH, Metrics
from .pak import PakSet
from .profiles import Profile
from .scope import FileSpec

REPORTS_DIR = config.WORK / "reports"
GLOSSARY_PATH = config.WORK / "glossary.json"
GLOSSARY_SCHEMA = 1
LOCAL_ONLY_REFUSED = (config.BUILD, config.DIST)     # reports hold game text (contract rule 0.3)

ERROR, WARN, INFO = "error", "warn", "info"
# code -> (level, what it checks); the order is the report order
CHECKS: dict[str, tuple[str, str]] = {
    "E_STRUCTURE": (ERROR, "work file entries (count, order, name, guid, en) or path differ from the source "
                           "msg, or a field has the wrong type"),
    "E_STATUS": (ERROR, "unknown status, or a translated status with empty es"),
    "E_CODES": (ERROR, "codes differ from the English (textrules.code_diff), or a broken code"),
    "E_FORMAT": (ERROR, "DATETIME format the game cannot use"),
    "E_REPLACE": (ERROR, "a <REPLACE tag not followed by exactly two spaces"),
    "E_BREAK": (ERROR, "lone CR / LF, line break inside a code, or another control character (C0, DEL, C1)"),
    "E_LINES": (ERROR, "visible lines > max_lines (hard profile)"),
    "E_WIDTH": (ERROR, "a line wider than max_px (hard profile)"),
    "E_CHARSET": (ERROR, "a character no font in the slot chain can draw, or one that draws nothing"),
    "E_EMPTY": (ERROR, "English empty but Spanish not, or the reverse"),
    "W_WIDTH": (WARN, "a line wider than the widest English line of the file (observed profile)"),
    "W_LINES": (WARN, "more lines than the English maximum of the file (observed profile)"),
    "W_STYLE": (WARN, "full-width typographic characters in slot 0, or double spaces inside a line"),
    "W_PADDING": (WARN, "English padding (spaces or U+3000) not kept"),
    "W_SAME": (WARN, "translated status but es == en (spacing aside) for text with letters"),
    "W_SOURCE": (WARN, "translated entry flagged source_changed or relinked (needs review)"),
    "W_INVERTED": (INFO, "a sentence ending in ? or ! without a matching opening mark"),
    "W_GLOSSARY": (WARN, "glossary term used inconsistently"),
    "W_STALE": (WARN, "the work file differs from the file in the paks (sha256 or English; re-run extract)"),
    "I_FORMAT": (INFO, "a DATETIME format was changed (allowed; listed for review)"),
    "I_TODO": (INFO, "untranslated (todo) entries in the file"),
}
CODE_ORDER = {code: k for k, code in enumerate(CHECKS)}
CSV_COLUMNS = ("level", "code", "id", "file", "entry", "line", "detail", "es_preview")

# Every character of category Cc (C0, DEL, C1); CRLF is matched only to be skipped (the game's line break).
CONTROL_RX = re.compile(r"\r\n|[\x00-\x1f\x7f-\x9f]")
CONTROL_NAMES = {"\r": "lone CR", "\n": "lone LF", "\x00": "NUL", "\t": "tab"}
# Categories that draw nothing or attach to a neighbour: format, combining marks, private use, unassigned.
HIDDEN_CATEGORIES = frozenset({"Cf", "Mn", "Mc", "Me", "Co", "Cn"})
# Marks that only codes use; in plain text they are a broken or unknown code.
CODE_MARKS = ("_CMD_", "{", "}", "<", ">")
# Characters a changed DATETIME format may have between its fields.
FORMAT_SEPARATORS = frozenset("/:.- 　")
REPLACE_RX = re.compile(r"<REPLACE(?![A-Z0-9_])[^<>]*>?")
GOOD_REPLACE_RX = re.compile(r"<REPLACE  [^ <>][^<>]*>")
SPACE_RUN_RX = re.compile(r" {2,}")
SPACING_RX = re.compile(r"[ 　\r\n]+")
WORD_RX = re.compile(r"\w+|[^\w\s]")
# 1.0 em wide in the slot 0 chain (BO-TBGoStd-Medium) -> what to write instead (None: no direct replacement)
STYLE_CHARS: dict[str, str | None] = {"“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "—": "-", "°": None,
                                      "´": "'", "¨": None, "′": "'", "″": '"', "‐": "-", "―": "-"}
PAD_CHARS = (" ", "　")
OPENING = {"¿": "?", "¡": "!"}
CLOSING = frozenset("?!")
CODE_MARK = "￼"          # a printing code in a visible-text skeleton
STYLE_SLOT = 0
KEEP_STATUS = "keep"
CONTEXT_UNITS = 24
PREVIEW_CHARS = 100
EXAMPLES_PER_CODE = 3
WORST_FILES = 25
CONSOLE_ISSUES = 40
TERM_KEYS = frozenset({"en", "es", "variants", "forbidden", "case_sensitive", "note"})

Finding = tuple[str, int | None, str]          # code, 1-based visible line or None, detail


class ValidateError(ValueError):
    """The validation cannot run as asked (unknown stems, conflicting options, unreadable work files)."""


class GlossaryError(ValidateError):
    """The glossary file exists but does not follow the glossary format."""


# ------------------------------------------------------------------------------------------------ results

@dataclass(frozen=True)
class Layout:
    """Final visible lines of an entry, their widths and the limits they were measured against."""
    lines: tuple[str, ...]
    widths: tuple[float, ...]
    unit: str
    max_width: float
    max_lines: int
    hard: bool


@dataclass(frozen=True)
class Issue:
    level: str
    code: str
    id: str                    # entry id; '' for file issues
    file: str                  # stem of the msg file
    entry: str                 # entry name; '' for file issues
    line: int | None           # 1-based visible line of the final text
    detail: str
    es_preview: str
    layout: Layout | None = None


@dataclass(frozen=True)
class FileSummary:
    file: str
    entries: int
    translated: int            # status reaches the minimum (text checks applied)
    todo: int


@dataclass
class Report:
    scope: str
    min_status: str
    fonts: str | None
    metrics_key: str
    glossary: str              # state of the glossary check, for the report header
    stems: tuple[str, ...]     # selected stems; () = the whole scope
    issues: list[Issue]
    files: list[FileSummary]
    elapsed: float = 0.0

    @property
    def files_checked(self) -> int:
        return len(self.files)

    @property
    def entries_checked(self) -> int:
        return sum(f.entries for f in self.files)

    @property
    def translated(self) -> int:
        return sum(f.translated for f in self.files)

    @property
    def counts_by_code(self) -> Counter:
        return Counter(i.code for i in self.issues)

    @property
    def counts_by_level(self) -> Counter:
        return Counter(i.level for i in self.issues)

    @property
    def errors(self) -> int:
        return self.counts_by_level[ERROR]


# ------------------------------------------------------------------------------------------------ glossary

def word_tokens(text: str) -> list[str]:
    """Words (letters, digits, underscore) and single punctuation marks; spaces and line breaks dropped."""
    return WORD_RX.findall(text)


def _fold(words: Sequence[str], case_sensitive: bool) -> tuple[str, ...]:
    return tuple(words) if case_sensitive else tuple(w.casefold() for w in words)


def _occurs(seq: tuple[str, ...], words: tuple[str, ...]) -> bool:
    n = len(seq)
    return any(words[p:p + n] == seq for p in range(len(words) - n + 1))


@dataclass(frozen=True)
class Term:
    en: str
    es: str
    variants: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    case_sensitive: bool = False
    note: str = ""

    def detail(self, missing: bool, forbidden: Sequence[str]) -> str:
        """One report line for this term: the agreed rendering is missing and / or forbidden forms are used."""
        note = f" ({self.note})" if self.note else ""
        parts = [f"{self.en!r} should be {' or '.join(repr(f) for f in (self.es, *self.variants))}{note}"
                 ] if missing else []
        if forbidden:
            parts.append(f"forbidden {', '.join(map(repr, forbidden))} for {self.en!r}"
                         + ("" if missing else f"; use {self.es!r}{note}"))
        return "; ".join(parts)


class _Phrases:
    """Word sequences looked up by their first word; case-insensitive ones are compared casefolded."""

    def __init__(self) -> None:
        self._by_first: dict[tuple[bool, str], list[tuple[tuple[str, ...], object]]] = {}

    def add(self, phrase: str, case_sensitive: bool, payload: object) -> None:
        seq = _fold(word_tokens(phrase), case_sensitive)
        self._by_first.setdefault((case_sensitive, seq[0]), []).append((seq, payload))

    def find(self, words: list[str]) -> list:
        """Payloads of the phrases that occur in `words`, in order of first occurrence."""
        views = {True: tuple(words), False: _fold(words, False)}
        found: dict[object, None] = {}
        for p in range(len(words)):
            for cs, view in views.items():
                for seq, payload in self._by_first.get((cs, view[p]), ()):
                    if view[p:p + len(seq)] == seq:
                        found.setdefault(payload)
        return list(found)


class Glossary:
    """Agreed renderings of English terms (format in the module docstring)."""

    def __init__(self, terms: list[Term], path: Path):
        self.terms = terms
        self.path = path
        self._used = _Phrases()
        self._forbidden = _Phrases()
        self._wanted = {t: [_fold(word_tokens(f), t.case_sensitive) for f in (t.es, *t.variants)] for t in terms}
        for t in terms:
            self._used.add(t.en, t.case_sensitive, t)
            for form in t.forbidden:
                self._forbidden.add(form, t.case_sensitive, (t, form))

    def check(self, en: str, es: str) -> list[str]:
        """Problems of one Spanish text (codes already replaced by spaces) against its English, one per term:
        terms of the English first, then terms whose forbidden forms alone occur."""
        es_words = word_tokens(es)
        views = {cs: _fold(es_words, cs) for cs in (False, True)}
        missing = [t for t in self._used.find(word_tokens(en))
                   if not any(_occurs(seq, views[t.case_sensitive]) for seq in self._wanted[t])]
        forbidden: dict[Term, list[str]] = {t: [] for t in missing}
        for t, form in self._forbidden.find(es_words):
            forbidden.setdefault(t, []).append(form)
        return [t.detail(t in missing, forms) for t, forms in forbidden.items()]


def _is_phrase(value: object) -> bool:
    return isinstance(value, str) and bool(word_tokens(value))


def parse_term(raw: object) -> Term:
    """A glossary term from its JSON object; GlossaryError says what is wrong with it."""
    if not isinstance(raw, dict):
        raise GlossaryError("not an object")
    unknown = sorted(set(raw) - TERM_KEYS)
    if unknown:
        raise GlossaryError(f"unknown keys {unknown}")
    if not (_is_phrase(raw.get("en")) and _is_phrase(raw.get("es"))):
        raise GlossaryError("en and es must be texts with at least one word")
    lists = {k: raw.get(k, []) for k in ("variants", "forbidden")}
    for k, v in lists.items():
        if not isinstance(v, list) or not all(_is_phrase(x) for x in v):
            raise GlossaryError(f"{k} must be a list of texts with at least one word")
    cs, note = raw.get("case_sensitive", False), raw.get("note", "")
    if not isinstance(cs, bool) or not isinstance(note, str):
        raise GlossaryError("case_sensitive must be true or false and note a text")
    return Term(raw["en"], raw["es"], tuple(lists["variants"]), tuple(lists["forbidden"]), cs, note)


def load_glossary(path: Path = GLOSSARY_PATH) -> Glossary | None:
    """The glossary at `path`, or None when the file does not exist (the check is then inactive)."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise GlossaryError(f"{path}: not readable as JSON ({exc})") from exc
    if not isinstance(doc, dict) or doc.get("schema") != GLOSSARY_SCHEMA or not isinstance(doc.get("terms"), list):
        raise GlossaryError(f'{path}: expected {{"schema": {GLOSSARY_SCHEMA}, "terms": [...]}}')
    terms, problems = [], []
    for k, raw in enumerate(doc["terms"]):
        try:
            terms.append(parse_term(raw))
        except GlossaryError as exc:
            problems.append(f"term {k}: {exc}")
    keys = Counter(_fold(word_tokens(t.en), t.case_sensitive) for t in terms)
    problems += [f"term {' '.join(k)!r} is defined {n} times" for k, n in keys.items() if n > 1]
    if problems:
        raise GlossaryError(f"{path}:\n  " + "\n  ".join(problems))
    return Glossary(terms, path)


def glossary_state(glossary: Glossary | None, path: Path) -> str:
    if glossary is None:
        return f"inactive ({path} does not exist)"
    return f"{len(glossary.terms)} terms from {path}"


# ----------------------------------------------------------------------------------------- text helpers

def skeleton(line: str) -> str:
    """Visible text of one line: plain characters, CODE_MARK per printing code, nothing for codes that print
    nothing (waits, portraits)."""
    return "".join(t.raw if t.kind == "text" else CODE_MARK if t.prints in ("insert", "glyph") else ""
                   for t in codes.tokenize(line))


def plain_text(text: str) -> str:
    """Everything outside complete codes and line breaks."""
    return "".join(t.raw for t in codes.tokenize(text) if t.kind == "text")


def word_text(text: str) -> str:
    """Plain text with every code and line break replaced by a space (for word matching)."""
    return "".join(t.raw if t.kind == "text" else " " for t in codes.tokenize(text))


def blank(text: str) -> bool:
    """Nothing printed: only spaces (ASCII or U+3000), line breaks and codes that print nothing."""
    return all(t.prints in ("none", "break") or (t.kind == "text" and not t.raw.strip(" 　"))
               for t in codes.tokenize(text))


def spacing_free(text: str) -> str:
    """text with every run of spaces (ASCII or U+3000) and line breaks as one space, and none at the ends."""
    return SPACING_RX.sub(" ", text).strip(" ")


def control_chars(text: str) -> list[tuple[int, str]]:
    """(position, character) of every control character that is not part of a CRLF."""
    return [(m.start(), m.group()) for m in CONTROL_RX.finditer(text) if m.group() != "\r\n"]


def code_marks(plain: str) -> set[str]:
    """CODE_MARKS found in a plain text (plain_text; _CMD_ in any case)."""
    upper = plain.upper()
    return {mark for mark in CODE_MARKS if mark in upper}


def raw_codes(text: str, keys: Iterable[str]) -> list[str]:
    """The codes of `text` behind comparison keys (textrules.code_key, as code_diff returns them): the first
    occurrences, in text order."""
    wanted = Counter(keys)
    out = []
    for tok in codes.tokenize(text):
        key = textrules.code_key(tok.raw) if tok.is_code else None
        if key is not None and wanted[key] > 0:
            wanted[key] -= 1
            out.append(tok.raw)
    return out


def line_of(text: str, pos: int) -> int:
    """1-based line (CRLF-separated) of a position in a final text."""
    return text.count("\r\n", 0, pos) + 1


def _printable(text: str) -> str:
    """Single-line form for reports: CRLF as ' / ', every other non-printable character as <U+XXXX>."""
    flat = text.replace("\r\n", " / ")
    if flat.isprintable():
        return flat
    return "".join(ch if ch.isprintable() else f"<U+{ord(ch):04X}>" for ch in flat)


def _preview(text: str) -> str:
    flat = _printable(text)
    return flat if len(flat) <= PREVIEW_CHARS else flat[:PREVIEW_CHARS - 3] + "..."


def _width(value: float, unit: str) -> str:
    return f"{value:.1f} px" if unit == "px" else f"{value:.3f} em"


def _char_name(ch: str) -> str:
    if unicodedata.category(ch) == "Cs":
        return f"U+{ord(ch):04X} lone surrogate (cannot be stored as UTF-16)"
    name = unicodedata.name(ch, "")
    return f"U+{ord(ch):04X} {name}" if name else f"U+{ord(ch):04X}"


def _control_name(ch: str) -> str:
    name = CONTROL_NAMES.get(ch)
    return f"U+{ord(ch):04X}" + (f" ({name})" if name else "")


def _type_name(value: object) -> str:
    return "null" if value is None else type(value).__name__


def _english_padding(en: str) -> tuple[bool, bool]:
    """(some English line starts with a space, some English line ends with a space)."""
    body, _ = codes.body_and_trailing(wrap.normalize_breaks(en))
    lines = [skeleton(line) for line in codes.split_lines(body)]
    return any(s.startswith(" ") for s in lines), any(s.endswith(" ") for s in lines)


# ---------------------------------------------------------------------------------------- text checks

@dataclass(frozen=True)
class _Text:
    """One translated entry ready for the text checks."""
    en: str
    es: str
    final: wrap.WrapResult
    hard: bool
    slot: int
    flags: tuple[str, ...]

    @cached_property
    def en_plain(self) -> str:
        return plain_text(self.en)

    @cached_property
    def final_plain(self) -> str:
        return plain_text(self.final.text)


def check_codes(t: _Text) -> list[Finding]:
    missing, extra = textrules.code_diff(t.en, t.es)
    parts = [f"{label}: {', '.join(raw_codes(text, keys))}"
             for label, text, keys in (("missing", t.en, missing), ("extra", t.es, extra)) if keys]
    marks = sorted(code_marks(t.final_plain) - code_marks(t.en_plain))
    if marks:
        parts.append(f"{' '.join(marks)} outside a complete code (a broken or unknown code)")
    return [("E_CODES", None, "; ".join(parts))] if parts else []


def format_char_problems(t: _Text, metrics: Metrics) -> list[str]:
    """Characters between the fields of a changed DATETIME format that are not FORMAT_SEPARATORS the slot
    draws (letters are textrules.format_problems')."""
    english = {fmt for _, fmt in textrules.datetime_formats(t.en)}
    out = []
    for arg, fmt in textrules.datetime_formats(t.es):
        if fmt in english:
            continue
        bad = sorted({ch for ch in textrules.FIELD_RX.sub("", fmt) if not ch.isalpha()
                      and (ch not in FORMAT_SEPARATORS or not metrics.supported(ch, t.slot))})
        if bad:
            out.append(f'<DATETIME {arg} "{fmt}">: {", ".join(map(repr, bad))} between the date fields '
                       "(allowed: / : . - space U+3000)")
    return out


def check_format(t: _Text, metrics: Metrics) -> list[Finding]:
    """E_FORMAT for unusable DATETIME formats, else I_FORMAT when a format was changed."""
    problems = textrules.format_problems(t.en, t.es) + format_char_problems(t, metrics)
    if problems:
        return [("E_FORMAT", None, "; ".join(problems))]
    if textrules.format_changed(t.en, t.es):
        before = ", ".join(f'"{f}"' for _, f in textrules.datetime_formats(t.en))
        after = ", ".join(f'"{f}"' for _, f in textrules.datetime_formats(t.es))
        return [("I_FORMAT", None, f"DATETIME format {before} -> {after}")]
    return []


def check_replace(t: _Text) -> list[Finding]:
    text = t.final.text
    bad = [m for m in REPLACE_RX.finditer(text) if not GOOD_REPLACE_RX.fullmatch(m.group())]
    if not bad:
        return []
    shown = ", ".join(repr(m.group()) for m in bad)
    return [("E_REPLACE", line_of(text, bad[0].start()), f"{shown}: the name must follow exactly two spaces")]


def check_break(t: _Text) -> list[Finding]:
    text = t.final.text
    found: list[tuple[int, str]] = []
    code = wrap.code_with_break(text)
    if code is not None:
        found.append((text.find(code), f"line break inside a code: {code!r}"))
    controls = control_chars(text)
    if controls:
        names = ", ".join(map(_control_name, sorted({ch for _, ch in controls})))
        found.append((controls[0][0], f"control characters {names}"))
    if not found:
        return []
    return [("E_BREAK", line_of(text, min(found)[0]), "; ".join(d for _, d in found))]


def check_lines(t: _Text) -> list[Finding]:
    f = t.final
    if not f.overflow_lines:
        return []
    if t.hard:
        return [("E_LINES", f.max_lines + 1, f"{len(f.lines)} lines > {f.max_lines} (box limit)")]
    return [("W_LINES", f.max_lines + 1,
             f"{len(f.lines)} lines > {f.max_lines} (most lines of an English entry of the file)")]


def check_width(t: _Text) -> list[Finding]:
    f = t.final
    if not f.overflow_width:
        return []
    over = [(k, w) for k, w in enumerate(f.widths_px, 1) if w > f.max_px + wrap.EPS]
    detail = "; ".join(f"line {k}: {_width(w, f.unit)} > {_width(f.max_px, f.unit)}" for k, w in over)
    if t.hard:
        return [("E_WIDTH", over[0][0], detail)]
    return [("W_WIDTH", over[0][0], detail + " (widest English line of the file)")]


def check_charset(t: _Text, metrics: Metrics) -> list[Finding]:
    """Characters the slot cannot draw and hidden ones; control characters are E_BREAK's."""
    missing = [c for c in metrics.unsupported_chars(t.final.text, t.slot) if unicodedata.category(c) != "Cc"]
    hidden = sorted({c for c in t.final_plain if unicodedata.category(c) in HIDDEN_CATEGORIES}
                    - set(t.en_plain) - set(missing))
    parts = []
    if missing:
        parts.append(", ".join(map(_char_name, missing)) + f" (no font of slot {t.slot} has it)")
    if hidden:
        parts.append(", ".join(map(_char_name, hidden)) + " (draws nothing or attaches to its neighbour: "
                     "format character, combining mark, private use or unassigned)")
    if not parts:
        return []
    bad = set(missing) | set(hidden)
    line = next((k for k, line in enumerate(t.final.lines, 1) if bad.intersection(line)), None)
    return [("E_CHARSET", line, "; ".join(parts))]


def check_empty(t: _Text) -> list[Finding]:
    if t.en == "":
        return [("E_EMPTY", None, "the English is empty: the Spanish must stay empty (status empty)")]
    en_blank, es_blank = blank(t.en), blank(t.final.text)
    if en_blank == es_blank:
        return []
    if en_blank:
        return [("E_EMPTY", None, "the English prints nothing but the Spanish has text")]
    return [("E_EMPTY", None, "the English has text but the Spanish prints nothing")]


def _style_line(sk: str, en_lead: bool, en_trail: bool) -> list[str]:
    """W_STYLE problems of one line skeleton."""
    out = [f"{ch!r} is full-width here" + (f" (write {STYLE_CHARS[ch]!r})" if STYLE_CHARS[ch] else "")
           for ch in STYLE_CHARS if ch in sk]
    for m in SPACE_RUN_RX.finditer(sk):
        lead, trail = m.start() == 0, m.end() == len(sk)
        if not (lead or trail):
            out.append(f"{len(m.group())} spaces in a row")
        elif (lead and not en_lead) or (trail and not en_trail):
            out.append(f"{len(m.group())} spaces at the {'start' if lead else 'end'} of the line "
                       "(the English has no such padding)")
    return out


def check_style(t: _Text) -> list[Finding]:
    if t.slot != STYLE_SLOT:
        return []
    en_lead, en_trail = _english_padding(t.en)
    found = [(k, p) for k, line in enumerate(t.final.lines, 1) for p in _style_line(skeleton(line), en_lead, en_trail)]
    if not found:
        return []
    return [("W_STYLE", found[0][0], "; ".join(f"line {k}: {p}" for k, p in found))]


def _meaningful_padding(t: _Text, en_body: str) -> bool:
    """Padding that lays something out: U+3000, 2+ spaces on a side, both sides padded, or any padding in an
    observed (menu/label) profile. A single stray space on one side of running dialogue is not
    (phase1: 110 of the 397 padded dialogue bodies)."""
    lead = len(en_body) - len(en_body.lstrip(" "))
    trail = len(en_body) - len(en_body.rstrip(" "))
    return (not t.hard or "　" in (en_body[:1] + en_body[-1:]) or max(lead, trail) >= 2
            or (lead > 0 and trail > 0))


def check_padding(t: _Text) -> list[Finding]:
    en_body, _ = codes.body_and_trailing(wrap.normalize_breaks(t.en))
    es_body, _ = codes.body_and_trailing(t.final.text)
    if not _meaningful_padding(t, en_body):
        return []
    lost = [f"{side} with {'spaces' if en_ch == ' ' else 'U+3000 (full-width space)'}"
            for side, en_ch, es_ch in (("starts", en_body[:1], es_body[:1]), ("ends", en_body[-1:], es_body[-1:]))
            if en_ch in PAD_CHARS and es_ch != en_ch]
    if not lost:
        return []
    return [("W_PADDING", None, f"the English {' and '.join(lost)}; the Spanish does not")]


def check_same(t: _Text) -> list[Finding]:
    if spacing_free(t.es) == spacing_free(t.en) and extract.has_letters(t.en):
        return [("W_SAME", None, "identical to the English (status keep if that is intended)")]
    return []


def check_source(t: _Text) -> list[Finding]:
    flags = [f for f in (extract.FLAG_SOURCE_CHANGED, extract.FLAG_RELINKED) if f in t.flags]
    if not flags:
        return []
    return [("W_SOURCE", None, f"flagged {', '.join(flags)}: check the Spanish against the current English, "
                               "then remove the flag")]


def check_glossary(t: _Text, glossary: Glossary | None) -> list[Finding]:
    if glossary is None:
        return []
    return [("W_GLOSSARY", None, d) for d in glossary.check(word_text(t.en), word_text(t.final.text))]


def text_findings(t: _Text, metrics: Metrics, glossary: Glossary | None) -> list[Finding]:
    """Every entry-level text check except W_INVERTED (which needs the previous entries)."""
    return [*check_codes(t), *check_format(t, metrics), *check_replace(t), *check_break(t), *check_lines(t),
            *check_width(t), *check_charset(t, metrics), *check_empty(t), *check_style(t), *check_padding(t),
            *check_same(t), *check_source(t), *check_glossary(t, glossary)]


def check_text(es: str, en: str, *, profile: Profile, path: str, slot: int, metrics: Metrics,
               resolver: InsertResolver | None, wrap_mode: str, flags: Sequence[str] = (),
               glossary: Glossary | None = None) -> tuple[wrap.WrapResult, list[Finding]]:
    """The final text of one translated entry and its text findings (all but W_INVERTED), in check order.
    `en` is the entry's English in the paks, `slot` the profile's slot or profiles.observed_slot(path)."""
    final = wrap.final_text(es, en, profile=profile, path=path, metrics=metrics, resolver=resolver,
                            wrap_mode=wrap_mode)
    return final, text_findings(_Text(en, es, final, profile.hard, slot, tuple(flags)), metrics, glossary)


# -------------------------------------------------------------------------------------- inverted marks

class InvertedMarks:
    """W_INVERTED for the translated entries of one file, fed in file order (rules: module docstring)."""

    def __init__(self) -> None:
        self.pending: list[frozenset[str]] = []      # waiting openers, as the closing kinds they accept
        self.last: int | None = None

    def check(self, index: int, lines: Sequence[str], report: bool = True) -> list[Finding]:
        """Read one box; report=False reads its marks only (a keep box, shown in English)."""
        if self.last is None or index != self.last + 1:
            self.pending = []
        self.last = index
        units = [(ch, k) for k, line in enumerate(lines, 1) for ch in skeleton(line) + " "]
        misses: list[tuple[int, str]] = []
        words = False
        p = 0
        while p < len(units):
            ch = units[p][0]
            if ch in OPENING or ch in CLOSING:
                end = _run_end(units, p, OPENING if ch in OPENING else CLOSING)
                run = frozenset(c for c, _ in units[p:end])
                if ch in OPENING:
                    self.pending.append(frozenset(OPENING[c] for c in run))
                else:
                    if not self._close(run) and words:
                        misses.append((units[p][1], _miss_detail(run, units, end)))
                    words = False
                p = end
                continue
            words = words or ch == CODE_MARK or ch.isalnum()
            p += 1
        if _ends_with_period(units):
            self.pending = []
        if not misses or not report:
            return []
        return [("W_INVERTED", misses[0][0], "; ".join(d for _, d in misses))]

    def _close(self, run: frozenset[str]) -> bool:
        """Close the newest waiting opener that accepts a kind of the run, and every newer one; repeat for the
        kinds of the run that opener did not accept. True if any opener was closed."""
        want = set(run)
        closed = False
        while want:
            j = next((j for j in range(len(self.pending) - 1, -1, -1) if self.pending[j] & want), None)
            if j is None:
                break
            want -= self.pending[j]
            del self.pending[j:]
            closed = True
        return closed


def _run_end(units: list[tuple[str, int]], p: int, marks: Iterable[str]) -> int:
    end = p
    while end < len(units) and units[end][0] in marks:
        end += 1
    return end


def _ends_with_period(units: list[tuple[str, int]]) -> bool:
    """The box ends with a single '.' (not '...'): its sentence is finished."""
    text = "".join(c for c, _ in units).rstrip(" ")
    return text.endswith(".") and not text.endswith("..")


def _miss_detail(run: frozenset[str], units: list[tuple[str, int]], end: int) -> str:
    wanted = " or ".join(o for o, c in OPENING.items() if c in run)
    context = "".join(c for c, _ in units[max(0, end - CONTEXT_UNITS):end]).replace(CODE_MARK, "[code]")
    return f"no opening {wanted} for {context.strip()!r}"


# ---------------------------------------------------------------------------------------- file checks

@dataclass
class _Run:
    """What every file check shares."""
    scope: str
    statuses: frozenset[str]
    ps: PakSet
    metrics: Metrics
    resolvers: dict[str, InsertResolver]
    glossary: Glossary | None
    specs: dict[str, FileSpec]          # by stem


@dataclass(frozen=True)
class _File:
    stem: str
    path: str
    profile: Profile | None
    slot: int
    resolver: InsertResolver
    english: list[str] | None           # the paks' English per entry; None when the entries do not match


def _issue(code: str, file: str, detail: str, entry: dict | None = None, line: int | None = None,
           preview: str = "", layout: Layout | None = None) -> Issue:
    entry = entry or {}
    return Issue(CHECKS[code][0], code, _printable(str(entry.get("id", ""))), file,
                 _printable(str(entry.get("name", ""))), line, _printable(detail), preview, layout)


def _entries(doc: dict) -> list[dict] | None:
    """The document's entries, or None when they are not a list of objects."""
    entries = doc.get("entries")
    if isinstance(entries, list) and all(isinstance(e, dict) for e in entries):
        return entries
    return None


def _profile_of(doc: dict) -> Profile | None:
    name = doc.get("profile")
    return profiles.PROFILES.get(name) if isinstance(name, str) else None


def entries_problem(doc: dict, stem: str, msg: msg23.Msg) -> str | None:
    """Why the work file's entries do not match the source msg, or None."""
    entries = _entries(doc)
    if entries is None:
        return "entries is not a list of objects"
    work, source = extract.structure(doc), [(e.name, e.guid) for e in msg.entries]
    if work != source:
        return "entries differ from the source msg: " + extract.describe_change(work, source)
    bad = [k for k, e in enumerate(entries) if e.get("i") != k or e.get("id") != extract.entry_id(stem, e.get("name"))]
    return f"{len(bad)} entries with a wrong i or id, first at index {bad[0]}" if bad else None


def _aligned_english(doc: dict, msg: msg23.Msg) -> list[str] | None:
    """The paks' English of every work-file entry, when the entries match the msg by name and guid."""
    if _entries(doc) is None or extract.structure(doc) != [(e.name, e.guid) for e in msg.entries]:
        return None
    return [msg.get(e, config.LANG_EN) for e in msg.entries]


def _stale_detail(doc: dict, spec: FileSpec, raw: bytes, english: list[str] | None, run: _Run) -> str | None:
    """Why the work file looks extracted from another version of the msg (W_STALE), or None."""
    parts = []
    sha = hashlib.sha256(raw).hexdigest()
    if doc.get("source_sha256") != sha:
        parts.append(f"source_sha256 {str(doc.get('source_sha256'))[:12]}... differs from "
                     f"{run.ps.source_name(spec.path)} ({sha[:12]}...)")
    if english is not None:
        changed = [e.get("name") for e, en in zip(doc["entries"], english) if e.get("en") != en]
        if changed:
            parts.append(f"{len(changed)} entries have an en that differs from the paks (first {changed[0]})")
    if not parts:
        return None
    return "; ".join(parts) + f"; re-run extract --scope {scopes.home_scope(spec.group)}"


def structure_findings(doc: dict, stem: str, run: _Run) -> tuple[list[tuple[str, str]], list[str] | None]:
    """E_STRUCTURE and W_STALE of one work file as (code, detail), and the paks' English per entry (None
    when the entries cannot be paired with the source msg)."""
    spec = run.specs.get(stem)
    if spec is None:
        return [("E_STRUCTURE", f"{doc['path']} is not a msg file of scope {run.scope}")], None
    out = []
    if doc["path"] != spec.path or doc.get("group") != spec.group:
        out.append(("E_STRUCTURE", f"path {doc['path']} ({doc.get('group')}) differs from the source "
                                   f"{spec.path} ({spec.group})"))
    if _profile_of(doc) is None:
        out.append(("E_STRUCTURE", f"unknown profile {doc.get('profile')!r}; text checks skipped"))
    raw = run.ps.read(spec.path)
    msg = msg23.parse(raw)
    problem = entries_problem(doc, stem, msg)
    if problem:
        out.append(("E_STRUCTURE", problem))
    english = _aligned_english(doc, msg)
    stale = _stale_detail(doc, spec, raw, english, run)
    if stale:
        out.append(("W_STALE", stale))
    return out, english


def _wrap_mode(entry: dict, profile: Profile) -> tuple[str, list[Finding]]:
    mode = entry.get("wrap")
    if mode in wrap.MODES:
        return mode, []
    return profile.wrap, [("E_STRUCTURE", None, f"unknown wrap mode {mode!r}; checked as {profile.wrap}")]


def _english_of(entry: dict, source: str | None) -> tuple[str | None, list[Finding]]:
    """The English to check against (the paks' when known) and the E_STRUCTURE finding of a bad en."""
    work = entry.get("en")
    if source is not None:
        if work == source:
            return source, []
        what = "en differs from" if isinstance(work, str) else f"en is not a text ({_type_name(work)}), unlike"
        return source, [("E_STRUCTURE", None, f"{what} the English in the paks: the build keeps this entry in "
                                              "English (checked against the paks' English); re-run extract")]
    if isinstance(work, str):
        return work, []
    return None, [("E_STRUCTURE", None, f"en is not a text ({_type_name(work)}); text checks skipped")]


def _flags_of(entry: dict) -> tuple[tuple[str, ...], list[Finding]]:
    flags = entry.get("flags") or []
    if isinstance(flags, list) and all(isinstance(f, str) for f in flags):
        return tuple(flags), []
    return (), [("E_STRUCTURE", None, f"flags is not a list of texts ({_type_name(flags)})")]


def _entry_findings(entry: dict, index: int, f: _File, run: _Run,
                    marks: InvertedMarks) -> tuple[list[Finding], wrap.WrapResult | None]:
    """Findings of one entry whose status reaches the minimum (or is unknown), and its final text."""
    status = entry.get("status")
    if status not in extract.STATUSES:
        return [("E_STATUS", None, f"unknown status {status!r} (valid: {', '.join(extract.STATUSES)})")], None
    es = entry.get("es")
    if not isinstance(es, str):
        return [("E_STRUCTURE", None, f"es is not a text ({_type_name(es)})")], None
    if not es:
        return [("E_STATUS", None, f"status {status} but es is empty")], None
    en, found = _english_of(entry, None if f.english is None else f.english[index])
    if en is None or f.profile is None:
        return found, None
    mode, more = _wrap_mode(entry, f.profile)
    flags, bad_flags = _flags_of(entry)
    final, text = check_text(es, en, profile=f.profile, path=f.path, slot=f.slot, metrics=run.metrics,
                             resolver=f.resolver, wrap_mode=mode, flags=flags, glossary=run.glossary)
    return found + more + bad_flags + text + marks.check(index, final.lines), final


def merge_findings(found: Iterable[Finding]) -> list[Finding]:
    """One finding per code (W_GLOSSARY: one per term), in code order: details joined, first line kept."""
    by_code: dict[str, list[Finding]] = {}
    for item in found:
        by_code.setdefault(item[0], []).append(item)
    out: list[Finding] = []
    for code in sorted(by_code, key=CODE_ORDER.__getitem__):
        items = by_code[code]
        if code == "W_GLOSSARY":
            out += items
            continue
        line = next((k for _, k, _ in items if k is not None), None)
        out.append((code, line, "; ".join(d for _, _, d in items)))
    return out


def entry_issues(entry: dict, index: int, f: _File, run: _Run, marks: InvertedMarks) -> list[Issue]:
    """Issues of one entry whose status reaches the minimum (or is unknown), in code order."""
    found, final = _entry_findings(entry, index, f, run, marks)
    if not found:
        return []
    found = merge_findings(found)
    if final is None:
        return [_issue(code, f.stem, detail, entry, line) for code, line, detail in found]
    layout = Layout(tuple(final.lines), tuple(final.widths_px), final.unit, final.max_px, final.max_lines,
                    f.profile.hard)
    preview = _preview(final.text)
    return [_issue(code, f.stem, detail, entry, line, preview, layout) for code, line, detail in found]


def _read_keep(entry: dict, index: int, f: _File, marks: InvertedMarks) -> None:
    """Let InvertedMarks read a keep box (shown in English) so a sentence can run through it."""
    en = entry.get("en") if f.english is None else f.english[index]
    if isinstance(en, str):
        marks.check(index, wrap.visible_lines(wrap.normalize_breaks(en)), report=False)


def _file_context(doc: dict, stem: str, run: _Run, english: list[str] | None) -> _File:
    profile = _profile_of(doc)
    slot = profile.slot if profile is not None and profile.slot is not None else profiles.observed_slot(doc["path"])
    return _File(stem, doc["path"], profile, slot, run.resolvers[doc["scope"]], english)


def check_file(doc: dict, run: _Run) -> tuple[list[Issue], FileSummary]:
    """Every issue of one work file, and its entry counts."""
    stem = scopes.stem_of(doc["path"])
    findings, english = structure_findings(doc, stem, run)
    issues = [_issue(code, stem, detail) for code, detail in findings]
    entries = _entries(doc) or []
    todo = sum(e.get("status") == "todo" for e in entries)
    if todo:
        issues.append(_issue("I_TODO", stem, f"{todo} of {len(entries)} entries untranslated (todo)"))
    f = _file_context(doc, stem, run, english)
    marks = InvertedMarks()
    translated = 0
    for k, entry in enumerate(entries):
        status = entry.get("status")
        reached = isinstance(status, str) and status in run.statuses
        translated += reached
        if reached or status not in extract.STATUSES:
            issues += entry_issues(entry, k, f, run, marks)
        elif status == KEEP_STATUS:
            _read_keep(entry, k, f, marks)
    return issues, FileSummary(stem, len(entries), translated, todo)


# ------------------------------------------------------------------------------------------- validate

def _scope_docs(scope: str, strings_dir: Path) -> list[tuple[Path, dict]]:
    """extract.scope_docs; its WorkFileError (a damaged work file, entries that are not a list of objects,
    a stray copy, a repeated id; each names the file) becomes a ValidateError."""
    try:
        return extract.scope_docs(scope, strings_dir)
    except extract.WorkFileError as exc:
        raise ValidateError(str(exc)) from exc


def _select(scope: str, docs: list[tuple[Path, dict]], specs: list[FileSpec],
            stems: Iterable[str] | None) -> tuple[list[tuple[Path, dict]], list[FileSpec], tuple[str, ...]]:
    if stems is None:
        return docs, specs, ()
    wanted = tuple(sorted(set(stems)))
    known = {sp.stem for sp in specs} | {p.stem for p, _ in docs}
    unknown = [s for s in wanted if s not in known]
    if unknown:
        raise ValidateError(f"no file of scope {scope} is named {', '.join(unknown)}")
    return [d for d in docs if d[0].stem in wanted], [s for s in specs if s.stem in wanted], wanted


def _load_metrics(fonts: str | None, ps: PakSet, cache: Path) -> Metrics:
    overrides = fontpatch.outputs(fonts, ps) if fonts else None
    return Metrics.load(overrides=overrides, cache_path=cache, paks=ps)


def _paks(ps: PakSet | None, metrics: Metrics | None, resolver: InsertResolver | None) -> PakSet:
    if ps is not None:
        return ps
    if resolver is not None:
        return resolver.paks
    return metrics.paks if metrics is not None else PakSet()


def validate(scope: str = "phase1", *, strings_dir: Path = config.STRINGS, min_status: str = "draft",
             fonts: str | None = None, metrics: Metrics | None = None, resolver: InsertResolver | None = None,
             ps: PakSet | None = None, stems: Iterable[str] | None = None, glossary_path: Path = GLOSSARY_PATH,
             metrics_cache: Path = CACHE_PATH) -> Report:
    """Check the work files of `scope` (or only the files named by `stems`).

    fonts      a fontpatch variant: widths and the character check use Metrics.load(overrides=
               fontpatch.outputs(fonts)) (cache at `metrics_cache`); not together with metrics / resolver.
    metrics    measurements to use (default: the official fonts); resolver: insert widths (default: an
               InsertResolver per base scope reading Spanish table texts from `strings_dir` at `min_status`).
    Raises FileNotFoundError when the scope has no work files, GlossaryError for a bad glossary file, and
    ValidateError for an unusable work file or folder (named), unknown stems or conflicting options."""
    t0 = time.perf_counter()
    statuses = inserts.statuses_from(min_status)
    if fonts is not None and (metrics is not None or resolver is not None):
        raise ValidateError("fonts selects the metrics; do not pass metrics or resolver with it")
    ps = _paks(ps, metrics, resolver)
    glossary = load_glossary(glossary_path)
    docs, specs, chosen = _select(scope, _scope_docs(scope, Path(strings_dir)), scopes.files(scope, ps), stems)
    if resolver is not None:
        metrics = resolver.metrics
    elif metrics is None:
        metrics = _load_metrics(fonts, ps, Path(metrics_cache))
    resolvers = {s: resolver or InsertResolver(metrics, s, work_dir=Path(strings_dir), paks=ps,
                                               min_status=min_status) for s in scopes.members(scope)}
    run = _Run(scope, statuses, ps, metrics, resolvers, glossary, {s.stem: s for s in specs})
    have = {p.stem for p, _ in docs}
    issues = [_issue("E_STRUCTURE", s.stem, f"no work file for {s.path}; run extract --scope "
                                            f"{scopes.home_scope(s.group)}") for s in specs if s.stem not in have]
    files = []
    for _, doc in docs:
        found, summary = check_file(doc, run)
        issues += found
        files.append(summary)
    return Report(scope, min_status, fonts, metrics.key, glossary_state(glossary, Path(glossary_path)), chosen,
                  issues, files, time.perf_counter() - t0)


# -------------------------------------------------------------------------------------------- reports

def report_paths(scope: str, out_dir: Path = REPORTS_DIR, selected: bool = False) -> tuple[Path, Path]:
    """CSV and MD paths: validate_<scope>.* for the whole scope, validate_<scope>_selected.* for some stems."""
    name = f"validate_{scope}_selected" if selected else f"validate_{scope}"
    out = Path(out_dir)
    return out / f"{name}.csv", out / f"{name}.md"


def check_report_path(path: Path) -> None:
    """Refuse the game folder, original/ (fontpatch._check_writable), build/ and dist/ (contract rule 0.3)."""
    fontpatch._check_writable(path)
    target = os.path.normcase(os.path.realpath(path))
    for folder in LOCAL_ONLY_REFUSED:
        root = os.path.normcase(os.path.realpath(folder)).rstrip("\\/")
        if target == root or target.startswith(root + os.sep):
            raise PermissionError(f"refusing to write reports under {folder}: they hold game text, which "
                                  "stays local (work/)")


def csv_bytes(report: Report) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for i in report.issues:
        w.writerow([i.level, i.code, i.id, i.file, i.entry, "" if i.line is None else i.line, i.detail,
                    i.es_preview])
    return buf.getvalue().encode("utf-8-sig")


def _md(text: str) -> str:
    """Text safe inside Markdown paragraphs and table cells."""
    return re.sub(r"([\\`*_\[\]<>|#])", r"\\\1", _printable(text))


def _fence(rows: list[str]) -> str:
    longest = max((len(m) for r in rows for m in re.findall(r"`+", r)), default=0)
    return "`" * max(3, longest + 1)


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def _md_header(r: Report) -> list[str]:
    levels = r.counts_by_level
    fonts = f"variant {r.fonts}" if r.fonts else "official"
    chosen = f" (selected: {', '.join(r.stems)})" if r.stems else ""
    statuses = ", ".join(sorted(inserts.statuses_from(r.min_status), key=inserts.TRANSLATED_ORDER.index))
    return [f"- Minimum status: {r.min_status} (text checks on {statuses})",
            f"- Fonts: {fonts} (metrics {r.metrics_key})",
            f"- Glossary: {_md(r.glossary)}",
            f"- Files checked: {r.files_checked:,}{_md(chosen)}",
            f"- Entries: {r.entries_checked:,}; translated and checked for text issues: {r.translated:,}",
            f"- Issues: {_plural(levels[ERROR], 'error')}, {_plural(levels[WARN], 'warning')}, "
            f"{levels[INFO]:,} info", ""]


def _md_codes(r: Report) -> list[str]:
    counts = r.counts_by_code
    files = Counter(code for code, _ in {(i.code, i.file) for i in r.issues})
    rows = [f"| {code} | {level} | {counts[code]:,} | {files[code]:,} | {_md(what)} |"
            for code, (level, what) in CHECKS.items()]
    return ["## Issues by code", "", "| code | level | issues | files | check |", "|---|---|---|---|---|",
            *rows, ""]


def _md_files(r: Report) -> list[str]:
    per = {f.file: Counter() for f in r.files}
    for i in r.issues:
        per.setdefault(i.file, Counter())[i.level] += 1
    summary = {f.file: f for f in r.files}
    worst = sorted((f for f, c in per.items() if c[ERROR] or c[WARN]),
                   key=lambda f: (-per[f][ERROR], -per[f][WARN], -per[f][INFO], f))[:WORST_FILES]
    out = ["## Worst files", ""]
    if not worst:
        return out + ["No file has errors or warnings.", ""]
    out += ["| file | errors | warnings | info | translated | entries |", "|---|---|---|---|---|---|"]
    for f in worst:
        s = summary.get(f)
        counts = (s.translated, s.entries) if s else ("-", "-")
        out.append(f"| {_md(f)} | {per[f][ERROR]} | {per[f][WARN]} | {per[f][INFO]} | {counts[0]} | {counts[1]} |")
    return out + ([f"Showing {len(worst)} files, most errors first."] if len(worst) == WORST_FILES else []) + [""]


def _layout_rows(layout: Layout) -> list[str]:
    rows = []
    for k, line in enumerate(layout.lines, 1):
        w = layout.widths[k - 1]
        over = w > layout.max_width + wrap.EPS or k > layout.max_lines
        rows.append(f"{k:2d} {_width(w, layout.unit):>11} {'!' if over else ' '} {_printable(line)}")
    where = "box limit" if layout.hard else "English maximum of the file"
    return rows + [f"limit {_width(layout.max_width, layout.unit)} x {_plural(layout.max_lines, 'line')} ({where})"]


def _md_example(i: Issue) -> list[str]:
    where = f"`{i.id}`" if i.id else f"file {_md(i.file)}"
    line = f" line {i.line}" if i.line is not None else ""
    out = [f"- {where}{line}: {_md(i.detail)}"]
    if i.layout is not None:
        rows = _layout_rows(i.layout)
        fence = _fence(rows)
        out += ["", f"  {fence}text", *(f"  {row}" for row in rows), f"  {fence}"]
    return out


def _md_examples(r: Report) -> list[str]:
    out = ["## Examples", ""]
    by_code: dict[str, list[Issue]] = {}
    for i in r.issues:
        by_code.setdefault(i.code, []).append(i)
    if not by_code:
        return out + ["No issues.", ""]
    for code in CHECKS:
        found = by_code.get(code)
        if not found:
            continue
        out += [f"### {code} ({CHECKS[code][0]}): {_plural(len(found), 'issue')}", ""]
        for i in found[:EXAMPLES_PER_CODE]:
            out += _md_example(i)
        out.append("")
    return out


def markdown(report: Report) -> str:
    lines = [f"# Validation report: {report.scope}", "", *_md_header(report), *_md_codes(report),
             *_md_files(report), *_md_examples(report)]
    return "\n".join(lines).rstrip("\n") + "\n"


def write_reports(report: Report, scope: str, out_dir: Path = REPORTS_DIR) -> tuple[Path, Path]:
    """Write the CSV and MD reports into out_dir (names: report_paths; refused folders: check_report_path).
    Raises PermissionError for a refused folder, OSError when a file cannot be written (e.g. open in a
    spreadsheet program; the old file then stays)."""
    csv_path, md_path = report_paths(scope, out_dir, selected=bool(report.stems))
    for p in (csv_path, md_path):
        check_report_path(p)
    extract.write_if_changed(csv_path, csv_bytes(report))
    extract.write_if_changed(md_path, markdown(report).encode("utf-8"))
    return csv_path, md_path


# ------------------------------------------------------------------------------------------------ CLI

def summary_lines(r: Report, quiet: bool = False) -> list[str]:
    levels = r.counts_by_level
    total = (f"errors {levels[ERROR]}, warnings {levels[WARN]}, info {levels[INFO]} "
             f"({r.elapsed:.1f} s)")
    if quiet:
        return [f"validate {r.scope}: {total}"]
    fonts = r.fonts or "official"
    chosen = f" ({len(r.stems)} selected)" if r.stems else ""
    out = [f"validate {r.scope}: {r.files_checked} files{chosen}, {r.entries_checked} entries, {r.translated} "
           f"translated (min status {r.min_status}); fonts {fonts}; glossary {r.glossary}"]
    counts = r.counts_by_code
    out += [f"  {code:12s} {CHECKS[code][0]:6s} {counts[code]:6d}" for code in CHECKS if counts[code]]
    shown = [i for i in r.issues if i.level != INFO][:CONSOLE_ISSUES]
    out += [f"  {i.level} {i.code} {i.id or i.file}{f' line {i.line}' if i.line else ''}: {i.detail}"
            for i in shown]
    if len(shown) < sum(1 for i in r.issues if i.level != INFO):
        out.append(f"  ... (first {CONSOLE_ISSUES} errors and warnings; all in the reports)")
    return out + [total]


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="validate", description="Check translation work files before a build.")
    ap.add_argument("--scope", default="phase1", choices=scopes.SCOPES)
    ap.add_argument("--min-status", default=inserts.TRANSLATED_ORDER[0], choices=inserts.TRANSLATED_ORDER,
                    help="lowest status whose Spanish is checked (the build's minimum)")
    ap.add_argument("--fonts", choices=list(fontpatch.VARIANTS),
                    help="measure with a font variant of sfes.fontpatch instead of the official fonts")
    ap.add_argument("--stem", dest="stems", action="extend", nargs="+", metavar="STEM",
                    help="check only these files (msg file names without .msg.23)")
    ap.add_argument("--quiet", action="store_true", help="print only the totals")
    ap.add_argument("--strings", type=Path, default=config.STRINGS, help="work file folder (default work/strings)")
    ap.add_argument("--glossary", type=Path, default=GLOSSARY_PATH, help="glossary file (default work/glossary.json)")
    ap.add_argument("--out", type=Path, default=REPORTS_DIR, help="report folder (default work/reports)")
    return ap.parse_args(argv)


def run_cli(args: argparse.Namespace) -> int:
    """Validate, write the reports and print the summary; exceptions go to main()."""
    report = validate(args.scope, strings_dir=args.strings, min_status=args.min_status, fonts=args.fonts,
                      stems=args.stems, glossary_path=args.glossary)
    paths = write_reports(report, args.scope, args.out)
    print("\n".join(summary_lines(report, args.quiet)))
    print("reports: " + ", ".join(map(str, paths)))
    return 1 if report.errors else 0


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        return run_cli(args)
    except (ValidateError, extract.WorkFileError, OSError) as exc:
        print(f"validate: {exc}")
        return 2
    except Exception:                       # noqa: BLE001 - keep exit code 1 for 'errors found'
        traceback.print_exc()
        print("validate: internal error (exit code 2)")
        return 2
