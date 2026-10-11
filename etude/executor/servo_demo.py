"""Demo-only helpers for the executor and runner demos (no tests in this repo).

* :func:`deploy_frame` augments a :class:`~etude.fakes.FakeTaskEnv` obs exactly
  like our deploy.py does (design §2 "两行白名单增强": ``step_lim`` and the
  measured gripper width via ``robot_manager.get_end_effector_real_val``) and
  returns an :class:`~etude.core.types.ObsFrame`;
* :class:`ServoDemoEnv` is a FakeTaskEnv whose left wrist image shows the
  target block where a downward camera at the measured ee pose would see it,
  so the ee_move servo has something to converge on.
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from etude.core.types import ARMS, IMAGE_HW, ObsFrame
from etude.fakes import Block, FakeTaskEnv, fake_obs


def augment_obs(env: Any, obs: Mapping[str, Any]) -> dict[str, Any]:
    """Add ``additional_info.step_lim`` and ``gripper_width_measured`` using only
    allow-listed TASK_ENV accessors (design §2, §8.1 item 3)."""
    out = dict(obs)
    info = dict(out.get("additional_info") or {})
    rm = env.robot_manager
    widths = {}
    for arm in ARMS:
        vals = rm.get_end_effector_real_val(rm.get_robot_by_arm_name(f"{arm}_arm"), [0])[0]
        widths[arm] = None if vals is None else float(np.mean(np.asarray(vals)))
    info["step_lim"] = int(env.step_lim)
    info["gripper_width_measured"] = widths
    out["additional_info"] = info
    return out


def deploy_frame(env: Any) -> ObsFrame:
    """``ObsFrame`` of ``augment_obs(env, env.get_obs())`` at ``t = env.fake_t``."""
    return ObsFrame.from_robodojo_obs(augment_obs(env, env.get_obs()), t=env.fake_t)


class ServoDemoEnv(FakeTaskEnv):
    """FakeTaskEnv with a pose-dependent left wrist image.

    The red block sits at env ``target_xy``; a downward wrist camera with focal
    ``true_focal_px`` at the measured left ee pose sees it at
    ``u = 320 - dy*f/h``, ``v = 240 - dx*f/h`` (the inverse of the default
    :class:`~etude.executor.ee_move.ServoCalib` map, with a deliberately
    different focal length so the loop needs more than one chunk).
    """

    def __init__(self, *args: Any, target_xy: tuple[float, float] = (0.36, 0.21),
                 true_focal_px: float = 440.0, block_px: int = 60, **kwargs: Any):
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "_target_xy", np.asarray(target_xy, dtype=np.float64))
        object.__setattr__(self, "_f", float(true_focal_px))
        object.__setattr__(self, "_bpx", int(block_px))

    def get_obs(self) -> dict[str, Any]:
        """Raw obs whose left wrist view depends on the measured left ee pose."""
        ee = object.__getattribute__(self, "_ee")["left"]
        h = max(float(ee[2]), 0.05)
        dx, dy = object.__getattribute__(self, "_target_xy") - ee[:2]
        f = object.__getattribute__(self, "_f")
        bpx = object.__getattribute__(self, "_bpx")
        u = IMAGE_HW[1] / 2.0 - dy * f / h
        v = IMAGE_HW[0] / 2.0 - dx * f / h
        blocks = [
            Block("cam_head", "red", (200, 260, 60, 40)),
            Block("cam_head", "blue", (400, 260, 60, 40)),
            Block("cam_left_wrist", "red", (int(round(u - bpx / 2)), int(round(v - bpx / 2)), bpx, bpx)),
            Block("cam_right_wrist", "blue", (100, 300, 80, 80)),
        ]
        g = object.__getattribute__
        return fake_obs(g(self, "fake_t"), with_gripper=False, step_lim=None, blocks=blocks,
                        instruction=g(self, "_instruction"), joints=g(self, "_joints"), ee_pose=g(self, "_ee"),
                        grip_echo=g(self, "_echo"))


__all__ = ["augment_obs", "deploy_frame", "ServoDemoEnv"]
