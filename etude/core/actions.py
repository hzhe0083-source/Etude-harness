"""RoboDojo action-dict helpers (design §8.1 item 7, §8.2 F1).

Facts these helpers encode (verified in RoboDojo ``eval_env.py``):

* joint dict keys: ``left_arm_joint_state``(6), ``left_ee_joint_state``(1),
  ``right_arm_joint_state``(6), ``right_ee_joint_state``(1);
* ee dict keys: ``left_ee_pose``(7: x,y,z,qw,qx,qy,qz), ``left_ee_joint_state``(1)
  and ``right_*``; only absolute ``*_ee_pose`` executes (``delta_ee_pose`` /
  ``tcp_pose`` alone raise "Cannot infer action type");
* a dict must not mix joint and ee arm keys; ``take_action_batch`` indexes
  every arm's keys, so BOTH arms must be present in every dict;
* gripper values are clipped to [0, 1] by the env (1 = open in ARX X5 config
  is NOT assumed here; callers pass the raw value).
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from etude.core.types import ACTION_DIM, ARM_DOF, ARMS

JOINT_KEYS: tuple[str, ...] = (
    "left_arm_joint_state",
    "left_ee_joint_state",
    "right_arm_joint_state",
    "right_ee_joint_state",
)
EE_KEYS: tuple[str, ...] = (
    "left_ee_pose",
    "left_ee_joint_state",
    "right_ee_pose",
    "right_ee_joint_state",
)


def joint_action_dict(vec14: Sequence[float] | np.ndarray) -> dict[str, np.ndarray]:
    """14-dim ``[L arm6, L grip, R arm6, R grip]`` -> joint action dict (design §8.2 F1)."""
    v = np.asarray(vec14, dtype=np.float32).reshape(-1)
    if v.shape[0] != ACTION_DIM:
        raise ValueError(f"expected {ACTION_DIM}-dim joint vector, got {v.shape}")
    return {
        "left_arm_joint_state": v[0:ARM_DOF].copy(),
        "left_ee_joint_state": v[ARM_DOF : ARM_DOF + 1].copy(),
        "right_arm_joint_state": v[ARM_DOF + 1 : 2 * ARM_DOF + 1].copy(),
        "right_ee_joint_state": v[2 * ARM_DOF + 1 : 2 * ARM_DOF + 2].copy(),
    }


def chunk_to_joint_actions(chunk: np.ndarray, n: int | None = None) -> list[dict[str, np.ndarray]]:
    """(H,14) absolute joint chunk -> list of joint action dicts, first ``n`` rows (design §3.3)."""
    c = np.asarray(chunk)
    if c.ndim != 2 or c.shape[1] != ACTION_DIM:
        raise ValueError(f"expected (H,{ACTION_DIM}) chunk, got {c.shape}")
    rows = c if n is None else c[: int(n)]
    return [joint_action_dict(r) for r in rows]


def ee_action_dict(
    left_pose: Sequence[float] | np.ndarray,
    left_grip: float,
    right_pose: Sequence[float] | np.ndarray,
    right_grip: float,
) -> dict[str, np.ndarray]:
    """Absolute ee action dict with both arms (design §8.1 item 7).

    Poses are 7-dim ``[x,y,z,qw,qx,qy,qz]`` in the same frame as
    ``obs["state"]["*_ee_pose"]`` (frame equality is open issue §8.4 #1).
    """
    lp = np.asarray(left_pose, dtype=np.float32).reshape(-1)
    rp = np.asarray(right_pose, dtype=np.float32).reshape(-1)
    if lp.shape[0] != 7 or rp.shape[0] != 7:
        raise ValueError("ee poses must be 7-dim [x,y,z,qw,qx,qy,qz]")
    return {
        "left_ee_pose": lp,
        "left_ee_joint_state": np.asarray([left_grip], dtype=np.float32),
        "right_ee_pose": rp,
        "right_ee_joint_state": np.asarray([right_grip], dtype=np.float32),
    }


def action_type(action: Mapping[str, Any]) -> str:
    """Mirror of RoboDojo ``get_action_type``: returns ``joint`` or ``ee``;
    raises ValueError for mixed or un-inferable dicts (design §8.1 item 7)."""
    kinds = set()
    for arm in ARMS:
        if f"{arm}_arm_joint_state" in action:
            kinds.add("joint")
        if f"{arm}_ee_pose" in action:
            kinds.add("ee")
    if not kinds:
        raise ValueError(f"Cannot infer action type from action dict keys: {list(action.keys())}")
    if len(kinds) > 1:
        raise ValueError(f"Multiple action types found in action dict keys: {list(action.keys())}")
    return kinds.pop()


def validate_action(action: Mapping[str, Any]) -> list[str]:
    """Local check equivalent to RoboDojo ``validate_action_dict`` + completeness
    for a dual-arm ARX X5. Returns error strings (empty = valid)."""
    dims = {
        "left_arm_joint_state": ARM_DOF, "right_arm_joint_state": ARM_DOF,
        "left_ee_joint_state": 1, "right_ee_joint_state": 1,
        "left_ee_pose": 7, "right_ee_pose": 7,
        "left_tcp_pose": 7, "right_tcp_pose": 7,
        "left_delta_ee_pose": 7, "right_delta_ee_pose": 7,
    }
    errs: list[str] = []
    for k, v in action.items():
        if k not in dims:
            errs.append(f"unexpected key {k}")
            continue
        n = np.asarray(v).reshape(-1).shape[0]
        if n != dims[k]:
            errs.append(f"{k}: expected dim {dims[k]}, got {n}")
    try:
        kind = action_type(action)
    except ValueError as e:
        return errs + [str(e)]
    need = JOINT_KEYS if kind == "joint" else EE_KEYS
    errs.extend(f"missing key {k}" for k in need if k not in action)
    return errs


__all__ = [
    "JOINT_KEYS", "EE_KEYS", "joint_action_dict", "chunk_to_joint_actions", "ee_action_dict",
    "action_type", "validate_action",
]
