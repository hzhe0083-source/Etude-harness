"""Semantic rules: `check(state, event)` lists AGENTS.md violations of applying `event` to `state`.

`admit` (schema + check + R36) is the single "is this event allowed" path used by the CLI
before appending and by `research validate` on replay.
"""

import json

from .authority import SCOPE_KIND, authority_covers, contract_authority
from .derive import claim_verified, r27_violations
from .runs import (PLAN_FIELDS, child_of, dependency_violations, same_code_and_inputs, slot_violations,
                   start_violations, same_restart_definition)
from .schema import event_errors
from .metrics import core_metrics
from .errors import ResearchError
from .state import RUN_ALIAS_RE, CREATES, EVIDENCE_KEYS, PREFIX, extension_needs_authority, payload_view
from .views import extension_violations, slot_outside_view, style_violations, view_spec_violations

EVIDENCE_KINDS = ("experiment", "run", "observation", "claim")
EVIDENCE_REF_KINDS = ("experiment", "run", "observation")
REVISABLE = ("candidate", "contested")
SUBJECT_KIND = {
    "project_updated": "project", "policy_revoked": "policy",
    "hypothesis_closed": "hypothesis", "hypothesis_reopened": "hypothesis",
    "experiment_amended": "experiment", "experiment_completed": "experiment",
    "experiment_abandoned": "experiment", "experiment_marked_legacy": "experiment",
    "experiment_dataset_recorded": "experiment", "experiment_view_defined": "experiment", "experiment_view_styled": "experiment", "experiment_view_extended": "experiment",
    "run_archived": "run", "run_dataset_recorded": "run", "run_positioned": "run", "run_completed": "run", "run_validity_changed": "run",
    "run_interrupted": "run", "run_resumed": "run", "run_restarted": "run", "run_output_recorded": "run",
    "observation_retracted": "observation", "claim_revised": "claim", "claim_status_changed": "claim",
    "claim_verification_recorded": "claim", "decision_made": "decision", "decision_revoked": "decision",
}
REQUESTED_SHAPES = {
    "experiment_contract": [{}],
    "claim_transition": [{"to": "supported"}, {"to": "refuted"}],
    "decision": [{"to": "made"}, {"to": "revoked"}],
    "run_validity": [{"to": "valid"}],
    "policy": [{"to": "revoked"}],
}


def refs_violations(state, refs, kinds, what):
    found = []
    for ref in refs:
        if state.kinds.get(ref) not in kinds:
            found.append(f"{what} {ref} is not an existing {'/'.join(kinds)}")
    return found


def authority_violations(state, event, scope, requested, required, rule):
    """Missing authority when `required`; any authority that is claimed must actually cover the act (R9)."""
    authority = event["payload"]["authority"]
    if authority["mode"] == "none":
        return [f"{rule}: authority required for {scope} {requested}"] if required else []
    return authority_covers(state, authority, scope, event["subject"], requested, event["ts"])


# ---------------------------------------------------------------- per event type

def _policy_granted(state, event):
    scope = event["payload"]["scope"]
    if scope == "project":
        return []
    return refs_violations(state, [scope["hypothesis"]], ("hypothesis",), "policy scope")


def _policy_revoked(state, event):
    return [] if not state.records[event["subject"]]["revoked"] else ["policy is already revoked"]


def _hypothesis_created(state, event):
    parent = event["payload"].get("parent")
    return refs_violations(state, [parent], ("hypothesis",), "parent") if parent else []


def _hypothesis_closed(state, event):
    if state.records[event["subject"]]["status"] != "open":
        return ["hypothesis is already closed"]
    found = []
    for ref in event["payload"]["resolution"]:
        obj, kind = state.records.get(ref), state.kinds.get(ref)
        resolved = (kind == "claim" and obj["status"] in ("supported", "refuted")) or (
            kind == "decision" and obj["status"] == "made")
        if not resolved:
            found.append(f"R31: resolution {state.label(ref)} is not a supported/refuted Claim or a made Decision")
    return found


def _hypothesis_reopened(state, event):
    return [] if state.records[event["subject"]]["status"] == "closed" else ["hypothesis is not closed"]


def _view_violations(payload):
    view = payload_view(payload)
    return view_spec_violations(view["value"]) if view else []


