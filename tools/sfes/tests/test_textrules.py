"""Tests for sfes.textrules (code comparison with localizable DATETIME formats)."""
from __future__ import annotations

from sfes import textrules as tr

US = '<DATETIME {0} "hh:mm A MM/DD/YYYY">'
US_DATE = '<DATETIME {0} "MM/DD/YYYY">'


def _checks() -> list[tuple[str, bool]]:
    out = []

    def check(label: str, ok: bool) -> None:
        out.append((label, bool(ok)))

    # codes compare equal when only the DATETIME format differs
    check("same text matches", tr.codes_match(US, US))
    check("24 h day-first matches", tr.codes_match(US, '<DATETIME {0} "DD/MM/YYYY HH:mm">'))
    check("other argument differs", not tr.codes_match(US, '<DATETIME {1} "DD/MM/YYYY HH:mm">'))
    check("missing DATETIME differs", not tr.codes_match(US, "31/10/2026"))
    check("other codes still exact",
          not tr.codes_match("<ICON INGAME_KEY_A> ok", "<ICON INGAME_KEY_B> ok"))
    check("REPLACE spacing still exact",
          not tr.codes_match("x<REPLACE  RR1_EX>", "x<REPLACE RR1_EX>"))
    check("cmd and placeholder still compared",
          tr.code_diff("_CMD_wait(5){0} a", "{0} a") == (["_CMD_wait(5)"], []))
    check("code_diff reports extras", tr.code_diff("a", "a{1}") == ([], ["{1}"]))

    # formats
    ok_formats = ["DD/MM/YYYY HH:mm", "HH:mm DD/MM/YYYY", "DD-MM-YYYY  HH:mm", "hh:mm A DD/MM/YYYY",
                  "YYYY/MM/DD　　HH:mm"]
    for f in ok_formats:
        check(f"accepted: {f}", tr.format_problem(US, f) is None)
    check("date-only accepted", tr.format_problem(US_DATE, "DD/MM/YYYY") is None)
    bad = {
        "DD/MM HH:mm": "missing year",
        "DD/MM/YYYY": "missing hour",
        "DD de MM de YYYY HH:mm": "letters",
        "hh:mm DD/MM/YYYY": "hh without A",
        "HH:mm A DD/MM/YYYY": "A without hh",
        "DD/MM/YYYY/YYYY HH:mm": "repeated",
        "DD/MM/YYYY {1} HH:mm": "placeholder between fields",
        "DD/MM/YYYY,HH:mm": "comma separator",
        "DD/MM/YYYY HH:mm": "no-break space",
        "DD/MM/YYYY​HH:mm": "zero-width space",
        "DD/MM/20YY HH:mm": "digits",
    }
    for f, why in bad.items():
        check(f"refused ({why}): {f}", tr.format_problem(US, f) is not None)
    check("date-only with a time refused", tr.format_problem(US_DATE, "DD/MM/YYYY HH:mm") is not None)

    # whole texts
    es_ok = 'Guardado: <DATETIME {0} "DD/MM/YYYY HH:mm">'
    check("format_problems empty when fine", tr.format_problems("Saved: " + US, es_ok) == [])
    check("format_problems reports", len(tr.format_problems(US, '<DATETIME {0} "DD/MM">')) == 1)
    check("format_changed", tr.format_changed(US, es_ok) and not tr.format_changed(US, "x " + US))
    check("datetime_formats", tr.datetime_formats(es_ok) == [("{0}", "DD/MM/YYYY HH:mm")])
    check("non-DATETIME tag ignored", tr.datetime_formats("<REF X_1>") == [])
    return out


def main(argv: list[str] | None = None) -> int:
    results = _checks()
    failed = [label for label, ok in results if not ok]
    for label, ok in results:
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0
