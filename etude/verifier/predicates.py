"""Verifier predicate table (design §4 "postcondition 只能用验证器谓词表"; §7
"夹爪回显误判抓取 → 实测开度 + 腕相机差分双证据"; §8.3 E row 1; §3.5 threat
table "放宽验证器阈值").

Predicates (names = :data:`etude.core.types.PREDICATE_NAMES`):

=====================  ======================================================
``gripper_holding``    measured width (``additional_info.gripper_width_measured``)
                       with close commanded, fused with wrist-camera evidence
                       (target detection, or RGB diff in the finger ROI)
``target_lifted(>Xm)`` measured ``ee_pose.z`` rise >= max(X, lift.min_dz_m),
                       fused with "target still in the wrist view"
``target_in(A,B)``     2D containment of A's box in B's box, >= 2 views agree
``target_on(A,B)``     A on top of B (x-overlap, above, touching), views agree
``stacked([a,b,..])``  consecutive items (bottom first) on/nested, views agree
``moved(A)``           RGB diff inside A's box (before ∪ after), static views
``not_moved(A)``       same, inverted, with the not-moved dead band
``arm_home[(arm)]``    joints within arm_home.joint_tol_rad of the episode's
                       first observed joints (or an injected home)
=====================  ======================================================

Derived (not a postcondition): :func:`scene_not_moved` -- the ``scene_intact``
field of non-Pass verdicts (design §4; Recova 2610.01178 App. B M_int): every
detected non-target object ``not_moved`` against the episode's t=0 keyframe,
with the same ``rgb_diff`` dead band (no new threshold). Head-view boxes the
robot gripper overlaps (detector query, not a VLM call) are undecidable, not
"moved". A node's targets include the implicit object of a contact node
without ``args.target`` (:func:`implicit_targets`).

Every threshold is read from ``frozen/params/thresholds.yaml`` via
:func:`load_verifier_thresholds`, which refuses any entry looser than the
frozen baseline embedded below (design §3.0 "冻结，只收紧"). Predicate args
may only tighten a threshold (e.g. ``target_lifted(>0.08m)``), never loosen it.

Inputs are sanitized frames only (see :func:`sanitize_frame`): no
instruction text, no episode-end flag, no official score, no self-report.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from etude.core.config import load_forbidden, load_thresholds_raw
from etude.core.interfaces import Perceiver
from etude.core.schema import parse_predicate
from etude.core.types import ARMS, VIEW_TO_CAM, Detection, Node, ObsFrame, PredicateResult
from etude.perceive import geometry as geo

__all__ = [
    "FROZEN_BASELINE",
    "VERIFIER_THRESHOLD_NAMES",
    "SINGLE_VIEW_CONF_SCALE",
    "GRIPPER_ROI_FRAC",
    "ARM_TO_WRIST",
    "HARNESS_FLAG_KEYS",
    "ThresholdError",
    "VerifierThresholds",
    "load_verifier_thresholds",
    "sanitize_frame",
    "PredicateOutcome",
    "EvalContext",
    "PREDICATES",
    "evaluate_predicate",
    "node_objects",
    "node_manipulated",
    "implicit_targets",
    "untracked_contact",
    "CONTACT_EXECUTORS",
    "ARM_STATE_PREDICATES",
    "ROBOT_OCCLUDER_QUERIES",
    "scene_not_moved",
]

# ---------------------------------------------------------------------------
# frozen thresholds (tighten-only)
# ---------------------------------------------------------------------------

#: Frozen baseline values of every threshold the verifier/perceiver reads
#: (copied from frozen/params/thresholds.yaml v1, 2026-10-11). The loaded
#: file may equal or tighten these, never loosen them (design §3.5).
FROZEN_BASELINE: dict[str, float] = {
    "perceive.conf_min": 0.6,
    "verifier.pred_conf_min": 0.6,
    "verifier.views_agree_min": 2,
    "verifier.max_inspect_per_node": 1,
    "gripper.holding_width_min": 0.012,
    "gripper.closed_cmd_max": 0.2,
    "gripper.open_cmd_min": 0.8,
    "lift.min_dz_m": 0.05,
    "rgb_diff.moved_min": 0.08,
    "rgb_diff.not_moved_max": 0.03,
    "box.on_overlap_min": 0.5,
    "box.in_containment_min": 0.7,
    "arm_home.joint_tol_rad": 0.1,
    "safety.joint_vel_max_rad_s": 3.0,
    "ee_move.pos_tol_m": 0.01,
}
VERIFIER_THRESHOLD_NAMES: tuple[str, ...] = tuple(FROZEN_BASELINE)

#: Confidence multiplier when fewer than ``verifier.views_agree_min`` views
#: could evaluate a box relation (frozen code constant; design §4 "两视角一致").
SINGLE_VIEW_CONF_SCALE: float = 0.7
#: Finger region of a wrist image as fractions ``(x, y, w, h)`` of (W, H):
#: where a held object appears (bottom centre). Geometry constant, to be
#: checked against ARX X5 wrist renders in M0 (design §8.4 #6).
GRIPPER_ROI_FRAC: tuple[float, float, float, float] = (0.25, 0.5, 0.5, 0.5)
ARM_TO_WRIST: dict[str, str] = {"left": "wrist_l", "right": "wrist_r"}
#: Harness-written flags (not env reads) the verifier honours in
#: ``ObsFrame.additional_info``: written by our executor / adapter.
HARNESS_FLAG_KEYS: tuple[str, ...] = ("physx_warning", "ik_failed", "harness")

_LIFT_ARG_RE = re.compile(r"^>\s*([0-9]*\.?[0-9]+)\s*m$")


class ThresholdError(ValueError):
    """A threshold entry is missing, not tighten-only, or looser than frozen."""


@dataclass(frozen=True)
class VerifierThresholds:
    """Threshold values used by predicates and the verdict logic (design §3.5)."""

    perceive_conf_min: float
    pred_conf_min: float
    views_agree_min: int
    max_inspect_per_node: int
    holding_width_min: float
    closed_cmd_max: float
    open_cmd_min: float
    lift_min_dz_m: float
    moved_min: float
    not_moved_max: float
    on_overlap_min: float
    in_containment_min: float
    home_joint_tol_rad: float
    joint_vel_max_rad_s: float
    ee_pos_tol_m: float
    raw: Mapping[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, float]:
        return dict(self.raw)


def _tighter_or_equal(tighter: str, baseline: float, value: float) -> bool:
    if tighter == "higher":
        return value >= baseline
    if tighter == "lower":
        return value <= baseline
    return value == baseline


def load_verifier_thresholds(root: Any = None, overrides: Mapping[str, float] | None = None) -> VerifierThresholds:
    """Load and validate verifier thresholds (design §3.0 "阈值...冻结，只收紧", §3.5).

    Checks, for each name in :data:`VERIFIER_THRESHOLD_NAMES`: present;
    ``direction == "tighten_only"``; ``tighter`` in {higher, lower}; value
    not looser than :data:`FROZEN_BASELINE`. ``overrides`` (e.g. an
    experiment config) must also be tightenings of the loaded value.
    Raises :class:`ThresholdError` listing every violation.
    """
    raw = load_thresholds_raw(root)
    errs: list[str] = []
    vals: dict[str, float] = {}
    for name, base in FROZEN_BASELINE.items():
        e = raw.get(name)
        if not isinstance(e, Mapping) or "value" not in e:
            errs.append(f"{name}: missing from frozen/params/thresholds.yaml")
            continue
        if e.get("direction") != "tighten_only":
            errs.append(f"{name}: direction must be tighten_only, got {e.get('direction')!r}")
        tighter = e.get("tighter")
        if tighter not in ("higher", "lower"):
            errs.append(f"{name}: tighter must be higher|lower, got {tighter!r}")
            continue
        v = float(e["value"])
        if not _tighter_or_equal(tighter, base, v):
            errs.append(f"{name}: {v} is looser than frozen {base} (tighter={tighter})")
        vals[name] = v
    for name, v in (overrides or {}).items():
        if name not in vals:
            errs.append(f"override {name}: not a verifier threshold")
            continue
        tighter = raw[name]["tighter"]
        if not _tighter_or_equal(tighter, vals[name], float(v)):
            errs.append(f"override {name}: {v} loosens {vals[name]} (tighter={tighter})")
        else:
            vals[name] = float(v)
    if errs:
        raise ThresholdError("threshold check failed: " + "; ".join(errs))
    return VerifierThresholds(
        perceive_conf_min=vals["perceive.conf_min"],
        pred_conf_min=vals["verifier.pred_conf_min"],
        views_agree_min=int(vals["verifier.views_agree_min"]),
        max_inspect_per_node=int(vals["verifier.max_inspect_per_node"]),
        holding_width_min=vals["gripper.holding_width_min"],
        closed_cmd_max=vals["gripper.closed_cmd_max"],
        open_cmd_min=vals["gripper.open_cmd_min"],
        lift_min_dz_m=vals["lift.min_dz_m"],
        moved_min=vals["rgb_diff.moved_min"],
        not_moved_max=vals["rgb_diff.not_moved_max"],
        on_overlap_min=vals["box.on_overlap_min"],
        in_containment_min=vals["box.in_containment_min"],
        home_joint_tol_rad=vals["arm_home.joint_tol_rad"],
        joint_vel_max_rad_s=vals["safety.joint_vel_max_rad_s"],
        ee_pos_tol_m=vals["ee_move.pos_tol_m"],
        raw=dict(vals),
    )


# ---------------------------------------------------------------------------
# input sanitation (design §3.5 "自宣成功 / 回合提前结束泄露官方成功")
# ---------------------------------------------------------------------------


def _allowed_info_keys(root: Any = None) -> tuple[str, ...]:
    try:
        env_keys = list((load_forbidden(root).get("allowed_obs") or {}).get("additional_info") or [])
    except FileNotFoundError:
        env_keys = []
    env_keys = env_keys or ["frequency", "step_lim", "gripper_width_measured"]
    return tuple(env_keys) + HARNESS_FLAG_KEYS


def sanitize_frame(frame: ObsFrame, allowed_info: Sequence[str] | None = None) -> ObsFrame:
    """Copy of ``frame`` the verifier may read (design §3.5): images, the six
    allowed state keys, ``additional_info`` restricted to
    ``forbidden.yaml:allowed_obs.additional_info`` plus harness flags
    (:data:`HARNESS_FLAG_KEYS`), and NO instruction text. Anything like
    ``episode_end``, ``success``, ``done`` or an official score is dropped."""
    keys = tuple(allowed_info) if allowed_info is not None else _allowed_info_keys()
    state_keys = {f"{a}_{k}" for a in ARMS for k in ("arm_joint_state", "ee_pose", "ee_joint_state")}
    return ObsFrame(
        images=dict(frame.images),
        state={k: v for k, v in frame.state.items() if k in state_keys},
        additional_info={k: copy.deepcopy(v) for k, v in frame.additional_info.items() if k in keys},
        t=frame.t,
        instruction="",
    )


# ---------------------------------------------------------------------------
# evaluation context
# ---------------------------------------------------------------------------


@dataclass
class PredicateOutcome:
    """One evaluated predicate plus where a second opinion should look
    (design §4 ``inspect(frame_ref, region)``)."""

    result: PredicateResult
    focus_view: str | None = None
    focus_region: list[float] | None = None
    question: str = ""


@dataclass
class EvalContext:
    """Everything a predicate may read (design §4): the node (for
    ``args.target`` / ``args.arm`` only), sanitized frames (oldest first),
    the perceiver, thresholds and the episode home joints."""

    node: Node
    frames: Sequence[ObsFrame]
    perceiver: Perceiver
    th: VerifierThresholds
    home: Mapping[str, np.ndarray] | None = None
    _cache: dict[tuple[int, str, str], Detection] = field(default_factory=dict)

    @property
    def before(self) -> ObsFrame:
        return self.frames[0]

    @property
    def after(self) -> ObsFrame:
        return self.frames[-1]

    def det(self, frame: ObsFrame, query: str, view: str) -> Detection:
        """Cached ``perceiver.perceive(query, frame.image(view), view)``; a
        missing camera yields ``found=False, conf=0``."""
        key = (id(frame), query, view)
        if key not in self._cache:
            cam = VIEW_TO_CAM[view]
            if cam not in frame.images:
                self._cache[key] = Detection(found=False, conf=0.0, query=query, view=view)
            else:
                self._cache[key] = self.perceiver.perceive(query, frame.image(view), view)
        return self._cache[key]

    def target_query(self) -> str | None:
        return self.node.args.target_query()

    def arms_for(self, args: Sequence[str]) -> list[str]:
        if args and args[0] in ARMS:
            return [args[0]]
        if self.node.args.arm in ARMS:
            return [self.node.args.arm]
        return list(ARMS)

    def static_views(self) -> list[str]:
        """Views whose camera did not move over the node: head always; a wrist
        view if that arm's measured ee position moved < ``ee_move.pos_tol_m``."""
        views = ["head"]
        for arm, view in ARM_TO_WRIST.items():
            try:
                d = float(np.linalg.norm(self.after.ee_pose(arm)[:3] - self.before.ee_pose(arm)[:3]))
            except KeyError:
                continue
            if d < self.th.ee_pos_tol_m:
                views.append(view)
        return views


