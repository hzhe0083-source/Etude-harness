# PhysicalRSI 1.0: Recursive Self-Harness for Scaling Embodied Skills（无论文；项目页 + 仓库 3c8cf7f + XPolicyLab 适配器 f6e309e）

日期 2026-10-11。PhysicalRSI 没有论文。本卡关于 PhysicalRSI 的事实只取自本轮写成、经过复核的两份阅读报告。第一份读的是 XPolicyLab 里的评测适配器：本地 `~/Documents/RoboDojo/XPolicyLab/policy/physicalRSI`，XPolicyLab HEAD f6e309e，该目录没有本地改动。它由五份只读代码阅读加一轮审计复核组成，两者冲突时以复核为准；第 8 节的特权访问逐条命中表（F01–F36）就出自这轮复核。第二份讲它怎么做自进化，读的是主仓库 PhysicalRSI/PhysicalRSI 的浅克隆（commit 3c8cf7f）、项目页提取文本和页面内嵌 JSON。我们 10-10 的笔记 N1（`docs/research/embodied_rsi_update_2026-10-10.md` 第 71–126 行）只在第 11 节用来记录更正。写卡人另做了六处核对，全程没有运行、安装、导入或下载任何 PhysicalRSI 代码：

1. 用 cmp 和 diff 比两份副本。`PhysicalRSI_baselines/robodojo/` 整棵树逐字节相同，只有 `harness_bundle.py` 不同；仓库一侧多出 `releases/`、`provenance.json`、`xpolicylab_policy/` 和一个未初始化的 XPolicyLab 子模块。`PhysicalRSI_core/self_harness/__init__.py`、`self_harness/selection.py`、`lineage/__init__.py` 和 `PhysicalRSI/commands.py` 两边不同，这几处的行号按副本分开写。
2. 两份报告对 `rb/workflow.py:489-494` 的解读不同。按原文 `rb/workflow.py:432-494` 和 `rb/skill_selection.py:61-79` 核对，两种说法都成立（见 6.3 第 6 条）。
3. 一份报告说"适配器把完成状态写死"，另一份说"原生运行失败直接抛错"。按 `rb/workflow.py:598-614` 和 `rb/native_observer.py:42-67` 核对，两者也不矛盾（见 6.3 第 7 条）。
4. 项目页"#1"的日期，按页txt:366-384 和页面数据里的 metric.source 字段核对。
5. 仓库顶层目录用 ls 列出（第 2 节）。
6. 第 9、10 节涉及我们仓库的事实，写卡人逐条读了当前工作树，包括开发者 2026-10-11 已提交的三处修复（commit 9e83a8c）。

**证据标签。** 【页】页面或文档自述；【码】代码实现；【桩】演示、桩或没有调用方的惰性代码；【缺】仓库或本地缺失；【推】推断。我方文件也用这五个标签，靠路径前缀区分。查不到的写"未查到"。全卡只引两处英文原文，都不到 15 词。

**路径缩写**

- `xp/`：适配器根目录 `XPolicyLab/policy/physicalRSI/`（f6e309e），含 eval.sh、deploy.yml、deploy.py、model.py、README.md、download_assets.py、install.sh、setup_eval_*.sh。
- `rb/`：`PhysicalRSI_baselines/robodojo/`。适配器里它在 `xp/runtime/` 下，仓库里在根下；两份逐字节相同，只有 `harness_bundle.py` 不同（本卡引用的 `rb/harness_bundle.py:31` 是适配器版）。`rel/` = `rb/releases/robodojo-v1/`，只在仓库里有。
- `core/`：适配器的 `xp/runtime/PhysicalRSI_core/`。`PC/`：仓库的 `PhysicalRSI_core/`；`sh/` = `PC/self_harness/`。两份不同，行号不能互换。
- `prsi/`：适配器的 `xp/runtime/PhysicalRSI/`；`eh/` = `prsi/Embodied_Harness/`。`P/`：仓库的 `PhysicalRSI/`。
- `AR/` = 仓库 `PhysicalRSI_Autoresearch/`；`AE/` = 仓库 `PhysicalRSI_demos/auto_engineering/`；`rdocs/` = 仓库 `docs/`。`configs/`、`tests/`、`AGENTS.md`、`.gitignore` 指仓库根下的同名项。
- `XPL:` = XPolicyLab 根（f6e309e）；`RD:` = RoboDojo 根（266130a）。这两类路径只用来说明适配器怎么被调用。
- `页txt:N` = 项目页提取文本第 N 行；`页json:<名>` = 页面 HTML 内嵌 JSON 的数据块。
- 我方：`设计 §x` = `docs/design/vla_harness_v1.md`；`N1:行` = 10-10 笔记；`part_C:行` = `docs/research/part_C_robodojo_protocol.md`。以 `etude/`、`deploy/`、`frozen/`、`evolvable/`、`experiments/` 开头的路径都属于本仓库。

## 1 一句话

- **没有论文。** 公开物只有三样：项目页、一个预览版代码仓库、XPolicyLab 里的评测适配器【页】（N1:108；页txt:350-353 的 Architecture Code、Baseline Code、XPolicyLab 三个链接）。
- **评测时它是一个回合级执行器选择器。** 每个回合开始时调用一次外部视觉大模型，从三类执行器里选一个，一直用到 reset。三类是：π0.5、π0.5 加稀疏视觉历史、22 个按任务编写的代码程序之一【码】（xp/model.py:6-33 → rb/skill_model.py:132-230, 247-248）。
- **自进化回路是真的，但窄。** 仓库里的回路每轮只产 1 个子代，只接 stack_bowls 和 put_bottles_into_dustbin 两个任务，提案者只收纯文本；接受门默认 0 容差，不做统计检验【码】（rb/workflow.py:138-164；rb/configs/tasks.json:4-7；rb/proposal.py:44-66）。
- **榜单上起作用的东西不在仓库里。** 22 个程序和 fold_clothes 的 R10–R23 进化记录都不在，也没有任何进化收据把它们和这条回路连起来。发布包的来源文件写着 "No verified evolution receipt attached"【缺】（rel/provenance/manifest.json:3-10）。
- **榜单数字。** 总分 36.27，SR 31.38%【页】（页txt:366-373）。项目页说它排第一，依据是官方榜的一份快照，页面数据里的来源字段写作 "updated 2026-10-03; verified 2026-10-08"【页】（页txt:377-384；页json:settings-gallery 各条 metric.source；页json:benchmarkEntries）。页面内嵌榜单里的 VPP2-Preview 是 31.40 / 25.62【页】（页json:benchmarkEntries）。我们 10-09 读官方榜时，它已降到第 2，被 VPP2 的 39.26 / 32.26% 超过【页】（N1:93；VPP2 的数字本轮没有复核）。
- **对我们有什么用。** 可借的是工程纪律：冻结之后才生成验证布局、给评测器身份上锁、写阶段收据、在进程内装文件审计钩子、开端口前预载模型。不能借的有两样：一是它的评测路径，实质上按任务分派，有 P13 风险；二是它的接受门，0 容差又不做统计检验【推】（第 4、6、10 节）。

## 2 公开物是什么

### 2.1 三处公开物和一份外部资产

| 公开物 | 版本 | 内容 | 出处 |
|---|---|---|---|
| 项目页 mmlab.hk/research/PhysicalRSI | 本地存档的 HTML 约 22 MB | 主式与 System 1/2 分工；`Darwinian` 多候选的说法；52 个设置的视频画廊；fold_clothes 进化图；212 段技能片段画廊；麻将插图 | 【页】N1:74；页txt:409-807, 821-1273；页json:settings-gallery、fold-clothes-evolution、skill-clips |
| 仓库 PhysicalRSI/PhysicalRSI | 3c8cf7f，浅克隆 | 核心库、RoboDojo 端口、Autoresearch、演示、文档、测试，目录见 2.2 | 【码】目录列表 |
| XPolicyLab 适配器 policy/physicalRSI | XPolicyLab f6e309e，提交于 2026-10-11 21:46 +0800 | 评测入口（eval.sh、deploy.py、model.py、deploy.yml）加 runtime/ 下三个包的副本 | 【码】xp/deploy.py:1-6；xp/model.py:6-33 |
| 技能资产 release | v4 | 22 个程序、两份 π0.5 权重、openpi 分支，按 sha256 清单下载 | 【缺】xp/download_assets.py:14-15；xp/README.md:13-27；rel/download_assets.py:14-15 |

### 2.2 仓库 3c8cf7f 的目录

| 目录或文件 | 内容 | 出处 |
|---|---|---|
| `PhysicalRSI/` | 应用层和命令行，有 `/rsi`、`/evolve`、`/play` 三个命令；`rsi.py` 里的 harness 描述只是数据，`notes` 和 `budget` 没有读者 | 【码】P/commands.py:118-125；【桩】P/rsi.py:50-149；rb/workflow.py:99, 131, 200 |
| `PhysicalRSI_core/` | self_harness（冻结、选择、阶段收据）、lineage（比较并交换提交）、infra（chroot 加 seccomp 隔离）、README | 【码】sh/__init__.py:56-357；PC/lineage/__init__.py:24-220；core/infra/isolation.py:76-86 |
| `PhysicalRSI_baselines/robodojo/` | RoboDojo 端口。内含评测路径文件（与适配器同款）、RSI 端口 `workflow.py`、`layout_sampling/`、`releases/robodojo-v1/`（发布包描述）、`xpolicylab_policy/`（适配器脚本和 README）、XPolicyLab 子模块（393f730） | 【码】目录列表；rb/workflow.py:336-620 |
| `PhysicalRSI_baselines/opentrons_liquid_handling/` | Autoresearch 用的移液协议例子 | 【码】PhysicalRSI_baselines/opentrons_liquid_handling/policy.py:24-72 |
| `PhysicalRSI_Autoresearch/` | run.py、study.py、heartbeat.py、records.py、program.md | 【码】AR/study.py:19-30；AR/heartbeat.py:16-95 |
| `PhysicalRSI_demos/` | auto_engineering 是唯一真跑过一轮核心 SelfHarness 的 Isaac Sim 演示；另有 runtime_evolution.py 等 | 【码】AE/harness.py:83-165 |
| `docs/` | architecture、autoresearch-pipeline、research-protocol、implementation-status、baseline-scope、xpolicylab-audit、migration.json 等 | 【页】目录列表 |
| `configs/`、`examples/` | `robodojo.curated.json` 是一次 pilot：两个任务各 5 回合、seed 1、SR 1.0，自己标明不是资格声明。另有 `fold_clothes.task.json` 和 `examples/fold_clothes/`，本卡未读 | 【桩】configs/robodojo.curated.json:8-39；未读部分未查到 |
| `tests/` | ImprovementCampaign、CaseProtocol 等安全件只在测试里构造 | 【码】sh/campaign.py:1-6；rdocs/research-protocol.md:92-96 |
| `AGENTS.md` | 编码 agent 在集群上按 benchmark 目标工作，每 30 分钟审阅一次，从 /cpfs02 上的 heartbeat 恢复 | 【页】AGENTS.md:41-63 |
| 根下其他 | LICENSE、NOTICE、CITATION.cff、pyproject.toml、scripts/ | 【码】目录列表 |

### 2.3 本地缺什么

- **适配器一侧**：`skill-assets/`、`skills.json`、`results/`、`runtime_env/`、`checkpoints/` 都不存在，`programs.json` 也不在【缺】（适配器目录列表；rb/skills/code_policy.py:8-14）。两份 π0.5 权重和 openpi 分支同样要从 release 下载【缺】（xp/download_assets.py:14-15；rb/skills/sparse_backend.py:14-15）。
- **适配器副本缺 `PhysicalRSI_demos`**：不带任务运行 `/evolve` 会抛 `ModuleNotFoundError`，控制台不捕获【缺】（prsi/application.py:288, 411-414；prsi/cli/terminal.py:98）。
- **原生运行时跑不起来**：原生运行时的 JSON 要从已安装的资产构建【缺】（rb/native_package.py:15-44；rb/release_preflight.py:14, 46-48）。`native_worker` 要求 RoboDojo 的 `main.py` 打过补丁，本地版本没有这个补丁，会被拒绝【码】（rb/native_worker.py:76-79）。
- **仓库一侧没有任何运行记录**：没有一轮 RoboDojo 的 round、lineage 或 selection 收据。发布包的 `harness_state/state.json` 是手写的静态描述【桩】（rel/harness_state/state.json）。评测记录被 .gitignore 排除，release 标 `evaluation_ready false`【缺】（.gitignore:34-36；P/application.py:368-398）。
- **子模块和外部工件**：XPolicyLab 子模块（393f730）在克隆里是空的【缺】（git submodule status）。fold_clothes 战役的工件路径全在 /cpfs02 下，仓库里没有【缺】（页json:fold-clothes-evolution）。22 个程序用过哪些布局，没有记录【缺】（rdocs/migration.json:19, 31-34）。
- **结论**：两份副本在本地都跑不起来。凡涉及 22 个程序内部的说法，本卡都只能写成缺失或推断【推】。

## 3 评测路径架构

### 3.1 进程

