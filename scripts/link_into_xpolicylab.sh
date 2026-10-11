#!/usr/bin/env bash
# Symlink the Etude XPolicyLab policy dir into RoboDojo's XPolicyLab (design §2 P2/P4, §4 目录树).
#
#   ~/Documents/RoboDojo/XPolicyLab/policy/Etude -> <etude checkout>/deploy/XPolicyLab/policy/Etude
#
# Usage: bash scripts/link_into_xpolicylab.sh [--source DIR] [--robodojo-root DIR] [--force] [--check]
#   --source DIR         policy dir to link (default: this checkout's deploy/XPolicyLab/policy/Etude)
#   --robodojo-root DIR  RoboDojo clone (default: $ROBODOJO_ROOT or ~/Documents/RoboDojo)
#   --force              replace an existing symlink that points elsewhere (never replaces a real dir)
#   --check              only report the current link target; exit 1 if it is not --source
# Prints the resolved link target on success.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ETUDE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source_dir="${ETUDE_ROOT}/deploy/XPolicyLab/policy/Etude"
robodojo_root="${ROBODOJO_ROOT:-${HOME}/Documents/RoboDojo}"
force="false"
check="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) source_dir="$2"; shift 2 ;;
    --robodojo-root) robodojo_root="$2"; shift 2 ;;
    --force) force="true"; shift ;;
    --check) check="true"; shift ;;
    -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "[link] unknown argument: $1" >&2; exit 2 ;;
  esac
done

source_dir="$(cd "${source_dir}" && pwd -P)"
for f in deploy.py deploy.yml model.py setup_eval_policy_server.sh setup_eval_env_client.sh; do
  [[ -f "${source_dir}/${f}" ]] || { echo "[link] missing ${source_dir}/${f}" >&2; exit 1; }
done
policy_root="${robodojo_root}/XPolicyLab/policy"
[[ -d "${policy_root}" ]] || { echo "[link] XPolicyLab policy dir not found: ${policy_root}" >&2; exit 1; }
link="${policy_root}/Etude"

current=""
if [[ -L "${link}" ]]; then
  current="$(readlink -f "${link}")"
elif [[ -e "${link}" ]]; then
  echo "[link] ${link} exists and is not a symlink; refusing to touch it" >&2
  exit 1
fi

if [[ "${check}" == "true" ]]; then
  echo "${current:-<none>}"
  [[ "${current}" == "${source_dir}" ]]
  exit $?
fi

if [[ "${current}" == "${source_dir}" ]]; then
  echo "${source_dir}"
  exit 0
fi
if [[ -n "${current}" && "${force}" != "true" ]]; then
  echo "[link] ${link} -> ${current}; pass --force to repoint it to ${source_dir}" >&2
  exit 1
fi
ln -sfn "${source_dir}" "${link}"
echo "[link] ${link} -> ${source_dir}" >&2
echo "${source_dir}"
