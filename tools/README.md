# Toolkit (for maintaining the translation)

Players do not need anything in this folder: the patch is at the repository root (see the main README).

The translation work files, game data and reference material stay on the translator's PC and are never committed
(`.gitignore` is an allowlist). Everything here runs against the installed game, read only.

| Path | What it is |
|---|---|
| `run.py` | launcher: `python -I -X utf8 tools/run.py <module> [args]` |
| `sfes/` | pak reading (`pak`), msg.23 files (`msg23`), extraction, CSV import/export, validation, line wrapping by pixel width, font patch for ¡ ¿, build, batch translation helpers, patch data export (`patchdata`), packaging (`package`) |
| `sfes/tests/` | toolkit tests (they read the local work files and the installed game) |
| `patcher/` | sources of the player-facing files at the repository root (`Instalar.bat`, `LEEME.txt`, `programa/instalador.py`, `programa/sfes/parche.py`, ...) |
| `patcher_test.py` | end-to-end test of the package, run with the package's own embedded Python |

## How the patch works

The patch ships no game files. `programa/datos/traduccion_es.json.gz` holds, for each translated entry, the entry
guid, a fingerprint of the English text and the Spanish text. The installer reads the player's own `.pak` files,
applies the Spanish where the fingerprint matches, adds ¡ and ¿ to the player's own `RR_System_Font`, and writes
loose files under `<game>/natives/` for REFramework's Loose File Loader (the game rejects modified paks). It keeps
a manifest and backups in `<game>/sfes_es/`, so uninstalling removes exactly what it wrote.

## Release flow

```
python -I -X utf8 tools/run.py validate --scope phase1 --fonts rrsystem --glossary work/glossary.json
python -I -X utf8 tools/run.py build --scope phase1 --name release_v1 --fonts rrsystem
python -I -X utf8 tools/run.py patchdata --build release_v1 --version 1.0
python -I -X utf8 tools/run.py package --name MMSFLC_Espanol_v1.0 --author Diegocorp --repo
dist/MMSFLC_Espanol_v1.0/programa/python/python.exe -I -B -X utf8 tools/patcher_test.py dist/MMSFLC_Espanol_v1.0 build/release_v1
```

`--repo` copies the finished package to the repository root. Then commit, push, and attach
`dist/MMSFLC_Espanol_v1.0.zip` to a GitHub release.

Requirements: Windows, Python 3.14 with fontTools, the game installed through Steam, the local work files (`work/`)
and the embeddable CPython zip in `third_party/python/`.

Known test drift: three checks in `sfes/tests/test_validate.py` expect untranslated entries and the warning set from
before the translation was finished; they fail on the finished work files.
