"""Planner client side: pi-mono RPC driver (design §2 P1, §8.2 F5).

* :mod:`etude.planner.request` — prompt construction for one planning call.
* :mod:`etude.planner.rpc_driver` — :class:`PiRpcPlanner` (``pi --mode rpc``).
* :mod:`etude.planner.fake_pi` — a python stand-in for ``pi --mode rpc`` used
  by the demo (never calls a model).

Demo: ``uv run python -m etude.planner --demo``.
"""
__all__: list[str] = []
