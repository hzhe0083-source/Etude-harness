"""JSON-schema validation for nodes, verdicts and receipts (design §4, §3.5).

Schemas live in ``frozen/schema/{node,verdict,receipt}.json`` and are frozen.
On top of JSON Schema this module enforces the two semantic rules the schema
language cannot express cleanly:

* a ``perceive("…")`` query must not contain digits (design §3.5 "schema 拒数值
  target": no coordinates, layout numbers or seeds);
* node ids in a sequence are unique, and a verdict's ``t_chunk`` is ordered.

Validators return a list of human-readable error strings (empty = valid); the
``assert_*`` variants raise :class:`SchemaError`.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

import jsonschema

from etude import FROZEN_DIR

SCHEMA_DIR: Path = FROZEN_DIR / "schema"

_PERCEIVE_RE = re.compile(r'^perceive\("(.+)"\)$', re.S)
_PRED_RE = re.compile(r"^([a-z_]+)(?:\((.*)\))?$", re.S)


class SchemaError(ValueError):
    """Raised by ``assert_valid_*``; ``.errors`` holds the error strings."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@functools.lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    """Load ``frozen/schema/<name>.json`` (name ∈ node, verdict, receipt)."""
    with open(SCHEMA_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


@functools.lru_cache(maxsize=None)
def _validator(name: str) -> jsonschema.protocols.Validator:
    schema = load_schema(name)
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def _as_dict(obj: Any) -> Any:
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


def _schema_errors(name: str, obj: Any, prefix: str = "") -> list[str]:
    errs = []
    for e in sorted(_validator(name).iter_errors(_as_dict(obj)), key=lambda e: list(e.absolute_path)):
        path = "/".join(str(p) for p in e.absolute_path)
        errs.append(f"{prefix}{path or '<root>'}: {e.message}")
    return errs


# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------


def parse_perceive(target: str | None) -> str | None:
    """Return the query inside ``perceive("…")`` or None if not that form (design §4)."""
    if not isinstance(target, str):
        return None
    m = _PERCEIVE_RE.match(target.strip())
    return m.group(1) if m else None


def make_perceive(query: str) -> str:
    """Build a ``perceive("…")`` target string from a text query (design §4)."""
    return f'perceive("{query}")'


def parse_predicate(expr: str) -> tuple[str, list[str]]:
    """Split a postcondition string into ``(name, args)`` (design §4 谓词表).

    Examples::

        "gripper_holding"            -> ("gripper_holding", [])
        "gripper_holding(left)"      -> ("gripper_holding", ["left"])
        "target_lifted(>0.05m)"      -> ("target_lifted", [">0.05m"])
        'target_in("bottle","bin")'  -> ("target_in", ["bottle", "bin"])
        "stacked([bowl a,bowl b])"   -> ("stacked", ["bowl a", "bowl b"])

    Quotes around args are stripped. Raises ValueError on malformed input or
    unknown predicate names.
    """
    from etude.core.types import PREDICATE_NAMES

    m = _PRED_RE.match(expr.strip())
    if not m:
        raise ValueError(f"malformed predicate: {expr!r}")
    name, inner = m.group(1), m.group(2)
    if name not in PREDICATE_NAMES:
        raise ValueError(f"unknown predicate: {name!r}")
    if inner is None or inner.strip() == "":
        return name, []
    inner = inner.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    args = [a.strip().strip("\"'").strip() for a in inner.split(",")]
    return name, [a for a in args if a]


# ---------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------


def validate_node(node: Any) -> list[str]:
    """Validate one node (dict or :class:`~etude.core.types.Node`) against
    ``frozen/schema/node.json`` plus the no-digits-in-target rule (design §4, §3.5)."""
    d = _as_dict(node)
    nid = d.get("id", "?") if isinstance(d, Mapping) else "?"
    errs = _schema_errors("node", d, prefix=f"node {nid}: ")
    if isinstance(d, Mapping):
        target = (d.get("args") or {}).get("target") if isinstance(d.get("args"), Mapping) else None
        q = parse_perceive(target) if target is not None else None
        if q is not None and re.search(r"\d", q):
            errs.append(f"node {nid}: args/target: numeric content not allowed in perceive query: {q!r}")
    return errs


def validate_nodes(nodes: Iterable[Any]) -> list[str]:
    """Validate a node sequence: each node plus unique ids and non-empty (design §4)."""
    nodes = list(nodes)
    errs: list[str] = []
    if not nodes:
        errs.append("sequence: empty")
    seen: set[str] = set()
    for n in nodes:
        errs.extend(validate_node(n))
        nid = _as_dict(n).get("id") if isinstance(_as_dict(n), Mapping) else None
        if nid in seen:
            errs.append(f"sequence: duplicate node id {nid!r}")
        if nid is not None:
            seen.add(nid)
    return errs


def validate_verdict(verdict: Any) -> list[str]:
    """Validate a verdict against ``frozen/schema/verdict.json`` (design §4)."""
    d = _as_dict(verdict)
    errs = _schema_errors("verdict", d, prefix="verdict: ")
    if isinstance(d, Mapping):
        tc = d.get("t_chunk")
        if isinstance(tc, list) and len(tc) == 2 and all(isinstance(x, int) for x in tc) and tc[0] > tc[1]:
            errs.append("verdict: t_chunk must be [first, last] with first <= last")
    return errs


def validate_receipt(receipt: Any) -> list[str]:
    """Validate a lineage receipt against ``frozen/schema/receipt.json`` (design §4)."""
    return _schema_errors("receipt", receipt, prefix="receipt: ")


def assert_valid_node(node: Any) -> None:
    """Raise :class:`SchemaError` if :func:`validate_node` reports errors."""
    errs = validate_node(node)
    if errs:
        raise SchemaError(errs)


def assert_valid_nodes(nodes: Iterable[Any]) -> None:
    """Raise :class:`SchemaError` if :func:`validate_nodes` reports errors."""
    errs = validate_nodes(nodes)
    if errs:
        raise SchemaError(errs)


def assert_valid_verdict(verdict: Any) -> None:
    """Raise :class:`SchemaError` if :func:`validate_verdict` reports errors."""
    errs = validate_verdict(verdict)
    if errs:
        raise SchemaError(errs)


def assert_valid_receipt(receipt: Any) -> None:
    """Raise :class:`SchemaError` if :func:`validate_receipt` reports errors."""
    errs = validate_receipt(receipt)
    if errs:
        raise SchemaError(errs)


__all__ = [
    "SCHEMA_DIR", "SchemaError", "load_schema", "parse_perceive", "make_perceive", "parse_predicate",
    "validate_node", "validate_nodes", "validate_verdict", "validate_receipt",
    "assert_valid_node", "assert_valid_nodes", "assert_valid_verdict", "assert_valid_receipt",
]
