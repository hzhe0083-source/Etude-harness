"""File-backed trace writer for one P2 episode (design §4 tree
``traces/<episode_id>/{planner.jsonl, trace.jsonl, keyframes/*.png}`` and
"存储：关键帧（节点边界与每秒 1 帧）PNG 无损").

Layout per episode::

    traces/<episode_id>/
      trace.jsonl        one event per line: start, keyframe, node_start, chunk, verdict, end
      planner.jsonl      planner requests / responses / tool calls / usage
      keyframes/*.png    lossless PNG, <tag>_<cam>.png; tags: node boundaries
                         (e.g. n3_before / n3_after) and per-second "s0004"
      episode.json       final EpisodeRecord (official_* always null here)

Keyframe refs are paths relative to ``traces/``:
``"<episode_id>/keyframes/<tag>_<cam>.png"``; they go into verdict
``evidence_refs`` and are what MCP ``inspect(frame_ref, …)`` resolves.

Run-time guarantees (interface contract): every method returns quickly and
never raises on I/O errors (they are logged and counted in ``errors``);
official success / score are never written at run time (design §3.5 last row).
"""
from __future__ import annotations

import json
import logging
import os
import re
import struct
import time
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from etude import TRACES_DIR
from etude.core.interfaces import TraceWriter
from etude.core.types import CONTROL_HZ, EpisodeRecord, Node, ObsFrame, Verdict

log = logging.getLogger(__name__)

TRACE_FILE = "trace.jsonl"
PLANNER_FILE = "planner.jsonl"
EPISODE_FILE = "episode.json"
KEYFRAME_DIR = "keyframes"
_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")

try:  # PIL is the preferred lossless encoder; a zlib PNG writer is the fallback.
    from PIL import Image as _PILImage
except Exception:  # noqa: BLE001
    _PILImage = None


def _jsonable(x: Any) -> Any:
    """numpy scalars/arrays -> plain JSON types (recursive)."""
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def safe_name(s: str) -> str:
    """Filesystem-safe component (letters, digits, ``_ . -``)."""
    s = _SAFE_RE.sub("_", str(s)).strip("._")
    return s[:120] or "x"


def _png_bytes(img: np.ndarray) -> bytes:
    """Minimal lossless RGB/gray PNG encoder (filter 0, zlib) used without PIL."""
    a = np.ascontiguousarray(img, dtype=np.uint8)
    if a.ndim == 2:
        color = 0
        h, w = a.shape
        rows = a.reshape(h, w)
    else:
        h, w, c = a.shape
        color = {1: 0, 3: 2, 4: 6}[c]
        rows = a.reshape(h, w * c)
    raw = b"".join(b"\x00" + rows[i].tobytes() for i in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b"")


def save_png(img: np.ndarray, path: str | Path) -> None:
    """Write ``img`` (HxWx3 uint8) as a lossless PNG (design §4 存储)."""
    path = Path(path)
    a = np.asarray(img)
    if a.dtype != np.uint8:
        a = np.clip(a, 0, 255).astype(np.uint8)
    if _PILImage is not None:
        _PILImage.fromarray(a).save(path, format="PNG", compress_level=1)
    else:
        path.write_bytes(_png_bytes(a))


