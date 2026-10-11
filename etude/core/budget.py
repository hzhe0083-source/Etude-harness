"""Per-episode call / step budget for P2 (design §1 "每回合 ≤12 次调用", §3.5
"超调用预算" row, §4 MCP ``budget() → {calls_left, chunks_left}``).

The budget is a hard stop counted inside P2: every blocking planner call and
every ``inspect`` second opinion is one VLM call (cap 12 per episode, frozen
threshold ``budget.vlm_calls_per_episode``, tighten-only). ``chunks_left`` is
derived from the episode ``step_lim`` that deploy.py writes into
``obs["additional_info"]["step_lim"]`` (design §2 P4 row).

When a charge would exceed a cap, :class:`BudgetExhausted` is raised; callers
check :meth:`Budget.can_call` first and take the no-VLM path for the node
(design §3.5: "超限节点走无 VLM 路径").

Run ``uv run python -m etude.core.budget --demo`` for a walk-through on fakes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from etude.core.types import DEFAULT_EXEC_STEPS, MAX_VLM_CALLS_PER_EPISODE, STEP_LIM, ObsFrame

#: Charge kinds that count as one VLM call (design §3.3: plan; §4: inspect).
CALL_KINDS: tuple[str, ...] = ("plan", "inspect", "other")


class BudgetExhausted(RuntimeError):
    """Raised when a charge exceeds a cap (design §3.5 "P2 计数硬停").

    ``resource`` is ``"calls"`` or ``"steps"``; ``used``/``cap`` are the
    counters at the time of the refused charge (the charge is NOT applied).
    """

    def __init__(self, resource: str, used: int, requested: int, cap: int):
        super().__init__(f"budget exhausted: {resource} used={used} requested={requested} cap={cap}")
        self.resource = resource
        self.used = used
        self.requested = requested
        self.cap = cap


@dataclass
class Budget:
    """Episode budget (design §1, §3.5, §4 ``budget`` tool).

    * ``calls_cap``: max VLM calls per episode (planner + inspect); must not
      exceed the frozen cap :data:`MAX_VLM_CALLS_PER_EPISODE` (tighten-only,
      design §3.0 "阈值...冻结，只收紧").
    * ``step_lim``: env ``take_action`` limit for the episode (None = unknown;
      then step/chunk budgets are unbounded and reported as None).
    * ``chunk_steps``: steps per chunk used to convert remaining steps into
      ``chunks_left`` (default ``exec_steps`` = 25, design §4 node ``stop``).
    """

    calls_cap: int = MAX_VLM_CALLS_PER_EPISODE
    step_lim: int | None = None
    chunk_steps: int = DEFAULT_EXEC_STEPS
    calls_used: int = 0
    steps_used: int = 0
    chunks_used: int = 0
    tokens_used: int = 0
    calls_by_kind: dict[str, int] = field(default_factory=dict)
    log: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.calls_cap, int) or self.calls_cap < 0:
            raise ValueError(f"calls_cap must be a non-negative int, got {self.calls_cap!r}")
        if self.calls_cap > MAX_VLM_CALLS_PER_EPISODE:
            raise ValueError(
                f"calls_cap={self.calls_cap} loosens the frozen cap {MAX_VLM_CALLS_PER_EPISODE} "
                "(design §3.0: thresholds are tighten-only)"
            )
        if self.step_lim is not None and int(self.step_lim) < 0:
            raise ValueError(f"step_lim must be >= 0, got {self.step_lim!r}")
        if self.chunk_steps < 1:
            raise ValueError(f"chunk_steps must be >= 1, got {self.chunk_steps!r}")

    # -- constructors ---------------------------------------------------------
    @classmethod
    def from_config(
        cls,
        step_lim: int | None = None,
        *,
        task: str | None = None,
        chunk_steps: int = DEFAULT_EXEC_STEPS,
        root: str | Path | None = None,
    ) -> "Budget":
        """Build with ``calls_cap`` read from frozen threshold
        ``budget.vlm_calls_per_episode`` (design §3.5). ``step_lim`` falls back to
        :data:`STEP_LIM[task]` when not given (design §8.2 F6)."""
        from etude.core.config import threshold

        cap = int(threshold("budget.vlm_calls_per_episode", root=root))
        if step_lim is None and task is not None:
            step_lim = STEP_LIM.get(task)
        return cls(calls_cap=min(cap, MAX_VLM_CALLS_PER_EPISODE), step_lim=step_lim, chunk_steps=chunk_steps)

    # -- VLM calls -------------------------------------------------------------
    @property
    def calls_left(self) -> int:
        """Remaining VLM calls this episode (design §4 ``budget``)."""
        return max(0, self.calls_cap - self.calls_used)

    def can_call(self, n: int = 1) -> bool:
        """True if ``n`` more VLM calls fit (design §3.5: else take the no-VLM path)."""
        return self.calls_used + n <= self.calls_cap

    def charge_call(self, kind: str = "plan", tokens: int = 0, *, t: int | None = None) -> int:
        """Count one VLM call of ``kind`` (plan / inspect / other) plus its
        token usage; return ``calls_left`` afterwards. Raises
        :class:`BudgetExhausted` (without charging) if the cap is reached
        (design §3.5 "P2 计数硬停（12 次/回合）")."""
        if kind not in CALL_KINDS:
            raise ValueError(f"unknown call kind {kind!r}; expected one of {CALL_KINDS}")
        if not self.can_call(1):
            raise BudgetExhausted("calls", self.calls_used, 1, self.calls_cap)
        self.calls_used += 1
        self.tokens_used += max(0, int(tokens))
        self.calls_by_kind[kind] = self.calls_by_kind.get(kind, 0) + 1
        self.log.append({"kind": kind, "tokens": int(tokens), "t": t, "calls_used": self.calls_used})
        return self.calls_left

    def add_tokens(self, tokens: int) -> None:
        """Add token usage reported after a call returned (cost item, design §3.4)."""
        self.tokens_used += max(0, int(tokens))

    # -- env steps / chunks ----------------------------------------------------
    @property
    def steps_left(self) -> int | None:
        """Remaining env steps, or None when ``step_lim`` is unknown."""
        if self.step_lim is None:
            return None
        return max(0, int(self.step_lim) - self.steps_used)

    @property
    def chunks_left(self) -> int | None:
        """Remaining chunks of ``chunk_steps`` steps, from ``step_lim`` (design §4)."""
        left = self.steps_left
        return None if left is None else int(math.ceil(left / self.chunk_steps))

    def clip_actions(self, n: int) -> int:
        """Number of the next ``n`` actions that still fit in ``step_lim``."""
        left = self.steps_left
        return n if left is None else max(0, min(int(n), left))

    def charge_steps(self, n: int) -> int:
        """Count ``n`` executed env steps; raise :class:`BudgetExhausted`
        (without charging) if that would pass ``step_lim``. Returns steps used."""
        n = int(n)
        if n < 0:
            raise ValueError("n must be >= 0")
        if self.step_lim is not None and self.steps_used + n > int(self.step_lim):
            raise BudgetExhausted("steps", self.steps_used, n, int(self.step_lim))
        self.steps_used += n
        return self.steps_used

    def charge_chunk(self, n_actions: int) -> int | None:
        """Count one emitted chunk of ``n_actions`` steps; return ``chunks_left``."""
        self.charge_steps(n_actions)
        self.chunks_used += 1
        return self.chunks_left

    def sync_obs(self, frame: ObsFrame) -> None:
        """Adopt ``step_lim`` from the obs (deploy.py writes it, design §2) and
        never let ``steps_used`` lag behind the frame's env step ``t``."""
        if frame.step_lim is not None:
            self.step_lim = frame.step_lim
        self.steps_used = max(self.steps_used, int(frame.t))

    # -- reporting -------------------------------------------------------------
    def to_mcp(self) -> dict[str, int | None]:
        """Exact MCP ``budget()`` payload ``{calls_left, chunks_left}`` (design §4)."""
        return {"calls_left": self.calls_left, "chunks_left": self.chunks_left}

    def snapshot(self) -> dict[str, Any]:
        """Full counters for trace / EpisodeRecord (vlm_calls, vlm_tokens, steps, chunks)."""
        return {
            "calls_cap": self.calls_cap,
            "calls_used": self.calls_used,
            "calls_left": self.calls_left,
            "calls_by_kind": dict(self.calls_by_kind),
            "tokens_used": self.tokens_used,
            "step_lim": self.step_lim,
            "steps_used": self.steps_used,
            "steps_left": self.steps_left,
            "chunks_used": self.chunks_used,
            "chunks_left": self.chunks_left,
        }

    def reset(self, step_lim: int | None = None) -> None:
        """Clear per-episode counters (design §3.5 "reset() 清空回合态")."""
        self.step_lim = step_lim
        self.calls_used = self.steps_used = self.chunks_used = self.tokens_used = 0
        self.calls_by_kind = {}
        self.log = []


def _demo() -> int:
    from etude.fakes import fake_frame

    b = Budget.from_config(task="stack_bowls")
    print("init      ", b.to_mcp())
    b.sync_obs(fake_frame(t=0, step_lim=100))
    print("step_lim←obs", b.to_mcp())
    b.charge_call("plan", tokens=9000)
    b.charge_chunk(25)
    print("after plan+chunk", b.to_mcp())
    print("clip 50 actions with 75 steps left ->", b.clip_actions(50))
    while b.can_call():
        b.charge_call("inspect")
    try:
        b.charge_call("plan")
    except BudgetExhausted as e:
        print("refused   :", e)
    try:
        b.charge_steps(1000)
    except BudgetExhausted as e:
        print("refused   :", e)
    try:
        Budget(calls_cap=20)
    except ValueError as e:
        print("refused   :", e)
    print("snapshot  ", b.snapshot())
    return 0


if __name__ == "__main__":
    import sys

    if "--demo" in sys.argv[1:]:
        raise SystemExit(_demo())
    print("usage: python -m etude.core.budget --demo")
