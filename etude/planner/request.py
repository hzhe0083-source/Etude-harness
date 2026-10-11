"""Planner prompt construction for one planning call (design §2 P1, §3.3
``self.plan()``, §8.3 "VLM 看图: 默认最后一帧，inspect 按需").

:func:`build_prompt` turns a :class:`~etude.core.interfaces.Planner` request
dict plus the live :class:`~etude.mcp.state.HarnessState` into the user
message sent to pi with ``{"type":"prompt"}``. Sections:

1. task instruction and why we are planning (start / node_failed / sequence_done);
2. last-frame tool hint: the latest frame is ``observe(frame="latest")``;
   older keyframes are listed only as refs for ``inspect`` (VISTA-style);
3. memory top-3 (from ``request["memory"]`` or a read-only lookup);
4. previous verdict, failed node and a compact verdict history;
5. budget ``{calls_left, chunks_left}``;
6. the output contract (call ``submit_sequence``; fix errors; then stop).

The system prompt (``planner/SYSTEM.md``) and the evolvable surfaces
(``evolvable/prompts/planner.md``, ``evolvable/skills/SKILL.md``) are passed to
pi separately as system-prompt text, so they are not repeated here.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Mapping, Sequence

from etude.core.types import Node, SequenceEntry, Verdict

REASONS = ("start", "node_failed", "sequence_done")
DEFAULT_MAX_KEYFRAME_REFS = 8
DEFAULT_MEMORY_TOP_K = 3


def _compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _as_dict(x: Any) -> Any:
    return x.to_dict() if hasattr(x, "to_dict") else x


def memory_for_prompt(request: Mapping[str, Any], state: Any = None, k: int = DEFAULT_MEMORY_TOP_K) -> list[dict[str, Any]]:
    """Top-``k`` memory sequences as ``{seq_id, instruction, nodes}`` (design §4
    memory_lookup top-3); ``lineage``/``evidence_refs`` are dropped (§3.5)."""
    raw = request.get("memory")
    if raw is None and state is not None and getattr(state, "memory", None) is not None and request.get("task"):
        raw = state.memory.lookup(str(request["task"]), str(request.get("instruction", "")), k=k)
    out: list[dict[str, Any]] = []
    for e in list(raw or [])[:k]:
        d = _as_dict(e)
        if isinstance(e, SequenceEntry) or isinstance(d, Mapping):
            out.append({"seq_id": d.get("seq_id", ""), "instruction": d.get("instruction", ""),
                        "nodes": [_as_dict(n) for n in d.get("nodes", [])]})
    return out


def verdict_line(v: Mapping[str, Any]) -> str:
    """``n2 Fail [gripper_holding(left)=False@0.82] t_chunk=[4,7]`` (design §4 verdict)."""
    preds = ", ".join(
        f"{k}={p.get('v')}@{float(p.get('conf', 0.0)):.2f}" for k, p in (v.get("predicates") or {}).items()
    )
    return f"{v.get('node')} {v.get('kind')} [{preds}] t_chunk={v.get('t_chunk')} src={v.get('source')}"


def build_prompt(
    request: Mapping[str, Any],
    state: Any = None,
    *,
    max_keyframe_refs: int = DEFAULT_MAX_KEYFRAME_REFS,
    memory_top_k: int = DEFAULT_MEMORY_TOP_K,
) -> str:
    """User message for one planning call (design §3.3 ``self.plan()``).

    ``request`` keys follow :class:`etude.core.interfaces.Planner`:
    ``instruction`` (required), ``task``, ``reason``, ``history`` (verdict
    dicts), ``failed_node``, ``budget``, ``memory``, and after an invalidated
    anchor ``anchor_invalidated`` (True), the measured ``scene_intact``
    (True / False / None) + ``anchor_recheck``. ``state`` (a
    :class:`~etude.mcp.state.HarnessState`, optional) supplies the latest
    frame ref, keyframe refs, budget and memory when the request lacks them.
    """
    instruction = str(request.get("instruction") or "").strip()
    if not instruction:
        raise ValueError("planner request needs a non-empty 'instruction'")
    task = str(request.get("task") or (getattr(state, "task", "") if state is not None else "") or "")
    reason = str(request.get("reason") or "start")
    lines: list[str] = ["# Planning request", ""]
    lines.append(f'Task instruction: "{instruction}"')
    if task:
        lines.append(f"Task id (only for memory_lookup; never write it into nodes): {task}")
    lines.append(f"Reason for this call: {reason}")
    lines.append("")

    # 2. last-frame hint (VISTA-style: latest frame by default, inspect on demand)
    lines.append("## Observation")
    latest = state.latest() if state is not None else None
    if latest is not None:
        lines.append(
            f'The latest frame (t={latest.t}) is available as observe(frame="latest"); '
            "look at it before planning. Use perceive(query, view) to confirm every target."
        )
    else:
        lines.append('Call observe(frame="latest") to see the current scene before planning.')
    refs = state.keyframe_refs()[-max_keyframe_refs:] if state is not None else []
    if refs:
        lines.append("Earlier keyframes (open only if needed, with observe(frame=ref) or inspect(frame_ref=ref, ...)): "
                     + ", ".join(refs))
    lines.append("")

    # 3. memory
    mem = memory_for_prompt(request, state, k=memory_top_k)
    lines.append(f"## Memory (top {memory_top_k} verified sequences; adapt, do not copy blindly)")
    if mem:
        for m in mem:
            lines.append(f"- {m['seq_id']} for \"{m['instruction']}\": {_compact(m['nodes'])}")
    else:
        lines.append("- none")
    lines.append("")

    # 4. verdicts
    history = [_as_dict(v) for v in (request.get("history") or [])]
    if not history and state is not None:
        history = [v.to_dict() for v in state.verdicts()]
    lines.append("## Verdicts so far")
    if history:
        lines.append("Previous verdict: " + _compact(history[-1]))
        if len(history) > 1:
            lines.append("History (oldest first): " + "; ".join(verdict_line(v) for v in history))
    else:
        lines.append("- none (nothing executed yet)")
    failed = request.get("failed_node")
    if failed is not None:
        lines.append("Failed node: " + _compact(_as_dict(failed)))
        lines.append("Plan a recovery that starts from the current state; do not repeat the failed node unchanged.")
    rechecks = [c for c in (request.get("anchor_recheck") or []) if isinstance(c, Mapping)]
    if request.get("scene_intact") is False:
        moved = sorted({q for c in rechecks for q in (c.get("moved_objects") or [])})
        lines.append("Scene check: scene_intact=false -- the scene was disturbed"
                     + (f" ({', '.join(moved)} moved since episode start)" if moved else "")
                     + "; earlier verdicts are stale. Re-locate objects with perceive before reusing them.")
    if request.get("anchor_invalidated"):
        broken = [f"{c.get('anchor')} ({c.get('postcondition')})" for c in rechecks
                  if c.get("postcondition") not in (None, "Pass")]
        if broken:
            scene = request.get("scene_intact")
            lines.append("Anchor check: the postconditions of the last verified node(s) "
                         + ", ".join(broken) + " no longer hold after retreating to them"
                         + (" (no watched object moved)" if scene is True
                            else " (scene check undecidable)" if scene is None else "")
                         + "; restore that state before continuing.")
    lines.append("")

    # 5. budget
    budget = request.get("budget") or (state.budget() if state is not None else None)
    lines.append("## Budget")
    if budget:
        lines.append(f"calls_left={budget.get('calls_left')} chunks_left={budget.get('chunks_left')} "
                     "(a vla chunk is exec_steps env steps; keep the plan short)")
    else:
        lines.append("unknown; call budget()")
    lines.append("")

    # 6. contract
    lines.append("## Output")
    lines.append(
        "Call submit_sequence(nodes=[...]) with the full node list. If it returns ok=false, fix every "
        "listed error and call it again. After ok=true, reply with one short line and stop."
    )
    return "\n".join(lines)


def build_rpc_prompt(
    request: Mapping[str, Any],
    state: Any = None,
    *,
    attach_last_frame: bool = False,
    views: Sequence[str] = ("head",),
    req_id: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """The pi RPC ``prompt`` command (pi docs/rpc.md "prompt"). With
    ``attach_last_frame`` the latest frame's ``views`` are attached as PNG
    images (saves one ``observe`` round trip; design §8.3)."""
    cmd: dict[str, Any] = {"type": "prompt", "message": build_prompt(request, state, **kw)}
    if req_id:
        cmd["id"] = req_id
    if attach_last_frame and state is not None and state.latest() is not None:
        from etude.core.types import VIEW_TO_CAM
        from etude.mcp.server import encode_png

        f = state.latest()
        imgs = []
        for v in views:
            cam = VIEW_TO_CAM.get(v, v)
            if cam in f.images:
                imgs.append({"type": "image", "data": base64.b64encode(encode_png(f.images[cam])).decode("ascii"),
                             "mimeType": "image/png"})
        if imgs:
            cmd["images"] = imgs
    return cmd


def nodes_to_json(nodes: Sequence[Node]) -> list[dict[str, Any]]:
    """Node list → §4 node JSON list."""
    return [n.to_dict() for n in nodes]


def verdicts_to_json(verdicts: Sequence[Verdict]) -> list[dict[str, Any]]:
    """Verdict list → §4 verdict JSON list (for ``request["history"]``)."""
    return [v.to_dict() for v in verdicts]


__all__ = [
    "REASONS", "build_prompt", "build_rpc_prompt", "memory_for_prompt", "nodes_to_json",
    "verdict_line", "verdicts_to_json",
]
