"""Code verifier: five-kind node verdicts (design §4 判决 JSON; §3.3
``verifier.check(n, self.hist)``; §3.5 threat table; §8.3 E row 1).

Decision order in :meth:`CodeVerifier.check` (design §4 "判定顺序 SafetyStop →
后置条件 → Blocked"; completion takes precedence, Recova 2610.01178 Alg. 1
line 23):

1. **SafetyStop** -- a joint-velocity spike between consecutive frames
   (> ``safety.joint_vel_max_rad_s``) or a ``physx_warning`` flag in any
   frame of ``hist`` (harness-written, see HARNESS_FLAG_KEYS). Always first.
2. Postcondition predicates (:mod:`etude.verifier.predicates`):
   any confident False → Fail; all confident True → **Pass**; otherwise
   InsufficientEvidence. A Pass stands even if an ``ik_failed`` flag was
   raised mid-node: the flag is recorded as evidence
   (``predicates["ik_failed"] = {v: true, ...}``) and does not change the kind.
3. **Blocked** -- only when the postconditions are NOT all met: an
   ``ik_failed`` flag (the runner writes it into the last frame's harness
   info when our ee_move executor arrived although its last chunk was not
   followed; IK failure is silent in RoboDojo, design §8.1 #7; an ee_move
   that did not arrive is Blocked by the runner without a check)
   for the node's arm, or any arm when the node has none, turns the Fail /
   InsufficientEvidence into Blocked (``predicates["ik_ok"] = false`` plus the
   postcondition results as evidence).
4. **Fail** / **InsufficientEvidence** otherwise. On InsufficientEvidence
   (and no ``ik_failed`` flag, so P3 adds no VLM call) the injected
   ``inspect_hook(frame_ref, region)`` (VISTA-style second opinion, design §4
   MCP ``inspect``) is called at most ``verifier.max_inspect_per_node`` (=1)
   times per check. A bool answer replaces the uncertain predicate and the
   verdict is re-decided with ``source="inspect_second_opinion"``; None leaves
   it InsufficientEvidence. ``check(..., recheck=True)`` (anchor re-verification,
   design §3.3) never calls the hook: 0 VLM calls.

Every non-Pass verdict carries the derived field ``scene_intact`` (design §4;
Recova 2610.01178 App. B M_int, Alg. 1 lines 27-30): detected non-target
objects ``not_moved`` against the episode's t=0 keyframe I_0 (registered by
the runner through :meth:`CodeVerifier.set_reference`), reusing
``rgb_diff.not_moved_max``. "Non-target" = scene objects the episode's nodes
mention (:func:`~etude.verifier.predicates.node_objects`) minus those that
the judged node or an earlier passed node legitimately moves
(:func:`~etude.verifier.predicates.node_manipulated`, incl. the implicit
object of a contact node without ``args.target``, e.g. the block a ``vla``
pick lifts after an ``ee_move`` "above the red block"; the runner registers
each installed sequence through :meth:`CodeVerifier.register_sequence`). When
such a node's object cannot be named
(:func:`~etude.verifier.predicates.untracked_contact`) the field is None
(undecidable), never a guess. It never changes the kind.

The verifier never reads the node's instruction text, planner self-reports,
episode-end / termination signals or official scores: frames are sanitized
before any predicate sees them (design §3.5 "自宣成功", "回合提前结束").
"""
from __future__ import annotations

import inspect as _inspect
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from etude.core.config import load_forbidden
from etude.core.interfaces import Perceiver, Verifier
from etude.core.types import ARMS, CONTROL_HZ, VIEW_TO_CAM, Node, ObsFrame, PredicateResult, Verdict
from etude.verifier.predicates import (
    HARNESS_FLAG_KEYS,
    EvalContext,
    PredicateOutcome,
    VerifierThresholds,
    evaluate_predicate,
    load_verifier_thresholds,
    node_manipulated,
    node_objects,
    sanitize_frame,
    scene_not_moved,
    untracked_contact,
)

__all__ = [
    "InspectHook",
    "KeyframeFn",
    "CodeVerifier",
    "PngKeyframeSaver",
    "decide_kind",
    "joint_velocity_peak",
]

