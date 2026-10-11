"""Etude XPolicyLab deploy forwarder (design §2 P4 row, §2 "边界落地", §8.1 items 3 and 5).

FROZEN FILE: HASH AUDIT
-----------------------
This file runs inside the RoboDojo evaluation client (P4), in the same process
as the official judge, and is handed the live environment object ``TASK_ENV``.
It is therefore frozen. Its sha256 is recorded in ``DEPLOY_SHA256`` next to it.
* The policy server (``model.py``, via ``etude.adapter.xpl_model.verify_deploy_hash``)
  refuses to start when the hash differs (``deploy_hash_check: strict`` in deploy.yml).
* ``scripts/run_m0.sh`` and ``scripts/run_harness.sh`` copy the hash into the run record.
* ``uv run python -m etude.adapter --deploy-hash`` prints the current value.
Editing this file means writing a new hash, which needs a human decision
(a Research OS Decision). An agent must not refresh ``DEPLOY_SHA256`` on its own.

What differs from the official ``XPolicyLab/policy/demo_policy/deploy.py``
-------------------------------------------------------------------------
The ``eval_one_episode`` loop is copied verbatim. The only change is that
every ``obs`` passes through ``_augment`` before ``update_obs``. ``_augment``
holds the two allow-listed enhancement lines:
  1. ``additional_info["step_lim"] = TASK_ENV.step_lim``. This is the
     episode step budget; the harness uses it for budget/chunks_left.
  2. ``additional_info["gripper_width_measured"]``. This is the measured
     gripper opening per arm from ``robot_manager.get_end_effector_real_val``:
     element 0 of the gripper joint positions, the same element RoboDojo's
     ``process_control_info`` reads as the current gripper position. It is
     needed because ``*_ee_joint_state`` is only the command echo (design §8.1
     item 3). The robot handle comes from ``robot_manager.get_robot_by_arm_name``
     and is only passed back. Nothing is read off it.
``eval_one_episode_batch`` raises NotImplementedError: deploy.yml forces
``eval_batch: false``, so RoboDojo uses num_envs=1.

TASK_ENV allow-list (design §2; frozen/forbidden.yaml ``deploy_task_env_allow``)
--------------------------------------------------------------------------------
get_obs, take_action, is_episode_end, step_lim,
robot_manager.get_end_effector_real_val, robot_manager.get_robot_by_arm_name
(the last one is the documented extension). The remaining allow-listed names
(get_running_env_idx_list, get_obs_batch, take_action_batch) are not used
because the batch path is disabled. No other attribute of TASK_ENV may be
read here. ``etude.frozen.ast_check`` enforces this.
"""

_ARMS = ("left", "right")


def _augment(TASK_ENV, obs):
    """Add the two allow-listed fields to ``obs["additional_info"]`` (design §2 P4, §8.1 item 3)."""
    info = obs.setdefault("additional_info", {})
    env_idx = int(obs.get("env_idx", 0))
    info["step_lim"] = int(TASK_ENV.step_lim)  # enhancement 1
    info["gripper_width_measured"] = {arm: float(TASK_ENV.robot_manager.get_end_effector_real_val(TASK_ENV.robot_manager.get_robot_by_arm_name(arm + "_arm"), [env_idx])[env_idx][0]) for arm in _ARMS}  # enhancement 2
    return obs


def eval_one_episode(TASK_ENV, model_client):
    """Official demo forwarder loop. Every obs goes through ``_augment`` (design §2 P4)."""

    model_client.call(func_name="reset") # reset policy

    while not TASK_ENV.is_episode_end(): # Check whether the episode ends
        obs = _augment(TASK_ENV, TASK_ENV.get_obs()) # Get Observation
        model_client.call(func_name="update_obs", obs=obs)  # Update Observation
        actions = model_client.call(func_name="get_action") # Get Action according to observation chunk

        for action_idx, action in enumerate(actions):
            TASK_ENV.take_action(action)

            if TASK_ENV.is_episode_end() or action_idx + 1 == len(actions):
                break

            obs = _augment(TASK_ENV, TASK_ENV.get_obs())
            model_client.call(func_name="update_obs", obs=obs)


def eval_one_episode_batch(TASK_ENV, model_client):
    """Disabled (design §2 P2 row and §8.2 F5: eval_batch:false, num_envs=1)."""
    raise NotImplementedError(
        "Etude deploy.py supports eval_batch:false only (design §2/§8.2 F5); "
        "set eval_batch: false in deploy.yml"
    )
