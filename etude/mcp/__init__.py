"""MCP server exposing the 7 tools to P1 (design §4 MCP table).

* :mod:`etude.mcp.state` — :class:`HarnessState`, injected by the P2 runner.
* :mod:`etude.mcp.server` — tool bodies, ``MCPServer`` factory, background
  streamable-http thread.
* :mod:`etude.mcp.validate` — ``submit_sequence`` schema + leakage gate.

Demo: ``uv run python -m etude.mcp --demo``.
"""
from etude.mcp.state import HarnessState

__all__ = ["HarnessState"]