def _pr(v: bool | None, conf: float, src: str) -> PredicateResult:
    return PredicateResult(v=None if v is None else bool(v), conf=float(np.clip(conf, 0.0, 1.0)), src=src)


def _roi(img: np.ndarray) -> list[float]:
    h, w = img.shape[:2]
    fx, fy, fw, fh = GRIPPER_ROI_FRAC
    return [fx * w, fy * h, fw * w, fh * h]


def _det_evidence(d: Detection) -> tuple[bool | None, float]:
    """found → (True, conf); seen but not confident → (None, conf); absent → (False, 0.6)."""
    if d.found:
        return True, d.conf
    if d.conf > 0:
        return None, d.conf
    return False, 0.6


# ---------------------------------------------------------------------------
# predicates
# ---------------------------------------------------------------------------


def _gripper_holding(args: list[str], ctx: EvalContext) -> PredicateOutcome:
    """design §4 ``gripper_holding``; §7 measured width + wrist evidence."""
    th = ctx.th
    per_arm: list[tuple[str, bool | None, float, str, str, list[float] | None]] = []
    q = ctx.target_query()
    for arm in ctx.arms_for(args):
        w = ctx.after.gripper_width_measured(arm)
        e = ctx.after.gripper_cmd_echo(arm)
        if w is None:
            ev_w: tuple[bool | None, float] = (None, 0.0)
            src_w = "gripper_width_missing"
        elif e >= th.open_cmd_min:
            ev_w = (False, geo.margin_conf(e, th.open_cmd_min, 0.2))
            src_w = f"gripper_width_measured(cmd open {e:.2f})"
        elif e <= th.closed_cmd_max:
            ev_w = (w >= th.holding_width_min, geo.margin_conf(w, th.holding_width_min))
            src_w = f"gripper_width_measured({w * 1000:.1f}mm)"
        else:
            ev_w = (None, 0.3)
            src_w = f"gripper_width_measured(cmd mid {e:.2f})"
        view = ARM_TO_WRIST[arm]
        region: list[float] | None
        if q:
            d = ctx.det(ctx.after, q, view)
            ev_r = _det_evidence(d)
            src_r = f"wrist_detection({view})"
            region = d.bbox
        else:
            cam = VIEW_TO_CAM[view]
            if cam in ctx.after.images and cam in ctx.before.images:
                img0, img1 = ctx.before.image(view), ctx.after.image(view)
                region = _roi(img1)
                v, c, diff = geo.moved(img0, img1, region, th.moved_min, th.not_moved_max)
                ev_r = (v, c)
                src_r = f"wrist_rgb_diff({view}:{diff:.3f})"
            else:
                ev_r, src_r, region = (None, 0.0), "wrist_rgb_missing", None
        v, c = geo.combine_evidence(ev_w, ev_r)
        if ev_w[0] is None:
            # image-only evidence never decides holding (design §7, Spotter §3.1)
            c = min(c, 0.5)
        per_arm.append((arm, v, c, f"{src_w}+{src_r}", view, region))
    trues = [p for p in per_arm if p[1] is True]
    if trues:
        best = max(trues, key=lambda p: p[2])
    elif all(p[1] is False for p in per_arm):
        best = min(per_arm, key=lambda p: p[2])
    else:
        best = next(p for p in per_arm if p[1] is None)
    arm, v, c, src, view, region = best
    return PredicateOutcome(_pr(v, c, f"{arm}:{src}"), view, region, f"Is the {arm} gripper holding an object?")


