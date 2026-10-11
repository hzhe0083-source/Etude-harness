#!/usr/bin/env bash
# XPolicyLab deploy: policy server env = the Etude repo's own uv .venv (design §2 P2).
# Adapted from policy/Pi_05/install.sh. pyproject.toml / uv.lock are NOT modified.
# XPolicyLab is installed into the venv with `uv pip install -e`, which is outside the lock.
# The plain `uv run` of a later session does an inexact sync and keeps it; `uv sync --exact` would remove it.
set -euo pipefail

POLICY_DIR_REAL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ETUDE_ROOT="$(cd "${POLICY_DIR_REAL}/../../../.." && pwd)"
XPOLICYLAB_ROOT="${XPOLICYLAB_ROOT:-${HOME}/Documents/RoboDojo/XPolicyLab}"

echo "[Etude] ETUDE_ROOT=${ETUDE_ROOT}"
echo "[Etude] XPOLICYLAB_ROOT=${XPOLICYLAB_ROOT}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install via: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi
if [[ ! -f "${XPOLICYLAB_ROOT}/setup_policy_server.py" ]]; then
  echo "[Etude] XPolicyLab not found at ${XPOLICYLAB_ROOT} (set XPOLICYLAB_ROOT)" >&2
  exit 1
fi

cd "${ETUDE_ROOT}"
uv sync
# XPolicyLab's server-side deps (msgpack-numpy, pydantic, h5py, ...) and its packages.
uv pip install --python "${ETUDE_ROOT}/.venv/bin/python" -e "${XPOLICYLAB_ROOT}"
uv run python -c "import XPolicyLab, etude.adapter.xpl_model; print('XPolicyLab + etude ok')"
# The openpi websocket client is needed only for backend.kind=remote (design §8.1 item 8).
uv pip install --python "${ETUDE_ROOT}/.venv/bin/python" -e "${XPOLICYLAB_ROOT}/policy/Pi_05/openpi/packages/openpi-client" \
  || echo "[Etude] WARNING: openpi-client install failed; the remote backend will be unavailable"

bash "${ETUDE_ROOT}/scripts/link_into_xpolicylab.sh"
echo "[Etude] Installation finished. Server venv: ${ETUDE_ROOT}/.venv"
