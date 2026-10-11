# Etude

The Etude VLA harness, packaged as an XPolicyLab policy. It covers design §2 (process P2 and the P4 forwarder), §3.3 and §5 M0. The specification is `docs/design/vla_harness_v1.md`.

This directory lives in the Etude repo at `deploy/XPolicyLab/policy/Etude/`. It is symlinked to `~/Documents/RoboDojo/XPolicyLab/policy/Etude` by `scripts/link_into_xpolicylab.sh`. RoboDojo itself is not edited.

| File | Role |
|---|---|
| `deploy.py` | **Frozen.** It copies the official `demo_policy/deploy.py` loop and adds two allow-listed enhancements: `step_lim` and `gripper_width_measured` in `obs["additional_info"]`. The batch path raises `NotImplementedError`. Its sha256 is recorded in `DEPLOY_SHA256`. |
| `DEPLOY_SHA256` | The audited hash of `deploy.py`. `model.py` (with `deploy_hash_check: strict`) and `scripts/run_*.sh` refuse to run when it does not match. Changing it requires a human decision. |
| `deploy.yml` | Fields follow Pi_05. It sets `eval_batch: false`, `request_timeout_s: 1800` and `action_type: ee`. It also sets `mode` (`harness` or `m0_requery`), `exec_steps`, the `backend` (remote ws, inprocess or fake), and the harness components (`planner`, `perceiver`, `verifier`, `memory`, `trace`). |
| `model.py` | The XPolicyLab `Model`, a thin shell over `etude.adapter.xpl_model.EtudeModelCore`. |
| `eval.sh`, `setup_eval_policy_server.sh`, `setup_eval_env_client.sh` | Same positional contract as Pi_05. The server uses the Etude `.venv` and puts the Etude checkout first on `PYTHONPATH`. |
| `install.sh` | Runs `uv sync` in the Etude repo, then `uv pip install -e` for XPolicyLab and openpi-client into that `.venv`, then creates the link. |

## Modes

- **`m0_requery`** (design §5 M0). Each `get_action` performs one `PolicyBackend.infer(latest_obs, instruction)` and returns the first `exec_steps` = N rows of the (50,14) chunk as joint dicts.
- **`harness`** (design §3.3). `etude.adapter.harness_build.HarnessRunner` builds `etude.runner.NodeRunner` with the backend, perceiver, verifier, memory and trace components. When `planner.kind: pi_rpc`, it also builds `PiRpcPlanner` and the MCP server (`planner/.pi/mcp.json`, port 8765). Observations, verdicts and chunk counts are mirrored into the MCP `HarnessState`.

Every action dict passes `etude.adapter.action_check` before it leaves the server. That module is a pure-function copy of RoboDojo's `validate_action_dict` and `get_action_type`, plus a completeness and finiteness check.

## Install and run

```bash
bash deploy/XPolicyLab/policy/Etude/install.sh          # once; uses this repo's .venv
# P3: the pi0.5 openpi server, on the second GPU / machine (design §8.1 items 1 and 8)
#   cd ~/Documents/RoboDojo/XPolicyLab/policy/Pi_05/openpi && uv run scripts/serve_policy.py policy:checkpoint \
#     --policy.config=pi05_base_aloha_full_sim_arx-x5_seed_0 --policy.dir=<ckpt dir>   (port 8000)
scripts/run_m0.sh --task stack_bowls --seed 0 --eval-num 50 --n 25 --backend-host <p3-host>
scripts/run_harness.sh --task stack_bowls --seed 0 --eval-num 50 --backend-host <p3-host>
scripts/run_m0.sh ... --dry-run                          # print the robodojo.sh command only
```

The run scripts wrap `~/Documents/RoboDojo/scripts/robodojo.sh eval`. They take a lock and point the symlink at the checkout that is executing, which may be a Research OS detached worktree. They set `ROBODOJO_RUN_ID` so that the run's official `_result.json` can be found afterwards. When a run ends they write the following:
- `{"successes","trials","success_rate"}` to `$RESEARCH_METRICS`. Without Research OS they print it instead.
- `etude_run.json` to `$RESEARCH_RUN_DIR`. It records the result dir, the deploy.py hash, the Etude, RoboDojo and XPolicyLab commits, the unstable count and the wall time. Per-episode adapter logs go to `etude_episodes/episodes.jsonl` in the same place.
- `$RESEARCH_OUTPUTS` only when you pass `--output NAME=model|dataset=OWNER/NAME@REV`, because Research OS accepts only HF refs there.

Research OS command template. Source must be committed and pushed. The `--human/--quote/--channel` authorisations are the user's to give.

```bash
RESEARCH_ACTOR=agent:<id> uv run python research/tools/research run --experiment E-<n> --kind evaluation \
  --archive-to model=<OWNER/NAME> --checkpoint model=<OWNER/NAME@REV[:path]> \
  --parameters '{"task":"stack_bowls","seed":0,"exec_steps":25,"mode":"m0_requery"}' \
  -- scripts/run_m0.sh --task stack_bowls --seed 0 --eval-num 50 --n 25
```

Environment overrides read by the server (exported by the run scripts): `ETUDE_MODE`, `ETUDE_EXEC_STEPS`, `ETUDE_BACKEND_KIND`, `ETUDE_BACKEND_HOST`, `ETUDE_BACKEND_PORT`, `ETUDE_LOG_DIR`, `ETUDE_VENV` and `ETUDE_ROOT`.

To check everything on fakes: `uv run python -m etude.adapter --demo`.

## Known gaps

1. **`request_timeout_s` is not wired by RoboDojo's sim client.** In RoboDojo 266130a, `src/eval_client/eval_env.py` builds `WsModelClient` without `request_timeout_s`, and `XPolicyLab/utils/debug_env_client.py` (f6e309e) does not either: it forwards `deploy_cfg.get("request_timeout_s")`, but `deploy_cfg` is its argparse namespace, which has no such key. Both clients therefore keep the 120 s default, and any harness `get_action` that blocks longer, for example a slow planner call, kills the episode. Our `deploy.yml: 1800` takes effect only after an upstream fix or a user-approved local patch. M0 is unaffected.
2. **What `gripper_width_measured` means.** It is element 0 of `get_end_effector_real_val`, the primary gripper joint position in sim units, which is the value RoboDojo itself uses as the "current" gripper position. Whether that equals the finger gap in metres is unverified, so `gripper.holding_width_min` must be calibrated in M0 (design §5 M0 卡点).
3. **The batch path is disabled**: `eval_batch: false` means num_envs=1. Design §5 plans M0 with `eval_batch: true, num_envs 4` for throughput, which this adapter does not support.
4. `robot_manager.get_robot_by_arm_name` is an allow-list extension (frozen/forbidden.yaml) that awaits user sign-off.
