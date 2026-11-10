r"""End-to-end test of the distributable patcher, run with the package's own embedded Python:

  dist\<NAME>\programa\python\python.exe -I -B -X utf8 C:\StarForceES\tools\patcher_test.py <dist folder> <build folder>

1. generate() from the installed game (read-only) must reproduce build/<name>/natives byte for byte.
2. install() into a scratch "game folder" (scratch dir, not the real game) that already holds one foreign
   loose file and one foreign mod file must back up the foreign file, write every file and a complete manifest.
3. uninstall() must remove exactly our files, restore the foreign file, keep the unrelated file, and leave the
   folder as it was before (same files, same bytes).
4. A file changed after installation must be left alone by uninstall(), with its backup kept.
5. A manifest with a path outside natives/ is refused.
"""
import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

dist, build = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(dist / "programa"))
from sfes import parche  # noqa: E402

GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\MegaMan StarForce LegacyCollection")
results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""), flush=True)


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


data = parche.load_data(dist / "programa" / "datos" / "traduccion_es.json.gz")
files, rep = parche.generate(GAME, data)
built = {"natives/" + p.relative_to(build / "natives").as_posix(): p.read_bytes()
         for p in (build / "natives").rglob("*") if p.is_file()}
same = sum(1 for k, v in built.items() if files.get(k) == v)
check("generate reproduces the build byte for byte", same == len(built) == len(files),
      f"{same}/{len(built)} identical, {len(files)} generated")
check("generate report", rep.entries == sum(len(f["entries"]) for f in data["files"]) and not rep.entries_stale
      and rep.font_as_tested is True and not rep.files_missing,
      f"entries {rep.entries}, stale {rep.entries_stale}, font as tested {rep.font_as_tested}")

