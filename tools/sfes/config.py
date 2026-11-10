"""Project paths and constants for the Spanish patch toolkit (sfes).

The game folder is only ever opened for reading by this toolkit.
Override the game location with the environment variable SFES_GAME_DIR.
"""
from __future__ import annotations

import os
from pathlib import Path

GAME_DIR = Path(os.environ.get(
    "SFES_GAME_DIR",
    r"C:\Program Files (x86)\Steam\steamapps\common\MegaMan StarForce LegacyCollection"))

ROOT = Path(os.environ.get("SFES_ROOT", r"C:\StarForceES"))
ORIGINAL = ROOT / "original"
PAK_EXTRACT = ORIGINAL / "pak_extract"
RESEARCH = ROOT / "research"
WORK = ROOT / "work"
STRINGS = WORK / "strings"          # translation work files (JSON, one per msg file)
REF = WORK / "ref"                  # local-only reference material (DS Spanish etc.); never shipped
CACHE = WORK / "cache"              # derived caches (font metrics, file index)
EXPORT = WORK / "export"            # CSV exports for spreadsheet work
BUILD = ROOT / "build"              # build staging (natives tree, pak)
DIST = ROOT / "dist"                # packages handed to the user
NAMELIST = ROOT / "third_party" / "refs" / "ekey_lists" / "MMSFLC_STM_Release.list"

BASE_PAK = "re_chunk_000.pak"
PATCH_FMT = "re_chunk_000.pak.patch_{:03d}.pak"

# Our own patch paks carry this marker entry so the toolkit never mistakes them for official data.
MARKER_PATH = "natives/stm/_sfes/sfes_patch_marker.txt"

LANG_JA = 0
LANG_EN = 1
LANG_ES = 5
LANG_ZHTW = 12
LANG_ZHCN = 13
LANG_ES419 = 32
