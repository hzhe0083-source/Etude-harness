"""Evolution loop (design §3): gates L0–L3 + cost rule (``gates``), A/A noise
calibration of ε (``aa_noise``), lineage receipts (``lineage``), candidate git
worktrees (``worktree``) and one harness-loop round (``round.evolve_round``).

``python -m etude.evolution --demo`` runs three rounds on a throw-away git repo
with a fake editor and fake per-layout result tables and prints the receipts.
Submodules are imported lazily so ``python -m etude.evolution.<mod>`` works
without importing the whole package.
"""
from __future__ import annotations

__all__ = ["aa_noise", "gates", "lineage", "round", "worktree"]
