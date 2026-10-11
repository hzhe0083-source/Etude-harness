"""Local open-set perception (design §4 MCP ``perceive``, §8.3 E row 1).

* :mod:`etude.perceive.detector` -- ``OwlV2Perceiver`` (optional transformers) and
  ``ColorBlobPerceiver`` (demo); ``python -m etude.perceive.detector`` smoke run.
* :mod:`etude.perceive.geometry` -- 2D box relations, RGB diff, multi-view agreement.
"""
from etude.perceive.detector import ColorBlobPerceiver, OwlV2Perceiver

__all__ = ["ColorBlobPerceiver", "OwlV2Perceiver"]
