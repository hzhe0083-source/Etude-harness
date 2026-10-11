# VLA Harness v1 实现说明（集成轮，2026-10-11）

本文件汇总八个组件（executor、verifier、memory、planner/MCP、adapter、evolution/frozen/scorer、research_bridge、experiments）的未决问题，以及集成轮的修改。规格仍以 `docs/design/vla_harness_v1.md` 为准；这里只记录"实现和规格差在哪、哪些要真实环境验证、用户要做什么"。

仓库约定：不写测试。验证 = 各组件 `--demo` 在 fakes 上跑通 + `uv run python -m etude.frozen.ast_check`。

## 0 当前状态

| 检查 | 命令 | 结果 |
|---|---|---|
| 全部组件 demo + 端到端假回合 + split/views/干跑 | `make demo` | demo OK（21 个模块 + `scripts/demo_fake_episode.py`） |
| 端到端假回合 | `uv run python scripts/demo_fake_episode.py [--out DIR]` | 19/19 项检查 ok（含 B 锚点复验、B2 锚点被推翻、H 抬起后的节点失败锚点仍成立） |
| AST 禁读 | `uv run python -m etude.frozen.ast_check`（默认范围，含 `scripts/`）与 `… ast_check etude deploy evolvable` | 0 error，2 warning：`etude/adapter/xpl_model.py:220` 配置驱动的 `importlib.import_module`（只允许 `etude.*`）；`deploy.py:50` 的 `robot_manager.get_robot_by_arm_name` 为 `pending_signoff`（第 4 节第 2 条，`--strict` 下为错误） |
| 作弊自测 | `uv run python -m etude.frozen.ast_check --selftest` | 55 个作弊候选全部 BLOCKED，4 个对照全部 passed（最终核验轮） |
| deploy.py 哈希 | `uv run python -m etude.adapter --deploy-hash` | actual = recorded = `a75b3b4f…` |
| 全模块可导入 | `pkgutil.walk_packages(etude)` | 0 失败 |

没有改 `pyproject.toml` / `uv.lock`：demo 不缺依赖。真实运行需要的依赖见第 3 节。

## 1 集成轮做的修改

| 文件 | 修改 | 原因（设计节号） |
|---|---|---|
| `etude/runner.py` | 规划改为可选后台线程：`NodeRunner(plan_wait_s=…)`，每次 `get_action` 最多等 `plan_wait_s`，未完成就返回 1 步保持块并记 `plan_wait_timeout`，下次 `get_action` 再取结果；回合结束写 `episode_cost`（含超时次数） | 设计文档在本轮被更新：§2 慢回路、§3.3、§8.2 F5。官方 `eval_env.py` 不传 `request_timeout_s`，每次调用硬上限 120 s，规划等待设 90 s |
| `etude/adapter/harness_build.py`、`planner/config.yaml`、`deploy.yml`（仅注释） | `pi_rpc` 规划器把 `timeout.wait_in_get_action_s: 90` 传给 NodeRunner；`timeout.plan_s: 1800` 改为后台总上限 | 同上 |
| `etude/runner.py` | 开回合时用首帧给验证器设 `arm_home` 参考位姿（`verifier.set_home`），`reset()` 同时重置验证器 | Bug：CodeVerifier 只在 `check()` 里见到 `t == 0` 帧时才设 home；首个尝试走 Blocked（runner 自判）或首次 check 晚于 t=0 时，`arm_home` 永远 InsufficientEvidence，回合陷入 reobserve→retry→replan 循环直到 VLM 预算耗尽（端到端 demo C 复现） |
| `etude/runner.py` | 每次 `update_obs` 调 `trace.tick(frame)`（若 writer 有），写每秒 1 帧关键帧 | §4 存储"节点边界与每秒 1 帧"；memory 组件报告 tick 无人调用 |
| `etude/runner.py` | 读取 `recovery.yaml` 的 `fallback.max_backtracks`（每回合退回次数上限）和 `fallback.replan_when_calls_left_below` | planner 组件新增了该块但 runner 不读，等于无效 |
| `etude/fakes.py` | `FakePolicy(stall=True)` 返回 `(50,14)` 而不是 `(1,14)` | executor 组件报告的 bug |
| `research/README_SOURCE.md` | `--parameters` 模板改为 JSON 文件路径 | `cmd_runs.py` 用 `Path(...).read_text()` 读，内联 JSON 会失败（research_bridge 报告） |
| `scripts/demo_fake_episode.py`（新） | 端到端假回合演示（见第 2 节） | 集成任务 |
| `Makefile` | `make demo` 加入 `pi05_inprocess` 与端到端假回合；新增 `make fake-episode` | — |

### 1.1 Recova 补丁 P1/P2/P3/P10（2026-10-11，依据 `docs/research/recova_2610.01178.md` 补丁表）

| 文件 | 修改 | 依据 |
|---|---|---|
| `etude/core/types.py`、`frozen/schema/verdict.json` | `Verdict.scene_intact`（可选，`boolean\|null`）；`to_dict` 对所有非 Pass 判决写出（可为 null），Pass 不写；判决种类不变 | P2；设计 §4 |
| `etude/verifier/predicates.py` | `scene_not_moved`：以 I_0（回合 t=0 关键帧）为参照，对非目标物体在 I_0 框内做 RGB 差分 `not_moved`，复用 `rgb_diff.moved_min/not_moved_max`、`views_agree_min`，不新增阈值；`node_objects` / `node_manipulated`（ee_move 不算移动物体，`not_moved` 参数不算目标）；`ARM_STATE_PREDICATES=("arm_home",)` | P2 |
| `etude/verifier/verdict.py` | `check()` 顺序改为 SafetyStop → 后置条件 → Blocked：后置条件全满足即 Pass，`ik_failed` 记为 `predicates["ik_failed"]={v:true}` 证据；未满足且有 `ik_failed` 才 Blocked（附 `ik_ok=false` 与后置条件结果）；有 `ik_failed` 时不调 inspect（不新增 VLM 调用）。新增 `set_reference` / `register_scene_objects` / `note_pass` / `scene_intact(frame, node, as_of_t)`；`check(recheck=True, as_of_t=…)` 不调 inspect、不记账 | P3、P2；设计 §4 |
| `etude/runner.py` | 开回合时 `set_reference(t=0 帧)`；装序列时登记其中所有物体；runner 自判的非 Pass（Blocked/lost/SafetyStop）也带 `scene_intact`。`retreat_end` 后 `_recheck_anchor`：对锚点节点后置条件（跳过 `arm_home`）重跑 `verifier.check(recheck=True)` 并查 `scene_intact`（as of 锚点通过时刻），0 次 VLM，trace 记 `anchor_recheck`。通过才重试；否则 `anchors.pop()`：scene_intact=false 时清空全部锚点（更早锚点的目标集更小，必然也不成立），以当前帧为新 I_0，replan 请求带 `scene_intact=false` 与 `anchor_recheck`；只是后置条件不成立时退到当前序列里更早的锚点（再退一次、复验、从其后一节点续跑，计入 `max_backtracks`），没有则同样 replan | P1；设计 §3.3 |
| `etude/runner.py` | `_PlanJob` 带 `episode_id`、`len(verdicts)`、退回代数；取结果时任一变化即丢弃（`plan_discarded_stale`）并重发 | P10 |
| `etude/planner/request.py`、`etude/core/interfaces.py` | 规划提示在 `scene_intact=false` 时加一行"锚点已失效、哪些物体移动过、先 perceive 再用"；Planner 请求键文档 | P1 |
| `etude/verifier/__main__.py` | demo 新增"Fail + scene_intact=false（蓝块被碰走）""ik_failed 但后置条件满足 → Pass"；原 Blocked 用例改为后置条件不满足 | P2、P3 |
| `scripts/demo_fake_episode.py` | B 增加锚点复验检查；新增 B2：第 9 次打滑把红块碰开 6 cm → 该次 Fail 的 scene_intact=false → 退回后复验失败 → replan(scene_intact=false) → 重新接近 → Pass | P1 |

