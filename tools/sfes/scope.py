r"""Translation scopes: which msg.23 files belong to a scope, and in which group (contract section 2).

Groups:
  common    natives/stm/text/common/*.msg.23 and natives/stm/text/common/message/*.msg.23
  sfN_eng   natives/stm/text/rrN/message/eng/*_eng.msg.23
  sfN_base  natives/stm/text/rrN/message/*.msg.23 with no eng/<stem>_eng.msg.23 twin

Scopes: phase1 = common + sf1_eng + sf1_base (961 files), sf2, sf3, all.
phase1, sf2 and sf3 are the base scopes: each has its own work files (work/strings/<scope>/). 'all' is
the union of the three and has no work files of its own; tools read and write the base scopes' files
for it (home_scope), so a translation exists exactly once.
The file list comes from the community name list, filtered to paths present in the official paks
(PakSet().winner). Order is deterministic: groups in scope order, then paths sorted.

CLI: run.py scope [--scope phase1] [--list]
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass

from . import profiles
from .pak import PakSet, name_list
from .profiles import Profile

TEXT_ROOT = "natives/stm/text/"
MSG_SUFFIX = ".msg.23"
ENG_SUFFIX = "_eng"

SCOPE_GROUPS: dict[str, tuple[str, ...]] = {
    "phase1": ("common", "sf1_eng", "sf1_base"),
    "sf2": ("sf2_eng", "sf2_base"),
    "sf3": ("sf3_eng", "sf3_base"),
}
BASE_SCOPES = tuple(SCOPE_GROUPS)
SCOPE_GROUPS["all"] = SCOPE_GROUPS["phase1"] + SCOPE_GROUPS["sf2"] + SCOPE_GROUPS["sf3"]
SCOPES = tuple(SCOPE_GROUPS)
GROUP_SCOPE = {g: s for s in BASE_SCOPES for g in SCOPE_GROUPS[s]}

GAME_DIRS = {"sf1": "rr1", "sf2": "rr2", "sf3": "rr3"}


@dataclass(frozen=True)
class FileSpec:
    """One msg.23 file of a scope. `profile` is a key of profiles.PROFILES."""
    path: str
    group: str
    stem: str
    category: str
    profile: str

    @property
    def layout(self) -> Profile:
        return profiles.PROFILES[self.profile]

    @property
    def is_eng(self) -> bool:
        """True for files reached through the English scripts (*.user.3.en) by entry GUID."""
        return self.group.endswith("_eng")


def members(scope: str) -> tuple[str, ...]:
    """Base scopes that make up `scope` ('all' = phase1, sf2, sf3; a base scope is its own member)."""
    if scope not in SCOPE_GROUPS:
        raise ValueError(f"unknown scope {scope!r}; choose from {', '.join(SCOPES)}")
    return BASE_SCOPES if scope == "all" else (scope,)


def home_scope(group: str) -> str:
    """The base scope whose folder holds the work files of `group`."""
    return GROUP_SCOPE[group]


def stem_of(path: str) -> str:
    """File name without the .msg.23 extension."""
    return path.rsplit("/", 1)[1][: -len(MSG_SUFFIX)]


def _eng_twin(path: str) -> str:
    """eng/<stem>_eng.msg.23 path next to a base message file."""
    folder, _ = path.rsplit("/", 1)
    return f"{folder}/eng/{stem_of(path)}{ENG_SUFFIX}{MSG_SUFFIX}"


def group_of(path: str, available: set[str]) -> str | None:
    """Group of a msg path, or None if it belongs to no group. `available` = all msg paths in the paks."""
    rest = path[len(TEXT_ROOT):] if path.startswith(TEXT_ROOT) else None
    if rest is None or not path.endswith(MSG_SUFFIX):
        return None
    parts = rest.split("/")
    if parts[0] == "common" and (len(parts) == 2 or (len(parts) == 3 and parts[1] == "message")):
        return "common"
    for game, folder in GAME_DIRS.items():
        if parts[0] != folder or len(parts) < 3 or parts[1] != "message":
            continue
        if len(parts) == 4 and parts[2] == "eng" and stem_of(path).endswith(ENG_SUFFIX):
            return f"{game}_eng"
        if len(parts) == 3 and _eng_twin(path) not in available:
            return f"{game}_base"
    return None


def msg_paths(ps: PakSet) -> set[str]:
    """Every named .msg.23 path that is present in the official paks."""
    return {p for h, p in name_list().items() if p.endswith(MSG_SUFFIX) and h in ps.winner}


def files(scope: str, ps: PakSet | None = None) -> list[FileSpec]:
    """FileSpecs of a scope, groups in scope order, paths sorted inside each group."""
    if scope not in SCOPE_GROUPS:
        raise ValueError(f"unknown scope {scope!r}; choose from {', '.join(SCOPES)}")
    available = msg_paths(ps or PakSet())
    order = {g: k for k, g in enumerate(SCOPE_GROUPS[scope])}
    specs = []
    for path in available:
        group = group_of(path, available)
        if group in order:
            specs.append(FileSpec(path, group, stem_of(path), profiles.category_of(path),
                                  profiles.profile_for(path).name))
    specs.sort(key=lambda s: (order[s.group], s.path))
    return specs


def group_counts(specs: list[FileSpec]) -> Counter:
    return Counter(s.group for s in specs)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="scope", description="List the msg.23 files of a scope.")
    ap.add_argument("--scope", default="phase1", choices=SCOPES)
    ap.add_argument("--list", action="store_true", help="print every file (group, profile, category, path)")
    args = ap.parse_args(argv)
    specs = files(args.scope)
    if args.list:
        for s in specs:
            print(f"{s.group}\t{s.profile}\t{s.category}\t{s.path}")
    counts = group_counts(specs)
    for g in SCOPE_GROUPS[args.scope]:
        print(f"{g:10s} {counts[g]:5d} files")
    print(f"{'total':10s} {len(specs):5d} files")
    by_profile = Counter(s.profile for s in specs)
    print("profiles:", ", ".join(f"{k} {v}" for k, v in sorted(by_profile.items())))
    return 0
