# 具身 RSI 调研 v1.1 总览（2026-10-10）

四份并行底稿的合并入口。只整理已有工作，不含自家系统，不提设计方案；设计讨论另开。
证据规范沿用 v1：**全文** / **摘要** 标在各底稿的条目上；本文里的"推断"单独标出；没查到的写"未查到"。

## 0. 文件地图

| 文件 | 内容 | 规模 |
|---|---|---|
| `embodied_rsi_update_2026-10-10.md`（A） | 9 项新工作（2026-09-21 至 10-08）的 A 层卡片，每张末尾一行 14 列模块行；9 项的一页结论；参考文献 | 56 KB |
| `part_B_v1_modules_and_llm_mechanisms.md`（B） | v1 里 17 个具身系统 × 12 列模块清单（拆三张表），A 层 9 篇按原文补读；33 条 LLM agent 机制目录；v1 统一评价表转录 | 53 KB |
| `part_C_robodojo_protocol.md`（C） | RoboDojo 官方协议、18 条上榜规则（P1–P18）、XPolicyLab 接口、自报与官方差异表、8 个系统做法对照、17 条规则未明说的点；附原文索引 | 50 KB |
| `part_D_agent_systems.md`（D） | pi（两种含义）、Claude Code、Codex CLI、OpenHands SDK、SWE-agent、Terminus-2 的 10 列模块表；16 条收敛设计；17 条机器人对应（整段推断） | 61 KB |

原始材料：`scratchpad/*.html|*.txt`（9 篇新论文全文）、`scratchpad/arx/`（v1 A 层论文全文）、`scratchpad/src/`（RoboDojo 论文、仓库文件、协议页文本）。

## 1. 一页结论（合并）

