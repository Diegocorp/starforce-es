r"""Final game text of a Spanish entry: line-break normalization and automatic wrapping by pixel width.

final_text() always converts lone LF / CR outside codes to CRLF (the game's only line break). Then:
  manual  line breaks are kept as written.
  auto    the body (codes.body_and_trailing) is re-broken, then exactly codes.trailing_breaks(en) CRLFs
          are appended (also to a body of only spaces; an empty text stays empty).
          "\r\n\r\n" (or longer runs) inside the body stays as a hard paragraph break. A single break is
          hard too when the line before it ends with ':' (the SF1 get/use/give boxes: 'Geo obtuvo:' then
          '"<item>"!!'; 199 phase1 English entries) or the line after it is indented (starts with two or
          more spaces and has text, e.g. '*ding ding' then '      ding ding*'; the indentation is kept).
          Every other break becomes a space, runs of spaces collapse, and words are put back greedily so
          that every line fits the width limit. Breaks happen only at U+0020 between words; a code is
          never split. Non-printing codes (wait, face, ...)
          that follow a space stay with the preceding word; glyph and insert codes belong to the word they
          touch. Spaces at line ends are removed, except the outer padding of the body: the spaces before
          its first word / after its last word are kept as written when the English body is padded on
          that side too (397 SF1 dialogue entries are padded, most of them fixed-width choice labels
          such as '   No   ').
          A text with a line break inside a code (a typo such as '<ICON\r\nKEY_A>') is kept as written,
          with a note; the validator reports the code.
The multiset of codes never changes (checked on every call; CodesChangedError would mean a bug here).

Limits: hard profiles (dialogue_sf1, ...) use profile.max_px at profile.size and profile.max_lines;
widths are in px. Observed profiles use the widest English line of the same file in em at
profiles.observed_slot(path) (font size cancels out) and the English maximum line count of the file;
widths are in em (WrapResult.unit). They are measured with resolver.english(), so Spanish table or REF
texts in the work files never move them. final_text() takes them from the file's English in the official
paks (file_limits()); observed_limits() computes them from given English texts and its result can be
passed as final_text(limits=...). Both are cached per file, English, Metrics.state_key() and insert scope.
Visible lines are the body lines: a trailing CRLF adds no visible line.

CLI: run.py wrap --path PAK_PATH [--mode auto|manual] [--en ENGLISH] TEXT   (\n and \r are escapes)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from . import codes, config, msg23, profiles
from .codes import Token
from .inserts import InsertResolver
from .metrics import Metrics
from .pak import PakSet
from .profiles import Profile

MODES = ("auto", "manual")
EPS = 1e-6
CRLF = "\r\n"
PARAGRAPH_RX = re.compile(r"((?:\r\n){2,})")
SPACES_RX = re.compile(r"( +)")
HARD_BREAK_AFTER = ":"     # a line ending with this keeps its line break in auto mode
INDENT = "  "              # a line starting with this (and having content) keeps the break before it


@dataclass(frozen=True)
class Limits:
    """Where a text is drawn and how much fits. Widths and max_px share `unit` ('px' or 'em')."""
    slot: int
    size: float          # px per em used for widths (1.0 when unit is 'em')
    max_px: float
    max_lines: int
    hard: bool
    unit: str
    source: str


@dataclass
class WrapResult:
    """Final text, its visible lines with their widths (in `unit`), the limits and what exceeds them."""
    text: str
    lines: list[str]
    widths_px: list[float]
    max_px: float
    max_lines: int
    overflow_lines: bool
    overflow_width: bool
    notes: list[str] = field(default_factory=list)
    unit: str = "px"


class CodesChangedError(RuntimeError):
    """A transformation changed the multiset of codes (an internal error, never a translation error)."""


# ------------------------------------------------------------------------------------- line breaks

def normalize_breaks(text: str) -> str:
    """Every lone LF or CR outside a code becomes CRLF (a break inside a code belongs to that code)."""
    if "\r" not in text and "\n" not in text:
        return text
    return "".join(CRLF if t.kind in ("lf", "cr") else t.raw for t in codes.tokenize(text))


def visible_lines(text: str) -> list[str]:
    """Body lines of a CRLF text (trailing breaks add no visible line); [] for an empty body."""
    body, _ = codes.body_and_trailing(text)
    return codes.split_lines(body) if body else []


def code_with_break(text: str) -> str | None:
    """The first code of `text` that contains a CR or LF, or None."""
    return next((t.raw for t in codes.tokenize(text) if t.is_code and ("\r" in t.raw or "\n" in t.raw)), None)


# ------------------------------------------------------------------------------------------ limits

_OBSERVED: dict[tuple, Limits] = {}
_FILE_LIMITS: dict[tuple, Limits] = {}


def hard_limits(profile: Profile) -> Limits:
    return Limits(profile.slot, profile.size, profile.max_px, profile.max_lines, True, "px", profile.name)


def _context(metrics: Metrics, resolver: InsertResolver | None) -> tuple:
    """What observed limits depend on besides the English: the measurement state and the insert scope."""
    return metrics.state_key(), None if resolver is None else resolver.scope


def _english_limits(path: str, en_texts: Iterable[str], metrics: Metrics, resolver: InsertResolver | None,
                    source: str) -> Limits:
    slot = profiles.observed_slot(path)
    english = None if resolver is None else resolver.english()
    widths, counts = [0.0], [0]
    for en in en_texts:
        lines = visible_lines(normalize_breaks(en))
        counts.append(len(lines))
        widths.extend(metrics.line_em(line, slot, english) for line in lines)
    return Limits(slot, 1.0, max(widths), max(counts), False, "em", source)


def observed_limits(path: str, en_texts: Iterable[str], metrics: Metrics,
                    resolver: InsertResolver | None = None) -> Limits:
    """Limits of an observed-profile file from all of its English texts, inserts measured in English."""
    texts = list(en_texts)
    digest = hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode("utf-8")).hexdigest()
    key = (path, digest, _context(metrics, resolver))
    if key not in _OBSERVED:
        _OBSERVED[key] = _english_limits(path, texts, metrics, resolver, "English maximum of the file")
    return _OBSERVED[key]


def file_limits(path: str, metrics: Metrics, resolver: InsertResolver | None = None) -> Limits:
    """observed_limits() of a file from its English in the official paks."""
    key = (path, _context(metrics, resolver))
    if key not in _FILE_LIMITS:
        msg = msg23.parse(metrics.paks.read(path))
        ens = [msg.get(e, config.LANG_EN) for e in msg.entries]
        _FILE_LIMITS[key] = observed_limits(path, ens, metrics, resolver)
    return _FILE_LIMITS[key]


def clear_observed_cache() -> None:
    _OBSERVED.clear()
    _FILE_LIMITS.clear()


def _profile(profile: Profile | str) -> Profile:
    return profiles.PROFILES[profile] if isinstance(profile, str) else profile


def limits_for(profile: Profile | str, path: str, en: str, metrics: Metrics,
               resolver: InsertResolver | None) -> tuple[Limits, list[str]]:
    """Limits of one entry and notes: the profile's (hard) or the file's English maximum (observed)."""
    p = _profile(profile)
    if p.hard:
        return hard_limits(p), []
    if metrics.paks.has(path):
        return file_limits(path, metrics, resolver), []
    return (_english_limits(path, [en], metrics, resolver, "English of this entry"),
            [f"{path} is not in the paks; this entry's English is the limit"])


# ---------------------------------------------------------------------------------------- wrapping

Word = list[Token]


def words_of(paragraph: str) -> list[Word]:
    """Words separated by spaces; codes stay inside the word they touch, and a non-printing code after a
    space joins the preceding word."""
    words: list[Word] = []
    cur: Word = []
    for tok in codes.tokenize(paragraph):
        if tok.kind != "text":
            if tok.prints == "none" and not cur and words:
                words[-1].append(tok)
            else:
                cur.append(tok)
            continue
        for part in SPACES_RX.split(tok.raw):
            if part.startswith(" "):
                if cur:
                    words.append(cur)
                    cur = []
            elif part:
                cur.append(Token("text", part))
    if cur:
        words.append(cur)
    return words


def word_text(word: Word) -> str:
    return "".join(t.raw for t in word)


def greedy_lines(widths: list[float], space: float, limit: float) -> list[list[int]]:
    """Indices of the words on each line, filling lines left to right while they fit `limit`."""
    lines: list[list[int]] = []
    used = 0.0
    for i, w in enumerate(widths):
        if lines and used + space + w <= limit + EPS:
            lines[-1].append(i)
            used += space + w
        else:
            lines.append([i])
            used = w
    return lines


def ends_with_colon(line: str) -> bool:
    """The last visible character of the line (spaces and non-printing codes ignored) is ':'."""
    for tok in reversed(codes.tokenize(line)):
        if tok.prints == "none" or (tok.kind == "text" and not tok.raw.strip(" ")):
            continue
        return tok.kind == "text" and tok.raw.rstrip(" ").endswith(HARD_BREAK_AFTER)
    return False


def hard_break(before: str, after: str) -> bool:
    """A single line break that auto mode keeps: after a line ending with ':' or before an indented line."""
    return ends_with_colon(before) or (after.startswith(INDENT) and bool(after.strip(" ")))


def blocks_of(paragraph: str) -> list[str]:
    """The paragraph cut at its hard line breaks; the other breaks inside a block become spaces.
    Lines of only spaces are dropped (they would collapse into a single space anyway)."""
    lines = [line for line in codes.split_lines(paragraph) if line.strip(" ")] or [""]
    blocks = [lines[0]]
    for before, after in zip(lines, lines[1:]):
        if hard_break(before, after):
            blocks.append(after)
        else:
            blocks[-1] += " " + after
    return blocks


def wrap_block(block: str, keep_lead: bool, keep_trail: bool, limits: Limits, metrics: Metrics,
               resolver: InsertResolver | None) -> tuple[list[str], list[str]]:
    """Greedy lines of one block and notes; [] for a block without words. Its leading / trailing spaces
    are kept only if asked (padding, indentation)."""
    words = words_of(block)
    if not words:
        return [], []
    lead = block[: len(block) - len(block.lstrip(" "))] if keep_lead else ""
    trail = block[len(block.rstrip(" ")):] if keep_trail else ""
    space = metrics.text_em(" ", limits.slot) * limits.size
    widths = [sum(metrics.token_em(t, limits.slot, resolver) for t in w) * limits.size for w in words]
    widths[0] += len(lead) * space
    widths[-1] += len(trail) * space
    notes = [f"word wider than the limit ({w:.1f} > {limits.max_px:.1f} {limits.unit}): "
             f"{word_text(word)!r}" for word, w in zip(words, widths) if w > limits.max_px + EPS]
    lines = [" ".join(word_text(words[i]) for i in idx)
             for idx in greedy_lines(widths, space, limits.max_px)]
    lines[0] = lead + lines[0]
    lines[-1] += trail
    return lines, notes


def wrap_paragraph(paragraph: str, pad_lead: bool, pad_trail: bool, limits: Limits, metrics: Metrics,
                   resolver: InsertResolver | None) -> tuple[list[str], list[str], int]:
    """Lines of one paragraph, notes, and how many hard single line breaks were kept. `pad_lead` /
    `pad_trail`: keep the paragraph's outer padding on that side; an indented block keeps its indentation."""
    blocks = blocks_of(paragraph)
    lines: list[str] = []
    notes: list[str] = []
    filled = 0
    for i, block in enumerate(blocks):
        out, more = wrap_block(block, pad_lead or i > 0, pad_trail and i == len(blocks) - 1,
                               limits, metrics, resolver)
        lines += out
        notes += more
        filled += bool(out)
    return lines, notes, max(filled - 1, 0)


