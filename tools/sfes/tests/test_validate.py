r"""Tests for sfes.validate (the translation validator).

Run: C:\StarForceES\.venv\Scripts\python.exe -I -X utf8 C:\StarForceES\tools\run.py tests.test_validate
  - crafted entries in copies of real work files: every issue code is triggered by a crafted entry or file
    and not by a clean neighbour (a neighbour has no issue at all); control characters (C0, DEL, C1),
    hidden characters, broken codes, DATETIME separators, an en edited in the work file, fields of the wrong
    type, an unknown profile given as a list; inverted marks across boxes and through a keep box;
  - text checks (unit): each case gives exactly the expected codes, and every reason for which the build's
    safety gate (build.gate_problems) refuses a case is an error of the same code here; W_SAME for English
    copies, W_PADDING and W_GLOSSARY multiplicity, E_CODES details with the codes as written;
  - glossary: a temporary glossary (variants, forbidden forms, word boundaries, case), bad glossary files;
  - --min-status filtering; --fonts rrsystem loads the font override (metrics cache in the test folder);
    CLI exit codes (errors 1, clean 0, unusable input or report folder 2, without any write attempt for a
    refused folder); a --stem run writes the _selected reports; reports escape invisible characters and are
    byte-identical across runs, in this process and in two separate processes;
  - the real phase1 work files (read only): 0 errors, no W_STALE, I_TODO present;
  - identity translation (every phase1 work file copied with es = en, status done, wrap manual): no issue
    besides W_SAME (one per entry with letters) except the inherited ones it lists; then the same copy with
    each entry's own wrap mode; both timed.
Temporary files live in work/reports/_test/ and are removed afterwards; the real work files are only read.
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

from sfes import config, extract, fontpatch, profiles, validate, wrap
from sfes import scope as scopes
from sfes.inserts import InsertResolver
from sfes.metrics import Metrics, cache_key
from sfes.pak import PakSet
from sfes.tests.test_metrics import Checks
from sfes.validate import ERROR, WARN, Report

TEST_DIR = validate.REPORTS_DIR / "_test"
RUN_PY = Path(__file__).resolve().parents[2] / "run.py"
TIME_BUDGET_S = 60.0

DM0401 = "rr1_dm0401_eng"            # SF1 dialogue (hard profile, slot 0, auto)
DM0000 = "rr1_dm0000_eng"            # SF1 dialogue: glossary, min status, wrap mode
DM0003 = "rr1_dm0003_eng"            # SF1 dialogue: en edited, wrong field types, keep box at index 7
CHIPINFO = "rr1_chipinfo_eng"        # observed_text (slot 0)
CHIPNAME = "rr1_chipname_eng"        # observed (slot 2): <REPLACE  RR1_EX>
GUI0102 = "rr1_msggui01020200"       # observed (slot 2, manual), one empty English entry
GUI0100 = "rr1_msggui01000000"       # one entry: stale source hash
GUI0104 = "rr1_msggui01040000"       # one entry: clean file
ST0403 = "rr1_st0403_d_eng"          # one entry: changed GUID
C0101 = "common_msggui00040101"      # DATETIME "hh:mm A MM/DD/YYYY"
C0102 = "common_msggui00040102"      # DATETIME "MM/DD/YYYY"
C0502 = "common_msggui00050002"      # DATETIME "hh:mm A MM/DD/YYYY"
C0503 = "common_msggui00050003"      # DATETIME "hh:mm A MM/DD/YYYY"
MISSING = "rr1_msggui01030009"       # selected, but no work file is copied
COPIED = (DM0401, DM0000, DM0003, CHIPINFO, CHIPNAME, GUI0102, GUI0100, GUI0104, ST0403, C0101, C0102, C0502,
          C0503)
SELECTED = COPIED + (MISSING,)

DLG_PATH = "natives/stm/text/rr1/message/eng/rr1_dm0003_eng.msg.23"
BATTLE_PATH = "natives/stm/text/rr1/message/eng/rr1_battle_eng.msg.23"
STAMP_PATH = "natives/stm/text/common/common_msggui00050003.msg.23"

# Edits per stem and entry index. "es" without "status" means status done; "add_flags" extends the flags.
EDITS: dict[str, dict[int, dict]] = {
    DM0401: {
        1: {"es": "din don*_CMD_wait(60)"},                          # W_PADDING: lead padding lost
        2: {"status": "finished", "es": "Después de clases..."},      # E_STATUS: unknown status
        3: {"es": "¡Eso es todo por hoy!"},
        4: {"es": ""},                                               # E_STATUS: empty es
        5: {"es": "No quiero enterarme de que alguien anduvo jugando.\x7f"},     # E_BREAK: DEL
        6: {"es": "Ah, y Bud, todavía no\x85 entregas tu"},                       # E_BREAK: C1 (NEL)
        7: {"es": "tarea de mate\u00admáticas. Si todavía no la tienes"},        # E_CHARSET: soft hyphen
        8: {"es": "para mañana, {será una semana de castigo."},                  # E_CODES: broken code
        9: {"es": "Ay, no puede\tser..."},                           # E_BREAK: tab
        10: {"es": "¡Me va a explotar la cabeza si tengo que pensar tanto!"},
        11: {"es": "???"},                                           # no letters: no W_INVERTED
        12: {"es": "¿No dijiste hace 3 días..."},                    # 12-14: one sentence over 3 boxes
        13: {"es": "que ya la habías terminado,"},
        14: {"es": "pero que se te olvidó traerla?"},
        15: {"es": "   "},                                           # E_EMPTY: prints nothing
        16: {"es": "¡Presiii\u2028dentaaa!!"},                        # E_CHARSET: U+2028 (escaped in reports)
        17: {"es": "¡No me mire\u0301s a mí!"},                       # E_CHARSET: combining accent (NFD)
        19: {"es": "Si te ayudara,\r\nno te serviría\r\nde nada\r\nal final.", "wrap": "manual"},   # E_LINES
        23: {"es": "Pero yo siempre la hago solo y me esfuerzo al máximo."},
        26: {"es": "Lo de Zack... ¿Coincidencia...?!"},
        27: {"es": "Ah!!"},                                          # W_INVERTED
        28: {"es": "Ups..."},
        29: {"es": "Hasta le advertí que no lo copiara todo, de verdad que se lo dije...\r\n",
             "wrap": "manual"},                                      # E_WIDTH
        30: {"es": "¡Jajajajaja!!"},
        31: {"es": "Je\u00a0je..."},                                 # E_CHARSET: no-break space
        32: {"es": "¿Sabes? La escuela no está nada mal."},
        33: {"es": "El Sr. Shepar y los demás\r\nson muy  amables.\r\n", "wrap": "manual"},   # W_STYLE spaces
        34: {"es": "Nunca imaginé que la escuela fuera tan divertida…"},                       # W_STYLE …
        35: {"es": "Un último anuncio.\r\nGeo,"},                     # E_CODES: insert_option(0) missing
        39: {"es": "Sí, señor..."},
        40: {"es": "¡Bueno, se acabó la clase!"},
        42: {"es": "Bye!"},                                          # W_SAME
        44: {"es": "Al menos deberías hacer tu tarea de matemáticas."},
        46: {"es": "¡ser Hermana tuya?"},                            # W_INVERTED: ¡ does not open ?
        50: {"es": "Pero no le acabas de decir"},
        51: {"es": "que la hiciera solo? Además, hoy dan mi caricatura favorita..."},   # W_INVERTED
        52: {"es": "¿De verdad crees que Bud"},
        54: {"es": "puede terminarla?"},                             # W_INVERTED: box 53 not translated
        56: {"es": "Entiendo. Vamos, Bud. Andando."},
        60: {"es": "Ya volvió a ser la alegre de siempre..."},
        61: {"es": "Sí...", "add_flags": ["source_changed"]},        # W_SOURCE
        62: {"es": "Ser su Hermano debe ser agotador...", "add_flags": ["relinked"]},   # W_SOURCE
        63: {"es": "Bueno, debería ir al estudio."},
    },
    DM0000: {
        0: {"es": "Es el año 220X..."},
        2: {"es": "La nueva agencia de exploración espacial, Naza,"},           # W_GLOSSARY: NAZA (case)
        5: {"es": "lanzó una estación del espacio llamada \"Paz\"..."},          # W_GLOSSARY: missing + forbidden
        8: {"status": "draft", "es": "Sin embargo, la estación espacial,_CMD_wait(5) la esperanza del hombre,"},
        9: {"es": "sufrió un misterioso accidente y", "wrap": "free"},           # E_STRUCTURE: wrap mode
        10: {"es": "desapareció sin dejar rastro."},
        12: {"es": "pero la estación espacial no apareció."},
        16: {"es": "La NAZA lo tomó muy mal y detuvo la búsqueda,"},
        18: {"es": "Pasaron tres inviernos desde aquel día..."},                 # 'years' is not 'year'
    },
    DM0003: {
        # E_STRUCTURE (en edited in the work file; the paks have no wait code) and E_CODES (extra wait)
        1: {"en": "This is a sample, isn't\r\nit? _CMD_wait(5)\r\n", "es": "Eres tú, ¿verdad? _CMD_wait(5)"},
        2: {"es": "El niño que nunca viene a la escuela."},
        3: {"en": None, "wrap": "free", "es": "Soy Luna Platz, de la Primaria Echo,"},   # one E_STRUCTURE
        4: {"es": 5},                                                                    # E_STRUCTURE
        5: {"es": "El grandote detrás de mí es Bud Bison,", "flags": "relinked"},       # E_STRUCTURE
        6: {"es": "¿Y el bajito, Zack Temple,"},               # 6, keep box 7 ('......'), 8: one sentence
        8: {"es": "no piensas decir nada?"},
    },
    CHIPINFO: {0: {"es": "Megabuster  \r\nMegabuster  \r\nMegabuster  ", "wrap": "manual"}},
    CHIPNAME: {152: {"es": "Fuego Tauro<REPLACE RR1_EX>"},                     # E_REPLACE (and E_CODES)
               153: {"es": "Fuego Tauro<REPLACE  RR1_SP>"}},
    GUI0102: {2: {"es": "MEGAMAN EN MODO DE TRANSFORMACIÓN TOTAL"},            # W_WIDTH
              3: {"es": "P\r\nS"},                                             # W_LINES
              7: {"es": "ARMA"}, 8: {"es": "HABILIDAD"}, 10: {"es": "NV"},
              20: {"es": "NIVEL"}},                                            # E_EMPTY: English empty
    GUI0104: {0: {"es": "INFO"}},                                              # narrower than 'HELP'
    ST0403: {0: {"es": "Mmm..."}},                         # unknown profile: text checks skipped, no crash
    C0101: {10: {"es": '<DATETIME {0} "DD/MM/YYYY hh:mm">'}},                  # E_FORMAT: hh without A
    C0102: {40: {"es": '<DATETIME {0} "DD/MM/YYYY">'}},                        # I_FORMAT
    C0502: {10: {"es": '<DATETIME {0} "hh:mm A MM/DD/YYYY">'}},
    C0503: {12: {"es": '<DATETIME {0} "DD,MM,YYYY hh:mm A">'}},                # E_FORMAT: ',' separator
}

GLOSSARY = {"schema": 1, "terms": [
    {"en": "space station", "es": "estación espacial", "variants": ["estaciones espaciales"],
     "forbidden": ["estación del espacio"], "case_sensitive": False, "note": "SF1 opening"},
    {"en": "NAZA", "es": "NAZA", "case_sensitive": True},
    {"en": "year", "es": "año", "variants": ["años"]},
]}

Ref = tuple[str, int | None]          # (stem, entry index) or (stem, None) for a file issue
# (code, crafted, clean neighbour)
EXPECT: list[tuple[str, Ref, Ref]] = [
    ("E_STRUCTURE", (ST0403, None), (GUI0104, None)),
    ("E_STRUCTURE", (MISSING, None), (GUI0104, None)),
    ("E_STRUCTURE", (GUI0100, None), (GUI0104, None)),
    ("E_STRUCTURE", (DM0000, 9), (DM0000, 10)),
    ("E_STRUCTURE", (DM0003, 1), (DM0003, 2)),
    ("E_STRUCTURE", (DM0003, 3), (DM0003, 2)),
    ("E_STRUCTURE", (DM0003, 4), (DM0003, 2)),
    ("E_STRUCTURE", (DM0003, 5), (DM0003, 2)),
    ("E_STATUS", (DM0401, 2), (DM0401, 3)),
    ("E_STATUS", (DM0401, 4), (DM0401, 40)),
    ("E_CODES", (DM0401, 35), (DM0401, 56)),
    ("E_CODES", (DM0401, 8), (DM0401, 56)),
    ("E_CODES", (DM0003, 1), (DM0003, 2)),
    ("E_FORMAT", (C0101, 10), (C0502, 10)),
    ("E_FORMAT", (C0503, 12), (C0502, 10)),
    ("E_REPLACE", (CHIPNAME, 152), (CHIPNAME, 153)),
    ("E_BREAK", (DM0401, 9), (DM0401, 10)),
    ("E_BREAK", (DM0401, 5), (DM0401, 10)),
    ("E_BREAK", (DM0401, 6), (DM0401, 10)),
    ("E_LINES", (DM0401, 19), (DM0401, 23)),
    ("E_WIDTH", (DM0401, 29), (DM0401, 28)),
    ("E_CHARSET", (DM0401, 31), (DM0401, 32)),
    ("E_CHARSET", (DM0401, 7), (DM0401, 32)),
    ("E_CHARSET", (DM0401, 16), (DM0401, 32)),
    ("E_CHARSET", (DM0401, 17), (DM0401, 32)),
    ("E_EMPTY", (DM0401, 15), (DM0401, 39)),
    ("E_EMPTY", (GUI0102, 20), (GUI0102, 10)),
    ("W_WIDTH", (GUI0102, 2), (GUI0102, 7)),
    ("W_LINES", (GUI0102, 3), (GUI0102, 8)),
    ("W_STYLE", (DM0401, 34), (DM0401, 44)),
    ("W_STYLE", (DM0401, 33), (CHIPINFO, 0)),
    ("W_PADDING", (DM0401, 1), (CHIPINFO, 0)),
    ("W_SAME", (DM0401, 42), (DM0401, 63)),
    ("W_SOURCE", (DM0401, 61), (DM0401, 60)),
    ("W_SOURCE", (DM0401, 62), (DM0401, 60)),
    ("W_INVERTED", (DM0401, 27), (DM0401, 30)),
    ("W_INVERTED", (DM0401, 46), (DM0401, 26)),
    ("W_INVERTED", (DM0401, 51), (DM0401, 14)),
    ("W_INVERTED", (DM0401, 54), (DM0401, 11)),
    ("W_INVERTED", (DM0401, 54), (DM0003, 8)),
    ("W_GLOSSARY", (DM0000, 5), (DM0000, 12)),
    ("W_GLOSSARY", (DM0000, 2), (DM0000, 16)),
    ("W_GLOSSARY", (DM0000, 2), (DM0000, 0)),
    ("W_GLOSSARY", (DM0000, 5), (DM0000, 18)),
    ("W_STALE", (GUI0100, None), (GUI0104, None)),
    ("W_STALE", (DM0003, None), (GUI0104, None)),
    ("I_FORMAT", (C0102, 40), (C0502, 10)),
    ("I_TODO", (GUI0102, None), (GUI0104, None)),
]

# Text checks (unit): (name, es, en, pak path, wrap mode, expected codes). The build gate must refuse no
# case for a reason that is not an error of the same code here.
TEXT_CASES: list[tuple[str, str, str, str, str, set[str]]] = [
    ("clean", "Hola, ¿qué tal?", "Hello, how are you?\r\n", DLG_PATH, "auto", set()),
    ("DEL", "Hola, mundo.\x7f", "Hello, world.\r\n", DLG_PATH, "auto", {"E_BREAK"}),
    ("C1 (NEL)", "Hola,\x85 mundo.", "Hello, world.\r\n", DLG_PATH, "auto", {"E_BREAK"}),
    ("C1 (CSI)", "Hola\x9b mundo.", "Hello, world.\r\n", DLG_PATH, "auto", {"E_BREAK"}),
    ("lone CR inside a code", "Mira <ICON\rINGAME_KEY_A>", "Look <ICON\rINGAME_KEY_A>", DLG_PATH, "manual",
     {"E_BREAK"}),
    ("soft hyphen", "Hola\u00admundo", "Hello world\r\n", DLG_PATH, "auto", {"E_CHARSET"}),
    ("combining accent", "la estacio\u0301n", "the station\r\n", DLG_PATH, "auto", {"E_CHARSET"}),
    ("private use", "Hola\ue000", "Hello\r\n", DLG_PATH, "auto", {"E_CHARSET"}),
    ("zero-width space", "Hola\u200bmundo", "Hello world\r\n", DLG_PATH, "auto", {"E_CHARSET"}),
    ("lone surrogate", "Hola\ud800", "Hello\r\n", DLG_PATH, "auto", {"E_CHARSET"}),
    ("brace outside a code", "Hola {", "Hello\r\n", DLG_PATH, "auto", {"E_CODES"}),
    ("lowercase command", "Hola _cmd_wait(5)", "Hello\r\n", DLG_PATH, "auto", {"E_CODES"}),
    ("English empty, Spanish blank", " ", "", DLG_PATH, "manual", {"E_EMPTY"}),
    ("DATETIME separator", '<DATETIME {0} "DD,MM,YYYY hh:mm A">', '<DATETIME {0} "hh:mm A MM/DD/YYYY">',
     STAMP_PATH, "manual", {"E_FORMAT"}),
    ("DATETIME format changed", '<DATETIME {0} "DD/MM/YYYY HH:mm">', '<DATETIME {0} "hh:mm A MM/DD/YYYY">',
     STAMP_PATH, "manual", {"I_FORMAT"}),
    ("copy without the trailing break", "...Is this a sample?", "...Is this a sample?\r\n", DLG_PATH, "auto",
     {"W_SAME"}),
    ("copy re-broken", "This is a sample, isn't it?", "This is a sample, isn't\r\nit?\r\n", DLG_PATH, "auto",
     {"W_SAME"}),
    ("acute accent as apostrophe", "Mega Man´s", "Mega Man's\r\n", DLG_PATH, "auto", {"W_STYLE"}),
    ("padding lost on both sides", "PS MÁX", "   HP MAX   ", BATTLE_PATH, "manual", {"W_PADDING"}),
]


# ------------------------------------------------------------------------------------------- helpers

def real_docs() -> dict[str, tuple[Path, dict]]:
    """stem -> (path, document) of the real phase1 work files (read only)."""
    return {p.stem: (p, doc) for p, doc in extract.scope_docs("phase1", config.STRINGS)}


def write_doc(strings: Path, doc: dict) -> None:
    path = extract.work_path(strings, doc["scope"], doc["group"], scopes.stem_of(doc["path"]))
    fontpatch._check_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(extract.dump_json(doc))


def apply_edits(doc: dict, edits: dict[int, dict]) -> dict:
    out = json.loads(json.dumps(doc))
    for k, fields in edits.items():
        entry = out["entries"][k]
        fields = dict(fields)
        entry["flags"] = sorted(entry["flags"] + fields.pop("add_flags", []))
        entry.update({"status": "done", **fields} if "es" in fields and "status" not in fields else fields)
    return out


def crafted_tree(strings: Path, docs: dict[str, tuple[Path, dict]]) -> dict[str, dict]:
    """Write the crafted copies; returns stem -> crafted document."""
    out = {}
    for stem in COPIED:
        doc = apply_edits(docs[stem][1], EDITS.get(stem, {}))
        if stem == ST0403:
            doc["entries"][0]["guid"] = "00000000-0000-4000-8000-000000000000"
            doc["profile"] = ["dialogue_sf1"]                  # not a text: unknown profile, no crash
        if stem == GUI0100:
            doc["source_sha256"] = "0" * 64
            doc["path"] = doc["path"].replace("/message/", "/message/eng/")
        write_doc(strings, doc)
        out[stem] = doc
    return out


def issues_of(report: Report, ref: Ref, docs: dict[str, dict]) -> list[validate.Issue]:
    stem, k = ref
    if k is None:
        return [i for i in report.issues if i.file == stem and not i.id]
    entry_id = docs[stem]["entries"][k]["id"]
    return [i for i in report.issues if i.id == entry_id]


def codes_of(issues: Iterable[validate.Issue]) -> list[str]:
    return sorted({i.code for i in issues})


@contextlib.contextmanager
def no_writes() -> Iterator[list[Path]]:
    """Make extract.write_if_changed (the only writer of write_reports) record and refuse every call."""
    calls: list[Path] = []
    real = extract.write_if_changed

    def refuse(path: Path, data: bytes) -> bool:
        calls.append(Path(path))
        raise AssertionError(f"write attempted: {path}")

    extract.write_if_changed = refuse
    try:
        yield calls
    finally:
        extract.write_if_changed = real


# ---------------------------------------------------------------------------------------------- tests

def test_crafted(c: Checks, report: Report, docs: dict[str, dict]) -> None:
    print("crafted entries: each code triggered, clean neighbours untouched")
    c.check("every issue code has a crafted case", {code for code, _, _ in EXPECT} == set(validate.CHECKS),
            str(sorted(set(validate.CHECKS) - {code for code, _, _ in EXPECT})))
    for code, crafted, clean in EXPECT:
        got, near = issues_of(report, crafted, docs), issues_of(report, clean, docs)
        c.check(f"{code}: {crafted[0]} {crafted[1]} -> triggered; neighbour {clean[0]} {clean[1]} clean",
                code in codes_of(got) and not near,
                f"crafted {codes_of(got)}, neighbour {codes_of(near)}; "
                + "; ".join(i.detail for i in got if i.code == code)[:160])
    levels = {i.code: i.level for i in report.issues}
    c.check("levels follow the table", all(validate.CHECKS[k][0] == v for k, v in levels.items()))
    side_effects = {(CHIPNAME, "E_CODES"), (DM0000, "E_CODES")} | {(s, "I_TODO") for s in COPIED if s != GUI0104}
    unexpected = sorted({(i.file, i.code) for i in report.issues}
                        - {(ref[0], code) for code, ref, _ in EXPECT} - side_effects)
    c.check("no issue outside the crafted files and codes (plus E_CODES of the bad <REPLACE and the draft, "
            "I_TODO)", not unexpected, str(unexpected))
    per_entry = Counter((i.id, i.code) for i in report.issues if i.id and i.code != "W_GLOSSARY")
    c.check("one issue per entry and code", max(per_entry.values()) == 1,
            str([k for k, n in per_entry.items() if n > 1]))
    by_id = {i.id: i for i in report.issues if i.code == "E_WIDTH"}
    width = next(iter(by_id.values()), None)
    c.check("E_WIDTH carries the final lines and widths", width is not None and width.layout is not None
            and width.layout.unit == "px" and width.layout.max_width == 1026.0 and width.line == 1,
            "" if width is None else f"{width.detail} {width.layout}")
    details = [i.detail for i in report.issues if i.code == "E_STRUCTURE"]
    kinds = ("entries differ", "unknown profile", "path ", "no work file", "unknown wrap mode", "en differs",
             "en is not a text", "es is not a text", "flags is not a list")
    c.check("E_STRUCTURE kinds: entries, profile, path, missing file, wrap mode, en edited, field types",
            all(any(k in d for d in details) for k in kinds), " | ".join(details)[:400])
    test_crafted_details(c, report, docs)


def test_crafted_details(c: Checks, report: Report, docs: dict[str, dict]) -> None:
    """Details of the new crafted cases: the paks' English is used, merged findings, control names."""
    def detail(stem: str, k: int, code: str) -> str:
        return " | ".join(i.detail for i in issues_of(report, (stem, k), docs) if i.code == code)

    c.check("en edited: checked against the paks' English (the wait code is extra)",
            "extra: _CMD_wait(5)" in detail(DM0003, 1, "E_CODES"), detail(DM0003, 1, "E_CODES"))
    both = detail(DM0003, 3, "E_STRUCTURE")
    c.check("en null and unknown wrap: one E_STRUCTURE issue naming both",
            "en is not a text (null)" in both and "unknown wrap mode" in both, both)
    c.check("E_BREAK names DEL and C1", "U+007F" in detail(DM0401, 5, "E_BREAK")
            and "U+0085" in detail(DM0401, 6, "E_BREAK"))
    c.check("E_CHARSET names hidden characters", "SOFT HYPHEN" in detail(DM0401, 7, "E_CHARSET")
            and "COMBINING ACUTE ACCENT" in detail(DM0401, 17, "E_CHARSET"))
    stale = " | ".join(i.detail for i in issues_of(report, (DM0003, None), docs) if i.code == "W_STALE")
    c.check("W_STALE counts the edited en (sha untouched)", "2 entries have an en" in stale, stale)
    glossary = [i for i in issues_of(report, (DM0000, 5), docs) if i.code == "W_GLOSSARY"]
    c.check("W_GLOSSARY: missing rendering and forbidden form of one term in one issue",
            len(glossary) == 1 and "should be" in glossary[0].detail and "forbidden" in glossary[0].detail,
            str([i.detail for i in glossary]))
    inv = [i.detail for i in report.issues if i.code == "W_INVERTED"]
    print("    W_INVERTED details:", inv)


