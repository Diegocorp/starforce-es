r"""In-game probe pack: a temporary test build that answers the open in-game questions.

The questions (Phase 1 report section 11) need the game running: does our patch pak load, where accented
letters and our own ¡ ¿ glyphs sit, what the dialogue box does with a 4th or an over-wide line, which font the
space comes from, how menus treat long labels, and whether a changed DATETIME format works. The probe answers
them with labelled test texts ('#3 ...') that the project owner reads in the game, following the generated
PROBE_CHECKLIST.md.

Spec: work/probe/probe_spec.json (read only). "variants" names A-D; each of "items" replaces the English slot
(config.LANG_EN) of one entry of "file", found by "entry" (entry name) or "match_en" (the exact English text,
which must match exactly one entry). The text is "es", or "computed" from font metrics (below); "wrap" is
'manual' or 'auto' (sfes.wrap); "format_change": true lets the item change a DATETIME format (textrules
rules); "n" is the label number (0 = unnumbered helper text); "check" says what to look at. Refused: unknown
keys, a file outside scope phase1, an entry used twice, a label number used twice, a computed item outside a
hard (dialogue) profile.

Computed items (the dialogue profile gives slot, font size and box width: dialogue_sf1 = slot 0, 60 px,
1026 px; the game wraps over-wide lines by itself):
  space_calibration  one line '#N <words>' that measures about CALIBRATION_PX[0] px if the engine uses the
                     narrow space of the font that draws the letters (BO-TBGoStd-Medium; its advance is read
                     from the font with fontTools) and about CALIBRATION_PX[1] px if it uses the space of the
                     font that serves it first in the chain (RR_System_Font, which sfes.metrics assumes). The
                     number of spaces sets the difference of the two widths; the words bring their midpoint
                     to the midpoint of the targets.
  overlong_line      one line '#N <words>' of about OVERLONG_PX px (current metrics) with no break.
The words are plain Spanish without accents (SPACE_WORDS, OVERLONG_WORDS); pick_words() chooses, in order, the
subsequence whose width is closest to the target. Computed lines carry no trailing line break, so the box
shows exactly that line (on one line, or wrapped onto two).

Variants (VARIANT_FONTS): A text only; B, C, D the same text plus fontpatch.outputs('rrsystem',
'rrsystem_range', 'supplement'). Per variant: the real phase1 work documents of the spec's files
(extract.scope_docs), deep-copied in memory, every entry reset to status 'todo' and es '' (real translations
never reach the probe), the items set with status 'done', then build.apply_docs with
Metrics.load(overrides=<the variant's fonts or None>) and an English-only InsertResolver. The work files are
only read.

Checks (a ProbeError lists every problem, and nothing is written):
  - every item passes the build's safety gate in every variant (a refused item or file is an error);
  - every item changes its entry's text; only "format_change" items change a DATETIME format;
  - dialogue (hard profile) items fit their box, except DELIBERATE_OVERFLOW (#23 four lines, #24 wider than
    the box with the wide space, #25 wider than the box), which must overflow exactly that way; #24 must lie
    CALIBRATION_MARGIN_PX inside the box with the narrow space and outside it with the wide one; #25 must lie
    within OVERLONG_TOLERANCE_PX of OVERLONG_PX;
  - an item's final text is the same in every variant (only the fonts differ between variants);
  - a changed msg file differs from its source only in the English slot of the item entries;
  - each written pak reads back (build.pak_readback: files, marker, entry count, fingerprint); if one does
    not, the output folder is removed again.

Outputs in the output folder (one of OUT_ROOTS, dist/probe (the default) and the tests' scratch folder
dist/_test_probe, or a folder inside one; it is cleared first, so no other folder of dist is accepted; every
path goes through fontpatch._check_writable):
  <V>/re_chunk_000.pak.patch_NNN.pak  per variant: the changed msg files, the variant's font files and a
                                      marker entry (PROBE_MARKER); NNN = PakSet.next_free_index(); entry
                                      attributes from build.pak_templates, fingerprint of the newest official
                                      pak, checksums zero, as build.write_outputs writes its pak
  probe_manifest.json                 per variant: pak name, sha256, size, files with sha256; per item: n,
                                      file, entry, final text (as written into the msg), lines, widths per
                                      variant, overflow, notes; the computed values of #24 and #25 (text,
                                      spaces, both space advances, both widths); spec_sha256 = sha256 of the
                                      spec's canonical JSON (extract.dump_json)
  install_probe.bat VARIANT [GAME]    copies one variant's pak into the game folder (default: config.GAME_DIR)
  uninstall_probe.bat [GAME]          deletes it again, only if it is byte-identical to a probe pak
  PROBE_CHECKLIST.md                  English walk-through for the owner: save backup first, install, the
                                      screens in CHECKLIST_STEPS order, other variants, uninstall, safety notes
The .bat files are ASCII with CRLF line ends, generated from INSTALL_BAT and UNINSTALL_BAT. They use the game
path only quoted (it holds spaces and parentheses): never in a parenthesized if block, and in a for list only
as one quoted item. They use goto labels, setlocal with delayed expansion off, no CALL with a path argument
(CALL expands % and doubles ^ a second time), and exit 0 on success, 1 on refusal or error. Both files check
the game folder the same way (GAME_CHECK_BAT): the path is made absolute, and they refuse a missing folder, the
probe folder or a folder inside it (a relative 'B' given from the probe folder names the probe's own variant
folder), and a folder without the official paks (re_chunk_000.pak and every official patch, among them the
patch_002 placeholder). install also refuses an existing target file (it names the probe variant if fc /b
finds the file identical to one, else calls it unknown) and removes its own copy again if the copy or its
fc /b check fails. uninstall refuses a bare variant letter (it takes none) and deletes only a file that fc /b
finds identical to a probe pak of its folder.
No timestamps are written: the same inputs give byte-identical files.

CLI: run.py probe [--out DIR] [--variants A,B,C,D]
     exit 0 success, 1 the spec or a probe check failed (nothing written), 2 usage, input or internal error.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import logging
import shutil
import traceback
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from fontTools.ttLib import TTFont

from . import build, config, extract, fontpatch, msg23, oft, profiles, textrules, wrap
from . import scope as scopes
from .inserts import InsertResolver
from .metrics import Metrics
from .pak import PakSet, PakWriter
from .profiles import Profile

SCOPE = "phase1"
SPEC_PATH = config.WORK / "probe" / "probe_spec.json"
OUT_DIR = config.DIST / "probe"
SCRATCH_DIR = config.DIST / "_test_probe"          # the tests' scratch folder
OUT_ROOTS = (OUT_DIR, SCRATCH_DIR)                 # the only folders (and their subfolders) write_probe clears
STEAM_APP_ID = "3500390"                           # the collection on Steam (Phase 1 report)
STEAM_USERDATA = r"C:\Program Files (x86)\Steam\userdata"   # where Steam keeps saves on a default install
VARIANT_FONTS: dict[str, str | None] = {"A": None, "B": "rrsystem", "C": "rrsystem_range", "D": "supplement"}
VARIANT_SUMMARY = {
    "A": "text only, fonts unchanged",
    "B": "text plus our own ¡ and ¿ in the dialogue font (main candidate)",
    "C": "B plus a font-slot change that moves accented letters and ¡ ¿ together with plain letters",
    "D": "text plus ¡ and ¿ in a separate small font (in case they are cut at the bottom in B)",
}
FIRST_VARIANT = "B"
PROBE_STATUS = "done"
RESET_STATUS = "todo"
ITEM_KEYS = frozenset({"n", "file", "entry", "match_en", "es", "computed", "wrap", "format_change", "check"})
SPACE_CALIBRATION, OVERLONG_LINE = "space_calibration", "overlong_line"
COMPUTED_KINDS = (SPACE_CALIBRATION, OVERLONG_LINE)
CALIBRATION_PX = (990.0, 1070.0)       # target widths with the narrow and with the wide space
CALIBRATION_MARGIN_PX = 20.0           # both widths must lie at least this far from the box edge
OVERLONG_PX = 1300.0
OVERLONG_TOLERANCE_PX = 20.0
OVERLONG_COUNT_RANGE = 3               # word counts tried on each side of the estimate
WORD_STEP_PX = 1.0                     # pick_words keeps one candidate per step of width and word count
SPACE_WORDS = tuple("esta frase de prueba tiene palabras simples para medir el ancho real de cada espacio entre "
                    "las palabras y saber si cabe entera en una sola fila de la caja o si se parte en dos".split())
OVERLONG_WORDS = tuple("esta otra frase de prueba es demasiado larga para la caja de texto y no tiene saltos "
                       "manuales para ver si el juego la corta la encoge o la parte en dos filas por su "
                       "cuenta".split())
# Spec items that overflow their dialogue box on purpose, and how (the spec's checks of #23, #24 and #25).
DELIBERATE_OVERFLOW = {23: frozenset({"lines"}), 24: frozenset({"width"}), 25: frozenset({"width"})}
PROBE_MARKER = "Spanish fan translation patch (sfes): in-game probe variant {variant}, a temporary test build\n"
MANIFEST_SCHEMA = 1
MANIFEST_NAME = "probe_manifest.json"
INSTALL_NAME = "install_probe.bat"
UNINSTALL_NAME = "uninstall_probe.bat"
CHECKLIST_NAME = "PROBE_CHECKLIST.md"
BAT_FORBIDDEN = frozenset('%"\r\n')    # characters a path written into a .bat file may not hold
EXAMPLE_GAME = r"D:\SteamLibrary\steamapps\common\MegaMan StarForce LegacyCollection"
GAME_HINT = "Leave the game folder out to use the default one, or give it in quotes, for example:"


class ProbeError(ValueError):
    """The spec cannot be built as written, or a probe check failed; `problems` lists every reason."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("probe not built:\n  " + "\n  ".join(self.problems))


