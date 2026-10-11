"""Leakage regexes (design §3.1 防作弊 row, §3.5 "写死坐标 / seed / 任务名").

Scans free text (evolvable prompts/rules/skills, diffs) and node sequences
(instructions and ``perceive("…")`` queries) for:

* ``task_names``   - any of the 42 RoboDojo task identifiers (+ ``_random``), also
  after normalisation (``stack bowls``, ``stack-bowls``, ``StackBowls``);
* ``layout_ids``   - ``layout 7``, ``seed=1``, ``episode#3``, ``ep7``, ``no. 7``,
  ``seventh layout`` …;
* ``coordinates``  - ``[0.31,-0.15,0.02]`` / ``(x, y, z)`` tuples;
* ``float_literals`` - bare high-precision floats (``0.314``);
* ``distances`` / ``axis_values`` - ``0.3 m``, ``31 cm``, ``x=0.3``;
* ``node_digits`` - any digit in a node field (instruction, perceive query,
  postcondition) after ``leakage_allow`` masking; not applied to prose;
* ``layout_files`` - ``Eval_Layout``, ``_result.json``, ``7.json``, ``task/*/config/``.

The regexes live in ``frozen/forbidden.yaml`` (``leakage_regex``) and the
allowlist in ``leakage_allow``; this module only compiles and applies them.
Run ``python -m etude.frozen.leakage --demo`` or ``python -m etude.frozen.leakage FILE…``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: The 42 RoboDojo tasks (RoboDojo ``scripts/internal/summarize_result.py``
#: ``DIMENSIONS`` at commit 266130a). Used to regenerate / cross-check the
#: ``task_names`` regex in ``frozen/forbidden.yaml``.
TASK_NAMES_42: tuple[str, ...] = (
    # Generalization
    "stack_bowls", "push_T", "pack_objects_into_box", "fold_clothes", "hang_mugs", "sweep_blocks",
    "pour_liquid_into_cup", "make_toast", "arrange_largest_number", "sort_nesting_dolls_by_size",
    "store_laptop_and_headphones", "stack_blocks",
    # Precision
    "fasten_screws", "plug_in_charger", "insert_tubes", "pour_balls_into_vase", "play_Xylophone",
    "deposit_coin", "insert_key", "build_tower",
    # Long-Horizon
    "put_bottles_into_dustbin", "fill_pen_holder", "classify_objects", "play_tic_tac_toe",
    "fill_egg_holder", "organize_table", "make_kong", "play_stacking_toy",
    # Memory
    "cover_blocks", "match_and_pick_from_conveyor", "swap_blocks", "swap_T", "press_by_number",
    "imitate_sorting_sequence",
    # Open
    "align_blocks", "general_pickup", "stack_blocks_by_language", "solve_equation",
    "classify_objects_by_language", "pick_from_conveyor_by_image", "store_tools_in_toolbox",
    "pour_by_language",
)
assert len(TASK_NAMES_42) == 42

#: Fallback regexes if ``frozen/forbidden.yaml`` lacks a key (same content).
DEFAULT_REGEX: dict[str, str] = {
    "task_names": r"(?i)\b(" + "|".join(sorted(n.lower() for n in TASK_NAMES_42)) + r")(_random)?\b",
    "layout_ids": r"(?i)(\b(layout|seed|episode|env_seed|eval_layout|ep|scene|config|variant)\s*(no\.?|number|num|id|#)?"
                  r"[\s_#:=.-]*\d+|\bno\.?\s*\d+|\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
                  r"|eleventh|twelfth|[a-z]+teenth|[a-z]+tieth|\d+(st|nd|rd|th))[\s-]+(layout|seed|episode|scene|setup"
                  r"|config|configuration|variant)s?\b)",
    "coordinates": r"\[\s*-?\d+(\.\d+)?\s*(,\s*-?\d+(\.\d+)?\s*){1,6}\]"
                   r"|\(\s*-?\d+(\.\d+)?\s*(,\s*-?\d+(\.\d+)?\s*){2,6}\)",
    "float_literals": r"(?<![\w.])-?\d+\.\d{2,}(?![\w.])",
    "distances": r"(?i)(?<![\w.])[-+]?\d+(\.\d+)?\s*(m|cm|mm|meters?|metres?|centimet(er|re)s?|millimet(er|re)s?"
                 r"|inch(es)?|deg|degrees?|rad|radians?)(?![\w])",
    "axis_values": r"(?i)(?<![\w.])[xyz]\s*[=:]\s*[-+]?\.?\d",
    "layout_files": r"(?i)(eval_layout|_result\.json|_resume_[\w-]*\.json|\b\d{1,4}\.json\b|task/[\w*]+/config/)",
    "node_digits": r"\d+",
}
#: Kinds applied only to node fields (instruction, perceive query, postconditions), not to
#: evolvable prose, which legitimately carries counts such as "at most 8 nodes".
NODE_ONLY_KINDS: frozenset[str] = frozenset({"node_digits"})
#: Task names matched on normalised text (camelCase → snake, spaces/hyphens → ``_``).
NORM_TASK_RX = re.compile(r"(?<![a-z0-9])(" + "|".join(sorted((n.lower() for n in TASK_NAMES_42), key=len, reverse=True))
                          + r")(_random)?(?![a-z0-9])")


def normalize_names(text: str) -> str:
    """``"StackBowls"``/``"stack-bowls"``/``"stack bowls"`` → ``"stack_bowls"`` (lower-case,
    camelCase split, runs of spaces/hyphens/underscores → ``_``) for task-name matching."""
    t = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    return re.sub(r"[\s\-_]+", "_", t.lower())


DEFAULT_ALLOW: tuple[str, ...] = (r"target_lifted\(>\d*\.\d+m\)",)


@dataclass(frozen=True)
class LeakHit:
    """One leakage match (design §3.5)."""

    kind: str
    match: str
    start: int
    end: int
    where: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "match": self.match, "start": self.start, "end": self.end, "where": self.where}

    def __str__(self) -> str:
        loc = f"{self.where}: " if self.where else ""
        return f"{loc}leak[{self.kind}] {self.match!r}"


class LeakageScanner:
    """Compiled leakage regexes + allowlist (design §3.1 防作弊, §3.5).

    ``regexes``: ``{kind: pattern}``; ``allow``: patterns whose matches are
    blanked out before scanning (e.g. ``target_lifted(>0.05m)``);
    ``kinds``: optional subset of kinds to apply.
    """

    def __init__(self, regexes: Mapping[str, str] | None = None, allow: Iterable[str] = DEFAULT_ALLOW,
                 kinds: Iterable[str] | None = None):
        rx = dict(DEFAULT_REGEX)
        if regexes:
            rx.update({k: v for k, v in regexes.items() if v})
        if kinds is not None:
            keep = set(kinds)
            rx = {k: v for k, v in rx.items() if k in keep}
        self.patterns: dict[str, re.Pattern[str]] = {k: re.compile(v) for k, v in rx.items()}
        self.allow: list[re.Pattern[str]] = [re.compile(a) for a in allow]

    @classmethod
    def from_forbidden(cls, root: str | Path | None = None, kinds: Iterable[str] | None = None) -> "LeakageScanner":
        """Build from ``frozen/forbidden.yaml`` under ``root`` (design §3.5)."""
        from etude.core.config import load_forbidden

        fb = load_forbidden(root)
        allow = fb.get("leakage_allow")
        return cls(fb.get("leakage_regex") or {}, DEFAULT_ALLOW if allow is None else allow, kinds)

    def _mask(self, text: str) -> str:
        for a in self.allow:
            text = a.sub(lambda m: " " * len(m.group(0)), text)
        return text

    def scan(self, text: str, where: str = "", node_field: bool = False) -> list[LeakHit]:
        """All leakage hits in ``text`` (design §3.5); empty list = clean.

        ``node_field``: the text is a node instruction / perceive query /
        postcondition, so :data:`NODE_ONLY_KINDS` (any digit) apply as well."""
        masked = self._mask(text)
        hits: list[LeakHit] = []
        for kind, pat in self.patterns.items():
            if kind in NODE_ONLY_KINDS and not node_field:
                continue
            for m in pat.finditer(masked):
                hits.append(LeakHit(kind, text[m.start():m.end()], m.start(), m.end(), where))
        if "task_names" in self.patterns and not any(h.kind == "task_names" for h in hits):
            norm = normalize_names(masked)
            for m in NORM_TASK_RX.finditer(norm):
                hits.append(LeakHit("task_names", m.group(0), 0, len(text), where))
        hits.sort(key=lambda h: (h.start, h.kind))
        return hits

    def scan_lines(self, text: str, where: str = "") -> list[LeakHit]:
        """Like :meth:`scan` but ``where`` gets ``:<line>`` per hit."""
        out: list[LeakHit] = []
        for i, line in enumerate(text.splitlines(), 1):
            out.extend(self.scan(line, f"{where}:{i}" if where else f"line {i}"))
        return out

    def scan_diff(self, diff: str, where: str = "") -> list[LeakHit]:
        """Scan only the added lines of a unified diff (design §3.1 L0)."""
        out: list[LeakHit] = []
        for i, line in enumerate(diff.splitlines(), 1):
            if line.startswith("+") and not line.startswith("+++"):
                out.extend(self.scan(line[1:], f"{where}:+{i}" if where else f"+{i}"))
        return out

    def scan_nodes(self, nodes: Sequence[Any], where: str = "nodes") -> list[LeakHit]:
        """Scan node ``instruction`` and ``args.target`` strings (design §4
        节点 schema; postconditions are excluded, they are schema-checked)."""
        out: list[LeakHit] = []
        for i, n in enumerate(nodes):
            d = n.to_dict() if hasattr(n, "to_dict") else dict(n)
            nid = d.get("id", f"#{i}")
            out.extend(self.scan(str(d.get("instruction", "")), f"{where}[{nid}].instruction", node_field=True))
            args = d.get("args") or {}
            for key in ("target", "cmd", "arm"):
                if args.get(key) is not None:
                    out.extend(self.scan(str(args[key]), f"{where}[{nid}].args.{key}", node_field=True))
        return out


def task_regex_from_list(names: Iterable[str] = TASK_NAMES_42) -> str:
    """Regenerate the ``task_names`` regex (design §3.5) from a name list."""
    return r"(?i)\b(" + "|".join(sorted(n.lower() for n in names)) + r")(_random)?\b"


def scan_text(text: str, root: str | Path | None = None, where: str = "") -> list[LeakHit]:
    """Convenience: :meth:`LeakageScanner.scan_lines` with forbidden.yaml regexes."""
    return LeakageScanner.from_forbidden(root).scan_lines(text, where)


def _demo() -> int:
    sc = LeakageScanner.from_forbidden()
    samples = {
        "clean prompt": "Refer to objects only through perceive descriptions; check target_lifted(>0.05m).",
        "layout id": "In layout 7 the left bowl is hidden, start with the right arm.",
        "task name": "For stack_bowls_random always open first.",
        "coordinates": 'ee_move to [0.31,-0.15,0.02] then close',
        "float": "move 0.314 m forward",
        "layout file": "read Eval_Layout/x/0/12.json first",
        "distance": "move 0.3 m left then 31 cm forward",
        "axis": "go to x=0.3, y=-0.2",
        "layout no.": "as in layout no. 7",
        "ep id": "like ep7",
        "ordinal layout": "the seventh layout needs the right arm",
        "spaced task": "for stack bowls random open first",
        "hyphen task": "stack-bowls needs care",
        "camel task": "StackBowls needs care",
    }
    bad = 0
    for name, text in samples.items():
        hits = sc.scan(text, name)  # prose mode: node_digits off
        bad += bool(hits)
        print(f"{name:12s} -> {'CLEAN' if not hits else '; '.join(str(h) for h in hits)}")
    yaml_rx = sc.patterns["task_names"].pattern
    print("task_names regex matches the 42-name list:", yaml_rx == task_regex_from_list())
    print(f"{bad}/{len(samples) - 1} leaky samples caught")
    return 0 if bad == len(samples) - 1 else 1


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python -m etude.frozen.leakage [--demo] [--json] FILE…`` (design §3.5)."""
    ap = argparse.ArgumentParser(prog="python -m etude.frozen.leakage", description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.demo:
        return _demo()
    sc = LeakageScanner.from_forbidden()
    hits: list[LeakHit] = []
    for f in a.files:
        hits.extend(sc.scan_lines(Path(f).read_text(encoding="utf-8"), f))
    if a.json:
        print(json.dumps([h.to_dict() for h in hits], indent=1))
    else:
        for h in hits:
            print(h)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
