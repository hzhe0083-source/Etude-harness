"""``python -m etude.mcp`` — demo / manual server for the 7 MCP tools (design §4).

* ``--demo``: build a :class:`HarnessState` on fakes, call all 7 tools through
  an in-process MCP client, then one ``budget`` call over real streamable-http.
* ``--serve [--port N]``: serve the fake state on ``http://127.0.0.1:N/mcp``
  until Ctrl-C (for trying pi + an MCP adapter by hand; no real env).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

import anyio


def _fake_state() -> Any:
    """HarnessState over fakes: one frame at t=100 with step_lim 800, a memory entry, a verdict."""
    from etude.core.types import PredicateResult, SequenceEntry, Verdict, instruction_hash
    from etude.fakes import FakeMemoryStore, FakePerceiver, FakeTraceWriter, fake_frame, three_node_sequence
    from etude.mcp.state import HarnessState

    instr = "stack the bowls"
    mem = FakeMemoryStore(
        [
            SequenceEntry(
                seq_id="s0001",
                task="stack_bowls",
                instruction=instr,
                instruction_hash=instruction_hash(instr),
                nodes=three_node_sequence("red"),
                evidence_refs=["kf/ep_x/n2_after_cam_head.png"],
                lineage="ep_x",
            )
        ]
    )
    import tempfile
    from pathlib import Path

    from etude.trace.writer import save_png

    # two episodes' keyframes on disk: inspect may open only the current one's (design §3.5)
    troot = Path(tempfile.mkdtemp(prefix="etude-mcp-demo-traces-"))
    for ep in ("ep_now", "ep_old"):
        (troot / ep / "keyframes").mkdir(parents=True)
        save_png(fake_frame(t=10, step_lim=800, instruction=instr).images["cam_head"], troot / ep / "keyframes" / "t10_cam_head.png")
    state = HarnessState(
        perceiver=FakePerceiver(),
        memory=mem,
        trace=FakeTraceWriter(),
        task="stack_bowls",
        instruction=instr,
        traces_root=troot,
    )
    state.start_episode("stack_bowls", instr, episode_id="ep_now")
    state.add_keyframe("n1_before", fake_frame(t=50, step_lim=800, instruction=instr))
    state.update_obs(fake_frame(t=100, step_lim=800, instruction=instr))
    state.set_verdict(
        Verdict(
            node="n1",
            kind="Pass",
            predicates={"arm_home(right)": PredicateResult(v=True, conf=0.95, src="joint_state")},
            evidence_refs=["kf/fake/n1_after_cam_head.png"],
            t_chunk=[2, 4],
        )
    )
    return state


def _text(res: Any) -> dict[str, Any]:
    for c in res.content:
        if getattr(c, "type", None) == "text":
            return json.loads(c.text)
    return {}


def _n_images(res: Any) -> int:
    return sum(1 for c in res.content if getattr(c, "type", None) == "image")


async def _demo_inproc(state: Any) -> None:
    from mcp import Client

    from etude.core.types import MCP_TOOLS
    from etude.fakes import three_node_sequence
    from etude.frozen.ast_check import CHEAT_SAMPLES
    from etude.mcp.server import build_server

    server = build_server(state)
    async with Client(server) as client:
        listed = await client.list_tools()
        names = sorted(t.name for t in listed.tools)
        print("tools:", names)
        assert names == sorted(MCP_TOOLS), names

        r = await client.call_tool("budget", {})
        print("budget ->", _text(r))

        r = await client.call_tool("observe", {})
        meta = _text(r)
        print("observe(latest) -> views=%s t=%s images=%d left.grip_w=%s" % (
            meta["views"], meta["t"], _n_images(r), meta["arms"]["left"]["gripper_width_measured_m"]))
        assert _n_images(r) == 3

        r = await client.call_tool("observe", {"views": ["wrist_l"], "frame": "n1_before"})
        print("observe(n1_before, wrist_l) -> t=%s images=%d" % (_text(r)["t"], _n_images(r)))

        r = await client.call_tool("observe", {"frame": "nope"})
        print("observe(unknown) -> is_error=%s %s" % (r.is_error, _text(r)))

        r = await client.call_tool("inspect", {"frame_ref": "latest", "region": {"x": 190, "y": 250, "w": 80, "h": 60}, "zoom": 3})
        print("inspect(latest head) ->", _text(r), "images=%d" % _n_images(r))
        r = await client.call_tool("inspect", {"frame_ref": "latest", "region": {"x": 0, "y": 0, "w": 10, "h": 10}, "zoom": 9})
        print("inspect(zoom 9) -> is_error=%s %s" % (r.is_error, _text(r)))
        r = await client.call_tool("inspect", {"frame_ref": "../../etc/passwd.png", "region": {"x": 0, "y": 0, "w": 10, "h": 10}})
        print("inspect(path escape) -> is_error=%s %s" % (r.is_error, _text(r)))
        region = {"x": 0, "y": 0, "w": 64, "h": 48}
        r = await client.call_tool("inspect", {"frame_ref": "ep_now/keyframes/t10_cam_head.png", "region": region})
        print("inspect(own episode keyframe) -> is_error=%s images=%d" % (r.is_error, _n_images(r)))
        assert not r.is_error
        r_old = await client.call_tool("inspect", {"frame_ref": "ep_old/keyframes/t10_cam_head.png", "region": region})
        r_none = await client.call_tool("inspect", {"frame_ref": "ep_now/keyframes/missing.png", "region": region})
        print("inspect(earlier episode keyframe) -> is_error=%s %s" % (r_old.is_error, _text(r_old)))
        assert r_old.is_error and r_none.is_error
        assert _text(r_old)["error"].split("'")[2] == _text(r_none)["error"].split("'")[2], "error text must not leak existence"

        for q, v in (("red block", "head"), ("red block", "wrist_l"), ("green cup", "head")):
            r = await client.call_tool("perceive", {"query": q, "view": v})
            print(f"perceive({q!r}, {v}) ->", _text(r))

        r = await client.call_tool("memory_lookup", {"task": "stack_bowls", "instruction": "stack the bowls"})
        seqs = _text(r)["sequences"]
        print("memory_lookup -> %d seq, keys=%s, n_nodes=%d" % (len(seqs), sorted(seqs[0]), len(seqs[0]["nodes"])))
        assert "lineage" not in seqs[0]

        good = [n.to_dict() for n in three_node_sequence("red")]
        bad_cases = {
            "numeric target": [dict(good[0], args={"target": 'perceive("block at 0.31, -0.15")', "arm": "left"})],
            "raw coords": [dict(good[0], args={"target": "[0.31,-0.15,0.02]"})],
            "layout leak": [dict(good[1], instruction="pick the bowl as in layout 7")],
            "task-name leak": [dict(good[1], instruction="do stack_bowls quickly")],
            "privileged": [dict(good[1], instruction=CHEAT_SAMPLES["privileged_instruction"])],
            "self-declared done": [dict(good[1], done=True)],
            "bad predicate": [dict(good[1], postcondition=["task_success"])],
            "empty": [],
        }
        for label, nodes in bad_cases.items():
            r = await client.call_tool("submit_sequence", {"nodes": nodes})
            out = _text(r)
            print(f"submit_sequence[{label}] -> ok={out['ok']} first_error={out['errors'][0] if out['errors'] else None!r}")
            assert out["ok"] is False

        # Planner omits stop/on_fail -> filled from evolvable/rules/recovery.yaml defaults.
        minimal = [{"id": "n1", "executor": "vla", "instruction": "pick up the red block",
                    "postcondition": ["gripper_holding(left)"]}]
        r = await client.call_tool("submit_sequence", {"nodes": json.dumps(minimal)})
        print("submit_sequence[minimal json string] ->", _text(r))
        r = await client.call_tool("submit_sequence", {"nodes": good})
        print("submit_sequence[good] ->", _text(r))
        accepted = state.take_submitted()
        assert accepted is not None and [n.id for n in accepted] == ["n1", "n2", "n3"]

        r = await client.call_tool("get_verdict", {"node_id": "n1"})
        print("get_verdict(n1) ->", _text(r)["kind"], sorted(_text(r)))
        r = await client.call_tool("get_verdict", {"node_id": "n9"})
        print("get_verdict(n9) -> is_error=%s %s" % (r.is_error, _text(r)))

    print("tool counts:", state.tool_counts())


async def _demo_http(url: str) -> None:
    from mcp import Client

    async with Client(url) as client:
        r = await client.call_tool("budget", {})
        print(f"http {url} budget ->", _text(r))


def demo() -> int:
    """Run all 7 tools in-process, then one call over streamable-http (design §4)."""
    from etude.mcp.server import McpServerThread

    state = _fake_state()
    anyio.run(_demo_inproc, state)
    with McpServerThread(state, port=0) as srv:
        anyio.run(_demo_http, srv.url)
    print("planner trace events:", len([e for e in state.trace.events if e[0] == "planner"]))
    print("etude.mcp demo OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m etude.mcp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="run the fake-backed demo of all 7 tools")
    ap.add_argument("--serve", action="store_true", help="serve a fake-backed state over streamable-http")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None, help="default: planner/config.yaml mcp.port")
    args = ap.parse_args(argv)
    if args.demo:
        return demo()
    if args.serve:
        from etude.mcp.server import serve
        from etude.planner.rpc_driver import load_planner_config

        port = args.port or int(load_planner_config()["mcp"]["port"])
        print(f"serving fake HarnessState on http://{args.host}:{port}/mcp (Ctrl-C to stop)", file=sys.stderr)
        serve(_fake_state(), host=args.host, port=port)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
