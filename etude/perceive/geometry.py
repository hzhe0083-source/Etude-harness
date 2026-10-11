"""2D box relations, RGB differencing and multi-view agreement used by the
verifier predicates (design §4 "谓词由 2D 框关系 + 两视角一致 + RGB 差分实现，
阈值在 frozen/params"; §8.3 E row 1).

All boxes are ``[x, y, w, h]`` pixels (origin top-left, y grows downward) as
in :class:`etude.core.types.Detection`. Every function takes its thresholds
as explicit arguments; callers read them from frozen/params/thresholds.yaml
(see :mod:`etude.verifier.predicates`). Nothing here reads env state.

Return convention for evidence functions: ``(v, conf)`` where ``v`` is
True/False/None (None = cannot decide) and ``conf`` in [0, 1].
"""
from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

__all__ = [
    "Box",
    "area",
    "intersection",
    "iou",
    "x_overlap_frac",
    "containment",
    "center",
    "union_box",
    "clip_box",
    "is_on",
    "is_in",
    "stacked",
    "rgb_diff",
    "moved",
    "not_moved",
    "lifted",
    "arm_home",
    "margin_conf",
    "combine_evidence",
    "views_agree",
]

Box = Sequence[float]


# ---------------------------------------------------------------------------
# box algebra
# ---------------------------------------------------------------------------


def area(b: Box) -> float:
    """Area of an ``[x,y,w,h]`` box (design §4 box relations)."""
    return max(0.0, float(b[2])) * max(0.0, float(b[3]))


