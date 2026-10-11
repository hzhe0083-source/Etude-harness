"""Offline memory writer (design §3.1 记忆回路 编辑者 "无 LLM；离线脚本
memory/writer.py", §3.3 记忆写入伪代码)::

    for ep in episodes(E):
        if P5.official(ep).success and all(v.kind == "Pass" for v in ep.verdicts):
            store.append(task, strip_numeric(ep.seq), evidence=ep.refs, lineage=ep.id)

The official result comes ONLY from P5's offline scoring of the official
per-episode result files; the run-time trace never carries it (design §3.5
last row). Verifier-only admission is off in v1 (design §3.2: FPR × failures
would write wrong sequences), so ``official_result`` is mandatory.

The split is derived offline as well: ``official_result`` must be a mapping
carrying the official ``seed`` and ``layout_id`` (and ``task``) of the
episode, from which :func:`etude.core.splits.classify` gives E/V/T/spare.
The trace's ``split`` label is never trusted (the runner cannot know it), so
V, A/A and T episodes cannot enter memory even when their official result
is supplied (design §3.2 "记忆从这里积累" = E).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from etude.core.splits import classify
from etude.core.types import EpisodeRecord, SequenceEntry, instruction_hash
from etude.memory.sanitize import NumericLeakError, strip_numeric
from etude.memory.store import MemoryDB, MemoryFrozenError, new_seq_id, utc_now

#: Splits whose episodes may feed memory (design §3.2: "记忆从这里积累" = E).
DEFAULT_ALLOWED_SPLITS: tuple[str, ...] = ("E",)


@dataclass
class WriteResult:
    """Outcome of :func:`write_if_passed` for one episode (design §3.1 门)."""

    episode_id: str
    written: bool
    reason: str
    seq_id: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"episode_id": self.episode_id, "written": self.written, "reason": self.reason, "seq_id": self.seq_id, "errors": list(self.errors)}


def official_success(official_result: Any, episode_id: str | None = None) -> bool:
    """Interpret P5's offline official result (design §3.1 证据 "官方 judge（P5 离线）").

    Accepts ``True/False`` or a mapping with ``success`` / ``official_success``
    (bool). If the mapping names an ``episode_id`` it must match. Anything else
    (None, scores, strings) counts as not passed: only an explicit ``True`` admits.
    """
    if isinstance(official_result, bool):
        return official_result
    if isinstance(official_result, Mapping):
        rid = official_result.get("episode_id")
        if rid is not None and episode_id is not None and str(rid) != str(episode_id):
            raise ValueError(f"official result is for episode {rid!r}, not {episode_id!r}")
        for key in ("success", "official_success"):
            if key in official_result:
                return official_result[key] is True
    return False


def offline_split(official_result: Any, record: EpisodeRecord, root: str | Path | None = None) -> tuple[str | None, dict[str, Any], str]:
    """``(split, {task, seed, layout}, problem)`` from P5's official result (design §3.2).

    ``official_result`` must be a mapping with the official ``seed`` and
    ``layout_id`` (alias ``layout``); ``task`` defaults to the trace's task
    and must match it when both are given. ``split`` is None (and ``problem``
    says why) when the triple is missing or outside every split."""
    if not isinstance(official_result, Mapping):
        return None, {}, "official result carries no (seed, layout_id): split unknown"
    task = official_result.get("task") or record.task
    if official_result.get("task") and record.task and str(official_result["task"]) != str(record.task) \
            and str(official_result["task"]).removesuffix("_random") != str(record.task).removesuffix("_random"):
        return None, {}, f"official task {official_result['task']!r} != trace task {record.task!r}"
    seed = official_result.get("seed")
    layout = official_result.get("layout_id", official_result.get("layout"))
    if seed is None or layout is None:
        return None, {}, "official result lacks seed/layout_id: split unknown"
    try:
        meta = {"task": str(task), "seed": int(seed), "layout": int(layout)}
    except (TypeError, ValueError):
        return None, {}, f"official seed/layout not integers: {seed!r}/{layout!r}"
    split = classify(meta["task"], meta["seed"], meta["layout"], root)
    return split, meta, "" if split else f"{meta} is outside every split"


def node_pass_errors(record: EpisodeRecord) -> list[str]:
    """Why ``record`` fails "全节点 Pass" (design §3.1 门, §3.3 ``all(v.kind == "Pass")``):
    no verdicts, any non-Pass verdict (retries included), or a node without a Pass verdict."""
    errs: list[str] = []
    if not record.nodes:
        errs.append("episode has no nodes")
    if not record.verdicts:
        errs.append("episode has no verdicts")
    for v in record.verdicts:
        if v.kind != "Pass":
            errs.append(f"node {v.node}: verdict {v.kind}")
    passed = {v.node for v in record.verdicts if v.kind == "Pass"}
    for n in record.nodes:
        if n.id not in passed:
            errs.append(f"node {n.id}: no Pass verdict")
    return errs


def write_if_passed(
    episode_record: EpisodeRecord,
    official_result: Any,
    *,
    store: MemoryDB | None = None,
    allowed_splits: Iterable[str] = DEFAULT_ALLOWED_SPLITS,
    research_run: str | None = None,
) -> WriteResult:
    """Append the episode's node sequence to memory iff official success ∧
    every node Pass ∧ no numeric target / leakage (design §3.1 门, §3.3).

    * ``official_result``: P5's result for this episode: a mapping with
      ``success`` plus the official ``task``/``seed``/``layout_id`` (see
      :func:`official_success`, :func:`offline_split`);
      ``episode_record.official_*`` and ``episode_record.split`` are ignored
      on purpose so a run-time record can never self-admit or self-label.
    * ``allowed_splits``: only E-split episodes feed memory by default (§3.2);
      the split comes from :func:`offline_split`, never from the trace.
    * Dedupe/keep-shorter is done by :meth:`MemoryDB.append`.
    * ``research_run``: Research OS Run label that produced the official
      result, recorded in ``created_from`` (optional; may also be given as
      ``official_result["research_run"]``).

    Never raises for a rejected episode; returns a :class:`WriteResult`.
    :class:`MemoryFrozenError` from a frozen store IS raised (writing during
    evaluation is a protocol violation, design §3.5).
    """
    rec = episode_record
    store = store if store is not None else MemoryDB()
    split, meta, problem = offline_split(official_result, rec, store.config_root)
    if split is None:
        return WriteResult(rec.episode_id, False, "split unknown", errors=[problem])
    if split not in tuple(allowed_splits):
        return WriteResult(rec.episode_id, False, f"split {split!r} not in {tuple(allowed_splits)}")
    if rec.split is not None and rec.split != split:
        return WriteResult(rec.episode_id, False, f"trace split label {rec.split!r} != offline split {split!r}")
    try:
        ok = official_success(official_result, rec.episode_id)
    except ValueError as e:
        return WriteResult(rec.episode_id, False, "official result mismatch", errors=[str(e)])
    if not ok:
        return WriteResult(rec.episode_id, False, "official judge did not pass")
    errs = node_pass_errors(rec)
    if errs:
        return WriteResult(rec.episode_id, False, "not all nodes Pass", errors=errs)
    try:
        nodes = strip_numeric(rec.nodes, config_root=store.config_root)
    except NumericLeakError as e:
        return WriteResult(rec.episode_id, False, "numeric target / leakage", errors=e.errors)
    if isinstance(official_result, Mapping) and research_run is None and official_result.get("research_run"):
        research_run = str(official_result["research_run"])
    entry = SequenceEntry(
        seq_id=new_seq_id(),
        task=rec.task,
        instruction=" ".join(rec.instruction.split()),
        instruction_hash=instruction_hash(rec.instruction),
        nodes=nodes,
        evidence_refs=[r for r in rec.evidence_refs] + [r for v in rec.verdicts for r in v.evidence_refs if r not in rec.evidence_refs],
        lineage=rec.episode_id,
        created_at=utc_now(),
    )
    created_from = {"episode_id": rec.episode_id, "seed": meta["seed"], "layout": meta["layout"], "split": split}
    if research_run:
        created_from["research_run"] = research_run
    try:
        stored = store.append(entry, official_pass=True, created_from=created_from)
    except NumericLeakError as e:
        return WriteResult(rec.episode_id, False, "numeric target / leakage", errors=e.errors)
    except MemoryFrozenError:
        raise
    if not stored:
        return WriteResult(rec.episode_id, False, "duplicate (task, instruction_hash) with an equal or shorter live sequence")
    return WriteResult(rec.episode_id, True, "stored", seq_id=entry.seq_id)


def ingest_traces(
    official_results: Mapping[str, Any],
    *,
    traces_dir: str | Path | None = None,
    store: MemoryDB | None = None,
    allowed_splits: Iterable[str] = DEFAULT_ALLOWED_SPLITS,
) -> list[WriteResult]:
    """Offline batch over ``traces/<episode_id>/episode.json`` (design §3.3
    ``for ep in episodes(E)``): ``official_results`` maps episode_id → P5
    result mapping (``success``, ``task``, ``seed``, ``layout_id``); episodes
    without a result, or whose result lacks the official seed/layout, are
    rejected (the split is derived offline, design §3.2)."""
    from etude.trace.reader import TraceReader

    reader = TraceReader(traces_dir)
    store = store if store is not None else MemoryDB()
    out: list[WriteResult] = []
    for ep in reader.list_episodes():
        rec = reader.record(ep)
        if rec is None:
            out.append(WriteResult(ep, False, "no episode.json"))
            continue
        out.append(write_if_passed(rec, official_results.get(ep), store=store, allowed_splits=allowed_splits))
    return out


__all__ = [
    "DEFAULT_ALLOWED_SPLITS", "WriteResult", "ingest_traces", "node_pass_errors", "official_success", "offline_split",
    "strip_numeric", "write_if_passed",
]
