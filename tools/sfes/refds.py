r"""Local-only DS Spanish reference (contract section 3). Never shipped.

Builds work/ref/ds_es/<scope>/<stem>.json for every *_eng work file of a scope that has a Phase 1 DS
alignment (sf1_eng: research/ds/lc_align_sf1.jsonl, sf2_eng: research/ds/lc_align_sf2.jsonl):

  { "<entry name>": {"es": "...", "match": "exact|fuzzy", "score": 0.97, "ds": "arc:script:box",
                     "level": "box|script"} }

Accepted alignment rows:
  exact          always;
  fuzzy >= 0.85  only if the visible English has at least 6 characters and the same set of digits as the
                 DS English (Phase 1 verification found short or numbered fuzzy matches unreliable).
A row is used only while its entry name, index and English still equal the work file; otherwise it is
counted as stale. DS line breaks are written as CRLF like the game text. Level "script" means the DS
Spanish is a whole DS script (its boxes separated by " / "), not a single box.

Coverage per file is written to work/ref/ds_es/<scope>_coverage.csv. Text entries are entries whose
English has letters once codes are removed (the entries that need a translation). A scope without an
alignment (sf3) gets no files at all. Scope 'all' builds each base scope that has work files.
The alignment holds text from the user's own cartridges: everything written here stays local.

CLI: run.py refds --scope phase1
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import codes, config
from . import scope as scopes
from .extract import dump_json, normalize_breaks, scope_docs, write_if_changed

DS_REF = config.REF / "ds_es"
ALIGNMENTS = {
    "sf1_eng": config.RESEARCH / "ds" / "lc_align_sf1.jsonl",
    "sf2_eng": config.RESEARCH / "ds" / "lc_align_sf2.jsonl",
}
MIN_FUZZY_SCORE = 0.85
MIN_FUZZY_LEN = 6
EXACT, FUZZY = "exact", "fuzzy>=0.85"
LEVELS = {"segment": "box", "script": "script"}
DS_CODE_RX = re.compile(r"\{[^}]*\}")
COVERAGE_COLUMNS = ["file", "entries", "text_entries", "exact", "fuzzy", "rejected_fuzzy", "stale",
                    "covered", "coverage_pct"]


class NoAlignment(ValueError):
    """No Phase 1 DS alignment covers the scope."""


def alignment_groups(scope: str) -> list[str]:
    return [g for g in scopes.SCOPE_GROUPS[scope] if g in ALIGNMENTS]


def visible(text: str) -> str:
    """Game text without codes, with runs of whitespace collapsed."""
    return " ".join(codes.strip_markup(text, keep_breaks=False).split())


def digits(text: str) -> frozenset[str]:
    return frozenset(ch for ch in text if ch.isdigit())


def fuzzy_ok(row: dict) -> bool:
    """Fuzzy rows need enough text and identical digits (card levels, amounts, counts)."""
    lc = visible(row["lc_en"])
    ds = DS_CODE_RX.sub("", row.get("ds_en", ""))
    return (row.get("score", 0) >= MIN_FUZZY_SCORE and len(lc) >= MIN_FUZZY_LEN
            and digits(lc) == digits(ds))


def ref_record(row: dict) -> dict:
    return {
        "es": normalize_breaks(row["ds_es"]),
        "match": "exact" if row["match"] == EXACT else "fuzzy",
        "score": row["score"],
        "ds": f"{row['ds_arc']}:{row['ds_script']}:{row['ds_seg']}",
        "level": LEVELS[row["es_level"]],
    }


def load_alignment(path: Path) -> dict[str, list[dict]]:
    """Alignment rows grouped by work-file stem (<lc_file>_eng)."""
    out: dict[str, list[dict]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            out.setdefault(f"{row['lc_file']}_eng", []).append(row)
    return out


@dataclass
class FileCoverage:
    stem: str
    entries: int = 0
    text_entries: int = 0
    counts: Counter = field(default_factory=Counter)   # exact, fuzzy, rejected_fuzzy, stale
    covered: int = 0

    @property
    def pct(self) -> float:
        return 100.0 * self.covered / self.text_entries if self.text_entries else 100.0


def build_file(doc: dict, rows: list[dict]) -> tuple[dict, FileCoverage]:
    """Reference mapping (in entry order) and coverage for one work file."""
    stem = scopes.stem_of(doc["path"])
    entries = doc["entries"]
    cov = FileCoverage(stem, entries=len(entries))
    by_name = {e["name"]: e for e in entries}
    accepted: dict[str, dict] = {}
    for row in rows:
        if row["match"] not in (EXACT, FUZZY):
            continue
        e = by_name.get(row["lc_name"])
        if e is None or e["i"] != row["lc_index"] or e["en"] != row["lc_en"]:
            cov.counts["stale"] += 1
        elif row["match"] == FUZZY and not fuzzy_ok(row):
            cov.counts["rejected_fuzzy"] += 1
        else:
            accepted[row["lc_name"]] = ref_record(row)
            cov.counts["exact" if row["match"] == EXACT else "fuzzy"] += 1
    text = [e for e in entries if codes.has_letters(e["en"])]
    cov.text_entries = len(text)
    cov.covered = sum(1 for e in text if e["name"] in accepted)
    ordered = {e["name"]: accepted[e["name"]] for e in entries if e["name"] in accepted}
    return ordered, cov


def coverage_csv(rows: list[FileCoverage]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(COVERAGE_COLUMNS)
    for c in rows:
        w.writerow([c.stem, c.entries, c.text_entries, c.counts["exact"], c.counts["fuzzy"],
                    c.counts["rejected_fuzzy"], c.counts["stale"], c.covered, f"{c.pct:.1f}"])
    return buf.getvalue().encode("utf-8-sig")


def build(scope: str, strings_dir: Path = config.STRINGS, out_dir: Path = DS_REF) -> list[FileCoverage]:
    """Write the reference files of base scope `scope` and its coverage CSV. Returns per-file coverage.
    Raises NoAlignment (scope without DS alignment) or FileNotFoundError (no work files) before writing."""
    if scope not in scopes.BASE_SCOPES:
        raise ValueError(f"build the base scopes of {scope!r} one by one: {', '.join(scopes.members(scope))}")
    groups = alignment_groups(scope)
    if not groups:
        raise NoAlignment(f"no DS alignment covers scope {scope}")
    docs = [doc for _, doc in scope_docs(scope, strings_dir) if doc["group"] in groups]
    alignments = {g: load_alignment(ALIGNMENTS[g]) for g in groups}
    out_dir = Path(out_dir)
    result = []
    for doc in docs:
        stem = scopes.stem_of(doc["path"])
        refs, cov = build_file(doc, alignments[doc["group"]].get(stem, []))
        write_if_changed(out_dir / scope / f"{stem}.json", dump_json(refs))
        result.append(cov)
    write_if_changed(out_dir / f"{scope}_coverage.csv", coverage_csv(result))
    return result


def load(scope: str, stem: str, ref_dir: Path = DS_REF) -> dict:
    """Reference mapping of one file ({} if there is none)."""
    path = Path(ref_dir) / scope / f"{stem}.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def report_lines(scope: str, result: list[FileCoverage], out_dir: Path, worst: int = 10) -> list[str]:
    total = FileCoverage("total")
    for c in result:
        total.entries += c.entries
        total.text_entries += c.text_entries
        total.covered += c.covered
        total.counts += c.counts
    lines = [f"DS reference for {scope}: {len(result)} files -> {Path(out_dir) / scope}",
             f"  text entries {total.text_entries}, covered {total.covered} ({total.pct:.1f}%): "
             f"exact {total.counts['exact']}, fuzzy {total.counts['fuzzy']}; "
             f"rejected fuzzy {total.counts['rejected_fuzzy']}, stale {total.counts['stale']}",
             f"  per-file coverage: {Path(out_dir) / (scope + '_coverage.csv')}",
             "  lowest coverage:"]
    for c in sorted(result, key=lambda c: (c.pct, c.stem))[:worst]:
        lines.append(f"    {c.stem:28s} {c.covered:4d}/{c.text_entries:<4d} {c.pct:5.1f}%")
    return lines


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="refds", description="Build the local DS Spanish reference.")
    ap.add_argument("--scope", default="phase1", choices=scopes.SCOPES)
    args = ap.parse_args(argv)
    targets = [s for s in scopes.members(args.scope) if alignment_groups(s)]
    if not targets:
        print(f"refds: no DS alignment covers scope {args.scope}")
        return 1
    built = 0
    for scope in targets:
        try:
            result = build(scope)
        except FileNotFoundError as exc:      # not extracted yet; 'all' goes on with the other scopes
            print(f"refds: {exc}")
            continue
        except (ValueError, OSError) as exc:  # inconsistent work files, unreadable alignment
            print(f"refds: {exc}")
            return 2
        built += 1
        print("\n".join(report_lines(scope, result, DS_REF)))
    return 0 if built else 2