def _raise_if(problems: Sequence[str]) -> None:
    if problems:
        raise ProbeError(problems)


# ---------------------------------------------------------------------------------------------- spec

@dataclass(frozen=True)
class Item:
    """One validated spec item; k is its position in the spec."""
    k: int
    n: int
    file: str
    entry: str | None
    match_en: str | None
    es: str | None
    computed: str | None
    wrap: str
    format_change: bool
    check: str

    @property
    def label(self) -> str:
        return f"#{self.n}" if self.n else f"item {self.k + 1} (unnumbered)"


def load_spec(path: Path = SPEC_PATH) -> dict:
    """The probe spec document (read only)."""
    return extract.load_json(path)


def _type_problems(raw: dict) -> list[str]:
    n = raw.get("n")
    out = [] if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else ["n must be a whole number >= 0"]
    out += [f"{key} must be a text" for key in ("file", "entry", "match_en", "es", "check")
            if key in raw and not isinstance(raw[key], str)]
    if "file" not in raw:
        out.append("file missing")
    if ("entry" in raw) == ("match_en" in raw):
        out.append("give exactly one of entry and match_en")
    if ("es" in raw) == ("computed" in raw):
        out.append("give exactly one of es and computed")
    if "computed" in raw and raw["computed"] not in COMPUTED_KINDS:
        out.append(f"computed must be one of {', '.join(COMPUTED_KINDS)}")
    if raw.get("wrap") not in wrap.MODES:
        out.append(f"wrap must be one of {', '.join(wrap.MODES)}")
    if not isinstance(raw.get("format_change", False), bool):
        out.append("format_change must be true or false")
    return out


def _item(k: int, raw: object) -> tuple[Item | None, list[str]]:
    where = f"spec item {k + 1}"
    if not isinstance(raw, dict):
        return None, [f"{where}: not an object"]
    problems = [f"unknown key {key!r}" for key in sorted(set(raw) - ITEM_KEYS)] + _type_problems(raw)
    if problems:
        return None, [f"{where}: {p}" for p in problems]
    return Item(k, raw["n"], raw["file"], raw.get("entry"), raw.get("match_en"), raw.get("es"),
                raw.get("computed"), raw["wrap"], raw.get("format_change", False), raw.get("check", "")), []


def parse_spec(spec: object) -> list[Item]:
    """Validated items of a spec document; ProbeError lists every problem."""
    if not isinstance(spec, dict) or not isinstance(spec.get("items"), list) or not spec["items"]:
        raise ProbeError(["the spec has no list of items"])
    variants = spec.get("variants")
    problems = ([] if isinstance(variants, dict) and sorted(variants) == sorted(VARIANT_FONTS)
                else [f"the spec must describe exactly the variants {', '.join(VARIANT_FONTS)}"])
    items = []
    for k, raw in enumerate(spec["items"]):
        item, more = _item(k, raw)
        problems += more
        if item is not None:
            items.append(item)
    numbers = [i.n for i in items if i.n]
    problems += [f"label #{n} is used by more than one item" for n in sorted(set(numbers)) if numbers.count(n) > 1]
    _raise_if(problems)
    return items


def select_variants(names: Iterable[str]) -> tuple[str, ...]:
    """The named variants in VARIANT_FONTS order (case-insensitive); unknown or no variants are an error."""
    wanted = {v.strip().upper() for v in names if v.strip()}
    unknown = sorted(wanted - set(VARIANT_FONTS))
    if unknown or not wanted:
        raise ValueError(f"variants must be some of {', '.join(VARIANT_FONTS)}, not {', '.join(unknown) or 'none'}")
    return tuple(v for v in VARIANT_FONTS if v in wanted)


# ------------------------------------------------------------------------------------------- targets

@dataclass(frozen=True)
class Target:
    """The entry an item replaces (en is the work file's English, which the build checks against the paks)."""
    item: Item
    path: str
    index: int
    name: str
    en: str
    profile: Profile

    @property
    def id(self) -> str:
        return extract.entry_id(scopes.stem_of(self.path), self.name)

    @property
    def where(self) -> str:
        return f"{self.item.label} {self.id}"


def source_docs(paths: Iterable[str], strings_dir: Path) -> dict[str, dict]:
    """Deep copies of the scope's work documents of `paths` (scope order), every entry reset to status todo
    with empty es; the work files are only read."""
    wanted = set(paths)
    found = {d["path"]: d for _, d in extract.scope_docs(SCOPE, strings_dir) if d["path"] in wanted}
    _raise_if([f"{p}: not a file of scope {SCOPE} (no work file)" for p in sorted(wanted - set(found))])
    docs = {}
    for path, doc in found.items():
        doc = copy.deepcopy(doc)
        for entry in doc["entries"]:
            entry["status"], entry["es"] = RESET_STATUS, ""
        docs[path] = doc
    return docs


def find_entry(item: Item, doc: dict) -> tuple[int | None, str | None]:
    """Index of the entry an item names (by entry name or exact English), or why there is not exactly one."""
    entries = doc["entries"]
    if item.entry is not None:
        hits, what = [k for k, e in enumerate(entries) if e.get("name") == item.entry], f"entry {item.entry}"
    else:
        hits = [k for k, e in enumerate(entries) if e.get("en") == item.match_en]
        what = f"English text {item.match_en!r}"
    if len(hits) != 1:
        return None, f"{item.label}: {what} matches {len(hits)} entries of {item.file} (exactly 1 needed)"
    return hits[0], None


