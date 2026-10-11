"""Run commands (AGENTS.md section 7): plan, run/resume, outputs, interruption, validity, cells, claim table."""

import json
import os
import sys
import shutil
import re
from pathlib import Path

from . import capture, hf, store, worktable
from .hf import parse_hf
from .metrics import core_metrics
from .common import authority_args, authority_for, emit, parse_pairs, resolve_list, root_of, writer
from .errors import ResearchError
from .views import cell_runs
from .writer import SYSTEM_ACTOR, load_state

def parse_slot(items):
    return dict(parse_pairs(items, "--slot"))


def _plan_fields(state, args):
    fields = {"slot": parse_slot(args.slot), "depends_on": resolve_list(state, args.depends_on, "run"),
              "hf_inputs": [parse_hf(item) for item in args.hf_input or []], "products": args.produces or [],
              "run_kind": args.kind, "parameters": json.loads(Path(args.parameters).read_text()) if args.parameters else {},
              "archive_target": hf.parse_target(args.archive_to) if args.archive_to else None}
    if args.checkpoint:
        fields["checkpoint"] = hf.verify_ref(parse_hf(args.checkpoint))
    if args.supersedes:
        fields["supersedes"] = state.resolve(args.supersedes, "run")
    return fields if fields["slot"] else {key: value for key, value in fields.items() if key != "slot"}


# ---------------------------------------------------------------- plan

def cmd_run_plan(args):
    w = writer(args, "proposer")

    def build(state):
        fields = _plan_fields(state, args)
        if "slot" not in fields:
            raise ResearchError("run-plan needs --slot KEY=VALUE (repeatable)")
        experiment = state.resolve(args.experiment, "experiment")
        payload = dict(fields, alias=state.next_run_alias(experiment), experiment=experiment)
        return [w.event(state, "run_planned", payload)]

    return emit(w.transact(build))


# ---------------------------------------------------------------- run / resume

