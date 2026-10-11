"""Experiment views (AGENTS.md R44): computed from events every time, never stored or maintained.

A view stores no Run pointers. Each cell names slots; the Run shown for a slot is the end of its
supersession chain, so replacing a Run needs no view update.
"""

import math
import statistics

from .runs import children, dependency_ready, needs_rerun, slot_heads
from .schema import view_addition_errors
from .state import axis_of, extension_needs_authority

# Worst first: a cell shows the first state any of its slots is in; it is done only when all are.
STATES = ("rerun", "interrupted", "running", "waiting", "pending", "done")
RUN_STATE = {"running": "running", "interrupted": "interrupted", "completed": "done"}
# Two-sided 95% Student t quantiles for 1..30 degrees of freedom.
T975 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145,
        2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048,
        2.045, 2.042)


# ---------------------------------------------------------------- spec checks

def _axis_keys(spec):
    keys = [spec["rows"]["key"], spec["cols"]["key"]]
    replicates = spec["cell"].get("replicates")
    return keys + [replicates["key"]] if replicates else keys


def view_spec_violations(spec):
    keys = _axis_keys(spec)
    found = [] if len(set(keys)) == len(keys) else [f"R44: view axis keys must differ: {keys}"]
    fixed = set(keys) & set(spec["cell"]["match"])
    if fixed:
        found.append(f"R44: cell.match must not fix axis keys {sorted(fixed)}")
    if spec["rows"]["key"] in spec.get("row_model", {}):
        found.append("R44: row_model must not fix rows.key; each row adds it")
    return found


def extension_violations(spec, add):
    """Values may only be appended to existing axes (R44); keys, match and metric never change."""
    found = view_addition_errors(add)
    if found:
        return found
    for axis, values in add.items():
        current = axis_of(spec, axis)
        if current is None:
            found.append(f"R44: the view has no {axis} axis")
            continue
        repeated = sorted(set(values) & set(current["values"]))
        if repeated:
            found.append(f"R44: {axis} already has {repeated}")
    return found


def slot_outside_view(spec, slot):
    """Axis values in `slot` that the view does not list: a Run must not occupy a cell before it exists (R44)."""
    found = []
    for axis in ("rows", "cols", "replicates"):
        current = axis_of(spec, axis)
        value = slot.get(current["key"]) if current else None
        if value is not None and value not in current["values"]:
            found.append(f"R44: {current['key']}={value} is not in the view {axis}; extend the view first")
    return found


def style_violations(spec, style):
    found = []
    for field, axis in (("row_labels", "rows"), ("col_labels", "cols")):
        unknown = sorted(set(style.get(field, {})) - set(spec[axis]["values"]))
        if unknown:
            found.append(f"{field} names values not in the view: {unknown}")
    return found


# ---------------------------------------------------------------- cell computation

def cell_slots(spec, row, col):
    """[(replicate value or None, slot)] for cell (row, col)."""
    base = dict(spec["cell"]["match"], **{spec["rows"]["key"]: row, spec["cols"]["key"]: col})
    replicates = spec["cell"].get("replicates")
    if not replicates:
        return [(None, base)]
    return [(value, dict(base, **{replicates["key"]: value})) for value in replicates["values"]]


def slot_head(state, experiment_id, slot, kids):
    heads = slot_heads(state, experiment_id, slot, kids)
    return max(heads) if heads else None


def slot_state(state, run_id):
    if run_id is None:
        return "pending"
    run = state.records[run_id]
    if needs_rerun(state, run_id):
        return "rerun"
    if run["status"] != "planned":
        return RUN_STATE[run["status"]]
    return "pending" if all(dependency_ready(state, dep) for dep in run["depends_on"]) else "waiting"


def cell_state(states):
    return next(state for state in STATES if state in states)


def summarize(values):
    """Mean with a two-sided 95% t interval half-width (None below two values)."""
    if not values:
        return None
    mean = statistics.fmean(values)
    if len(values) < 2:
        return {"mean": mean, "ci": None, "n": 1}
    t = T975[min(len(values) - 1, len(T975)) - 1]
    return {"mean": mean, "ci": t * statistics.stdev(values) / math.sqrt(len(values)), "n": len(values)}