def resolve_targets(items: Sequence[Item], docs: Mapping[str, dict]) -> list[Target]:
    """The entry of every item; ProbeError for 0 or several matches, an entry used twice, or a computed item
    outside a hard profile."""
    targets, problems, used = [], [], {}
    for item in items:
        doc = docs[item.file]
        index, problem = find_entry(item, doc)
        if problem:
            problems.append(problem)
            continue
        entry = doc["entries"][index]
        target = Target(item, item.file, index, entry["name"], entry["en"], profiles.PROFILES[doc["profile"]])
        if (item.file, index) in used:
            problems.append(f"{item.label}: {target.id} is already used by {used[item.file, index]}")
        used[item.file, index] = item.label
        if item.computed and not target.profile.hard:
            problems.append(f"{item.label}: a computed line needs a dialogue profile, not {target.profile.name}")
        targets.append(target)
    _raise_if(problems)
    return targets


# ---------------------------------------------------------------------------------------- computed lines

@dataclass(frozen=True)
class LineModel:
    """How a dialogue line is measured, with both candidate spaces (em, x-scale included)."""
    slot: int
    size: float
    box_px: float
    wide_font: str          # serves the space in sfes.metrics (the first font of the chain that has one)
    wide_em: float
    narrow_font: str        # draws the letters
    narrow_em: float

    def space_px(self, wide: bool) -> float:
        return (self.wide_em if wide else self.narrow_em) * self.size

    def widths(self, text: str, metrics: Metrics, resolver: InsertResolver) -> tuple[float, float]:
        """(width with the narrow space, width with the wide space) of one line, in px."""
        wide = metrics.line_px(text, self.slot, self.size, resolver)
        return wide - text.count(" ") * (self.space_px(True) - self.space_px(False)), wide


def font_space_em(raw: bytes) -> float:
    """Advance of U+0020 in em (without the fslt x-scale) of an .oft.1 font."""
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    font = TTFont(io.BytesIO(oft.decode(raw)), lazy=True)
    glyph = font.getBestCmap().get(0x20)
    if glyph is None:
        raise ProbeError(["the font that draws the letters has no space"])
    return font["hmtx"][glyph][0] / font["head"].unitsPerEm


def line_model(profile: Profile, metrics: Metrics, ps: PakSet) -> LineModel:
    """The slot's space as sfes.metrics measures it, and the space of the font that draws the letters (read
    from the font in the official paks)."""
    slot = profile.slot
    wide_font, letters = metrics.font_of(" ", slot), metrics.font_of("a", slot)
    chain = {f.name: f for f in metrics.chain(slot)}
    if wide_font is None or letters not in chain:
        raise ProbeError([f"slot {slot} has no space or no letter font"])
    if wide_font == letters:
        raise ProbeError([f"slot {slot}: the letters' font also serves the space; there is nothing to calibrate"])
    narrow_em = font_space_em(ps.read(chain[letters].path)) * chain[letters].x_scale
    return LineModel(slot, profile.size, profile.max_px, wide_font, metrics.advance_em(" ", slot), letters,
                     narrow_em)


def pick_words(widths: Sequence[float], targets: Mapping[int, float], step: float = WORD_STEP_PX) -> tuple[int, ...]:
    """Indices, in order, of the subsequence of words whose total width is closest to targets[word count].
    One candidate is kept per `step` of total width and word count; ties go to fewer, then earlier words."""
    top = max(targets)
    ceiling = max(targets.values()) + max(widths)
    layers: list[dict[int, tuple[float, tuple[int, ...]]]] = [{0: (0.0, ())}] + [{} for _ in range(top)]
    for i, width in enumerate(widths):
        for count in range(min(i, top - 1), -1, -1):
            for total, chosen in list(layers[count].values()):
                if total + width <= ceiling:
                    layers[count + 1].setdefault(round((total + width) / step), (total + width, chosen + (i,)))
    candidates = [(abs(total - targets[c]), c, chosen) for c in sorted(targets) for total, chosen in layers[c].values()]
    if not candidates:
        raise ProbeError([f"no line of {', '.join(map(str, sorted(targets)))} words can be built from the word list"])
    return min(candidates)[2]


@dataclass(frozen=True)
class Computed:
    """A generated line, how it was measured, and its widths in px."""
    kind: str
    text: str
    model: LineModel
    narrow_px: float
    wide_px: float
    target_px: tuple[float, ...]

    @property
    def spaces(self) -> int:
        return self.text.count(" ")


def _word_widths(words: Sequence[str], model: LineModel, metrics: Metrics, resolver: InsertResolver) -> list[float]:
    return [metrics.line_px(w, model.slot, model.size, resolver) for w in words]


def _line(kind: str, label: str, words: Sequence[str], chosen: Iterable[int], model: LineModel, metrics: Metrics,
          resolver: InsertResolver, target: tuple[float, ...]) -> Computed:
    text = " ".join([label, *(words[i] for i in chosen)])
    return Computed(kind, text, model, *model.widths(text, metrics, resolver), target)


def calibration_line(n: int, model: LineModel, metrics: Metrics, resolver: InsertResolver) -> Computed:
    """'#n <words>': about CALIBRATION_PX[0] px with the narrow space and CALIBRATION_PX[1] px with the wide."""
    label = f"#{n}"
    gap = model.space_px(True) - model.space_px(False)
    if gap <= 0:
        raise ProbeError([f"#{n}: the space of {model.wide_font} is not wider than that of {model.narrow_font}"])
    spaces = max(1, round((CALIBRATION_PX[1] - CALIBRATION_PX[0]) / gap))
    middle = sum(CALIBRATION_PX) / 2 - spaces * (model.space_px(True) + model.space_px(False)) / 2
    target = middle - metrics.line_px(label, model.slot, model.size, resolver)
    chosen = pick_words(_word_widths(SPACE_WORDS, model, metrics, resolver), {spaces: target})
    return _line(SPACE_CALIBRATION, label, SPACE_WORDS, chosen, model, metrics, resolver, CALIBRATION_PX)


def overlong_line(n: int, model: LineModel, metrics: Metrics, resolver: InsertResolver) -> Computed:
    """'#n <words>': about OVERLONG_PX px wide with the current metrics, without a break."""
    label = f"#{n}"
    widths = _word_widths(OVERLONG_WORDS, model, metrics, resolver)
    space = model.space_px(True)
    room = OVERLONG_PX - metrics.line_px(label, model.slot, model.size, resolver)
    guess = round(room / (sum(widths) / len(widths) + space))
    counts = range(max(1, guess - OVERLONG_COUNT_RANGE), min(len(widths), guess + OVERLONG_COUNT_RANGE) + 1)
    chosen = pick_words(widths, {c: room - c * space for c in counts})
    return _line(OVERLONG_LINE, label, OVERLONG_WORDS, chosen, model, metrics, resolver, (OVERLONG_PX,))


def computed_problems(c: Computed, label: str) -> list[str]:
    """A calibration line inside the box with the narrow space and outside with the wide one, each by
    CALIBRATION_MARGIN_PX; an overlong line within OVERLONG_TOLERANCE_PX of its target."""
    box = c.model.box_px
    if c.kind == SPACE_CALIBRATION:
        if c.narrow_px <= box - CALIBRATION_MARGIN_PX and c.wide_px >= box + CALIBRATION_MARGIN_PX:
            return []
        return [f"{label}: {c.narrow_px:.1f} / {c.wide_px:.1f} px do not straddle the {box:.0f} px box by "
                f"{CALIBRATION_MARGIN_PX:.0f} px"]
    if abs(c.wide_px - OVERLONG_PX) > OVERLONG_TOLERANCE_PX:
        return [f"{label}: {c.wide_px:.1f} px is not within {OVERLONG_TOLERANCE_PX:.0f} px of {OVERLONG_PX:.0f} px"]
    return []


def compute_texts(targets: Sequence[Target], metrics: Metrics, resolver: InsertResolver,
                  ps: PakSet) -> dict[int, Computed]:
    """The computed line of every computed item, by spec position (item.k), measured with the official fonts."""
    out, problems = {}, []
    for t in targets:
        if t.item.computed is None:
            continue
        make = calibration_line if t.item.computed == SPACE_CALIBRATION else overlong_line
        out[t.item.k] = make(t.item.n, line_model(t.profile, metrics, ps), metrics, resolver)
        problems += computed_problems(out[t.item.k], t.item.label)
    _raise_if(problems)
    return out