def read_outputs(path):
    """(outputs, gaps) from $RESEARCH_OUTPUTS: {name: 'model=owner/name@rev'}."""
    if not path.is_file():
        return {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, ["outputs_not_json"]
    if not isinstance(data, dict) or not all(isinstance(v, str) and hf.HF_RE.fullmatch(v) for v in data.values()):
        return {}, ["outputs_not_hf_refs"]
    return {name: parse_hf(text) for name, text in data.items()}, []


def _finish_payload(code, temp, run, gaps):
    metrics, metric_gaps = capture.read_metrics(temp / "metrics.json")
    metrics = core_metrics(run["run_kind"], metrics)
    outputs, output_gaps = read_outputs(temp / "outputs.json")
    if code == 0 and not metrics:
        raise ResearchError("successful execution must report core metrics")
    gaps = gaps + metric_gaps + output_gaps
    status = "succeeded" if code == 0 else "failed"
    payload = {"status": status, "exit_code": code, "metrics": metrics,
               "validity": "valid" if status == "succeeded" and not gaps else "unknown", "capture_gaps": gaps}
    if code:
        payload["failure_reason"] = f"process exited with code {code}; full stderr is in HF execution records"
    if outputs:
        payload["outputs"] = outputs
    return payload


def _attempt_directory(root, run):
    return Path(root).parent / "runs" / "research" / run["id"] / f"attempt-{run['attempt']}"


def _archive(w, run, directory):
    ref = hf.upload_folder(directory, run["archive_target"],
                           f"runs/{run['id']}/attempts/{run['attempt']}")
    return _run_archive_event(w, run, ref)


def _run_archive_event(w, run, ref):
    def build(state):
        return [w.event(state, "run_archived", {"attempt": run["attempt"], "evidence": ref},
                        subject=run["id"], recorded_by=SYSTEM_ACTOR)]
    return w.transact(build)


def cmd_run_upload(args):
    """Retry only the upload; never execute training again."""
    w = writer(args, "executor")
    state = load_state(w.root)
    run = state.records[state.resolve(args.ref, "run")]
    directory = _attempt_directory(w.root, run)
    metadata = json.loads((directory / "execution.json").read_text())
    if metadata["run_id"] != run["id"] or metadata["attempt"] != run["attempt"]:
        raise ResearchError("execution directory belongs to another Run/attempt")
    if capture.compact(metadata["capture"]) != run["capture"]:
        raise ResearchError("execution capture differs from the registered Run")
    if "result" in metadata and run["status"] == "running" and "exit_code" not in run:
        def finish(state):
            return [w.event(state, "run_completed", metadata["result"], subject=run["id"],
                            recorded_by=SYSTEM_ACTOR, role="executor")]
        w.transact(finish)
    if "result" not in metadata:
        if run["status"] != "interrupted":
            raise ResearchError("mark the externally stopped execution with run-interrupt before archiving")
        metadata["interruption"] = run.get("failure_reason", "externally interrupted")
        store.atomic_write(directory / "execution.json", store.encode(metadata))
    emit(_archive(w, run, directory), sys.stderr)
    return _record_outputs(w, run, metadata.get("reported_outputs", {}))


def _record_outputs(w, run, outputs):
    for name, ref in outputs.items():
        hf.verify_ref(ref)
        def build(state):
            current = state.records[run["id"]]["outputs"].get(name)
            if current is not None:
                if current != ref:
                    raise ResearchError("recorded product differs from reported output")
                return []
            return [w.event(state, "run_output_recorded", {"name": name, "hf": ref}, subject=run["id"])]
        w.transact(build)
    return 0


def _start_event(w, args, record):
    """run_started for a planned run, run_resumed for an interrupted one, or a direct run_started."""
    def build(state):
        if args.resume or args.restart:
            subject = state.resolve(args.resume or args.restart, "run")
            return [w.event(state, "run_restarted" if args.restart else "run_resumed", {"capture": record}, subject=subject,
                            recorded_by=SYSTEM_ACTOR, role="executor")]
        if args.plan:
            subject = state.resolve(args.plan, "run")
            _refuse_if_taken_by_other(w, state.records[subject])
            planned = state.records[subject]
            payload = {"alias": planned["alias"], "experiment": planned["experiment"], "capture": record}
            return [w.event(state, "run_started", payload, subject=subject, recorded_by=SYSTEM_ACTOR, role="executor")]
        experiment = state.resolve(args.experiment, "experiment")
        payload = dict(_plan_fields(state, args), alias=state.next_run_alias(experiment),
                       experiment=experiment, capture=record)
        return [w.event(state, "run_started", payload, recorded_by=SYSTEM_ACTOR, role="executor")]

    (started,) = w.transact(build)
    return started


def _refuse_if_taken_by_other(w, run):
    if not worktable.shared(w.root):
        return
    owner = worktable.taker(w.root, run["experiment"], run["id"])
    if owner and owner != w.agent["id"]:
        raise ResearchError(f"{run['alias']} is taken by {owner} (R45)")


def cmd_run(args):
    w = writer(args, "executor")
    cwd = Path.cwd()
    reports = parse_pairs(args.record, "--record")
    if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) for name, _ in reports):
        raise ResearchError("record names must be lowercase identifiers")
    full, gaps = capture.capture(w.root, args.command, cwd, args.seed, args.input or [])
    top = capture._toplevel(cwd)
    with capture.frozen_checkout(top, full["git"]["commit"], args.command, cwd, args.input or []) as (command, work, source):
        started = _start_event(w, args, capture.compact(full))
        emit(started, sys.stderr)
        state = load_state(w.root)
        run = state.records[started["subject"]]
        directory = _attempt_directory(w.root, run)
        directory.mkdir(parents=True, exist_ok=False)
        metadata = {"run_id": run["id"], "attempt": run["attempt"], "capture": full, "source_checkout": str(source),
                    "hf_inputs": run["hf_inputs"], "parameters": run["parameters"],
                    "checkpoint": run.get("checkpoint"), "started_at": started["ts"]}
        store.atomic_write(directory / "execution.json", store.encode(metadata))
        env = dict(os.environ, RESEARCH_METRICS=str(directory / "metrics.json"),
                   RESEARCH_OUTPUTS=str(directory / "outputs.json"), RESEARCH_RUN_DIR=str(directory),
                   RESEARCH_RUN_ID=run["id"], RESEARCH_CHECKPOINT=json.dumps(run.get("checkpoint")),
                   PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE="1")
        code = capture.execute(command, work, env, sys.stdout, sys.stderr, directory)
        metadata["exit_code"] = code
        metadata["finished_at"] = store.now_ts()
        store.atomic_write(directory / "execution.json", store.encode(metadata))
        try:
            payload = _finish_payload(code, directory, run, gaps)
        except ResearchError as exc:
            payload = {"status": "succeeded" if code == 0 else "failed", "exit_code": code,
                       "metrics": {}, "validity": "unknown", "capture_gaps": gaps + ["invalid_result"],
                       "failure_reason": str(exc)[:512]}
        try:
            input_changed = capture.input_capture(args.input or []) != full["inputs"]
        except ResearchError as exc:
            input_changed = True
            payload["failure_reason"] = str(exc)[:512]
        if input_changed:
            payload["capture_gaps"].append("inputs_modified_during_execution")
            payload["validity"] = "unknown"
        if capture.checkout_changed(source):
            payload["capture_gaps"].append("source_modified_during_execution")
            payload["validity"] = "unknown"
        for name, path in reports:
            src = Path(path)
            if not src.is_file():
                payload["capture_gaps"].append("record_missing:" + name)
                payload["validity"] = "unknown"
                continue
            dest = directory / "checks" / (name + src.suffix)
            dest.parent.mkdir(exist_ok=True)
            shutil.copyfile(src, dest)
        metadata["reported_outputs"] = payload.pop("outputs", {})
        metadata["result"] = payload
        store.atomic_write(directory / "execution.json", store.encode(metadata))
        def finish(state):
            return [w.event(state, "run_completed", payload, subject=run["id"],
                            recorded_by=SYSTEM_ACTOR, role="executor")]
        emit(w.transact(finish), sys.stderr)
        emit(_archive(w, run, directory), sys.stderr)
        _record_outputs(w, run, metadata["reported_outputs"])
        execution_errors = {"invalid_result", "source_modified_during_execution", "inputs_modified_during_execution"}
        if execution_errors.intersection(payload["capture_gaps"]):
            raise ResearchError(payload.get("failure_reason", "execution changed its source/inputs; see archived records"))
    return code if code >= 0 else 128 - code