def auto_text(es: str, en: str, limits: Limits, metrics: Metrics,
              resolver: InsertResolver | None) -> tuple[str, list[str]]:
    """The re-broken body of `es` plus exactly codes.trailing_breaks(en) CRLFs, and notes."""
    if not es:
        return es, []
    body, _ = codes.body_and_trailing(es)
    trailing = CRLF * codes.trailing_breaks(en)
    if not body.strip(" \r\n"):
        return body + trailing, ["no visible text; kept as written"]
    en_body, _ = codes.body_and_trailing(normalize_breaks(en))
    parts = PARAGRAPH_RX.split(body)
    out: list[str] = []
    notes: list[str] = []
    kept = 0
    for k, part in enumerate(parts):
        if k % 2:
            out.append(part)                        # hard paragraph break, kept as written
            continue
        lines, more, hard = wrap_paragraph(part, k == 0 and en_body.startswith(" "),
                                           k == len(parts) - 1 and en_body.endswith(" "),
                                           limits, metrics, resolver)
        out.append(CRLF.join(lines))
        notes += more
        kept += hard
    if kept:
        notes.append(f"kept {kept} single line break(s) after ':' or before an indented line")
    text, _ = codes.body_and_trailing("".join(out))      # a last paragraph of only spaces leaves no line
    return text + trailing, notes