def apply_items(docs: Mapping[str, dict], targets: Sequence[Target], computed: Mapping[int, Computed]) -> None:
    """Set every item's text, status done and wrap mode in the in-memory documents."""
    for t in targets:
        text = computed[t.item.k].text if t.item.k in computed else t.item.es
        docs[t.path]["entries"][t.index].update(es=text, status=PROBE_STATUS, wrap=t.item.wrap)


# ------------------------------------------------------------------------------------------- variants

@dataclass(frozen=True)
class Final:
    """What one variant writes for one item, measured as the build measures it. For an item the safety gate
    refused (applied False) it is the text the build would have written, so its overflow is checked too."""
    text: str
    lines: tuple[str, ...]
    widths: tuple[float, ...]
    unit: str
    max_width: float
    max_lines: int
    overflow: frozenset[str]
    notes: tuple[str, ...]
    applied: bool


@dataclass(frozen=True)
class VariantBuild:
    """One variant: its font files, every output file ({pak path: bytes}) and the final text of every item."""
    name: str
    fonts: dict[str, bytes]
    files: dict[str, bytes]
    finals: tuple[Final, ...]


def _overflow(res: wrap.WrapResult) -> frozenset[str]:
    return frozenset(k for k, on in (("lines", res.overflow_lines), ("width", res.overflow_width)) if on)


def _intended(t: Target, es: str, metrics: Metrics, resolver: InsertResolver) -> wrap.WrapResult:
    """The final text the build computes for an item (used for items the safety gate refused)."""
    try:
        return wrap.final_text(es, t.en, profile=t.profile, path=t.path, metrics=metrics, resolver=resolver,
                               wrap_mode=t.item.wrap)
    except wrap.CodesChangedError:           # the gate reports it (E_WRAP); measure the text as written
        limits, _ = wrap.limits_for(t.profile, t.path, t.en, metrics, resolver)
        return wrap.measure(es, limits, metrics, resolver)


def _final(t: Target, es: str, msg: msg23.Msg | None, refused: bool, notes: Sequence[str], metrics: Metrics,
           resolver: InsertResolver) -> Final:
    if refused:
        res = _intended(t, es, metrics, resolver)
        own = tuple(res.notes)
    else:
        text = t.en if msg is None else msg.get(msg.entries[t.index], config.LANG_EN)
        limits, _ = wrap.limits_for(t.profile, t.path, t.en, metrics, resolver)
        res = wrap.measure(text, limits, metrics, resolver)
        prefix = f"{t.name}: "
        own = tuple(n[len(prefix):] for n in notes if n.startswith(prefix))
    return Final(res.text, tuple(res.lines), tuple(round(w, 2) for w in res.widths_px), res.unit,
                 round(res.max_px, 2), res.max_lines, _overflow(res), own, not refused)


def report_problems(report: build.ApplyReport, targets: Sequence[Target]) -> tuple[list[str], set[tuple[str, int]]]:
    """Files and entries the build refused (with the safety gate's reasons), and the refused (path, index)."""
    by_entry = {(t.path, t.index): t.where for t in targets}
    out = [f"{f.path}: file refused: {f.refused_file}" for f in report.files if f.refused_file]
    out += [f"{by_entry.get((f.path, r.i), r.id)}: refused by the safety gate: {'; '.join(r.reasons)}"
            for f in report.files for r in f.refused]
    refused_files = {f.path for f in report.files if f.refused_file}
    refused = {(f.path, r.i) for f in report.files for r in f.refused}
    return out, refused | {(t.path, t.index) for t in targets if t.path in refused_files}


def build_variant(name: str, docs: Mapping[str, dict], targets: Sequence[Target], ps: PakSet,
                  base: Metrics) -> tuple[VariantBuild, list[str]]:
    """Apply the probe documents for one variant: the build and the problems of its apply report."""
    variant = VARIANT_FONTS[name]
    fonts = fontpatch.outputs(variant, ps) if variant else {}
    metrics = Metrics.load(overrides=fonts, paks=ps) if fonts else base
    resolver = InsertResolver(metrics, scope=SCOPE, paks=ps, english_only=True)
    msgs, report = build.apply_docs(list(docs.values()), ps=ps, metrics=metrics, resolver=resolver,
                                    min_status=PROBE_STATUS)
    problems, refused = report_problems(report, targets)
    parsed = {p: msg23.parse(data) for p, data in msgs.items()}
    notes = {f.path: f.notes for f in report.files}
    finals = tuple(_final(t, docs[t.path]["entries"][t.index]["es"], parsed.get(t.path),
                          (t.path, t.index) in refused, notes.get(t.path, []), metrics, resolver) for t in targets)
    return VariantBuild(name, fonts, {**msgs, **fonts}, finals), problems


def item_problems(targets: Sequence[Target], finals: Sequence[Final]) -> list[str]:
    """Unchanged texts, a DATETIME format changed without format_change, and dialogue overflow other than
    DELIBERATE_OVERFLOW (deliberate overflow that does not happen included)."""
    out = []
    for t, f in zip(targets, finals):
        if f.applied and f.text == t.en:
            out.append(f"{t.where}: the text is unchanged")
        if not t.item.format_change and textrules.format_changed(t.en, f.text):
            out.append(f"{t.where}: changes a DATETIME format without format_change")
        expected = DELIBERATE_OVERFLOW.get(t.item.n, frozenset())
        if t.profile.hard and f.overflow != expected:
            out.append(f"{t.where}: overflow {sorted(f.overflow) or 'none'}, expected {sorted(expected) or 'none'} "
                       f"({len(f.lines)} lines, widest {max(f.widths, default=0):.0f} of {f.max_width:.0f} {f.unit})")
    return out


def msg_changes(where: str, old: msg23.Msg, new: msg23.Msg, allowed: set[int]) -> list[str]:
    """Differences between two versions of a msg file other than the English slot of the `allowed` entries."""
    def shape(m: msg23.Msg) -> tuple:
        return m.languages, m.attr_names, [(e.name, e.guid, e.entry_id) for e in m.entries]

    if shape(old) != shape(new):
        return [f"{where}: the file structure changed"]
    en = old.slot(config.LANG_EN)
    out = []
    for k, (a, b) in enumerate(zip(old.entries, new.entries)):
        slots = [s for s, (x, y) in enumerate(zip(a.texts, b.texts)) if x != y and (s != en or k not in allowed)]
        out += [f"{where}: entry {a.name}: slot {s} changed" for s in slots]
        if a.attributes != b.attributes:
            out.append(f"{where}: entry {a.name}: attributes changed")
    return out


def change_problems(vb: VariantBuild, targets: Sequence[Target], ps: PakSet) -> list[str]:
    """Every item file is changed, and only in the English slot of its item entries."""
    out = [f"{p}: not changed" for p in sorted({t.path for t in targets} - set(vb.files))]
    for path in sorted(set(vb.files) - set(vb.fonts)):
        allowed = {t.index for t in targets if t.path == path}
        out += msg_changes(path, msg23.parse(ps.read(path)), msg23.parse(vb.files[path]), allowed)
    return out


def consistency_problems(builds: Sequence[VariantBuild], targets: Sequence[Target]) -> list[str]:
    """An item's final text must be the same in every variant."""
    return [f"{t.where}: the final text differs between the variants"
            for k, t in enumerate(targets) if len({vb.finals[k].text for vb in builds}) > 1]


def grouped(problems: Iterable[tuple[str, str]]) -> list[str]:
    """(variant, problem) pairs as 'A, B: problem', identical problems of several variants on one line."""
    by_text: dict[str, list[str]] = {}
    for variant, text in problems:
        by_text.setdefault(text, []).append(variant)
    return [f"{', '.join(variants)}: {text}" for text, variants in by_text.items()]