def _experiment_created(state, event):
    payload = event["payload"]
    found = refs_violations(state, payload["motivated_by"], ("experiment",), "motivated_by")
    found += _view_violations(payload)
    if payload["hypothesis"] is None:
        return found
    found += refs_violations(state, [payload["hypothesis"]], ("hypothesis",), "hypothesis")
    hypothesis = state.get(payload["hypothesis"], "hypothesis")
    if hypothesis is not None and hypothesis["status"] != "open":
        found.append(f"hypothesis {state.label(payload['hypothesis'])} is closed")
    return found


def _experiment_open(state, event):
    ended = state.records[event["subject"]]["_ended"]
    return [f"experiment is already {ended}"] if ended else []


def _experiment_amended(state, event):
    found = _experiment_open(state, event) + _view_violations(event["payload"])
    if state.records[event["subject"]]["kind"] == "dataset":
        found.append("R38: a dataset experiment has no contract; extend its view with `research experiment extend`")
    return found


def _experiment_marked_legacy(state, event):
    return ["experiment is already marked legacy"] if state.records[event["subject"]]["legacy"] else []


def _experiment_dataset_recorded(state, event):
    card, exp = event["payload"], state.records[event["subject"]]
    found = []
    if exp["kind"] != "dataset" or exp["view"] is None:
        found.append("dataset cards require a dataset experiment with a view")
    if card["hf"]["type"] != "dataset":
        found.append("dataset cards must link to an HF dataset")
    if card["published_assets"] > card["planned_assets"]:
        found.append("published assets exceed the planned scope")
    if bool(card["published_assets"]) != bool(card["published_samples"]):
        found.append("published asset and sample counts must both be zero or positive")
    return found


def _experiment_view_styled(state, event):
    spec = state.records[event["subject"]]["view"]
    return style_violations(spec, event["payload"]) if spec else ["experiment has no view"]


def _extension_target(exp, add):
    if exp["legacy"]:
        return ["experiment is marked legacy"]
    return extension_violations(exp["view"], add) if exp["view"] else ["experiment has no view to extend"]


def _experiment_view_extended(state, event):
    add, exp = event["payload"]["add"], state.records[event["subject"]]
    found = _experiment_open(state, event) + _extension_target(exp, add)
    required = extension_needs_authority(exp)
    return found + authority_violations(state, event, "view_extension", {"add": add}, required, "A6")


def _experiment_completed(state, event):
    found = _experiment_open(state, event)
    if state.records[event["subject"]]["mode"] == "committed" and "decision_rule_result" not in event["payload"]:
        found.append("R19: a committed experiment completes with a decision_rule_result")
    return found


def _authority_requested(state, event):
    payload, subject = event["payload"], event["subject"]
    if state.kinds.get(subject) != SCOPE_KIND[payload["scope"]]:
        return [f"scope {payload['scope']} needs a {SCOPE_KIND[payload['scope']]} subject"]
    for pending in state.open_requests():
        if pending["subject"] == subject and all(pending["payload"][k] == payload[k] for k in ("scope", "subject_version", "requested")):
            return ["an identical current authority request is already open"]
    found = []
    if payload["subject_version"] != state.version(subject):
        found.append(f"subject_version is not the current version {state.version(subject)}")
    if payload["scope"] == "view_extension":
        found += _experiment_open(state, event)
        return found + _extension_request_violations(state, subject, payload["requested"])
    if payload["requested"] not in REQUESTED_SHAPES[payload["scope"]]:
        found.append(f"requested must be one of {REQUESTED_SHAPES[payload['scope']]}")
    if payload["scope"] == "experiment_contract" and state.records[subject]["mode"] != "committed":
        found.append("A1 applies only to committed experiments")
    return found


def _extension_request_violations(state, subject, requested):
    if set(requested) != {"add"}:
        return ["requested must be {\"add\": {axis: [values]}}"]
    exp = state.records[subject]
    if not extension_needs_authority(exp):
        return ["A6: only committed experiments need authority to extend their view"]
    return _extension_target(exp, requested["add"])


