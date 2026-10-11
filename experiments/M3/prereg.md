# M3 预注册：条件 4，harness 回路逐面进化与最终 T

依据 `docs/design/vla_harness_v1.md` 第 5 节 M3 列、3.0–3.5 节和第 6 节。数值写在 `experiments/M3/config.yaml`，表格写在 `view.json` 和 `view_T.json`。

## 设计原文（§5 M3 列，逐字）

- **文件**：`evolution/*`；P6 会话；收据
- **实验**：三个面各 R≤4 轮；最后 T 一次：H* 对冻结 H2
- **预注册通过**：每面至少 1 次 accept；T 上 H* − H2 ≥ ε（seed 2 + 留出）
- **预算（本机）**：12 轮×8 h ≈ 96 h + T 400 回合 ≈ 27 h，约 $700
- **卡点**：A/A 给出的 ε 不能大于 15，否则 V 扩到 150

## 设计
| 项 | 取值 |
|---|---|
| 面 | 一次只开一个面，按顺序：planner_prompt（`evolvable/prompts/planner.md`）→ recovery_rules（`evolvable/rules/recovery.yaml`）→ skill_md（`evolvable/skills/SKILL.md`） |
| 轮次 | 每个面最多 4 轮，每轮 1 个候选。diff 不超过 40 行，或不超过 3 条规则 |
| 编辑者 | P6，claude-opus-5-5，在 git worktree 里只写 `evolvable/` |
| L0 | schema、AST 禁读、泄漏正则、diff 预算 |
| L1 | 在 E 的 medoid 布局上重跑，不超过 5 回合，要求全过 |
| L2 | 历史回归集 F，不超过 20 回合，最多失守 1 次 |
| L3 | 在 V 上配对 100 回合，要求 b − c ≥ ε 且 McNemar 单侧 p ≤ 0.05 |
| 成本 | ΔC ≤ β0 + β1·ΔSR |
| ε | 取 M1 的 A/A 结果 |
| 视图 view.json | 行 = 面，列 = candidate（incumbent_v0–incumbent_v4、r1–r4），replicates = seed。seed 0 是 L2（E），seed 1 是 L3（V）。L1 用 seed 0 加辅助槽 `stage=L1`；该面第 K 次 accept 后在 V 上重跑新现任，记在格子 `incumbent_vK`（每个现任版本一个格子，不覆盖上一个现任的 Run） |
| 视图 view_T.json | 冻结 H2 对 H\*，match 为 split=T。开发任务只跑 seed 2，留出任务跑 seed 0/1/2 |

## 通过标准（§5 原文）

> 每面至少 1 次 accept；T 上 H* − H2 ≥ ε（seed 2 + 留出）

## 证伪条件与停止规则（§6 原文）
| 条件 | 统计量 | 余量 | 后果 |
|---|---|---|---|
| 进化后 seed 2 不超过冻结 harness | T 上 H* − H2 配对 | < ε | 记 M3 证伪，保留 H2 |
| 同回合同 VLM 预算 M3 ≤ M2 | M2+ = M2 用同等额外回合扩记忆；比较 H* 与 M2+ | H* − M2+ < ε | 进化不值 |
| 停止规则 | 连续 3 轮 reject；或 V 已被 6 个候选复用；或预算用尽；或 ε>15 | — | 冻结现任，进入 T |

M2 的"条件 3 在留出上缩水"检查（开发任务 seed 2：SR2 − SR1 < −δ_AA）也在这次 T 中一起看。

## 不跑的单元格

以下单元格有意不跑，会一直显示 pending：
- `view_T.json` 里开发任务的 seed 0/1 两格。这两个 seed 是 E/V，不属于 T。
- `view.json` 里没走到 L2/L3 就被拒的候选所在的格。

## 未决（需用户确认）

- **M2+ 的比较集**：设计没写明用哪个集合，暂定 TBD。放在 T 上会让 T 多出 400 回合；放在 V 上又会让 V 的复用次数加 1。
- **T 的预算**：§5 写"T 400 回合"，但 H\* 和 H2 配对需要两者都在 T 上跑，共 800 回合。除非 H2 在 T 上的结果另有来源。

## lineage

每个候选写一张收据（§4），`Receipt.research_run` 记对应 Run 的标签。

## Research OS
| 实验 | 视图 | kind | 槽 |
|---|---|---|---|
| E-M3 | `experiments/M3/view.json` | search | `surface, candidate, seed`（candidate 含 incumbent_v0–v4）；辅助槽 `stage=L1` |
| E-M3-T | `experiments/M3/view_T.json` | main | `split=T, task, condition, seed` |
