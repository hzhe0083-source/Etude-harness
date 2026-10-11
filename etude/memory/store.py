"""Append-only node-sequence memory ``memory_db/<task>/sequences.jsonl``
(design §2 记忆库 row, §3.1 记忆回路, §3.5 "评测期写记忆" row, §4 tree and
MCP ``memory_lookup(task, instruction) → [sequence]`` top-3).

File format — one JSON object per line, never rewritten (append-only, like the
Research OS event log borrowed from ``research/tools/research_os/store.py``):

* ``{"op": "add", "seq_id", "task", "instruction", "instruction_hash",
  "nodes": [...], "evidence_refs": [...], "official_pass": true,
  "created_from": {"episode_id", "seed", "layout", "research_run"},
  "lineage", "created_at", "actor", "supersedes": <seq_id>|null}``
* ``{"op": "retract", "seq_id", "reason", "created_at", "actor"}`` — the
  rollback "按 seq_id 删行" of design §3.1 expressed as a tombstone so the file
  stays append-only and the raw history (and traces) is kept.

Dedupe (design §3.1 提案): one live entry per ``(task, instruction_hash)``;
a new entry only lands if it is strictly shorter, and its ``supersedes``
field retires the previous one.

Provenance (``created_from`` incl. seed/layout) stays in the file for audit;
:meth:`MemoryDB.lookup` returns :class:`SequenceEntry` objects, which carry no
seed or layout, so P1 never sees layout ids through ``memory_lookup``.

Frozen mode (evaluation period, design §3.2 "评测期（T）记忆冻结"): ``append``
and ``retract`` raise :class:`MemoryFrozenError`; the directory digest recorded
by :meth:`MemoryDB.freeze` is re-checked when a frozen store opens
(:class:`MemoryTampered` on mismatch). Frozen is on if ``frozen=True``, if the
env var ``ETUDE_MEMORY_FROZEN`` is truthy, or if ``<root>/FROZEN.json`` exists.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from etude import MEMORY_DB_DIR
from etude.core.interfaces import MemoryStore
from etude.core.types import SequenceEntry, instruction_hash
from etude.memory.sanitize import NumericLeakError, numeric_errors

SEQUENCES_FILE = "sequences.jsonl"
FROZEN_MARKER = "FROZEN.json"
FROZEN_ENV = "ETUDE_MEMORY_FROZEN"
DEFAULT_TOP_K = 3
#: Per-line cap (borrowed from Research OS MAX_RECORD_BYTES: keep records small).
MAX_LINE_BYTES = 64 * 1024
_TASK_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HASH_EXCLUDE = {FROZEN_MARKER}
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_WORD_RE = re.compile(r"[a-z]+")


class MemoryFrozenError(RuntimeError):
    """Write attempted on a frozen memory store (design §3.5 "seed 2 期间 append" must fail)."""


class MemoryTampered(RuntimeError):
    """Frozen memory directory digest differs from the one recorded at freeze time (design §3.1 哈希审计)."""


# ---------------------------------------------------------------------------
# helpers (ULID / atomic write / hashing: same recipes as research_os/store.py)
# ---------------------------------------------------------------------------


def new_seq_id() -> str:
    """``seq_`` + 26-char Crockford ULID (48-bit ms time + 80 random bits), sortable by time."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "seq_" + "".join(reversed(chars))


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _actor() -> str:
    return os.environ.get("ETUDE_ACTOR") or os.environ.get("RESEARCH_ACTOR") or "offline:memory_writer"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dir_digest(root: str | Path) -> tuple[str, dict[str, str]]:
    """Content digest of a memory directory (design §3.1 防作弊 "评测期只读（哈希审计）").

    Returns ``(digest, files)`` where ``files`` maps each relative path (POSIX,
    sorted; dotfiles, temp files and ``FROZEN.json`` excluded) to its sha256,
    and ``digest`` = sha256 over ``"<relpath>\\0<sha>\\n"`` lines.
    """
    root = Path(root)
    files: dict[str, str] = {}
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if not p.is_file() or any(part.startswith(".") for part in Path(rel).parts) or rel in _HASH_EXCLUDE:
                continue
            files[rel] = _sha256_file(p)
    h = hashlib.sha256()
    for rel, sha in files.items():
        h.update(f"{rel}\0{sha}\n".encode())
    return h.hexdigest(), files


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


@dataclass
class AuditResult:
    """Result of :meth:`MemoryDB.audit` (design §3.1 哈希审计)."""

    digest: str
    files: dict[str, str]
    expected: str | None = None
    frozen: bool = False

    @property
    def ok(self) -> bool:
        return self.expected is None or self.expected == self.digest

    def to_dict(self) -> dict[str, Any]:
        return {"digest": self.digest, "expected": self.expected, "ok": self.ok, "frozen": self.frozen, "files": dict(self.files)}