def _authority_answer(state, event):
    request = state.events.get(event["payload"]["request"])
    if request is None or request["type"] != "authority_requested":
        return ["request is not an authority_requested event"]
    if state.answers[request["event_id"]] is not None:
        return [f"request already answered by {state.answers[request['event_id']]}"]
    if event["subject"] != request["subject"]:
        return ["subject differs from the request subject"]
    current = state.version(request["subject"])
    if request["payload"]["subject_version"] != current or event["payload"]["subject_version"] != current:
        return [f"R12: stale request; current version of {state.label(request['subject'])} is {current}"]
    if event["type"] == "authority_granted" and request["payload"]["scope"] == "experiment_contract":
        if "view" not in state.records[request["subject"]]["contract"]:
            return ["R44: the contract has no view; amend it with --view before it can be authorized"]
    return []


def _experiment_accepts_runs(state, exp_id):
    found = refs_violations(state, [exp_id], ("experiment",), "experiment")
    if found:
        return found
    exp = state.records[exp_id]
    if exp["_ended"]:
        return [f"experiment {state.label(exp_id)} is {exp['_ended']}"]
    return [f"experiment {state.label(exp_id)} is marked legacy"] if exp["legacy"] else []


def _plan_violations(state, payload):
    """Slot, supersession and dependency rules for a Run definition (R40-R42)."""
    exp = state.records[payload["experiment"]]
    found = []
    if not all(key in payload for key in ("run_kind", "archive_target", "parameters")):
        return ["run plan requires kind, archive target and parameters"]
    if payload["run_kind"] == "evaluation":
        if "checkpoint" not in payload or payload["checkpoint"]["type"] != "model":
            found.append("evaluation requires a pinned model checkpoint")
        elif any(payload["checkpoint"][k] != payload["archive_target"][k] for k in ("type", "repo")):
            found.append("evaluation evidence must use the checkpoint's HF repository")
    elif "checkpoint" in payload:
        found.append("checkpoint identifies the model evaluated; only evaluation uses it")
    if (exp["kind"] == "dataset") != (payload["run_kind"] == "dataset"):
        found.append("dataset Runs belong to dataset experiments")
    if exp["view"] and "slot" not in payload:
        found.append(f"R40: {state.label(exp['id'])} has a view; its runs need a slot")
    if exp["view"] and "slot" in payload:
        found += slot_outside_view(exp["view"], payload["slot"])
    found += dependency_violations(state, payload.get("depends_on", []))
    return found + slot_violations(state, payload["experiment"], payload)


def _authorized_to_start(state, exp_id, at):
    exp = state.records[exp_id]
    if exp["mode"] != "committed" or contract_authority(state, exp_id, at) is not None:
        return []
    return [f"R15/A1: committed experiment {state.label(exp_id)} contract version {exp['version']} "
            f"is not authorized; run `research authority request {state.label(exp_id)} --scope experiment_contract ...`"]


def _run_planned(state, event):
    payload = event["payload"]
    return _experiment_accepts_runs(state, payload["experiment"]) or _plan_violations(state, payload)


def _start_planned(state, event, planned):
    payload = event["payload"]
    given = [key for key in PLAN_FIELDS if key in payload]
    if given:
        return [f"starting planned run {planned['alias']}: {', '.join(given)} come from the plan, not the start"]
    if (payload["alias"], payload["experiment"]) != (planned["alias"], planned["experiment"]):
        return [f"alias/experiment differ from planned run {planned['alias']}"]
    found = _experiment_accepts_runs(state, planned["experiment"])
    return found or start_violations(state, planned) + _authorized_to_start(state, planned["experiment"], event["ts"])


def _run_started(state, event):
    planned = state.get(event["subject"], "run")
    if planned is not None:
        return _start_planned(state, event, planned)
    payload = event["payload"]
    found = _experiment_accepts_runs(state, payload["experiment"])
    if found:
        return found
    found = _plan_violations(state, payload)
    found += start_violations(state, dict(payload, validity="unknown", depends_on=payload.get("depends_on", [])))
    return found + _authorized_to_start(state, payload["experiment"], event["ts"])


