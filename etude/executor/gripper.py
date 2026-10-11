"""gripper executor: open/close only (design §3.3 ``gripper(n.args.cmd)``, §8.1 items 3 and 7).

Actions are joint-mode dicts that copy the current measured
``*_arm_joint_state`` of both arms and change only ``*_ee_joint_state`` of
the commanded arm(s) (1.0 = open, 0.0 = close; the env clips to [0, 1] and
rate-limits the gripper, so the command is repeated for ``exec_steps`` steps).

Stop rule after each chunk (design §8.3 "停止 = 夹爪实测"): the command echo
reached the commanded side (≥ ``gripper.open_cmd_min`` / ≤
``gripper.closed_cmd_max``), or the measured width settled (changed less
than the settle distance since the previous chunk; closing on an object
leaves the echo clamped above the closed band, design §8.1 item 3), or
``stop.max_chunks``. The settle distance is ``gripper.settle_delta_m`` if that
frozen threshold exists, else ``ee_move.ik_progress_min_m``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from etude.core.actions import joint_action_dict
from etude.core.types import ARM_DOF, ARMS, GRIPPER_CMDS, Node, ObsFrame
from etude.executor.common import ChunkResult, load_exec_params

#: Normalised gripper commands (ARX X5 config: env clips to [0, 1]).
GRIPPER_VALUE: dict[str, float] = {"open": 1.0, "close": 0.0}


def gripper_action(frame: ObsFrame, cmd: str, arms: Sequence[str] = ARMS) -> dict[str, np.ndarray]:
    """Joint-mode dict: both arms hold their measured joints; the ``arms``
    grippers get ``cmd`` (open/close), the others keep their echo (design §3.3)."""
    if cmd not in GRIPPER_CMDS:
        raise ValueError(f"gripper cmd must be one of {GRIPPER_CMDS}, got {cmd!r}")
    vec = frame.joint_vector().copy()
    for arm in arms:
        if arm not in ARMS:
            raise ValueError(f"unknown arm {arm!r}")
        idx = ARM_DOF if arm == "left" else 2 * ARM_DOF + 1
        vec[idx] = GRIPPER_VALUE[cmd]
    return joint_action_dict(vec)


def node_arms(node: Node) -> tuple[str, ...]:
    """Arms a gripper node drives: ``args.arm`` or both (design §4 ``args.arm`` extension)."""
    return (str(node.args.arm),) if node.args.arm in ARMS else ARMS


class GripperExecutor:
    """Open/close executor with a measured-width stop rule (design §3.3)."""

    name = "gripper"

    def __init__(self, params: Mapping[str, float] | None = None, root: str | Path | None = None):
        """``params``: frozen thresholds (default from ``frozen/params``)."""
        self.params = dict(params) if params is not None else load_exec_params(root)

    def begin(self, node: Node, frame: ObsFrame) -> dict[str, Any]:
        """New attempt state (design §3.3)."""
        return {"start": frame, "prev": None, "n_chunks": 0}

    def settle_distance(self) -> float:
        """Width change (m) below which the gripper counts as settled."""
        return float(self.params.get("gripper.settle_delta_m", self.params["ee_move.ik_progress_min_m"]))

    def reached(self, frame: ObsFrame, cmd: str, arm: str) -> bool:
        """Echo is on the commanded side of the frozen open/closed bands."""
        echo = frame.gripper_cmd_echo(arm)
        if cmd == "open":
            return echo >= self.params["gripper.open_cmd_min"]
        return echo <= self.params["gripper.closed_cmd_max"]

    def stop_reason(self, node: Node, frame: ObsFrame, state: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        """Stop rule (module docstring); returns ``(reason | None, info)``."""
        info: dict[str, Any] = {}
        if state["n_chunks"] == 0 or state["prev"] is None:
            return None, info
        cmd = str(node.args.cmd)
        arms = node_arms(node)
        prev: ObsFrame = state["prev"]
        widths = {a: frame.gripper_width_measured(a) for a in arms}
        info["width_m"] = widths
        info["echo"] = {a: round(frame.gripper_cmd_echo(a), 3) for a in arms}
        reached = all(self.reached(frame, cmd, a) for a in arms)
        settled = all(
            widths[a] is not None and prev.gripper_width_measured(a) is not None
            and abs(widths[a] - prev.gripper_width_measured(a)) < self.settle_distance()
            for a in arms
        )
        if reached and (settled or any(w is None for w in widths.values())):
            return "reached", info
        if settled:
            return "settled", info
        if state["n_chunks"] >= node.stop.max_chunks:
            return "max_chunks", info
        return None, info

    def next_chunk(self, node: Node, frame: ObsFrame, state: dict[str, Any],
                   max_steps: int | None = None) -> ChunkResult:
        """Stop check on ``frame``, else ``exec_steps`` copies of the
        open/close dict (design §3.3 ``gripper(n.args.cmd)``)."""
        if node.args.cmd not in GRIPPER_CMDS:
            return ChunkResult(status="blocked", reason="no_cmd")
        reason, info = self.stop_reason(node, frame, state)
        if reason is not None:
            return ChunkResult(status="done", reason=reason, info=info)
        n = int(node.stop.exec_steps) if max_steps is None else max(0, min(int(node.stop.exec_steps), int(max_steps)))
        a = gripper_action(frame, str(node.args.cmd), node_arms(node))
        state["n_chunks"] += 1
        state["prev"] = frame
        info.update({"cmd": node.args.cmd, "arms": list(node_arms(node)), "n": n})
        return ChunkResult(actions=[{k: v.copy() for k, v in a.items()} for _ in range(n)], status="running", info=info)


def _demo() -> int:
    """Close then open the left gripper on FakeTaskEnv (one with an object)."""
    from etude.executor.servo_demo import deploy_frame
    from etude.fakes import FakeTaskEnv, three_node_sequence

    base = three_node_sequence()[2]
    for cmd, holding in (("close", {"left": 0.015}), ("open", {})):
        node = Node.from_dict({**base.to_dict(), "args": {"cmd": cmd, "arm": "left"},
                               "stop": {"stall_chunks": 1, "max_chunks": 3, "exec_steps": 5}})
        env = FakeTaskEnv(step_lim=100, holding_width=holding)
        if cmd == "open":
            env.take_action(gripper_action(deploy_frame(env), "close", ("left",)))
        ex = GripperExecutor()
        frame = deploy_frame(env)
        st = ex.begin(node, frame)
        while True:
            r = ex.next_chunk(node, frame, st)
            if r.terminal:
                print(f"gripper {cmd}: status={r.status} reason={r.reason} chunks={st['n_chunks']} info={r.info}")
                break
            for a in r.actions:
                env.take_action(a)
            frame = deploy_frame(env)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python -m etude.executor.gripper --demo``."""
    ap = argparse.ArgumentParser(prog="python -m etude.executor.gripper")
    ap.add_argument("--demo", action="store_true")
    if ap.parse_args(argv).demo:
        return _demo()
    ap.print_help()
    return 0


__all__ = ["GRIPPER_VALUE", "GripperExecutor", "gripper_action", "node_arms"]

if __name__ == "__main__":
    sys.exit(main())
