"""Reusable logic behind ``deploy/XPolicyLab/policy/Etude/model.py`` (design §2 P2 row, §3.3, §5 M0).

``model.py`` is a thin XPolicyLab ``Model(ModelTemplate)`` shell that delegates
everything to :class:`EtudeModelCore` defined here. The shell exists because
XPolicyLab imports ``XPolicyLab.policy.<name>.model.Model``.

Modes (deploy.yml ``mode``)
---------------------------
* ``m0_requery`` (design §5 M0): every ``get_action`` runs one
  ``PolicyBackend.infer`` on the latest observation and returns the first
  ``exec_steps`` rows of the ``(H, 14)`` chunk as joint action dicts.
  This is plain π0.5 with a fixed requery step N.
* ``harness`` (design §3.3): the runner named in deploy.yml ``runner.class``
  receives every observation frame and produces each chunk. The default is
  :class:`etude.adapter.harness_build.HarnessRunner`, which wraps
  ``etude.runner.NodeRunner``, its components and the MCP server. The runner
  may block in ``get_action`` while the planner runs, up to ``request_timeout_s``.

Runner contract (see :class:`RunnerProtocol`)
---------------------------------------------
The runner is built with ``Class.from_config(cfg)``, or ``Class(cfg)`` when
there is no ``from_config``, where ``cfg`` is the resolved deploy config. It must provide:
``update_obs(frame: ObsFrame) -> None`` (called once per env step, oldest first),
``get_action() -> list[dict]`` (one chunk of RoboDojo action dicts; joint and
ee dicts may alternate) and ``reset() -> None`` (finish and flush the episode's
trace, then clear episode state).

Every returned dict passes :func:`etude.adapter.action_check.assert_action_ok`
before it leaves this process (design §8.1 item 7).

Hash audit (design §2 边界落地)
-------------------------------
:func:`deploy_sha256` and :func:`verify_deploy_hash` compare the frozen
``deploy.py`` with ``DEPLOY_SHA256`` stored next to it.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import atexit
import os
import signal
import sys
import threading
import time
import weakref
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

import numpy as np

from etude import REPO_ROOT
from etude.adapter.action_check import ARX_X5, RobotSpec, assert_action_ok
from etude.core.actions import chunk_to_joint_actions, joint_action_dict
from etude.core.interfaces import PolicyBackend
from etude.core.types import ACTION_DIM, CHUNK_LEN, DEFAULT_EXEC_STEPS, ObsFrame

ADAPTER_MODES: tuple[str, ...] = ("harness", "m0_requery")
#: In-repo location of the XPolicyLab policy directory (symlinked into XPolicyLab/policy/Etude).
POLICY_DIR: Path = REPO_ROOT / "deploy" / "XPolicyLab" / "policy" / "Etude"
DEPLOY_HASH_FILE = "DEPLOY_SHA256"
#: Harness-mode runner: NodeRunner plus components plus the MCP server (etude/adapter/harness_build.py).
DEFAULT_RUNNER = "etude.adapter.harness_build:HarnessRunner"

#: Fallback copy of frozen/forbidden.yaml ``allowed_obs``. The YAML file is authoritative.
_ALLOWED_OBS_FALLBACK: dict[str, list[str]] = {
    "vision": ["cam_head", "cam_left_wrist", "cam_right_wrist"],
    "state": [
        "left_arm_joint_state", "left_ee_pose", "left_ee_joint_state",
        "right_arm_joint_state", "right_ee_pose", "right_ee_joint_state",
    ],
    "additional_info": ["frequency", "step_lim", "gripper_width_measured"],
}


def _log(msg: str) -> None:
    print(f"[Etude] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def load_deploy_cfg(path: str | Path | None = None) -> dict[str, Any]:
    """Read ``deploy.yml`` (default: the in-repo one) (design §2 P2, §4 目录树)."""
    import yaml

    p = Path(path) if path is not None else POLICY_DIR / "deploy.yml"
    with open(p, encoding="utf-8") as f:
        return dict(yaml.safe_load(f) or {})


def _set_dotted(cfg: dict[str, Any], dotted: str, value: Any) -> None:
    cur = cfg
    parts = dotted.split(".")
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def resolve_cfg(model_cfg: Mapping[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Normalize the config XPolicyLab passes to ``Model.__init__`` (design §2 P2).

    XPolicyLab ``--overrides key=value`` can only set flat keys, so dotted keys such
    as ``backend.host=10.0.0.2`` are folded into nested dicts here. Then the
    ``ETUDE_*`` environment variables, which ``scripts/run_*.sh`` export, take
    precedence: ``ETUDE_MODE``, ``ETUDE_EXEC_STEPS``, ``ETUDE_BACKEND_KIND``,
    ``ETUDE_BACKEND_HOST``, ``ETUDE_BACKEND_PORT``, ``ETUDE_LOG_DIR`` and
    ``ETUDE_VLA_EXEC_STEPS_FIXED``.
    Defaults are filled in and ``mode``/``exec_steps`` are validated.
    """
    env = os.environ if env is None else env
    cfg: dict[str, Any] = {}
    dotted: list[tuple[str, Any]] = []
    for k, v in dict(model_cfg).items():
        if "." in str(k):
            dotted.append((str(k), v))
        else:
            cfg[str(k)] = copy.deepcopy(v)
    for k, v in dotted:
        _set_dotted(cfg, k, v)
    env_map = {
        "ETUDE_MODE": ("mode", str),
        "ETUDE_EXEC_STEPS": ("exec_steps", int),
        "ETUDE_BACKEND_KIND": ("backend.kind", str),
        "ETUDE_BACKEND_HOST": ("backend.host", str),
        "ETUDE_BACKEND_PORT": ("backend.port", int),
        "ETUDE_LOG_DIR": ("log_dir", str),
        "ETUDE_VLA_EXEC_STEPS_FIXED": ("vla_exec_steps_fixed", lambda v: str(v).strip().lower() in ("1", "true", "yes")),
    }
    for var, (key, typ) in env_map.items():
        if env.get(var):
            _set_dotted(cfg, key, typ(env[var]))
    cfg.setdefault("mode", "harness")
    cfg.setdefault("exec_steps", DEFAULT_EXEC_STEPS)
    cfg.setdefault("vla_exec_steps_fixed", False)
    cfg.setdefault("backend", {"kind": "remote"})
    cfg.setdefault("runner", {"class": DEFAULT_RUNNER})
    cfg.setdefault("deploy_hash_check", "strict")
    cfg.setdefault("history_len", 64)
    cfg.setdefault("log_dir", "")
    if cfg["mode"] not in ADAPTER_MODES:
        raise ValueError(f"deploy.yml mode must be one of {ADAPTER_MODES}, got {cfg['mode']!r}")
    cfg["exec_steps"] = int(cfg["exec_steps"])
    if not 1 <= cfg["exec_steps"] <= CHUNK_LEN:
        raise ValueError(f"exec_steps must be in [1, {CHUNK_LEN}], got {cfg['exec_steps']}")
    if cfg.get("eval_batch"):
        raise ValueError("Etude requires eval_batch: false (design §2 P2 / §8.2 F5)")
    return cfg


