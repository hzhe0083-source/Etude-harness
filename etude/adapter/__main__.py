"""``python -m etude.adapter`` (design §2 P2/P4, §5 M0).

  --demo          run both adapter modes through deploy.eval_one_episode on fakes
  --deploy-hash   print sha256 of deploy/XPolicyLab/policy/Etude/deploy.py and the recorded value;
                  exit 1 on mismatch (the hash file is written by a human decision, not by this tool)
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m etude.adapter", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--deploy-hash", action="store_true")
    args = ap.parse_args(argv)
    if args.deploy_hash:
        from etude.adapter.xpl_model import deploy_sha256, recorded_deploy_sha256

        actual, recorded = deploy_sha256(), recorded_deploy_sha256()
        print(f"actual   {actual}\nrecorded {recorded}")
        return 0 if actual == recorded else 1
    if args.demo:
        from etude.adapter.demo import run_demo

        return run_demo()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
