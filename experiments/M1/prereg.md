# M1 预注册：条件 2，完整 harness（记忆关）

依据 `docs/design/vla_harness_v1.md` 第 5 节 M1 列，以及第 6 节的条件 2 行和"收益全来自缩短重查步长"行。数值写在 `experiments/M1/config.yaml`，表格写在 `view.json` 和 `view_aa.json`。

## 设计原文（§5 M1 列，逐字）

- **文件**：全部 P1/P2；`evolvable/` 初版；记忆关
- **实验**：开发+留出任务，seed 0/1 各 50；记录调用/token/墙钟；A/A 3 次
- **预注册通过**：配对 SR1 − SR0(N*) ≥ ε 且 p≤0.05（开发任务 seed 0/1 合并 200 回合）；FNR 不升
- **预算（本机）**：约 400 回合 ≈ 27 h，VLM 约 $120
- **卡点**：节点边界阻塞 ≤1800 s；perceive 检测器在三视角可用

## 设计
| 项 | 取值 |
|---|---|
| 条件 h1 | 全部 P1/P2，`evolvable/` 初版，记忆关。VLA 节点用默认 `exec_steps` 25 |
| 条件 h1_nstar | 同 h1，但 VLA 节点 `exec_steps` 固定为 N\*（依据 §6 第 5 行）。N\* = 25 时与 h1 相同，不另跑，直接引用 h1 的 Run |
| 对照 | M0 的 N=N\* 单元格，同任务、同 seed、同布局配对 |
| 任务 | 开发 + 留出，共 4 个；seed 0 和 1 各 50 回合 |
| 模型 | 规划用 claude-sonnet-5-5，每回合最多 12 次调用；VLM 默认只看最后一帧，inspect 按需 |
| 部署 | `eval_batch: false`、`num_envs: 1`、`request_timeout_s: 1800` |
| A/A | 现任 H1 在 V（开发任务 seed 1）上重跑 3 次，ε = max(4, max\|S_a−S_b\| + 1) |

## 主分析

**配对 McNemar（单侧）**：取开发任务 seed 0/1 合并的 200 个布局，比较 H1 和 M0(N\*)。
- b = H1 成功而 M0(N\*) 失败的布局数；c = 反过来的布局数。
- 通过条件是 b − c ≥ ε 且 p ≤ 0.05。ε 取 M1 自己的 A/A 结果。

## 通过标准（§5 原文）

> 配对 SR1 − SR0(N*) ≥ ε 且 p≤0.05（开发任务 seed 0/1 合并 200 回合）；FNR 不升

## 证伪条件（§6 原文）
| 条件 | 统计量 | 余量 | 后果 |
|---|---|---|---|
| 条件 2 不超过最好的 N | 同布局配对 McNemar，开发任务 seed 0/1（200 回合） | b−c < ε 或 p>0.05；或漏检率上升 >2 pp | 如实写成"带验证器的定时重查器"；M2 仍做（记忆对重查器也可测），M3 只开恢复规则一个面 |
| 收益全来自缩短重查步长 | M1 内 VLA 节点 `exec_steps` 固定为 N*；对照 M0(N*) | 若 put_bottles/make_kong 上 M1 − M0(N*) < ε 而 stack_bowls 上 ≥ε | 结论限定为"短程任务收益=重查" |

## 记录项

VLM 调用次数、token、墙钟、漏检率（验证器 Pass 而官方失败）。

## 预算说明

- §5 的 400 回合指 h1：4 个任务 × 2 个 seed × 50。
- h1_nstar 只在 N\* ≠ 25 时跑，最多再加 400 回合。
- A/A 再加 300 回合。§5 的预算行没有单列这一项。

后两项都超出 §5 的预算行，需要用户确认。

## 卡点

- 节点边界阻塞不超过 1800 s。超时后是作废还是重试，用一次人为超时实测（§8.4 #7）。
- perceive 检测器在三个视角都可用（§8.4 #6）。

## Research OS
| 实验 | 视图 | kind | 槽 |
|---|---|---|---|
| E-M1 | `experiments/M1/view.json`（task × condition × seed） | main | `task, condition, seed` |
| E-M1-AA | `experiments/M1/view_aa.json`（match split=V） | replication | `split=V, task, condition=h1, rep` |

- 入口是 `scripts/run_harness.sh --task T --seed S --eval-num 50 [--n N*]`。
- A/A 用 `scripts/aa_rerun.sh --milestone M1`。
