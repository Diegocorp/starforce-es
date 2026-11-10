r"""Núcleo del parche: genera los archivos en español a partir de la copia del juego del jugador y los instala,
los desinstala o los empaqueta para Fluffy Mod Manager.

El parche no trae ningún archivo del juego. Lee los .pak oficiales de la carpeta del juego (solo lectura),
cambia el texto en inglés de cada entrada por el español de datos/traduccion_es.json.gz cuando la huella del
inglés coincide con la del juego probado, añade los signos ¡ ¿ a la fuente RR_System_Font y escribe el
resultado como archivos sueltos en natives\ (los carga el Loose File Loader de REFramework).

En la carpeta del juego solo se escribe:
  natives\...            los archivos del parche (nunca un .pak ni otro archivo del juego)
  sfes_es\               manifiesto de la instalación y respaldo de archivos sueltos que ya existían
  re2_fw_config.txt      solo si el jugador acepta activar el Loose File Loader de REFramework
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import stat
import subprocess
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import fontpatch, msg23
from .pak import PakSet

APPID = "3500390"
GAME_EXE = "MEGAMAN_STARFORCE_Legacy_Collection.exe"
BASE_PAK = "re_chunk_000.pak"
STATE_DIR = "sfes_es"
MANIFEST = "manifiesto.json"
BACKUP_DIR = "respaldo"
REF_DLL = "dinput8.dll"
REF_CONFIG = "re2_fw_config.txt"
LOOSE_KEY = "LooseFileLoader_Enabled"
TMP_SUFFIX = ".sfes_tmp"


class ParcheError(Exception):
    """Error con un mensaje para el jugador."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def en_fingerprint(text: str) -> str:
    return sha256(text.encode("utf-8"))[:16]


def load_data(path: Path) -> dict:
    data = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    if data.get("schema") != 1:
        raise ParcheError(f"Formato de datos desconocido en {path.name}.")
    return data


# ---------------------------------------------------------------------------------------------- juego

def is_game_dir(path: Path) -> bool:
    return (path / GAME_EXE).is_file() and (path / BASE_PAK).is_file()


def _vdf_values(text: str, key: str) -> list[str]:
    out = []
    for line in text.splitlines():
        parts = [p for p in line.strip().split('"') if p.strip()]
        if len(parts) == 2 and parts[0].lower() == key.lower():
            out.append(parts[1].replace("\\\\", "\\"))
    return out


def _steam_roots() -> list[Path]:
    roots = []
    try:
        import winreg
        for hive, sub, name in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    roots.append(Path(winreg.QueryValueEx(k, name)[0]))
            except OSError:
                pass
    except ImportError:
        pass
    roots.append(Path(r"C:\Program Files (x86)\Steam"))
    return roots


def find_game_dirs() -> list[Path]:
    """Carpetas del juego encontradas en las bibliotecas de Steam."""
    libraries: list[Path] = []
    for root in _steam_roots():
        libraries.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            libraries += [Path(p) for p in _vdf_values(vdf.read_text(encoding="utf-8", errors="replace"), "path")]
    found: list[Path] = []
    seen: set[str] = set()
    for lib in libraries:
        acf = lib / "steamapps" / f"appmanifest_{APPID}.acf"
        names = []
        if acf.is_file():
            names = _vdf_values(acf.read_text(encoding="utf-8", errors="replace"), "installdir")
        names.append("MegaMan StarForce LegacyCollection")
        for name in names:
            cand = lib / "steamapps" / "common" / name
            key = os.path.normcase(str(cand.resolve())) if cand.exists() else ""
            if key and key not in seen and is_game_dir(cand):
                seen.add(key)
                found.append(cand)
    return found


def game_buildid(game_dir: Path) -> str | None:
    acf = game_dir.parent.parent / f"appmanifest_{APPID}.acf"
    if not acf.is_file():
        return None
    ids = _vdf_values(acf.read_text(encoding="utf-8", errors="replace"), "buildid")
    return ids[0] if ids else None


