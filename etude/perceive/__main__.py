"""``python -m etude.perceive --demo``: ColorBlobPerceiver smoke run on the
default fake scene (design §4 MCP ``perceive``)."""
from __future__ import annotations

import argparse
import sys

from etude.perceive.detector import _demo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m etude.perceive")
    ap.add_argument("--demo", action="store_true", help="run the colour-blob perceive demo")
    a = ap.parse_args(argv)
    if not a.demo:
        ap.print_help()
        return 0
    return _demo()


if __name__ == "__main__":
    sys.exit(main())
