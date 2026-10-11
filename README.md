# Etude-harness

冻结 π0.5 加可编辑 harness，在 RoboDojo 仿真上按官方协议跑，不提交榜单。第一版不训练任何权重。

- 设计纪要：<https://claude.ai/artifact/7q41geTu1qsh3FYbGaMPhy>
- 旧 Etude（π0.5 + Astra 数据飞轮，LIBERO）的代码和产物：`../Etude-harness-archive-2026-10-10/`

## 调研底稿

纪要里引用的表号、节号和核对过的数字都在 `docs/research/`。

| 文件 | 内容 |
| --- | --- |
| `embodied_rsi_v1.1_overview.md` | 13 条合并结论、9 项统一表、12 个模块的可复用实现 |
| `embodied_rsi_update_2026-10-10.md` | 底稿 A：9 项新工作的卡片和模块行 |
| `part_B_v1_modules_and_llm_mechanisms.md` | 底稿 B：17 个具身系统的模块清单、33 条 LLM agent 机制 |
| `part_C_robodojo_protocol.md` | 底稿 C：RoboDojo 协议、上榜规则、XPolicyLab 接口、规则没明说的点 |
| `part_D_agent_systems.md` | 底稿 D：pi-mono、openpi、Claude Code、Codex 等的模块表 |
| `part_E1_spotter_roboharness.md` | 底稿 E1：Spotter、RoboHarness |
| `part_E2_pyrua_showharness.md` | 底稿 E2：PyRUA-Lean、Show-Harness |
| `vista_2610.02200.md` | VISTA 卡片：所有帧全存、按需 inspect 回看的视觉 harness（ARC-AGI-3，回合制，非具身）；借"默认只给最后一帧、按需回看"和"先写预期、再对照结果"，不借 read_pixels、上采样和上下文交接；附设计文档 VISTA 引用的更正 |
| `recova_2610.01178.md` | Recova 卡片：VLM 四查询监控 + 复原验收 + DAgger 微调 π0.5；借复原验收与完成优先，不借训练 |
| `memory_anchors_2608.26545.md` | Memory Anchors 卡片：持续模仿学习里按"表征重叠 + 动作冲突"挑旧数据进回放缓冲；v1 只借诊断思路（记忆检索防撞、L2 回归集选取），回放留给 v2 |

Deep Research 用的 prompt 在 `docs/prompts/`。

## 运行方式

规格以 `docs/design/vla_harness_v1.md` 为准。本仓库没有测试：组件验证只靠两条命令，一是各组件的 `--demo`（在 `etude/fakes.py` 的假环境上跑），二是 `make ast-check`（禁读 AST 检查）。

### 1 环境

- **Etude 本体**：Python 3.12 + uv，执行 `uv sync`。
- **RoboDojo**：克隆在 `~/Documents/RoboDojo`，commit 266130a，含子模块 XPolicyLab（commit bb9a0b5）。仿真侧用 RoboDojo 自己的 conda 环境（默认名 `RoboDojo`）。
- **显卡**：单卡 16 GB 放不下 Isaac Sim 和 π0.5。π0.5 要放到第二张卡或另一台机器（设计 §8.1 #1）。
- **策略服务端环境**：执行 `bash deploy/XPolicyLab/policy/Etude/install.sh`。它把 XPolicyLab 和 openpi-client 装进本仓库的 `.venv`，并完成第 3 步的链接。

### 2 下载 checkpoint 与布局

```bash
# π0.5 官方 ckpt，约 12.4 GB（HF 数据集 RoboDojo-Benchmark/RoboDojo，固定 revision）
hf download RoboDojo-Benchmark/RoboDojo --repo-type dataset \
  --revision 35efbc7dedfdbeeb6e95fb749bd885d73d483e41 \
  --include "ckpt/RoboDojo/Pi_05/RoboDojo-sim-arx_x5-joint-0/59999/*" --local-dir ~/models/robodojo-ckpt
# 4 个任务和替补任务的评测布局，seed 0/1/2，下载到 RoboDojo 自己的 Assets/
hf download RoboDojo-Benchmark/RoboDojo --repo-type dataset \
  --revision 35efbc7dedfdbeeb6e95fb749bd885d73d483e41 \
  --include "Assets/Eval_Layout/RoboDojo/arx_x5/*/stack_bowls_*.json" \
  --include "Assets/Eval_Layout/RoboDojo/arx_x5/*/put_bottles_into_dustbin_*.json" \
  --include "Assets/Eval_Layout/RoboDojo/arx_x5/*/build_tower_*.json" \
  --include "Assets/Eval_Layout/RoboDojo/arx_x5/*/make_kong_*.json" \
  --include "Assets/Eval_Layout/RoboDojo/arx_x5/*/fold_clothes_*.json" --local-dir ~/Documents/RoboDojo
make split    # 重新生成 experiments/splits.json（只读文件名）
```

按设计 §3.2，划分如下。`make split` 把它们写进 `experiments/splits.json`，没有布局的任务记为 `missing`。

| 划分 | 内容 |
| --- | --- |
| E | 开发任务 seed 0，布局 0–49 |
| V | 开发任务 seed 1，布局 0–49 |
| A/A | 在 V 上重跑 3 次 |
| T | 开发任务 seed 2，加留出任务 seed 0/1/2 |
| 备用 | seed 0 布局 50–54 |

