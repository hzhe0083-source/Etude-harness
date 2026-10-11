"""VLA executor: frozen pi0.5 for contact segments (design §2 P2, §3.3, §8.1 item 7).

Per chunk the executor asks the backend for an ``(H, 14)`` absolute joint
chunk with the node's ``instruction`` as prompt and returns only the first
``node.stop.exec_steps`` rows (default 25 of 50) as joint action dicts.

Stop rule, evaluated on the measured frame after each chunk (design §3.3
``node_done``, §8.3 "停止 = 夹爪实测 + 停滞块数"):

1. stall: for ``stop.stall_chunks`` consecutive chunks the measured ee
   translation of every arm stayed below the stall distance AND the max joint
   change stayed below ``stall.joint_delta_max_rad``. The stall distance is
   ``stall.ee_delta_max_m`` if that frozen threshold exists, else
   ``ee_move.ik_progress_min_m`` (the smallest metric motion the executors
   treat as real; see open issue in the module report);
2. gripper (measured): the node's postcondition contains
   ``gripper_holding[(arm)]`` and the measured width says the gripper holds
   something (close commanded, width ≥ ``gripper.holding_width_min``); if the
   postcondition also has ``target_lifted(>Xm)`` the holding arm's measured
   ee z must additionally have risen by X (default ``lift.min_dz_m``) since
   the attempt started;
3. ``stop.max_chunks`` chunks executed.

This executor never reads episode-end or official signals (design §3.5).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from etude.core.actions import chunk_to_joint_actions
from etude.core.interfaces import PolicyBackend
from etude.core.schema import parse_predicate
from etude.core.types import ACTION_DIM, ARMS, Node, ObsFrame
from etude.executor.common import (
    ChunkResult,
    gripper_holding,
    load_exec_params,
    max_ee_translation,
    max_joint_delta,
)

_LIFT_RE = re.compile(r"^>?\s*([0-9]*\.?[0-9]+)\s*m?$")


def gripper_stop_spec(node: Node, params: Mapping[str, float]) -> dict[str, Any] | None:
    """Parse the measured-gripper stop condition from ``node.postcondition``
    (design §3.3 "夹爪实测"). Returns ``{"arms": (...), "lift_dz": float|None}``
    or None when the node has no ``gripper_holding`` postcondition."""
    arms: tuple[str, ...] | None = None
    lift: float | None = None
    for expr in node.postcondition:
        try:
            name, args = parse_predicate(expr)
        except ValueError:
            continue
        if name == "gripper_holding":
            a = tuple(x for x in args if x in ARMS)
            arms = a or ((node.args.arm,) if node.args.arm in ARMS else ARMS)
        elif name == "target_lifted":
            lift = float(params["lift.min_dz_m"])
            for x in args:
                m = _LIFT_RE.match(x.strip())
                if m:
                    lift = max(float(m.group(1)), float(params["lift.min_dz_m"]))
    if arms is None:
        return None
    return {"arms": arms, "lift_dz": lift}


class VlaExecutor:
    """Chunked pi0.5 executor (design §3.3 ``pi05.infer(...)[: n.stop.exec_steps]``).

    ``state`` is a per-attempt dict owned by the runner (create it with
    :meth:`begin`).
    """

    name = "vla"

    def __init__(self, backend: PolicyBackend, params: Mapping[str, float] | None = None,
                 root: str | Path | None = None, default_prompt: str = ""):
        """``backend``: any :class:`PolicyBackend`; ``params``: frozen thresholds
        (default: loaded from ``frozen/params``); ``default_prompt``: used when a
        node has an empty instruction (design §3.3)."""
        self.backend = backend
        self.params = dict(params) if params is not None else load_exec_params(root)
        self.default_prompt = default_prompt

    # -- per-attempt state ----------------------------------------------------
    def begin(self, node: Node, frame: ObsFrame) -> dict[str, Any]:
        """New attempt state for ``node`` starting at ``frame`` (design §3.3)."""
        return {
            "start": frame,
            "prev": None,
            "n_chunks": 0,
            "stall": 0,
            "gripper": gripper_stop_spec(node, self.params),
        }

    def stall_distance(self) -> float:
        """Stall distance in metres (see module docstring, rule 1)."""
        return float(self.params.get("stall.ee_delta_max_m", self.params["ee_move.ik_progress_min_m"]))

    # -- stop rule --------------------------------------------------------------
    def stop_reason(self, node: Node, frame: ObsFrame, state: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        """Evaluate the stop rule on ``frame`` (design §3.3 ``node_done``).

        Returns ``(reason | None, info)`` with reason ∈ {stall, gripper,
        max_chunks}. Updates the stall counter in ``state``.
        """
        info: dict[str, Any] = {}
        if state["n_chunks"] == 0 or state["prev"] is None:
            return None, info
        prev: ObsFrame = state["prev"]
        dee = max_ee_translation(prev, frame)
        dq = max_joint_delta(prev, frame)
        stalled = dee < self.stall_distance() and dq < self.params["stall.joint_delta_max_rad"]
        state["stall"] = state["stall"] + 1 if stalled else 0
        info.update({"ee_delta_m": round(dee, 5), "joint_delta_rad": round(dq, 5), "stall": state["stall"]})
        if state["stall"] >= node.stop.stall_chunks:
            return "stall", info
        g = state["gripper"]
        if g is not None:
            for arm in g["arms"]:
                hold = gripper_holding(frame, arm, self.params)
                if not hold:
                    continue
                if g["lift_dz"] is None:
                    info["holding_arm"] = arm
                    return "gripper", info
                dz = float(frame.ee_pose(arm)[2] - state["start"].ee_pose(arm)[2])
                info["lift_dz_m"] = round(dz, 4)
                if dz >= g["lift_dz"]:
                    info["holding_arm"] = arm
                    return "gripper", info
        if state["n_chunks"] >= node.stop.max_chunks:
            return "max_chunks", info
        return None, info

    # -- one chunk --------------------------------------------------------------
    def next_chunk(self, node: Node, frame: ObsFrame, state: dict[str, Any],
                   max_steps: int | None = None) -> ChunkResult:
        """Stop check on ``frame``, else infer and return the first
        ``exec_steps`` (≤ ``max_steps``) joint dicts (design §3.3)."""
        reason, info = self.stop_reason(node, frame, state)
        if reason is not None:
            return ChunkResult(status="done", reason=reason, info=info)
        prompt = node.instruction or self.default_prompt
        chunk = np.asarray(self.backend.infer(frame, prompt), dtype=np.float64)
        if chunk.ndim != 2 or chunk.shape[1] != ACTION_DIM or chunk.shape[0] == 0:
            raise ValueError(f"backend returned chunk of shape {chunk.shape}, expected (H,{ACTION_DIM})")
        if not np.all(np.isfinite(chunk)):
            return ChunkResult(status="blocked", reason="non_finite_actions", info=info)
        n = min(int(node.stop.exec_steps), chunk.shape[0])
        if max_steps is not None:
            n = max(0, min(n, int(max_steps)))
        state["n_chunks"] += 1
        state["prev"] = frame
        info.update({"prompt": prompt, "horizon": int(chunk.shape[0]), "n": n})
        return ChunkResult(actions=chunk_to_joint_actions(chunk, n), status="running", info=info)


def _demo() -> int:
    """Run one vla node on FakeTaskEnv until its stop rule fires (no tests)."""
    from etude.executor.servo_demo import deploy_frame as _frame
    from etude.fakes import FakePolicy, FakeTaskEnv, three_node_sequence

    node = three_node_sequence()[1]
    for label, pol, holding in (
        ("moving policy -> max_chunks", FakePolicy(), None),
        ("stalled policy -> stall", FakePolicy(stall=True), None),
        ("closing policy + object -> gripper", FakePolicy(grip=0.0), {"left": 0.015}),
    ):
        env = FakeTaskEnv(step_lim=400, holding_width=holding)
        ex = VlaExecutor(pol)
        n = node
        if label.startswith("closing"):
            n = Node.from_dict({**node.to_dict(), "postcondition": ["gripper_holding(left)"]})
        frame = _frame(env)
        st = ex.begin(n, frame)
        while True:
            r = ex.next_chunk(n, frame, st)
            if r.terminal:
                print(f"{label}: status={r.status} reason={r.reason} chunks={st['n_chunks']} t={env.fake_t} info={r.info}")
                break
            for a in r.actions:
                env.take_action(a)
            frame = _frame(env)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python -m etude.executor.vla --demo``."""
    ap = argparse.ArgumentParser(prog="python -m etude.executor.vla")
    ap.add_argument("--demo", action="store_true")
    if ap.parse_args(argv).demo:
        return _demo()
    ap.print_help()
    return 0


__all__ = ["VlaExecutor", "gripper_stop_spec"]

if __name__ == "__main__":
    sys.exit(main())
