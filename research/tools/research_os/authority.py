"""Authority resolution (AGENTS.md section 5): one helper shared by `check`, the CLI and projections."""

from .store import parse_ts

SCOPE_KIND = {
    "experiment_contract": "experiment", "claim_transition": "claim", "decision": "decision",
    "run_validity": "run", "policy": "policy", "view_extension": "experiment",
}
# Reusable grants authorize a record version; one-shot grants authorize a single act.
REUSABLE_SCOPES = ("experiment_contract",)
CONTRACT_CONSTRAINT_KEYS = ("primary_metric", "baseline")


def hypotheses_of(state, ref, seen=None):
    """Hypotheses a record belongs to (used for policy scope and context overlap)."""
    kind = state.kinds.get(ref)
    obj = state.records.get(ref)
    if kind == "hypothesis":
        return {ref}
    if kind == "experiment":
        return {obj["hypothesis"]} if obj["hypothesis"] else set()
    if kind == "run":
        return hypotheses_of(state, obj["experiment"], seen)
    if kind == "observation":
        return set().union(*[hypotheses_of(state, exp, seen) for exp in obj["experiments"]])
    if kind == "claim":
        return set(obj["hypotheses"])
    if kind == "decision":
        seen = (seen or set()) | {ref}
        parts = [hypotheses_of(state, base, seen) for base in obj["based_on"] if base not in seen]
        return set().union(*parts)
    return set()


def policy_active(policy, at):
    if policy["revoked"]:
        return False
    return "expires_at" not in policy or parse_ts(at) < parse_ts(policy["expires_at"])


def _policy_scope_covers(state, policy, scope, subject):
    if policy["scope"] == "project":
        return True
    hypothesis = policy["scope"]["hypothesis"]
    if scope != "decision":
        return hypothesis in hypotheses_of(state, subject)
    based_on = state.records[subject]["based_on"]
    return bool(based_on) and all(hypothesis in hypotheses_of(state, base) for base in based_on)


def _contract_permitted(policy, experiment):
    contract = experiment.get("contract") or {}
    if experiment["kind"] not in policy["permits"]["experiment_kinds"] or "view" not in contract:
        return False
    constraints = policy["constraints"]
    keys = [key for key in CONTRACT_CONSTRAINT_KEYS if key in constraints]
    return all(contract[key]["value"] == constraints[key] for key in keys)


def _permits(policy, scope, subject_obj, requested):
    permits = policy["permits"]
    if scope == "experiment_contract":
        return _contract_permitted(policy, subject_obj)
    if scope == "claim_transition":
        return requested.get("to") in permits["claim_transitions"]
    if scope == "decision":
        return permits["decisions"]
    if scope == "run_validity":
        return permits["run_validity"]
    return False


def _policy_violations(state, policy_id, scope, subject, requested, at):
    policy = state.get(policy_id, "policy")
    if policy is None:
        return [f"authority policy {policy_id} is not a Policy"]
    label = state.label(policy_id)
    if scope == "policy":
        return [f"A5: a Policy cannot cover granting or revoking a Policy ({label})"]
    if not policy_active(policy, at):
        return [f"policy {label} is not active (revoked or expired)"]
    if not _policy_scope_covers(state, policy, scope, subject):
        return [f"policy {label} scope does not cover {state.label(subject)}"]
    if not _permits(policy, scope, state.records[subject], requested):
        return [f"policy {label} permits/constraints do not cover {scope} {requested} on {state.label(subject)}"]
    return []


def _explicit_violations(state, grant_id, scope, subject, requested):
    grant = state.events.get(grant_id)
    if grant is None or grant["type"] != "authority_granted":
        return [f"authority grant {grant_id} is not an authority_granted event"]
    request = state.events.get(grant["payload"]["request"])
    if request is None or request["type"] != "authority_requested":
        return [f"grant {grant_id} answers no authority request"]
    rp = request["payload"]
    if (rp["scope"], request["subject"], rp["requested"]) != (scope, subject, requested):
        return [f"grant {grant_id} was for {rp['scope']} {rp['requested']} on {state.label(request['subject'])}, "
                f"not {scope} {requested} on {state.label(subject)}"]
    if grant["payload"]["subject_version"] != state.version(subject):
        return [f"R12: grant {grant_id} binds version {grant['payload']['subject_version']}, "
                f"current version of {state.label(subject)} is {state.version(subject)}"]
    if scope not in REUSABLE_SCOPES and grant_id in state.consumed:
        return [f"grant {grant_id} was already used for one act"]
    return []


def authority_covers(state, authority, scope, subject, requested, at):
    """Violations (empty = covered) of `authority` authorizing `scope`/`requested` on `subject` at time `at`."""
    if authority["mode"] == "explicit":
        return _explicit_violations(state, authority["grant"], scope, subject, requested)
    if authority["mode"] == "policy":
        return _policy_violations(state, authority["policy"], scope, subject, requested, at)
    return [f"authority mode none covers no {scope} act"]


def find_authority(state, scope, subject, requested, at):
    """First explicit grant, else first active covering policy, that authorizes the act; None if neither."""
    candidates = [{"mode": "explicit", "grant": e["event_id"]}
                  for e in sorted(state.events.values(), key=lambda e: e["event_id"])
                  if e["type"] == "authority_granted"]
    candidates += [{"mode": "policy", "policy": p["id"]} for p in state.of_kind("policy")]
    for authority in candidates:
        if not authority_covers(state, authority, scope, subject, requested, at):
            return authority
    return None


def contract_authority(state, experiment_id, at):
    """Authority valid for the experiment's current contract version (A1), or None."""
    return find_authority(state, "experiment_contract", experiment_id, {}, at)
