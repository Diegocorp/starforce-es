r"""Tests for sfes.probe: whether the real spec builds; the variant paks (marker, exactly the expected msg and
font files, every item's final text in the English slot, every other entry and slot identical to the source);
the computed #24 / #25 lines (widths re-measured independently); determinism; the manifest and checklist; the
.bat files (ASCII, CRLF, pak name, default folder, no CALL with a path) and their behaviour under cmd.exe
(install and uninstall of each variant, refusals: second install, unknown file, missing official paks or
folder, the probe folder or a folder inside it given as the game folder, a bare variant letter for uninstall,
wildcards; access denied; ^ % & ! and other characters in the game path); spec refusals; output folders
outside dist\probe and the scratch folder refused; CLI exit codes; the real work files left untouched.

real_spec_builds checks the real spec; the other checks build the probe from an in-memory copy of it with
TEST_SPEC_FIXES applied (empty while the real spec is sound). The .bat files are run only against fake game folders under
dist\_test_probe\ whose paths hold spaces and parentheses, and they are generated with a fake default game
folder, so the real game folder is never touched (run_bat refuses any path outside the scratch folder).
Everything is written under dist\_test_probe\ (removed at the end); the real work files and the spec are only
read.
Run: C:\StarForceES\.venv\Scripts\python.exe -I -X utf8 C:\StarForceES\tools\run.py tests.test_probe
"""
from __future__ import annotations

import copy
import hashlib
import io
import os
import re
import shutil
import subprocess
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stdout
from functools import cached_property
from pathlib import Path

from fontTools.ttLib import TTFont

from sfes import config, extract, fontpatch, msg23, oft, probe, profiles, wrap
from sfes.inserts import InsertResolver
from sfes.metrics import Metrics
from sfes.pak import Pak, PakSet, path_hash

SCRATCH = probe.SCRATCH_DIR
PROBE_OUT = SCRATCH / "probe (1)"
FAKE_GAME = SCRATCH / "fake (x86)" / "Steam game"
FAKE_DEFAULT = SCRATCH / "default (x86)" / "MegaMan StarForce LegacyCollection"
EVERYONE = "*S-1-1-0"
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
DIALOGUE = "natives/stm/text/rr1/message/eng/rr1_dm0001_eng.msg.23"
SETTINGS = "natives/stm/text/common/common_msggui00030000.msg.23"
# Corrections applied to an in-memory copy of the real spec (label -> fields), for when the real spec is
# broken and the other checks should still run. Empty: the real spec builds.
TEST_SPEC_FIXES: dict[int, dict[str, str]] = {}


class Ctx:
    """Shared data, built on first use."""

    @cached_property
    def ps(self) -> PakSet:
        return PakSet()

    @cached_property
    def metrics(self) -> Metrics:
        return Metrics.load(paks=self.ps)

    @cached_property
    def work_hashes(self) -> dict[str, str]:
        return _tree_hashes(config.STRINGS)

    @cached_property
    def real_spec(self) -> dict:
        return probe.load_spec()

    @cached_property
    def real_problems(self) -> list[str]:
        try:
            probe.plan_probe(self.real_spec, ps=self.ps)
        except probe.ProbeError as exc:
            return exc.problems
        return []

    @cached_property
    def spec(self) -> dict:
        """The real spec with TEST_SPEC_FIXES applied (in memory)."""
        spec = copy.deepcopy(self.real_spec)
        for item in spec["items"]:
            item.update(TEST_SPEC_FIXES.get(item["n"], {}))
        return spec

    @cached_property
    def result(self) -> probe.ProbeResult:
        return probe.build_probe(PROBE_OUT, spec=self.spec, game_dir=FAKE_DEFAULT)

    @cached_property
    def pak_name(self) -> str:
        return config.PATCH_FMT.format(self.ps.next_free_index())

    def source(self, path: str) -> msg23.Msg:
        return msg23.parse(self.ps.read(path))

    @cached_property
    def variant_metrics(self) -> dict[str, Metrics]:
        """Metrics of every variant, measured with its font files (A: the official fonts)."""
        return {v: Metrics.load(overrides=fontpatch.outputs(f, self.ps), paks=self.ps) if f else self.metrics
                for v, f in probe.VARIANT_FONTS.items()}


CHECKS: list[Callable[[Ctx], None]] = []


def check(fn: Callable[[Ctx], None]) -> Callable[[Ctx], None]:
    CHECKS.append(fn)
    return fn


# ------------------------------------------------------------------------------------------- helpers

def _tree_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _entry_index(msg: msg23.Msg, item: dict) -> int:
    """The entry an item names, found independently of sfes.probe (exactly one must match)."""
    if "entry" in item:
        hits = [k for k, e in enumerate(msg.entries) if e.name == item["entry"]]
    else:
        hits = [k for k, e in enumerate(msg.entries) if msg.get(e, config.LANG_EN) == item["match_en"]]
    assert len(hits) == 1, (item, hits)
    return hits[0]