# ----------------------------------------------------------------------------------------------- plan

@dataclass(frozen=True)
class Plan:
    """Everything the probe writes, built in memory and checked."""
    pak: str
    official: tuple[str, ...]
    targets: tuple[Target, ...]
    computed: dict[int, Computed]
    builds: tuple[VariantBuild, ...]
    spec_sha256: str


def plan_probe(spec: dict, variants: Iterable[str] = VARIANT_FONTS, *, strings_dir: Path = config.STRINGS,
               ps: PakSet | None = None) -> Plan:
    """Build every variant in memory and run every check (a ProbeError lists all problems)."""
    items = parse_spec(spec)
    names = select_variants(variants)
    ps = ps or PakSet()
    docs = source_docs([i.file for i in items], Path(strings_dir))
    targets = resolve_targets(items, docs)
    base = Metrics.load(paks=ps)
    computed = compute_texts(targets, base, InsertResolver(base, scope=SCOPE, paks=ps, english_only=True), ps)
    apply_items(docs, targets, computed)
    builds, problems = [], []
    for name in names:
        vb, refused = build_variant(name, docs, targets, ps, base)
        builds.append(vb)
        problems += [(name, p) for p in refused + item_problems(targets, vb.finals) + change_problems(vb, targets, ps)]
    _raise_if(grouped(problems) + consistency_problems(builds, targets))
    official = (config.BASE_PAK, *(config.PATCH_FMT.format(i) for i in sorted(ps.official_patch_indices())))
    return Plan(build.pak_name(ps), official, tuple(targets), computed, tuple(builds),
                hashlib.sha256(extract.dump_json(spec)).hexdigest())


# ---------------------------------------------------------------------------------------- batch files

BAT_HEADER = r"""@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem {name} - SFES in-game probe, a temporary test build of the Spanish fan translation.
rem Usage: {usage}
rem {about}
rem Generated by sfes.probe. The game path is only used in quotes: no parenthesized if blocks, no CALL with it.
set "PAK={pak}"
set "PROBE=%~dp0"
set "DEFAULT_GAME={game_dir}"
set "FC=%SystemRoot%\System32\fc.exe"
set "VARIANT="
set "FOUND="
set "MISSING="
set "PARENT="
"""

# Sets GAME (absolute, no trailing backslash) and TARGET, or jumps to no_game, in_probe or missing_official.
# The walk climbs from GAME to its drive root and stops at the probe folder; %%~dp of '<dir>\.' is the parent.
GAME_CHECK_BAT = r"""if not defined GAME set "GAME=%DEFAULT_GAME%"
for %%G in ("%GAME%") do set "GAME=%%~fG"
if "%GAME:~-1%"=="\" set "GAME=%GAME:~0,-1%"
set "TARGET=%GAME%\%PAK%"
if not exist "%GAME%\" goto no_game
set "UP=%GAME%\"
:walk_up
if /i "%UP%"=="%PROBE%" goto in_probe
for %%U in ("%UP%.") do set "PARENT=%%~dpU"
if /i "%PARENT%"=="%UP%" goto game_checked
set "UP=%PARENT%"
goto walk_up
:game_checked
{official_lines}
if defined MISSING goto missing_official
"""

GAME_ERRORS_BAT = r"""
:no_game
echo Game folder not found:
echo   "%GAME%"
echo {game_hint}
echo   {example}
exit /b 1

:in_probe
echo This is the probe folder or a folder inside it, not the game folder. Nothing was changed:
echo   "%GAME%"
echo {game_hint}
echo   {example}
exit /b 1

:missing_official
echo This does not look like the game folder; missing:%MISSING%
echo   "%GAME%"
echo Nothing was changed. {game_hint}
echo   {example}
exit /b 1
"""

FIND_PROBE_BAT = r"""
:find_probe
rem Sets FOUND to the variant whose probe pak is byte-identical to the target file, if any.
set "FOUND="
set "TARGET_SIZE="
for %%F in ("%TARGET%") do set "TARGET_SIZE=%%~zF"
{match_calls}
exit /b 0

:match_variant
if defined FOUND exit /b 0
set "CANDIDATE=%PROBE%%~1\%PAK%"
if not exist "%CANDIDATE%" exit /b 0
set "CANDIDATE_SIZE="
for %%F in ("%CANDIDATE%") do set "CANDIDATE_SIZE=%%~zF"
if not "%CANDIDATE_SIZE%"=="%TARGET_SIZE%" exit /b 0
"%FC%" /b "%TARGET%" "%CANDIDATE%" >nul 2>&1
if errorlevel 1 exit /b 0
set "FOUND=%~1"
exit /b 0
"""

INSTALL_BAT = BAT_HEADER + r"""if not "%~3"=="" goto usage
{variant_lines}
if not defined VARIANT goto usage
set "SOURCE=%PROBE%%VARIANT%\%PAK%"
if not exist "%SOURCE%" goto no_source
set "GAME=%~2"
""" + GAME_CHECK_BAT + r"""if exist "%TARGET%" goto target_exists
copy /b "%SOURCE%" "%TARGET%" >nul
if errorlevel 1 goto copy_failed
"%FC%" /b "%SOURCE%" "%TARGET%" >nul 2>&1
if errorlevel 1 goto verify_failed
echo Probe variant %VARIANT% is installed:
echo   "%TARGET%"
echo Next steps:
echo   1. Start the game from Steam.
echo   2. Follow PROBE_CHECKLIST.md in this folder.
echo   3. When you are done, run uninstall_probe.bat from this folder.
exit /b 0

:target_exists
call :find_probe
if defined FOUND goto already_installed
echo A file named %PAK% already exists in the game folder, and it is not
echo one of the probe paks. It may be an official game update. Nothing was changed:
echo   "%TARGET%"
exit /b 1

:already_installed
echo Probe variant %FOUND% is already installed:
echo   "%TARGET%"
echo Run uninstall_probe.bat first, then install the variant you want.
exit /b 1

:copy_failed
if exist "%TARGET%" del /f /q "%TARGET%" >nul 2>&1
echo.
echo The copy failed and nothing was installed.
echo If the message above says that access is denied, run this file as administrator:
echo open the Start menu, type cmd, right-click Command Prompt, choose Run as administrator,
echo then type these two lines:
echo   cd /d "%PROBE%"
echo   install_probe.bat %VARIANT% "%GAME%"
exit /b 1

:verify_failed
del /f /q "%TARGET%" >nul 2>&1
echo The copied file differs from the probe file, so it was removed again. Nothing is installed.
if exist "%TARGET%" echo Could not remove the broken copy; delete it by hand: "%TARGET%"
exit /b 1

:no_source
echo The probe file of variant %VARIANT% is missing:
echo   "%SOURCE%"
echo Run this file from the probe folder, next to the folders {folders}.
exit /b 1
""" + GAME_ERRORS_BAT + r"""
:usage
echo Usage: install_probe.bat VARIANT [GAME_FOLDER]
echo   VARIANT is {choices}. Start with {first}, see PROBE_CHECKLIST.md.
echo   GAME_FOLDER, in quotes, only if the game is not in:
echo   "%DEFAULT_GAME%"
echo Example: install_probe.bat {first}
exit /b 1
""" + FIND_PROBE_BAT

