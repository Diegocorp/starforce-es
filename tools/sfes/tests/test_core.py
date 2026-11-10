r"""Core round-trip tests: every msg.23 in the official paks rebuilds byte-identically; encryption is
inverse of decryption; a written patch pak reads back. Run: py -I -X utf8 -m sfes.tests.test_core
(from C:\StarForceES\tools)."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from sfes import config, msg23, codes
from sfes.pak import PakSet, PakWriter, Pak, name_list


def main(argv=None) -> int:
    t0 = time.time()
    ps = PakSet()
    names = name_list()
    msg_paths = sorted(p for h, p in names.items() if p.endswith(".msg.23") and h in ps.winner)
    print(f"paks: {[p.path.name for p in ps.paks]} skipped(sfes): {ps.skipped} next index: {ps.next_free_index()}")
    print(f"msg files in name list and paks: {len(msg_paths)}")
    bad = 0
    for i, p in enumerate(msg_paths):
        raw = ps.read(p)
        m = msg23.parse(raw)
        if msg23.build(m) != raw:
            bad += 1
            print("NOT IDENTICAL:", p)
        if i < 50:  # spot check crypto both ways
            pool = raw[msg23.parse.__globals__["struct"].unpack_from("<Q", raw, 32)[0]:]
            assert msg23.encrypt(msg23.decrypt(pool)) == pool
    print(f"byte-identical rebuild: {len(msg_paths) - bad}/{len(msg_paths)}  ({time.time() - t0:.0f}s)")

    # modified text round trip
    p = "natives/stm/text/rr1/message/eng/rr1_dm0000_eng.msg.23"
    m = msg23.parse(ps.read(p))
    m.set(m.entries[0], config.LANG_EN, "¿Qué pasó? ¡Ñandú! áéíóú ÁÉÍÓÚ üÜ_CMD_wait(5)\r\n")
    data = msg23.build(m)
    m2 = msg23.parse(data)
    assert m2.get(m2.entries[0], config.LANG_EN) == "¿Qué pasó? ¡Ñandú! áéíóú ÁÉÍÓÚ üÜ_CMD_wait(5)\r\n"
    assert [e.guid for e in m2.entries] == [e.guid for e in m.entries]
    print("modified-text round trip: OK")

    # pak writer read-back
    with tempfile.TemporaryDirectory(dir=str(config.ROOT)) as td:
        w = PakWriter(fingerprint=ps.paks[-1].fingerprint)
        w.add(p, data, template=ps.entry(p))
        w.add_marker()
        out = Path(td) / config.PATCH_FMT.format(3)
        rep = w.write(out)
        pk = Pak(out)
        assert pk.is_sfes and pk.total == 2
        e = pk.by_hash[ps.entry(p).hash]
        assert pk.read(e) == data and e.attributes == (ps.entry(p).attributes & ~0xF) | 1
        print("pak writer read-back: OK", [(r["path"], hex(r["attributes"]), r["csize"]) for r in rep])
    tok = codes.tokenize("A_CMD_wait(5) <REPLACE  RR1_EX>{0}\r\nB")
    assert [t.kind for t in tok] == ["text", "cmd", "text", "tag", "ph", "nl", "text"], tok
    print("tokenizer: OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
