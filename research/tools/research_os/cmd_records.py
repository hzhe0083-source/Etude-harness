"""Commands for configuration and planning records: project, policy, hypothesis, experiment, authority."""

import json
import os
from pathlib import Path

from . import store, hf
from .common import (agent, approval, authority_args, authority_for, emit, human_actor, parse_pairs, parse_scope,
                     provenanced, resolve_list, split, view_addition, view_addition_args, writer)
from .errors import ResearchError
from .state import extension_needs_authority
from .writer import Writer

KINDS = ("feasibility", "debug", "search", "main", "ablation", "replication", "aggregation", "dataset")
SCOPES = ("experiment_contract", "claim_transition", "decision", "run_validity", "policy", "view_extension")
CONTRACT_FLAGS = (("intent", "intent"), ("decision_rule", "decision_rule"),
                  ("baseline", "baseline"), ("primary_metric", "metric"))


# ---------------------------------------------------------------- project

def _field_source(args):
    """Who wrote the project values: the human, or the agent (--drafted) with the human only approving them."""
    return agent(args) if args.drafted else human_actor(args)

DRAFTED_HELP = "values were drafted by the agent; the human only approved them (source = agent)"


def cmd_init(args):
    root = store.init_layout(args.root or os.environ.get("RESEARCH_OS_ROOT") or "research")
    w = Writer(root, agent(args), args.role)
    source = _field_source(args)

    def build(state):
        payload = {
            "research_goal": provenanced(args.goal, source),
            "default_primary_metric": provenanced(args.metric, source),
            "default_baseline": provenanced(args.baseline, source),
            "hard_constraints": provenanced(args.constraint, source),
            "approval_record": approval(args),
        }
        return [w.event(state, "project_initialized", payload, actor=human_actor(args))]

    return emit(w.transact(build))


def cmd_project_update(args):
    w = writer(args)
    source = _field_source(args)
    fields = {"research_goal": args.goal, "default_primary_metric": args.metric, "default_baseline": args.baseline,
              "hard_constraints": args.constraint}
    changes = {key: provenanced(value, source) for key, value in fields.items() if value is not None}
    if not changes:
        raise ResearchError("project update needs at least one of --goal/--metric/--baseline/--constraint")

    def build(state):
        payload = dict(changes, approval_record=approval(args))
        return [w.event(state, "project_updated", payload, subject=state.project_id, actor=human_actor(args))]

    return emit(w.transact(build))


# ---------------------------------------------------------------- policy

def cmd_policy_grant(args):
    w = writer(args)
    if args.expires_at:
        store.parse_ts(args.expires_at)

    def build(state):
        scope = "project" if args.scope == "project" else {"hypothesis": state.resolve(args.scope, "hypothesis")}
        payload = {
            "alias": state.next_alias("P"), "scope": scope,
            "permits": {"experiment_kinds": split(args.kinds), "claim_transitions": split(args.claim_transitions),
                        "decisions": args.decisions, "run_validity": args.run_validity},
            "constraints": json.loads(args.constraints), "require_verification": args.require_verification,
            "approval_record": approval(args),
        }
        if args.expires_at:
            payload["expires_at"] = args.expires_at
        return [w.event(state, "policy_granted", payload, actor=human_actor(args))]

    return emit(w.transact(build))


def cmd_policy_revoke(args):
    w = writer(args)

    def build(state):
        payload = {"reason": args.reason, "approval_record": approval(args)}
        subject = state.resolve(args.policy_ref, "policy")
        return [w.event(state, "policy_revoked", payload, subject=subject, actor=human_actor(args))]

    return emit(w.transact(build))


# ---------------------------------------------------------------- hypothesis

def cmd_hypothesis_create(args):
    w = writer(args, "proposer")

    def build(state):
        payload = {"alias": state.next_alias("H"), "statement": args.statement, "scope": parse_scope(args.scope)}
        if args.parent:
            payload["parent"] = state.resolve(args.parent, "hypothesis")
        return [w.event(state, "hypothesis_created", payload)]

    return emit(w.transact(build))


def cmd_hypothesis_close(args):
    w = writer(args, "interpreter")

    def build(state):
        payload = {"resolution": resolve_list(state, args.resolution)}
        return [w.event(state, "hypothesis_closed", payload, subject=state.resolve(args.ref, "hypothesis"))]

    return emit(w.transact(build))