def _run_completed(state, event):
    payload, run = event["payload"], state.records[event["subject"]]
    if run["status"] != "running" or "exit_code" in run:
        return ["run is not running"]
    found = [f"R43: output {name} is not a declared product" for name in payload.get("outputs", {})
             if name not in run["products"]]
    if (payload["exit_code"] == 0) != (payload["status"] == "succeeded"):
        found.append("R22: status is succeeded iff exit_code is 0")
    try:
        canonical = core_metrics(run["run_kind"], payload["metrics"])
        if canonical != payload["metrics"]:
            found.append("evaluation metrics must include the derived success_rate")
    except ResearchError as exc:
        found.append(str(exc))
    if payload["status"] == "succeeded" and not payload["metrics"] and not payload["capture_gaps"]:
        found.append("successful Run without core metrics cannot be valid")
    expected = "valid" if payload["status"] == "succeeded" and not payload["capture_gaps"] else "unknown"
    if payload["validity"] != expected:
        found.append(f"R22: initial validity must be {expected}")
    return found


def _run_archived(state, event):
    run = state.records[event["subject"]]
    if event["payload"]["attempt"] != run["attempt"]:
        return ["archive belongs to a different execution attempt"]
    if run["status"] == "planned":
        return ["run has not started"]
    return []


def _run_dataset_recorded(state, event):
    result, run = event["payload"], state.records[event["subject"]]
    found = []
    if state.records[run["experiment"]]["kind"] != "dataset":
        found.append("dataset results require a dataset experiment")
    if run["status"] != "completed" or run["validity"] == "invalid":
        found.append("dataset results require a completed, non-invalid Run")
    if result["published_samples"] > result["accepted_samples"]:
        found.append("published samples exceed accepted samples")
    if bool(result["published_samples"]) != ("hf" in result):
        found.append("positive published samples require an HF dataset; zero samples have no publication")
    if "hf" in result and result["hf"]["type"] != "dataset":
        found.append("dataset publication must reference a dataset")
    return found


def _run_output_recorded(state, event):
    payload, run = event["payload"], state.records[event["subject"]]
    if payload["name"] not in run["products"]:
        return [f"R43: {payload['name']} is not a declared product of {run['alias']}"]
    if payload["name"] in ("checkpoint", "dataset") and any(payload["hf"][k] != run["archive_target"][k] for k in ("type", "repo")):
        return ["main product and execution records must share the HF repository"]
    if payload["name"] in run["outputs"]:
        return [f"output {payload['name']} is already recorded"]
    return ["run has not started"] if run["status"] == "planned" else []


def _run_interrupted(state, event):
    return [] if state.records[event["subject"]]["status"] == "running" else ["run is not running"]


def _run_resumed(state, event):
    run = state.records[event["subject"]]
    if run["archive_status"] != "uploaded":
        return ["archive the interrupted attempt with run-upload before restarting"]
    if run["status"] != "interrupted":
        return ["R40: only an interrupted run can resume"]
    if run["validity"] == "invalid":
        return [f"{run['alias']} is deprecated; plan a superseding run instead of resuming"]
    return same_code_and_inputs(run["capture"], event["payload"]["capture"])


def _experiment_view_defined(state, event):
    exp = state.records[event["subject"]]
    if exp["mode"] != "scout" or exp["view"] is not None or exp["legacy"]:
        return ["view definition requires a non-legacy scout without a view"]
    if event["payload"]["kind"] != "debug" and exp["hypothesis"] is None:
        return ["R14: non-debug experiments require a Hypothesis"]
    return _view_violations(event["payload"])


def _run_positioned(state, event):
    run = state.records[event["subject"]]
    if run.get("slot"):
        return ["Run already has a slot"]
    exp = state.records[run["experiment"]]
    if exp["mode"] != "scout" or exp["view"] is None or exp["legacy"]:
        return ["position requires a non-legacy scout view"]
    return slot_outside_view(exp["view"], event["payload"]["slot"]) + slot_violations(state, run["experiment"], event["payload"])


def _run_restarted(state, event):
    run = state.records[event["subject"]]
    if run["archive_status"] != "uploaded":
        return ["archive the interrupted attempt with run-upload before restarting"]
    if run["status"] != "interrupted":
        return ["R40: restart requires an interrupted Run"]
    return _experiment_accepts_runs(state, run["experiment"]) + start_violations(state, run) + same_restart_definition(run["capture"], event["payload"]["capture"])


def _run_validity_changed(state, event):
    to = event["payload"]["to"]
    if to != "invalid" and child_of(state, event["subject"]):
        return [f"R41: {state.label(event['subject'])} is superseded; it stays deprecated"]
    return authority_violations(state, event, "run_validity", {"to": to}, to == "valid", "A4")


