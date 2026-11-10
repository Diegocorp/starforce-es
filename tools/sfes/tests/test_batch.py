"""Tests for sfes.batch on temporary copies of real work files (work/batches/_test, removed afterwards)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from sfes import batch, config, extract

TEST = config.WORK / "batches" / "_test"
DIALOGUE = "rr1_dm0001_eng"
MENU = "common_msggui00040101"


def _copy_scope() -> Path:
    if TEST.exists():
        shutil.rmtree(TEST)
    strings = TEST / "strings"
    for stem, path in batch.work_files().items():
        if stem in (DIALOGUE, MENU, "rr1_chipname_eng"):
            dst = strings / path.relative_to(config.STRINGS)
            dst.parent.mkdir(parents=True, exist_ok=True)
            doc = extract.load_json(path)
            for e in doc["entries"]:          # start from untranslated copies, whatever the real progress is
                if e["status"] in ("draft", "done", "reviewed"):
                    e.update(status="todo", es="", note="")
            dst.write_bytes(extract.dump_json(doc))
    return strings


def main(argv: list[str] | None = None) -> int:
    results = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        results.append((label, bool(ok)))
        print(f"  [{'ok' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))

    strings = _copy_scope()
    try:
        files = batch.work_files(strings_dir=strings)
        check("work_files finds the copies", set(files) == {DIALOGUE, MENU, "rr1_chipname_eng"}, str(sorted(files)))
        d = batch.dump([DIALOGUE], {"todo"}, strings_dir=strings)
        ents = d["files"][0]["entries"]
        check("dump lists todo entries in order", len(ents) > 30 and ents[0]["id"].endswith("_ENG_0"))
        check("dump has a speaker field; the speaker map is loaded",
              all("speaker" in x for x in ents) and len(batch._speakers()) > 20000)
        check("dump has the limit text", "1026" in d["files"][0]["limit"])

        doc = extract.load_json(files[DIALOGUE])
        e0, e1 = doc["entries"][0], doc["entries"][1]
        rows = [{"id": e0["id"], "es": "Me cuesta creer que ya\npasaron 3 años", "note": "prueba"},
                {"id": e1["id"], "es": "texto <ICON X>"},          # extra code -> refused
                {"id": e1["id"], "es": "Línea uno\\nLínea dos"},   # literal backslash-n -> refused
                {"id": "nope#X", "es": "x"},
                {"id": e1["id"], "status": "keep"}]
        res = batch.apply_rows(rows, strings_dir=strings)
        reasons = [r["reason"] for r in res["refused"]]
        check("apply accepts good rows and refuses bad ones", res["accepted"] == 2 and len(res["refused"]) == 3,
              str(reasons))
        doc = extract.load_json(files[DIALOGUE])
        a, b = doc["entries"][0], doc["entries"][1]
        check("es stored with CRLF and status draft", a["es"] == "Me cuesta creer que ya\r\npasaron 3 años"
              and a["status"] == "draft" and a["note"] == "prueba")
        check("keep row empties es and sets keep", b["status"] == "keep" and b["es"] == "")
        e2, e3 = doc["entries"][2], doc["entries"][3]
        res = batch.apply_rows([{"id": e2["id"], "status": "todo", "note": "SF2/SF3 scope"},
                                {"id": e3["id"], "status": "todo"}], strings_dir=strings)
        doc = extract.load_json(files[DIALOGUE])
        check("todo row sets only a note; without a note it is refused",
              doc["entries"][2]["status"] == "todo" and doc["entries"][2]["note"] == "SF2/SF3 scope"
              and doc["entries"][2]["es"] == "" and len(res["refused"]) == 1)
        res = batch.apply_rows([{"id": e0["id"], "es": a["es"], "wrap": "manual"},
                                {"id": e0["id"], "es": a["es"], "wrap": "sideways"}], strings_dir=strings)
        doc = extract.load_json(files[DIALOGUE])
        check("wrap field sets manual; a bad value is refused",
              doc["entries"][0]["wrap"] == "manual" and len(res["refused"]) == 1, str(res["refused"]))
        res = batch.apply_rows([{"id": e0["id"], "es": "Hola"}], "done", strings_dir=strings, dry_run=True)
        doc = extract.load_json(files[DIALOGUE])
        check("dry run writes nothing", doc["entries"][0]["status"] == "draft" and res["accepted"] == 1)

        menu = extract.load_json(files[MENU])
        ts = next(e for e in menu["entries"] if "DATETIME" in e["en"] and "hh:mm" in e["en"])
        res = batch.apply_rows([{"id": ts["id"], "es": '<DATETIME {0} "DD/MM/YYYY HH:mm">'},
                                {"id": ts["id"], "es": '<DATETIME {0} "DD/MM/YYYY hh:mm">'}], strings_dir=strings)
        check("DATETIME: valid format accepted, invalid refused", res["accepted"] == 1 and len(res["refused"]) == 1)

        found = {c for c, _ in batch.lint_text("¿Vosotros habéis visto a usted? Si. 1.000 zennys")}
        check("lint finds vosotros/usted/si/thousands", {"L_VOSOTROS", "L_USTED", "L_SI", "L_THOUSANDS"} <= found,
              str(sorted(found)))
        found = {c for c, _ in batch.lint_text("Obtuviste la _CMD_insert_itemname(3). The end")}
        check("lint finds article before insert and English", {"L_ARTICLE_INSERT", "L_ENGLISH"} <= found)
        check("lint quiet on clean Spanish", batch.lint_text("¡Sí! Ustedes tienen 1000 zennys, ¿verdad?") == [])

        names = TEST / "names.json"
        chip = extract.load_json(files["rr1_chipname_eng"])
        c0, c1 = chip["entries"][1], chip["entries"][0]
        names.write_text(json.dumps({"records": [
            {"id": c0["id"], "en": c0["en"], "es": "Cañón", "status_hint": "draft"},
            {"id": c1["id"], "en": c1["en"], "es": c1["en"], "status_hint": "keep"},
            {"id": "rr1_chipname_eng#NOPE", "en": "x", "es": "y", "status_hint": "draft"}]}, ensure_ascii=False),
            encoding="utf-8")
        res = batch.import_names(names, strings_dir=strings)
        chip = extract.load_json(files["rr1_chipname_eng"])
        check("import-names: draft and keep applied, missing id reported",
              chip["entries"][1]["es"] == "Cañón" and chip["entries"][1]["status"] == "draft"
              and chip["entries"][0]["status"] == "keep" and res["stats"].get("id_missing") == 1, str(res["stats"]))
        res2 = batch.import_names(names, strings_dir=strings)
        check("import-names is idempotent", res2["stats"].get("files_changed", 0) == 0, str(res2["stats"]))

        with batch.file_lock(files[MENU]):
            try:
                with batch.file_lock(files[MENU], timeout=0.5):
                    pass
                locked = False
            except TimeoutError:
                locked = True
        check("file lock excludes a second writer", locked)
        prog = batch.progress("file", strings_dir=strings)
        check("progress counts", prog[DIALOGUE].get("draft") == 1 and prog[DIALOGUE].get("keep", 0) >= 1)
    finally:
        shutil.rmtree(TEST, ignore_errors=True)
    failed = [l for l, ok in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0
