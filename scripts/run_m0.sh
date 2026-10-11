#!/usr/bin/env bash
# M0 evaluation entry: plain pi0.5 requeried every N steps through the Etude adapter
# (design §5 M0, §2 P2/P4). Wraps ~/Documents/RoboDojo/scripts/robodojo.sh eval.
# It is a Git-tracked entry script, so Research OS can run it as
#   research run --experiment E-<n> --kind evaluation ... -- scripts/run_m0.sh --task stack_bowls --seed 0 --eval-num 50 --n 25
#
# On exit it writes {"successes","trials","success_rate"} from the official RoboDojo result
# to $RESEARCH_METRICS when that variable is set; otherwise it prints them. It also writes etude_run.json
# (result dir, deploy.py hash, commits, unstable count) to $RESEARCH_RUN_DIR (or --out-dir).
# $RESEARCH_OUTPUTS only accepts HF refs ("model|dataset=OWNER/NAME@REV[:path]"), so it is
# written only from --output NAME=REF. The local result directory goes to etude_run.json instead.
#
# scripts/run_harness.sh calls this file with --mode harness. Requires: the openpi server (P3)
# reachable at --backend-host:--backend-port, the RoboDojo conda env, and the Etude .venv
# with XPolicyLab installed (deploy/XPolicyLab/policy/Etude/install.sh).
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/run_m0.sh --task TASK --seed SEED --eval-num K --n N [options]
       scripts/run_harness.sh --task TASK --seed SEED --eval-num K [options]

Required:
  --task TASK            RoboDojo task (e.g. stack_bowls)
  --seed SEED            layout seed (design §3.2: 0 = E, 1 = V, 2 = T)
  --eval-num K|native    episodes (layouts) to run
  --n N                  M0 requery step N = exec_steps (design §5: 5,10,15,25,50); in harness mode
                         optional and fixes every vla node's exec_steps to N (design §6 control)

Options:
  --mode MODE            m0_requery (default here) | harness
  --ckpt NAME            checkpoint label recorded in result paths (default: pi05-joint-0-59999)
  --action-type NAME     result-path label (default: ee, as in deploy.yml)
  --env-cfg NAME         env_cfg stem (default: arx_x5)
  --policy-gpu ID        policy server GPU (default: 0)
  --env-gpu ID           Isaac Sim GPU (default: 0)
  --eval-env ENV         RoboDojo sim conda env (default: RoboDojo)
  --backend-kind KIND    remote | inprocess | fake (default: remote)
  --backend-host HOST    openpi serve_policy.py host (default: localhost)
  --backend-port PORT    openpi serve_policy.py port (default: 8000)
  --robodojo-root DIR    RoboDojo clone (default: $ROBODOJO_ROOT or ~/Documents/RoboDojo)
  --out-dir DIR          where etude_run.json/episode logs go when $RESEARCH_RUN_DIR is unset
  --output NAME=REF      extra $RESEARCH_OUTPUTS entry (REF = model|dataset=OWNER/NAME@REV[:path]); repeatable
  --dry-run              print the RoboDojo command and stop
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ETUDE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
POLICY_SRC="${ETUDE_ROOT}/deploy/XPolicyLab/policy/Etude"

task=""; seed=""; eval_num=""; n=""
mode="m0_requery"
ckpt="pi05-joint-0-59999"
action_type="ee"
env_cfg="arx_x5"
policy_gpu="0"; env_gpu="0"
eval_env="RoboDojo"
backend_kind="remote"; backend_host="localhost"; backend_port="8000"
robodojo_root="${ROBODOJO_ROOT:-${HOME}/Documents/RoboDojo}"
out_dir=""
dry_run="false"
outputs=()

need() { [[ $# -ge 2 && "$2" != --* ]] || { echo "[run] missing value for $1" >&2; exit 2; }; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --task) need "$@"; task="$2"; shift 2 ;;
    --seed) need "$@"; seed="$2"; shift 2 ;;
    --eval-num) need "$@"; eval_num="$2"; shift 2 ;;
    --n) need "$@"; n="$2"; shift 2 ;;
    --mode) need "$@"; mode="$2"; shift 2 ;;
    --ckpt) need "$@"; ckpt="$2"; shift 2 ;;
    --action-type) need "$@"; action_type="$2"; shift 2 ;;
    --env-cfg) need "$@"; env_cfg="$2"; shift 2 ;;
    --policy-gpu) need "$@"; policy_gpu="$2"; shift 2 ;;
    --env-gpu) need "$@"; env_gpu="$2"; shift 2 ;;
    --eval-env) need "$@"; eval_env="$2"; shift 2 ;;
    --backend-kind) need "$@"; backend_kind="$2"; shift 2 ;;
    --backend-host) need "$@"; backend_host="$2"; shift 2 ;;
    --backend-port) need "$@"; backend_port="$2"; shift 2 ;;
    --robodojo-root) need "$@"; robodojo_root="$2"; shift 2 ;;
    --out-dir) need "$@"; out_dir="$2"; shift 2 ;;
    --output) need "$@"; outputs+=("$2"); shift 2 ;;
    --dry-run) dry_run="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[run] unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "${task}" && -n "${seed}" && -n "${eval_num}" ]] || { echo "[run] --task, --seed and --eval-num are required" >&2; exit 2; }