def _observation_created(state, event):
    payload = event["payload"]
    found = refs_violations(state, payload["experiments"], ("experiment",), "experiment")
    found += refs_violations(state, payload["evidence"], ("run",), "evidence")
    for ref in payload["evidence"]:
        run = state.get(ref, "run")
        if run is not None and run["experiment"] not in payload["experiments"]:
            found.append(f"evidence run {state.label(ref)} belongs to none of the listed experiments")
    return found


def _observation_retracted(state, event):
    return [] if state.records[event["subject"]]["status"] == "active" else ["observation is already retracted"]


def _claim_content(state, payload):
    found = refs_violations(state, payload.get("hypotheses", []), ("hypothesis",), "hypothesis")
    evidence = payload.get("evidence") or {}
    for key in EVIDENCE_KEYS:
        found += refs_violations(state, evidence.get(key, []), EVIDENCE_REF_KINDS, f"evidence.{key}")
    return found


def _claim_revised(state, event):
    """A supported/refuted/superseded belief is not edited in place: its authority bound the old version (R12)."""
    status = state.records[event["subject"]]["status"]
    found = [] if status in REVISABLE else [f"R12: a {status} claim cannot be revised; move it to contested first"]
    return found + _claim_content(state, event["payload"])


def _claim_status_changed(state, event):
    payload, claim = event["payload"], state.records[event["subject"]]
    if payload["from"] != claim["status"]:
        return [f"from={payload['from']} but the claim is {claim['status']}"]
    if payload["to"] == payload["from"]:
        return ["status is unchanged"]
    to = payload["to"]
    found = authority_violations(state, event, "claim_transition", {"to": to}, to in ("supported", "refuted"), "A2")
    if to == "supported":
        found += r27_violations(state, claim["evidence"]["supporting"])
        found += _verification_violations(state, event, claim)
    if to == "superseded":
        found += _superseded_violations(state, event)
    return found


def _verification_violations(state, event, claim):
    authority = event["payload"]["authority"]
    policy = state.get(authority.get("policy"), "policy")
    if policy is None or not policy["require_verification"] or claim_verified(state, claim):
        return []
    return [f"R28: policy {policy['alias']} requires a consistent_with_evidence verification of version {claim['version']}"]


def _superseded_violations(state, event):
    successor = event["payload"]["superseded_by"]
    obj = state.get(successor, "claim")
    if obj is None or successor == event["subject"]:
        return [f"superseded_by {successor} is not another Claim"]
    return [] if obj["status"] == "supported" else [f"R26: superseded_by {obj['alias']} is not supported"]


def _claim_verification(state, event):
    current = state.version(event["subject"])
    found = [] if event["payload"]["claim_version"] == current else [f"claim_version is not the current version {current}"]
    if event["actor"]["id"] in state.records[event["subject"]]["_authors"]:
        found.append(f"R28: verifier {event['actor']['id']} proposed or revised this Claim; use a separate context")
    return found


def _decision_proposed(state, event):
    return refs_violations(state, event["payload"]["based_on"], tuple(PREFIX), "based_on")


def decision_needs_authority(state, decision_id):
    """A3: authority is needed iff based_on cites an Experiment, Run, Observation or Claim."""
    return any(state.kinds.get(ref) in EVIDENCE_KINDS for ref in state.records[decision_id]["based_on"])


def _decision_act(state, event, before, to):
    decision = state.records[event["subject"]]
    if decision["status"] != before:
        return [f"decision is {decision['status']}, expected {before}"]
    required = decision_needs_authority(state, event["subject"])
    return authority_violations(state, event, "decision", {"to": to}, required, "A3")


def _alias_reassigned(state, event):
    payload, obj = event["payload"], state.records.get(event["subject"])
    if obj is None or "alias" not in obj:
        return ["subject has no alias"]
    found = [] if obj["alias"] == payload["from"] else [f"subject alias is {obj['alias']}, not {payload['from']}"]
    if state.kinds[event["subject"]] == "run":
        match = RUN_ALIAS_RE.fullmatch(payload["to"])
        if not match or int(match.group(2)) < 1 or payload["to"] != state.run_alias(obj["experiment"], int(match.group(2))):
            found.append("Run alias must match its Experiment and use a positive padded sequence")
    elif payload["to"].split("-")[0] != payload["from"].split("-")[0]:
        found.append("alias prefix cannot change")
    if state.ids_with_alias(payload["to"]):
        found.append(f"alias {payload['to']} is already in use")
    return found


