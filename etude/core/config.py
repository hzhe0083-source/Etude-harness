"""Loaders for frozen params and evolvable rules (design §3.0, §3.1, §3.5).

* ``frozen/params/thresholds.yaml``: frozen thresholds, tighten-only.
* ``frozen/forbidden.yaml``: deny-list / allow-list / leakage regexes.
* ``evolvable/rules/recovery.yaml``: evolvable recovery defaults.

All loaders accept an optional ``root`` (repo root) so demos can point at a
temporary copy of the repo (:func:`etude.fakes.make_tmp_repo`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from etude import REPO_ROOT


def _root(root: str | Path | None) -> Path:
    return Path(root) if root is not None else REPO_ROOT


def load_yaml(path: str | Path) -> Any:
    """Read a YAML file (safe_load)."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_thresholds_raw(root: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Full threshold entries ``{name: {value, direction, tighter, unit, used_by}}`` (design §3.5)."""
    data = load_yaml(_root(root) / "frozen" / "params" / "thresholds.yaml") or {}
    return dict(data.get("thresholds") or {})


def load_thresholds(root: str | Path | None = None) -> dict[str, float]:
    """``{name: value}`` from ``frozen/params/thresholds.yaml`` (design §3.5)."""
    return {k: v["value"] for k, v in load_thresholds_raw(root).items()}


def threshold(name: str, root: str | Path | None = None) -> float:
    """Single frozen threshold value; KeyError if unknown (design §3.5)."""
    return load_thresholds(root)[name]


def is_tightening(name: str, old: float, new: float, root: str | Path | None = None) -> bool:
    """True if changing ``name`` from ``old`` to ``new`` is a tightening (or no-op)
    under its ``tighter`` direction (design §3.0 "只收紧")."""
    entry = load_thresholds_raw(root)[name]
    if entry.get("tighter") == "higher":
        return new >= old
    if entry.get("tighter") == "lower":
        return new <= old
    return new == old


def load_forbidden(root: str | Path | None = None) -> dict[str, Any]:
    """Parsed ``frozen/forbidden.yaml`` (keys: deny_names, deny_attr_chains,
    deny_paths, deploy_task_env_allow, allowed_obs, evolvable_whitelist,
    leakage_regex) (design §2, §3.5)."""
    return dict(load_yaml(_root(root) / "frozen" / "forbidden.yaml") or {})


def load_recovery_rules(root: str | Path | None = None) -> dict[str, Any]:
    """Parsed ``evolvable/rules/recovery.yaml`` (design §3.1 harness surface 2)."""
    return dict(load_yaml(_root(root) / "evolvable" / "rules" / "recovery.yaml") or {})


def read_text(rel: str, root: str | Path | None = None) -> str:
    """Read a repo-relative text file, e.g. ``evolvable/prompts/planner.md``."""
    return (_root(root) / rel).read_text(encoding="utf-8")


def deep_get(d: Mapping[str, Any], dotted: str, default: Any = None) -> Any:
    """``deep_get(d, "a.b.c")`` helper for nested config dicts."""
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return default
        cur = cur[part]
    return cur


__all__ = [
    "load_yaml", "load_thresholds_raw", "load_thresholds", "threshold", "is_tightening",
    "load_forbidden", "load_recovery_rules", "read_text", "deep_get",
]
