"""Pure-function copy of RoboDojo's action-dict rules (design §8.1 item 7, §8.2 F1).

Source of the rules (read-only reference, RoboDojo commit 266130a):
``src/eval_client/eval_env.py`` -> ``validate_action_dict``, ``get_action_type``
and the key indexing inside ``take_action_batch``. They are re-stated here
as pure functions over a static robot description so that the policy server
(P2, which has no environment handle) can self-check every action dict it is
about to return, *before* it crosses the websocket. A dict that fails here
would make the official client raise mid-episode (and lose the episode).

Rules mirrored exactly
----------------------
* ``validate_action_dict``: every key must be one of the per-arm keys
  ``{arm}_arm_joint_state``(arm_dim), ``{arm}_ee_joint_state``(ee_dim),
  ``{arm}_ee_pose``(7), ``{arm}_tcp_pose``(7), ``{arm}_delta_ee_pose``(7)
  (unprefixed for a single-arm robot, and then left_/right_ prefixes are
  forbidden); each value must be ``np.ndarray | list | tuple`` (TypeError
  otherwise), 1-D, with the expected length.
* ``get_action_type``: ``joint`` if any ``{arm}_arm_joint_state`` key is
  present, ``ee`` if any ``{arm}_ee_pose`` key is present; neither ->
  "Cannot infer action type" (this is what happens to a dict carrying only
  ``delta_ee_pose``/``tcp_pose``); both -> "Multiple action types".

Extra checks (not in RoboDojo's validator, but RoboDojo would crash or
misbehave at runtime without them)
---------------------------------------------------------------
* completeness: ``take_action_batch`` indexes ``action[key]`` for EVERY target
  arm (arm key for the inferred type + the gripper key), so a dict that
  omits one arm raises KeyError inside the env;
* finiteness: NaN/inf would be pushed straight into the joint controller;
* (warnings only) gripper value outside [0, 1] is silently clipped by the
  env; an ``*_ee_pose`` quaternion far from unit norm goes to IK as is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

#: ``XPolicyLab/utils/robot/_robot_info.json["arx_x5"]`` (arm_dim, ee_dim).
ARX_X5_ARM_DIM: tuple[int, ...] = (6, 6)
ARX_X5_EE_DIM: tuple[int, ...] = (1, 1)


class ActionCheckError(ValueError):
    """Raised by :func:`assert_action_ok`; ``.errors`` lists every problem."""

    def __init__(self, errors: Sequence[str], index: int | None = None):
        self.errors = list(errors)
        self.index = index
        where = "" if index is None else f" (action #{index})"
        super().__init__(f"invalid RoboDojo action dict{where}: " + "; ".join(self.errors))


@dataclass(frozen=True)
class RobotSpec:
    """Static description of the target arms (what ``robot_action_dim_info``
    and ``robot_manager.robot_list`` give the env). Design §8.1 item 7."""

    arm_dim: tuple[int, ...] = ARX_X5_ARM_DIM
    ee_dim: tuple[int, ...] = ARX_X5_EE_DIM
    ee_type: str = "gripper"

    @property
    def arm_count(self) -> int:
        return len(self.arm_dim)

    def prefixes(self) -> list[str]:
        """Key prefixes in robot order: ``["left_", "right_"]`` for two
        target arms, ``[""]`` for one (RoboDojo ``_set_robot_obs_name``)."""
        if self.arm_count == 1:
            return [""]
        if self.arm_count == 2:
            return ["left_", "right_"]
        raise ValueError(f"Unsupported arm count: {self.arm_count}")

    def expected_dims(self) -> dict[str, int]:
        """Key -> expected length (verbatim from ``validate_action_dict``)."""
        if len(self.arm_dim) != len(self.ee_dim):
            raise ValueError(
                f"robot_action_dim_info mismatch: len(arm_dim)={len(self.arm_dim)} != len(ee_dim)={len(self.ee_dim)}"
            )
        out: dict[str, int] = {}
        for p, a, e in zip(self.prefixes(), self.arm_dim, self.ee_dim):
            out[f"{p}arm_joint_state"] = int(a)
            out[f"{p}ee_joint_state"] = int(e)
            out[f"{p}ee_pose"] = 7
            out[f"{p}tcp_pose"] = 7
            out[f"{p}delta_ee_pose"] = 7
        return out


ARX_X5 = RobotSpec()


def validate_action_dict(action: Mapping[str, Any], spec: RobotSpec = ARX_X5) -> None:
    """Pure copy of RoboDojo ``EvalEnv.validate_action_dict`` (design §8.1 item 7).

    Raises ValueError (unexpected keys, forbidden prefixes, wrong dims/ndim)
    or TypeError (value not ``np.ndarray | list | tuple``), exactly as the env.
    """
    expected = spec.expected_dims()
    if spec.arm_count == 1:
        bad = [k for k in action.keys() if k.startswith(("left_", "right_"))]
        if bad:
            raise ValueError(f"Single-arm robot should not contain prefixed keys, but got: {bad}")
    unexpected = [k for k in action if k not in expected]
    if unexpected:
        raise ValueError(f"Unexpected state keys: {unexpected}")
    for key, dim in expected.items():
        if key not in action.keys():
            continue
        value = action[key]
        if not isinstance(value, (np.ndarray, list, tuple)):
            raise TypeError(f"action_dict['{key}'] must be array-like, got {type(value)}")
        arr = np.asarray(value)
        if arr.ndim != 1:
            raise ValueError(f"action_dict['{key}'] must be 1D, got shape {arr.shape}")
        if arr.shape[0] != dim:
            raise ValueError(f"action_dict['{key}'] dim mismatch: expected {dim}, got shape {arr.shape}")


def get_action_type(action: Mapping[str, Any], spec: RobotSpec = ARX_X5) -> str:
    """Pure copy of RoboDojo ``EvalEnv.get_action_type`` (design §8.1 item 7).

    Returns ``"joint"`` or ``"ee"``; raises ValueError("Cannot infer action
    type ...") when neither ``*_arm_joint_state`` nor ``*_ee_pose`` is present
    and ValueError("Multiple action types ...") when both are.
    """
    kinds: list[str] = []
    for p in spec.prefixes():
        if f"{p}arm_joint_state" in action.keys() and "joint" not in kinds:
            kinds.append("joint")
        if f"{p}ee_pose" in action.keys() and "ee" not in kinds:
            kinds.append("ee")
    if not kinds:
        raise ValueError(f"Cannot infer action type from action dict keys: {action.keys()}")
    if len(kinds) > 1:
        raise ValueError(f"Multiple action types found in action dict keys: {action.keys()}")
    return kinds[0]


def required_keys(action_type: str, spec: RobotSpec = ARX_X5) -> list[str]:
    """Keys ``take_action_batch`` indexes for ``action_type`` (every target arm
    + its gripper). Design §8.1 item 7 ("同一字典不能混键", both arms needed)."""
    keys: list[str] = []
    for p in spec.prefixes():
        keys.append(f"{p}arm_joint_state" if action_type == "joint" else f"{p}ee_pose")
        keys.append(f"{p}ee_joint_state")
    return keys


@dataclass
class ActionReport:
    """Result of :func:`check_action`: ``errors`` make RoboDojo raise or
    crash; ``warnings`` are silently altered by the env (clipping)."""

    action_type: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_action(action: Mapping[str, Any], spec: RobotSpec = ARX_X5) -> ActionReport:
    """Run every rule above on one dict and collect findings (design §8.1 item 7).

    Never raises for a bad dict; use :func:`assert_action_ok` to raise.
    """
    rep = ActionReport()
    if not isinstance(action, Mapping):
        rep.errors.append(f"action must be a dict, got {type(action).__name__}")
        return rep
    try:
        validate_action_dict(action, spec)
    except (ValueError, TypeError) as e:
        rep.errors.append(f"validate_action_dict: {e}")
    try:
        rep.action_type = get_action_type(action, spec)
    except ValueError as e:
        rep.errors.append(f"get_action_type: {e}")
    if rep.action_type is not None:
        missing = [k for k in required_keys(rep.action_type, spec) if k not in action]
        if missing:
            rep.errors.append(f"take_action_batch would KeyError: missing {missing}")
    for k, v in action.items():
        try:
            arr = np.asarray(v, dtype=np.float64)
        except (TypeError, ValueError):
            rep.errors.append(f"{k}: not numeric")
            continue
        if not np.all(np.isfinite(arr)):
            rep.errors.append(f"{k}: non-finite value")
            continue
        if k.endswith("ee_joint_state") and spec.ee_type == "gripper":
            if arr.size and (arr.min() < 0.0 or arr.max() > 1.0):
                rep.warnings.append(f"{k}={arr.tolist()} outside [0,1]; env clips it")
        if k.endswith("ee_pose") and not k.endswith("delta_ee_pose") and arr.size == 7:
            qn = float(np.linalg.norm(arr[3:7]))
            if abs(qn - 1.0) > 1e-2:
                rep.warnings.append(f"{k}: quaternion norm {qn:.3f} != 1")
    return rep


def assert_action_ok(action: Mapping[str, Any], spec: RobotSpec = ARX_X5, index: int | None = None) -> str:
    """Raise :class:`ActionCheckError` if ``action`` would fail in RoboDojo;
    return its action type otherwise (design §8.1 item 7)."""
    rep = check_action(action, spec)
    if not rep.ok:
        raise ActionCheckError(rep.errors, index=index)
    assert rep.action_type is not None
    return rep.action_type


def check_actions(actions: Iterable[Mapping[str, Any]], spec: RobotSpec = ARX_X5) -> list[str]:
    """Assert every dict in a chunk; return the per-dict action types
    (a chunk may alternate joint/ee dicts, design §8.1 item 7)."""
    return [assert_action_ok(a, spec, index=i) for i, a in enumerate(actions)]


__all__ = [
    "ARX_X5", "ARX_X5_ARM_DIM", "ARX_X5_EE_DIM", "ActionCheckError", "ActionReport", "RobotSpec",
    "assert_action_ok", "check_action", "check_actions", "get_action_type", "required_keys",
    "validate_action_dict",
]
