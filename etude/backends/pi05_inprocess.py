"""pi0.5 in-process via XPolicyLab's official ``Pi_05`` Model (design §8.1 item 8).

The official adapter loads openpi with ``create_trained_policy`` inside the
policy-server process. v1 does NOT use this path (P3 is a separate openpi
server so pi0.5 can live on a second GPU, design §8.1 item 1); this wrapper
exists for M0 parity checks on a machine that has openpi + JAX + the
checkpoint installed. Importing it never loads a model; constructing
:class:`Pi05InProcessBackend` does.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from etude.backends.base import PolicyBackend
from etude.core.actions import JOINT_KEYS
from etude.core.types import ObsFrame

#: Parent directory of the ``XPolicyLab`` package (read-only reference clone).
DEFAULT_ROBODOJO_DIR = Path.home() / "Documents/RoboDojo"

#: Official Pi_05 config (``policy/Pi_05/deploy.yml``).
DEFAULT_MODEL_CFG: dict[str, Any] = {
    "policy_name": "Pi_05",
    "action_type": "joint",
    "env_cfg_type": "arx_x5",
    "train_config_name": "pi05_base_aloha_full_sim_arx-x5_seed_0",
    "repo_id": "arx_x5_sim",
    "checkpoint_num": 59999,
}


def _import_pi05_model() -> Any:
    root = Path(os.environ.get("ROBODOJO_DIR", str(DEFAULT_ROBODOJO_DIR))).expanduser()
    if (root / "XPolicyLab").is_dir() and str(root) not in sys.path:
        sys.path.append(str(root))
    try:
        return importlib.import_module("XPolicyLab.policy.Pi_05.model")
    except Exception as e:  # ImportError for openpi/jax, or XPolicyLab missing
        raise NotImplementedError(
            "In-process pi0.5 needs XPolicyLab (parent dir on sys.path, or $ROBODOJO_DIR) and the vendored "
            "openpi package with JAX installed in this venv; neither is a dependency of etude. "
            f"Import failed: {type(e).__name__}: {e}. Use Pi05RemoteBackend (design §2 P3) instead."
        ) from e


class Pi05InProcessBackend(PolicyBackend):
    """Wraps XPolicyLab ``policy/Pi_05/model.py::Model`` (design §8.1 item 8).

    ``model_cfg`` must give the checkpoint (``model_path`` or ``ckpt_name``)
    and ``task_name``; the remaining keys default to :data:`DEFAULT_MODEL_CFG`.
    Raises NotImplementedError when XPolicyLab/openpi cannot be imported.
    """

    def __init__(self, model_cfg: Mapping[str, Any] | None = None, model: Any = None):
        cfg = {**DEFAULT_MODEL_CFG, **dict(model_cfg or {})}
        if model is None:
            mod = _import_pi05_model()
            model = mod.Model(cfg)
        self.model = model
        self.model_cfg = cfg

    def infer(self, obs: ObsFrame, prompt: str) -> np.ndarray:
        """``update_obs`` + ``get_action`` on the official Model, re-packed into
        ``(H, 14)`` (design §3.3 ``pi05.infer``). Pi_05 reads the prompt from
        ``obs["instruction"]``, so the node instruction is put there."""
        raw = obs.to_robodojo_obs()
        raw["instruction"] = str(prompt)
        self.model.update_obs(raw)
        acts = self.model.get_action()
        rows = []
        for a in acts:
            rows.append(np.concatenate([np.asarray(a[k], dtype=np.float64).reshape(-1) for k in JOINT_KEYS]))
        return np.stack(rows, axis=0)

    def reset(self) -> None:
        """Forward to ``Model.reset``."""
        self.model.reset()


__all__ = ["Pi05InProcessBackend", "DEFAULT_MODEL_CFG", "DEFAULT_ROBODOJO_DIR"]


def _demo() -> int:
    """Show the import failure path, then wrap a stub with the Pi_05 Model API."""
    from etude.fakes import fake_frame

    try:
        Pi05InProcessBackend({"task_name": "demo"})
        print("XPolicyLab Pi_05 imported (a model was loaded)")
    except NotImplementedError as e:
        print("in-process unavailable (expected here):", str(e).splitlines()[0][:160])

    class _StubModel:  # Pi_05 Model API: update_obs / get_action / reset
        def update_obs(self, obs: dict) -> None:
            self.obs = obs

        def get_action(self) -> list[dict]:
            s = self.obs["state"]
            return [{k: np.asarray(s[k]) for k in JOINT_KEYS} for _ in range(50)]

        def reset(self) -> None:
            pass

    be = Pi05InProcessBackend(model=_StubModel())
    chunk = be.infer(fake_frame(0), "pick up the red block")
    print("stub chunk", chunk.shape)
    return 0


def main(argv: Any = None) -> int:
    """CLI: ``python -m etude.backends.pi05_inprocess --demo``."""
    import argparse

    ap = argparse.ArgumentParser(prog="python -m etude.backends.pi05_inprocess")
    ap.add_argument("--demo", action="store_true")
    if ap.parse_args(argv).demo:
        return _demo()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
