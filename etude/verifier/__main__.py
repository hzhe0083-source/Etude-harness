"""``python -m etude.verifier --demo``: code verifier on synthetic colour-blob
scenes (design §4 判决 JSON, §3.5 threat table, §8.3 E row 1).

Builds Pass / Fail / InsufficientEvidence (plus inspect-resolved Pass,
Fail with scene_intact=false, the same without sequence context (undecidable),
a gripper occluding an object in the head view (undecidable, not moved),
ik_failed-but-Pass, Blocked and SafetyStop) cases from :func:`etude.fakes.fake_frame` images,
prints each verdict JSON and checks it against frozen/schema/verdict.json.
No GPU, no VLM, no env; keyframe PNGs go to a temp dir (or ``--out``).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from typing import Any, Mapping, Sequence

import numpy as np

from etude.core.schema import make_perceive, validate_verdict
from etude.core.types import Node, NodeArgs, ObsFrame, OnFail, StopSpec, Verdict
from etude.fakes import DEFAULT_EE_POSE, Block, fake_frame
from etude.perceive.detector import ColorBlobPerceiver
from etude.verifier.predicates import ROBOT_OCCLUDER_QUERIES, ThresholdError, load_verifier_thresholds
from etude.verifier.verdict import CodeVerifier, PngKeyframeSaver


def _pose(arm: str, dz: float = 0.0) -> np.ndarray:
    p = DEFAULT_EE_POSE[arm].copy()
    p[2] += dz
    return p


def _hist(n: int, blocks_before: Sequence[Block], blocks_after: Sequence[Block], *, t0: int = 0, dt: int = 25,
          dz_left: float = 0.0, echo_after: float = 1.0, width_after: float | None = None,
          joint_jump: float = 0.0, info_after: Mapping[str, Any] | None = None) -> list[ObsFrame]:
    """``n`` frames: first uses ``blocks_before``, the rest ``blocks_after``;
    left ee z ramps by ``dz_left``; joints drift 0.05 rad/frame (+``joint_jump``
    on the last frame); gripper echo/width switch to ``*_after`` from frame 1."""
    frames = []
    for i in range(n):
        frac = i / max(n - 1, 1)
        q = np.full(6, 0.05 * i)
        if i == n - 1:
            q = q + joint_jump
        after = i > 0
        kw: dict[str, Any] = dict(
            blocks=list(blocks_after if after else blocks_before),
            joints={"left": q, "right": np.zeros(6)},
            ee_pose={"left": _pose("left", dz_left * frac), "right": _pose("right")},
            grip_echo={"left": echo_after if after else 1.0, "right": 1.0},
        )
        if after and width_after is not None:
            kw["grip_width"] = {"left": width_after, "right": 0.04}
        f = fake_frame(t0 + i * dt, **kw)
        if i == n - 1 and info_after:
            f.additional_info.update(dict(info_after))
        frames.append(f)
    return frames


def _node(nid: str, executor: str, post: list[str], *, target: str | None = None, arm: str | None = "left",
          cmd: str | None = None, instruction: str = "") -> Node:
    return Node(id=nid, executor=executor, instruction=instruction,
                args=NodeArgs(target=make_perceive(target) if target else None, cmd=cmd, arm=arm),
                stop=StopSpec(), postcondition=post, on_fail=OnFail(), evidence=["wrist_rgb_diff", "gripper_width", "ee_pose"])


HEAD_SCENE = [Block("cam_head", "red", (200, 260, 60, 40)), Block("cam_head", "blue", (400, 260, 60, 40))]
WRIST_R = [Block("cam_right_wrist", "blue", (100, 300, 80, 80))]


class GripperBlobPerceiver(ColorBlobPerceiver):
    """Demo stand-in for OWLv2 seeing the robot gripper: the occluder query
    (:data:`ROBOT_OCCLUDER_QUERIES`) is answered by the black blob."""

    def perceive(self, query: str, image: np.ndarray, view: str):
        if query in ROBOT_OCCLUDER_QUERIES:
            d = super().perceive("black gripper", image, view)
            d.query = query
            return d
        return super().perceive(query, image, view)


def _show(title: str, v: Verdict, verifier: CodeVerifier | None = None) -> bool:
    d = v.to_dict()
    errs = validate_verdict(d)
    print(f"\n=== {title}: {v.kind} (source={v.source}) ===")
    print(json.dumps(d, indent=1, ensure_ascii=False))
    if verifier is not None and verifier.inspect_calls:
        print("inspect calls:", json.dumps(verifier.inspect_calls, ensure_ascii=False))
    if errs:
        print("SCHEMA ERRORS:", errs)
    return not errs


def demo(out: str | None = None) -> int:
    """Run all verdict scenarios; return 0 if every expectation and schema check holds."""
    out_dir = out or tempfile.mkdtemp(prefix="etude_verifier_demo_")
    perceiver = GripperBlobPerceiver()
    ok = True
    seen: list[str] = []

    def run(title: str, node: Node, hist: list[ObsFrame], expect: str, hook=None, expect_scene: Any = "any",
            seq: Sequence[Node] | None = None) -> None:
        nonlocal ok
        saver = PngKeyframeSaver(out_dir, prefix=f"kf/demo_ep{len(seen)}")
        seen.append(title)
        ver = CodeVerifier(perceiver, inspect_hook=hook, keyframe_fn=saver)
        if seq is not None:  # the installed sequence (the runner registers it): implicit targets
            ver.register_sequence(seq)
        v = ver.check(node, hist)
        ok &= _show(title, v, ver)
        if v.kind != expect:
            print(f"!! expected {expect}, got {v.kind}")
            ok = False
        if v.kind != "Pass":
            print("scene_intact detail:", json.dumps(ver.last_scene, ensure_ascii=False))
        if expect_scene != "any" and v.scene_intact is not expect_scene:
            print(f"!! expected scene_intact={expect_scene}, got {v.scene_intact}")
            ok = False
        if hook is not None and len(ver.inspect_calls) > 1:
            print("!! inspect_hook called more than once")
            ok = False

    reach = _node("n1", "ee_move", ["arm_home(right)"], target="red block", instruction="move above the red block")
    pick = _node("n2", "vla", ["gripper_holding(left)", "target_lifted(>0.05m)", 'not_moved("blue block")'],
                 instruction="pick up the red block")
    wrist_before = [Block("cam_left_wrist", "red", (280, 200, 80, 80))]
    wrist_held = [Block("cam_left_wrist", "red", (220, 280, 200, 200))]

    # Pass: closed command, measured width 25 mm (object between fingers), object
    # fills the wrist finger ROI, ee rose 8 cm, blue block untouched in 2 static views.
    run("Pass", pick,
        _hist(5, HEAD_SCENE + wrist_before + WRIST_R, HEAD_SCENE + wrist_held + WRIST_R,
              dz_left=0.08, echo_after=0.0, width_after=0.025),
        "Pass")

    # Fail: closed on nothing (2 mm), wrist ROI unchanged; self-report / episode-end
    # / success flags in additional_info are stripped and ignored (design §3.5).
    run("Fail (self-report + episode_end ignored)", pick,
        _hist(5, HEAD_SCENE + wrist_before + WRIST_R, HEAD_SCENE + wrist_before + WRIST_R,
              dz_left=0.08, echo_after=0.0, width_after=0.002,
              info_after={"episode_end": True, "success": True, "done": True, "official_score": 1.0}),
        "Fail")

    # InsufficientEvidence: red block no longer visible after the place (inside the
    # bin?) → target_in undecidable; one inspect call answers None (VLM unsure).
    place = _node("n4", "gripper", ['target_in("red block","blue bin")'], cmd="open", instruction="release over the bin")
    bin_scene = [Block("cam_head", "blue", (360, 220, 160, 120))]
    place_hist = _hist(3, HEAD_SCENE + [Block("cam_head", "red", (410, 180, 40, 30))] + bin_scene, bin_scene)
    unsure_calls: list[tuple[str, Any]] = []

    def unsure_hook(frame_ref: str, region: Mapping[str, float] | None, question: str = "") -> bool | None:
        unsure_calls.append((frame_ref, region))
        return None

    run("InsufficientEvidence (inspect → None)", place, place_hist, "InsufficientEvidence", hook=unsure_hook)

    # Same evidence; the second opinion says yes → Pass with source inspect_second_opinion.
    run("InsufficientEvidence → Pass via inspect", place, place_hist, "Pass", hook=lambda ref, region, question="": True)

    # Fail with the scene disturbed: the blue block was knocked 60 px (head) during the
    # attempt -> not_moved("blue block") False -> Fail, and the derived field
    # scene_intact (non-target objects vs the t=0 keyframe) is false (design §4, P2).
    # The pick has no args.target: its implicit object is n1's ee_move target (the red
    # block), exempt as the pick's own target; the blue block is watched.
    knocked = [Block("cam_head", "red", (200, 260, 60, 40)), Block("cam_head", "blue", (460, 300, 60, 40))]
    knocked_hist = _hist(5, HEAD_SCENE + wrist_before + WRIST_R,
                         knocked + wrist_before + [Block("cam_right_wrist", "blue", (180, 330, 80, 80))],
                         dz_left=0.08, echo_after=0.0, width_after=0.002)
    run("Fail + scene_intact=false (blue block knocked)", pick, knocked_hist, "Fail", expect_scene=False,
        seq=[reach, pick])

    # Same evidence, but no installed sequence: the pick's object cannot be named, so
    # whatever it touched is indistinguishable from a disturbance -> undecidable (None).
    run("Fail, contact node without a nameable object -> scene_intact None", pick, knocked_hist, "Fail",
        expect_scene=None)

    # The gripper (black blob; OWLv2 query "robot gripper" in production) hovers over the
    # red block in the head view after the node: the pixels in the block's I_0 box changed
    # because of the arm, not the block -> that view is undecidable, never "moved".
    reach_l = _node("n1", "ee_move", ["arm_home(left)"], target="red block", instruction="move above the red block")
    hover = HEAD_SCENE + [Block("cam_head", "black", (190, 240, 80, 70))]
    run("Fail, gripper over the target in the head view -> scene_intact None (occluded)", reach_l,
        _hist(5, HEAD_SCENE, hover), "Fail", expect_scene=None)

    # P3 (completion takes precedence): our ee_move executor flagged a silent IK
    # drop on the left arm mid-node, but the postcondition holds -> Pass, with
    # ik_failed recorded as evidence (Recova 2610.01178 Alg. 1 line 23).
    run("ik_failed mid-node, postcondition met -> Pass", reach,
        _hist(3, HEAD_SCENE, HEAD_SCENE, info_after={"harness": {"ik_failed": {"left": True}}}), "Pass")

    # Blocked: same IK flag and the postcondition is NOT met (left arm drifted 0.2 rad from home).
    run("Blocked (ik_failed, postcondition not met)", reach_l,
        _hist(5, HEAD_SCENE, HEAD_SCENE, info_after={"harness": {"ik_failed": {"left": True}}}), "Blocked",
        expect_scene=True)

    # SafetyStop: +0.5 rad joint jump between frames one step (40 ms) apart → ~14 rad/s > 3 rad/s.
    run("SafetyStop", pick, _hist(3, HEAD_SCENE, HEAD_SCENE, dt=1, joint_jump=0.5), "SafetyStop")

    # Threat table: loosening perceive.conf_min 0.6 → 0.4 is refused at load time.
    try:
        load_verifier_thresholds(overrides={"perceive.conf_min": 0.4})
        print("\n!! loosening perceive.conf_min was accepted")
        ok = False
    except ThresholdError as e:
        print(f"\n=== threshold loosening refused ===\n{e}")
    print(f"\nkeyframes written under {out_dir}")
    print("DEMO OK" if ok else "DEMO FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m etude.verifier", description=__doc__.splitlines()[0])
    ap.add_argument("--demo", action="store_true", help="run the synthetic verdict demo")
    ap.add_argument("--out", default=None, help="keyframe output dir (default: a temp dir)")
    a = ap.parse_args(argv)
    if not a.demo:
        ap.print_help()
        return 0
    return demo(a.out)


if __name__ == "__main__":
    sys.exit(main())
