"""Shared executor plumbing (design §3.3 pseudocode, §8.1 items 3 and 7).

Every executor works chunk by chunk. ``next_chunk(node, frame, state)`` first
evaluates the stop rule on the newest measured frame (the result of the
previous chunk), then either returns the next actions (``status="running"``)
or a terminal status with no actions:

* ``done``    the node's stop rule fired; the runner asks the verifier;
* ``blocked`` IK failure / no progress (design §4 Blocked); the runner emits a
  Blocked verdict without calling the verifier;
* ``lost``    the perceive target is not visible with enough confidence; the
  runner emits InsufficientEvidence (design §4 semantics).

Thresholds come only from ``frozen/params/thresholds.yaml`` (tighten-only).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from etude.core.actions import joint_action_dict
from etude.core.config import load_thresholds
from etude.core.types import ARMS, ObsFrame

#: Executor statuses (see module docstring).
STATUSES: tuple[str, ...] = ("running", "done", "blocked", "lost")

#: Threshold names every executor reads (design §3.3; frozen/params).
EXECUTOR_THRESHOLDS: tuple[str, ...] = (
    "stall.joint_delta_max_rad",
    "ee_move.max_step_m",
    "ee_move.pos_tol_m",
    "ee_move.center_tol_px",
    "ee_move.ik_progress_min_m",
    "ee_move.max_actions_per_chunk",
    "perceive.conf_min",
    "gripper.holding_width_min",
    "gripper.closed_cmd_max",
    "gripper.open_cmd_min",
    "lift.min_dz_m",
    "safety.joint_vel_max_rad_s",
)


def load_exec_params(root: str | Path | None = None, overrides: Mapping[str, float] | None = None) -> dict[str, float]:
    """Frozen thresholds used by the executors (design §3.0 "阈值冻结").

    ``overrides`` exists for demos only; the runner never passes it.
    """
    params = load_thresholds(root)
    missing = [k for k in EXECUTOR_THRESHOLDS if k not in params]
    if missing:
        raise KeyError(f"frozen/params/thresholds.yaml lacks executor thresholds: {missing}")
    out = {k: float(params[k]) for k in params}
    if overrides:
        out.update({k: float(v) for k, v in overrides.items()})
    return out


@dataclass
class ChunkResult:
    """One executor step (design §3.3): ``actions`` to return to deploy.py and
    the node ``status`` evaluated on the frame that came in."""

    actions: list[dict[str, np.ndarray]] = field(default_factory=list)
    status: str = "running"
    reason: str = ""
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.status != "running"


def hold_action(frame: ObsFrame) -> dict[str, np.ndarray]:
    """Joint-mode dict that holds both arms at the measured joints and keeps
    the gripper command echo (design §8.1 item 7: both arms in every dict)."""
    return joint_action_dict(frame.joint_vector())


def hold_actions(frame: ObsFrame, n: int) -> list[dict[str, np.ndarray]]:
    """``n`` copies of :func:`hold_action` (budget exhausted / aborted / re-observe)."""
    a = hold_action(frame)
    return [{k: v.copy() for k, v in a.items()} for _ in range(max(0, int(n)))]


def ee_translation(a: ObsFrame, b: ObsFrame, arm: str) -> float:
    """Measured ee translation (m) of ``arm`` between two frames."""
    return float(np.linalg.norm(b.ee_pose(arm)[:3] - a.ee_pose(arm)[:3]))


def max_ee_translation(a: ObsFrame, b: ObsFrame, arms: tuple[str, ...] = ARMS) -> float:
    """Max over ``arms`` of :func:`ee_translation`."""
    return max(ee_translation(a, b, arm) for arm in arms)


def max_joint_delta(a: ObsFrame, b: ObsFrame, arms: tuple[str, ...] = ARMS) -> float:
    """Max |Δq| (rad) over the arm joints of ``arms`` between two frames."""
    return max(float(np.max(np.abs(b.joints(arm) - a.joints(arm)))) for arm in arms)


def gripper_holding(frame: ObsFrame, arm: str, params: Mapping[str, float]) -> bool | None:
    """Close commanded (echo ≤ ``gripper.closed_cmd_max``) and measured width
    ≥ ``gripper.holding_width_min`` (design §8.1 item 3; Spotter App. B).

    Returns None when the measured width is unavailable (deploy.py did not
    add ``gripper_width_measured``).
    """
    w = frame.gripper_width_measured(arm)
    if w is None:
        return None
    return frame.gripper_cmd_echo(arm) <= params["gripper.closed_cmd_max"] and w >= params["gripper.holding_width_min"]


__all__ = [
    "STATUSES", "EXECUTOR_THRESHOLDS", "load_exec_params", "ChunkResult", "hold_action", "hold_actions",
    "ee_translation", "max_ee_translation", "max_joint_delta", "gripper_holding",
]