def _none(state, event):
    return []


CHECKS = {
    "project_updated": _none, "policy_granted": _policy_granted, "policy_revoked": _policy_revoked,
    "hypothesis_created": _hypothesis_created, "hypothesis_closed": _hypothesis_closed,
    "hypothesis_reopened": _hypothesis_reopened, "experiment_created": _experiment_created,
    "experiment_amended": _experiment_amended, "experiment_completed": _experiment_completed,
    "experiment_abandoned": _experiment_open, "experiment_marked_legacy": _experiment_marked_legacy,
    "experiment_view_defined": _experiment_view_defined, "experiment_dataset_recorded": _experiment_dataset_recorded, "experiment_view_styled": _experiment_view_styled, "experiment_view_extended": _experiment_view_extended,
    "authority_requested": _authority_requested,
    "authority_granted": _authority_answer, "authority_denied": _authority_answer,
    "run_planned": _run_planned, "run_started": _run_started, "run_completed": _run_completed,
    "run_validity_changed": _run_validity_changed, "run_archived": _run_archived, "run_dataset_recorded": _run_dataset_recorded, "run_output_recorded": _run_output_recorded,
    "run_interrupted": _run_interrupted, "run_resumed": _run_resumed, "run_restarted": _run_restarted, "run_positioned": _run_positioned,
    "observation_created": _observation_created, "observation_retracted": _observation_retracted,
    "claim_proposed": lambda state, event: _claim_content(state, event["payload"]),
    "claim_revised": _claim_revised,
    "claim_status_changed": _claim_status_changed, "claim_verification_recorded": _claim_verification,
    "decision_proposed": _decision_proposed,
    "decision_made": lambda state, event: _decision_act(state, event, "proposed", "made"),
    "decision_revoked": lambda state, event: _decision_act(state, event, "made", "revoked"),
    "alias_reassigned": _alias_reassigned,
}


def _subject_violations(state, event):
    etype, subject = event["type"], event["subject"]
    if etype == "run_started" and (state.get(subject, "run") or {}).get("status") == "planned":
        return []
    if etype in CREATES:
        if subject in state.records:
            return [f"subject {subject} already exists"]
        alias = event["payload"].get("alias")
        expected = PREFIX.get(CREATES[etype])
        if expected == "R":
            match = RUN_ALIAS_RE.fullmatch(alias)
            if match is None:
                return ["Run alias must use E{experiment}-R{sequence:03d}"]
            experiment = event["payload"]["experiment"]
            if state.get(experiment, "experiment") is None:
                return ["Run experiment does not exist"]
            if int(match.group(2)) < 1 or alias != state.run_alias(experiment, int(match.group(2))):
                return ["Run alias must match its Experiment and use a positive padded sequence"]
            return []
        if expected and not alias.startswith(expected + "-"):
            return [f"R4: alias {alias} must start with {expected}-"]
        return []
    kind = SUBJECT_KIND.get(etype)
    if kind and state.kinds.get(subject) != kind:
        return [f"subject {subject} is not an existing {kind}"]
    if etype.startswith("authority_") and subject not in state.records:
        return [f"subject {subject} does not exist"]
    return []


def check(state, event):
    """Semantic violations of applying `event` to `state` (empty list = allowed)."""
    if event["type"] == "project_initialized":
        return ["project is already initialized"] if state.project_id else []
    if state.project_id is None:
        return ["project is not initialized; run `research init`"]
    found = _subject_violations(state, event)
    return found or CHECKS[event["type"]](state, event)


def handoff_violations(event, handoff_ids):
    text = json.dumps(event)
    return [f"R36: event cites handoff {hid}" for hid in sorted(handoff_ids) if hid in text]


def admit(state, event, handoff_ids):
    """All reasons `event` may not be appended to `state`: schema first, then rules, then R36."""
    errors = event_errors(event)
    if errors:
        return errors
    return check(state, event) + handoff_violations(event, handoff_ids)
