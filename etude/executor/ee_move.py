"""ee_move executor: wrist-camera visual servo with absolute ``*_ee_pose`` steps
(design §1, §3.3 ``servo(perceive(...), hist[-1])``, §8.1 item 7, §8.2 F2).

Facts this executor is built on:

* only absolute ``*_ee_pose`` executes (``delta_ee_pose`` raises "Cannot infer
  action type"), so every action is ``measured pose ⊕ small increment``;
* every dict carries both arms; the idle arm is commanded to its measured
  pose; grippers keep their current command echo;
* IK failure silently drops that arm and still costs a step, so the executor
  reads back the measured ``ee_pose`` after every chunk and reports
  ``blocked`` when the arm did not follow the command;
* depth is off in the official config (§8.2 F2): metric scale of a pixel
  error comes from the measured ``ee_pose.z`` above the table plane.

Servo law (one detection per chunk, at most ``k`` actions per chunk,
``k = min(ee_move.max_actions_per_chunk, node.stop.exec_steps)``)::

    e      = bbox_center - image_center                    # px, wrist view
    h      = max(ee_z - table_z + cam_offset_z, min_height) # m
    d_xy   = img_to_ee_xy @ e * h / focal_px                # m
    goal   = measured_xyz + [gain * d_xy, dz_to_hover]
    steps  = straight line to goal, each ≤ ee_move.max_step_m

Arrival: ``|e| ≤ ee_move.center_tol_px`` (and z within ``ee_move.pos_tol_m``
of the hover height when one is configured); it is checked before the IK
read-back, so an arm that arrived although its last chunk was dropped ends
``done`` with ``info["ik_failed"]`` (completion precedence, design §4). The camera model
(:class:`ServoCalib`) is a calibration, not a threshold; its defaults are
placeholders until the M0 probe (design §8.4 #1).
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from etude.core.actions import ee_action_dict
from etude.core.interfaces import Perceiver
from etude.core.types import ARMS, IMAGE_HW, Node, ObsFrame
from etude.executor.common import ChunkResult, load_exec_params

#: Wrist view per arm (design §4 MCP view names).
WRIST_VIEW: dict[str, str] = {"left": "wrist_l", "right": "wrist_r"}


@dataclass
class ServoCalib:
    """Wrist camera model for the servo (design §8.2 F2). Placeholders until
    the M0 4-point probe; not a frozen threshold because it only changes the
    gain of the loop, never what counts as arrived.

    * ``focal_px``: focal length in px of the 640x480 wrist image;
    * ``img_to_ee_xy``: 2x2 map from image ``(+u right, +v down)`` to env
      ``(x, y)`` per arm, for a camera looking down the tool axis;
    * ``table_z``: table plane height in the ``ee_pose`` frame;
    * ``cam_offset_z``: camera height above the ee frame origin;
    * ``min_height``: floor on the height used for scaling;
    * ``gain``: fraction of the estimated offset commanded per chunk;
    * ``hover_height``: if set, also servo z to ``table_z + hover_height``.
    """

    focal_px: float = 380.0
    img_to_ee_xy: dict[str, list[list[float]]] = field(
        default_factory=lambda: {
            "left": [[0.0, -1.0], [-1.0, 0.0]],
            "right": [[0.0, -1.0], [-1.0, 0.0]],
        }
    )
    table_z: float = 0.0
    cam_offset_z: float = 0.0
    min_height: float = 0.05
    gain: float = 0.8
    hover_height: float | None = None

    def pixel_to_xy(self, arm: str, err_px: np.ndarray, ee_z: float) -> np.ndarray:
        """Metric ``(dx, dy)`` that would centre a target seen at ``err_px``."""
        h = max(ee_z - self.table_z + self.cam_offset_z, self.min_height)
        m = np.asarray(self.img_to_ee_xy[arm], dtype=np.float64)
        return m @ (np.asarray(err_px, dtype=np.float64) * h / self.focal_px)


def straight_steps(start: np.ndarray, goal: np.ndarray, max_step: float, k: int) -> list[np.ndarray]:
    """Up to ``k`` xyz waypoints from ``start`` toward ``goal``, each ≤ ``max_step`` apart
    (design §3.3: "≤k 条绝对 *_ee_pose")."""
    start = np.asarray(start, dtype=np.float64)
    goal = np.asarray(goal, dtype=np.float64)
    d = goal - start
    dist = float(np.linalg.norm(d))
    if dist <= 1e-9 or k <= 0 or max_step <= 0:
        return []
    need = int(math.ceil(dist / max_step))
    n = min(int(k), need)
    unit = d / dist
    pts = [start + unit * max_step * (i + 1) for i in range(n)]
    if n == need:
        pts[-1] = goal.copy()
    return pts


class EeMoveExecutor:
    """Visual-servo / pose-servo executor (design §3.3 ``servo``).

    Two entry points share the IK read-back and stepping code:
    :meth:`next_chunk` servos toward a ``perceive("…")`` target in the wrist
    view; :meth:`next_chunk_to_pose` moves to recorded ee poses (used by the
    runner's retreat to the last verified node, design §3.3 / §7).
    """

    name = "ee_move"

    def __init__(self, perceiver: Perceiver, params: Mapping[str, float] | None = None,
                 root: str | Path | None = None, calib: ServoCalib | None = None):
        """``perceiver``: the local detector (design §4 ``perceive``);
        ``params``: frozen thresholds (default from ``frozen/params``)."""
        self.perceiver = perceiver
        self.params = dict(params) if params is not None else load_exec_params(root)
        self.calib = calib or ServoCalib()

    # -- helpers ---------------------------------------------------------------
    def k(self, node: Node | None) -> int:
        """Actions per chunk: ``min(ee_move.max_actions_per_chunk, exec_steps)``."""
        kmax = int(self.params["ee_move.max_actions_per_chunk"])
        return kmax if node is None else max(1, min(kmax, int(node.stop.exec_steps)))

    def choose_arm(self, node: Node, frame: ObsFrame, query: str) -> str:
        """``node.args.arm`` if set, else the side of the head-view detection
        (image left half → left arm), else left."""
        if node.args.arm in ARMS:
            return str(node.args.arm)
        try:
            det = self.perceiver.perceive(query, frame.image("head"), "head")
        except KeyError:
            return "left"
        if det.found and det.center is not None and det.conf >= self.params["perceive.conf_min"]:
            return "left" if det.center[0] < IMAGE_HW[1] / 2.0 else "right"
        return "left"

    def begin(self, node: Node | None, frame: ObsFrame) -> dict[str, Any]:
        """New attempt state (design §3.3)."""
        return {"start": frame, "n_chunks": 0, "arm": None, "last_cmd": None, "prev_xyz": None,
                "prev_err": None, "no_progress": 0}

    def _ik_check(self, frame: ObsFrame, state: dict[str, Any]) -> dict[str, Any] | None:
        """Blocked if the last chunk commanded ≥ ik_progress_min but the measured
        ee moved less than that (IK silently dropped, design §8.1 item 7)."""
        if state["last_cmd"] is None:
            return None
        thr = self.params["ee_move.ik_progress_min_m"]
        for arm, (p_prev, p_cmd) in state["last_cmd"].items():
            commanded = float(np.linalg.norm(p_cmd - p_prev))
            moved = float(np.linalg.norm(frame.ee_pose(arm)[:3] - p_prev))
            if commanded >= thr and moved < thr:
                return {"arm": arm, "commanded_m": round(commanded, 4), "moved_m": round(moved, 4)}
        return None

    def _actions(self, frame: ObsFrame, waypoints: Mapping[str, list[np.ndarray]],
                 orient: Mapping[str, np.ndarray] | None = None) -> list[dict[str, np.ndarray]]:
        """Absolute ee dicts for both arms; arms without waypoints hold their
        measured pose; grippers keep the echo (design §8.1 item 7)."""
        n = max((len(w) for w in waypoints.values()), default=0)
        out = []
        for i in range(n):
            poses = {}
            for arm in ARMS:
                p = frame.ee_pose(arm).copy()
                if orient is not None and arm in orient:
                    p[3:] = orient[arm]
                w = waypoints.get(arm) or []
                if w:
                    p[:3] = w[min(i, len(w) - 1)]
                poses[arm] = p
            out.append(ee_action_dict(poses["left"], frame.gripper_cmd_echo("left"),
                                      poses["right"], frame.gripper_cmd_echo("right")))
        return out

    # -- perceive-target servo ---------------------------------------------------
    def next_chunk(self, node: Node, frame: ObsFrame, state: dict[str, Any],
                   max_steps: int | None = None) -> ChunkResult:
        """One servo chunk toward ``node.args.target`` (design §3.3 ``servo``).

        Terminal statuses: ``blocked`` (IK did not follow and the target is
        not reached, or the pixel error did not shrink for
        ``stop.stall_chunks`` chunks), ``lost`` (target not found in the wrist
        view above ``perceive.conf_min``), ``done`` (``arrived`` or
        ``max_chunks``). Completion takes precedence (design §4, P3): an arm
        that already arrived although its last chunk was not followed ends
        ``done/arrived`` with ``info["ik_failed"]`` (the runner hands the flag
        to the verifier as evidence).
        """
        query = node.args.target_query()
        if not query:
            return ChunkResult(status="blocked", reason="no_target")
        if state["arm"] is None:
            state["arm"] = self.choose_arm(node, frame, query)
        arm = state["arm"]
        info: dict[str, Any] = {"arm": arm, "query": query}

        ik = self._ik_check(frame, state)

        view = WRIST_VIEW[arm]
        det = self.perceiver.perceive(query, frame.image(view), view)
        info["det"] = det.to_dict()
        if not det.found or det.center is None or det.conf < self.params["perceive.conf_min"]:
            if ik is not None:
                return ChunkResult(status="blocked", reason="ik_no_progress", info={**info, **ik})
            return ChunkResult(status="lost", reason="target_not_found", info=info)
        h, w = frame.image(view).shape[:2]
        err = np.array([det.center[0] - w / 2.0, det.center[1] - h / 2.0])
        err_n = float(np.linalg.norm(err))
        info["err_px"] = round(err_n, 1)

        xyz = frame.ee_pose(arm)[:3].copy()
        dz = 0.0
        if self.calib.hover_height is not None:
            dz = (self.calib.table_z + self.calib.hover_height) - xyz[2]
        z_ok = abs(dz) <= self.params["ee_move.pos_tol_m"]
        if err_n <= self.params["ee_move.center_tol_px"] and z_ok:
            if ik is not None:
                info["ik_failed"] = ik
            return ChunkResult(status="done", reason="arrived", info=info)
        if ik is not None:
            return ChunkResult(status="blocked", reason="ik_no_progress", info={**info, **ik})

        if state["prev_err"] is not None and err_n >= state["prev_err"]:
            state["no_progress"] += 1
        else:
            state["no_progress"] = 0
        state["prev_err"] = err_n
        if state["no_progress"] >= node.stop.stall_chunks:
            return ChunkResult(status="blocked", reason="no_progress", info=info)
        if state["n_chunks"] >= node.stop.max_chunks:
            return ChunkResult(status="done", reason="max_chunks", info=info)

        d_xy = self.calib.pixel_to_xy(arm, err, float(xyz[2])) * self.calib.gain
        goal = xyz + np.array([d_xy[0], d_xy[1], dz])
        k = self.k(node) if max_steps is None else max(0, min(self.k(node), int(max_steps)))
        pts = straight_steps(xyz, goal, self.params["ee_move.max_step_m"], k)
        if not pts:
            return ChunkResult(status="done", reason="arrived", info=info)
        state["last_cmd"] = {arm: (xyz, pts[-1].copy())}
        state["n_chunks"] += 1
        info.update({"goal_xyz": [round(float(v), 4) for v in goal], "n": len(pts)})
        return ChunkResult(actions=self._actions(frame, {arm: pts}), status="running", info=info)

    # -- pose servo (retreat) ------------------------------------------------------
    def next_chunk_to_pose(self, frame: ObsFrame, state: dict[str, Any], goals: Mapping[str, np.ndarray],
                           max_chunks: int, k: int | None = None) -> ChunkResult:
        """One chunk toward recorded 7-dim poses ``goals[arm]`` (design §7
        "退回上一已验证节点的记录位姿"). ``done/arrived`` when every arm is
        within ``ee_move.pos_tol_m`` (even if the last chunk was not followed:
        ``info["ik_failed"]``); ``blocked`` on IK read-back failure before
        arriving or after ``max_chunks`` chunks without arriving."""
        ik = self._ik_check(frame, state)
        tol = self.params["ee_move.pos_tol_m"]
        dist = {arm: float(np.linalg.norm(np.asarray(g)[:3] - frame.ee_pose(arm)[:3])) for arm, g in goals.items()}
        info: dict[str, Any] = {"dist_m": {a: round(d, 4) for a, d in dist.items()}}
        if all(d <= tol for d in dist.values()):  # arrival takes precedence over an IK drop
            if ik is not None:
                info["ik_failed"] = ik
            return ChunkResult(status="done", reason="arrived", info=info)
        if ik is not None:
            return ChunkResult(status="blocked", reason="ik_no_progress", info={**info, **ik})
        if state["n_chunks"] >= max_chunks:
            return ChunkResult(status="blocked", reason="retreat_max_chunks", info=info)
        kk = int(self.params["ee_move.max_actions_per_chunk"]) if k is None else max(1, int(k))
        wps = {}
        last = {}
        for arm, g in goals.items():
            xyz = frame.ee_pose(arm)[:3].copy()
            pts = straight_steps(xyz, np.asarray(g, dtype=np.float64)[:3], self.params["ee_move.max_step_m"], kk)
            if pts:
                wps[arm] = pts
                last[arm] = (xyz, pts[-1].copy())
        state["last_cmd"] = last or None
        state["n_chunks"] += 1
        orient = {arm: np.asarray(g, dtype=np.float64)[3:7] for arm, g in goals.items()}
        return ChunkResult(actions=self._actions(frame, wps, orient), status="running", info=info)


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------


def _demo() -> int:
    """Servo the left arm until the red block is centred in a synthetic wrist
    view that shifts with the measured ee pose; then show an IK-failure Blocked,
    and an arm that arrived although its last chunk was dropped (done + ik_failed)."""
    from etude.executor.servo_demo import ServoDemoEnv, deploy_frame
    from etude.fakes import FakePerceiver, three_node_sequence

    ok = True
    node = three_node_sequence()[0]
    for ik_fail in ((), ("left",)):
        env = ServoDemoEnv(step_lim=300, ik_fail_arms=ik_fail)
        ex = EeMoveExecutor(FakePerceiver())
        frame = deploy_frame(env)
        st = ex.begin(node, frame)
        while True:
            r = ex.next_chunk(node, frame, st)
            print(f"  ik_fail={ik_fail} chunk={st['n_chunks']} status={r.status} reason={r.reason} "
                  f"err_px={r.info.get('err_px')} ee={np.round(frame.ee_pose('left')[:3], 3).tolist()}")
            if r.terminal:
                break
            for a in r.actions:
                env.take_action(a)
            frame = deploy_frame(env)
        if not ik_fail and r.status == "done":
            # completion precedence (design §4, P3): the target is centred, and a last
            # 1 cm correction was not followed (IK drop) -> done/arrived + ik_failed evidence
            xyz = frame.ee_pose("left")[:3].copy()
            st["last_cmd"] = {"left": (xyz, xyz + np.array([0.01, 0.0, 0.0]))}
            r2 = ex.next_chunk(node, frame, st)
            print(f"  arrived, last chunk dropped: status={r2.status} reason={r2.reason} "
                  f"ik_failed={r2.info.get('ik_failed')}")
            ok &= r2.status == "done" and bool(r2.info.get("ik_failed"))
        if ik_fail:
            ok &= r.status == "blocked" and r.reason == "ik_no_progress"
    # pose retreat
    env = ServoDemoEnv(step_lim=300)
    ex = EeMoveExecutor(FakePerceiver())
    frame = deploy_frame(env)
    goal = frame.ee_pose("left").copy()
    goal[:3] += np.array([0.03, -0.02, 0.05])
    st = ex.begin(None, frame)
    while True:
        r = ex.next_chunk_to_pose(frame, st, {"left": goal}, max_chunks=4)
        if r.terminal:
            print(f"  retreat status={r.status} reason={r.reason} info={r.info}")
            break
        for a in r.actions:
            env.take_action(a)
        frame = deploy_frame(env)
    print("demo ok" if ok else "demo had problems")
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python -m etude.executor.ee_move --demo``."""
    ap = argparse.ArgumentParser(prog="python -m etude.executor.ee_move")
    ap.add_argument("--demo", action="store_true")
    if ap.parse_args(argv).demo:
        return _demo()
    ap.print_help()
    return 0


__all__ = ["EeMoveExecutor", "ServoCalib", "WRIST_VIEW", "straight_steps"]

if __name__ == "__main__":
    sys.exit(main())