def _target_lifted(args: list[str], ctx: EvalContext) -> PredicateOutcome:
    """design §4 ``target_lifted(>Xm)``: ee_pose.z rise + wrist box."""
    th = ctx.th
    dz_min = th.lift_min_dz_m
    if args:
        m = _LIFT_ARG_RE.match(args[0])
        if m:
            dz_min = max(dz_min, float(m.group(1)))  # args may only tighten
    q = ctx.target_query()
    best: tuple[float, str] | None = None
    for arm in ctx.arms_for([]):
        try:
            dz = float(ctx.after.ee_pose(arm)[2] - ctx.before.ee_pose(arm)[2])
        except KeyError:
            continue
        if best is None or dz > best[0]:
            best = (dz, arm)
    if best is None:
        return PredicateOutcome(_pr(None, 0.0, "ee_pose_missing"), None, None, "Was the target lifted?")
    dz, arm = best
    view = ARM_TO_WRIST[arm]
    z0 = float(ctx.before.ee_pose(arm)[2])
    found: bool | None = None
    wconf = 0.0
    region = None
    src = f"{arm}:ee_pose(dz={dz:+.3f}m,min={dz_min:.3f})"
    if q:
        d = ctx.det(ctx.after, q, view)
        found, wconf = _det_evidence(d)
        region = d.bbox
        src += f"+wrist_detection({view})"
    v, c = geo.lifted(z0, z0 + dz, dz_min, found, wconf)
    return PredicateOutcome(_pr(v, c, src), view, region, f"Is the target lifted above the table by the {arm} gripper?")


