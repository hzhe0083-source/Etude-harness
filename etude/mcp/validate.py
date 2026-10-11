"""``submit_sequence`` gate: node schema + numeric-target rejection + leakage
regexes (design §4 "submit_sequence: schema + 泄漏检查，失败即阻断", §3.5 rows
"写死坐标 / seed / 任务名" and "读 scene_manager…").

Pipeline (:func:`check_sequence`):

1. parse: a list of node dicts, a JSON string of one, or ``{"nodes": [...]}``;
2. normalise: fill keys the planner omitted from ``evolvable/rules/recovery.yaml``
   ``defaults`` (``stop.*``, ``on_fail.*``) and empty ``instruction``/``args``/
   ``postcondition``/``evidence`` — never overwrite a given value;
3. ``budget.max_nodes`` (frozen) cap;
4. :func:`etude.core.schema.validate_nodes` (JSON Schema + digits in perceive query);
5. ``frozen/forbidden.yaml`` ``leakage_regex`` on every free-text field
   (instruction, perceive query, postcondition) and ``deny_names`` on every
   string in the submission.

Every error is a human-readable string the planner can act on.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from etude.core.config import deep_get, load_forbidden, load_recovery_rules, load_thresholds
from etude.core.schema import parse_perceive, validate_nodes
from etude.core.types import Node

#: Node keys accepted from the planner (design §4 node schema).
NODE_KEYS = ("id", "executor", "instruction", "args", "stop", "postcondition", "on_fail", "evidence")


def parse_nodes_arg(raw: Any) -> tuple[list[Any] | None, list[str]]:
    """Accept ``list[dict]``, a JSON string, or ``{"nodes": [...]}`` (design §4
    ``submit_sequence(nodes[])``). Returns ``(nodes, errors)``."""
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, [f"nodes: not valid JSON ({e.msg} at pos {e.pos})"]
    if isinstance(raw, Mapping) and "nodes" in raw and len(raw) == 1:
        raw = raw["nodes"]
    if not isinstance(raw, list):
        return None, [f"nodes: expected a list of node objects, got {type(raw).__name__}"]
    bad = [i for i, n in enumerate(raw) if not isinstance(n, Mapping)]
    if bad:
        return None, [f"nodes[{i}]: expected an object" for i in bad]
    return list(raw), []


def normalize_node(d: Mapping[str, Any], defaults: Mapping[str, Any]) -> dict[str, Any]:
    """Fill omitted optional parts from recovery defaults (design §3.1 surface 2).

    Unknown keys are kept so that the schema reports them (e.g. ``done``)."""
    out = copy.deepcopy(dict(d))
    out.setdefault("instruction", "")
    out.setdefault("args", {})
    out.setdefault("postcondition", [])
    out.setdefault("evidence", [])
    stop = out.get("stop")
    if stop is None:
        stop = {}
    if isinstance(stop, dict):
        for k in ("stall_chunks", "max_chunks", "exec_steps"):
            if k not in stop and k in defaults:
                stop[k] = defaults[k]
        out["stop"] = stop
    on_fail = out.get("on_fail")
    if on_fail is None:
        on_fail = {}
    if isinstance(on_fail, dict):
        if "retry" not in on_fail and "retry" in defaults:
            on_fail["retry"] = defaults["retry"]
        if "then" not in on_fail and "then" in defaults:
            on_fail["then"] = defaults["then"]
        out["on_fail"] = on_fail
    return out


def _strings(x: Any) -> Iterable[str]:
    if isinstance(x, str):
        yield x
    elif isinstance(x, Mapping):
        for k, v in x.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _strings(v)


def _free_text_fields(d: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(field, text) pairs that the leakage regexes apply to."""
    out: list[tuple[str, str]] = []
    if isinstance(d.get("instruction"), str):
        out.append(("instruction", d["instruction"]))
    args = d.get("args") if isinstance(d.get("args"), Mapping) else {}
    tgt = args.get("target")
    if isinstance(tgt, str):
        q = parse_perceive(tgt)
        out.append(("args.target", q if q is not None else tgt))
    for i, p in enumerate(d.get("postcondition") or []):
        if isinstance(p, str):
            out.append((f"postcondition[{i}]", p))
    return out


def leakage_errors(nodes: list[Mapping[str, Any]], forbidden: Mapping[str, Any], root: str | Path | None = None) -> list[str]:
    """Leakage-regex and deny-name hits (design §3.5).

    Regexes and the allowlist (e.g. ``target_lifted(>0.05m)``) come from
    ``frozen/forbidden.yaml`` through the frozen
    :class:`etude.frozen.leakage.LeakageScanner`; they are applied to the
    instruction, the perceive query and each postcondition."""
    from etude.frozen.leakage import LeakageScanner

    scanner = LeakageScanner.from_forbidden(root)
    errs: list[str] = []
    # Only identifier-like names (with "_") are matched in free text; plain
    # English words such as "success" stay legal in instructions.
    deny = [str(n) for n in (forbidden.get("deny_names") or []) if "_" in str(n)]
    deny += [str(c) for c in (forbidden.get("deny_attr_chains") or [])]
    deny_re = re.compile(r"(?i)\b(" + "|".join(re.escape(n) for n in deny) + r")\b") if deny else None
    for d in nodes:
        nid = d.get("id", "?")
        for fld, text in _free_text_fields(d):
            for hit in scanner.scan(text, node_field=True):
                errs.append(
                    f"node {nid}: {fld}: leakage ({hit.kind}) {hit.match!r} — describe objects visually, "
                    "never with task names, layout/seed ids, coordinates or decimal numbers"
                )
        if deny_re is not None:
            for s in _strings(d):
                m = deny_re.search(s)
                if m:
                    errs.append(f"node {nid}: forbidden name {m.group(0)!r} (privileged simulator state)")
                    break
    return errs


def check_sequence(
    raw: Any,
    *,
    root: str | Path | None = None,
    max_nodes: int | None = None,
) -> tuple[list[Node] | None, list[str]]:
    """Full ``submit_sequence`` check (design §4). Returns ``(nodes, [])`` when
    valid, else ``(None, errors)``."""
    nodes_raw, errs = parse_nodes_arg(raw)
    if nodes_raw is None:
        return None, errs
    rules = load_recovery_rules(root)
    defaults = deep_get(rules, "defaults", {}) or {}
    norm = [normalize_node(n, defaults) for n in nodes_raw]
    cap = int(max_nodes if max_nodes is not None else load_thresholds(root).get("budget.max_nodes", 8))
    if len(norm) > cap:
        errs.append(f"sequence: {len(norm)} nodes exceeds budget.max_nodes={cap}")
    errs.extend(validate_nodes(norm))
    errs.extend(leakage_errors(norm, load_forbidden(root), root))
    if errs:
        return None, errs
    return [Node.from_dict(n) for n in norm], []


__all__ = ["NODE_KEYS", "parse_nodes_arg", "normalize_node", "leakage_errors", "check_sequence"]