| 进程 | 怎么启动 | 做什么 | 通信 | 出处 |
|---|---|---|---|---|
| bash 驱动 | `xp/eval.sh` | 要求设置 agent 的 API key 环境变量（也接受 OpenAI 或 ARK 的变量名）和技能配置路径；把 JAX 显存占比设为 0.3；用 setsid 起服务端，最多等 1200 s 端口就绪，再前台跑客户端；退出时向服务端进程组发 SIGTERM | — | 【码】xp/eval.sh:3-7, 32-38, 42-56, 60-71 |
| 策略服务端 | `XPL:setup_policy_server.py`，PYTHONPATH 为 runtime、RoboDojo、XPolicyLab 三项 | 开端口之前先构造 `Model`，构造时预载两份 π0.5；所有调用在一把锁下串行；任何公共方法都能被远程调用 | 对客户端走 WS + msgpack；对 Agent 走 HTTPS；对代码 worker 走 Pipe | 【码】xp/setup_eval_policy_server.sh:61-77；XPL:setup_policy_server.py:31-32, 60-70；rb/skill_model.py:104-127；XPL:client_server/ws/model_server.py:398-399, 460-477 |
| 代码 worker | 每回合每个被选中的程序一个，`Popen(..., start_new_session=True)` | 执行下载来的 launcher 和绑定模型，拉起感知 sidecar，检查外部服务健康 | 继承的 Pipe，消息只有 reset、update_obs_batch、get_action_batch 三种 | 【码】rb/skills/program_service.py:61-70；rb/skills/program_worker.py:19-21, 38-73；内容【缺】 |
| 环境客户端 | 默认是 RoboDojo 的 `src/eval_client/main.py`；debug 模式用 `XPL:utils/debug_env_client.py` | 调 `deploy.eval_one_episode(TASK_ENV=self, model_client=…)` | 连服务端 | 【码】XPL:utils/setup_env_client.sh:29-47；xp/setup_eval_env_client.sh:27-41；RD:src/eval_client/eval_env.py:287-310 |
| 外部 Agent API | endpoint 和模型取自 skills.json 或两个环境变量 | 每个 env 每回合调 1 次 | 服务端发起 | 【码】rb/agent_planner.py:67-74 |

### 3.2 一个回合怎么走

前提是 `eval_batch: false`，即单环境【码】（xp/deploy.yml:12；RD:src/eval_client/main.py:143-146）。

1. 客户端先 reset，再把 `env.get_obs()` 交给 `update_obs`【码】（rb/evaluation.py:73-86）。
2. 服务端发现该 env 还没有会话，进入 `_start_episode`【码】（rb/skill_model.py:247-248, 132-230）：
   - 要求 instruction 非空（:133-135）；
   - 调一次 agent，校验返回的计划，写 `agent-decision.json`（:136-148）；
   - 若选的是程序库，必须恰好指定一个 operation，并把它解析成 code-policy 描述（:150-163）；
   - 把 operation 计划冻结写盘，用环境变量把路径和摘要交给 worker（:172-196；eh/skills/episode_plan.py:8-9, 77-88）；
   - 加载非预载的技能；若选中代码程序，worker 进程就在这一次 WS 请求里启动（:197-199）；
   - 写 `episode.json`（:213-228）。
3. 观测交给所选技能的 `update_obs_batch`【码】（rb/skill_model.py:250-253；rb/execution_skills.py:104-117）。
4. 客户端调 `get_action`。服务端只在动作队列为空时推理，每次调用都 fsync 写一份 `execution.json`【码】（rb/skill_model.py:284-298, 307-312）。
5. 客户端开环执行整个动作块，每步之间调一次 `update_obs`，遇到回合结束就提前退出【码】（rb/evaluation.py:82-86）。
6. 回合结束时 reset 清掉会话和队列，关闭代码 worker。`on_trial_end(result)` 直接调 reset，结果被丢弃【码】（rb/skill_model.py:321-342）。
7. 任何一次异常都会置 `failed=True`，此后每次调用都抛错，直到下一次 reset【码】（rb/skill_model.py:236-237, 256-259, 315-319）。

环境侧只用了四类公开方法：取运行中 env 列表、判回合结束、取观测、执行动作。它不读私有属性、不读分数，也不发 trial_end【码】（rb/evaluation.py:20-86；复核 F02）。

### 3.3 VLM 路由器看到什么、剔除什么

调用位置在 rb/skill_model.py:136-138【码】。

**进入上下文的内容**

- 指令【码】（rb/skill_model.py:133-138）。
- 状态白名单：臂关节、EE 关节状态、EE/TCP 位姿、delta 位姿【码】（rb/agent_planner.py:16-24）。
- 每个相机的 color、内参、外参和 shape；首帧 RGB 编成 PNG data URL【码】（rb/agent_planner.py:25, 32-36, 92-104）。
- 冻结记忆【码】（rb/skill_model.py:88-90；rb/agent_planner.py:110-111）。
- 组合目录和 22 个 operation 的目录。每项能力的 description、observation_cues、execution_scope 原文都保留【码】（rb/agent_planner.py:113-115；复核 F21、F34 的更正）。

**被剔除的内容**

- 观测里任意层级的 `task_name, benchmark_task, layout_id, seed_id, env_idx, depth, segmentation, pointcloud`【码】（rb/agent_planner.py:85）。
- 目录里的 `implementation, source, source_name, provenance, capability_id, skill_name, configuration, evidence`【码】（rb/agent_planner.py:45-46，以复核为准）。

**调用参数与提示**

- `temperature=0`，`response_format=json_object`；只有传输错误会重试【码】（rb/agent_planner.py:157-158, 163-172）。
- 实际生效的系统提示有三个要点：某项能力覆盖所请求的操作时，选恰好一个 operation；有明确的兼容支持就用结构化 operation，否则用学习型组合；标识符一律当作不透明的 handle【码】（rb/agent_planner.py:129-149, 144-145）。
- 默认的 subgoal 提示里另有一句"不要选择模型、checkpoint 或独立的任务策略"，但它只在没被使用的 AgentLoop 路径上生效，评测路径上被上面的组合选择提示取代【码】（rb/agent_planner.py:117 对照 :129-149）。

**arx_x5 配置下的实际输入**：观测里没有内参和外参，所以 VLM 实际只拿到 RGB、状态和文本【码】（RD:env_cfg/arx_x5.yml:14-18；RD:env/observation_manager/obs_manager.py:49-53）。

**模型是哪个**：未查到。配置里只有占位符，`ARK_API_KEY` 只是变量名上的线索【缺】（rb/configs/skill_library.example.json:4-5；xp/eval.sh:3）。

### 3.4 三类执行器与两份 π0.5

| 组合 | 执行器 | 何时被选 | 备注 | 出处 |
|---|---|---|---|---|
| memory-guided-manipulation | π0.5，配置名 `pi05_base_aloha_full_sim_arx-x5_seed_0`，第 59999 步，repo_id `arx_x5_sim` | 没有程序覆盖当前操作时，作为学习型选项 | 进程内 `create_trained_policy`；返回完整 horizon，不截断 | 【码】rb/configs/skill_compositions.json:4-10；rb/configs/skill_library.example.json:17-25；rb/skills/pi05_backend.py:108-118, 142-155 |
| memory-guided-visual-history | π0.5 加稀疏视觉历史，配置名 `pi05_sparse_mem_robodojo_all35_80k`，第 120000 步 | 线索是遮挡、连续性和时间性措辞；该条目自己也承认，单凭首帧建立不起历史 | 每个 env 一份 SparseVisualHistory，每帧追加；算法本体在 openpi 分支里 | 【码】rb/configs/skill_library.example.json:26-34；rb/configs/policy_memory.json:46-50；rb/configs/skill_capabilities.json:2193-2196, 2244；rb/skills/sparse_backend.py:46-69；算法【缺】:14-15 |
| memory-guided-program-library 加 1 个 operation | 22 个程序之一 | VLM 判断某个程序覆盖当前操作；只有 programs.json 存在时才生成这个组合 | 6 个程序输出 EE 动作，16 个输出关节动作 | 【码】rb/configure_skills.py:125-136；rb/configs/skill_capabilities.json:332, 779, 1039, 1398, 1477, 1957 |
| memory-guided-primitive-program | code-policy | 不可达：生成的配置里没有这个技能 | — | 【桩】rb/configure_skills.py:15-16, 136；rb/skill_model.py:32-42 |

- **选定后用到 reset。** `previous_plan` 恒为 None，回合内不重选【码】（rb/skill_model.py:137, 247-248, 335）。
- **`memory-guided`没有对应的数据流。** `memory.snapshot` 被挂到 `request['memory']` 上，执行器不读它【码】（rb/execution_skills.py:88-94）。
- **两份 π0.5 共进程。** 两个模型共用一个 JAX 进程，显存占比 0.3【码】（xp/eval.sh:7；rb/skills/pi05_backend.py:108-118）。
- **权重来源。** 第一份的训练配置名和步数与我们用的官方 ckpt 相同，但权重来自它自己的 release【码】（rb/configs/skill_library.example.json:17-25 对照设计 §1）。评测路径不校验权重哈希【码】（rb/execution_skills.py:72-74；rb/skill_preflight.py:33-43）。`all35` 是否指 35 个训练任务，未查到【缺】。
- **π0.5 存了 task_name 但不用。** prompt 用的是原始指令，seed 也不转发【码】（rb/skills/pi05_backend.py:97, 175, 191；rb/execution_skills.py:63-64；复核 F20）。
- **状态打包跟随启动时的 action_type。** π0.5 后端按启动参数里的 action_type 打包状态【码】（rb/skills/pi05_backend.py:98, 190）。6 个 EE 程序能在 joint 标签下运行，靠的是 RoboDojo 逐个动作字典推断动作类型【码】（RD:src/eval_client/eval_env.py:337-352, 358-420）。

### 3.5 120 s 超时：deploy.yml 写的 1800 s 在本地链路上不生效

- **配置和实际不符。** `deploy.yml` 写的是 `request_timeout_s: 1800`【码】（xp/deploy.yml:16）。RoboDojo 客户端构造 `WsModelClient` 时不传这个值【码】（RD:src/eval_client/eval_env.py:188-196）。debug 客户端虽然转发 `deploy_cfg.get("request_timeout_s")`，但它的 `deploy_cfg` 是 argparse 参数，没有这个键，也不读 yml【码】（XPL:utils/debug_env_client.py:47, 335-348）。所以两种客户端实际生效的都是 WS 默认值 120 s【码】（XPL:client_server/ws/protocol/client.py:91, 520-530）。
- **第一个 update_obs 可能超时。** 回合开头这一次请求里要先后做两件事。一是 agent 调用：最多 3 次、每次 120 s，加上退避，合计约 362 s【码】（rb/agent_planner.py:163-172）。二是启动 worker：默认上限 600 s【码】（rb/skills/program_service.py:34, 71）。两者相加可能超过 120 s【推】。
- **README 写的预算是 1800 s。** README 按 1800 s 估回合预算【页】（xp/README.md:57, 71）。worker 每次调用的上限也是 1800 s，但这个值来自程序配置，不来自 deploy.yml【码】（rb/skills/program_service.py:44, 86-94）。
- **官方那次运行用的客户端是否遵守 1800 s，未查到。** 本地两种客户端都不遵守【推】。

## 4 "按任务路由"的事实与风险

本节只陈述事实和风险，不下合规结论。

### 4.1 它不读任务名字符串

- `task_name` 不进 VLM 上下文【码】（rb/agent_planner.py:85）。
- 能力记录里禁止出现 `task_name / task_id / layout_id` 和分数字段【码】（rb/capability_memory.py:8-9, 28-31）。
- π0.5 存了 `task_name` 但不使用，prompt 是原始指令【码】（rb/skills/pi05_backend.py:97, 175, 191；复核 F20）。
- RoboDojo 的观测本身不带任务名【码】（RD:env/observation_manager/obs_manager.py:95-108）。
- 评测路径上没有"任务 → 执行器"的配置表；读者逐个文件核对过【码】（适配器报告 §3.2 第 7 条）。

### 4.2 但实际效果接近按任务分派

1. **程序与任务一一对应。** 22 个程序名和 `RD:task/RoboDojo/tasks/` 下的任务模块一一对应。代码把程序名换成了能力名，但新名字仍是描述性的，例如 connector-alignment、board-cell-placement【码】（rb/configure_skills.py:48-71；复核 F34）。`configure_skills` 的注释说程序名"不作为任务选项提供"，这只在名字层面成立【码】（rb/configure_skills.py:108-110）【推】。
2. **能力描述里写了区分相邻任务的排除条件。** deposit-coin 排除瓶子和大开口容器；push-t 排除直边对齐和多块对齐；align-blocks 排除"把异形物推到目标轮廓"【码】（rb/configs/skill_capabilities.json:650-660, 1655-1662, 96-104）。这些原文会进入 VLM 上下文【码】（3.3；复核 F21）。
3. **由指令认出任务不难。** RoboDojo 的指令是从每个环境的描述列表里随机抽的【码】（RD:env/description_manager/desc_manager.py:79-87）。
4. **代码自己的命名就是"任务感知"。** `baseline_mode='task-aware-skill-library'`；bundle 安装器要求协议模式为 `task_aware_skill_library`；发布清单也这样声明【码】（rb/skill_model.py:93；rb/harness_bundle.py:31；rel/manifest.json:6）。
5. **同时加载两份不同的 π0.5，每回合二选一**【码】（rb/configs/skill_library.example.json:17-34）。
6. **离线开发回路明确按任务选择**，但不在评测路径上：先在每个任务上比较两份 VLA 的开发均分与阈值，不够才转向代码；每个任务各写一份 `skill_selection.json` 和 `code.<task>`【码】（rb/skill_selection.py:48-79；rb/workflow.py:410-499）。

