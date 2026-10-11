"""Read-side of ``traces/<episode_id>/`` (design §4 tree; consumers: the
offline memory writer §3.3, P6 evidence clustering §3.1 "按最早失败节点聚类",
MCP ``inspect(frame_ref, …)`` which needs lossless history frames §4).

Refs are paths relative to ``traces/`` (``"<ep>/keyframes/<tag>_<cam>.png"``);
:meth:`TraceReader.resolve_ref` refuses anything that escapes ``traces/``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from etude import TRACES_DIR
from etude.core.types import EpisodeRecord, Node, Verdict
from etude.trace.writer import EPISODE_FILE, KEYFRAME_DIR, PLANNER_FILE, TRACE_FILE


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"ev": "corrupt", "raw": line[:200]})  # a crash mid-write leaves a partial last line
    return out


def load_png(path: str | Path) -> np.ndarray:
    """Decode a keyframe PNG to HxWx3 uint8 (PIL; lossless round trip of :func:`save_png`)."""
    try:
        from PIL import Image
    except Exception as e:  # noqa: BLE001
        raise NotImplementedError("reading keyframes needs Pillow (PIL); it is not importable here") from e
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)


@dataclass
class EpisodeTrace:
    """Everything recorded for one episode (design §4 traces/<episode_id>/)."""

    episode_id: str
    path: Path
    events: list[dict[str, Any]] = field(default_factory=list)
    planner_events: list[dict[str, Any]] = field(default_factory=list)
    record: EpisodeRecord | None = None

    @property
    def header(self) -> dict[str, Any]:
        """The ``start`` event (task, instruction, meta) or {}."""
        return next((e for e in self.events if e.get("ev") == "start"), {})

    def of(self, ev: str) -> list[dict[str, Any]]:
        """Events of one kind (start, keyframe, node_start, chunk, verdict, end)."""
        return [e for e in self.events if e.get("ev") == ev]

    def nodes(self) -> list[Node]:
        """Nodes in start order (one per node_start, retries included)."""
        return [Node.from_dict(e["spec"]) for e in self.of("node_start") if "spec" in e]

    def verdicts(self) -> list[Verdict]:
        """Verdicts in order (design §4 判决 JSON)."""
        return [Verdict.from_dict(e["verdict"]) for e in self.of("verdict") if "verdict" in e]

    def earliest_failure(self) -> Verdict | None:
        """First non-Pass verdict: the clustering key for P6 evidence (design §3.1 证据)."""
        return next((v for v in self.verdicts() if v.kind != "Pass"), None)

    def keyframes(self, tag: str | None = None) -> list[dict[str, Any]]:
        """Keyframe events ``{t, tag, refs, state, …}``, optionally filtered by tag."""
        return [e for e in self.of("keyframe") if tag is None or e.get("tag") == tag]

    def keyframe_at(self, t: int) -> dict[str, Any] | None:
        """Latest keyframe event at or before env step ``t`` (for ``observe(frame=…)``)."""
        best = None
        for e in self.of("keyframe"):
            if int(e.get("t", 0)) <= t and (best is None or int(e["t"]) >= int(best["t"])):
                best = e
        return best


class TraceReader:
    """Reads ``traces_dir`` (default :data:`etude.TRACES_DIR`) (design §4)."""

    def __init__(self, traces_dir: str | Path | None = None):
        self.traces_dir = Path(traces_dir) if traces_dir is not None else TRACES_DIR

    def list_episodes(self) -> list[str]:
        """Episode ids that have a ``trace.jsonl`` or ``episode.json``, sorted."""
        if not self.traces_dir.is_dir():
            return []
        return sorted(
            p.name for p in self.traces_dir.iterdir()
            if p.is_dir() and ((p / TRACE_FILE).exists() or (p / EPISODE_FILE).exists())
        )

    def episode_dir(self, episode_id: str) -> Path:
        p = (self.traces_dir / episode_id).resolve()
        if p.parent != self.traces_dir.resolve():
            raise ValueError(f"bad episode id {episode_id!r}")
        return p

    def record(self, episode_id: str) -> EpisodeRecord | None:
        """``episode.json`` as :class:`EpisodeRecord` (official_* null at run time), or None."""
        p = self.episode_dir(episode_id) / EPISODE_FILE
        if not p.exists():
            return None
        return EpisodeRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def load(self, episode_id: str) -> EpisodeTrace:
        """Load events, planner events and the record of one episode."""
        d = self.episode_dir(episode_id)
        return EpisodeTrace(
            episode_id=episode_id,
            path=d,
            events=_read_jsonl(d / TRACE_FILE),
            planner_events=_read_jsonl(d / PLANNER_FILE),
            record=self.record(episode_id),
        )

    def iter_traces(self, split: str | None = None) -> Iterator[EpisodeTrace]:
        """All episodes, optionally only those whose record has ``split``."""
        for ep in self.list_episodes():
            tr = self.load(ep)
            if split is None or (tr.record is not None and tr.record.split == split):
                yield tr

    def resolve_ref(self, ref: str) -> Path:
        """Path of a keyframe ref relative to ``traces/``; refuses escapes and
        non-keyframe paths (MCP ``inspect`` input is untrusted, design §4)."""
        root = self.traces_dir.resolve()
        p = (root / ref).resolve()
        if root not in p.parents or p.parent.name != KEYFRAME_DIR or p.suffix != ".png":
            raise ValueError(f"not a keyframe ref under traces/: {ref!r}")
        return p

    def read_keyframe(self, ref: str) -> np.ndarray:
        """Decode a keyframe ref to HxWx3 uint8 (design §4 ``inspect`` 历史帧无损)."""
        return load_png(self.resolve_ref(ref))


__all__ = ["EpisodeTrace", "TraceReader", "load_png"]
