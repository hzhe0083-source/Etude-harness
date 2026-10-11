"""Demo for the XPolicyLab adapter (design §2 P2/P4, §5 M0, §8.1 items 5 and 7).

``uv run python -m etude.adapter --demo`` runs the frozen ``deploy.py``
forwarder loop (``eval_one_episode``) against :class:`etude.fakes.FakeTaskEnv`.
The model side is the real ``deploy/XPolicyLab/policy/Etude/model.py``, called
through an in-process stand-in for XPolicyLab's ``WsModelClient``. It runs both
modes over several chunks. It checks four things: every action dict that
reaches the env passes :mod:`etude.adapter.action_check`; the env saw only
allow-listed attribute reads; the two deploy.py enhancements arrive in the
model's observations; and the batch path is disabled.

Harness mode runs the real :class:`etude.adapter.harness_build.HarnessRunner`
and ``etude.runner.NodeRunner`` with fake components. It runs once with the
fake planner and once with the real ``PiRpcPlanner`` driving
``etude/planner/fake_pi.py`` over the real MCP server. No model is called.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import math
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from etude.adapter.action_check import ActionCheckError, assert_action_ok, check_action
from etude.adapter.xpl_model import (
    POLICY_DIR,
    deploy_sha256,
    load_deploy_cfg,
    recorded_deploy_sha256,
)
from etude.core.actions import joint_action_dict


def load_module(path: Path, name: str) -> ModuleType:
    """Import a file by path (deploy.py / model.py are not on sys.path in demos)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LocalModelClient:
    """In-process stand-in for XPolicyLab ``WsModelClient.call`` (design §8.2 F5).

    Same calling convention: only ``func_name`` and ``obs``; ``reset`` takes no
    payload; other public method names are dispatched generically. Payloads are
    deep-copied in both directions to mimic msgpack serialization.
    """

    def __init__(self, model: Any):
        self.model = model
        self.calls: list[str] = []

    def call(self, func_name: str | None = None, obs: Any = None, **kwargs: Any) -> Any:
        if kwargs:
            raise TypeError(f"WsModelClient.call() only accepts func_name and obs; got {sorted(kwargs)}")
        if not isinstance(func_name, str) or not func_name or func_name.startswith("_"):
            raise NotImplementedError(f"unsupported websocket model call: {func_name!r}")
        self.calls.append(func_name)
        if func_name == "reset":
            if obs is not None:
                raise TypeError("reset takes no obs payload")
            return self.model.reset()
        fn = getattr(self.model, func_name)
        res = fn() if obs is None else fn(copy.deepcopy(obs))
        return copy.deepcopy(res)


