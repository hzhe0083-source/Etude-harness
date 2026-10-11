"""Fakes for demos (design §4 interfaces; no tests in this repo).

Every component's ``python -m etude.<module> --demo`` runs on these fakes.
Nothing here touches GPU, Isaac Sim, pi, the network or a real checkpoint.

Contents
--------
* synthetic observations: :func:`fake_obs` (raw RoboDojo obs dict as our
  deploy.py delivers it, 640x480 images), :func:`fake_frame` (same, as
  :class:`~etude.core.types.ObsFrame`), :class:`Block`, :func:`make_image`;
* fake components implementing the ABCs in :mod:`etude.core.interfaces`:
  :class:`FakePolicy`, :class:`FakePerceiver`, :class:`FakePlanner`,
  :class:`FakeVerifier`, :class:`FakeMemoryStore`, :class:`FakeTraceWriter`,
  :class:`FakeEditor`;
* :class:`FakeTaskEnv`: a RoboDojo ``TASK_ENV`` stand-in that implements only
  the deploy.py allow-list (design §2) and records every attribute name read;
* :func:`three_node_sequence`, :func:`make_tmp_repo`.

Run ``python -m etude.fakes --demo`` for a smoke run of all of the above.
"""
from __future__ import annotations

import argparse
import copy
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from etude import REPO_ROOT
from etude.core.actions import chunk_to_joint_actions, validate_action
from etude.core.interfaces import (
    Editor,
    MemoryStore,
    Perceiver,
    Planner,
    PolicyBackend,
    TraceWriter,
    Verifier,
)
from etude.core.schema import make_perceive
from etude.core.types import (
    ACTION_DIM,
    ARM_DOF,
    ARMS,
    CAMERAS,
    CHUNK_LEN,
    CONTROL_HZ,
    IMAGE_HW,
    VIEW_TO_CAM,
    Detection,
    EpisodeRecord,
    Node,
    NodeArgs,
    ObsFrame,
    OnFail,
    Patch,
    PredicateResult,
    SequenceEntry,
    StopSpec,
    Verdict,
    instruction_hash,
)

# ---------------------------------------------------------------------------
# synthetic observations
# ---------------------------------------------------------------------------

#: Named colours usable in blocks and in FakePerceiver queries (RGB uint8).
COLORS: dict[str, tuple[int, int, int]] = {
    "red": (220, 30, 30),
    "green": (30, 200, 60),
    "blue": (30, 60, 220),
    "yellow": (230, 210, 40),
    "white": (245, 245, 245),
    "black": (15, 15, 15),
}
BACKGROUND_RGB: tuple[int, int, int] = (120, 110, 100)

#: Default home joint configuration per arm (6 dof, rad).
HOME_JOINTS: np.ndarray = np.zeros(ARM_DOF)
#: Default measured ee poses ``[x,y,z,qw,qx,qy,qz]`` (env-relative).
DEFAULT_EE_POSE: dict[str, np.ndarray] = {
    "left": np.array([0.30, 0.25, 0.20, 1.0, 0.0, 0.0, 0.0]),
    "right": np.array([0.30, -0.25, 0.20, 1.0, 0.0, 0.0, 0.0]),
}
#: Gripper opening in metres when the command echo is 1.0 (fully open).
FAKE_MAX_GRIPPER_WIDTH_M: float = 0.04


@dataclass
class Block:
    """A solid colour rectangle drawn on one camera.

    ``cam`` is a RoboDojo cam name or MCP view name; ``bbox=[x,y,w,h]`` px.
    """

    cam: str
    color: str
    bbox: Sequence[int]


#: Scene drawn by :func:`fake_obs` when ``blocks`` is None: a red and a blue
#: object in the head view, the red one centred in the left wrist view.
DEFAULT_BLOCKS: tuple[Block, ...] = (
    Block("cam_head", "red", (200, 260, 60, 40)),
    Block("cam_head", "blue", (400, 260, 60, 40)),
    Block("cam_left_wrist", "red", (280, 200, 80, 80)),
    Block("cam_right_wrist", "blue", (100, 300, 80, 80)),
)