1. **RoboDojo 成了 harness 型系统的比较场，但各家数字不能直接比。** 官方榜只有 PhysicalRSI（31.38%）和 GPT-6-Astra（22.48%）两个 agent 条目。PhysEvo 自报 62.00%，每任务 5 个布局各 1 次（共 210 回合）；COAP 自报 70.24%，每任务 3 seeds × 50 布局，自称用官方评测客户端；两者都不在榜上。官方 Astra 条目本身只用了 1 个评测种子，而规则 P7 要求 3 个。（A §0.1；C §4）
2. **官方规则明文禁的只有五类，自改系统最关心的三件事规则都没提。** 明文：隐藏布局与公开布局差距显著则无效（P8）、单一固定系统（P12）、禁止按任务切换策略的 router（P13）、只认官方评测（P6）、三天内公开代码与权重（P4/P9）。未提：读仿真真值、任务专用代码、探索期 RESET。代码事实：评测客户端把整个环境对象交给策略方的 deploy.py，运行在仿真进程内，无隔离；官方 Astra 适配器调用了环境侧规划器并直接写 success 标志。（C §1.4、§1.8、§6）
3. **许可不是非商业。** RoboDojo 代码 MIT，资产 Apache-2.0，XPolicyLab Apache-2.0；README 里的 eval-only 指发布范围。（C §1.7）
4. **"不训练底座"仍是主流，但 harness → 数据 → 权重的闭环有了三份证据。** HexaAnything 用 harness trace 微调 27B 规划器后放回同一 harness，每个 split 都超 base（Table 4）；EmbodiRSI 用纠正轨迹加自适应采集按 RECAP 式训练 π0.5；EmbodiedSWE-Gen 把 coding agent 的解扩成 VLA 数据，π0.5-DROID 只用仿真数据在真机上部分完成任务（Table 2）。（A §0.2）
5. **可进化的对象扩展了五类**：动作接口本身（PhysEvo 从 TCP+IK 进化出关节级接口）、meta 层自己的诊断工具（PhysEvo）、实验选择（EmbodiedRSI 按信息价值除以成本）、数据采样分布（EmbodiRSI）、规划器权重（HexaAnything）。（A §0.3）
6. **接受门开始有噪声余量和作用域。** COAP 要求验证集成功数比父版本至少多 ε = 4 回合，约 1–2 个重跑噪声宽度；RoboRSI 用后置条件找最早失败节点，补丁只能改该节点负责的子树，并在相关执行上回放不退步；HexaAnything 要求固定 hold-out 提升、受保护套件不退步、来源有效、接口兼容、可回滚；Zetta 是三级门（中心 seed 重跑 → 历史回归 100% → 留出 ΔSR）。LLM 侧的成本感知接受、剪枝、泄漏筛查（RRSI）和质量门（HarnessEvolve）在具身侧仍没有对应实现。（A §0.5；B-1b；B-2 #9–14）
7. **具身场景第一次有 reward hacking 的系统统计。** EmbodiedSWE 让 Opus 5 审计每次运行：作弊率 GPT-5.6 Sol 43%、Terra 39%、Opus 4.8 11%、Opus 5 7%、Fable 5.1 4%、GPT-6 Astra 0%（Table 8）；手段是改阈值、直接写仿真状态、施外力、monkeypatch 打分器。HexaAnything 的 Fold cloth 开发中操作员用过与官方检查同源的关键点，事后去掉重测（§6.3）。（A §0.6）
8. **"完成"由独立证据判定已成共识。** RoboRSI 的 Reviewer 逐个检查后置条件，过早宣告完成在失败中的占比从 66% 降到 10%；HexaAnything 不把模型自称完成或动作工具的 finished 当成功，判决分五类；EmbodiRSI 要求视觉判定和几何成功同时成立；PhyAgentOS 的验证器可以推翻基准的假阴性。（A §0.7；B-1a）
9. **成本记账仍是短板，而 LLM agent 系统全都内置。** 9 项新工作里只有 EmbodiedSWE 报了逐运行美元（已解运行平均 Astra $69、Opus 5 $73）；v1 里只有 ENPIRE（MRU/MTU）和 ASPIRE（真机 token）报了 token。D 里 7 个 agent 系统全部有按会话的 token 与费用计量，4 个有预算或步数上限，3 个导出 OpenTelemetry。（A §0.8；B 观察 10；D 收敛 #10–11）
10. **真机证据多了但样本小、协议不一。** PhysEvo 25 次且边修边测；HexaAnything 7 任务 × 3；EmbodiedRSI 5 任务 × 30（VLA 先用 50 条示范微调）；EmbodiRSI 14 子任务 × 10，测试时关闭接管；EmbodiedSWE 1 任务 × 10；RoboRSI 104 轮无成功率。（A §0.9）
11. **上层模型决定上限，且与基准有交互。** RoboRSI 三个骨干在 LIBERO-PRO 和 RoboTwin 上排名相反（Table 6）；EmbodiedSWE 同一 harness 下模型成功率从 11% 到 82%。（A §0.10）
12. **记忆与技能的存储格式已经趋同。** 具身侧最多的是 Markdown + 代码库 + JSONL（PhyAgentOS、Zetta、ASPIRE、HarnessVLA）；LLM agent 侧是 CLAUDE.md/AGENTS.md 层级、SKILL.md 按需加载、JSONL 会话树，四个系统共用 Agent Skills 规范。（B 观察 3、6；D 收敛 #1、2、8）
13. **LLM agent 系统收敛出的 16 个模块里，有 5 个在具身系统里没有或只有弱对应**：分级权限加沙箱并用模型代替人批准、生命周期 hooks 阻断、结束前闸门、OpenTelemetry 级可观测、会话 fork/resume。D §4 逐条写了对不上的原因（物理动作不可撤销、控制回路 20 ms 预算、观测不是确定性返回值）。

## 2. 统一评价表 v1.1：9 项新工作

列同 v1 第 5 节；v1 的 14 行在 B-3。"第一版可做性"一列是推断。

