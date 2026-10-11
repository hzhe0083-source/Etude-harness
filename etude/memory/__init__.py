"""Node-sequence memory store (read-only at eval) and offline writer (design §3.1 memory loop).

* :mod:`etude.memory.store` — :class:`MemoryDB`, append-only
  ``memory_db/<task>/sequences.jsonl``, top-3 lookup, frozen mode, digest audit.
* :mod:`etude.memory.writer` — :func:`write_if_passed` (official success ∧ all
  nodes Pass ∧ no numeric target), :func:`ingest_traces`.
* :mod:`etude.memory.sanitize` — :func:`strip_numeric` / leakage checks.

Demo: ``uv run python -m etude.memory --demo``.
"""
from etude.memory.sanitize import NumericLeakError, numeric_errors, strip_numeric
from etude.memory.store import (
    AuditResult,
    MemoryDB,
    MemoryFrozenError,
    MemoryTampered,
    StoredSequence,
    dir_digest,
)
from etude.memory.writer import WriteResult, ingest_traces, official_success, write_if_passed

__all__ = [
    "AuditResult", "MemoryDB", "MemoryFrozenError", "MemoryTampered", "NumericLeakError", "StoredSequence",
    "WriteResult", "dir_digest", "ingest_traces", "numeric_errors", "official_success", "strip_numeric",
    "write_if_passed",
]
