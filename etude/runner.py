"""NodeRunner: the P2 per-chunk loop (design §2 P2, §3.3 pseudocode, §4, §7, §8.1 items 7/8).

XPolicyLab's forwarder (``deploy.py``) calls, per chunk, ``update_obs(obs)``
then ``get_action()``, and ``update_obs`` again after every executed step
inside the chunk; ``reset()`` starts an episode. :class:`NodeRunner` exposes
exactly those three methods so ``policy/Etude/model.py`` can delegate to it.

One ``get_action`` call (design §3.3)::

    safety check on the per-step joints of the last chunk  -> SafetyStop
    loop:
      no sequence / cursor past the end -> plan()           (1 VLM call, ≤ budget)
      node = seq[cursor]; executor.next_chunk(node, latest)
        running  -> return its actions (vla: first exec_steps rows of the pi0.5 chunk;
                    ee_move: ≤k absolute *_ee_pose; gripper: open/close dicts)
        done     -> verifier.check(node, hist) (an ee_move that arrived despite an IK drop
                    carries the ik_failed flag: postconditions met → Pass, else Blocked)
        blocked  -> Blocked (not arrived) ; lost -> InsufficientEvidence
        verdict  -> advance(): Pass → cursor+1 (record ee_pose anchor)
                                else → evolvable/rules/recovery.yaml decides:
                                  retry_then_fallback: retry this (earliest failing) node in place
                                      ≤ on_fail.retry times, then on_fail.then
                                  reobserve_then_inspect: hold a few steps and re-check once,
                                      then ≤1 injected second opinion, then as Fail
                                  previous_node: ee_move back to the pose recorded when the last
                                      node passed, re-verify that anchor in code (its
                                      postconditions + scene_intact, 0 VLM calls; the failed
                                      and re-run nodes' own objects exempt), then re-attempt
                                      (once per node, then replan); an anchor that no longer
                                      holds is dropped for an earlier one or replan
                                  replan / abort

Budgets: VLM calls (planner + second opinion) ≤ ``budget.vlm_calls_per_episode``;
when exhausted (or the planner fails / returns an invalid sequence) the
runner takes the no-VLM path: the top memory sequence if any, else one vla
node with the episode instruction. Step budget comes from
``obs["additional_info"]["step_lim"]`` (never from episode-end signals);
chunks never run past it.

Planner, verifier, memory and trace are injected through the ABCs in
:mod:`etude.core.interfaces`. The runner never reads episode-end reasons,
official scores or any environment object (design §3.5).
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import inspect as _inspect
import itertools
import math
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from etude.core.config import deep_get, load_recovery_rules
from etude.core.interfaces import MemoryStore, Perceiver, Planner, PolicyBackend, TraceWriter, Verifier
from etude.core.schema import parse_predicate, validate_nodes, validate_verdict
from etude.core.types import (
    ARMS,
    DEFAULT_EXEC_STEPS,
    DEFAULT_MAX_CHUNKS,
    DEFAULT_STALL_CHUNKS,
    EpisodeRecord,
    Node,
    NodeArgs,
    ObsFrame,
    OnFail,
    SequenceEntry,
    StopSpec,
    Verdict,
)
from etude.executor.common import ChunkResult, hold_actions, load_exec_params
from etude.executor.ee_move import EeMoveExecutor, ServoCalib
from etude.executor.gripper import GripperExecutor
from etude.executor.vla import VlaExecutor

#: Recovery actions understood by the runner (values of recovery.yaml ``on_verdict`` / ``rules[].do``).
RECOVERY_ACTIONS: tuple[str, ...] = (
    "retry_then_fallback", "retry", "reobserve_then_inspect", "previous_node", "replan", "abort",
)
#: Used when recovery.yaml lacks an ``on_verdict`` entry (mirrors the shipped file).
DEFAULT_ON_VERDICT: dict[str, str] = {
    "Fail": "retry_then_fallback",
    "InsufficientEvidence": "reobserve_then_inspect",
    "Blocked": "replan",
    "SafetyStop": "previous_node",
}
#: Hold steps used to get a fresh observation before re-checking (design §4 "先再观测").
REOBSERVE_STEPS = 5
#: Retreats to the last verified pose allowed per node before replanning.
MAX_RETREATS_PER_NODE = 1
#: Frames kept per node attempt for the verifier (first frame always kept).
MAX_HIST_FRAMES = 32
#: Guard on internal transitions within one get_action call.
MAX_TRANSITIONS = 64

SecondOpinion = Callable[[Node, Sequence[ObsFrame]], "Verdict | None"]


class NullTrace(TraceWriter):
    """No-op :class:`TraceWriter` used when no trace writer is injected."""

    def start_episode(self, episode_id: str, task: str, instruction: str, meta: dict[str, Any] | None = None) -> None:
        pass

    def keyframe(self, frame: ObsFrame, tag: str) -> list[str]:
        return []

    def node_start(self, node: Node, t: int, chunk_idx: int) -> None:
        pass

    def chunk(self, node_id: str, chunk_idx: int, t: int, n_actions: int, info: dict[str, Any] | None = None) -> None:
        pass

    def verdict(self, node: Node, verdict: Verdict) -> None:
        pass

    def planner_event(self, event: dict[str, Any]) -> None:
        pass

    def end_episode(self, record: EpisodeRecord) -> None:
        pass


@dataclass
class _Attempt:
    """One attempt at one node (design §3.3: retries are new attempts)."""

    node: Node
    number: int
    state: dict[str, Any]
    frames: list[ObsFrame] = field(default_factory=list)
    first_chunk: int | None = None
    last_chunk: int | None = None
    reobserved: bool = False
    inspected: int = 0
    pending: str | None = None  # "verify" | "resume" after a re-observation
    terminal: ChunkResult | None = None


class _PlanJob:
    """One planner call running in a daemon thread (design §2 慢回路: hold, keep planning).

    Carries the episode id, ``len(verdicts)`` and the retreat generation at
    launch: a result taken after any of them changed is stale and is dropped
    and re-requested (Recova 2610.01178 App. A: verdicts carry their round and
    submit step; ones from a finished round or before a scene change are dropped)."""

    def __init__(self, fn: Callable[[dict[str, Any]], Any], req: dict[str, Any], reason: str, t: int,
                 clock: Callable[[], float], *, episode_id: str | None = None, n_verdicts: int = 0,
                 retreat_gen: int = 0, failed_node: Node | None = None, extra: dict[str, Any] | None = None):
        self.reason = reason
        self.t = t
        self.episode_id = episode_id
        self.n_verdicts = n_verdicts
        self.retreat_gen = retreat_gen
        self.failed_node = failed_node
        self.extra = dict(extra or {})
        self.started = clock()
        self.holds = 0
        self.out: Any = None
        self.err: BaseException | None = None
        self.done = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(fn, req), name="etude-plan", daemon=True)
        self._thread.start()

    def _run(self, fn: Callable[[dict[str, Any]], Any], req: dict[str, Any]) -> None:
        try:
            self.out = fn(req)
        except BaseException as e:  # noqa: BLE001 - reported through _finish_plan
            self.err = e
        finally:
            self.done.set()


@dataclass
class _Anchor:
    """ee poses recorded when a node passed (design §7 "上一已验证节点的记录位姿"),
    plus what is needed to re-verify it after a retreat (design §3.3): the
    node, the frame its passing attempt started from, and its place in the
    sequence it belongs to (``index`` -1 = before the first node)."""

    node_id: str
    t: int
    poses: dict[str, np.ndarray]
    node: Node | None = None
    start_frame: ObsFrame | None = None
    seq_id: int = 0
    index: int = -1
    #: objects the node legitimately moved (``verifier.note_pass``); None = not nameable
    moved: frozenset[str] | None = frozenset()


class NodeRunner:
    """Per-chunk node sequencer (design §3.3 ``get_action``).

    Parameters
    ----------
    backend, perceiver, planner, verifier:
        :class:`PolicyBackend`, :class:`Perceiver`, :class:`Planner`,
        :class:`Verifier` implementations.
    memory, trace:
        optional :class:`MemoryStore` (read-only here) and :class:`TraceWriter`.
    task:
        task name passed to memory lookup and the planner (from deploy.yml's
        ``task_name``; never read from the environment).
    root:
        repo root for ``frozen/params`` and ``evolvable/rules`` (default: this repo).
    plan_wait_s:
        if set, planning runs in a background thread and each ``get_action``
        waits at most this long, returning a 1-step hold chunk while P1 is
        still working (design §2 慢回路, §3.3; 90 s in production because the
        official eval client caps every call at 120 s). None = synchronous.
    second_opinion:
        optional ``(node, hist) -> Verdict | None`` used at most
        ``verifier.max_inspect_per_node`` times per node after a re-observed
        InsufficientEvidence; costs one VLM call (design §4).
    """

    def __init__(
        self,
        backend: PolicyBackend,
        perceiver: Perceiver,
        planner: Planner,
        verifier: Verifier,
        memory: MemoryStore | None = None,
        trace: TraceWriter | None = None,
        *,
        task: str = "",
        root: str | Path | None = None,
        calib: ServoCalib | None = None,
        second_opinion: SecondOpinion | None = None,
        episode_prefix: str = "ep",
        clock: Callable[[], float] = time.monotonic,
        plan_wait_s: float | None = None,
        exec_steps: int | None = None,
        fixed_vla_exec_steps: bool = False,
    ):
        self.backend = backend
        self.perceiver = perceiver
        self.planner = planner
        self.verifier = verifier
        self.memory = memory
        self.trace: TraceWriter = trace if trace is not None else NullTrace()
        self.task = task
        self.root = root
        self.second_opinion = second_opinion
        self.episode_prefix = episode_prefix
        self.clock = clock
        #: max seconds one get_action waits for the planner (None = block until it returns).
        #: Design §2 慢回路: 90 s under the official client's fixed 120 s per-call limit.
        self.plan_wait_s = None if plan_wait_s is None else float(plan_wait_s)
        self.params = load_exec_params(root)
        self.recovery = load_recovery_rules(root)
        #: deploy.yml exec_steps: the chunks_left divisor (same as the MCP budget tool), the
        #: no-VLM fallback node's exec_steps and, if ``fixed_vla_exec_steps``, every vla node's
        #: (design §6 control "M1 内 VLA 节点 exec_steps 固定为 N*").
        self.exec_steps = int(exec_steps if exec_steps is not None
                              else (self.recovery.get("defaults") or {}).get("exec_steps", DEFAULT_EXEC_STEPS))
        self.fixed_vla_exec_steps = bool(fixed_vla_exec_steps)
        self.max_vlm_calls = int(self.params["budget.vlm_calls_per_episode"])
        self.max_nodes = int(self.params["budget.max_nodes"])
        self.memory_top_k = int(self.params["budget.memory_top_k"])
        self.max_inspect = int(self.params["verifier.max_inspect_per_node"])
        fb = self.recovery.get("fallback") or {}
        #: recovery.yaml ``fallback.max_backtracks``: retreats to the last verified
        #: node allowed per episode before falling through to replan (design §3.1 surface 2).
        self.max_backtracks = int(fb.get("max_backtracks", 2))
        #: recovery.yaml ``fallback.replan_when_calls_left_below``: with fewer VLM calls
        #: left than this, never call the planner; take the no-VLM path (design §3.5).
        self.min_calls_to_replan = max(1, int(fb.get("replan_when_calls_left_below", 1)))
        self.executors: dict[str, Any] = {
            "vla": VlaExecutor(backend, self.params),
            "ee_move": EeMoveExecutor(perceiver, self.params, calib=calib),
            "gripper": GripperExecutor(self.params),
        }
        self._ep_counter = itertools.count()
        self._open = False
        self._init_episode_state(None, None)

    # ------------------------------------------------------------------
    # episode lifecycle (XPolicyLab Model API)
    # ------------------------------------------------------------------
    def _init_episode_state(self, episode_id: str | None, instruction: str | None) -> None:
        self.episode_id = episode_id
        self.instruction = instruction or ""
        self.latest: ObsFrame | None = None
        self._n_obs = 0
        self._step_joints: list[tuple[int, np.ndarray]] = []
        self.seq: list[Node] | None = None
        self.cursor = 0
        self.attempt: _Attempt | None = None
        self.retries: dict[str, int] = {}
        self.retreats: dict[str, int] = {}
        self.backtracks = 0
        self.anchors: list[_Anchor] = []
        self._seq_id = 0  # incremented per installed sequence (anchors remember theirs)
        self._retreat_gen = 0  # incremented per retreat / anchor re-verification (stale-plan check)
        self.mode = "nodes"  # nodes | reobserve | retreat | halt
        self.halt_reason = ""
        self._retreat: dict[str, Any] | None = None
        self._next_reason = "start"
        self._failed_node: Node | None = None
        self.verdicts: list[Verdict] = []
        self.executed_nodes: list[Node] = []
        self.evidence_refs: list[str] = []
        self.memory_entries: list[SequenceEntry] = []
        self._memory_used = False
        self._attempt_count: dict[str, int] = {}
        self.vlm_calls = 0
        self.vlm_tokens = 0
        self._pending_plan: _PlanJob | None = None  # a stale job from the last episode is dropped
        self.plan_wait_timeouts = 0
        self.chunk_idx = 0
        self.steps_emitted = 0
        self._t0 = self.clock()

    def reset(self, episode_id: str | None = None, instruction: str | None = None) -> None:
        """Close the previous episode (``trace.end_episode``) and clear all
        per-episode state (design §3.5 "reset() 清空回合态")."""
        self.finish()
        for comp in (self.planner, self.backend, self.verifier):
            try:
                fn = getattr(comp, "reset", None)  # Verifier ABC has no reset(); CodeVerifier does
                if callable(fn):
                    fn()
            except Exception as e:  # never let a component reset kill the episode
                self.trace.planner_event({"type": "reset_error", "component": type(comp).__name__, "error": repr(e)})
        self._init_episode_state(episode_id, instruction)

    def finish(self) -> EpisodeRecord | None:
        """Write the :class:`EpisodeRecord` of the open episode (if any) and return it."""
        if not self._open:
            return None
        rec = self.episode_record()
        # cost bookkeeping (design §2 慢回路 "超时次数计入成本")
        self.trace.planner_event({"type": "episode_cost", "vlm_calls": self.vlm_calls, "vlm_tokens": self.vlm_tokens,
                                  "plan_wait_timeouts": self.plan_wait_timeouts, "wall_s": rec.wall_s})
        try:
            self.trace.end_episode(rec)
        finally:
            self._open = False
        return rec

    def episode_record(self) -> EpisodeRecord:
        """Snapshot of the current episode (official_* fields stay None; P5 fills them offline)."""
        return EpisodeRecord(
            episode_id=self.episode_id or "",
            task=self.task,
            instruction=self.instruction,
            nodes=copy.deepcopy(self.executed_nodes),
            verdicts=copy.deepcopy(self.verdicts),
            evidence_refs=list(self.evidence_refs),
            steps=int(self.latest.t + 1) if self.latest is not None else 0,
            chunks=self.chunk_idx,
            vlm_calls=self.vlm_calls,
            vlm_tokens=self.vlm_tokens,
            wall_s=round(self.clock() - self._t0, 3),
        )

    def ensure_episode_id(self) -> str:
        """The open episode's id, assigning ``<prefix>-<timestamp>-<n>`` if none yet
        (the MCP state scopes ``inspect`` refs to it before planning starts)."""
        if self.episode_id is None:
            self.episode_id = f"{self.episode_prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{next(self._ep_counter):03d}"
        return self.episode_id

    def _open_episode(self, frame: ObsFrame) -> None:
        if not self.instruction:
            self.instruction = frame.instruction
        self.ensure_episode_id()
        self.executors["vla"].default_prompt = self.instruction
        self.trace.start_episode(self.episode_id, self.task, self.instruction,
                                 {"step_lim": frame.step_lim, "max_vlm_calls": self.max_vlm_calls})
        self._open = True
        self._tick(frame)
        # arm_home reference = the episode's first observed joints (design §4 谓词 arm_home).
        # CodeVerifier otherwise only learns it from a t == 0 frame inside check(), which
        # never happens when the first attempt ends Blocked or the first check starts later.
        set_home = getattr(self.verifier, "set_home", None)
        if callable(set_home) and getattr(self.verifier, "home", None) is None:
            set_home(frame)
        # scene_intact reference I_0 = the episode's t=0 keyframe (design §4 derived field, P2)
        set_ref = getattr(self.verifier, "set_reference", None)
        if callable(set_ref):
            set_ref(frame)
        self.anchors = [_Anchor("start", frame.t, {a: frame.ee_pose(a).copy() for a in ARMS},
                                start_frame=frame, seq_id=1, index=-1)]
        if self.memory is not None:
            try:
                self.memory_entries = list(self.memory.lookup(self.task, self.instruction, k=self.memory_top_k))
            except Exception as e:
                self.trace.planner_event({"type": "memory_error", "error": repr(e)})
                self.memory_entries = []

    def update_obs(self, obs: Mapping[str, Any] | ObsFrame) -> None:
        """Store the newest observation (called every env step by deploy.py; design §8.2 F5).
        ``frame.t`` is set to the number of earlier ``update_obs`` calls."""
        if isinstance(obs, ObsFrame):
            # the runner's update count is the step clock (deploy.py calls update_obs once per step)
            frame = obs if obs.t == self._n_obs else dataclasses.replace(obs, t=self._n_obs)
        else:
            frame = ObsFrame.from_robodojo_obs(obs, t=self._n_obs)
        self._n_obs += 1
        self.latest = frame
        self._step_joints.append((frame.t, np.concatenate([frame.joints(a) for a in ARMS])))
        if self._open:
            self._tick(frame)

    def _tick(self, frame: ObsFrame) -> None:
        """Periodic keyframe (design §4 存储 "节点边界与每秒 1 帧"): forwarded to
        ``trace.tick(frame)`` when the trace writer has it (FileTraceWriter does)."""
        tick = getattr(self.trace, "tick", None)
        if callable(tick):
            try:
                tick(frame)
            except Exception as e:  # a trace failure never kills the episode
                self.trace.planner_event({"type": "trace_error", "where": "tick", "error": repr(e)})

    # ------------------------------------------------------------------
    # MCP-facing read-only views (design §4 get_verdict / budget / observe)
    # ------------------------------------------------------------------
    def budget(self) -> dict[str, int | None]:
        """``{"calls_left", "chunks_left"}``; chunks from ``step_lim`` at ``exec_steps`` steps/chunk (design §4 ``budget``)."""
        left = self._steps_left()
        return {
            "calls_left": max(0, self.max_vlm_calls - self.vlm_calls),
            "chunks_left": None if left is None else int(math.ceil(left / max(1, self.exec_steps))),
        }

    def get_verdict(self, node_id: str) -> dict[str, Any] | None:
        """Latest verdict dict for ``node_id`` (design §4 ``get_verdict``), or None."""
        for v in reversed(self.verdicts):
            if v.node == node_id:
                return v.to_dict()
        return None

    @property
    def current_node(self) -> Node | None:
        """Node under execution, if any."""
        if self.seq is None or self.cursor >= len(self.seq):
            return None
        return self.seq[self.cursor]

    # ------------------------------------------------------------------
    # main entry
    # ------------------------------------------------------------------
    def get_action(self, obs: Mapping[str, Any] | ObsFrame | None = None) -> list[dict[str, np.ndarray]]:
        """Return the next action chunk (design §3.3 ``get_action``); ``obs`` is
        optional when ``update_obs`` was already called by deploy.py."""
        if obs is not None:
            self.update_obs(obs)
        if self.latest is None:
            raise RuntimeError("NodeRunner.get_action: call update_obs first")
        frame = self.latest
        if not self._open:
            self._open_episode(frame)
        spike = self._joint_speed_spike(frame)
        self._step_joints = [(frame.t, np.concatenate([frame.joints(a) for a in ARMS]))]
        if spike is not None:
            self._on_safety(frame, spike)

        for _ in range(MAX_TRANSITIONS):
            left = self._steps_left(frame)
            if left is not None and left <= 0:
                return self._emit(hold_actions(frame, 1), None, {"halt": "step_budget"})
            if self.mode == "halt":
                n = self.exec_steps if left is None else min(self.exec_steps, left)
                return self._emit(hold_actions(frame, n), None, {"halt": self.halt_reason})
            if self.mode == "reobserve":
                self.mode = "nodes"
                n = REOBSERVE_STEPS if left is None else min(REOBSERVE_STEPS, left)
                nid = self.attempt.node.id if self.attempt else None
                return self._emit(hold_actions(frame, n), nid, {"reobserve": True})
            if self.mode == "retreat":
                out = self._retreat_step(frame, left)
                if out is not None:
                    return out
                continue
            # --- nodes mode ---
            if self._pending_plan is not None:
                out = self._poll_plan(frame)
                if out is not None:
                    return out
                continue
            if self.attempt is not None and self.attempt.pending == "verify":
                self.attempt.pending = None
                self._conclude(self._verify(self.attempt, frame), frame)
                continue
            if self.attempt is not None and self.attempt.pending == "resume":
                self.attempt.pending = None
            if self.seq is None or self.cursor >= len(self.seq):
                self._plan(self._next_reason, frame)
                continue
            node = self.seq[self.cursor]
            if self.attempt is None or self.attempt.node is not node:
                self._start_attempt(node, frame)
            att = self.attempt
            assert att is not None
            self._hist(att, frame)  # keep chunk-boundary frames for the verifier
            ex = self.executors[node.executor]
            r: ChunkResult = ex.next_chunk(node, frame, att.state, max_steps=left)
            if r.status == "running" and r.actions:
                return self._emit(r.actions, node.id, {"executor": node.executor, **_short(r.info)})
            att.terminal = r
            if r.status == "running":  # no actions possible (step budget)
                self._halt("step_budget")
                continue
            self._conclude(self._terminal_verdict(att, r, frame), frame)
        self._halt("transition_guard")
        return self._emit(hold_actions(frame, 1), None, {"halt": "transition_guard"})

    # ------------------------------------------------------------------
    # helpers: emission, budgets, safety
    # ------------------------------------------------------------------
    def _emit(self, actions: list[dict[str, np.ndarray]], node_id: str | None, info: dict[str, Any]) -> list[dict[str, np.ndarray]]:
        idx = self.chunk_idx
        self.chunk_idx += 1
        self.steps_emitted += len(actions)
        if self.attempt is not None and node_id == self.attempt.node.id:
            if self.attempt.first_chunk is None:
                self.attempt.first_chunk = idx
            self.attempt.last_chunk = idx
        t = self.latest.t if self.latest is not None else 0
        self.trace.chunk(node_id or "-", idx, t, len(actions), info)
        # incremental EpisodeRecord: a hard kill (SIGKILL after the last episode) loses at most one chunk
        checkpoint = getattr(self.trace, "checkpoint", None)
        if callable(checkpoint) and self._open:
            try:
                checkpoint(self.episode_record())
            except Exception as e:  # a trace failure never kills the episode
                self.trace.planner_event({"type": "trace_error", "where": "checkpoint", "error": repr(e)})
        return actions

    def _steps_left(self, frame: ObsFrame | None = None) -> int | None:
        f = frame or self.latest
        if f is None or f.step_lim is None:
            return None
        return max(0, int(f.step_lim) - int(f.t))

    def _joint_speed_spike(self, frame: ObsFrame) -> dict[str, float] | None:
        """Max joint speed over consecutive steps since the last chunk vs
        ``safety.joint_vel_max_rad_s`` (design §4 SafetyStop)."""
        hz = float(frame.frequency or 25)
        vmax = 0.0
        for (t0, q0), (t1, q1) in zip(self._step_joints, self._step_joints[1:]):
            if t1 - t0 != 1:
                continue
            vmax = max(vmax, float(np.max(np.abs(q1 - q0))) * hz)
        if vmax > self.params["safety.joint_vel_max_rad_s"]:
            return {"joint_vel_rad_s": round(vmax, 3)}
        return None

    def _on_safety(self, frame: ObsFrame, info: dict[str, float]) -> None:
        self.trace.planner_event({"type": "safety_spike", "t": frame.t, **info})
        if self.mode == "retreat" and self._retreat is not None:
            node = self._retreat["node"]
            self._retreat = None
            self.mode = "nodes"
            self._replan_after(node, frame)
            return
        if self.attempt is not None and self.mode == "nodes":
            att = self.attempt
            att.terminal = ChunkResult(status="safety", reason="joint_velocity", info=dict(info))
            self._conclude(self._make_verdict(att, "SafetyStop", frame), frame)

    def _halt(self, reason: str) -> None:
        if self.mode != "halt":
            self.trace.planner_event({"type": "halt", "reason": reason})
        self.mode = "halt"
        self.halt_reason = reason

    # ------------------------------------------------------------------
    # planning (design §3.3 self.plan(); §3.5 budget row)
    # ------------------------------------------------------------------
    def _plan(self, reason: str, frame: ObsFrame, failed_node: Node | None = None,
              extra: Mapping[str, Any] | None = None) -> None:
        """Ask P1 for a sequence (design §3.3 ``self.plan()``; 1 VLM call). With
        ``plan_wait_s`` set, the call runs in a background thread and
        :meth:`get_action` waits at most ``plan_wait_s`` per call, returning a
        1-step hold chunk meanwhile (design §2 慢回路: the official eval client
        caps every call at 120 s, so the wait is 90 s). ``extra`` keys (e.g.
        ``scene_intact``, ``anchor_invalidated``, ``anchor_recheck`` after an
        invalidated anchor) are added to the request."""
        failed = failed_node or self._failed_node
        if self.max_vlm_calls - self.vlm_calls < self.min_calls_to_replan:
            self._install_plan(None, "no_vlm_budget", reason)
            return
        req = {
            "instruction": self.instruction,
            "task": self.task,
            "reason": reason,
            "history": [v.to_dict() for v in self.verdicts],
            "failed_node": failed.to_dict() if failed is not None else None,
            "budget": self.budget(),
            "memory": [e.to_dict() for e in self.memory_entries],
            **dict(extra or {}),
        }
        self.vlm_calls += 1
        self.trace.planner_event({"type": "plan_request", "t": frame.t, "reason": reason,
                                  "failed_node": req["failed_node"], "budget": req["budget"],
                                  **{k: req[k] for k in ("scene_intact", "anchor_invalidated", "anchor_recheck")
                                     if k in req}})
        if self.plan_wait_s is None:
            try:
                out, err = self.planner.plan(req), None
            except Exception as e:
                out, err = None, e
            self._finish_plan(out, err, reason)
            return
        # background planning: nothing executes until the result is taken in get_action
        self.seq = None
        self.cursor = 0
        self.attempt = None
        self._pending_plan = _PlanJob(self.planner.plan, req, reason, frame.t, self.clock,
                                      episode_id=self.episode_id, n_verdicts=len(self.verdicts),
                                      retreat_gen=self._retreat_gen, failed_node=failed, extra=extra)

    def _poll_plan(self, frame: ObsFrame) -> list[dict[str, np.ndarray]] | None:
        """Wait ≤ ``plan_wait_s`` for the background plan; hold 1 step if still running."""
        job = self._pending_plan
        assert job is not None
        if not job.done.wait(self.plan_wait_s):
            job.holds += 1
            self.plan_wait_timeouts += 1
            self.trace.planner_event({"type": "plan_wait_timeout", "t": frame.t, "reason": job.reason,
                                      "waited_s": round(self.clock() - job.started, 3), "holds": job.holds})
            return self._emit(hold_actions(frame, 1), None, {"plan_pending": True, "holds": job.holds})
        self._pending_plan = None
        stale = [k for k, then, now in (("episode", job.episode_id, self.episode_id),
                                         ("verdicts", job.n_verdicts, len(self.verdicts)),
                                         ("retreat", job.retreat_gen, self._retreat_gen)) if then != now]
        if stale:  # P10: the scene/state the request described is gone; drop and re-request
            self.trace.planner_event({"type": "plan_discarded_stale", "t": frame.t, "reason": job.reason,
                                      "changed": stale, "requested_t": job.t})
            if job.episode_id == self.episode_id:
                self._plan(job.reason, frame, failed_node=job.failed_node, extra=job.extra)
            return None
        self._finish_plan(job.out, job.err, job.reason)
        return None

    def _finish_plan(self, out: Any, err: BaseException | None, reason: str) -> None:
        """Validate the planner answer and install it, or fall back (design §3.5)."""
        nodes: list[Node] | None = None
        source = "planner"
        if err is not None:
            self.trace.planner_event({"type": "plan_error", "error": repr(err)})
            source = "planner_error"
        else:
            try:
                nodes = [n if isinstance(n, Node) else Node.from_dict(n) for n in (out or [])]
            except Exception as e:
                self.trace.planner_event({"type": "plan_error", "error": repr(e)})
                nodes, source = None, "planner_error"
            usage = getattr(self.planner, "last_usage", None)
            if isinstance(usage, Mapping):
                self.vlm_tokens += int(usage.get("total_tokens", 0) or 0)
        if nodes is not None and not nodes:
            if reason == "sequence_done":
                self.trace.planner_event({"type": "plan_response", "n_nodes": 0, "source": "planner"})
                self._halt("planner_finished")
                return
            nodes, source = None, "planner_empty"
        if nodes is not None:
            errs = validate_nodes(nodes)
            if len(nodes) > self.max_nodes:
                errs.append(f"sequence: {len(nodes)} nodes > budget.max_nodes {self.max_nodes}")
            if errs:
                self.trace.planner_event({"type": "plan_invalid", "errors": errs})
                nodes, source = None, "planner_invalid"
        self._install_plan(nodes, source, reason)

    def _install_plan(self, nodes: list[Node] | None, source: str, reason: str) -> None:
        if nodes is None:
            nodes = self._no_vlm_sequence(reason)
            if source == "planner":
                source = "fallback"
        if self.fixed_vla_exec_steps:
            overridden = [n.id for n in nodes if n.executor == "vla" and n.stop.exec_steps != self.exec_steps]
            for n in nodes:
                if n.executor == "vla":
                    n.stop.exec_steps = self.exec_steps
            if overridden:
                self.trace.planner_event({"type": "exec_steps_fixed", "exec_steps": self.exec_steps, "nodes": overridden})
        self.seq = nodes
        self._seq_id += 1
        # every object the plan mentions becomes watchable by scene_intact, and contact
        # nodes without args.target get their implicit object from it (design §4, P2)
        reg_seq = getattr(self.verifier, "register_sequence", None)
        reg = getattr(self.verifier, "register_scene_objects", None)
        try:
            if callable(reg_seq):
                reg_seq(list(nodes))
            elif callable(reg):
                from etude.verifier.predicates import node_objects

                reg(sorted({q for n in nodes for q in node_objects(n)}))
        except Exception as e:
            self.trace.planner_event({"type": "verifier_error", "where": "register_sequence", "error": repr(e)})
        self.cursor = 0
        self.attempt = None
        self.retries = {}
        self.retreats = {}
        self._failed_node = None
        self._next_reason = "sequence_done"
        self.trace.planner_event({"type": "plan_response", "source": source, "n_nodes": len(nodes),
                                  "nodes": [n.to_dict() for n in nodes], "calls_used": self.vlm_calls})

    def _no_vlm_sequence(self, reason: str) -> list[Node]:
        """No-VLM path (design §3.5 "超限节点走无 VLM 路径"): the top memory
        sequence once per episode, else one pure-pi0.5 node on the instruction."""
        if self.memory_entries and not self._memory_used:
            self._memory_used = True
            nodes = copy.deepcopy(self.memory_entries[0].nodes)
            if nodes and not validate_nodes(nodes):
                return nodes
        defaults = self.recovery.get("defaults") or {}
        return [Node(
            id="n1",
            executor="vla",
            instruction=self.instruction,
            args=NodeArgs(),
            stop=StopSpec(stall_chunks=int(defaults.get("stall_chunks", DEFAULT_STALL_CHUNKS)),
                          max_chunks=40, exec_steps=self.exec_steps),
            postcondition=[],
            on_fail=OnFail(retry=0, then="abort"),
            evidence=[],
        )]

    # ------------------------------------------------------------------
    # attempts and verdicts
    # ------------------------------------------------------------------
    def _start_attempt(self, node: Node, frame: ObsFrame) -> None:
        self._attempt_count[node.id] = number = self._attempt_count.get(node.id, 0) + 1
        ex = self.executors.get(node.executor)
        if ex is None:
            raise ValueError(f"unknown executor {node.executor!r}")
        self.attempt = _Attempt(node=node, number=number, state=ex.begin(node, frame), frames=[frame])
        if not any(n is node for n in self.executed_nodes):
            self.executed_nodes.append(node)
        self.trace.node_start(node, frame.t, self.chunk_idx)
        self.evidence_refs.extend(self.trace.keyframe(frame, f"{node.id}_a{number}_before"))

    def _hist(self, att: _Attempt, frame: ObsFrame) -> list[ObsFrame]:
        frames = att.frames + ([frame] if att.frames[-1] is not frame else [])
        if len(frames) > MAX_HIST_FRAMES:
            frames = frames[:1] + frames[-(MAX_HIST_FRAMES - 1):]
        att.frames = frames
        return list(frames)

    def _span(self, att: _Attempt) -> list[int]:
        a = att.first_chunk if att.first_chunk is not None else max(0, self.chunk_idx - 1)
        b = att.last_chunk if att.last_chunk is not None else a
        return [int(a), int(max(a, b))]

    def _make_verdict(self, att: _Attempt, kind: str, frame: ObsFrame | None = None) -> Verdict:
        v = Verdict(node=att.node.id, kind=kind, predicates={}, evidence_refs=[], source="code_verifier",
                    t_chunk=self._span(att))
        if kind != "Pass" and frame is not None:
            v.scene_intact = self._scene(frame, att.node)
        return v

    def _scene(self, frame: ObsFrame, node: Node | None, as_of_t: int | None = None, **kw: Any) -> bool | None:
        """Derived ``scene_intact`` from the verifier (design §4, P2); None if unsupported.
        ``kw`` (``pending``, ``keep``) is passed only to a verifier that accepts it."""
        fn = getattr(self.verifier, "scene_intact", None)
        if not callable(fn):
            return None
        try:
            if kw:
                params = _inspect.signature(fn).parameters
                kw = {k: v for k, v in kw.items() if k in params}
            return fn(frame, node, as_of_t=as_of_t, **kw)
        except Exception as e:  # a scene check failure never kills the episode
            self.trace.planner_event({"type": "verifier_error", "where": "scene_intact", "error": repr(e)})
            return None

    def _verify(self, att: _Attempt, frame: ObsFrame) -> Verdict:
        """Call the injected verifier on the attempt's history (design §4)."""
        hist = self._hist(att, frame)
        try:
            v = self.verifier.check(att.node, hist)
        except Exception as e:
            self.trace.planner_event({"type": "verifier_error", "node": att.node.id, "error": repr(e)})
            v = self._make_verdict(att, "InsufficientEvidence", frame)
        v.t_chunk = self._span(att)
        return v

    def _terminal_verdict(self, att: _Attempt, r: ChunkResult, frame: ObsFrame) -> Verdict:
        """Map an executor terminal status to a verdict (design §4 semantics).

        Completion precedence (design §4, P3): an ee_move that arrived although
        its last chunk was not followed (IK drop) ends ``done`` with
        ``info["ik_failed"]``; the flag is written into the last frame's
        harness info so the verifier records it as evidence on a Pass and
        turns an unmet postcondition into Blocked. ``blocked`` means the node
        did not complete (not arrived), so it is Blocked without a check."""
        self._hist(att, frame)
        if r.status == "done":
            ik = r.info.get("ik_failed") if isinstance(r.info, Mapping) else None
            if ik:
                arm = ik.get("arm") if isinstance(ik, Mapping) else None
                info = dict(frame.additional_info)
                harness = dict(info.get("harness") or {}) if isinstance(info.get("harness"), Mapping) else {}
                harness["ik_failed"] = {arm: True} if arm in ARMS else True
                info["harness"] = harness
                flagged = dataclasses.replace(frame, additional_info=info)
                att.frames[-1] = flagged
                return self._verify(att, flagged)
            return self._verify(att, frame)
        if r.status == "blocked":
            return self._make_verdict(att, "Blocked", frame)
        if r.status == "lost":
            return self._make_verdict(att, "InsufficientEvidence", frame)
        raise ValueError(f"unexpected executor status {r.status!r}")

    def _conclude(self, v: Verdict, frame: ObsFrame) -> None:
        """Record ``v`` (trace, keyframe) and advance (design §3.3 ``advance``)."""
        att = self.attempt
        assert att is not None
        node = att.node
        refs = self.trace.keyframe(frame, f"{node.id}_a{att.number}_after")
        self.evidence_refs.extend(refs)
        if not v.evidence_refs:
            v.evidence_refs = list(refs)
        errs = validate_verdict(v)
        if errs:
            self.trace.planner_event({"type": "verdict_invalid", "node": node.id, "errors": errs})
        self.trace.verdict(node, v)
        self.verdicts.append(v)
        info = {"type": "verdict", "node": node.id, "kind": v.kind, "attempt": att.number,
                "reason": att.terminal.reason if att.terminal else ""}
        self.trace.planner_event(info)
        self._advance(node, v, frame)

    def _advance(self, node: Node, v: Verdict, frame: ObsFrame) -> None:
        if v.kind == "Pass":
            att = self.attempt
            anchor = _Anchor(node.id, frame.t, {a: frame.ee_pose(a).copy() for a in ARMS}, node=node,
                             start_frame=att.frames[0] if att is not None and att.frames else None,
                             seq_id=self._seq_id, index=self.cursor)
            note = getattr(self.verifier, "note_pass", None)
            if callable(note):
                moved = note(node, frame.t)
                if moved is None or isinstance(moved, (set, frozenset)):
                    anchor.moved = None if moved is None else frozenset(moved)
            self.anchors.append(anchor)
            self.retries.pop(node.id, None)
            self.cursor += 1
            self.attempt = None
            return
        action = self.recovery_action(node, v)
        self._dispatch(action, node, v, frame)

    def recovery_action(self, node: Node, v: Verdict) -> str:
        """Recovery action for verdict ``v`` from ``evolvable/rules/recovery.yaml``
        (design §3.1 harness surface 2): first matching ``rules[]`` entry, else
        ``on_verdict[kind]``. Rule form::

            {when: {kind: Fail, executor: vla, attempts_ge: 1, predicate_false: gripper_holding,
                    reason: stall}, do: replan}

        (all ``when`` keys optional; ``do`` is an action name or ``{action: ...}``).
        """
        att = self.attempt
        fails = self.retries.get(node.id, 0)
        reason = att.terminal.reason if att is not None and att.terminal is not None else ""
        false_preds = set()
        for expr, pr in v.predicates.items():
            if pr.v is False:
                try:
                    false_preds.add(parse_predicate(expr)[0])
                except ValueError:
                    false_preds.add(expr)
        for rule in self.recovery.get("rules") or []:
            if not isinstance(rule, Mapping):
                continue
            when = rule.get("when") or {}
            if "kind" in when and when["kind"] != v.kind:
                continue
            if "executor" in when and when["executor"] != node.executor:
                continue
            if "attempts_ge" in when and fails < int(when["attempts_ge"]):
                continue
            if "predicate_false" in when and when["predicate_false"] not in false_preds:
                continue
            if "reason" in when and when["reason"] != reason:
                continue
            do = rule.get("do")
            act = do.get("action") if isinstance(do, Mapping) else do
            if act in RECOVERY_ACTIONS:
                return str(act)
        act = deep_get(self.recovery, f"on_verdict.{v.kind}", DEFAULT_ON_VERDICT.get(v.kind, "retry_then_fallback"))
        return str(act) if act in RECOVERY_ACTIONS else "retry_then_fallback"

    def _dispatch(self, action: str, node: Node, v: Verdict, frame: ObsFrame) -> None:
        att = self.attempt
        self.trace.planner_event({"type": "recovery", "node": node.id, "kind": v.kind, "action": action})
        if action == "reobserve_then_inspect":
            if att is not None and not att.reobserved:
                att.reobserved = True
                att.pending = "verify" if att.terminal is not None and att.terminal.status == "done" else "resume"
                self.mode = "reobserve"
                return
            if att is not None and self.second_opinion is not None and att.inspected < self.max_inspect \
                    and self.vlm_calls < self.max_vlm_calls:
                att.inspected += 1
                self.vlm_calls += 1
                v2 = None
                try:
                    v2 = self.second_opinion(node, self._hist(att, frame))
                except Exception as e:
                    self.trace.planner_event({"type": "inspect_error", "node": node.id, "error": repr(e)})
                if v2 is not None:
                    v2.source = "inspect_second_opinion"
                    v2.t_chunk = self._span(att)
                    if v2.kind != "InsufficientEvidence":
                        self._conclude(v2, frame)
                        return
            action = "retry_then_fallback"
        if action in ("retry_then_fallback", "retry"):
            fails = self.retries.get(node.id, 0) + 1
            if fails <= node.on_fail.retry:
                self.retries[node.id] = fails
                self.attempt = None  # same cursor: retry only this (earliest failing) node
                return
            action = node.on_fail.then
        if action == "previous_node":
            if self.retreats.get(node.id, 0) < MAX_RETREATS_PER_NODE and self.backtracks < self.max_backtracks \
                    and self.anchors:
                anchor = self.anchors[-1]
                self.retreats[node.id] = self.retreats.get(node.id, 0) + 1
                self.backtracks += 1
                self.retries[node.id] = 0
                self._start_retreat(node, anchor, frame, rewind_to=None, chain=[])
                return
            action = "replan"
        if action == "replan":
            self._replan_after(node, frame)
            return
        if action == "abort":
            self.attempt = None
            self._halt(f"abort:{node.id}")
            return
        self._replan_after(node, frame)

    def _replan_after(self, node: Node, frame: ObsFrame, extra: Mapping[str, Any] | None = None) -> None:
        self.attempt = None
        self._failed_node = node
        self._plan("node_failed", frame, failed_node=node, extra=extra)

    def _start_retreat(self, node: Node, anchor: _Anchor, frame: ObsFrame, *, rewind_to: int | None,
                       chain: list[dict[str, Any]]) -> None:
        """ee_move back to ``anchor``'s recorded poses (design §7); on arrival the
        anchor is re-verified (:meth:`_after_retreat`). ``rewind_to`` = cursor to
        resume from (None: retry the failed node at the current cursor)."""
        ee = self.executors["ee_move"]
        self._retreat_gen += 1
        self._retreat = {"node": node, "anchor": anchor, "state": ee.begin(None, frame),
                         "rewind_to": rewind_to, "chain": chain}
        self.attempt = None
        self.mode = "retreat"
        ev: dict[str, Any] = {"type": "retreat", "node": node.id, "to": anchor.node_id}
        if chain:
            ev["after_invalidated"] = [c["anchor"] for c in chain]
        self.trace.planner_event(ev)

    def _recheck_anchor(self, anchor: _Anchor, frame: ObsFrame, pending: Sequence[Node] = ()) -> dict[str, Any]:
        """Re-verify ``anchor`` in code after the retreat (design §3.3 退回后复验; Recova
        2610.01178 §3.3 Verify, Alg. 1 lines 41-42): its node's postconditions via
        ``verifier.check`` on ``[start_frame, now]`` without second opinions, and
        ``scene_intact`` as of the anchor's pass. 0 VLM calls. Arm-state
        predicates (``arm_home``) are skipped. A node without remaining
        postconditions (or the episode start) is judged on scene_intact alone;
        scene_intact None (undecidable) does not invalidate.

        ``pending`` = the failed node plus every node the resume will re-run:
        objects they are meant to move are exempt from scene_intact (their
        legitimate moves are no disturbance; they re-locate their targets when
        they run), except the objects the anchor state itself depends on (the
        anchor node's mentioned-but-not-moved objects, e.g. its ``ee_move``
        target), which must still be where they were."""
        from etude.verifier.predicates import ARM_STATE_PREDICATES, node_objects

        post_kind: str | None = None
        post: dict[str, Any] | None = None
        skipped: list[str] = []
        node = anchor.node
        if node is not None:
            # arm-state predicates (arm_home) are not scene facts: the retreat's arrival
            # (ee pose within ee_move.pos_tol_m) covers the arm, and an ee-space retreat
            # does not restore joint-space home
            keep = []
            for expr in node.postcondition:
                try:
                    name = parse_predicate(expr)[0]
                except ValueError:
                    name = expr
                (skipped if name in ARM_STATE_PREDICATES else keep).append(expr)
            node = dataclasses.replace(node, postcondition=keep)
        if node is not None and node.postcondition:
            hist = [f for f in (anchor.start_frame, frame) if f is not None]
            try:
                params = _inspect.signature(self.verifier.check).parameters
                if "recheck" in params:
                    v = self.verifier.check(node, hist, recheck=True, as_of_t=anchor.t)
                else:  # a verifier without the flag (fakes): plain check
                    v = self.verifier.check(node, hist)
            except Exception as e:
                self.trace.planner_event({"type": "verifier_error", "where": "anchor_recheck", "error": repr(e)})
                v = Verdict(node=node.id, kind="InsufficientEvidence", source="code_verifier")
            post_kind, post = v.kind, v.to_dict()
        keep: set[str] = set()
        if anchor.node is not None:
            keep = node_objects(anchor.node) - set(anchor.moved or ())
        scene = self._scene(frame, None, as_of_t=anchor.t, pending=list(pending), keep=sorted(keep))
        detail = getattr(self.verifier, "last_scene", None)
        moved = sorted(q for q, r in ((detail or {}).get("objects") or {}).items() if r.get("v") is False)
        return {"anchor": anchor.node_id, "anchor_t": anchor.t, "t": frame.t, "postcondition": post_kind,
                "verdict": post, "skipped_arm_state": skipped, "scene_intact": scene, "moved_objects": moved,
                "exempt_pending": [n.id for n in pending], "kept": sorted(keep),
                "scene_undecidable": (detail or {}).get("undecidable"),
                "ok": post_kind in (None, "Pass") and scene is not False, "vlm_calls": 0}

    def _after_retreat(self, rt: dict[str, Any], frame: ObsFrame) -> None:
        """Retreat arrived: retry the failed node only if the anchor still holds;
        otherwise drop it (``anchors.pop()``) and fall back to an earlier anchor of
        the current sequence that still holds, or replan with ``scene_intact=false``
        (design §3.3; Recova 2610.01178 App. A: verdicts older than a scene change
        are dropped)."""
        node: Node = rt["node"]
        anchor: _Anchor = rt["anchor"]
        self._retreat_gen += 1
        res = self._recheck_anchor(anchor, frame, self._pending_nodes(node, rt["rewind_to"]))
        chain: list[dict[str, Any]] = rt["chain"] + [res]
        self.trace.planner_event({"type": "anchor_recheck", "node": node.id, **res})
        if res["ok"]:
            if rt["rewind_to"] is not None:
                self.cursor = int(rt["rewind_to"])
            self.attempt = None
            return
        if self.anchors and self.anchors[-1] is anchor:
            self.anchors.pop()
        else:
            self.anchors = [a for a in self.anchors if a is not anchor]
        if res["scene_intact"] is False:
            # objects moved since I_0 stay moved for every earlier anchor (fewer targets
            # by then): none of them holds any more
            dropped = [a.node_id for a in self.anchors]
            self.anchors.clear()
            if dropped:
                self.trace.planner_event({"type": "anchors_dropped", "node": node.id, "anchors": dropped,
                                          "why": "scene_intact=false"})
        else:
            earlier = self.anchors[-1] if self.anchors else None
            if earlier is not None and earlier.seq_id == self._seq_id and self.backtracks < self.max_backtracks:
                self.backtracks += 1
                self._start_retreat(node, earlier, frame, rewind_to=earlier.index + 1, chain=chain)
                return
        summary = [{k: c[k] for k in ("anchor", "postcondition", "scene_intact", "moved_objects")} for c in chain]
        # the request carries the measured scene_intact (True / False / None): an anchor
        # whose postcondition no longer holds is not a disturbed scene (P2/P5)
        if res["scene_intact"] is False:
            # P1 replans from the current scene: it is the new scene_intact reference, and
            # a fresh anchor at the current pose opens the new sequence
            set_ref = getattr(self.verifier, "set_reference", None)
            if callable(set_ref):
                set_ref(frame)
            self.anchors = [_Anchor("replan", frame.t, {a: frame.ee_pose(a).copy() for a in ARMS},
                                    start_frame=frame, seq_id=self._seq_id + 1, index=-1)]
        self._replan_after(node, frame, extra={"scene_intact": res["scene_intact"], "anchor_invalidated": True,
                                               "anchor_recheck": summary})

    def _pending_nodes(self, failed: Node, rewind_to: int | None) -> list[Node]:
        """Nodes a resume after a retreat will (re-)run: the failed node, and with
        ``rewind_to`` every node of the current sequence from there up to it."""
        out: list[Node] = []
        if rewind_to is not None and self.seq is not None:
            out = list(self.seq[int(rewind_to):self.cursor + 1])
        if not any(n is failed for n in out):
            out.append(failed)
        return out

    def _retreat_step(self, frame: ObsFrame, left: int | None) -> list[dict[str, np.ndarray]] | None:
        """One ee_move chunk back to the last verified pose (design §7)."""
        rt = self._retreat
        assert rt is not None
        ee: EeMoveExecutor = self.executors["ee_move"]
        k = int(self.params["ee_move.max_actions_per_chunk"])
        if left is not None:
            k = max(1, min(k, left))
        r = ee.next_chunk_to_pose(frame, rt["state"], rt["anchor"].poses, max_chunks=DEFAULT_MAX_CHUNKS, k=k)
        if r.status == "running" and r.actions:
            return self._emit(r.actions, rt["node"].id, {"retreat": True, "to": rt["anchor"].node_id, **_short(r.info)})
        self._retreat = None
        self.mode = "nodes"
        self.trace.planner_event({"type": "retreat_end", "node": rt["node"].id, "status": r.status, "reason": r.reason})
        if r.status != "done":
            self._replan_after(rt["node"], frame)
        else:
            self._after_retreat(rt, frame)
        return None