### 4.3 等价流程

从效果看，评测时这套系统等价于三步【推】（4.2 第 1–5 条合起来）：

1. VLM 从指令和首帧认出任务；
2. 这个任务有程序，就选这个程序；
3. 没有程序，就在两份 π0.5 里选一个。

旁证：N1 记的高分任务都有对应程序，cover_blocks 100、play_tic_tac_toe 94、make_kong 82、solve_equation 60；为 0 的 sweep_blocks 和 fasten_screws 都没有程序【推】（N1:96 对照 3.4 的程序表）。各任务被路由到预期程序的实际准确率未查到【缺】。

### 4.4 与 RoboDojo P12/P13 的关系（只陈述事实）

| 条款（我们的整理） | 原意 | PhysicalRSI 一侧的对应事实 | 出处 |
|---|---|---|---|
| P12 单一固定系统 | 所有任务用相同的 checkpoint，并遵循完全相同的推理流程 | 同时加载两份 π0.5，每回合由 VLM 二选一；22 个程序各对应一个任务 | 【页】part_C:180；【码】rb/configs/skill_library.example.json:17-34；rb/configure_skills.py:48-71 |
| P13 禁止 router | 不许按任务切换策略或 checkpoint，也不许为每个任务挑出"已知最好"的策略 | 路由由 VLM 依据指令、首帧和带排除条件的能力描述做出，不读任务名；代码自称 task-aware | 【页】part_C:181；【码】rb/agent_planner.py:85, 113-115；rb/skill_model.py:93 |
| 条款没说清的地方 | 单一系统内部按指令或观测分支到任务专用程序，算不算 P13 意义上的 router | 它上了官方榜（10-09 读到第 2 名） | 【页】part_C:351；N1:93 |
| 版本一致性 | — | 审计文档自己声明，不独立担保当前产物与参与评测的版本相同；release 说两者的等价性"待验证" | 【页】rdocs/xpolicylab-audit.md:11-24；rel/manifest.json:38-41 |

是否落在 P12 或 P13 范围内，需要 RoboDojo 维护方解释，本卡不下结论【推】（part_C:332, 351）。

## 5 代码技能与感知

### 5.1 评测路径上的 22 个程序（本体【缺】）

- **形态**：XPolicyLab 风格的 `Model`，只有 reset、update_obs_batch、get_action_batch 三个方法；模块名形如 `rdj_rgb_adapters.general_pickup`【码】（rb/skills/program_worker.py:65-73；rb/configs/skill_capabilities.json:669-671）。
- **输入**：完整的 RoboDojo 观测，只去掉了 `state.mobile`【码】（rb/skills/program_service.py:96-103）。
- **输出**：动作字典块，其中 `left/right_gripper` 被改名为 `*_ee_joint_state`【码】（rb/skills/program_service.py:105-122）。
- **worker 提供给程序的东西**：程序根目录变量、描述文件声明的 PYTHONPATH、感知 sidecar、冻结的 operation 计划【码】（rb/skills/program_worker.py:29, 36, 43-61）。计划必须和注册的步骤逐项相等，所以 agent 改不了程序【码】（eh/skills/episode_plan.py:40-42）。
- **没有隔离**：程序就是 worker 进程里的普通 Python，没有 API 白名单，也没有沙箱【码】（rb/skills/program_worker.py:19-73）。
- **运行方式无法确认**：README 和预检代码都暗示所有程序走 frozen-program-service，但 programs.json 不在本地，确认不了【页】（xp/README.md:55）【码】（rb/skill_preflight.py:46-58）【缺】（rb/skills/code_policy.py:8-14）。
- **观测读取清单不完整**：22 个条目里有 16 个的静态观测读取为空，条目自己也声明这份清单不完整【码】（rb/configs/skill_capabilities.json:75；复核 F34）。
- **能力文件与当前代码对不上**：能力记忆引用的 `skill_model.py` 哈希是 b3d6e0…（共 46 处），当前文件是 a9a965…，引用的行号也对不上【码】（rb/configs/skill_capabilities.json:30-34；rb/capability_memory.py:32-37）。
- **README 的自述**：程序按任务专门编写，可以使用标定的相机或工作平面，以及物体几何先验，不保证对任意布局不变【页】（xp/README.md:78-80）。

### 5.2 仓库内的 `def policy(robot, memory)` 原语栈（开发和 RSI 用，评测时不构建）

- **静态检查**：AST 禁止 import，禁止 open、eval、exec、compile、`__import__`、input、help、dir、globals、locals、vars 这些名字，也禁止以 `__` 开头的属性；getattr、setattr、type 不在禁止之列【码】（rb/skill_proposal.py:21-47）。要求有顶层 `policy` 函数，但不检查签名【码】（rb/skill_proposal.py:86-90）。代码自己写明这只是语法门，不是安全沙箱【码】（rb/proposal.py:81）。
- **运行环境**：要 root 权限的 chroot 加 seccomp，内存限 256 MB，原语调用最多 100 次【码】（core/infra/isolation.py:76-86）。
- **原语**：`scene()` 返回世界系 AABB 和单位四元数；`reobserve()` 只返回关节；`approach()` 用 cuRobo 规划后逐步回放；`gripper()`【码】（rb/primitives.py:74-79, 146-211）。
- **示例**：`code_policy_demo.py` 是个没人引用的示例字符串【桩】（rb/code_policy_demo.py:8-38）。

### 5.3 没有物体位姿时，目标从哪来

**评测路径：本地无法确定**【缺】。已知线索只有四条：

- 观测里没有 depth、内参和外参【码】（RD:env_cfg/arx_x5.yml:14-18）。
- README 说程序可以用标定的相机或工作平面，以及物体几何先验【页】（xp/README.md:78-80）。
- 元数据里有"块位置由 RGB 估计"这样的提示【码】（rb/configs/skill_capabilities.json:93）。
- `install.sh` 装了 pin、warp-lang、yourdfpy、trimesh、timm、pycocotools，本地代码却基本不 import 它们【码】（xp/install.sh:6）。资产程序很可能自己做 IK 和检测或分割【推】。

**仓库内路径**只用单个 RGB 相机，分五步【码】（rb/perception_factory.py:15-69 → rb/grounded_scene.py:134-161）：

1. **检测**：零样本检测，接口是 GroundingDINO 风格，每个冻结查询做一次前向，最多 8 个查询【码】（rb/rgb_grounding.py:47-143）。
2. **消歧**：用 CLIP 算裁剪图与文本的余弦相似度来排序。`select_unique` 要求达到最低分，并且比其他候选高出一定余量；任何一个查询解析不出来，整个场景就失败【码】（rb/clip_ranking.py:34-88, 178-196；rb/grounded_scene.py:138-139）。
3. **深度**：单目相对深度，不用仿真深度【码】（rb/rgb_depth.py:17-65；rb/action_channel.py:66-86）。
4. **尺度**：在冻结的平面掩码上，把预测深度拟合到 z = plane_z 的解析深度，用 RANSAC 加最小二乘，留出四分之一做检验【码】（rb/depth_alignment.py:14-137）。
5. **输出**：从平面延伸到最高点的轴对齐包围盒，朝向恒为单位四元数【码】（rb/grounded_scene.py:65-84）。所以它不估计偏航，物体必须放在标定过的平面上【推】。

所有 HF 模型加载都传 `local_files_only=True`【码】（rb/rgb_grounding.py:84-90；rb/clip_ranking.py:108-115）。

### 5.4 相机标定和几何先验从哪来

- **仓库内路径全部来自冻结配置，没有默认值。** 相机用 `PinholeCamera(**config["camera"])`，代码明确不提供标定默认值【码】（rb/perception_factory.py:17；rb/camera_geometry.py:15-33；rb/code_policy_spec.py:24-30）。机器人基座位姿取自 `config["planners"][arm]["calibration"]`【码】（rb/primitive_assembly.py:67；rb/planner_bridge.py:43-102）。这些数值本地都没有【缺】。
- **可能的来源。** `native_camera.py` 在 reset 阶段读仿真相机句柄（世界位姿、内参矩阵、视图矩阵），输出字典的形状与 `PinholeCamera` 的入参完全一致【码】（rb/native_camera.py:17-69, 33-41；复核 F13）。`native_robot.py` 读取基座和 EE 的世界位姿【码】（rb/native_robot.py:9-88；复核 F14）。两者都只能在 initialize 模式下运行【码】（rb/native_worker.py:100-107）。树内没有代码把这些读数写进配置，所以"标定值来自这里"只是推断【推】。
- **评测路径的程序用的标定值从哪来：未查到**【缺】。

### 5.5 cuRobo（仅仓库内路径）

- **配置**：cuRobo v2 的 MotionPlanner，32 个 IK 种子、4 个轨迹优化种子，开启自碰撞，在基座坐标系下规划【码】（rb/curobo_backend.py:30-171）。
- **进程**：每条手臂一个规划子进程，走 HTTP RPC；规划不在仿真里做，得到的轨迹逐步回放【码】（rb/planner_service.py:30-147；rb/primitives.py:184-195）。
- **碰撞世界**：每次规划都重建，内容是感知得到的 AABB 加另一条臂的 FK 球【码】（rb/collision_world.py:59-138；rb/curobo_backend.py:122-127）。
- **两个缺陷**：支撑桌面从来不在碰撞世界里【码】（rb/primitive_assembly.py:72-80）。目标物体本身被当成障碍物，所以 demo 里"逼近物体中心"的写法走不通【码】（rb/code_policy_demo.py:15-20；rb/grounded_scene.py:81-84）。
- **可达性**：`curobo` 不在 install.sh 的依赖里，只有走非 frozen 的代码策略分支才会用到【码】（xp/install.sh:6；复核 F35 更正）。评测用的程序用不用 cuRobo，未查到【缺】。

## 6 自进化回路：页面声称 vs 代码实现

### 6.1 页面怎么说

| 要素 | 页面说法 | 出处 |
|---|---|---|
| 主式 | A_k = Agent(F, H_k)；H_{k+1} = Improve_{A_k}(H_k, τ_k)。大意是：用 harness 干活，改写它，再用改写后的版本干活 | 【页】页txt:454-481 |
| F | 固定的 System-2 多模态基础模型，负责理解、决策和改写 | 【页】页txt:483-503 |
| H | 可编辑的 harness：路由与技能选择、技能、工具、记忆，以及 System-1 的控制代码 | 【页】页txt:483-503 |
| τ | 具身反馈：观测、动作、成功与失败 | 【页】页txt:483-503 |
| Improve | vary → evaluate → select → inherit；伪代码里 `rewrite_many` 产生多个变体，`select_on_eval([A_k, *pool])` 选出存活者 | 【页】页txt:504-521 |
| `Darwinian` | 自我改写产生多个候选 agent，按具身表现择优 | 【页】页txt:522-532 |
| 极简式 | System 2：H_{k+1} = F(H_k, E⁺, E⁻)；System 1：(a_t, m_{t+1}) = H_k(o_t, g, m_t; S, T) | 【页】页txt:533-596 |
| System 1 的选择 | σ_t = d_H(o_t, g, m_t)，可选执行器是代码技能、π0.5、π0.5 加稀疏记忆 | 【页】页txt:630-663 |
| 进化对象 | 表示 φ、状态 m、规划 T、控制 ΔT；例子有 arrange digits、cover blocks、swap T、insert key | 【页】页txt:409-453 |
| 记忆与复用 | 技能保留、状态刷新；技能之间可 import 复用；技能可以组合成 σ_place∘σ_move∘σ_pick∘f∘φ | 【页】页txt:701-807 |
| 麻将示例 | 约 5 轮，每轮生成未见布局再迭代；页面自己标明这是插图，不是运行记录 | 【页】页txt:664-700 |
| F 不训练 | 不自动改进基础模型权重；试验配额不是 GPU 或 token 记账 | 【页】PC/README.md:32, 36 |

### 6.2 代码里一轮做什么（12 步）

