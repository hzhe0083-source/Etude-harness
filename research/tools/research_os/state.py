"""Pure command reducer for the current-state ledger. No historical event replay."""

import copy
import re
from dataclasses import dataclass, field

from .errors import ResearchError

PREFIX = {
    "hypothesis": "H", "experiment": "E", "run": "R", "observation": "O",
    "claim": "C", "decision": "D", "policy": "P",
}
KINDS = tuple(PREFIX)
CREATES = {
    "project_initialized": "project", "policy_granted": "policy", "hypothesis_created": "hypothesis",
    "experiment_created": "experiment", "run_planned": "run", "run_started": "run", "observation_created": "observation",
    "claim_proposed": "claim", "decision_proposed": "decision",
}
ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
ALIAS_RE = re.compile(r"^(H|E|O|C|D|P|HS)-([1-9][0-9]*)$")
RUN_ALIAS_RE = re.compile(r"^E([1-9][0-9]*)-R([0-9]{3,})$")
EVIDENCE_KEYS = ("supporting", "contradicting", "context")
EXPLICIT_CONSUMERS = ("claim_status_changed", "decision_made", "decision_revoked", "run_validity_changed",
                      "experiment_view_extended")
VIEW_AXES = ("rows", "cols", "replicates")


@dataclass
class State:
    project_id: str = None
    records: dict = field(default_factory=dict)        # id -> projection object (keys "_x" are internal)
    kinds: dict = field(default_factory=dict)          # id -> kind
    events: dict = field(default_factory=dict)         # event_id -> event
    answers: dict = field(default_factory=dict)        # authority_requested event_id -> answer event_id | None
    consumed: set = field(default_factory=set)         # explicit grants already used by a one-shot act
    verifications: dict = field(default_factory=dict)  # claim id -> [verification event]
    last_event_id: str = None
    archives: dict = field(default_factory=dict)

    def get(self, ref, kind=None):
        if ref not in self.records or (kind and self.kinds[ref] != kind):
            return None
        return self.records[ref]

    def of_kind(self, kind):
        return [self.records[ref] for ref in sorted(self.records) if self.kinds[ref] == kind]

    def ids_with_alias(self, alias):
        return sorted(ref for ref, obj in self.records.items() if obj.get("alias") == alias)

    def next_alias(self, prefix):
        numbers = [0]
        for obj in self.records.values():
            match = ALIAS_RE.match(obj.get("alias") or "")
            if match and match.group(1) == prefix:
                numbers.append(int(match.group(2)))
        return f"{prefix}-{max(numbers) + 1}"

    def run_alias(self, experiment, number):
        return f"E{self.records[experiment]['alias'].split('-')[1]}-R{number:03d}"

    def next_run_alias(self, experiment):
        numbers = [0]
        for run in self.of_kind("run"):
            match = RUN_ALIAS_RE.fullmatch(run["alias"])
            if run["experiment"] == experiment and match:
                numbers.append(int(match.group(2)))
        return self.run_alias(experiment, max(numbers) + 1)

    def resolve(self, token, kind=None):
        """Alias or ULID -> record id. Raises ResearchError when missing, ambiguous or of the wrong kind."""
        if ULID_RE.match(token):
            ref = token if token in self.records else None
        else:
            matches = self.ids_with_alias(token)
            if len(matches) > 1:
                raise ResearchError(f"alias {token} is ambiguous ({', '.join(matches)}); run `research alias-repair`")
            ref = matches[0] if matches else None
        if ref is None:
            raise ResearchError(f"unknown record {token!r}")
        if kind and self.kinds[ref] != kind:
            raise ResearchError(f"{token} is a {self.kinds[ref]}, expected a {kind}")
        return ref

    def project(self):
        if self.project_id is None:
            raise ResearchError("project is not initialized; run `research init`")
        return self.records[self.project_id]

    def label(self, ref):
        obj = self.records.get(ref)
        return (obj and obj.get("alias")) or ref

    def version(self, ref):
        return self.records[ref]["version"]

    def open_requests(self):
        return [self.events[req] for req in sorted(self.answers) if self.answers[req] is None]


# ---------------------------------------------------------------- creation

def _base(event, extra):
    obj = {"id": event["subject"], "version": event["event_id"], "created_at": event["ts"],
           "created_by": event["actor"], "_created_event": event["event_id"]}
    obj.update(extra)
    return obj


def _create(state, event, kind, extra):
    if event["subject"] in state.records:
        return
    state.records[event["subject"]] = _base(event, extra)
    state.kinds[event["subject"]] = kind


def _project_initialized(state, event):
    payload = dict(event["payload"])
    if state.project_id is None:
        state.project_id = event["subject"]
    _create(state, event, "project", payload)


def _policy_granted(state, event):
    _create(state, event, "policy", dict(event["payload"], revoked=False))