case "${mode}" in
  m0_requery) [[ -n "${n}" ]] || { echo "[run] m0_requery needs --n" >&2; exit 2; } ;;
  harness) ;;
  *) echo "[run] --mode must be m0_requery or harness" >&2; exit 2 ;;
esac
if [[ -n "${n}" ]] && ! [[ "${n}" =~ ^[0-9]+$ && "${n}" -ge 1 && "${n}" -le 50 ]]; then
  echo "[run] --n must be an integer in [1,50]" >&2; exit 2
fi
robodojo_sh="${robodojo_root}/scripts/robodojo.sh"
[[ -f "${robodojo_sh}" ]] || { echo "[run] robodojo.sh not found: ${robodojo_sh}" >&2; exit 1; }

# --- python for small JSON jobs (Etude venv if present, else python3) --------------------------
PY="python3"
for cand in "${ETUDE_VENV:-}/bin/python" "${ETUDE_ROOT}/.venv/bin/python"; do
  [[ -x "${cand}" ]] && { PY="${cand}"; break; }
done

# --- deploy.py hash audit (design §2 边界落地) -----------------------------------------------
deploy_sha="$(sha256sum "${POLICY_SRC}/deploy.py" | cut -d' ' -f1)"
recorded_sha="$(cut -d' ' -f1 "${POLICY_SRC}/DEPLOY_SHA256" 2>/dev/null || true)"
if [[ "${deploy_sha}" != "${recorded_sha}" ]]; then
  echo "[run] deploy.py hash ${deploy_sha} != DEPLOY_SHA256 ${recorded_sha:-<missing>}; refusing to run" >&2
  exit 1
fi

# --- record directory ----------------------------------------------------------------------------
stamp="$(date +%Y%m%d-%H%M%S)"
tag="etude-${mode}-${task}-s${seed}${n:+-n${n}}-${stamp}"
record_dir="${RESEARCH_RUN_DIR:-${out_dir:-${ETUDE_ROOT}/runs/local/${tag}}}"
mkdir -p "${record_dir}"

export ROBODOJO_RUN_ID="${tag}"
export ETUDE_MODE="${mode}"
vla_fixed="false"
if [[ -n "${n}" ]]; then
  export ETUDE_EXEC_STEPS="${n}"
  # harness: --n fixes every vla node's exec_steps (design §6 control); m0_requery: N itself
  if [[ "${mode}" == "harness" ]]; then export ETUDE_VLA_EXEC_STEPS_FIXED=1; vla_fixed="true"; fi
fi
export ETUDE_BACKEND_KIND="${backend_kind}"
export ETUDE_BACKEND_HOST="${backend_host}"
export ETUDE_BACKEND_PORT="${backend_port}"
export ETUDE_LOG_DIR="${record_dir}/etude_episodes"
export ETUDE_ROOT

eval_cmd=(bash "${robodojo_sh}" eval
  --policy-dir "${robodojo_root}/XPolicyLab/policy/Etude"
  --task "${task}" --ckpt "${ckpt}" --env-cfg "${env_cfg}" --action-type "${action_type}"
  --seed "${seed}" --policy-gpu "${policy_gpu}" --env-gpu "${env_gpu}"
  --policy-env uv --eval-env "${eval_env}" --eval-num "${eval_num}")

if [[ "${dry_run}" == "true" ]]; then
  echo "[run] mode=${mode} n=${n:-default} vla_exec_steps_fixed=${vla_fixed} record_dir=${record_dir} run_id=${ROBODOJO_RUN_ID} deploy_sha256=${deploy_sha}"
  printf '[run] would link %q -> %q and run:' "${robodojo_root}/XPolicyLab/policy/Etude" "${POLICY_SRC}"
  printf ' %q' "${eval_cmd[@]}"; printf '\n'
  if [[ -f "${robodojo_root}/XPolicyLab/policy/Etude/eval.sh" ]]; then
    "${eval_cmd[@]}" --dry-run
  else
    echo "[run] (XPolicyLab/policy/Etude not linked yet; robodojo.sh --dry-run skipped)"
  fi
  rmdir "${record_dir}" 2>/dev/null || true
  exit 0
fi

# --- one Etude link at a time: the symlink must point at THIS checkout during the run -------------
lock="${robodojo_root}/XPolicyLab/policy/.etude_link.lock"
exec 9>"${lock}"
flock -n 9 || { echo "[run] another Etude run holds ${lock}" >&2; exit 1; }
bash "${SCRIPT_DIR}/link_into_xpolicylab.sh" --robodojo-root "${robodojo_root}" --source "${POLICY_SRC}" --force >/dev/null

