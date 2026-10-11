"""Official-result parser for P5 (design §2 P5, §3.0 适应度, §3.2, §5 指标统一).

Reads RoboDojo ``eval_result/RoboDojo/<task>/<policy>/<config>/<seed>_<info>/<run_id>/_result.json``
exactly the way ``scripts/internal/summarize_result.py`` (RoboDojo 266130a) does:

* ``details`` entries are sorted by their integer key (non-int keys skipped);
  each gives ``success`` (bool) and ``score`` (float, default 0);
* for every ``(task, policy, embodiment, seed)`` only the latest timestamp dir
  that contains ``_result.json`` is used;
* Generalization tasks with a ``<task>_random`` sibling are "paired": first 25
  of the base + first 25 of ``_random`` → 50; every other task: first 50;
* ``success_rate = successes / count * 100``, ``score = sum(score) / count * 100``.

It also emits per-layout outcomes (for gate pairing, keyed by
:func:`layout_key`) and the Research OS evaluation core metrics
``{"successes", "trials", "success_rate"}`` (``success_rate`` in 0–1, equal to
successes/trials, as ``research/tools/research_os/metrics.py`` requires) to
``$RESEARCH_METRICS``.

P5 only: this module is exempt from the AST scan (``frozen/forbidden.yaml``
``ast_scan.exempt``); P1/P2/P6 must never import it at run time.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

STANDALONE_EPISODES = 50
PAIRED_HALF_EPISODES = 25
RESULT_FILE = "_result.json"
SEED_RE = re.compile(r"^(\d+)_")
#: summarize_result.py DIMENSIONS["Generalization"] (paired 25 std + 25 random).
GENERALIZATION_TASKS: frozenset[str] = frozenset({
    "stack_bowls", "push_T", "pack_objects_into_box", "fold_clothes", "hang_mugs", "sweep_blocks",
    "pour_liquid_into_cup", "make_toast", "arrange_largest_number", "sort_nesting_dolls_by_size",
    "store_laptop_and_headphones", "stack_blocks",
})


@dataclass(frozen=True)
class EpisodeResult:
    """One ``details`` entry of ``_result.json`` (design §3.2)."""

    index: int          # details key (episode index; summarize sort key)
    layout_id: int      # entry["layout_id"] (== key when absent)
    success: bool
    score: float
    task: str = ""
    seed: int | None = None

    @property
    def key(self) -> str:
        return layout_key(self.task, self.seed, self.layout_id)

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "layout_id": self.layout_id, "success": self.success,
                "score": self.score, "task": self.task, "seed": self.seed, "key": self.key}


# Canonical per-layout key ``"<task>/seed<s>/<id:03d>"`` (design §3.2); one definition,
# shared with the split classifier and the gates.
from etude.core.splits import layout_key  # noqa: E402


def load_result(path: str | Path, task: str = "", seed: int | None = None) -> list[EpisodeResult] | None:
    """Parse one ``_result.json`` → entries sorted by integer details key, or
    None for missing/invalid JSON (same as summarize ``load_entries``)."""
    p = Path(path)
    if p.is_dir():
        p = p / RESULT_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    details = data.get("details", {}) if isinstance(data, Mapping) else {}
    items: list[EpisodeResult] = []
    for k, entry in (details or {}).items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        entry = entry if isinstance(entry, Mapping) else {}
        lid = entry.get("layout_id", idx)
        try:
            lid = int(lid)
        except (TypeError, ValueError):
            lid = idx
        items.append(EpisodeResult(idx, lid, bool(entry.get("success", False)),
                                   float(entry.get("score", 0.0) or 0.0), task, seed))
    items.sort(key=lambda e: e.index)
    return items


def latest_timestamp_dir(run_dir: str | Path) -> Path | None:
    """Latest ``YYYY-MM-DD_HH-MM-SS`` subdir holding ``_result.json`` (summarize rule)."""
    d = Path(run_dir)
    if not d.is_dir():
        return None
    cands = sorted(c.name for c in d.iterdir() if c.is_dir() and (c / RESULT_FILE).is_file())
    return d / cands[-1] if cands else None


def scan_task(eval_root: str | Path, task: str) -> dict[tuple[str, str, int], list[EpisodeResult]]:
    """``{(policy, embodiment, seed): entries}`` for one task (summarize ``scan_task``)."""
    out: dict[tuple[str, str, int], list[EpisodeResult]] = {}
    latest: dict[tuple[str, str, int], str] = {}
    tdir = Path(eval_root) / task
    if not tdir.is_dir():
        return out
    for pol in sorted(p for p in tdir.iterdir() if p.is_dir()):
        for emb in sorted(e for e in pol.iterdir() if e.is_dir()):
            for run in sorted(r for r in emb.iterdir() if r.is_dir()):
                m = SEED_RE.match(run.name)
                if not m:
                    continue
                seed = int(m.group(1))
                ts = latest_timestamp_dir(run)
                if ts is None:
                    continue
                entries = load_result(ts, task, seed)
                if entries is None:
                    continue
                key = (pol.name, emb.name, seed)
                if key not in out or ts.name > latest[key]:
                    out[key], latest[key] = entries, ts.name
    return out


def stats(entries: Sequence[EpisodeResult]) -> tuple[int, float, float]:
    """``(count, success_rate %, score %)`` (summarize ``stats``)."""
    n = len(entries)
    if n == 0:
        return 0, 0.0, 0.0
    k = sum(1 for e in entries if e.success)
    return n, k / n * 100.0, sum(e.score for e in entries) / n * 100.0


def merge_cell(base: Sequence[EpisodeResult], random: Sequence[EpisodeResult] | None = None,
               paired: bool | None = None) -> list[EpisodeResult] | None:
    """Entries that count for one (task, seed) cell, or None if incomplete.

    Paired (Generalization task with a ``_random`` run): first 25 + first 25.
    Standalone: first 50. Mirrors summarize ``main``.
    """
    if paired is None:
        paired = random is not None
    if paired:
        r = list(random or [])
        if len(base) < PAIRED_HALF_EPISODES or len(r) < PAIRED_HALF_EPISODES:
            return None
        return list(base[:PAIRED_HALF_EPISODES]) + r[:PAIRED_HALF_EPISODES]
    if len(base) < STANDALONE_EPISODES:
        return None
    return list(base[:STANDALONE_EPISODES])


def research_metrics(entries: Sequence[EpisodeResult]) -> dict[str, Any]:
    """Research OS evaluation core metrics ``{successes, trials, success_rate}``
    (``success_rate`` in 0–1, exactly successes/trials; research/AGENTS.md R40)."""
    n = len(entries)
    if n == 0:
        raise ValueError("research metrics need trials > 0")
    k = sum(1 for e in entries if e.success)
    return {"successes": k, "trials": n, "success_rate": k / n}


def per_layout(entries: Iterable[EpisodeResult]) -> dict[str, dict[str, Any]]:
    """``{layout_key: {"success": bool, "score": float}}`` for gate pairing (design §3.3 paired_runs)."""
    return {e.key: {"success": e.success, "score": e.score} for e in entries}


def gate_outcomes(entries: Iterable[EpisodeResult], costs: Mapping[str, Mapping[str, float]] | None = None) -> dict[str, dict[str, Any]]:
    """Per-layout outcomes in the gate format ``{layout_key: {"ok", "score", "vlm_tokens", "wall_s"}}``
    (design §3.3 paired_runs; consumed by ``etude.evolution.gates``). ``costs`` (per layout, from the
    trace ``EpisodeRecord``s) adds ``vlm_tokens``/``wall_s``."""
    out: dict[str, dict[str, Any]] = {}
    for e in entries:
        d: dict[str, Any] = {"ok": e.success, "score": e.score}
        d.update((costs or {}).get(e.key, {}))
        out[e.key] = d
    return out


@dataclass
class CellSummary:
    """One (task, policy, embodiment, seed) cell as the official summarize computes it."""

    task: str
    policy: str
    embodiment: str
    seed: int
    entries: list[EpisodeResult]

    def to_dict(self) -> dict[str, Any]:
        n, sr, score = stats(self.entries)
        return {"task": self.task, "policy": self.policy, "embodiment": self.embodiment, "seed": self.seed,
                "count": n, "success_rate": sr, "score": score, "metrics": research_metrics(self.entries),
                "layouts": per_layout(self.entries)}


def summarize(eval_root: str | Path, tasks: Iterable[str], policy: str | None = None) -> list[CellSummary]:
    """Official-rule cells for ``tasks`` under ``eval_root`` (= ``ROBODOJO_EVAL_ROOT``)."""
    root = Path(eval_root)
    cells: list[CellSummary] = []
    for task in tasks:
        base = scan_task(root, task)
        rand_name = task + "_random"
        paired = task in GENERALIZATION_TASKS and (root / rand_name).is_dir()
        rand = scan_task(root, rand_name) if paired else {}
        for key in sorted(set(base) | set(rand)):
            if policy is not None and key[0] != policy:
                continue
            merged = merge_cell(base.get(key, []), rand.get(key, []) if paired else None, paired)
            if merged is None:
                continue
            cells.append(CellSummary(task, key[0], key[1], key[2], merged))
    return cells


def write_research_metrics(metrics: Mapping[str, Any], path: str | Path | None = None) -> Path | None:
    """Write core metrics to ``path`` or ``$RESEARCH_METRICS`` (no-op if neither)."""
    target = path or os.environ.get("RESEARCH_METRICS")
    if not target:
        return None
    k, n, sr = metrics["successes"], metrics["trials"], metrics["success_rate"]
    if not (isinstance(k, int) and isinstance(n, int) and n > 0 and 0 <= k <= n and math.isclose(sr, k / n)):
        raise ValueError(f"invalid evaluation metrics {dict(metrics)}")
    p = Path(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"successes": k, "trials": n, "success_rate": sr}) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# demo / CLI
# ---------------------------------------------------------------------------


def _write_fake_run(root: Path, task: str, seed: int, n: int, rate: float, ts: str, layout_offset: int = 0) -> Path:
    import random

    rng = random.Random(f"{task}/{seed}/{ts}")
    d = root / task / "Etude" / "arx_x5" / f"{seed}_ckpt_name=demo,action_type=joint" / ts
    d.mkdir(parents=True, exist_ok=True)
    details = {}
    for i in range(n):
        ok = rng.random() < rate
        details[str(i)] = {"layout_id": i + layout_offset, "success": ok, "score": 1.0 if ok else round(rng.random() * 0.5, 2)}
    k = sum(v["success"] for v in details.values())
    (d / RESULT_FILE).write_text(json.dumps({"success_rate": k / n, "eval_time": n, "score": 0.0, "details": details}))
    return d


def _demo() -> int:
    with tempfile.TemporaryDirectory(prefix="etude-scorer-") as td:
        root = Path(td) / "eval_result" / "RoboDojo"
        _write_fake_run(root, "stack_bowls", 1, 25, 0.7, "2026-10-11_10-00-00")
        _write_fake_run(root, "stack_bowls_random", 1, 25, 0.1, "2026-10-11_10-00-00")
        _write_fake_run(root, "put_bottles_into_dustbin", 1, 30, 0.6, "2026-10-11_09-00-00")  # incomplete, older
        _write_fake_run(root, "put_bottles_into_dustbin", 1, 50, 0.6, "2026-10-11_11-00-00")
        cells = summarize(root, ["stack_bowls", "put_bottles_into_dustbin"])
        all_entries: list[EpisodeResult] = []
        for c in cells:
            d = c.to_dict()
            all_entries.extend(c.entries)
            first = next(iter(d["layouts"].items()))
            print(f"{c.task:26s} seed{c.seed} n={d['count']} SR={d['success_rate']:.1f} Score={d['score']:.1f} "
                  f"metrics={d['metrics']} first_layout={first}")
        m = research_metrics(all_entries)
        out = write_research_metrics(m, Path(td) / "metrics.json")
        print("research metrics:", out.read_text().strip() if out else None)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI (design §2 P5): parse ``_result.json`` files or an eval root.

    ``python -m etude.scorer.results --root eval_result/RoboDojo --task stack_bowls [--policy P]
    [--metrics-out $RESEARCH_METRICS] [--layouts-out FILE]`` or
    ``python -m etude.scorer.results path/_result.json [--random path/_result.json]``.
    """
    ap = argparse.ArgumentParser(prog="python -m etude.scorer.results")
    ap.add_argument("result", nargs="?", help="_result.json (or its dir)")
    ap.add_argument("--random", help="paired _random _result.json (25+25 rule)")
    ap.add_argument("--task", action="append", default=[], help="task name(s) with --root")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--root", help="eval root (ROBODOJO_EVAL_ROOT)")
    ap.add_argument("--policy", default=None)
    ap.add_argument("--metrics-out", default=None, help="default: $RESEARCH_METRICS")
    ap.add_argument("--layouts-out", default=None)
    ap.add_argument("--gate-out", default=None, help="per-layout outcomes in the gate format (ok/score)")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args(argv)
    if a.demo:
        return _demo()
    entries: list[EpisodeResult] = []
    report: dict[str, Any] = {}
    if a.root:
        cells = summarize(a.root, a.task, a.policy)
        report["cells"] = [c.to_dict() for c in cells]
        for c in cells:
            entries.extend(c.entries)
    elif a.result:
        task = a.task[0] if a.task else ""
        base = load_result(a.result, task, a.seed) or []
        rnd = load_result(a.random, task + "_random", a.seed) if a.random else None
        merged = merge_cell(base, rnd, paired=a.random is not None)
        if merged is None:
            print(f"incomplete cell: base={len(base)} random={len(rnd or [])}", file=sys.stderr)
            return 2
        entries = merged
        n, sr, score = stats(entries)
        report["cells"] = [{"task": task, "seed": a.seed, "count": n, "success_rate": sr, "score": score}]
    else:
        ap.error("give a _result.json, --root, or --demo")
    if not entries:
        print("no complete cells", file=sys.stderr)
        return 2
    report["metrics"] = research_metrics(entries)
    write_research_metrics(report["metrics"], a.metrics_out)
    if a.layouts_out:
        Path(a.layouts_out).write_text(json.dumps(per_layout(entries), indent=1), encoding="utf-8")
    if a.gate_out:
        Path(a.gate_out).write_text(json.dumps(gate_outcomes(entries), indent=1), encoding="utf-8")
    for c in report["cells"]:
        print(json.dumps({k: v for k, v in c.items() if k != "layouts"}))
    print(json.dumps({"metrics": report["metrics"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
