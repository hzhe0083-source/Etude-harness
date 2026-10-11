"""Shared backend state behind the 7 MCP tools (design §2 P2, §4 MCP 工具表).

The P2 runner (``deploy/XPolicyLab/policy/Etude/model.py`` via ``etude.adapter``)
owns one :class:`HarnessState` per server process and injects into it:

* the latest observation (:meth:`HarnessState.update_obs`) and named keyframes
  (:meth:`HarnessState.add_keyframe`) — lossless, for ``observe``/``inspect``;
* a :class:`~etude.core.interfaces.Perceiver` (``perceive``) and a read-only
  :class:`~etude.core.interfaces.MemoryStore` (``memory_lookup``);
* node verdicts (:meth:`HarnessState.set_verdict`) for ``get_verdict``;
* the VLM-call count (:meth:`HarnessState.begin_plan`) for ``budget``.

The MCP server thread reads it and stores the sequence accepted by
``submit_sequence``; :class:`etude.planner.rpc_driver.PiRpcPlanner` collects it
after ``agent_settled``. All public methods are thread-safe (one lock).

Nothing here exposes episode-end reasons, official success or env handles
(design §3.5 last row); the state only ever sees :class:`ObsFrame` copies.
"""
from __future__ import annotations

import copy
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from etude import REPO_ROOT, TRACES_DIR
from etude.core.config import deep_get, load_recovery_rules, load_thresholds
from etude.core.interfaces import MemoryStore, Perceiver, TraceWriter
from etude.core.types import (
    DEFAULT_EXEC_STEPS,
    MAX_VLM_CALLS_PER_EPISODE,
    Node,
    ObsFrame,
    Verdict,
)

#: Keyframes kept in memory for ``inspect`` (oldest dropped first).
DEFAULT_MAX_KEYFRAMES = 64


@dataclass
class ToolCallRecord:
    """One MCP tool invocation as seen by the server (design §3.5 调用计数)."""

    tool: str
    ok: bool
    t_wall: float
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "ok": self.ok, "t_wall": self.t_wall, **self.summary}