| 步 | 实际做什么 | 出处 |
|---|---|---|
| 1 入口 | `/rsi` 和 `/evolve` 都进 `Application.evolve`。绑定了任务就走 `RoboDojoTask.evolve`，没绑定就跑玩具演示。每次调用只跑一轮；唯一的多轮驱动 ImprovementCampaign 只在测试里调用 | 【码】P/commands.py:118-125；P/application.py:157-160；sh/campaign.py:1-6 |
| 2 开发 rollout | 每轮用 `secrets.randbits(63)` 新采一批开发布局，pi05 和 pi05-sparse-memory 两个 VLA 都跑；现任执行器若是代码策略，也跑一遍 | 【码】rb/workflow.py:343-385；rb/native_runtime.py:155-163 |
| 3 选执行器 | 每个任务算两个 VLA 在开发集上的平均分。只要有一个达到 `minimum_vla_score`，就选分高的（平手选 pi05）；否则标为需要开发代码策略。基础设施失败或截断的回合直接报错，不算"能力不足" | 【码】rb/skill_selection.py:10-79, 23-24 |
| 4 提案 | `CodeProposer` 对每个需要代码的任务发一次 chat completion。输入是纯文本 JSON（任务、当前代码、记忆、开发反馈、原语 API），没有图像，没有工具，不重试；输出 source、memory、rationale 三项。所有任务的改动打包进一个子 harness；子哈希等于父哈希时返回空 | 【码】rb/proposal.py:13-82；rb/workflow.py:387-515 |
| 5 准入 | AST 黑名单（见 5.2）；运行时准入恒报 `physical_qualification False`；`foundation` 组件必须与父相同，这里的 foundation 指钉死的 VLA 版本，不是 System-2 的 LLM | 【码】rb/skill_proposal.py:21-47, 86-90；rb/native_runtime.py:239-266；sh/__init__.py:209-211；rb/workflow.py:110-135 |
| 6 冻结 | 冻结比较池（候选 sha、profile、protocol、scope）；评测器、选择器和核心文件的哈希在每个阶段前后都复核 | 【码】sh/__init__.py:56-95, 258-300 |
| 7 验证布局 | 冻结之后才新生成，`generated_at` 必须晚于 `frozen_at`；排除本轮开发布局；同一 state root 内不许复用验证布局的哈希；每个布局要有原生初始化有效的证据；审计钩子拦截对 Eval_Layout、Train_Layout、Traj、eval_result 的读取 | 【码】sh/__init__.py:258-300；PC/lineage/__init__.py:210-220；rb/evidence.py:80-109；rb/layouts.py:21, 36-70 |
| 8 配对评测 | 父和子在同一组布局上各跑一次。分数取原生 `env.eval_result` 里的 score∈[0,1] 和 success，并与 `env.success` 交叉核对；选择路径上没有 VLM 判官。原生运行失败或被截断时直接抛错，不记零分 | 【码】rb/native_observer.py:41-76；rb/evidence.py:48-55；rb/workflow.py:544-620 |
| 9 选择 | 按任务归一化求平均，再按任务加权。任一任务下降超过 `maximum_regression` 加容差就拒；增益不超过 `minimum_gain` 加容差也拒；两者默认都是 0，容差 1e-10；平手保留父代。不做显著性检验，不给置信区间，不重复 seed | 【码】sh/selection.py:164-345；rb/workflow.py:138-150 |
| 10 继承与回滚 | `HarnessState` 用比较并交换提交：写 `history/<sha>.json`，再改 `current.json` 指针；每次读取都复核闭包和证据哈希；`rollback()` 发布一条复制旧 harness 的新记录；锁是单机 flock | 【码】PC/lineage/__init__.py:24-220；sh/__init__.py:320-357 |
| 11 断点恢复 | 每个阶段写收据。停在 started 状态的阶段会抛 `ReconciliationRequired`，除非端口提供对应的 `resume_*`；RoboDojo 端口一个也没实现，所以崩溃后要人工对账 | 【码】sh/__init__.py:97-140；rb/workflow.py:336-620 |
| 12 记忆 | `memory_rules.json` 是按任务的 dict，由 LLM 整份返回、整份替换，交给下一次提案和运行时代码；没有合并、剪枝或大小上限 | 【码】rb/workflow.py:405, 455-466, 488, 501-502 |

### 6.3 关键性质

1. **每轮 1 个子代，是 (1+1) 爬山。** 核心允许 1 到 32 个候选，但所有调用方都传 `max_candidates=1`【码】（sh/__init__.py:56-95；core/self_harness/__init__.py:60-61；rb/workflow.py:163；AE/harness.py:154）。代码里没有 `Darwinian` 一词【码】（适配器全树检索）。
2. **只接 2 个任务。** RSI 回路只接受 put_bottles_into_dustbin 和 stack_bowls【码】（rb/configs/tasks.json:4-7；rb/layouts.py:20）。这两个恰好是我们的开发任务【页】（设计 §1）。
3. **提案者纯文字、一次性。** 页面说 F 是多模态模型，实际的提案者只收纯文本 JSON，一次调用，不重试【码】（rb/proposal.py:44-66 对照页txt:483-503）。
4. **0 容差加全任务打包。** 门的默认值是 `minimum_gain=0`、`maximum_regression=0`、平手容差 1e-10【码】（rb/workflow.py:144, 148-149）。所有任务的改动打包成一个子代，又不许任何任务退步。验证回合少时，任何一个任务的单回合噪声都能否决整体改进【推】（rb/workflow.py:138-164, 410-504）。
5. **没有统计检验。** 选择器不做显著性检验，不给置信区间，也不重复 seed【码】（sh/selection.py:164-345）。仓库文档也没有给出相关设计【缺】（rdocs/research-memory-evolution.md:79-107）。
6. **新代码怎么进候选（两份报告的说法都对）。** LLM 写出代码后，`propose_skill_choice` 带着这份代码再判一次【码】（rb/workflow.py:486-494）。只要有一个 VLA 达到阈值，选的仍是 VLA，新代码只写进 skills.json、不被路由【码】（rb/skill_selection.py:61-72）。两个 VLA 都不达标时才路由到新代码【码】（rb/skill_selection.py:73-79）。默认方案是 hybrid【码】（rb/workflow.py:59）。code_policy 方案会强制要求写代码，learned_skill 方案则在低于阈值时仍保留最好的 VLA【码】（rb/workflow.py:432-446）。只要写出了新代码并被路由，它都不先在开发集上跑，而是直接进入配对验证，所以验证集同时承担了"第一次测试"的角色【推】（rb/workflow.py:447-494）。
7. **完整性字段写死，但不构成漏洞。** 评测行把 `state="completed"`、`native_exit_code=0` 写死，选择器里对应的完整性检查因此必然通过【码】（rb/workflow.py:602, 614；core/self_harness/selection.py:203-208, 230）。不过 observer 在此之前已经拦掉了几类情况：不稳定或被放弃的布局、初始化无效、未正常结束、分数缺失或重复、success 与 `env.success` 不一致，遇到就抛错【码】（rb/native_observer.py:42-67）。所以这个检查是多余的，不是漏洞【推】。
8. **lineage 是真的，也有坑。** 提交时用 flock 加比较并交换，幂等；提交时重跑选择器；续跑时重新计算选择，结果必须与存档一致【码】（core/lineage/__init__.py:71-184；core/self_harness/__init__.py:260-315）。坑有三个，下面三条都是读代码推出的，没有运行验证【推】：
   - 某一步停在 started（例如 LLM 的 HTTP 出错），树里又没有对账程序，`evolve` 会一直重选这一轮（core/self_harness/__init__.py:97-133；rb/workflow.py:173-178）；
   - 身份摘要覆盖所有 `.py` 文件，拉一次新代码，已有状态就会被拒（rb/workflow.py:83-100；core/lineage/__init__.py:47-49）；
   - 证据用绝对路径做键，搬动工作区后 resolve 会失败（core/lineage/__init__.py:28-42；core/self_harness/__init__.py:317-332）。
9. **验证布局永不复用。** 每轮比较都用冻结后新生成、预留的验证布局，跨轮次不复用【码】（core/lineage/__init__.py:203-213；sh/__init__.py:258-300）。
10. **不稳定回合不算失败，整轮作废。** RSI 端口拒绝把不稳定或被放弃的布局记成失败，直接抛错【码】（rb/native_observer.py:42-43）；回合缺失时整轮选择作废，不记零分【码】（sh/selection.py:164-266）。

### 6.4 页面与代码的落差

| 页面说法 | 代码实际 | 出处 |
|---|---|---|
| `rewrite_many` 产生多个变体，按具身表现择优，即 `Darwinian` | 每轮只产 1 个子代；核心允许 32 个，但没有调用方用到 | 【页】页txt:504-532；【码】rb/workflow.py:163；sh/__init__.py:56-95 |
| F 是多模态基础模型，负责理解、决策和改写 | 评测期的路由器看首帧图像；进化期的提案者只收纯文本 JSON，看不到任何图像 | 【页】页txt:483-503；【码】rb/agent_planner.py:92-104；rb/proposal.py:44-66 |
| System 1 每一步按 σ_t = d_H(o_t, g, m_t) 选执行器 | 评测时每回合只选一次，一直用到 reset；`previous_plan` 恒为 None | 【页】页txt:630-663；【码】rb/skill_model.py:137, 247-248, 335 |
| 技能之间可以 import 复用，也可以组合 | 仓库回路产出的代码禁止 import，只能是单个 `policy` 函数；组合只出现在发布的多文件程序包里，而这些程序包不在仓库 | 【页】页txt:701-807；【码】rb/skill_proposal.py:21-47；【缺】rel/download_assets.py:14-15 |
| 保留技能，刷新状态 | memory_rules 是每个任务一份 dict，由 LLM 整份替换；评测期执行器根本不读记忆 | 【页】页txt:701-807；【码】rb/workflow.py:455-466；rb/execution_skills.py:88-94 |
| 生成未见布局再迭代（麻将示例） | 仓库回路确实每轮新采布局；但页面上唯一有记录的战役（fold_clothes）复用同一个 40 场景 cohort，用的还是官方 Eval_Layout 文件 | 【页】页txt:664-700；页json:fold-clothes-evolution；【码】rb/native_runtime.py:155-163 |
| 改进信号是具身反馈：观测、动作、成功与失败 | 选择依据是仿真原生 `env.eval_result` 的分数和 `env.success`，也就是仿真真值；它只用在 RSI 回路里，不在评测路径上 | 【页】页txt:483-503；【码】rb/native_observer.py:41-76 |
| 不改基础模型权重 | 一致：回路只改 harness 文件。但两份 π0.5 本身怎么训的，另外没有公开 | 【页】PC/README.md:32；【码】rb/workflow.py:387-515；【缺】rel/README.md:18-24 |

### 6.5 未接线、桩和惰性代码

| 内容 | 出处 |
|---|---|
| CaseProtocol（dev/val/test 划分）、ShadowGate、ResearchMemoryGate、ExperimentEvaluator / TrialQuota 只在测试里构造，文档写明是可选启用 | 【缺】rdocs/research-protocol.md:92-96 |
| `cost_reduction` 和 `capability_qualification` 分支不可达；`rsi_plan.budget` 没人读 | 【桩】sh/selection.py:87-90, 285-322；P/rsi.py:144-147 |
| 适配器副本里的死分支：成本选择、`capability_qualification`、`rollback`、`validate_admission_binding`、`write_selection` 都没有调用方 | 【桩】core/self_harness/selection.py:82-85, 89-157, 266-331, 356-362；core/lineage/__init__.py:186-201 |
| `OfflineProposer` 只输出确定性模板（`observe()`/`reobserve()` 一类，不解题）；仓库里唯一能生成 task.json 的 `native_package` 默认用它 | 【桩】rb/proposal.py:85-161；rb/native_package.py:25, 77-79 |
| 有真实的 LLM 提案路径：`workflow.create` 默认用 api 模式的 CodeProposer | 【码】rb/workflow.py:623-637；rb/proposal.py:36-82 |
| `demo()` 是玩具示例；`/play` 接到 `run_baseline` 上 | 【桩】prsi/application.py:229-260；prsi/commands.py:27, 99-104 |
| 仓库里没有任何一轮 RoboDojo 的 round、lineage 或 selection 收据 | 【缺】仓库全树；rel/harness_state/state.json |

### 6.6 Autoresearch 与 self_harness 的关系

| 组件 | 是什么 | 出处 |
|---|---|---|
| runner（run.py / study.py） | Opentrons 稀释协议的顺序 keep/discard 账本，可断点续跑。提案是 4 个手写的硬编码方案，或用 `--proposals` 传入的 JSON。可调的只有 `mix_repetitions`（0–10）和两个布尔量：44 种设置，42 个不同计划，只有 4 种不同的有效分。按（tips, commands）字典序严格变小才 keep；验证只在最后跑一次，不参与选择 | 【码】AR/study.py:19-30, 205-233；PhysicalRSI_baselines/opentrons_liquid_handling/policy.py:24-72 |
| 记忆 | 一条固定的 lesson 字符串，之后没有代码读回 | 【码】AR/study.py:235-239；rdocs/autoresearch-pipeline.md:20 |
| heartbeat | 每 30 分钟排一个审阅请求，`complete()` 要求证据、反思、备选、决定、下一个实验五个字段齐全；它不启动任何作业 | 【码】AR/heartbeat.py:16-95 |
| records | 内容寻址的"假设 → 实验 → 结果 → 决定"状态机，绑定 HarnessState 和 ResearchMemory，但从不提交 lineage，也不做选择 | 【码】AR/records.py:1-6, 40-247 |
| 谁在提案 | 文档说由外部编码 agent 按 program.md 提交 JSON 批次；runner 本身不调模型；仓库别处标明了由 Codex 编写 | 【页】AR/program.md:29-31；PhysicalRSI_baselines/opentrons_liquid_handling/README.md:30-31；AE/harness.py:142 |
| 和 Self-Harness 的关系 | liquid study 写死了 `self_harness_admitted: False`；文档自认还不是无人值守的研究回路，ImprovementCampaign 没有自主提案者或调度器 | 【码】AR/study.py:142, 169-170；【页】rdocs/autoresearch-pipeline.md:15-22 |
| 报告数字 | README 写 tips 32→16、commands 118→80；这两组数可以由 `plan()` 的算术直接推出，仿真器只给 pass/fail；仓库里没有运行工作区 | 【页】AR/README.md:156-170；【推】AR/README.md:73；【缺】 |
| auto_engineering | 唯一真跑过一轮核心 SelfHarness 的 Isaac Sim 演示。System 2 是手写规则：transit 高度 = max(父值, 障碍物顶 + 0.12 + 0.06)，超过 0.55 就不再提案。只有一轮、一个候选；验证布局只抖动 x/y；没有提交运行产物 | 【码】AE/policy.py:21-32；AE/harness.py:83-165；【缺】AE/README.md:39-79 |