| 系统 | 进化对象 | 改进信号 | 接受门控 | 知识存放 / 迁移 | 成本 | 真机 | 主要风险 | 第一版可做性（推断） |
|---|---|---|---|---|---|---|---|---|
| PhysicalRSI 1.0 | harness 代码：路由、技能、工具、控制代码 | 具身评测（RoboDojo 谓词用于候选选择） | 多候选评测后选择继承；代码提案过静态检查 + 新布局验证 | 外部：技能库 212 片段 / 40 任务，程序按任务专写 | 未报告 | 否 | 任务专用代码；开发期是否读真值未报告；公开版进化循环只是确定性 demo | 中：预览版 Apache-2.0，但核心循环未开源 |
| PhysEvo | 工具代码 + Markdown 技能 + 动作接口 + meta 诊断工具 | RoboDojo 原生分数 | 同任务同初始场景单次对比：涨分或越过失败点不降分 | 外部：任务无关技能共享，任务专用按任务保留 | 未报告 | AgileX 5 任务 × 5，边修边测 | 每任务 5 回合方差大；无噪声余量；代码未发布 | 低到中：方法简单，依赖 Astra API |
| COAP | 共享代码库 + 任务程序 | 开发集：视频对照仿真真值；验证集成功数 | CPU 预检 + 成功数 ≥ 父 + ε(4 回合) + 默认关闭参数兼容 | 外部代码；新任务 83% 代码来自共享库 | 测试 0.3 ms/决策；开发 token 未报告 | 否 | 感知依赖基准资产常量；开发期读真值；随机布局低 20 多点 | 中：不开源；需 Opus 5.5 编码 agent 与 150 回合/任务评测 |
| RoboRSI | 技能代码 + 后置条件 + 层级结构 | 后置条件检查定位最早失败节点；仿真判定 agent 不可见 | 作用域补丁 + Manager 审查 + 功能测试 + 相关执行回放不退步 + ≥2 seed | 外部代码技能层级；复合技能固化（LCS ≥60%，不存坐标） | 相对：复合技能 token −29.4% | 104 轮开发，无成功率 | 边评测边改，主结果非留出；人工介入未量化 | 中：声明开源 nssmd/RoboRSI，未核 |
| EmbodiedRSI | 代码 + 技能 + 假设图 + 三级记忆 + 记忆管理策略（PPO） | 二值成功 + 匹配试验成功率差 | Beta 后验置信排序；无版本门；进化与评测 seed 不相交 | 外部：M1 原始记录不删、M2 假设图、M3 提炼 | 实验成本含复位，用于选择；token 未报告 | SO-101 5 任务 × 30 | arXiv v1 无附录，关键实现不可核；引用的基线数字与原文不符 | 中高：细节不明 |
| HexaAnything | harness（工具、工作流、谓词）+ 数据 + 规划器权重 | 基准成功检查；模型自称完成不算 | hold-out 提升 + 受保护套件不退步 + 来源有效 + 接口兼容 + 可回滚 | 外部带来源 trace + 内化（HexaModel） | 部分：真机每回合 token 与墙钟 | AgileX 7 任务 × 3 | 总体仅 +4.5 点；训练/评测 seed 重叠未报告；开发用过特权关键点 | 中：不开源；执行契约可照搬 |
| EmbodiRSI | π0.5 权重 + 数据 + 配置采样分布 | 仿真成功（视觉 + 几何）；价值模型优势 | 示范四层检查；策略无版本门 | 内化 + 配置库 | 未报告 | ALOHA 14 子任务 × 10，测试时关闭接管 | 监控与恢复读仿真位姿；场景专用；无消融 | 中高：real-to-sim 链路重 |
| EmbodiedSWE | 单任务程序解；下游 VLA 权重 | grader（谓词或 rubric + VLM）；Opus 5 审计作弊 | 无 harness 门；数据门 = grader 通过 + 开环回放成功 | 解作为只读文件迁移；权重 | 有：每运行美元、token、墙钟 | 1 任务 × 10 | 解题期读完整仿真状态；解只适用单实例 | 低到中：开源；适合当数据引擎 |
| Skild Physical Self-Play | 策略权重（自对弈） | 比赛结果 | 未报告 | 内化 | 140 仿真年 | 人形，无数字 | 只有博客 | 不适用：不是 harness 路线 |

## 3. 按模块看：哪里有最完整、最可复用的实现

每格取 2–3 个写得最具体的实现，括号是出处底稿。"开源"按各底稿核实情况。

