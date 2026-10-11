"""Validation against research/SCHEMA.json (JSON Schema 2020-12)."""

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "SCHEMA.json"


@lru_cache(maxsize=None)
def _validator(ref):
    """Validator for one definition; the whole document stays the root so internal $refs resolve."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["$ref"] = ref
    return Draft202012Validator(schema)


def _messages(error, depth=0):
    """Flatten an error; for a oneOf over event types report only the branch whose `type` matched."""
    location = error.json_path
    if error.validator != "oneOf" or not error.context or depth > 3:
        return [f"schema: {location}: {error.message}"]
    branches = {}
    for sub in error.context:
        branches.setdefault(sub.schema_path[0], []).append(sub)
    matched = [errs for errs in branches.values() if not any(list(e.path) == ["type"] for e in errs)]
    if len(matched) != 1:
        return [f"schema: {location}: {error.message}"]
    return [msg for sub in matched[0] for msg in _messages(sub, depth + 1)]


def _errors(ref, instance):
    found = sorted(_validator(ref).iter_errors(instance), key=lambda e: list(e.path))
    return [msg for error in found for msg in _messages(error)]


def event_errors(event):
    return _errors("#/$defs/Event", event)


def view_addition_errors(add):
    return _errors("#/$defs/P_view_extended/properties/add", add)


def handoff_errors(handoff):
    return _errors("#/$defs/Handoff", handoff)