UNINSTALL_BAT = BAT_HEADER + r"""if not "%~2"=="" goto usage
{letter_lines}
set "GAME=%~1"
""" + GAME_CHECK_BAT + r"""if not exist "%TARGET%" goto not_installed
call :find_probe
if not defined FOUND goto unknown_file
del /f /q "%TARGET%" >nul
if exist "%TARGET%" goto delete_failed
echo Probe variant %FOUND% was removed:
echo   "%TARGET%"
echo The game runs without the probe again.
exit /b 0

:not_installed
echo No probe is installed: there is no %PAK% in
echo   "%GAME%"
echo Nothing to do.
exit /b 0

:unknown_file
echo %PAK% in the game folder is not one of the probe paks of this folder,
echo so it was NOT deleted. It may be an official game update or another build:
echo   "%TARGET%"
exit /b 1

:delete_failed
echo Could not delete the probe file:
echo   "%TARGET%"
echo Close the game and try again. If the message above says that access is denied, run this
echo file as administrator: open the Start menu, type cmd, right-click Command Prompt, choose
echo Run as administrator, then type these two lines:
echo   cd /d "%PROBE%"
echo   uninstall_probe.bat "%GAME%"
exit /b 1
""" + GAME_ERRORS_BAT + r"""
:usage
echo Usage: uninstall_probe.bat [GAME_FOLDER]
echo   It takes no variant letter: it removes whichever probe variant is installed.
echo   GAME_FOLDER, in quotes, only if the game is not in:
echo   "%DEFAULT_GAME%"
exit /b 1
""" + FIND_PROBE_BAT


