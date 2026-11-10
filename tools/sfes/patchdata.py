r"""Patch data for the distributable patcher: our Spanish text only, never game text.

export(build_name) compares every msg file of build/<name>/natives with the same file read from the official
paks and records, per changed entry, the entry guid, a fingerprint of the official English text (first 16 hex
digits of its SHA-256) and the final Spanish text exactly as the build wrote it. The patcher on the player's PC
reads the player's own paks, checks each fingerprint and writes the same Spanish, so its output is
byte-identical to the build when the game is the same version. Font files are not copied either: the data
names the fontpatch variant, which the patcher applies to the player's own font.

The file also carries SHA-256 digests of the official source files and of the expected outputs (digests only),
so the patcher can tell "same game version as tested" from "updated game".

Output: dist/data/traduccion_es.json.gz (gzip, UTF-8 JSON)
  {"schema": 1, "name", "version", "game": {"appid", "buildid"}, "lang_slot": 1,
   "files": [{"path", "src_sha256", "out_sha256", "entries": [[guid, en_fp16, es], ...]}],
   "fonts": [{"path", "variant", "src_sha256", "out_sha256"}]}

CLI: run.py patchdata --build release_v1 --version 1.0 [--out FILE]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from . import config, fontpatch, msg23
from .pak import PakSet

APPID = "3500390"


def en_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def game_buildid(game_dir: Path) -> str | None:
    acf = game_dir.parent.parent / f"appmanifest_{APPID}.acf"
    if not acf.exists():
        return None
    for line in acf.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = [p for p in line.strip().split('"') if p.strip()]
        if len(parts) == 2 and parts[0] == "buildid":
            return parts[1]
    return None


def export(build_name: str, version: str, ps: PakSet | None = None) -> dict:
    ps = ps or PakSet()
    root = config.BUILD / build_name
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    variant = manifest["options"].get("fonts")
    font_paths = set(fontpatch.outputs(variant, ps)) if variant else set()
    natives = root / "natives"
    files, fonts = [], []
    for path in sorted(p for p in natives.rglob("*") if p.is_file()):
        rel = "natives/" + path.relative_to(natives).as_posix()
        built = path.read_bytes()
        source = ps.read(rel)
        if rel in font_paths:
            fonts.append({"path": rel, "variant": variant, "src_sha256": sha256(source), "out_sha256": sha256(built)})
            continue
        if not rel.endswith(".msg.23"):
            raise ValueError(f"unexpected output file {rel}")
        a, b = msg23.parse(source), msg23.parse(built)
        if [e.guid for e in a.entries] != [e.guid for e in b.entries]:
            raise ValueError(f"{rel}: entry structure differs from the source")
        slot = a.slot(config.LANG_EN)
        entries = []
        for ea, eb in zip(a.entries, b.entries):
            for k, (ta, tb) in enumerate(zip(ea.texts, eb.texts)):
                if k != slot and ta != tb:
                    raise ValueError(f"{rel}: slot {k} changed in {ea.name}")
            if ea.texts[slot] != eb.texts[slot]:
                entries.append([ea.guid, en_fingerprint(ea.texts[slot]), eb.texts[slot]])
        files.append({"path": rel, "src_sha256": sha256(source), "out_sha256": sha256(built), "entries": entries})
    return {"schema": 1, "name": "Mega Man Star Force Legacy Collection: traducción al español", "version": version,
            "game": {"appid": APPID, "buildid": game_buildid(ps.game_dir)}, "lang_slot": config.LANG_EN,
            "files": files, "fonts": fonts}


def write(data: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    out.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="patchdata", description=__doc__.split("\n")[0])
    ap.add_argument("--build", required=True, help="build folder name under build/")
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", type=Path, default=config.DIST / "data" / "traduccion_es.json.gz")
    a = ap.parse_args(argv)
    data = export(a.build, a.version)
    write(data, a.out)
    n = sum(len(f["entries"]) for f in data["files"])
    print(f"patch data {a.out}: {len(data['files'])} msg files, {n} entries, {len(data['fonts'])} font file(s), "
          f"game build {data['game']['buildid']}, {a.out.stat().st_size} bytes")
    return 0