def game_running() -> bool:
    """True si el juego está abierto. La salida de tasklist llega en la página de códigos del sistema (no UTF-8
    en Windows en español), así que se busca el nombre del .exe (ASCII) en los bytes, sin decodificar."""
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/NH", "/FO", "CSV"],
                             capture_output=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return GAME_EXE.lower().encode("ascii") in (out.stdout or b"").lower()
    except Exception:
        return False


@dataclass
class RefState:
    dll: bool
    config: bool
    loose_enabled: bool | None   # None: the key is not in the config file


def reframework_state(game_dir: Path) -> RefState:
    cfg = game_dir / REF_CONFIG
    enabled = None
    if cfg.is_file():
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == LOOSE_KEY:
                enabled = v.strip().lower() == "true"
    return RefState((game_dir / REF_DLL).is_file(), cfg.is_file(), enabled)


def enable_loose_loader(game_dir: Path) -> None:
    """Pone LooseFileLoader_Enabled=true en re2_fw_config.txt (lo crea si no existe)."""
    cfg = game_dir / REF_CONFIG
    lines = cfg.read_text(encoding="utf-8", errors="replace").splitlines() if cfg.is_file() else []
    done = False
    for i, line in enumerate(lines):
        k, sep, _ = line.partition("=")
        if sep and k.strip() == LOOSE_KEY:
            lines[i] = f"{LOOSE_KEY}=true"
            done = True
    if not done:
        lines.append(f"{LOOSE_KEY}=true")
    _atomic_write(cfg, ("\n".join(lines) + "\n").encode("utf-8"))


# ---------------------------------------------------------------------------------------------- generar

@dataclass
class GenReport:
    files: int = 0
    entries: int = 0
    entries_stale: int = 0          # the English changed in this game version: the entry stays English
    entries_missing: int = 0
    files_missing: list[str] = field(default_factory=list)
    files_as_tested: int = 0
    font_as_tested: bool | None = None
    font_error: str | None = None


def generate(game_dir: Path, data: dict, progress=None) -> tuple[dict[str, bytes], GenReport]:
    """Archivos del parche (ruta del pak -> bytes) generados a partir de los .pak del juego."""
    ps = PakSet(game_dir)
    rep = GenReport()
    out: dict[str, bytes] = {}
    lang = data["lang_slot"]
    total = len(data["files"])
    for i, f in enumerate(data["files"], 1):
        if progress and (i % 50 == 0 or i == total):
            progress(i, total)
        path = f["path"]
        if not ps.has(path):
            rep.files_missing.append(path)
            continue
        msg = msg23.parse(ps.read(path))
        slot = msg.slot(lang)
        by_guid = {e.guid: e for e in msg.entries}
        applied = 0
        for guid, fp, es in f["entries"]:
            e = by_guid.get(guid)
            if e is None:
                rep.entries_missing += 1
            elif en_fingerprint(e.texts[slot]) != fp:
                rep.entries_stale += 1
            else:
                e.texts[slot] = es
                applied += 1
        rep.entries += applied
        if applied:
            raw = msg23.build(msg)
            out[path] = raw
            rep.files += 1
            rep.files_as_tested += sha256(raw) == f["out_sha256"]
    for font in data["fonts"]:
        try:
            raw = fontpatch.outputs(font["variant"], ps)[font["path"]]
        except Exception as exc:  # the dialogue keeps working without ¡ ¿; report it
            rep.font_error = f"{type(exc).__name__}: {exc}"
            continue
        out[font["path"]] = raw
        rep.files += 1
        rep.font_as_tested = sha256(raw) == font["out_sha256"]
    return out, rep


# ---------------------------------------------------------------------------------------------- instalar

def _safe_rel(rel: str) -> PurePosixPath:
    """Ruta relativa segura dentro de natives/ (rechaza rutas absolutas, '..' y otras carpetas)."""
    if not isinstance(rel, str):
        raise ParcheError("El manifiesto de la instalación tiene una ruta no válida.")
    p = PurePosixPath(rel.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0].lower() != "natives" or ":" in rel:
        raise ParcheError(f"Ruta no válida en el manifiesto: {rel}")
    return p


def _make_writable(p: Path) -> None:
    """Quita el atributo de solo lectura (de un archivo del parche o de su respaldo) antes de cambiarlo."""
    try:
        if p.is_file() and not os.access(p, os.W_OK):
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass


def _atomic_write(dest: Path, data: bytes, sync: bool = False) -> None:
    tmp = dest.with_name(dest.name + TMP_SUFFIX)
    _make_writable(tmp)
    with open(tmp, "wb") as f:
        f.write(data)
        if sync:
            f.flush()
            os.fsync(f.fileno())
    _make_writable(dest)
    os.replace(tmp, dest)


def manifest_path(game_dir: Path) -> Path:
    return game_dir / STATE_DIR / MANIFEST


def _valid_manifest(m: object) -> bool:
    return isinstance(m, dict) and isinstance(m.get("files"), list) and all(
        isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("sha256"), str)
        for f in m["files"])


def read_manifest(game_dir: Path) -> dict | None:
    """El manifiesto de la instalación, o None si el parche no está instalado. Si está dañado se usa la copia
    anterior (manifiesto.json.bak); si las dos están dañadas, ParcheError."""
    p = manifest_path(game_dir)
    candidates = [p, p.with_name(MANIFEST + ".bak")]
    if not any(c.exists() for c in candidates):
        return None
    for c in candidates:
        try:
            m = json.loads(c.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _valid_manifest(m):
            return m
    raise ParcheError("La lista de la instalación (sfes_es\\manifiesto.json) está dañada. Ejecuta Instalar.bat: "
                      "reconoce los archivos del parche por su contenido y repara la instalación.")


def _write_manifest(game_dir: Path, manifest: dict) -> None:
    p = manifest_path(game_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        try:
            if _valid_manifest(json.loads(p.read_text(encoding="utf-8"))):
                _atomic_write(p.with_name(MANIFEST + ".bak"), p.read_bytes(), sync=True)
        except (OSError, ValueError):
            pass
    _atomic_write(p, json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"), sync=True)


def _free_backup(state: Path, p: PurePosixPath) -> PurePosixPath:
    """Ruta libre en respaldo\\ para p: nunca se sobrescribe un respaldo que ya exista."""
    cand, n = p, 1
    while state.joinpath(BACKUP_DIR, *cand.parts).exists():
        n += 1
        cand = p.with_name(f"{p.name}.sfes_respaldo{n}")
    return cand


@dataclass
class InstallReport:
    written: int = 0
    backed_up: int = 0
    gen: GenReport | None = None


def install(game_dir: Path, data: dict, files: dict[str, bytes], gen: GenReport,
            ref_config_changed: bool = False) -> InstallReport:
    """Escribe los archivos generados en natives\\ con manifiesto y respaldo. Una instalación anterior se
    desinstala antes. Los archivos que ya son del parche (mismo contenido que esta versión o que la versión
    probada) se reconocen y no se respaldan. Si se interrumpe, el manifiesto (marcado incompleto) permite
    desinstalar igual."""
    try:
        old = read_manifest(game_dir)
    except ParcheError:
        old = None                      # damaged: our files are recognised by content below
    if old is not None:
        uninstall(game_dir)
    state = game_dir / STATE_DIR
    tested = {f["path"]: f["out_sha256"] for f in data["files"]}
    tested.update({f["path"]: f["out_sha256"] for f in data["fonts"]})
    plan = []
    created: set[str] = set()
    for rel in sorted(files):
        p = _safe_rel(rel)
        dest = game_dir.joinpath(*p.parts)
        backup = None
        if dest.exists():
            if not dest.is_file():
                raise ParcheError(f"Hay una carpeta donde va un archivo del parche: {dest}")
            if sha256(dest.read_bytes()) not in (sha256(files[rel]), tested.get(rel)):
                backup = _free_backup(state, p)
        parent = p.parent
        while parent.parts and not game_dir.joinpath(*parent.parts).exists():
            created.add(str(parent))
            parent = parent.parent
        plan.append((p, dest, backup))
    manifest = {"schema": 1, "patch": data["name"], "version": data["version"],
                "installed": datetime.now().isoformat(timespec="seconds"), "game_build": game_buildid(game_dir),
                "complete": False, "reframework_config_changed": ref_config_changed,
                "files": [{"path": str(p), "sha256": sha256(files[str(p)]), "backup": str(b) if b else None}
                          for p, _, b in plan],
                "dirs": sorted(created, key=lambda d: (-d.count("/"), d))}
    rep = InstallReport(gen=gen)
    for p, dest, backup in plan:          # backups first, then the manifest, then the files
        if backup is not None:
            bk = state.joinpath(BACKUP_DIR, *backup.parts)
            bk.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(bk, dest.read_bytes(), sync=True)
            rep.backed_up += 1
    _write_manifest(game_dir, manifest)
    for p, dest, _ in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(dest, files[str(p)])
        rep.written += 1
    manifest["complete"] = True
    _write_manifest(game_dir, manifest)
    return rep


@dataclass
class UninstallReport:
    removed: int = 0
    restored: int = 0
    missing: int = 0
    kept_changed: list[str] = field(default_factory=list)   # replaced by something else since: left alone
    leftover_backups: int = 0                               # backups still in sfes_es\respaldo
    state_removed: bool = False


def uninstall(game_dir: Path) -> UninstallReport | None:
    """Quita exactamente los archivos del manifiesto que siguen siendo los del parche y restaura los respaldos.
    Los que otro mod cambió se dejan como están y siguen en el manifiesto (con su respaldo), para que una
    desinstalación posterior los quite si vuelven a ser los del parche."""
    manifest = read_manifest(game_dir)
    if manifest is None:
        return None
    state = game_dir / STATE_DIR
    rep = UninstallReport()
    remaining = []
    for f in manifest["files"]:
        p = _safe_rel(f["path"])
        dest = game_dir.joinpath(*p.parts)
        tmp = dest.with_name(dest.name + TMP_SUFFIX)
        if tmp.is_file():
            _make_writable(tmp)
            tmp.unlink()
        bk = state.joinpath(BACKUP_DIR, *_safe_rel(f["backup"]).parts) if f.get("backup") else None
        if dest.is_file():
            current = sha256(dest.read_bytes())
            if current != f["sha256"]:
                if bk is not None and bk.is_file() and sha256(bk.read_bytes()) == current:
                    _make_writable(bk)
                    bk.unlink()            # the other mod's original is already back in place
                    rep.restored += 1
                    continue
                rep.kept_changed.append(str(p))
                remaining.append(f)
                continue
            _make_writable(dest)
            dest.unlink()
            rep.removed += 1
        else:
            rep.missing += 1
        if bk is not None and bk.is_file() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(bk), str(dest))
            rep.restored += 1
    for d in manifest.get("dirs", []):
        path = game_dir.joinpath(*_safe_rel(d).parts)
        try:
            path.rmdir()                     # only if empty
        except OSError:
            pass
    backups = state / BACKUP_DIR
    if backups.exists():                     # drop empty folders left in respaldo\
        for d in sorted((x for x in backups.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
            try:
                d.rmdir()
            except OSError:
                pass
    rep.leftover_backups = sum(1 for x in backups.rglob("*") if x.is_file()) if backups.exists() else 0
    if remaining:
        _write_manifest(game_dir, dict(manifest, files=remaining, complete=True, residual=True))
    else:
        for name in (MANIFEST, MANIFEST + ".bak"):
            q = state / name
            if q.is_file():
                _make_writable(q)
                q.unlink()
        if not rep.leftover_backups:
            shutil.rmtree(state, ignore_errors=True)
        rep.state_removed = not state.exists()
    return rep


# ---------------------------------------------------------------------------------------------- Fluffy

MODINFO = """name={name}
version={version}
description=Traduccion al espanol de Mega Man Star Force 1 y de los menus de la coleccion. Requiere REFramework con el Loose File Loader activado y el texto del juego en ingles.
author={author}
"""


def fluffy_zip(out_zip: Path, data: dict, files: dict[str, bytes], author: str) -> None:
    """Paquete para Fluffy Mod Manager con los archivos generados en este PC (no se debe compartir)."""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_zip.with_name(out_zip.name + TMP_SUFFIX)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("modinfo.ini", MODINFO.format(name="Traduccion al espanol (SF1)", version=data["version"],
                                                 author=author))
        for rel in sorted(files):
            z.writestr(str(_safe_rel(rel)), files[rel])
    os.replace(tmp, out_zip)
