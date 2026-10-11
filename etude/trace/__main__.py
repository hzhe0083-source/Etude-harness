"""``uv run python -m etude.trace --demo``: write one fake episode to a temp
``traces/`` and read it back (design §4 tree, 存储). Fakes only; no env, no model."""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import numpy as np

from etude.core.types import CONTROL_HZ, EpisodeRecord, Node
from etude.fakes import FakePolicy, FakeVerifier, fake_frame, three_node_sequence
from etude.trace.reader import TraceReader
from etude.trace.writer import FileTraceWriter


def run_fake_episode(traces_dir: Path, episode_id: str, *, instruction: str = "stack the bowls",
                     task: str = "stack_bowls", nodes: list[Node] | None = None,
                     kinds: list[str] | None = None, split: str = "E") -> EpisodeRecord:
    """Drive :class:`FileTraceWriter` through a fake episode: per node a
    ``<id>_before`` keyframe, chunks of 25 steps with per-second ticks, an
    ``<id>_after`` keyframe and a verdict (design §3.3 get_action loop shape)."""
    nodes = nodes if nodes is not None else three_node_sequence()
    tw = FileTraceWriter(traces_dir)
    pol, ver = FakePolicy(), FakeVerifier(kinds=kinds or [])
    tw.start_episode(episode_id, task, instruction, meta={"split": split, "seed": 0, "layout_idx": 3})
    t, chunk_idx, verdicts, refs = 0, 0, [], []
    for n in nodes:
        f0 = fake_frame(t=t, instruction=instruction)
        before = tw.keyframe(f0, f"{n.id}_before")
        tw.node_start(n, t, chunk_idx)
        hist = [f0]
        for _ in range(2):
            a = pol.infer(hist[-1], n.instruction)[: n.stop.exec_steps]
            tw.chunk(n.id, chunk_idx, t, len(a), {"executor": n.executor})
            for _row in a:
                t += 1
                fr = fake_frame(t=t, instruction=instruction)
                tw.tick(fr)
            hist.append(fr)
            chunk_idx += 1
        after = tw.keyframe(hist[-1], f"{n.id}_after")
        v = ver.check(n, hist)
        v.evidence_refs = before[:1] + after[:1]
        tw.verdict(n, v)
        verdicts.append(v)
        refs += v.evidence_refs
    rec = EpisodeRecord(episode_id=episode_id, task=task, instruction=instruction, seed=0, layout_idx=3, split=split,
                        nodes=nodes, verdicts=verdicts, evidence_refs=refs, steps=t, chunks=chunk_idx, vlm_calls=1,
                        official_success=True)  # dropped by end_episode: official_* is P5-only
    tw.end_episode(rec)
    if tw.errors:
        raise RuntimeError(f"trace writer errors: {tw.errors}")
    return rec


def _demo() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="etude-trace-demo-"))
    traces = tmp / "traces"
    run_fake_episode(traces, "ep0001")
    run_fake_episode(traces, "ep0002", kinds=["Pass", "Fail"])
    r = TraceReader(traces)
    print("traces dir :", traces)
    print("episodes   :", r.list_episodes())
    for ep in r.list_episodes():
        tr = r.load(ep)
        kf = tr.keyframes()
        ef = tr.earliest_failure()
        print(f"{ep}: events={len(tr.events)} kinds={sorted({e['ev'] for e in tr.events})} keyframe_events={len(kf)} "
              f"pngs={len(list((tr.path / 'keyframes').glob('*.png')))} verdicts={[v.kind for v in tr.verdicts()]} "
              f"earliest_failure={ef.node if ef else None} official_success={tr.record.official_success}")
    tr = r.load("ep0001")
    ref = tr.verdicts()[0].evidence_refs[0]
    img = r.read_keyframe(ref)
    print("ref        :", ref, img.shape, img.dtype)
    orig = fake_frame(t=0).image("cam_head")
    head_ref = tr.keyframes("n1_before")[0]["refs"][0]
    print("lossless   :", bool(np.array_equal(r.read_keyframe(head_ref), orig)), head_ref)
    print("periodic   :", [e["tag"] for e in tr.keyframes() if e["tag"].startswith("s")][:5], f"(every {CONTROL_HZ} steps)")
    try:
        r.resolve_ref("../../etc/passwd")
    except ValueError as e:
        print("refused    :", e)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="python -m etude.trace")
    ap.add_argument("--demo", action="store_true")
    if ap.parse_args().demo:
        raise SystemExit(_demo())
    ap.print_help()
