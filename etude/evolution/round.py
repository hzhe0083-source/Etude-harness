"""One harness-loop evolution round (design §3.1 harness 回路, §3.3 ``evolve_round``, §3.4, §6 停止规则).

``evolve_round`` takes an :class:`etude.core.interfaces.Editor` (P6, injected)
and an episode runner (injected; ``run(ref, layouts) -> {layout: outcome}``),
then: propose → L0 (incl. medoids ⊆ E) → worktree + patch + commit → L1
(medoids) → L2 (F) → L3 (paired over all of V vs. incumbent cache, ε from
A/A) → cost rule → lineage receipt. Every proposal gets a receipt, also when
a gate or the runner raises.

Merging is NOT part of the round: a gate "accept" keeps ``cand/<id>`` and
records ``state.pending``; the merge happens only in :func:`apply_accept`
after the human has granted the Research OS Decision (research/AGENTS.md:
a Decision based on evidence needs human authority). On apply:
incumbent := merge commit, its V cache is re-run, F |= medoids (E only).
:func:`discard_pending` drops a gate-accepted candidate the human refused.

Preconditions (checked before anything is spent): ε calibrated from A/A
(``state.eps`` + ``state.eps_source``; never COAP's 4 by default) and a main
checkout that is on the base branch and clean apart from the Research OS
ledger files.

Information flow (design §3.2): the editor gets only E-split evidence and,
afterwards, :func:`etude.evolution.lineage.editor_view` (accept/reject).

The runner is where real evaluation plugs in: for v1 that is a Research OS
evaluation Run of the pushed candidate commit, scored offline by P5
(``scorer/run_official.sh`` → ``etude.scorer.results`` per-layout table).
:class:`TableRunner` replays precomputed per-layout tables (demo / offline replay).
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from etude import REPO_ROOT
from etude.core.interfaces import Editor
from etude.core.splits import keys_outside
from etude.core.types import EVOLVABLE_SURFACES, GateResults, Receipt
from etude.evolution import gates as G
from etude.evolution.lineage import (default_lineage_dir, diff_sha, editor_view, make_receipt, next_receipt_id,
                                     write_receipt)
from etude.evolution.worktree import Candidate, GitError, WorktreeManager


@dataclass
class CandidateRef:
    """What a runner receives: candidate id, exact commit and a checkout path."""

    cand_id: str
    commit: str
    path: str


@dataclass
class EvolutionState:
    """Persistent loop state (``<lineage dir>/state.json``, outside the repo; it holds
    per-layout V outcomes P6 must not see, design §3.2)."""

    incumbent_id: str = "c0000"
    incumbent_commit: str = ""
    incumbent_cache: dict[str, dict[str, Any]] = field(default_factory=dict)  # V outcomes of the incumbent
    #: Research OS Run label of the incumbent's V run (receipt / Observation evidence)
    incumbent_research_run: str | None = None
    F: list[str] = field(default_factory=list)                                 # historical regression layouts (E)
    next_cand: int = 1
    rejects_in_row: int = 0
    V_uses: int = 0
    #: ε from A/A (design §1 门, §3.3); None until :func:`set_eps` — evolve_round refuses to run without it
    eps: float | None = None
    #: provenance of ε: {"aa": AAResult.to_dict(), "aa_runs": [Research OS Run labels], "set_at": ...}
    eps_source: dict[str, Any] | None = None
    #: gate-accepted candidate awaiting the human Decision (see apply_accept / discard_pending)
    pending: dict[str, Any] | None = None
    seen_diff_shas: list[str] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "EvolutionState":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=1) + "\n", encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "EvolutionState":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class RoundConfig:
    """Per-loop constants (design §3.1, §3.2, §4 receipt ``evaluated_on``/``env``)."""

    surface: str
    V: list[str]                                  # paired held-out layout keys (seed 1)
    V_label: str = "seed1[0:50]"
    tasks: list[str] = field(default_factory=lambda: ["stack_bowls", "put_bottles_into_dustbin"])
    env: dict[str, str] = field(default_factory=dict)
    repo: Path = REPO_ROOT
    base_branch: str = "main"
    lineage_dir: Path | None = None
    research_run: str | None = None
    run_selftest: bool = True

    def __post_init__(self) -> None:
        if self.surface not in EVOLVABLE_SURFACES:
            raise ValueError(f"surface must be one of {EVOLVABLE_SURFACES}")
        self.repo = Path(self.repo)
        if self.lineage_dir is None:
            # outside the repo (receipts and state carry V results; design §3.2 P6 sees one bit)
            self.lineage_dir = default_lineage_dir()


@dataclass
class RoundResult:
    """Receipt + where it was written + what the editor may see."""

    receipt: Receipt
    path: Path
    gates: list[G.GateOutcome]
    editor_feedback: dict[str, str]
    candidate: Candidate | None = None


class TableRunner:
    """Replays per-layout outcome tables keyed by candidate id (demo / offline replay of P5 tables).

    ``tables[cand_id]`` is one ``{layout: outcome}`` dict or a list of them
    returned on successive calls (e.g. three A/A runs).
    """

    def __init__(self, tables: Mapping[str, Mapping[str, Any] | Sequence[Mapping[str, Any]]]):
        self.tables = dict(tables)
        self.calls: list[tuple[str, int]] = []
        self._n: dict[str, int] = {}

    def __call__(self, ref: CandidateRef, layouts: Sequence[str]) -> dict[str, Any]:
        t = self.tables.get(ref.cand_id)
        if t is None:
            raise KeyError(f"no result table for candidate {ref.cand_id}")
        if isinstance(t, Sequence) and not isinstance(t, Mapping):
            i = self._n.get(ref.cand_id, 0)
            self._n[ref.cand_id] = i + 1
            t = t[min(i, len(t) - 1)]
        self.calls.append((ref.cand_id, len(layouts)))
        return {k: t[k] for k in layouts if k in t}


def rule_budget(k: int, root: str | Path | None = None) -> int:
    """Annealed rule budget for round ``k`` (RRSI 2609.24972 Eq. 4: 3 → 1; design §3.1 编辑者)."""
    return max(1, int(G.gate_params(root)["gate.diff_max_rules"]) - (k - 1))


def build_evidence(surface: str, clusters: Sequence[Mapping[str, Any]] = (), traces: Sequence[str] = (),
                   medoids: Sequence[str] = (), k: int = 1, root: str | Path | None = None) -> dict[str, Any]:
    """Editor evidence (design §3.1 证据: E-split traces/verdicts clustered by earliest failing node).

    Keys: surface, clusters, traces, budget, medoids. Never V/T results.
    """
    gp = G.gate_params(root)
    return {"surface": surface, "clusters": list(clusters), "traces": list(traces),
            "budget": {"diff_max_lines": int(gp["gate.diff_max_lines"]), "diff_max_rules": rule_budget(k, root)},
            "medoids": list(medoids)}


def stop_reason(state: EvolutionState, root: str | Path | None = None) -> str | None:
    """§6 停止规则: 3 rejects in a row, V reused by 6 candidates, or ε > eps_max."""
    gp = G.gate_params(root)
    if state.rejects_in_row >= int(gp["gate.stop_after_rejects"]):
        return f"{state.rejects_in_row} consecutive rejects"
    if state.V_uses >= int(gp["gate.max_V_reuse"]):
        return f"V reused by {state.V_uses} candidates"
    if state.eps is not None and state.eps > gp["gate.eps_max"]:
        return f"eps {state.eps} > eps_max {gp['gate.eps_max']} (enlarge V to 150)"
    return None


def incumbent_ref(state: EvolutionState, cfg: RoundConfig) -> CandidateRef:
    return CandidateRef(state.incumbent_id, state.incumbent_commit, str(cfg.repo))


def set_eps(state: EvolutionState, aa: Any, aa_runs: Sequence[str] = ()) -> float:
    """Install ε from an A/A calibration (``etude.evolution.aa_noise.AAResult``) and record
    its provenance (design §1 门 "ε 不直接用 COAP 的 4：先用 A/A 重跑校准", §3.3)."""
    d = aa.to_dict() if hasattr(aa, "to_dict") else dict(aa)
    state.eps = float(d["eps"])
    state.eps_source = {"aa": d, "aa_runs": list(aa_runs), "set_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    return state.eps


def _round_budget(k: int, root: str | Path | None) -> dict[str, int]:
    """L0 diff budget of round ``k`` from frozen thresholds only (never from the evidence dict the
    editor saw): lines = ``gate.diff_max_lines``, rules = :func:`rule_budget` (RRSI 3 → 1)."""
    gp = G.gate_params(root)
    return {"diff_max_lines": int(gp["gate.diff_max_lines"]), "diff_max_rules": rule_budget(k, root)}


def evolve_round(k: int, cfg: RoundConfig, state: EvolutionState, editor: Editor, runner: G.RunFn,
                 evidence: Mapping[str, Any] | None = None, wt: WorktreeManager | None = None) -> RoundResult:
    """Run one harness-loop round and write its receipt (design §3.3 ``evolve_round``).

    Mutates ``state`` (candidate counter, reject streak, V uses; ``pending`` on a gate
    accept). Never merges: see :func:`apply_accept`. Raises before spending anything
    when ε is not A/A-calibrated, a candidate is still pending, or the main checkout
    cannot take a later merge."""
    root = cfg.repo
    wt = wt or WorktreeManager(root)
    if state.pending:
        raise RuntimeError(f"candidate {state.pending.get('cand_id')} (receipt {state.pending.get('receipt_id')}) awaits "
                           "the human Decision: run apply_accept(...) after the grant, or discard_pending(...)")
    if state.eps is None or not state.eps_source:
        raise ValueError("ε is not calibrated: run the A/A reruns and set_eps(state, aa_result, aa_runs) first "
                         "(design §1 门, §3.3; COAP's 4 is only the floor)")
    pre = wt.preflight(cfg.base_branch)
    if pre:
        raise GitError("main checkout not ready for this round: " + "; ".join(pre))
    if not state.incumbent_commit:
        state.incumbent_commit = wt.rev(cfg.base_branch)
    budget = _round_budget(k, root)
    ev = copy.deepcopy(dict(evidence)) if evidence is not None else build_evidence(cfg.surface, k=k, root=root)
    ev["budget"] = dict(budget)
    ev.setdefault("surface", cfg.surface)
    offered_medoids = list(ev.get("medoids") or [])
    patch = editor.propose(copy.deepcopy(ev))  # the editor only ever sees a copy
    cand_id = f"c{state.next_cand:04d}"
    state.next_cand += 1
    rid = next_receipt_id(cfg.lineage_dir)
    parent_id = state.incumbent_id
    gates = GateResults()
    outs: list[G.GateOutcome] = []
    cost: dict[str, list[float]] = {}
    evaluated_on: dict[str, Any] = {"medoids": list(patch.medoids)}
    decision, cand = "reject", None
    rollback = ""

    written: list[RoundResult] = []

    def finish() -> RoundResult:
        if written:  # a receipt was already written for this proposal
            return written[0]
        if decision == "accept":
            state.rejects_in_row = 0
        else:
            state.rejects_in_row += 1
        r = make_receipt(rid, parent_id, cand_id, patch.surface,
                         patch.diff, patch.hypothesis, gates, cost, decision, evaluated_on, cfg.env,
                         rollback or wt.rollback_cmd(None, cand), cfg.research_run)
        path = write_receipt(r, cfg.lineage_dir)
        state.receipts.append(rid)
        state.seen_diff_shas.append(diff_sha(patch.diff))
        written.append(RoundResult(r, path, outs, editor_view(r), cand))
        return written[0]

    try:
        # -- L0 (budget from frozen thresholds; medoids must be E layouts the evidence offered) --
        g0 = G.L0(patch, root, state.seen_diff_shas, budget["diff_max_lines"], budget["diff_max_rules"],
                  run_selftest=cfg.run_selftest)
        bad = keys_outside(patch.medoids, "E", root)
        if bad:
            g0.reasons.append(f"medoids outside the E split (V/T/unknown layouts may not run in L1): {bad[:5]}")
        if offered_medoids:
            extra = [m for m in patch.medoids if m not in set(offered_medoids)]
            if extra:
                g0.reasons.append(f"medoids not among the evidence medoids: {extra[:5]}")
        g0.ok = g0.summary = not g0.reasons
        outs.append(g0)
        gates.L0 = g0.ok
        if not g0.ok:
            return finish()
        # -- worktree + patch + commit -------------------------------------------
        try:
            cand = wt.add(cand_id, state.incumbent_commit)
            wt.apply_patch(cand, patch.diff)
            wt.commit(cand, f"{cand_id}: {patch.surface}: {patch.hypothesis}"[:200], [f"evolvable/{patch.surface}"])
        except GitError as e:
            outs.append(G.GateOutcome("worktree", False, None, [str(e)]))
            gates.L0 = False
            return finish()
        ref = CandidateRef(cand_id, cand.commit or "", str(cand.path))
        # -- L1 / L2 -----------------------------------------------------------
        g1 = G.L1(runner, ref, patch.medoids, root=root)
        outs.append(g1)
        gates.L1 = g1.summary
        if not g1.ok:
            return finish()
        g2 = G.L2(runner, ref, state.F, root=root)
        outs.append(g2)
        gates.L2 = g2.summary
        if not g2.ok:
            return finish()
        # -- L3 over all of V + cost --------------------------------------------
        if not state.incumbent_cache:
            state.incumbent_cache = {k: v.to_dict() for k, v in G.coerce_outcomes(runner(incumbent_ref(state, cfg), cfg.V)).items()}
        cand_V = G.coerce_outcomes(runner(ref, cfg.V))
        state.V_uses += 1
        evaluated_on.update({"V": cfg.V_label, "tasks": list(cfg.tasks)})
        g3 = G.L3(cand_V, state.incumbent_cache, state.eps, root=root, V=cfg.V)
        outs.append(g3)
        gates.L3 = g3.summary
        evaluated_on["L3_pairs"] = {k: g3.details[k] for k in ("pairs", "V", "missing_cand", "missing_inc",
                                                                "unstable_cand", "unstable_inc")}
        evaluated_on["eps_source"] = {"eps": state.eps, "aa_runs": list((state.eps_source or {}).get("aa_runs") or []),
                                      "delta_aa": ((state.eps_source or {}).get("aa") or {}).get("delta_aa"),
                                      "aa_successes": ((state.eps_source or {}).get("aa") or {}).get("successes")}
        evaluated_on["research_runs"] = {"L3": cfg.research_run, "incumbent_V": state.incumbent_research_run,
                                         "aa": list((state.eps_source or {}).get("aa_runs") or [])}
        cost = G.receipt_cost(state.incumbent_cache, cand_V)
        c_ok, c_reasons = G.cost_ok(G.mean_cost(state.incumbent_cache), G.mean_cost(cand_V), g3.details["delta_sr"], root=root)
        outs.append(G.GateOutcome("cost", c_ok, cost, c_reasons))
        if not (g3.ok and c_ok):
            return finish()
        # -- gate accept: keep cand/<id>; merge only after the human Decision -------------
        decision = "accept"
        state.pending = {"receipt_id": rid, "cand_id": cand_id, "branch": cand.branch, "commit": cand.commit,
                         "base": cand.base, "surface": patch.surface, "medoids": list(patch.medoids)}
        rollback = (f"pending: not merged; cand/{cand_id} kept at {cand.commit}. After the Research OS Decision "
                    f"grant, apply_accept merges it; roll back with git revert -m 1 <merge-sha> (see {rid}.applied.json)")
        return finish()
    except Exception as e:  # runner / gate / git failure: still one receipt per proposal
        if written:
            raise
        outs.append(G.GateOutcome("error", False, None, [f"{type(e).__name__}: {e}"]))
        evaluated_on["error"] = f"{type(e).__name__}: {e}"[:500]
        try:
            wt.abort_merge()
        except GitError:
            pass
        decision = "reject"
        state.pending = None
        return finish()
    finally:
        if cand is not None:
            try:
                wt.remove(cand)  # branch kept (audit trail / pending merge)
            except GitError:
                pass


def _ledger_has(repo: Path, event_id: str) -> bool | None:
    """Whether ``research/ledger.json`` mentions ``event_id`` (None if there is no ledger)."""
    p = Path(repo) / "research" / "ledger.json"
    if not p.is_file():
        return None
    return event_id in p.read_text(encoding="utf-8")


def apply_accept(cfg: RoundConfig, state: EvolutionState, runner: G.RunFn, grant_event_id: str,
                 wt: WorktreeManager | None = None, incumbent_research_run: str | None = None) -> dict[str, Any]:
    """Merge the gate-accepted pending candidate AFTER the human granted the Decision
    (design §3.3 ``if ok: merge(wt); incumbent = wt; incumbent_cache = rerun(V); F |= medoids``;
    research/AGENTS.md: evidence-based Decisions need human authority).

    ``grant_event_id`` is the Research OS ``authority grant`` event id; when a ledger exists
    it must appear in it. Writes ``<rid>.applied.json`` next to the receipt (receipts are
    append-only). A failed merge is aborted (main is never left half-merged) and the
    candidate stays pending."""
    p = state.pending
    if not p:
        raise RuntimeError("no gate-accepted candidate is pending")
    if not grant_event_id or not str(grant_event_id).strip():
        raise ValueError("apply_accept needs the human's authority grant event id")
    if _ledger_has(cfg.repo, str(grant_event_id)) is False:
        raise ValueError(f"grant {grant_event_id!r} not found in research/ledger.json")
    wt = wt or WorktreeManager(cfg.repo)
    pre = wt.preflight(cfg.base_branch)
    if pre:
        raise GitError("main checkout not ready to merge: " + "; ".join(pre))
    cand = Candidate(p["cand_id"], p["branch"], wt.wt_dir / p["cand_id"], p["base"], p["commit"])
    merge_sha = wt.merge(cand, cfg.base_branch,
                         f"evolution {p['receipt_id']}: accept {p['cand_id']} ({p['surface']}); grant {grant_event_id}")
    state.incumbent_id, state.incumbent_commit = p["cand_id"], merge_sha
    state.incumbent_cache = {k: v.to_dict() for k, v in G.coerce_outcomes(runner(incumbent_ref(state, cfg), cfg.V)).items()}
    state.incumbent_research_run = incumbent_research_run
    ok_medoids = [m for m in p["medoids"] if not keys_outside([m], "E", cfg.repo)]
    state.F = list(dict.fromkeys([*state.F, *ok_medoids]))
    state.pending = None
    rec = {"receipt_id": p["receipt_id"], "candidate": p["cand_id"], "merge_sha": merge_sha,
           "grant_event_id": str(grant_event_id), "rollback": f"git revert -m 1 {merge_sha}",
           "incumbent_V_run": incumbent_research_run, "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    out = Path(cfg.lineage_dir) / f"{p['receipt_id']}.applied.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    return rec


def discard_pending(cfg: RoundConfig, state: EvolutionState, reason: str) -> dict[str, Any]:
    """The human refused the gate-accepted candidate: keep the incumbent, count a reject
    (design §6 停止规则), record ``<rid>.declined.json``. The branch stays for audit."""
    p = state.pending
    if not p:
        raise RuntimeError("no gate-accepted candidate is pending")
    state.pending = None
    state.rejects_in_row += 1
    rec = {"receipt_id": p["receipt_id"], "candidate": p["cand_id"], "declined": True, "reason": reason,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    out = Path(cfg.lineage_dir) / f"{p['receipt_id']}.declined.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    return rec


__all__ = ["CandidateRef", "EvolutionState", "RoundConfig", "RoundResult", "TableRunner", "apply_accept",
           "build_evidence", "discard_pending", "evolve_round", "incumbent_ref", "rule_budget", "set_eps",
           "stop_reason"]