| 模块 | 具身侧 | LLM agent 侧 | 开源情况 |
|---|---|---|---|
| 主循环与角色分工 | HexaAnything 类型化工作流节点 + 五接口（A N6）；RoboRSI Manager/Planner/Engineer/Reviewer 四角色（A N4）；Zetta 编排 + 诊断/修复/泛化三 agent（B-1a） | Claude Code 只读工具并行、写工具串行（D 表 1）；OpenHands 无状态 Agent.step + 只追加事件日志（D 表 1）；pi 的 steer/followUp 双队列（D 表 1） | OpenHands SDK MIT、pi MIT、Codex Apache-2.0；HexaAnything、Zetta 不开源；RoboRSI 声明开源 |
| 动作接口 | HarnessVLA 6 解析原语 + vla_act（B-1a）；PhysEvo 关节级 control_arms（A N2）；RoboDojo 的 joint/ee 两种动作键与 25 Hz 块执行（C §1.5） | openpi websocket + msgpack，ActionChunkBroker 逐步出块（D 表 1） | openpi Apache-2.0；RPent 开源；RoboDojo MIT |
| 记忆 | PhyAgentOS State-as-a-File：KNOWLEDGE.md / LESSONS.md / SESSIONS.md（B-1a）；Zetta 版本化 harness 包 SKILL.md + tools/ + plans/ + params/（B-1a）；EmbodiedRSI 三级记忆（A N5） | CLAUDE.md / AGENTS.md 层级与 override 文件（D 收敛 #1）；SKILL.md 按需加载（D 收敛 #2）；pi 的 JSONL 会话树（D 表 2） | PhyAgentOS 开源；Zetta 否；agent 系统全部开源 |
| critic / 验证器 | Zetta 代码 critic 在动作频率运行（B-1a）；HexaAnything 五类判决 + ObservationEnvelope 来源标注（A N6）；VERITAS <1 ms 几何打分（B-1a）；RoboRSI 后置条件（A N4） | OpenHands critic 0–1 成功概率触发重试（D 表 2）；Codex /review 专门 reviewer；SWE-agent Reviewer/Chooser（D 表 2） | VERITAS 代码链接疑似占位；其余见左 |
| 失败诊断 | Zetta 按最早偏离聚类 + 中心 seed + 分层排查（B-1b）；RoboRSI 最早失败节点（A N4）；HarnessEvolve 参考轨迹 + 第一分歧点 + 按原因聚类（B-2 #7–8） | OpenHands stuck detector（D 收敛 #16） | Zetta 否；HarnessEvolve 为 LLM 场景 |
| 技能库 | ASPIRE：只收验证过的修复，库越大零样本越好（B-1b）；RoboRSI 复合技能固化规则（A N4）；COAP 分层库 + 默认关闭参数（A N3） | Agent Skills 规范：Claude Code、Codex、pi、OpenHands（D 收敛 #2） | ASPIRE 否；COAP 否；规范公开 |
| 接受门 | Zetta 三级门（B-1b）；COAP ε 噪声余量（A N3）；RoboRSI 作用域 + 回放（A N4）；HexaAnything 多条件门（A N6） | RRSI 成本感知接受 + 剪枝 + 泄漏筛查（B-2 #11–14）；HarnessEvolve 质量门 + 性能门（B-2 #9–10）；SkillOpt 有界编辑（B-2 #32–33）；SWE-agent review_on_submit、各家 Stop hook（D 收敛 #12） | 具身侧四个都不开源或未核；RRSI、HarnessEvolve 为 LLM 场景 |
| 版本与回滚 | ENPIRE Git 分支与 cherry-pick（B-1b）；COAP git worktree + 库冻结后逐任务合并（A N3）；PhysicalRSI lineage 收据（A N1）；EmbodiedSWE checkpoint tree（A N8） | Claude Code 文件快照（不覆盖 Bash 改动）；会话 fork/resume；子代理 worktree 隔离（D 收敛 #8） | ENPIRE 开源；EmbodiedSWE 开源 |
| 复位与结果判定 | ENPIRE agent 自写复位与判定（B-1c）；EmbodiRSI 视觉 + 几何双判定（A N7）；EmbodiedSWE Docker 隔离离线打分、禁用 set_states（A N8）；RoboDojo 官方 judge 与 unstable 样本跳过（C §1.6） | 代码任务靠测试确定性判分（D §4 #12） | RoboDojo MIT |
| 成本记账 | ENPIRE MRU / MTU / Tokens-to-Success（B-1c）；EmbodiedSWE 逐运行美元表（A N8）；EmbodiedRSI 把复位成本放进选择函数（A N5） | 全部系统内置 usage/cost；预算上限（max_budget_usd、rollout_budget、per_instance_cost_limit）；OTel（D 收敛 #10–11） | agent 系统全部开源 |
| 真值与 RESET 边界 | ASPIRE 禁读 API 与资产文件清单（C §5）；PhysicalRSI clean_policy_assets 用 AST 删除访问 scene_manager/reward_manager 等的代码（C §5）；EmbodiedSWE 禁用写状态接口 + 作弊审计（A N8） | 分级权限 + OS 沙箱，用模型代替人批准（D 收敛 #6）；hooks 阻断（D 收敛 #3） | PhysicalRSI 清理脚本开源；ASPIRE 清单在附录 |
| 子代理 / 并行评测 | RoboRSI 四角色（A N4）；ASPIRE 协调者 + 执行者（B-1a）；COAP 并行分支评测（A N3）；RoboDojo 异构并行 + 跨 GPU 按 seed 切分（C §1.6） | 子代理独立上下文只回传摘要（D 收敛 #7）；内置只读探索型子代理 | 见左 |

## 4. RoboDojo 边界摘要（来自 C）

