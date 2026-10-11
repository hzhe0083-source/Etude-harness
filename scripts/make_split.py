#!/usr/bin/env python3
"""Build experiments/splits.json from RoboDojo Eval_Layout file *names* (design §3.2).

Split definition (docs/design/vla_harness_v1.md §3.2):

    E      dev tasks, seed 0, layouts 0-49        (P6 may read traces + official scores)
    V      dev tasks, seed 1, layouts 0-49        (P5 only; P6 sees accept/reject)
    AA     the incumbent re-run 3x on V           (same layouts as V; gives epsilon)
    T      dev tasks seed 2 + held-out tasks seeds 0/1/2, layouts 0-49 (looked at once, end of M3)
    spare  seed 0, layouts 50-54                  (smoke only; official eval uses the first 50)

This script only lists directory entries (``os.scandir`` names). It never opens,
parses or hashes a layout file's contents: layout contents are on the forbidden
list (frozen/forbidden.yaml ``deny_paths``) for everything that runs at eval time,
and the split only needs to know which layout indices exist.

A task whose layout files are absent locally is recorded with ``status: "missing"``
(today only ``plug_in_charger`` ships locally). The layout index RoboDojo uses is the
position in the numerically sorted file list (``env/seed_manager/seed_manager.py``
``init_eval``); we record both the position and the file number and flag gaps.

Usage::

    uv run python scripts/make_split.py                       # writes experiments/splits.json
    uv run python scripts/make_split.py --layout-root DIR --out PATH
    uv run python scripts/make_split.py --print               # summary only, no write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:  # single source of truth for the task sets (design §1)
    from etude.core.types import DEV_TASKS, HELDOUT_TASKS
except Exception:  # pragma: no cover - fallback keeps the script usable without the package
    DEV_TASKS = ("stack_bowls", "put_bottles_into_dustbin")
    HELDOUT_TASKS = ("build_tower", "make_kong")

RESERVE_TASKS: tuple[str, ...] = ("fold_clothes",)  # design §1: fold_clothes is the substitute
BENCHMARK = "RoboDojo"
CONFIG_NAME = "arx_x5"
DEFAULT_ROBODOJO = Path(os.environ.get("ROBODOJO_ROOT", Path.home() / "Documents" / "RoboDojo"))
DEFAULT_LAYOUT_ROOT = DEFAULT_ROBODOJO / "Assets" / "Eval_Layout" / BENCHMARK / CONFIG_NAME
DEFAULT_OUT = REPO_ROOT / "experiments" / "splits.json"

OFFICIAL_N = 50          # official eval uses the first 50 layouts per seed (design §3.2 [code])
SPARE_RANGE = (50, 55)   # seed 0 layouts 50-54
AA_REPS = 3              # design §3.2 / §5
HF_LAYOUT_SOURCE = "dataset=RoboDojo-Benchmark/RoboDojo (Assets/Eval_Layout/RoboDojo/arx_x5/<seed>/<task>_<n>.json)"

_NAME_RE = re.compile(r"^(?P<task>[a-z0-9_]+?)_(?P<num>\d+)\.json$")


def list_layout_names(seed_dir: Path) -> dict[str, list[int]]:
    """Map task -> sorted file numbers found in one seed directory (names only). §3.2.

    Only directory entries are inspected; no layout file is opened.
    """
    found: dict[str, list[int]] = {}
    if not seed_dir.is_dir():
        return found
    with os.scandir(seed_dir) as it:
        for entry in it:
            m = _NAME_RE.match(entry.name)
            if m and entry.is_file():
                found.setdefault(m.group("task"), []).append(int(m.group("num")))
    for task in found:
        found[task].sort()
    return found


def names_digest(seed_dir: Path) -> str | None:
    """sha256 over the sorted file *names* of a seed directory (provenance, not content). §3.2."""
    if not seed_dir.is_dir():
        return None
    names = sorted(e.name for e in os.scandir(seed_dir) if e.is_file())
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def select(numbers: list[int], lo: int, hi: int) -> dict:
    """Pick layout positions [lo, hi) from one task's sorted file numbers. §3.2.

    RoboDojo's seed_manager indexes layouts by position in the numerically sorted list,
    so position == file number only when the numbers are contiguous from 0.
    """
    if not numbers:
        return {"status": "missing", "layout_idx": [], "file_num": [], "n": 0}
    contiguous = numbers == list(range(len(numbers)))
    positions = [i for i in range(lo, min(hi, len(numbers)))]
    status = "ok" if len(positions) == hi - lo else "short"
    out = {
        "status": status,
        "layout_idx": positions,
        "file_num": [numbers[i] for i in positions],
        "n": len(positions),
        "available_in_seed": len(numbers),
    }
    if not contiguous:
        out["warning"] = "file numbers are not contiguous from 0; layout_idx is the sorted position"
    if status == "short":
        out["warning"] = f"only {len(positions)} of {hi - lo} requested layouts exist"
    return out


def build_splits(layout_root: Path) -> dict:
    """Assemble the E/V/AA/T/spare split document (design §3.2)."""
    seeds = {s: list_layout_names(layout_root / str(s)) for s in (0, 1, 2)}

    def block(tasks, seed, lo, hi):
        return {t: select(seeds[seed].get(t, []), lo, hi) for t in tasks}

    dev, held = list(DEV_TASKS), list(HELDOUT_TASKS)
    all_tasks = dev + held + list(RESERVE_TASKS)
    splits = {
        "E": {"seed": 0, "range": [0, OFFICIAL_N], "tasks": block(dev, 0, 0, OFFICIAL_N),
              "visible_to": "P6 traces + official scores; memory accumulates here", "when": "development"},
        "V": {"seed": 1, "range": [0, OFFICIAL_N], "tasks": block(dev, 1, 0, OFFICIAL_N),
              "visible_to": "P5 only; P6 receives accept/reject bit", "when": "once per candidate"},
        "AA": {"same_layouts_as": "V", "seed": 1, "range": [0, OFFICIAL_N], "reps": AA_REPS,
               "tasks": block(dev, 1, 0, OFFICIAL_N),
               "visible_to": "P5; yields epsilon", "when": "once in M0, once in M1"},
        "T": {"seeds": {"dev": [2], "heldout": [0, 1, 2]}, "range": [0, OFFICIAL_N],
              "tasks": {**{f"{t}@2": select(seeds[2].get(t, []), 0, OFFICIAL_N) for t in dev},
                        **{f"{t}@{s}": select(seeds[s].get(t, []), 0, OFFICIAL_N)
                           for t in held for s in (0, 1, 2)}},
              "visible_to": "looked at once", "when": "end of M3"},
        "spare": {"seed": 0, "range": list(SPARE_RANGE), "tasks": block(all_tasks, 0, *SPARE_RANGE),
                  "visible_to": "anyone (smoke only)", "when": "any"},
    }
    missing = sorted({t.split("@")[0] for sp in splits.values() for t, v in sp["tasks"].items()
                      if v["status"] == "missing"})
    other = {t: {str(s): len(seeds[s].get(t, [])) for s in (0, 1, 2)}
             for t in sorted(set().union(*[seeds[s].keys() for s in (0, 1, 2)])) if t not in all_tasks}
    return {
        "design": "docs/design/vla_harness_v1.md §3.2",
        "generated_by": "scripts/make_split.py",
        "benchmark": BENCHMARK,
        "config_name": CONFIG_NAME,
        "layout_root": str(layout_root),
        "layout_source_if_missing": HF_LAYOUT_SOURCE,
        "names_sha256": {str(s): names_digest(layout_root / str(s)) for s in (0, 1, 2)},
        "tasks": {"dev": dev, "heldout": held, "reserve": list(RESERVE_TASKS)},
        "missing_tasks": missing,
        "other_tasks_present": other,
        "note": "Only file names were listed; no layout content was read. layout_idx = position in "
                "the numerically sorted <task>_<n>.json list (RoboDojo seed_manager.init_eval).",
        "splits": splits,
    }


def summary(doc: dict) -> str:
    """One line per split/task with status and count (design §3.2)."""
    lines = [f"layout_root: {doc['layout_root']}"]
    for name, sp in doc["splits"].items():
        for task, v in sp["tasks"].items():
            lines.append(f"  {name:5s} {task:32s} {v['status']:8s} n={v['n']}")
    lines.append(f"missing tasks: {', '.join(doc['missing_tasks']) or '-'}")
    if doc["other_tasks_present"]:
        lines.append(f"other tasks present (not in our task set): {doc['other_tasks_present']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: write experiments/splits.json (design §3.2)."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--layout-root", type=Path, default=DEFAULT_LAYOUT_ROOT,
                    help="…/Assets/Eval_Layout/RoboDojo/arx_x5 (contains seed dirs 0/1/2)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--print", dest="print_only", action="store_true", help="print summary, do not write")
    args = ap.parse_args(argv)

    if not args.layout_root.is_dir():
        print(f"[make_split] layout root not found: {args.layout_root}; every task will be 'missing'",
              file=sys.stderr)
    doc = build_splits(args.layout_root)
    print(summary(doc))
    if args.print_only:
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[make_split] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