def test_text_unit(c: Checks, m: Metrics, resolver: InsertResolver) -> None:
    print("text checks (unit) and the build's safety gate")
    from sfes import build                           # imported here: only this test compares with the build
    for name, es, en, path, mode, want in TEXT_CASES:
        profile = profiles.profile_for(path)
        slot = profile.slot if profile.slot is not None else profiles.observed_slot(path)
        final, found = validate.check_text(es, en, profile=profile, path=path, slot=slot, metrics=m,
                                           resolver=resolver, wrap_mode=mode)
        got = {code for code, _, _ in found}
        gate = {g.split(":", 1)[0] for g in build.gate_problems(en, es, final.text, metrics=m, slot=slot)}
        errors = {code for code in got if validate.CHECKS[code][0] == ERROR}
        c.check(f"{name}: {sorted(want)}; build gate {sorted(gate)} covered", got == want and gate <= errors,
                f"got {[(k, d[:70]) for k, _, d in found]}")
    final, found = validate.check_text('<DATETIME {1} "hh:mm A MM/DD/YYYY">', '<DATETIME {0} "hh:mm A MM/DD/YYYY">',
                                       profile=profiles.profile_for(STAMP_PATH), path=STAMP_PATH, slot=2,
                                       metrics=m, resolver=resolver, wrap_mode="manual")
    want = 'missing: <DATETIME {0} "hh:mm A MM/DD/YYYY">; extra: <DATETIME {1} "hh:mm A MM/DD/YYYY">'
    c.check("E_CODES names the codes as written (not the comparison key)", found == [("E_CODES", None, want)],
            str(found))
    _, found = validate.check_text("PS MÁX", "   HP MAX   ", profile=profiles.profile_for(BATTLE_PATH),
                                   path=BATTLE_PATH, slot=2, metrics=m, resolver=resolver, wrap_mode="manual")
    c.check("W_PADDING: one issue for both sides", [d for k, _, d in found if k == "W_PADDING"]
            == ["the English starts with spaces and ends with spaces; the Spanish does not"], str(found))
    c.check("report text escapes invisible characters",
            validate._printable("a\u2028b\x85c\u200bd\u00a0e\u3000f\r\ng")
            == "a<U+2028>b<U+0085>c<U+200B>d<U+00A0>e<U+3000>f / g")


