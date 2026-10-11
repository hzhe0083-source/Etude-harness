"""A/A noise calibration of ε (design §1 门, §3.2 A/A row, §3.3 "ε 校准", §6 停止规则).

``ε = max(eps_min, max_{i<j} |S_i − S_j| + 1)`` where ``S_i`` are the success
counts of the incumbent re-run on V (3 times, same layouts, so only PhysX and
flow-matching noise remain). ``eps_min`` = 4 (COAP 2610.12369 App. B floor).
``δ_AA`` = the max pairwise difference itself (§6 condition 3). ε above
``gate.eps_max`` (15) → enlarge V to 150 (§5 M3 卡点) / stop (§6).

CLI::

    python -m etude.evolution.aa_noise --counts 41 38 44
    # Research OS $RESEARCH_METRICS files, grouped per A/A repetition. view_aa.json has one
    # Run per (task, rep), so each rep is the SUM over the dev tasks; every rep must cover
    # the same V that L3 tests (default 100 trials = 2 tasks × 50 layouts):
    python -m etude.evolution.aa_noise --metrics rep1=sb_r1.json,pb_r1.json rep2=sb_r2.json,pb_r2.json \
        rep3=sb_r3.json,pb_r3.json [--expect-trials 100]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

from etude.evolution.gates import RunFn, coerce_outcomes, gate_params


@dataclass
class AAResult:
    """A/A calibration result (design §3.3)."""

    successes: list[int]
    trials: list[int]
    delta_aa: int
    eps: int
    eps_min: int
    eps_max: int
    too_noisy: bool
    per_run: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"successes": self.successes, "trials": self.trials, "delta_aa": self.delta_aa, "eps": self.eps,
                "eps_min": self.eps_min, "eps_max": self.eps_max, "too_noisy": self.too_noisy}


def max_pairwise_diff(counts: Sequence[int]) -> int:
    """``max |S_i − S_j|`` over pairs (0 for fewer than 2 runs)."""
    return max((abs(a - b) for a, b in combinations(counts, 2)), default=0)


def eps_from_counts(counts: Sequence[int], eps_min: int | None = None, root: str | Path | None = None) -> int:
    """``ε = max(eps_min, max pairwise diff + 1)`` (design §3.3)."""
    if len(counts) < 2:
        raise ValueError("A/A calibration needs at least 2 runs")
    lo = int(gate_params(root)["gate.eps_min"] if eps_min is None else eps_min)
    return max(lo, max_pairwise_diff(counts) + 1)


def aa_from_counts(counts: Sequence[int], trials: Sequence[int] | None = None, root: str | Path | None = None) -> AAResult:
    """Build :class:`AAResult` from success counts (e.g. Research OS ``successes``)."""
    gp = gate_params(root)
    eps = eps_from_counts(counts, root=root)
    hi = int(gp["gate.eps_max"])
    tr = list(trials) if trials is not None else [0] * len(counts)
    if len(set(t for t in tr if t)) > 1:
        raise ValueError(f"A/A runs must use the same V (trials differ: {tr})")
    return AAResult(list(map(int, counts)), tr, max_pairwise_diff(counts), eps, int(gp["gate.eps_min"]), hi, eps > hi)


def calibrate(run: RunFn, ref: Any, V: Sequence[str], n_runs: int = 3, root: str | Path | None = None) -> AAResult:
    """Run the incumbent ``n_runs`` times on V through the injected runner and derive ε (design §3.3)."""
    counts, trials, per = [], [], []
    for _ in range(n_runs):
        out = coerce_outcomes(run(ref, list(V)))
        stable = {k: v for k, v in out.items() if not v.unstable and k in set(V)}
        counts.append(sum(v.ok for v in stable.values()))
        trials.append(len(stable))
        per.append({"successes": counts[-1], "trials": trials[-1]})
    res = aa_from_counts(counts, None, root)
    res.trials, res.per_run = trials, per
    return res


def counts_from_metrics(paths: Sequence[str | Path]) -> tuple[list[int], list[int]]:
    """Read Research OS evaluation metrics JSON files ``{successes, trials, success_rate}``
    (one count per file; use :func:`counts_from_metric_groups` for per-task A/A Runs)."""
    s, t = [], []
    for p in paths:
        d: Mapping[str, Any] = json.loads(Path(p).read_text(encoding="utf-8"))
        s.append(int(d["successes"]))
        t.append(int(d["trials"]))
    return s, t


def parse_metric_groups(args: Sequence[str]) -> dict[str, list[str]]:
    """``["rep1=a.json,b.json", "rep2=c.json,d.json"]`` → ``{"rep1": [...], "rep2": [...]}``."""
    groups: dict[str, list[str]] = {}
    for a in args:
        name, sep, files = a.partition("=")
        if not sep or not name or not files:
            raise ValueError(f"--metrics expects REP=file1.json,file2.json (one group per A/A repetition), got {a!r}")
        if name in groups:
            raise ValueError(f"repetition {name!r} given twice")
        groups[name] = [f for f in files.split(",") if f]
    return groups


def counts_from_metric_groups(groups: Mapping[str, Sequence[str | Path]],
                              expect_trials: int | None = None) -> tuple[list[int], list[int]]:
    """Per-repetition ``(successes, trials)``: sum over the files of each group (one per
    task; view_aa.json rows = task) before any pairwise difference (design §3.3 ε 校准).

    Refuses groups with different file counts or different task sets (a file's
    optional ``task`` key), and, with ``expect_trials``, any repetition whose summed
    trials differ from it (the V size L3 tests on, e.g. 100)."""
    if len(groups) < 2:
        raise ValueError("A/A calibration needs at least 2 repetitions")
    sizes = {name: len(files) for name, files in groups.items()}
    if len(set(sizes.values())) != 1:
        raise ValueError(f"repetitions have different numbers of task files: {sizes}")
    counts, trials, tasksets = [], [], {}
    for name, files in groups.items():
        k = n = 0
        tasks = []
        for f in files:
            d: Mapping[str, Any] = json.loads(Path(f).read_text(encoding="utf-8"))
            k += int(d["successes"])
            n += int(d["trials"])
            if d.get("task"):
                tasks.append(str(d["task"]))
        counts.append(k)
        trials.append(n)
        tasksets[name] = tuple(sorted(tasks))
    if len({ts for ts in tasksets.values() if ts}) > 1:
        raise ValueError(f"repetitions cover different task sets: {tasksets}")
    if expect_trials is not None and any(t != expect_trials for t in trials):
        raise ValueError(f"per-repetition trials {dict(zip(groups, trials))} != V size {expect_trials} used by L3")
    return counts, trials


def main(argv: Sequence[str] | None = None) -> int:
    """CLI (design §3.3 ε 校准)."""
    ap = argparse.ArgumentParser(prog="python -m etude.evolution.aa_noise")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--counts", type=int, nargs="+")
    g.add_argument("--metrics", nargs="+", metavar="REP=FILE[,FILE...]",
                   help="Research OS metrics files grouped per A/A repetition (summed over tasks)")
    g.add_argument("--demo", action="store_true")
    ap.add_argument("--expect-trials", type=int, default=100,
                    help="V size L3 tests on; every repetition must sum to it (default 100; 0 = no check)")
    a = ap.parse_args(argv)
    if a.demo:
        a.counts = [41, 38, 44]
    if a.metrics:
        counts, trials = counts_from_metric_groups(parse_metric_groups(a.metrics), a.expect_trials or None)
        res = aa_from_counts(counts, trials)
    else:
        res = aa_from_counts(a.counts)
    print(json.dumps(res.to_dict()))
    if res.too_noisy:
        print(f"ε={res.eps} > eps_max={res.eps_max}: enlarge V to 150 (design §5 M3) or stop (§6)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
