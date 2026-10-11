"""Drive pi-mono as the P1 planner over ``pi --mode rpc`` (design §2 P1, §3.3
``self.plan()``, §8.2 F5 "pi 侧用 --mode rpc 等 agent_settled").

Flow of one :meth:`PiRpcPlanner.plan` call (blocking; the sim is paused because
P2 is inside ``get_action``, design §2 慢回路):

1. budget hard stop: if the episode's VLM calls are used up, raise
   :class:`PlannerBudgetExhausted` without starting pi (design §3.5 超调用预算);
2. ``state.begin_plan()`` counts one VLM call and clears the last submission;
3. ensure the pi subprocess is up: ``pi --mode rpc --session-dir
   <traces>/<episode>/planner ...`` with ``cwd=planner/`` (so pi finds
   ``planner/.pi/mcp.json`` and ``planner/.pi/extensions/guard.ts``);
4. write one JSONL ``prompt`` command (:func:`etude.planner.request.build_rpc_prompt`);
5. read JSONL events until ``agent_settled``; sum assistant ``usage.cost``
   (``message_update`` keeps the running value, ``message_end`` is
   authoritative); count tool executions and guard blocks;
6. on timeout (default ``deploy.request_timeout_s`` = 1800 s) send
   ``{"type":"abort"}``, wait ``abort_grace_s`` for ``agent_settled``, then kill;
7. return the sequence that ``submit_sequence`` accepted into the
   :class:`~etude.mcp.state.HarnessState` (the MCP server thread), or raise
   :class:`PlannerNoSequence` / :class:`PlannerTimeout`.

The pi executable is injectable (``pi_cmd``); the demo uses
``[sys.executable, etude/planner/fake_pi.py]``.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from etude import REPO_ROOT, TRACES_DIR
from etude.core.config import load_thresholds, load_yaml
from etude.core.interfaces import Planner, TraceWriter
from etude.core.types import MCP_TOOLS, Node
from etude.mcp.state import HarnessState
from etude.planner.request import build_rpc_prompt

PLANNER_DIR: Path = REPO_ROOT / "planner"
PLANNER_CONFIG: Path = PLANNER_DIR / "config.yaml"
GUARD_STATUS_KEY = "etude-guard"
_EOF = object()


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def load_planner_config(path: str | Path | None = None) -> dict[str, Any]:
    """Parsed ``planner/config.yaml`` (design §8.3 规划模型与上限)."""
    return dict(load_yaml(Path(path) if path else PLANNER_CONFIG) or {})


def canonical_tool(name: str, prefixes: Sequence[str] = ("", "etude_", "etude-")) -> str | None:
    """Map an exposed tool name (possibly MCP-adapter prefixed) to one of the
    7 MCP tools, else None (same rule as ``planner/.pi/extensions/guard.ts``)."""
    for p in prefixes:
        if name.startswith(p) and name[len(p):] in MCP_TOOLS:
            return name[len(p):]
    return None


# ---------------------------------------------------------------------------
# results / errors
# ---------------------------------------------------------------------------


@dataclass
class PlanResult:
    """Outcome of one planning call (logged to ``planner.jsonl``; feeds
    ``EpisodeRecord.vlm_calls/vlm_tokens`` and the receipt cost row, design §4)."""

    plan_idx: int = 0
    nodes: list[Node] | None = None
    settled: bool = False
    timed_out: bool = False
    aborted: bool = False
    killed: bool = False
    exited: bool = False
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache_read: int = 0
    tokens_total: int = 0
    wall_s: float = 0.0
    tool_calls: dict[str, int] = field(default_factory=dict)
    blocked_calls: int = 0
    requests: int = 0  # assistant turns (LLM requests) in this planning call
    submit_attempts: int = 0
    guard_active: bool = False
    guard_status: str = ""
    stop_reason: str | None = None
    final_text: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if k != "nodes"}
        d["nodes"] = None if self.nodes is None else [n.to_dict() for n in self.nodes]
        return d


class PlannerError(RuntimeError):
    """Planning call failed; ``.result`` holds the :class:`PlanResult`."""

    def __init__(self, msg: str, result: PlanResult | None = None):
        super().__init__(msg)
        self.result = result


class PlannerTimeout(PlannerError):
    """No accepted sequence before the timeout (pi was aborted / killed)."""


class PlannerNoSequence(PlannerError):
    """pi settled without an accepted ``submit_sequence``."""


class PlannerBudgetExhausted(PlannerError):
    """Per-episode VLM call budget used up (design §3.5: node goes the no-VLM path)."""


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


class PiRpcPlanner(Planner):
    """:class:`~etude.core.interfaces.Planner` backed by ``pi --mode rpc`` (design §2 P1).

    Parameters
    ----------
    state:
        The :class:`HarnessState` the MCP server thread serves; the accepted
        sequence is read back from it.
    pi_cmd:
        Executable + leading args (default ``[config.pi.executable]`` or
        ``$ETUDE_PI``). The demo passes ``[sys.executable, ".../fake_pi.py"]``.
    cwd:
        Working dir for pi (default ``planner/``).
    traces_root:
        Session dirs go to ``<traces_root>/<episode_id>/planner``.
    timeout_s:
        Wall-clock limit per plan call (default frozen ``deploy.request_timeout_s``).
    mcp_url:
        URL exported to the child as ``ETUDE_MCP_URL`` (fake_pi uses it; real pi
        reads ``planner/.pi/mcp.json``, whose port must match).
    trace:
        Optional :class:`TraceWriter` for ``planner_event`` summaries.
    """

    def __init__(
        self,
        state: HarnessState,
        *,
        pi_cmd: Sequence[str] | None = None,
        cwd: str | Path | None = None,
        traces_root: str | Path | None = None,
        timeout_s: float | None = None,
        abort_grace_s: float | None = None,
        model: str | None = None,
        provider: str | None = None,
        thinking: str | None = None,
        extra_args: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
        mcp_url: str | None = None,
        config: Mapping[str, Any] | None = None,
        trace: TraceWriter | None = None,
        require_guard: bool | None = None,
        attach_last_frame: bool | None = None,
    ):
        cfg = dict(config) if config is not None else load_planner_config()
        self.cfg = cfg
        pcfg = dict(cfg.get("pi") or {})
        tcfg = dict(cfg.get("timeout") or {})
        self.state = state
        exe = os.environ.get("ETUDE_PI") or pcfg.get("executable") or "pi"
        self.pi_cmd = list(pi_cmd) if pi_cmd is not None else [exe]
        self.cwd = Path(cwd) if cwd is not None else PLANNER_DIR
        self.traces_root = Path(traces_root) if traces_root is not None else TRACES_DIR
        frozen_timeout = load_thresholds(state.root).get("deploy.request_timeout_s", 1800)
        self.timeout_s = float(timeout_s if timeout_s is not None else tcfg.get("plan_s", frozen_timeout))
        self.abort_grace_s = float(abort_grace_s if abort_grace_s is not None else tcfg.get("abort_grace_s", 15))
        self.model = model if model is not None else pcfg.get("model")
        self.provider = provider if provider is not None else pcfg.get("provider")
        self.thinking = thinking if thinking is not None else pcfg.get("thinking")
        self.extra_args = list(extra_args) if extra_args is not None else list(pcfg.get("extra_args") or [])
        self.env = dict(env or {})
        mcfg = dict(cfg.get("mcp") or {})
        self.mcp_url = mcp_url or f"http://{mcfg.get('host', '127.0.0.1')}:{mcfg.get('port', 8765)}{mcfg.get('path', '/mcp')}"
        self.trace = trace
        self.require_guard = bool(require_guard if require_guard is not None else (cfg.get("guard") or {}).get("required", True))
        pr = dict(cfg.get("prompt") or {})
        self.attach_last_frame = bool(attach_last_frame if attach_last_frame is not None else pr.get("attach_last_frame", False))
        self.prompt_kw = {
            "max_keyframe_refs": int(pr.get("max_keyframe_refs", 8)),
            "memory_top_k": int(pr.get("memory_top_k", 3)),
        }
        # clamp to the frozen threshold (tighten-only; guard.ts clamps ETUDE_MAX_TOOL_CALLS the same way)
        frozen_tools = int(load_thresholds(state.root).get("budget.max_tool_calls_per_plan", 40))
        self.max_tool_calls = min(int((cfg.get("guard") or {}).get("max_tool_calls_per_plan", frozen_tools)), frozen_tools)

        self.episode_id: str | None = None
        self.session_dir: Path | None = None
        self.results: list[PlanResult] = []
        self._proc: subprocess.Popen[bytes] | None = None
        self._q: queue.Queue[Any] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._events_fh: Any = None
        self._stdin_lock = threading.Lock()
        self._req = 0
        self._guard_status = ""

    # -- lifecycle ------------------------------------------------------------
    def start_episode(self, episode_id: str) -> Path:
        """Bind to an episode: session dir ``<traces_root>/<episode_id>/planner``
        (design §4 tree ``traces/<episode_id>/``). Stops any previous pi."""
        self.stop()
        self.episode_id = episode_id
        self.session_dir = self.traces_root / episode_id / "planner"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        return self.session_dir

    def reset(self) -> None:
        """End the episode's pi session (design §3.5 "reset() 清空回合态")."""
        self.stop()
        self.episode_id = None
        self.session_dir = None

    def build_command(self) -> list[str]:
        """Full argv for ``pi --mode rpc`` (design §8.2 F5)."""
        if self.session_dir is None:
            raise RuntimeError("call start_episode(episode_id) first")
        pcfg = dict(self.cfg.get("pi") or {})
        cmd = [*self.pi_cmd, "--mode", "rpc", "--session-dir", str(self.session_dir)]
        if self.provider:
            cmd += ["--provider", str(self.provider)]
        if self.model:
            cmd += ["--model", str(self.model)]
        if self.thinking:
            cmd += ["--thinking", str(self.thinking)]
        sp = pcfg.get("system_prompt")
        if sp:
            p = (self.cwd / sp) if not Path(sp).is_absolute() else Path(sp)
            if p.is_file():
                cmd += ["--system-prompt", p.read_text(encoding="utf-8")]
        for ap in pcfg.get("append_system_prompts") or []:
            p = (self.cwd / ap) if not Path(ap).is_absolute() else Path(ap)
            if p.is_file():
                cmd += ["--append-system-prompt", str(p.resolve())]
        cmd += self.extra_args
        return cmd

    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self.env)
        env["ETUDE_MCP_URL"] = self.mcp_url
        env["ETUDE_MAX_TOOL_CALLS"] = str(self.max_tool_calls)
        return env

    def _ensure_process(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self.stop()
        cmd = self.build_command()
        if shutil.which(cmd[0]) is None and not Path(cmd[0]).exists():
            raise PlannerError(f"pi executable not found: {cmd[0]!r} (set planner/config.yaml pi.executable or $ETUDE_PI)")
        assert self.session_dir is not None
        self._events_fh = open(self.session_dir / "rpc_events.jsonl", "a", encoding="utf-8")
        stderr_fh = open(self.session_dir / "stderr.log", "ab")
        self._q = queue.Queue()
        self._proc = subprocess.Popen(
            cmd,
            cwd=str(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_fh,
            env=self._child_env(),
        )
        stderr_fh.close()
        t = threading.Thread(target=self._reader, args=(self._proc, self._q), name="pi-rpc-reader", daemon=True)
        t.start()
        self._threads = [t]
        self._guard_status = ""

    @staticmethod
    def _reader(proc: subprocess.Popen[bytes], q: "queue.Queue[Any]") -> None:
        # Strict JSONL: split on b"\n" only, strip an optional trailing b"\r" (pi docs/rpc.md "Framing").
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = raw.rstrip(b"\n")
            if line.endswith(b"\r"):
                line = line[:-1]
            if not line.strip():
                continue
            try:
                q.put(json.loads(line.decode("utf-8")))
            except (json.JSONDecodeError, UnicodeDecodeError):
                q.put({"type": "_unparsed", "raw": line[:500].decode("utf-8", "replace")})
        q.put(_EOF)

    def _send(self, cmd: Mapping[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise PlannerError("pi process not running")
        data = (json.dumps(cmd, ensure_ascii=False) + "\n").encode("utf-8")
        with self._stdin_lock:
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise PlannerError(f"pi stdin closed: {e}") from e

    def stop(self, timeout: float = 5.0) -> None:
        """Terminate the pi subprocess (if any) and close logs."""
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout)
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads = []
        if self._events_fh is not None:
            self._events_fh.close()
            self._events_fh = None

    close = stop

    # -- event log ------------------------------------------------------------
    def _log_event(self, ev: Mapping[str, Any], plan_idx: int) -> None:
        if self._events_fh is None:
            return
        typ = ev.get("type")
        if typ == "message_update":
            ame = ev.get("assistantMessageEvent") or {}
            if ame.get("type") not in ("toolcall_end", "text_end"):
                return  # skip streaming deltas
            ev = {"type": typ, "assistantMessageEvent": {k: ame.get(k) for k in ("type", "toolCall", "content")}}
        rec = {"t": time.time(), "plan": plan_idx, **ev}
        try:
            self._events_fh.write(json.dumps(rec, ensure_ascii=False, default=str)[:20000] + "\n")
            self._events_fh.flush()
        except OSError:
            pass

    def _trace(self, event: dict[str, Any]) -> None:
        if self.trace is not None:
            try:
                self.trace.planner_event(event)
            except Exception:
                pass

    # -- planning -------------------------------------------------------------
    def plan(self, request: dict[str, Any]) -> list[Node]:
        """Blocking planning call → accepted node sequence (design §3.3 ``self.plan()``).

        Raises :class:`PlannerBudgetExhausted`, :class:`PlannerTimeout`,
        :class:`PlannerNoSequence` or :class:`PlannerError`; each carries the
        :class:`PlanResult` (also appended to ``self.results``)."""
        res = self.plan_detailed(request)
        if res.nodes is None:
            msg = "; ".join(res.errors) or "no accepted sequence"
            if res.timed_out:
                raise PlannerTimeout(msg, res)
            if res.settled:
                raise PlannerNoSequence(msg, res)
            raise PlannerError(msg, res)
        return res.nodes

    def plan_detailed(self, request: dict[str, Any]) -> PlanResult:
        """Like :meth:`plan` but returns the :class:`PlanResult` (``nodes`` may be None)."""
        if self.state.budget()["calls_left"] <= 0:
            res = PlanResult(errors=["VLM call/request budget exhausted"])
            self.results.append(res)
            self._trace({"kind": "plan_skipped", "reason": "budget"})
            raise PlannerBudgetExhausted("VLM call budget exhausted", res)
        if self.session_dir is None:
            self.start_episode(self.episode_id or f"adhoc-{int(time.time())}")
        plan_idx = self.state.begin_plan()
        res = PlanResult(plan_idx=plan_idx)
        self.results.append(res)
        t0 = time.monotonic()
        self._ensure_process()

        self._req += 1
        req_id = f"plan-{plan_idx}-{self._req}"
        cmd = build_rpc_prompt(request, self.state, attach_last_frame=self.attach_last_frame, req_id=req_id, **self.prompt_kw)
        self._trace({"kind": "plan_request", "plan": plan_idx, "reason": request.get("reason", "start"),
                     "prompt_chars": len(cmd["message"]), "n_images": len(cmd.get("images", []))})
        self._log_event({"type": "_sent_prompt", "id": req_id, "message": cmd["message"]}, plan_idx)
        self._send(cmd)

        deadline = t0 + self.timeout_s
        kill_at: float | None = None
        cur_usage: dict[str, Any] | None = None
        while True:
            now = time.monotonic()
            if not res.aborted and now >= deadline:
                res.timed_out = True
                res.aborted = True
                res.errors.append(f"timeout after {self.timeout_s:g}s; sent abort")
                try:
                    self._send({"type": "abort", "id": f"{req_id}-abort"})
                except PlannerError as e:
                    res.errors.append(str(e))
                kill_at = now + self.abort_grace_s
            if kill_at is not None and now >= kill_at:
                res.killed = True
                res.errors.append(f"no agent_settled {self.abort_grace_s:g}s after abort; killed pi")
                self.stop(timeout=2.0)
                break
            wait = min(0.25, max(0.01, (kill_at or deadline) - now))
            try:
                ev = self._q.get(timeout=wait)
            except queue.Empty:
                continue
            if ev is _EOF:
                res.exited = True
                rc = self._proc.poll() if self._proc is not None else None
                res.errors.append(f"pi exited (rc={rc}); see {self.session_dir}/stderr.log")
                self.stop(timeout=1.0)
                break
            self._log_event(ev, plan_idx)
            typ = ev.get("type")
            if typ == "response":
                # pi failure responses carry an "error" field (docs/rpc.md "Error Handling").
                if ev.get("command") == "prompt" and ev.get("error"):
                    res.errors.append(f"prompt rejected: {ev.get('error')}")
                    break
            elif typ == "message_update":
                if isinstance(ev.get("usage"), Mapping):
                    cur_usage = dict(ev["usage"])
            elif typ == "message_end":
                msg = ev.get("message") or {}
                if msg.get("role") == "assistant":
                    # every assistant turn is one LLM request (design §3.5 超调用预算): hard stop
                    res.requests += 1
                    if self.state.note_vlm_request() <= 0 and not res.aborted:
                        res.aborted = True
                        res.errors.append(f"VLM request budget ({self.state.max_vlm_requests}/episode) used up; sent abort")
                        try:
                            self._send({"type": "abort", "id": f"{req_id}-budget-abort"})
                        except PlannerError as e:
                            res.errors.append(str(e))
                        kill_at = time.monotonic() + self.abort_grace_s
                    usage = msg.get("usage") if isinstance(msg.get("usage"), Mapping) else cur_usage
                    self._add_usage(res, usage)
                    cur_usage = None
                    res.stop_reason = msg.get("stopReason", res.stop_reason)
                    texts = [c.get("text", "") for c in (msg.get("content") or []) if isinstance(c, Mapping) and c.get("type") == "text"]
                    if texts:
                        res.final_text = "\n".join(texts)[-2000:]
                    if msg.get("errorMessage"):
                        res.errors.append(str(msg.get("errorMessage")))
            elif typ == "tool_execution_start":
                name = str(ev.get("toolName", ""))
                key = canonical_tool(name) or name
                res.tool_calls[key] = res.tool_calls.get(key, 0) + 1
            elif typ == "tool_execution_end":
                name = str(ev.get("toolName", ""))
                if canonical_tool(name) is None and ev.get("isError"):
                    res.blocked_calls += 1
            elif typ == "extension_ui_request":
                self._handle_ui(ev, res)
            elif typ == "extension_error":
                res.errors.append(f"extension_error {ev.get('extensionPath')}: {ev.get('error')}")
            elif typ == "agent_settled":
                res.settled = True
                break
        if cur_usage is not None:  # aborted mid-message: count what the provider reported
            self._add_usage(res, cur_usage)
        res.wall_s = time.monotonic() - t0
        res.submit_attempts = self.state.submit_attempts
        res.guard_status = self._guard_status
        res.guard_active = bool(self._guard_status)
        nodes = self.state.take_submitted()
        if self.require_guard and not res.guard_active and nodes is not None:
            res.errors.append("guard extension (planner/.pi/extensions/guard.ts) never reported; sequence discarded")
            nodes = None
        res.nodes = nodes
        if nodes is None and not res.errors:
            res.errors.append("pi settled without an accepted submit_sequence")
        self._trace({"kind": "plan_result", **{k: v for k, v in res.to_dict().items() if k != "final_text"}})
        return res

    @staticmethod
    def _add_usage(res: PlanResult, usage: Mapping[str, Any] | None) -> None:
        if not usage:
            return
        cost = usage.get("cost") or {}
        res.cost_usd += float(cost.get("total", 0.0) or 0.0)
        res.tokens_in += int(usage.get("input", 0) or 0)
        res.tokens_out += int(usage.get("output", 0) or 0)
        res.tokens_cache_read += int(usage.get("cacheRead", 0) or 0)
        res.tokens_total += int(usage.get("totalTokens", 0) or 0) or (
            int(usage.get("input", 0) or 0) + int(usage.get("output", 0) or 0) + int(usage.get("cacheRead", 0) or 0)
        )

    def _handle_ui(self, ev: Mapping[str, Any], res: PlanResult) -> None:
        method = ev.get("method")
        if method == "setStatus" and ev.get("statusKey") == GUARD_STATUS_KEY:
            self._guard_status = str(ev.get("statusText") or "")
        elif method in ("select", "confirm", "input", "editor"):
            # Headless: never let an extension dialog block the planner.
            try:
                self._send({"type": "extension_ui_response", "id": ev.get("id"), "cancelled": True})
            except PlannerError:
                pass

    # -- usage totals -----------------------------------------------------------
    def totals(self) -> dict[str, Any]:
        """Sum over this planner's calls (for ``EpisodeRecord.vlm_calls/vlm_tokens``)."""
        return {
            "calls": len([r for r in self.results if r.plan_idx]),
            "cost_usd": round(sum(r.cost_usd for r in self.results), 6),
            "tokens": sum(r.tokens_total for r in self.results),
            "timeouts": sum(1 for r in self.results if r.timed_out),
            "wall_s": round(sum(r.wall_s for r in self.results), 3),
        }


__all__ = [
    "GUARD_STATUS_KEY", "PLANNER_CONFIG", "PLANNER_DIR", "PiRpcPlanner", "PlanResult", "PlannerBudgetExhausted",
    "PlannerError", "PlannerNoSequence", "PlannerTimeout", "canonical_tool", "load_planner_config",
]