def test_inverted_unit(c: Checks) -> None:
    print("inverted marks (unit)")

    def misses(*boxes: str, gap: bool = False, keep: Iterable[int] = ()) -> list[int]:
        marks = validate.InvertedMarks()
        return [len(marks.check(2 * k if gap else k, wrap.visible_lines(box), report=k not in keep))
                for k, box in enumerate(boxes)]

    cases = [(("¡¿Qué?!",), [0]), (("¿¡Qué!?",), [0]), (("¿Qué?!",), [0]), (("¡Qué!?",), [0]),
             (("¡Hola!! ¿Qué tal?? Bien...",), [0]), (("¡Qué?",), [1]), (("Qué?!",), [1]),
             (("Hola, _CMD_insert_option(0)!",), [1]), (("¡Mira <ICON INGAME_KEY_A?>!",), [0]),
             (("Mira <ICON INGAME_KEY_A?>",), [0]), (("...?", "???", "!!"), [0, 0, 0]),
             (("¿Viste", "a ese", "tipo?"), [0, 0, 0]), (("Hola. ¿Y tú?", "Bien!"), [0, 1]),
             (("¿Viste a ¡ese tipo?", "Sí!"), [0, 1]), (("¿Sabes\r\nlo que\r\npasó?",), [0]),
             (("¿Viste al Sr. Shepar?",), [0]), (("¿No dijiste...", "que vendrías?"), [0, 0]),
             (("¿Sabes que vino.", "Luego se fue.", "Y después volvió?"), [0, 0, 1]),
             (("¿Sabes que ¡ganamos!?", "Bueno, ya veremos?"), [0, 1])]
    for boxes, want in cases:
        got = misses(*boxes)
        c.check(f"{' | '.join(boxes)!r} -> {want}", got == want, str(got))
    c.check("an opener does not carry over a box that is not checked", misses("¿Viste a", "ese tipo?", gap=True)
            == [0, 1])
    c.check("a keep box ('......') does not break the sentence", misses("¿Viste a", "......", "ese tipo?",
                                                                        keep=[1]) == [0, 0, 0])
    c.check("a keep box closes with its marks but is never reported", misses("¿Qué", "...?!", "Bien?",
                                                                             keep=[1]) == [0, 0, 1])


