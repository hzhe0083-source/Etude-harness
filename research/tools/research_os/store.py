"""Atomic, size-bounded current-state ledger; Git stores its history."""

import fcntl
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import ResearchError

LEDGER_FILE = "ledger.json"
HANDOFF_DIR = Path("WORKSTATE") / "handoff"
MAX_LEDGER_BYTES = 5 * 1024 * 1024
MAX_RECORD_BYTES = 32 * 1024
CHUNK = 1024 * 1024

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_last_ulid = [0]


# ---------------------------------------------------------------- ULID / time

def _encode_ulid(value):
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def _decode_ulid(text):
    value = 0
    for char in text:
        value = (value << 5) | _CROCKFORD.index(char)
    return value


def new_ulid(after=None):
    """48-bit ms time + 80 random bits; strictly greater than `after` and the last id of this process."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    floor = max(_last_ulid[0], _decode_ulid(after) if after else 0)
    if value <= floor:
        value = floor + 1
    _last_ulid[0] = value
    return _encode_ulid(value)


def now_ts():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(text):
    """Parse an RFC 3339 timestamp (Python 3.10 fromisoformat lacks 'Z'); naive times are rejected."""
    parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    if parsed.tzinfo is None:
        raise ResearchError(f"timestamp {text!r} must carry a timezone")
    return parsed


# ---------------------------------------------------------------- root / lock

def find_root(explicit=None):
    chosen = explicit or os.environ.get("RESEARCH_OS_ROOT")
    candidates = [Path(chosen)] if chosen else [d / "research" for d in [Path.cwd(), *Path.cwd().parents]]
    for root in candidates:
        if (root / LEDGER_FILE).is_file():
            return root.resolve()
    raise ResearchError("no research/ledger.json found; pass --root or run research init")


def init_layout(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / HANDOFF_DIR).mkdir(parents=True, exist_ok=True)
    with locked(root):
        if not (root / LEDGER_FILE).exists():
            from .state import State
            save_state(root, State())
    return root


@contextmanager
def locked(root):
    with open(Path(root) / ".lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def encode(data):
    return (json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()


def state_data(state):
    from dataclasses import asdict
    data = asdict(state)
    data["consumed"] = sorted(state.consumed)
    return dict(format=1, **data)


def size_errors(data):
    errors = []
    size = len(encode(data))
    if size > MAX_LEDGER_BYTES:
        errors.append(f"ledger exceeds {MAX_LEDGER_BYTES} bytes ({size}); move detailed records to HF")
    for ref, record in {**data["records"], **data["events"]}.items():
        if len(encode(record)) > MAX_RECORD_BYTES:
            errors.append(f"{ref} exceeds {MAX_RECORD_BYTES} bytes; keep only core results")
    return errors


def compact_authority(state):
    """Keep only current requests, usable grants and authorizations cited by current records."""
    def grants(value):
        if isinstance(value, dict):
            if value.get("mode") == "explicit":
                yield value["grant"]
            for child in value.values():
                yield from grants(child)
        elif isinstance(value, list):
            for child in value:
                yield from grants(child)
    keep = set(grants(state.records))
    for request, answer in state.answers.items():
        event = state.events[request]
        current = state.records.get(event["subject"], {}).get("version")
        if event["payload"]["subject_version"] != current:
            continue
        if answer is None:
            keep.add(request)
        elif state.events[answer]["type"] == "authority_granted" and (
                answer not in state.consumed or event["payload"]["scope"] == "experiment_contract"):
            keep.update((request, answer))
    for ident in list(keep):
        event = state.events.get(ident)
        if event and event["type"] == "authority_granted":
            keep.add(event["payload"]["request"])
    state.events = {k:v for k,v in state.events.items() if k in keep}
    state.answers = {k:v for k,v in state.answers.items() if k in keep}
    state.consumed.intersection_update(keep)


def save_state(root, state):
    compact_authority(state)
    data = state_data(state)
    from .validate import state_errors
    errors = state_errors(state)
    if errors:
        raise ResearchError("; ".join(errors))
    atomic_write(Path(root) / LEDGER_FILE, encode(data))


def load_state(root):
    from .state import State
    data = json.loads((Path(root) / LEDGER_FILE).read_text())
    from .schema import _errors
    errors = _errors("#/$defs/Ledger", data)
    if errors:
        raise ResearchError("; ".join(errors))
    if data.pop("format") != 1:
        raise ResearchError("unsupported ledger format")
    data["consumed"] = set(data["consumed"])
    return State(**data)


def atomic_write(path, data):
    path = Path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()