def intersection(a: Box, b: Box) -> float:
    """Intersection area of two boxes (design §4)."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def iou(a: Box, b: Box) -> float:
    """Intersection over union (design §4)."""
    i = intersection(a, b)
    u = area(a) + area(b) - i
    return 0.0 if u <= 0 else i / u


def x_overlap_frac(a: Box, b: Box) -> float:
    """Horizontal overlap of ``a`` with ``b`` as a fraction of ``a``'s width
    (``box.on_overlap_min`` semantics, design §4 ``target_on``)."""
    lo, hi = max(a[0], b[0]), min(a[0] + a[2], b[0] + b[2])
    return 0.0 if a[2] <= 0 else max(0.0, hi - lo) / float(a[2])


def containment(a: Box, b: Box) -> float:
    """``area(a ∩ b) / area(a)`` (``box.in_containment_min`` semantics, design §4 ``target_in``)."""
    aa = area(a)
    return 0.0 if aa <= 0 else intersection(a, b) / aa


def center(b: Box) -> tuple[float, float]:
    """Box centre ``(cx, cy)``."""
    return (b[0] + b[2] / 2.0, b[1] + b[3] / 2.0)


def union_box(boxes: Iterable[Box]) -> list[float] | None:
    """Smallest box covering all ``boxes`` (None if empty)."""
    bs = [b for b in boxes if b is not None]
    if not bs:
        return None
    x0 = min(b[0] for b in bs)
    y0 = min(b[1] for b in bs)
    x1 = max(b[0] + b[2] for b in bs)
    y1 = max(b[1] + b[3] for b in bs)
    return [float(x0), float(y0), float(x1 - x0), float(y1 - y0)]


def clip_box(b: Box, hw: tuple[int, int]) -> list[int]:
    """Integer box clipped to an image of size ``hw=(H, W)``."""
    h, w = hw
    x0 = int(max(0, min(w, np.floor(b[0]))))
    y0 = int(max(0, min(h, np.floor(b[1]))))
    x1 = int(max(0, min(w, np.ceil(b[0] + b[2]))))
    y1 = int(max(0, min(h, np.ceil(b[1] + b[3]))))
    return [x0, y0, max(0, x1 - x0), max(0, y1 - y0)]


# ---------------------------------------------------------------------------
# relations (single view)
# ---------------------------------------------------------------------------


def is_on(a: Box, b: Box, overlap_min: float) -> bool:
    """``a`` rests on ``b`` in one image (design §4 ``target_on(A,B)``):
    x-overlap of ``a`` over ``b`` >= ``overlap_min`` (``box.on_overlap_min``),
    ``a``'s centre above ``b``'s centre, and the boxes touch or overlap
    vertically (``a`` bottom >= ``b`` top). No extra tolerance constant."""
    if x_overlap_frac(a, b) < overlap_min:
        return False
    if center(a)[1] >= center(b)[1]:
        return False
    return a[1] + a[3] >= b[1]


def is_in(a: Box, b: Box, containment_min: float) -> bool:
    """``a`` inside ``b`` in one image (design §4 ``target_in(A,B)``):
    ``area(a∩b)/area(a) >= containment_min`` (``box.in_containment_min``)."""
    return containment(a, b) >= containment_min


def stacked(boxes_bottom_first: Sequence[Box], overlap_min: float, containment_min: float) -> bool:
    """Consecutive pairs stacked (design §4 ``stacked([...])``): for each i,
    box i+1 is on box i, or nested in it (bowls), in one image. The list is
    ordered bottom first."""
    if len(boxes_bottom_first) < 2:
        return False
    for lower, upper in zip(boxes_bottom_first[:-1], boxes_bottom_first[1:]):
        if not (is_on(upper, lower, overlap_min) or is_in(upper, lower, containment_min)):
            return False
    return True


# ---------------------------------------------------------------------------
# RGB differencing
# ---------------------------------------------------------------------------


def rgb_diff(img0: np.ndarray, img1: np.ndarray, box: Box | None = None) -> float:
    """Mean ``|img1 - img0| / 255`` over ``box`` (whole image if None)
    (``rgb_diff.*`` semantics, design §4, §8.3 E row 1)."""
    a = np.asarray(img0)[..., :3]
    b = np.asarray(img1)[..., :3]
    if a.shape != b.shape:
        raise ValueError(f"rgb_diff: shape mismatch {a.shape} vs {b.shape}")
    if box is not None:
        x, y, w, h = clip_box(box, a.shape[:2])
        if w == 0 or h == 0:
            return 0.0
        a = a[y : y + h, x : x + w]
        b = b[y : y + h, x : x + w]
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean() / 255.0)


def margin_conf(value: float, thr: float, scale: float | None = None) -> float:
    """Confidence from the distance of ``value`` to threshold ``thr``:
    0.6 at the threshold rising to 0.95 at one ``scale`` away (default
    ``scale = |thr|``). Used to turn scalar evidence into ``conf``."""
    s = abs(thr) if scale is None else abs(scale)
    if s <= 0:
        return 0.95
    return float(0.6 + 0.35 * min(1.0, abs(value - thr) / s))


def moved(img0: np.ndarray, img1: np.ndarray, box: Box, moved_min: float, not_moved_max: float) -> tuple[bool | None, float, float]:
    """Object region changed (design §4 ``moved(A)``): RGB diff inside ``box``
    (union of before/after boxes) >= ``moved_min`` → True; <= ``not_moved_max``
    → False; in between → None. Returns ``(v, conf, diff)``."""
    d = rgb_diff(img0, img1, box)
    if d >= moved_min:
        return True, margin_conf(d, moved_min), d
    if d <= not_moved_max:
        return False, margin_conf(d, not_moved_max), d
    return None, 0.3, d


def not_moved(img0: np.ndarray, img1: np.ndarray, box: Box, moved_min: float, not_moved_max: float) -> tuple[bool | None, float, float]:
    """Object region unchanged (design §4 ``not_moved(A)``); negation of
    :func:`moved` with the same dead band. Returns ``(v, conf, diff)``."""
    v, c, d = moved(img0, img1, box, moved_min, not_moved_max)
    return (None if v is None else not v), c, d


# ---------------------------------------------------------------------------
# kinematic evidence
# ---------------------------------------------------------------------------


def lifted(
    z0: float,
    z1: float,
    dz_min: float,
    wrist_found_after: bool | None = None,
    wrist_conf: float = 0.0,
) -> tuple[bool | None, float]:
    """Target lifted (design §4 ``target_lifted(>X m)``): measured
    ``ee_pose.z`` rise ``z1 - z0 >= dz_min`` (``lift.min_dz_m``), corroborated
    by the target still being detected in the arm's wrist camera after the
    lift (``wrist_found_after``; None = no wrist evidence)."""
    dz = float(z1) - float(z0)
    v_z = dz >= dz_min
    c_z = margin_conf(dz, dz_min)
    if wrist_found_after is None:
        return v_z, c_z
    c_w = wrist_conf if wrist_found_after else 0.6
    return combine_evidence((v_z, c_z), (bool(wrist_found_after), c_w))


def arm_home(joints: np.ndarray, home: np.ndarray, tol_rad: float) -> tuple[bool, float, float]:
    """Arm at home (design §4 ``arm_home``): ``max |q - q_home| <= tol_rad``
    (``arm_home.joint_tol_rad``). Returns ``(v, conf, max_err)``."""
    err = float(np.max(np.abs(np.asarray(joints, dtype=float) - np.asarray(home, dtype=float))))
    return err <= tol_rad, margin_conf(err, tol_rad), err


# ---------------------------------------------------------------------------
# evidence fusion
# ---------------------------------------------------------------------------


def combine_evidence(primary: tuple[bool | None, float], secondary: tuple[bool | None, float]) -> tuple[bool | None, float]:
    """Fuse two independent evidence sources (design §7 "实测开度 + 腕相机差分双证据").

    * either None → the other one unchanged;
    * agree → noisy-OR confidence ``1 - (1-c1)(1-c2)``;
    * disagree → primary's value with ``min(c1, c2) / 2`` (low enough that the
      verifier returns InsufficientEvidence and may ask ``inspect``).
    """
    (v1, c1), (v2, c2) = primary, secondary
    if v2 is None:
        return v1, c1
    if v1 is None:
        return v2, c2
    if v1 == v2:
        return v1, float(1.0 - (1.0 - c1) * (1.0 - c2))
    return v1, float(min(c1, c2) / 2.0)


def views_agree(
    per_view: Sequence[tuple[str, bool | None, float]],
    views_agree_min: int,
    single_view_scale: float,
) -> tuple[bool | None, float, list[str]]:
    """Multi-view agreement (design §4 "两视角一致"; ``verifier.views_agree_min``).

    ``per_view`` is ``[(view, v, conf), ...]`` for the views where the
    relation could be evaluated (v None entries are ignored). Rules:

    * no decided view → ``(None, 0.0, [])``;
    * views disagree → ``(None, 0.3, views)``;
    * all decided views agree and their count >= ``views_agree_min`` →
      ``(v, mean conf, views)``;
    * fewer agreeing views than required → ``(v, mean conf * single_view_scale, views)``.
    """
    decided = [(vw, v, c) for vw, v, c in per_view if v is not None]
    if not decided:
        return None, 0.0, []
    views = [vw for vw, _, _ in decided]
    vals = {v for _, v, _ in decided}
    if len(vals) > 1:
        return None, 0.3, views
    v = decided[0][1]
    conf = float(np.mean([c for _, _, c in decided]))
    if len(decided) < views_agree_min:
        conf *= single_view_scale
    return v, conf, views