def test_glossary_unit(c: Checks) -> None:
    print("glossary (unit)")
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    path = TEST_DIR / "glossary_unit.json"
    path.write_text(json.dumps(GLOSSARY, ensure_ascii=False, indent=1), encoding="utf-8")
    g = validate.load_glossary(path)
    c.check("glossary loads", g is not None and len(g.terms) == 3)
    c.check("variant accepted", g.check("two Space Stations? no: Space Station", "las estaciones espaciales") == [])
    c.check("plural English is another word", g.check("the space stations", "otra cosa") == [])
    c.check("missing rendering reported", len(g.check("the space station", "la base")) == 1)
    c.check("forbidden form reported", any("forbidden" in d for d in g.check("x", "la estación del espacio")))
    both = g.check("the space station", "la estación del espacio")
    c.check("missing and forbidden of one term: one problem", len(both) == 1 and "should be" in both[0]
            and "forbidden" in both[0], str(both))
    c.check("case-sensitive term", g.check("NAZA", "la Naza") != [] and g.check("naza", "x") == []
            and g.check("NAZA", "la NAZA") == [])
    c.check("line breaks and spacing ignored", g.check("space\r\nstation", "estación  espacial") == [])
    c.check("missing glossary: inactive", validate.load_glossary(TEST_DIR / "none.json") is None)
    bad = {"unknown key": {"schema": 1, "terms": [{"en": "a", "es": "b", "context": "x"}]},
           "no es": {"schema": 1, "terms": [{"en": "a"}]},
           "duplicate": {"schema": 1, "terms": [{"en": "Card", "es": "carta"}, {"en": "card", "es": "x"}]},
           "schema": {"schema": 2, "terms": []},
           "variants type": {"schema": 1, "terms": [{"en": "a", "es": "b", "variants": "c"}]}}
    for name, doc in bad.items():
        p = TEST_DIR / "glossary_bad.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        try:
            validate.load_glossary(p)
            ok = False
        except validate.GlossaryError:
            ok = True
        c.check(f"bad glossary refused: {name}", ok)


