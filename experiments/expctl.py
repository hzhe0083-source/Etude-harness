#!/usr/bin/env python3
"""Experiment-config helper for M0..M3 (docs/design/vla_harness_v1.md §3.2, §5, §6; Research OS views).

This tool never writes research/ledger.json and never fills --human/--quote/--channel.

    get M0 sweep.exec_steps_N            print a config value (lists space-joined) for shell scripts
    check-views                          validate every experiments/M*/view*.json: research/SCHEMA.json
                                         $defs/ViewSpec, Research OS R44 rules, and agreement with config.yaml
    split-status --task T --seed S       print ok|short|missing from experiments/splits.json (exit 0 iff ok)
    run-argv --milestone M0 [--aa|--t] [--stage AUX] --slot k=v ... [--param k=v ...] [--write-params]
             -- ENTRY ARGS...            print, one token per line, the `research run ...` argv that
                                         etude.evolution.research_bridge.run_command builds for one Run
                                         (used by scripts/m0_sweep.sh and scripts/aa_rerun.sh, RESEARCH_RUN=1)
    bootstrap                            print the Research OS command sequence for the user (from
                                         research_bridge) plus display-label `experiment style` commands

Usage: ``uv run python experiments/expctl.py <subcommand> ...``
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
EXP_DIR = REPO_ROOT / "experiments"
RESEARCH_TOOLS = REPO_ROOT / "research" / "tools"
SCHEMA_PATH = REPO_ROOT / "research" / "SCHEMA.json"
SPLITS_PATH = EXP_DIR / "splits.json"
MILESTONES = ("M0", "M1", "M2", "M3")

# Research OS Experiment per (milestone, variant): "main" table, "aa" A/A replication, "t" final T.
# The config prefix matches research_bridge.Unit.prefix ("", "aa_", "t_").
_PREFIX = {"main": "", "aa": "aa_", "t": "t_"}
_UNIT_SUFFIX = {"main": "", "aa": "-AA", "t": "-T"}


# ---------------------------------------------------------------- config access

def load_config(milestone: str) -> dict:
    """Load experiments/<M>/config.yaml (design §5 numbers)."""
    if milestone not in MILESTONES:
        raise SystemExit(f"unknown milestone {milestone!r}; expected one of {MILESTONES}")
    return yaml.safe_load((EXP_DIR / milestone / "config.yaml").read_text(encoding="utf-8"))


def deep_get(d, dotted: str, default=None):
    """Return d[a][b][c] for "a.b.c", or default (design §5 config lookup)."""
    cur = d
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _fmt(value) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def cmd_get(args) -> int:
    """Print one config value for shell use (design §5)."""
    value = deep_get(load_config(args.milestone), args.key)
    if value is None:
        print(f"[expctl] {args.milestone}: no key {args.key}", file=sys.stderr)
        return 1
    print(_fmt(value))
    return 0


def _bridge():
    """etude.evolution.research_bridge, or None if that component is unavailable."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from etude.evolution import research_bridge
        return research_bridge
    except Exception as exc:  # pragma: no cover - other component missing/broken
        print(f"[expctl] research_bridge unavailable: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------- views

def _view_paths() -> list[Path]:
    return sorted(EXP_DIR.glob("M*/view*.json"))


def _schema_errors(spec: dict) -> list[str]:
    import jsonschema
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    wrapper = {"$schema": schema.get("$schema"), "$defs": schema["$defs"], "$ref": "#/$defs/ViewSpec"}
    return [e.message for e in jsonschema.Draft202012Validator(wrapper).iter_errors(spec)]


def _r44_violations(spec: dict) -> list[str]:
    sys.path.insert(0, str(RESEARCH_TOOLS))
    try:
        from research_os.views import view_spec_violations  # vendored Research OS
    except Exception as exc:  # pragma: no cover
        return [f"cannot import research_os.views: {exc}"]
    return list(view_spec_violations(spec))


def _config_cross_check(path: Path, spec: dict) -> list[str]:
    """Axis values must agree with config.yaml, where the design §5 numbers live."""
    m, name = path.parent.name, path.name
    cfg = load_config(m)
    errs: list[str] = []
    rows, cols = spec["rows"]["values"], spec["cols"]["values"]
    reps = deep_get(spec, "cell.replicates.values", [])

    def expect(label, got, want):
        if [str(x) for x in got] != [str(x) for x in want]:
            errs.append(f"{m}/{name}: {label} {got} != config {want}")

    if name == "view_aa.json":
        expect("rows", rows, cfg["aa"]["tasks"])
        expect("cols", cols, [cfg["aa"]["incumbent"]])
        expect("replicates", reps, list(range(1, cfg["aa"]["reps"] + 1)))
    elif m == "M0":
        expect("rows", rows, cfg["tasks"]["dev"] + cfg["tasks"]["heldout"])
        expect("cols", cols, cfg["sweep"]["exec_steps_N"])
        expect("replicates", reps, cfg["seeds"])
    elif m == "M1":
        expect("rows", rows, cfg["tasks"]["dev"] + cfg["tasks"]["heldout"])
        expect("cols", cols, cfg["conditions"]["values"])
        expect("replicates", reps, cfg["seeds"])
    elif m == "M2":
        expect("rows", rows, cfg["tasks"]["dev"])
        expect("cols", cols, cfg["conditions"]["values"])
        expect("replicates", reps, sorted({s for c in cfg["conditions"]["values"]
                                           for s in cfg["conditions"][c]["seeds"]}))
    elif m == "M3" and name == "view.json":
        expect("rows", rows, cfg["surfaces"]["order"])
        expect("cols", cols, cfg["candidates"]["values"])
        expect("replicates", reps, sorted(cfg["gates"]["seed_map"]))
    elif m == "M3" and name == "view_T.json":
        t = cfg["final_T"]
        expect("rows", rows, t["slices"]["dev"]["tasks"] + t["slices"]["heldout"]["tasks"])
        expect("cols", cols, t["conditions"])
        expect("replicates", reps, sorted({s for sl in t["slices"].values() for s in sl["seeds"]}))
    else:
        errs.append(f"{m}/{name}: no config cross-check defined")
    rel = str(path.relative_to(REPO_ROOT))
    if rel not in {deep_get(cfg, f"research.{p}view") for p in _PREFIX.values()}:
        errs.append(f"{m}/{name}: not referenced by research.view/aa_view/t_view in config.yaml")
    return errs


def check_views() -> list[str]:
    """Validate all view files: SCHEMA ViewSpec, R44 axis rules, research_bridge, config agreement."""
    bridge = _bridge()
    problems: list[str] = []
    paths = _view_paths()
    if not paths:
        return ["no experiments/M*/view*.json found"]
    for path in paths:
        rel = path.relative_to(REPO_ROOT)
        spec = json.loads(path.read_text(encoding="utf-8"))
        errs = [f"{rel}: schema: {e}" for e in _schema_errors(spec)]
        if not errs:
            errs += [f"{rel}: {e}" for e in _r44_violations(spec)]
            errs += _config_cross_check(path, spec)
            if bridge is not None:
                errs += [f"{rel}: research_bridge: {e}" for e in bridge.validate_view(spec)]
        problems += errs
        print(f"[expctl] {rel}: {'OK' if not errs else 'FAIL'}  rows={spec['rows']['key']} "
              f"cols={spec['cols']['key']} reps={deep_get(spec, 'cell.replicates.key')} "
              f"match={spec['cell']['match']}")
    return problems


def cmd_check_views(args) -> int:
    """CLI: validate all view files; exit 1 on any problem (Research OS ViewSpec/R44; design §5)."""
    problems = check_views()
    for p in problems:
        print("  " + p, file=sys.stderr)
    return 1 if problems else 0


# ---------------------------------------------------------------- split status

def split_status(task: str, seed: int, lo: int = 0, hi: int = 50) -> str:
    """'ok' | 'short' | 'missing' | 'no-splits' for task layouts [lo,hi) at seed (design §3.2).

    Reads experiments/splits.json (built from file names by scripts/make_split.py).
    """
    if not SPLITS_PATH.is_file():
        return "no-splits"
    doc = json.loads(SPLITS_PATH.read_text(encoding="utf-8"))
    for sp in doc["splits"].values():
        if sp.get("range") != [lo, hi]:
            continue
        seeds = [sp["seed"]] if "seed" in sp else sorted({s for v in sp.get("seeds", {}).values() for s in v})
        for key, v in sp["tasks"].items():
            name, _, at = key.partition("@")
            if name == task and (int(at) == seed if at else seed in seeds):
                return v["status"]
    return "missing"


def cmd_split_status(args) -> int:
    """CLI: print a task/seed layout status from experiments/splits.json (design §3.2)."""
    status = split_status(args.task, args.seed, args.lo, args.hi)
    print(status)
    return 0 if status == "ok" else 1


# ---------------------------------------------------------------- research run argv

def _pairs(items: list[str] | None, flag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"{flag} expects KEY=VALUE, got {item!r}")
        k, v = item.split("=", 1)
        out[k] = v
    return out


def _scalar(v: str):
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return {"true": True, "false": False, "null": None}.get(v, v)


def research_run(milestone: str, variant: str, slot: dict[str, str], params: dict[str, str],
                 command: list[str], *, stage: str | None = None, experiment: str | None = None,
                 checkpoint: str | None = None, write_params: bool = False) -> list[str]:
    """argv of one `research run` for an evaluation Run (Research OS R20–R24, R40–R44; design §5).

    Delegates to etude.evolution.research_bridge.run_command, which reads our view files,
    checks the slot (R44), requires a `model=` checkpoint whose repo is also the
    --archive-to target, and puts --parameters in a git-ignored JSON file under runs/params/.

    - Experiment alias: `experiment`, else $RESEARCH_EXPERIMENT, else config
      research.<prefix>experiment, else the bridge's default alias for this unit (valid only
      if the ledger was bootstrapped in the bridge's order; a warning is printed).
    - Checkpoint: `checkpoint`, else $RESEARCH_CHECKPOINT (your HF model mirror of the official
      ckpt, `model=OWNER/NAME@REV40:path`), else the bridge placeholder (dry runs only).
    - command[0] must be `bash` or `python` followed by a Git-tracked entry script.
    """
    bridge = _bridge()
    if bridge is None:
        raise SystemExit("[expctl] etude.evolution.research_bridge is required to build research run commands")
    unit_key = f"E-{milestone}{_UNIT_SUFFIX[variant]}"
    cfg = load_config(milestone)
    alias = (experiment or os.environ.get("RESEARCH_EXPERIMENT")
             or deep_get(cfg, f"research.{_PREFIX[variant]}experiment"))
    if not alias:
        alias = bridge.DEFAULT_ALIASES[unit_key]
        print(f"[expctl] warning: no experiment alias for {unit_key}; using bridge default {alias} "
              f"(set RESEARCH_EXPERIMENT or research.{_PREFIX[variant]}experiment)", file=sys.stderr)
    ckpt = checkpoint or os.environ.get("RESEARCH_CHECKPOINT") or bridge.CHECKPOINT_TEMPLATE
    if len(command) < 2 or command[0] not in ("bash", "python", "python3"):
        raise SystemExit("[expctl] command must be: bash|python <git-tracked entry> [args...]")
    entry, entry_args = command[1], command[2:]
    full_slot = dict(slot)
    if stage:
        full_slot["stage"] = stage
    plan = bridge.run_command(alias, "evaluation", slot=full_slot, entrypoint=entry, args=entry_args,
                              checkpoint=ckpt, parameters={k: _scalar(v) for k, v in params.items()},
                              aliases={unit_key: alias})
    argv = list(plan.command.argv)
    if write_params:
        holes = [t for t in argv if "<" in t]
        if holes:
            raise ValueError("placeholders left in the command (set RESEARCH_CHECKPOINT / RESEARCH_EXPERIMENT): "
                             + " ".join(holes))
        plan.write_parameters()
    return argv


def cmd_run_argv(args) -> int:
    """CLI: print one `research run` argv, one token per line (Research OS R20–R24; design §5)."""
    variant = "aa" if args.aa else "t" if args.t else "main"
    cmd = list(args.command)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    try:
        argv = research_run(args.milestone, variant, _pairs(args.slot, "--slot"), _pairs(args.param, "--param"),
                            cmd, stage=args.stage, experiment=args.experiment, checkpoint=args.checkpoint,
                            write_params=args.write_params)
    except (ValueError, FileNotFoundError) as exc:
        print(f"[expctl] {exc}", file=sys.stderr)
        return 1
    print("\n".join(argv))
    return 0


# ---------------------------------------------------------------- bootstrap templates

_STYLE = {
    "M0/view.json": ("M0 π0.5 每块执行步数 N 扫描", {"5": "N=5", "10": "N=10", "15": "N=15", "25": "N=25", "50": "N=50"}),
    "M0/view_aa.json": ("M0 A/A（π0.5@N* 在 V 上 ×3）", {"pi05_nstar": "π0.5 @ N*"}),
    "M1/view.json": ("M1 完整 harness（记忆关）", {"h1": "H1", "h1_nstar": "H1，VLA exec_steps=N*"}),
    "M1/view_aa.json": ("M1 A/A（H1 在 V 上 ×3）", {"h1": "H1"}),
    "M2/view.json": ("M2 记忆回路", {"h2": "H2 记忆", "shuffled": "打乱记忆（换任务）"}),
    "M3/view.json": ("M3 harness 回路逐面进化", {"incumbent": "现任", "r1": "第 1 轮", "r2": "第 2 轮",
                                            "r3": "第 3 轮", "r4": "第 4 轮"}),
    "M3/view_T.json": ("M3 最终 T（只看一次）", {"h2_frozen": "冻结 H2", "h_star": "H*"}),
}


def bootstrap_text() -> str:
    """Research OS command sequence for the user (research/README_SOURCE.md; AGENTS.md 授权)."""
    q = shlex.quote
    out = ["# Research OS 初始化（由用户本人执行；--human/--quote/--channel 必须是你的原话，代理不得代填）",
           "uv run python experiments/expctl.py check-views", ""]
    bridge = _bridge()
    if bridge is not None:
        for c in bridge.bootstrap_commands():
            if c.note:
                out.append(f"# [{c.who}] {c.note}")
            out.append(c.shell())
        out.append("")
    else:
        out.append("# research_bridge 不可用：命令模板见 research/README_SOURCE.md")
    out.append("# 显示标签（不需要授权）：E-<n> 换成实际别名，并写进 experiments/<M>/config.yaml research.*experiment")
    for rel, (title, labels) in _STYLE.items():
        parts = [f"RESEARCH_ACTOR=agent:<id> uv run python research/tools/research experiment style E-<n> --title {q(title)}"]
        parts += [f"  --col-label {q(k + '=' + v)}" for k, v in labels.items()]
        out.append(f"# experiments/{rel}")
        out.append(" \\\n".join(parts))
    out += ["", "# 跑 Run（源码须已 commit 且已 push origin）",
            "RESEARCH_RUN=1 RESEARCH_CHECKPOINT=model=<OWNER>/<NAME>@<REV40>:ckpt/RoboDojo/Pi_05/RoboDojo-sim-arx_x5-joint-0/59999 \\",
            "  RESEARCH_EXPERIMENT=E-<n> bash scripts/m0_sweep.sh"]
    return "\n".join(out)


def cmd_bootstrap(args) -> int:
    """CLI: print the Research OS bootstrap commands for the user (research/AGENTS.md 授权; design §5)."""
    print(bootstrap_text())
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry (design §5 experiments; Research OS command templates)."""
    ap = argparse.ArgumentParser(description="experiments/ helper (design §5, Research OS)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("get")
    p.add_argument("milestone")
    p.add_argument("key")
    p.set_defaults(func=cmd_get)
    sub.add_parser("check-views").set_defaults(func=cmd_check_views)
    p = sub.add_parser("split-status", help="exit 0 iff layouts exist for TASK at SEED (experiments/splits.json)")
    p.add_argument("--task", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--lo", type=int, default=0)
    p.add_argument("--hi", type=int, default=50)
    p.set_defaults(func=cmd_split_status)
    p = sub.add_parser("run-argv")
    p.add_argument("--milestone", required=True, choices=MILESTONES)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--aa", action="store_true", help="the A/A replication experiment")
    g.add_argument("--t", action="store_true", help="the final T experiment (M3)")
    p.add_argument("--stage", help="auxiliary slot family (e.g. verifier_audit, L1, incumbent_v)")
    p.add_argument("--slot", action="append")
    p.add_argument("--param", action="append")
    p.add_argument("--experiment")
    p.add_argument("--checkpoint")
    p.add_argument("--write-params", action="store_true", help="write the runs/params/*.json file the CLI reads")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_run_argv)
    sub.add_parser("bootstrap").set_defaults(func=cmd_bootstrap)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
