r"""Shared text rules for validation and the build: code comparison and DATETIME formats.

codes.code_signature() compares the raw text of every code. One part of one code is localizable: the
quoted format of a <DATETIME {n} "format"> tag. English uses the US order ('hh:mm A MM/DD/YYYY'); Spanish
wants day/month/year and usually a 24-hour clock ('DD/MM/YYYY HH:mm'), which the Japanese and Chinese slots
of the same entries already use. signature() replaces the format with '*', so English and Spanish compare
equal when only the format differs, and format_problems() checks each Spanish format:
  - only the fields the game's own formats use (all languages, all files): YYYY MM DD HH hh mm A;
  - each field at most once;
  - the same kinds of fields as the English format (year, month, day, hour, minute), AM/PM aside;
  - A (AM/PM) only together with the 12-hour hh, and hh only together with A;
  - no letters outside those fields (the formatter could read them as fields), and between the fields only
    the separators SEPARATORS ('/', ':', '.', '-', space, U+3000; the game's own formats use '/', ':', space
    and U+3000).
Formats are paired by the tag's argument ({0}, {1}, ...), in order of appearance.
"""
from __future__ import annotations

import re
from collections import Counter

from . import codes

DATETIME_RX = re.compile(r'^<DATETIME (?P<arg>\{\d+\}) "(?P<fmt>[^"]*)">$')
FIELD_RX = re.compile(r"YYYY|MM|DD|HH|hh|mm|A")
KIND = {"YYYY": "year", "MM": "month", "DD": "day", "HH": "hour", "hh": "hour", "mm": "minute", "A": "ampm"}
WILDCARD = "*"
SEPARATORS = frozenset("/:.- 　")


def datetime_parts(raw: str) -> tuple[str, str] | None:
    """(argument, format) of a <DATETIME {n} "format"> code, else None."""
    m = DATETIME_RX.match(raw)
    return (m.group("arg"), m.group("fmt")) if m else None


def code_key(raw: str) -> str:
    """The raw code, with a DATETIME format replaced by '*'."""
    parts = datetime_parts(raw)
    return f'<DATETIME {parts[0]} "{WILDCARD}">' if parts else raw


def signature(text: str) -> Counter:
    """Multiset of codes as compared between English and Spanish (codes.code_signature + code_key)."""
    return Counter(code_key(tok.raw) for tok in codes.tokenize(text) if tok.is_code)


def code_diff(en: str, es: str) -> tuple[list[str], list[str]]:
    """(codes missing from es, codes es has in excess), each sorted; both empty when the codes match."""
    a, b = signature(en), signature(es)
    return sorted((a - b).elements()), sorted((b - a).elements())


def codes_match(en: str, es: str) -> bool:
    return signature(en) == signature(es)


def datetime_formats(text: str) -> list[tuple[str, str]]:
    """(argument, format) of every DATETIME code in text, in order."""
    out = []
    for tok in codes.tokenize(text):
        if tok.kind == "tag" and tok.name == "DATETIME":
            parts = datetime_parts(tok.raw)
            if parts:
                out.append(parts)
    return out


def format_problem(en_fmt: str, es_fmt: str) -> str | None:
    """Why es_fmt cannot replace en_fmt, or None when it can."""
    if es_fmt == en_fmt:
        return None
    fields = FIELD_RX.findall(es_fmt)
    rest = FIELD_RX.sub("", es_fmt)
    letters = sorted({ch for ch in rest if ch.isalpha()})
    if letters:
        return f"letters outside date fields: {''.join(letters)!r} (allowed fields: YYYY MM DD HH hh mm A)"
    others = sorted({ch for ch in rest if ch not in SEPARATORS})
    if others:
        shown = " ".join(f"U+{ord(ch):04X}" for ch in others)
        return f"characters between the date fields other than / : . - space U+3000: {shown}"
    repeated = sorted(f for f, n in Counter(fields).items() if n > 1)
    if repeated:
        return f"field repeated: {' '.join(repeated)}"
    kinds_en = {KIND[f] for f in FIELD_RX.findall(en_fmt)} - {"ampm"}
    kinds_es = {KIND[f] for f in fields} - {"ampm"}
    if kinds_es != kinds_en:
        missing, extra = sorted(kinds_en - kinds_es), sorted(kinds_es - kinds_en)
        detail = "; ".join(x for x in (f"missing {', '.join(missing)}" if missing else "",
                                       f"extra {', '.join(extra)}" if extra else "") if x)
        return f"different fields than the English format ({detail})"
    if "A" in fields and "hh" not in fields:
        return "A (AM/PM) without the 12-hour field hh"
    if "hh" in fields and "A" not in fields:
        return "12-hour field hh without A (AM/PM); use HH for a 24-hour clock"
    return None


def format_problems(en: str, es: str) -> list[str]:
    """Problems of every changed DATETIME format in es, compared with the English format of the same argument."""
    en_by_arg: dict[str, list[str]] = {}
    for arg, fmt in datetime_formats(en):
        en_by_arg.setdefault(arg, []).append(fmt)
    seen: Counter = Counter()
    out = []
    for arg, fmt in datetime_formats(es):
        k = seen[arg]
        seen[arg] += 1
        ens = en_by_arg.get(arg, [])
        if k >= len(ens):
            continue                       # an extra code: code_diff reports it
        problem = format_problem(ens[k], fmt)
        if problem:
            out.append(f'<DATETIME {arg} "{fmt}">: {problem}')
    return out


def format_changed(en: str, es: str) -> bool:
    """True when es changes a DATETIME format (allowed, but worth a note)."""
    return [f for _, f in datetime_formats(en)] != [f for _, f in datetime_formats(es)]