def test_min_status(c: Checks, strings: Path, glossary: Path, docs: dict[str, dict], ps: PakSet,
                    m: Metrics) -> None:
    print("--min-status")
    draft_id = docs[DM0000]["entries"][8]["id"]
    runs = {s: validate.validate(strings_dir=strings, min_status=s, stems=[DM0000], glossary_path=glossary,
                                 ps=ps, metrics=m) for s in ("draft", "done", "reviewed")}
    c.check("draft: the draft entry is checked (E_CODES)",
            any(i.id == draft_id and i.code == "E_CODES" for i in runs["draft"].issues))
    c.check("done: the draft entry is not checked", not any(i.id == draft_id for i in runs["done"].issues))
    c.check("translated counts follow the minimum",
            runs["draft"].translated == runs["done"].translated + 1 and runs["reviewed"].translated == 0,
            str({s: r.translated for s, r in runs.items()}))
    c.check("reviewed: only file issues and status errors", all(not i.id or i.code in ("E_STATUS",)
                                                                for i in runs["reviewed"].issues))


def test_fonts(c: Checks, strings: Path, ps: PakSet) -> None:
    print("--fonts rrsystem")
    args = validate.parse_args(["--fonts", "rrsystem", "--stem", "a", "b", "--stem", "c"])
    c.check("CLI parses --fonts and repeated --stem", args.fonts == "rrsystem" and args.stems == ["a", "b", "c"])
    t0 = time.perf_counter()
    r = validate.validate(strings_dir=strings, stems=[DM0401], fonts="rrsystem", ps=ps,
                          metrics_cache=TEST_DIR / "metrics_cache.json", glossary_path=TEST_DIR / "none.json")
    expected = cache_key(fontpatch.outputs("rrsystem", ps))
    c.check("font override measured", r.fonts == "rrsystem" and r.metrics_key == expected != "base",
            f"{r.metrics_key} ({time.perf_counter() - t0:.1f}s)")
    c.check("report header names the variant", "Fonts: variant rrsystem" in validate.markdown(r))
    try:
        validate.validate(strings_dir=strings, stems=[DM0401], fonts="rrsystem", metrics=Metrics.load(paks=ps))
        refused = False
    except validate.ValidateError:
        refused = True
    c.check("fonts together with metrics refused", refused)


