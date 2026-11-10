r"""Assemble the distributable patch folder and zip (Phase 5).

dist/<NAME>/
  Instalar.bat, Desinstalar.bat, Crear_mod_Fluffy.bat, LEEME.txt     (tools/patcher)
  programa/instalador.py, programa/sfes/{__init__,config,parche}.py   (tools/patcher/programa)
  programa/sfes/{pak,msg23,oft,fslt,fontpatch}.py                    (unchanged copies of the toolkit modules)
  programa/datos/traduccion_es.json.gz                               (patchdata output: our Spanish only)
  programa/python/                                                   (official embeddable CPython, PSF licence)
  programa/lib/fontTools/                                            (fontTools, MIT licence, from the venv)
  licencias/                                                         (third-party licences)
dist/<NAME>.zip

Nothing from the game is copied: the patcher builds the game files on the player's PC. check() refuses the
package if it finds a game file type (.pak, .msg.23, .oft*, .tex*, .user*) or a file larger than the data file
outside python/ and lib/.

CLI: run.py package --name MMSFLC_Espanol_v1.0 --author "NOMBRE" [--data FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import zipfile
from pathlib import Path

from . import config

PATCHER = config.ROOT / "tools" / "patcher"
SHARED = ("pak.py", "msg23.py", "oft.py", "fslt.py", "fontpatch.py")
PY_EMBED = config.ROOT / "third_party" / "python" / "python-3.14.7-embed-amd64.zip"
PY_NOTICES = config.ROOT / "third_party" / "python" / "python_3.14.7_license.rst"
VENV_SITE = config.ROOT / ".venv" / "Lib" / "site-packages"
GAME_EXT = re.compile(r"\.(pak|msg\.\d+|oft(\.\d+)?|tex\.\d+|user\.\d+|mesh\.\d+|fslt\.\d+)$", re.I)
PTH = "python314.zip\n.\n..\n..\\lib\n"
# Words that must not appear in the shipped text files: one per line in a local, untracked file.
_BANNED = config.WORK / "package_banned_words.txt"
BANNED_WORDS = ([w.strip().lower() for w in _BANNED.read_text(encoding="utf-8").splitlines() if w.strip()]
                if _BANNED.is_file() else [])


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def assemble(name: str, author: str, data: Path) -> Path:
    out = config.DIST / name
    if out.exists():
        shutil.rmtree(out)
    for src in PATCHER.rglob("*"):
        if src.is_file() and "__pycache__" not in src.parts:
            _copy(src, out / src.relative_to(PATCHER))
    sfes_src = config.ROOT / "tools" / "sfes"
    for f in SHARED:
        _copy(sfes_src / f, out / "programa" / "sfes" / f)
    inst = out / "programa" / "instalador.py"
    text = inst.read_text(encoding="utf-8")
    text = text.replace('AUTHOR = "Equipo de traducción"', f"AUTHOR = {author!r}")
    inst.write_text(text, encoding="utf-8", newline="\n")
    for txt in out.glob("*.txt"):         # Notepad-friendly: UTF-8 with BOM, CRLF
        body = txt.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\n", "\r\n")
        txt.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    _copy(data, out / "programa" / "datos" / data.name)
    py = out / "programa" / "python"
    with zipfile.ZipFile(PY_EMBED) as z:
        z.extractall(py)
    (py / "python314._pth").write_text(PTH, encoding="ascii", newline="\r\n")
    lic = out / "licencias"
    _copy(py / "LICENSE.txt", lic / "Python_LICENSE.txt")
    # CPython's notices for incorporated software (expat, zlib, OpenSSL, libffi, libmpdec, zstd...):
    # Doc/license.rst of the same release tag (github.com/python/cpython, v3.14.7)
    _copy(PY_NOTICES, lic / "Python_software_incorporado.txt")
    ft = VENV_SITE / "fontTools"
    shutil.copytree(ft, out / "programa" / "lib" / "fontTools", ignore=shutil.ignore_patterns("__pycache__"))
    _scrub_comments(out / "programa" / "lib")
    dist_info = next(VENV_SITE.glob("fonttools-*.dist-info"))
    for lf in dist_info.rglob("LICENSE*"):
        if lf.is_file():
            _copy(lf, lic / f"fontTools_{lf.name}.txt")
    return out


def _scrub_comments(lib: Path) -> None:
    """Drop comment lines of bundled third-party code that contain a listed word (comments only; code unchanged)."""
    if not BANNED_WORDS:
        return
    for py in lib.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        low = text.lower()
        if not any(w in low for w in BANNED_WORDS):
            continue
        lines = text.split("\n")
        kept = [ln for ln in lines
                if not (ln.lstrip().startswith("#") and any(w in ln.lower() for w in BANNED_WORDS))]
        py.write_text("\n".join(kept), encoding="utf-8", newline="")


def check(folder: Path, data_name: str) -> list[str]:
    problems = []
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(folder).as_posix()
        if p.name == "registro.txt" or p.name.endswith("_Fluffy.zip") or "sfes_es" in p.parts:
            problems.append(f"user-generated file: {rel}")
        if GAME_EXT.search(p.name):
            problems.append(f"game file type: {rel}")
        inside_runtime = rel.startswith(("programa/python/", "programa/lib/"))
        if not inside_runtime and p.name != data_name and p.stat().st_size > 2_000_000:
            problems.append(f"unexpected large file: {rel}")
        if p.suffix in (".py", ".txt", ".bat", ".md") and BANNED_WORDS and (
                not inside_runtime or p.suffix == ".py"):
            low = p.read_text(encoding="utf-8", errors="replace").lower()
            for word in BANNED_WORDS:
                if word in low:
                    problems.append(f"{rel}: mentions '{word}'")
    return problems


REPO_ITEMS = ("Instalar.bat", "Desinstalar.bat", "Crear_mod_Fluffy.bat", "LEEME.txt", "programa", "licencias")


def sync_repo(folder: Path) -> None:
    """Copy the finished package to the repository root, so that downloading the repository gives the
    ready-to-use patch. Only the package's own items are replaced."""
    for item in REPO_ITEMS:
        dst = config.ROOT / item
        if dst.is_dir():
            shutil.rmtree(dst)
        elif dst.exists():
            dst.unlink()
        src = folder / item
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def make_zip(folder: Path) -> Path:
    z = folder.parent / (folder.name + ".zip")
    if z.exists():
        z.unlink()
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                zf.write(p, Path(folder.name) / p.relative_to(folder))
    return z


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="package", description=__doc__.split("\n")[0])
    ap.add_argument("--name", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--data", type=Path, default=config.DIST / "data" / "traduccion_es.json.gz")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--repo", action="store_true", help="also copy the package to the repository root")
    a = ap.parse_args(argv)
    folder = assemble(a.name, a.author, a.data)
    problems = check(folder, a.data.name)
    for p in problems:
        print("  PROBLEM:", p)
    if problems:
        return 1
    size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    print(f"package folder {folder}: {sum(1 for p in folder.rglob('*') if p.is_file())} files, {size} bytes")
    if not a.no_zip:
        z = make_zip(folder)
        print(f"zip {z}: {z.stat().st_size} bytes, sha256 {hashlib.sha256(z.read_bytes()).hexdigest()}")
    if a.repo:
        sync_repo(folder)
        print(f"package copied to the repository root {config.ROOT}")
    return 0