def scan_deploy_task_env(path: Path, allow: set[str], deny: set[str]) -> dict[str, Any]:
    """Static scan of deploy.py (a demo copy of what etude.frozen.ast_check enforces):
    every ``TASK_ENV.<a>[.<b>]`` chain must be allow-listed; no deny-listed name appears."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    chains: set[str] = set()
    bad_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in deny:
                bad_names.add(node.attr)
            parts: list[str] = []
            cur: ast.AST = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name) and cur.id == "TASK_ENV":
                parts.reverse()
                chain = parts[0] if parts[0] != "robot_manager" or len(parts) == 1 else ".".join(parts[:2])
                chains.add(chain)
        elif isinstance(node, ast.Name) and node.id in deny:
            bad_names.add(node.id)
    chains.discard("robot_manager")  # prefix of the robot_manager.* entries
    return {"chains": sorted(chains), "outside_allow": sorted(chains - allow), "deny_hits": sorted(bad_names)}


def _check_env(env: Any, label: str) -> None:
    from etude.fakes import ROBOT_MANAGER_ALLOW, TASK_ENV_ALLOW

    allowed = set(TASK_ENV_ALLOW) | {f"robot_manager.{n}" for n in ROBOT_MANAGER_ALLOW}
    accessed = set(env.fake_accessed)
    assert not env.fake_violations, f"{label}: allow-list violations {env.fake_violations}"
    assert accessed <= allowed, f"{label}: accessed {sorted(accessed - allowed)}"
    for i, a in enumerate(env.fake_actions):
        assert_action_ok(a, index=i)
    print(f"  {label}: env attrs read = {sorted(accessed)}; violations = 0; "
          f"{len(env.fake_actions)} executed dicts re-checked OK")


def run_demo() -> int:
    """Run both adapter modes through deploy.eval_one_episode on fakes (design §5 M0, §3.3)."""
    from etude.core.config import load_forbidden
    from etude.fakes import FakePolicy, FakeTaskEnv

    deploy = load_module(POLICY_DIR / "deploy.py", "etude_xpl_deploy")
    model_mod = load_module(POLICY_DIR / "model.py", "etude_xpl_model_py")
    base_cfg = load_deploy_cfg()
    forbidden = load_forbidden()

    print("[1] deploy.py audit")
    actual, recorded = deploy_sha256(), recorded_deploy_sha256()
    print(f"  sha256 actual={actual}\n  sha256 recorded={recorded}  match={actual == recorded}")
    pending = set(forbidden.get("deploy_task_env_pending_signoff") or ())
    allow = set(forbidden["deploy_task_env_allow"]) | pending
    scan = scan_deploy_task_env(POLICY_DIR / "deploy.py", allow, set(forbidden["deny_names"]))
    print(f"  TASK_ENV chains: {scan['chains']}")
    print(f"  awaiting human sign-off (forbidden.yaml deploy_task_env_pending_signoff): "
          f"{sorted(pending & set(scan['chains']))}")
    assert not scan["outside_allow"], scan
    assert not scan["deny_hits"], scan

    print("[2] action_check rejects what RoboDojo would reject")
    good = joint_action_dict(np.zeros(14))
    bad_cases = {
        "mixed joint+ee": {**good, "left_ee_pose": np.r_[0, 0, 0, 1, 0, 0, 0.0]},
        "delta only": {"left_delta_ee_pose": np.zeros(7), "right_delta_ee_pose": np.zeros(7),
                       "left_ee_joint_state": np.ones(1), "right_ee_joint_state": np.ones(1)},
        "missing right arm": {k: v for k, v in good.items() if not k.startswith("right_arm")},
        "scalar gripper": {**good, "left_ee_joint_state": 1.0},
        "wrong dim": {**good, "left_arm_joint_state": np.zeros(7)},
        "nan": {**good, "right_arm_joint_state": np.full(6, np.nan)},
    }
    for name, a in bad_cases.items():
        rep = check_action(a)
        assert not rep.ok, name
        print(f"  {name:18s} -> {rep.errors[0][:90]}")
    try:
        assert_action_ok(bad_cases["mixed joint+ee"])
        raise AssertionError("mixed dict accepted")
    except ActionCheckError:
        pass

    print("[3] m0_requery: FakePolicy chunk[:N] through deploy.eval_one_episode")
    for n, step_lim in ((10, 95), (25, 120)):
        cfg = dict(base_cfg, mode="m0_requery", exec_steps=n, task_name="stack_bowls", log_dir="")
        pol = FakePolicy()
        model = model_mod.Model(cfg, backend=pol)
        env = FakeTaskEnv(step_lim=step_lim)
        client = LocalModelClient(model)
        client.call(func_name="reset")  # RoboDojo env.reset() also resets the policy
        deploy.eval_one_episode(TASK_ENV=env, model_client=client)
        latest = model.core.history.latest()
        assert latest.step_lim == step_lim, latest.additional_info
        assert latest.gripper_width_measured("left") is not None and latest.gripper_width_measured("right") is not None
        n_infer = len(pol.calls)  # FakePolicy.reset() (called by Model.reset) clears .calls
        client.call(func_name="reset")
        summary = model.core.finished[-1] if model.core.finished else None
        assert len(env.fake_actions) == step_lim
        assert n_infer == math.ceil(step_lim / n), (n_infer, step_lim, n)
        assert summary is not None and summary["chunks"] == n_infer
        assert client.calls.count("update_obs") == step_lim, client.calls.count("update_obs")
        print(f"  N={n} step_lim={step_lim}: chunks={summary['chunks']} actions_returned={summary['actions_returned']} "
              f"steps_observed={summary['steps_observed']} types={summary['action_types']} "
              f"grip_w(L)={latest.gripper_width_measured('left'):.3f}")
        _check_env(env, f"m0 N={n}")

    print("[4] harness mode: HarnessRunner -> etude.runner.NodeRunner (fake components) via deploy.eval_one_episode")
    fakes = {"backend": {"kind": "fake"}, "perceiver": {"kind": "fake"}, "verifier": {"kind": "fake"},
             "memory": {"kind": "none"}, "trace": {"kind": "fake"}}
    hcfg = dict(base_cfg, mode="harness", exec_steps=25, task_name="stack_bowls", log_dir="",
                planner={"kind": "fake"}, **fakes)
    for step_lim, ik_fail in ((300, ()), (120, ("left",))):
        model = model_mod.Model(hcfg)  # runner built by make_runner(cfg) -> HarnessRunner.from_config
        runner = model.core.runner
        env = FakeTaskEnv(step_lim=step_lim, ik_fail_arms=ik_fail)
        client = LocalModelClient(model)
        client.call(func_name="reset")
        deploy.eval_one_episode(TASK_ENV=env, model_client=client)
        verdicts = list(runner.node_log)
        trace_kinds = runner.runner.trace.kinds()
        client.call(func_name="reset")
        summary = model.core.finished[-1]
        assert len(env.fake_actions) == step_lim and summary["chunks"] >= 2, summary
        assert "end" in runner.runner.trace.kinds(), trace_kinds  # trace closed by Model.reset
        print(f"  step_lim={step_lim} ik_fail={ik_fail}: chunks={summary['chunks']} types={summary['action_types']} "
              f"holds={summary['hold_fallbacks']} verdicts(node,kind,t_chunk)={verdicts[:8]}")
        _check_env(env, f"harness step_lim={step_lim}")

    print("[4b] harness mode with the pi_rpc planner on etude/planner/fake_pi.py + MCP server (no model call)")
    import os
    import sys
    import tempfile

    fake_pi = Path(__file__).resolve().parents[1] / "planner" / "fake_pi.py"
    with tempfile.TemporaryDirectory(prefix="etude-adapter-demo-") as tmp:
        pcfg = dict(base_cfg, mode="harness", exec_steps=25, task_name="stack_bowls", log_dir="", **fakes,
                    planner={"kind": "pi_rpc", "mcp_port": 0, "pi_cmd": [sys.executable, str(fake_pi)],
                             "traces_root": tmp, "timeout_s": 60})
        old = os.environ.get("FAKE_PI_SCENARIO")
        os.environ["FAKE_PI_SCENARIO"] = "ok"
        model = model_mod.Model(pcfg)
        hr = model.core.runner
        try:
            env = FakeTaskEnv(step_lim=150)
            client = LocalModelClient(model)
            client.call(func_name="reset")
            deploy.eval_one_episode(TASK_ENV=env, model_client=client)
            verdicts = list(hr.node_log)
            mcp_verdicts = [v.node for v in hr.state.verdicts()] if hr.state is not None else []
            tools = hr.state.tool_counts() if hr.state is not None else {}
            vlm = hr.runner.vlm_calls
            client.call(func_name="reset")
        finally:
            hr.close()
            if old is None:
                os.environ.pop("FAKE_PI_SCENARIO", None)
            else:
                os.environ["FAKE_PI_SCENARIO"] = old
        summary = model.core.finished[-1]
        print(f"  MCP {type(hr.mcp).__name__ if hr.mcp else 'stopped'}; planner calls={vlm}; tool calls={tools}")
        print(f"  chunks={summary['chunks']} types={summary['action_types']} verdicts={verdicts[:6]} "
              f"mirrored into MCP state={mcp_verdicts[:6]}")
        assert len(env.fake_actions) == 150
        _check_env(env, "harness+pi_rpc")

    print("[5] batch path is disabled")
    try:
        deploy.eval_one_episode_batch(TASK_ENV=FakeTaskEnv(step_lim=5), model_client=None)
        raise AssertionError("batch path ran")
    except NotImplementedError as e:
        print(f"  deploy.eval_one_episode_batch -> NotImplementedError: {e}")
    try:
        model.get_action_batch([0])
        raise AssertionError("get_action_batch ran")
    except NotImplementedError as e:
        print(f"  Model.get_action_batch -> NotImplementedError: {e}")
    try:
        model_mod.Model(dict(hcfg, eval_batch=True))
        raise AssertionError("eval_batch: true accepted")
    except ValueError as e:
        print(f"  eval_batch: true -> ValueError: {e}")

    print("[6] the last episode is flushed on SIGTERM / exit (RoboDojo never resets after it)")
    import tempfile

    from etude.fakes import fake_obs

    logd = tempfile.mkdtemp(prefix="etude-adapter-flush-")
    fm = model_mod.Model(dict(base_cfg, mode="m0_requery", exec_steps=10, task_name="stack_bowls", log_dir=logd),
                         backend=FakePolicy())
    fm.reset()
    for t in range(3):
        fm.update_obs(fake_obs(t))
    fm.get_action()
    flushed = fm.core.flush_open_episode("demo SIGTERM")  # what the SIGTERM handler / atexit call does
    lines = (Path(logd) / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
    assert flushed is not None and len(lines) == 1, (flushed, lines)
    print(f"  flushed episode {flushed['episode']}: steps_observed={flushed['steps_observed']}; "
          f"episodes.jsonl lines={len(lines)}; second flush is a no-op: {fm.core.flush_open_episode('again') is None}")

    print("adapter demo OK")
    return 0


__all__ = ["LocalModelClient", "load_module", "run_demo", "scan_deploy_task_env"]