def test_malformed(c: Checks, docs: dict[str, tuple[Path, dict]], ps: PakSet, m: Metrics) -> None:
    print("malformed work files")
    strings = TEST_DIR / "malformed"
    doc = json.loads(json.dumps(docs[GUI0104][1]))
    doc["entries"].append("junk")
    write_doc(strings, doc)
    try:
        validate.validate(strings_dir=strings, ps=ps, metrics=m, glossary_path=TEST_DIR / "none.json")
        message = ""
    except validate.ValidateError as exc:
        message = str(exc)
    c.check("an entry that is not an object: ValidateError naming the file",
            f"{GUI0104}.json" in message and "not a list of objects" in message, message[:160])
    rc = validate.main(["--strings", str(strings), "--glossary", str(TEST_DIR / "none.json"), "--quiet",
                        "--out", str(TEST_DIR / "out_malformed")])
    c.check("CLI: exit code 2", rc == 2, str(rc))
    shutil.rmtree(strings)


def test_cli_and_reports(c: Checks, strings: Path, glossary: Path, ps: PakSet, m: Metrics) -> None:
    print("CLI and reports")
    out = TEST_DIR / "out"
    base = ["--scope", "phase1", "--strings", str(strings), "--glossary", str(glossary), "--quiet"]
    rc_err = validate.main(base + ["--stem", *SELECTED, "--out", str(out / "cli")])
    rc_ok = validate.main(base + ["--stem", GUI0104, "--out", str(out / "cli_ok")])
    rc_stem = validate.main(base + ["--stem", "no_such_file", "--out", str(out / "cli_bad")])
    bad = TEST_DIR / "glossary_bad.json"
    bad.write_text('{"schema": 1, "terms": [{"en": "a"}]}', encoding="utf-8")
    rc_gloss = validate.main(["--strings", str(strings), "--glossary", str(bad), "--stem", GUI0104, "--quiet",
                              "--out", str(out / "x")])
    c.check("exit codes: errors 1, clean 0, unknown stem 2, bad glossary 2",
            (rc_err, rc_ok, rc_stem, rc_gloss) == (1, 0, 2, 2), str((rc_err, rc_ok, rc_stem, rc_gloss)))
    c.check("a --stem run writes the _selected reports, never the full-scope ones",
            all(p.exists() for p in validate.report_paths("phase1", out / "cli", selected=True))
            and not any(p.exists() for p in validate.report_paths("phase1", out / "cli")))
    test_refused_out(c, base)
    paths = []
    for run in ("run1", "run2"):
        r = validate.validate(strings_dir=strings, stems=SELECTED, glossary_path=glossary, ps=ps, metrics=m)
        paths.append(validate.write_reports(r, "phase1", out / run))
    same = all(a.read_bytes() == b.read_bytes() for a, b in zip(*paths))
    c.check("two runs write byte-identical reports", same, ", ".join(p.name for p in paths[0]))
    test_processes(c, base, paths[0])
    raw = paths[0][0].read_bytes()
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    c.check("CSV: BOM, header, one row per issue, CRLF rows",
            raw.startswith(b"\xef\xbb\xbf") and tuple(rows[0]) == validate.CSV_COLUMNS
            and len(rows) - 1 == len(r.issues) and b'"\r\n"' in raw)
    md = paths[0][1].read_text(encoding="utf-8")
    c.check("MD: summary table, worst files, an example per code with lines and widths",
            "## Issues by code" in md and "## Worst files" in md
            and all(f"### {code} " in md for code in validate.CHECKS) and " px ! " in md and " em ! " in md)
    text = raw.decode("utf-8-sig") + md
    raw_chars = sorted({f"U+{ord(ch):04X}" for ch in text if not ch.isprintable() and ch not in "\r\n"})
    c.check("reports hold no raw invisible characters (U+2028, C1, DEL ... written as <U+XXXX>)",
            not raw_chars and "<U+2028>" in text and "<U+007F>" in text and "<U+0085>" in text, str(raw_chars))


