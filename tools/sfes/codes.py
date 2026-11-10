"""Control codes, tags and placeholders found in the game's message text.

Catalog with counts and examples: research/text/control_codes.csv and control_codes_examples.md.

Token kinds:
  cmd   _CMD_name(args)          e.g. _CMD_wait(5), _CMD_insert_option(0)
  tag   <NAME args>              e.g. <ICON INGAME_KEY_R>, <REF RR1_CHIPNAME_ENG_152>, <REPLACE  RR1_EX>
  ph    {n}                      .NET format placeholder
  nl    \\r\\n                     the only line break used by the game
  lf/cr lone \\n or \\r            never used by the game; always an error in a translation
  text  everything else
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

TOKEN_RX = re.compile(
    r"(?P<cmd>_CMD_(?P<cname>[A-Za-z0-9_]+?)\((?P<cargs>[^)]*)\))"
    r"|(?P<tag><(?P<tname>[A-Z][A-Z0-9_]*)(?P<targs>(?:\s[^<>]*)?)>)"
    r"|(?P<ph>\{\d+\})"
    r"|(?P<nl>\r\n)"
    r"|(?P<lf>\n)|(?P<cr>\r)"
)

# Commands that print nothing (pacing, portrait animation, page control). They may move with words but
# must stay. Every other _CMD_ (insert_*, *name, str1-4, num_*, pro_*, ...) inserts text at run time.
NONPRINT_CMD_PREFIX = ("wait", "rwait", "face_", "page_wait")
# Tags that draw one glyph/icon.
GLYPH_TAGS = {"ICON", "REPLACE", "DIR", "LADIR", "LADIRR", "LAARDIR"}
# Tags that insert text.
TEXT_TAGS = {"REF", "PLATMSG", "DATETIME"}


@dataclass(frozen=True)
class Token:
    kind: str           # cmd | tag | ph | nl | lf | cr | text
    raw: str
    name: str | None = None
    args: str | None = None

    @property
    def is_code(self) -> bool:
        return self.kind in ("cmd", "tag", "ph")

    @property
    def prints(self) -> str:
        """'none' (zero width), 'insert' (runtime text), 'glyph' (one icon), 'text', or 'break'."""
        if self.kind == "text":
            return "text"
        if self.kind in ("nl", "lf", "cr"):
            return "break"
        if self.kind == "ph":
            return "insert"
        if self.kind == "tag":
            if self.name in GLYPH_TAGS:
                return "glyph"
            return "insert" if self.name in TEXT_TAGS else "glyph"
        n = self.name or ""
        return "none" if n.startswith(NONPRINT_CMD_PREFIX) else "insert"


def tokenize(t: str) -> list[Token]:
    out: list[Token] = []
    pos = 0
    for m in TOKEN_RX.finditer(t):
        if m.start() > pos:
            out.append(Token("text", t[pos:m.start()]))
        g = m.lastgroup
        if m.group("cmd"):
            out.append(Token("cmd", m.group(0), m.group("cname"), m.group("cargs")))
        elif m.group("tag"):
            out.append(Token("tag", m.group(0), m.group("tname"), m.group("targs")))
        elif g == "ph":
            out.append(Token("ph", m.group(0), m.group(0)))
        else:
            out.append(Token(g, m.group(0)))
        pos = m.end()
    if pos < len(t):
        out.append(Token("text", t[pos:]))
    return out


def code_signature(t: str) -> Counter:
    """Multiset of control codes, tags and placeholders (exact raw text). Used to compare EN vs ES."""
    return Counter(tok.raw for tok in tokenize(t) if tok.is_code)


def strip_markup(t: str, keep_breaks: bool = True) -> str:
    """Visible-ish text: codes removed, line breaks kept as \\n (or dropped)."""
    parts = []
    for tok in tokenize(t):
        if tok.kind == "text":
            parts.append(tok.raw)
        elif tok.kind in ("nl", "lf", "cr"):
            parts.append("\n" if keep_breaks else " ")
    return "".join(parts)


def split_lines(t: str) -> list[str]:
    """Split on CRLF only (the game's line break). Codes stay inside their line."""
    return t.split("\r\n")


def trailing_breaks(t: str) -> int:
    n = 0
    while t.endswith("\r\n" * (n + 1)):
        n += 1
    return n


def body_and_trailing(t: str) -> tuple[str, int]:
    k = trailing_breaks(t)
    return (t[: len(t) - 2 * k] if k else t), k


def has_letters(t: str) -> bool:
    return any(ch.isalpha() for ch in strip_markup(t))
