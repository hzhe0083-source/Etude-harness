"""Etude XPolicyLab policy model (design §2 P2 row, §3.3, §5 M0).

XPolicyLab imports ``XPolicyLab.policy.Etude.model.Model`` inside the policy
server process (P2) and serves its public methods over websocket+msgpack.
The deploy.py forwarder calls them in this order: ``reset``, ``update_obs``,
``get_action``, ``update_obs`` (once per executed step), ``get_action`` and so on.

All logic lives in :class:`etude.adapter.xpl_model.EtudeModelCore` so it can be
reused without XPolicyLab on the path:

* ``mode: m0_requery``: ``PolicyBackend.infer`` returns an (H,14) chunk and
  ``get_action`` returns its first ``exec_steps`` rows as joint dicts.
* ``mode: harness``: ``etude.runner.NodeRunner`` produces each chunk.

``etude`` must be importable. setup_eval_policy_server.sh puts the Etude
checkout first on PYTHONPATH and activates the Etude .venv.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # the real policy server always has XPolicyLab on the path
    from XPolicyLab.model_template import ModelTemplate
except ImportError:  # demo / standalone import (python -m etude.adapter --demo)
    class ModelTemplate:  # type: ignore[no-redef]
        """Stand-in for XPolicyLab.model_template.ModelTemplate (same contract)."""

        def __init__(self):
            self.model = None

from etude.adapter.xpl_model import EtudeModelCore

_POLICY_DIR = Path(__file__).resolve().parent


class Model(ModelTemplate):
    """XPolicyLab ``Model`` for Etude (design §2 P2 row). Thin shell over :class:`EtudeModelCore`."""

    def __init__(self, model_cfg: dict[str, Any], **core_kwargs: Any):
        """Build from the deploy.yml dict plus overrides. ``core_kwargs`` (``backend=``,
        ``runner=``, ``policy_dir=``) are only used by demos."""
        super().__init__()
        core_kwargs.setdefault("policy_dir", _POLICY_DIR)
        self.core = EtudeModelCore(model_cfg, **core_kwargs)
        self.model = self.core
        # RoboDojo never resets after the last episode and the server is SIGTERMed (then
        # SIGKILLed ~1 s later): flush the open episode's record on SIGTERM / exit.
        self.core.install_exit_flush()

    def update_obs(self, obs):
        """Store one observation (design §3.3 ``self.hist``)."""
        self.core.update_obs(obs)

    def update_obs_batch(self, obs_list):
        """num_envs=1 only (design §8.2 F5). Any other size raises NotImplementedError."""
        self.core.update_obs_batch(obs_list)

    def get_action(self, **kwargs):
        """One chunk of RoboDojo action dicts, each checked by etude.adapter.action_check (design §3.3)."""
        return self.core.get_action()

    def get_action_batch(self, env_idx_list=None, **kwargs):
        """Disabled: deploy.yml sets eval_batch: false (design §8.2 F5)."""
        return self.core.get_action_batch(env_idx_list)

    def reset(self):
        """Finish the current episode and clear episode state (design §3.5 "reset() 清空回合态")."""
        self.core.reset()