def cmd_hypothesis_reopen(args):
    w = writer(args, "proposer")

    def build(state):
        subject = state.resolve(args.ref, "hypothesis")
        return [w.event(state, "hypothesis_reopened", {"reason": args.reason}, subject=subject)]

    return emit(w.transact(build))


# ---------------------------------------------------------------- experiment

def _flag_name(flag):
    return f"--{flag.replace('_', '-')}"


def load_view(path, source):
    return provenanced(json.loads(Path(path).read_text(encoding="utf-8")), source)


def build_contract(state, args, source, current):
    """Full contract: given flags (agent source), else the current contract, else Project defaults (inherited)."""
    project = state.project()
    contract, missing = {}, []
    for key, flag in CONTRACT_FLAGS:
        value = getattr(args, flag)
        if value is not None:
            contract[key] = provenanced(value, source)
        elif current and key in current:
            contract[key] = current[key]
        elif key in ("baseline", "primary_metric"):
            inherited = {"type": "inherited", "from": state.project_id}
            contract[key] = provenanced(project[f"default_{key}"]["value"], inherited)
        else:
            missing.append(_flag_name(flag))
    if missing:
        raise ResearchError(f"R15: a contract needs {', '.join(missing)}")
    if args.view:
        contract["view"] = load_view(args.view, source)
    elif current and "view" in current:
        contract["view"] = current["view"]
    return contract


def _wants_contract(args):
    flags = [getattr(args, flag) for _, flag in CONTRACT_FLAGS] + [args.view]
    return args.mode == "committed" or any(value is not None for value in flags)


def _dataset_view(args, source):
    """A dataset experiment has a view but no contract (R38): there is no decision rule to pre-register."""
    flags = [_flag_name(flag) for _, flag in CONTRACT_FLAGS if getattr(args, flag) is not None]
    if flags:
        raise ResearchError(f"R38: a dataset experiment has no contract; drop {', '.join(flags)}")
    if args.mode != "scout":
        raise ResearchError("R38: a dataset experiment must be --mode scout")
    if not args.view:
        raise ResearchError("R38: a dataset experiment needs --view")
    return load_view(args.view, source)


def cmd_experiment_create(args):
    w = writer(args, "proposer")
    kind = args.kind or ("debug" if args.debug else None)
    if kind is None:
        raise ResearchError("--kind is required unless --debug")

    def build(state):
        payload = {
            "alias": state.next_alias("E"),
            "hypothesis": None if args.debug else state.resolve(args.hypothesis, "hypothesis"),
            "kind": kind, "mode": args.mode, "motivated_by": resolve_list(state, args.motivated_by, "experiment"),
        }
        if kind == "dataset":
            payload["view"] = _dataset_view(args, w.agent)
        elif _wants_contract(args):
            payload["contract"] = build_contract(state, args, w.agent, None)
        return [w.event(state, "experiment_created", payload)]

    return emit(w.transact(build))


def cmd_experiment_amend(args):
    w = writer(args, "proposer")

    def build(state):
        subject = state.resolve(args.ref, "experiment")
        record = state.records[subject]
        current = record.get("contract")
        if current and "view" in current:
            # Keep authorized extensions: the amended contract starts from the view as it stands now.
            current = dict(current, view=dict(current["view"], value=record["view"]))
        contract = build_contract(state, args, w.agent, current)
        return [w.event(state, "experiment_amended", {"contract": contract, "reason": args.reason}, subject=subject)]

    return emit(w.transact(build))


def cmd_experiment_extend(args):
    w = writer(args, "proposer")
    add = view_addition(args)
    if not add:
        raise ResearchError("extend needs --rows, --cols or --replicates")

    def build(state):
        subject = state.resolve(args.ref, "experiment")
        required = extension_needs_authority(state.records[subject])
        authority = authority_for(state, args, "view_extension", subject, {"add": add}, required)
        payload = {"add": add, "reason": args.reason, "authority": authority}
        return [w.event(state, "experiment_view_extended", payload, subject=subject)]

    return emit(w.transact(build))


def cmd_experiment_complete(args):
    w = writer(args, "interpreter")

    def build(state):
        payload = {"decision_rule_result": args.result} if args.result else {}
        return [w.event(state, "experiment_completed", payload, subject=state.resolve(args.ref, "experiment"))]

    return emit(w.transact(build))


def cmd_experiment_legacy(args):
    w = writer(args, "interpreter")

    def build(state):
        events = []
        for ref in args.refs:
            subject = state.resolve(ref, "experiment")
            events.append(w.event(state, "experiment_marked_legacy", {"reason": args.reason}, subject=subject))
        return events

    return emit(w.transact(build))


