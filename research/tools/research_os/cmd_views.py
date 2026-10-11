"""Read-only views, handoff state and maintenance commands."""

import json
from collections import defaultdict
from pathlib import Path

from . import context, handoff, queries, render, store
from .common import agent, emit, root_of, writer
from .derive import project_object
from .errors import ResearchError
from .state import ALIAS_RE, KINDS
from .validate import validate
from .views import experiment_view, view_experiments
from .writer import SYSTEM_ACTOR, load_state


def _state(args):
    root = root_of(args)
    return root, load_state(root)


def cmd_define_view(args):
    w = writer(args)
    payload = json.loads(Path(args.file).read_text())
    def build(state):
        return [w.event(state, "experiment_view_defined", payload, subject=state.resolve(args.ref, "experiment"))]
    return emit(w.transact(build))


def cmd_position(args):
    from .common import parse_pairs
    w = writer(args)
    def build(state):
        payload = {"slot": dict(parse_pairs(args.slot, "--slot"))}
        if args.supersedes:
            payload["supersedes"] = state.resolve(args.supersedes, "run")
        return [w.event(state, "run_positioned", payload, subject=state.resolve(args.ref, "run"))]
    return emit(w.transact(build))


def cmd_show(args):
    _, state = _state(args)
    return emit(project_object(state, state.resolve(args.ref), store.now_ts()))


def cmd_list(args):
    _, state = _state(args)
    where = [tuple(item.split("=", 1)) for item in args.where or []]
    if any(len(pair) != 2 for pair in where):
        raise ResearchError("--where takes KEY=VALUE")
    return emit(queries.list_records(state, args.type, where, store.now_ts()))


def cmd_evidence(args):
    _, state = _state(args)
    return emit(queries.evidence_tree(state, state.resolve(args.ref)))


def cmd_context(args):
    root, state = _state(args)
    return emit(context.build(state, root, state.resolve(args.ref), args.role, store.now_ts()))


def cmd_pending(args):
    _, state = _state(args)
    return emit(queries.pending_authority(state))


def cmd_handoff_save(args):
    root = root_of(args)
    body = json.loads(Path(args.file).read_text(encoding="utf-8"))
    return emit(handoff.save(root, body, agent(args)))


def cmd_handoff_load(args):
    root, state = _state(args)
    scope_ids = {state.resolve(args.scope)} if args.scope else None
    return emit(handoff.latest(root, store.now_ts(), scope_ids))


def cmd_handoff_clear(args):
    if bool(args.alias) == args.all:
        raise ResearchError("handoff clear takes exactly one of HS-n or --all")
    return emit({"cleared": handoff.clear(root_of(args), None if args.all else args.alias)})


def _repairs(state):
    """(record id, from, to) for every duplicate alias; the earliest-created record keeps it."""
    holders = defaultdict(list)
    for ref, obj in state.records.items():
        if obj.get("alias"):
            holders[obj["alias"]].append(ref)
    counters = {}
    for alias in sorted(holders):
        ids = sorted(holders[alias], key=lambda ref: state.records[ref]["_created_event"])
        if state.kinds[ids[0]] == "run":
            for loser in ids[1:]:
                experiment = state.records[loser]["experiment"]
                counters.setdefault(experiment, int(state.next_run_alias(experiment).split("-R")[1]))
                yield loser, alias, state.run_alias(experiment, counters[experiment])
                counters[experiment] += 1
            continue
        prefix = ALIAS_RE.match(alias).group(1)
        for loser in ids[1:]:
            counters.setdefault(prefix, int(state.next_alias(prefix).split("-")[1]))
            yield loser, alias, f"{prefix}-{counters[prefix]}"
            counters[prefix] += 1


def cmd_alias_repair(args):
    w = writer(args)

    def build(state):
        return [w.event(state, "alias_reassigned", {"from": old, "to": new}, subject=ref, recorded_by=SYSTEM_ACTOR)
                for ref, old, new in _repairs(state)]

    return emit(w.transact(build))