def _words(names: Sequence[str], last: str) -> str:
    """'A, B, C or D' (last = 'or')."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {last} {names[-1]}"


def _bat(template: str, **values: str) -> bytes:
    """A .bat file: the template filled in, ASCII, CRLF line ends."""
    text = template.format(**values)
    if "\r" in text or not text.isascii():
        raise ValueError("a batch file must be ASCII with plain line ends in its template and values")
    return text.replace("\n", "\r\n").encode("ascii")


def _bat_values(name: str, usage: str, about: str, pak: str, variants: Sequence[str], official: Sequence[str],
                game_dir: Path, example: str) -> dict:
    game = str(game_dir)
    if not game.isascii() or BAT_FORBIDDEN & set(game):
        raise ValueError(f"game folder {game!r}: a batch file default must be ASCII without % or quotes")
    return {"name": name, "usage": usage, "about": about, "pak": pak, "game_dir": game, "example": example,
            "game_hint": GAME_HINT, "match_calls": "\n".join(f"call :match_variant {v}" for v in variants),
            "official_lines": "\n".join(f'if not exist "%GAME%\\{p}" set "MISSING=%MISSING% {p}"' for p in official)}


def install_bat(pak: str, variants: Sequence[str], official: Sequence[str], game_dir: Path) -> bytes:
    """install_probe.bat for the given variant folders and the official paks that must exist."""
    values = _bat_values(INSTALL_NAME, "install_probe.bat VARIANT [GAME_FOLDER]",
                         f"Copies one file, {pak}, into the game folder and changes nothing else.", pak,
                         variants, official, game_dir, f'install_probe.bat %VARIANT% "{EXAMPLE_GAME}"')
    variant_lines = "\n".join(f'if /i "%~1"=="{v}" set "VARIANT={v}"' for v in variants)
    return _bat(INSTALL_BAT, **values, variant_lines=variant_lines, choices=_words(variants, "or"),
                folders=_words(variants, "and"), first=_first(variants))


def uninstall_bat(pak: str, variants: Sequence[str], official: Sequence[str], game_dir: Path) -> bytes:
    """uninstall_probe.bat: in a folder holding the official paks, deletes the target only when it is
    byte-identical to the pak of one of `variants`."""
    values = _bat_values(UNINSTALL_NAME, "uninstall_probe.bat [GAME_FOLDER]",
                         f"Deletes {pak} from the game folder only if it is byte-identical to a probe pak of this "
                         "folder.", pak, variants, official, game_dir, f'uninstall_probe.bat "{EXAMPLE_GAME}"')
    letter_lines = "\n".join(f'if /i "%~1"=="{v}" goto usage' for v in variants)
    return _bat(UNINSTALL_BAT, **values, letter_lines=letter_lines)


def _first(variants: Sequence[str]) -> str:
    return FIRST_VARIANT if FIRST_VARIANT in variants else variants[0]


# ------------------------------------------------------------------------------------------ checklist

# Walk-through in the order the screens appear: (heading, how to get there and what you see, labels shown).
CHECKLIST_STEPS = (
    ("Collection title screen", "", (1,)),
    ("Main menu", "Its labels read {Games} (Games), {Gallery} (Gallery), {Settings} (Settings), {Network} "
     "(Network), {Other} (Other).", (2,)),
    ("{Games} (Games)", "Highlight Mega Man Star Force (the Pegasus version) and read its description.", (3,)),
    ("{Settings} (Settings)", "Look at the list and at the {Language} (Language) screen.", (4, 5, 6)),
    ("{Gallery} (Gallery) > battle cards of SF1", "Open the battle card gallery (its header reads \"{cards}\") "
     "and choose SF1: Standard cards first, then Mega cards.", (33, 30, 31, 32)),
    ("SF1 title screen", "Start Mega Man Star Force (Pegasus) from {Games} (Games).", (7,)),
    ("Opening narration", "Choose {NEW_GAME} (NEW GAME; the other option reads {CONTINUE}). The narration lines "
     "after #11 are unnumbered Spanish samples wrapped by our tools: report anything odd.", (10, 11)),
    ("First conversation", "After #20 come unnumbered Spanish lines, then #21 to #27, one box each. Press the "
     "button slowly and take a screenshot of every numbered box.", (20, 21, 22, 23, 24, 25, 26, 27)),
    ("Save time stamp", "Save the game in an EMPTY slot, never over one of your own saves (with your saves backed "
     "up, the autosave works too). Then open the list of saves: in the in-game menu the Load entry reads "
     "\"{load}\". If the in-game menu has no \"{load}\", look on the SF1 page under {Games} (Games) or behind "
     "{Continue} (Continue) in the main menu. Highlight the save you just made.", (8,)),
)
# Placeholders of CHECKLIST_STEPS and checklist() -> the spec item (its match_en or entry) whose final text they
# quote; '<placeholder>_en' quotes the item's English.
CHECKLIST_LABELS = {
    "Games": "Games", "Gallery": "Gallery", "Settings": "Settings", "Network": "Network", "Other": "Other",
    "Continue": "Continue", "Language": "Language", "NEW_GAME": "NEW GAME", "CONTINUE": "CONTINUE",
    "title": "Press Any Button", "spanish": "Common_MsgGUI00030000_Lang_3", "load": "Common_MsgGUI00040101_2",
    "cards": "Common_MsgGUI00020201_Header",
}
REPORT_HINTS = {
    8: "Report the time stamp exactly as shown.",
    23: "Report which: line 4 shown, cut off, drawn outside the box, or the box scrolls or waits for a button.",
    24: "Report \"#24 one line\" or \"#24 two lines\" (for two lines, also the first word of line 2).",
    25: "Report \"#25 wrapped\" (also the first word of line 2: it shows where the game breaks the line), "
        "\"#25 shrunk\" or \"#25 cut\" (also the last word you can see).",
}
OTHER_VARIANTS = {
    "C": "accented letters (á é í ó ú ñ) or ¡ ¿ sit lower than plain letters (#11, #22, #27)",
    "D": "the bottoms of ¡ and ¿ on line 3 of #22 are cut off",
    "A": "the game does not start, shows no text, or shows its usual English texts (in A, ¡ is expected to "
         "look like i)",
}


def _labels(plan: Plan) -> dict[str, str]:
    finals = {t.item.match_en or t.item.entry: (t, f) for t, f in zip(plan.targets, plan.builds[0].finals)}
    _raise_if([f"the checklist quotes the spec item {v!r}, which the spec does not have"
               for v in CHECKLIST_LABELS.values() if v not in finals])
    labels = {k: finals[v][1].text for k, v in CHECKLIST_LABELS.items()}
    return labels | {f"{k}_en": finals[v][0].en.rstrip("\r\n") for k, v in CHECKLIST_LABELS.items()}


def _explain(t: Target, plan: Plan) -> str:
    """For a computed line: what it reads, its widths, and how to tell the outcomes apart."""
    c = plan.computed.get(t.item.k)
    if c is None:
        return ""
    box = c.model.box_px
    reads = f" It reads \"{c.text}\": real words chosen only for their width, not a sentence."
    if c.kind == SPACE_CALIBRATION:
        return (f"{reads} With the narrow space it is {c.narrow_px:.0f} px wide, with the wide space "
                f"{c.wide_px:.0f} px ({c.spaces} spaces); the box is {box:.0f} px. ONE line means the game uses the "
                "narrow space of the letter font; TWO lines mean it uses the wider space of the digit font.")
    return (f"{reads} It is about {c.wide_px:.0f} px on one line, wider than the {box:.0f} px box on purpose, and "
            "has no line break. Wrapped: the end continues on a second line (is the last word visible?). Shrunk: "
            "everything stays on one line in smaller letters. Cut: the line stops at the box edge.")


def _walkthrough(plan: Plan, labels: Mapping[str, str]) -> list[str]:
    by_n = {t.item.n: t for t in plan.targets if t.item.n}
    shown = [n for _, _, numbers in CHECKLIST_STEPS for n in numbers]
    _raise_if([f"the checklist does not cover #{n}" for n in sorted(by_n) if n not in shown]
              + [f"the checklist names #{n}, which the spec does not have" for n in shown if n not in by_n])
    out = []
    for k, (heading, text, numbers) in enumerate(CHECKLIST_STEPS, start=3):
        out.append(f"{k}. **{heading.format(**labels)}**" + (f" {text.format(**labels)}" if text else ""))
        out += [f"    - **#{n}**: {by_n[n].item.check}{_explain(by_n[n], plan)} "
                f"{REPORT_HINTS.get(n, 'Report OK, or what is wrong.')}" for n in numbers]
    return out


def _other_variants(names: Sequence[str], first: str) -> list[str]:
    rows = [f"- If, in {first}, {why}: try **{v}**." for v, why in OTHER_VARIANTS.items()
            if v in names and v != first]
    if not rows:
        return []
    return ["## Other variants (only if needed)", "", *rows, "",
            "To switch: close the game, run `uninstall_probe.bat` (no letter), then `install_probe.bat` with the "
            "other letter, and repeat the steps for the items in question. Say which variant each answer is for.",
            ""]


def checklist(plan: Plan, out_dir: Path) -> bytes:
    """PROBE_CHECKLIST.md: save backup, install, walk-through in screen order, other variants, uninstall, safety
    notes."""
    names = [vb.name for vb in plan.builds]
    first = _first(names)
    labels = _labels(plan)
    folder = str(Path(out_dir).resolve())
    lines = [
        "# SFES in-game probe: checklist", "",
        "The probe is a temporary test build, not the translation. It replaces some English texts of the "
        "collection and of Mega Man Star Force 1 with Spanish test lines, so we can see how the game shows them. "
        "Most test lines start with a number such as \"#3\": report each one by its number.", "",
        f"It adds one file to the game folder, `{plan.pak}`, and changes nothing else there. It does not touch "
        "your save files itself, but the test plays a NEW GAME of SF1 and the game saves by itself (autosave), "
        "so back up your saves first.", "",
        "Variants (the same texts in all of them): "
        + "; ".join(f"**{v}** {VARIANT_SUMMARY[v]}" for v in names) + f". Start with **{first}**.", "",
        "## Before you start: back up your saves", "",
        f"1. Close the game. In File Explorer open `{STEAM_USERDATA}` (the `userdata` folder of your Steam "
        "folder), then the folder named with your Steam account number.",
        f"2. Copy its folder `{STEAM_APP_ID}` (this game; the saves are in `remote\\win64_save`) to a safe place, "
        "for example the Desktop.",
        f"3. To restore the saves later: close the game and Steam, and copy the `{STEAM_APP_ID}` folder back. If "
        f"you cannot find `{STEAM_APP_ID}`, stop here and ask before you test.", "",
        "## Install", "",
        "1. Close the game.",
        f"2. Open this folder (`{folder}`) in File Explorer, click the address bar, type `cmd` and press Enter. "
        "A command prompt opens in this folder.",
        f"3. Type `install_probe.bat {first}` and press Enter. Double-clicking the file is not enough: it needs "
        "the variant letter.",
        f"    - Game in another Steam library? Add the game folder in quotes: `install_probe.bat {first} "
        f"\"{EXAMPLE_GAME}\"`, and give the same folder to `uninstall_probe.bat` later.",
        "    - It says that access is denied? Open the Start menu, type `cmd`, right-click Command Prompt, choose "
        f"Run as administrator, type `cd /d \"{folder}\"` and repeat step 3.",
        f"4. The output must say \"Probe variant {first} is installed\" and list the next steps.", "",
        "## In the game", "",
        "For every numbered item take a screenshot (F12 in Steam; Steam > View > Screenshots shows them) and "
        "report `#N OK` or `#N problem: what you see`, with the screenshot.", "",
        "1. Start the game from Steam. If it does not start or shows no text, see \"Other variants\". If the "
        f"collection title screen shows its usual English \"{labels['title_en']}\" instead of "
        f"\"{labels['title']}\", the game did not load the probe: stop, report that with a screenshot, and try "
        "**A** (see \"Other variants\").",
        "2. Text language: open Settings > Language > Text. The option that was \"English\" now reads "
        f"\"{labels['spanish']}\" (#5). Select it and keep it selected. With it, Settings reads "
        f"\"{labels['Settings']}\" and Language reads \"{labels['Language']}\". Go back to the title screen "
        "(restart the game if needed) and walk through these screens in this order:",
        *_walkthrough(plan, labels), "",
        *_other_variants(names, first),
        "## Uninstall (at the end)", "",
        "1. Close the game.",
        "2. In the command prompt in this folder, type `uninstall_probe.bat` without a variant letter (add the "
        "game folder in quotes if you gave one when installing).",
        "3. It must say \"Probe variant ... was removed\". It deletes only the probe file, and only if it is "
        "exactly one of the probe paks; anything else is left alone.", "",
        "## Safety notes", "",
        "1. The probe adds one file to the game folder and changes nothing else there; `uninstall_probe.bat` "
        "removes it.",
        "2. The probe never writes save files, but the game does while you test: an autosave of the NEW GAME "
        "may replace your own SF1 autosave. Back up your saves first (see \"Before you start\") and save only "
        "in an empty slot.",
        "3. If Steam updates the game while the probe is installed, run `uninstall_probe.bat` and ask for a new "
        "probe.", ""]
    return "\n".join(lines).encode("utf-8")


# ------------------------------------------------------------------------------------------- manifest

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _variant_entry(vb: VariantBuild, pak: str, pak_bytes: bytes) -> dict:
    files = [{"path": p, "kind": build.kind_of(p), "sha256": _sha(d), "size": len(d)}
             for p, d in sorted(vb.files.items())]
    return {"fonts": VARIANT_FONTS[vb.name], "summary": VARIANT_SUMMARY[vb.name], "pak": f"{vb.name}/{pak}",
            "sha256": _sha(pak_bytes), "size": len(pak_bytes), "pak_entries": len(files) + 1,
            "marker": {"path": config.MARKER_PATH, "text": PROBE_MARKER.format(variant=vb.name)}, "files": files}


def _item_entry(k: int, t: Target, plan: Plan) -> dict:
    finals = {vb.name: vb.finals[k] for vb in plan.builds}
    first = plan.builds[0].finals[k]
    return {"n": t.item.n, "file": t.path, "entry": t.name, "id": t.id,
            "found_by": "entry" if t.item.entry else "match_en", "wrap": t.item.wrap, "computed": t.item.computed,
            "format_change": t.item.format_change, "profile": t.profile.name, "check": t.item.check,
            "final_text": first.text, "lines": list(first.lines), "unit": first.unit, "max_width": first.max_width,
            "max_lines": first.max_lines, "deliberate_overflow": sorted(DELIBERATE_OVERFLOW.get(t.item.n, ())),
            "widths": {v: list(f.widths) for v, f in finals.items()},
            "overflow": {v: sorted(f.overflow) for v, f in finals.items()}, "notes": list(first.notes)}


def _space(model: LineModel, wide: bool) -> dict:
    em = model.wide_em if wide else model.narrow_em
    return {"font": model.wide_font if wide else model.narrow_font, "em": round(em, 5),
            "px": round(model.space_px(wide), 3)}


def _computed_entry(t: Target, c: Computed) -> dict:
    out = {"n": t.item.n, "id": t.id, "text": c.text, "spaces": c.spaces, "slot": c.model.slot,
           "size_px": c.model.size, "box_px": c.model.box_px,
           "space": {"metrics_uses": c.model.wide_font, "narrow": _space(c.model, False),
                     "wide": _space(c.model, True)},
           "width_px": {"narrow_space": round(c.narrow_px, 2), "wide_space": round(c.wide_px, 2)}}
    if c.kind == SPACE_CALIBRATION:
        out["target_px"] = {"narrow_space": c.target_px[0], "wide_space": c.target_px[1]}
    else:
        out["target_px"] = {"wide_space": c.target_px[0]}
    return out


def manifest(plan: Plan, paks: Mapping[str, bytes], game_dir: Path) -> dict:
    """probe_manifest.json: pak, variants, computed lines, items (spec order)."""
    by_k = {t.item.k: t for t in plan.targets}
    return {"schema": MANIFEST_SCHEMA,
            "about": "In-game probe: a temporary test build, not the translation. Install one variant with "
                     "install_probe.bat and follow PROBE_CHECKLIST.md.",
            "pak_name": plan.pak, "official_paks": list(plan.official), "default_game_dir": str(game_dir),
            "spec_sha256": plan.spec_sha256, "status": PROBE_STATUS, "resolver": "english_only",
            "variants": {vb.name: _variant_entry(vb, plan.pak, paks[vb.name]) for vb in plan.builds},
            "computed": {c.kind: _computed_entry(by_k[k], c) for k, c in sorted(plan.computed.items())},
            "items": [_item_entry(k, t, plan) for k, t in enumerate(plan.targets)]}


# --------------------------------------------------------------------------------------------- writing

def check_out_dir(out_dir: Path) -> Path:
    """The resolved output folder: one of OUT_ROOTS or a folder inside one (write_probe clears it, so no other
    folder of dist is accepted); it also passes _check_writable."""
    target = Path(out_dir).resolve()
    fontpatch._check_writable(target)
    roots = [root.resolve() for root in OUT_ROOTS]
    if not any(target == root or root in target.parents for root in roots):
        raise ValueError(f"{out_dir}: the probe output folder must be {OUT_DIR} or lie inside "
                         f"{' or '.join(map(str, OUT_ROOTS))}")
    return target


def output_paths(out_dir: Path, pak: str, variants: Sequence[str]) -> list[Path]:
    """Every path write_probe writes: the folder, one pak per variant, manifest, .bat files, checklist."""
    return [out_dir, *(out_dir / v / pak for v in variants), out_dir / MANIFEST_NAME, out_dir / INSTALL_NAME,
            out_dir / UNINSTALL_NAME, out_dir / CHECKLIST_NAME]


def write_pak(path: Path, vb: VariantBuild, ps: PakSet) -> list[str]:
    """Write one variant's pak (as build.write_outputs does) and re-read it; the differences found."""
    templates = build.pak_templates(vb.files, ps)
    fingerprint = build.newest_official_pak(ps).fingerprint
    writer = PakWriter(fingerprint=fingerprint, checksum_mode=build.CHECKSUM_MODE)
    for p, data in sorted(vb.files.items()):
        writer.add(p, data, template=templates[p][0])
    writer.add_marker(PROBE_MARKER.format(variant=vb.name))
    writer.write(path)
    return [f"{vb.name}: pak read-back: {d}" for d in build.pak_readback(path, vb.files, fingerprint)["different"]]


