"""Numeric / leakage stripping for memory sequences (design §3.1 记忆行
"成功序列去数值后入库", §3.5 "写死坐标 / seed / 任务名" row).

Shared by :mod:`etude.memory.store` (append re-checks every entry) and
:mod:`etude.memory.writer` (the offline writer). "Strip" here means
*canonicalise and refuse*: a node whose target is not a ``perceive("…")``
string, whose perceive query contains a digit, or whose free text carries a
coordinate / float literal / layout or seed id / task name is rejected with
:class:`NumericLeakError`. Nothing numeric is silently rewritten, because a
silently rewritten sequence would no longer be the sequence that passed the
official judge.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from etude.core.schema import parse_perceive, validate_nodes
from etude.core.types import Node

#: Top-level node keys whose numeric leaves are legitimate budgets (design §4).
_NUMERIC_OK_KEYS = ("stop", "on_fail")
#: Fallback leakage regexes if ``frozen/forbidden.yaml`` cannot be read.
_FALLBACK_LEAKAGE = {
    "task_names": r"(?i)\b(stack_bowls|put_bottles_into_dustbin|build_tower|make_kong|fold_clothes)\b",
    "layout_ids": r"(?i)\b(layout|seed|episode|env_seed|eval_layout)[\s_#:=-]*\d+",
    "coordinates": r"\[\s*-?\d+(\.\d+)?\s*(,\s*-?\d+(\.\d+)?\s*){1,6}\]",
    "float_literals": r"(?<![\w.])-?\d+\.\d{2,}(?![\w.])",
}


class NumericLeakError(ValueError):
    """A sequence carries numeric targets or leakage (design §3.5); ``.errors`` lists why."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@functools.lru_cache(maxsize=8)
def leakage_patterns(config_root: str | None = None) -> dict[str, re.Pattern[str]]:
    """Compiled ``leakage_regex`` from ``frozen/forbidden.yaml`` (design §3.5);
    only used when the frozen scanner :mod:`etude.frozen.leakage` is unavailable."""
    raw: Mapping[str, str] = _FALLBACK_LEAKAGE
    try:
        from etude.core.config import load_forbidden

        loaded = load_forbidden(None if config_root is None else Path(config_root)).get("leakage_regex")
        if isinstance(loaded, Mapping) and loaded:
            raw = loaded
    except Exception:  # noqa: BLE001 - frozen file missing in a stripped checkout: use the fallback
        pass
    return {k: re.compile(v) for k, v in raw.items()}


@functools.lru_cache(maxsize=8)
def _scanner(config_root: str | None):
    """The frozen :class:`etude.frozen.leakage.LeakageScanner` (single source of
    truth for leakage, incl. its allowlist), or None if it cannot be built."""
    try:
        from etude.frozen.leakage import LeakageScanner

        return LeakageScanner.from_forbidden(None if config_root is None else Path(config_root))
    except Exception:  # noqa: BLE001
        return None


def _numeric_leaves(obj: Any, path: str) -> list[str]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [path]
    if isinstance(obj, Mapping):
        out: list[str] = []
        for k, v in obj.items():
            out.extend(_numeric_leaves(v, f"{path}/{k}"))
        return out
    if isinstance(obj, (list, tuple)):
        out = []
        for i, v in enumerate(obj):
            out.extend(_numeric_leaves(v, f"{path}/{i}"))
        return out
    return []


def text_leaks(text: str, *, config_root: str | Path | None = None) -> list[str]:
    """Kinds of leakage (task_names, layout_ids, coordinates, float_literals,
    layout_files) found in ``text`` (design §3.5), via the frozen scanner."""
    key = None if config_root is None else str(config_root)
    sc = _scanner(key)
    if sc is not None:
        return sorted({h.kind for h in sc.scan(text or "", node_field=True)})
    pats = leakage_patterns(key)
    return [name for name, pat in pats.items() if pat.search(text or "")]


def numeric_errors(nodes: Iterable[Node | Mapping[str, Any]], *, config_root: str | Path | None = None) -> list[str]:
    """All reasons ``nodes`` may not enter memory (design §3.1 门 "schema 通过（无数值 target）").

    Checks, per node: (1) no numeric leaf outside ``stop``/``on_fail`` (catches
    ``"target": [0.31, -0.15, 0.02]`` or extra pose keys in raw dicts); (2)
    ``args.target`` is ``perceive("…")`` whose query has no digit; (3) the
    instruction and perceive query match none of the leakage regexes; plus
    the frozen node schema for the whole sequence.
    """
    errs: list[str] = []
    dicts: list[dict[str, Any]] = []
    for i, n in enumerate(nodes):
        d = n.to_dict() if isinstance(n, Node) else dict(n)
        dicts.append(d)
        nid = d.get("id", f"#{i}")
        for k, v in d.items():
            if k in _NUMERIC_OK_KEYS:
                continue
            for p in _numeric_leaves(v, k):
                errs.append(f"node {nid}: numeric value at {p} (memory stores perceive(\"…\") targets only)")
        args = d.get("args") if isinstance(d.get("args"), Mapping) else {}
        target = args.get("target")
        if target is not None:
            q = parse_perceive(target) if isinstance(target, str) else None
            if q is None:
                errs.append(f"node {nid}: args.target must be perceive(\"…\"), got {target!r}")
            elif re.search(r"\d", q):
                errs.append(f"node {nid}: digits in perceive query {q!r}")
            else:
                for leak in text_leaks(q, config_root=config_root):
                    errs.append(f"node {nid}: perceive query matches leakage regex {leak}")
        for leak in text_leaks(str(d.get("instruction", "")), config_root=config_root):
            errs.append(f"node {nid}: instruction matches leakage regex {leak}")
    if not errs:
        errs.extend(validate_nodes(dicts))
    return errs


def strip_numeric(nodes: Iterable[Node | Mapping[str, Any]], *, config_root: str | Path | None = None) -> list[Node]:
    """Return canonical deep-copied :class:`Node` objects ready for memory, or
    raise :class:`NumericLeakError` if any target is numeric / not
    ``perceive("…")`` or any text leaks coordinates, layout ids or task names
    (design §3.1 "坐标一律换成 perceive", §3.3 ``strip_numeric(ep.seq)``).

    Canonicalisation: unknown keys are dropped by :meth:`Node.from_dict`,
    instructions are whitespace-collapsed. Numeric content is never rewritten.
    """
    nodes = list(nodes)
    errs = numeric_errors(nodes, config_root=config_root)
    if errs:
        raise NumericLeakError(errs)
    out: list[Node] = []
    for n in nodes:
        node = Node.from_dict(n.to_dict() if isinstance(n, Node) else n)
        node.instruction = " ".join(node.instruction.split())
        out.append(node)
    return out


__all__ = ["NumericLeakError", "leakage_patterns", "numeric_errors", "strip_numeric", "text_leaks"]