def _hypothesis_created(state, event):
    _create(state, event, "hypothesis", dict(event["payload"], status="open", resolution=[]))


def _set_view(record, view):
    """The table's current spec: a copy of the given ViewValue, which extensions then append to (R44)."""
    record["view"] = copy.deepcopy(view["value"]) if view else None
    record["view_extended"] = {}


def axis_of(spec, axis):
    return spec["cell"].get("replicates") if axis == "replicates" else spec[axis]


def payload_view(payload):
    """The ViewValue an experiment_created payload carries: a dataset's own, else its contract's."""
    return payload.get("view") or (payload.get("contract") or {}).get("view")


def extension_needs_authority(experiment):
    """A6: appending to a committed view needs authority; a scout's view (datasets included) changes freely."""
    return experiment["mode"] == "committed"


def _experiment_created(state, event):
    payload = event["payload"]
    extra = dict(payload, post_hoc_amended=False, legacy=False, runs=[], _ended=None)
    _create(state, event, "experiment", extra)
    _set_view(state.records[event["subject"]], payload_view(payload))


RUN_DEFAULTS = {"depends_on": [], "hf_inputs": [], "products": []}


def _new_run(state, event, status):
    payload = event["payload"]
    extra = dict(RUN_DEFAULTS, **payload, status=status, validity="unknown",
                 metrics={}, outputs={}, attempt=0, archive_status="pending")
    _create(state, event, "run", extra)
    experiment = state.get(payload["experiment"], "experiment")
    if experiment is not None and event["subject"] not in experiment["runs"]:
        experiment["runs"].append(event["subject"])


def _run_planned(state, event):
    _new_run(state, event, "planned")


def _run_started(state, event):
    """Starts a planned Run in place (code is bound now), or creates and starts a Run directly."""
    planned = state.get(event["subject"], "run")
    if planned is not None and planned["status"] == "planned":
        planned.update(capture=event["payload"]["capture"], status="running", started_at=event["ts"], attempt=1)
        return
    _new_run(state, event, "running")
    state.records[event["subject"]].update(started_at=event["ts"], attempt=1)


def _observation_created(state, event):
    _create(state, event, "observation", dict(event["payload"], status="active"))


def _claim_proposed(state, event):
    _create(state, event, "claim", dict(event["payload"], status="candidate", _authors=[event["actor"]["id"]]))


def _decision_proposed(state, event):
    _create(state, event, "decision", dict(event["payload"], status="proposed", authority={"mode": "none"}))


# ---------------------------------------------------------------- mutation

def _project_updated(record, event, state):
    record.update(event["payload"])
    record["version"] = event["event_id"]


def _policy_revoked(record, event, state):
    record["revoked"] = True


def _hypothesis_closed(record, event, state):
    record["status"] = "closed"
    record["resolution"] = list(event["payload"]["resolution"])


def _hypothesis_reopened(record, event, state):
    record["status"] = "open"
    record["resolution"] = []


def _experiment_amended(record, event, state):
    record["contract"] = event["payload"]["contract"]
    record["version"] = event["event_id"]
    _set_view(record, record["contract"].get("view"))
    if record["runs"]:
        record["post_hoc_amended"] = True


def _experiment_completed(record, event, state):
    record["_ended"] = "completed"
    if "decision_rule_result" in event["payload"]:
        record["decision_rule_result"] = event["payload"]["decision_rule_result"]


def _experiment_abandoned(record, event, state):
    record["_ended"] = "abandoned"


def _experiment_marked_legacy(record, event, state):
    record["legacy"] = True


def _experiment_dataset_recorded(record, event, state):
    record["dataset_card"] = event["payload"]


def _experiment_view_styled(record, event, state):
    record["view_style"] = dict(record.get("view_style") or {}, **event["payload"])


def _experiment_view_extended(record, event, state):
    record["_view_authority"] = event["payload"]["authority"]
    for axis, values in event["payload"]["add"].items():
        axis_of(record["view"], axis)["values"].extend(values)
        record["view_extended"].setdefault(axis, []).extend(values)


def outputs_complete(run):
    return all(name in run["outputs"] for name in run["products"])


def _settle(run):
    """Exit 0 is completed once every declared product is recorded; until then the Run is still running."""
    if run.get("exit_code") == 0:
        run["status"] = "completed" if outputs_complete(run) else "running"


def _run_completed(record, event, state):
    payload = event["payload"]
    record.update({key: value for key, value in payload.items() if key not in ("status", "outputs", "validity")})
    if record["validity"] != "invalid":
        record["validity"] = payload["validity"]
    record["outputs"].update(payload.get("outputs") or {})

    record["status"] = "interrupted" if payload["exit_code"] != 0 else "running"
    _settle(record)


def _run_archived(record, event, state):
    record["evidence"] = event["payload"]["evidence"]
    record["archive_status"] = "uploaded"


def _run_dataset_recorded(record, event, state):
    record["dataset_result"] = event["payload"]