def _relation(args: list[str], ctx: EvalContext, kind: str) -> PredicateOutcome:
    """design §4 ``target_in`` / ``target_on`` / ``stacked`` over all views."""
    th = ctx.th
    queries = list(args)
    if kind != "stacked" and len(queries) != 2:
        return PredicateOutcome(_pr(None, 0.0, f"{kind}:bad_args"))
    if kind == "stacked" and len(queries) < 2:
        return PredicateOutcome(_pr(None, 0.0, "stacked:bad_args"))
    per_view: list[tuple[str, bool | None, float]] = []
    focus: tuple[str, list[float] | None] | None = None
    for view in VIEW_TO_CAM:
        dets = [ctx.det(ctx.after, qq, view) for qq in queries]
        if not all(d.found for d in dets):
            if focus is None and any(d.found for d in dets):
                focus = (view, geo.union_box(d.bbox for d in dets if d.found))
            continue
        boxes = [d.bbox for d in dets]
        # identical boxes for distinct items → the detector cannot separate them
        if any(geo.iou(boxes[i], boxes[j]) > 0.9 for i in range(len(boxes)) for j in range(i + 1, len(boxes))):
            per_view.append((view, None, 0.0))
            continue
        if kind == "target_in":
            v = geo.is_in(boxes[0], boxes[1], th.in_containment_min)
        elif kind == "target_on":
            v = geo.is_on(boxes[0], boxes[1], th.on_overlap_min)
        else:
            v = geo.stacked(boxes, th.on_overlap_min, th.in_containment_min)
        per_view.append((view, v, float(np.mean([d.conf for d in dets]))))
        if focus is None:
            focus = (view, geo.union_box(boxes))
    v, c, views = geo.views_agree(per_view, th.views_agree_min, SINGLE_VIEW_CONF_SCALE)
    src = f"detection({','.join(views) or 'none'})+box_{kind}"
    if v is None and kind == "target_in" and not views:
        # A invisible after: maybe inside B (occluded) -- leave to inspect
        db = next((ctx.det(ctx.after, queries[1], vw) for vw in VIEW_TO_CAM if ctx.det(ctx.after, queries[1], vw).found), None)
        if db is not None:
            focus = (db.view, db.bbox)
            src = f"detection({db.view}):target_not_visible(occluded?)"
            c = 0.4
    fv, fr = focus if focus else (None, None)
    q = {"target_in": f"Is {queries[0]} inside {queries[-1]}?", "target_on": f"Is {queries[0]} on {queries[-1]}?"}.get(
        kind, f"Are {', '.join(queries)} stacked (bottom first)?"
    )
    return PredicateOutcome(_pr(v, c, src), fv, fr, q)


