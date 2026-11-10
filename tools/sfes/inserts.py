r"""Widths of runtime inserts: text the game puts into a message when it is shown.

What each insert is assumed to print (InsertResolver.describe() says which rule was used):
  _CMD_insert_option(0|1)      player given / family name: entries 0 / 1 of the base-only
                               rr1/message/rr1_player_name.msg.23 (English slot: 'Geo', 'Stelar').
  _CMD_insert_option(N >= 2)   other people's names and screen values: unknown, default width.
  _CMD_insert_itemname(id)     SF1 item / card name. The id is a game id, not an entry index: the English
  _CMD_insert_btlwarename(id)  script text/rr1/userdata/itemname.user.3.en (chipname.user.3.en for cards)
                               maps each id to a show-text command whose GUID names the msg entry.
                               Items (256 ids): 0-68 -> entries 0-68, 70-80 -> 69-79, 96-110 -> 80-94,
                               112-113 -> 95-96; 69, 81-95, 111 and 114+ are empty.
                               Cards (257 ids): 0-150 -> 0-150, 151-168 -> 206-223 (moved to the end of
                               the English table), 181-216 -> 151-186, 231-245 -> 187-201, 247 -> 202,
                               252-254 -> 203-205; 169-180, 217-230, 246, 248-251, 255+ are empty.
                               Ids with no entry use the widest entry of the table.
  _CMD_insert_itemnamefree(x)  runtime item / card variable: widest entry of the same table.
  _CMD_insert_btlwarenamefree(x)
  _CMD_insert_numdisp(fmt, v)  low nibble of fmt (hex) = digit count, measured with the widest digit;
                               low nibble 0 (no digit count; never used by SF1-SF3) gets the default.
  {n}                          3 digits.
  <REF EntryName>              text of that entry, found by name in any msg file of the paks
                               (index cached in work/cache/ref_index.json).
  <PLATMSG X>                  widest platform variant X_000, X_002, ... of common/localize.msg.23.
  <DATETIME {0} "fmt">         the format with every date/time field as digits and A as AM/PM;
                               "88:88" when no format is given.
  anything else                default: 8 average lowercase letters of the slot (metrics).
Table texts are Spanish when the table's work file (work/strings/<scope>/<group>/<stem>.json) has the entry
with a status at or above `min_status` (draft < done < reviewed; default draft, pass the build's minimum)
and a non-empty es, otherwise English. Missing or unreadable work files are treated as untranslated.
InsertResolver.english() is the same resolver without work files (English texts only); limits that are
defined by the English (sfes.wrap observed limits) are measured with it. Text inside inserted entries is
measured with the same rules (codes inside card names, e.g. <REPLACE  RR1_EX>, count as usual); a
multi-line entry counts as its widest line.

CLI: run.py inserts [--scope phase1] [--slot N] [--size PX] TEXT...   e.g. "_CMD_insert_itemname(112)"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import codes, config, msg23
from . import scope as scopes
from .metrics import Metrics
from .pak import PakSet

TRANSLATED_ORDER = ("draft", "done", "reviewed")     # statuses whose es the build uses, lowest first
REF_INDEX_PATH = config.CACHE / "ref_index.json"
REF_SCHEMA = 1
LOCALIZE = "natives/stm/text/common/localize.msg.23"
PLACEHOLDER_DIGITS = 3
MAX_NESTING = 3
LINE_RX = re.compile(r"\r\n|\n|\r")
DATE_FIELD_RX = re.compile(r"Y+|M+|D+|h+|H+|m+|s+|A|a")


@dataclass(frozen=True)
class TableSpec:
    """A name table: its msg file and the English script that maps game ids to entries (None: id = index)."""
    msg: str
    script: str | None


SF1_TABLES = {
    "player": TableSpec("natives/stm/text/rr1/message/rr1_player_name.msg.23", None),
    "item": TableSpec("natives/stm/text/rr1/message/eng/rr1_itemname_eng.msg.23",
                      "natives/stm/text/rr1/userdata/itemname.user.3.en"),
    "card": TableSpec("natives/stm/text/rr1/message/eng/rr1_chipname_eng.msg.23",
                      "natives/stm/text/rr1/userdata/chipname.user.3.en"),
}
TABLES_BY_SCOPE = {"phase1": SF1_TABLES}
COMMON_TABLES = {"localize": TableSpec(LOCALIZE, None)}
# command name -> (table, lookup): 'id' = first argument is a game id, 'any' = runtime variable (widest)
TABLE_COMMANDS = {
    "insert_itemname": ("item", "id"),
    "insert_itemnamefree": ("item", "any"),
    "insert_btlwarename": ("card", "id"),
    "insert_btlwarenamefree": ("card", "any"),
}
PLAYER_OPTIONS = {0: "player given name", 1: "player family name"}


@dataclass(frozen=True)
class Assumption:
    """What an insert prints: the widest of `texts`, plus `digits` digits, or the default width.
    `rule` is one of player, table_id, table_widest, digits, ref, platmsg, datetime, default;
    `note` says what was assumed (for reports)."""
    rule: str
    note: str
    texts: tuple[str, ...] = ()
    digits: int = 0
    default: bool = False


@dataclass
class Table:
    """Entries of a name table in file order, each with its chosen text and language ('es' or 'en')."""
    names: list[str]
    texts: list[str]
    langs: list[str]
    by_id: list[int | None] | None = None   # game id -> entry index (None: no entry); None: id = index

    def index_of(self, game_id: int) -> int | None:
        if self.by_id is None:
            return game_id if 0 <= game_id < len(self.names) else None
        return self.by_id[game_id] if 0 <= game_id < len(self.by_id) else None


# ---------------------------------------------------------------------------- English message scripts

_T_CMD, _T_BLOCK, _T_ROOT = 0x7F7EFE9C, 0x5BB3ECA0, 0xA94249FE
_SHOW_TEXT = 0xAC


def _align(pos: int, n: int) -> int:
    return pos + (-pos) % n


def _read_command(d: bytes, p: int) -> tuple[tuple[int, str], int]:
    p = _align(p, 4)
    op = struct.unpack_from("<I", d, p)[0]
    p = _align(p + 4, 8)
    guid = str(uuid.UUID(bytes_le=bytes(d[p:p + 16])))
    p += 16
    (n,) = struct.unpack_from("<I", d, p)
    p += 4
    for _ in range(n):
        p = _align(p, 4)
        (ln,) = struct.unpack_from("<I", d, p)
        p += 4 + 2 * ln
    return (op, guid), p


def script_guids(raw: bytes) -> list[str | None]:
    """Per message id of an English text script (*.user.3.en): GUID of the entry it shows, or None.
    Layout (Phase 1, tools/investigate/other_script_decode.py): USR header -> RSZ block whose instances are
    commands (u32 op, Guid, string[] params), blocks (2 x u32) and one root (cmd refs, entry points per id,
    block refs). Message id k runs from entry_points[k] to entry_points[k+1] in command order."""
    if raw[:4] != b"USR\0":
        raise ValueError("not a USR file")
    (base,) = struct.unpack_from("<Q", raw, 32)
    if raw[base:base + 4] != b"RSZ\0":
        raise ValueError("no RSZ block")
    (count,) = struct.unpack_from("<I", raw, base + 12)
    inst_off, data_off = struct.unpack_from("<2Q", raw, base + 24)
    types = [struct.unpack_from("<I", raw, base + inst_off + 8 * i)[0] for i in range(count)]
    p = base + data_off
    commands: dict[int, tuple[int, str]] = {}
    root: list[list[int]] = []
    for i, t in enumerate(types[1:], 1):
        if t == _T_CMD:
            commands[i], p = _read_command(raw, p)
        elif t == _T_BLOCK:
            p = _align(p, 4) + 8
        elif t == _T_ROOT:
            p = _align(p, 4)
            for _ in range(3):
                (n,) = struct.unpack_from("<I", raw, p)
                root.append(list(struct.unpack_from(f"<{n}I", raw, p + 4)))
                p += 4 + 4 * n
        else:
            raise ValueError(f"unknown instance type {t:08x}")
    if len(root) != 3:
        raise ValueError("script has no root object")
    ordered = [commands[r] for r in root[0]]
    starts = root[1] + [len(ordered)]
    return [next((g for op, g in ordered[a:b] if op == _SHOW_TEXT), None) for a, b in zip(starts, starts[1:])]


# -------------------------------------------------------------------------------------- work files

def _read_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def statuses_from(min_status: str) -> frozenset[str]:
    """Statuses whose Spanish is used: `min_status` and every higher one of TRANSLATED_ORDER."""
    if min_status not in TRANSLATED_ORDER:
        raise ValueError(f"min_status must be one of {TRANSLATED_ORDER}, not {min_status!r}")
    return frozenset(TRANSLATED_ORDER[TRANSLATED_ORDER.index(min_status):])


def spanish_texts(doc: dict, statuses: frozenset[str]) -> dict[str, str]:
    """entry name -> es for translated entries (status in `statuses`, es not empty)."""
    out = {}
    for e in doc.get("entries", []):
        if isinstance(e, dict) and e.get("status") in statuses and e.get("es"):
            out[e.get("name")] = e["es"]
    return out


class WorkTexts:
    """Spanish texts per msg path, read lazily from work/strings/<scope>/<group>/<stem>.json.
    `work_dir` None means no work files (every text is English)."""

    def __init__(self, work_dir: Path | None, scope: str, statuses: frozenset[str]):
        self.work_dir = work_dir
        self.scope = scope
        self.statuses = statuses
        self._files: dict[str, Path] | None = None
        self._texts: dict[str, dict[str, str]] = {}

    def _index(self) -> dict[str, Path]:
        if self._files is None:
            found = self.work_dir.glob("*/*/*.json") if self.work_dir else ()
            self._files = {}
            for p in sorted(found, key=lambda p: (p.parts[-3] != self.scope, str(p))):   # own scope first
                self._files.setdefault(p.stem, p)
        return self._files

    def for_path(self, msg_path: str) -> dict[str, str]:
        if msg_path not in self._texts:
            wp = self._index().get(scopes.stem_of(msg_path))
            self._texts[msg_path] = spanish_texts(_read_json(wp), self.statuses) if wp else {}
        return self._texts[msg_path]


# ------------------------------------------------------------------------------------- REF index

def _paks_identity(ps: PakSet) -> list[list]:
    out = []
    for pak in ps.paks:
        st = os.stat(pak.path)
        out.append([pak.path.name, st.st_size, st.st_mtime_ns])
    return out


def build_ref_index(ps: PakSet) -> dict[str, dict[str, str]]:
    """name -> {"path", "en"} for every entry that some English text references with <REF name>."""
    entries: dict[str, tuple[str, str]] = {}
    wanted: set[str] = set()
    for path in sorted(scopes.msg_paths(ps)):
        m = msg23.parse(ps.read(path))
        for e in m.entries:
            en = m.get(e, config.LANG_EN)
            entries[e.name] = (path, en)
            wanted.update(t.args.strip() for t in codes.tokenize(en) if t.kind == "tag" and t.name == "REF")
    return {n: {"path": entries[n][0], "en": entries[n][1]} for n in sorted(wanted) if n in entries}


def load_ref_index(ps: PakSet, cache_path: Path = REF_INDEX_PATH) -> dict[str, dict[str, str]]:
    """REF targets from the cache, rebuilt when the official paks change (name, size, mtime)."""
    ident = _paks_identity(ps)
    doc = _read_json(cache_path)
    if doc.get("schema") == REF_SCHEMA and doc.get("paks") == ident:
        return doc["targets"]
    targets = build_ref_index(ps)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
    body = {"schema": REF_SCHEMA, "paks": ident, "targets": targets}
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, cache_path)
    return targets


# ------------------------------------------------------------------------------------------ helpers

def _first_arg(token: codes.Token) -> str:
    return (token.args or "").split(",")[0].strip()


def _int_arg(s: str) -> int | None:
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s, 10)
    except ValueError:
        return None


def _hex_arg(s: str) -> int | None:
    try:
        return int(s[2:] if s.lower().startswith("0x") else s, 16)
    except ValueError:
        return None


def datetime_assumption(args: str) -> Assumption:
    """<DATETIME {0} "hh:mm A MM/DD/YYYY">: digit fields as digits, A as the wider of AM/PM."""
    m = re.search(r'"([^"]*)"', args)
    if m is None:
        return Assumption("datetime", 'no format: "88:88"', texts=(":",), digits=4)
    fmt = m.group(1)
    fields = DATE_FIELD_RX.findall(fmt)
    digits = sum(len(f) for f in fields if f not in ("A", "a"))
    literal = tuple(dict.fromkeys(_date_literals(fmt, ampm) for ampm in ("AM", "PM")))
    ampm = ", A as AM/PM" if len(literal) > 1 else ""
    return Assumption("datetime", f'format "{fmt}": {digits} digits{ampm}', texts=literal, digits=digits)


def _date_literals(fmt: str, ampm: str) -> str:
    """The non-digit part of a date format: literals kept, digit fields dropped, A/a replaced by `ampm`."""
    return DATE_FIELD_RX.sub(lambda f: ampm if f.group() in ("A", "a") else "", fmt)


def _quote(text: str, limit: int = 40) -> str:
    s = text.replace("\r\n", "/")
    return repr(s if len(s) <= limit else s[:limit - 3] + "...")


# ---------------------------------------------------------------------------------------- resolver

class InsertResolver:
    """Width in em of runtime inserts, per font slot. Pass it to Metrics.line_em / lines_px / wrap.
    `work_dir` defaults to work/strings; `english_only` ignores the work files (see english())."""

    def __init__(self, metrics: Metrics, scope: str = "phase1", work_dir: Path | None = None,
                 paks: PakSet | None = None, ref_index_path: Path = REF_INDEX_PATH, *,
                 min_status: str = TRANSLATED_ORDER[0], english_only: bool = False):
        self.metrics = metrics
        self.scope = scope
        self.english_only = english_only
        work = None if english_only else Path(work_dir) if work_dir else config.STRINGS
        self.work = WorkTexts(work, scope, statuses_from(min_status))
        self._paks = paks
        self._ref_index_path = Path(ref_index_path)
        self._refs: dict[str, dict[str, str]] | None = None
        self._tables: dict[str, Table | None] = {}
        self._assumptions: dict[str, Assumption] = {}
        self._widths: dict[tuple[str, int], float] = {}
        self._depth = 0
        self._english: InsertResolver | None = None

    @property
    def paks(self) -> PakSet:
        if self._paks is None:
            self._paks = PakSet()
        return self._paks

    def english(self) -> InsertResolver:
        """This resolver without work files: every table and REF text is the English one."""
        if self.english_only:
            return self
        if self._english is None:
            self._english = InsertResolver(self.metrics, self.scope, paks=self.paks,
                                           ref_index_path=self._ref_index_path, english_only=True)
        return self._english

    # ---- public API

    def width_em(self, token: codes.Token, slot: int) -> float:
        key = (token.raw, slot)
        if key in self._widths:
            return self._widths[key]
        width = self._measure(self.assumption(token), slot)
        if self._depth == 0:
            self._widths[key] = width
        return width

    def describe(self, token: codes.Token) -> str:
        return f"{token.raw}: {self.assumption(token).note}"

    def assumption(self, token: codes.Token) -> Assumption:
        if token.raw not in self._assumptions:
            self._assumptions[token.raw] = self._assume(token)
        return self._assumptions[token.raw]

    def table(self, name: str) -> Table | None:
        """A name table of the scope ('player', 'item', 'card') or 'localize'; None if unknown or missing."""
        if name not in self._tables:
            spec = TABLES_BY_SCOPE.get(self.scope, {}).get(name) or COMMON_TABLES.get(name)
            self._tables[name] = self._load_table(spec) if spec else None
        return self._tables[name]

    # ---- measuring

    def _measure(self, a: Assumption, slot: int) -> float:
        if a.default:
            return self.metrics.default_insert_em(slot)
        text = max((self._text_em(t, slot) for t in a.texts), default=0.0)
        return text + a.digits * self.metrics.digit_em(slot)

    def _text_em(self, text: str, slot: int) -> float:
        nested = self if self._depth < MAX_NESTING else None
        self._depth += 1
        try:
            return max(self.metrics.line_em(line, slot, nested) for line in LINE_RX.split(text))
        finally:
            self._depth -= 1

    # ---- rules

    def _assume(self, token: codes.Token) -> Assumption:
        if token.kind == "ph":
            return Assumption("digits", f"placeholder: {PLACEHOLDER_DIGITS} digits",
                              digits=PLACEHOLDER_DIGITS)
        if token.kind == "tag":
            return self._assume_tag(token)
        if token.name == "insert_option":
            return self._assume_option(token)
        if token.name in TABLE_COMMANDS:
            return self._assume_table(token, *TABLE_COMMANDS[token.name])
        if token.name == "insert_numdisp":
            fmt = _hex_arg(_first_arg(token))
            digits = 0 if fmt is None else fmt & 0xF
            if digits:
                return Assumption("digits", f"number: {digits} digits", digits=digits)
            return self._default(f"number format {_first_arg(token)!r} has no digit count")
        return self._default("no rule for this insert")

    def _default(self, why: str) -> Assumption:
        return Assumption("default", f"{why}; default 8 x average lowercase", default=True)

    def _assume_tag(self, token: codes.Token) -> Assumption:
        target = (token.args or "").strip()
        if token.name == "REF":
            return self._assume_ref(target)
        if token.name == "PLATMSG":
            return self._assume_platmsg(target)
        if token.name == "DATETIME":
            return datetime_assumption(token.args or "")
        return self._default(f"unknown text tag {token.name}")

    def _assume_option(self, token: codes.Token) -> Assumption:
        n = _int_arg(_first_arg(token))
        table = self.table("player")
        if n not in PLAYER_OPTIONS or table is None or n >= len(table.names):
            return self._default(f"option({_first_arg(token)}) is filled at run time")
        note = f"{PLAYER_OPTIONS[n]} {table.names[n]} = {_quote(table.texts[n])} ({table.langs[n]})"
        return Assumption("player", note, texts=(table.texts[n],))

    def _assume_table(self, token: codes.Token, name: str, lookup: str) -> Assumption:
        table = self.table(name)
        if table is None:
            return self._default(f"no {name} table for scope {self.scope}")
        widest = Assumption("table_widest", f"widest of {len(table.names)} {name} names",
                            texts=tuple(table.texts))
        if lookup == "any":
            return widest
        game_id = _int_arg(_first_arg(token))
        idx = None if game_id is None else table.index_of(game_id)
        if idx is None:
            return Assumption("table_widest", f"{name} id {_first_arg(token)} has no entry; " + widest.note,
                              texts=widest.texts)
        note = f"{name} id {game_id} -> {table.names[idx]} = {_quote(table.texts[idx])} ({table.langs[idx]})"
        return Assumption("table_id", note, texts=(table.texts[idx],))

    def _assume_ref(self, target: str) -> Assumption:
        if self._refs is None:
            self._refs = load_ref_index(self.paks, self._ref_index_path)
        ref = self._refs.get(target)
        if ref is None:
            return self._default(f"REF target {target} not found")
        es = self.work.for_path(ref["path"]).get(target)
        text, lang = (es, "es") if es else (ref["en"], "en")
        return Assumption("ref", f"REF {target} = {_quote(text)} ({lang})", texts=(text,))

    def _assume_platmsg(self, target: str) -> Assumption:
        table = self.table("localize")
        pairs = zip(table.names, table.texts) if table else ()
        variants = [t for n, t in pairs if n.startswith(target + "_")]
        if not variants:
            return self._default(f"no platform variants for {target}")
        return Assumption("platmsg", f"widest of {len(variants)} variants of {target}", texts=tuple(variants))

    # ---- tables

    def _load_table(self, spec: TableSpec) -> Table | None:
        if not self.paks.has(spec.msg):
            return None
        m = msg23.parse(self.paks.read(spec.msg))
        es = self.work.for_path(spec.msg)
        names = [e.name for e in m.entries]
        texts, langs = [], []
        for e in m.entries:
            t = es.get(e.name)
            texts.append(t if t else m.get(e, config.LANG_EN))
            langs.append("es" if t else "en")
        table = Table(names, texts, langs)
        if spec.script is not None:
            table.by_id = self._id_map(spec.script, m)
        return table

    def _id_map(self, script: str, m: msg23.Msg) -> list[int | None]:
        """game id -> entry index from the English script; empty (every id unresolved) if it is missing."""
        if not self.paks.has(script):
            return []
        index = {e.guid: i for i, e in enumerate(m.entries)}
        return [index.get(g) if g else None for g in script_guids(self.paks.read(script))]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="inserts", description="Show the assumed width of runtime inserts.")
    ap.add_argument("--scope", default="phase1")
    ap.add_argument("--slot", type=int, default=0)
    ap.add_argument("--size", type=float, default=60.0)
    ap.add_argument("codes", nargs="+", help="text containing insert codes")
    args = ap.parse_args(argv)
    ps = PakSet()
    r = InsertResolver(Metrics.load(paks=ps), scope=args.scope, paks=ps)
    for text in args.codes:
        for tok in codes.tokenize(text):
            if tok.prints == "insert":
                em = r.width_em(tok, args.slot)
                print(f"{em:7.3f} em {em * args.size:7.1f} px  {r.describe(tok)}")
    return 0
