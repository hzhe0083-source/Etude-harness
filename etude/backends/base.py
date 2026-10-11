"""Policy backends for the frozen pi0.5 (design §2 P3, §8.1 item 8, §8.2 F1).

:class:`PolicyBackend` is the ABC from :mod:`etude.core.interfaces`
(re-exported). This module adds the pi0.5 / XPolicyLab ``Pi_05`` observation
packing shared by the remote and in-process backends, and a factory.

Pi_05 packing (XPolicyLab ``policy/Pi_05/model.py::encode_obs`` with
``action_type=joint``, ``env_cfg_type=arx_x5``):

* ``state``: float32 (14,) = ``pack_robot_state`` order
  ``[left_arm_joint_state(6), left_ee_joint_state(1), right_arm_joint_state(6),
  right_ee_joint_state(1)]`` (gripper = command echo, as in the official run);
* ``images``: ``cam_high`` ← ``cam_head``, ``cam_left_wrist``,
  ``cam_right_wrist``; CHW uint8 at native 480x640 (the openpi input
  transform resizes);
* ``prompt``: the node instruction.

The served policy returns ``{"actions": (H, 14), ...}`` already passed through
openpi's output transforms (delta joints restored to absolute, absolute
gripper), which is exactly what ``Pi_05`` feeds to ``unpack_robot_state``.
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from etude.core.interfaces import PolicyBackend
from etude.core.types import ACTION_DIM, ObsFrame

#: openpi camera key ← RoboDojo camera name (Pi_05 ``encode_obs``).
PI05_CAMERAS: dict[str, str] = {
    "cam_high": "cam_head",
    "cam_left_wrist": "cam_left_wrist",
    "cam_right_wrist": "cam_right_wrist",
}


def to_chw_uint8(image: np.ndarray) -> np.ndarray:
    """HWC (or CHW) image → CHW uint8, like Pi_05 ``ensure_chw_uint8``."""
    img = np.asarray(image)
    if img.ndim != 3:
        raise ValueError(f"expected a 3-dim image, got shape {img.shape}")
    if np.issubdtype(img.dtype, np.floating):
        img = (np.clip(img, 0.0, 1.0) * 255.0).astype(np.uint8)
    elif img.dtype != np.uint8:
        img = img.astype(np.uint8)
    if img.shape[-1] in (1, 3):
        hwc = img
    elif img.shape[0] in (1, 3):
        return np.ascontiguousarray(img)
    else:
        raise ValueError(f"unsupported image shape {img.shape}")
    return np.ascontiguousarray(np.transpose(hwc[..., :3], (2, 0, 1)))


def encode_pi05_obs(frame: ObsFrame, prompt: str) -> dict[str, Any]:
    """Pack ``frame`` + ``prompt`` into the openpi Aloha-style input dict
    ``{"state", "images", "prompt"}`` (design §2 P3; Pi_05 ``encode_obs``)."""
    images = {}
    for key, cam in PI05_CAMERAS.items():
        if cam not in frame.images:
            raise KeyError(f"observation lacks camera {cam!r} needed for pi0.5 input {key!r}")
        images[key] = to_chw_uint8(frame.images[cam])
    return {
        "state": frame.joint_vector().astype(np.float32),
        "images": images,
        "prompt": str(prompt),
    }


def decode_actions(result: Mapping[str, Any] | np.ndarray) -> np.ndarray:
    """``{"actions": (H, D)}`` (or the array) → float64 ``(H, 14)`` absolute
    joint chunk (design §8.2 F1). Extra trailing dims are dropped, as
    openpi's Aloha output transform does; fewer than 14 is an error."""
    arr = result["actions"] if isinstance(result, Mapping) else result
    a = np.asarray(arr, dtype=np.float64)
    if a.ndim == 3 and a.shape[0] == 1:
        a = a[0]
    if a.ndim != 2 or a.shape[1] < ACTION_DIM or a.shape[0] == 0:
        raise ValueError(f"policy returned actions of shape {a.shape}, expected (H, >= {ACTION_DIM})")
    return a[:, :ACTION_DIM].copy()


def make_backend(spec: Mapping[str, Any]) -> PolicyBackend:
    """Build a backend from a config dict (design §2): ``{"kind": "fake" |
    "pi05_remote" | "pi05_inprocess", ...kwargs}``. ``pi05_remote`` takes
    ``host``, ``port``, ``api_key``; ``pi05_inprocess`` takes ``model_cfg``."""
    kind = str(spec.get("kind", "pi05_remote"))
    kw = {k: v for k, v in spec.items() if k != "kind"}
    if kind == "fake":
        from etude.fakes import FakePolicy

        return FakePolicy(**kw)
    if kind == "pi05_remote":
        from etude.backends.pi05_remote import Pi05RemoteBackend

        return Pi05RemoteBackend(**kw)
    if kind == "pi05_inprocess":
        from etude.backends.pi05_inprocess import Pi05InProcessBackend

        return Pi05InProcessBackend(**kw)
    raise ValueError(f"unknown backend kind {kind!r}")


__all__ = ["PolicyBackend", "PI05_CAMERAS", "to_chw_uint8", "encode_pi05_obs", "decode_actions", "make_backend"]
