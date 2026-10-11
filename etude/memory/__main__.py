"""``uv run python -m etude.memory --demo`` and small offline commands
(design §3.1 记忆回路, §3.3 记忆写入, §3.5 评测期冻结).

Demo (temp directory only; never touches the real ``memory_db/``):
write two sequences, show dedupe/keep-shorter and the four rejection gates,
look up top-3, ingest from a trace dir, freeze, show that append fails and
that tampering is detected, print digests.

Other commands (operate on ``--root``, default ``memory_db/``)::

    python -m etude.memory audit
    python -m etude.memory lookup <task> "<instruction>"
"""
from __future__ import annotations

import argparse
import copy
import json
import tempfile
from pathlib import Path

from etude.core.types import EpisodeRecord, Node, NodeArgs, Verdict
from etude.fakes import three_node_sequence
from etude.memory.store import MemoryDB, MemoryFrozenError, MemoryTampered
from etude.memory.writer import ingest_traces, write_if_passed


def _episode(ep_id: str, instruction: str, nodes: list[Node], *, kinds: list[str] | None = None,
             split: str | None = None, task: str = "stack_bowls") -> EpisodeRecord:
    kinds = kinds or ["Pass"] * len(nodes)
    verdicts = [Verdict(node=n.id, kind=k, evidence_refs=[f"{ep_id}/keyframes/{n.id}_after_cam_head.png"])
                for n, k in zip(nodes, kinds, strict=True)]
    return EpisodeRecord(episode_id=ep_id, task=task, instruction=instruction, seed=0, layout_idx=int(ep_id[-2:]),
                         split=split, nodes=nodes, verdicts=verdicts, steps=300, chunks=12, vlm_calls=2)


def _off(ep_id: str, passed: bool = True, *, task: str = "stack_bowls", seed: int = 0, **extra) -> dict:
    """P5's offline result for one episode: official success + the official (task, seed, layout_id)
    the split is derived from (design §3.2; never from the trace)."""
    return {"episode_id": ep_id, "official_success": passed, "task": task, "seed": seed, "layout_id": int(ep_id[-2:]),
            **extra}