def _short(info: Mapping[str, Any]) -> dict[str, Any]:
    """Drop bulky entries from executor info before tracing."""
    return {k: v for k, v in info.items() if k not in ("det",)}


# ---------------------------------------------------------------------------
# demo: XPolicyLab-style loop on fakes
# ---------------------------------------------------------------------------


def run_fake_episode(runner: NodeRunner, env: Any) -> None:
    """Mirror of XPolicyLab ``demo_policy/deploy.py::eval_one_episode`` plus our
    two-line augmentation (design §2), on a FakeTaskEnv."""
    from etude.executor.servo_demo import augment_obs

    runner.reset()
    while not env.is_episode_end():
        runner.update_obs(augment_obs(env, env.get_obs()))
        actions = runner.get_action()
        for i, a in enumerate(actions):
            env.take_action(a)
            if env.is_episode_end() or i + 1 == len(actions):
                break
            runner.update_obs(augment_obs(env, env.get_obs()))
    runner.finish()


def _demo() -> int:
    """Four scenarios on fakes; prints the verdict sequence of each (no tests)."""
    from etude.executor.servo_demo import ServoDemoEnv
    from etude.fakes import FakePerceiver, FakePlanner, FakePolicy, FakeTraceWriter, FakeVerifier

    class _DonePlanner(FakePlanner):
        """FakePlanner that returns [] once the sequence is done."""

        def plan(self, request: dict[str, Any]) -> list[Node]:
            if request.get("reason") == "sequence_done":
                self.requests.append(copy.deepcopy(request))
                return []
            return super().plan(request)

    scenarios = [
        ("all pass", dict(kinds=[]), dict()),
        ("n2 fails 3x -> retry x2 -> retreat to n1 pose -> pass", dict(kinds=["Pass", "Fail", "Fail", "Fail"]), dict()),
        ("n1 InsufficientEvidence -> re-observe -> pass", dict(kinds=["InsufficientEvidence"]), dict()),
        ("left IK fails -> Blocked -> replan until VLM budget -> no-VLM path", dict(kinds=[]),
         dict(ik_fail_arms=("left",))),
        ("joint-speed spike in vla -> SafetyStop -> retreat -> replan", dict(kinds=[]), dict(policy_amp=8.0)),
    ]
    ok = True
    for title, vkw, ekw in scenarios:
        ekw = dict(ekw)
        amp = ekw.pop("policy_amp", 0.2)
        env = ServoDemoEnv(step_lim=800, holding_width={"left": 0.015}, **ekw)
        trace = FakeTraceWriter()
        planner = _DonePlanner()
        runner = NodeRunner(FakePolicy(amp=amp, grip=0.0), FakePerceiver(), planner, FakeVerifier(**vkw),
                            trace=trace, task="demo_task")
        run_fake_episode(runner, env)
        rec = trace.record
        seq = " ".join(f"{v.node}:{v.kind}{'*' if v.source != 'code_verifier' else ''}" for v in rec.verdicts[:14])
        if len(rec.verdicts) > 14:
            seq += f" ... ({len(rec.verdicts)} verdicts)"
        retreats = [p for k, p in trace.events if k == "planner" and p.get("type") in ("retreat", "retreat_end")]
        halts = [p.get("reason") for k, p in trace.events if k == "planner" and p.get("type") == "halt"]
        sources = [p.get("source") for k, p in trace.events if k == "planner" and p.get("type") == "plan_response"]
        print(f"[{title}]")
        print(f"  verdicts: {seq}")
        print(f"  steps={rec.steps} chunks={rec.chunks} vlm_calls={rec.vlm_calls} planner_requests="
              f"{[r['reason'] for r in planner.requests]}")
        if retreats:
            ends = [p.get("status") for p in retreats if p["type"] == "retreat_end"]
            print(f"  retreats to last verified pose: {len(ends)} (ended: {sorted(set(ends))})")
        print(f"  plan sources: {sources}")
        print(f"  halt: {halts}  env violations: {env.fake_violations}")
        bad = [e for v in rec.verdicts for e in validate_verdict(v)]
        if bad or env.fake_violations:
            ok = False
            print("  PROBLEM:", bad, env.fake_violations)
    # design §6 control "M1 内 VLA 节点 exec_steps 固定为 N*": deploy.yml exec_steps=N + vla_exec_steps_fixed
    env = ServoDemoEnv(step_lim=400, holding_width={"left": 0.015})
    trace = FakeTraceWriter()
    runner = NodeRunner(FakePolicy(amp=0.2, grip=0.0), FakePerceiver(), _DonePlanner(), FakeVerifier(),
                        trace=trace, task="demo_task", exec_steps=10, fixed_vla_exec_steps=True)
    run_fake_episode(runner, env)
    vla_steps = sorted({n.stop.exec_steps for n in trace.record.nodes if n.executor == "vla"})
    fixed_ev = [p for k, p in trace.events if k == "planner" and p.get("type") == "exec_steps_fixed"]
    print(f"[vla exec_steps fixed to N=10] vla nodes run with exec_steps={vla_steps}; overrides logged={len(fixed_ev)}; "
          f"budget divisor={runner.exec_steps}")
    if vla_steps != [10]:
        ok = False
        print("  PROBLEM: vla exec_steps not fixed")
    print("demo ok" if ok else "demo had problems")
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python -m etude.runner --demo``."""
    ap = argparse.ArgumentParser(prog="python -m etude.runner", description="NodeRunner (design §3.3)")
    ap.add_argument("--demo", action="store_true", help="run 3-node episodes on fakes and print verdicts")
    if ap.parse_args(argv).demo:
        return _demo()
    ap.print_help()
    return 0


__all__ = ["NodeRunner", "NullTrace", "RECOVERY_ACTIONS", "DEFAULT_ON_VERDICT", "run_fake_episode"]

if __name__ == "__main__":
    sys.exit(main())