def _pak_text(pak: Pak, path: str) -> bytes:
    return pak.read(pak.by_hash[path_hash(path)])


def _expected_text(ctx: Ctx, variant: str, item: dict, en: str, computed: dict) -> str:
    """The text the probe must write for an item, computed here without sfes.probe."""
    if "computed" in item:
        return computed[item["computed"]]["text"]
    if item["wrap"] == "manual":
        return wrap.normalize_breaks(item["es"])
    metrics = ctx.variant_metrics[variant]
    return wrap.final_text(item["es"], en, profile=profiles.profile_for(item["file"]), path=item["file"],
                           metrics=metrics, resolver=InsertResolver(metrics, paks=ctx.ps, english_only=True),
                           wrap_mode="auto").text


def _bo_space_em(ctx: Ctx) -> float:
    """BO-TBGoStd-Medium's own space (x-scale of slot 0 included), read here with fontTools."""
    bo = next(f for f in ctx.metrics.chain(0) if f.name == "BO-TBGoStd-Medium")
    font = TTFont(io.BytesIO(oft.decode(ctx.ps.read(bo.path))), lazy=True)
    return font["hmtx"][font.getBestCmap()[0x20]][0] / font["head"].unitsPerEm * bo.x_scale


def _inside_scratch(path: Path) -> bool:
    scratch = SCRATCH.resolve()
    path = path.resolve()
    return path == scratch or scratch in path.parents


def _guard(paths: list[str], cwd: Path) -> None:
    """Refuse to run a .bat file with any path, or from a folder, outside the scratch folder (never the real game
    folder); a relative argument resolves against cwd."""
    for p in paths:
        if not _inside_scratch(cwd / p):
            raise AssertionError(f"refusing to run a .bat file with a path outside {SCRATCH}: {p}")


def run_bat(name: str, *args: str, cwd: Path = SCRATCH) -> tuple[int, str]:
    """Run one of the generated .bat files of PROBE_OUT with cmd.exe from cwd; (exit code, output)."""
    bat = PROBE_OUT / name
    _guard([str(cwd), str(bat), *args], cwd)
    assert str(config.GAME_DIR).lower() not in bat.read_text("ascii").lower()
    line = " ".join(f'"{a}"' for a in (str(bat), *args))
    env = {k: v for k, v in os.environ.items() if k.upper() != "PAK"}     # the command line must not expand %PAK%
    proc = subprocess.run(f'"{SYSTEM32 / "cmd.exe"}" /d /v:off /s /c "{line}"', stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=120, cwd=str(cwd), env=env)
    return proc.returncode, (proc.stdout + proc.stderr).decode("ascii", "replace")