def _moved(args: list[str], ctx: EvalContext, invert: bool) -> PredicateOutcome:
    """design §4 ``moved(A)`` / ``not_moved(A)``: RGB diff in A's box on static views."""
    th = ctx.th
    name = "not_moved" if invert else "moved"
    if len(args) != 1:
        return PredicateOutcome(_pr(None, 0.0, f"{name}:bad_args"))
    q = args[0]
    per_view: list[tuple[str, bool | None, float]] = []
    srcs: list[str] = []
    focus: tuple[str, list[float] | None] | None = None
    for view in ctx.static_views():
        cam = VIEW_TO_CAM[view]
        if cam not in ctx.before.images or cam not in ctx.after.images:
            continue
        d0, d1 = ctx.det(ctx.before, q, view), ctx.det(ctx.after, q, view)
        box = geo.union_box([d.bbox for d in (d0, d1) if d.found])
        if box is None:
            continue
        fn = geo.not_moved if invert else geo.moved
        v, c, diff = fn(ctx.before.image(view), ctx.after.image(view), box, th.moved_min, th.not_moved_max)
        det_conf = max(d.conf for d in (d0, d1) if d.found)
        per_view.append((view, v, min(c, det_conf)))
        srcs.append(f"{view}:{diff:.3f}")
        if focus is None:
            focus = (view, box)
    v, c, _ = geo.views_agree(per_view, th.views_agree_min, SINGLE_VIEW_CONF_SCALE)
    src = f"rgb_diff({';'.join(srcs) or 'no_detection'})"
    fv, fr = focus if focus else ("head", None)
    return PredicateOutcome(_pr(v, c, src), fv, fr, f"Did {q} {'stay still' if invert else 'move'} during this node?")