# ---------------------------------------------------------------- after the fact

def _run_act(args, etype, payload_of, role):
    w = writer(args, role)

    def build(state):
        subject = state.resolve(args.ref, "run")
        return [w.event(state, etype, payload_of(state, subject), subject=subject)]

    return emit(w.transact(build))


def cmd_run_dataset(args):
    w = writer(args, "interpreter")
    result = json.loads(Path(args.file).read_text())
    if "evidence" in result:
        raise ResearchError("evidence is registered through --evidence")
    result["evidence"] = hf.verify_ref(parse_hf(args.evidence))
    def build(state):
        return [w.event(state, "run_dataset_recorded", result, subject=state.resolve(args.ref, "run"))]
    return emit(w.transact(build))


def cmd_run_output(args):
    return _run_act(args, "run_output_recorded", lambda state, subject: {"name": args.name, "hf": hf.verify_ref(parse_hf(args.hf))},
                    "executor")


def cmd_run_interrupt(args):
    return _run_act(args, "run_interrupted", lambda state, subject: {"reason": args.reason}, "executor")


def cmd_run_validity(args):
    def payload_of(state, subject):
        authority = authority_for(state, args, "run_validity", subject, {"to": args.to}, args.to == "valid")
        return {"to": args.to, "reason": args.reason, "authority": authority}

    return _run_act(args, "run_validity_changed", payload_of, "interpreter")


# ---------------------------------------------------------------- cells and claim table

def _view_spec(state, experiment_id):
    spec = state.records[experiment_id]["view"]
    if spec is None:
        raise ResearchError(f"{state.label(experiment_id)} has no view")
    return spec


def cmd_cell(args):
    state = load_state(root_of(args))
    experiment_id = state.resolve(args.experiment, "experiment")
    spec = _view_spec(state, experiment_id)
    for given, axis in ((args.row, "rows"), (args.col, "cols")):
        if given not in spec[axis]["values"]:
            raise ResearchError(f"{given!r} is not one of the view {axis}: {spec[axis]['values']}")
    return emit(cell_runs(state, experiment_id, args.row, args.col, args.rep))


def _claim_table_act(args, act):
    w = writer(args)
    state = load_state(w.root)
    run = state.records[state.resolve(args.ref, "run")]
    table = act(w.root, run["experiment"], run["id"], run["alias"], w.agent["id"])
    return emit({"experiment": run["experiment"], "taken": table})


def cmd_takes(args):
    root = root_of(args)
    state = load_state(root)
    experiment_id = state.resolve(args.experiment, "experiment")
    return emit(worktable.remote_table(root, experiment_id))