### 1.2 P1/P2/P3 审查修复（2026-10-11）

| 文件 | 修改 | 审查意见 |
|---|---|---|
| `etude/verifier/predicates.py` | `implicit_targets(node, preceding)`：无 `args.target`、也无对象型后置条件的接触节点（vla/gripper，如只带 `args.arm` 的 pick）的隐式对象 = 同臂前序节点中最近的 `args.target`（或其显式操作对象）；`node_manipulated(node, preceding)` 含隐式对象；`untracked_contact` 判"接触节点但对象无法确定" | demo n2 这类 pick 把自己抬起的红块留在监视名单里 → 后续非 Pass 判决与退回 n2 的复验都误报 scene_intact=false |
| `etude/verifier/verdict.py` | `register_sequence(nodes)`（runner 装序列时调用，记每个节点的前序节点）；`manipulated(node)`；台账存 `(t, objs\|None, node_id)`；`scene_intact(..., pending=, keep=)`：被计入的节点里有对象无法确定的接触节点 → null（`last_scene.undecidable` 写明），不猜；`set_reference` 清空台账（新 I_0 已包含此前的移动）；`check(recheck=True)` 的 scene 不再按锚点副本查隐式对象（锚点的移动已在台账里） | 同上 |
| `etude/runner.py` | 锚点复验把"待（重）跑节点"（失败节点 + 退到更早锚点时 `seq[rewind_to..cursor]`）的操作对象加入豁免，但锚点节点"提到但不移动"的对象（如 ee_move 目标）保留监视（`keep`）；`_Anchor.moved` 记 `note_pass` 返回的对象集；trace `anchor_recheck` 增 `exempt_pending`/`kept`/`scene_undecidable` | 复验不豁免将重试/重跑的节点 → 退到更早锚点几乎必然误判 |
| `etude/runner.py`、`etude/planner/request.py`、`etude/core/interfaces.py` | 锚点失效后的 replan 请求带实测 `scene_intact`（true/false/null）与 `anchor_invalidated: true`，不再一律 false；提示词只在 false 时写"场景被扰乱"，后置条件不再成立时另写一行 Anchor check | "锚点失效"与"场景被扰乱"被混为一谈 |
| `etude/verifier/predicates.py` | `scene_not_moved` 在头相机里检测机器人夹爪（`ROBOT_OCCLUDER_QUERIES=("robot gripper",)`，检测器调用，非 VLM，无新阈值）：物体 I_0 框与夹爪框（I_0 或当前帧）相交时改用当前框（物体离开遮挡处时能看到），都被遮挡则该视角不判（null），不判"移动" | 退回 ee_move 锚点后臂悬在目标上方，头相机差分会误报 |
| `etude/executor/ee_move.py`、`etude/runner.py` | ee_move 先判到位再判 IK：到位但最后一块被丢弃 → `done` + `info.ik_failed`；runner 把它写进最后一帧 `harness.ik_failed` 再交验证器（后置条件满足 → Pass 带 `ik_failed` 证据，不满足 → Blocked）；未到位就 IK 失败仍直接 Blocked（未完成）。设计 §4 补了这条 | P3 的顺序在生产路径上不可达（无人写 `ik_failed`） |
| demos | verifier demo 新增"无序列上下文 → null""夹爪遮挡 → null"，原 knocked 用例注册 `[reach, pick]`；ee_move demo 新增"到位但最后一块被丢弃 → done+ik_failed"；端到端 demo 观察 `blue block`（`scene_queries`），B2 的 Fail 现为 scene_intact=true（红块是 n2 的隐式目标，复验仍因 n1 的目标移动而失效），新增 H | — |

已在组件轮之后自行消失的问题：`etude/evolution/gates.py:95` 与 `etude/planner/rpc_driver.py:439` 的 `.get('success')` AST 错误已不再报；`experiments/expctl.py` 已改为经 `research_bridge.run_command` 生成 Run 命令（`--parameters` 写文件、`model=` checkpoint）。

## 2 端到端假回合（`scripts/demo_fake_episode.py`）

真实组件：`NodeRunner`、`CodeVerifier`、`ColorBlobPerceiver`、`MemoryDB` + `write_if_passed`、`FileTraceWriter` / `TraceReader`。假组件：`FakePolicy`（π0.5）、`FakePlanner`（P1）、`PickWorld`（`FakeTaskEnv` 子类：红块按实测 ee 位姿渲染到头相机和左腕相机，关节动作经假正运动学抬升 ee，闭合夹爪在 3 cm 内抓住红块，张开则落回桌面）。驱动循环照抄 `deploy.py eval_one_episode` + `_augment`，环境访问全部在 allow-list 内（每回合 0 违规）。

