"""Offline split classification (design §3.2 划分与证据可用时段).

The split of an episode (E / V / T / spare) is a property of the official
``(task, seed, layout_idx)`` triple that P5 reads offline from the official
result (``_result.json`` ``details[i].layout_id`` and the ``<seed>_<info>``
run directory). It is never taken from the run-time trace: the runner cannot
know the layout, and a default label would let V/A-A/T episodes pass as E
(review finding: memory writer split guard).

Split rules come from ``experiments/splits.json`` (``scripts/make_split.py``):

* E     = dev tasks, seed 0, layouts 0-49 (Gen tasks: also ``<task>_random``);
* V     = dev tasks, seed 1, layouts 0-49 (A/A reruns use the same layouts);
* T     = dev tasks seed 2, held-out tasks seeds 0/1/2;
* spare = seed 0 layouts 50-54.

Gate pairing keys use :func:`layout_key` (``"<task>/seed<s>/<idx:03d>"``),
the same format as ``etude.scorer.results.layout_key``.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from etude import REPO_ROOT

SPLITS_FILE = Path("experiments") / "splits.json"
RANDOM_SUFFIX = "_random"
#: used when experiments/splits.json is missing (same content as design §3.2)
_DEFAULT = {
    "tasks": {"dev": ["stack_bowls", "put_bottles_into_dustbin"], "heldout": ["build_tower", "make_kong"]},
    "splits": {"E": {"seed": 0, "range": [0, 50]}, "V": {"seed": 1, "range": [0, 50]},
               "T": {"seeds": {"dev": [2], "heldout": [0, 1, 2]}}, "spare": {"seed": 0, "range": [50, 55]}},
}


def layout_key(task: str, seed: int | None, layout_id: int) -> str:
    """Canonical per-layout key ``"<task>/seed<s>/<id:03d>"`` (design §3.2; gate pairing)."""
    return f"{task}/seed{'' if seed is None else seed}/{int(layout_id):03d}"


@functools.lru_cache(maxsize=4)
def _load(root: str | None) -> dict[str, Any]:
    p = (Path(root) if root else REPO_ROOT) / SPLITS_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT)


def load_splits(root: str | Path | None = None) -> dict[str, Any]:
    """Parsed ``experiments/splits.json`` (design §3.2)."""
    return _load(None if root is None else str(root))


def base_task(task: str) -> str:
    """``stack_bowls_random`` → ``stack_bowls``."""
    return task[: -len(RANDOM_SUFFIX)] if task.endswith(RANDOM_SUFFIX) else task


def _rng(spec: Mapping[str, Any], default: tuple[int, int]) -> tuple[int, int]:
    r = spec.get("range") or default
    return int(r[0]), int(r[1])


def classify(task: str, seed: int | None, layout_idx: int | None, root: str | Path | None = None) -> str | None:
    """Split of one official episode, or None when it is outside every split or
    ``seed``/``layout_idx`` are unknown (design §3.2)."""
    if seed is None or layout_idx is None or not task:
        return None
    d = load_splits(root)
    tasks = d.get("tasks") or _DEFAULT["tasks"]
    sp = d.get("splits") or _DEFAULT["splits"]
    t = base_task(str(task))
    seed, idx = int(seed), int(layout_idx)
    dev, held = set(tasks.get("dev") or ()), set(tasks.get("heldout") or ())
    tseeds = (sp.get("T") or {}).get("seeds") or _DEFAULT["splits"]["T"]["seeds"]
    if t in held and seed in set(tseeds.get("heldout") or ()):
        return "T"
    if t not in dev:
        return None
    if seed in set(tseeds.get("dev") or ()):
        return "T"
    for name in ("E", "V", "spare"):
        spec = sp.get(name) or _DEFAULT["splits"][name]
        lo, hi = _rng(spec, tuple(_DEFAULT["splits"][name]["range"]))
        if seed == int(spec.get("seed", -1)) and lo <= idx < hi:
            return name
    return None


def split_layout_keys(split: str, root: str | Path | None = None, with_random: bool = True) -> set[str]:
    """All :func:`layout_key` values of an E/V/spare split over the dev tasks
    (``<task>_random`` variants included) (design §3.2, §3.3 L1 medoids ⊆ E)."""
    d = load_splits(root)
    tasks = (d.get("tasks") or _DEFAULT["tasks"]).get("dev") or ()
    spec = (d.get("splits") or _DEFAULT["splits"]).get(split) or _DEFAULT["splits"].get(split)
    if spec is None or "seed" not in spec:
        raise ValueError(f"split {split!r} has no single seed/range")
    lo, hi = _rng(spec, (0, 50))
    names: list[str] = []
    for t in tasks:
        names.append(t)
        if with_random:
            names.append(t + RANDOM_SUFFIX)
    return {layout_key(t, int(spec["seed"]), i) for t in names for i in range(lo, hi)}


def parse_layout_key(key: str) -> tuple[str, int | None, int] | None:
    """Inverse of :func:`layout_key`; None if ``key`` is not in that format."""
    try:
        task, s, idx = key.rsplit("/", 2)
        if not s.startswith("seed"):
            return None
        return task, (int(s[4:]) if s[4:] else None), int(idx)
    except ValueError:
        return None


def keys_outside(keys: Iterable[str], split: str, root: str | Path | None = None) -> list[str]:
    """The ``keys`` that are not layouts of ``split`` (design §3.2; used by gate L1/F)."""
    out: list[str] = []
    for k in keys:
        p = parse_layout_key(str(k))
        if p is None or classify(p[0], p[1], p[2], root) != split:
            out.append(str(k))
    return out


__all__ = ["SPLITS_FILE", "base_task", "classify", "keys_outside", "layout_key", "load_splits",
           "parse_layout_key", "split_layout_keys"]
