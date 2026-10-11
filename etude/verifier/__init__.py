"""Code verifier (design §4 判决 JSON and 谓词表, §3.5 threat table, §8.3 E row 1).

* :mod:`etude.verifier.predicates` -- predicate table + tighten-only threshold loader.
* :mod:`etude.verifier.verdict` -- :class:`CodeVerifier` (five verdict kinds).
* ``python -m etude.verifier --demo`` -- synthetic Pass / Fail / InsufficientEvidence demo.
"""
from etude.verifier.predicates import ThresholdError, VerifierThresholds, load_verifier_thresholds
from etude.verifier.verdict import CodeVerifier, PngKeyframeSaver, decide_kind

__all__ = [
    "CodeVerifier",
    "PngKeyframeSaver",
    "ThresholdError",
    "VerifierThresholds",
    "decide_kind",
    "load_verifier_thresholds",
]