| 回合 | 场景 | 判决序列 | 附带检查 |
|---|---|---|---|
| A | 全 Pass | n1 Pass、n2 Pass、n3 Pass | 离线 judge 替身为真 → `write_if_passed` 入库，`lookup` 取回 |
| B | 前 9 次闭合打滑 | n1 Pass、n2 Fail×3（原地重试 2 次）→ 退回 n1 通过时的记录位姿 → 代码复验 n1（scene_intact=true，0 VLM）→ n2 Pass、n3 Pass | 记忆拒收（有非 Pass 判决） |
| B2 | 同 B，第 9 次打滑把红块碰开 6 cm | n1 Pass、n2 Fail×3（scene_intact 均 true：红块是 n2 的隐式目标，蓝块未动）→ 退回 → 复验失败（n1 的 ee_move 目标红块相对 t=0 移动，`kept=[red block]`）→ replan(scene_intact=false, anchor_invalidated) → n1/n2/n3 Pass | 共 3 次规划调用 |
| H | n3 = vla `arm_home(left)`（抬着块时不可能成立，retry 0 → previous_node） | n1 Pass、n2 Pass、n3 Fail → 退回 n2 锚点 → 复验 n2（后置条件 Pass，scene_intact=true，被抬起的红块是 n2 的目标）→ n3 Fail → replan → n4 张开 Pass | 无 anchors_dropped，没有请求带 scene_intact=false |
| C | 前 4 个 ee 指令 IK 失败 | n1 Blocked → replan → n1/n2/n3 Pass | — |
| D | 左臂 IK 永久失败 | n1 Blocked ×13 | 12 次规划后 `calls_left=0`；无 VLM 路径先用 A 入库的记忆序列，再退到单个 π0.5 节点 |
| E | 记忆冻结 | n1/n2/n3 Pass（只读记忆） | `append` 和 `write_if_passed` 都抛 `MemoryFrozenError`，摘要校验通过 |
| G | 规划器慢（0.35 s，等待上限 0.1 s） | n1/n2/n3 Pass | 3 次 1 步保持块，规划在后台完成 |
| F | 落盘 | — | 每回合有 `trace.jsonl`、`planner.jsonl`、`episode.json`、PNG 关键帧；关键帧无损读回；`episode.json` 的 `official_success` 为 null |

## 3 依赖真实环境的部分与验证方法（M0 开工清单）

按依赖排序。"验证"一列写的是第一次在真实环境上怎么确认，不是测试。

| # | 事项 | 现状 | 如何验证 |
|---|---|---|---|
| 1 | π0.5 服务（P3） | `pi05_remote` 只对协议一致的本地假服务跑过；`openpi_client` 未装进 `.venv`，运行时从 vendored XPolicyLab 路径导入（该副本要求 numpy<2，实测 numpy 2.x 可导入）；`Pi05InProcessBackend` 在本机抛 NotImplementedError（无 JAX/openpi） | checkpoint 下载完后在第二张卡起 `serve_policy.py`（配置 `pi05_base_aloha_full_sim_arx-x5_seed_0`），`uv run python -m etude.backends.pi05_remote --demo` 改连真服务；核对输出为绝对关节目标（`decode_actions` 按官方 unpack 路径假定绝对值） |
| 2 | 官方流程跑通 + N=50 等价性 | `scripts/run_m0.sh --dry-run` 通过；未真跑 | `run_m0.sh --task stack_bowls --seed 0 --eval-num 2 --n 50` 后用 `etude.scorer.results` 汇总，与官方 `summarize_result.py` 对数（§8.4 #2） |
| 3 | 布局文件 | 最终核验轮（2026-10-11）：`make_split.py --print` 显示 E/V/AA/T/spare 全部 ok、`missing tasks: -`（`Eval_Layout/RoboDojo/arx_x5` 已下载） | 核对下载 revision 与 README 固定的一致；`make split` 写盘后把 `experiments/splits.json` 随代码一起 commit |
| 4 | `request_timeout_s` | 官方 `eval_env.py` 不转发，固定 120 s；runner 已按新设计 90 s 等待 + 保持块 | 人为制造一次 >120 s 的 `get_action`，看回合作废还是重试（§8.4 #7）；记录规划墙钟 p95，>90 s 时换小提示或快模型 |
| 5 | 夹爪实测开度 | deploy.py 取 `get_end_effector_real_val(...)[env_idx][0]`（主夹爪关节位置，仿真单位）；fakes 的 `augment_obs` 取两指均值；阈值 `gripper.holding_width_min=0.012` 假定单位是米 | M0 用空夹/夹物两种状态读数标定单位与阈值（§8.1 #3、§8.4 #4）；阈值只许收紧 |
| 6 | ee 坐标系与腕相机标定 | `ServoCalib` 全是占位（focal 380、像素→xy 映射、table_z 0、gain 0.8）；无 `--arm` 时按头相机左右半边选臂 | 开工第一天 4 点探针（§8.4 #1）：发绝对 `*_ee_pose`，比较实测 `ee_pose`；用已知物体在腕相机里的像素位移拟合 focal 与映射，经 `NodeRunner(calib=…)` 传入 |
| 7 | 检测器 | `OwlV2Perceiver` 从未运行（transformers/torch 未装）；OWLv2 原始分数通常远低于 0.6，需要 `calibrate` 映射；`post_process_object_detection` 新版可能弃用 | 装依赖后在三视角 640×480 上测召回与 FPR（§8.4 #6），拟合分数校准；FPR ≤ 5% 才进 M1 |
| 8 | arm_home 参考 | 取每回合首帧关节（runner 已注入）；env_cfg 里没找到 ARX X5 的 home 位姿 | 核对首帧确为复位位姿；如不是，改为注入 `home_joints` |
| 9 | pi-mono 规划器 | 没登录任何 provider（`auth.json` 空）；`planner/.pi/mcp.json` 依赖未安装的第三方 `pi-mcp-adapter`；`guard.ts` 只用 fake_pi 模拟过状态上报，真实拦截未验证 | 用户 `pi auth`（或设 API key）并安装 adapter（`cd planner && pi install npm:pi-mcp-adapter -l`），再跑一次真实 `plan()`：确认 7 个工具出现、`bash` 等被拦、`etude-guard` 状态上报 |
| 10 | 多进程拓扑 | run 脚本不启动 P3；`run_m0.sh` 把全局 `XPolicyLab/policy/Etude` 软链指向当前 checkout 并持 flock，同一 RoboDojo 克隆不能并行两个 Etude 评测 | 真跑前先 `scripts/link_into_xpolicylab.sh --check`；软链写入 RoboDojo 克隆需用户同意 |
| 11 | Research OS 工作树运行 | `setup_eval_policy_server.sh` 在 detached worktree 里回退到主 checkout 的 `.venv`（`git --git-common-dir`）或 `$ETUDE_VENV` | 第一次真实 `research run` 时确认 PYTHONPATH 指向工作树 |
| 12 | 评分镜像 | `scorer/Dockerfile` 固定 numpy 2.1.3 / pyyaml 6.0.2 / jsonschema 4.23.0，未构建 | `docker build scorer/` 后用 `scorer/run_official.sh` 对一份真实 eval 目录复算 |
| 13 | 单回合墙钟 / t_step | 未测（§8.4 #3） | M0 的 `etude_run.json` 记录墙钟；adapter 每回合写 JSONL 统计 |
| 14 | scene_intact 的夹爪遮挡 | PickWorld 不渲染机械臂；OWLv2 对 "robot gripper" 在 ARX X5 头相机里是否稳定出框未知；连杆遮挡其它物体未处理 | 退回 ee_move 锚点后存头相机帧，看 `anchor_recheck.moved_objects` 是否为空、`last_scene` 里目标是否为 `occluded_by_gripper`；不稳就换查询词或改用投影 ee 位姿（需相机外参） |