def test_refused_out(c: Checks, base: list[str]) -> None:
    """--out under build/, dist/ or original/: exit code 2 and no write attempted."""
    targets = [config.BUILD / "_validate_probe", config.DIST / "_validate_probe", config.ORIGINAL / "_validate_probe"]
    with no_writes() as calls:
        codes = [validate.main(base + ["--stem", GUI0104, "--out", str(t)]) for t in targets]
    c.check("--out under build/, dist/, original/: exit code 2, no write attempted, nothing created",
            codes == [2, 2, 2] and not calls and not any(t.exists() for t in targets), f"{codes} {calls}")


def test_processes(c: Checks, base: list[str], in_process: tuple[Path, Path]) -> None:
    """The same reports from two separate processes (other hash seeds) as from this one."""
    outs = []
    for run in ("proc1", "proc2"):
        out = TEST_DIR / "out" / run
        cmd = [sys.executable, "-I", "-X", "utf8", str(RUN_PY), "validate", *base, "--stem", *SELECTED,
               "--out", str(out)]
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        outs.append((done.returncode, validate.report_paths("phase1", out, selected=True)))
    same = all(rc == 1 and all(p.read_bytes() == q.read_bytes() for p, q in zip(paths, in_process))
               for rc, paths in outs)
    c.check("two separate processes write the same bytes as this process", same, str([rc for rc, _ in outs]))