scratch = Path(tempfile.mkdtemp(prefix="sfes_install_test_"))
try:
    some = sorted(files)[5]
    foreign = scratch.joinpath(*some.split("/"))
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"another mod")
    unrelated = scratch / "natives" / "stm" / "othermod" / "x.txt"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"keep me")
    (scratch / "re2_fw_config.txt").write_text("Foo=1\nLooseFileLoader_Enabled=false\n", encoding="utf-8")
    before = snapshot(scratch)

    ir = parche.install(scratch, data, files, rep)
    m = parche.read_manifest(scratch)
    check("install writes every file and a complete manifest",
          ir.written == len(files) and m["complete"] and len(m["files"]) == len(files)
          and all(scratch.joinpath(*k.split("/")).read_bytes() == v for k, v in files.items()),
          f"written {ir.written}, backed up {ir.backed_up}")
    check("foreign file backed up", ir.backed_up == 1 and
          (scratch / parche.STATE_DIR / parche.BACKUP_DIR).joinpath(*some.split("/")).read_bytes() == b"another mod")
    st = parche.reframework_state(scratch)
    parche.enable_loose_loader(scratch)
    st2 = parche.reframework_state(scratch)
    check("loose loader switch", st.loose_enabled is False and st2.loose_enabled is True
          and "Foo=1" in (scratch / "re2_fw_config.txt").read_text(encoding="utf-8"))
    (scratch / "re2_fw_config.txt").write_text("Foo=1\nLooseFileLoader_Enabled=false\n", encoding="utf-8")

    ur = parche.uninstall(scratch)
    after = snapshot(scratch)
    check("uninstall restores the folder exactly", after == before and ur.removed == len(files) and ur.restored == 1
          and ur.state_removed, f"removed {ur.removed}, restored {ur.restored}, diff {set(after) ^ set(before)}")

    ir = parche.install(scratch, data, files, rep)
    changed = scratch.joinpath(*sorted(files)[10].split("/"))
    changed.write_bytes(b"changed by someone else")
    ur = parche.uninstall(scratch)
    check("a file changed after install is left alone", changed.read_bytes() == b"changed by someone else"
          and ur.kept_changed == [sorted(files)[10]] and ur.removed == len(files) - 1)
    changed.unlink()
    shutil.rmtree(scratch / parche.STATE_DIR, ignore_errors=True)

    bad = {"schema": 1, "files": [{"path": "../evil.txt", "sha256": "0", "backup": None}], "dirs": []}
    parche._write_manifest(scratch, bad)
    try:
        parche.uninstall(scratch)
        refused = False
    except parche.ParcheError:
        refused = True
    check("manifest path outside natives/ is refused", refused)

    # --- review fixes -------------------------------------------------------------------------------------
    import subprocess
    import stat as _stat

    class _Out:
        def __init__(self, b):
            self.stdout = b

    real_run = subprocess.run
    try:
        subprocess.run = lambda *a, **k: _Out("INFORMACIÓN: no hay tareas ejecutándose.".encode("cp850"))
        not_running = parche.game_running() is False
        subprocess.run = lambda *a, **k: _Out(b'"MEGAMAN_STARFORCE_Legacy_Collection.exe","1234","Console"')
        running = parche.game_running() is True
        subprocess.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        fail_open = parche.game_running() is False
    finally:
        subprocess.run = real_run
    check("game_running: non-UTF-8 tasklist output, match, and failure", not_running and running and fail_open)

    shutil.rmtree(scratch / parche.STATE_DIR, ignore_errors=True)  # the tampered manifest of the last check
    parche.install(scratch, data, files, rep)
    shutil.rmtree(scratch / parche.STATE_DIR)                      # manifest lost
    ir = parche.install(scratch, data, files, rep)
    check("lost manifest: reinstall recognises the patch's own files", ir.backed_up == 0)
    parche.manifest_path(scratch).write_bytes(b"\0" * 64)          # damaged manifest
    (scratch / parche.STATE_DIR / (parche.MANIFEST + ".bak")).unlink(missing_ok=True)
    try:
        parche.uninstall(scratch)
        damaged_refused = False
    except parche.ParcheError:
        damaged_refused = True
    ir = parche.install(scratch, data, files, rep)
    check("damaged manifest: uninstall explains, install repairs", damaged_refused and ir.backed_up == 0
          and parche.read_manifest(scratch)["complete"])
    parche.uninstall(scratch)
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_bytes(b"another mod")        # its backup was deleted with the "lost manifest" on purpose

    target = sorted(files)[20]
    tpath = scratch.joinpath(*target.split("/"))
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_bytes(b"mod A")
    parche.install(scratch, data, files, rep)                      # backup A
    tpath.write_bytes(b"mod B")                                    # another mod replaces our file
    ur1 = parche.uninstall(scratch)
    residual = parche.read_manifest(scratch)
    ir = parche.install(scratch, data, files, rep)                 # B must get its own backup; A's is kept
    ur2 = parche.uninstall(scratch)
    backups = sorted(x.name for x in (scratch / parche.STATE_DIR / parche.BACKUP_DIR).rglob("*") if x.is_file())
    check("backups are never overwritten", ur1.kept_changed == [target] and residual and residual.get("residual")
          and ir.backed_up == 2 and tpath.read_bytes() == b"mod B" and ur2.leftover_backups == 1,
          f"backups left {backups}")
    shutil.rmtree(scratch / parche.STATE_DIR)
    tpath.unlink()

    parche.install(scratch, data, files, rep)
    tpath.write_bytes(b"changed")
    parche.uninstall(scratch)                                      # kept, stays in the residual manifest
    tpath.write_bytes(files[target])                               # our content is back (e.g. a manager restored it)
    ur = parche.uninstall(scratch)
    check("residual manifest: a later uninstall removes the file once it is ours again",
          ur.removed == 1 and not tpath.exists() and ur.state_removed)

    parche.install(scratch, data, files, rep)
    ro = scratch.joinpath(*sorted(files)[30].split("/"))
    os_chmod_ok = True
    try:
        import os
        os.chmod(ro, _stat.S_IREAD)
    except OSError:
        os_chmod_ok = False
    ur = parche.uninstall(scratch)
    check("read-only files are removed by uninstall", os_chmod_ok and not ro.exists() and ur.state_removed)
    check("uninstall restores the folder exactly after all of that", snapshot(scratch) == before,
          str(set(snapshot(scratch)) ^ set(before)))

    import importlib.util
    spec = importlib.util.spec_from_file_location("instalador", dist / "programa" / "instalador.py")
    inst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inst)
    log = dist / "registro.txt"
    had_log = log.exists()
    rc = inst.main(["comprobar", str(GAME)])
    check("comprobar (game folder as positional argument) writes nothing", rc == 0 and log.exists() == had_log)
    inst._write_log = False
    rc = inst.main(["instalar", str(scratch / "not_a_game"), "--si"])
    check("a folder that is not the game is refused with a message", rc == 2)

    out_zip = scratch / "fluffy.zip"
    parche.fluffy_zip(out_zip, data, files, "test")
    import zipfile
    with zipfile.ZipFile(out_zip) as z:
        names = z.namelist()
    check("Fluffy zip has modinfo.ini and every file", "modinfo.ini" in names and len(names) == len(files) + 1)
finally:
    shutil.rmtree(scratch, ignore_errors=True)

print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