建议补的依赖（本轮未装，需要用户同意下载）：

```bash
uv add huggingface_hub==0.35.3          # Research OS --checkpoint 校验与 HF 归档（research/requirements.txt 的固定版本）
uv pip install -e ~/Documents/RoboDojo/XPolicyLab/policy/Pi_05/openpi/packages/openpi-client   # 或 deploy/.../install.sh；不进 uv.lock，`uv sync --exact` 会删掉
uv add transformers torch               # OWLv2 检测器（体积大，建议放可选依赖组）
```

## 4 需要用户决定或签字的事项

1. **deploy.py 冻结哈希**：`deploy/XPolicyLab/policy/Etude/DEPLOY_SHA256`（`a75b3b4f…`）由 adapter 代理首次写入。用户确认 deploy.py 后与代码一起 commit；之后改哈希需要人类 Decision，任何代理不得刷新。
2. **allow-list 扩展**：deploy.py 为拿到 `get_end_effector_real_val(robot, …)` 的 robot 句柄额外访问了 `robot_manager.get_robot_by_arm_name`。设计 §2 的 allow-list 没有列它，已写进 `frozen/forbidden.yaml`，需要用户签字。AST 规则"句柄上不许读属性"只覆盖赋给简单变量名的句柄。
3. **批量评测**：adapter 只支持 `eval_batch: false`（num_envs 1，16 GB 单卡也只能这样）。§5 M0 预算按 `eval_batch: true`、num_envs 4 估的 12 h 达不到，需要重新批准 M0 墙钟预算。
4. **规划模型**：`planner/config.yaml` 用 openai/gpt-5.4（本机模型缓存里只有 openai 目录）；设计推荐 anthropic/claude-sonnet-5-5。登录 provider 时一并决定。
5. **MCP 传输**：设计 §2 写 MCP(stdio)，实现用 localhost streamable-http（`127.0.0.1:8765/mcp`），因为 pi 由 P2 拉起且要访问 P2 进程内的 `HarnessState`。端口在 `planner/config.yaml` 与 `planner/.pi/mcp.json` 两处，须一致。
6. **代码里的常量**（不在 frozen/params，也不在 recovery.yaml），需定是冻结还是可进化：
   - runner：`REOBSERVE_STEPS=5`、`MAX_RETREATS_PER_NODE=1`、`MAX_HIST_FRAMES=32`、退回 `max_chunks=6`、无 VLM 兜底节点 `max_chunks=40`、`on_fail retry 0 → abort`；
   - verifier：`SINGLE_VIEW_CONF_SCALE=0.7`、`GRIPPER_ROI_FRAC=(0.25,0.5,0.5,0.5)`、`margin_conf` 映射（阈值处 0.6，到 0.95）、两证据冲突时置信减半、`target_in` 遮挡时 conf 0.4、IoU>0.9 视为分不开；
   - frozen/params 缺 `stall.ee_delta_max_m` 与 `gripper.settle_delta_m`（当前都回退到 `ee_move.ik_progress_min_m`=0.002 m），缺 `gate.cost_beta0/beta1`（代码默认 0.10 / 1.0）。
7. **阈值放宽的基线**：`etude/verifier/predicates.py` 的 `FROZEN_BASELINE` 复制了 thresholds.yaml v1 的值，"不得放宽"比较的是这份副本而不是 git HEAD（thresholds.yaml 尚未提交）。提交后可改为比较 HEAD。
8. **退回策略的细节**：退回上一已验证节点时保留当前夹爪回显，不恢复该节点通过时的回显（避免松手掉物）；每节点退回一次，第二次耗尽就 replan。设计没定。
9. **规划器返回 `[]`**：`reason=sequence_done` 时 runner 原地保持到 `step_lim`，不自宣成功（官方 judge 离线判）。schema 的 `validate_nodes` 拒绝空序列，所以只在这一个 reason 下接受空答复。
10. **记忆入库口径**：严格按 §3.3 `all(v.kind == "Pass")`，重试后才通过的回合不入库（端到端 demo B）。如要收，改 `etude/memory/writer.py:node_pass_errors`。
11. **实验设计里的预注册附加项**（experiments 组件提出，非设计原文）：N* 规则（seed 0/1 开发任务 200 回合中 SR 最高，平手取大 N）；M2 打乱记忆 = 与另一开发任务交换记忆（节点顺序打乱为未跑的备选）；N* ≠ 25 时才跑 h1_nstar。额外回合：M0/M1 A/A 各 +300、h1_nstar 最多 +400、M3 最终 T 800（设计写 400）、M2+ 比较集未定（T 或 V）。
12. **划分矛盾**：§3.2 把留出任务 seed 0/1 放进只看一次的 T，但 §5 M0/M1 要在留出任务 seed 0/1 上评测。实现按 §5 走，所以 T 的这部分在最终评测前已被看过；§6 条件 3 用的开发任务 seed 2 属于 T，推迟到唯一一次 T 评测。
13. **Gen 任务的 random 半场**：仅凭文件名分不出 25 std / 25 random 布局，§7 与 M2 的 random 半场检查需要另一个来源（例如官方结果里的字段）。
14. **M2 开关**：adapter 没有"记忆开/关/打乱"开关，M2 目前无法从脚本驱动；需要给 `run_harness.sh` 或 `deploy.yml` 加 memory-mode。
15. **两个命令生成器**：`experiments/expctl.py`（bootstrap、run-argv）已委托给 `etude/evolution/research_bridge.py` 的 `run_command`；bootstrap 文本仍各有一份，建议只保留 bridge 的。

## 5 Research OS 启用前用户要做的授权步骤

规则：`research/AGENTS.md`；详细用法：`docs/research_os_usage.md`。任何代理都不得填写 `--human/--quote/--channel`，也不得执行 `authority grant`。