def _run_output_recorded(record, event, state):
    record["outputs"][event["payload"]["name"]] = event["payload"]["hf"]
    _settle(record)


def _run_interrupted(record, event, state):
    record["status"] = "interrupted"
    record["failure_reason"] = event["payload"]["reason"]


def _begin_attempt(record, event, state):
    record["status"] = "running"
    record["attempt"] += 1
    record["archive_status"] = "pending"
    for key in ("exit_code", "evidence", "failure_reason", "capture_gaps", "dataset_result"):
        record.pop(key, None)
    record["outputs"] = {}
    record["metrics"] = {}



def _experiment_view_defined(record, event, state):
    payload = event["payload"]
    record["kind"] = payload["kind"]
    if payload["kind"] == "dataset":
        record.pop("contract", None)
        _set_view(record, payload["view"])
    else:
        record["contract"] = payload["contract"]
        _set_view(record, payload["contract"]["view"])


def _run_positioned(record, event, state):
    record.update(event["payload"])




def _run_validity_changed(record, event, state):
    record["validity"] = event["payload"]["to"]
    record["validity_authority"] = event["payload"]["authority"]

    if event["payload"]["to"] == "invalid":
        record["deprecation_reason"] = event["payload"]["reason"]


def _observation_retracted(record, event, state):
    record["status"] = "retracted"


def _claim_revised(record, event, state):
    record.update({key: value for key, value in event["payload"].items() if key != "reason"})
    record["version"] = event["event_id"]
    record["_authors"] = sorted(set(record["_authors"]) | {event["actor"]["id"]})


def _claim_status_changed(record, event, state):
    payload = event["payload"]
    record["status"] = payload["to"]
    record["authority"] = payload["authority"]
    if "superseded_by" in payload:
        record["superseded_by"] = payload["superseded_by"]


def _claim_verification_recorded(record, event, state):
    state.verifications[event["subject"]] = [event]


def _decision_made(record, event, state):
    record["status"] = "made"
    record["authority"] = event["payload"]["authority"]


def _decision_revoked(record, event, state):
    record["status"] = "revoked"


def _alias_reassigned(record, event, state):
    record["alias"] = event["payload"]["to"]


CREATORS = {
    "project_initialized": _project_initialized, "policy_granted": _policy_granted,
    "hypothesis_created": _hypothesis_created, "experiment_created": _experiment_created,
    "run_planned": _run_planned, "run_started": _run_started, "observation_created": _observation_created,
    "claim_proposed": _claim_proposed, "decision_proposed": _decision_proposed,
}
MUTATORS = {
    "project_updated": _project_updated, "policy_revoked": _policy_revoked,
    "hypothesis_closed": _hypothesis_closed, "hypothesis_reopened": _hypothesis_reopened,
    "experiment_amended": _experiment_amended, "experiment_completed": _experiment_completed,
    "experiment_abandoned": _experiment_abandoned, "experiment_marked_legacy": _experiment_marked_legacy,
    "experiment_view_defined": _experiment_view_defined, "experiment_dataset_recorded": _experiment_dataset_recorded, "experiment_view_styled": _experiment_view_styled, "experiment_view_extended": _experiment_view_extended,
    "run_positioned": _run_positioned, "run_completed": _run_completed,
    "run_archived": _run_archived,
    "run_dataset_recorded": _run_dataset_recorded, "run_output_recorded": _run_output_recorded, "run_interrupted": _run_interrupted, "run_resumed": _begin_attempt, "run_restarted": _begin_attempt,
    "run_validity_changed": _run_validity_changed, "observation_retracted": _observation_retracted,
    "claim_revised": _claim_revised, "claim_status_changed": _claim_status_changed,
    "claim_verification_recorded": _claim_verification_recorded, "decision_made": _decision_made,
    "decision_revoked": _decision_revoked, "alias_reassigned": _alias_reassigned,
}


def _apply_authority_event(state, event):
    if event["type"] == "authority_requested":
        state.answers.setdefault(event["event_id"], None)
        return
    request = event["payload"]["request"]
    if request in state.answers and state.answers[request] is None:
        state.answers[request] = event["event_id"]


def apply(state, event):
    if event["type"].startswith("authority_"):
        state.events[event["event_id"]] = event
    state.last_event_id = max(state.last_event_id or "", event["event_id"])
    etype = event["type"]
    if etype in CREATORS:
        CREATORS[etype](state, event)
    elif etype.startswith("authority_"):
        _apply_authority_event(state, event)
    elif event["subject"] in state.records:
        MUTATORS[etype](state.records[event["subject"]], event, state)
    state.records.get(event["subject"], {}).update(updated_at=event["ts"])
    authority = event["payload"].get("authority") or {}
    if etype in EXPLICIT_CONSUMERS and authority.get("mode") == "explicit":
        state.consumed.add(authority["grant"])
