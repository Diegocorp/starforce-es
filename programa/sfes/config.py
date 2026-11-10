"""Constantes del parche (versión distribuible de la configuración del kit de herramientas).

La carpeta del juego se pasa siempre de forma explícita; esta ruta solo es el valor por defecto.
"""
from __future__ import annotations

from pathlib import Path

GAME_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\MegaMan StarForce LegacyCollection")

# Carpetas del kit original. El parche no las usa; existen para que los módulos compartidos se importen igual.
ROOT = Path(__file__).resolve().parent.parent
ORIGINAL = ROOT / "_sin_uso"
BUILD = ROOT / "_sin_uso"
WORK = ROOT / "_sin_uso"
NAMELIST = ROOT / "_sin_uso" / "names.list"

BASE_PAK = "re_chunk_000.pak"
PATCH_FMT = "re_chunk_000.pak.patch_{:03d}.pak"
MARKER_PATH = "natives/stm/_sfes/sfes_patch_marker.txt"

LANG_JA = 0
LANG_EN = 1
LANG_ES = 5
LANG_ZHTW = 12
LANG_ZHCN = 13
LANG_ES419 = 32