1. **提交并推送代码**。Run 只能执行已 commit 且已推送到 origin 的源码，目前仓库没有 origin remote，大量文件未跟踪（`check_run_preconditions` 报 5 个阻塞：工作树脏、无 origin、`scripts/run_m0.sh` 未跟踪、无台账、缺 `huggingface_hub`）。
   - 先确认 `DEPLOY_SHA256`、`frozen/` 下的阈值与 `forbidden.yaml`（含第 4 节第 2 条的 allow-list 扩展）；
   - `git remote add origin …`，commit 并 push；
   - 安装 `huggingface_hub==0.35.3`。
2. **镜像 checkpoint**。evaluation Run 要求 `--checkpoint model=…` 且 `--archive-to` 是同一个 repo，而官方权重在 HF dataset 仓库 `RoboDojo-Benchmark/RoboDojo@35efbc7dedfdbeeb6e95fb749bd885d73d483e41`。用户自建 model 仓库 `<OWNER>/robodojo-pi05-arx_x5-joint-0`，按原路径上传，README 注明来源 revision，固定 40 位 commit，然后设置 `RESEARCH_CHECKPOINT=model=<OWNER>/<NAME>@<REV40>:ckpt/RoboDojo/Pi_05/RoboDojo-sim-arx_x5-joint-0/59999`。也可以改 vendored 规则，但那是用户的决定。
3. **初始化台账**（人类原话）：
   ```bash
   uv run python -m etude.evolution.research_bridge --check-views
   RESEARCH_HUMAN=<id> RESEARCH_QUOTE='<你的原话>' RESEARCH_CHANNEL=<渠道> RESEARCH_GOAL='<目标>' \
   RESEARCH_ACTOR=agent:<id> bash scripts/research_bootstrap.sh
   ```
   脚本依次做 `research init` → H-1/H-2/H-3 → 7 个 committed Experiment（每个后跟 `authority request --scope experiment_contract`）→ `pending-authority`。改过 `experiments/*/config.yaml` 后先重新生成脚本：`uv run python -m etude.evolution.research_bridge --bootstrap-script > scripts/research_bootstrap.sh`。
4. **审阅并授权契约**。用户逐个读 7 个 Experiment 的意图、决策规则（逐字取自设计 §3/§5/§6）与 view，再对每个 pending request 执行 `authority grant`（人类原话）。H-1/H-2/H-3 的假设句是代理根据 §1/§5/§6 拼写的，需要用户先审。建议在授权前给 `experiments/M3/view_T.json` 补列（如 h1、m2_plus），否则 §6 的 SR1 vs SR2（seed 2）和 M2+ vs H* 比较没有格子，以后要走人类授权的 view 扩展。
5. **回写别名**。把 CLI 实际分配的别名写回 `experiments/<M>/config.yaml` 的 `research.experiment / aa_experiment / t_experiment`（或设 `RESEARCH_EXPERIMENT`），然后 commit + push `research/ledger.json`。
6. **之后每个里程碑结论**：代理做 `observation create`、`decision propose`、`authority request`；用户做 `authority grant`；之后代理才能 `decision make`。Claim 的 supported/refuted 同样需要用户授权，`claim verify` 必须由非作者执行。
7. **约定**：verifier FPR 审计借用 evaluation 指标键（`successes` = 验证器 Pass 且官方失败，`trials` = 官方失败数），由 E-M0 的辅助槽 `stage=verifier_audit` 的参数 `metric_semantics` 注明，需要用户认可这一约定。

## 6 各组件未决问题（去重后）

### executor / runner / backends
- SafetyStop 只由 runner 按逐步关节速度（`safety.joint_vel_max_rad_s`）判定；PhysX 警告在 P2 不可观测，验证器读的 `physx_warning` 标志目前没有来源。
- runner 自判的判决（Blocked、SafetyStop、ee_move 丢目标的 InsufficientEvidence）`predicates` 为空；所有判决的 `t_chunk` 由 runner 用节点尝试的块序号覆盖。
- `frame.t` = runner 收到的 `update_obs` 次数（步时钟），`budget()` 由它和 `step_lim` 算。
- InsufficientEvidence 的二审：runner 有 `second_opinion(node, hist)` 钩子（计 1 次 VLM 调用，受 `verifier.max_inspect_per_node` 限制），验证器也有 `inspect_hook`；`harness_build` 目前两者都没接，二审只能由 P1 自己调 `inspect`。
- VLM 调用计数有三处：runner 的 `vlm_calls`、`HarnessState.begin_plan()`、未被使用的 `etude.core.budget.Budget`。上限都是 12，规划调用两边各计一次所以一致；接入二审后要统一到一处。pi 拒绝提示（如无 API key）时那次调用也已计数。另有每回合 LLM 请求上限（frozen `budget.vlm_requests_per_episode` = 48，按 pi 的 assistant `message_end` 计数，`rpc_driver` 用完即 abort pi，之后 `budget().calls_left = 0`），因为一次规划调用内可有多达 40 次工具回合。
- `planner/.pi/extensions/budget.ts`（设计 §4 目录树）没有写；预算硬停在 P2 侧（runner 计数 + rpc_driver 请求计数），`guard.ts` 另有每次规划的工具调用上限，并把 `ETUDE_MAX_TOOL_CALLS` 钳到 frozen `budget.max_tool_calls_per_plan`（40）。

### verifier / perceive
- `check()` 把 `hist[0]` 当节点开始帧、`hist[-1]` 当当前帧；runner 只传该节点尝试的块边界帧（不是整回合 `self.hist`）。
- 空 postcondition 判 InsufficientEvidence，不会空真 Pass；规划提示应要求至少一个后置条件（无 VLM 兜底节点的后置为空，所以它永远不会 Pass）。
- `stacked([...])` 自底向上；同名查询（三个 "bowl"）检测器分不开，判 InsufficientEvidence，规划提示应用可区分的描述。
- inspect 回答了但其他谓词仍不确定时，判决仍是 InsufficientEvidence 却标 `source=inspect_second_opinion`。

### memory / trace
- 记忆文件只追加（`op:add` / `op:retract`），去重用 `supersedes`，回滚写墓碑行，没有压缩。
- `SequenceEntry` 没有 `official_pass` / `created_from` 字段，这两项只存在 JSONL 行里；MCP `memory_lookup` 必须返回 `SequenceEntry.to_dict()`（已去掉 `lineage`、`evidence_refs`），不能返回原始行，否则 seed/layout 会泄漏给 P1。
- 关键帧引用格式：实现按目录树用 `<episode_id>/keyframes/<tag>_<cam>.png`（相对 `traces/`）；接口 docstring 与 §4 判决示例写的是 `kf/<ep>/…`。读取一律走 `TraceReader.resolve_ref`。
- `end_episode` 强制把 `official_*` 写成 null；P5 的官方结果只能另行传给 `write_if_passed` / `ingest_traces`。
- `etude/memory/writer.py` 离线读官方结果的字典键 `success` / `official_success`，若 AST 检查将来扫描字符串常量需豁免该模块。

