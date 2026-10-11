"""``python -m etude.evolution --demo`` (design §3.3 evolve_round on fakes).

Builds a temporary git repo (:func:`etude.fakes.make_tmp_repo`), calibrates ε
from three fake A/A tables, then runs three rounds with :class:`FakeEditor`:

1. a clean one-line planner-prompt bullet whose fake V table is clearly better → accept + merge;
2. a bullet that leaks a layout id ("layout 7") → rejected at L0;
3. a harmless bullet whose V table is no better than the incumbent → rejected at L3;
4. a candidate that names a V layout as its medoid → rejected at L0.

A gate accept only marks the candidate pending; the demo then plays the human
grant and calls :func:`apply_accept`, which merges.

Prints each receipt (§4 JSON + ``research_run``) and the repo's git log.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from etude.core.types import DEV_TASKS
from etude.evolution import aa_noise
from etude.evolution.round import (EvolutionState, RoundConfig, TableRunner, apply_accept, build_evidence, evolve_round,
                                   set_eps, stop_reason)
from etude.fakes import FakeEditor, make_tmp_repo
from etude.scorer.results import layout_key


def _outcome(ok: bool, rng: random.Random, tokens: float, wall: float) -> dict[str, Any]:
    return {"ok": ok, "score": 1.0 if ok else round(rng.random() * 0.5, 2),
            "vlm_tokens": round(tokens * rng.uniform(0.9, 1.1)), "wall_s": round(wall * rng.uniform(0.9, 1.1), 1)}


def _tables(V: list[str], E: list[str]) -> dict[str, Any]:
    rng = random.Random(0)
    base = {k: rng.random() < 0.42 for k in V}
    e_base = {k: True for k in E}

    def noisy(src: dict[str, bool], flip: float, r: random.Random) -> dict[str, bool]:
        return {k: (not v) if r.random() < flip else v for k, v in src.items()}

    def table(v_ok: dict[str, bool], e_ok: dict[str, bool], tokens: float, wall: float, seed: int) -> dict[str, Any]:
        r = random.Random(seed)
        t = {k: _outcome(ok, r, tokens, wall) for k, ok in v_ok.items()}
        t.update({k: _outcome(ok, r, tokens, wall) for k, ok in e_ok.items()})
        return t

    aa = [table(noisy(base, 0.06, random.Random(s)), e_base, 90000, 205, s) for s in (11, 12, 13)]
    r = random.Random(21)
    better = {k: (v or r.random() < 0.35) and not (v and r.random() < 0.04) for k, v in base.items()}
    c1 = table(better, e_base, 94000, 210, 21)
    c3 = table(noisy(better, 0.05, random.Random(31)), e_base, 95000, 211, 31)
    return {"c0000": aa, "c0001": c1, "c0003": c3}


def demo(keep: bool = False) -> int:
    td = tempfile.mkdtemp(prefix="etude-evolution-demo-")
    repo = make_tmp_repo(Path(td) / "repo")
    V = [layout_key(t, 1, i) for t in DEV_TASKS for i in range(50)]
    E = [layout_key("stack_bowls", 0, 12), layout_key("put_bottles_into_dustbin", 0, 31),
         layout_key("stack_bowls", 0, 3), layout_key("put_bottles_into_dustbin", 0, 7)]
    runner = TableRunner(_tables(V, E))
    cfg = RoundConfig(surface="prompts/planner.md", V=V, repo=repo, lineage_dir=Path(td) / "lineage",
                      env={"robodojo": "266130a3ec41ba9b20b0e5648d2f3542f219c06c",
                           "xpolicylab": "bb9a0b5f5136a74503b679af830bfd0a3a837d5c",
                           "pi05": "joint-0/59999", "model": "claude-opus-5-5"})
    state = EvolutionState(F=E[2:])

    from etude.evolution.round import CandidateRef

    try:
        evolve_round(1, cfg, state, FakeEditor("- x"), runner)
        print("ERROR: evolve_round ran without an A/A-calibrated eps")
        return 1
    except ValueError as e:
        print("without A/A eps ->", str(e)[:70], "...")
    aa = aa_noise.calibrate(runner, CandidateRef("c0000", "", str(repo)), V, n_runs=3, root=repo)
    set_eps(state, aa, aa_runs=["E-M1-AA-R001", "E-M1-AA-R002", "E-M1-AA-R003"])
    print(f"A/A on V ({len(V)} layouts): successes={aa.successes} delta_AA={aa.delta_aa} -> eps={aa.eps}")
    state.incumbent_cache = runner.tables["c0000"][0]
    state.incumbent_research_run = "E-M3-R000"

    editors = [
        FakeEditor("- After a failed grasp, re-observe the wrist view before retrying."),
        FakeEditor("- In layout 7 the left bowl is occluded; start with the right arm."),
        FakeEditor("- Prefer the arm on the target's side of the head view."),
    ]
    for k, ed in enumerate(editors, 1):
        ev = build_evidence(cfg.surface, clusters=[{"earliest_failing_node": "n2", "count": 9}],
                            traces=["traces/ep_e_012"], medoids=E[:2], k=k, root=repo)
        res = evolve_round(k, cfg, state, ed, runner, ev)
        print(f"\n=== round {k}: {res.receipt.decision} ({res.path.name}); editor sees {res.editor_feedback}")
        for g in res.gates:
            print("  ", g)
        print(json.dumps(res.receipt.to_dict(), indent=1, ensure_ascii=False))
        if state.pending:
            # gate accept: nothing merged yet; the human grants the Decision, then apply_accept merges
            head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
            print(f"pending {state.pending['cand_id']} (main still at {head_before[:8]}); "
                  "after `authority grant` + `decision make` (human): apply_accept")
            applied = apply_accept(cfg, state, runner, grant_event_id="demo-grant-event", incumbent_research_run="E-M3-R002")
            print("applied:", json.dumps(applied))
    # a V/T layout offered as a medoid is refused at L0 (L1 may only rerun E layouts)
    leak = FakeEditor("- Re-check the gripper width after closing.")
    ev = build_evidence(cfg.surface, medoids=[V[0]], k=4, root=repo)
    res = evolve_round(4, cfg, state, leak, runner, ev)
    print(f"\n=== round 4 (V layout as medoid): {res.receipt.decision}; L0 = {res.gates[0]}")
    print("\nstate:", json.dumps({k: v for k, v in state.to_dict().items() if k != "incumbent_cache"}, indent=None))
    print("stop rule:", stop_reason(state, repo))
    log = subprocess.run(["git", "log", "--oneline", "--graph", "--all"], cwd=repo, capture_output=True, text=True).stdout
    print("\ngit log:\n" + log)
    head = (repo / "evolvable/prompts/planner.md").read_text().splitlines()[0]
    print("main:evolvable/prompts/planner.md line 1:", head)
    if not keep:
        import shutil

        shutil.rmtree(td, ignore_errors=True)
    else:
        print("kept", td)
    ok = [r for r in state.receipts]
    return 0 if len(ok) == 4 and res.receipt.decision == "reject" else 1


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m etude.evolution")
    ap.add_argument("--demo", action="store_true", help="three evolve_round calls on a temp git repo")
    ap.add_argument("--keep", action="store_true", help="keep the temp repo")
    a = ap.parse_args(argv)
    if not a.demo:
        ap.print_help()
        return 0
    return demo(a.keep)


if __name__ == "__main__":
    sys.exit(main())
