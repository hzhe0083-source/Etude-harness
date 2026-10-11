"""Run slots, supersession chains and dependency staleness (AGENTS.md section 7). Pure; no I/O."""

import shlex


PLAN_FIELDS = ("slot", "depends_on", "supersedes", "hf_inputs", "products", "run_kind", "parameters", "checkpoint", "archive_target")


def children(state):
    """parent run id -> child run ids (Runs whose `supersedes` names the parent)."""
    found = {}
    for ref, kind in state.kinds.items():
        parent = state.records[ref].get("supersedes") if kind == "run" else None
        if parent:
            found.setdefault(parent, []).append(ref)
    return found


def child_of(state, run_id, kids=None):
    kids = children(state) if kids is None else kids
    found = kids.get(run_id, [])
    return found[0] if found else None


def slot_runs(state, experiment_id, slot):
    exp = state.get(experiment_id, "experiment")
    runs = exp["runs"] if exp else []
    return [ref for ref in runs if state.records[ref].get("slot") == slot]


def slot_heads(state, experiment_id, slot, kids=None):
    """Runs in the slot that nothing supersedes yet (normally exactly one, or none)."""
    kids = children(state) if kids is None else kids
    return [ref for ref in slot_runs(state, experiment_id, slot) if ref not in kids]


def needs_rerun(state, run_id, seen=None):
    """Invalid itself, or some dependency (transitively) needs a rerun."""
    run = state.records.get(run_id)
    if run is None:
        return False
    if run["validity"] == "invalid":
        return True
    seen = (seen or set()) | {run_id}
    return any(needs_rerun(state, dep, seen) for dep in run["depends_on"] if dep not in seen)


def dependency_ready(state, dep_id):
    dep = state.records.get(dep_id)
    return dep is not None and dep["status"] == "completed" and not needs_rerun(state, dep_id)


def slot_violations(state, experiment_id, payload):
    """A new Run in an occupied slot must supersede the slot's current Run, and that Run must need a rerun."""
    slot, parent = payload.get("slot"), payload.get("supersedes")
    if slot is None:
        return ["supersedes needs a slot"] if parent else []
    heads = slot_heads(state, experiment_id, slot)
    if len(heads) > 1:
        return [f"slot {slot} has several current runs: {', '.join(state.label(h) for h in heads)}"]
    if not heads:
        return [f"slot {slot} is empty; supersedes {state.label(parent)} is not its current run"] if parent else []
    head = heads[0]
    if parent != head:
        return [f"R41: slot {slot} already holds {state.label(head)}; deprecate it and plan with --supersedes "
                f"{state.label(head)}"]
    if not needs_rerun(state, head):
        return [f"R41: {state.label(head)} is not deprecated and none of its dependencies is; it cannot be superseded"]
    return []


def dependency_violations(state, refs):
    found = []
    for ref in refs:
        if state.kinds.get(ref) != "run":
            found.append(f"depends_on {ref} is not an existing run")
    return found


def start_violations(state, run):
    """At start: every dependency is completed and needs no rerun (R42); the Run itself is not deprecated."""
    found = [] if run["validity"] != "invalid" else [f"{run['alias']} is deprecated; plan a superseding run"]
    for dep in run["depends_on"]:
        if not dependency_ready(state, dep):
            found.append(f"R42: dependency {state.label(dep)} is not completed, or needs a rerun")
    return found


def same_code_and_inputs(original, resumed):
    """Resume is allowed only with the same commit, command, seed and input files (R40)."""
    found = [] if original["git"] == resumed["git"] else ["resume code repository/commit differs"]
    found += [f"resume {key} differs from the original run" for key in ("command", "seed", "inputs")
              if original[key] != resumed[key]]
    return found


def same_restart_definition(original, restarted):
    """An external restart may relocate run output and checksummed data caches."""
    left, right = dict(original), dict(restarted)
    def execution_command(command):
        tokens = shlex.split(command)
        result, index = [], 0
        while index < len(tokens):
            token = tokens[index]
            if token in ("--run-name", "--dataset-root"):
                if index + 1 >= len(tokens):
                    return tokens
                index += 2
            else:
                result.append(token)
                index += 1
        return result
    left["command"] = execution_command(left["command"])
    right["command"] = execution_command(right["command"])
    return same_code_and_inputs(left, right)