### planner / MCP
- `inspect` 比 §4 签名多一个可选 `view` 参数（默认 head）；`frame_ref` 可以是 `latest`、runner 注册的关键帧 tag（`t<step>`），或 `traces/` 下的 `.png` 引用。
- `guard.required: true` 时，`guard.ts` 没上报状态的序列会被丢弃。
- `PiRpcPlanner` 现在在 runner 的后台线程里被调用（真实 pi 与 fake_pi 的 adapter demo 均已跑通）；`reset()` 时还在跑的旧规划结果会被丢弃。

### adapter / deploy
- `$RESEARCH_OUTPUTS` 只接受 HF 引用，所以 `run_m0.sh` 把本地结果目录写进 `$RESEARCH_RUN_DIR/etude_run.json`，只有 `--output NAME=REF` 才写 `$RESEARCH_OUTPUTS`。
- `install.sh` 用 `uv pip install -e` 装 XPolicyLab 与 openpi-client，不进 `uv.lock`；XPolicyLab 服务端还需要 msgpack-numpy、pydantic、h5py（不在 pyproject）。
- 未真实执行 `link_into_xpolicylab.sh`（会往只读的 RoboDojo 克隆写软链），只跑了 `--check` 与两个 `--dry-run`。

### evolution / frozen / scorer
- McNemar 用精确单侧检验（`math.comb`）：§4 收据示例 b=17、c=6 给 p=0.012，精确值是 0.0173（mid-p 0.0113），示例数字仅作示意。
- 真实回合运行器没写：`evolve_round` 注入 `run(ref, layouts)`，`TableRunner` 回放预计算表。真实流程 = 用户推送 `cand/<id>` → Research OS 评测 Run → P5 `scorer/run_official.sh --gate-out`；每布局 `vlm_tokens`、`wall_s` 来自 trace 的 EpisodeRecord。
- 门通过（收据 decision=accept）时**不合并**：`state.pending` 记下 `cand/<id>`；用户授权 Research OS Decision 后由 `round.apply_accept(cfg, state, runner, grant_event_id)` 在主 checkout 基线分支上 `--no-ff` 合并（台账存在时 grant id 必须出现在 `research/ledger.json`），写 `<rid>.applied.json`；用户拒绝则 `discard_pending`。合并失败会 `git merge --abort`。`evolve_round` 开始前检查：ε 已由 A/A 设定（`set_eps`，记来源）、无待决候选、主 checkout 在基线分支且除 `research/ledger.json`、WORK、WORKSTATE 外干净；L1–L3 任何异常也写拒绝收据（`evaluated_on.error`）。`push()` 不自动调用。
- `ast_check` 的文本扫描跳过 `planner/` 下的 `.ts`；自由文本里只标记带下划线的禁名（允许 prompt 里出现普通单词 success）。Python 代码里：任何非 docstring 字符串常量含带下划线的禁名即拒（`deny_string`），常量折叠（`+`、`%`、`.format`、`.join`、切片、rot13、单次赋值的名字）后的字符串等于/含禁名即拒；非常量名的 `getattr/hasattr/attrgetter/__getattribute__`、它们的别名、`vars()`/`__dict__`/栈帧/`gc`/`sys.modules` 反射全仓拒绝，例外只在 `forbidden.yaml ast_scan.reflection_allow`（按 `路径::限定名`）。运行期代码（`deny_imports.scope`）不得 import `etude.scorer`/`scorer`/`etude.frozen` 内部（`leakage`、`ast_check` 除外）。
- `scripts/` 已进入 AST 扫描范围；`scripts/make_split.py`（只列 `Eval_Layout` 文件名的离线划分脚本）显式豁免。

### research_bridge / experiments
- 本仓库无 origin、无台账，所有命令只用 vendored CLI 的 argparse 解析过，没有对真实台账执行。
- `scripts/research_bootstrap.sh` 是生成物，config 改动后要重新生成。

## 7 和设计文档的已知偏差

| 设计 | 实现 | 说明 |
|---|---|---|
| §4 目录树 `harness_server/{…}`、`mcp_server.py`、`budget.py` | Python 包 `etude/`（`etude/runner.py`、`etude/mcp/`、`etude/core/budget.py` 等） | 只是布局不同，接口与字段照 §4 |
| §2 MCP(stdio) | streamable-http on localhost | 见第 4 节第 5 条 |
| §4 `planner/.pi/extensions/budget.ts` | 未实现 | 预算在 P2 硬停 |
| §5 M0 `eval_batch:true`，num_envs 4 | 只支持 `eval_batch:false` | 见第 4 节第 3 条 |
| §8.3 规划 claude-sonnet-5-5 | 配置为 openai/gpt-5.4 | 待用户登录 provider 时改 |
| §2 allow-list | 多 `robot_manager.get_robot_by_arm_name` | 待签字：放在 `forbidden.yaml deploy_task_env_pending_signoff`，ast_check 报 `pending_signoff` 警告（`--strict` 为错误），句柄只能直接作为 `get_end_effector_real_val` 的第一个参数 |
| §4 目录树 `evolution/lineage/` | 收据与 `EvolutionState` 默认在仓库外 `~/.local/state/etude/<repo>/lineage`（`$ETUDE_LINEAGE_DIR`） | 收据含 V 结果；仓库内会被提交并出现在 P6 worktree。`evolution/lineage/` git-ignore 且列入 `deny_paths` |

## 8 审查第 1 轮修复后新增的未决事项

