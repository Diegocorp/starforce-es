r"""Launcher for the sfes toolkit.

Usage (always with the project venv, isolated mode, UTF-8):
  C:\StarForceES\.venv\Scripts\python.exe -I -X utf8 C:\StarForceES\tools\run.py <module> [args...]
Examples:
  run.py tests.test_core
  run.py extract --scope phase1
Each module sfes.<module> exposes main(argv: list[str]) -> int.
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    mod = importlib.import_module("sfes." + sys.argv[1])
    fn = getattr(mod, "main", None)
    if fn is None:
        raise SystemExit(f"sfes.{sys.argv[1]} has no main()")
    rc = fn(sys.argv[2:])
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