### 3 链接到 XPolicyLab，启动 π0.5 服务端（P3）

```bash
bash scripts/link_into_xpolicylab.sh     # ~/Documents/RoboDojo/XPolicyLab/policy/Etude -> deploy/XPolicyLab/policy/Etude
# 在第二张卡或另一台机器上（端口 8000）
cd ~/Documents/RoboDojo/XPolicyLab/policy/Pi_05/openpi && uv run scripts/serve_policy.py policy:checkpoint \
  --policy.config=pi05_base_aloha_full_sim_arx-x5_seed_0 \
  --policy.dir=~/models/robodojo-ckpt/ckpt/RoboDojo/Pi_05/RoboDojo-sim-arx_x5-joint-0/59999
```

### 4 Research OS 初始化

`research/` 下是从 a2r vendored 来的 Research OS。规则见 `research/AGENTS.md`，Etude 里怎么用见 `docs/research_os_usage.md`。

**授权分工**：以下动作必须由你本人用原话授权（`--human/--quote/--channel`），代理只生成命令模板：
- `research init`
- 各实验 committed 契约的 `authority grant`
- Decision 与 Policy

**步骤**：

1. 生成完整命令序列：

   ```bash
   make research-bootstrap   # 先校验 experiments/M*/view*.json，再打印 init → 假设 → 7 个 committed 实验 → 授权请求 → style 命令
   ```

2. 准备 checkpoint 镜像。Research OS 的评测 Run 要求 `--checkpoint` 是 `model=` 仓库，并且 `--archive-to` 必须是同一个仓库。所以要把上面的官方 ckpt 原样镜像到你自己的 HF model 仓库，再设置：

   ```bash
   export RESEARCH_CHECKPOINT=model=<OWNER>/<NAME>@<40位revision>:ckpt/RoboDojo/Pi_05/RoboDojo-sim-arx_x5-joint-0/59999
   ```

3. 把 CLI 实际分配的实验别名（E-n）写回 `experiments/<M>/config.yaml` 的 `research.experiment`、`aa_experiment`、`t_experiment`。也可以每次跑时用 `RESEARCH_EXPERIMENT=E-n` 传入。

4. 每个 Run 都只执行**已 commit 且已推送 origin** 的代码，在 detached worktree 里运行。入口脚本把 `{"successes","trials","success_rate"}` 写到 `$RESEARCH_METRICS`。原始日志放在 `runs/research/<Run>/attempt-N/`。

### 5 跑 M0（设计 §5 M0 列，预注册见 `experiments/M0/prereg.md`）

```bash
make m0 M0_ARGS=--dry-run                                   # 只打印 2 任务 × N∈{5,10,15,25,50} × seed 0/1 的命令
bash scripts/run_m0.sh --task stack_bowls --seed 0 --eval-num 50 --n 25 --backend-host <P3主机>   # 单格本地烟测
RESEARCH_RUN=1 RESEARCH_EXPERIMENT=E-<n> make m0           # 正式扫描：每格一个 research run
RESEARCH_RUN=1 RESEARCH_EXPERIMENT=E-<n> bash scripts/m0_sweep.sh --heldout --nstar <N*>   # 留出任务只跑最佳 N
RESEARCH_RUN=1 RESEARCH_EXPERIMENT=E-<n-AA> make aa MILESTONE=M0 NSTAR=<N*>               # A/A：V 上 ×3，得 ε
```

- **跳过与 `--force`**：`experiments/splits.json` 里记为 missing 的任务和 seed 会被跳过，加 `--force` 才会照跑。
- **透传参数**：`EXTRA_ARGS="--backend-host H --policy-gpu 1"` 原样传给 `run_m0.sh`。
- **不设 `RESEARCH_RUN`**：直接调用入口，适合本地烟测。

M1 及以后单次评测用 `make harness TASK=… SEED=… [N=…]`。

### 6 演示与检查

```bash
make demo                               # 逐个跑组件 --demo（fakes，不调真实模型、不依赖 ckpt），再跑划分、视图校验、M0/A-A 干跑
make ast-check                          # uv run python -m etude.frozen.ast_check
uv run python experiments/expctl.py check-views   # ViewSpec + R44 + 与 config.yaml 一致
```

### `experiments/` 目录

| 路径 | 内容 |
| --- | --- |
| `M0..M3/config.yaml` | 任务集、seed、eval-num、N 列表、A/A 次数、模型、预算上限（数值照设计 §5） |
| `M0..M3/prereg.md` | 预注册：通过标准与证伪条件逐字引自设计 §5/§6 |
| `M*/view.json`、`M0/view_aa.json`、`M1/view_aa.json`、`M3/view_T.json` | Research OS ViewSpec。M0 是 task×N×seed，M1/M2 是 task×condition×seed，M3 是 surface×candidate×seed；A/A 和 T 各自单独成表 |
| `splits.json` | E/V/AA/T/备用划分，由 `scripts/make_split.py` 生成 |
| `expctl.py` | 读配置、校验视图、查划分状态，并经 `etude.evolution.research_bridge` 生成 `research run` 命令 |