- **需用户签字**：`frozen/params/thresholds.yaml` 新增 `budget.vlm_requests_per_episode`（48）与 `budget.max_tool_calls_per_plan`（40）；`forbidden.yaml` 新增 `deny_imports`、`deploy_import_allow`、`deploy_task_env_pending_signoff`、`ast_scan.reflection_allow`、11 个防御性禁名、`evolution/lineage/` 禁路径、扩展的泄漏正则（`distances`、`axis_values`、`node_digits`、扩展的 `layout_ids`、任务名规范化匹配）。`deploy.py` 本身没改，`DEPLOY_SHA256` 不变。
- **deploy.py 结构比对**：`ast_check` 内嵌 XPolicyLab bb9a0b5 官方 `demo_policy/deploy.py` 的两个函数；`eval_one_episode` 去掉 `_augment(TASK_ENV, ·)` 后必须逐 AST 相等，`eval_one_episode_batch` 必须是官方循环或 `raise NotImplementedError`。XPolicyLab 升级时要同步这段常量。
- **划分来自 P5**：记忆写入要求 P5 的官方结果映射带 `task`、`seed`、`layout_id`（`etude.core.splits.classify` 判 E/V/T/spare），trace 里的 `split` 不再被信任（默认 None）。P5 如何把 harness 的 `episode_id` 与官方 `details[i].layout_id` 对上（按回合顺序与 `episodes.jsonl`）还没写成脚本。
- **L3 的 unstable**：候选缺失或 unstable 的 V 布局按候选失败计（不再从配对里丢掉），任一侧 unstable 率 > `scorer.unstable_rate_review` 即拒；两侧 unstable 布局集合不同时没有直接拒（审查建议直接拒），因为 PhysX 非确定性下会频繁误拒，而按失败计已去掉"把要输的布局标 unstable"的收益。
- **P6 编辑会话守卫**：`editor/.pi/extensions/guard.ts` 已写（读路径白名单：worktree 的 `evolvable/`、`docs/` 与 `$ETUDE_EDITOR_EVIDENCE_DIRS`；只写白名单面文件；禁 bash），但还没有驱动真实 P6 pi 会话的代码，也没有 P5 导出 E 划分证据目录的脚本。
- **M3 视图**：`experiments/M3/view.json` 的 candidate 列改为 `incumbent_v0…v4, r1…r4`（每个现任版本一格），需要在授权契约前由用户确认；收据 `evaluated_on.research_runs` 记 L3、现任 V、A/A 的 Run 标签，`receipt_to_observation` 把它们都写进 `--evidence`。
- **退出时落盘**：`Model.__init__` 装 SIGTERM/atexit 处理器，最后一回合在进程被杀前 `reset()` 落盘；`NodeRunner` 每块重写一次 `episode.json`（`_trace.partial: true`）。真实 XPolicyLab 服务端是否在主线程构造 `Model`（SIGTERM 处理器只能在主线程安装）未核实，否则只剩 atexit 与逐块落盘。
- **P3 连接**：`Pi05RemoteBackend` 不再用上游 `WebsocketClientPolicy`（其等待无上限），改为同协议的有界客户端（连接 ≤30 s、单次推理 ≤60 s、断线重连一次）。


## 9 最终核验记录（2026-10-11）

只运行，不改源码。全部在 fakes 上，不连真实模型、不读 checkpoint。

| 项 | 命令 | 结果 |
|---|---|---|
| 端到端假回合 | `uv run python scripts/demo_fake_episode.py --out <tmp>` | 通过，14/14 检查 ok |
| 21 个组件 demo | `uv run python -m <module> --demo`（`Makefile` 的 `DEMO_MODULES`） | 21/21 退出码 0 |
| 划分 / 视图 | `scripts/make_split.py --print`、`experiments/expctl.py check-views` | 通过；划分无 missing |
| 干跑 | `scripts/m0_sweep.sh --dry-run --force --ns 25 --seeds 0`、`scripts/aa_rerun.sh --milestone M0 --nstar 25 --reps 1 --dry-run --force`、`scripts/run_m0.sh --task stack_bowls --seed 0 --eval-num 2 --n 50 --dry-run` | 通过（run_m0 因未链接而跳过 `robodojo.sh --dry-run`） |
| AST | `uv run python -m etude.frozen.ast_check`、`--selftest` | 0 error / 2 warning；55 个作弊候选全拦，4 个对照通过 |
| deploy.py 哈希 | `uv run python -m etude.adapter --deploy-hash` | actual = recorded = `a75b3b4f…` |
| Research OS CLI | `uv run python research/tools/research --help` | 通过 |
| research_bridge | `uv run python -m etude.evolution.research_bridge --demo` | 退出码 0；前置检查如实报 BLOCKED（工作树脏、无 origin、无台账、缺 `huggingface_hub` 等），符合预期 |
| 链接检查 | `scripts/link_into_xpolicylab.sh --check` | 退出码 1，`<none>`：尚未链接（预期，需用户同意） |

文件数：`find etude deploy planner evolvable frozen evolution scorer experiments scripts research -type f | wc -l` = 161（无 `__pycache__`）。

小瑕疵（不影响 demo）：`etude.evolution.aa_noise --demo` 用 `aa_from_counts(counts)` 而不传 trials，输出里 `"trials": [0, 0, 0]`；真实路径（`--metrics` 读 Research OS 指标文件）会填 trials。

### 9.1 与设计 §4 目录树的差异

| 设计 §4 | 实际 | 性质 |
|---|---|---|
| 顶层 `etude/` 包含全部 | 仓库根即项目根；`deploy/ planner/ evolvable/ frozen/ evolution/ scorer/ memory_db/ traces/ experiments/` 在根下，Python 代码在包 `etude/` | 布局 |
| `harness_server/{executor,verifier,perceive,memory,trace}` | `etude/{executor,verifier,perceive,memory,trace}/`，文件名照 §4（另有 `executor/common.py`、`servo_demo.py`、`perceive/geometry.py`、`memory/sanitize.py`、`trace/{reader,writer}.py`） | 布局 |
| `harness_server/mcp_server.py` | `etude/mcp/server.py`（+`state.py`、`validate.py`） | 布局 |
| `harness_server/budget.py` | `etude/core/budget.py` | 布局 |
| `planner/.pi/extensions/budget.ts` | **缺**（预算在 P2 硬停，见第 6、7 节） | 未实现 |
| `planner/` | 多 `planner/config.yaml` | 新增 |
| `frozen/ast_check.py` | `etude/frozen/ast_check.py` | 布局 |
| `frozen/schema/{node,verdict}.json` | 有，另多 `frozen/schema/receipt.json` | 新增 |
| `evolution/{gates.py,aa_noise.py,leakage.py}` | `etude/evolution/{gates,aa_noise}.py`，`leakage.py` 在 `etude/frozen/leakage.py`；另有 `lineage.py`、`round.py`、`worktree.py`、`research_bridge.py` | 布局 |
| `evolution/lineage/` | 目录在但只有 `.gitkeep`，收据默认写仓库外 `~/.local/state/etude/<repo>/lineage` | 偏差（第 7 节） |
| `scorer/audit/` | 只有 `.gitkeep`；审计代码在 `etude/scorer/audit.py`、`results.py` | 布局 |
| `memory_db/<task>/sequences.jsonl` | 只有 `.gitkeep`，运行时生成（`*.jsonl` 被 git-ignore） | 运行时 |
| `traces/<episode_id>/{planner.jsonl,trace.jsonl,keyframes/*.png,videos→官方}` | 只有 `.gitkeep`；demo 产出 `planner.jsonl`、`trace.jsonl`、`episode.json`、`keyframes/*.png`，无 `videos` 链接 | 运行时；`videos→官方` 未实现 |
| `experiments/{M0..M3}/{config.yaml,prereg.md,results/}` | 有 `config.yaml`、`prereg.md`、`view*.json`；**无 `results/`**；另有 `experiments/expctl.py`、`experiments/splits.json` | `results/` 缺（结果走 Research OS 与 `runs/`） |
| 无 | `editor/.pi/extensions/guard.ts`（P6 守卫）、`research/`（vendored Research OS）、`scripts/`、`Makefile`、`runs/`（git-ignore） | 新增 |