# ---------------------------------------------------------------------------
# deploy.py hash audit
# ---------------------------------------------------------------------------


def deploy_sha256(policy_dir: str | Path = POLICY_DIR) -> str:
    """sha256 hex of ``<policy_dir>/deploy.py`` (design §2 边界落地 "冻结并记哈希")."""
    return hashlib.sha256((Path(policy_dir) / "deploy.py").read_bytes()).hexdigest()


def recorded_deploy_sha256(policy_dir: str | Path = POLICY_DIR) -> str | None:
    """Hash recorded in ``<policy_dir>/DEPLOY_SHA256`` (first token), or None."""
    p = Path(policy_dir) / DEPLOY_HASH_FILE
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8").strip()
    return text.split()[0] if text else None


def verify_deploy_hash(policy_dir: str | Path = POLICY_DIR, mode: str = "strict") -> dict[str, Any]:
    """Compare deploy.py with its recorded hash (design §2, §3.5 "改打分或汇总" row).

    ``mode``: ``strict`` raises RuntimeError on a mismatch or a missing
    record, ``warn`` only logs, and ``off`` skips the check. Returns
    ``{"ok", "actual", "recorded", "mode"}``.
    """
    if mode == "off":
        return {"ok": None, "actual": None, "recorded": None, "mode": mode}
    actual = deploy_sha256(policy_dir)
    recorded = recorded_deploy_sha256(policy_dir)
    ok = recorded is not None and actual == recorded
    res = {"ok": ok, "actual": actual, "recorded": recorded, "mode": mode}
    if not ok:
        msg = (f"deploy.py hash audit failed: actual {actual} != recorded {recorded} "
               f"({Path(policy_dir) / DEPLOY_HASH_FILE}); deploy.py is frozen (design §2)")
        if mode == "strict":
            raise RuntimeError(msg)
        _log("WARNING " + msg)
    return res