def cmd_run_renumber(args):
    """Reassign Run aliases in creation order within each Experiment, preserving all IDs."""
    w = writer(args)

    def build(state):
        changes = []
        runs = sorted(state.of_kind("run"), key=lambda run: run["_created_event"])
        counts = defaultdict(int)
        for run in runs:
            experiment = run["experiment"]
            counts[experiment] += 1
            target = state.run_alias(experiment, counts[experiment])
            if run["alias"] != target:
                changes.append((run, target))
        events = []
        # Free occupied targets first, including permutations of existing scoped aliases.
        temporary = {}
        targets = {target for _, target in changes}
        for run, _ in changes:
            if run["alias"] in targets:
                experiment = run["experiment"]
                number = max(counts[experiment], int(state.next_run_alias(experiment).split("-R")[1]))
                number += len(temporary) + 1
                temp = state.run_alias(experiment, number)
                temporary[run["id"]] = temp
                events.append(w.event(state, "alias_reassigned", {"from": run["alias"], "to": temp},
                                      subject=run["id"], recorded_by=SYSTEM_ACTOR))
        for run, target in changes:
            old = temporary.get(run["id"], run["alias"])
            events.append(w.event(state, "alias_reassigned", {"from": old, "to": target},
                                  subject=run["id"], recorded_by=SYSTEM_ACTOR))
        return events

    return emit(w.transact(build))


def cmd_view(args):
    """HTML (or --json) for one experiment's view, or the master view of every non-legacy view (R44)."""
    _, state = _state(args)
    if args.ref:
        experiment_id = state.resolve(args.ref, "experiment")
        if state.records[experiment_id]["view"] is None:
            raise ResearchError(f"{args.ref} has no view")
        views = [experiment_view(state, experiment_id)]
        html = render.page(views[0]["title"], [render.experiment_section(state, views[0])])
    else:
        views = [experiment_view(state, ref) for ref in view_experiments(state)]
        html = render.master_page(state, views)
    if args.json:
        return emit(views)
    if args.out:
        Path(args.out).write_text(html, encoding="utf-8")
        return emit({"written": args.out})
    print(html)
    return 0


def cmd_validate(args):
    report = validate(root_of(args), store.now_ts())
    emit(report)
    return 1 if report["errors"] else 0


def register(sub, common):
    p = sub.add_parser("define-view", parents=[common], help="define a first view for an existing scout")
    p.add_argument("ref")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_define_view)
    p = sub.add_parser("run-position", parents=[common], help="assign a first slot to an existing scout Run")
    p.add_argument("ref")
    p.add_argument("--slot", action="append", required=True)
    p.add_argument("--supersedes")
    p.set_defaults(func=cmd_position)

    for name, func, help_text in (
        ("show", cmd_show, "projection of one record"),
        ("evidence", cmd_evidence, "evidence tree down to runs and HF evidence"),
    ):
        p = sub.add_parser(name, parents=[common], help=help_text)
        p.add_argument("ref")
        p.set_defaults(func=func)
    p = sub.add_parser("list", parents=[common])
    p.add_argument("type", choices=KINDS)
    p.add_argument("--where", action="append", help="KEY=VALUE filter (repeatable)")
    p.set_defaults(func=cmd_list)
    p = sub.add_parser("context", parents=[common], help="minimum sufficient context (R33)")
    p.add_argument("ref")
    p.set_defaults(func=cmd_context)
    sub.add_parser("pending-authority", parents=[common], help="open authority requests").set_defaults(func=cmd_pending)
    _register_handoff(sub, common)
    sub.add_parser("alias-repair", parents=[common]).set_defaults(func=cmd_alias_repair)
    sub.add_parser("run-renumber", parents=[common], help="number Runs by creation order within each Experiment").set_defaults(func=cmd_run_renumber)
    sub.add_parser("validate", parents=[common]).set_defaults(func=cmd_validate)
    p = sub.add_parser("view", parents=[common], help="experiment table (or all of them) as HTML; never commit it")
    p.add_argument("ref", nargs="?", help="experiment; omit for the master view")
    p.add_argument("--out", help="write HTML here instead of stdout")
    p.add_argument("--json", action="store_true", help="print the computed view model instead of HTML")
    p.set_defaults(func=cmd_view)


def _register_handoff(sub, common):
    hand = sub.add_parser("handoff").add_subparsers(dest="action", required=True)
    p = hand.add_parser("save", parents=[common])
    p.add_argument("--file", required=True, help="JSON without id/alias/created_by/created_at")
    p.set_defaults(func=cmd_handoff_save)
    p = hand.add_parser("load", parents=[common])
    p.add_argument("--scope", help="Hypothesis/Experiment ref")
    p.set_defaults(func=cmd_handoff_load)
    p = hand.add_parser("clear", parents=[common])
    p.add_argument("alias", nargs="?")
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_handoff_clear)
