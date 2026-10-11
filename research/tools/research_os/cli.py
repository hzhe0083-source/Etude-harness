"""`research` CLI entry point. Exact syntax: `research --help` (AGENTS.md)."""

import argparse
import sys

from . import cmd_evidence, cmd_records, cmd_runs, cmd_views, migrate
from .common import common_parser, human_parser
from .errors import ResearchError, RuleViolation


def build_parser():
    parser = argparse.ArgumentParser(
        prog="research", description="Research OS v1: compact ledger and HF execution records (see research/AGENTS.md).")
    sub = parser.add_subparsers(dest="command_name", required=True)
    common, human = common_parser(), human_parser()
    cmd_records.register(sub, common, human)
    cmd_runs.register(sub, common)
    cmd_evidence.register(sub, common)
    cmd_views.register(sub, common)
    migrate.register(sub, common)
    return parser


def _split_run_command(argv):
    """`research run [opts] -- CMD ARGS...`: everything after the first `--` is the wrapped command."""
    if argv[:1] != ["run"] or "--" not in argv:
        return argv, []
    index = argv.index("--")
    return argv[:index], argv[index + 1:]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    argv, command = _split_run_command(argv)
    args = build_parser().parse_args(argv)
    if args.command_name == "run":
        args.command = command
    try:
        return args.func(args)
    except RuleViolation as exc:
        for violation in exc.violations:
            print(f"research: refused: {violation}", file=sys.stderr)
        return 2
    except ResearchError as exc:
        print(f"research: {exc}", file=sys.stderr)
        return 2
