# M0 预注册：条件 1，冻结 π0.5 的重查步长扫描

依据 `docs/design/vla_harness_v1.md` 第 5 节 M0 列、3.2 节和 3.3 节。数值写在 `experiments/M0/config.yaml`，表格写在 `view.json` 和 `view_aa.json`。本文件在第一个 Run 之前提交。之后要改，只能走 `research experiment amend`，台账会标记 `post_hoc_amended`。

## 设计原文（§5 M0 列，逐字）

- **文件**：`model.py` 返回块前 N 步；`aa_noise.py`；验证器离线跑 M0 录像
- **实验**：开发任务×N∈{5,10,15,25,50}×seed 0/1×50（批量 eval_batch:true，num_envs 4）；留出任务只跑最佳 N；A/A 3 次；验证器对官方 judge 的 FPR/FNR
- **预注册通过**：跑完、t_step 与 A/A 的 δ 记下；FPR ≤5%（否则验证器先修）
- **预算（本机）**：约 1000+200 回合，无 VLM，批量 ≈ 12 h
- **卡点**：π0.5 单独跑通官方流程；单回合墙钟实测；夹爪访问器可用

## 设计
| 项 | 取值 |
|---|---|
| 策略 | π0.5 官方 ckpt `RoboDojo-sim-arx_x5-joint-0/59999`，14 维关节，块长 50，冻结 |
| 自变量 | N ∈ {5, 10, 15, 25, 50}：每块只执行前 N 步，然后重新推理 |
| 任务 | 开发 = stack_bowls、put_bottles_into_dustbin；留出 = build_tower、make_kong，只跑 N* |
| 划分 | seed 0 = E，seed 1 = V，每 seed 50 个布局（0–49），见 `experiments/splits.json` |
| 回合 | 扫描 2×5×2×50 = 1000；留出 2×1×2×50 = 200；A/A 3×2×50 = 300 |
| 运行 | 设计计划批量 `eval_batch: true`、`num_envs: 4`。实际只能 `eval_batch: false`、`num_envs: 1`：Etude 适配器不支持批量，本机 16 GB 也只能跑 1 个环境（§8.1 #1）。所以 12 h 上限多半不够，需要用户确认新的墙钟预算 |

## 主分析

1. **N\* 的选法**：取开发任务 seed 0/1 合并 200 回合 SR 最高的 N。平手时取更大的 N（推理次数少）。设计没写选法，这一条是本预注册补充的。
2. **A/A**：现任 π0.5@N\* 在 V（开发任务 seed 1，100 回合）上重跑 3 次，得到成功数 S₁、S₂、S₃。
   - δ_AA = max |S_a − S_b|。
   - ε = max(4, δ_AA + 1)，原文为 `eps = max(4, max(abs(a - b) for a, b in pairs(S)) + 1)`（§3.3）。
   - ε 大于 15（frozen `gate.eps_max`）时 V 扩到 150（§5 M3 卡点）。
3. **验证器 FPR/FNR**：验证器离线跑全部 M0 录像，官方 judge 由 P5 的 summarize 给出。
   - FPR = 验证器 Pass 而官方失败的回合数 / 官方失败回合数。
   - FNR = 验证器未 Pass 而官方成功的回合数 / 官方成功回合数。
   - 记入 E-M0 的辅助槽 `stage=verifier_audit`。
4. **记录**：t_step、单回合墙钟、unstable 率。unstable 率超过 5%（`scorer.unstable_rate_review`）时触发人工复核（§3.5）。

## 通过标准（§5 原文）

> 跑完、t_step 与 A/A 的 δ 记下；FPR ≤5%（否则验证器先修）

## 卡点（§5、§8.4）

- π0.5 单独跑通官方流程。
- 单回合墙钟实测。t_step 超过 0.2 s 时，本机方案每轮超过 14 h（§8.4 #3）。
- 夹爪访问器 `gripper_width_measured` 可用（§8.1 #3）。
- N=50 那一行和官方榜 11.44 核对：用 seed 0 复跑（§8.4 #2、#8）。

## 不跑的单元格

`view.json` 的行包括留出任务。留出任务只填 N\* 这一列，其余列有意不跑。这些格会一直显示 pending，属于预注册的空格，不是缺失数据。

## Research OS
| 实验 | 视图 | kind | 槽 |
|---|---|---|---|
| E-M0 | `experiments/M0/view.json`（task × n × seed） | search | `task, n, seed`；辅助槽 `stage=verifier_audit` |
| E-M0-AA | `experiments/M0/view_aa.json`（task × condition × rep，match split=V） | replication | `split=V, task, condition=pi05_nstar, rep` |

- 入口是 `scripts/run_m0.sh --task T --seed S --eval-num 50 --n N`，批量执行用 `scripts/m0_sweep.sh`，A/A 用 `scripts/aa_rerun.sh --milestone M0 --nstar N*`。
- 设 `RESEARCH_RUN=1` 后，每次调用都会包进 `research run`。
- 契约授权由用户本人执行，命令见 `uv run python experiments/expctl.py bootstrap`。