def _check_codes(before: str, after: str) -> None:
    if codes.code_signature(before) != codes.code_signature(after):
        raise CodesChangedError(f"wrapping changed the codes: {before!r} -> {after!r}")


def final_text(es: str, en: str, *, profile: Profile | str, path: str, metrics: Metrics,
               resolver: InsertResolver | None, wrap_mode: str, limits: Limits | None = None) -> WrapResult:
    """Normalize and (auto mode) re-wrap a Spanish entry; measure the result against its limits.
    `limits` replaces the profile's or file's limits (e.g. a result of observed_limits())."""
    if wrap_mode not in MODES:
        raise ValueError(f"wrap_mode must be one of {MODES}, not {wrap_mode!r}")
    notes: list[str] = []
    if limits is None:
        limits, notes = limits_for(profile, path, en, metrics, resolver)
    text = normalize_breaks(es)
    if text != es:
        notes.append("lone LF/CR converted to CRLF")
    broken = code_with_break(text) if wrap_mode == "auto" else None
    if broken is not None:
        notes.append(f"a code contains a line break; kept as written: {broken!r}")
    if wrap_mode == "auto" and broken is None:
        text, more = auto_text(text, en, limits, metrics, resolver)
        notes += more
    elif codes.trailing_breaks(text) != codes.trailing_breaks(en):
        notes.append(f"trailing breaks differ from English ({codes.trailing_breaks(text)} vs "
                     f"{codes.trailing_breaks(en)})")
    _check_codes(es, text)
    return measure(text, limits, metrics, resolver, notes)