if [[ "${backend_kind}" == "remote" ]]; then
  if ! timeout 5 bash -c ">/dev/tcp/${backend_host}/${backend_port}" 2>/dev/null; then
    echo "[run] openpi server not reachable at ${backend_host}:${backend_port} (start serve_policy.py first)" >&2
    exit 1
  fi
fi

git_sha() { git -C "$1" rev-parse HEAD 2>/dev/null || echo unknown; }
start_s="$(date +%s)"
set +e
"${eval_cmd[@]}" 2>&1 | tee "${record_dir}/robodojo_eval.log"
rc=${PIPESTATUS[0]}
set -e
end_s="$(date +%s)"

# --- locate the official result of THIS run (run_id is unique) ------------------------------------
result_json="$(find "${robodojo_root}/eval_result" -path "*/${task}/Etude/*/${ROBODOJO_RUN_ID}/_result.json" 2>/dev/null | head -n1 || true)"
result_dir="${result_json:+$(dirname "${result_json}")}"

set +e
# Official metrics (design §5 指标统一). RoboDojo's _result.json holds only
# {success_rate, eval_time, score, details} (eval_env.py 266130a, lines 813-832); the
# success/fail/unstable counters live in the resume manifest _resume_<run_id>.json one
# directory above save_dir. Parsing goes through etude.scorer.results / etude.scorer.audit
# (the single P5 definition): k = successful details, n = len(details) (= eval_time; unstable
# episodes are already excluded by RoboDojo).
PYTHONPATH="${ETUDE_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" "${PY}" - "${result_json}" "${record_dir}/etude_run.json" "${RESEARCH_METRICS:-}" "${RESEARCH_OUTPUTS:-}" <<PYEOF
import json, os, sys
from etude.scorer.results import load_result, research_metrics
from etude.scorer.audit import unstable_report
result_path, record_path, metrics_path, outputs_path = sys.argv[1:5]
record = {
    "mode": "${mode}", "task": "${task}", "seed": int("${seed}"), "eval_num": "${eval_num}",
    "exec_steps": ("${n}" and int("${n}")) or None, "vla_exec_steps_fixed": "${vla_fixed}" == "true",
    "ckpt_label": "${ckpt}", "action_type": "${action_type}",
    "backend": {"kind": "${backend_kind}", "host": "${backend_host}", "port": int("${backend_port}")},
    "robodojo_run_id": "${ROBODOJO_RUN_ID}", "result_dir": "${result_dir}" or None,
    "deploy_sha256": "${deploy_sha}", "etude_commit": "$(git_sha "${ETUDE_ROOT}")",
    "robodojo_commit": "$(git_sha "${robodojo_root}")", "xpolicylab_commit": "$(git_sha "${robodojo_root}/XPolicyLab")",
    "exit_code": int("${rc}"), "wall_s": int("${end_s}") - int("${start_s}"),
    "episode_log": "${ETUDE_LOG_DIR}/episodes.jsonl",
}
metrics = None
if result_path:
    entries = load_result(result_path, "${task}", int("${seed}")) or []
    with open(result_path, encoding="utf-8") as f:
        res = json.load(f)
    official = {"successes": sum(1 for e in entries if e.success), "trials": len(entries),
                "eval_time": res.get("eval_time"), "score": res.get("score"),
                "success_rate_official": res.get("success_rate"),
                "layouts": [{"layout_id": e.layout_id, "success": e.success, "score": e.score} for e in entries]}
    manifest = os.path.join(os.path.dirname(os.path.dirname(result_path)), "_resume_${ROBODOJO_RUN_ID}.json")
    if os.path.isfile(manifest):
        u = unstable_report(manifest)
        official.update(unstable_nums=u.unstable, unstable_rate=u.rate, unstable_review=u.review,
                        abandoned=u.abandoned, resume_manifest=manifest,
                        manifest_success_nums=u.successes, manifest_fail_nums=u.fails)
    else:
        official.update(unstable_nums=None, resume_manifest=None)
    record["official"] = official
    if entries:
        metrics = research_metrics(entries)
record["metrics"] = metrics
with open(record_path, "w", encoding="utf-8") as f:
    json.dump(record, f, indent=2, sort_keys=True)
if metrics is None:
    print("[run] no official result with trials > 0 found", file=sys.stderr)
    sys.exit(3)
print("[run] metrics " + json.dumps(metrics))
if metrics_path:
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f)
outs = {}
for item in """${outputs[*]:-}""".split():
    name, _, ref = item.partition("=")
    if name and ref:
        outs[name] = ref
if outputs_path and outs:
    with open(outputs_path, "w", encoding="utf-8") as f:
        json.dump(outs, f)
PYEOF
py_rc=$?
set -e
echo "[run] record: ${record_dir}/etude_run.json  result_dir: ${result_dir:-<none>}"
if [[ "${rc}" -ne 0 ]]; then exit "${rc}"; fi
exit "${py_rc}"
