"""Evidence closure, R27 eligibility and derived projection fields (AGENTS.md R27-R29)."""

from .authority import contract_authority, policy_active
from .runs import child_of, needs_rerun

DEGRADED_BASIS = {"run": ("validity", ("invalid",)), "observation": ("status", ("retracted",)),
                  "claim": ("status", ("contested", "refuted"))}


def run_closure(state, refs):
    """(runs, directly referenced experiments): Experiment -> completed runs, Observation -> evidence, Run -> itself."""
    runs, experiments = [], set()
    for ref in refs:
        kind = state.kinds.get(ref)
        if kind == "experiment":
            experiments.add(ref)
            runs += [r for r in state.records[ref]["runs"] if state.records[r]["status"] == "completed"]
        elif kind == "observation":
            runs += [r for r in state.records[ref]["evidence"] if r in state.records]
        elif kind == "run":
            runs.append(ref)
    return list(dict.fromkeys(runs)), experiments


def _experiment_violations(state, experiments):
    found = []
    for ref in sorted(experiments):
        exp = state.records[ref]
        label = state.label(ref)
        if exp["mode"] == "scout" or exp["kind"] == "debug":
            found.append(f"R27: {label} is a scout/debug experiment; cite it in context, not supporting")
        contract = exp.get("contract") or {}
        if "baseline" not in contract or "primary_metric" not in contract:
            found.append(f"R27: {label} has no baseline/primary_metric")
    qualifying = [ref for ref in experiments if _qualifies(state.records[ref])]
    if not qualifying:
        found.append("R27: no committed experiment with a decision_rule_result that is not post_hoc_amended")
    return found


def _qualifies(exp):
    return exp["mode"] == "committed" and "decision_rule_result" in exp and not exp["post_hoc_amended"]


def r27_violations(state, refs, drop_invalid=False):
    """Why supporting `refs` do not satisfy R27. drop_invalid=True evaluates only the remaining valid runs (R29)."""
    runs, experiments = run_closure(state, refs)
    if drop_invalid:
        runs = [r for r in runs if state.records[r]["validity"] == "valid"]
    if not runs:
        return ["R27: supporting evidence closure contains no runs"]
    found = []
    for ref in runs:
        run = state.records[ref]
        if run["status"] != "completed" or run["validity"] != "valid":
            found.append(f"R27: run {state.label(ref)} is {run['status']}/{run['validity']}, not completed/valid")
    experiments |= {state.records[r]["experiment"] for r in runs if state.records[r]["experiment"] in state.records}
    return found + _experiment_violations(state, experiments)


def _closure_degraded(state, refs):
    runs, _ = run_closure(state, refs)
    return any(state.records[r]["validity"] != "valid" for r in runs)


def claim_verified(state, claim):
    return any(v["payload"]["claim_version"] == claim["version"]
               and v["payload"]["result"] == "consistent_with_evidence"
               for v in state.verifications.get(claim["id"], []))


def _derive_claim(state, obj):
    evidence = obj["evidence"]
    obj["verified"] = claim_verified(state, obj)
    obj["evidence_degraded"] = _closure_degraded(state, evidence["supporting"] + evidence["contradicting"])
    obj["support_broken"] = _support_broken(state, obj)


def _support_broken(state, claim):
    return claim["status"] == "supported" and bool(
        r27_violations(state, claim["evidence"]["supporting"], drop_invalid=True))


def _ref_degraded(state, ref):
    kind = state.kinds.get(ref)
    rule = DEGRADED_BASIS.get(kind)
    if rule and state.records[ref][rule[0]] in rule[1]:
        return True
    return kind == "claim" and _support_broken(state, state.records[ref])


def _basis_degraded(state, refs):
    return any(_ref_degraded(state, ref) for ref in refs)


def _experiment_status(state, exp, authority):
    """`authority` is the contract authority of a committed experiment (None for scout or unauthorized)."""
    if exp["_ended"]:
        return exp["_ended"]
    if any(state.records[r]["status"] == "running" for r in exp["runs"]):
        return "running"
    if exp["mode"] == "scout" or authority:
        return "ready"
    waiting = [r for r in state.open_requests()
               if r["subject"] == exp["id"] and r["payload"]["subject_version"] == exp["version"]]
    return "awaiting_authority" if waiting else "draft"


def project_object(state, ref, now):
    """Projection object for one record: stored fields minus internals, plus derived fields at time `now`."""
    raw = state.records[ref]
    obj = {key: value for key, value in raw.items() if not key.startswith("_")}
    kind = state.kinds[ref]
    if kind == "experiment":
        authority = contract_authority(state, ref, now) if obj["mode"] == "committed" else None
        obj["status"] = _experiment_status(state, raw, authority)
        if obj["mode"] == "committed":
            obj["authority"] = authority or {"mode": "none"}
    elif kind == "claim":
        _derive_claim(state, obj)
    elif kind == "observation":
        obj["evidence_degraded"] = _closure_degraded(state, obj["evidence"])
    elif kind == "decision":
        obj["basis_degraded"] = _basis_degraded(state, obj["based_on"])
    elif kind == "policy":
        obj["active"] = policy_active(raw, now)
    elif kind == "run":
        _derive_run(state, obj)
    return obj


def _derive_run(state, obj):
    child = child_of(state, obj["id"])
    if child:
        obj["superseded_by"] = child
    obj["needs_rerun"] = needs_rerun(state, obj["id"])