class HarnessState:
    """Thread-safe backend for the MCP tools (design §4 MCP 工具表).

    Parameters
    ----------
    perceiver, memory, trace:
        Injected components (any may be None; the matching tool then returns
        an ``error`` field instead of raising).
    task, instruction:
        Current episode's task name and language instruction.
    max_vlm_calls:
        Per-episode VLM call budget (frozen ``budget.vlm_calls_per_episode``,
        design §3.5 "P2 计数硬停 12 次/回合").
    exec_steps:
        Steps per chunk used to turn remaining steps into ``chunks_left``
        (``evolvable/rules/recovery.yaml`` ``defaults.exec_steps``).
    root:
        Repo root for frozen/evolvable config (demo: a tmp repo).
    traces_root:
        Directory that trace refs (``<episode_id>/keyframes/…png``) are relative
        to. ``inspect`` may only open PNGs under
        ``traces_root/<current episode_id>/keyframes/`` (design §3.5
        "跨回合泄漏"); the episode id is set by the runner (:meth:`set_episode_id`).
    """

    def __init__(
        self,
        *,
        perceiver: Perceiver | None = None,
        memory: MemoryStore | None = None,
        trace: TraceWriter | None = None,
        task: str = "",
        instruction: str = "",
        max_vlm_calls: int | None = None,
        exec_steps: int | None = None,
        root: str | Path | None = None,
        traces_root: str | Path | None = None,
        max_keyframes: int = DEFAULT_MAX_KEYFRAMES,
    ):
        self.root = Path(root) if root is not None else REPO_ROOT
        thr = load_thresholds(self.root)
        rules = load_recovery_rules(self.root)
        self.thresholds: dict[str, float] = thr
        self.perceiver = perceiver
        self.memory = memory
        self.trace = trace
        self.task = task
        self.instruction = instruction
        self.max_vlm_calls = int(
            max_vlm_calls if max_vlm_calls is not None else thr.get("budget.vlm_calls_per_episode", MAX_VLM_CALLS_PER_EPISODE)
        )
        #: frozen budget.vlm_requests_per_episode: assistant turns (LLM requests) across all
        #: planning calls of one episode (design §3.5 "规划器循环 inspect").
        self.max_vlm_requests = int(thr.get("budget.vlm_requests_per_episode", 4 * self.max_vlm_calls))
        self.exec_steps = int(
            exec_steps if exec_steps is not None else deep_get(rules, "defaults.exec_steps", DEFAULT_EXEC_STEPS)
        )
        self.traces_root = Path(traces_root) if traces_root is not None else TRACES_DIR
        self.max_keyframes = int(max_keyframes)

        self._lock = threading.RLock()
        self._latest: ObsFrame | None = None
        self._keyframes: OrderedDict[str, ObsFrame] = OrderedDict()
        self._verdicts: dict[str, Verdict] = {}
        self._verdict_order: list[str] = []
        self._submitted: list[Node] | None = None
        self._submit_attempts = 0
        self._submit_event = threading.Event()
        self._vlm_calls_used = 0
        self._vlm_requests_used = 0
        self._chunks_used = 0
        self._tool_log: list[ToolCallRecord] = []
        self._plan_idx = 0
        self.episode_id: str | None = None

    # ------------------------------------------------------------------
    # runner-side injection (design §2 P2)
    # ------------------------------------------------------------------
    def start_episode(self, task: str, instruction: str, episode_id: str | None = None) -> None:
        """Reset all per-episode state (design §3.5 "reset() 清空回合态").
        ``episode_id`` scopes ``inspect`` PNG refs to this episode's keyframes."""
        with self._lock:
            self.episode_id = episode_id
            self.task = task
            self.instruction = instruction
            self._latest = None
            self._keyframes.clear()
            self._verdicts.clear()
            self._verdict_order.clear()
            self._submitted = None
            self._submit_attempts = 0
            self._submit_event.clear()
            self._vlm_calls_used = 0
            self._vlm_requests_used = 0
            self._chunks_used = 0
            self._tool_log.clear()
            self._plan_idx = 0

    def set_episode_id(self, episode_id: str | None) -> None:
        """Current episode id (set by the runner before planning; design §3.5 跨回合泄漏)."""
        with self._lock:
            self.episode_id = episode_id

    def keyframe_dir(self) -> Path | None:
        """``traces_root/<episode_id>/keyframes`` of the current episode, or None."""
        from etude.trace.writer import KEYFRAME_DIR, safe_name

        with self._lock:
            ep = self.episode_id
        return None if not ep else self.traces_root / safe_name(ep) / KEYFRAME_DIR

    def update_obs(self, frame: ObsFrame) -> None:
        """Set the latest observation (called on every ``update_obs``; design §8.2 F5)."""
        with self._lock:
            self._latest = frame
            if not self.instruction and frame.instruction:
                self.instruction = frame.instruction

    def add_keyframe(self, tag: str, frame: ObsFrame | None = None) -> str:
        """Register a lossless keyframe under ``tag`` for ``observe(frame=tag)``
        and ``inspect(frame_ref=tag)`` (design §4 inspect "历史帧无损存").
        ``frame=None`` snapshots the latest observation. Returns ``tag``."""
        with self._lock:
            f = frame if frame is not None else self._latest
            if f is None:
                raise ValueError("add_keyframe: no frame and no latest observation")
            self._keyframes[tag] = f
            self._keyframes.move_to_end(tag)
            while len(self._keyframes) > self.max_keyframes:
                self._keyframes.popitem(last=False)
            return tag

    def set_verdict(self, verdict: Verdict) -> None:
        """Store the latest verdict for ``verdict.node`` (design §4 get_verdict)."""
        with self._lock:
            self._verdicts[verdict.node] = verdict
            self._verdict_order.append(verdict.node)

    def note_chunks(self, n: int = 1) -> None:
        """Count emitted action chunks (fallback for ``chunks_left`` when no ``step_lim``)."""
        with self._lock:
            self._chunks_used += int(n)

    def begin_plan(self) -> int:
        """Start one planner invocation: count one VLM call (design §3.3 "计 1 次调用")
        and clear the previous submission. Returns the 1-based plan index."""
        with self._lock:
            self._vlm_calls_used += 1
            self._plan_idx += 1
            self._submitted = None
            self._submit_attempts = 0
            self._submit_event.clear()
            return self._plan_idx

    # ------------------------------------------------------------------
    # planner-side results
    # ------------------------------------------------------------------
    def accept_sequence(self, nodes: list[Node]) -> None:
        """Store a validated sequence (called by ``submit_sequence`` on ok)."""
        with self._lock:
            self._submitted = copy.deepcopy(nodes)
            self._submit_event.set()

    def note_submit_attempt(self) -> int:
        with self._lock:
            self._submit_attempts += 1
            return self._submit_attempts

    def take_submitted(self) -> list[Node] | None:
        """Return (a copy of) the last accepted sequence of this plan call, or None."""
        with self._lock:
            return copy.deepcopy(self._submitted)

    def wait_submitted(self, timeout: float | None = None) -> bool:
        """Block until ``submit_sequence`` accepted a sequence (or timeout)."""
        return self._submit_event.wait(timeout)

    @property
    def submit_attempts(self) -> int:
        with self._lock:
            return self._submit_attempts

    # ------------------------------------------------------------------
    # read accessors used by the tools
    # ------------------------------------------------------------------
    def latest(self) -> ObsFrame | None:
        with self._lock:
            return self._latest

    def frame(self, ref: str) -> ObsFrame | None:
        """``"latest"`` or a registered keyframe tag → frame (None if unknown)."""
        with self._lock:
            if ref in ("latest", "", None):
                return self._latest
            return self._keyframes.get(ref)

    def keyframe_refs(self) -> list[str]:
        with self._lock:
            return list(self._keyframes.keys())

    def verdict(self, node_id: str) -> Verdict | None:
        with self._lock:
            return self._verdicts.get(node_id)

    def verdicts(self) -> list[Verdict]:
        """Verdicts in the order they were set (latest per node kept)."""
        with self._lock:
            seen: list[str] = []
            for n in self._verdict_order:
                if n in seen:
                    seen.remove(n)
                seen.append(n)
            return [self._verdicts[n] for n in seen]

    def note_vlm_request(self) -> int:
        """Count one LLM request (an assistant turn of pi); returns the requests left
        this episode (design §3.5 "P2 计数硬停")."""
        with self._lock:
            self._vlm_requests_used += 1
            return max(0, self.max_vlm_requests - self._vlm_requests_used)

    @property
    def vlm_requests_left(self) -> int:
        with self._lock:
            return max(0, self.max_vlm_requests - self._vlm_requests_used)

    def budget(self) -> dict[str, int]:
        """``{calls_left, chunks_left}`` (design §4 budget "来自 step_lim").

        ``calls_left`` is 0 once either the planning-call cap or the per-episode
        LLM-request cap is used up. ``chunks_left = ceil((step_lim - t_latest) /
        exec_steps)``; without a ``step_lim`` in the observation it is -1 (unknown)."""
        with self._lock:
            calls_left = max(0, self.max_vlm_calls - self._vlm_calls_used)
            if self._vlm_requests_used >= self.max_vlm_requests:
                calls_left = 0
            f = self._latest
            if f is not None and f.step_lim is not None:
                steps_left = max(0, int(f.step_lim) - int(f.t))
                chunks_left = int(math.ceil(steps_left / max(1, self.exec_steps)))
            else:
                chunks_left = -1
            return {"calls_left": int(calls_left), "chunks_left": int(chunks_left)}

    @property
    def vlm_calls_used(self) -> int:
        with self._lock:
            return self._vlm_calls_used

    # ------------------------------------------------------------------
    # logging
    # ------------------------------------------------------------------
    def log_tool(self, tool: str, ok: bool, **summary: Any) -> None:
        """Record a tool call and forward it to ``trace.planner_event``."""
        rec = ToolCallRecord(tool=tool, ok=ok, t_wall=time.time(), summary=summary)
        with self._lock:
            self._tool_log.append(rec)
            plan_idx = self._plan_idx
        if self.trace is not None:
            try:
                self.trace.planner_event({"kind": "mcp_tool", "plan": plan_idx, **rec.to_dict()})
            except Exception:  # trace I/O must never break a tool call
                pass

    def tool_log(self) -> list[dict[str, Any]]:
        with self._lock:
            return [r.to_dict() for r in self._tool_log]

    def tool_counts(self) -> dict[str, int]:
        with self._lock:
            out: dict[str, int] = {}
            for r in self._tool_log:
                out[r.tool] = out.get(r.tool, 0) + 1
            return out


__all__ = ["HarnessState", "ToolCallRecord", "DEFAULT_MAX_KEYFRAMES"]
