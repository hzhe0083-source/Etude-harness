#!/usr/bin/env bash
# P5 offline scorer entry (design §2 P5, §3.5 "改打分或汇总"). Git-tracked entry
# script usable as a Research OS evaluation Run command (no inline -c):
#
#   research run --experiment E-<n> --kind evaluation ... -- scorer/run_official.sh \
#       --eval-root <eval_result/RoboDojo> --task stack_bowls --task put_bottles_into_dustbin [--policy Etude]
#
# Steps:
#   1. hash the official summarize_result.py and refuse if it differs from
#      --expect-summarize-sha (the hash pinned in the incumbent receipt);
#   2. run the official summarize unchanged (ROBODOJO_EVAL_ROOT=<eval root>),
#      copying _summary.md into $OUT_DIR;
#   3. run etude.scorer.results with the same 50 / 25+25 rule -> per-layout
#      table and the Research OS core metrics {successes,trials,success_rate}
#      into $RESEARCH_METRICS (when set);
#   4. write an audit record (unstable rate, hashes, repeated runs).
# Inputs are only read; outputs go to $OUT_DIR (default $RESEARCH_OUTPUTS dir or ./gate/out).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS="$(cd "$HERE/.." && pwd)"
ROBODOJO="${ROBODOJO:-$HOME/Documents/RoboDojo}"
EVAL_ROOT=""
POLICY=""
EXPECT_SHA=""
TASKS=()
OUT_DIR="${OUT_DIR:-}"
NAME="${AUDIT_NAME:-score-$(date -u +%Y%m%dT%H%M%SZ)}"
read -r -a PY <<< "${PYTHON:-python3}"   # may be "uv run python"

usage() { sed -n '2,20p' "$0"; exit 2; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --eval-root) EVAL_ROOT="$2"; shift 2;;
    --task) TASKS+=("$2"); shift 2;;
    --policy) POLICY="$2"; shift 2;;
    --robodojo) ROBODOJO="$2"; shift 2;;
    --expect-summarize-sha) EXPECT_SHA="$2"; shift 2;;
    --out-dir) OUT_DIR="$2"; shift 2;;
    --name) NAME="$2"; shift 2;;
    -h|--help) usage;;
    *) echo "unknown arg: $1" >&2; usage;;
  esac
done
[[ -n "$EVAL_ROOT" && ${#TASKS[@]} -gt 0 ]] || usage
if [[ -z "$OUT_DIR" ]]; then
  if [[ -n "${RESEARCH_RUN_DIR:-}" ]]; then OUT_DIR="$RESEARCH_RUN_DIR/scorer"; else OUT_DIR="$PWD/gate/out/$NAME"; fi
fi
mkdir -p "$OUT_DIR"

SUMMARIZE="$ROBODOJO/scripts/internal/summarize_result.py"
[[ -f "$SUMMARIZE" ]] || { echo "official summarize not found: $SUMMARIZE" >&2; exit 3; }
SHA="$(sha256sum "$SUMMARIZE" | cut -d' ' -f1)"
echo "summarize_result.py sha256=$SHA"
if [[ -n "$EXPECT_SHA" && "$SHA" != "$EXPECT_SHA" ]]; then
  echo "REFUSE: summarize_result.py hash $SHA != pinned $EXPECT_SHA" >&2; exit 4
fi

# 2. official summarize writes _summary.md into the eval root; run it on a
#    scratch copy so a read-only mount of the results stays untouched.
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT
cp -r "$EVAL_ROOT"/. "$SCRATCH"/
ROBODOJO_EVAL_ROOT="$SCRATCH" "${PY[@]}" "$SUMMARIZE" | tee "$OUT_DIR/summarize_stdout.txt"
cp "$SCRATCH/_summary.md" "$OUT_DIR/_summary.md"

# 3. our parser (same rule) -> per-layout outcomes + Research OS core metrics
ARGS=(--root "$EVAL_ROOT" --layouts-out "$OUT_DIR/layouts.json" --gate-out "$OUT_DIR/gate_outcomes.json")
for t in "${TASKS[@]}"; do ARGS+=(--task "$t"); done
[[ -n "$POLICY" ]] && ARGS+=(--policy "$POLICY")
[[ -n "${RESEARCH_METRICS:-}" ]] && ARGS+=(--metrics-out "$RESEARCH_METRICS")
( cd "$HARNESS" && PYTHONPATH="$HARNESS${PYTHONPATH:+:$PYTHONPATH}" "${PY[@]}" -m etude.scorer.results "${ARGS[@]}" ) | tee "$OUT_DIR/results.jsonl"

# 4. audit (flags are reported, exit status of the scorer is not changed by them)
( cd "$HARNESS" && PYTHONPATH="$HARNESS${PYTHONPATH:+:$PYTHONPATH}" "${PY[@]}" -m etude.scorer.audit \
    --name "$NAME" --eval-root "$EVAL_ROOT" --robodojo "$ROBODOJO" --out-dir "$OUT_DIR" ) || true
echo "scorer outputs in $OUT_DIR"