# ---------------------------------------------------------------- parser

def _plan_args(parser):
    parser.add_argument("--kind", choices=("dataset", "training", "evaluation"), help="required when creating a Run")
    parser.add_argument("--archive-to", help="required destination model|dataset=OWNER/NAME for raw records")
    parser.add_argument("--parameters", help="JSON of at most 20 scalar experimental settings")
    parser.add_argument("--checkpoint", help="evaluation input model=OWNER/NAME@REV[:path]")
    parser.add_argument("--slot", action="append", help="KEY=VALUE slot coordinate (repeatable)")
    parser.add_argument("--depends-on", dest="depends_on", help="upstream Run refs, comma-separated")
    parser.add_argument("--supersedes", help="the deprecated Run in the same slot this one replaces")
    parser.add_argument("--hf-input", dest="hf_input", action="append",
                        help="model|dataset=OWNER/NAME@<40-hex revision> (repeatable)")
    parser.add_argument("--produces", action="append", help="product that must be recorded on HF, e.g. checkpoint")


def register(sub, common):
    p = sub.add_parser("run-plan", parents=[common], help="reserve a slot (code is bound at start)")
    p.add_argument("--experiment", required=True)
    _plan_args(p)
    p.set_defaults(func=cmd_run_plan)
    p = sub.add_parser("run", parents=[common], help="run CMD as evidence: research run (--plan E1-R001 | --resume E1-R001 "
                                                     "| --experiment E-n [plan flags]) -- CMD")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--plan", help="start this planned Run")
    target.add_argument("--resume", help="continue this interrupted Run with the same code and inputs")
    target.add_argument("--restart", help="restart an externally interrupted Run from scratch with unchanged code and input hashes")
    target.add_argument("--experiment", help="create and start a Run directly")
    _plan_args(p)
    p.add_argument("--seed", type=int)
    p.add_argument("--input", action="append", help="input file to checksum (repeatable)")
    p.add_argument("--record", action="append", help="NAME=PATH: copy a check report beside logs before HF archival")
    p.set_defaults(func=cmd_run, command=[])
    p = sub.add_parser("run-upload", parents=[common], help="retry archival of an existing execution, without rerunning it")
    p.add_argument("ref")
    p.set_defaults(func=cmd_run_upload)
    p = sub.add_parser("run-dataset", parents=[common], help="record evidence-backed dataset counts and publication on its owning Run")
    p.add_argument("ref")
    p.add_argument("--file", required=True, help="JSON: accepted_samples, published_samples, optional hf reference")
    p.add_argument("--evidence", required=True, help="pinned HF evidence model|dataset=OWNER/NAME@REV:path")
    p.set_defaults(func=cmd_run_dataset)
    p = sub.add_parser("run-output", parents=[common], help="record a product uploaded to HF")
    p.add_argument("ref")
    p.add_argument("--name", required=True)
    p.add_argument("--hf", required=True, help="model|dataset=OWNER/NAME@<40-hex revision>")
    p.set_defaults(func=cmd_run_output)
    p = sub.add_parser("run-interrupt", parents=[common], help="mark a Run stopped by something outside it")
    p.add_argument("ref")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_run_interrupt)
    p = sub.add_parser("run-validity", parents=[common], help="--to invalid deprecates a Run (R41)")
    p.add_argument("ref")
    p.add_argument("--to", choices=("valid", "invalid", "unknown"), required=True)
    p.add_argument("--reason", required=True)
    authority_args(p)
    p.set_defaults(func=cmd_run_validity)
    p = sub.add_parser("cell", parents=[common], help="resolve a view cell to its slots and current Runs")
    p.add_argument("experiment")
    p.add_argument("--row", required=True)
    p.add_argument("--col", required=True)
    p.add_argument("--rep", help="one replicate value only")
    p.set_defaults(func=cmd_cell)
    for name, act in (("take", worktable.take), ("release", worktable.release)):
        p = sub.add_parser(name, parents=[common], help=f"{name} a Run in its experiment's claim table (git push)")
        p.add_argument("ref")
        p.set_defaults(func=lambda args, act=act: _claim_table_act(args, act))
    p = sub.add_parser("takes", parents=[common], help="show an experiment's claim table")
    p.add_argument("experiment")
    p.set_defaults(func=cmd_takes)
