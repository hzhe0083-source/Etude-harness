#!/usr/bin/env bash
# M0 sweep (docs/design/vla_harness_v1.md §5 M0, §3.2): frozen π0.5, execute the first N steps
# of each 50-step chunk, N in {5,10,15,25,50}, dev tasks x seeds 0/1 x 50 layouts.
# Held-out tasks run only at the best N (--heldout --nstar N).
#
# Each cell calls scripts/run_m0.sh --task T --seed S --eval-num K --n N (adapter component).
# With RESEARCH_RUN=1 every call is wrapped in the `research run ...` that
# etude.evolution.research_bridge.run_command builds (via experiments/expctl.py run-argv):
# the Run executes committed+pushed code in a detached worktree and the entry writes
# {"successes","trials","success_rate"} to $RESEARCH_METRICS. Without RESEARCH_RUN the entry
# runs directly (local smoke).
#
# Usage:
#   bash scripts/m0_sweep.sh [--tasks "a b"] [--ns "5 10"] [--seeds "0 1"] [--eval-num 50]
#                            [--heldout --nstar N] [--dry-run] [--force]
# Env:
#   RESEARCH_RUN=1             wrap each call in `research run`. Needs RESEARCH_EXPERIMENT=E-<n> (or
#                              experiments/M0/config.yaml research.experiment) and
#                              RESEARCH_CHECKPOINT=model=<OWNER>/<NAME>@<REV40>:<path> (your HF model
#                              mirror of the official ckpt; --archive-to is that same repo)
#   RESEARCH_ACTOR             Research OS writer id (default agent:m0_sweep)
#   DRY_RUN=1                  print the commands only (same as --dry-run)
#   EXTRA_ARGS="..."           extra flags appended to every run_m0.sh call (e.g. --backend-host H --policy-gpu 1)
#   ENTRY=scripts/run_m0.sh    override the entry script
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
EXPCTL=(uv run --quiet python experiments/expctl.py)
cfg() { "${EXPCTL[@]}" get M0 "$1"; }

ENTRY="${ENTRY:-scripts/run_m0.sh}"
TASKS=""; NS=""; SEEDS=""; EVAL_NUM=""; HELDOUT=0; NSTAR="${NSTAR:-}"; FORCE=0
DRY_RUN="${DRY_RUN:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tasks) TASKS="$2"; shift 2 ;;
    --ns) NS="$2"; shift 2 ;;
    --seeds) SEEDS="$2"; shift 2 ;;
    --eval-num) EVAL_NUM="$2"; shift 2 ;;
    --heldout) HELDOUT=1; shift ;;
    --nstar) NSTAR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "[m0_sweep] unknown option $1" >&2; exit 2 ;;
  esac
done

if [[ "${HELDOUT}" == 1 ]]; then
  [[ -n "${NSTAR}" ]] || { echo "[m0_sweep] --heldout needs --nstar N (best N from the dev sweep)" >&2; exit 2; }
  TASKS="${TASKS:-$(cfg tasks.heldout)}"
  NS="${NSTAR}"
else
  TASKS="${TASKS:-$(cfg tasks.dev)}"
  NS="${NS:-$(cfg sweep.exec_steps_N)}"
fi
SEEDS="${SEEDS:-$(cfg seeds)}"
EVAL_NUM="${EVAL_NUM:-$(cfg eval_num)}"

if [[ "${DRY_RUN}" != 1 && ! -f "${ENTRY}" ]]; then
  echo "[m0_sweep] ${ENTRY} not found (adapter component not written yet); use --dry-run to inspect" >&2
  exit 2
fi

read -r -a task_arr <<<"${TASKS}"; read -r -a n_arr <<<"${NS}"; read -r -a seed_arr <<<"${SEEDS}"
total=$(( ${#task_arr[@]} * ${#n_arr[@]} * ${#seed_arr[@]} ))
i=0; failed=(); skipped=()
echo "[m0_sweep] tasks=(${TASKS}) N=(${NS}) seeds=(${SEEDS}) eval_num=${EVAL_NUM} cells=${total} research=${RESEARCH_RUN:-0} dry=${DRY_RUN}"

for seed in "${seed_arr[@]}"; do
  for task in "${task_arr[@]}"; do
    status="$("${EXPCTL[@]}" split-status --task "${task}" --seed "${seed}" || true)"
    for n in "${n_arr[@]}"; do
      i=$((i + 1))
      tag="m0-${task}-s${seed}-n${n}"
      if [[ "${status}" != ok && "${FORCE}" != 1 ]]; then
        echo "[m0_sweep] (${i}/${total}) skip ${tag}: layouts ${status} (experiments/splits.json; --force to run anyway)"
        skipped+=("${tag}")
        continue
      fi
      # shellcheck disable=SC2206
      entry=(bash "${ENTRY}" --task "${task}" --seed "${seed}" --eval-num "${EVAL_NUM}" --n "${n}" ${EXTRA_ARGS:-})
      if [[ "${RESEARCH_RUN:-0}" == 1 ]]; then
        wp=(); [[ "${DRY_RUN}" == 1 ]] || wp=(--write-params)
        mapfile -t cmd < <("${EXPCTL[@]}" run-argv --milestone M0 "${wp[@]}" \
          --slot "task=${task}" --slot "n=${n}" --slot "seed=${seed}" \
          --param "eval_num=${EVAL_NUM}" --param "split=$([[ ${seed} == 0 ]] && echo E || echo V)" \
          -- "${entry[@]}")
        [[ ${#cmd[@]} -gt 0 ]] || { failed+=("${tag}"); continue; }
        export RESEARCH_ACTOR="${RESEARCH_ACTOR:-agent:m0_sweep}"
      else
        cmd=("${entry[@]}")
      fi
      echo "[m0_sweep] (${i}/${total}) ${tag}: $(printf '%q ' "${cmd[@]}")"
      [[ "${DRY_RUN}" == 1 ]] && continue
      if ! "${cmd[@]}"; then
        echo "[m0_sweep] FAILED ${tag}" >&2
        failed+=("${tag}")
      fi
    done
  done
done

echo "[m0_sweep] done: ${total} cells, ${#skipped[@]} skipped (missing layouts), ${#failed[@]} failed"
[[ ${#failed[@]} -eq 0 ]] || { printf '[m0_sweep] failed: %s\n' "${failed[@]}" >&2; exit 1; }
