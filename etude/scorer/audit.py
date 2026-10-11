"""P5 audit (design §3.5 威胁表: mark_env_unstable, 改打分或汇总, 评测期写记忆, 同布局多次尝试).

* Unstable rate from RoboDojo resume manifests
  (``<seed>_<info>/_resume_<run_id>.json``: ``success_nums``, ``fail_nums``,
  ``unstable_nums``, ``abandoned_layout_ids``); above
  ``scorer.unstable_rate_review`` (0.05) → human review.
* File hashes recorded in receipts/audits: our ``deploy.py``, RoboDojo
  ``scripts/internal/summarize_result.py``, plus RoboDojo/XPolicyLab commit SHAs.
* Memory-directory tree hash (``memory_db/``) before/after an evaluation
  window: any change during T means the read-only rule was broken.
* Multiple timestamp runs per (task, policy, embodiment, seed) → flagged
  (best-of-N over the same layouts).

Audit records go to ``scorer/audit/<name>.json`` (unreadable to P1/P6 by
``deny_paths``). CLI: ``python -m etude.scorer.audit --demo`` /
``python -m etude.scorer.audit --eval-root … --memory memory_db --out NAME``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from etude import REPO_ROOT

DEFAULT_ROBODOJO = Path.home() / "Documents" / "RoboDojo"
SUMMARIZE_REL = "scripts/internal/summarize_result.py"
DEPLOY_REL = "deploy/XPolicyLab/policy/Etude/deploy.py"
AUDIT_DIR = REPO_ROOT / "scorer" / "audit"


def sha256_file(path: str | Path) -> str | None:
    """sha256 hex of a file, None if missing."""
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(directory: str | Path, ignore: Iterable[str] = (".gitkeep",)) -> str:
    """Order-independent sha256 over ``relpath\\0filehash\\n`` of every file (design §3.5 评测期写记忆)."""
    d = Path(directory)
    skip = set(ignore)
    h = hashlib.sha256()
    if d.is_dir():
        for p in sorted(x for x in d.rglob("*") if x.is_file() and x.name not in skip):
            h.update(f"{p.relative_to(d).as_posix()}\0{sha256_file(p)}\n".encode())
    return h.hexdigest()


def git_sha(repo: str | Path) -> str | None:
    """HEAD commit of a git checkout (None if not a repo)."""
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def env_shas(robodojo: str | Path = DEFAULT_ROBODOJO, harness_root: str | Path = REPO_ROOT) -> dict[str, str | None]:
    """Receipt ``env`` provenance + frozen-file hashes (design §3.5 改打分或汇总; §4 收据 env)."""
    rd = Path(robodojo)
    return {
        "robodojo": git_sha(rd),
        "xpolicylab": git_sha(rd / "XPolicyLab"),
        "summarize_result_sha256": sha256_file(rd / SUMMARIZE_REL),
        "deploy_py_sha256": sha256_file(Path(harness_root) / DEPLOY_REL),
        "harness": git_sha(harness_root),
    }


@dataclass
class UnstableReport:
    """Unstable / abandoned accounting for one run manifest (design §3.5 mark_env_unstable)."""

    manifest: str
    task: str
    seed: str
    successes: int
    fails: int
    unstable: int
    abandoned: int
    rate: float
    review: bool


def unstable_report(manifest: str | Path, threshold: float | None = None) -> UnstableReport:
    """Parse one ``_resume_<run_id>.json``; ``rate = unstable / (success+fail+unstable)``."""
    p = Path(manifest)
    d = json.loads(p.read_text(encoding="utf-8"))
    if threshold is None:
        threshold = _unstable_threshold()
    s, f, u = int(d.get("success_nums", 0)), int(d.get("fail_nums", 0)), int(d.get("unstable_nums", 0))
    total = s + f + u
    rate = u / total if total else 0.0
    return UnstableReport(str(p), str(d.get("task_name", p.parent.parent.parent.name)), str(d.get("eval_seed", "")),
                          s, f, u, len(d.get("abandoned_layout_ids") or []), rate, rate > threshold)


def _unstable_threshold(root: str | Path | None = None) -> float:
    try:
        from etude.core.config import threshold

        return float(threshold("scorer.unstable_rate_review", root))
    except (KeyError, FileNotFoundError):
        return 0.05


def find_manifests(eval_root: str | Path) -> list[Path]:
    """All ``_resume_*.json`` under an eval root."""
    return sorted(Path(eval_root).rglob("_resume_*.json"))


def repeated_runs(eval_root: str | Path) -> dict[str, list[str]]:
    """Seed dirs holding more than one timestamp run (design §3.5 同布局多次尝试取最好)."""
    out: dict[str, list[str]] = {}
    for res in Path(eval_root).rglob("_result.json"):
        seed_dir = res.parent.parent
        out.setdefault(str(seed_dir), []).append(res.parent.name)
    return {k: sorted(v) for k, v in out.items() if len(v) > 1}


@dataclass
class AuditRecord:
    """One P5 audit (design §3.5); stored as ``scorer/audit/<name>.json``."""

    name: str
    created_at: str
    env: dict[str, Any]
    memory_tree_sha256: str | None = None
    memory_unchanged: bool | None = None
    unstable: list[dict[str, Any]] = field(default_factory=list)
    repeated_runs: dict[str, list[str]] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit(name: str, eval_root: str | Path | None = None, memory_dir: str | Path | None = None,
          memory_before: str | None = None, robodojo: str | Path = DEFAULT_ROBODOJO,
          harness_root: str | Path = REPO_ROOT, expect: Mapping[str, str] | None = None) -> AuditRecord:
    """Assemble an :class:`AuditRecord` (design §3.5).

    ``memory_before``: tree hash taken before the evaluation window (T: must be
    unchanged). ``expect``: pinned hashes/SHAs (e.g. the incumbent receipt's
    ``env``); any mismatch becomes a flag.
    """
    rec = AuditRecord(name, datetime.now(timezone.utc).isoformat(timespec="seconds"), env_shas(robodojo, harness_root))
    if memory_dir is not None:
        rec.memory_tree_sha256 = tree_hash(memory_dir)
        if memory_before is not None:
            rec.memory_unchanged = rec.memory_tree_sha256 == memory_before
            if not rec.memory_unchanged:
                rec.flags.append("memory_db changed during a frozen-memory evaluation window")
    if eval_root is not None:
        for m in find_manifests(eval_root):
            r = unstable_report(m)
            rec.unstable.append(asdict(r))
            if r.review:
                rec.flags.append(f"unstable rate {r.rate:.3f} > review threshold in {m}")
        rec.repeated_runs = repeated_runs(eval_root)
        for k, v in rec.repeated_runs.items():
            rec.flags.append(f"{len(v)} timestamp runs in {k} (only the latest may count; check resume, not best-of-N)")
    for k, v in (expect or {}).items():
        if rec.env.get(k) != v:
            rec.flags.append(f"env.{k} = {rec.env.get(k)} != pinned {v}")
    return rec


def write_audit(rec: AuditRecord, out_dir: str | Path = AUDIT_DIR) -> Path:
    """Write ``<out_dir>/<name>.json``."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{rec.name}.json"
    p.write_text(json.dumps(rec.to_dict(), indent=1) + "\n", encoding="utf-8")
    return p