一句话：Self-Harness 管"候选怎么被选、怎么继承"，Autoresearch 管"外部 agent 的研究过程怎么记账"。两者共用 lineage 和 ResearchMemory 的类型，但没有接成一个闭环【页】（rdocs/autoresearch-pipeline.md:15-22）。AGENTS.md 规定编码 agent 在集群上带着 benchmark 目标工作、定时审阅、从 heartbeat 恢复【页】（AGENTS.md:41-63），真正的"进化"更可能发生在这一层【推】。

## 7 RoboDojo 的技能和分数是怎么来的

### 7.1 22 个程序的来历

- **能力记忆有 24 项**：22 个代码策略，加 pi05 和 sparse-memory 两项。每一项的 provenance 都是 `implementation_inspection`，`rollout_derived=false`【码】（rel/memory/capability_memory.json:8-92；rb/capability_memory.py:20-21）。`rollout_derived False` 只约束能力元数据，不约束程序本身【码】（rb/capability_memory.py:20）。
- **代码本体不在仓库里**：放在外部的 code-skills 根目录，靠 GitHub release 清单按 sha256 下载【缺】（rel/download_assets.py:14-15）。
- **发布包的来源文件自相矛盾**：一方面写着由本地打包脚手架生成、没有附带经过验证的进化收据、中间变异已排除；另一方面又把 `source_engine` 写成 `rd.workflow`【码】（rel/provenance/manifest.json:3-10）。
- **按当前配置，仓库内的回路造不出这 22 个程序**【推】，理由有三：
  - 回路只接了 stack_bowls 和 put_bottles，而这两个任务都不在 22 个代码程序里【码】（rb/configs/tasks.json:4-7）；
  - 回路只能产出一个不带 import 的函数，而发布的程序是多文件包，依赖感知和规划模块【码】（rb/skill_proposal.py:38-46）【页】（rb/xpolicylab_policy/README.md:29）；
  - 没有任何进化收据【缺】（rel/provenance/manifest.json:5）。
- **作者是人还是 agent，仓库无法区分**【缺】。最可能出自仓库外 rdj_master/EGL 工作区里的"外部编码 agent 加人工"【推】（rdocs/autoresearch-pipeline.md:13-22；AGENTS.md:41-63）。布局采样器本身也是从 /cpfs02 上的 rdj_master 脚本导入的【码】（rdocs/migration.json:258-272）。

### 7.2 两个开发任务上的分数从哪来

- **页面数字**：stack_bowls 的官方 SR 是 74.67，put_bottles 是 75.33【页】（页json:settings-gallery；N1:95）。我们记录的官方 π0.5 分别是 38.0 和 69.3【页】（设计 §1）。
- **这两个任务没有代码程序**【码】（3.4 的程序表），所以榜单分数应来自它自有的 π0.5 或 sparse-memory 路由，或者来自仓库外的 serve_cap 路径【推】（rel/reference_runtime/serve_cap.py:8-21）。
- **它的 π0.5 怎么训的没有公开**【缺】（rel/README.md:18-24）。不能假设与官方 Pi-05 基线是同一份权重【推】。
- **仓库里的 pilot**：两个任务都选了 pi05-sparse-memory，每个任务 5 回合、seed 1、SR 1.0，自标不是资格声明【桩】（configs/robodojo.curated.json:8-39）。

### 7.3 fold_clothes 曲线是精选点

| 项 | 内容 | 出处 |
|---|---|---|
| 图上四个点 | k=1..4：7.5/17.5 → 27.5/31.5 → 32.5/35.5 → 38.0/46.13（SR/Score） | 【页】页json:fold-clothes-evolution |
| 前三点 | R12、R14、R17 三个不连续的修订，共用同一个 40 场景的开发 cohort | 【页】同上 |
| 终点 | 标为 OFFICIAL、"用户报告"，没有 policy_revision；数值正好等于榜上 fold clothes 41.33/48.80 与 fold clothes random 34.67/43.47 的平均 | 【页】同上；【推】算术 (41.33+34.67)/2=38.0，(48.80+43.47)/2≈46.13 |
| 页面自己的声明 | 这不是学习曲线，也不含泛化声明 | 【页】同上 |
| 没画进图的点 | 同一 cohort 上，R16 只有 2.5%，R23 有 65% / 66.0 | 【页】同上 |
| lineage | 是有分叉、有合并的树：R16 出自 R11，R18 出自 R13，R23 合并了多条线 | 【页】同上 |
| 工件 | 全在 /cpfs02 下，仓库里没有 | 【缺】同上路径对照仓库检索 |
| 计算量 | 页面没有；只有路径名里的暗示，例如 `official6300_24gpu_groups10_max20/max40 … attempt_N`，约 24–27 卡、多次 attempt，修订之间约隔 1–2 小时 | 【缺】【推】页json:fold-clothes-evolution |

### 7.4 212 是视频片段，不是程序

- 页面的技能库画廊是 212 个视频时间窗，带 106 种自由文本标签【页】（页txt:1212-1273；页json:skill-clips）。
- 代码程序只有 22 个，本体也不在仓库里【码】（rel/memory/capability_memory.json:8-92）【缺】（rel/download_assets.py:14-15）。
- 适配器本地的能力记忆同样只有 24 条；布局采样器覆盖 41+13 个任务名【码】（rb/layout_sampling/catalog.py:22-64；rb/layout_sampling/extended.py:18-32）。

### 7.5 开发用的是官方 Eval_Layout 文件

- **仓库内的回路**：自己采布局，并且禁止读取 Eval_Layout【码】（6.2 第 7 步；rb/layouts.py:21, 36-70）。
- **页面上的 fold_clothes 战役**：用的却是 `Eval_Layout/RoboDojo/arx_x5/{0,1,2}` 下的文件。40 个场景的构成是 base seed0 ×4、seed1 ×15、seed2 ×1，random seed0 ×20，全部标 `confirmed_unseen=false`；到 R16 时有 20 个标为 `known_native_exposed`【页】（页json:fold-clothes-evolution episodes）。也就是说，它恰好用了仓库自己禁读的那一类输入【推】。
- **战役的反馈是看图的**：开发期的 RGB 也被拿来做选择（页面数据里有 `shared_predeclared_coverage_with` 字段）【页】（页json:fold-clothes-evolution）。
- **跨修订复用同一 cohort**：仓库回路每轮新采布局，fold_clothes 战役却在多个修订之间复用同一个 40 场景 cohort【码】（rb/native_runtime.py:155-163）【页】（页json:fold-clothes-evolution）。
- **22 个程序开发时用过哪些布局**：没有记录【缺】（rdocs/migration.json:19, 31-34）。

### 7.6 gt_readers

- `clean_policy_assets.py` 静态剥掉读取 11 项特权属性的代码，例如 scene_manager、layout_manager、reward_manager、get_instance_pose、env_seeds、end_flag【码】（rb/clean_policy_assets.py:1-20）。测试里把这些叫 gt_readers；清理报告标 `qualified=False`【码】（tests/test_policy_asset_cleanup.py:4-27；rb/clean_policy_assets.py:148-195）。
- 适配器 README 说剥掉的是"未使用的"读取器【页】（rb/xpolicylab_policy/README.md:75-82）。由此可以推出开发版源码里含有这类读取器【推】。
- 榜单评测时的版本有没有调用它们，无法确定【缺】。作者自己声明当前产物与评测产物的等价性"待验证"【页】（rel/manifest.json:38-41；rdocs/xpolicylab-audit.md:11-24）。
- 清理报告 `source-cleanup-review.json` 不在仓库里【缺】。

### 7.7 页面数字能被支撑到什么程度

| 数字 | 页面出处 | 仓库或记录能支撑多少 | 标签 |
|---|---|---|---|
| 36.27 / 31.38%，#1；#2 是 Awomo-0.5 的 35.34 / 29.64，领先 0.93 分 | 页txt:350, 366-385；页json:benchmarkEntries | 仓库没有评测记录，release 写着 `evaluation_ready false`。算术自洽：按单个设置重算是 31.383，舍入为 31.38；用四舍五入后的维度值算是 31.386，会进位成 31.39 | 【页】【缺】【推】 |
| 52 个设置、18 个 0%、40 个成功视频 | 页txt:821-1158；页json:settings-gallery | 由 Gen-random 的算术反推，官方集合应有 54 个设置，画廊少了两个 random 设置 | 【页】【推】 |
| fold_clothes k=1..4 | 页json:fold-clothes-evolution | 见 7.3 | 【页】【推】【缺】 |
| 212 片段 / 40 任务 | 页txt:1212-1273；页json:skill-clips | 是视频时间窗，不是程序 | 【页】【缺】 |
| 麻将约 5 轮 | 页txt:664-700 | 页面自标为插图 | 【页】 |
| 计算量与成本 | 页面全文没有 | 只有路径名暗示 | 【缺】【推】 |
| Opentrons tips 32→16 | AR/README.md:156-170 | 由 `plan()` 的算术决定，没有工作区 | 【页】【推】 |

## 8 特权信息审计

本节只讲评测与开发两条路径能否接触仿真真值，素材全部来自适配器报告的审计复核。

### 8.1 清理器 `rb/clean_policy_assets.py`

- **定位**：从资产里提取只依赖观测的辅助函数，生成一个新的资产版本供人审阅；自述不是资格认定，也不是运行时沙箱【码】（rb/clean_policy_assets.py:1-7）。
- **名单**：11 个属性名，与我们 `frozen/forbidden.yaml` 的前 11 项相同；我们名单里其余的项（如 success、eval_result、seed_manager），它都不管【码】（rb/clean_policy_assets.py:16-20；frozen/forbidden.yaml）。
- **检测方式**：纯语法匹配，只认两种写法：`ast.Attribute.attr` 命中名单，或者 `getattr(x, '<常量>')`【码】（rb/clean_policy_assets.py:23-32）。所以以下写法都会漏掉【推】：
  - 计算出来的属性名，或者给 getattr 起的别名；
  - `operator.attrgetter`、`functools.reduce(getattr, …)`；
  - `vars()`、`__dict__`、`object.__getattribute__`；
  - 读文件；
  - 硬编码的数值先验。
- **删除规则**：
  - 只处理顶层语句；类里只要有一个方法命中，整个类都删掉【码】（rb/clean_policy_assets.py:74-85）。
  - Import、With、For、AugAssign、if、try 里定义的名字不追踪【码】（:81-82）。
  - 跨模块传播只认 `from X import name` 这一种写法【码】（:86-96）。
  - 从 9 个目录以外 import 进来的依赖永远传播不到，同名模块会被合并【码】（:168-171）。
- **两处特例改名**：
  - `plug_in_charger` 模块里只要找到一个 `SimpleNamespace(take_action_cnt=defaultdict(int))`，就把整个模块里的 `take_action_cnt` 全部改名，包括可能存在的 `env.take_action_cnt` 读取；它并不证明这些读取都来自那个 namespace【码】（:42-51, 62-65）。
  - `charger_server_backend` 里只检查接收者名字是不是 `runtime`【码】（:52-56）。
- **扫描范围**：只扫 `<source>/implementations/` 下 9 个顶层目录里的 `*.py`，外加 1 个 openpi 文件【码】（:151, 154-166）。它不扫能力元数据实际指向的 `skills/<pkg>/<hash>/`，有 12 个程序包不在扫描名单里，也不看任何非 Python 文件【码】（适配器报告 §6.1）。
- **何时运行**：只能手动用 CLI 运行。安装、准备、配置、预检、评测流程里都不调用它，也没有任何地方校验已安装的资产清理过【码】（:198-204；复核 F15）。
- **能否复现发布版**：不能。清理器要读 `source/programs.json`，而 `prepare_code_programs` 会拒绝已有 programs.json 的目录；清理后文件的哈希也过不了 `configure_skills` 的校验。所以用树内代码复现不出"清理输出 → v4 发布资产"这条路径【码】（:192；rb/prepare_code_programs.py:31-33；rb/configure_skills.py:99-105）。

### 8.2 逐条命中（F01–F36，按可达性分三组）

"可达"指在 RoboDojo + XPolicyLab 的默认评测路径上能否执行到。每行的结论都经过审计复核【码】（适配器报告 §6.2）。

**A 组：评测路径可达（8 条，加 1 条混合）**

