#!/usr/bin/env bash
# A/A re-runs (docs/design/vla_harness_v1.md §3.2 A/A, §3.3 ε calibration, §5 M0/M1):
# the incumbent re-runs V (dev tasks, seed 1, layouts 0-49) 3 times; same layouts, so the spread
# measures PhysX non-determinism + flow-matching sampling only. ε = max(4, max|S_a - S_b| + 1).
#
#   M0: incumbent = π0.5 @ N*  -> scripts/run_m0.sh      --task T --seed 1 --eval-num 50 --n N*
#   M1: incumbent = H1         -> scripts/run_harness.sh --task T --seed 1 --eval-num 50
#
# With RESEARCH_RUN=1 each re-run is its own Research OS Run in the A/A experiment
# (experiments/<M>/view_aa.json: slot task, condition, rep, split=V), built by
# etude.evolution.research_bridge.run_command via experiments/expctl.py run-argv --aa.
#
# Usage:
#   bash scripts/aa_rerun.sh --milestone M0|M1 [--nstar N] [--reps 3] [--tasks "a b"]
#                            [--eval-num 50] [--dry-run] [--force]
# Env: RESEARCH_RUN, RESEARCH_EXPERIMENT (the A/A experiment, or research.aa_experiment),
#      RESEARCH_CHECKPOINT, RESEARCH_ACTOR, NSTAR, DRY_RUN, EXTRA_ARGS (as in scripts/m0_sweep.sh)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
EXPCTL=(uv run --quiet python experiments/expctl.py)

MILESTONE=""; NSTAR="${NSTAR:-}"; REPS=""; TASKS=""; EVAL_NUM=""; FORCE=0
DRY_RUN="${DRY_RUN:-0}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --milestone) MILESTONE="$2"; shift 2 ;;
    --nstar) NSTAR="$2"; shift 2 ;;
    --reps) REPS="$2"; shift 2 ;;
    --tasks) TASKS="$2"; shift 2 ;;
    --eval-num) EVAL_NUM="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "[aa_rerun] unknown option $1" >&2; exit 2 ;;
  esac
done
[[ "${MILESTONE}" == M0 || "${MILESTONE}" == M1 ]] || { echo "[aa_rerun] --milestone M0|M1 required" >&2; exit 2; }
cfg() { "${EXPCTL[@]}" get "${MILESTONE}" "$1"; }

REPS="${REPS:-$(cfg aa.reps)}"
TASKS="${TASKS:-$(cfg aa.tasks)}"
SEED="$(cfg aa.seed)"
EVAL_NUM="${EVAL_NUM:-$(cfg eval_num)}"
CONDITION="$(cfg aa.incumbent)"

if [[ "${MILESTONE}" == M0 ]]; then
  [[ -n "${NSTAR}" ]] || { echo "[aa_rerun] M0 A/A needs --nstar N (incumbent π0.5 @ N*)" >&2; exit 2; }
  ENTRY="${ENTRY:-scripts/run_m0.sh}"
else
  ENTRY="${ENTRY:-scripts/run_harness.sh}"
fi
if [[ "${DRY_RUN}" != 1 && ! -f "${ENTRY}" ]]; then
  echo "[aa_rerun] ${ENTRY} not found (adapter component not written yet); use --dry-run to inspect" >&2
  exit 2
fi

read -r -a task_arr <<<"${TASKS}"
total=$(( REPS * ${#task_arr[@]} ))
i=0; failed=(); skipped=()
echo "[aa_rerun] ${MILESTONE} incumbent=${CONDITION} tasks=(${TASKS}) seed=${SEED} reps=${REPS} eval_num=${EVAL_NUM} research=${RESEARCH_RUN:-0} dry=${DRY_RUN}"

for rep in $(seq 1 "${REPS}"); do
  for task in "${task_arr[@]}"; do
    i=$((i + 1))
    tag="aa-${MILESTONE}-${CONDITION}-${task}-s${SEED}-r${rep}"
    status="$("${EXPCTL[@]}" split-status --task "${task}" --seed "${SEED}" || true)"
    if [[ "${status}" != ok && "${FORCE}" != 1 ]]; then
      echo "[aa_rerun] (${i}/${total}) skip ${tag}: layouts ${status} (experiments/splits.json; --force to run anyway)"
      skipped+=("${tag}")
      continue
    fi
    if [[ "${MILESTONE}" == M0 ]]; then
      # shellcheck disable=SC2206
      entry=(bash "${ENTRY}" --task "${task}" --seed "${SEED}" --eval-num "${EVAL_NUM}" --n "${NSTAR}" ${EXTRA_ARGS:-})
    else
      # shellcheck disable=SC2206
      entry=(bash "${ENTRY}" --task "${task}" --seed "${SEED}" --eval-num "${EVAL_NUM}" ${EXTRA_ARGS:-})
    fi
    if [[ "${RESEARCH_RUN:-0}" == 1 ]]; then
      wp=(); [[ "${DRY_RUN}" == 1 ]] || wp=(--write-params)
      xp=(); [[ "${MILESTONE}" == M0 ]] && xp=(--param "n=${NSTAR}")
      mapfile -t cmd < <("${EXPCTL[@]}" run-argv --milestone "${MILESTONE}" --aa "${wp[@]}" \
        --slot "task=${task}" --slot "condition=${CONDITION}" --slot "rep=${rep}" --slot "split=V" \
        --param "seed=${SEED}" --param "eval_num=${EVAL_NUM}" "${xp[@]}" \
        -- "${entry[@]}")
      [[ ${#cmd[@]} -gt 0 ]] || { failed+=("${tag}"); continue; }
      export RESEARCH_ACTOR="${RESEARCH_ACTOR:-agent:aa_rerun}"
    else
      cmd=("${entry[@]}")
    fi
    echo "[aa_rerun] (${i}/${total}) ${tag}: $(printf '%q ' "${cmd[@]}")"
    [[ "${DRY_RUN}" == 1 ]] && continue
    if ! "${cmd[@]}"; then
      echo "[aa_rerun] FAILED ${tag}" >&2
      failed+=("${tag}")
    fi
  done
done

echo "[aa_rerun] done: ${total} re-runs, ${#skipped[@]} skipped (missing layouts), ${#failed[@]} failed"
echo "[aa_rerun] next: S_i = successes per rep on V; eps = max(4, max|S_a - S_b| + 1); eps > 15 -> enlarge V to 150 (§3.3, §5)"
[[ ${#failed[@]} -eq 0 ]] || { printf '[aa_rerun] failed: %s\n' "${failed[@]}" >&2; exit 1; }