# ---------------------------------------------------------------------------
# component factories
# ---------------------------------------------------------------------------


def import_object(path: str) -> Any:
    """Import ``"pkg.module:Attr.sub"`` (or ``"pkg.module.Attr"``) for config-driven plug-ins.

    Only ``etude.*`` modules are accepted. A deploy.yml edit therefore cannot
    pull code from outside the repo, where ``etude.frozen.ast_check`` would not
    scan it (design §3.5).
    """
    if ":" in path:
        mod_name, attr = path.split(":", 1)
    else:
        mod_name, _, attr = path.rpartition(".")
    if mod_name != "etude" and not mod_name.startswith("etude."):
        raise ValueError(f"plug-in {path!r} must live in the etude package (design §3.5)")
    obj: Any = importlib.import_module(mod_name)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _construct(cls: Any, cfg: Mapping[str, Any]) -> Any:
    if hasattr(cls, "from_config"):
        return cls.from_config(dict(cfg))
    return cls(dict(cfg))


def backend_spec(bcfg: Mapping[str, Any]) -> dict[str, Any]:
    """Translate deploy.yml ``backend`` into an ``etude.backends.base.make_backend`` spec (design §2 P3).

    ``remote`` maps to ``pi05_remote(host, port, api_key, expect_horizon)``,
    ``inprocess`` maps to ``pi05_inprocess(model_cfg)`` (the official Pi_05
    Model in this process), and ``fake`` maps to ``FakePolicy(horizon)``.
    """
    kind = str(bcfg.get("kind", "remote"))
    if kind in ("remote", "pi05_remote"):
        spec: dict[str, Any] = {"kind": "pi05_remote", "host": str(bcfg.get("host", "localhost")),
                                "port": int(bcfg.get("port", 8000))}
        if bcfg.get("api_key"):
            spec["api_key"] = str(bcfg["api_key"])
        if bcfg.get("horizon"):
            spec["expect_horizon"] = int(bcfg["horizon"])
        return spec
    if kind in ("inprocess", "pi05_inprocess"):
        model_cfg = {k: bcfg[k] for k in ("train_config_name", "repo_id", "checkpoint_num", "task_name") if k in bcfg}
        if bcfg.get("checkpoint_dir"):
            model_cfg["model_path"] = str(Path(str(bcfg["checkpoint_dir"])).expanduser())
        model_cfg.setdefault("task_name", "etude")
        return {"kind": "pi05_inprocess", "model_cfg": model_cfg}
    if kind == "fake":
        return {"kind": "fake", "horizon": int(bcfg.get("horizon", CHUNK_LEN)), **dict(bcfg.get("fake_kwargs") or {})}
    raise ValueError(f"unknown backend.kind {kind!r} (remote | inprocess | fake)")