def cmd_experiment_dataset(args):
    w = writer(args, "interpreter")
    payload = json.loads(Path(args.file).read_text())
    if "evidence" in payload:
        raise ResearchError("evidence is registered through --evidence")
    payload["evidence"] = hf.verify_ref(hf.parse_hf(args.evidence))
    def build(state):
        return [w.event(state, "experiment_dataset_recorded", payload,
                        subject=state.resolve(args.ref, "experiment"))]
    return emit(w.transact(build))


def cmd_experiment_style(args):
    w = writer(args, "proposer")
    payload = {key: value for key, value in (
        ("title", args.title), ("row_labels", dict(parse_pairs(args.row_label, "--row-label"))),
        ("col_labels", dict(parse_pairs(args.col_label, "--col-label")))) if value}
    if not payload:
        raise ResearchError("style needs --title, --row-label or --col-label")

    def build(state):
        subject = state.resolve(args.ref, "experiment")
        return [w.event(state, "experiment_view_styled", payload, subject=subject)]

    return emit(w.transact(build))


def cmd_experiment_abandon(args):
    w = writer(args, "interpreter")

    def build(state):
        subject = state.resolve(args.ref, "experiment")
        return [w.event(state, "experiment_abandoned", {"reason": args.reason}, subject=subject)]

    return emit(w.transact(build))


# ---------------------------------------------------------------- authority

def cmd_authority_request(args):
    w = writer(args, "proposer")

    def build(state):
        subject = state.resolve(args.ref)
        requested = {"to": args.to} if args.to else {}
        add = view_addition(args)
        if add:
            requested["add"] = add
        payload = {
            "scope": args.scope, "subject_version": state.version(subject), "requested": requested,
            "summary": {"diff": args.diff, "on_approve": args.on_approve, "would_change_conclusion": args.would_change},
        }
        return [w.event(state, "authority_requested", payload, subject=subject)]

    return emit(w.transact(build))


def _answer(args, etype):
    w = writer(args)

    def build(state):
        request = state.events.get(args.request)
        if request is None or request["type"] != "authority_requested":
            raise ResearchError(f"{args.request} is not an authority_requested event id (see `research pending-authority`)")
        payload = {"request": args.request, "subject_version": request["payload"]["subject_version"],
                   "approval_record": approval(args)}
        return [w.event(state, etype, payload, subject=request["subject"], actor=human_actor(args))]

    return emit(w.transact(build))


# ---------------------------------------------------------------- parser

def _contract_flags(parser):
    parser.add_argument("--intent")
    parser.add_argument("--decision-rule", dest="decision_rule")
    parser.add_argument("--baseline")
    parser.add_argument("--metric", help="primary metric")
    parser.add_argument("--view", help="JSON file with the view spec (SCHEMA.json $defs/ViewSpec); part of the contract")


def _register_project(sub, common, human):
    p = sub.add_parser("init", parents=[common, human], help="initialize the project (human statement)")
    p.add_argument("--goal", required=True)
    p.add_argument("--metric", required=True, help="default primary metric")
    p.add_argument("--baseline", required=True, help="default baseline")
    p.add_argument("--constraint", action="append", default=[], help="hard constraint (repeatable)")
    p.add_argument("--drafted", action="store_true", help=DRAFTED_HELP)
    p.set_defaults(func=cmd_init)
    project = sub.add_parser("project").add_subparsers(dest="action", required=True)
    p = project.add_parser("update", parents=[common, human])
    for flag in ("--goal", "--metric", "--baseline"):
        p.add_argument(flag)
    p.add_argument("--constraint", action="append", help="replaces the hard constraint list (repeatable)")
    p.add_argument("--drafted", action="store_true", help=DRAFTED_HELP)
    p.set_defaults(func=cmd_project_update)


def _register_policy(sub, common, human):
    policy = sub.add_parser("policy").add_subparsers(dest="action", required=True)
    p = policy.add_parser("grant", parents=[common, human])
    p.add_argument("--scope", required=True, help="project or a Hypothesis alias/id")
    p.add_argument("--kinds", default="", help="experiment kinds whose committed contracts are covered")
    p.add_argument("--claim-transitions", dest="claim_transitions", default="", help="supported,refuted")
    p.add_argument("--decisions", action="store_true")
    p.add_argument("--run-validity", dest="run_validity", action="store_true")
    p.add_argument("--constraints", default="{}", help="JSON object, e.g. {\"primary_metric\": \"acc\"}")
    p.add_argument("--require-verification", dest="require_verification", action="store_true")
    p.add_argument("--expires-at", dest="expires_at")
    p.set_defaults(func=cmd_policy_grant)
    p = policy.add_parser("revoke", parents=[common, human])
    p.add_argument("policy_ref")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_policy_revoke)


