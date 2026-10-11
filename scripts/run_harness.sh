#!/usr/bin/env bash
# Harness evaluation entry (design §3.3 / §5 M1+): the same wrapper as run_m0.sh with --mode harness.
# Git-tracked entry for Research OS, for example:
#   research run --experiment E-<n> --kind evaluation ... -- scripts/run_harness.sh --task stack_bowls --seed 0 --eval-num 50
# Accepts every run_m0.sh option. --n N fixes the exec_steps of EVERY vla node to N
# (deploy.yml exec_steps + vla_exec_steps_fixed: true; design §6 control "M1 内 VLA 节点 exec_steps 固定为 N*").
# Without --n the planner's per-node stop.exec_steps is executed and deploy.yml exec_steps only sets
# the chunks_left divisor and the no-VLM fallback node.
# The planner (pi --mode rpc) and MCP server are started inside the policy server by etude.runner.NodeRunner.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
for a in "$@"; do
  if [[ "${a}" == "--mode" ]]; then echo "[run_harness] --mode is fixed to harness" >&2; exit 2; fi
done
exec bash "${SCRIPT_DIR}/run_m0.sh" --mode harness "$@"
