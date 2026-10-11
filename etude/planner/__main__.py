"""``python -m etude.planner`` — demo / helpers for the pi RPC planner (design §2 P1, §8.2 F5).

* ``--demo``: start the MCP server thread on a fake HarnessState, drive
  ``etude/planner/fake_pi.py`` through :class:`PiRpcPlanner` and walk
  prompt → submit_sequence → agent_settled (ok, fix-errors, no-submit), the
  timeout → abort → settled path, the ignore-abort → kill path, and the
  VLM-budget hard stop. Nothing calls a real model.
* ``--print-command``: show the real ``pi --mode rpc`` argv from planner/config.yaml.
* ``--print-prompt``: show the prompt built for a fake request.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

FAKE_PI = Path(__file__).resolve().parent / "fake_pi.py"


def _fake_state() -> Any:
    from etude.mcp.__main__ import _fake_state as mk

    return mk()


def _run(planner: Any, scenario: str, request: dict[str, Any], **override: Any) -> Any:
    from etude.planner.rpc_driver import PlannerError

    planner.reset()
    planner.env["FAKE_PI_SCENARIO"] = scenario
    for k, v in override.items():
        setattr(planner, k, v)
    planner.start_episode(f"demo-{scenario}")
    try:
        nodes = planner.plan(request)
        res = planner.results[-1]
        print(f"[{scenario}] nodes={[n.id + ':' + n.executor for n in nodes]} settled={res.settled} "
              f"submit_attempts={res.submit_attempts} tools={res.tool_calls} blocked={res.blocked_calls} "
              f"cost=${res.cost_usd:.4f} tokens={res.tokens_total} wall={res.wall_s:.2f}s guard={res.guard_active}")
        return res
    except PlannerError as e:
        res = e.result
        print(f"[{scenario}] {type(e).__name__}: {e} | settled={res.settled if res else None} "
              f"timed_out={res.timed_out if res else None} killed={res.killed if res else None} "
              f"cost=${(res.cost_usd if res else 0):.4f} wall={(res.wall_s if res else 0):.2f}s")
        return e


def demo() -> int:
    """Walk the RPC driver through all paths on fake_pi (design §8.2 F5)."""
    from etude.mcp.server import McpServerThread
    from etude.planner.request import build_prompt
    from etude.planner.rpc_driver import (
        PiRpcPlanner,
        PlannerBudgetExhausted,
        PlannerNoSequence,
        PlannerTimeout,
    )

    state = _fake_state()
    request = {"instruction": "stack the bowls", "task": "stack_bowls", "reason": "start"}
    print("---- prompt preview ----")
    print(build_prompt(request, state))
    print("------------------------")

    with tempfile.TemporaryDirectory(prefix="etude-planner-demo-") as tmp, McpServerThread(state, port=0) as srv:
        planner = PiRpcPlanner(
            state,
            pi_cmd=[sys.executable, str(FAKE_PI)],
            traces_root=Path(tmp),
            timeout_s=20.0,
            abort_grace_s=3.0,
            mcp_url=srv.url,
            trace=state.trace,
        )
        r = _run(planner, "ok", request)
        print("argv:", " ".join(c if len(c) < 100 else f"<{len(c)} chars>" for c in planner.build_command()))
        assert not isinstance(r, Exception) and r.nodes and r.settled and r.cost_usd > 0

        r = _run(planner, "fix_errors", dict(request, reason="node_failed",
                                             failed_node=state.take_submitted()[1].to_dict() if state.take_submitted() else None))
        assert not isinstance(r, Exception) and r.submit_attempts == 2 and r.blocked_calls == 1

        r = _run(planner, "no_submit", request)
        assert isinstance(r, PlannerNoSequence)

        r = _run(planner, "hang", request, timeout_s=1.5)
        assert isinstance(r, PlannerTimeout) and r.result.settled and r.result.timed_out and not r.result.killed

        r = _run(planner, "ignore_abort", request, timeout_s=1.0, abort_grace_s=1.0)
        assert isinstance(r, PlannerTimeout) and r.result.killed

        # per-episode LLM-request cap (assistant turns; frozen budget.vlm_requests_per_episode): one left
        state.max_vlm_requests = (state.max_vlm_requests - state.vlm_requests_left) + 1
        r = _run(planner, "ok", request, timeout_s=20.0)
        res = r.result if isinstance(r, Exception) else r
        print(f"[request cap] requests={res.requests} aborted={res.aborted} errors={res.errors[:1]} "
              f"budget={state.budget()}")
        assert res.aborted and any("request budget" in e for e in res.errors) and state.budget()["calls_left"] == 0

        state.max_vlm_calls = state.vlm_calls_used  # exhaust the per-episode VLM budget
        r = _run(planner, "ok", request, timeout_s=20.0)
        assert isinstance(r, PlannerBudgetExhausted)

        planner.stop()
        print("totals:", json.dumps(planner.totals()))
        sess = Path(tmp) / "demo-ok" / "planner"
        print("session files (ok):", sorted(p.name for p in sess.iterdir()))
    kinds = [e[1].get("kind") for e in state.trace.events if e[0] == "planner"]
    print("planner trace kinds:", {k: kinds.count(k) for k in sorted(set(map(str, kinds)))})
    print("etude.planner demo OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m etude.planner", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--print-command", action="store_true", help="real pi argv for episode 'example'")
    ap.add_argument("--print-prompt", action="store_true")
    args = ap.parse_args(argv)
    if args.demo:
        return demo()
    if args.print_command or args.print_prompt:
        from etude.planner.request import build_prompt
        from etude.planner.rpc_driver import PiRpcPlanner

        state = _fake_state()
        if args.print_prompt:
            print(build_prompt({"instruction": "stack the bowls", "task": "stack_bowls"}, state))
        if args.print_command:
            with tempfile.TemporaryDirectory() as tmp:
                p = PiRpcPlanner(state, traces_root=Path(tmp))
                p.start_episode("example")
                cmd = p.build_command()
                shown = [c if len(c) < 120 else c[:60].replace("\n", " ") + f"...<{len(c)} chars>" for c in cmd]
                print(" ".join(shown))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