@dataclass
class StoredSequence:
    """A live ``op: add`` line plus its file-only fields (design §3.1 row 记忆).

    ``entry`` is what P1 may see; ``official_pass`` / ``created_from`` /
    ``actor`` / ``supersedes`` stay offline (provenance and audit).
    """

    entry: SequenceEntry
    official_pass: bool = True
    created_from: dict[str, Any] = field(default_factory=dict)
    actor: str = ""
    supersedes: str | None = None

    def to_line(self) -> dict[str, Any]:
        d = {"op": "add", **self.entry.to_dict()}
        d.update(
            official_pass=bool(self.official_pass),
            created_from=dict(self.created_from),
            actor=self.actor,
            supersedes=self.supersedes,
        )
        return d

    @classmethod
    def from_line(cls, d: Mapping[str, Any]) -> "StoredSequence":
        return cls(
            entry=SequenceEntry.from_dict(d),
            official_pass=bool(d.get("official_pass", False)),
            created_from=dict(d.get("created_from") or {}),
            actor=str(d.get("actor", "")),
            supersedes=d.get("supersedes"),
        )


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


class MemoryDB(MemoryStore):
    """JSONL node-sequence store under ``root`` (default ``memory_db/``) (design §3.1).

    ``frozen``: None = auto (env var / marker file), True/False = forced.
    ``config_root``: repo root for ``frozen/forbidden.yaml`` leakage regexes.
    """

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        frozen: bool | None = None,
        config_root: str | Path | None = None,
        verify_on_open: bool = True,
    ):
        self.root = Path(root) if root is not None else MEMORY_DB_DIR
        self.config_root = config_root
        env_frozen = os.environ.get(FROZEN_ENV, "").strip().lower() in ("1", "true", "yes", "on")
        self._forced = frozen
        self.frozen = bool(frozen) if frozen is not None else (env_frozen or self.marker_path.exists())
        if self.frozen and verify_on_open:
            self.verify()

    # -- paths ---------------------------------------------------------------
    @property
    def marker_path(self) -> Path:
        return self.root / FROZEN_MARKER

    def task_file(self, task: str) -> Path:
        """``<root>/<task>/sequences.jsonl``; task names are validated (no path traversal)."""
        if not _TASK_RE.match(task or ""):
            raise ValueError(f"invalid task name {task!r}")
        return self.root / task / SEQUENCES_FILE

    def tasks(self) -> list[str]:
        """Tasks that have a sequences file."""
        if not self.root.is_dir():
            return []
        return sorted(p.parent.name for p in self.root.glob(f"*/{SEQUENCES_FILE}") if _TASK_RE.match(p.parent.name))

    # -- raw log -------------------------------------------------------------
    def iter_lines(self, task: str) -> Iterator[dict[str, Any]]:
        """Every JSON line of the task file in append order (add and retract ops)."""
        path = self.task_file(task)
        if not path.exists():
            return
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"{path}:{i}: corrupt JSONL line ({e})") from e
                d.setdefault("op", "add")
                yield d

    def live(self, task: str) -> list[StoredSequence]:
        """Live entries of ``task``: adds minus retracted and superseded, in append order."""
        adds: dict[str, StoredSequence] = {}
        dead: set[str] = set()
        for d in self.iter_lines(task):
            if d["op"] == "retract":
                dead.add(str(d.get("seq_id")))
            elif d["op"] == "add":
                s = StoredSequence.from_line(d)
                adds[s.entry.seq_id] = s
                if s.supersedes:
                    dead.add(str(s.supersedes))
        return [s for sid, s in adds.items() if sid not in dead]

    def _append_line(self, task: str, line: dict[str, Any]) -> None:
        if self.frozen:
            raise MemoryFrozenError(f"memory store {self.root} is frozen (evaluation period); refusing {line.get('op')}")
        data = (json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if len(data) > MAX_LINE_BYTES:
            raise ValueError(f"memory line exceeds {MAX_LINE_BYTES} bytes")
        path = self.task_file(task)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    @contextlib.contextmanager
    def _task_lock(self, task: str) -> Iterator[None]:
        lock = self.task_file(task).parent / ".lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # -- MemoryStore API -----------------------------------------------------
    def lookup(self, task: str, instruction: str, k: int = DEFAULT_TOP_K) -> list[SequenceEntry]:
        """Top-``k`` (default 3, threshold ``budget.memory_top_k``) live sequences
        of ``task`` for ``instruction`` (design §4 ``memory_lookup``; read-only,
        allowed in frozen mode).

        Ranking: exact instruction hash first, then word-overlap (Jaccard) with
        the instruction, then fewer nodes, then newest. Returns deep copies.
        """
        k = max(0, min(int(k), DEFAULT_TOP_K))
        try:
            items = self.live(task)
        except ValueError:
            return []
        h = instruction_hash(instruction)
        q = _tokens(instruction)

        def score(s: StoredSequence) -> tuple[int, float, int, str]:
            e = s.entry
            t = _tokens(e.instruction)
            jac = len(q & t) / len(q | t) if (q | t) else 0.0
            return (0 if e.instruction_hash == h else 1, -jac, len(e.nodes), _neg(e.seq_id))

        items.sort(key=score)
        return [copy.deepcopy(s.entry) for s in items[:k]]

    def append(
        self,
        entry: SequenceEntry,
        *,
        official_pass: bool = True,
        created_from: Mapping[str, Any] | None = None,
    ) -> bool:
        """Append ``entry`` (design §3.1 门 + 提案): refuses if frozen
        (:class:`MemoryFrozenError`), if ``official_pass`` is not True, or if
        nodes carry numeric targets / leakage (:class:`NumericLeakError`).
        Dedupe on ``(task, instruction_hash)``: returns True if stored (new key,
        or strictly fewer nodes than the live entry, which it supersedes);
        False if an equal-or-shorter sequence is already live.
        """
        if self.frozen:
            raise MemoryFrozenError(f"memory store {self.root} is frozen (evaluation period); append refused")
        if official_pass is not True:
            raise ValueError("memory admits only officially passed episodes (design §3.1 门)")
        errs = numeric_errors(entry.nodes, config_root=self.config_root)
        if errs:
            raise NumericLeakError(errs)
        entry = copy.deepcopy(entry)
        expect_hash = instruction_hash(entry.instruction)
        if entry.instruction and entry.instruction_hash != expect_hash:
            if entry.instruction_hash:
                raise ValueError("instruction_hash does not match instruction")
            entry.instruction_hash = expect_hash
        if not entry.instruction_hash:
            raise ValueError("entry needs an instruction or instruction_hash")
        entry.seq_id = entry.seq_id or new_seq_id()
        entry.created_at = entry.created_at or utc_now()
        with self._task_lock(entry.task):
            supersedes = None
            for s in self.live(entry.task):
                if s.entry.seq_id == entry.seq_id:
                    raise ValueError(f"duplicate seq_id {entry.seq_id}")
                if s.entry.instruction_hash == entry.instruction_hash:
                    if len(entry.nodes) >= len(s.entry.nodes):
                        return False
                    supersedes = s.entry.seq_id
            stored = StoredSequence(
                entry=entry,
                official_pass=True,
                created_from=dict(created_from or {}),
                actor=_actor(),
                supersedes=supersedes,
            )
            self._append_line(entry.task, stored.to_line())
        return True

    def retract(self, task: str, seq_id: str, reason: str) -> None:
        """Rollback one entry by ``seq_id`` via an append-only tombstone (design §3.1 回滚)."""
        if self.frozen:
            raise MemoryFrozenError(f"memory store {self.root} is frozen (evaluation period); retract refused")
        if not reason.strip():
            raise ValueError("retract needs a reason (it goes into the lineage)")
        with self._task_lock(task):
            if seq_id not in {s.entry.seq_id for s in self.live(task)}:
                raise KeyError(f"{seq_id} is not a live entry of {task}")
            self._append_line(task, {"op": "retract", "seq_id": seq_id, "reason": reason, "created_at": utc_now(), "actor": _actor()})

    def get(self, task: str, seq_id: str) -> StoredSequence | None:
        """The live stored sequence with ``seq_id`` (incl. provenance), or None."""
        for s in self.live(task):
            if s.entry.seq_id == seq_id:
                return s
        return None

    # -- freeze / audit ------------------------------------------------------
    def audit(self) -> AuditResult:
        """Current directory digest vs the one in ``FROZEN.json`` (if any) (design §3.1 哈希审计)."""
        digest, files = dir_digest(self.root)
        expected = None
        if self.marker_path.exists():
            expected = json.loads(self.marker_path.read_text(encoding="utf-8")).get("digest")
        return AuditResult(digest=digest, files=files, expected=expected, frozen=self.frozen)

    def verify(self) -> AuditResult:
        """Raise :class:`MemoryTampered` if a recorded freeze digest no longer matches."""
        res = self.audit()
        if not res.ok:
            raise MemoryTampered(f"memory digest {res.digest} != frozen digest {res.expected} under {self.root}")
        return res

    def freeze(self, reason: str = "evaluation period") -> str:
        """Enter frozen mode and record the directory digest in ``FROZEN.json``
        (design §3.2 "评测期（T）记忆冻结"); returns the digest. Run it before the
        T split; the P2 server then opens the store frozen."""
        digest, files = dir_digest(self.root)
        marker = {"digest": digest, "files": files, "frozen_at": utc_now(), "reason": reason, "actor": _actor()}
        _atomic_write(self.marker_path, (json.dumps(marker, indent=2, sort_keys=True) + "\n").encode())
        self.frozen = True
        return digest

    def unfreeze(self) -> None:
        """Leave frozen mode (offline, after evaluation): verifies the digest, then removes the marker."""
        self.verify()
        self.marker_path.unlink(missing_ok=True)
        self.frozen = bool(self._forced) or os.environ.get(FROZEN_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _neg(seq_id: str) -> str:
    """Sort key making newer ULIDs sort first."""
    return "".join(chr(0x10FFFF - ord(c)) for c in seq_id)


__all__ = [
    "AuditResult", "FROZEN_ENV", "FROZEN_MARKER", "MemoryDB", "MemoryFrozenError", "MemoryTampered",
    "SEQUENCES_FILE", "StoredSequence", "dir_digest", "new_seq_id", "utc_now",
]
