"""Validate the bounded current-state ledger, without replaying an operation log."""
from collections import Counter

from . import store
from .derive import project_object
from .queries import pending_authority, links
from .schema import _errors, event_errors
from .metrics import core_metrics
from .errors import ResearchError
from .state import KINDS, RUN_ALIAS_RE


def state_errors(state):
    data = store.state_data(state)
    errors = store.size_errors(data) + _errors("#/$defs/Ledger", data)
    if set(state.records) != set(state.kinds):
        return errors + ["record IDs and kind index differ"]
    if state.project_id is not None and state.kinds.get(state.project_id) != "project":
        errors.append("project_id is not a project")
    aliases = Counter(r.get("alias") for r in state.records.values() if r.get("alias"))
    errors += [f"duplicate alias {a}" for a, n in aliases.items() if n > 1]
    for ref, record in state.records.items():
        kind = state.kinds[ref]
        if kind not in (*KINDS, "project"):
            errors.append(f"{ref}: unknown kind {kind}")
            continue
        if ref != record["id"]:
            errors.append(f"{ref}: record ID mismatch")
        errors += [f"{ref}: {e}" for e in _errors("#/$defs/Objects/$defs/" + kind.capitalize(), record)]
        for linked in links(state, ref) + (record.get("depends_on", []) if kind == "run" else []):
            if linked is not None and linked not in state.records:
                errors.append(f"{ref}: dangling reference {linked}")
        if kind == "run":
            if not RUN_ALIAS_RE.fullmatch(record["alias"]):
                errors.append(f"{ref}: Run alias must be scoped to its Experiment")
            try:
                metrics = core_metrics(record["run_kind"], record["metrics"])
                if metrics != record["metrics"]:
                    errors.append(f"{ref}: success_rate missing")
            except ResearchError as exc:
                errors.append(f"{ref}: {exc}")
            if record["run_kind"] == "evaluation" and "checkpoint" not in record:
                errors.append(f"{ref}: evaluation checkpoint missing")
            if record["archive_status"] == "uploaded" and "evidence" not in record:
                errors.append(f"{ref}: archived Run has no pinned evidence")
            if record["status"] == "completed" and record.get("exit_code") != 0:
                errors.append(f"{ref}: completed Run must have exit code zero")
        if kind == "experiment":
            expected = {r["id"] for r in state.of_kind("run") if r["experiment"] == ref}
            if set(record["runs"]) != expected:
                errors.append(f"{ref}: Run membership differs from experiment links")
    for event in state.events.values():
        if not event["type"].startswith("authority_"):
            errors.append("ledger contains a non-authorization event")
        errors += event_errors(event)
    for request, answer in state.answers.items():
        if request not in state.events or (answer and answer not in state.events):
            errors.append("authority answer has a dangling request/answer")
    return errors


def validate(root, now):
    state = store.load_state(root)
    warnings = []
    for claim in state.of_kind("claim"):
        if project_object(state, claim["id"], now)["support_broken"]:
            warnings.append(f"supported claim {claim['alias']} has degraded evidence")
    warnings += [f"stale authority request {r['request']}" for r in pending_authority(state) if r["stale"]]
    return {"errors": state_errors(state), "warnings": warnings,
            "bytes": (root / store.LEDGER_FILE).stat().st_size, "limit_bytes": store.MAX_LEDGER_BYTES,
            "records": len(state.records)}