## 10 下一步要在真实环境验证的清单（按顺序）

每一步都是"第一次在真实环境上确认"，不是测试。标【用户】的只能由用户本人执行或授权，代理只准备命令。

1. **commit 并加 origin**【用户】
   - 先审：`deploy/XPolicyLab/policy/Etude/DEPLOY_SHA256`（`a75b3b4f…`）、`frozen/params/thresholds.yaml`、`frozen/forbidden.yaml`（含 `deploy_task_env_pending_signoff` 的 `get_robot_by_arm_name` 扩展与第 8 节新增项）、`experiments/*/view*.json`。
   - `git remote add origin <url>`；`git add -A && git commit`；`git push -u origin main`。
   - 验证：`git status --short` 为空；`git rev-parse HEAD` 等于 `git rev-parse origin/main`；`uv run python -m etude.frozen.ast_check --strict` 在签字后（把扩展移出 pending）为 0 error。
   - 装 `huggingface_hub==0.35.3`（第 3 节），再跑 `uv run python -m etude.evolution.research_bridge --demo`，前置检查只剩"无台账"。
2. **research init 的人类授权**【用户】
   - 镜像 checkpoint 到自建 HF model 仓库并固定 40 位 revision（第 5 节第 2 步），设 `RESEARCH_CHECKPOINT`。
   - 重新生成并审阅 bootstrap 脚本：`uv run python -m etude.evolution.research_bridge --bootstrap-script > scripts/research_bootstrap.sh`；用户本人以原话填 `RESEARCH_HUMAN / RESEARCH_QUOTE / RESEARCH_CHANNEL / RESEARCH_GOAL` 执行。代理不得代填。
   - 用户审 H-1/H-2/H-3 与 7 个 Experiment 契约后逐个 `authority grant`；别名回写 `experiments/<M>/config.yaml`，commit + push `research/ledger.json`。
   - 验证：`uv run python research/tools/research validate` 无错；`research pending-authority` 为空。
3. **链接 XPolicyLab**【用户同意后执行】
   - `bash deploy/XPolicyLab/policy/Etude/install.sh`（`uv pip install -e` XPolicyLab 与 openpi-client，不进 `uv.lock`；服务端还需 msgpack-numpy、pydantic、h5py）。
   - `bash scripts/link_into_xpolicylab.sh`（向 `~/Documents/RoboDojo/XPolicyLab/policy/Etude` 写软链，改动只读参考克隆，需用户明确同意）。
   - 验证：`scripts/link_into_xpolicylab.sh --check` 退出码 0；`scripts/run_m0.sh --task stack_bowls --seed 0 --eval-num 2 --n 50 --dry-run` 不再显示"not linked yet"，并执行 `robodojo.sh --dry-run`。
4. **起 serve_policy（P3）**
   - checkpoint `~/models/robodojo-ckpt/ckpt/RoboDojo/Pi_05/RoboDojo-sim-arx_x5-joint-0/59999` 最终核验时约 12 GB、无下载进程，但完整性未校验：先对照 HF 文件清单（大小/哈希）确认。
   - 第二张卡：`cd ~/Documents/RoboDojo/XPolicyLab/policy/Pi_05/openpi && uv run scripts/serve_policy.py policy:checkpoint --policy.config=pi05_base_aloha_full_sim_arx-x5_seed_0 --policy.dir=<ckpt dir>`（端口 8000，与 `deploy.yml backend.port` 一致）。
   - 验证：`uv run python -m etude.backends.pi05_remote --demo` 改连真服务能收到 metadata 帧与 `(50,14)` 动作块；核对输出是绝对关节目标（第 3 节第 1 条）；记录单次推理延迟。
5. **M0 烟测**
   - 先 2 回合：`scripts/run_m0.sh --task stack_bowls --seed 0 --eval-num 2 --n 50 --backend-host <p3-host>`；通过后再在 Research OS 下以 `research run` 包装同一入口（工作树内 PYTHONPATH 指向工作树，第 3 节第 11 条）。
   - 验证：官方 `_result.json` 存在；`etude.scorer.results` 汇总与官方 `summarize_result.py` 一致（§8.4 #2）；`$RESEARCH_METRICS` 写出 `{successes,trials,success_rate}`；每回合 `traces/<ep>/` 有 `trace.jsonl`、`episode.json`、关键帧，`official_success` 为 null；AST/allow-list 运行期 0 违规。
   - 同时完成第 3 节的开工测量：4 点 ee 探针与腕相机标定（#6）、夹爪开度单位与阈值（#5）、首帧是否复位位姿（#8）、单回合墙钟与 t_step（#13）、`get_action` >120 s 的行为（#4）。
   - M0 墙钟预算按 `eval_batch:false`（num_envs 1）重估并由用户重新批准（第 4 节第 3 条），然后才开 `make m0`。

## 10 Recova 补丁审查后仍开放的低优先级项（2026-10-11）

来源：`docs/research/recova_2610.01178.md` 补丁 P1–P3、P10 的实现与两位审查者的发现；high/medium 已修，以下为 low，未改：

1. `etude/verifier/predicates.py` `scene_not_moved`：t=0 未检测到、当前检测到的物体，会按当前框对 I_0 做差分而被判"moved"（例如 t=0 藏在抽屉里的物体）。M0 前决定是否把"参照帧未检测到"判为 null。
2. `etude/runner.py` `_recheck_anchor` 跳过 `arm_home`（按末端位姿退回不恢复关节角）。设计 §3.3 的"复验锚点后置条件"在这一谓词上不成立；要么按关节空间退回，要么把 arm_home 定义为不参与复验。
3. 设计 §7 的"同一失败签名重复 2 次直接 replan"（P4）未实现，文档已标为建议。
4. `scene_intact=false` 触发 replan 后，runner 把参照帧换成当前帧；`etude/planner/request.py` 的提示和 `verdict.json` 的字段说明仍写"相对回合开始"。措辞待统一。
5. `_PlanJob` 丢弃过期结果并重发时，`vlm_calls` 会记两次；当前没有路径能触发（规划期间执行冻结），接入二审或异步后要改成只记一次。
