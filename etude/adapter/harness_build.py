"""Harness-mode assembly inside the policy server (design §2 P2 row, §3.3, §4 MCP table).

:class:`HarnessRunner` is the default ``runner.class`` of deploy.yml. It builds
the P2 process from the deploy config:

* ``etude.runner.NodeRunner``, with its backend, perceiver, planner, verifier,
  memory and trace components;
* ``etude.mcp.state.HarnessState``, plus ``McpServerThread`` serving it, but
  only when the planner is the real ``pi --mode rpc`` driver, since P1 talks to
  P2 over MCP;
* the bridge between them. NodeRunner does not know about HarnessState, so
  every observation, chunk boundary and verdict is mirrored into the MCP state
  here. That is how ``observe``/``get_verdict``/``budget`` see the episode.

Component kinds (deploy.yml sections; each one also accepts ``class: "module:Class"``):

========== =========================================== ==================================
section    kinds                                       default
========== =========================================== ==================================
backend    remote | inprocess | fake                   remote (openpi serve_policy.py)
perceiver  owlv2 | color_blob | fake                   owlv2
planner    pi_rpc | fake                               pi_rpc
verifier   code | fake                                 code
memory     none | db | fake                            none (design §5 M1 "记忆关")
trace      file | none | fake                          file (traces/)
========== =========================================== ==================================
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

from etude.adapter.xpl_model import _construct, import_object, make_backend
from etude.core.types import ObsFrame


def _log(msg: str) -> None:
    print(f"[Etude.harness] {msg}", file=sys.stderr, flush=True)


def _section(cfg: Mapping[str, Any], name: str, default_kind: str) -> dict[str, Any]:
    sec = dict(cfg.get(name) or {})
    sec.setdefault("kind", default_kind)
    return sec


def make_perceiver(sec: Mapping[str, Any]) -> Any:
    """Perceiver for ``perceive(query, view)`` (design §4; local detector, no VLM)."""
    if sec.get("class"):
        return _construct(import_object(str(sec["class"])), sec)
    kind = str(sec["kind"])
    if kind == "owlv2":
        from etude.perceive.detector import OwlV2Perceiver

        kw = {k: sec[k] for k in ("model_name", "device") if sec.get(k)}
        return OwlV2Perceiver(**kw)
    if kind == "color_blob":
        from etude.perceive.detector import ColorBlobPerceiver

        return ColorBlobPerceiver()
    if kind == "fake":
        from etude.fakes import FakePerceiver

        return FakePerceiver()
    raise ValueError(f"unknown perceiver.kind {kind!r}")


def make_memory(sec: Mapping[str, Any]) -> Any:
    """Read-only memory store (design §3.1; frozen during evaluation, §8.3)."""
    if sec.get("class"):
        return _construct(import_object(str(sec["class"])), sec)
    kind = str(sec["kind"])
    if kind == "none":
        return None
    if kind == "db":
        from etude.memory.store import MemoryDB

        root = sec.get("root")
        return MemoryDB(Path(str(root)).expanduser() if root else None, frozen=bool(sec.get("frozen", True)))
    if kind == "fake":
        from etude.fakes import FakeMemoryStore

        return FakeMemoryStore()
    raise ValueError(f"unknown memory.kind {kind!r}")


def make_trace(sec: Mapping[str, Any]) -> Any:
    """Trace writer for ``traces/<episode_id>/`` (design §4 目录树)."""
    if sec.get("class"):
        return _construct(import_object(str(sec["class"])), sec)
    kind = str(sec["kind"])
    if kind == "none":
        return None
    if kind == "file":
        from etude.trace.writer import FileTraceWriter

        d = sec.get("dir")
        return FileTraceWriter(Path(str(d)).expanduser() if d else None)
    if kind == "fake":
        from etude.fakes import FakeTraceWriter

        return FakeTraceWriter()
    raise ValueError(f"unknown trace.kind {kind!r}")


def make_verifier(sec: Mapping[str, Any], perceiver: Any, trace: Any) -> Any:
    """Code verifier (design §4 判决 JSON). Keyframes go through the trace writer."""
    if sec.get("class"):
        return _construct(import_object(str(sec["class"])), sec)
    kind = str(sec["kind"])
    if kind == "code":
        from etude.verifier.verdict import CodeVerifier

        return CodeVerifier(perceiver, keyframe_fn=(trace.keyframe if trace is not None else None))
    if kind == "fake":
        from etude.fakes import FakeVerifier

        return FakeVerifier(kinds=list(sec.get("kinds") or []))
    raise ValueError(f"unknown verifier.kind {kind!r}")


class HarnessRunner:
    """NodeRunner plus the MCP state bridge (design §2 P2, §3.3). Satisfies
    :class:`etude.adapter.xpl_model.RunnerProtocol`.

    Build it with :meth:`from_config` (the deploy config) or directly from parts.
    ``state`` and ``mcp`` may be None (fake planner: no P1 process, no MCP).
    """

    def __init__(self, runner: Any, *, state: Any = None, mcp: Any = None, task: str = ""):
        self.runner = runner
        self.state = state
        self.mcp = mcp
        self.task = task
        self._synced_verdicts = 0
        self._episode_open = False

    # -- construction --------------------------------------------------------
    @classmethod
    def from_config(cls, cfg: Mapping[str, Any]) -> "HarnessRunner":
        """Assemble every P2 component from the resolved deploy config (design §2 P2 row)."""
        from etude.runner import NodeRunner

        task = str(cfg.get("task_name") or "")
        backend = make_backend(_section(cfg, "backend", "remote"))
        perceiver = make_perceiver(_section(cfg, "perceiver", "owlv2"))
        memory = make_memory(_section(cfg, "memory", "none"))
        trace = make_trace(_section(cfg, "trace", "file"))
        verifier = make_verifier(_section(cfg, "verifier", "code"), perceiver, trace)
        psec = _section(cfg, "planner", "pi_rpc")
        state = mcp = None
        plan_wait_s: float | None = psec.get("wait_in_get_action_s")
        if psec.get("class"):
            planner = _construct(import_object(str(psec["class"])), psec)
        elif psec["kind"] == "pi_rpc":
            from etude.mcp.server import McpServerThread
            from etude.mcp.state import HarnessState
            from etude.planner.rpc_driver import PiRpcPlanner, load_planner_config

            pcfg = load_planner_config()
            mcfg = dict(pcfg.get("mcp") or {})
            host = str(psec.get("mcp_host") or mcfg.get("host", "127.0.0.1"))
            port = int(psec["mcp_port"] if psec.get("mcp_port") is not None else mcfg.get("port", 8765))
            state = HarnessState(perceiver=perceiver, memory=memory, trace=trace, task=task,
                                 exec_steps=int(cfg.get("exec_steps") or 25),
                                 traces_root=getattr(trace, "traces_dir", None))
            mcp = McpServerThread(state, host=host, port=port).start()
            kw = {k: psec[k] for k in ("model", "provider", "thinking", "pi_cmd", "traces_root", "timeout_s", "env")
                  if psec.get(k) is not None}
            planner = PiRpcPlanner(state, mcp_url=mcp.url, trace=trace, config=pcfg, **kw)
            # design §2 慢回路: ≤ 90 s per get_action (official client hard limit 120 s), then hold 1 step
            wait = psec.get("wait_in_get_action_s", (pcfg.get("timeout") or {}).get("wait_in_get_action_s", 90))
            plan_wait_s = None if wait is None else float(wait)
            _log(f"MCP server at {mcp.url}; planner = pi --mode rpc; get_action waits <= {plan_wait_s} s")
        elif psec["kind"] == "fake":
            from etude.fakes import FakePlanner

            planner = FakePlanner()
        else:
            raise ValueError(f"unknown planner.kind {psec['kind']!r}")
        runner = NodeRunner(backend, perceiver, planner, verifier, memory, trace, task=task, plan_wait_s=plan_wait_s,
                            exec_steps=int(cfg.get("exec_steps") or 25),
                            fixed_vla_exec_steps=bool(cfg.get("vla_exec_steps_fixed", False)))
        return cls(runner, state=state, mcp=mcp, task=task)

    # -- RunnerProtocol ------------------------------------------------------
    def update_obs(self, frame: ObsFrame) -> None:
        """Forward to NodeRunner and mirror into the MCP state (design §8.2 F5)."""
        if self.state is not None and not self._episode_open:
            self.state.start_episode(self.task, frame.instruction)
        self._episode_open = True
        self.runner.update_obs(frame)
        if self.state is not None:
            self.state.update_obs(frame)

    def get_action(self) -> list[dict[str, Any]]:
        """NodeRunner chunk; afterwards the new verdicts, the chunk count and a keyframe
        for ``inspect`` are mirrored into the MCP state (design §3.3, §4)."""
        if self.state is not None:
            # scope inspect's PNG refs to this episode before any planning starts (design §3.5)
            self.state.set_episode_id(self.runner.ensure_episode_id())
            if self.runner.latest is not None:
                self.state.add_keyframe(f"t{self.runner.latest.t}")
        actions = self.runner.get_action()
        if self.state is not None:
            for v in self.runner.verdicts[self._synced_verdicts:]:
                self.state.set_verdict(v)
            self.state.note_chunks(1)
        self._synced_verdicts = len(self.runner.verdicts)
        return actions

    def reset(self) -> None:
        """Close the episode (trace) and clear runner and MCP episode state (design §3.5)."""
        self.runner.reset()
        if self.state is not None:
            self.state.start_episode(self.task, "")
        self._synced_verdicts = 0
        self._episode_open = False

    def close(self) -> None:
        """Stop the MCP server thread (process shutdown)."""
        if self.mcp is not None:
            self.mcp.stop()
            self.mcp = None

    @property
    def node_log(self) -> list[tuple[str, str, int]]:
        """(node id, verdict kind, t_chunk end) of this episode's verdicts (demo display)."""
        return [(v.node, v.kind, int(v.t_chunk[1])) for v in self.runner.verdicts]


__all__ = ["HarnessRunner", "make_memory", "make_perceiver", "make_trace", "make_verifier"]
