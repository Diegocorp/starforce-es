"""fslt and oft codecs: byte-exact round trip on the game's files."""
from sfes import fslt, oft
from sfes.pak import PakSet, name_list


def main(argv=None) -> int:
    ps = PakSet()
    names = name_list()
    bad = 0
    for h, p in sorted(names.items(), key=lambda kv: kv[1]):
        if h not in ps.winner:
            continue
        if p.endswith(".fslt.4"):
            raw = ps.read(p)
            ok = fslt.encode(fslt.parse(raw)) == raw
            print("fslt", p.rsplit("/", 1)[1], "byte-exact" if ok else "DIFFERENT")
            bad += not ok
        elif p.endswith(".oft.1"):
            raw = ps.read(p)
            ok = oft.encode(oft.decode(raw)) == raw
            bad += not ok
    m = fslt.parse(ps.read(fslt.FSLT_EN))
    for i in (0, 1, 2):
        print("slot", i, [f["path"].rsplit("/", 1)[1] for f in m["slots"][i]["fonts"]])
    for f in m["slots"][0]["fonts"]:
        assert ps.has(fslt.font_pak_path(f["path"])), f["path"]
    print("oft/fslt failures:", bad)
    return 1 if bad else 0
