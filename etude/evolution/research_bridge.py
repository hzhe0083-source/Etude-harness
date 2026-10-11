"""Research OS bridge: the ledger records milestones M0–M3 and evolution rounds (design §5, §6, §3.3, §4).

This module generates text only. It produces `research` CLI command strings and the
`$RESEARCH_METRICS` / `$RESEARCH_OUTPUTS` files that entry scripts write. It never
writes `research/ledger.json` and never fills in a human authorisation
(`--human/--quote/--channel`). Those fields stay as placeholders that the user fills
in and runs (research/AGENTS.md "科学记录与授权").

The experiment structure comes from the experiments component's files:
`experiments/<M>/config.yaml` (`research:` block) and `experiments/<M>/view*.json`.
There are seven Experiments: the four milestones E-M0..E-M3, plus the A/A companions
E-M0-AA and E-M1-AA and the final T experiment E-M3-T. Each Research OS contract holds
exactly one view. Decision rules are always built here from verbatim design §3/§5/§6
text.

The vendored Research OS lives in `research/tools/research_os` (copied from a2r). The
bridge reuses its rules instead of re-implementing them:
- `capture.git_capture`: the work tree must be clean and HEAD must be pushed to origin.
- `views.view_spec_violations` / `slot_outside_view`: the R44 rules for views and slots.
- `metrics.core_metrics`: the evaluation metric whitelist.
- `hf.HF_RE`: pinned HF references.
- `cli.build_parser`: syntax-checks every generated command.

Run `uv run python -m etude.evolution.research_bridge --demo` to print the M0 commands
and an example metrics file, then the blocking reasons for this repo.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from etude import REPO_ROOT
from etude.core.types import DEV_TASKS, HELDOUT_TASKS, Receipt

# ---------------------------------------------------------------------------- constants

MILESTONES = ("M0", "M1", "M2", "M3")
ALL_TASKS = tuple(DEV_TASKS) + tuple(HELDOUT_TASKS)
CLI = ("uv", "run", "python", "research/tools/research")
DESIGN_DOC = REPO_ROOT / "docs" / "design" / "vla_harness_v1.md"
SCHEMA_JSON = REPO_ROOT / "research" / "SCHEMA.json"

# Placeholders the user fills in. Agents never substitute them (research/AGENTS.md).
HUMAN_ID = "<HUMAN_ID>"
HUMAN_QUOTE = "<人类原话，逐字>"
HUMAN_CHANNEL = "<CHANNEL>"
AGENT_ACTOR = "agent:<id>"

# The official π0.5 checkpoint (design §1, F3). The pin comes from
# ~/models/robodojo-ckpt/.cache/huggingface. The checkpoint sits in an HF *dataset*
# repo, but Research OS evaluation Runs require a `model=` checkpoint and an
# --archive-to that names the same repo (rules._plan_violations). Run commands
# therefore point at a user-owned model mirror of this exact path and revision. See
# docs/research_os_usage.md §4.
OFFICIAL_CKPT = {
    "type": "dataset",
    "repo": "RoboDojo-Benchmark/RoboDojo",
    "revision": "35efbc7dedfdbeeb6e95fb749bd885d73d483e41",
    "path": "ckpt/RoboDojo/Pi_05/RoboDojo-sim-arx_x5-joint-0/59999",
}
CKPT_MIRROR_PLACEHOLDER = "<OWNER>/robodojo-pi05-arx_x5-joint-0"
CKPT_REV_PLACEHOLDER = "<REV40>"
CHECKPOINT_TEMPLATE = f"model={CKPT_MIRROR_PLACEHOLDER}@{CKPT_REV_PLACEHOLDER}:{OFFICIAL_CKPT['path']}"
FORBIDDEN_COMMAND_TOKENS = ("-c", "-lc", "-e")  # capture.frozen_checkout refuses inline scripts

# ---------------------------------------------------------------------------- verbatim design text

# Every string below is copied character-for-character from docs/design/vla_harness_v1.md.
# verify_rules_against_design() re-checks that.
SEC5_PASS = {
    "M0": "跑完、t_step 与 A/A 的 δ 记下；FPR ≤5%（否则验证器先修）",
    "M1": "配对 SR1 − SR0(N*) ≥ ε 且 p≤0.05（开发任务 seed 0/1 合并 200 回合）；FNR 不升",
    "M2": "SR2(seed1) − SR1(seed1) ≥ ε；打乱记忆 ≤ SR1 + δ",
    "M3": "每面至少 1 次 accept；T 上 H* − H2 ≥ ε（seed 2 + 留出）",
}
SEC5_EXPERIMENT = {
    "M0": "开发任务×N∈{5,10,15,25,50}×seed 0/1×50（批量 eval_batch:true，num_envs 4）；留出任务只跑最佳 N；A/A 3 次；验证器对官方 judge 的 FPR/FNR",
    "M1": "开发+留出任务，seed 0/1 各 50；记录调用/token/墙钟；A/A 3 次",
    "M2": "E 上积累 → seed 0/1 评；对照 = 打乱记忆（换任务或打乱节点顺序）",
    "M3": "三个面各 R≤4 轮；最后 T 一次：H* 对冻结 H2",
}
SEC5_M3_BLOCKER = "A/A 给出的 ε 不能大于 15，否则 V 扩到 150"
SEC31_GATE = ("L0 静态 → L1 medoid 重跑（≤5 回合，Zetta 2608.16590 §2.5.2）→ L2 历史回归集 F（≤20 回合，≤1 次失守）"
              "→ L3 配对留出（V=100 回合，b−c≥ε 且 McNemar p≤0.05）→ 成本 ΔC≤β0+β1·ΔSR（RRSI §3.3）")
SEC32_AA = "现任 harness 在 V 上重跑 3 次"
SEC33_EPS = "eps = max(4, max(abs(a - b) for a, b in pairs(S)) + 1)"
SEC33_EPS_NOTE = "COAP 2610.12369 App. B 的 4 是下限"
# §6 rows: (condition, statistic, margin, consequence)
SEC6_ROWS = {
    "cond2": ("条件 2 不超过最好的 N", "同布局配对 McNemar，开发任务 seed 0/1（200 回合）",
              "b−c < ε 或 p>0.05；或漏检率上升 >2 pp",
              "如实写成\"带验证器的定时重查器\"；M2 仍做（记忆对重查器也可测），M3 只开恢复规则一个面"),
    "cond3": ("条件 3 在留出上缩水", "开发任务 seed 2：SR2 − SR1", "< −δ_AA", "序列泄漏布局信息，回查 schema"),
    "evolve": ("进化后 seed 2 不超过冻结 harness", "T 上 H* − H2 配对", "< ε", "记 M3 证伪，保留 H2"),
    "budget": ("同回合同 VLM 预算 M3 ≤ M2", "M2+ = M2 用同等额外回合扩记忆；比较 H* 与 M2+", "H* − M2+ < ε", "进化不值"),
    "recheck": ("收益全来自缩短重查步长",
                "M1 内 VLA 节点 `exec_steps` 固定为 N*；对照 M0(N*)；Long-WAM 2610.10528 §6：原子任务 15 步重查 84.4 ≈ 层级 85.6，复合任务 11.2/5.0 对 38.8/35.0",
                "若 put_bottles/make_kong 上 M1 − M0(N*) < ε 而 stack_bowls 上 ≥ε", "结论限定为\"短程任务收益=重查\""),
    "stop": ("停止规则", "连续 3 轮 reject；或 V 已被 6 个候选复用；或预算用尽；或 ε>15", "—", "冻结现任，进入 T"),
}

# H-1/H-2/H-3 = conditions 2/3/4. The design has no literal hypothesis sentence; these are
# composed from §1, §5 and §6.
HYPOTHESES: dict[str, dict[str, Any]] = {
    "H-cond2": {
        "condition": 2,
        "statement": ("条件 2：冻结 π0.5 加节点执行器、代码验证器与节点边界规划（harness H1，记忆关），"
                      "在开发任务 seed 0/1 同布局配对上，成功率比 π0.5 每 N 步重查的最佳 N（M0 的 N*）高至少 ε 且 McNemar p≤0.05，漏检率不升"),
        "scope": {"condition": ["2"], "task": list(ALL_TASKS)},
    },
    "H-cond3": {
        "condition": 3,
        "statement": ("条件 3：在 H1 上加只读节点序列记忆（E 划分积累，入库 = 官方成功 ∧ 全节点 Pass，目标只存 perceive 查询），"
                      "seed 1 成功率比条件 2 高至少 ε，打乱记忆对照不超过 SR1 + δ，且在 seed 2 上不缩水超过 δ_AA"),
        "scope": {"condition": ["3"], "task": list(DEV_TASKS)},
    },
    "H-cond4": {
        "condition": 4,
        "statement": ("条件 4：harness 回路逐面进化（规划 prompt → 恢复规则 → SKILL.md，每候选过 L0–L3 门）"
                      "得到的 H*，在 T（开发任务 seed 2 + 留出任务 seed 0/1/2）上配对成功率比冻结 H2 高至少 ε，且不低于同预算扩记忆的 M2+ 减 ε"),
        "scope": {"condition": ["4"], "task": list(ALL_TASKS),
                  "surface": ["planner_prompt", "recovery_rules", "skill_md"]},
    },
}

# ---------------------------------------------------------------------------- experiment units


@dataclass(frozen=True)
class Unit:
    """One Research OS Experiment, which has exactly one view (design §5; research SCHEMA Contract).

    `prefix` selects the keys in config.yaml's `research:` block: "" gives intent/view/…,
    "aa_" gives aa_intent/aa_view/…, and "t_" gives t_intent/t_view/….
    `aux_stages` are slot families outside the view's cells. Each entry is (name, keys,
    note). The slot is {"stage": name} plus `keys`; with keys=None it uses the cell's
    keys. Because the stage differs from cell.match, `research cell` never shows these
    slots, yet their axis values must still lie inside the view (R44).
    """

    key: str             # logical alias, e.g. "E-M0-AA"
    milestone: str
    prefix: str
    hypothesis: str      # key in HYPOTHESES
    rule_parts: tuple[str, ...]
    default_view: str
    default_kind: str
    aux_stages: tuple[tuple[str, tuple[str, ...] | None, str], ...] = ()

    def config(self, root: Path | None = None) -> dict[str, Any]:
        """The `research:` block of experiments/<M>/config.yaml, or {} if it is absent (design §4 tree)."""
        path = Path(root or REPO_ROOT) / "experiments" / self.milestone / "config.yaml"
        if not path.is_file():
            return {}
        try:
            import yaml
            return dict((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("research") or {})
        except Exception:
            return {}

    def cfg(self, name: str, default: Any = None, root: Path | None = None) -> Any:
        val = self.config(root).get(self.prefix + name)
        return default if val in (None, "") else val


def _sec6(key: str) -> str:
    cond, stat, margin, consequence = SEC6_ROWS[key]
    return f"§6 证伪「{cond}」：统计量 {stat}；余量 {margin}；后果 {consequence}"


_AA_RULE = (f"§3.2 A/A：{SEC32_AA}", f"§3.3 ε 校准：{SEC33_EPS}（{SEC33_EPS_NOTE}）", f"§5 M3 卡点：{SEC5_M3_BLOCKER}")

UNITS: dict[str, Unit] = {u.key: u for u in (
    Unit("E-M0", "M0", "", "H-cond2", (f"§5 M0 预注册通过：{SEC5_PASS['M0']}",),
         "experiments/M0/view.json", "search",
         aux_stages=(("verifier_audit", None,
                      "验证器离线对官方 judge：successes=验证器 Pass 且官方失败，trials=官方失败 → success_rate 读作 FPR"),)),
    Unit("E-M0-AA", "M0", "aa_", "H-cond2", _AA_RULE, "experiments/M0/view_aa.json", "replication"),
    Unit("E-M1", "M1", "", "H-cond2", (f"§5 M1 预注册通过：{SEC5_PASS['M1']}", _sec6("cond2"), _sec6("recheck")),
         "experiments/M1/view.json", "main"),
    Unit("E-M1-AA", "M1", "aa_", "H-cond2", _AA_RULE, "experiments/M1/view_aa.json", "replication"),
    Unit("E-M2", "M2", "", "H-cond3", (f"§5 M2 预注册通过：{SEC5_PASS['M2']}", _sec6("cond3")),
         "experiments/M2/view.json", "main"),
    Unit("E-M3", "M3", "", "H-cond4",
         (f"§5 M3 预注册通过：{SEC5_PASS['M3']}", f"§3.1 门：{SEC31_GATE}", f"§5 M3 卡点：{SEC5_M3_BLOCKER}", _sec6("stop")),
         "experiments/M3/view.json", "search",
         # Cell seed=1 is L3 (V) and cell seed=0 is L2 (E). L1 (medoid) needs its own auxiliary slot.
         aux_stages=(("L1", ("surface", "candidate"), "L1 medoid 重跑（≤5 回合，E 划分 seed 0；seed 记在 parameters）"),)),
    Unit("E-M3-T", "M3", "t_", "H-cond4", (f"§5 M3 预注册通过：{SEC5_PASS['M3']}", _sec6("evolve"), _sec6("budget")),
         "experiments/M3/view_T.json", "main"),
)}
UNIT_ORDER = ("E-M0", "E-M0-AA", "E-M1", "E-M1-AA", "E-M2", "E-M3", "E-M3-T")

# Logical → CLI alias in a fresh ledger when scripts/research_bootstrap.sh creates them
# in UNIT_ORDER. H-1/H-2/H-3 = conditions 2/3/4. Pass `aliases=` to override.
DEFAULT_ALIASES: dict[str, str] = {
    "H-cond2": "H-1", "H-cond3": "H-2", "H-cond4": "H-3",
    **{k: f"E-{i}" for i, k in enumerate(UNIT_ORDER, start=1)},
}

_FALLBACK_INTENT = {
    "E-M0": "§5 M0 条件 1：" + SEC5_EXPERIMENT["M0"],
    "E-M0-AA": "M0 现任 π0.5@N* 在 V 上 A/A 3 次，得 δ_AA 与 ε",
    "E-M1": "§5 M1 条件 2：" + SEC5_EXPERIMENT["M1"],
    "E-M1-AA": "M1 现任 H1 在 V 上 A/A 3 次，得 δ_AA 与 ε",
    "E-M2": "§5 M2 条件 3：" + SEC5_EXPERIMENT["M2"],
    "E-M3": "§5 M3 条件 4：" + SEC5_EXPERIMENT["M3"],
    "E-M3-T": "最终一次 T：H* 对冻结 H2，开发任务 seed 2 + 留出任务 seed 0/1/2",
}
_FALLBACK_BASELINE = {
    "E-M0": "π0.5 joint-0/59999 官方流程（N=50）", "E-M0-AA": "π0.5@N* 自身（A/A）",
    "E-M1": "π0.5 每 N 步重查的最佳 N（E-M0 cell n=N*，同布局配对）", "E-M1-AA": "H1 自身（A/A）",
    "E-M2": "E-M1 cell condition=h1（同布局配对）", "E-M3": "冻结 H2（M2 现任）", "E-M3-T": "冻结 H2",
}


def unit(key: str) -> Unit:
    """Resolve "E-M0", "M0", "M0-AA", "e-m3-t" … to a Unit (design §5)."""
    k = key.upper()
    k = k if k.startswith("E-") else "E-" + k
    if k not in UNITS:
        raise ValueError(f"unknown experiment {key!r}; known {list(UNITS)}")
    return UNITS[k]


def decision_rule(key: str) -> str:
    """Pre-registered decision rule for one Experiment, built only from verbatim §3/§5/§6 text (design §5, §6)."""
    return "。".join(unit(key).rule_parts) + "。"


def verify_rules_against_design(doc: Path | None = None) -> list[str]:
    """Fragments of the rule text that no longer appear verbatim in the design doc (design §3, §5, §6).

    An empty list means every pre-registered rule is still a literal quote of the design.
    """
    path = Path(doc) if doc else DESIGN_DOC
    if not path.is_file():
        return [f"design doc not found: {path}"]
    text = path.read_text(encoding="utf-8")
    frags = [*SEC5_PASS.values(), *SEC5_EXPERIMENT.values(), SEC5_M3_BLOCKER, SEC31_GATE, SEC32_AA,
             SEC33_EPS, SEC33_EPS_NOTE]
    for row in SEC6_ROWS.values():
        frags += [cell for cell in row if cell != "—"]
    return [f"not verbatim in {path.name}: {f!r}" for f in frags if f not in text]


# ---------------------------------------------------------------------------- Research OS access


def _research_os():
    """Import the vendored research_os package (research/tools); None if it is unavailable."""
    tools = REPO_ROOT / "research" / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        import research_os
        from research_os import capture, cli, hf, metrics, views  # noqa: F401
        return research_os
    except Exception:  # only when research/ is missing or broken
        return None


# ---------------------------------------------------------------------------- commands


@dataclass
class Command:
    """One `research` CLI invocation to print or hand to the user (design §5).

    `who` is "agent" when an agent may run it, or "human" when it carries a human act
    (`--human/--quote/--channel`) that only the user may type. `blocked_on` lists the
    `<…>` placeholders still to fill.
    """

    argv: list[str]
    who: str = "agent"
    note: str = ""
    env: dict[str, str] = field(default_factory=lambda: {"RESEARCH_ACTOR": AGENT_ACTOR})

    @property
    def blocked_on(self) -> list[str]:
        return sorted({tok for a in self.argv for tok in re.findall(r"<[^<>\s]+>", a)})

    def research_args(self) -> list[str]:
        """argv after the `research` program name (what cli.main() receives)."""
        return self.argv[len(CLI):]

    def shell(self) -> str:
        env = " ".join(f"{k}={shlex.quote(v)}" for k, v in self.env.items())
        return (env + " " if env else "") + shlex.join(self.argv)

    def to_dict(self) -> dict[str, Any]:
        return {"who": self.who, "note": self.note, "shell": self.shell(), "blocked_on": self.blocked_on}


def _cmd(*args: str, who: str = "agent", note: str = "") -> Command:
    return Command(list(CLI) + list(args), who=who, note=note)


def syntax_check(commands: Iterable[Command]) -> list[str]:
    """argparse errors from the vendored CLI for each generated command. Nothing is executed (design §5)."""
    ros = _research_os()
    if ros is None:
        return ["research_os not importable; syntax not checked"]
    parser = ros.cli.build_parser()
    errors = []
    for c in commands:
        argv, _ = ros.cli._split_run_command(c.research_args())
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                parser.parse_args(argv)
        except SystemExit:
            last = (err.getvalue().strip().splitlines() or ["parse error"])[-1]
            errors.append(f"{' '.join(argv[:3])}…: {last}")
    return errors


def _alias(key: str, aliases: Mapping[str, str] | None) -> str:
    """CLI alias for a logical key: `aliases` override, then config `research.<prefix>experiment`, then DEFAULT_ALIASES."""
    if aliases and key in aliases:
        return aliases[key]
    u = None
    with contextlib.suppress(ValueError):
        u = unit(key)
    if u is not None:
        if aliases and u.key in aliases:
            return aliases[u.key]
        recorded = u.cfg("experiment", None)
        if recorded:
            return str(recorded)
        return DEFAULT_ALIASES[u.key]
    return DEFAULT_ALIASES.get(key, key)


def _unit_of_alias(experiment: str, aliases: Mapping[str, str] | None) -> Unit | None:
    with contextlib.suppress(ValueError):
        return unit(experiment)
    for k in UNITS:
        if _alias(k, aliases) == experiment:
            return UNITS[k]
    return None


# ---------------------------------------------------------------------------- views


def view_path(key: str, root: Path | None = None) -> str:
    """Repo-relative view file of an Experiment, taken from config.yaml (`view`/`aa_view`/`t_view`) or the default (design §4)."""
    u = unit(key)
    return str(u.cfg("view", u.default_view, root))


def load_view(key: str, root: Path | None = None) -> tuple[dict[str, Any] | None, Path]:
    """(view spec or None if the file is missing, path). The experiments component owns these files."""
    path = Path(root or REPO_ROOT) / view_path(key, root)
    if not path.is_file():
        return None, path
    return json.loads(path.read_text(encoding="utf-8")), path


def validate_view(spec: Mapping[str, Any]) -> list[str]:
    """Errors in a view: JSON Schema $defs/ViewSpec, the R44 rules, and the evaluation metric convention (design §5)."""
    errors: list[str] = []
    try:
        import jsonschema
        schema = json.loads(SCHEMA_JSON.read_text(encoding="utf-8"))
        sub = dict(schema["$defs"]["ViewSpec"], **{"$defs": schema["$defs"]})
        errors += [f"schema: {e.message}" for e in jsonschema.Draft202012Validator(sub).iter_errors(spec)]
    except Exception as exc:  # jsonschema missing or SCHEMA.json moved
        errors.append(f"schema not checked: {exc}")
    ros = _research_os()
    if ros is not None and not errors:
        errors += ros.views.view_spec_violations(spec)
    if spec.get("cell", {}).get("metric") != "success_rate":
        errors.append("cell.metric must be success_rate (evaluation core metric)")
    if "stage" in _axis_keys(spec):
        errors.append("axis key 'stage' is reserved for auxiliary slots (docs/research_os_usage.md §2)")
    return errors


def _axis_keys(spec: Mapping[str, Any]) -> list[str]:
    keys = [spec["rows"]["key"], spec["cols"]["key"]]
    rep = spec["cell"].get("replicates")
    return keys + ([rep["key"]] if rep else [])


def slot_for(key: str, *, stage: str | None = None, root: Path | None = None, **coords: Any) -> dict[str, str]:
    """Build a slot from an Experiment's view and check it (research/AGENTS.md R40–R44; design §5).

    A cell slot is `cell.match` plus one value for each axis key. An auxiliary slot is
    {"stage": name} plus that stage's keys (the cell keys when the keys are None). It
    never equals a cell slot. `N=` is accepted as `n=`. All values become strings.
    """
    u = unit(key)
    spec, path = load_view(u.key, root)
    coords = {("n" if k == "N" else k): str(v) for k, v in coords.items() if v is not None}
    if spec is None:
        # View file not written (yet): fall back to config.yaml `<prefix>slot_keys`; values are unchecked.
        cell_keys = set(u.cfg("slot_keys", [], root))
        if not cell_keys:
            raise FileNotFoundError(f"{path} missing and config has no {u.prefix}slot_keys")
        print(f"research_bridge: warning: {path.name} missing; slot checked against config "
              f"{u.prefix}slot_keys only", file=sys.stderr)
        match: dict[str, str] = {}
    else:
        match = {k: str(v) for k, v in spec["cell"]["match"].items()}
        cell_keys = set(match) | set(_axis_keys(spec))
    if stage is None or stage == match.get("stage"):
        slot = {**match, **coords}
        want = cell_keys
    else:
        aux = {name: keys for name, keys, _ in u.aux_stages}
        if stage not in aux:
            raise ValueError(f"{u.key}: unknown auxiliary stage {stage!r}; known {list(aux)}")
        keys = aux[stage]
        want = {"stage"} | (set(keys) if keys is not None else (cell_keys - {"stage"}))
        slot = {"stage": stage, **coords}
    if set(slot) != want:
        raise ValueError(f"{u.key}: slot keys {sorted(slot)} must be exactly {sorted(want)} ({path.name})")
    ros = _research_os()
    errs = ros.views.slot_outside_view(spec, slot) if (ros and spec is not None) else []
    if errs:
        raise ValueError("; ".join(errs))
    return slot


# ---------------------------------------------------------------------------- (a) hypotheses + experiments


def hypothesis_command(key: str) -> Command:
    """`research hypothesis create` for H-cond2/3/4, which become H-1/H-2/H-3 (design §5, §6)."""
    h = HYPOTHESES[key]
    scope = [a for k, vals in h["scope"].items() for a in ("--scope", f"{k}={','.join(vals)}")]
    return _cmd("hypothesis", "create", "--statement", h["statement"], *scope,
                note=f"{DEFAULT_ALIASES[key]} = 条件 {h['condition']}")


def _authority_grant(request: str, note: str) -> Command:
    return _cmd("authority", "grant", request, "--human", HUMAN_ID, "--quote", HUMAN_QUOTE,
                "--channel", HUMAN_CHANNEL, who="human", note=note)


def experiment_command(key: str, aliases: Mapping[str, str] | None = None, root: Path | None = None) -> list[Command]:
    """Commands that create one committed Experiment and request its contract authority (design §5, §6).

    The list holds `experiment create --mode committed --kind <kind> --intent … --decision-rule …
    --baseline … --metric success_rate --view <file>`, then `authority request
    --scope experiment_contract` (an agent may run both), then the human `authority grant`
    template with its fields left blank. Rules A1 forbid starting Runs before the grant.
    """
    u = unit(key)
    exp = _alias(u.key, aliases)
    view = view_path(u.key, root)
    intent = u.cfg("intent", _FALLBACK_INTENT[u.key], root)
    create = _cmd("experiment", "create", "--hypothesis", _alias(u.hypothesis, aliases), "--mode", "committed",
                  "--kind", str(u.cfg("kind", u.default_kind, root)), "--intent", intent,
                  "--decision-rule", decision_rule(u.key),
                  "--baseline", u.cfg("baseline", _FALLBACK_BASELINE[u.key], root),
                  "--metric", "success_rate", "--view", view,
                  note=f"{exp} = {u.key}（view {view}）")
    request = _cmd("authority", "request", exp, "--scope", "experiment_contract",
                   "--diff", f"新建 committed 契约 {u.key}：{intent[:80]}",
                   "--on-approve", f"{exp} 可以开始 research run",
                   "--would-change", "否：契约在任何 Run 之前登记",
                   note="代理可提交；记下返回的 event_id")
    grant = _authority_grant("<REQUEST_EVENT_ID>", note=f"仅用户：授权 {exp}（{u.key}）契约；event_id 用 research pending-authority 查")
    return [create, request, grant]


def experiment_commands(milestone: str, *, include_hypothesis: bool | None = None,
                        aliases: Mapping[str, str] | None = None, root: Path | None = None) -> list[Command]:
    """All Experiments of one milestone (main + A/A or T companion) with their hypothesis (design §5).

    For example, M0 gives E-M0 and E-M0-AA, and M3 gives E-M3 and E-M3-T. By default the
    `hypothesis create` is emitted only for the first milestone that uses that hypothesis.
    """
    m = milestone.upper().removeprefix("E-")
    if m not in MILESTONES:
        raise ValueError(f"unknown milestone {milestone!r}")
    keys = [k for k in UNIT_ORDER if UNITS[k].milestone == m]
    hyp = UNITS[keys[0]].hypothesis
    if include_hypothesis is None:
        include_hypothesis = next(UNITS[k].milestone for k in UNIT_ORDER if UNITS[k].hypothesis == hyp) == m
    out = [hypothesis_command(hyp)] if include_hypothesis else []
    for k in keys:
        out += experiment_command(k, aliases, root)
    return out


def init_command(goal: str = "<研究目标>", constraints: Sequence[str] = ()) -> Command:
    """`research init` template. Human fields stay placeholders, and only the user runs it (research/AGENTS.md)."""
    cons = list(constraints) or [
        "π0.5 权重冻结；只演化 evolvable/ 白名单三文件",
        "禁读清单由 frozen/forbidden.yaml 与 AST 检查强制",
        "V/T 结果对编辑者不可读；T 只看一次",
        "Run 只执行已 commit 且已推送 origin 的源码",
    ]
    args = ["init", "--human", HUMAN_ID, "--quote", HUMAN_QUOTE, "--channel", HUMAN_CHANNEL,
            "--goal", goal, "--metric", "success_rate", "--baseline", "π0.5 每 N 步重查的最佳 N"]
    for c in cons:
        args += ["--constraint", c]
    return _cmd(*args, who="human", note="仅用户：项目初始化，--quote 必须是你的原话")


def bootstrap_commands(aliases: Mapping[str, str] | None = None) -> list[Command]:
    """The whole sequence: init → H-1..H-3 → the 7 committed Experiments with contract requests (design §5)."""
    out = [init_command()] + [hypothesis_command(k) for k in ("H-cond2", "H-cond3", "H-cond4")]
    for k in UNIT_ORDER:
        out += experiment_command(k, aliases)
    return out


# ---------------------------------------------------------------------------- (b) runs


@dataclass
class RunPlan:
    """A ready-to-run `research run` plus its --parameters file content (design §5; AGENTS.md R20–R24)."""

    command: Command
    parameters: dict[str, Any]
    parameters_path: str
    slot: dict[str, str]

    def write_parameters(self, root: Path | None = None) -> Path:
        """Write the --parameters JSON file. The CLI reads a *path*. runs/ is git-ignored, so the tree stays clean."""
        path = Path(root or REPO_ROOT) / self.parameters_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.parameters, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def shell(self) -> str:
        """Two shell steps: write the parameters file, then research run."""
        body = json.dumps(self.parameters, ensure_ascii=False)
        return (f"mkdir -p {shlex.quote(str(Path(self.parameters_path).parent))} && "
                f"printf '%s\\n' {shlex.quote(body)} > {shlex.quote(self.parameters_path)}\n{self.command.shell()}")


def checkpoint_ref(mirror_repo: str = CKPT_MIRROR_PLACEHOLDER, revision: str = CKPT_REV_PLACEHOLDER,
                   path: str = OFFICIAL_CKPT["path"]) -> str:
    """`model=OWNER/NAME@REV:path` for the user-owned mirror of the official π0.5 checkpoint (design §1 F3)."""
    return f"model={mirror_repo}@{revision}:{path}"


def run_command(experiment_alias: str, kind: str = "evaluation", slot: Mapping[str, Any] | None = None,
                entrypoint: str | None = None, args: Sequence[str] = (), archive_to: str | None = None, *,
                checkpoint: str = CHECKPOINT_TEMPLATE, parameters: Mapping[str, Any] | None = None,
                supersedes: str | None = None, depends_on: Sequence[str] = (), inputs: Sequence[str] = (),
                aliases: Mapping[str, str] | None = None, research_root: str | None = None,
                root: Path | None = None) -> RunPlan:
    """`research run --experiment E-n --kind evaluation --archive-to … --slot … --parameters … -- bash <entry> …` (design §5).

    `slot` holds the view coordinates, e.g. {task, seed, n}, {surface, candidate, seed} or
    {task, condition, rep}. It may also hold `stage` for an auxiliary slot. A
    `candidate_id` key moves into parameters together with `cand/<id>`.

    The bridge applies the vendored CLI's rules before anything is executed:
    - --parameters is a JSON file path with at most 20 scalar values. It goes under the
      git-ignored runs/params/.
    - An evaluation needs a `model=` checkpoint, and --archive-to must name that same
      repo (rules._plan_violations). With archive_to=None the target is derived from
      the checkpoint.
    - The entrypoint is a Git-tracked script; by default it is `entry` from config.yaml.
      -c/-lc/-e are refused.
    - A slot of a known Experiment is checked against its view (R44).
    - A failed Run is never deleted. To redo a slot after changing code, mark the old
      Run invalid, then pass supersedes=<old Run>.
    """
    if kind not in ("evaluation", "training", "dataset"):
        raise ValueError(f"kind must be evaluation|training|dataset, got {kind!r}")
    u = _unit_of_alias(experiment_alias, aliases)
    exp = _alias(u.key, aliases) if u else experiment_alias
    raw = dict(slot or {})
    cand = raw.pop("candidate_id", None)
    stage = raw.pop("stage", None)
    if u is not None:
        full_slot = slot_for(u.key, stage=stage, root=root, **raw)
    else:
        full_slot = {("n" if k == "N" else k): str(v) for k, v in raw.items()}
        if stage:
            full_slot["stage"] = str(stage)
        if not full_slot:
            raise ValueError("a slot is required (R40)")
    for k in full_slot:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", k):
            raise ValueError(f"slot key {k!r} must match ^[a-z][a-z0-9_]*$ (SCHEMA SlotKey)")
    entry = entrypoint or (u.cfg("entry", None, root) or UNITS[f"E-{u.milestone}"].cfg("entry", None, root) if u else None)
    if not entry:
        raise ValueError("entrypoint is required")
    if not str(entry).endswith((".sh", ".py")):
        raise ValueError("entrypoint must be a Git-tracked .sh or .py file (R21)")
    bad = [t for t in args if t in FORBIDDEN_COMMAND_TOKENS]
    if bad:
        raise ValueError(f"inline-script flags {bad} are refused by capture.frozen_checkout; commit an entrypoint")

    ros = _research_os()
    if kind == "evaluation":
        if not checkpoint.startswith("model="):
            raise ValueError("Research OS evaluation needs a model= checkpoint (rules._plan_violations); "
                             "mirror the dataset checkpoint into a model repo (docs/research_os_usage.md §4)")
        if "<" not in checkpoint and ros is not None and not ros.hf.HF_RE.fullmatch(checkpoint):
            raise ValueError("checkpoint must be model=OWNER/NAME@<40-hex>[:path]")
        derived = "model=" + checkpoint.split("=", 1)[1].split("@", 1)[0]
        if archive_to is None:
            archive_to = derived
        elif archive_to != derived:
            raise ValueError(f"evaluation --archive-to must be the checkpoint repo ({derived}), got {archive_to!r}")
    elif archive_to is None:
        raise ValueError("archive_to is required for non-evaluation Runs")

    params: dict[str, Any] = {"experiment": u.key if u else exp, **full_slot}
    if cand is not None:
        params["candidate_id"] = str(cand)
        params["candidate_branch"] = f"cand/{cand}"
    params.update(dict(parameters or {}))
    if len(params) > 20:
        raise ValueError(f"--parameters holds at most 20 values (SCHEMA Parameters), got {len(params)}")
    for k, v in params.items():
        if not (v is None or isinstance(v, (str, int, float, bool))):
            raise ValueError(f"parameter {k} must be a scalar")
        if isinstance(v, str) and len(v) > 512 or len(k) > 64:
            raise ValueError(f"parameter {k}: name ≤64 and value ≤512 chars")
    tag = "_".join(f"{k}-{full_slot[k]}" for k in sorted(full_slot))
    params_path = f"runs/params/{exp}/{re.sub(r'[^A-Za-z0-9_.-]', '_', tag)}.json"

    argv = ["run", "--experiment", exp, "--kind", kind, "--archive-to", archive_to]
    if kind == "evaluation":
        argv += ["--checkpoint", checkpoint]
    for k, v in full_slot.items():
        argv += ["--slot", f"{k}={v}"]
    argv += ["--parameters", params_path]
    if full_slot.get("seed", "").isdigit():
        argv += ["--seed", full_slot["seed"]]
    if supersedes:
        argv += ["--supersedes", supersedes]
    if depends_on:
        argv += ["--depends-on", ",".join(depends_on)]
    for i in inputs:
        argv += ["--input", i]
    if research_root:
        argv += ["--root", research_root]
    argv += ["--", "bash" if str(entry).endswith(".sh") else "python", str(entry), *args]
    return RunPlan(_cmd(*argv, note=f"{exp} ({u.key if u else '?'}) slot {full_slot}"), params, params_path, full_slot)


# ---------------------------------------------------------------------------- (c) metrics


def write_metrics(path: str | os.PathLike | None, successes: int, trials: int) -> dict[str, Any]:
    """Write `{"successes","trials","success_rate"}` to $RESEARCH_METRICS (design §5 metrics; metrics.core_metrics).

    `path=None` reads $RESEARCH_METRICS. success_rate is exactly successes/trials. Any
    other key is refused by the ledger, so per-episode details belong in
    $RESEARCH_RUN_DIR instead.
    """
    if path is None:
        path = os.environ.get("RESEARCH_METRICS")
        if not path:
            raise RuntimeError("RESEARCH_METRICS is not set; this must run inside `research run`")
    if type(successes) is not int or type(trials) is not int or trials <= 0 or not 0 <= successes <= trials:
        raise ValueError("need integers with 0 <= successes <= trials and trials > 0")
    data = {"successes": successes, "trials": trials, "success_rate": successes / trials}
    ros = _research_os()
    if ros is not None:
        data = ros.metrics.core_metrics("evaluation", data)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".metrics-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
        fh.write("\n")
    os.replace(tmp, p)
    return data


def write_outputs(path: str | os.PathLike | None, outputs: Mapping[str, str]) -> dict[str, str]:
    """Write `{product: "model|dataset=OWNER/NAME@REV[:path]"}` to $RESEARCH_OUTPUTS (research/AGENTS.md R22)."""
    if path is None:
        path = os.environ.get("RESEARCH_OUTPUTS")
        if not path:
            raise RuntimeError("RESEARCH_OUTPUTS is not set; this must run inside `research run`")
    ros = _research_os()
    for name, ref in outputs.items():
        if ros is not None and not ros.hf.HF_RE.fullmatch(ref):
            raise ValueError(f"output {name}: {ref!r} is not a pinned HF reference")
    Path(path).write_text(json.dumps(dict(outputs)) + "\n", encoding="utf-8")
    return dict(outputs)


# ---------------------------------------------------------------------------- (d) receipts → observation / decision


def _receipt_dict(receipt: Receipt | Mapping[str, Any]) -> dict[str, Any]:
    return receipt.to_dict() if isinstance(receipt, Receipt) else Receipt.from_dict(receipt).to_dict()


def receipt_statement(receipt: Receipt | Mapping[str, Any]) -> str:
    """One factual sentence summarising a lineage receipt (design §4). No interpretation beyond the gate outcome."""
    r = _receipt_dict(receipt)
    g = r["gates"]
    l3 = g.get("L3")
    l3s = f"L3 b={l3['b']} c={l3['c']} ε={l3['eps']} p={l3['p']}" if l3 else "L3 未到达"
    cost = "；".join(f"{k} 现任/候选={v}" for k, v in r["cost"].items()) or "成本未记录"
    return (f"收据 {r['receipt_id']}：候选 {r['candidate']}（父 {r['parent']}，面 {r['surface']}，"
            f"diff_sha {r['diff_sha'][:12] or '—'}）门 L0={g.get('L0', '未到达')} L1={g.get('L1', '未到达')} "
            f"L2={g.get('L2', '未到达')} {l3s}；{cost}；评测于 {json.dumps(r['evaluated_on'], ensure_ascii=False)}；"
            f"判定 {r['decision']}。假设：{r['hypothesis']}")[:4000]


def receipt_to_observation(receipt: Receipt | Mapping[str, Any], *, experiment_alias: str = "E-M3",
                           extra_runs: Sequence[str] = (), aliases: Mapping[str, str] | None = None) -> list[Command]:
    """Commands that turn one lineage receipt (design §4, §3.3 evolve_round) into ledger records.

    The list holds, in order:
    1. `observation create`, which an agent may run. Its evidence is the
       candidate's L3 Run (Receipt.research_run), the incumbent's V Run and the
       A/A Runs that b, c and ε depend on (receipt ``evaluated_on.research_runs``),
       plus extra_runs (L1/L2), so the paired McNemar result can be rebuilt
       from the ledger.
    2. `decision propose`, which an agent may run: accept merges the candidate and
       reject keeps the incumbent.
    3. `authority request <D> --scope decision --to made`, which an agent may run.
    4. `authority grant …`, which only the user may run. --human/--quote/--channel are left blank.
    5. `decision make <D> --grant <GRANT_EVENT_ID>`.

    A decision based on evidence needs authority (rules A3). The verifier of any related
    Claim must be someone other than its author.
    """
    r = _receipt_dict(receipt)
    exp = _alias(experiment_alias, aliases)
    run = r.get("research_run") or "<L3_RUN>"
    rr = (r.get("evaluated_on") or {}).get("research_runs") or {}
    inc_run = rr.get("incumbent_V") or "<INCUMBENT_V_RUN>"
    aa_runs = list(rr.get("aa") or []) or ["<AA_RUN_1>", "<AA_RUN_2>", "<AA_RUN_3>"]
    accept = r["decision"] == "accept"
    if accept:
        decision = (f"接受候选 {r['candidate']}（{r['surface']}）为新现任：授权后执行 "
                    f"etude.evolution.round.apply_accept 合并 cand/{r['candidate']}（门通过时尚未合并）；"
                    "合并后回滚 = git revert -m 1 <merge-sha>（见 <receipt>.applied.json）")
    else:
        decision = f"拒绝候选 {r['candidate']}（{r['surface']}），保留现任 {r['parent']}"
    stmt = receipt_statement(r)
    evidence = list(dict.fromkeys([run, inc_run, *aa_runs, *extra_runs]))
    obs = _cmd("observation", "create", "--experiments", exp, "--statement", stmt,
               "--evidence", ",".join(evidence), note="代理可执行；记下返回的 O-n（证据含候选 L3、现任 V、A/A Run）")
    dec = _cmd("decision", "propose", "--decision", decision, "--reason", f"门结果：{stmt[:900]}",
               "--based-on", f"<O-n>,{run}", "--revisit-if", "A/A 重测给出的 ε 改变门结论，或 T 上 H* − H2 < ε",
               note="代理可执行（只是提案）；记下返回的 D-n")
    req = _cmd("authority", "request", "<D-n>", "--scope", "decision", "--to", "made", "--diff", decision,
               "--on-approve", ("决策生效后才执行 apply_accept 合并 cand 分支（门通过时尚未合并）" if accept
                                else "决策生效；现任不变，cand 分支保留备审（保留收据）"),
               "--would-change", "是：改变现任 harness" if accept else "否：现任不变",
               note="代理可提交；记下 event_id")
    grant = _authority_grant("<REQUEST_EVENT_ID>", note="仅用户：授权该决策（原话逐字）")
    make = _cmd("decision", "make", "<D-n>", "--grant", "<GRANT_EVENT_ID>",
                note="授权之后执行；accept 时随后执行 apply_accept(cfg, state, runner, grant_event_id=<GRANT_EVENT_ID>)")
    return [obs, dec, req, grant, make]


# ---------------------------------------------------------------------------- (e) preconditions


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def check_run_preconditions(repo: str | os.PathLike | None = None, *, entrypoint: str | None = None,
                            candidate: str | None = None, fetch: bool = False,
                            research_root: str | os.PathLike | None = None) -> list[str]:
    """Readable reasons why `research run` would refuse in `repo`. An empty list means none (AGENTS.md R20–R22; capture.git_capture).

    Mirrors capture.git_capture: the repo must be a git repository, the work tree must
    be clean, untracked files included, apart from research/ledger.json, WORK and
    WORKSTATE, HEAD must exist, there must be an `origin` remote, and HEAD must be on
    an origin/* branch. It also checks that:
    - the entry script is tracked,
    - for a candidate, HEAD is the pushed `cand/<id>` branch (design §3.3),
    - a ledger exists,
    - huggingface_hub is importable (needed by --checkpoint and archival).

    With fetch=True, and when the cheap checks pass, it also calls the real
    capture.git_capture, which runs `git fetch origin`.
    """
    probe = _git(Path(repo or REPO_ROOT), "rev-parse", "--show-toplevel")
    if probe.returncode != 0:
        return [f"R20: {repo or REPO_ROOT} is not inside a git repository"]
    top = Path(probe.stdout.strip())
    reasons: list[str] = []
    spec = [".", ":(exclude)research/ledger.json", ":(exclude)research/WORK", ":(exclude)research/WORKSTATE"]
    status = _git(top, "status", "--porcelain", "--untracked-files=all", "--", *spec).stdout.splitlines()
    if status:
        untracked = sum(1 for s in status if s.startswith("??"))
        shown = "; ".join(s.strip() for s in status[:6])
        reasons.append(f"未提交：工作树有 {len(status)} 处改动（{untracked} 个未跟踪文件），research run 拒绝脏树。例：{shown}"
                       + (" …" if len(status) > 6 else ""))
    head = _git(top, "rev-parse", "HEAD")
    commit = head.stdout.strip() if head.returncode == 0 else None
    if commit is None:
        reasons.append("没有 commit：HEAD 不存在")
    if _git(top, "remote", "get-url", "origin").returncode != 0:
        reasons.append("无 origin：git remote 里没有 origin，Run 只执行已推送 origin 的 commit（git remote add origin <url> 后 push）")
    elif commit:
        if fetch:
            _git(top, "fetch", "--quiet", "origin")
        branches = _git(top, "branch", "-r", "--contains", commit).stdout.splitlines()
        if not any(b.strip().startswith("origin/") for b in branches):
            reasons.append(f"未推送：HEAD {commit[:12]} 不在任何 origin/* 分支上（git push origin <branch>）"
                           + ("" if fetch else "；未 fetch，按本地远程引用判断"))
    if entrypoint:
        if not (top / entrypoint).is_file():
            reasons.append(f"入口脚本不存在：{entrypoint}")
        elif _git(top, "ls-files", "--error-unmatch", entrypoint).returncode != 0:
            reasons.append(f"入口脚本未受 Git 管理：{entrypoint}（R21 禁止外部临时脚本）")
    if candidate:
        branch = _git(top, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch != f"cand/{candidate}":
            reasons.append(f"候选 {candidate} 必须在 cand/{candidate} 分支上运行，当前是 {branch or '?'}")
        elif _git(top, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/cand/{candidate}").returncode != 0:
            reasons.append(f"cand/{candidate} 尚未推送到 origin")
    rroot = Path(research_root or os.environ.get("RESEARCH_OS_ROOT") or (REPO_ROOT / "research"))
    if not (rroot / "ledger.json").is_file():
        reasons.append(f"无台账：{rroot}/ledger.json 不存在，先由用户执行 research init（scripts/research_bootstrap.sh 第 1 步）")
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:
        reasons.append("缺 huggingface_hub：--checkpoint 校验与 HF 归档需要它（装 research/requirements.txt 的固定版本；不改 pyproject.toml）")
    if fetch and not reasons:
        ros = _research_os()
        if ros is not None:
            try:
                ros.capture.git_capture(str(rroot), str(top))
            except Exception as exc:  # ResearchError or git failure
                reasons.append(f"capture.git_capture: {exc}")
    return reasons


# ---------------------------------------------------------------------------- bootstrap script


def _script_line(c: Command, subst: Mapping[str, str] | None = None) -> str:
    """`$R <args>` with one `--flag value` pair per line; `subst` maps placeholders to "${VAR}" shell expansions."""
    subst = subst or {}
    parts = [f'"{subst[a]}"' if a in subst else shlex.quote(a) for a in c.research_args()]
    lines, i = [], 0
    while i < len(parts):
        if parts[i].startswith("--") and i + 1 < len(parts) and not parts[i + 1].startswith("--"):
            lines.append(f"{parts[i]} {parts[i + 1]}")
            i += 2
        else:
            lines.append(parts[i])
            i += 1
    return "$R " + " \\\n  ".join(lines)


def bootstrap_script(aliases: Mapping[str, str] | None = None) -> str:
    """Contents of scripts/research_bootstrap.sh. Human fields come from env vars that the user sets (design §5)."""
    subst = {HUMAN_ID: "${RESEARCH_HUMAN}", HUMAN_QUOTE: "${RESEARCH_QUOTE}", HUMAN_CHANNEL: "${RESEARCH_CHANNEL}",
             "<研究目标>": "${RESEARCH_GOAL}"}
    out = [
        "#!/usr/bin/env bash",
        "# Research OS bootstrap for Etude. Generated by:",
        "#   uv run python -m etude.evolution.research_bridge --bootstrap-script > scripts/research_bootstrap.sh",
        "# One-time sequence (docs/research_os_usage.md §1):",
        "#   1) research init (human statement)",
        "#   2) H-1/H-2/H-3 = 条件 2/3/4",
        "#   3) 7 committed Experiments, each followed by its contract-authority request",
        "#   4) The user grants each contract (printed at the end; it cannot be scripted ahead)",
        "# Agents must never fill RESEARCH_HUMAN / RESEARCH_QUOTE / RESEARCH_CHANNEL. The user sets them, using their own words.",
        "# Usage:",
        "#   RESEARCH_HUMAN=<id> RESEARCH_QUOTE='<你的原话>' RESEARCH_CHANNEL=<渠道> RESEARCH_GOAL='<目标>' \\",
        "#   RESEARCH_ACTOR=agent:<id> bash scripts/research_bootstrap.sh",
        "set -euo pipefail",
        'cd "$(git rev-parse --show-toplevel)"',
        ': "${RESEARCH_HUMAN:?set RESEARCH_HUMAN to your id}"',
        ': "${RESEARCH_QUOTE:?set RESEARCH_QUOTE to your verbatim words}"',
        ': "${RESEARCH_CHANNEL:?set RESEARCH_CHANNEL, e.g. claude-code}"',
        ': "${RESEARCH_GOAL:?set RESEARCH_GOAL}"',
        ': "${RESEARCH_ACTOR:?set RESEARCH_ACTOR=agent:<id>}"',
        'test ! -f research/ledger.json || { echo "research/ledger.json already exists; bootstrap is one-time" >&2; exit 1; }',
        'R="uv run python research/tools/research"',
        "",
        "# 0) every view file exists and passes the ViewSpec schema + R44 rules",
        "uv run python -m etude.evolution.research_bridge --check-views",
        "",
        "# 1) init: human statement (research/AGENTS.md 科学记录与授权)",
        _script_line(init_command(), subst),
        "",
        "# 2) hypotheses, in this order so the aliases come out as H-1/H-2/H-3",
    ]
    out += [_script_line(hypothesis_command(k)) for k in ("H-cond2", "H-cond3", "H-cond4")]
    out.append("")
    out.append("# 3) committed experiments, in this order so the aliases come out as "
               + ", ".join(f"{k}→{DEFAULT_ALIASES[k]}" for k in UNIT_ORDER))
    for k in UNIT_ORDER:
        create, request, _grant = experiment_command(k, aliases)
        out += [f"# {k}", _script_line(create), _script_line(request), ""]
    out += [
        "# 4) The user grants each contract (agents never run this). List the open requests:",
        "$R pending-authority",
        "cat <<'EOF'",
        "For each request event_id above, grant it yourself, quoting your own words:",
        "  RESEARCH_ACTOR=agent:<id> uv run python research/tools/research authority grant <REQUEST_EVENT_ID> \\",
        "    --human <你的id> --quote \"<你的原话>\" --channel <渠道>",
        "Then commit the ledger: git add research/ledger.json && git commit -m 'research: bootstrap ledger' && git push origin main",
        "EOF",
        "",
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------------- demo / CLI


def check_views(root: Path | None = None) -> dict[str, list[str]]:
    """{experiment key: errors} for every view file named by the configs (design §5; SCHEMA ViewSpec)."""
    found = {}
    for k in UNIT_ORDER:
        spec, path = load_view(k, root)
        found[k] = [f"missing {path}"] if spec is None else validate_view(spec)
    return found


def _demo() -> int:
    print("== verbatim check of §3/§5/§6 rule text against the design doc")
    mism = verify_rules_against_design()
    print("  ok" if not mism else "\n".join("  " + x for x in mism))

    print("\n== views (owned by the experiments component) — schema + R44")
    for k, errs in check_views().items():
        print(f"  {k:8s} {view_path(k):30s} {'ok' if not errs else '; '.join(errs)}")

    print("\n== M0 commands: init (human) → H-1 → E-M0, E-M0-AA (+ contract authority)")
    cmds = [init_command()] + experiment_commands("M0")
    for c in cmds:
        print(f"# [{c.who}] {c.note}" + (f"  (placeholders: {', '.join(c.blocked_on)})" if c.blocked_on else ""))
        print(c.shell())

    print("\n== M0 run commands: sweep cell, superseding rerun, verifier audit (aux slot), A/A rep")
    sweep_args = ["--task", "stack_bowls", "--seed", "0", "--n", "25", "--episodes", "50"]
    plans = [
        run_command("E-M0", slot={"task": "stack_bowls", "N": 25, "seed": 0}, args=sweep_args,
                    parameters={"episodes": 50, "layouts": "seed0[0:50]", "num_envs": 1}),
        run_command("E-M0", slot={"task": "stack_bowls", "N": 25, "seed": 0}, args=sweep_args, supersedes="E1-R001"),
        run_command("E-M0", slot={"stage": "verifier_audit", "task": "stack_bowls", "N": 25, "seed": 0},
                    args=["--audit-verifier", *sweep_args],
                    parameters={"metric_semantics": "successes=verifier Pass & official fail; trials=official fail; rate=FPR"}),
        run_command("E-M0-AA", slot={"task": "stack_bowls", "condition": "pi05_nstar", "rep": 2},
                    args=["--task", "stack_bowls", "--seed", "1", "--n", "<N*>", "--episodes", "50"],
                    parameters={"split": "V", "seed": 1}),
    ]
    for p in plans:
        print(f"# {p.command.note}")
        print(p.shell())

    print("\n== M3 L3 gate for candidate c0018 (run from the cand/c0018 worktree; ledger = main checkout)")
    p3 = run_command("E-M3", slot={"surface": "planner_prompt", "candidate": "r1", "seed": 1, "candidate_id": "c0018"},
                     args=["--gate", "L3", "--candidate", "c0018"], research_root="<MAIN_CHECKOUT>/research")
    print(p3.shell())
    print("# same candidate, L1 medoid rerun = auxiliary slot (not a view cell)")
    p1 = run_command("E-M3", slot={"stage": "L1", "surface": "planner_prompt", "candidate": "r1", "candidate_id": "c0018"},
                     args=["--gate", "L1", "--candidate", "c0018"], parameters={"seed": 0, "episodes": 5},
                     research_root="<MAIN_CHECKOUT>/research")
    print(p1.shell())

    allc = cmds + [p.command for p in plans] + [p3.command, p1.command] + bootstrap_commands()
    print("\n== argparse syntax check against research_os/cli.py:", syntax_check(allc) or "all parse")

    print("\n== example $RESEARCH_METRICS file (written by write_metrics)")
    with tempfile.TemporaryDirectory() as d:
        mpath = Path(d) / "metrics.json"
        write_metrics(mpath, successes=19, trials=50)
        print(f"  metrics.json: {mpath.read_text().strip()}")

    print("\n== lineage receipt → observation / decision templates (fake receipt; research_run unset)")
    rec = Receipt.from_dict({
        "receipt_id": "r0042", "parent": "c0017", "candidate": "c0018", "surface": "prompts/planner.md",
        "diff_sha": "0" * 40, "hypothesis": "先 ee_move 对中再交还 VLA 可减少抓空",
        "gates": {"L0": True, "L1": "5/5", "L2": "19/20", "L3": {"b": 17, "c": 6, "eps": 9, "p": 0.012}},
        "cost": {"vlm_tokens_per_ep": [91000, 97000], "wall_per_ep_s": [205, 214]}, "decision": "accept",
        "evaluated_on": {"V": "seed1[0:50]", "tasks": list(DEV_TASKS)}, "env": {}, "rollback": "git revert <merge-sha>",
    })
    for c in receipt_to_observation(rec, extra_runs=["<L1_RUN>", "<L2_RUN>"]):
        print(f"# [{c.who}] {c.note}")
        print(c.shell())

    entry = UNITS["E-M0"].cfg("entry", "scripts/run_m0.sh")
    print(f"\n== check_run_preconditions({REPO_ROOT}, entrypoint={entry})")
    reasons = check_run_preconditions(REPO_ROOT, entrypoint=entry)
    for r in reasons:
        print("  BLOCKED:", r)
    if not reasons:
        print("  no blocking reasons")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: --demo | --print-commands M | --check-views | --bootstrap-script | --preconditions (design §5)."""
    ap = argparse.ArgumentParser(prog="research_bridge", description="Research OS bridge (design §5)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--demo", action="store_true")
    g.add_argument("--print-commands", metavar="M")
    g.add_argument("--check-views", action="store_true")
    g.add_argument("--bootstrap-script", action="store_true")
    g.add_argument("--preconditions", action="store_true")
    ap.add_argument("--entrypoint")
    ap.add_argument("--candidate")
    ap.add_argument("--fetch", action="store_true")
    a = ap.parse_args(argv)
    if a.demo:
        return _demo()
    if a.print_commands:
        for c in experiment_commands(a.print_commands):
            print(f"# [{c.who}] {c.note}\n{c.shell()}")
        return 0
    if a.check_views:
        res = check_views()
        for k, errs in res.items():
            print(f"{k}: {view_path(k)}: " + ("ok" if not errs else "; ".join(errs)))
        return 1 if any(res.values()) else 0
    if a.bootstrap_script:
        sys.stdout.write(bootstrap_script())
        return 0
    reasons = check_run_preconditions(REPO_ROOT, entrypoint=a.entrypoint, candidate=a.candidate, fetch=a.fetch)
    print("\n".join(reasons) or "ok")
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