def make_image(blocks: Iterable[Block] = (), cam: str = "cam_head", hw: tuple[int, int] = IMAGE_HW) -> np.ndarray:
    """HxWx3 uint8 background plus the ``blocks`` that belong to ``cam``."""
    h, w = hw
    img = np.empty((h, w, 3), dtype=np.uint8)
    img[:] = BACKGROUND_RGB
    for b in blocks:
        if VIEW_TO_CAM.get(b.cam, b.cam) != cam:
            continue
        x, y, bw, bh = (int(v) for v in b.bbox)
        img[max(y, 0) : max(y + bh, 0), max(x, 0) : max(x + bw, 0)] = COLORS[b.color]
    return img


def fake_obs(
    t: int = 0,
    *,
    with_gripper: bool = True,
    blocks: Iterable[Block | Mapping[str, Any]] | None = None,
    instruction: str = "stack the bowls",
    step_lim: int | None = 800,
    joints: Mapping[str, Sequence[float]] | None = None,
    ee_pose: Mapping[str, Sequence[float]] | None = None,
    grip_echo: Mapping[str, float] | None = None,
    grip_width: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Complete raw RoboDojo obs dict as delivered by OUR deploy.py (design §8.1 item 3).

    Keys: ``vision[cam]["color"]`` (480x640x3 uint8 for the 3 cams),
    ``state`` (``{left,right}_arm_joint_state``(6), ``*_ee_pose``(7),
    ``*_ee_joint_state``(1)), ``action`` ({}), ``data_format_version``,
    ``env_idx`` (0), ``instruction``, ``additional_info`` = ``{"frequency":
    25, "step_lim": step_lim, "gripper_width_measured": {"left","right"}}``.
    ``with_gripper=False`` omits ``gripper_width_measured``; ``step_lim=None``
    omits ``step_lim`` (i.e. the raw env obs before deploy.py augments it).
    ``blocks=None`` draws :data:`DEFAULT_BLOCKS`; ``t`` is informational only
    (the image does not change with ``t``).
    """
    src = DEFAULT_BLOCKS if blocks is None else blocks
    blist = [b if isinstance(b, Block) else Block(**b) for b in src]
    joints = joints or {}
    ee_pose = ee_pose or {}
    grip_echo = grip_echo or {}
    grip_width = grip_width or {}
    state: dict[str, np.ndarray] = {}
    for arm in ARMS:
        state[f"{arm}_arm_joint_state"] = np.asarray(joints.get(arm, HOME_JOINTS), dtype=np.float64).copy()
        state[f"{arm}_ee_pose"] = np.asarray(ee_pose.get(arm, DEFAULT_EE_POSE[arm]), dtype=np.float64).copy()
        state[f"{arm}_ee_joint_state"] = np.array([grip_echo.get(arm, 1.0)], dtype=np.float64)
    info: dict[str, Any] = {"frequency": CONTROL_HZ}
    if step_lim is not None:
        info["step_lim"] = int(step_lim)
    if with_gripper:
        info["gripper_width_measured"] = {
            arm: float(grip_width.get(arm, FAKE_MAX_GRIPPER_WIDTH_M * float(grip_echo.get(arm, 1.0)))) for arm in ARMS
        }
    return {
        "vision": {cam: {"color": make_image(blist, cam)} for cam in CAMERAS},
        "state": state,
        "action": {},
        "data_format_version": "v1.0",
        "env_idx": 0,
        "instruction": instruction,
        "additional_info": info,
    }


def fake_frame(t: int = 0, **kwargs: Any) -> ObsFrame:
    """:class:`ObsFrame` built from :func:`fake_obs` (same kwargs), ``frame.t == t``."""
    return ObsFrame.from_robodojo_obs(fake_obs(t, **kwargs), t=t)


# Backwards-compatible aliases of the aborted scaffold's names.
make_robodojo_obs = fake_obs
make_obs_frame = fake_frame


# ---------------------------------------------------------------------------
# fake components
# ---------------------------------------------------------------------------


class FakePolicy(PolicyBackend):
    """Smooth ``(horizon, 14)`` absolute-joint chunk (design §3.3 ``pi05.infer``).

    Row k (1-based) = ``start + amp*(1-cos(pi*k/H))/2`` on the 12 arm joints,
    where ``start = obs.joint_vector()``; grippers (cols 6, 13) hold the echo
    value, or ``grip`` if given. ``stall=True`` returns a constant chunk.
    ``calls`` records ``{"t", "prompt"}`` per call.
    """

    def __init__(self, horizon: int = CHUNK_LEN, amp: float = 0.2, stall: bool = False, grip: float | None = None):
        self.horizon = horizon
        self.amp = amp
        self.stall = stall
        self.grip = grip
        self.calls: list[dict[str, Any]] = []

    def infer(self, obs: ObsFrame, prompt: str) -> np.ndarray:
        start = obs.joint_vector()
        k = np.arange(1, self.horizon + 1)[:, None] / self.horizon
        ramp = np.zeros_like(k) if self.stall else self.amp * (1 - np.cos(np.pi * k)) / 2.0
        mask = np.ones(ACTION_DIM)
        mask[[ARM_DOF, 2 * ARM_DOF + 1]] = 0.0  # grippers do not ramp
        chunk = start[None, :] + ramp * mask[None, :]
        if self.grip is not None:
            chunk[:, [ARM_DOF, 2 * ARM_DOF + 1]] = self.grip
        self.calls.append({"t": obs.t, "prompt": prompt})
        return chunk.astype(np.float64)

    def reset(self) -> None:
        self.calls.clear()

    def actions(self, obs: ObsFrame, prompt: str, n: int | None = None) -> list[dict[str, np.ndarray]]:
        """``chunk_to_joint_actions(self.infer(obs, prompt), n)``."""
        return chunk_to_joint_actions(self.infer(obs, prompt), n)


class FakePerceiver(Perceiver):
    """Colour-blob detector (design §4 ``perceive``).

    The first word of ``query`` naming a colour in :data:`COLORS` selects the
    pixels (exact RGB match); bbox = their extent ``[x,y,w,h]``; ``conf`` =
    ``base_conf`` when found. ``overrides[(query, view)]`` forces a
    :class:`Detection`. ``calls`` records ``(query, view)``.
    """

    def __init__(self, base_conf: float = 0.9, overrides: Mapping[tuple[str, str], Detection] | None = None):
        self.base_conf = base_conf
        self.overrides = dict(overrides or {})
        self.calls: list[tuple[str, str]] = []

    def perceive(self, query: str, image: np.ndarray, view: str) -> Detection:
        self.calls.append((query, view))
        if (query, view) in self.overrides:
            return copy.deepcopy(self.overrides[(query, view)])
        color = next((w for w in query.lower().replace('"', " ").replace(",", " ").split() if w in COLORS), None)
        if color is None:
            return Detection(found=False, bbox=None, conf=0.0, query=query, view=view)
        mask = np.all(np.asarray(image)[..., :3] == np.array(COLORS[color], dtype=np.uint8), axis=-1)
        if not mask.any():
            return Detection(found=False, bbox=None, conf=0.0, query=query, view=view)
        ys, xs = np.nonzero(mask)
        x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
        return Detection(
            found=True,
            bbox=[float(x0), float(y0), float(x1 - x0), float(y1 - y0)],
            conf=self.base_conf,
            query=query,
            view=view,
        )


def three_node_sequence(color: str = "red") -> list[Node]:
    """Canonical schema-valid 3-node plan ``n1 ee_move → n2 vla → n3 gripper open``."""
    return [
        Node(
            id="n1",
            executor="ee_move",
            instruction=f"move the left gripper above the {color} block",
            args=NodeArgs(target=make_perceive(f"{color} block"), arm="left"),
            stop=StopSpec(stall_chunks=2, max_chunks=4, exec_steps=10),
            postcondition=["arm_home(right)"],
            on_fail=OnFail(retry=1, then="replan"),
            evidence=["ee_pose", "detection"],
        ),
        Node(
            id="n2",
            executor="vla",
            instruction=f"pick up the {color} block",
            args=NodeArgs(),
            stop=StopSpec(stall_chunks=2, max_chunks=6, exec_steps=25),
            postcondition=["gripper_holding(left)", "target_lifted(>0.05m)"],
            on_fail=OnFail(retry=2, then="previous_node"),
            evidence=["wrist_rgb_diff", "gripper_width", "ee_pose"],
        ),
        Node(
            id="n3",
            executor="gripper",
            instruction="open the left gripper",
            args=NodeArgs(cmd="open", arm="left"),
            stop=StopSpec(stall_chunks=1, max_chunks=2, exec_steps=10),
            postcondition=[f'moved("{color} block")'],
            on_fail=OnFail(retry=1, then="abort"),
            evidence=["gripper_width"],
        ),
    ]


class FakePlanner(Planner):
    """Returns a deep copy of :func:`three_node_sequence` (or ``sequence``) per
    request (design §3.3 ``self.plan()``); ``requests`` records deep copies.
    ``fail_after`` > 0 raises RuntimeError once more than that many calls."""

    def __init__(self, sequence: Sequence[Node] | None = None, fail_after: int = 0):
        self.sequence = list(sequence) if sequence is not None else three_node_sequence()
        self.fail_after = fail_after
        self.requests: list[dict[str, Any]] = []

    def plan(self, request: dict[str, Any]) -> list[Node]:
        self.requests.append(copy.deepcopy(request))
        if self.fail_after and len(self.requests) > self.fail_after:
            raise RuntimeError("FakePlanner: call budget exceeded")
        return copy.deepcopy(self.sequence)

    def reset(self) -> None:
        self.requests.clear()


class FakeVerifier(Verifier):
    """Returns scripted verdict kinds in order, then ``default`` (design §4).
    Predicates mirror ``node.postcondition`` with ``v = (kind == "Pass")``;
    ``t_chunk = [hist[0].t // 25, hist[-1].t // 25]``. ``calls`` records
    ``(node_id, len(hist))``."""

    def __init__(self, kinds: Sequence[str] = (), default: str = "Pass"):
        self.kinds = list(kinds)
        self.default = default
        self.calls: list[tuple[str, int]] = []

    def check(self, node: Node, hist: Sequence[ObsFrame]) -> Verdict:
        kind = self.kinds.pop(0) if self.kinds else self.default
        self.calls.append((node.id, len(hist)))
        t0 = hist[0].t if hist else 0
        t1 = hist[-1].t if hist else 0
        return Verdict(
            node=node.id,
            kind=kind,
            predicates={p: PredicateResult(v=kind == "Pass", conf=0.9, src="fake") for p in node.postcondition},
            evidence_refs=[],
            source="code_verifier",
            t_chunk=[t0 // CONTROL_HZ, max(t0, t1) // CONTROL_HZ],
        )


class FakeMemoryStore(MemoryStore):
    """In-memory :class:`MemoryStore` (design §3.1): lookup = same task, exact
    instruction hash first, then shorter sequences; append dedupes on
    ``(task, instruction_hash)`` keeping the shorter sequence."""

    def __init__(self, entries: Iterable[SequenceEntry] = ()):
        self.entries: list[SequenceEntry] = list(entries)

    def lookup(self, task: str, instruction: str, k: int = 3) -> list[SequenceEntry]:
        h = instruction_hash(instruction)
        same = [e for e in self.entries if e.task == task]
        same.sort(key=lambda e: (e.instruction_hash != h, len(e.nodes)))
        return copy.deepcopy(same[:k])

    def append(self, entry: SequenceEntry) -> bool:
        for i, e in enumerate(self.entries):
            if e.task == entry.task and e.instruction_hash == entry.instruction_hash:
                if len(entry.nodes) < len(e.nodes):
                    self.entries[i] = copy.deepcopy(entry)
                    return True
                return False
        self.entries.append(copy.deepcopy(entry))
        return True


@dataclass
class FakeTraceWriter(TraceWriter):
    """Collects events in ``events`` as ``(kind, payload)``; writes nothing.
    Kinds: start, keyframe, node_start, chunk, verdict, planner, end."""

    events: list[tuple[str, Any]] = field(default_factory=list)
    record: EpisodeRecord | None = None

    def start_episode(self, episode_id: str, task: str, instruction: str, meta: dict[str, Any] | None = None) -> None:
        self.events.append(("start", {"episode_id": episode_id, "task": task, "instruction": instruction, "meta": meta}))

    def keyframe(self, frame: ObsFrame, tag: str) -> list[str]:
        refs = [f"kf/fake/{tag}_{cam}.png" for cam in sorted(frame.images)]
        self.events.append(("keyframe", {"t": frame.t, "tag": tag, "refs": refs}))
        return refs

    def node_start(self, node: Node, t: int, chunk_idx: int) -> None:
        self.events.append(("node_start", {"node": node.id, "t": t, "chunk": chunk_idx}))

    def chunk(self, node_id: str, chunk_idx: int, t: int, n_actions: int, info: dict[str, Any] | None = None) -> None:
        self.events.append(("chunk", {"node": node_id, "chunk": chunk_idx, "t": t, "n": n_actions, "info": info}))

    def verdict(self, node: Node, verdict: Verdict) -> None:
        self.events.append(("verdict", verdict.to_dict()))

    def planner_event(self, event: dict[str, Any]) -> None:
        self.events.append(("planner", event))

    def end_episode(self, record: EpisodeRecord) -> None:
        self.record = record
        self.events.append(("end", record.to_dict()))

    def kinds(self) -> list[str]:
        """Event kinds in order."""
        return [k for k, _ in self.events]


class FakeEditor(Editor):
    """Returns a fixed one-line patch appending a bullet to the given surface
    (design §3.1 提案). ``calls`` records the evidence dicts."""

    def __init__(self, line: str = "- Prefer re-observing before retrying a failed grasp."):
        self.line = line
        self.calls: list[dict[str, Any]] = []

    def propose(self, evidence: dict[str, Any]) -> Patch:
        self.calls.append(copy.deepcopy(evidence))
        surface = str(evidence.get("surface", "prompts/planner.md"))
        diff = (
            f"--- a/evolvable/{surface}\n+++ b/evolvable/{surface}\n"
            f"@@ -0,0 +1 @@\n+{self.line}\n"
        )
        return Patch(surface=surface, diff=diff, hypothesis="re-observing reduces repeated grasp failures",
                     medoids=list(evidence.get("medoids", [])))


# ---------------------------------------------------------------------------
# fake TASK_ENV for deploy.py demos (allow-list only, records accesses)
# ---------------------------------------------------------------------------

#: TASK_ENV attributes the fake serves (design §2 allow-list). Access to any
#: other public name is recorded in ``fake_violations`` and raises AttributeError.
TASK_ENV_ALLOW: tuple[str, ...] = (
    "get_obs",
    "take_action",
    "is_episode_end",
    "get_running_env_idx_list",
    "get_obs_batch",
    "take_action_batch",
    "step_lim",
    "robot_manager",
)
#: ``robot_manager.<attr>`` names served. ``get_robot_by_arm_name`` is needed to
#: obtain the robot handle that ``get_end_effector_real_val(robot, ...)`` takes
#: (see frozen/forbidden.yaml ``deploy_task_env_allow``).
ROBOT_MANAGER_ALLOW: tuple[str, ...] = ("get_end_effector_real_val", "get_robot_by_arm_name")


@dataclass
class FakeRobot:
    """Mimics a RoboDojo robot in ``robot_manager.robot_list``
    (``arm_name`` ∈ {left_arm, right_arm}, ``gripper_name`` ∈ {left_ee, right_ee})."""

    arm_name: str
    gripper_name: str
    type: str = "target"


class FakeRobotManager:
    """Allow-listed ``robot_manager`` proxy: every attribute read is appended to
    the owning env's ``fake_accessed`` as ``"robot_manager.<name>"``;
    non-allow-listed names also go to ``fake_violations`` and raise."""

    def __init__(self, env: "FakeTaskEnv"):
        object.__setattr__(self, "_env", env)
        object.__setattr__(self, "_robots", [FakeRobot("left_arm", "left_ee"), FakeRobot("right_arm", "right_ee")])

    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_"):
            return object.__getattribute__(self, name)
        env = object.__getattribute__(self, "_env")
        dotted = f"robot_manager.{name}"
        env._log_access(dotted)
        if name not in ROBOT_MANAGER_ALLOW:
            env._log_violation(dotted)
            raise AttributeError(f"FakeTaskEnv: {dotted} is not on the deploy.py allow-list")
        return object.__getattribute__(self, name)

    def get_robot_by_arm_name(self, arm_name: str) -> FakeRobot:
        """RoboDojo ``get_robot_by_arm_name`` (``"left_arm"``/``"right_arm"``)."""
        for r in object.__getattribute__(self, "_robots"):
            if r.arm_name == arm_name:
                return r
        raise ValueError(f"No robot found with arm name: {arm_name}")

    def get_end_effector_real_val(self, robot: FakeRobot, env_idx_list: Sequence[int] | None = None) -> dict[int, np.ndarray | None]:
        """RoboDojo semantics: ``{env_idx: np.array([finger joint positions])}``
        (two equal values here, metres); ``None`` for envs not in the list."""
        env = object.__getattribute__(self, "_env")
        arm = robot.arm_name.split("_")[0]
        w = env._grip_width(arm)
        idx = list(env_idx_list) if env_idx_list is not None else [0]
        return {0: np.array([w, w]) if 0 in idx else None}


class FakeTaskEnv:
    """RoboDojo ``TASK_ENV`` stand-in for deploy.py demos (design §2 allow-list).

    Public surface (exactly the allow-list): ``get_obs()``,
    ``get_obs_batch(env_idx_list=None)``, ``take_action(action)``,
    ``take_action_batch(actions_list, env_idx_list=None)``,
    ``is_episode_end()``, ``get_running_env_idx_list()``, ``step_lim`` (int),
    ``robot_manager`` (:class:`FakeRobotManager`).

    Behaviour: ``get_obs`` returns the RAW env obs (no ``step_lim`` /
    ``gripper_width_measured`` in ``additional_info`` — deploy.py adds them).
    ``take_action`` validates the dict like RoboDojo (raises ValueError on
    mixed / un-inferable dicts), counts a step, and updates the state: joint
    dicts set joints and gripper echo; ee dicts set ``*_ee_pose`` unless the
    arm is in ``ik_fail_arms`` (then that arm is silently dropped, step still
    counted, like RoboDojo). Measured gripper width = 0.04 m × echo, but never
    below ``holding_width[arm]`` when set (simulates an object in the gripper).

    Introspection (names prefixed ``fake_`` are not recorded):
    ``fake_accessed`` (list of attribute names read, in order, including
    ``robot_manager.<name>``), ``fake_violations`` (names outside the
    allow-list), ``fake_actions`` (executed action dicts), ``fake_t``.
    """

    def __init__(
        self,
        step_lim: int = 100,
        instruction: str = "stack the bowls",
        blocks: Iterable[Block] | None = None,
        ik_fail_arms: Iterable[str] = (),
        holding_width: Mapping[str, float] | None = None,
    ):
        d = object.__setattr__
        d(self, "step_lim", int(step_lim))
        d(self, "_instruction", instruction)
        d(self, "_blocks", None if blocks is None else list(blocks))
        d(self, "_ik_fail", set(ik_fail_arms))
        d(self, "_holding", dict(holding_width or {}))
        d(self, "_joints", {a: HOME_JOINTS.copy() for a in ARMS})
        d(self, "_ee", {a: DEFAULT_EE_POSE[a].copy() for a in ARMS})
        d(self, "_echo", {a: 1.0 for a in ARMS})
        d(self, "fake_accessed", [])
        d(self, "fake_violations", [])
        d(self, "fake_actions", [])
        d(self, "fake_t", 0)
        d(self, "robot_manager", FakeRobotManager(self))

    # -- access recording ----------------------------------------------------
    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_") or name.startswith("fake_"):
            return object.__getattribute__(self, name)
        object.__getattribute__(self, "_log_access")(name)
        if name not in TASK_ENV_ALLOW:
            object.__getattribute__(self, "_log_violation")(name)
            raise AttributeError(f"FakeTaskEnv: TASK_ENV.{name} is not on the deploy.py allow-list")
        return object.__getattribute__(self, name)

    def _log_access(self, name: str) -> None:
        object.__getattribute__(self, "fake_accessed").append(name)

    def _log_violation(self, name: str) -> None:
        object.__getattribute__(self, "fake_violations").append(name)

    def _grip_width(self, arm: str) -> float:
        w = FAKE_MAX_GRIPPER_WIDTH_M * float(np.clip(self._echo[arm], 0.0, 1.0))
        if arm in self._holding:
            w = max(w, float(self._holding[arm]))
        return w

    # -- allow-listed API ----------------------------------------------------
    def get_obs(self) -> dict[str, Any]:
        """Raw env obs (``fake_obs(..., with_gripper=False, step_lim=None)``) for the current state."""
        return fake_obs(
            self.fake_t,
            with_gripper=False,
            step_lim=None,
            blocks=self._blocks,
            instruction=self._instruction,
            joints=self._joints,
            ee_pose=self._ee,
            grip_echo=self._echo,
        )

    def get_obs_batch(self, env_idx_list: Sequence[int] | None = None) -> list[dict[str, Any]]:
        return [self.get_obs() for _ in (env_idx_list if env_idx_list is not None else [0])]

    def take_action(self, action: Mapping[str, Any]) -> None:
        errs = validate_action(action)
        if errs:
            raise ValueError("; ".join(errs))
        if object.__getattribute__(self, "fake_t") >= object.__getattribute__(self, "step_lim"):
            return
        for arm in ARMS:
            if f"{arm}_arm_joint_state" in action:
                self._joints[arm] = np.asarray(action[f"{arm}_arm_joint_state"], dtype=np.float64).reshape(-1).copy()
            elif f"{arm}_ee_pose" in action and arm not in self._ik_fail:
                self._ee[arm] = np.asarray(action[f"{arm}_ee_pose"], dtype=np.float64).reshape(-1).copy()
            if f"{arm}_ee_joint_state" in action:
                self._echo[arm] = float(np.clip(np.asarray(action[f"{arm}_ee_joint_state"]).reshape(-1)[0], 0.0, 1.0))
        self.fake_actions.append(copy.deepcopy(dict(action)))
        object.__setattr__(self, "fake_t", self.fake_t + 1)

    def take_action_batch(self, actions_list: Sequence[Mapping[str, Any]], env_idx_list: Sequence[int] | None = None) -> None:
        for a in actions_list:
            object.__getattribute__(self, "take_action")(a)

    def is_episode_end(self) -> bool:
        return self.fake_t >= object.__getattribute__(self, "step_lim")

    def get_running_env_idx_list(self) -> list[int]:
        return [] if object.__getattribute__(self, "is_episode_end")() else [0]


# ---------------------------------------------------------------------------
# temporary git repo helper (evolution / memory demos)
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make_tmp_repo(dst: str | Path) -> Path:
    """Create a throw-away git repo at ``dst`` (must not exist) with copies of
    ``frozen/`` and ``evolvable/`` plus an empty ``memory_db/``, branch
    ``main``, one commit "init". Pass it as ``root=`` to
    :mod:`etude.core.config` loaders. Returns the repo path."""
    repo = Path(dst)
    repo.mkdir(parents=True, exist_ok=False)
    for d in ("frozen", "evolvable"):
        shutil.copytree(REPO_ROOT / d, repo / d)
    (repo / "memory_db").mkdir()
    (repo / "memory_db" / ".gitkeep").touch()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "demo@example.invalid")
    _git(repo, "config", "user.name", "etude-demo")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------


def _demo() -> int:
    """Smoke-run every fake and the core schema/types (no assertions framework)."""
    import tempfile

    from etude.core import config, schema
    from etude.core.actions import ee_action_dict
    from etude.core.types import Receipt

    obs = fake_obs(0)
    assert obs["vision"]["cam_head"]["color"].shape == (480, 640, 3)
    frame = fake_frame(3)
    print("fake_frame: t=%d cams=%s grip=%s" % (frame.t, sorted(frame.images), frame.gripper_width_measured("left")))

    pol = FakePolicy()
    chunk = pol.infer(frame, "pick up the red block")
    print("FakePolicy chunk", chunk.shape)
    assert chunk.shape == (CHUNK_LEN, ACTION_DIM)

    per = FakePerceiver()
    det = per.perceive("red block", frame.image("wrist_l"), "wrist_l")
    print("FakePerceiver", det.to_dict())

    seq = FakePlanner().plan({"instruction": "stack the bowls"})
    errs = schema.validate_nodes(seq)
    print("FakePlanner nodes", [n.id for n in seq], "schema errors:", errs)
    assert not errs

    bad = seq[0].to_dict()
    bad["args"]["target"] = "[0.31,-0.15,0.02]"
    print("numeric target rejected:", bool(schema.validate_node(bad)))

    v = FakeVerifier(kinds=["Fail"]).check(seq[1], [frame, fake_frame(60)])
    print("FakeVerifier", v.kind, "schema errors:", schema.validate_verdict(v))

    mem = FakeMemoryStore()
    entry = SequenceEntry(seq_id="s1", task="stack_bowls", instruction="stack the bowls",
                          instruction_hash=instruction_hash("stack the bowls"), nodes=seq)
    print("FakeMemoryStore append", mem.append(entry), "lookup", len(mem.lookup("stack_bowls", "stack the bowls")))

    tw = FakeTraceWriter()
    tw.start_episode("ep0", "stack_bowls", "stack the bowls")
    tw.keyframe(frame, "n1_before")
    tw.verdict(seq[1], v)
    tw.end_episode(EpisodeRecord(episode_id="ep0", task="stack_bowls", nodes=seq, verdicts=[v]))
    print("FakeTraceWriter kinds", tw.kinds())

    patch = FakeEditor().propose({"surface": "prompts/planner.md"})
    print("FakeEditor patch surface", patch.surface)

    env = FakeTaskEnv(step_lim=30, holding_width={"left": 0.012})
    while not env.is_episode_end():
        raw = env.get_obs()
        f = ObsFrame.from_robodojo_obs(raw, t=env.fake_t)
        for a in pol.actions(f, "pick", n=10):
            env.take_action(a)
            if env.is_episode_end():
                break
    rm = env.robot_manager
    width = rm.get_end_effector_real_val(rm.get_robot_by_arm_name("left_arm"), [0])[0]
    env.take_action(ee_action_dict(DEFAULT_EE_POSE["left"], 0.0, DEFAULT_EE_POSE["right"], 0.0))
    try:
        getattr(env, "num_envs")  # not on the allow-list -> recorded as a violation
    except AttributeError:
        pass
    print("FakeTaskEnv steps", env.fake_t, "width", width.tolist(),
          "accessed", sorted(set(env.fake_accessed)), "violations", env.fake_violations)

    with tempfile.TemporaryDirectory() as td:
        repo = make_tmp_repo(Path(td) / "repo")
        print("make_tmp_repo thresholds", len(config.load_thresholds(repo)))

    r = Receipt(receipt_id="r0001", parent="c0000", candidate="c0001", surface="prompts/planner.md",
                diff_sha="x", hypothesis="h", research_run="E1-R001")
    print("Receipt schema errors:", schema.validate_receipt(r))
    print("demo ok")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python -m etude.fakes --demo``."""
    ap = argparse.ArgumentParser(prog="python -m etude.fakes", description=__doc__.splitlines()[0])
    ap.add_argument("--demo", action="store_true", help="smoke-run all fakes")
    args = ap.parse_args(argv)
    if args.demo:
        return _demo()
    ap.print_help()
    return 0


__all__ = [
    "BACKGROUND_RGB", "COLORS", "DEFAULT_BLOCKS", "DEFAULT_EE_POSE", "FAKE_MAX_GRIPPER_WIDTH_M", "HOME_JOINTS",
    "ROBOT_MANAGER_ALLOW", "TASK_ENV_ALLOW", "Block", "FakeEditor", "FakeMemoryStore", "FakePerceiver",
    "FakePlanner", "FakePolicy", "FakeRobot", "FakeRobotManager", "FakeTaskEnv", "FakeTraceWriter",
    "FakeVerifier", "fake_frame", "fake_obs", "make_image", "make_obs_frame", "make_robodojo_obs",
    "make_tmp_repo", "three_node_sequence",
]

if __name__ == "__main__":
    sys.exit(main())