def _register_hypothesis(sub, common):
    hyp = sub.add_parser("hypothesis").add_subparsers(dest="action", required=True)
    p = hyp.add_parser("create", parents=[common])
    p.add_argument("--statement", required=True)
    p.add_argument("--scope", action="append", help="KEY=V1,V2 (repeatable)")
    p.add_argument("--parent")
    p.set_defaults(func=cmd_hypothesis_create)
    p = hyp.add_parser("close", parents=[common])
    p.add_argument("ref")
    p.add_argument("--resolution", required=True, help="supported/refuted Claims or made Decisions, comma-separated")
    p.set_defaults(func=cmd_hypothesis_close)
    p = hyp.add_parser("reopen", parents=[common])
    p.add_argument("ref")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_hypothesis_reopen)


def _register_experiment(sub, common):
    exp = sub.add_parser("experiment").add_subparsers(dest="action", required=True)
    p = exp.add_parser("create", parents=[common])
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--hypothesis")
    target.add_argument("--debug", action="store_true", help="debug experiment without a hypothesis")
    p.add_argument("--kind", choices=KINDS)
    p.add_argument("--mode", choices=("scout", "committed"), required=True)
    p.add_argument("--motivated-by", dest="motivated_by", help="scout experiments, comma-separated (R17)")
    _contract_flags(p)
    p.set_defaults(func=cmd_experiment_create)
    p = exp.add_parser("dataset", parents=[common], help="record a dataset-wide card with targets and publication evidence")
    p.add_argument("ref")
    p.add_argument("--file", required=True)
    p.add_argument("--evidence", required=True)
    p.set_defaults(func=cmd_experiment_dataset)
    p = exp.add_parser("amend", parents=[common])
    p.add_argument("ref")
    p.add_argument("--reason", required=True)
    _contract_flags(p)
    p.set_defaults(func=cmd_experiment_amend)
    p = exp.add_parser("extend", parents=[common],
                       help="append values to the view's axes; needs A6 unless the experiment is a dataset")
    p.add_argument("ref")
    view_addition_args(p)
    p.add_argument("--reason", required=True)
    authority_args(p)
    p.set_defaults(func=cmd_experiment_extend)
    p = exp.add_parser("complete", parents=[common])
    p.add_argument("ref")
    p.add_argument("--result", choices=("met", "not_met", "inconclusive"))
    p.set_defaults(func=cmd_experiment_complete)
    p = exp.add_parser("legacy", parents=[common], help="mark experiments as legacy: kept, excluded from views")
    p.add_argument("refs", nargs="+")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_experiment_legacy)
    p = exp.add_parser("style", parents=[common], help="display-only view text; needs no authority")
    p.add_argument("ref")
    p.add_argument("--title")
    p.add_argument("--row-label", dest="row_label", action="append", help="VALUE=LABEL (repeatable)")
    p.add_argument("--col-label", dest="col_label", action="append", help="VALUE=LABEL (repeatable)")
    p.set_defaults(func=cmd_experiment_style)
    p = exp.add_parser("abandon", parents=[common])
    p.add_argument("ref")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_experiment_abandon)


def _register_authority(sub, common, human):
    auth = sub.add_parser("authority").add_subparsers(dest="action", required=True)
    p = auth.add_parser("request", parents=[common])
    p.add_argument("ref")
    p.add_argument("--scope", choices=SCOPES, required=True)
    p.add_argument("--to", help="requested status (claim/run/decision/policy scopes)")
    view_addition_args(p)
    p.add_argument("--diff", required=True)
    p.add_argument("--on-approve", dest="on_approve", required=True)
    p.add_argument("--would-change", dest="would_change", required=True)
    p.set_defaults(func=cmd_authority_request)
    for action, etype in (("grant", "authority_granted"), ("deny", "authority_denied")):
        p = auth.add_parser(action, parents=[common, human])
        p.add_argument("request", help="event_id of the authority_requested event")
        p.set_defaults(func=lambda args, etype=etype: _answer(args, etype))


def register(sub, common, human):
    _register_project(sub, common, human)
    _register_policy(sub, common, human)
    _register_hypothesis(sub, common)
    _register_experiment(sub, common)
    _register_authority(sub, common, human)
