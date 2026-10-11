#!/bin/bash
# Etude policy server (P2), design §2. Adapted from policy/Pi_05/setup_eval_policy_server.sh.
# Same positional contract as Pi_05, which RoboDojo scripts/internal/run_policy_eval.sh relies on.
# Differences from Pi_05:
#   * the Python env is the Etude repo's .venv, not openpi's. Arg 8 is "uv" or an explicit venv/project path.
#   * the Etude checkout is resolved through the symlink and put first on PYTHONPATH,
#     so a Research OS detached worktree runs its own etude/ code.
#   * the ETUDE_* environment variables (exported by scripts/run_m0.sh and run_harness.sh)
#     become --overrides.
set -euo pipefail

bench_name=$1
task_name=$2
ckpt_name=$3
env_cfg_type=$4
action_type=$5
seed=$6
policy_gpu_id=$7
policy_uv_env=${8:-uv}
policy_server_port=$9
policy_server_host=${10:-"localhost"}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"        # logical: XPolicyLab/policy/Etude
SCRIPT_DIR_REAL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" # physical: <etude>/deploy/XPolicyLab/policy/Etude
XPL_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BENCH_ROOT="$(cd "${XPL_ROOT}/.." && pwd)"
UTILS_DIR="${XPL_ROOT}/utils"
ETUDE_ROOT="${ETUDE_ROOT:-$(cd "${SCRIPT_DIR_REAL}/../../../.." && pwd)}"

policy_name="$(basename "${SCRIPT_DIR}")"
yaml_file="${SCRIPT_DIR}/deploy.yml"

action_dim=$(bash "${UTILS_DIR}/get_action_dim.sh" "${BENCH_ROOT}" "${env_cfg_type}")

echo "[SERVER] policy=${policy_name}, task=${task_name}, port=${policy_server_port}, action_dim=${action_dim}"
echo "[SERVER] etude checkout: ${ETUDE_ROOT}"

# Resolve the venv: explicit path > ETUDE_VENV > <checkout>/.venv > main worktree's .venv
resolve_venv() {
    local raw=$1
    if [[ "${raw}" != "uv" && "${raw}" != "etude_repo" ]]; then
        if [[ -f "${raw}/bin/activate" ]]; then echo "${raw}"; return; fi
        if [[ -f "${raw}/.venv/bin/activate" ]]; then echo "${raw}/.venv"; return; fi
        echo "[SERVER][ERROR] not a venv or project with .venv: ${raw}" >&2; return 1
    fi
    if [[ -n "${ETUDE_VENV:-}" ]]; then echo "${ETUDE_VENV}"; return; fi
    if [[ -f "${ETUDE_ROOT}/.venv/bin/activate" ]]; then echo "${ETUDE_ROOT}/.venv"; return; fi
    local common
    common="$(git -C "${ETUDE_ROOT}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    if [[ -n "${common}" && -f "${common}/../.venv/bin/activate" ]]; then
        (cd "${common}/../.venv" && pwd); return
    fi
    echo "[SERVER][ERROR] Etude .venv not found (set ETUDE_VENV or run install.sh)" >&2
    return 1
}

VENV="$(resolve_venv "${policy_uv_env}")"
echo "[SERVER] Activating Etude venv: ${VENV}"
# shellcheck disable=SC1091
source "${VENV}/bin/activate"
PYTHON_BIN="$(command -v python)"
echo "[SERVER] Using python: ${PYTHON_BIN}"

# ETUDE_* -> deploy.yml overrides (flat keys; dotted keys are folded by etude.adapter.xpl_model.resolve_cfg)
etude_overrides=()
[[ -n "${ETUDE_MODE:-}" ]] && etude_overrides+=("mode=${ETUDE_MODE}")
[[ -n "${ETUDE_EXEC_STEPS:-}" ]] && etude_overrides+=("exec_steps=${ETUDE_EXEC_STEPS}")
[[ -n "${ETUDE_BACKEND_KIND:-}" ]] && etude_overrides+=("backend.kind=${ETUDE_BACKEND_KIND}")
[[ -n "${ETUDE_BACKEND_HOST:-}" ]] && etude_overrides+=("backend.host=${ETUDE_BACKEND_HOST}")
[[ -n "${ETUDE_BACKEND_PORT:-}" ]] && etude_overrides+=("backend.port=${ETUDE_BACKEND_PORT}")
[[ -n "${ETUDE_LOG_DIR:-}" ]] && etude_overrides+=("log_dir=${ETUDE_LOG_DIR}")

exec env \
    PYTHONUNBUFFERED=1 \
    PYTHONWARNINGS=ignore::UserWarning \
    PYTHONPATH="${ETUDE_ROOT}:${BENCH_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
    CUDA_VISIBLE_DEVICES="${policy_gpu_id}" \
    "${PYTHON_BIN}" "${XPL_ROOT}/setup_policy_server.py" \
        --config_path "${yaml_file}" \
        --overrides \
            port="${policy_server_port}" \
            host="${policy_server_host}" \
            bench_name="${bench_name}" \
            task_name="${task_name}" \
            ckpt_name="${ckpt_name}" \
            env_cfg_type="${env_cfg_type}" \
            seed="${seed}" \
            policy_name="${policy_name}" \
            action_type="${action_type}" \
            action_dim="${action_dim}" \
            "${etude_overrides[@]}"