class FileTraceWriter(TraceWriter):
    """Writes ``traces/<episode_id>/…`` (design §4). ``traces_dir`` defaults to
    :data:`etude.TRACES_DIR`. ``keyframe_hz`` = periodic keyframes per second
    (design: 1). ``cams`` limits which cameras are saved (default: all)."""

    def __init__(self, traces_dir: str | Path | None = None, *, keyframe_hz: float = 1.0, cams: tuple[str, ...] | None = None):
        self.traces_dir = Path(traces_dir) if traces_dir is not None else TRACES_DIR
        self.keyframe_hz = keyframe_hz
        self.cams = cams
        self.episode_id: str | None = None
        self.ep_dir: Path | None = None
        self.errors: list[str] = []
        self.keyframe_refs: list[str] = []
        self._last_periodic: int | None = None
        self._t0 = 0.0
        self._seq = 0

    # -- internals ------------------------------------------------------------
    def _fail(self, where: str, e: BaseException) -> None:
        msg = f"{where}: {type(e).__name__}: {e}"
        self.errors.append(msg)
        log.warning("trace writer %s", msg)

    def _emit(self, fname: str, ev: str, payload: dict[str, Any]) -> None:
        if self.ep_dir is None:
            self._fail(ev, RuntimeError("no active episode (call start_episode first)"))
            return
        self._seq += 1
        line = {"ev": ev, "seq": self._seq, "wall": round(time.time() - self._t0, 4), **_jsonable(payload)}
        try:
            with open(self.ep_dir / fname, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001 - never raise during an episode
            self._fail(ev, e)

    # -- TraceWriter API -------------------------------------------------------
    def start_episode(self, episode_id: str, task: str, instruction: str, meta: dict[str, Any] | None = None) -> None:
        """Open ``traces/<episode_id>/`` and write the ``start`` event (design §4)."""
        try:
            self.episode_id = safe_name(episode_id)
            self.ep_dir = self.traces_dir / self.episode_id
            (self.ep_dir / KEYFRAME_DIR).mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            self.ep_dir = None
            self._fail("start_episode", e)
            return
        self.keyframe_refs = []
        self._last_periodic = None
        self._t0 = time.time()
        self._seq = 0
        self._emit(TRACE_FILE, "start", {"episode_id": self.episode_id, "task": task, "instruction": instruction,
                                         "meta": meta or {}, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})

    def keyframe(self, frame: ObsFrame, tag: str) -> list[str]:
        """Save lossless PNGs of every cam in ``frame`` as ``<tag>_<cam>.png``
        and return refs relative to ``traces/`` (design §4 存储; node boundaries)."""
        if self.ep_dir is None:
            self._fail("keyframe", RuntimeError("no active episode"))
            return []
        refs: list[str] = []
        tag_s = safe_name(tag)
        for cam in sorted(frame.images):
            if self.cams is not None and cam not in self.cams:
                continue
            name = f"{tag_s}_{cam}.png"
            path = self.ep_dir / KEYFRAME_DIR / name
            if path.exists():  # same tag twice (e.g. a retried node): disambiguate by step
                name = f"{tag_s}_t{int(frame.t):05d}_{cam}.png"
                path = self.ep_dir / KEYFRAME_DIR / name
            try:
                save_png(frame.images[cam], path)
            except Exception as e:  # noqa: BLE001
                self._fail(f"keyframe {name}", e)
                continue
            refs.append(f"{self.episode_id}/{KEYFRAME_DIR}/{name}")
        self.keyframe_refs.extend(refs)
        self._emit(TRACE_FILE, "keyframe", {"t": int(frame.t), "tag": tag, "refs": refs,
                                            "state": frame.to_dict()["state"],
                                            "gripper_width_measured": frame.additional_info.get("gripper_width_measured")})
        return refs

    def tick(self, frame: ObsFrame) -> list[str]:
        """Periodic keyframe: saves ``s<sec>`` once per ``1/keyframe_hz`` seconds
        of env time (``t / frequency``) (design §4 "每秒 1 帧"). Call on every
        ``update_obs``; returns refs when a frame was saved, else []."""
        if self.keyframe_hz <= 0:
            return []
        freq = frame.frequency or CONTROL_HZ
        period = max(1, int(round(freq / self.keyframe_hz)))
        slot = int(frame.t) // period
        if self._last_periodic is not None and slot <= self._last_periodic:
            return []
        self._last_periodic = slot
        return self.keyframe(frame, f"s{slot:04d}")

    def node_start(self, node: Node, t: int, chunk_idx: int) -> None:
        """Record node start with its full node JSON (design §4 节点 schema)."""
        self._emit(TRACE_FILE, "node_start", {"node": node.id, "t": int(t), "chunk": int(chunk_idx), "spec": node.to_dict()})

    def chunk(self, node_id: str, chunk_idx: int, t: int, n_actions: int, info: dict[str, Any] | None = None) -> None:
        """Record one emitted action chunk (design §3.3 get_action)."""
        self._emit(TRACE_FILE, "chunk", {"node": node_id, "chunk": int(chunk_idx), "t": int(t), "n": int(n_actions), "info": info or {}})

    def verdict(self, node: Node, verdict: Verdict) -> None:
        """Record a verdict exactly in §4 判决 JSON form."""
        self._emit(TRACE_FILE, "verdict", {"node": node.id, "verdict": verdict.to_dict()})

    def planner_event(self, event: dict[str, Any]) -> None:
        """Append to ``planner.jsonl`` (requests, responses, MCP tool calls, usage)."""
        self._emit(PLANNER_FILE, str(event.get("type", "planner")), {"event": event})

    def checkpoint(self, record: EpisodeRecord) -> None:
        """Rewrite ``episode.json`` with the episode so far, marked ``_trace.partial``
        (called after every chunk; a hard kill before ``end_episode`` then loses at
        most the last chunk). ``end_episode`` overwrites it with the final record."""
        self._write_record(record, partial=True)

    def _write_record(self, record: EpisodeRecord, partial: bool) -> None:
        if self.ep_dir is None:
            self._fail("checkpoint" if partial else "end_episode", RuntimeError("no active episode"))
            return
        d = record.to_dict()
        if d.get("official_success") is not None or d.get("official_score") is not None:
            log.warning("trace writer: dropping official_* from run-time episode record %s", record.episode_id)
        d["official_success"] = None
        d["official_score"] = None
        for r in self.keyframe_refs:
            if r not in d["evidence_refs"]:
                d["evidence_refs"].append(r)
        d["_trace"] = {"keyframes": len(self.keyframe_refs), "errors": list(self.errors),
                       "wall_s": round(time.time() - self._t0, 3), "partial": partial}
        try:
            tmp = self.ep_dir / f".{EPISODE_FILE}.tmp"
            tmp.write_text(json.dumps(_jsonable(d), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, self.ep_dir / EPISODE_FILE)
        except Exception as e:  # noqa: BLE001
            self._fail("checkpoint" if partial else "end_episode", e)

    def end_episode(self, record: EpisodeRecord) -> None:
        """Write ``episode.json`` atomically and the ``end`` event. ``official_*``
        are forced to null: they are filled offline by P5 only (design §3.5)."""
        if self.ep_dir is None:
            self._fail("end_episode", RuntimeError("no active episode"))
            return
        self._write_record(record, partial=False)
        self._emit(TRACE_FILE, "end", {"episode_id": record.episode_id, "steps": record.steps, "chunks": record.chunks,
                                       "vlm_calls": record.vlm_calls, "all_pass": record.all_pass})


__all__ = ["EPISODE_FILE", "FileTraceWriter", "KEYFRAME_DIR", "PLANNER_FILE", "TRACE_FILE", "safe_name", "save_png"]