def _arm_home(args: list[str], ctx: EvalContext) -> PredicateOutcome:
    """design §4 ``arm_home``: joints within tol of the episode home joints."""
    arms = [args[0]] if args and args[0] in ARMS else list(ARMS)
    if not ctx.home:
        return PredicateOutcome(_pr(None, 0.0, "joint_state:no_home_reference"))
    vs: list[bool] = []
    cs: list[float] = []
    srcs: list[str] = []
    for arm in arms:
        if arm not in ctx.home:
            return PredicateOutcome(_pr(None, 0.0, f"joint_state:no_home_reference({arm})"))
        v, c, err = geo.arm_home(ctx.after.joints(arm), ctx.home[arm], ctx.th.home_joint_tol_rad)
        vs.append(v)
        cs.append(c)
        srcs.append(f"{arm}:{err:.3f}rad")
    v_all = all(vs)
    # conf of the decisive arm(s): min over arms when all home, else min over failing arms
    c = min(cs) if v_all else min(c for v, c in zip(vs, cs) if not v)
    return PredicateOutcome(_pr(v_all, c, f"joint_state({','.join(srcs)})"), "head", None, "Are the arms back home?")


# ---------------------------------------------------------------------------
# scene_intact (derived field; design §4, Recova 2610.01178 App. B / Alg. 1 l. 27-30)
# ---------------------------------------------------------------------------

#: Predicates about the arm's own configuration, not the scene. An anchor
#: re-verification after an ee-space retreat skips them (design §3.3): the
#: retreat's arrival check already covers the arm, and it does not restore joints.
ARM_STATE_PREDICATES: tuple[str, ...] = ("arm_home",)
#: Predicates whose args are object queries.
_OBJECT_ARG_PREDICATES: tuple[str, ...] = ("target_in", "target_on", "stacked", "moved", "not_moved")
#: Predicates whose object args a node is *meant* to move (``not_moved`` args are not).
_MANIPULATED_ARG_PREDICATES: tuple[str, ...] = ("target_in", "target_on", "stacked", "moved")
#: Executors that touch objects (``ee_move`` is a non-contact primitive, design §1).
CONTACT_EXECUTORS: tuple[str, ...] = ("vla", "gripper")
#: Detector queries for the robot's own gripper in the head view: an object box
#: it overlaps (in I_0 or now) is occluded, so that view cannot say "moved"
#: (an arm hovering over its target after a retreat would otherwise read as a
#: disturbed scene). Open-set detector call, not a VLM call; no new threshold
#: (the detector's own ``perceive.conf_min`` decides "found"). To be checked on
#: real ARX X5 head renders in M0.
ROBOT_OCCLUDER_QUERIES: tuple[str, ...] = ("robot gripper",)


