"""WORKSTATE/handoff/<HS-n>.json: resume state with zero epistemic weight (AGENTS.md R35-R37)."""

import json

from . import store
from .errors import ResearchError, RuleViolation
from .schema import handoff_errors
from .state import ALIAS_RE

FILLED = ("id", "alias", "created_by", "created_at")
ISSUED = "issued.txt"


def handoff_files(root):
    return sorted((root / store.HANDOFF_DIR).glob("HS-*.json"))


def load_all(root):
    return [json.loads(path.read_text(encoding="utf-8")) for path in handoff_files(root)]


def issued_ids(root):
    """Every handoff id ever saved; `clear` never shrinks this, so R36 still sees cleared handoffs."""
    path = root / store.HANDOFF_DIR / ISSUED
    return set(path.read_text(encoding="utf-8").split()) if path.is_file() else set()


def save(root, body, agent):
    """Fill id/alias/created_by/created_at, validate against $defs/Handoff and write the file."""
    given = sorted(key for key in FILLED if key in body)
    if given:
        raise ResearchError(f"handoff file must not set {', '.join(given)}; the CLI fills them")
    with store.locked(root):
        (root / store.HANDOFF_DIR).mkdir(parents=True, exist_ok=True)
        numbers = [int(ALIAS_RE.match(path.stem).group(2)) for path in handoff_files(root)]
        alias = f"HS-{max(numbers, default=0) + 1}"
        handoff = dict(body, id=store.new_ulid(), alias=alias, created_by=agent, created_at=store.now_ts())
        errors = handoff_errors(handoff)
        if errors:
            raise RuleViolation(errors)
        path = root / store.HANDOFF_DIR / f"{alias}.json"
        path.write_text(json.dumps(handoff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        with (root / store.HANDOFF_DIR / ISSUED).open("a", encoding="utf-8") as issued:
            issued.write(handoff["id"] + "\n")
    return handoff


def _live(handoff, now):
    return "expires_at" not in handoff or store.parse_ts(now) < store.parse_ts(handoff["expires_at"])


def latest(root, now, scope_ids=None):
    """Latest non-expired handoff; with scope_ids, only one whose scope names one of them."""
    found = [h for h in load_all(root) if _live(h, now)]
    if scope_ids is not None:
        found = [h for h in found if set(h["scope"].values()) & set(scope_ids)]
    return max(found, key=lambda h: h["id"], default=None)


def clear(root, alias=None):
    with store.locked(root):
        paths = [p for p in handoff_files(root) if alias is None or p.stem == alias]
        if alias is not None and not paths:
            raise ResearchError(f"no handoff {alias}")
        for path in paths:
            path.unlink()
    return [path.stem for path in paths]