@dataclass(frozen=True)
class ProbeResult:
    """Where the probe was written: folder, pak name, the pak of each variant, and the manifest."""
    out_dir: Path
    pak: str
    paks: dict[str, Path]
    manifest: dict


def write_probe(plan: Plan, out_dir: Path, *, ps: PakSet, game_dir: Path = config.GAME_DIR) -> ProbeResult:
    """Clear out_dir and write the variant paks, the manifest, the .bat files and the checklist."""
    out_dir = check_out_dir(out_dir)
    names = [vb.name for vb in plan.builds]
    paths = output_paths(out_dir, plan.pak, names)
    for path in paths:
        fontpatch._check_writable(path)
    texts = {INSTALL_NAME: install_bat(plan.pak, names, plan.official, game_dir),
             UNINSTALL_NAME: uninstall_bat(plan.pak, names, plan.official, game_dir),
             CHECKLIST_NAME: checklist(plan, out_dir)}
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    pak_paths = dict(zip(names, paths[1:]))
    problems = [p for vb in plan.builds for p in write_pak(pak_paths[vb.name], vb, ps)]
    if problems:
        shutil.rmtree(out_dir)
        raise ProbeError(problems)
    body = manifest(plan, {v: p.read_bytes() for v, p in pak_paths.items()}, game_dir)
    (out_dir / MANIFEST_NAME).write_bytes(extract.dump_json(body))
    for name, data in texts.items():
        (out_dir / name).write_bytes(data)
    return ProbeResult(out_dir, plan.pak, pak_paths, body)


def build_probe(out_dir: Path = OUT_DIR, variants: Iterable[str] = VARIANT_FONTS, *, spec: dict | None = None,
                strings_dir: Path = config.STRINGS, game_dir: Path = config.GAME_DIR) -> ProbeResult:
    """Check the output folder, plan every variant (all checks) and write the probe."""
    check_out_dir(out_dir)
    ps = PakSet()
    plan = plan_probe(load_spec() if spec is None else spec, variants, strings_dir=strings_dir, ps=ps)
    return write_probe(plan, out_dir, ps=ps, game_dir=game_dir)


# ------------------------------------------------------------------------------------------------ CLI

def _print_result(res: ProbeResult) -> None:
    print(f"probe: {res.pak} for {', '.join(res.paks)} in {res.out_dir}")
    for name, info in res.manifest["variants"].items():
        print(f"  {name}: {info['pak']}  {info['size']} bytes  sha256 {info['sha256']}  "
              f"({len(info['files'])} files + marker)")
    for kind, c in res.manifest["computed"].items():
        print(f"  #{c['n']} {kind}: {c['spaces']} spaces, {c['width_px']['narrow_space']:.1f} px with the narrow "
              f"space, {c['width_px']['wide_space']:.1f} px with the wide space: {c['text']!r}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="probe", description="Build the in-game probe pack (a temporary test build).")
    ap.add_argument("--out", type=Path, default=OUT_DIR,
                    help=f"output folder (cleared first): {OUT_DIR} (default) or a folder inside it")
    ap.add_argument("--variants", default=",".join(VARIANT_FONTS), help="comma-separated, e.g. A,B,C,D")
    try:
        args = ap.parse_args(argv)
        variants = select_variants(args.variants.split(","))
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    except ValueError as exc:
        print(f"probe: {exc}")
        return 2
    try:
        _print_result(build_probe(args.out, variants))
    except ProbeError as exc:
        print(exc)
        return 1
    except (ValueError, OSError) as exc:
        print(f"probe: {exc}")
        return 2
    except Exception:          # an internal error: never exit 1, which means a spec or check problem
        traceback.print_exc()
        print("probe: internal error (traceback above)")
        return 2
    return 0
