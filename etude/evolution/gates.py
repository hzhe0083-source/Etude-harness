"""Evolution gates L0–L3 + cost rule (design §1 门, §3.1 门 row, §3.3 evolve_round, §6).

* **L0** static: surface whitelist, single-file diff, diff budget (≤40 changed
  lines; recovery.yaml ≤3 rules, annealed per round), no identical
  resubmission, clean apply, leakage regexes on added lines, forbidden names /
  paths in the patched file, recovery.yaml structure (no threshold overrides),
  and the §3.5 checker self-test must still block every cheat candidate.
* **L1** medoid layouts re-run (≤5 episodes): 100 % must pass.
* **L2** historical regression set F (≤20 episodes): at most 1 failure.
* **L3** paired held-out V: ``b − c ≥ ε`` and one-sided exact McNemar
  ``p ≤ 0.05`` (``p = P[X ≥ b], X ~ Bin(b+c, ½)``, computed with ``math.comb``),
  over all of V (missing/unstable candidate outcomes = failures; unstable
  rate > 0.05 on either side rejects).
* **cost_ok**: ``ΔC ≤ β0 + β1·ΔSR`` (RRSI 2609.24972 §3.3) on relative VLM
  tokens/episode and wall-time/episode.

Episode running is injected: ``run(ref, layouts) -> {layout_key: outcome}``
where an outcome is an :class:`EpisodeOutcome`, a bool, or a dict with
``ok`` (``etude.scorer.results.gate_outcomes`` produces that format). Gates never read
files under ``gate/V`` themselves; P5 hands outcomes in.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import yaml

from etude import REPO_ROOT
from etude.core.types import EVOLVABLE_SURFACES, L3Result, ON_FAIL_THEN, Patch, VERDICT_KINDS

# Not in frozen/params/thresholds.yaml yet (see open issue); defaults here.
DEFAULT_COST_BETA0 = 0.10   # relative cost increase allowed at ΔSR = 0
DEFAULT_COST_BETA1 = 1.0    # extra relative cost allowed per unit ΔSR (0–1 scale)

_GATE_DEFAULTS: dict[str, float] = {
    "gate.mcnemar_p_max": 0.05,
    "gate.eps_min": 4,
    "gate.eps_max": 15,
    "gate.L1_max_episodes": 5,
    "gate.L2_max_episodes": 20,
    "gate.L2_max_failures": 1,
    "gate.diff_max_lines": 40,
    "gate.diff_max_rules": 3,
    "gate.stop_after_rejects": 3,
    "gate.max_V_reuse": 6,
    "gate.cost_beta0": DEFAULT_COST_BETA0,
    "gate.cost_beta1": DEFAULT_COST_BETA1,
    "scorer.unstable_rate_review": 0.05,
}


def gate_params(root: str | Path | None = None) -> dict[str, float]:
    """Gate constants from ``frozen/params/thresholds.yaml`` with defaults (design §3.1, §6)."""
    from etude.core.config import load_thresholds

    out = dict(_GATE_DEFAULTS)
    try:
        th = load_thresholds(root)
    except FileNotFoundError:
        th = {}
    out.update({k: v for k, v in th.items() if k in out})
    return out


# ---------------------------------------------------------------------------
# outcomes
# ---------------------------------------------------------------------------


@dataclass
class EpisodeOutcome:
    """Official per-layout outcome as delivered by P5 (design §3.0 适应度, §3.2).

    ``ok`` = official judge success; ``unstable`` episodes (excluded by the
    official counter) are dropped from pairing.
    """

    ok: bool
    score: float = 0.0
    vlm_tokens: float = 0.0
    wall_s: float = 0.0
    unstable: bool = False

    @classmethod
    def coerce(cls, x: Any) -> "EpisodeOutcome":
        if isinstance(x, EpisodeOutcome):
            return x
        if isinstance(x, bool):
            return cls(ok=x, score=1.0 if x else 0.0)
        if isinstance(x, Mapping):
            if "ok" not in x:
                raise KeyError("episode outcome dict needs 'ok' (use etude.scorer.results.gate_outcomes)")
            ok = bool(x["ok"])
            return cls(ok=ok, score=float(x.get("score", 1.0 if ok else 0.0) or 0.0),
                       vlm_tokens=float(x.get("vlm_tokens", 0) or 0), wall_s=float(x.get("wall_s", 0) or 0),
                       unstable=bool(x.get("unstable", False)))
        raise TypeError(f"cannot interpret episode outcome {x!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "score": self.score, "vlm_tokens": self.vlm_tokens, "wall_s": self.wall_s,
                "unstable": self.unstable}


Outcomes = dict[str, EpisodeOutcome]
RunFn = Callable[[Any, Sequence[str]], Mapping[str, Any]]


def coerce_outcomes(raw: Mapping[str, Any]) -> Outcomes:
    """Normalise a runner result to ``{layout: EpisodeOutcome}``."""
    return {str(k): EpisodeOutcome.coerce(v) for k, v in raw.items()}


@dataclass
class GateOutcome:
    """Result of one gate: ``ok``, the receipt summary value and reasons."""

    gate: str
    ok: bool
    summary: Any = None          # L0: bool, L1/L2: "k/n", L3: L3Result
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        s = self.summary.to_dict() if hasattr(self.summary, "to_dict") else self.summary
        why = "; ".join(self.reasons[:4]) + (" …" if len(self.reasons) > 4 else "")
        return f"{self.gate}: {'pass' if self.ok else 'FAIL'} {s}" + (f" ({why})" if why else "")


# ---------------------------------------------------------------------------
# L0 static
# ---------------------------------------------------------------------------


def diff_stats(diff: str) -> tuple[int, int]:
    """(added, removed) content lines of a unified diff."""
    add = rem = 0
    for ln in diff.splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            add += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            rem += 1
    return add, rem


def apply_patch_text(patch: Patch, root: str | Path | None = None) -> str:
    """Return the surface text after applying ``patch.diff`` to ``<root>/evolvable/<surface>``
    (``git apply --unidiff-zero`` in a scratch dir; raises ValueError if it does not apply)."""
    r = Path(root) if root is not None else REPO_ROOT
    rel = Path("evolvable") / patch.surface
    with tempfile.TemporaryDirectory(prefix="etude-l0-") as td:
        dst = Path(td) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        src = r / rel
        if src.exists():
            shutil.copy2(src, dst)
        pf = Path(td) / "cand.patch"
        pf.write_text(patch.diff if patch.diff.endswith("\n") else patch.diff + "\n", encoding="utf-8")
        p = subprocess.run(["git", "apply", "--unidiff-zero", "--whitespace=nowarn", "--recount", str(pf)],
                           cwd=td, capture_output=True, text=True)
        if p.returncode != 0:
            raise ValueError(f"patch does not apply: {p.stderr.strip()}")
        return dst.read_text(encoding="utf-8")


def _rule_key(rule: Any) -> str:
    return yaml.safe_dump(rule, sort_keys=True)


def check_recovery_yaml(old_text: str, new_text: str, max_rules: int, root: str | Path | None = None) -> list[str]:
    """Structure + rule budget for ``evolvable/rules/recovery.yaml`` (design §3.1 harness surface 2)."""
    from etude.core.config import load_thresholds

    errs: list[str] = []
    try:
        new = yaml.safe_load(new_text) or {}
        old = yaml.safe_load(old_text) or {}
    except yaml.YAMLError as e:
        return [f"recovery.yaml does not parse: {e}"]
    if not isinstance(new, Mapping):
        return ["recovery.yaml must be a mapping"]
    # new top-level sections change the rule engine's contract: only keys the incumbent already has
    allowed_top = {"version", "defaults", "on_verdict", "rules"} | (set(old) if isinstance(old, Mapping) else set())
    extra = set(new) - allowed_top
    if extra:
        errs.append(f"recovery.yaml: new top-level keys {sorted(extra)} (structure change needs human sign-off)")
    d = new.get("defaults") or {}
    ranges = {"retry": (0, 3), "exec_steps": (1, 50), "stall_chunks": (1, 10), "max_chunks": (1, 40)}
    for k, (lo, hi) in ranges.items():
        if k in d and not (isinstance(d[k], int) and lo <= d[k] <= hi):
            errs.append(f"recovery.yaml: defaults.{k}={d[k]!r} outside node-schema range [{lo},{hi}]")
    if "then" in d and d["then"] not in ON_FAIL_THEN:
        errs.append(f"recovery.yaml: defaults.then={d['then']!r} not in {ON_FAIL_THEN}")
    ov = new.get("on_verdict") or {}
    bad = set(ov) - (set(VERDICT_KINDS) - {"Pass"})
    if bad:
        errs.append(f"recovery.yaml: on_verdict keys {sorted(bad)} not verdict kinds")
    try:
        th_names = set(load_thresholds(root))
    except FileNotFoundError:
        th_names = set()

    def walk(x: Any, where: str = "") -> None:
        if isinstance(x, Mapping):
            for k, v in x.items():
                if str(k) in th_names:
                    errs.append(f"recovery.yaml: {where}{k} overrides frozen threshold")
                walk(v, f"{where}{k}.")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{where}[{i}].")
        elif isinstance(x, str) and x in th_names:
            errs.append(f"recovery.yaml: {where.rstrip('.')} references frozen threshold {x}")

    walk(new)
    old_rules = {_rule_key(x) for x in (old.get("rules") or [])} if isinstance(old, Mapping) else set()
    new_rules_l = new.get("rules") or []
    if not isinstance(new_rules_l, list):
        errs.append("recovery.yaml: rules must be a list")
        new_rules_l = []
    new_rules = {_rule_key(x) for x in new_rules_l}
    changed = len(old_rules ^ new_rules)
    if changed > max_rules:
        errs.append(f"recovery.yaml: {changed} rules added/removed > budget {max_rules}")
    return errs


def L0(patch: Patch, root: str | Path | None = None, seen_diff_shas: Iterable[str] = (),
       max_lines: int | None = None, max_rules: int | None = None, run_selftest: bool = True) -> GateOutcome:
    """Static gate (design §3.1 L0: schema、AST 禁读、泄漏正则、diff 预算)."""
    from etude.evolution.lineage import diff_sha
    from etude.frozen.ast_check import Policy, check_diff_paths, check_text, selftest

    gp = gate_params(root)
    max_lines = int(gp["gate.diff_max_lines"] if max_lines is None else max_lines)
    max_rules = int(gp["gate.diff_max_rules"] if max_rules is None else max_rules)
    reasons: list[str] = []
    details: dict[str, Any] = {}
    if patch.surface not in EVOLVABLE_SURFACES:
        return GateOutcome("L0", False, False, [f"surface {patch.surface!r} not in {EVOLVABLE_SURFACES}"])
    pol = Policy.load(root)
    target = f"evolvable/{patch.surface}"
    reasons += [str(f) for f in check_diff_paths(patch.diff, pol, "<patch>")]
    from etude.frozen.ast_check import diff_paths

    touched = diff_paths(patch.diff)
    if touched and touched != [target]:
        reasons.append(f"patch must touch exactly {target}, touches {touched}")
    add, rem = diff_stats(patch.diff)
    details["lines"] = add + rem
    if add + rem > max_lines:
        reasons.append(f"diff budget: {add + rem} changed lines > {max_lines}")
    if add + rem == 0:
        reasons.append("empty patch")
    sha = diff_sha(patch.diff)
    details["diff_sha"] = sha
    if sha in set(seen_diff_shas):
        reasons.append("identical candidate resubmitted (候选不得原样重交)")
    for h in pol.leak.scan_diff(patch.diff, "<patch>"):
        reasons.append(str(h))
    for h in pol.leak.scan(patch.hypothesis, "<hypothesis>"):
        reasons.append(str(h))
    r = Path(root) if root is not None else REPO_ROOT
    old_text = (r / target).read_text(encoding="utf-8") if (r / target).exists() else ""
    try:
        new_text = apply_patch_text(patch, r)
    except ValueError as e:
        reasons.append(str(e))
        new_text = None
    if new_text is not None:
        reasons += [str(f) for f in check_text(new_text, target, pol) if f.severity == "error"]
        if patch.surface == "rules/recovery.yaml":
            reasons += check_recovery_yaml(old_text, new_text, max_rules, r)
    if run_selftest and not selftest(r, verbose=False):
        reasons.append("ast_check --selftest regression: a §3.5 cheat candidate is no longer blocked")
    ok = not reasons
    return GateOutcome("L0", ok, ok, reasons, details)


# ---------------------------------------------------------------------------
# L1 / L2 (re-runs through the injected runner)
# ---------------------------------------------------------------------------


def L1(run: RunFn, ref: Any, medoids: Sequence[str], max_episodes: int | None = None,
       root: str | Path | None = None) -> GateOutcome:
    """Medoid re-run (design §3.1 L1, Zetta 2608.16590 §2.5.2): every episode must pass."""
    n_max = int(gate_params(root)["gate.L1_max_episodes"] if max_episodes is None else max_episodes)
    layouts = list(islice(dict.fromkeys(medoids), n_max))
    if not layouts:
        return GateOutcome("L1", False, "0/0", ["no medoid layouts given"])
    out = coerce_outcomes(run(ref, layouts))
    missing = [k for k in layouts if k not in out]
    passed = [k for k in layouts if k in out and out[k].ok]
    reasons = [f"missing outcome for {k}" for k in missing] + [f"failed {k}" for k in layouts if k in out and not out[k].ok]
    return GateOutcome("L1", len(passed) == len(layouts), f"{len(passed)}/{len(layouts)}", reasons, {"outcomes": out})


def L2(run: RunFn, ref: Any, F: Sequence[str], max_episodes: int | None = None, max_failures: int | None = None,
       root: str | Path | None = None) -> GateOutcome:
    """Historical regression set F (design §3.1 L2): ≤ ``gate.L2_max_failures`` failures in ≤20 episodes."""
    gp = gate_params(root)
    n_max = int(gp["gate.L2_max_episodes"] if max_episodes is None else max_episodes)
    f_max = int(gp["gate.L2_max_failures"] if max_failures is None else max_failures)
    layouts = list(dict.fromkeys(F))[-n_max:]  # most recent members of F
    if not layouts:
        return GateOutcome("L2", True, "0/0", [], {"outcomes": {}})
    out = coerce_outcomes(run(ref, layouts))
    passed = [k for k in layouts if k in out and out[k].ok]
    fails = len(layouts) - len(passed)
    reasons = [f"regressed {k}" for k in layouts if not (k in out and out[k].ok)]
    return GateOutcome("L2", fails <= f_max, f"{len(passed)}/{len(layouts)}", reasons if fails > f_max else [],
                       {"outcomes": out, "failures": fails})


# ---------------------------------------------------------------------------
# L3 paired held-out
# ---------------------------------------------------------------------------


def mcnemar_one_sided(b: int, c: int) -> float:
    """Exact one-sided McNemar p = P[X ≥ b], X ~ Binomial(b+c, ½) (design §3.3); 1.0 if b+c = 0."""
    n = b + c
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(b, n + 1)) / 2 ** n


def paired_counts(cand: Mapping[str, Any], inc: Mapping[str, Any], V: Sequence[str] | None = None) -> tuple[int, int, list[str]]:
    """``(b, c, keys)``: b = candidate-only wins, c = incumbent-only wins (design §3.3 paired_runs).

    With ``V`` every V layout counts: a layout the candidate did not report, or
    reported as unstable, is a candidate failure (so dropping or "unstabling"
    would-be losses cannot help; §3.5 mark_env_unstable). Layouts where the
    incumbent has no stable outcome are left out. Without ``V``: layouts
    present and stable on both sides (legacy)."""
    co, io = coerce_outcomes(cand), coerce_outcomes(inc)
    if V is None:
        keys = sorted(k for k in co.keys() & io.keys() if not co[k].unstable and not io[k].unstable)
    else:
        keys = [k for k in dict.fromkeys(V) if k in io and not io[k].unstable]

    def cand_ok(k: str) -> bool:
        return k in co and co[k].ok and not co[k].unstable

    b = sum(1 for k in keys if cand_ok(k) and not io[k].ok)
    c = sum(1 for k in keys if io[k].ok and not cand_ok(k))
    return b, c, keys


def L3(cand: Mapping[str, Any], inc: Mapping[str, Any], eps: float, p_max: float | None = None,
       root: str | Path | None = None, min_pairs: int = 1, V: Sequence[str] | None = None,
       unstable_max: float | None = None) -> GateOutcome:
    """Paired held-out gate (design §3.3): ``b − c ≥ ε`` and ``p ≤ p_max``.

    With ``V`` (as :func:`etude.evolution.round.evolve_round` passes it) the pairs
    cover all of V: missing or unstable candidate outcomes count as candidate
    failures, the incumbent cache must cover V, and either side's unstable rate
    above ``scorer.unstable_rate_review`` (0.05) rejects (design §3.5 P5 审计
    unstable 率). ``details`` records pairs / missing / unstable."""
    gp = gate_params(root)
    p_max = float(gp["gate.mcnemar_p_max"] if p_max is None else p_max)
    u_max = float(gp["scorer.unstable_rate_review"] if unstable_max is None else unstable_max)
    co, io = coerce_outcomes(cand), coerce_outcomes(inc)
    b, c, keys = paired_counts(cand, inc, V)
    p = mcnemar_one_sided(b, c)
    res = L3Result(b=b, c=c, eps=eps, p=round(p, 6))
    reasons = []
    vk = list(dict.fromkeys(V)) if V is not None else keys
    missing_inc = [k for k in vk if k not in io]
    missing_cand = [k for k in vk if k not in co]
    unstable_inc = [k for k in vk if k in io and io[k].unstable]
    unstable_cand = [k for k in vk if k in co and co[k].unstable]
    n_v = max(1, len(vk))
    if V is not None:
        min_pairs = max(min_pairs, len(vk) - len(unstable_inc))
        if missing_inc:
            reasons.append(f"incumbent V cache lacks {len(missing_inc)} of {len(vk)} layouts")
        if len(unstable_cand) / n_v > u_max:
            reasons.append(f"candidate unstable rate {len(unstable_cand)}/{len(vk)} > {u_max} (human review)")
        if len(unstable_inc) / n_v > u_max:
            reasons.append(f"incumbent unstable rate {len(unstable_inc)}/{len(vk)} > {u_max} (human review)")
    if len(keys) < min_pairs:
        reasons.append(f"only {len(keys)} paired layouts (need {min_pairs})")
    if b - c < eps:
        reasons.append(f"b−c = {b - c} < ε = {eps}")
    if p > p_max:
        reasons.append(f"McNemar p = {p:.4f} > {p_max}")
    n = len(keys)
    sr_c = sum(1 for k in keys if k in co and co[k].ok and not co[k].unstable) / n if n else 0.0
    sr_i = sum(io[k].ok for k in keys) / n if n else 0.0
    details = {"pairs": n, "V": len(vk), "missing_cand": len(missing_cand), "missing_inc": len(missing_inc),
               "unstable_cand": len(unstable_cand), "unstable_inc": len(unstable_inc),
               "missing_cand_keys": missing_cand[:20], "unstable_cand_keys": unstable_cand[:20],
               "sr_cand": sr_c, "sr_inc": sr_i, "delta_sr": sr_c - sr_i}
    return GateOutcome("L3", not reasons, res, reasons, details)


# ---------------------------------------------------------------------------
# cost rule
# ---------------------------------------------------------------------------


def mean_cost(outcomes: Mapping[str, Any]) -> dict[str, float]:
    """``{"vlm_tokens_per_ep", "wall_per_ep_s"}`` means over stable episodes (receipt ``cost`` items)."""
    o = [x for x in coerce_outcomes(outcomes).values() if not x.unstable]
    if not o:
        return {"vlm_tokens_per_ep": 0.0, "wall_per_ep_s": 0.0}
    return {"vlm_tokens_per_ep": sum(x.vlm_tokens for x in o) / len(o), "wall_per_ep_s": sum(x.wall_s for x in o) / len(o)}


def cost_ok(inc_cost: Mapping[str, float], cand_cost: Mapping[str, float], delta_sr: float,
            beta0: float | None = None, beta1: float | None = None, root: str | Path | None = None) -> tuple[bool, list[str]]:
    """RRSI cost rule ``ΔC ≤ β0 + β1·ΔSR`` per cost item, ΔC relative to the incumbent (design §3.1, §3.3)."""
    gp = gate_params(root)
    b0 = float(gp["gate.cost_beta0"] if beta0 is None else beta0)
    b1 = float(gp["gate.cost_beta1"] if beta1 is None else beta1)
    bound = b0 + b1 * delta_sr
    reasons = []
    for k in ("vlm_tokens_per_ep", "wall_per_ep_s"):
        i, c = float(inc_cost.get(k, 0.0)), float(cand_cost.get(k, 0.0))
        dc = (c - i) / i if i > 0 else (0.0 if c <= 0 else math.inf)
        if dc > bound:
            reasons.append(f"cost {k}: ΔC = {dc:+.3f} > β0 + β1·ΔSR = {bound:+.3f}")
    return not reasons, reasons


def receipt_cost(inc: Mapping[str, Any], cand: Mapping[str, Any]) -> dict[str, list[float]]:
    """Receipt ``cost`` = ``{item: [incumbent, candidate]}`` (design §4 收据)."""
    i, c = mean_cost(inc), mean_cost(cand)
    return {k: [round(i[k], 3), round(c[k], 3)] for k in ("vlm_tokens_per_ep", "wall_per_ep_s")}


__all__ = [
    "DEFAULT_COST_BETA0", "DEFAULT_COST_BETA1", "EpisodeOutcome", "GateOutcome", "L0", "L1", "L2", "L3",
    "Outcomes", "RunFn", "apply_patch_text", "check_recovery_yaml", "coerce_outcomes", "cost_ok", "diff_stats",
    "gate_params", "mcnemar_one_sided", "mean_cost", "paired_counts", "receipt_cost",
]