def make_backend(bcfg: Mapping[str, Any]) -> PolicyBackend:
    """Build the frozen-VLA backend from deploy.yml ``backend`` (design §2 P3, §8.1 item 8).

    ``class: "module:Class"`` wins when set (via ``from_config(bcfg)`` or
    ``Class(bcfg)``). Otherwise :func:`backend_spec` feeds
    ``etude.backends.base.make_backend``. The remote backend connects lazily,
    on the first ``infer``.
    """
    if bcfg.get("class"):
        return _construct(import_object(str(bcfg["class"])), bcfg)
    spec = backend_spec(bcfg)
    try:
        from etude.backends.base import make_backend as _factory
    except ImportError as e:
        raise NotImplementedError(f"etude.backends.base.make_backend not importable: {e}") from e
    return _factory(spec)


@runtime_checkable
class RunnerProtocol(Protocol):
    """What harness mode needs from ``etude.runner.NodeRunner`` (design §3.3)."""

    def update_obs(self, frame: ObsFrame) -> None: ...

    def get_action(self) -> list[dict[str, Any]]: ...

    def reset(self) -> None: ...


def make_runner(cfg: Mapping[str, Any]) -> RunnerProtocol:
    """Build the harness node runner from ``cfg["runner"]["class"]`` (design §3.3).

    Raises NotImplementedError if the class cannot be imported. The default
    :data:`DEFAULT_RUNNER` is :class:`etude.adapter.harness_build.HarnessRunner`.
    It builds ``etude.runner.NodeRunner`` together with its components and the
    MCP server.
    """
    path = str((cfg.get("runner") or {}).get("class") or DEFAULT_RUNNER)
    try:
        cls = import_object(path)
    except (ImportError, AttributeError) as e:
        raise NotImplementedError(
            f"harness mode needs {path} (design §3.3 NodeRunner), which is not importable: {e}"
        ) from e
    runner = _construct(cls, cfg)
    present = {"update_obs": getattr(runner, "update_obs", None), "get_action": getattr(runner, "get_action", None),
               "reset": getattr(runner, "reset", None)}
    missing = [m for m, fn in present.items() if not callable(fn)]
    if missing:
        raise TypeError(f"{path} lacks runner methods {missing} (see etude.adapter.xpl_model.RunnerProtocol)")
    return runner


# ---------------------------------------------------------------------------
# observation history
# ---------------------------------------------------------------------------


def load_allowed_obs() -> dict[str, list[str]]:
    """``allowed_obs`` from frozen/forbidden.yaml, falling back to a built-in copy (design §2 "允许")."""
    try:
        from etude.core.config import load_forbidden

        allowed = load_forbidden().get("allowed_obs")
        if isinstance(allowed, Mapping) and allowed:
            return {k: list(v) for k, v in allowed.items()}
    except Exception as e:  # noqa: BLE001 - config is optional at runtime
        _log(f"allowed_obs fallback ({e})")
    return copy.deepcopy(_ALLOWED_OBS_FALLBACK)