#: ``inspect_hook(frame_ref, region) -> bool | None``; region is
#: ``{"x","y","w","h"}`` px or None (whole frame). May also accept a
#: ``question=`` keyword, which is then filled in.
InspectHook = Callable[..., "bool | None"]
#: ``keyframe_fn(frame, tag) -> list[str]`` (TraceWriter.keyframe signature).
KeyframeFn = Callable[[ObsFrame, str], list[str]]


def decide_kind(preds: Mapping[str, PredicateResult], conf_min: float) -> str:
    """Map predicate results to Pass / Fail / InsufficientEvidence (design §4):
    any confident False → Fail; all confident True → Pass; else (incl. no
    predicates) InsufficientEvidence."""
    if not preds:
        return "InsufficientEvidence"
    if any(p.v is False and p.conf >= conf_min for p in preds.values()):
        return "Fail"
    if all(p.v is True and p.conf >= conf_min for p in preds.values()):
        return "Pass"
    return "InsufficientEvidence"


def joint_velocity_peak(frames: Sequence[ObsFrame]) -> tuple[float, int | None]:
    """Max |Δq|/Δt (rad/s) over consecutive frames for both 6-dof arms, with
    Δt = Δstep / frequency (design §4 SafetyStop). Returns ``(peak, t)`` where
    ``t`` is the step of the later frame of the peak pair (None if <2 frames)."""
    peak, at = 0.0, None
    for f0, f1 in zip(frames[:-1], frames[1:]):
        dt = (f1.t - f0.t) / float(f1.frequency or CONTROL_HZ)
        if dt <= 0:
            continue
        for arm in ARMS:
            try:
                v = float(np.max(np.abs(f1.joints(arm) - f0.joints(arm))) / dt)
            except KeyError:
                continue
            if v > peak:
                peak, at = v, f1.t
    return peak, at


def _flag(frame: ObsFrame, key: str) -> Any:
    """Harness flag ``key`` from ``additional_info[key]`` or ``additional_info["harness"][key]``."""
    v = frame.additional_info.get(key)
    if v is None and isinstance(frame.additional_info.get("harness"), Mapping):
        v = frame.additional_info["harness"].get(key)
    return v


class PngKeyframeSaver:
    """Minimal keyframe writer (design §4 ``evidence_refs`` → ``kf/...png``;
    §4 storage: lossless PNG). Writes one PNG per camera to
    ``<root>/<prefix>/<tag>_<cam>.png`` and returns refs relative to ``root``.
    The production writer is ``TraceWriter.keyframe`` (etude/trace); this one
    exists for demos and offline re-verification."""

    def __init__(self, root: str | Path, prefix: str = "kf"):
        self.root = Path(root)
        self.prefix = prefix

    def __call__(self, frame: ObsFrame, tag: str) -> list[str]:
        from PIL import Image

        out_dir = self.root / self.prefix
        out_dir.mkdir(parents=True, exist_ok=True)
        refs = []
        for cam, img in sorted(frame.images.items()):
            rel = f"{self.prefix}/{tag}_{cam}.png"
            Image.fromarray(np.asarray(img, dtype=np.uint8)).save(self.root / rel)
            refs.append(rel)
        return refs

    def resolve(self, ref: str) -> np.ndarray:
        """Load a ref written by this saver."""
        from PIL import Image

        return np.asarray(Image.open(self.root / ref).convert("RGB"))


