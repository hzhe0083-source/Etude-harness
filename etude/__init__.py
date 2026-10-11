"""Etude VLA harness v1 (docs/design/vla_harness_v1.md).

Repo-root helpers so components can locate frozen/ and evolvable/ files without
hard-coding absolute paths.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
FROZEN_DIR: Path = REPO_ROOT / "frozen"
EVOLVABLE_DIR: Path = REPO_ROOT / "evolvable"
MEMORY_DB_DIR: Path = REPO_ROOT / "memory_db"
TRACES_DIR: Path = REPO_ROOT / "traces"

__all__ = ["REPO_ROOT", "FROZEN_DIR", "EVOLVABLE_DIR", "MEMORY_DB_DIR", "TRACES_DIR"]