def _fake_game(ctx: Ctx, folder: Path, official: tuple[str, ...] | None = None) -> Path:
    """A fake game folder with small stand-ins for the official paks."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for name in ctx.result.manifest["official_paks"] if official is None else official:
        (folder / name).write_bytes(f"fake {name}\n".encode("ascii"))
    return folder


def _icacls(path: Path, *args: str) -> None:
    subprocess.run([str(SYSTEM32 / "icacls.exe"), str(path), *args], check=True, capture_output=True, timeout=60)


@contextmanager
def _denied(path: Path, rights: str) -> Iterator[None]:
    """Deny Everyone `rights` on path for the duration of the block."""
    _icacls(path, "/deny", f"{EVERYONE}:({rights})")
    try:
        yield
    finally:
        _icacls(path, "/remove:d", EVERYONE)


def _expect(code: int, out: str, want_code: int, *phrases: str) -> None:
    assert code == want_code, (code, out)
    for phrase in phrases:
        assert phrase in out, (phrase, out)


def _plan_problems(spec: dict, ctx: Ctx, variants: tuple[str, ...] = ("A",)) -> list[str]:
    try:
        probe.plan_probe(spec, variants, ps=ctx.ps)
    except probe.ProbeError as exc:
        return exc.problems
    raise AssertionError("the spec was accepted")


# ------------------------------------------------------------------------------------------- checks

@check
def work_files_recorded(ctx: Ctx) -> None:
    assert len(ctx.work_hashes) >= 961, len(ctx.work_hashes)


@check
def real_spec_builds(ctx: Ctx) -> None:
    assert not ctx.real_problems, "the real spec does not build:\n  " + "\n  ".join(ctx.real_problems)


@check
def pick_words_finds_closest_subsequence(ctx: Ctx) -> None:
    widths = [10.0, 21.0, 32.0, 5.0, 17.0]
    assert probe.pick_words(widths, {2: 26.0}) == (1, 3)                # 21 + 5, exact
    assert probe.pick_words(widths, {1: 30.0, 3: 30.0}) == (2,)          # 32 and 10 + 5 + 17 tie: fewer words
    assert probe.pick_words(widths, {3: 48.0}) == (0, 1, 4)              # 10 + 21 + 17, the only exact triple
    assert probe.pick_words(widths, {3: 48.0}) == (0, 1, 4)              # deterministic
    assert probe.pick_words(widths, {9: 10.0, 1: 4.0}) == (3,)           # impossible counts are skipped


@check
def paks_hold_marker_and_exactly_the_expected_files(ctx: Ctx) -> None:
    res = ctx.result
    item_files = {i["file"] for i in ctx.spec["items"]}
    assert res.pak == ctx.pak_name and set(res.paks) == set(probe.VARIANT_FONTS)
    for variant, path in res.paks.items():
        assert path == PROBE_OUT / variant / ctx.pak_name, path
        pak = Pak(path)
        fonts = fontpatch.outputs(probe.VARIANT_FONTS[variant], ctx.ps) if probe.VARIANT_FONTS[variant] else {}
        expected = item_files | set(fonts) | {config.MARKER_PATH}
        assert pak.is_sfes and pak.total == len(expected), (variant, pak.total, len(expected))
        assert set(pak.by_hash) == {path_hash(p) for p in expected}, variant
        marker = _pak_text(pak, config.MARKER_PATH).decode("utf-8")
        assert marker == probe.PROBE_MARKER.format(variant=variant), marker
        for font_path, data in fonts.items():
            assert _pak_text(pak, font_path) == data, (variant, font_path)
        for p in expected - {config.MARKER_PATH}:
            official = ctx.ps.entry(p) or ctx.ps.entry(fontpatch.RR_PATH)        # a new file copies RR_System_Font
            entry = pak.by_hash[path_hash(p)]
            assert entry.attributes == (official.attributes & ~0xFF000F) | 1 and entry.checksum == 0, (variant, p)
        assert pak.fingerprint == ctx.ps.paks[-1].fingerprint, variant
        for p in item_files:
            msg23.parse(_pak_text(pak, p))                                  # every msg file reads


@check
def items_hold_their_final_text_and_nothing_else_changes(ctx: Ctx) -> None:
    computed = ctx.result.manifest["computed"]
    manifest_items = ctx.result.manifest["items"]
    assert len(manifest_items) == len(ctx.spec["items"])
    for variant, path in ctx.result.paks.items():
        pak = Pak(path)
        changed: dict[str, set[int]] = {}
        for item, row in zip(ctx.spec["items"], manifest_items):
            source = ctx.source(item["file"])
            k = _entry_index(source, item)
            changed.setdefault(item["file"], set()).add(k)
            new = msg23.parse(_pak_text(pak, item["file"]))
            text = new.get(new.entries[k], config.LANG_EN)
            want = _expected_text(ctx, variant, item, source.get(source.entries[k], config.LANG_EN), computed)
            assert text == want, (variant, item["n"], text, want)
            assert row["final_text"] == text and row["entry"] == source.entries[k].name, (variant, item["n"])
        for file, indexes in changed.items():
            old, new = ctx.source(file), msg23.parse(_pak_text(pak, file))
            assert [(e.name, e.guid, e.attributes) for e in old.entries] == \
                   [(e.name, e.guid, e.attributes) for e in new.entries], file
            en = old.slot(config.LANG_EN)
            for k, (a, b) in enumerate(zip(old.entries, new.entries)):
                for s, (x, y) in enumerate(zip(a.texts, b.texts)):
                    assert x == y or (s == en and k in indexes), (variant, file, a.name, s)


@check
def computed_lines_are_in_range(ctx: Ctx) -> None:
    computed = ctx.result.manifest["computed"]
    cal, over = computed[probe.SPACE_CALIBRATION], computed[probe.OVERLONG_LINE]
    resolver = InsertResolver(ctx.metrics, paks=ctx.ps, english_only=True)
    rr_space = ctx.metrics.advance_em(" ", 0)
    assert ctx.metrics.font_of(" ", 0) == "RR_System_Font" and abs(rr_space - 0.42285) < 1e-4, rr_space
    bo_space = _bo_space_em(ctx)
    for c, label in ((cal, "#24 "), (over, "#25 ")):
        text = c["text"]
        assert text.startswith(label) and "\r" not in text and "\n" not in text, text
        assert re.fullmatch(r"#\d+( [a-z]+)+", text), text                      # plain words, no accents
        assert c["spaces"] == text.count(" ")
        wide = ctx.metrics.line_px(text, 0, 60.0, resolver)
        narrow = wide - c["spaces"] * (rr_space - bo_space) * 60.0
        assert abs(wide - c["width_px"]["wide_space"]) < 0.01 and abs(narrow - c["width_px"]["narrow_space"]) < 0.01
        print(f"  {text!r}: {c['spaces']} spaces, {narrow:.2f} px narrow space, {wide:.2f} px wide space")
    assert abs(cal["width_px"]["narrow_space"] - 990) <= 15 and abs(cal["width_px"]["wide_space"] - 1070) <= 15, cal
    assert cal["width_px"]["narrow_space"] <= 1026 - 20 and cal["width_px"]["wide_space"] >= 1026 + 20, cal
    assert abs(over["width_px"]["wide_space"] - 1300) <= 20, over
    assert cal["space"]["narrow"]["font"] == "BO-TBGoStd-Medium" and cal["space"]["wide"]["font"] == "RR_System_Font"


@check
def manifest_matches_the_files(ctx: Ctx) -> None:
    body = extract.load_json(PROBE_OUT / probe.MANIFEST_NAME)
    assert body == ctx.result.manifest and body["pak_name"] == ctx.pak_name
    assert body["official_paks"][0] == config.BASE_PAK and "re_chunk_000.pak.patch_002.pak" in body["official_paks"]
    for variant, info in body["variants"].items():
        data = (PROBE_OUT / info["pak"]).read_bytes()
        assert info["sha256"] == hashlib.sha256(data).hexdigest() and info["size"] == len(data), variant
        print(f"  test build {info['pak']}: {info['size']} bytes, sha256 {info['sha256']}")
        pak = Pak(PROBE_OUT / info["pak"])
        for f in info["files"]:
            assert hashlib.sha256(_pak_text(pak, f["path"])).hexdigest() == f["sha256"], (variant, f["path"])
    for row in body["items"]:
        assert set(row["widths"]) == set(probe.VARIANT_FONTS) and row["lines"], row["n"]
        if row["profile"].startswith("dialogue"):
            assert all(sorted(o) == row["deliberate_overflow"] for o in row["overflow"].values()), row["n"]
    assert {r["n"]: r["deliberate_overflow"] for r in body["items"] if r["deliberate_overflow"]} == \
           {23: ["lines"], 24: ["width"], 25: ["width"]}


@check
def outputs_are_deterministic(ctx: Ctx) -> None:
    assert ctx.result.out_dir == PROBE_OUT.resolve()
    first = _tree_bytes(PROBE_OUT)
    probe.build_probe(PROBE_OUT, spec=ctx.spec, game_dir=FAKE_DEFAULT)
    second = _tree_bytes(PROBE_OUT)
    assert sorted(first) == sorted(second), (sorted(first), sorted(second))
    assert [k for k in first if first[k] != second[k]] == []
    expected = {f"{v}/{ctx.pak_name}" for v in probe.VARIANT_FONTS} | {
        probe.MANIFEST_NAME, probe.INSTALL_NAME, probe.UNINSTALL_NAME, probe.CHECKLIST_NAME}
    assert set(second) == expected, sorted(second)


@check
def bat_files_are_ascii_crlf_and_name_the_pak(ctx: Ctx) -> None:
    plan_official = tuple(ctx.result.manifest["official_paks"])
    real = {probe.INSTALL_NAME: probe.install_bat(ctx.pak_name, tuple(probe.VARIANT_FONTS), plan_official,
                                                  config.GAME_DIR),
            probe.UNINSTALL_NAME: probe.uninstall_bat(ctx.pak_name, tuple(probe.VARIANT_FONTS), plan_official,
                                                      config.GAME_DIR)}
    for name in (probe.INSTALL_NAME, probe.UNINSTALL_NAME):
        for data, default in ((real[name], config.GAME_DIR), ((PROBE_OUT / name).read_bytes(), FAKE_DEFAULT)):
            assert data.isascii(), name
            assert data.count(b"\n") == data.count(b"\r\n") and data.count(b"\r") == data.count(b"\r\n"), name
            assert data.startswith(b"@echo off\r\nsetlocal EnableExtensions DisableDelayedExpansion\r\n"), name
            assert data.endswith(b"\r\n"), name
            text = data.decode("ascii")
            assert f'set "PAK={ctx.pak_name}"' in text and f'set "DEFAULT_GAME={default}"' in text, name
            for v in probe.VARIANT_FONTS:
                assert f"call :match_variant {v}" in text, (name, v)
            for official in plan_official:                     # both files require every official pak
                assert f'if not exist "%GAME%\\{official}" set "MISSING=%MISSING% {official}"' in text, official
            assert 'if /i "%UP%"=="%PROBE%" goto in_probe' in text, name
    assert "re_chunk_000.pak.patch_002.pak" in plan_official and ctx.pak_name not in plan_official


@check
def bat_lines_never_expand_the_game_path_in_a_block(ctx: Ctx) -> None:
    for name in (probe.INSTALL_NAME, probe.UNINSTALL_NAME):
        for line in (PROBE_OUT / name).read_text("ascii").splitlines():
            stripped = line.strip()
            if stripped.lower().startswith(("rem ", "echo ")) and "(" in stripped:
                raise AssertionError(f"{name}: parenthesis in {stripped!r}")
            assert not re.match(r"(?i)(if|for)\b.*\(\s*$", stripped), (name, stripped)       # no blocks at all
            assert not re.match(r"(?i)call\b.*%", stripped), (name, stripped)               # no CALL with a value
            for var in ("GAME", "TARGET", "SOURCE", "PROBE", "CANDIDATE", "DEFAULT_GAME", "UP", "PARENT"):
                for m in re.finditer(f"%{var}%", stripped):
                    before = stripped[:m.start()]
                    assert before.count('"') % 2 == 1, (name, stripped)                       # always quoted
                    if stripped.lower().startswith("for "):                                  # one quoted list item
                        assert re.match(r'(?i)for %%\w in \("[^"]*"\) do ', stripped), (name, stripped)


@check
def install_and_uninstall_each_variant(ctx: Ctx) -> None:
    game = _fake_game(ctx, FAKE_GAME)
    before = _tree_hashes(game)
    target = game / ctx.pak_name
    for variant in probe.VARIANT_FONTS:
        code, out = run_bat(probe.INSTALL_NAME, variant, str(game))
        _expect(code, out, 0, f"Probe variant {variant} is installed", "PROBE_CHECKLIST.md")
        assert target.read_bytes() == ctx.result.paks[variant].read_bytes(), variant
        assert _tree_hashes(game) == {**before, ctx.pak_name: hashlib.sha256(target.read_bytes()).hexdigest()}
        code, out = run_bat(probe.UNINSTALL_NAME, str(game))
        _expect(code, out, 0, f"Probe variant {variant} was removed")
        assert not target.exists() and _tree_hashes(game) == before, variant
    code, out = run_bat(probe.INSTALL_NAME, "b", str(game) + "\\")              # lower case, trailing backslash
    _expect(code, out, 0, "Probe variant B is installed")
    code, out = run_bat(probe.UNINSTALL_NAME, str(game) + "\\")
    _expect(code, out, 0, "Probe variant B was removed")
    code, out = run_bat(probe.UNINSTALL_NAME, str(game))
    _expect(code, out, 0, "No probe is installed")
    assert _tree_hashes(game) == before


@check
def second_install_is_refused(ctx: Ctx) -> None:
    game = _fake_game(ctx, FAKE_GAME)
    target = game / ctx.pak_name
    _expect(*run_bat(probe.INSTALL_NAME, "B", str(game)), 0)
    for variant in ("C", "B"):
        code, out = run_bat(probe.INSTALL_NAME, variant, str(game))
        _expect(code, out, 1, "Probe variant B is already installed", "uninstall_probe.bat first")
        assert target.read_bytes() == ctx.result.paks["B"].read_bytes()
    _expect(*run_bat(probe.UNINSTALL_NAME, str(game)), 0, "Probe variant B was removed")


@check
def unknown_file_is_refused(ctx: Ctx) -> None:
    game = _fake_game(ctx, FAKE_GAME)
    target = game / ctx.pak_name
    same_size = bytearray(ctx.result.paks["B"].read_bytes())
    same_size[-1] ^= 0xFF
    for data in (b"an official update\n", bytes(same_size)):
        target.write_bytes(data)
        code, out = run_bat(probe.INSTALL_NAME, "B", str(game))
        _expect(code, out, 1, "not", "one of the probe paks", "official game update")
        code, out = run_bat(probe.UNINSTALL_NAME, str(game))
        _expect(code, out, 1, "NOT deleted")
        assert target.read_bytes() == data


@check
def missing_official_files_or_folder_are_refused(ctx: Ctx) -> None:
    official = tuple(ctx.result.manifest["official_paks"])
    for missing in ("re_chunk_000.pak.patch_002.pak", config.BASE_PAK):
        game = _fake_game(ctx, FAKE_GAME, tuple(p for p in official if p != missing))
        code, out = run_bat(probe.INSTALL_NAME, "B", str(game))
        _expect(code, out, 1, f"missing: {missing}", "Nothing was changed")
        assert not (game / ctx.pak_name).exists()
        shutil.copyfile(ctx.result.paks["C"], game / ctx.pak_name)          # a probe pak, but not the game folder
        code, out = run_bat(probe.UNINSTALL_NAME, str(game))
        _expect(code, out, 1, "does not look like the game folder", f"missing: {missing}", "Nothing was changed")
        assert (game / ctx.pak_name).read_bytes() == ctx.result.paks["C"].read_bytes()
    absent = SCRATCH / "no such (x86)" / "Steam game"
    _expect(*run_bat(probe.INSTALL_NAME, "B", str(absent)), 1, "Game folder not found")
    _expect(*run_bat(probe.UNINSTALL_NAME, str(absent)), 1, "Game folder not found")
    assert not absent.exists()


@check
def probe_folder_is_never_taken_for_the_game(ctx: Ctx) -> None:
    """The slips 'uninstall_probe.bat B' or '.\\B' from the probe folder (which name the probe's own variant
    folder) are refused, even when that folder also holds copies of the official paks."""
    before = _tree_hashes(PROBE_OUT)
    for args in (("B",), ("b",)):
        _expect(*run_bat(probe.UNINSTALL_NAME, *args, cwd=PROBE_OUT), 1, "takes no variant letter")
    for args in ((".\\B",), (".",), (str(PROBE_OUT / "A"),), (str(PROBE_OUT / "D") + "\\",)):
        _expect(*run_bat(probe.UNINSTALL_NAME, *args, cwd=PROBE_OUT), 1, "probe folder or a folder inside it")
    for args in (("B", "B"), ("B", "."), ("C", str(PROBE_OUT / "A"))):
        _expect(*run_bat(probe.INSTALL_NAME, *args, cwd=PROBE_OUT), 1, "probe folder or a folder inside it")
    assert _tree_hashes(PROBE_OUT) == before
    deep = PROBE_OUT / "B" / "deep (x86)" / "game"
    added = [PROBE_OUT / "B" / p for p in ctx.result.manifest["official_paks"]]
    try:
        _fake_game(ctx, deep)
        shutil.copyfile(ctx.result.paks["B"], deep / ctx.pak_name)
        for p in added:
            p.write_bytes(b"fake official pak\n")
        for folder in (".\\B", str(deep)):
            _expect(*run_bat(probe.UNINSTALL_NAME, folder, cwd=PROBE_OUT), 1, "probe folder or a folder inside it")
        assert (deep / ctx.pak_name).read_bytes() == ctx.result.paks["B"].read_bytes()
    finally:
        shutil.rmtree(PROBE_OUT / "B" / "deep (x86)", ignore_errors=True)
        for p in added:
            p.unlink(missing_ok=True)
    assert _tree_hashes(PROBE_OUT) == before


@check
def wildcards_in_the_game_folder_are_refused(ctx: Ctx) -> None:
    game = _fake_game(ctx, FAKE_GAME)
    _expect(*run_bat(probe.INSTALL_NAME, "B", str(game)), 0)
    for pattern in (str(game)[:-1] + "*", str(game)[:-1] + "?", str(game.parent) + "\\*"):
        _expect(*run_bat(probe.UNINSTALL_NAME, pattern), 1)
        _expect(*run_bat(probe.INSTALL_NAME, "C", pattern), 1)
        assert (game / ctx.pak_name).read_bytes() == ctx.result.paks["B"].read_bytes(), pattern
    _expect(*run_bat(probe.UNINSTALL_NAME, str(game)), 0, "Probe variant B was removed")


@check
def game_paths_with_special_characters_work(ctx: Ctx) -> None:
    """Carets, percent signs, a variable name, & ; , = ! and ' in the game path: install, second install and
    uninstall behave exactly as with a plain path."""
    for name in ("caret ^ (x86)\\game", "pct 100% (x86)\\game", "pct %PAK% (x86)\\game",
                 "a&b (x86)\\x;y,z=w game", "bang! (x86)\\it's game"):
        game = _fake_game(ctx, SCRATCH / name)
        before = _tree_hashes(game)
        _expect(*run_bat(probe.INSTALL_NAME, "B", str(game)), 0, "Probe variant B is installed")
        assert (game / ctx.pak_name).read_bytes() == ctx.result.paks["B"].read_bytes(), name
        _expect(*run_bat(probe.INSTALL_NAME, "C", str(game)), 1, "Probe variant B is already installed")
        _expect(*run_bat(probe.UNINSTALL_NAME, str(game)), 0, "Probe variant B was removed")
        assert _tree_hashes(game) == before, name


@check
def usage_errors_are_refused(ctx: Ctx) -> None:
    game = _fake_game(ctx, FAKE_GAME)
    for args in ((), ("E", str(game)), ("B", str(game), "extra")):
        _expect(*run_bat(probe.INSTALL_NAME, *args), 1, "Usage: install_probe.bat")
    for args in ((str(game), "extra"), ("D",)):
        _expect(*run_bat(probe.UNINSTALL_NAME, *args), 1, "Usage: uninstall_probe.bat")
    assert not (game / ctx.pak_name).exists()


@check
def default_game_folder_is_used(ctx: Ctx) -> None:
    game = _fake_game(ctx, FAKE_DEFAULT)
    _expect(*run_bat(probe.INSTALL_NAME, "D"), 0, "Probe variant D is installed", str(FAKE_DEFAULT))
    assert (game / ctx.pak_name).read_bytes() == ctx.result.paks["D"].read_bytes()
    _expect(*run_bat(probe.UNINSTALL_NAME), 0, "Probe variant D was removed")
    assert not (game / ctx.pak_name).exists()


@check
def access_denied_is_explained(ctx: Ctx) -> None:
    game = _fake_game(ctx, FAKE_GAME)
    target = game / ctx.pak_name
    with _denied(game, "WD,AD"):
        code, out = run_bat(probe.INSTALL_NAME, "B", str(game))
    _expect(code, out, 1, "Access is denied", "The copy failed and nothing was installed", "Run as administrator")
    assert not target.exists()
    _expect(*run_bat(probe.INSTALL_NAME, "B", str(game)), 0)
    with _denied(game, "DC"), _denied(target, "DE"):
        code, out = run_bat(probe.UNINSTALL_NAME, str(game))
        assert target.exists()
    _expect(code, out, 1, "Could not delete the probe file", "Run as administrator")
    _expect(*run_bat(probe.UNINSTALL_NAME, str(game)), 0, "Probe variant B was removed")


@check
def checklist_covers_every_item(ctx: Ctx) -> None:
    text = (PROBE_OUT / probe.CHECKLIST_NAME).read_bytes().decode("utf-8")
    for n in sorted({i["n"] for i in ctx.spec["items"] if i["n"]}):
        assert f"**#{n}**" in text, n
    for phrase in ("install_probe.bat B", "uninstall_probe.bat", ctx.pak_name, "Español", "Other variants",
                   "try **C**", "try **D**", "try **A**", "Run as administrator", "F12", "#8 Cargar",
                   str(PROBE_OUT.resolve()), probe.STEAM_USERDATA, f"`{probe.STEAM_APP_ID}`", "win64_save",
                   "\"Press Any Button\" instead of \"#1 Presiona cualquier botón\"", "did not load the probe",
                   "shows its usual English texts", "first word of line 2: it shows where the game breaks",
                   "EMPTY slot", "under Juegos (Games) or behind Continuar (Continue)", "without a variant letter",
                   "(no letter)", "autosave of the NEW GAME may replace your own SF1 autosave"):
        assert phrase in text, phrase
    assert "never changes your saves" not in text and "never changes save files" not in text
    order = [text.index(f"**#{n}**") for n in (1, 2, 3, 4, 5, 6, 33, 30, 31, 32, 7, 10, 11, 20, 21, 27, 8)]
    assert order == sorted(order), order
    assert text.index("## Before you start: back up your saves") < text.index("## Install") < order[0]
    assert "\n    - **#20**" in text and "\n    - **#8**" in text          # sub-items nest under 10. and 11.


@check
def bad_specs_are_refused_loudly(ctx: Ctx) -> None:
    variants = ctx.real_spec["variants"]
    shape = {"variants": variants, "items": [
        {"n": 1, "file": SETTINGS, "match_en": "Language", "entry": "x", "es": "a", "wrap": "manual"},
        {"n": 2, "file": SETTINGS, "match_en": "Language", "es": "a", "wrap": "sideways", "colour": 1},
        {"n": 3, "file": SETTINGS, "match_en": "Language", "computed": "magic", "wrap": "manual"},
        {"n": 7, "file": SETTINGS, "match_en": "Language", "es": "a", "wrap": "manual"},
        {"n": 7, "file": SETTINGS, "match_en": "Language", "es": "b", "wrap": "manual"}]}
    problems = "\n".join(_plan_problems(shape, ctx))
    for phrase in ("spec item 1: give exactly one of entry and match_en", "spec item 2: unknown key 'colour'",
                   "spec item 2: wrap must be one of", "spec item 3: computed must be one of",
                   "label #7 is used by more than one item"):
        assert phrase in problems, (phrase, problems)
    assert "spec must describe exactly the variants" in "\n".join(_plan_problems({"items": shape["items"][3:4]},
                                                                                 ctx))
    doc = extract.load_json(config.STRINGS / "phase1" / "common" / "common_msggui00030000.json")
    english = [e["en"] for e in doc["entries"]]
    twice = next(t for t in english if t and english.count(t) > 1)
    targets = {"variants": variants, "items": [
        {"n": 1, "file": SETTINGS, "match_en": "No such English text", "es": "a", "wrap": "manual"},
        {"n": 2, "file": SETTINGS, "match_en": twice, "es": "a", "wrap": "manual"},
        {"n": 3, "file": SETTINGS, "match_en": "Language", "es": "a", "wrap": "manual"},
        {"n": 4, "file": SETTINGS, "match_en": "Language", "es": "b", "wrap": "manual"},
        {"n": 5, "file": SETTINGS, "match_en": "Screen Layout", "computed": "overlong_line", "wrap": "manual"},
        {"n": 6, "file": "natives/stm/text/common/nothing.msg.23", "entry": "x", "es": "a", "wrap": "manual"}]}
    problems = "\n".join(_plan_problems(targets, ctx))
    assert "nothing.msg.23: not a file of scope phase1" in problems, problems
    targets["items"].pop()
    problems = "\n".join(_plan_problems(targets, ctx))
    for phrase in ("#1: English text 'No such English text' matches 0 entries",
                   f"#2: English text {twice!r} matches {english.count(twice)} entries", "is already used by #3",
                   "#5: a computed line needs a dialogue profile"):
        assert phrase in problems, (phrase, problems)
    gate = {"variants": variants, "items": [
        {"n": 1, "file": DIALOGUE, "entry": "RR1_DM0001_ENG_16", "es": "#1 _CMD_wait(5)Hola", "wrap": "manual"},
        {"n": 2, "file": DIALOGUE, "entry": "RR1_DM0001_ENG_15", "es": "#2 " + "palabra " * 40, "wrap": "manual"},
        {"n": 3, "file": "natives/stm/text/common/common_msggui00040101.msg.23",
         "entry": "Common_MsgGUI00040101_Detail_TimeStamp", "es": '<DATETIME {0} "DD/MM/YYYY HH:mm">',
         "wrap": "manual"},
        {"n": 4, "file": DIALOGUE, "entry": "RR1_DM0001_ENG_14", "computed": "overlong_line", "wrap": "manual"}]}
    problems = "\n".join(_plan_problems(gate, ctx))
    for phrase in ("A: #1 rr1_dm0001_eng#RR1_DM0001_ENG_16: refused by the safety gate: E_CODES",
                   "#2 rr1_dm0001_eng#RR1_DM0001_ENG_15: overflow ['width'], expected none",
                   "#3 common_msggui00040101#Common_MsgGUI00040101_Detail_TimeStamp: changes a DATETIME format "
                   "without format_change",
                   "#4 rr1_dm0001_eng#RR1_DM0001_ENG_14: overflow ['width'], expected none"):
        assert phrase in problems, (phrase, problems)


@check
def failed_build_writes_nothing(ctx: Ctx) -> None:
    out = SCRATCH / "failed (1)"
    bad = copy.deepcopy(ctx.spec)
    bad["items"][0]["es"] += "_CMD_wait(5)"
    try:
        probe.build_probe(out, ("A",), spec=bad, game_dir=FAKE_DEFAULT)
    except probe.ProbeError as exc:
        assert any("E_CODES" in p for p in exc.problems), exc.problems
    else:
        raise AssertionError("a gate failure was accepted")
    assert not out.exists()


@check
def protected_output_folders_are_refused(ctx: Ctx) -> None:
    others = [config.DIST / name for name in ("other package", "probe2", "_test_probe2")]   # never created
    for folder, error in ((config.GAME_DIR / "probe", PermissionError), (config.ORIGINAL / "probe", PermissionError),
                          (config.BUILD / "probe", ValueError), (config.DIST, ValueError),
                          (config.STRINGS / "probe", ValueError), *((p, ValueError) for p in others),
                          (probe.OUT_DIR / ".." / "other package", ValueError)):
        try:
            probe.build_probe(folder, spec=ctx.spec, game_dir=FAKE_DEFAULT)
        except error:
            pass
        else:
            raise AssertionError(f"{folder} was accepted")
        assert not folder.exists() or folder == config.DIST, folder
    for folder in (probe.OUT_DIR, probe.OUT_DIR / "sub", SCRATCH / "x (1)"):            # accepted, nothing written
        assert probe.check_out_dir(folder) == folder.resolve(), folder


@check
def cli_exit_codes(ctx: Ctx) -> None:
    def run(*argv: str) -> int:
        with redirect_stdout(io.StringIO()):
            return probe.main(list(argv))
    assert run("--variants", "E") == 2
    assert run("--variants", "") == 2
    assert run("--out", str(config.BUILD / "probe_cli")) == 2 and not (config.BUILD / "probe_cli").exists()
    assert run("--out", str(config.DIST / "other package")) == 2 and not (config.DIST / "other package").exists()
    out = SCRATCH / "cli (1)"
    assert run("--out", str(out), "--variants", "B") == (1 if ctx.real_problems else 0)
    assert out.exists() != bool(ctx.real_problems)


@check
def work_files_and_spec_untouched(ctx: Ctx) -> None:
    assert _tree_hashes(config.STRINGS) == ctx.work_hashes
    assert probe.load_spec() == ctx.real_spec


def main(argv: list[str] | None = None) -> int:
    ctx = Ctx()
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)
    failed = 0
    try:
        for fn in CHECKS:
            t0 = time.time()
            try:
                fn(ctx)
            except Exception:          # report every failing check, then fail the run
                failed += 1
                print(f"FAIL {fn.__name__}")
                traceback.print_exc()
            else:
                print(f"PASS {fn.__name__} ({time.time() - t0:.2f}s)")
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)
    print(f"{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    return 1 if failed else 0
