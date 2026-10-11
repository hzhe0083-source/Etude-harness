#!/usr/bin/env python
"""End-to-end fake episodes through the real P2 stack (demo script, NOT a test).

Design references: §2 (P2 harness server, deploy.py allow-list), §3.1/§3.3
(node loop, recovery, memory writer), §3.5 (budget hard stop, frozen memory),
§4 (node schema, verdict JSON, trace directory tree, keyframes).

Real components: ``etude.runner.NodeRunner``, ``etude.verifier.CodeVerifier``,
``etude.perceive.detector.ColorBlobPerceiver``, ``etude.memory.MemoryDB`` +
``write_if_passed``, ``etude.trace.FileTraceWriter`` / ``TraceReader``.
Fakes: ``FakePolicy`` (π0.5 stand-in), ``FakePlanner`` (P1 stand-in) and a
``FakeTaskEnv`` subclass (:class:`PickWorld`) whose red block is rendered from
the measured ee pose, so the verifier's predicates hold or fail for real.

Episodes (each prints its verdict sequence):

A. all nodes Pass → the offline writer admits the sequence into memory;
B. n2 Fail → 2 in-place retries → retreat to the pose recorded when n1 passed
   → code re-verification of anchor n1 (postconditions + scene_intact, 0 VLM
   calls) holds → n2 Pass (memory refuses the episode: not every verdict is Pass);
B2. same, but the red block is knocked 6 cm away during the last failed
   attempt → that Fail's scene_intact stays true (the red block is n2's own,
   implicit target; the watched blue block did not move) → after the retreat
   the anchor re-verification fails (red block, n1's ee_move target, moved vs
   the t=0 keyframe) → anchor dropped → replan with ``scene_intact=false``
   instead of retrying n2;
H. a node after the lift fails (n3 ``arm_home(left)`` while holding the block)
   → retreat to the n2 anchor → re-verification holds: the lifted red block is
   n2's implicit target, not a disturbance (scene_intact=true, no anchors
   dropped);
C. transient IK failure → n1 Blocked → replan → Pass;
D. permanent IK failure → Blocked/replan until the 12-call VLM budget is
   exhausted → no-VLM path (top memory sequence, then plain pi0.5 node);
E. memory frozen (evaluation period) → append / write_if_passed raise;
F. trace.jsonl, planner.jsonl, episode.json and PNG keyframes on disk;
G. slow planner: get_action waits at most ``plan_wait_s`` (90 s in production,
   design §2 慢回路) and returns 1-step hold chunks while planning continues in
   the background.

Usage::

    uv run python scripts/demo_fake_episode.py            # temp dir, removed afterwards
    uv run python scripts/demo_fake_episode.py --out DIR  # keep traces/ and memory_db/ in DIR

It never touches the repo's ``traces/`` or ``memory_db/``.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etude.core.schema import make_perceive, validate_verdict  # noqa: E402
from etude.core.types import IMAGE_HW, Node, NodeArgs, ObsFrame, OnFail, StopSpec  # noqa: E402
from etude.executor.servo_demo import ServoDemoEnv, augment_obs  # noqa: E402
from etude.fakes import Block, FakePlanner, FakePolicy, fake_obs  # noqa: E402
from etude.memory.store import MemoryDB, MemoryFrozenError  # noqa: E402
from etude.memory.writer import write_if_passed  # noqa: E402
from etude.perceive.detector import ColorBlobPerceiver  # noqa: E402
from etude.runner import NodeRunner  # noqa: E402
from etude.trace.reader import TraceReader  # noqa: E402
from etude.trace.writer import FileTraceWriter  # noqa: E402
from etude.verifier.verdict import CodeVerifier  # noqa: E402

TASK = "stack_bowls"  # a dev task, so P5 (seed 0, layout < 50) classifies the episodes as E (design §3.2)
INSTRUCTION = "pick up the red block, lift it and let it go"
#: Fake forward kinematics: ee z rises 0.3 m per rad of mean left-arm joint change.
FK_Z_PER_RAD = 0.3
#: Measured finger gap when the block is between the fingers (≥ gripper.holding_width_min).
HELD_WIDTH_M = 0.02
BLOCK_SIZE_M = 0.04


# ---------------------------------------------------------------------------
# fake world (P4 stand-in) and fake π0.5 (P3 stand-in)
# ---------------------------------------------------------------------------


class PickWorld(ServoDemoEnv):
    """FakeTaskEnv with a red block that follows physics-lite rules (demo only).

    * joint dicts move the left ee in z by ``FK_Z_PER_RAD`` × mean Δq;
    * closing the left gripper within 3 cm (xy) of the block grasps it, unless
      ``slip_closures`` > 0 (each closure then slips and decrements it);
    * a held block moves with the ee; opening the gripper drops it on the table;
    * ``ik_fail_steps`` drops that many left-arm ee commands (transient IK
      failure); ``ik_fail_arms`` (inherited) drops them forever;
    * ``displace_after_slips`` = k > 0: the k-th slipped closure knocks the
      block ``displace_m`` (x, y metres) away (scenario B2);
    * renders: head view (block xy/z → pixels), left wrist (pinhole below the
      ee when on the table; large blob in the finger region when held), right
      wrist (static blue block).

    ``fake_judge()`` is the offline P5 stand-in (lifted ≥ 5 cm, then released).
    Only this script reads it, after the episode; the runner never sees it.
    """

    def __init__(self, *args: Any, slip_closures: int = 0, ik_fail_steps: int = 0, displace_after_slips: int = 0,
                 displace_m: tuple[float, float] = (0.0, 0.06), **kwargs: Any):
        super().__init__(*args, **kwargs)
        d = object.__setattr__
        d(self, "_block", np.array([0.36, 0.21, 0.0]))
        d(self, "_held", False)
        d(self, "_max_lift", 0.0)
        d(self, "_released_after_lift", False)
        d(self, "_slip", int(slip_closures))
        d(self, "_ik_drop", int(ik_fail_steps))
        d(self, "_displace_after", int(displace_after_slips))
        d(self, "_displace_m", np.array(displace_m, dtype=float))
        d(self, "_slips_done", 0)
        d(self, "fake_events", [])

    # -- dynamics ------------------------------------------------------------
    def take_action(self, action: Mapping[str, Any]) -> None:
        g = object.__getattribute__
        q0 = g(self, "_joints")["left"].copy()
        echo0 = g(self, "_echo")["left"]
        transient = "left_ee_pose" in action and g(self, "_ik_drop") > 0
        if transient:
            g(self, "_ik_fail").add("left")
        try:
            super().take_action(action)
        finally:
            if transient:
                object.__setattr__(self, "_ik_drop", g(self, "_ik_drop") - 1)
                if g(self, "_ik_drop") == 0:
                    g(self, "_ik_fail").discard("left")
        ee = g(self, "_ee")["left"]
        if "left_arm_joint_state" in action:
            ee[2] += FK_Z_PER_RAD * float(np.mean(g(self, "_joints")["left"] - q0))
        echo1 = g(self, "_echo")["left"]
        block = g(self, "_block")
        ev = g(self, "fake_events")
        if echo0 > 0.5 >= echo1 and not g(self, "_held"):  # a closure
            near = float(np.linalg.norm(ee[:2] - block[:2])) < 0.03
            if near and g(self, "_slip") > 0:
                object.__setattr__(self, "_slip", g(self, "_slip") - 1)
                object.__setattr__(self, "_slips_done", g(self, "_slips_done") + 1)
                ev.append(("slip", g(self, "fake_t")))
                if g(self, "_slips_done") == g(self, "_displace_after"):  # the fumble knocks the block away
                    block[:2] += g(self, "_displace_m")
                    ev.append(("displaced", g(self, "fake_t")))
            elif near:
                object.__setattr__(self, "_held", True)
                object.__setattr__(self, "_z_grasp", float(ee[2]))
                ev.append(("grasp", g(self, "fake_t")))
        if g(self, "_held"):
            lift = float(ee[2]) - g(self, "_z_grasp")
            block[:] = [ee[0], ee[1], max(0.0, lift)]
            object.__setattr__(self, "_max_lift", max(g(self, "_max_lift"), lift))
            if echo1 > 0.5:  # opened: drop on the table
                object.__setattr__(self, "_held", False)
                block[2] = 0.0
                if g(self, "_max_lift") >= 0.05:
                    object.__setattr__(self, "_released_after_lift", True)
                ev.append(("release", g(self, "fake_t")))

    def _grip_width(self, arm: str) -> float:
        g = object.__getattribute__
        echo = float(np.clip(g(self, "_echo")[arm], 0.0, 1.0))
        if arm == "left" and g(self, "_held") and echo <= 0.5:
            return HELD_WIDTH_M
        return 0.04 * echo

    # -- rendering -----------------------------------------------------------
    def get_obs(self) -> dict[str, Any]:
        g = object.__getattribute__
        ee = g(self, "_ee")["left"]
        bx, by, bz = g(self, "_block")
        hu, hv = 320.0 - by * 500.0, 400.0 - bx * 400.0 - bz * 600.0
        blocks = [Block("cam_head", "red", (int(hu - 25), int(hv - 20), 50, 40)),
                  Block("cam_head", "blue", (430, 260, 60, 40)),
                  Block("cam_right_wrist", "blue", (100, 300, 80, 80))]
        if g(self, "_held"):
            blocks.append(Block("cam_left_wrist", "red", (220, 360, 200, 120)))
        else:
            h = max(float(ee[2]) - bz, 0.05)
            f = g(self, "_f")
            u = IMAGE_HW[1] / 2.0 - (by - ee[1]) * f / h
            v = IMAGE_HW[0] / 2.0 - (bx - ee[0]) * f / h
            s = int(round(BLOCK_SIZE_M * f / h))
            blocks.append(Block("cam_left_wrist", "red", (int(round(u - s / 2)), int(round(v - s / 2)), s, s)))
        return fake_obs(g(self, "fake_t"), with_gripper=False, step_lim=None, blocks=blocks,
                        instruction=g(self, "_instruction"), joints=g(self, "_joints"), ee_pose=g(self, "_ee"),
                        grip_echo=g(self, "_echo"))

    def fake_judge(self) -> bool:
        """Offline official-judge stand-in (P5): lifted ≥ 5 cm, then released."""
        return bool(object.__getattribute__(self, "_released_after_lift"))


class DemoPolicy(FakePolicy):
    """FakePolicy whose gripper column opens for 5 steps then closes (one grasp
    attempt per chunk), and stays closed while the measured width says it holds."""

    def infer(self, obs: ObsFrame, prompt: str) -> np.ndarray:
        chunk = super().infer(obs, prompt)
        w = obs.gripper_width_measured("left")
        holding = obs.gripper_cmd_echo("left") <= 0.2 and w is not None and w >= 0.012
        chunk[:, 6] = 0.0
        if not holding:
            chunk[:5, 6] = 1.0
        return chunk


class DemoPlanner(FakePlanner):
    """FakePlanner that answers [] once the sequence is done (runner then holds).
    ``delay_s`` makes the first call slow (scenario G); ``on_scene_disturbed``
    is answered to a replan request carrying ``scene_intact=false`` (B2),
    ``on_node_failed`` to any other ``node_failed`` request (H)."""

    def __init__(self, *args: Any, delay_s: float = 0.0, on_scene_disturbed: list[Node] | None = None,
                 on_node_failed: list[Node] | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.delay_s = delay_s
        self.on_scene_disturbed = on_scene_disturbed
        self.on_node_failed = on_node_failed

    def plan(self, request: dict[str, Any]) -> list[Node]:
        if self.delay_s and not self.requests:
            time.sleep(self.delay_s)
        if request.get("reason") == "sequence_done":
            self.requests.append(copy.deepcopy(request))
            return []
        if self.on_scene_disturbed is not None and request.get("scene_intact") is False:
            self.requests.append(copy.deepcopy(request))
            return copy.deepcopy(self.on_scene_disturbed)
        if self.on_node_failed is not None and request.get("reason") == "node_failed":
            self.requests.append(copy.deepcopy(request))
            return copy.deepcopy(self.on_node_failed)
        return super().plan(request)


def reapproach_sequence() -> list[Node]:
    """What P1 might answer to ``scene_intact=false`` (B2): re-locate the block
    and re-approach it (no ``arm_home(right)``: the right arm has moved since)."""
    nodes = pick_sequence()
    nodes[0].instruction = "move the left gripper above the red block again"
    nodes[0].postcondition = ['not_moved("red block")']
    return nodes


def lift_then_fail_sequence() -> list[Node]:
    """n1/n2 of :func:`pick_sequence`, then a vla node (scenario H) whose
    postcondition cannot hold while the arm is raised: it fails, and its
    ``previous_node`` fallback retreats to the n2 anchor (block held, lifted)."""
    nodes = pick_sequence()[:2]
    nodes.append(Node(id="n3", executor="vla", instruction="put the left arm back home",
                      args=NodeArgs(arm="left"), stop=StopSpec(stall_chunks=2, max_chunks=2, exec_steps=10),
                      postcondition=["arm_home(left)"], on_fail=OnFail(retry=0, then="previous_node"),
                      evidence=["ee_pose"]))
    return nodes


def release_sequence() -> list[Node]:
    """P1's answer to H's replan: open the gripper (n3 of :func:`pick_sequence`, as n4)."""
    node = pick_sequence()[2]
    node.id = "n4"
    return [node]


def pick_sequence() -> list[Node]:
    """The plan P1 would submit (design §4 node schema; targets only via perceive)."""
    return [
        Node(id="n1", executor="ee_move", instruction="move the left gripper above the red block",
             args=NodeArgs(target=make_perceive("red block"), arm="left"),
             stop=StopSpec(stall_chunks=2, max_chunks=8, exec_steps=10),
             postcondition=["arm_home(right)"], on_fail=OnFail(retry=1, then="replan"),
             evidence=["ee_pose", "detection"]),
        Node(id="n2", executor="vla", instruction="pick up the red block",
             args=NodeArgs(arm="left"), stop=StopSpec(stall_chunks=2, max_chunks=3, exec_steps=25),
             postcondition=["gripper_holding(left)", "target_lifted(>0.05m)"],
             on_fail=OnFail(retry=2, then="previous_node"),
             evidence=["wrist_rgb_diff", "gripper_width", "ee_pose"]),
        Node(id="n3", executor="gripper", instruction="open the left gripper",
             args=NodeArgs(cmd="open", arm="left"), stop=StopSpec(stall_chunks=1, max_chunks=2, exec_steps=10),
             postcondition=['moved("red block")'], on_fail=OnFail(retry=1, then="abort"),
             evidence=["gripper_width", "wrist_rgb_diff"]),
    ]


# ---------------------------------------------------------------------------
# episode driver (mirror of XPolicyLab deploy.py eval_one_episode + _augment)
# ---------------------------------------------------------------------------


def run_episode(ep_id: str, env: PickWorld, *, memory: MemoryDB, traces: Path, planner: FakePlanner | None = None,
                plan_wait_s: float | None = None):
    """One episode: deploy.py-style loop (update_obs per step, get_action per chunk)."""
    perceiver = ColorBlobPerceiver()
    trace = FileTraceWriter(traces)
    # the task's object vocabulary (here: the static blue block) is watched by scene_intact
    # besides the objects the plan mentions (design §4 derived field, P2)
    verifier = CodeVerifier(perceiver, keyframe_fn=trace.keyframe, scene_queries=["blue block"])
    planner = planner or DemoPlanner(pick_sequence())
    runner = NodeRunner(DemoPolicy(grip=None), perceiver, planner, verifier, memory, trace, task=TASK,
                        plan_wait_s=plan_wait_s)
    runner.reset(episode_id=ep_id)
    while not env.is_episode_end():
        runner.update_obs(augment_obs(env, env.get_obs()))
        actions = runner.get_action()
        for i, a in enumerate(actions):
            env.take_action(a)
            if env.is_episode_end() or i + 1 == len(actions):
                break
            runner.update_obs(augment_obs(env, env.get_obs()))
    rec = runner.finish()
    assert rec is not None
    return rec, runner, planner, trace


def show(title: str, rec, runner: NodeRunner, planner: FakePlanner, env: PickWorld, trace: FileTraceWriter) -> list[str]:
    """Print one episode summary; return problems (schema errors, env violations)."""
    def short(v) -> str:
        tag = "" if v.source == "code_verifier" else "*"
        return f"{v.node}:{v.kind}{tag}"
    seq = " ".join(short(v) for v in rec.verdicts[:16]) + (f" ... ({len(rec.verdicts)})" if len(rec.verdicts) > 16 else "")
    ev = [json.loads(line) for line in (trace.ep_dir / "planner.jsonl").read_text().splitlines()]
    kinds = [e["event"].get("type") for e in ev]
    sources = [e["event"].get("source") for e in ev if e["event"].get("type") == "plan_response"]
    retreats = [e["event"] for e in ev if e["event"].get("type") in ("retreat", "retreat_end")]
    print(f"\n[{title}] episode {rec.episode_id}")
    print(f"  verdicts      : {seq}")
    print(f"  steps={rec.steps} chunks={rec.chunks} vlm_calls={rec.vlm_calls} budget={runner.budget()}")
    print(f"  planner reqs  : {[r['reason'] for r in planner.requests]}")
    print(f"  plan sources  : {sources}")
    if retreats:
        print(f"  retreats      : {[(r['type'], r.get('to') or r.get('status')) for r in retreats]}")
    for e in ev:
        if e["event"].get("type") == "anchor_recheck":
            r = e["event"]
            print(f"  anchor recheck: anchor={r['anchor']} t={r['t']} postcondition={r['postcondition']} "
                  f"skipped={r.get('skipped_arm_state')} scene_intact={r['scene_intact']} moved={r['moved_objects']} "
                  f"ok={r['ok']} vlm_calls={r['vlm_calls']}")
    non_pass = [(v.node, v.kind, v.scene_intact) for v in rec.verdicts if v.kind != "Pass"]
    if non_pass:
        print(f"  scene_intact  : {non_pass[:8]}")
    halts = [e["event"].get("reason") for e in ev if e["event"].get("type") == "halt"]
    print(f"  halt          : {halts}  world events: {env.fake_events[:6]}{' ...' if len(env.fake_events) > 6 else ''}")
    print(f"  recovery      : {[(e['event']['node'], e['event']['kind'], e['event']['action']) for e in ev if e['event'].get('type') == 'recovery'][:6]}")
    probs = [f"{rec.episode_id}: {e}" for v in rec.verdicts for e in validate_verdict(v)]
    probs += [f"{rec.episode_id}: TASK_ENV violation {n}" for n in env.fake_violations]
    if trace.errors:
        probs += [f"{rec.episode_id}: trace error {e}" for e in trace.errors]
    if "plan_error" in kinds:
        probs.append(f"{rec.episode_id}: planner error events")
    return probs


def main(argv: list[str] | None = None) -> int:
    """Run episodes A–F (module docstring); exit 0 when everything behaved as described."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None, help="keep traces/ and memory_db/ here (default: temp dir, removed)")
    args = ap.parse_args(argv)
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="etude-fake-episode-"))
    out.mkdir(parents=True, exist_ok=True)
    traces, mem_root = out / "traces", out / "memory_db"
    memory = MemoryDB(mem_root, frozen=False)
    problems: list[str] = []
    checks: list[tuple[str, bool]] = []
    print(f"output dir: {out}")

    def p5_result(rec: Any, env: Any, layout: int, passed: bool | None = None) -> dict[str, Any]:
        """P5 stand-in: the official judge outcome plus the official (task, seed, layout_id) the
        memory writer derives the split from (design §3.2; seed 0 layouts 0-49 = E)."""
        ok = env.fake_judge() if passed is None else passed
        return {"episode_id": rec.episode_id, "official_success": ok, "task": TASK, "seed": 0, "layout_id": layout}

    def world(**kw: Any) -> PickWorld:
        return PickWorld(step_lim=kw.pop("step_lim", 400), instruction=INSTRUCTION, **kw)

    # A. all Pass -> memory admits the sequence
    env = world()
    rec_a, runner, planner, trace = run_episode("demoA_all_pass", env, memory=memory, traces=traces)
    problems += show("A: all Pass -> admitted to memory", rec_a, runner, planner, env, trace)
    wr = write_if_passed(rec_a, p5_result(rec_a, env, layout=3), store=memory)  # P5 stand-in: offline judge result
    print(f"  memory writer : written={wr.written} reason={wr.reason} seq_id={wr.seq_id}")
    print(f"  memory lookup : {[(e.seq_id, [n.id for n in e.nodes]) for e in memory.lookup(TASK, INSTRUCTION)]}")
    checks.append(("A all Pass", rec_a.all_pass and [v.node for v in rec_a.verdicts] == ["n1", "n2", "n3"]))
    checks.append(("A written to memory", wr.written))

    # B. n2 Fail -> retry x2 in place -> previous_node retreat -> Pass
    n2 = pick_sequence()[1]
    env = world(step_lim=700, slip_closures=n2.stop.max_chunks * (n2.on_fail.retry + 1))
    rec_b, runner, planner, trace = run_episode("demoB_fail_retry_retreat", env, memory=memory, traces=traces)
    problems += show("B: n2 Fail -> 2 retries -> retreat to n1 pose -> Pass", rec_b, runner, planner, env, trace)
    wr_b = write_if_passed(rec_b, p5_result(rec_b, env, layout=4), store=memory)
    print(f"  memory writer : written={wr_b.written} reason={wr_b.reason} errors={wr_b.errors[:2]}")
    kinds_b = [(v.node, v.kind) for v in rec_b.verdicts]
    checks.append(("B Fail x3 then retreat then Pass",
                   kinds_b[:5] == [("n1", "Pass"), ("n2", "Fail"), ("n2", "Fail"), ("n2", "Fail"), ("n2", "Pass")]
                   and runner.backtracks == 1))
    checks.append(("B not admitted (retries)", not wr_b.written))
    ev_b = [json.loads(line)["event"] for line in (trace.ep_dir / "planner.jsonl").read_text().splitlines()]
    rc_b = [e for e in ev_b if e.get("type") == "anchor_recheck"]
    checks.append(("B anchor n1 re-verified in code, holds", len(rc_b) == 1 and rc_b[0]["ok"] and rc_b[0]["anchor"] == "n1"
                   and rc_b[0]["scene_intact"] is True and rc_b[0]["vlm_calls"] == 0
                   and all(v.scene_intact is True for v in rec_b.verdicts if v.kind != "Pass")))

    # B2. as B, but the 9th (last) slipped closure knocks the red block 6 cm away:
    # the retreat's anchor re-verification fails -> anchor dropped -> replan with scene_intact=false
    env = world(step_lim=900, slip_closures=n2.stop.max_chunks * (n2.on_fail.retry + 1),
                displace_after_slips=n2.stop.max_chunks * (n2.on_fail.retry + 1))
    rec_b2, runner, planner, trace = run_episode("demoB2_anchor_invalidated", env, memory=memory, traces=traces,
                                                 planner=DemoPlanner(pick_sequence(), on_scene_disturbed=reapproach_sequence()))
    problems += show("B2: block knocked away during n2 -> anchor n1 re-verification fails -> replan", rec_b2, runner,
                     planner, env, trace)
    ev_b2 = [json.loads(line)["event"] for line in (trace.ep_dir / "planner.jsonl").read_text().splitlines()]
    rc_b2 = [e for e in ev_b2 if e.get("type") == "anchor_recheck"]
    replans = [r for r in planner.requests if r.get("reason") == "node_failed"]
    print(f"  replan request: scene_intact={replans[0].get('scene_intact') if replans else None} "
          f"anchor_recheck={replans[0].get('anchor_recheck') if replans else None}")
    fails_b2 = [v for v in rec_b2.verdicts if v.node == "n2" and v.kind == "Fail"]
    checks.append(("B2 recheck fails on moved red block -> replan(scene_intact=false)",
                   len(rc_b2) >= 1 and not rc_b2[0]["ok"] and rc_b2[0]["scene_intact"] is False
                   and "red block" in rc_b2[0]["moved_objects"] and rc_b2[0]["vlm_calls"] == 0
                   and rc_b2[0]["kept"] == ["red block"]
                   and len(replans) >= 1 and replans[0].get("scene_intact") is False
                   and replans[0].get("anchor_invalidated") is True
                   and len(fails_b2) >= 3 and all(v.scene_intact is True for v in fails_b2[:3])
                   and rec_b2.verdicts[-1].kind == "Pass"))

    # H. a node after the lift fails -> retreat to the n2 anchor -> the lifted block is
    # n2's implicit target: re-verification holds, no false "scene disturbed"
    env = world(step_lim=400)
    rec_h, runner, planner, trace = run_episode("demoH_fail_after_lift", env, memory=memory, traces=traces,
                                                planner=DemoPlanner(lift_then_fail_sequence(),
                                                                    on_node_failed=release_sequence()))
    problems += show("H: node after the lift fails -> retreat to n2 -> anchor holds", rec_h, runner, planner, env, trace)
    ev_h = [json.loads(line)["event"] for line in (trace.ep_dir / "planner.jsonl").read_text().splitlines()]
    rc_h = [e for e in ev_h if e.get("type") == "anchor_recheck"]
    kinds_h = [(v.node, v.kind) for v in rec_h.verdicts]
    n3_h = [v for v in rec_h.verdicts if v.node == "n3" and v.kind != "Pass"]
    checks.append(("H n2 anchor holds after a later failure (lifted block = n2's target)",
                   kinds_h[:3] == [("n1", "Pass"), ("n2", "Pass"), ("n3", "Fail")]
                   and len(rc_h) >= 1 and rc_h[0]["anchor"] == "n2" and rc_h[0]["ok"]
                   and rc_h[0]["postcondition"] == "Pass" and rc_h[0]["scene_intact"] is True
                   and "red block" not in rc_h[0]["moved_objects"]
                   and not any(e.get("type") == "anchors_dropped" for e in ev_h)
                   and all(v.scene_intact is True for v in n3_h)
                   and not any(r.get("scene_intact") is False for r in planner.requests)
                   and rec_h.verdicts[-1].kind == "Pass"))

    # C. transient IK failure -> Blocked -> replan -> Pass
    env = world(ik_fail_steps=4)
    rec_c, runner, planner, trace = run_episode("demoC_blocked_replan", env, memory=memory, traces=traces)
    problems += show("C: transient IK failure -> Blocked -> replan -> Pass", rec_c, runner, planner, env, trace)
    checks.append(("C Blocked then replan", rec_c.verdicts[0].kind == "Blocked" and "node_failed" in
                   [r["reason"] for r in planner.requests] and rec_c.verdicts[-1].kind == "Pass"))

    # D. permanent IK failure -> VLM budget exhausted -> no-VLM path (memory top sequence, then pi0.5 only)
    env = world(step_lim=500, ik_fail_arms=("left",))
    rec_d, runner, planner, trace = run_episode("demoD_budget_exhausted", env, memory=memory, traces=traces)
    problems += show("D: permanent IK failure -> 12 VLM calls -> no-VLM path", rec_d, runner, planner, env, trace)
    ev = [json.loads(line)["event"] for line in (trace.ep_dir / "planner.jsonl").read_text().splitlines()]
    srcs = [e.get("source") for e in ev if e.get("type") == "plan_response"]
    print(f"  planner calls : {len(planner.requests)} (cap {runner.max_vlm_calls}); calls_left={runner.budget()['calls_left']}")
    fb = [[f"{n['id']}:{n['executor']}" for n in e.get("nodes", [])] for e in ev
          if e.get("type") == "plan_response" and e.get("source") == "no_vlm_budget"]
    print(f"  no-VLM plans  : {fb}  (1st = top memory sequence from A, then one plain pi0.5 node)")
    checks.append(("D budget hard stop", len(planner.requests) == runner.max_vlm_calls == rec_d.vlm_calls
                   and runner.budget()["calls_left"] == 0 and "no_vlm_budget" in srcs))

    # E. frozen memory (evaluation period): append refused, lookup still served
    digest = memory.freeze(reason="demo: evaluation period")
    frozen = MemoryDB(mem_root)  # FROZEN.json marker -> frozen, digest verified on open
    print(f"\n[E: frozen memory] digest={digest[:16]}… frozen={frozen.frozen} lookup={len(frozen.lookup(TASK, INSTRUCTION))}")
    refused = 0
    try:
        frozen.append(copy.deepcopy(frozen.lookup(TASK, INSTRUCTION)[0]))
    except MemoryFrozenError as e:
        refused += 1
        print(f"  append          -> MemoryFrozenError: {e}")
    try:
        write_if_passed(rec_a, p5_result(rec_a, None, layout=3, passed=True), store=frozen)
    except MemoryFrozenError as e:
        refused += 1
        print(f"  write_if_passed -> MemoryFrozenError: {e}")
    env = world(step_lim=300)
    rec_e, runner, planner, trace = run_episode("demoE_frozen_eval", env, memory=frozen, traces=traces)
    problems += show("E: episode with frozen (read-only) memory", rec_e, runner, planner, env, trace)
    checks.append(("E frozen append refused", refused == 2 and frozen.audit().ok))

    # G. slow planner: bounded wait per get_action, 1-step holds, planning continues in the background
    env = world(step_lim=300)
    slow = DemoPlanner(pick_sequence(), delay_s=0.35)
    rec_g, runner, planner, trace = run_episode("demoG_slow_planner", env, memory=frozen, traces=traces,
                                                planner=slow, plan_wait_s=0.1)
    problems += show("G: slow planner -> hold 1 step per 0.1 s wait (90 s in production)", rec_g, runner, planner, env, trace)
    print(f"  plan_wait_timeouts={runner.plan_wait_timeouts} (1-step hold chunks while P1 was still planning)")
    checks.append(("G holds then Pass", runner.plan_wait_timeouts >= 2 and rec_g.all_pass))

    # F. trace files and keyframes on disk
    print("\n[F: traces on disk]")
    reader = TraceReader(traces)
    for ep in reader.list_episodes():
        d = traces / ep
        n_kf = len(list((d / "keyframes").glob("*.png")))
        files = sorted(p.name for p in d.iterdir() if p.is_file())
        et = reader.load(ep)
        ef = et.earliest_failure()
        print(f"  {ep:<26} files={files} keyframes={n_kf} events={len(et.events)} "
              f"earliest_failure={(ef or {}).get('node') if isinstance(ef, dict) else getattr(ef, 'node', ef)}")
        checks.append((f"F {ep} on disk", {"trace.jsonl", "planner.jsonl", "episode.json"} <= set(files) and n_kf > 0))
    kf = sorted((traces / "demoA_all_pass" / "keyframes").glob("n2_a1_after_cam_left_wrist*.png"))
    if kf:
        img = reader.read_keyframe(f"demoA_all_pass/keyframes/{kf[0].name}")
        print(f"  keyframe read back: {kf[0].name} shape={img.shape} dtype={img.dtype}")
    epj = json.loads((traces / "demoA_all_pass" / "episode.json").read_text())
    print(f"  episode.json official_success={epj['official_success']} (P5 fills it offline, never at run time)")

    print("\nchecks:")
    for name, ok in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    for p in problems:
        print("  PROBLEM:", p)
    good = all(ok for _, ok in checks) and not problems
    print("demo_fake_episode OK" if good else "demo_fake_episode had problems")
    if not args.out:
        shutil.rmtree(out, ignore_errors=True)
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
