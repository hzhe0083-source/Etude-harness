#!/usr/bin/env python3
"""Stand-in for ``pi --mode rpc`` used by ``python -m etude.planner --demo``
(design §8.2 F5; pi docs/rpc.md). Never calls a language model.

It speaks the same JSONL protocol on stdin/stdout:

* commands: ``prompt`` (→ ``response`` then an agent run), ``abort``,
  ``get_state``, ``extension_ui_response`` (ignored);
* events: ``agent_start``, ``turn_start``/``turn_end``, ``message_start``/
  ``message_update`` (with cumulative ``usage.cost``)/``message_end``,
  ``tool_execution_start``/``tool_execution_end``, ``agent_end``,
  ``agent_settled``, plus the ``extension_ui_request setStatus`` that
  ``planner/.pi/extensions/guard.ts`` emits.

Tool calls go to the real MCP server at ``$ETUDE_MCP_URL`` (streamable-http),
so the demo exercises the HarnessState round trip. Scenario via
``$FAKE_PI_SCENARIO``:

* ``ok``           budget → observe → perceive → memory_lookup → submit_sequence
* ``fix_errors``   a forbidden ``bash`` call (blocked like guard.ts would), an
                   invalid submission (numeric target), then a valid one
* ``no_submit``    looks around and stops without submitting
* ``hang``         never finishes until ``abort``; then settles with stopReason aborted
* ``ignore_abort`` never finishes and ignores ``abort`` (the driver must kill it)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from typing import Any

_out_lock = threading.Lock()
_abort = threading.Event()
_busy = threading.Event()

ALLOWED = ("observe", "inspect", "perceive", "memory_lookup", "submit_sequence", "get_verdict", "budget")
COST_PER_MSG = {"input": 1500, "output": 200, "cacheRead": 0, "cacheWrite": 0}
PRICE = {"input": 2.0e-6, "output": 10.0e-6}  # $/token, roughly the design's planner model (§3.4)


def emit(obj: dict[str, Any]) -> None:
    with _out_lock:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def usage(n_msgs: int = 1) -> dict[str, Any]:
    inp, out = COST_PER_MSG["input"] * n_msgs, COST_PER_MSG["output"] * n_msgs
    ci, co = inp * PRICE["input"], out * PRICE["output"]
    return {"input": inp, "output": out, "cacheRead": 0, "cacheWrite": 0, "totalTokens": inp + out,
            "cost": {"input": ci, "output": co, "cacheRead": 0, "cacheWrite": 0, "total": ci + co}}


class Run:
    """One agent run (one prompt) emitting pi-shaped events."""

    def __init__(self, message: str, session_log: Any):
        self.message = message
        self.session_log = session_log
        self.n_call = 0
        self.url = os.environ.get("ETUDE_MCP_URL", "http://127.0.0.1:8765/mcp")
        m = re.search(r'Task instruction: "(.*)"', message)
        self.instruction = m.group(1) if m else "do the task"
        m = re.search(r"Task id[^:]*: (\S+)", message)
        self.task = m.group(1) if m else ""

    def assistant(self, content: list[dict[str, Any]], stop: str) -> dict[str, Any]:
        u = usage()
        emit({"type": "message_start", "message": {"role": "assistant", "content": []}})
        for i, c in enumerate(content):
            if c["type"] == "text":
                emit({"type": "message_update", "usage": u, "assistantMessageEvent": {"type": "text_delta", "contentIndex": i, "delta": c["text"]}})
                emit({"type": "message_update", "usage": u, "assistantMessageEvent": {"type": "text_end", "contentIndex": i, "content": c["text"]}})
            else:
                emit({"type": "message_update", "usage": u, "assistantMessageEvent": {"type": "toolcall_end", "contentIndex": i, "toolCall": c}})
        msg = {"role": "assistant", "content": content, "api": "fake", "provider": "fake", "model": "fake-pi",
               "usage": u, "stopReason": stop, "timestamp": int(time.time() * 1000)}
        emit({"type": "message_end", "message": msg})
        return msg

    async def tool(self, client: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """One turn: assistant tool call → (guard) → MCP call → tool result."""
        self.n_call += 1
        call_id = f"call_{self.n_call}"
        emit({"type": "turn_start"})
        msg = self.assistant([{"type": "toolCall", "id": call_id, "name": name, "arguments": args}], "toolUse")
        emit({"type": "tool_execution_start", "toolCallId": call_id, "toolName": name, "args": args})
        if name not in ALLOWED:  # what planner/.pi/extensions/guard.ts does
            reason = f"etude-guard: tool '{name}' is not allowed; only {', '.join(ALLOWED)}"
            emit({"type": "extension_ui_request", "id": f"ui-{call_id}", "method": "notify", "message": reason, "notifyType": "warning"})
            result = {"content": [{"type": "text", "text": reason}], "details": {}}
            emit({"type": "tool_execution_end", "toolCallId": call_id, "toolName": name, "result": result, "isError": True})
            emit({"type": "turn_end", "message": msg, "toolResults": [result]})
            return {"blocked": reason}
        r = await client.call_tool(name, args)
        text = next((c.text for c in r.content if getattr(c, "type", "") == "text"), "{}")
        result = {"content": [{"type": "text", "text": text[:4000]}], "details": {"n_images": sum(1 for c in r.content if getattr(c, "type", "") == "image")}}
        emit({"type": "tool_execution_end", "toolCallId": call_id, "toolName": name, "result": result, "isError": bool(r.is_error)})
        emit({"type": "turn_end", "message": msg, "toolResults": [result]})
        self.session_log.write(json.dumps({"tool": name, "args": args, "result": text[:500]}) + "\n")
        self.session_log.flush()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

    def finish(self, text: str, stop: str = "stop") -> None:
        emit({"type": "turn_start"})
        msg = self.assistant([{"type": "text", "text": text}], stop)
        emit({"type": "turn_end", "message": msg, "toolResults": []})
        emit({"type": "agent_end", "messages": [], "willRetry": False})
        emit({"type": "extension_ui_request", "id": "ui-end", "method": "setStatus", "statusKey": "etude-guard",
              "statusText": json.dumps({"allowed": self.n_call, "blocked": 0})})
        emit({"type": "agent_settled"})

    async def main(self, scenario: str) -> None:
        from mcp import Client

        emit({"type": "agent_start"})
        emit({"type": "extension_ui_request", "id": "ui-start", "method": "setStatus", "statusKey": "etude-guard",
              "statusText": "active: 7 tools"})
        nodes = self.good_nodes()
        async with Client(self.url) as client:
            if scenario in ("hang", "ignore_abort"):
                await self.tool(client, "observe", {"views": ["head"]})
                emit({"type": "message_start", "message": {"role": "assistant", "content": []}})
                emit({"type": "message_update", "usage": usage(), "assistantMessageEvent": {"type": "thinking_delta", "contentIndex": 0, "delta": "..."}})
                import anyio

                while not (_abort.is_set() and scenario == "hang"):
                    await anyio.sleep(0.05)
                emit({"type": "message_end", "message": {"role": "assistant", "content": [], "usage": usage(), "stopReason": "aborted"}})
                emit({"type": "agent_end", "messages": [], "willRetry": False})
                emit({"type": "agent_settled"})
                return
            await self.tool(client, "budget", {})
            await self.tool(client, "observe", {"views": ["head", "wrist_l"], "frame": "latest"})
            det = await self.tool(client, "perceive", {"query": "red block", "view": "head"})
            if self.task:
                await self.tool(client, "memory_lookup", {"task": self.task, "instruction": self.instruction})
            if scenario == "no_submit":
                self.finish("I could not decide on a plan.")
                return
            if scenario == "fix_errors":
                await self.tool(client, "bash", {"command": "cat ../frozen/params/thresholds.yaml"})
                bad = json.loads(json.dumps(nodes))
                bad[0]["args"]["target"] = 'perceive("block at 0.31, -0.15")'
                out = await self.tool(client, "submit_sequence", {"nodes": bad})
                assert out.get("ok") is False, out
            if not det.get("found"):
                nodes = nodes[1:]
            out = await self.tool(client, "submit_sequence", {"nodes": nodes})
            self.finish("Submitted." if out.get("ok") else f"Submission failed: {out.get('errors')}")

    def good_nodes(self) -> list[dict[str, Any]]:
        from etude.fakes import three_node_sequence

        return [n.to_dict() for n in three_node_sequence("red")]


def run_prompt(message: str, session_log: Any) -> None:
    import anyio

    try:
        anyio.run(Run(message, session_log).main, os.environ.get("FAKE_PI_SCENARIO", "ok"))
    except Exception as e:  # report like pi would: an errored assistant message, then settle
        emit({"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "error",
                                                 "errorMessage": f"fake_pi: {type(e).__name__}: {e}", "usage": usage(0)}})
        emit({"type": "agent_end", "messages": [], "willRetry": False})
        emit({"type": "agent_settled"})
    finally:
        _busy.clear()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--mode", default="rpc")
    ap.add_argument("--session-dir", default=None)
    args, _unknown = ap.parse_known_args(argv)
    if args.mode != "rpc":
        print("fake_pi only implements --mode rpc", file=sys.stderr)
        return 2
    sdir = args.session_dir or "."
    os.makedirs(sdir, exist_ok=True)
    session_log = open(os.path.join(sdir, "fake_pi_session.jsonl"), "a", encoding="utf-8")
    for raw in sys.stdin.buffer:
        line = raw.rstrip(b"\n").rstrip(b"\r")
        if not line.strip():
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError as e:
            emit({"type": "response", "command": "parse", "success": False, "error": f"Failed to parse command: {e}"})
            continue
        typ, cid = cmd.get("type"), cmd.get("id")
        base = {"type": "response", "command": typ, **({"id": cid} if cid else {})}
        if typ == "prompt":
            if _busy.is_set():
                emit({**base, "success": False, "error": "agent is streaming; set streamingBehavior"})
                continue
            _busy.set()
            _abort.clear()
            session_log.write(json.dumps({"prompt": cmd.get("message", "")[:2000]}) + "\n")
            emit({**base, "success": True})
            threading.Thread(target=run_prompt, args=(cmd.get("message", ""), session_log), daemon=True).start()
        elif typ == "abort":
            _abort.set()
            emit({**base, "success": True})
        elif typ == "get_state":
            emit({**base, "success": True, "data": {"isStreaming": _busy.is_set(), "model": {"id": "fake-pi"}}})
        elif typ == "extension_ui_response":
            continue
        else:
            emit({**base, "success": False, "error": f"fake_pi: unsupported command {typ!r}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
