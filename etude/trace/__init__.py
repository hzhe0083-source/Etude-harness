"""Trace / keyframe writer and reader: traces/<episode_id>/ (design §4 tree).

Demo: ``uv run python -m etude.trace --demo``.
"""
from etude.trace.reader import EpisodeTrace, TraceReader, load_png
from etude.trace.writer import FileTraceWriter, save_png

__all__ = ["EpisodeTrace", "FileTraceWriter", "TraceReader", "load_png", "save_png"]