def _demo() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="etude-memory-demo-"))
    db = MemoryDB(tmp / "memory_db", frozen=False)
    print("memory root:", db.root)

    def show(tag: str, res) -> None:
        print(f"  {tag:<34} written={res.written!s:<5} reason={res.reason}" + (f"  errors={res.errors[:2]}" if res.errors else ""))

    print("[write]")
    seq3 = three_node_sequence("red")
    show("ep01 3 nodes, official pass", write_if_passed(_episode("ep01", "stack the bowls", seq3), _off("ep01"), store=db))
    show("ep02 other instruction", write_if_passed(
        _episode("ep02", "put the bottles into the dustbin", three_node_sequence("blue"), task="put_bottles_into_dustbin"),
        _off("ep02", task="put_bottles_into_dustbin", research_run="E1-R001"), store=db))
    show("ep03 same instr, 2 nodes (shorter)", write_if_passed(_episode("ep03", "Stack  the bowls", seq3[1:]), _off("ep03"), store=db))
    show("ep04 same instr, 3 nodes (longer)", write_if_passed(_episode("ep04", "stack the bowls", seq3), _off("ep04"), store=db))
    show("ep05 official fail", write_if_passed(_episode("ep05", "stack the blue bowls", seq3), _off("ep05", False), store=db))
    show("ep06 a node Fail (retry)", write_if_passed(_episode("ep06", "stack the blue bowls", seq3, kinds=["Pass", "Fail", "Pass"]), _off("ep06"), store=db))
    num = copy.deepcopy(seq3)
    num[0].args = NodeArgs(target="[0.31,-0.15,0.02]", arm="left")
    show("ep07 numeric target", write_if_passed(_episode("ep07", "stack the blue bowls", num), _off("ep07"), store=db))
    lay = copy.deepcopy(seq3)
    lay[1].instruction = "pick up the bowl like in layout 7"
    show("ep08 layout id in instruction", write_if_passed(_episode("ep08", "stack the blue bowls", lay), _off("ep08"), store=db))
    show("ep09 V layout (offline seed 1)", write_if_passed(_episode("ep09", "stack the blue bowls", seq3, split="V"), _off("ep09", seed=1), store=db))
    show("ep12 official pass, no seed/layout", write_if_passed(_episode("ep12", "stack the blue bowls", seq3), True, store=db))
    show("ep13 trace says E, P5 says seed 1", write_if_passed(_episode("ep13", "stack the blue bowls", seq3, split="E"), _off("ep13", seed=1), store=db))
    show("ep10 no official result", write_if_passed(_episode("ep10", "stack the blue bowls", seq3), None, store=db))
    show("ep11 similar instruction", write_if_passed(_episode("ep11", "stack the green bowls", three_node_sequence("green")), _off("ep11"), store=db))

    print("[ingest from traces/ (official results come from P5, not the trace)]")
    from etude.trace.__main__ import run_fake_episode

    traces = tmp / "traces"
    run_fake_episode(traces, "ep21", instruction="stack all bowls into one pile")
    run_fake_episode(traces, "ep22", instruction="stack the bowls neatly", kinds=["Pass", "Fail"])
    for r in ingest_traces({"ep21": _off("ep21"), "ep22": _off("ep22")}, traces_dir=traces, store=db):
        show(r.episode_id, r)

    print("[lookup stack_bowls 'stack the bowls' -> top-3]")
    for e in db.lookup("stack_bowls", "stack the bowls"):
        print(f"  {e.seq_id} nodes={len(e.nodes)} instr={e.instruction!r} lineage={e.lineage}")
    print("  (file lines:", sum(1 for _ in db.iter_lines("stack_bowls")), "live:", len(db.live("stack_bowls")), ")")
    print("  provenance kept offline:", db.get("stack_bowls", db.lookup("stack_bowls", "stack the bowls")[0].seq_id).created_from)

    green = db.lookup("stack_bowls", "stack the green bowls", k=1)[0]
    db.retract("stack_bowls", green.seq_id, reason="demo rollback by seq_id")
    print("[retract]", green.seq_id, "-> live:", len(db.live("stack_bowls")))

    print("[audit / freeze]")
    a = db.audit()
    print("  digest before freeze:", a.digest)
    for rel, sha in a.files.items():
        print(f"    {rel}  {sha[:16]}…")
    digest = db.freeze(reason="demo: evaluation period T")
    print("  frozen digest       :", digest)
    ev = MemoryDB(db.root)  # auto-frozen via FROZEN.json, verified on open
    print("  reopened frozen     :", ev.frozen, "lookup still works:", len(ev.lookup("stack_bowls", "stack the bowls")))
    try:
        ev.append(copy.deepcopy(ev.lookup("stack_bowls", "stack the bowls")[0]))
    except MemoryFrozenError as e:
        print("  append refused      :", e)
    try:
        write_if_passed(_episode("ep30", "stack the yellow bowls", three_node_sequence("yellow")), _off("ep30"), store=ev)
    except MemoryFrozenError as e:
        print("  writer refused      :", e)
    with open(db.task_file("stack_bowls"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"op": "retract", "seq_id": "x", "reason": "tamper"}) + "\n")
    try:
        MemoryDB(db.root)
    except MemoryTampered as e:
        print("  tamper detected     :", e)
    print("  digest after tamper :", db.audit().digest)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m etude.memory")
    ap.add_argument("--demo", action="store_true", help="run the fake demo in a temp dir")
    ap.add_argument("--root", default=None, help="memory_db directory (default: repo memory_db/)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("audit", help="print the directory digest and freeze status")
    lk = sub.add_parser("lookup", help="top-3 sequences for a task + instruction")
    lk.add_argument("task")
    lk.add_argument("instruction")
    args = ap.parse_args()
    if args.demo:
        return _demo()
    if args.cmd == "audit":
        db = MemoryDB(args.root, verify_on_open=False)
        print(json.dumps(db.audit().to_dict(), indent=2))
        return 0
    if args.cmd == "lookup":
        db = MemoryDB(args.root)
        print(json.dumps([e.to_dict() for e in db.lookup(args.task, args.instruction)], indent=2, ensure_ascii=False))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
