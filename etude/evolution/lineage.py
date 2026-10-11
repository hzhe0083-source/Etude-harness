"""Lineage receipts (design §3.1 回滚 row, §4 lineage 收据).

One :class:`etude.core.types.Receipt` per evolution round, written as
``<lineage dir>/<receipt_id>.json`` (default outside the repo, see
:func:`default_lineage_dir`; design §4 tree says ``evolution/lineage/``, moved
so P6 cannot read V results) and validated against
``frozen/schema/receipt.json``. Fields are exactly the §4 JSON plus
``research_run``: the Research OS Run label/ULID (``research/ledger.json``)
whose committed, pushed source produced the V numbers. Agents never create
that Run's authorisation; :func:`link_research_run` fills the field once the
user has created/run it.

P6 (the editor) only ever receives :func:`editor_view` — accept/reject, one
bit (design §3.2: "P6 只收 accept/reject 一位").
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from etude import REPO_ROOT
from etude.core.schema import assert_valid_receipt
from etude.core.types import GateResults, Receipt

def default_lineage_dir() -> Path:
    """Where receipts and :class:`EvolutionState` live: ``$ETUDE_LINEAGE_DIR``, else
    ``~/.local/state/etude/<repo name>/lineage``. Outside the repository on purpose:
    receipts carry L3 ``{b, c, eps, p}``, costs and V labels, and the state holds the
    incumbent's per-layout V outcomes. P6 may see only :func:`editor_view` (design §3.2),
    and a directory in the repo would be committed and present in P6's worktree."""
    env = os.environ.get("ETUDE_LINEAGE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".local" / "state" / "etude" / REPO_ROOT.name / "lineage"


LINEAGE_DIR: Path = default_lineage_dir()
_RID = re.compile(r"^r(\d{4,})\.json$")


def diff_sha(diff: str) -> str:
    """sha256 of the diff with trailing whitespace per line normalised (receipt ``diff_sha``)."""
    norm = "\n".join(ln.rstrip() for ln in diff.strip().splitlines())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def next_receipt_id(lineage_dir: str | Path = LINEAGE_DIR) -> str:
    """Next free ``rNNNN`` id in ``lineage_dir``."""
    d = Path(lineage_dir)
    nums = [int(m.group(1)) for p in (d.iterdir() if d.is_dir() else []) if (m := _RID.match(p.name))]
    return f"r{(max(nums) + 1) if nums else 1:04d}"


def make_receipt(receipt_id: str, parent: str, candidate: str, surface: str, diff: str, hypothesis: str,
                 gates: GateResults, cost: Mapping[str, list[float]] | None = None, decision: str = "reject",
                 evaluated_on: Mapping[str, Any] | None = None, env: Mapping[str, str] | None = None,
                 rollback: str = "", research_run: str | None = None) -> Receipt:
    """Assemble a §4 receipt (``diff_sha`` computed from ``diff``)."""
    return Receipt(receipt_id=receipt_id, parent=parent, candidate=candidate, surface=surface,
                   diff_sha=diff_sha(diff), hypothesis=hypothesis, gates=gates, cost=dict(cost or {}),
                   decision=decision, evaluated_on=dict(evaluated_on or {}),
                   env={k: str(v) for k, v in (env or {}).items() if v is not None},
                   rollback=rollback, research_run=research_run)


def write_receipt(receipt: Receipt, lineage_dir: str | Path = LINEAGE_DIR, overwrite: bool = False) -> Path:
    """Validate against ``frozen/schema/receipt.json`` and write ``<dir>/<receipt_id>.json``."""
    d = receipt.to_dict()
    assert_valid_receipt(d)
    out = Path(lineage_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{receipt.receipt_id}.json"
    if p.exists() and not overwrite:
        raise FileExistsError(f"receipt {p} exists (receipts are append-only)")
    p.write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def load_receipt(path: str | Path) -> Receipt:
    """Read one receipt JSON."""
    return Receipt.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def load_receipts(lineage_dir: str | Path = LINEAGE_DIR) -> list[Receipt]:
    """All receipts in id order."""
    d = Path(lineage_dir)
    files = sorted((p for p in d.iterdir() if _RID.match(p.name)), key=lambda p: int(_RID.match(p.name).group(1))) if d.is_dir() else []
    return [load_receipt(p) for p in files]


def link_research_run(receipt_id: str, run_label: str, lineage_dir: str | Path = LINEAGE_DIR) -> Path:
    """Fill ``research_run`` of an existing receipt (the only post-hoc edit allowed)."""
    p = Path(lineage_dir) / f"{receipt_id}.json"
    r = load_receipt(p)
    if r.research_run not in (None, run_label):
        raise ValueError(f"{receipt_id} already linked to {r.research_run}")
    r.research_run = run_label
    return write_receipt(r, lineage_dir, overwrite=True)


def editor_view(receipt: Receipt) -> dict[str, str]:
    """What P6 may see about a round: id + accept/reject (design §3.2)."""
    return {"receipt_id": receipt.receipt_id, "decision": receipt.decision}


__all__ = ["LINEAGE_DIR", "default_lineage_dir", "diff_sha", "editor_view", "link_research_run", "load_receipt", "load_receipts",
           "make_receipt", "next_receipt_id", "write_receipt"]