def _node_queries(node: Node, names: Sequence[str], with_target: bool) -> set[str]:
    out: set[str] = set()
    if with_target:
        q = node.args.target_query()
        if q:
            out.add(q)
    for expr in node.postcondition:
        try:
            name, args = parse_predicate(expr)
        except ValueError:
            continue
        if name in names:
            out.update(a for a in args if a)
    return out


def node_objects(node: Node) -> set[str]:
    """Object queries a node mentions: ``perceive("…")`` target + object args
    of ``target_in/target_on/stacked/moved/not_moved`` (the scene objects the
    verifier can watch; the perceiver has no "list everything" call)."""
    return _node_queries(node, _OBJECT_ARG_PREDICATES, with_target=True)


def _explicit_manipulated(node: Node) -> set[str]:
    if node.executor not in CONTACT_EXECUTORS:
        return set()
    return _node_queries(node, _MANIPULATED_ARG_PREDICATES, with_target=True)


def _arms_compatible(a: str | None, b: str | None) -> bool:
    return a not in ARMS or b not in ARMS or a == b


def implicit_targets(node: Node, preceding: Sequence[Node] | None) -> set[str]:
    """The object a contact node without an explicit one acts on: a ``vla``
    pick (``args.arm`` only, postconditions ``gripper_holding`` /
    ``target_lifted``) or a ``gripper`` open/close grasps or releases whatever
    the arm was last brought to. Walking ``preceding`` (the nodes before it in
    its sequence) nearest first, over nodes on a compatible arm, the first
    ``args.target`` (e.g. the ``ee_move`` "above the red block") -- or the
    explicit manipulated objects of a contact node -- is it. Empty for
    ``ee_move``, for nodes with explicit objects, and when nothing is found."""
    if node.executor not in CONTACT_EXECUTORS or _explicit_manipulated(node) or not preceding:
        return set()
    for p in reversed(list(preceding)):
        if not _arms_compatible(node.args.arm, p.args.arm):
            continue
        q = p.args.target_query()
        if q:
            return {q}
        objs = _explicit_manipulated(p)
        if objs:
            return objs
    return set()


def node_manipulated(node: Node, preceding: Sequence[Node] | None = None) -> set[str]:
    """Objects a node legitimately moves (its "targets" for ``scene_intact``):
    target + ``target_in/target_on/stacked/moved`` args, or, for a contact node
    without any, its :func:`implicit_targets` given ``preceding``; empty for
    ``ee_move`` (a non-contact primitive, design §1) and never ``not_moved`` args."""
    return _explicit_manipulated(node) or implicit_targets(node, preceding)


def untracked_contact(node: Node, preceding: Sequence[Node] | None = None) -> bool:
    """True for a contact node whose moved object cannot be named (no explicit
    object and no :func:`implicit_targets`): whatever it touched is
    indistinguishable from a disturbance, so ``scene_intact`` is undecidable
    (None) for judgements that must exempt it."""
    return node.executor in CONTACT_EXECUTORS and not node_manipulated(node, preceding)


