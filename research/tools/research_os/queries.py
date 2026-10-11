"""Read-only views generated on demand (AGENTS.md section 11). Nothing here is stored."""

import json

from .authority import hypotheses_of
from .derive import project_object, run_closure
from .errors import ResearchError
from .state import EVIDENCE_KEYS, KINDS


def _matches(obj, where):
    for key, expected in where:
        value = obj.get(key)
        text = value if isinstance(value, str) else json.dumps(value)
        if text != expected:
            return False
    return True


def list_records(state, kind, where, now):
    if kind not in KINDS:
        raise ResearchError(f"unknown type {kind}; expected one of {', '.join(KINDS)}")
    objects = [project_object(state, obj["id"], now) for obj in state.of_kind(kind)]
    return [obj for obj in objects if _matches(obj, where)]


def pending_authority(state):
    """Open requests, oldest first, never ranked; `stale` when the subject moved past subject_version."""
    rows = []
    for request in state.open_requests():
        payload, subject = request["payload"], request["subject"]
        rows.append({
            "request": request["event_id"], "subject_alias": state.label(subject),
            "scope": payload["scope"], "requested": payload["requested"], "summary": payload["summary"],
            "stale": payload["subject_version"] != state.version(subject),
        })
    return rows


# ---------------------------------------------------------------- evidence tree

def _node(state, ref):
    obj = state.records[ref]
    node = {"id": ref, "alias": obj.get("alias"), "type": state.kinds[ref], "status": obj.get("status")}
    if state.kinds[ref] == "run":
        node.update(validity=obj["validity"], metrics=obj["metrics"], evidence=obj.get("evidence"), archive_status=obj["archive_status"])
    if state.kinds[ref] == "experiment":
        node.update(kind=obj["kind"], mode=obj["mode"], post_hoc_amended=obj["post_hoc_amended"])
    return node


def evidence_tree(state, ref, seen=None):
    """Claim/Observation/Decision/Experiment -> nested evidence down to runs and their HF evidence."""
    seen = (seen or set()) | {ref}
    kind, obj = state.kinds[ref], state.records[ref]
    node = _node(state, ref)
    children = {
        "claim": lambda: {key: obj["evidence"].get(key, []) for key in EVIDENCE_KEYS},
        "observation": lambda: {"evidence": obj["evidence"]},
        "decision": lambda: {"based_on": obj["based_on"]},
        "experiment": lambda: {"runs": obj["runs"]},
    }.get(kind, dict)()
    for key, refs in children.items():
        node[key] = [evidence_tree(state, child, seen) for child in refs if child in state.records and child not in seen]
    return node


# ---------------------------------------------------------------- context (R33)

def _cited(evidence):
    return [ref for key in EVIDENCE_KEYS for ref in evidence.get(key, [])]


def links(state, ref):
    obj, kind = state.records[ref], state.kinds[ref]
    if kind == "hypothesis":
        return [obj.get("parent")] + obj["resolution"]
    if kind == "experiment":
        return [obj["hypothesis"]] + obj["motivated_by"] + obj["runs"]
    if kind == "run":
        return [obj["experiment"]]
    if kind == "observation":
        return obj["experiments"] + obj["evidence"]
    if kind == "claim":
        return obj["hypotheses"] + _cited(obj["evidence"]) + [obj.get("superseded_by")]
    if kind == "decision":
        return list(obj["based_on"])
    if kind == "policy" and isinstance(obj["scope"], dict):
        return [obj["scope"]["hypothesis"]]
    return []


def neighbors(state, ref):
    out = {link for link in links(state, ref) if link in state.records}
    out |= {other for other in state.records if other != ref and ref in links(state, other)}
    return out


def claim_closure(state, claim_id):
    """Claim evidence graph: cited refs, their runs, observations' runs and the experiments owning those runs."""
    found = {ref for ref in _cited(state.records[claim_id]["evidence"]) if ref in state.records}
    for ref in list(found):
        found |= set(run_closure(state, [ref])[0])
        if state.kinds[ref] == "experiment":
            found |= set(state.records[ref]["runs"])
    found |= {state.records[r]["experiment"] for r in found if state.kinds[r] == "run"}
    return found


def scopes_overlap(left, right):
    shared = set(left) & set(right)
    return bool(shared) and all(set(left[key]) & set(right[key]) for key in shared)


def overlapping(state, hypothesis_ids):
    scopes = [state.records[h]["scope"] for h in hypothesis_ids if h in state.records]
    found = {c["id"] for c in state.of_kind("claim") if any(scopes_overlap(c["scope"], s) for s in scopes)}
    found |= {d["id"] for d in state.of_kind("decision") if hypotheses_of(state, d["id"]) & set(hypothesis_ids)}
    return found
