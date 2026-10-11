"""Argument helpers shared by the CLI command modules."""

import argparse
import json
import os
import sys

from . import store
from .authority import find_authority
from .errors import ResearchError
from .state import VIEW_AXES
from .writer import Writer

ROLES = ("proposer", "executor", "interpreter", "verifier")


def common_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", help="research root (dir containing ledger.json); default $RESEARCH_OS_ROOT or nearest research/")
    parser.add_argument("--actor", default=os.environ.get("RESEARCH_ACTOR"),
                        help="agent:<id> writing the event (default $RESEARCH_ACTOR); required for writes")
    parser.add_argument("--role", choices=ROLES, help="role metadata for the event (R32)")
    return parser


def human_parser():
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_argument_group("human act (R7)")
    group.add_argument("--human", required=True, help="id of the human who said it")
    group.add_argument("--quote", required=True, help="the human's verbatim words")
    group.add_argument("--channel", required=True, help="where the human said it")
    return parser


def view_addition_args(parser):
    for axis in VIEW_AXES:
        parser.add_argument(f"--{axis}", help=f"values to append to the view's {axis}, comma-separated")


def view_addition(args):
    """{axis: [values]} from --rows/--cols/--replicates; empty when none is given."""
    return {axis: split(getattr(args, axis)) for axis in VIEW_AXES if getattr(args, axis)}


def _requested_flags(requested):
    flags = f" --to {requested['to']}" if "to" in requested else ""
    return flags + "".join(f" --{axis} {','.join(values)}" for axis, values in requested.get("add", {}).items())


def authority_args(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--grant", help="event_id of an authority_granted event")
    group.add_argument("--policy", help="Policy alias or id")


def agent(args):
    actor = args.actor or ""
    if not actor.startswith("agent:") or len(actor) <= len("agent:"):
        raise ResearchError("writes need --actor agent:<id> (or $RESEARCH_ACTOR)")
    return {"type": "agent", "id": actor[len("agent:"):]}


def root_of(args):
    return store.find_root(args.root)


def writer(args, default_role=None):
    return Writer(root_of(args), agent(args), args.role or default_role)


def human_actor(args):
    return {"type": "human", "id": args.human}


def approval(args):
    return {"channel": args.channel, "quote": args.quote, "at": store.now_ts()}


def provenanced(value, source):
    return {"value": value, "source": source}


def emit(data, stream=None):
    print(json.dumps(data, indent=2, ensure_ascii=False), file=stream or sys.stdout)
    return 0


def split(text):
    return [item.strip() for item in (text or "").split(",") if item.strip()]


def resolve_list(state, text, kind=None):
    return [state.resolve(token, kind) for token in split(text)]


def parse_scope(items):
    """['k=v1,v2', ...] -> {k: [v1, v2]}."""
    scope = {}
    for item in items or []:
        key, sep, values = item.partition("=")
        if not sep or not key.strip() or not split(values):
            raise ResearchError(f"scope entry {item!r} must look like KEY=V1,V2")
        scope.setdefault(key.strip(), []).extend(split(values))
    return scope


def parse_pairs(items, what):
    pairs = []
    for item in items or []:
        name, sep, value = item.partition("=")
        if not sep or not name or not value:
            raise ResearchError(f"{what} {item!r} must look like NAME=VALUE")
        pairs.append((name, value))
    return pairs


def authority_for(state, args, scope, subject, requested, required):
    """--grant / --policy as given; else auto-resolve when required (grant, then policy); else mode none."""
    if args.grant:
        return {"mode": "explicit", "grant": args.grant}
    if args.policy:
        return {"mode": "policy", "policy": state.resolve(args.policy, "policy")}
    if not required:
        return {"mode": "none"}
    found = find_authority(state, scope, subject, requested, store.now_ts())
    if found:
        return found
    label = state.label(subject)
    raise ResearchError(
        f"R10: no authority covers {scope} {requested} on {label}. Run `research authority request {label} "
        f"--scope {scope}{_requested_flags(requested)} --diff ... --on-approve ... --would-change ...` "
        f"and continue with independent work.")