def scene_not_moved(
    ref: ObsFrame,
    frame: ObsFrame,
    queries: Sequence[str],
    perceiver: Perceiver,
    th: VerifierThresholds,
    occluder_queries: Sequence[str] = ROBOT_OCCLUDER_QUERIES,
) -> tuple[bool | None, dict[str, PredicateResult]]:
    """``scene_intact`` (design §4 derived field): is every object in
    ``queries`` (the non-target objects) ``not_moved`` between the episode
    reference keyframe ``ref`` (I_0, t=0) and ``frame``?

    Per object and static view (head always; a wrist view only if that arm's
    ee is within ``ee_move.pos_tol_m`` of its I_0 position): the RGB diff
    inside the object's I_0 box (its current box if it was not detected at
    I_0) via :func:`geo.not_moved` with ``rgb_diff.moved_min`` /
    ``rgb_diff.not_moved_max`` (no new threshold); views combined with
    ``verifier.views_agree_min``. Objects not detected in either frame are
    skipped ("detected" objects only). In the head view, a box that a detected
    robot gripper (``occluder_queries``, in I_0 or now) intersects is
    undecidable for that view (the arm, not the object, would change the
    pixels), never "moved".

    Returns ``(intact, per_object)``: False if any object confidently moved
    (conf >= ``verifier.pred_conf_min``), True if every evaluated object is
    confidently unmoved, else None (incl. nothing evaluable)."""
    ctx = EvalContext(node=None, frames=[ref, frame], perceiver=perceiver, th=th)  # type: ignore[arg-type]
    views = ctx.static_views()
    occluders: dict[str, list[list[float]]] = {}
    for view in views:
        if view != "head" or VIEW_TO_CAM[view] not in ref.images or VIEW_TO_CAM[view] not in frame.images:
            continue
        occluders[view] = [d.bbox for f in (ref, frame) for oq in occluder_queries
                           for d in (ctx.det(f, oq, view),) if d.found and d.bbox is not None]
    per_obj: dict[str, PredicateResult] = {}
    for q in sorted(set(queries)):
        per_view: list[tuple[str, bool | None, float]] = []
        srcs: list[str] = []
        for view in views:
            cam = VIEW_TO_CAM[view]
            if cam not in ref.images or cam not in frame.images:
                continue
            d0, d1 = ctx.det(ref, q, view), ctx.det(frame, q, view)
            found = [d for d in (d0, d1) if d.found and d.bbox is not None]
            if not found:
                continue
            # I_0 box first; if the gripper covers it, the current box (an object that
            # left the occluded spot shows up there), else undecidable in this view
            clear = [d for d in found if not any(geo.intersection(ob, d.bbox) > 0 for ob in occluders.get(view, ()))]
            if not clear:
                per_view.append((view, None, 0.0))
                srcs.append(f"{view}:occluded_by_gripper")
                continue
            d = clear[0]
            v, c, diff = geo.not_moved(ref.image(view), frame.image(view), d.bbox, th.moved_min, th.not_moved_max)
            per_view.append((view, v, min(c, d.conf)))
            srcs.append(f"{view}:{diff:.3f}")
        if not per_view:
            per_obj[q] = _pr(None, 0.0, "scene_rgb_diff_vs_t0(no_detection)")
            continue
        v, c, _ = geo.views_agree(per_view, th.views_agree_min, SINGLE_VIEW_CONF_SCALE)
        per_obj[q] = _pr(v, c, f"scene_rgb_diff_vs_t{ref.t}({';'.join(srcs)})")
    decided = [r for r in per_obj.values() if not r.src.endswith("(no_detection)")]
    if any(r.v is False and r.conf >= th.pred_conf_min for r in decided):
        return False, per_obj
    if decided and all(r.v is True and r.conf >= th.pred_conf_min for r in decided):
        return True, per_obj
    return None, per_obj


PREDICATES: dict[str, Callable[[list[str], EvalContext], PredicateOutcome]] = {
    "gripper_holding": _gripper_holding,
    "target_lifted": _target_lifted,
    "target_in": lambda a, c: _relation(a, c, "target_in"),
    "target_on": lambda a, c: _relation(a, c, "target_on"),
    "stacked": lambda a, c: _relation(a, c, "stacked"),
    "moved": lambda a, c: _moved(a, c, invert=False),
    "not_moved": lambda a, c: _moved(a, c, invert=True),
    "arm_home": _arm_home,
}


def evaluate_predicate(expr: str, ctx: EvalContext) -> PredicateOutcome:
    """Evaluate one postcondition string (design §4 谓词表). Malformed or
    unknown predicates evaluate to ``v=None, conf=0`` (InsufficientEvidence),
    never to Pass."""
    try:
        name, args = parse_predicate(expr)
    except ValueError as e:
        return PredicateOutcome(_pr(None, 0.0, f"parse_error:{e}"))
    try:
        return PREDICATES[name](args, ctx)
    except KeyError as e:  # missing state key / camera
        return PredicateOutcome(_pr(None, 0.0, f"{name}:missing_input:{e}"))