def filter_obs(obs: Mapping[str, Any], allowed: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """Keep only allow-listed observation content (design §2 "允许", §3.5). Defence in depth:
    the harness never sees anything outside ``allowed_obs`` even if the client sends more."""
    vision = obs.get("vision") or {}
    state = obs.get("state") or {}
    info = obs.get("additional_info") or {}
    return {
        "vision": {k: vision[k] for k in allowed.get("vision", []) if k in vision},
        "state": {k: state[k] for k in allowed.get("state", []) if k in state},
        "additional_info": {k: info[k] for k in allowed.get("additional_info", []) if k in info},
        "instruction": obs.get("instruction") or "",
    }


class ObsHistory:
    """Episode observation history (design §3.3 ``self.hist``).

    Keeps a rolling window of the last ``maxlen`` full frames. It also keeps every
    frame tagged as a chunk boundary, which is where nodes start and end, so the
    verifier always has the node-start frame. :meth:`frames` returns both merged,
    oldest first.
    """

    def __init__(self, maxlen: int = 64):
        self.maxlen = max(1, int(maxlen))
        self._recent: deque[ObsFrame] = deque(maxlen=self.maxlen)
        self._boundary: dict[int, ObsFrame] = {}

    def append(self, frame: ObsFrame) -> None:
        self._recent.append(frame)

    def mark_boundary(self) -> None:
        """Pin the latest frame (called at each ``get_action``)."""
        if self._recent:
            f = self._recent[-1]
            self._boundary[f.t] = f

    def latest(self) -> ObsFrame:
        if not self._recent:
            raise AssertionError("update_obs first!")
        return self._recent[-1]

    def frames(self) -> list[ObsFrame]:
        merged = dict(self._boundary)
        for f in self._recent:
            merged[f.t] = f
        return [merged[t] for t in sorted(merged)]

    def __len__(self) -> int:
        return len(self._recent)

    def clear(self) -> None:
        self._recent.clear()
        self._boundary.clear()


# ---------------------------------------------------------------------------
# the model core
# ---------------------------------------------------------------------------


@dataclass
class EpisodeStats:
    """Per-episode counters for M0 wall-clock / t_step measurement (design §5 M0 卡点)."""

    episode: int = 0
    mode: str = ""
    task_name: str | None = None
    instruction: str = ""
    exec_steps: int = 0
    steps_observed: int = 0
    chunks: int = 0
    actions_returned: int = 0
    action_types: dict[str, int] = field(default_factory=dict)
    hold_fallbacks: int = 0
    infer_s: list[float] = field(default_factory=list)
    get_action_s: list[float] = field(default_factory=list)
    wall_s: float = 0.0
    step_lim: int | None = None

    def summary(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("infer_s")
        d.pop("get_action_s")
        d["infer_s_mean"] = float(np.mean(self.infer_s)) if self.infer_s else None
        d["get_action_s_mean"] = float(np.mean(self.get_action_s)) if self.get_action_s else None
        d["get_action_s_max"] = float(np.max(self.get_action_s)) if self.get_action_s else None
        return d


#: cores whose open episode is flushed on SIGTERM / interpreter exit (one handler for all)
_EXIT_CORES: "weakref.WeakSet[EtudeModelCore]" = weakref.WeakSet()
_EXIT_INSTALLED = {"atexit": False, "sigterm": False}


def _flush_all(why: str) -> None:
    for core in list(_EXIT_CORES):
        core.flush_open_episode(why)


def _on_sigterm_factory(prev: Any) -> Callable[[int, Any], None]:
    def _on_sigterm(signum: int, frame: Any) -> None:
        _flush_all("SIGTERM")
        if callable(prev):
            prev(signum, frame)
            return
        # default action: terminate with the signal (after the flush)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)

    return _on_sigterm


class EtudeModelCore:
    """Everything ``model.py``'s ``Model`` does (design §2 P2 row, §3.3, §5 M0).

    ``model_cfg`` is the dict that XPolicyLab passes to ``Model.__init__``: the
    deploy.yml contents plus overrides. Tests do not exist in this repo, but
    demos inject ``backend=`` or ``runner=`` to bypass the factories.
    """

    def __init__(
        self,
        model_cfg: Mapping[str, Any],
        *,
        policy_dir: str | Path | None = POLICY_DIR,
        backend: PolicyBackend | None = None,
        runner: RunnerProtocol | None = None,
        spec: RobotSpec = ARX_X5,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cfg = resolve_cfg(model_cfg)
        self.mode: str = self.cfg["mode"]
        self.exec_steps: int = int(self.cfg["exec_steps"])
        self.spec = spec
        self._clock = clock
        self.hash_audit = (
            verify_deploy_hash(policy_dir, str(self.cfg["deploy_hash_check"])) if policy_dir is not None else None
        )
        self.allowed_obs = load_allowed_obs()
        self.history = ObsHistory(int(self.cfg["history_len"]))
        self.backend: PolicyBackend | None = None
        self.runner: RunnerProtocol | None = None
        if self.mode == "m0_requery":
            self.backend = backend if backend is not None else make_backend(self.cfg.get("backend") or {})
        else:
            self.runner = runner if runner is not None else make_runner(self.cfg)
        self._episode = 0
        self._n_obs = 0
        self._t0: float | None = None
        self.stats = EpisodeStats(mode=self.mode, exec_steps=self.exec_steps, task_name=self.cfg.get("task_name"))
        self.finished: list[dict[str, Any]] = []
        self._flush_lock = threading.Lock()
        _log(f"mode={self.mode} exec_steps={self.exec_steps} task={self.cfg.get('task_name')} "
             f"deploy_hash={(self.hash_audit or {}).get('actual')}")

    # -- XPolicyLab API ------------------------------------------------------
    def update_obs(self, obs: Mapping[str, Any]) -> None:
        """Store one env observation (design §3.3 "obs 已由 update_obs 存入 self.hist").

        ``t`` is the number of ``update_obs`` calls since reset minus one. The deploy
        loop calls update_obs once at episode start and then once after every
        executed action except the one that ends a chunk; that action's
        observation arrives at the start of the next chunk. So ``t`` equals the
        number of ``take_action`` calls before this observation.
        """
        if self._t0 is None:
            self._t0 = self._clock()
        frame = ObsFrame.from_robodojo_obs(filter_obs(obs, self.allowed_obs), t=self._n_obs)
        self._n_obs += 1
        self.history.append(frame)
        self.stats.steps_observed = frame.t
        self.stats.instruction = frame.instruction or self.stats.instruction
        if frame.step_lim is not None:
            self.stats.step_lim = frame.step_lim
        if self.runner is not None:
            self.runner.update_obs(frame)

    def update_obs_batch(self, obs_list: Sequence[Mapping[str, Any]]) -> None:
        """Single-env only (design §8.2 F5: eval_batch:false)."""
        if len(obs_list) != 1:
            raise NotImplementedError("Etude supports num_envs=1 only (eval_batch: false)")
        self.update_obs(obs_list[0])

    def get_action(self) -> list[dict[str, Any]]:
        """Return one chunk of checked RoboDojo action dicts (design §3.3 ``get_action``)."""
        t_start = self._clock()
        latest = self.history.latest()
        self.history.mark_boundary()
        if self.mode == "m0_requery":
            actions = self._m0_actions(latest)
        else:
            assert self.runner is not None
            actions = list(self.runner.get_action())
        if not actions:
            # An empty chunk would make deploy.py spin without stepping. Hold for one step instead.
            self.stats.hold_fallbacks += 1
            _log(f"WARNING empty chunk at t={latest.t}; returning a 1-step hold")
            actions = [hold_action(latest)]
        out: list[dict[str, Any]] = []
        for i, a in enumerate(actions):
            kind = assert_action_ok(a, self.spec, index=i)
            self.stats.action_types[kind] = self.stats.action_types.get(kind, 0) + 1
            out.append({k: np.asarray(v, dtype=np.float32).reshape(-1) for k, v in a.items()})
        self.stats.chunks += 1
        self.stats.actions_returned += len(out)
        self.stats.get_action_s.append(self._clock() - t_start)
        return out

    def get_action_batch(self, env_idx_list: Sequence[int] | None = None) -> list[list[dict[str, Any]]]:
        """Not supported (design §8.2 F5: eval_batch:false)."""
        raise NotImplementedError("Etude supports eval_batch: false only; get_action_batch is disabled")

    def reset(self) -> dict[str, Any] | None:
        """Finish the current episode (log stats, ``runner.reset()``/``backend.reset()``) and
        clear all episode state (design §3.5 "reset() 清空回合态").

        XPolicyLab calls reset twice per episode: once from the env and once from
        deploy.py. A reset with no observations since the last one is a no-op.
        Returns the finished episode summary, or None.
        """
        summary = None
        if self._n_obs > 0:
            self.stats.wall_s = (self._clock() - self._t0) if self._t0 is not None else 0.0
            summary = self.stats.summary()
            self.finished.append(summary)
            self._write_log(summary)
            if self.runner is not None:
                self.runner.reset()
            if self.backend is not None:
                self.backend.reset()
            self._episode += 1
        self.history.clear()
        self._n_obs = 0
        self._t0 = None
        self.stats = EpisodeStats(episode=self._episode, mode=self.mode, exec_steps=self.exec_steps,
                                  task_name=self.cfg.get("task_name"))
        return summary

    # -- process exit --------------------------------------------------------
    def install_exit_flush(self) -> bool:
        """Flush the open episode on SIGTERM and at interpreter exit (design §3.3
        trace/记忆写入 need every episode's record). RoboDojo only calls ``reset``
        at the start of an episode, and run_policy_eval.sh SIGTERMs the policy
        server (SIGKILL ~1 s later) after the last one, so without this the last
        episode's ``episodes.jsonl`` line and ``EpisodeRecord`` are lost.
        Returns True if the SIGTERM handler is installed (main thread only)."""
        _EXIT_CORES.add(self)
        if not _EXIT_INSTALLED["atexit"]:
            atexit.register(_flush_all, "atexit")
            _EXIT_INSTALLED["atexit"] = True
        if _EXIT_INSTALLED["sigterm"]:
            return True
        if threading.current_thread() is not threading.main_thread():
            _log("WARNING exit flush: not on the main thread; SIGTERM handler not installed (atexit only)")
            return False
        prev = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _on_sigterm_factory(prev if callable(prev) else None))
        _EXIT_INSTALLED["sigterm"] = True
        return True

    def flush_open_episode(self, why: str = "exit") -> dict[str, Any] | None:
        """Finish the open episode now (same as :meth:`reset`), at most once at a time;
        never raises (it runs inside a signal handler / atexit)."""
        if not self._flush_lock.acquire(blocking=False):
            return None
        try:
            if self._n_obs > 0:
                _log(f"flushing the open episode on {why}")
                return self.reset()
        except Exception as e:  # noqa: BLE001 - best effort during shutdown
            _log(f"WARNING episode flush on {why} failed: {e!r}")
        finally:
            self._flush_lock.release()
        return None

    # -- internals -----------------------------------------------------------
    def _m0_actions(self, frame: ObsFrame) -> list[dict[str, Any]]:
        """design §5 M0: ``pi05.infer(hist[-1])[:N]`` as joint dicts."""
        assert self.backend is not None
        prompt = frame.instruction or str(self.cfg.get("instruction") or "")
        t0 = self._clock()
        chunk = np.asarray(self.backend.infer(frame, prompt), dtype=np.float64)
        self.stats.infer_s.append(self._clock() - t0)
        if chunk.ndim != 2 or chunk.shape[1] != ACTION_DIM or chunk.shape[0] < 1:
            raise ValueError(f"backend returned chunk of shape {chunk.shape}, expected (H,{ACTION_DIM})")
        if not np.all(np.isfinite(chunk)):
            raise ValueError("backend returned non-finite actions")
        return chunk_to_joint_actions(chunk, n=min(self.exec_steps, chunk.shape[0]))

    def _write_log(self, summary: Mapping[str, Any]) -> None:
        line = json.dumps(summary, sort_keys=True, default=str)
        _log("episode " + line)
        log_dir = str(self.cfg.get("log_dir") or "")
        if not log_dir:
            return
        try:
            p = Path(log_dir).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            with open(p / "episodes.jsonl", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            _log(f"WARNING cannot write episode log to {log_dir}: {e}")


def hold_action(frame: ObsFrame) -> dict[str, Any]:
    """Joint dict that re-commands the current joints and gripper echo (one safe step)."""
    return joint_action_dict(frame.joint_vector())


__all__ = [
    "ADAPTER_MODES", "DEFAULT_RUNNER", "DEPLOY_HASH_FILE", "POLICY_DIR", "EpisodeStats", "EtudeModelCore", "ObsHistory",
    "RunnerProtocol", "deploy_sha256", "filter_obs", "hold_action", "import_object", "load_allowed_obs",
    "load_deploy_cfg", "backend_spec", "make_backend", "make_runner", "recorded_deploy_sha256", "resolve_cfg",
    "verify_deploy_hash",
]