def test_real(c: Checks, ps: PakSet, m: Metrics) -> None:
    print("real phase1 work files (read only)")
    r = validate.validate(ps=ps, metrics=m)
    counts = r.counts_by_code
    c.check("0 errors", r.errors == 0, str(dict(counts)))
    c.check("no W_STALE (every work file and entry English matches the paks)", counts["W_STALE"] == 0,
            str(counts["W_STALE"]))
    c.check("I_TODO present", counts["I_TODO"] > 0, f"{counts['I_TODO']} files with todo entries")
    c.check("961 files, 31,216 entries", (r.files_checked, r.entries_checked) == (961, 31216),
            f"{r.files_checked} files, {r.entries_checked} entries, {r.translated} translated, {r.elapsed:.1f}s")


def identity_tree(strings: Path, docs: dict[str, tuple[Path, dict]], manual: bool) -> int:
    """Copy every phase1 work file with es = en and status done (English-empty entries stay 'empty');
    returns the number of entries with letters."""
    with_letters = 0
    for _, doc in docs.values():
        out = json.loads(json.dumps(doc))
        for e in out["entries"]:
            if e["en"]:
                e.update(es=e["en"], status="done", **({"wrap": "manual"} if manual else {}))
                with_letters += extract.has_letters(e["en"])
        write_doc(strings, out)
    return with_letters


def describe(issues: Iterable[validate.Issue]) -> list[str]:
    return [f"{i.code} {i.id or i.file} line {i.line}: {i.detail}" for i in issues]


def rewrapped(report: Report, english: dict[str, str]) -> int:
    """W_SAME entries whose final lines differ from the English lines (re-broken by auto wrap)."""
    return sum(tuple(i.layout.lines) != tuple(wrap.visible_lines(english[i.id]))
               for i in report.issues if i.code == "W_SAME")


def test_identity(c: Checks, docs: dict[str, tuple[Path, dict]], ps: PakSet, m: Metrics) -> None:
    print("identity translation (es = en, status done, wrap manual)")
    english = {e["id"]: e["en"] for _, doc in docs.values() for e in doc["entries"]}
    strings = TEST_DIR / "identity"
    with_letters = identity_tree(strings, docs, manual=True)
    r = validate.validate(strings_dir=strings, ps=ps, metrics=m, glossary_path=TEST_DIR / "none.json")
    counts = r.counts_by_code
    print(f"    {r.translated} entries checked in {r.elapsed:.1f}s; counts {dict(sorted(counts.items()))}")
    c.check("identity: manual wrap keeps every English line", rewrapped(r, english) == 0)
    c.check("identity: full scope within the time budget", r.elapsed < TIME_BUDGET_S, f"{r.elapsed:.1f}s")
    c.check("identity: W_SAME for every entry with letters", counts["W_SAME"] == with_letters,
            f"{counts['W_SAME']} of {with_letters}")
    others = [i for i in r.issues if i.level in (ERROR, WARN) and i.code != "W_SAME"]
    inherited = [i for i in others if i.code in ("E_CHARSET", "E_WIDTH")
                 or (i.code == "W_STYLE" and all("full-width" in part for part in i.detail.split("; ")))]
    for line in describe(inherited):
        print("    inherited from the English:", line)
    c.check("identity: no other error or warning", len(others) == len(inherited),
            "; ".join(describe(i for i in others if i not in inherited))[:400])
    dialogue = [i for i in inherited if i.code in ("E_CHARSET", "E_WIDTH") and i.layout and i.layout.hard]
    c.check("identity: no E_CHARSET / E_WIDTH in dialogue", not dialogue, "; ".join(describe(dialogue)))
    c.check("identity: no I_TODO, no I_FORMAT", counts["I_TODO"] == 0 and counts["I_FORMAT"] == 0)
    print(f"    info: W_INVERTED {counts['W_INVERTED']} (the English never opens with ¿ or ¡)")
    shutil.rmtree(strings)

    print("identity translation with each entry's own wrap mode (auto for dialogue)")
    strings = TEST_DIR / "identity_auto"
    identity_tree(strings, docs, manual=False)
    r = validate.validate(strings_dir=strings, ps=ps, metrics=m, glossary_path=TEST_DIR / "none.json")
    counts = r.counts_by_code
    print(f"    {r.translated} entries checked in {r.elapsed:.1f}s; counts {dict(sorted(counts.items()))}")
    for line in describe(i for i in r.issues if i.level in (ERROR, WARN) and i.code != "W_SAME")[:20]:
        print("    issue with own wrap modes:", line)
    moved = rewrapped(r, english)
    c.check("own wrap modes: auto wrap re-breaks English boxes", moved > 0, f"{moved} entries re-broken")
    c.check("own wrap modes: full scope within the time budget", r.elapsed < TIME_BUDGET_S, f"{r.elapsed:.1f}s")


def main(argv: list[str] | None = None) -> int:
    c = Checks()
    t0 = time.perf_counter()
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    try:
        ps = PakSet()
        m = Metrics.load(paks=ps)
        docs = real_docs()
        strings = TEST_DIR / "crafted"
        crafted = crafted_tree(strings, docs)
        glossary = TEST_DIR / "glossary.json"
        glossary.write_text(json.dumps(GLOSSARY, ensure_ascii=False, indent=1), encoding="utf-8")
        report = validate.validate(strings_dir=strings, stems=SELECTED, glossary_path=glossary, ps=ps, metrics=m)
        test_crafted(c, report, crafted)
        test_text_unit(c, m, InsertResolver(m, paks=ps))
        test_inverted_unit(c)
        test_glossary_unit(c)
        test_min_status(c, strings, glossary, crafted, ps, m)
        test_fonts(c, strings, ps)
        test_malformed(c, docs, ps, m)
        test_cli_and_reports(c, strings, glossary, ps, m)
        test_real(c, ps, m)
        test_identity(c, docs, ps, m)
    finally:
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    c.check("test folder removed", not TEST_DIR.exists())
    print(f"total {time.perf_counter() - t0:.1f}s")
    return c.result()
