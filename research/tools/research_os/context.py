"""`research context`: the minimum sufficient context for one root record and role (AGENTS.md R33, R28)."""

from . import handoff
from .authority import hypotheses_of
from .derive import project_object
from .queries import claim_closure, neighbors, overlapping

ROLE_KINDS = {
    "proposer": {"hypothesis", "claim", "decision", "experiment"},
    "executor": {"experiment"},
    "interpreter": {"experiment", "run"},
    "verifier": {"claim", "experiment", "run", "observation"},
    None: {"hypothesis", "experiment", "run", "observation", "claim", "decision", "policy"},
}
HANDOFF_ROLES = ("executor", None)


def _candidates(state, root_id, role):
    if role == "verifier":
        return claim_closure(state, root_id) if state.kinds[root_id] == "claim" else set()
    found = neighbors(state, root_id)
    for ref in [root_id, *found]:
        if state.kinds[ref] == "claim":
            found |= claim_closure(state, ref)
    return found | overlapping(state, hypotheses_of(state, root_id))


def build(state, root, root_id, role, now):
    """{root, role, records, handoff, project_constraints}; verifiers never receive a handoff."""
    allowed = ROLE_KINDS[role]
    refs = {ref for ref in _candidates(state, root_id, role) if state.kinds[ref] in allowed} | {root_id}
    records = {state.label(ref): project_object(state, ref, now) for ref in sorted(refs)}
    scope_ids = {root_id} | hypotheses_of(state, root_id)
    if state.kinds[root_id] == "run":
        scope_ids.add(state.records[root_id]["experiment"])
    project = state.project()
    return {
        "root": state.label(root_id),
        "role": role,
        "records": records,
        "handoff": handoff.latest(root, now, scope_ids) if role in HANDOFF_ROLES else None,
        "project_constraints": project["hard_constraints"]["value"],
    }
