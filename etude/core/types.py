"""Shared dataclasses for the VLA harness (design §4 "接口规格").

Every type here round-trips through ``to_dict`` / ``from_dict``; the dict form
of :class:`Node`, :class:`Verdict` and :class:`Receipt` is exactly the JSON in
design §4 and is validated by ``frozen/schema/*.json`` (see
:mod:`etude.core.schema`).

Conventions
-----------
* Arm order everywhere: left then right. 14-dim joint vector order (pi0.5 /
  XPolicyLab ``pack_robot_state``): ``[left_arm(6), left_grip(1),
  right_arm(6), right_grip(1)]``.
* Camera names are the RoboDojo ones (``cam_head``, ``cam_left_wrist``,
  ``cam_right_wrist``); MCP view names (``head``, ``wrist_l``, ``wrist_r``)
  map through :data:`VIEW_TO_CAM`.
* ``ee_pose`` is 7-dim ``[x, y, z, qw, qx, qy, qz]`` in env-relative frame
  (measured). ``*_ee_joint_state`` in obs is the rate-limited command echo,
  NOT measured; the measured gripper width is
  ``additional_info["gripper_width_measured"][arm]`` (design §8.1 item 3).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

import numpy as np

# ---------------------------------------------------------------------------
# Constants (design §4, §8.2)
# ---------------------------------------------------------------------------

EXECUTORS: tuple[str, ...] = ("vla", "ee_move", "gripper")
VERDICT_KINDS: tuple[str, ...] = ("Pass", "Fail", "InsufficientEvidence", "Blocked", "SafetyStop")
VERDICT_SOURCES: tuple[str, ...] = ("code_verifier", "inspect_second_opinion")
ON_FAIL_THEN: tuple[str, ...] = ("previous_node", "replan", "abort")
GRIPPER_CMDS: tuple[str, ...] = ("open", "close")
ARMS: tuple[str, ...] = ("left", "right")
EVIDENCE_KINDS: tuple[str, ...] = (
    "wrist_rgb_diff",
    "head_rgb_diff",
    "gripper_width",
    "ee_pose",
    "joint_state",
    "detection",
)
#: Predicate names allowed in ``Node.postcondition`` (design §4 谓词表).
PREDICATE_NAMES: tuple[str, ...] = (
    "gripper_holding",
    "target_lifted",
    "target_in",
    "target_on",
    "stacked",
    "moved",
    "not_moved",
    "arm_home",
)
#: The 7 MCP tools P2 exposes to P1 (design §4 MCP table).
MCP_TOOLS: tuple[str, ...] = (
    "observe",
    "inspect",
    "perceive",
    "memory_lookup",
    "submit_sequence",
    "get_verdict",
    "budget",
)

CAMERAS: tuple[str, ...] = ("cam_head", "cam_left_wrist", "cam_right_wrist")
VIEW_TO_CAM: dict[str, str] = {
    "head": "cam_head",
    "wrist_l": "cam_left_wrist",
    "wrist_r": "cam_right_wrist",
}
CAM_TO_VIEW: dict[str, str] = {v: k for k, v in VIEW_TO_CAM.items()}
IMAGE_HW: tuple[int, int] = (480, 640)

ARM_DOF = 6
ACTION_DIM = 14
CHUNK_LEN = 50  # pi0.5 official ckpt horizon
CONTROL_HZ = 25
DEFAULT_EXEC_STEPS = 25
DEFAULT_STALL_CHUNKS = 2
DEFAULT_MAX_CHUNKS = 6
DEFAULT_RETRY = 2
MAX_VLM_CALLS_PER_EPISODE = 12

#: Per-task step limits (take_action count at 25 Hz; design §8.2 F6).
STEP_LIM: dict[str, int] = {
    "stack_bowls": 800,
    "put_bottles_into_dustbin": 700,
    "build_tower": 1050,
    "make_kong": 600,
    "fold_clothes": 500,
}
DEV_TASKS: tuple[str, ...] = ("stack_bowls", "put_bottles_into_dustbin")
HELDOUT_TASKS: tuple[str, ...] = ("build_tower", "make_kong")

ExecutorName = Literal["vla", "ee_move", "gripper"]
VerdictKind = Literal["Pass", "Fail", "InsufficientEvidence", "Blocked", "SafetyStop"]
VerdictSource = Literal["code_verifier", "inspect_second_opinion"]


def instruction_hash(instruction: str) -> str:
    """sha256 hex of the case/whitespace-normalised instruction; the memory
    dedupe key together with the task (design §3.1 同任务同指令哈希去重)."""
    import hashlib

    return hashlib.sha256(" ".join(instruction.lower().split()).encode("utf-8")).hexdigest()


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _jsonable(x: Any) -> Any:
    """Convert numpy scalars/arrays (recursively) into plain JSON types."""
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, Mapping):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


# ---------------------------------------------------------------------------
# Node (design §4 节点 schema)
# ---------------------------------------------------------------------------


@dataclass
class NodeArgs:
    """``Node.args`` (design §4).

    ``target`` must be a ``perceive("…")`` string (no numeric coordinates);
    ``cmd`` is ``open``/``close`` for the gripper executor. ``arm`` is an
    optional extension (``left``/``right``) because ``ee_move`` and
    ``gripper`` need to know which arm to drive; ``None`` lets the executor
    choose (e.g. the arm on the target's side in the head view).
    """

    target: str | None = None
    cmd: str | None = None
    arm: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none({"target": self.target, "cmd": self.cmd, "arm": self.arm})

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "NodeArgs":
        d = d or {}
        return cls(target=d.get("target"), cmd=d.get("cmd"), arm=d.get("arm"))

    def target_query(self) -> str | None:
        """Return the text inside ``perceive("…")`` or None (design §4)."""
        from etude.core.schema import parse_perceive  # local import: avoid cycle

        return parse_perceive(self.target) if self.target else None


@dataclass
class StopSpec:
    """``Node.stop`` (design §4): stall_chunks, max_chunks, exec_steps."""

    stall_chunks: int = DEFAULT_STALL_CHUNKS
    max_chunks: int = DEFAULT_MAX_CHUNKS
    exec_steps: int = DEFAULT_EXEC_STEPS

    def to_dict(self) -> dict[str, Any]:
        return {
            "stall_chunks": self.stall_chunks,
            "max_chunks": self.max_chunks,
            "exec_steps": self.exec_steps,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "StopSpec":
        d = d or {}
        return cls(
            stall_chunks=int(d.get("stall_chunks", DEFAULT_STALL_CHUNKS)),
            max_chunks=int(d.get("max_chunks", DEFAULT_MAX_CHUNKS)),
            exec_steps=int(d.get("exec_steps", DEFAULT_EXEC_STEPS)),
        )


@dataclass
class OnFail:
    """``Node.on_fail`` (design §4, §3.3): retry r times then ``then``."""

    retry: int = DEFAULT_RETRY
    then: str = "previous_node"

    def to_dict(self) -> dict[str, Any]:
        return {"retry": self.retry, "then": self.then}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "OnFail":
        d = d or {}
        return cls(retry=int(d.get("retry", DEFAULT_RETRY)), then=str(d.get("then", "previous_node")))


@dataclass
class Node:
    """One plan node (design §4 节点 schema).

    Dict form::

        {"id":"n3","executor":"vla|ee_move|gripper","instruction":"...",
         "args":{"target":"perceive(\\"...\\")","cmd":"open|close"},
         "stop":{"stall_chunks":2,"max_chunks":6,"exec_steps":25},
         "postcondition":["gripper_holding","target_lifted(>0.05m)"],
         "on_fail":{"retry":2,"then":"previous_node"},
         "evidence":["wrist_rgb_diff","gripper_width","ee_pose"]}
    """

    id: str
    executor: str
    instruction: str = ""
    args: NodeArgs = field(default_factory=NodeArgs)
    stop: StopSpec = field(default_factory=StopSpec)
    postcondition: list[str] = field(default_factory=list)
    on_fail: OnFail = field(default_factory=OnFail)
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "executor": self.executor,
            "instruction": self.instruction,
            "args": self.args.to_dict(),
            "stop": self.stop.to_dict(),
            "postcondition": list(self.postcondition),
            "on_fail": self.on_fail.to_dict(),
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Node":
        return cls(
            id=str(d["id"]),
            executor=str(d["executor"]),
            instruction=str(d.get("instruction", "")),
            args=NodeArgs.from_dict(d.get("args")),
            stop=StopSpec.from_dict(d.get("stop")),
            postcondition=list(d.get("postcondition", [])),
            on_fail=OnFail.from_dict(d.get("on_fail")),
            evidence=list(d.get("evidence", [])),
        )


# ---------------------------------------------------------------------------
# Verdict (design §4 判决 JSON)
# ---------------------------------------------------------------------------


@dataclass
class PredicateResult:
    """One predicate entry ``{v, conf, src}`` in a verdict (design §4).

    ``v`` is None when the predicate could not be evaluated.
    """

    v: bool | None
    conf: float
    src: str

    def to_dict(self) -> dict[str, Any]:
        return {"v": self.v, "conf": float(self.conf), "src": self.src}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "PredicateResult":
        return cls(v=d.get("v"), conf=float(d.get("conf", 0.0)), src=str(d.get("src", "")))


@dataclass
class Verdict:
    """Five-kind node verdict (design §4 判决 JSON).

    ``t_chunk`` is ``[first_chunk, last_chunk]`` (inclusive chunk indices
    within the episode) the verdict covers.

    ``scene_intact`` is a derived, read-only field (design §4 "非 Pass 判决附带派生字段
    scene_intact"; Recova 2610.01178 App. B M_int): True when every detected
    non-target object is ``not_moved`` against the episode's t=0 keyframe,
    False when one confidently moved, None when undecidable. It never changes
    ``kind``. :meth:`to_dict` writes it for every non-Pass verdict (value may
    be null) and omits it for Pass.
    """

    node: str
    kind: str
    predicates: dict[str, PredicateResult] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    source: str = "code_verifier"
    t_chunk: list[int] = field(default_factory=lambda: [0, 0])
    scene_intact: bool | None = None

    @property
    def passed(self) -> bool:
        return self.kind == "Pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "kind": self.kind,
            "predicates": {k: p.to_dict() for k, p in self.predicates.items()},
            "evidence_refs": list(self.evidence_refs),
            "source": self.source,
            "t_chunk": [int(x) for x in self.t_chunk],
            **({} if self.kind == "Pass" else {"scene_intact": None if self.scene_intact is None else bool(self.scene_intact)}),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Verdict":
        return cls(
            node=str(d["node"]),
            kind=str(d["kind"]),
            predicates={k: PredicateResult.from_dict(v) for k, v in (d.get("predicates") or {}).items()},
            evidence_refs=list(d.get("evidence_refs", [])),
            source=str(d.get("source", "code_verifier")),
            t_chunk=[int(x) for x in d.get("t_chunk", [0, 0])],
            scene_intact=None if d.get("scene_intact") is None else bool(d["scene_intact"]),
        )


# ---------------------------------------------------------------------------
# Detection (design §4 MCP perceive → {found, bbox, conf})
# ---------------------------------------------------------------------------


@dataclass
class Detection:
    """Result of ``perceive(query, view)`` (design §4).

    ``bbox`` is ``[x, y, w, h]`` in pixels of the queried view's image
    (origin top-left, 640x480), or None when ``found`` is False.
    """

    found: bool
    bbox: list[float] | None = None
    conf: float = 0.0
    query: str = ""
    view: str = ""

    @property
    def center(self) -> tuple[float, float] | None:
        if self.bbox is None:
            return None
        x, y, w, h = self.bbox
        return (x + w / 2.0, y + h / 2.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": bool(self.found),
            "bbox": None if self.bbox is None else [float(v) for v in self.bbox],
            "conf": float(self.conf),
            "query": self.query,
            "view": self.view,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Detection":
        bbox = d.get("bbox")
        return cls(
            found=bool(d["found"]),
            bbox=None if bbox is None else [float(v) for v in bbox],
            conf=float(d.get("conf", 0.0)),
            query=str(d.get("query", "")),
            view=str(d.get("view", "")),
        )


# ---------------------------------------------------------------------------
# ObsFrame (observation snapshot; design §2 P2, §8.1 item 3)
# ---------------------------------------------------------------------------


@dataclass
class ObsFrame:
    """Snapshot of one RoboDojo observation as seen by the harness.

    * ``images``: ``{cam_name: HxWx3 uint8}`` (cam ∈ :data:`CAMERAS`).
    * ``state``: ``{key: np.ndarray float}`` with keys
      ``left_arm_joint_state``(6), ``left_ee_pose``(7),
      ``left_ee_joint_state``(1) and ``right_*``.
    * ``additional_info``: includes ``frequency`` (25), and from our
      deploy.py ``step_lim`` (int) and ``gripper_width_measured``
      (``{"left": float, "right": float}``).
    * ``t``: env step index (number of ``take_action`` calls before this obs).
    """

    images: dict[str, np.ndarray] = field(default_factory=dict)
    state: dict[str, np.ndarray] = field(default_factory=dict)
    additional_info: dict[str, Any] = field(default_factory=dict)
    t: int = 0
    instruction: str = ""

    # -- construction -------------------------------------------------------
    @classmethod
    def from_robodojo_obs(cls, obs: Mapping[str, Any], t: int = 0) -> "ObsFrame":
        """Build from the RoboDojo obs dict (``obs["vision"][cam]["color"]``,
        ``obs["state"]``, ``obs["instruction"]``, ``obs["additional_info"]``)."""
        images: dict[str, np.ndarray] = {}
        for cam, cam_data in (obs.get("vision") or {}).items():
            img = cam_data.get("color") if isinstance(cam_data, Mapping) else cam_data
            if img is None:
                continue
            img = np.asarray(img)
            if img.ndim == 3 and img.shape[-1] > 3:
                img = img[..., :3]
            images[str(cam)] = img.astype(np.uint8, copy=False)
        state = {str(k): np.asarray(v, dtype=np.float64).reshape(-1) for k, v in (obs.get("state") or {}).items()}
        return cls(
            images=images,
            state=state,
            additional_info=copy.deepcopy(dict(obs.get("additional_info") or {})),
            t=int(t),
            instruction=str(obs.get("instruction") or ""),
        )

    # -- accessors ----------------------------------------------------------
    def image(self, view_or_cam: str) -> np.ndarray:
        """Image by MCP view name (``head``/``wrist_l``/``wrist_r``) or cam name."""
        cam = VIEW_TO_CAM.get(view_or_cam, view_or_cam)
        return self.images[cam]

    def joints(self, arm: str) -> np.ndarray:
        return np.asarray(self.state[f"{arm}_arm_joint_state"], dtype=np.float64)

    def ee_pose(self, arm: str) -> np.ndarray:
        """Measured 7-dim ee pose ``[x,y,z,qw,qx,qy,qz]`` (env-relative)."""
        return np.asarray(self.state[f"{arm}_ee_pose"], dtype=np.float64)

    def gripper_cmd_echo(self, arm: str) -> float:
        """``*_ee_joint_state`` — command echo, NOT measured (design §8.1 item 3)."""
        return float(np.asarray(self.state[f"{arm}_ee_joint_state"]).reshape(-1)[0])

    def gripper_width_measured(self, arm: str) -> float | None:
        """Measured gripper opening from deploy.py's allow-listed accessor, or None."""
        gw = self.additional_info.get("gripper_width_measured")
        if not isinstance(gw, Mapping) or gw.get(arm) is None:
            return None
        return float(gw[arm])

    @property
    def step_lim(self) -> int | None:
        v = self.additional_info.get("step_lim")
        return None if v is None else int(v)

    @property
    def frequency(self) -> int:
        return int(self.additional_info.get("frequency", CONTROL_HZ))

    def joint_vector(self) -> np.ndarray:
        """14-dim state ``[L arm6, L grip(echo), R arm6, R grip(echo)]`` (pi0.5 order)."""
        parts = []
        for arm in ARMS:
            parts.append(self.joints(arm))
            parts.append(np.asarray(self.state[f"{arm}_ee_joint_state"], dtype=np.float64).reshape(-1)[:1])
        return np.concatenate(parts)

    def to_robodojo_obs(self) -> dict[str, Any]:
        """Inverse of :meth:`from_robodojo_obs` (numpy arrays kept)."""
        return {
            "vision": {cam: {"color": img} for cam, img in self.images.items()},
            "state": {k: np.asarray(v) for k, v in self.state.items()},
            "instruction": self.instruction,
            "additional_info": copy.deepcopy(self.additional_info),
        }

    # -- (de)serialization --------------------------------------------------
    def to_dict(self, include_images: bool = False) -> dict[str, Any]:
        """JSON-safe dict. Images are omitted unless ``include_images`` (then
        kept as ``np.ndarray`` values, suitable for msgpack/npz, not JSON)."""
        d: dict[str, Any] = {
            "t": int(self.t),
            "instruction": self.instruction,
            "state": {k: _jsonable(np.asarray(v)) for k, v in self.state.items()},
            "additional_info": _jsonable(self.additional_info),
            "cams": sorted(self.images.keys()),
        }
        if include_images:
            d["images"] = {k: np.asarray(v) for k, v in self.images.items()}
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ObsFrame":
        return cls(
            images={k: np.asarray(v, dtype=np.uint8) for k, v in (d.get("images") or {}).items()},
            state={k: np.asarray(v, dtype=np.float64) for k, v in (d.get("state") or {}).items()},
            additional_info=copy.deepcopy(dict(d.get("additional_info") or {})),
            t=int(d.get("t", 0)),
            instruction=str(d.get("instruction", "")),
        )


# ---------------------------------------------------------------------------
# Memory sequence entry (design §3.1 记忆回路; memory_db/<task>/sequences.jsonl)
# ---------------------------------------------------------------------------


@dataclass
class SequenceEntry:
    """One line of ``memory_db/<task>/sequences.jsonl`` (design §3.1).

    ``nodes`` contain only ``perceive("…")`` targets (no coordinates, no
    layout ids). ``instruction_hash`` = sha256 of the normalized instruction
    (dedupe key with ``task``; keep the shorter sequence). ``lineage`` is the
    source episode id.
    """

    seq_id: str
    task: str
    instruction: str
    instruction_hash: str
    nodes: list[Node] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    lineage: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq_id": self.seq_id,
            "task": self.task,
            "instruction": self.instruction,
            "instruction_hash": self.instruction_hash,
            "nodes": [n.to_dict() for n in self.nodes],
            "evidence_refs": list(self.evidence_refs),
            "lineage": self.lineage,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "SequenceEntry":
        return cls(
            seq_id=str(d["seq_id"]),
            task=str(d["task"]),
            instruction=str(d.get("instruction", "")),
            instruction_hash=str(d.get("instruction_hash", "")),
            nodes=[Node.from_dict(n) for n in d.get("nodes", [])],
            evidence_refs=list(d.get("evidence_refs", [])),
            lineage=str(d.get("lineage", "")),
            created_at=str(d.get("created_at", "")),
        )


# ---------------------------------------------------------------------------
# EpisodeRecord (traces/<episode_id>/; design §3.3 memory writer inputs)
# ---------------------------------------------------------------------------


@dataclass
class EpisodeRecord:
    """Per-episode summary written by the trace writer and read offline.

    ``official_success`` / ``official_score`` are filled ONLY offline by P5
    from the official per-episode result files (never visible to P1/P2 at run time; design §3.5
    last row). ``split`` ∈ {E, V, AA, T, spare} or None. The run-time
    runner never knows it (default None); it is derived offline from P5's
    ``(task, seed, layout_id)`` by :func:`etude.core.splits.classify`, and the
    memory writer ignores any value found here (design §3.2).
    """

    episode_id: str
    task: str
    instruction: str = ""
    seed: int | None = None
    layout_idx: int | None = None
    split: str | None = None
    nodes: list[Node] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    steps: int = 0
    chunks: int = 0
    vlm_calls: int = 0
    vlm_tokens: int = 0
    wall_s: float = 0.0
    harness_commit: str = ""
    official_success: bool | None = None
    official_score: float | None = None

    @property
    def all_pass(self) -> bool:
        return bool(self.verdicts) and all(v.kind == "Pass" for v in self.verdicts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "task": self.task,
            "instruction": self.instruction,
            "seed": self.seed,
            "layout_idx": self.layout_idx,
            "split": self.split,
            "nodes": [n.to_dict() for n in self.nodes],
            "verdicts": [v.to_dict() for v in self.verdicts],
            "evidence_refs": list(self.evidence_refs),
            "steps": int(self.steps),
            "chunks": int(self.chunks),
            "vlm_calls": int(self.vlm_calls),
            "vlm_tokens": int(self.vlm_tokens),
            "wall_s": float(self.wall_s),
            "harness_commit": self.harness_commit,
            "official_success": self.official_success,
            "official_score": self.official_score,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "EpisodeRecord":
        return cls(
            episode_id=str(d["episode_id"]),
            task=str(d["task"]),
            instruction=str(d.get("instruction", "")),
            seed=d.get("seed"),
            layout_idx=d.get("layout_idx"),
            split=None if d.get("split") is None else str(d["split"]),
            nodes=[Node.from_dict(n) for n in d.get("nodes", [])],
            verdicts=[Verdict.from_dict(v) for v in d.get("verdicts", [])],
            evidence_refs=list(d.get("evidence_refs", [])),
            steps=int(d.get("steps", 0)),
            chunks=int(d.get("chunks", 0)),
            vlm_calls=int(d.get("vlm_calls", 0)),
            vlm_tokens=int(d.get("vlm_tokens", 0)),
            wall_s=float(d.get("wall_s", 0.0)),
            harness_commit=str(d.get("harness_commit", "")),
            official_success=d.get("official_success"),
            official_score=d.get("official_score"),
        )


# ---------------------------------------------------------------------------
# Lineage receipt (design §4 lineage 收据)
# ---------------------------------------------------------------------------


@dataclass
class L3Result:
    """``gates.L3`` = paired held-out result ``{b, c, eps, p}`` (design §3.3)."""

    b: int
    c: int
    eps: float
    p: float

    def to_dict(self) -> dict[str, Any]:
        return {"b": int(self.b), "c": int(self.c), "eps": self.eps, "p": float(self.p)}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "L3Result":
        eps = d["eps"]
        return cls(b=int(d["b"]), c=int(d["c"]), eps=eps if isinstance(eps, (int, float)) else float(eps), p=float(d["p"]))


@dataclass
class GateResults:
    """``gates`` = ``{L0: bool, L1: "5/5", L2: "19/20", L3: {...}}``; a gate
    that was not reached is None (omitted from the dict)."""

    L0: bool | None = None
    L1: str | None = None
    L2: str | None = None
    L3: L3Result | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {"L0": self.L0, "L1": self.L1, "L2": self.L2, "L3": None if self.L3 is None else self.L3.to_dict()}
        )

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "GateResults":
        d = d or {}
        return cls(
            L0=d.get("L0"),
            L1=d.get("L1"),
            L2=d.get("L2"),
            L3=None if d.get("L3") is None else L3Result.from_dict(d["L3"]),
        )


@dataclass
class Receipt:
    """Lineage receipt (design §4), one per evolution round, stored under
    ``evolution/lineage/<receipt_id>.json``.

    ``cost`` = ``{"vlm_tokens_per_ep": [incumbent, candidate],
    "wall_per_ep_s": [incumbent, candidate]}``; ``evaluated_on`` =
    ``{"V": "seed1[0:50]", "tasks": [...]}``; ``env`` =
    ``{"robodojo","xpolicylab","pi05","model"}``.

    ``research_run`` (extension, not in the design JSON) is the Research OS
    Run label/ULID (``research/ledger.json``, e.g. ``"E4-R003"``) whose
    committed, pushed source produced the L3 numbers; None until the user has
    created that Run (agents never create authorised records).
    """

    receipt_id: str
    parent: str
    candidate: str
    surface: str
    diff_sha: str
    hypothesis: str
    gates: GateResults = field(default_factory=GateResults)
    cost: dict[str, list[float]] = field(default_factory=dict)
    decision: str = "reject"
    evaluated_on: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    rollback: str = ""
    research_run: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "parent": self.parent,
            "candidate": self.candidate,
            "surface": self.surface,
            "diff_sha": self.diff_sha,
            "hypothesis": self.hypothesis,
            "gates": self.gates.to_dict(),
            "cost": _jsonable(self.cost),
            "decision": self.decision,
            "evaluated_on": _jsonable(self.evaluated_on),
            "env": dict(self.env),
            "rollback": self.rollback,
            "research_run": self.research_run,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Receipt":
        return cls(
            receipt_id=str(d["receipt_id"]),
            parent=str(d["parent"]),
            candidate=str(d["candidate"]),
            surface=str(d["surface"]),
            diff_sha=str(d.get("diff_sha", "")),
            hypothesis=str(d.get("hypothesis", "")),
            gates=GateResults.from_dict(d.get("gates")),
            cost={k: list(v) for k, v in (d.get("cost") or {}).items()},
            decision=str(d.get("decision", "reject")),
            evaluated_on=dict(d.get("evaluated_on") or {}),
            env=dict(d.get("env") or {}),
            rollback=str(d.get("rollback", "")),
            research_run=None if d.get("research_run") is None else str(d["research_run"]),
        )


# ---------------------------------------------------------------------------
# Patch (evolution editor output; design §3.1 提案)
# ---------------------------------------------------------------------------

EVOLVABLE_SURFACES: tuple[str, ...] = (
    "prompts/planner.md",
    "rules/recovery.yaml",
    "skills/SKILL.md",
)


@dataclass
class Patch:
    """One candidate edit (design §3.1 提案): a unified diff against one
    ``evolvable/`` surface, a one-sentence hypothesis and the target medoid
    layout ids (used only by gate L1, never written into evolvable files)."""

    surface: str
    diff: str
    hypothesis: str = ""
    medoids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "diff": self.diff,
            "hypothesis": self.hypothesis,
            "medoids": list(self.medoids),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Patch":
        return cls(
            surface=str(d["surface"]),
            diff=str(d.get("diff", "")),
            hypothesis=str(d.get("hypothesis", "")),
            medoids=list(d.get("medoids", [])),
        )


__all__ = [
    "ACTION_DIM", "ARMS", "ARM_DOF", "CAMERAS", "CAM_TO_VIEW", "CHUNK_LEN", "CONTROL_HZ",
    "DEFAULT_EXEC_STEPS", "DEFAULT_MAX_CHUNKS", "DEFAULT_RETRY", "DEFAULT_STALL_CHUNKS",
    "DEV_TASKS", "EVIDENCE_KINDS", "EVOLVABLE_SURFACES", "EXECUTORS", "GRIPPER_CMDS",
    "HELDOUT_TASKS", "IMAGE_HW", "MAX_VLM_CALLS_PER_EPISODE", "MCP_TOOLS", "ON_FAIL_THEN",
    "PREDICATE_NAMES", "STEP_LIM", "VERDICT_KINDS", "VERDICT_SOURCES", "VIEW_TO_CAM",
    "Detection", "EpisodeRecord", "GateResults", "L3Result", "Node", "NodeArgs", "ObsFrame",
    "OnFail", "Patch", "PredicateResult", "Receipt", "SequenceEntry", "StopSpec", "Verdict",
    "instruction_hash",
]