def measure(text: str, limits: Limits, metrics: Metrics, resolver: InsertResolver | None,
            notes: list[str] | None = None) -> WrapResult:
    """Visible lines of a final text, their widths in limits.unit, and whether they exceed the limits."""
    lines = visible_lines(text)
    widths = [metrics.line_em(line, limits.slot, resolver) * limits.size for line in lines]
    return WrapResult(text, lines, widths, limits.max_px, limits.max_lines,
                      overflow_lines=len(lines) > limits.max_lines,
                      overflow_width=any(w > limits.max_px + EPS for w in widths),
                      notes=list(notes or []), unit=limits.unit)


# ------------------------------------------------------------------------------------------------ CLI

def _unescape(text: str) -> str:
    """Command-line escapes: backslash-r and backslash-n become CR and LF."""
    return text.replace("\\r", "\r").replace("\\n", "\n")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="wrap", description="Wrap one text the way the build does.")
    ap.add_argument("--path", default="natives/stm/text/rr1/message/eng/rr1_dm0000_eng.msg.23",
                    help="pak path of the msg file (selects the profile and, if observed, the limits)")
    ap.add_argument("--mode", default="auto", choices=MODES)
    ap.add_argument("--en", default="", help="English text (for the trailing breaks and padding)")
    ap.add_argument("text", help="Spanish text; backslash-n and backslash-r are line-break escapes")
    args = ap.parse_args(argv)
    ps = PakSet()
    metrics = Metrics.load(paks=ps)
    res = final_text(_unescape(args.text), normalize_breaks(_unescape(args.en)),
                     profile=profiles.profile_for(args.path), path=args.path, metrics=metrics,
                     resolver=InsertResolver(metrics, paks=ps), wrap_mode=args.mode)
    for line, w in zip(res.lines, res.widths_px):
        print(f"{w:8.1f} {res.unit}  {line}")
    print(f"limit {res.max_px:.1f} {res.unit} x {res.max_lines} lines; overflow lines={res.overflow_lines} "
          f"width={res.overflow_width}")
    for n in res.notes:
        print("note:", n)
    return 1 if res.overflow_lines or res.overflow_width else 0