def _demo() -> int:
    with tempfile.TemporaryDirectory(prefix="etude-audit-") as td:
        root = Path(td) / "eval_result" / "RoboDojo"
        seed_dir = root / "make_kong" / "Etude" / "arx_x5" / "0_ckpt_name=demo,action_type=joint"
        for ts in ("2026-10-11_10-00-00", "2026-10-11_12-00-00"):
            (seed_dir / ts).mkdir(parents=True)
            (seed_dir / ts / "_result.json").write_text(json.dumps({"details": {}}))
        (seed_dir / "_resume_2026-10-11_12-00-00.json").write_text(json.dumps(
            {"task_name": "make_kong", "eval_seed": 0, "success_nums": 12, "fail_nums": 34, "unstable_nums": 4,
             "abandoned_layout_ids": []}))
        mem = Path(td) / "memory_db"
        (mem / "stack_bowls").mkdir(parents=True)
        (mem / "stack_bowls" / "sequences.jsonl").write_text('{"seq_id": "s1"}\n')
        before = tree_hash(mem)
        (mem / "stack_bowls" / "sequences.jsonl").write_text('{"seq_id": "s1"}\n{"seq_id": "s2"}\n')  # write during T
        rec = audit("demo", root, mem, before)
        p = write_audit(rec, Path(td) / "audit")
        d = json.loads(p.read_text())
        print("env:", {k: (v[:12] if isinstance(v, str) else v) for k, v in d["env"].items()})
        print("unstable:", [(u["task"], round(u["rate"], 3), u["review"]) for u in d["unstable"]])
        print("flags:")
        for f in d["flags"]:
            print("  -", f)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI (design §2 P5, §3.5)."""
    ap = argparse.ArgumentParser(prog="python -m etude.scorer.audit")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--name", default=None)
    ap.add_argument("--eval-root", default=None)
    ap.add_argument("--memory", default=None)
    ap.add_argument("--memory-before", default=None, help="tree hash recorded before the window")
    ap.add_argument("--robodojo", default=str(DEFAULT_ROBODOJO))
    ap.add_argument("--out-dir", default=str(AUDIT_DIR))
    ap.add_argument("--hash-memory", default=None, help="print tree hash of a dir and exit")
    a = ap.parse_args(argv)
    if a.demo:
        return _demo()
    if a.hash_memory:
        print(tree_hash(a.hash_memory))
        return 0
    name = a.name or datetime.now(timezone.utc).strftime("audit-%Y%m%dT%H%M%SZ")
    rec = audit(name, a.eval_root, a.memory, a.memory_before, a.robodojo)
    p = write_audit(rec, a.out_dir)
    print(p)
    for f in rec.flags:
        print("FLAG:", f)
    return 1 if rec.flags else 0


if __name__ == "__main__":
    sys.exit(main())
