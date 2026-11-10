# starforce-es

Free fan translation of **Mega Man Star Force Legacy Collection** (Steam) into neutral Latin American Spanish:
Mega Man Star Force 1 and the collection's menus. Unofficial; not affiliated with Capcom.

This repository holds the code only. Game data, game text and translation work files stay on the
translator's PC and are never committed (see `.gitignore`: everything is ignored unless listed).

## Contents

| Path | What it is |
|---|---|
| `tools/run.py` | launcher: `python -I -X utf8 tools/run.py <module> [args]` |
| `tools/sfes/` | toolkit: pak reading (`pak`), msg.23 files (`msg23`), extraction, CSV import/export, validation, line wrapping by pixel width, font patch for ¡ ¿, build, batch translation helpers, patch data export (`patchdata`), packaging (`package`) |
| `tools/sfes/tests/` | toolkit tests (they read the local work files and the installed game) |
| `tools/patcher/` | sources of the player-facing package: `Instalar.bat`, `Desinstalar.bat`, `Crear_mod_Fluffy.bat`, `LEEME.txt` (Spanish README) and the patcher in `programa/` |
| `tools/patcher_test.py` | end-to-end test of the package, run with the package's own embedded Python |

## How the patch works

The release package ships no game files. Its data file holds, for each translated entry, the entry guid, a
fingerprint of the English text and the Spanish text. The installer reads the player's own `.pak` files,
applies the Spanish where the fingerprint matches, adds ¡ and ¿ to the player's own `RR_System_Font`, and writes
loose files under `<game>/natives/` for REFramework's Loose File Loader (the game rejects modified paks). It keeps
a manifest and backups in `<game>/sfes_es/` so uninstalling removes exactly what it wrote.

## Release flow (on the translator's PC)

```
python -I -X utf8 tools/run.py validate --scope phase1 --fonts rrsystem --glossary work/glossary.json
python -I -X utf8 tools/run.py build --scope phase1 --name release_v1 --fonts rrsystem
python -I -X utf8 tools/run.py patchdata --build release_v1 --version 1.0
python -I -X utf8 tools/run.py package --name MMSFLC_Espanol_v1.0 --author Diegocorp
dist/MMSFLC_Espanol_v1.0/programa/python/python.exe -I -B -X utf8 tools/patcher_test.py dist/MMSFLC_Espanol_v1.0 build/release_v1
```

Requirements: Windows, Python 3.14 with fontTools, the game installed through Steam, and the local work files
(`work/`), which are not in this repository. `package` also needs the embeddable CPython zip in
`third_party/python/`.

Known test drift: three checks in `tools/sfes/tests/test_validate.py` expect untranslated entries and the
warning set from before the translation was finished; they fail on the finished work files.