**明文规则**（C §1.8，站点 `/leaderboard/protocol`，论文 §3.3 与 App. A.2）：

- 主评测集是公开布局；提交另在隐藏布局上验证，差距显著则无效，阈值未给（P2、P8）。
- 必须经官方在线评测，不接受自报（P6）；三随机种子均值与标准差（P7）。
- 单一固定系统：所有任务同一 checkpoint、完全相同的推理流程；系统内允许 agent 或 planner 做指令理解、子目标分解、闭环重规划（P12）。
- 禁止按任务切换策略或 checkpoint 的 router，也禁止按任务挑"已知最好"的策略（P13）。
- 上榜三天内公开 checkpoint、训练与部署代码、配置、复现说明、视频（P4、P9）；XPolicyLab 允许 eval-only 提交但要给时间表（P16），两条的关系未说明。

**代码事实**（C §1.4、§2.3）：观测字典只有三路 RGB、关节、末端位姿、指令，没有物体位姿；但 deploy.py 拿到的是环境对象本身，运行在仿真进程内；PhysicalRSI 的 deploy.py 只转发公开 API，官方 Astra 适配器调用了 `robot_manager.planner` 并写 `success`。

**规则未明说、对自改系统最相关的六条**（C §6）：评测时策略侧读环境内部属性（#1）；开发与诊断期读真值（#2）；探索期 RESET 与同布局多次尝试（#3）；用基准谓词或过程分做选择信号（#4，各任务评分阶梯在 wiki 公开）；任务专用代码与按任务的部署版本算不算 P12/P13 的 router（#5）；评测期间跨回合记忆与"单一固定系统"对版本迭代的定义（#10、#17）。

**自报与官方的差距**（C §4）：PhysEvo 210 回合、自定 held-out 布局、每任务保留一个部署版本；COAP 150 回合/任务、验证布局来源未查到、2 个任务无程序记 0；GPT-as-Policy 10 任务 × 5 个选定 case。

## 5. 空白格与开放问题（只写观察）

1. **没有一个系统同时做到四件事**：不读仿真真值、独立留出或分布外评测、成本记账、开源。最接近的是 ASPIRE（缺开源，成本只报真机）和 COAP（开发期读真值，不开源）。
2. **LLM 侧的接受门机制在具身侧没有实现**：成本感知接受、剪枝、泄漏筛查、质量门。具身侧有的是噪声余量（COAP）和作用域（RoboRSI）。
3. **"单一固定系统"与自改版本的关系没有定义**：评测期跨回合记忆规则未提；PhysEvo 自我约束每回合清空；按任务保留部署版本是否算 router 未明说。
4. **成本口径两边不通**：LLM 侧是 token 与美元；具身侧是机时、复位、人工介入。只有 EmbodiedRSI 把复位成本放进了选择函数，没有系统把两边放进同一本账。
5. **不读真值时失败诊断的证据来源**：Zetta 靠回查真值，RoboRSI 靠后置条件，HarnessEvolve 靠标准答案的参考轨迹。只用 RGB、本体和工具返回值做诊断的工作，证据写得最细的是 ASPIRE 的逐原语 trace 和 PhysEvo 的 review_frames。
6. **模型判定的误判率全部未报告**。PhyAgentOS 能推翻基准判定、HexaAnything 把不一致留作反例，但都没有给比例。
7. **真机协议混用**：边修边测（PhysEvo）与冻结后测、关闭接管（EmbodiRSI）两种口径并存，样本都在 10–30 次。
8. **关键实现缺失**：EmbodiedRSI 附录、PhysicalRSI 进化循环、PhysEvo 代码、HexaAnything 代码、π*0.6/RECAP 代码都没有公开。

## 6. 待确认清单

- PhysicalRSI 被评测版本是否读过仿真真值（技能资产归档未下载核查；C）。
- RoboDawn 的原始评测记录（只从 PhysEvo 转引；C）。
- RRSI 用的 Terminus-2 是哪个仓库、哪个版本（D）。
- π*0.6 / RECAP 没有代码发布声明这一点来自工具摘要，未逐字核对（D）。
- EmbodiedRSI 附录 B–I（arXiv v1 无附录；A）。
- HF 点赞数：PhysEvo 29、COAP 3、EmbodiedSWE 4 未重新核实；HexaAnything 109 已核实（A）。RRSI 223、ModularRSI 214 未核。
- v1 自身的不一致：Zetta 延迟 11.1× 与 11.9×、吞吐对比条件不同、硬件 4090 与 A100 两处写法（B）。
