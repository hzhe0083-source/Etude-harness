"""Abstract component interfaces (design §2 process table, §3.3 pseudocode, §4).

Every component agent implements one of these ABCs; the adapter
(``deploy/XPolicyLab/policy/Etude/model.py`` + ``etude.adapter``) wires them
together; demos use the fakes in :mod:`etude.fakes`.
"""
from __future__ import annotations

import abc
from typing import Any, Sequence

import numpy as np

from etude.core.types import (
    Detection,
    EpisodeRecord,
    Node,
    ObsFrame,
    Patch,
    SequenceEntry,
    Verdict,
)


class PolicyBackend(abc.ABC):
    """Frozen VLA policy (design §2 P3; pi0.5 via openpi websocket).

    ``infer`` returns an ``(H, 14)`` float array of ABSOLUTE joint targets in
    state order ``[L arm6, L grip, R arm6, R grip]`` (H = 50 for the official
    ckpt); the openpi output transform has already restored absolute joints.
    Use :func:`etude.core.actions.chunk_to_joint_actions` to turn rows into
    RoboDojo action dicts.
    """

    @abc.abstractmethod
    def infer(self, obs: ObsFrame, prompt: str) -> np.ndarray:
        """Return an ``(H, 14)`` absolute joint action chunk for ``obs`` and
        language ``prompt`` (design §3.3 ``pi05.infer``)."""

    def reset(self) -> None:
        """Clear per-episode backend state (default: none)."""


class Perceiver(abc.ABC):
    """Local open-set detector behind ``perceive(query, view)`` (design §4; no VLM)."""

    @abc.abstractmethod
    def perceive(self, query: str, image: np.ndarray, view: str) -> Detection:
        """Detect ``query`` in ``image`` (HxWx3 uint8) taken from ``view``
        (``head``/``wrist_l``/``wrist_r``); return the best :class:`Detection`
        (``found=False`` when nothing above the frozen confidence threshold)."""


class Planner(abc.ABC):
    """Node-sequence planner P1 (design §2; pi-mono over MCP/RPC).

    ``request`` keys (all optional except ``instruction``):
    ``instruction`` (str), ``task`` (str), ``reason`` ("start" | "node_failed"
    | "sequence_done"), ``history`` (list of verdict dicts so far),
    ``failed_node`` (node dict | None), ``budget`` ({"calls_left", "chunks_left"}),
    ``memory`` (list of SequenceEntry dicts from memory_lookup); after a retreat
    whose anchor re-verification failed (design §3.3): ``anchor_invalidated``
    (True), ``scene_intact`` as measured (False = disturbed scene; True / None
    = intact / undecidable, the anchor's postcondition no longer holds) and
    ``anchor_recheck`` (``[{anchor, postcondition, scene_intact, moved_objects}]``).
    """

    @abc.abstractmethod
    def plan(self, request: dict[str, Any]) -> list[Node]:
        """Return a schema-valid node sequence (design §3.3 ``self.plan()``)."""

    def reset(self) -> None:
        """Clear per-episode planner state (default: none)."""


class Verifier(abc.ABC):
    """Code verifier producing five-kind verdicts (design §4 判决 JSON)."""

    @abc.abstractmethod
    def check(self, node: Node, hist: Sequence[ObsFrame]) -> Verdict:
        """Judge ``node``'s postconditions from the observation history
        ``hist`` (oldest first; must include the frame at node start and the
        latest frame). Never reads episode-end reasons or official scores
        (design §3.5 last row)."""


class MemoryStore(abc.ABC):
    """Node-sequence memory ``memory_db/<task>/sequences.jsonl`` (design §3.1).

    Read-only at evaluation time; ``append`` is used only by the offline
    writer and must reject numeric targets / layout ids via the schema.
    """

    @abc.abstractmethod
    def lookup(self, task: str, instruction: str, k: int = 3) -> list[SequenceEntry]:
        """Return up to ``k`` stored sequences for ``task`` most relevant to
        ``instruction`` (design §4 ``memory_lookup``: top-3)."""

    @abc.abstractmethod
    def append(self, entry: SequenceEntry) -> bool:
        """Append ``entry`` (dedupe by task + instruction_hash, keep shorter);
        return True if stored/replaced, False if rejected or not shorter."""


class TraceWriter(abc.ABC):
    """Writes ``traces/<episode_id>/{trace.jsonl, planner.jsonl, keyframes/*.png}``
    (design §4 tree). Every method returns quickly and never raises on I/O
    errors during an episode (log and continue)."""

    @abc.abstractmethod
    def start_episode(self, episode_id: str, task: str, instruction: str, meta: dict[str, Any] | None = None) -> None:
        """Open a new episode directory and write the header event."""

    @abc.abstractmethod
    def keyframe(self, frame: ObsFrame, tag: str) -> list[str]:
        """Save lossless PNGs of all cams in ``frame``; return their refs
        (paths relative to ``traces/``, e.g. ``"kf/<ep>/<tag>_cam_head.png"``)."""

    @abc.abstractmethod
    def node_start(self, node: Node, t: int, chunk_idx: int) -> None:
        """Record that ``node`` started at env step ``t`` / chunk ``chunk_idx``."""

    @abc.abstractmethod
    def chunk(self, node_id: str, chunk_idx: int, t: int, n_actions: int, info: dict[str, Any] | None = None) -> None:
        """Record one emitted action chunk (size and optional executor info)."""

    @abc.abstractmethod
    def verdict(self, node: Node, verdict: Verdict) -> None:
        """Record a node verdict."""

    @abc.abstractmethod
    def planner_event(self, event: dict[str, Any]) -> None:
        """Append one event to ``planner.jsonl`` (request/response/tool call/usage)."""

    @abc.abstractmethod
    def end_episode(self, record: EpisodeRecord) -> None:
        """Write the final :class:`EpisodeRecord` (``episode.json``) and close files."""


class Editor(abc.ABC):
    """Harness-loop editor P6 (design §3.1 编辑者; pi-mono, claude-opus-5-5)."""

    @abc.abstractmethod
    def propose(self, evidence: dict[str, Any]) -> Patch:
        """Return one candidate :class:`Patch` for ``evidence["surface"]`` given
        E-split evidence (``evidence`` keys: ``surface``, ``clusters`` (failure
        clusters by earliest failing node), ``traces`` (refs), ``budget``
        (max diff lines / rules)). Must not read V/T results."""


__all__ = ["PolicyBackend", "Perceiver", "Planner", "Verifier", "MemoryStore", "TraceWriter", "Editor"]