class CodeVerifier(Verifier):
    """Frozen code verifier implementing :class:`etude.core.interfaces.Verifier`
    (design §4 判决 JSON, §3.3, §3.5, §8.3 E row 1).

    Parameters
    ----------
    perceiver:
        Open-set detector (``OwlV2Perceiver`` in production,
        ``ColorBlobPerceiver`` / ``FakePerceiver`` in demos).
    root:
        Repo root for frozen/params and frozen/forbidden.yaml (default REPO_ROOT).
    thresholds:
        Pre-loaded thresholds; default :func:`load_verifier_thresholds` (root).
    inspect_hook:
        ``(frame_ref, region) -> bool | None`` second opinion; None disables.
    keyframe_fn:
        ``(frame, tag) -> list[str]`` that persists keyframes (e.g.
        ``TraceWriter.keyframe`` or :class:`PngKeyframeSaver`). If None the
        refs are in-memory ``mem://`` refs resolvable by :meth:`resolve_ref`.
    home_joints:
        ``{arm: (6,)}`` home configuration for ``arm_home``; if None, the
        first frame with ``t == 0`` seen by :meth:`check` sets it (episode
        start), and :meth:`reset` clears it.
    chunk_steps:
        Steps per chunk index used for the default ``t_chunk`` (= t // chunk_steps).
    scene_queries:
        extra object queries always watched by ``scene_intact`` (e.g. a task's
        object vocabulary); node-mentioned objects are added automatically.
    """

    def __init__(
        self,
        perceiver: Perceiver,
        *,
        root: str | Path | None = None,
        thresholds: VerifierThresholds | None = None,
        inspect_hook: InspectHook | None = None,
        keyframe_fn: KeyframeFn | None = None,
        home_joints: Mapping[str, Sequence[float]] | None = None,
        chunk_steps: int = CONTROL_HZ,
        scene_queries: Sequence[str] | None = None,
    ):
        self.perceiver = perceiver
        self.root = root
        self.th = thresholds or load_verifier_thresholds(root)
        self.inspect_hook = inspect_hook
        self.keyframe_fn = keyframe_fn
        self.chunk_steps = max(1, int(chunk_steps))
        self._home_injected = home_joints is not None
        self.home: dict[str, np.ndarray] | None = (
            {a: np.asarray(v, dtype=float) for a, v in home_joints.items()} if home_joints is not None else None
        )
        try:
            env_info = list((load_forbidden(root).get("allowed_obs") or {}).get("additional_info") or [])
        except FileNotFoundError:
            env_info = []
        self.allowed_info: tuple[str, ...] = tuple(env_info or ["frequency", "step_lim", "gripper_width_measured"]) + HARNESS_FLAG_KEYS
        self._mem: dict[str, np.ndarray] = {}
        self.inspect_calls: list[dict[str, Any]] = []
        self._scene_injected: tuple[str, ...] = tuple(q for q in (scene_queries or ()) if q)
        #: I_0: sanitized episode reference keyframe for ``scene_intact`` (t=0 frame).
        self.scene_ref: ObsFrame | None = None
        #: object queries ``scene_intact`` may watch (injected + mentioned by any node).
        self.scene_queries: set[str] = set(self._scene_injected)
        #: ``(t_pass, objects, node_id)`` per passed node: objects legitimately moved by
        #: then; ``objects`` None = a contact node whose object could not be named.
        self._moved_ledger: list[tuple[int, frozenset[str] | None, str]] = []
        #: preceding nodes per node of the installed sequences (implicit targets):
        #: by object identity, and by id for the latest sequence.
        self._preceding: dict[int, tuple[Node, ...]] = {}
        self._preceding_by_id: dict[str, tuple[Node, ...]] = {}
        #: per-object detail of the last :meth:`scene_intact` call (for traces).
        self.last_scene: dict[str, Any] = {}

    # -- episode state -------------------------------------------------------
    def reset(self) -> None:
        """Clear per-episode state (auto home, scene reference and ledger,
        in-memory keyframes, inspect log)."""
        if not self._home_injected:
            self.home = None
        self.scene_ref = None
        self.scene_queries = set(self._scene_injected)
        self._moved_ledger.clear()
        self._preceding.clear()
        self._preceding_by_id.clear()
        self.last_scene = {}
        self._mem.clear()
        self.inspect_calls.clear()

    # -- scene_intact (design §4 derived field; Recova 2610.01178 App. B) -----
    def set_reference(self, frame: ObsFrame) -> None:
        """Register I_0 for ``scene_intact``: the episode's t=0 keyframe (the
        runner calls this next to :meth:`set_home`). The runner also calls it
        again after an anchor was invalidated by a disturbed scene and P1
        replanned from the current scene (every anchor is dropped then). Moves
        recorded before the new reference are part of it, so the pass ledger
        is cleared."""
        self.scene_ref = sanitize_frame(frame, self.allowed_info)
        self._moved_ledger.clear()

    def register_scene_objects(self, queries: Sequence[str]) -> None:
        """Add object queries (e.g. every ``perceive`` target / object arg of an
        installed plan) to the set ``scene_intact`` watches."""
        self.scene_queries.update(q for q in queries if q)

    def register_sequence(self, nodes: Sequence[Node]) -> None:
        """Register an installed node sequence: its objects become watchable and
        each node's predecessors are kept so a contact node without
        ``args.target`` gets its implicit object
        (:func:`~etude.verifier.predicates.implicit_targets`)."""
        nodes = list(nodes)
        self.register_scene_objects(sorted({q for n in nodes for q in node_objects(n)}))
        self._preceding_by_id = {}
        for i, n in enumerate(nodes):
            pre = tuple(nodes[:i])
            self._preceding[id(n)] = pre
            self._preceding_by_id.setdefault(n.id, pre)

    def _context(self, node: Node) -> tuple[Node, ...]:
        pre = self._preceding.get(id(node))
        return pre if pre is not None else self._preceding_by_id.get(node.id, ())

    def manipulated(self, node: Node) -> frozenset[str] | None:
        """Objects ``node`` legitimately moves (explicit or implicit, given the
        registered sequence); None for a contact node whose object cannot be named."""
        pre = self._context(node)
        if untracked_contact(node, pre):
            return None
        return frozenset(node_manipulated(node, pre))

    def note_pass(self, node: Node, t: int) -> frozenset[str] | None:
        """Record that ``node`` passed at step ``t``: its manipulated objects
        are targets (exempt from ``scene_intact``) from then on. ``check``
        calls it for its own Pass verdicts; the runner calls it for every
        accepted Pass (incl. second opinions). Duplicates are harmless.
        Returns the recorded set (None = untracked contact node)."""
        self.scene_queries.update(node_objects(node))
        objs = self.manipulated(node)
        self._moved_ledger.append((int(t), objs, node.id))
        return objs

    def scene_intact(self, frame: ObsFrame, node: Node | None = None, *, as_of_t: int | None = None,
                     pending: Sequence[Node] = (), keep: Sequence[str] = ()) -> bool | None:
        """Derived ``scene_intact`` for ``frame`` (design §4): every detected
        non-target object ``not_moved`` vs I_0. Targets = objects moved by
        nodes that passed at ``t <= as_of_t`` (all passes if None), ``node``'s
        own manipulated objects, and those of the ``pending`` nodes (an anchor
        re-verification passes the failed node and every node it will re-run)
        except ``keep`` (objects the anchor state itself depends on, e.g. its
        ``ee_move`` target). None if there is no reference, nothing to watch,
        or a counted node is an untracked contact node. Details go to
        :attr:`last_scene`."""
        if node is not None:
            self.scene_queries.update(node_objects(node))
        counted: list[tuple[str, frozenset[str] | None]] = []
        if node is not None:
            counted.append((node.id, self.manipulated(node)))
        counted += [(nid, objs) for t, objs, nid in self._moved_ledger if as_of_t is None or t <= as_of_t]
        keep_set = frozenset(keep)
        for n in pending:
            m = self.manipulated(n)
            counted.append((n.id, None if m is None else m - keep_set))
        exempt: set[str] = set()
        untracked: list[str] = []
        for nid, objs in counted:
            if objs is None:
                untracked.append(nid)
            else:
                exempt |= objs
        watch = sorted(self.scene_queries - exempt)
        self.last_scene = {"ref_t": None if self.scene_ref is None else self.scene_ref.t, "frame_t": frame.t,
                           "watched": watch, "exempt": sorted(exempt), "objects": {}, "intact": None}
        if untracked:
            self.last_scene["undecidable"] = f"contact node(s) without a nameable object: {sorted(set(untracked))}"
            return None
        if self.scene_ref is None or not watch:
            return None
        cur = sanitize_frame(frame, self.allowed_info)
        intact, per_obj = scene_not_moved(self.scene_ref, cur, watch, self.perceiver, self.th)
        self.last_scene.update(objects={q: r.to_dict() for q, r in per_obj.items()}, intact=intact)
        return intact

    def set_home(self, frame_or_joints: ObsFrame | Mapping[str, Sequence[float]]) -> None:
        """Set the ``arm_home`` reference from a frame or ``{arm: (6,)}`` (design §4 ``arm_home``)."""
        if isinstance(frame_or_joints, ObsFrame):
            self.home = {a: frame_or_joints.joints(a).copy() for a in ARMS}
        else:
            self.home = {a: np.asarray(v, dtype=float) for a, v in frame_or_joints.items()}

    def resolve_ref(self, ref: str) -> np.ndarray | None:
        """Image for an in-memory ``mem://`` evidence ref (None if unknown)."""
        return self._mem.get(ref)

    # -- keyframes -----------------------------------------------------------
    def _keyframes(self, frame: ObsFrame, tag: str) -> list[str]:
        if self.keyframe_fn is not None:
            return list(self.keyframe_fn(frame, tag))
        refs = []
        for cam, img in sorted(frame.images.items()):
            ref = f"mem://{tag}_{cam}"
            self._mem[ref] = img
            refs.append(ref)
        return refs

    @staticmethod
    def _ref_for_view(refs: Sequence[str], view: str | None) -> str | None:
        if not refs:
            return None
        if view is not None:
            cam = VIEW_TO_CAM.get(view, view)
            for r in refs:
                if cam in r:
                    return r
            for r in refs:
                if view in r:
                    return r
        return refs[0]

    # -- inspect -------------------------------------------------------------
    def _call_hook(self, frame_ref: str, region: Mapping[str, float] | None, question: str) -> bool | None:
        hook = self.inspect_hook
        if hook is None:
            return None
        kwargs: dict[str, Any] = {}
        try:
            params = _inspect.signature(hook).parameters
            if "question" in params or any(p.kind is p.VAR_KEYWORD for p in params.values()):
                kwargs["question"] = question
        except (TypeError, ValueError):
            pass
        try:
            ans = hook(frame_ref, region, **kwargs)
        except Exception as e:  # a failing second opinion is "no opinion"
            self.inspect_calls[-1]["error"] = repr(e)
            return None
        return ans if isinstance(ans, bool) else None

    # -- main ----------------------------------------------------------------
    def check(self, node: Node, hist: Sequence[ObsFrame], *, t_chunk: Sequence[int] | None = None,
              recheck: bool = False, as_of_t: int | None = None) -> Verdict:
        """Judge ``node`` from observation history ``hist`` (oldest first;
        ``hist[0]`` = state at node start, ``hist[-1]`` = state now) and return
        a five-kind :class:`Verdict` (design §4 判决 JSON, §3.3), in the order
        SafetyStop → postconditions → Blocked (module docstring).

        ``t_chunk`` overrides the default ``[hist[0].t // chunk_steps,
        hist[-1].t // chunk_steps]`` when the caller tracks chunk indices.
        ``recheck=True`` re-verifies an already-passed anchor node (design §3.3
        退回后复验): no inspect hook (0 VLM calls) and no pass bookkeeping.
        ``as_of_t`` limits the passes counted as targets for ``scene_intact``.
        """
        th = self.th
        frames = [sanitize_frame(f, self.allowed_info) for f in hist]
        if not frames:
            return Verdict(node=node.id, kind="InsufficientEvidence", predicates={}, evidence_refs=[],
                           source="code_verifier", t_chunk=list(t_chunk or [0, 0]))
        first = next((f for f in frames if f.t == 0), None)
        if first is not None and self.home is None:
            self.set_home(first)
        if first is not None and self.scene_ref is None:
            self.scene_ref = first
        self.scene_queries.update(node_objects(node))
        before, after = frames[0], frames[-1]
        tc = [int(x) for x in t_chunk] if t_chunk is not None else [before.t // self.chunk_steps, after.t // self.chunk_steps]
        refs_before = self._keyframes(before, f"{node.id}_before_t{before.t}")
        refs_after = self._keyframes(after, f"{node.id}_after_t{after.t}")
        evidence_refs = refs_before + refs_after

        def verdict(kind: str, preds: Mapping[str, PredicateResult], source: str = "code_verifier", refs: Sequence[str] | None = None) -> Verdict:
            v = Verdict(node=node.id, kind=kind, predicates=dict(preds), evidence_refs=list(refs if refs is not None else evidence_refs),
                        source=source, t_chunk=tc)
            if kind == "Pass":
                if not recheck:
                    self.note_pass(node, after.t)
            else:
                # a re-verified anchor's own moves are in the ledger as of its pass
                v.scene_intact = self.scene_intact(after, None if recheck else node, as_of_t=as_of_t)
            return v

        # 1. SafetyStop
        peak, at = joint_velocity_peak(frames)
        physx = [f.t for f in frames if _flag(f, "physx_warning")]
        if peak > th.joint_vel_max_rad_s or physx:
            preds: dict[str, PredicateResult] = {}
            spike_refs: list[str] = []
            if peak > th.joint_vel_max_rad_s:
                preds["joint_velocity_ok"] = PredicateResult(False, 1.0, f"joint_state:peak {peak:.2f} rad/s > {th.joint_vel_max_rad_s} at t={at}")
                spike = next(f for f in frames if f.t == at)
                if spike is not after:
                    spike_refs = self._keyframes(spike, f"{node.id}_spike_t{at}")
            if physx:
                preds["physx_ok"] = PredicateResult(False, 1.0, f"physx_warning at t={physx[:5]}")
            return verdict("SafetyStop", preds, refs=evidence_refs + spike_refs)

        # ik_failed flags (IK failure / out of workspace, written by our executor):
        # evidence for Pass, decisive only when the postconditions are not met (P3).
        arms = [node.args.arm] if node.args.arm in ARMS else list(ARMS)
        blocked_t: list[int] = []
        for f in frames:
            v = _flag(f, "ik_failed")
            if isinstance(v, Mapping):
                hit = any(bool(v.get(a)) for a in arms)
            elif isinstance(v, (list, tuple, set)):
                hit = any(a in v for a in arms)
            else:
                hit = bool(v)
            if hit:
                blocked_t.append(f.t)
        ik_src = f"ik_failed({','.join(arms)}) at t={blocked_t[:5]}"

        # 2. postconditions (completion takes precedence over a mid-node IK failure)
        ctx = EvalContext(node=node, frames=frames, perceiver=self.perceiver, th=th, home=self.home)
        outcomes: dict[str, PredicateOutcome] = {expr: evaluate_predicate(expr, ctx) for expr in node.postcondition}
        preds = {k: o.result for k, o in outcomes.items()}
        kind = decide_kind(preds, th.pred_conf_min)

        def blocked_or(kind: str, preds: Mapping[str, PredicateResult], source: str = "code_verifier") -> Verdict:
            if kind == "Pass":
                if blocked_t:
                    preds = {**preds, "ik_failed": PredicateResult(True, 1.0, f"{ik_src}; evidence only, postconditions met")}
                return verdict("Pass", preds, source=source)
            if blocked_t:  # 3. Blocked: postconditions not met and IK failed
                return verdict("Blocked", {"ik_ok": PredicateResult(False, 1.0, ik_src), **preds}, source=source)
            return verdict(kind, preds, source=source)

        if kind != "InsufficientEvidence" or not outcomes or blocked_t or recheck:
            return blocked_or(kind, preds)

        # 4. at most max_inspect_per_node second opinions
        source = "code_verifier"
        budget = th.max_inspect_per_node
        for expr, o in outcomes.items():
            if budget <= 0 or self.inspect_hook is None:
                break
            r = o.result
            if r.v is not None and r.conf >= th.pred_conf_min:
                continue
            ref = self._ref_for_view(refs_after, o.focus_view)
            if ref is None:
                break
            region = None
            if o.focus_region is not None:
                x, y, w, h = (float(v) for v in o.focus_region)
                region = {"x": x, "y": y, "w": w, "h": h}
            budget -= 1
            self.inspect_calls.append({"node": node.id, "predicate": expr, "frame_ref": ref, "region": region, "question": o.question})
            ans = self._call_hook(ref, region, o.question)
            self.inspect_calls[-1]["answer"] = ans
            if ans is not None:
                preds[expr] = PredicateResult(ans, max(r.conf, th.pred_conf_min), f"inspect_second_opinion({ref})<-{r.src}")
                source = "inspect_second_opinion"
        kind = decide_kind(preds, th.pred_conf_min)
        return blocked_or(kind, preds, source=source)