| ID | 位置 | 内容 | 复核结论 |
|---|---|---|---|
| F01 | xp/deploy.py:1-6 | 只做 re-export，导入链不碰 env | 干净 |
| F02 | rb/evaluation.py:20-86 | 只用 4 类公开方法；不发 trial_end | 干净 |
| F20 | rb/execution_skills.py:63-64；rb/skills/pi05_backend.py:97 | task_name 被复制进 π0.5 配置，但没人读；seed 不转发 | 不涉及特权 |
| F21 | rb/agent_planner.py:16-37, 76-118 | VLM 上下文 | **更正**：上下文里还有带任务特征的能力描述原文（第 4 节） |
| F22 | rb/skills/program_service.py:37-103；rb/skills/program_worker.py:19-73 | 执行资产代码；PYTHONPATH 里有 RoboDojo 根（`xpolicylab_parent` 就是 RoboDojo 根，rb/configure_skills.py:31-35） | **更正**为"有条件可达"：装了程序且被选中就会执行；执行的代码读什么无法核实 |
| F31 | rb/skill_model.py:341-342 | `on_trial_end` 丢弃结果 | 干净 |
| F32 | xp/setup_eval_policy_server.sh:64；xp/setup_eval_env_client.sh:19 | PYTHONPATH 里有 RoboDojo 根 | **更正**：树内代码只用它导入 XPolicyLab.*，并让 process_data 读公开的 env_cfg/*.yml 和 _robot_info.json |
| F34 | rb/configure_skills.py:48-136；rb/configs/skill_capabilities.json | 22 个按任务命名的程序被映射成能力名 | **更正**：新名字是描述性的，不是不透明的 |
| F36 | core/infra/isolation.py:3；rb/capability_memory.py:8-9；rb/proposal.py:57 | 原判命中，实为误报或防护代码 | **更正**为"混合"：前两项在评测路径的导入闭包里，都不涉及特权 |

**B 组：评测路径不可达（24 条，都在 RSI 的 initialize / rollout、布局工具或开发选择里）**

| ID | 位置 | 内容 |
|---|---|---|
| F03–F07 | rb/native_initialization.py:51-76, 152-159 | `abandoned_seeds`、`unstable_*`、`env.env_seeds[index]`、`layout_valid`、`end_flag`、`take_action_cnt`；替换 run_eval 并关闭 env 和 app（initialize 阶段会换成 ResetOnlyClient，rb/native_worker.py:46-47） |
| F08–F10 | rb/native_observer.py:42-74 | rollout 阶段读 `unstable_*`、`eval_result["details"]`、`save_dir`、`_result.json`、`env_seeds`、`layout_valid`、`end_flag`、`success`、`take_action_cnt`、`step_lim`；文件在父进程读取（rb/native_execution.py:74-77） |
| F11 | rb/native_worker.py:27-188 | 打补丁替换资产路径、SeedManager、`create_eval_env` 和 WsModelClient；写死 `--env_cfg_type arx_x5`（:143-171） |
| F12 | rb/native_layouts.py:12-74 | `FreshSeedManager`；拒绝 Eval_Layout 和 Train_Layout（:31-34） |
| F13 | rb/native_camera.py:17-99 | 仿真相机位姿、内参、`camera_manager`；只在 reset 阶段；输出形状等于 PinholeCamera 的入参 |
| F14 | rb/native_robot.py:9-87 | `robot_manager`、`env_origins`、`get_link_pose` 等 |
| F15 | rb/clean_policy_assets.py:16-20, 46-64 | 名单字面量；全树没有调用方 |
| F16–F19 | rb/layouts.py:21, 36-123；rb/layout_sampling/catalog.py:192-262, 950-952, 4958-4980；rb/layout_sampling/extended.py:182-193；rb/layout_sampling/image_conveyor.py:23-24 | 读任务 yml、场景 yml、物体资产与包围盒、`photo/map.json`；extended.py 自身没有 Eval 检查，靠 layouts.py 的钩子 |
| F25 | eh/skills/remote.py:24-41 | `receipt['success'/'reward'/…]`；没有调用方 |
| F26 | core/self_harness/selection.py:224-257, 288 | `success`、`success_rate` |
| F27 | rb/native_execution.py:74-144 | 读 `_result.json` |
| F28 | rb/evidence.py:47-75 | `episode_score`、`success` |
| F29 | rb/workflow.py:425-436, 599-604 | 用仿真真值成功率选择执行器；资产程序是否由此产生，无法核实 |
| F30 | rb/xpolicy_adapter.py:18-19；rb/policy_model.py:63 | 要求 task_name 等于绑定的任务。**更正**：只有 `xpolicy_eval.py` 用它；原生 rollout 直接用 `policy_model.Model`（rb/native_runtime.py:312-316） |
| F33 | rb/native_preflight.py:15-41 | 校验 deploy 副本 |

**C 组：可达性不明（3 条）**

| ID | 位置 | 内容 | 复核结论 |
|---|---|---|---|
| F23 | rb/skills/code_policy.py:11-14；rb/code_policy_factory.py:102-137 | 按 `binding['task']` 读取任务配置和 memory_rules | 这条路径本身就是按任务设计的 |
| F24 | rb/clean_policy_assets.py:52-56, 154-166 | 资产里有 `rdj_master.evaluation.*`，以及带"旧评测回调"的 openpi 集成 | "由评测侧代码移植而来"是推断 |
| F35 | rb/curobo_backend.py:68-174 | cuRobo；全树没有 `robot_manager.planner` | **更正**：走 F23 那条分支时可达 |

### 8.3 间接的真值来源

1. **仿真相机标定**：F13 的输出形状与 PinholeCamera 吻合；README 承认用了"标定的相机或工作平面"；代码写明平面掩码是冻结的标定输入。具体数值在资产里，来源无法核实【码】（rb/grounded_scene.py:95-96）【页】（xp/README.md:78-79）【缺】。
2. **仿真机器人坐标系**：F14 的读数可能对应 `world_base_pose`，数值同样在资产里【推】（rb/planner_bridge.py:33-61；rb/primitive_assembly.py:67）。
3. **物体几何与任务配置**：只在布局工具里读（F16–F19）。但这些文件和策略进程在同一台机器上，PYTHONPATH 里又有 RoboDojo 根，策略进程读得到【推】（F22、F32）。
4. **拿真值成功率做开发反馈**：数据流是 F08–F10 → F27 → F29。树内回路有能力这样做；复核要求弱化表述，"资产程序就是这样调出来的"无法核实【码】【推】（rb/workflow.py:425-436）。
5. **资产代码由评测侧代码移植而来**：见 F24，属于推断【推】。
6. **冻结的、按任务定制的感知先验**：具体值在资产里【码】（rb/rgb_grounding.py:3；rb/depth_alignment.py:1-5）【缺】。
7. **worker 能读到的文件**：程序根目录、描述文件声明的路径和继承的 PYTHONPATH 加起来，资产代码能打开 Assets/ 和 Eval_Layout；清理器完全不查文件读取【码】（rb/skills/program_worker.py:23-25, 29, 36）【推】。

### 8.4 结论（谨慎）

- **树内评测路径没有特权访问。** deploy、evaluation、skill_model、π0.5、agent 这几部分都不访问特权属性，也不读结果。所有特权读取都在 `native_*` 和布局工具里，它们运行在仿真进程中，不在评测路径的静态导入闭包里【码】（复核 F03–F12）。
- **最大的未知是下载来的 22 个程序。** 它们在评测路径上可达（F22），本地看不到源码；清理器只做语法检查、覆盖面有限，安装和评测都不调用、也不校验它【码】【缺】。env 和程序在不同进程里，通过活的 env 句柄泄漏的可能性低；现实的通道有两条：读文件，以及从仿真得来的冻结标定或先验【推】。
- **既不能说有泄漏，也不能说无泄漏。** 要下结论，得把资产下载到隔离目录做静态审计（第 12 节第 1 条）【推】。
- **对我们的含义。** 我们的 P2 也跑在 XPolicyLab 的策略服务端里，和 RoboDojo 根目录在同一台机器上【页】（设计 §2 架构表、§8.1 第 1 条）。AST 检查是静态的，运行时由数据拼出来的文件路径它看不到；PhysicalRSI 的程序之所以"可能读文件"，正是因为同样的处境【推】。所以第 10 节的 PR7（进程内文件审计钩子）不只是借鉴，也是补我们自己的同一个缺口【推】。

## 9 与我们 v1 的逐项对比

左列是 PhysicalRSI 代码里实际做的，不是页面说法。我方各格由写卡人对当前工作树核对过。

| 维度 | PhysicalRSI（代码实际） | 我们 v1 | 要点 |
|---|---|---|---|
| 进化面 | 整个 harness 闭包，共 11 类组件；实际会变的是每个任务的执行器选择、代码策略和记忆 dict【码】（core/self_harness/artifacts.py:12-55；rb/workflow.py:387-515） | 一次只开一个面，顺序是 planner.md → recovery.yaml → SKILL.md；记忆回路离线进行，不用 LLM【页】（设计 §3.1 进化面行、编辑者行） | 我们的面窄，改动可归因 |
| 提案者 | 单次 chat，纯文本，不带图，没有工具，不重试；仓库打包脚本默认用确定性模板【码】【桩】（rb/proposal.py:13-82；rb/native_package.py:25, 77-79） | pi-mono 会话（claude-opus-5-5），diff ≤40 行或 ≤3 条规则，规则预算按轮 3→1；编辑者只拿到证据的副本【码】（etude/evolution/round.py:158-160, 233；设计 §3.1） | 我们是会话式 agent，它是一次性生成 |
| 批大小 | 每轮 1 个子代，一次 `/rsi` 只跑一轮【码】（rb/workflow.py:163；P/commands.py:118-125） | 每轮 1 个候选，串行；连拒 3 次或 V 被 6 个候选复用就停【码】（round.py:178-181；frozen/params/thresholds.yaml:50-51） | 两边都不是种群 |
| 给提案者的反馈 | 开发行：分数、成功、诊断、证据路径【码】（rb/evidence.py:65-77）；fold_clothes 战役实际看了开发期 RGB【页】（页json:fold-clothes-evolution） | E 的 trace、判决、按布局官方分，按"最早失败节点"聚类；V 只回 accept/reject 一位【码】（etude/evolution/lineage.py:105-107；设计 §3.1 证据行、§3.2） | 我们不给编辑者看图（建议 PR16） |
| 评测集 | dev 和 val 每轮新采；val 冻结后才生成、不许复用、禁读 Eval_Layout；没有 test 划分【码】（sh/__init__.py:258-300；sh/protocol.py:81-100） | E = seed 0，V = seed 1（固定，最多复用 6 次），T = seed 2 加留出任务、只看一次；全部是官方 Eval_Layout 公开布局【码】（etude/core/splits.py:12-15；设计 §3.2） | 它的 V 新鲜但没有 T；我们有 T，但 V 固定，而且全在官方布局上 |
| 接受门 | 加权均值增益 >0，且每个任务都不退步（容差 1e-10）【码】（sh/selection.py:269-345；rb/workflow.py:138-150） | L0 静态 → L1 medoid 全过 → L2 回归集最多失守 1 次 → L3 b−c≥ε 且单侧 McNemar p≤0.05 → 成本规则 → 人工 Decision 后才合并【码】（etude/evolution/gates.py:1-15；round.py:322-327, 356-392） | 我们多了统计检验、成本和人工环节 |
| 统计检验 | 没有，也没有置信区间或重复 seed【码】（sh/selection.py:164-345） | 精确单侧 McNemar；ε = max(4, A/A 三次重跑成功数最大两两差 + 1)；没校准 ε 就拒绝开跑【码】（gates.py:327-332；round.py:220-222；设计 §3.3 ε 校准） | 我们明显更强 |
| 多任务退步 | 每个任务都不许退步，容差 0，对噪声过敏【推】（rb/workflow.py:138-164） | L3 把两个任务合在一起算 b、c，不分任务查；L2 只覆盖历史 medoid；A/A 先跨任务求和再求差【码】（gates.py:305, 335-354；etude/evolution/aa_noise.py:115-141） | 两边各走一个极端（建议 PR11） |
| 现任基准 | 每轮父子在同一批新布局上同批重跑【码】（sh/__init__.py:302-317） | 现任的 V 结果缓存一次，复用给之后所有候选，直到合并【码】（round.py:301-302, 381） | 建议 PR9 |
| 缺失与不稳定 | 不稳定或被放弃的布局直接抛错，缺回合时整轮作废、不记零分【码】（rb/native_observer.py:42-43；sh/selection.py:164-266） | 缺失或 unstable 算候选失败；任一侧 unstable 率 >5% 就拒【码】（gates.py:335-354, 357-388） | 我们防"把失败踢出分母"；它防误判 |
| 回滚 | 内容寻址 lineage 加比较并交换；`rollback()` 发一条新记录（适配器副本里没有调用方）；发布版没有 lineage【码】【桩】（PC/lineage/__init__.py:24-220；core/lineage/__init__.py:186-201；rel/harness_state/state.json） | git worktree `cand/<id>`，不合并就等于回滚；合并后用 `git revert -m 1`；收据只追加，放在仓库外【码】（round.py:322-327, 386-391；lineage.py:29-38, 70-80） | 等价；我们的更贴近 git 审计 |
| 崩溃恢复 | 阶段收据加 `ReconciliationRequired`，副作用未知时不重试；RoboDojo 端口没实现 `resume_*`，要人工对账【码】（sh/__init__.py:97-140；rb/workflow.py:336-620） | 任何异常都写 reject 收据，计入连拒次数，登记 diff_sha【码】（round.py:251-259, 328-339） | 建议 PR10 |
| 防作弊 | AST 黑名单（可被 getattr 字符串等绕过）；提示词禁读；布局审计钩子；事后手动剥离 gt_readers；证据哈希【码】【推】（rb/skill_proposal.py:21-47；rb/layouts.py:36-70；rb/clean_policy_assets.py:16-32） | AST 禁非常量 getattr 和反射；禁 11 项特权属性，外加 success、mark_env_unstable、robot_manager.planner、资产与布局文件；泄漏正则；每轮 L0 都跑作弊自测；V/T 对编辑者不可见；禁止原样重交【码】（etude/frozen/ast_check.py:10-19；gates.py:280-281；设计 §2 边界落地、§3.5） | 我们是每轮前置检查，它是发布前事后剥离 |
| 成本记账 | 没有；试验配额明说不是 GPU 或 token 记账；演示里写死调用次数【页】【桩】（PC/README.md:36；PhysicalRSI_demos/runtime_evolution.py:183） | ΔC ≤ β0 + β1·ΔSR，按每回合 token 和墙钟算，写进收据；每轮预算有公式【码】（gates.py:410-444；设计 §3.4） | 我们有，它没有 |
| 记忆 | LLM 整份替换每任务的 dict，不合并、不剪枝【码】（rb/workflow.py:455-466, 501-502） | 离线写入，条件是官方成功且全部节点 Pass；只增；按指令哈希去重【码】（etude/memory/writer.py:93-111；设计 §3.1） | 我们的入库条件更严 |
| 评测期规划器看到什么 | VLM 上下文剔除 task_name 等 8 类键，但保留带排除条件的能力描述【码】（rb/agent_planner.py:85, 113-115） | 规划提示里直接写任务 id，注明只供 memory_lookup 用；id 取自部署配置【码】（etude/planner/request.py:84-89；etude/adapter/harness_build.py:140, 196） | 在"不给规划器任务名"这一点上它比我们严（建议 PR5） |

**小结。** 我们在四项上比它严：统计检验、成本记账、合并前的人工把关、每轮前置的防作弊检查。它在四项上比我们严：验证布局新鲜且不复用、每轮父子同批重跑、评测器身份每个阶段都复核、规划器看不到任务名。后四项分别对应建议 PR14、PR9、PR12、PR5【推】（本节表格）。

## 10 建议（不直接改文件，供决策）

"必要"指不改会让现有机制按它自己的定义出错；"建议"指可选的增强；"已修"指开发者已在 2026-10-11 改好并提交（commit 9e83a8c）。动 `etude/evolution/`、`etude/frozen/` 或 `frozen/` 的项属于冻结层，要在 M0 或 M3 开跑前由开发者改；动预注册或设计决策的项要用户授权。编号前缀 PR 只在本卡内使用，与 Recova 卡的 P 编号、Memory Anchors 卡的 MA 编号无关。

**按时机排。** M0 开跑前只需 PR4，它是文档修正。M1 开跑前做 PR5–PR8，它们改的是评测期 harness 的输入和边界，M1 起才有规划器在线。M3 开跑前做 PR9–PR12、PR14、PR15，它们改的是门和收据，要在第一轮进化之前定下，免得前后口径不一。PR13、PR16–PR18 随时可做；PR19–PR23 只记录结论，PR24 留给 v2【推】（设计 §5 里程碑表）。

| # | 目标 | 改成什么 | 依据 | 必要性 |
|---|---|---|---|---|
| PR1 | `deploy/XPolicyLab/policy/Etude/deploy.yml` 的 `request_timeout_s` 注释；设计 §2"回路"段、§8.2 F5；`deploy/XPolicyLab/policy/Etude/README.md` Known gaps 第 1 条 | 不再说"只被 debug 客户端读取"；改为两种客户端都是 120 s，debug 客户端的 deploy_cfg 是 argparse 参数、没有这个键 | 3.5；XPL:utils/debug_env_client.py:47, 335-348；RD:src/eval_client/eval_env.py:188-196 | **已修**（a） |
| PR2 | 设计 §8.1 第 4 条 | 删掉"PhysicalRSI 的采样器不在公开适配器里"；改为：采样器在适配器的 `rb/layout_sampling/` 下，配置只有我们的两个开发任务；它不验证物理可行性，注入仿真要靠补丁版 main.py；只借审计钩子和"拒绝 Eval 祖先"检查，加载器自己写 | rb/configs/tasks.json:4-7；rb/layouts.py:21, 36-70, 175-176；rb/native_worker.py:76-94 | **已修**（b） |
| PR3 | `etude/adapter/harness_build.py` 的 `make_perceiver`；`etude/perceive/detector.py` 的 `OwlV2Perceiver`；deploy.yml 的 `perceiver` 段 | OWLv2 从第一次 perceive 时懒加载，改为构建时预加载（`preload: true`），`from_pretrained` 一律 `local_files_only`，权重事先用 hf download 取好 | rb/skill_model.py:104-127（开端口前预载）；xp/eval.sh:56；rb/rgb_grounding.py:84-90；rb/clip_ranking.py:108-115 | **已修**（c） |
| PR4 | 设计 §5 M1 列的"卡点"格 | "节点边界阻塞 ≤1800 s"改为"规划等待 ≤90 s，超时返回保持块；每次 WS 调用 <120 s；记录超时次数"。这是 PR1 漏改的一处 | 3.5；设计 §2"回路"段（已按 PR1 改） | **已修**（2026-10-11，与本卡同一提交）：M1 卡点改为"≤90 s（官方客户端固定 120 s 上限，超过则返回保持块）" |
| PR5 | `etude/planner/request.py:84-89`；`etude/mcp/server.py:263` `HarnessTools.memory_lookup` | 规划提示不再写任务 id；memory_lookup 在服务端直接用 `HarnessState.task`，忽略规划器传入的 task。可与 Memory Anchors 卡的 MA2 合并做 | rb/agent_planner.py:85（PhysicalRSI 把 task_name 挡在 VLM 之外）；P12/P13（part_C:180-181） | 建议：v1 不上榜，但设计按官方协议跑；这条让"规划器看不到任务名"成为结构保证，而不只靠提示词 |
| PR6 | `evolvable/skills/SKILL.md`、`evolvable/prompts/planner.md` 的 L0 审阅；`etude/frozen/leakage.py` | L0 加一条人工检查项：不许写能区分任务的描述，例如排除条件、"只适用于某类物体"。泄漏正则只拦任务 id 本身，拦不住用描述区分任务的写法，这一条只能靠人看 | 4.2 第 2 条（rb/configs/skill_capabilities.json:96-104, 650-660, 1655-1662）；etude/frozen/leakage.py:57, 81（只匹配 42 个任务 id 及其大小写、驼峰、空格变体） | 建议 |
| PR7 | P2 进程启动处（`etude/adapter/xpl_model.py` 构造 Model 时）；`frozen/forbidden.yaml:42` 的 `deny_paths` | 用 `sys.addaudithook` 在 P2 进程内拦截对 deny_paths（Assets/、Eval_Layout、task/*/config/*.yml）的 open，命中就抛错并写 trace。现在 deny_paths 只在 AST 层静态查，MCP 只查 frame_ref | rb/layouts.py:21, 36-70；etude/frozen/ast_check.py:10；etude/mcp/server.py:141, 177 | 建议：作为设计 §2"边界落地"的第二道防线 |
| PR8 | `etude/frozen/ast_check.py` 的自测集（:1212-1343）；`gates.L0` 的自测调用（gates.py:280-281） | 补四个作弊用例并确认都被拦：`functools.reduce(getattr, …)`、`operator.attrgetter` 拼接属性名、`env.__dict__[...]`、`import X` 后 `X.f()` 读 Assets/。检查器文档声称 attrgetter 和 `__dict__` 都会拒（ast_check.py:15-16, 76-88），但自测集里没有对应用例；reduce 没有提到 | 8.1 清理器的盲点 | 建议（冻结层，M0 前加） |
| PR9 | `etude/evolution/round.py` L3 段（:301-302）和 `apply_accept`（:381） | 每个候选都同批重跑现任；至少在 3 份 A/A 结果之间轮换，作为现任参照 | sh/__init__.py:302-317（父子同批）。【推】缓存那一次若碰巧偏低，之后最多 6 个候选的 b−c 都会系统性偏正，ε 和 McNemar 挡不住这种跨候选重复出现的偏差 | 建议（每轮多约 100 回合，按设计 §3.4 的云配置约多 40 分钟） |
| PR10 | `round.py:251-259, 328-339`；`frozen/schema/receipt.json:43` 的 decision 枚举 | 基础设施异常记为 `decision="error"`，不计入 `rejects_in_row`，不登记 diff_sha；L3 写分布局的阶段收据，恢复时只补缺失的布局 | sh/__init__.py:97-140（阶段收据；副作用未知时不重试） | **必要**：停止规则"连续 3 次 reject"和 L0"禁止原样重交"都假定 reject 是门的判断。现在一次 Isaac 崩溃或超时就会消耗停止额度，并永久封掉同一补丁 |
| PR11 | `gates.L3`（gates.py:335-388）；`aa_noise.py:115-141` | 分任务算 b_t、c_t，要求 c_t − b_t ≤ δ_t，δ_t 来自分任务的 A/A；aa_noise 改为保留分任务计数。不用 0 容差 | rb/workflow.py:138-150（0 容差过严）对照我们完全不分任务（第 9 节） | 建议（冻结层，M3 前定） |
| PR12 | `round.py` 的 `RoundConfig.env`（:100-112）；设计 §3.5"改打分或汇总"一行 | 收据的 env 里记 gates.py、aa_noise.py、ast_check.py、frozen/params/thresholds.yaml 的 sha，在 L3 前和 apply_accept 前各复核一次，不一致就拒。设计目前只要求记 summarize_result.py 的哈希 | sh/__init__.py:56-95, 258-300（评测器与选择器哈希每阶段前后复核） | 建议 |
| PR13 | `etude/evolution/lineage.py` 的 `write_receipt`（:70-80） | 用独占创建模式写收据并 fsync。现在是先查 exists 再写，不是原子操作 | core/lineage/__init__.py:71-184（flock 加比较并交换，幂等）；rb/skill_model.py:307-312（每次调用 fsync） | 建议（低优先：目前串行运行） |
| PR14 | 设计 §7 风险表；§3.2 | 新增一行风险："V 被多个候选反复使用，对 V 自适应过拟合"。对策：停止规则已限 6 次，M3 结束时用 T 做单次检验；若要扩容 V，按 §8.1 第 4 条自写加载器生成新鲜的 V′，并在 M0 报告 V′ 与官方 seed 0/1 的 SR 差距 | core/lineage/__init__.py:203-213；sh/__init__.py:258-300（验证布局新鲜、不复用） | 建议 |
| PR15 | 收据的 `evaluated_on`（frozen/schema/receipt.json:44 起）；对外报告和进化图 | evaluated_on 给 E/V/T 标"官方 Eval_Layout 公开布局"；对外报告单列新鲜布局上的数；进化图只能从全部收据画（含 reject 和回退），不画没有 commit 身份的点 | 页json:fold-clothes-evolution 的 exposure / confirmed_unseen 标注和精选曲线（7.3、7.5）；设计 §3.2 Hidden Verification 段 | 建议 |
| PR16 | 设计 §3.1 证据行；§4 MCP 工具 | 给 P6 编辑者一个只读 E 划分关键帧的 inspect 工具，VISTA 式（默认不看、按需看），调用计入编辑成本 | 7.5（fold_clothes 战役看开发期 RGB 做选择）；6.3 第 3 条（仓库提案者只给文本） | 建议（可放 v1.1） |
| PR17 | 设计 §3.0"VLM 自写笔记 GUIDE/LESSONS"一行（v1.1） | 借 ResearchMemory 的三条规则：按精确条件检索；出现反例就挂起；refine 只许收窄 | sh/research_memory.py:47-128（只接在测试里） | v1.1 建议 |
| PR18 | `docs/research/part_C_robodojo_protocol.md` 第 332 行 PhysicalRSI 一格 | "系统提示要求不要选择模型、checkpoint 或独立任务策略"这句改为：它只出自默认 subgoal 提示，只在没被使用的 AgentLoop 路径上生效；评测路径用的是组合选择提示 | rb/agent_planner.py:117 对照 :129-149 | 建议（措辞修正，防止误引） |
| PR19 | — | 记录结论：`update_obs` 只存帧并转发给 runner，不触发规划；规划只在 `get_action` 里发起，等待上限 90 s | etude/adapter/xpl_model.py:488-507；etude/runner.py:402-413；对照 3.5 | 无需改（已核） |
| PR20 | — | 记录结论：`backend_spec` 只把 train_config_name、repo_id、checkpoint_num、task_name 传给进程内后端，不传 action_type；deploy.yml 的 `action_type: ee` 流不进后端的状态打包，后端默认 joint | etude/adapter/xpl_model.py:249-254；etude/backends/pi05_inprocess.py:28-35；对照 rb/skills/pi05_backend.py:98, 190 | 无需改（已核；可在 backend_spec 旁加一句注释说明为何不传） |
| PR21 | — | 记录结论：门已在 `evolve_round` 主路径上（L0 在 round.py:265，L3 在 :306），不像 PhysicalRSI 的 CaseProtocol 那样只在测试里 | round.py:263-327；对照 6.5 | 无需改（已核） |
| PR22 | — | 不借：全任务打包加 0 容差的接受门；纯文本一次性提案；黑名单 AST 加事后剥离。保持设计 §3.1 的"一次一个面"、ε + McNemar、AST 白名单加每轮自测 | 6.3 第 3–5 条；8.1 | 无需改（记录结论） |
| PR23 | — | 不借：直接用它的布局采样器扩 V；从仿真句柄读标定。保持 §8.1 第 4 条（已修）和 §8.2 F2（腕相机伺服，不做自标定 PnP），`etude/executor/ee_move.py` 不变 | rb/layouts.py:175-176；rb/native_worker.py:76-79；5.4 | 无需改（记录结论） |
| PR24 | v2 运动规划器（若引入） | 碰撞世界里要有桌面；目标物体不能当障碍物 | 5.5（rb/primitive_assembly.py:72-80；rb/code_policy_demo.py:15-20） | v2 备忘 |

## 11 相对 10-10 笔记的更正

N1 指 `docs/research/embodied_rsi_update_2026-10-10.md` 第 71–126 行的 PhysicalRSI 卡片，第 19 行是"一页结论"第 1 条。N1 写于只读过项目页、README 和 docs 的时候，下表按今天读代码的结果更正。

| # | N1 原文（行） | 更正 | 依据 |
|---|---|---|---|
| 1 | F 是冻结的多模态基础模型；仓库里是 API 视觉模型，实际型号未报告（:82） | 型号仍未查到，配置里只有占位符，`ARK_API_KEY` 只是变量名上的线索。代码里其实有两个 LLM 角色：评测期每回合调 1 次的图像型路由器，进化期只收纯文本的提案者。代码里的 `foundation` 组件指钉死的 VLA 版本，不是 System-2 模型 | 【缺】rb/configs/skill_library.example.json:4-5；xp/eval.sh:3；【码】rb/agent_planner.py:68-74；rb/proposal.py:44-66；rb/workflow.py:110-135 |
| 2 | 回合开始时选组合，到 reset 不变，不改写观测、指令和动作（:85） | 前半句证实，每个 env 每回合调 1 次。后半句小更正：程序拿到的观测去掉了 `state.mobile`，夹爪键被改名；agent 看到的是过滤后的视图 | 【码】rb/skill_model.py:136-138, 247-248, 335；rb/skills/program_service.py:96-122；rb/agent_planner.py:85 |
| 3 | 改写出多个候选 harness，按具身评测选出后继承，即 Darwinian Self-Harness（:86；模块行 :123） | 只有页面这么说。所有调用方都传 `max_candidates=1`，每轮是父代对一个子代，即 (1+1) 爬山；代码里没有 `Darwinian` 一词 | 【码】rb/workflow.py:163；AE/harness.py:154；core/self_harness/__init__.py:60-61 |
| 4 | 具体指标和评测集划分未报告（:87） | 指标是原生 `env.eval_result` 的 score∈[0,1]，按任务归一后加权；dev 和 val 每轮新采，没有 test | 【码】rb/native_observer.py:41-76；sh/selection.py:164-266；sh/protocol.py:81-100 |
| 5 | 核心循环实现了"在新的成对布局上评测，选出存活者"（:90，引 implementation-status） | 证实，RoboDojo workflow 在代码里确实这样做（6.2 第 6–9 步）；但 implementation-status 那句话原本指的是本地 counter 契约 | 【页】rdocs/implementation-status.md:3, 12；【码】sh/__init__.py:258-300 |
| 6 | 代码提案先过静态检查（禁 import、文件访问、网络、动态执行、dunder），再在新布局上验证通过才成为正式路由（:91） | 部分证实。静态检查是黑名单：不禁 getattr、setattr、type，不查签名，作者自认不是沙箱；网络只是靠禁 import 间接挡住。路由由开发分数和阈值决定；整个子代与父代比，门没有噪声余量；有 VLA 过阈值时，新代码只入库、不被路由 | 【码】rb/skill_proposal.py:21-47, 86-90；rb/proposal.py:81；rb/skill_selection.py:48-79；rb/workflow.py:138-150, 486-494 |
| 7 | 10-09 读官方榜为第 2 名，10-08 被 VPP2 的 39.26 / 32.26% 超过（:93） | 不冲突：页面的 #1 依据的是 10-03 更新、10-08 核对的快照，页内 VPP2-Preview 当时是 31.40 / 25.62。N1 里 VPP2 的数字本轮没有复核 | 【页】页txt:377-384；页json:benchmarkEntries |
| 8 | fold_clothes 进化曲线从 k=1 的 7.5% 到 k=4 的 38%（:97） | 是精选展示：三个不连续的修订加一个没有修订身份的"官方"终点，终点等于榜上两个设置的平均；页面自己说这不是学习曲线；同一 cohort 上 R23 有 65%，R16 掉到 2.5% | 【页】【推】页json:fold-clothes-evolution（7.3） |
| 9 | 技能库共 212 个片段，覆盖 40 个任务（:97；模块行 :123） | 212 是视频时间窗，带 106 种标签；代码程序是 22 个，本体不在仓库里 | 【页】页json:skill-clips；【缺】rel/memory/capability_memory.json；rel/download_assets.py:14-15 |
| 10 | 代码程序按任务专门编写，可用标定的相机或平面和几何先验（:106） | 证实，另补三点：程序与任务模块一一对应；能力描述里写了区分相邻任务的排除条件；代码自称 task-aware | 【页】xp/README.md:78-80；【码】rb/configure_skills.py:48-71；rb/configs/skill_capabilities.json:650-660；rb/skill_model.py:93 |
| 11 | 发布版移除了仿真状态读取器；开发期是否用过未报告（:107, :120） | 降级为"无法核实"：清理器只能手动运行，没有调用方，用树内代码也复现不出发布版。新证据有两条：剥离工具和测试把它们叫 gt_readers，说明开发版源码里有这类读取器（推断）；fold_clothes 的开发用的是 Eval_Layout 文件，标 `confirmed_unseen=false` | 【码】rb/clean_policy_assets.py:1-20, 192, 198-204；tests/test_policy_asset_cleanup.py:4-27；【页】页json:fold-clothes-evolution episodes；【推】 |
| 12 | 没有论文，没有成本记账（:108） | 证实。试验配额明说不是 GPU 或 token 记账；只有路径名暗示约 24–27 卡、多次 attempt | 【页】PC/README.md:36；【推】页json:fold-clothes-evolution |
| 13 | 审计文档声明不能证明当前产物与评测版本一致（:109） | 证实，另有旁证：能力文件引用的 skill_model.py 哈希与当前文件不同，行号也对不上 | 【页】rdocs/xpolicylab-audit.md:11-24；【码】rb/configs/skill_capabilities.json:30-34 |
| 14 | 可复用：回合级技能组合路由器，首帧选执行器、回合内不变（:111） | 要加限定：评测时它实质上等价于按任务分派（4.3），有 P13 风险 | 【码】rb/skill_model.py:137, 247-248, 335；【页】part_C:181, 351；【推】 |
| 15 | 可复用：lineage 记录和可从断点恢复的收据（:112）；回滚未报告（模块行 :123） | lineage 是真的，但 RoboDojo 端口没实现任何 `resume_*`，崩溃后要人工对账；还有身份范围过宽、绝对路径做键两个坑。核心里有 `rollback()`，适配器副本里没有调用方；发布版没有 lineage 记录 | 【码】sh/__init__.py:97-140；PC/lineage/__init__.py:193-208；【桩】core/lineage/__init__.py:186-201；rel/harness_state/state.json |
| 16 | 可复用：代码策略的静态检查边界（:113） | 它是黑名单式语法门，作者自认不是沙箱；真正的隔离（chroot 加 seccomp）要 root 权限 | 【码】rb/proposal.py:81；core/infra/isolation.py:76-86 |
| 17 | XPolicyLab 里的 physicalRSI 是只做评测的适配器，技能资产经 manifest 下载（:116） | 证实；补充一点：适配器的 runtime/ 里带着完整的 RSI 回路和布局采样器副本，只是评测路径执行不到 | 【码】rb/workflow.py:336-620；rb/layout_sampling/catalog.py:22-64；第 8 节 B 组 |
| 18 | Self-Harness 进化循环是确定性的本地 demo，外部模型生成代码只提供接口（:117） | 只有不绑定任务的 `/evolve` 是确定性演示。RoboDojo 路径有真实的 LLM CodeProposer，`workflow.create` 默认用 api 模式；但 `native_package` 默认用离线模板，而且只接了 2 个任务，还依赖本地缺失的原生运行时 | 【码】P/application.py:157-160；rb/workflow.py:623-637；rb/native_package.py:25, 77-79；rb/configs/tasks.json:4-7 |
| 19 | 探索期 RESET 未报告（:121） | 仓库回路每轮新采开发布局，验证布局冻结后才生成、永不复用；fold_clothes 战役却在多个修订之间复用同一个 40 场景 cohort | 【码】rb/native_runtime.py:155-163；core/lineage/__init__.py:203-213；【页】页json:fold-clothes-evolution |
| 20 | PhysicalRSI 的五维 SR 平均正好等于 31.38（:19） | 只在四舍五入意义上成立：用四舍五入后的维度值算是 31.386，按单个设置重算是 31.383 | 【推】页json:category-analysis |

N1 之外还有三处我方文档要改：设计 §2 与 §8.2 F5 的超时说法、设计 §8.1 第 4 条的采样器位置，这两处已修（PR1、PR2）；`part_C:332` 对系统提示的转述尚未修（PR18）。

## 12 未解决的问题

1. **22 个程序里到底有什么。** v4 的 assets.json 列了哪些资产、多大、分几段【缺】（xp/download_assets.py:14-15）？程序读哪些文件和常量，有没有打开 Assets/、Eval_Layout 或任务 yml，是否经过清理器【缺】？要回答，得把资产下载到一个新建的隔离目录，不执行、不导入，只按 `frozen/forbidden.yaml` 的完整名单做静态审计；下载前要用户批准，并说明文件名、来源和大小【推】。同时要拉 XPolicyLab@393f730 的 `policy/physicalRSI/` 做对照，因为克隆里的子模块是空的【缺】。
2. **程序怎么运行。** programs.json 是否对全部程序设了 frozen-program-service【缺】（rb/skills/code_policy.py:8-14）？launcher、`transport.binding` 和 sidecar 加载了什么模型，会不会访问外网【缺】（rb/skills/program_worker.py:19-21, 43-61）？
3. **它的 π0.5 权重怎么训的。** 两份权重用了哪些数据、任务和布局，和官方 Pi_05 是什么关系，`all35` 是否指 35 个训练任务，都未查到【缺】（rb/configs/skill_library.example.json:17-34；rel/README.md:18-24）。stack_bowls 上 74.67 对官方 π0.5 的 38.0，差距有多少来自权重、多少来自路由，也不知道【推】（7.2）。这直接影响我们开发任务基线的可比性。
4. **fold_clothes R10–R23 由谁做的。** 是 CodeProposer、人、外部 Codex 类 agent，还是混合？分叉和合并按什么选？仓库回路只支持单父单子的比较并交换【缺】（页json:fold-clothes-evolution；sh/__init__.py:320-357）。OFFICIAL 38.0 / 46.13 对应哪个修订，榜上这一项由 VLA 还是 serve_cap 的 bundle 跑出，都未查到；fold_clothes 不在能力记忆里【缺】（rel/reference_runtime/serve_cap.py:8-21）。仓库里的 `configs/fold_clothes.task.json` 和 `examples/fold_clothes/` 与这场战役是什么关系，本卡未读。
5. **Eval_Layout 是否就是官方评测布局。** `Eval_Layout/RoboDojo/arx_x5/{0,1,2}` 是否就是官方榜用的布局，未查到【缺】。如果是，它的开发直接在评测集上做了优化【推】；我们的 E/V/T 也处在同一处境，因为 `seed_manager` 只读 Eval_Layout【页】（设计 §8.1 第 4 条；设计 §3.2 Hidden Verification 段）。
6. **标定数值从哪来。** 程序用的相机、平面、基座标定值，是否来自 native_camera / native_robot 读出的仿真数据，未查到【缺】（5.4；rb/native_camera.py:17-69；rb/native_robot.py:9-88）。
7. **官方榜用的哪个 VLM。** 模型和 endpoint 未查到；temperature 0 下路由是否稳定、各任务（含 `*_random` 变体）被路由到预期程序的准确率是多少，也未查到【缺】（rb/agent_planner.py:67-74, 157-158）。
8. **官方运行的配置。** 官方运行用的客户端是否遵守 `request_timeout_s`【缺】（3.5）？评测是单 env（xp/deploy.yml:12）还是 batch（rb/xpolicy_eval.py 用 `--eval_batch true`）【码】？两份 π0.5 加 sidecar，在 0.3 的显存占比下能否放进一张卡，未查到【缺】（xp/eval.sh:7）。
9. **发布物是否就是评测物。** 能力文件的哈希和行号都与当前代码对不上（5.1），发布的能力文件是否就是官方运行用的那份，未查到【缺】。提交版本里是否还带着 gt_readers 也未查到，`source-cleanup-review.json` 不在仓库里【缺】。
10. **成本。** 总共跑了多少轮、多少次 LLM 调用、多少 token 和 GPU 小时，未查到【缺】。路径名里的 `max20/max40 … attempt_N` 是不是同一任务多次官方尝试取最好，也未查到【推】（页json:fold-clothes-evolution）。
11. **22 个程序的作者。** 多少出自 Self-Harness，多少是手写，未查到【缺】（7.1）。
12. **P12/P13 怎么界定。** 由指令选择按任务编写的程序库，算不算 router，需要向 RoboDojo 维护方确认【页】（part_C:351）。
13. **画廊少了什么。** 画廊有 52 个设置，官方集合应有 54 个；少的两个 random 设置是哪两个、为什么不放，未查到【推】（页json:settings-gallery）。
14. **对我们自己。** 若按 PR14 自采 V′，π0.5 在 V′ 上的 SR 是否与官方 seed 0/1 一致？这要在 M0 加一项分布差距检查【推】（设计 §5 M0 列）。