def _entry(state, experiment_id, metric, replicate, slot, kids):
    run_id = slot_head(state, experiment_id, slot, kids)
    status = slot_state(state, run_id)
    value = state.records[run_id]["metrics"].get(metric) if status == "done" else None
    return {"replicate": replicate, "slot": slot, "run": run_id, "state": status, "value": value,
            "dataset_result": state.records[run_id].get("dataset_result") if status == "done" else None}


def _cell(state, experiment_id, spec, row, col, kids):
    metric = spec["cell"]["metric"]
    entries = [_entry(state, experiment_id, metric, rep, slot, kids) for rep, slot in cell_slots(spec, row, col)]
    values = [entry["value"] for entry in entries if entry["value"] is not None]
    return {"row": row, "col": col, "state": cell_state([e["state"] for e in entries]), "entries": entries,
            "summary": summarize(values), "expected": len(entries)}


def _matches(slot, wanted):
    return all(slot.get(key) == value for key, value in wanted.items())


def _row_products(state, experiment_id, spec, row, kids):
    dataset = state.records[experiment_id]["kind"] == "dataset"
    if not dataset and "row_model" not in spec:
        return []
    wanted = dict(spec.get("row_model", {}), **{spec["rows"]["key"]: row})
    found = []
    for ref in state.records[experiment_id]["runs"]:
        run = state.records[ref]
        usable = ref not in kids and run["status"] == "completed" and not needs_rerun(state, ref)
        if usable and _matches(run.get("slot") or {}, wanted):
            product = run.get("dataset_result", {}).get("hf") if dataset else run["outputs"].get("checkpoint")
            if product:
                found.append({"run": ref, "slot": run["slot"], "hf": product})
    return found


def _decisions(state, experiment_id):
    refs = {experiment_id, *state.records[experiment_id]["runs"]}
    return [decision for decision in state.of_kind("decision") if refs & set(decision["based_on"])]


def experiment_view(state, experiment_id):
    """Everything a table needs, resolved now from events."""
    exp = state.records[experiment_id]
    spec, style = exp["view"], exp.get("view_style") or {}
    # Scout views (datasets included) grow freely; a committed view marks values appended after registration.
    added = exp["view_extended"] if extension_needs_authority(exp) else {}
    kids = children(state)
    rows = [{"value": row, "label": style.get("row_labels", {}).get(row, row), "added": row in added.get("rows", []),
             "products": _row_products(state, experiment_id, spec, row, kids),
             "cells": [_cell(state, experiment_id, spec, row, col, kids) for col in spec["cols"]["values"]]}
            for row in spec["rows"]["values"]]
    hypothesis = state.get(exp["hypothesis"], "hypothesis") if exp["hypothesis"] else None
    contract = exp.get("contract")
    return {
        "experiment": experiment_id, "alias": exp["alias"],
        "title": style.get("title") or (contract["intent"]["value"] if contract else exp["alias"]),
        "kind": exp["kind"], "dataset_card": exp.get("dataset_card"), "hypothesis": hypothesis and hypothesis["statement"], "metric": spec["cell"]["metric"],
        "cols": [{"value": col, "label": style.get("col_labels", {}).get(col, col),
                  "added": col in added.get("cols", [])} for col in spec["cols"]["values"]],
        "rows": rows, "added_replicates": added.get("replicates", []), "decisions": _decisions(state, experiment_id),
    }


def view_experiments(state):
    """Experiments that have a view and are not legacy, in alias order."""
    return [exp["id"] for exp in sorted(state.of_kind("experiment"), key=lambda exp:int(exp["alias"].split("-")[1]))
            if exp["view"] and not exp["legacy"]]


def chain(state, run_id):
    """The supersession chain ending at `run_id`, oldest first."""
    found = [run_id]
    while state.records[found[0]].get("supersedes") in state.records:
        found.insert(0, state.records[found[0]]["supersedes"])
    return found


def cell_runs(state, experiment_id, row, col, replicate=None):
    """Resolve a cell coordinate (R45) to its slots, current Runs and their chains."""
    spec = state.records[experiment_id]["view"]
    kids = children(state)
    found = []
    for rep, slot in cell_slots(spec, row, col):
        if replicate is not None and rep != replicate:
            continue
        run_id = slot_head(state, experiment_id, slot, kids)
        found.append({"replicate": rep, "slot": slot, "state": slot_state(state, run_id),
                      "run": run_id and state.label(run_id),
                      "chain": [state.label(ref) for ref in chain(state, run_id)] if run_id else []})
    return found
