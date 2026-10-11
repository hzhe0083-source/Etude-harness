# 任务

为我设计一个**能跑起来、并且能自进化**的 VLA harness 第一版（v1），要可以照着开工的设计文档，不要领域综述。稳定、可复现、可审计优先于分数。

"自进化"指两条回路，都不训练权重：记忆回路（无 LLM 编辑者，过门的成功节点序列入库）；harness 回路（LLM 编辑者按开发证据改白名单文件，候选过隔离的评测门才生效，可回滚）。它接在已定的三条件实验后，作条件 4。

# 已定决策（不要重新论证，当约束用）

某条若事实上做不到（不是"不够好"），在第 8 节开头标出并给最小修正，其余照写，不换路线。

0. 路线：π0.5 只做接触段，代码管非接触移动、验证、恢复。不走纯代码技能路线（COAP、PhysicalRSI、RoboRSI），感知代码在新布局上崩了兜不住；这三篇只借进化机制。
1. 底座：冻结 π0.5，经 openpi policy server（websocket + msgpack，一次观测换一个动作块）。不训练权重；ARLI、RECAP、代码 critic 放第二版。
2. 规划运行时：pi-mono（earendil-works/pi，TypeScript）。权限、子代理、plan mode 用扩展加；tool_call 可阻断；JSONL 会话树当 trace；消息带 cost。
3. 测试台：RoboDojo 42 个仿真任务（ARX X5 双臂，三路 RGB，25 Hz，观测里没有物体位姿；默认 depth 和相机内外参关），经 XPolicyLab 接入（model.py 实现 update_obs / get_action / reset）。官方 3 个评测种子、native 回合数（普通 50，Generalization 25 + 25 random）、summarize 出数，不提交榜单，不做针对基准的 hack。开发 seed 0/1，seed 2 留出；记忆和规则在两个任务上积累，另两个任务上测。3 种子总数含开发种子，seed 2 单独再报一组。
4. 三条边界。RoboDojo 不强制（官方 Astra 适配器就写了 success），用 tool_call 加 AST 检查拦，进化候选也过。
   - 不读特权信号。原则照 ASPIRE 附录（E.2 还是 E.3 请核实）：装了相机的真机能做到的就允许。具体禁读照 PhysicalRSI clean_policy_assets 的清单（scene_manager、reward_manager、get_instance_pose、end_flag 等），另禁 success、robot_manager.planner、资产和布局文件。允许三路 RGB、关节、末端位姿、夹爪状态、ee_pose 动作键。
   - 协议兼容。P12 允许 planner 但同一推理流程；P13 禁止按任务切换策略的 router。任务专用的只能是数据（节点序列），不能是代码。
   - 评测对被进化的部分隐藏。打分进程和写入者分开，Docker 隔离打分，禁写状态接口，审计样本不给写入者看。
5. VLM–VLA 契约：VLM 只出带可观测后置条件的原子节点序列；VLA 只接短指令 + 停止条件 + 块预算；验证器不信任何自述，判决用 HexaAnything 五类 Pass / Fail / InsufficientEvidence / Blocked / SafetyStop。VLM 不进 25 Hz 回路、不出关节目标、不宣告成功。VLA 不在段内自行重试：交还后先原地重试至多 r 次（默认 2，只改最早失败节点），仍失败退回上一个已验证节点。记忆存序列（坐标换感知查询），不存摘要，原始 trace 全留。节点字段已定：id / executor（vla、ee_move、gripper）/ instruction / args.target = perceive("…") / stop（停滞块数、max_chunks）/ postcondition / on_fail {retry, then: previous_node} / evidence；进化只改字段值或增删序列。
6. 三层：慢回路 pi-mono（只在节点边界经 MCP 发序列、拿回判决）；快回路机器人服务 + openpi + 环境；离线打分和记忆库。
7. 验收与回滚：单候选串行，不做多候选选择（PhysicalRSI、DGM）。门 = Zetta 三级（中心 seed 重跑 → 历史回归 100% → 留出 ΔSR）+ COAP ε = 4 回合 + RRSI 成本感知接受；回滚 = git worktree + lineage 收据。

资源：GPU 1 张 RTX 3080 Laptop 16 GB，仿真和 π0.5 推理共用（放不下就说明需要什么卡）；VLM 走 API，模型待定（见 E）；每轮进化墙钟 __ 小时、费用 __ 美元。未填项写成公式，并按"本机 16 GB + API VLM"和"云上 80 GB + 本地 VLM"各算一遍。

# 要你回答的问题

A. **进化面**。候选：节点序列库、SKILL.md 原语说明、VLM 自写笔记（VISTA 的 GUIDE.md、PhyAgentOS 的 LESSONS.md）、恢复规则、验证器阈值、规划 prompt。按"收益 / 作弊风险 / 评测成本 / 可回滚性"排序，给 v1 最小进化面和文献证据。不可改的（原语和验证器代码、门、打分器、禁读检查）落到写白名单并配越权自测。阈值只许收紧，放宽须由打分进程证明漏检率不升（EmbodiedSWE 把改阈值列为作弊）。

B. **进化回路**。先交八行表：进化面 / 编辑者 / 证据 / 提案 / 门 / 回滚 / 防作弊 / 预算。要定：谁改、批多大；每类证据的来源和可用时段（官方 judge 只在离线打分里给开发期的门用；评测期开记忆时入库只凭验证器 Pass，误判怎样污染记忆）。适应度只用官方分加成本。每级门几任务、几种子、几回合；用 A/A 重跑查 ε = 4 在 50 回合下是否够；门的划分从 seed 0/1 里切，seed 2 和留出任务只在最后看一次。威胁表：手段 → 拦截层 → 自测用的作弊候选。给伪代码和每轮预算（VLM 调用、回合、墙钟）。

C. **里程碑**，同一 π0.5、VLM、预算：M0 = 条件 1，π0.5 单独，每 N 步重查，N ∈ {5, 10, 15, 25}，另测 A/A 噪声、吞吐、VLM 误判率；M1 = 条件 2，VLM 生成节点序列 + 验证器 + 恢复，记忆关（手写序列只用于调试，不进报告）；M2 = 条件 3，记忆回路，加打乱记忆对照；M3 = 条件 4，harness 回路，对照冻结的条件 3，一次只开一个进化面。各写文件、实验、预注册通过标准、预算、卡点。指标：Score/SR、VLM 调用和 token、墙钟、漏检率。

D. **证伪条件**，各给统计量和余量：条件 2 不超过最好的 N 或漏检率上升（则如实写成"带验证器的定时重查器"，并说 M2、M3 还做不做）；条件 3 在留出任务上缩水；进化后 seed 2 不超过冻结 harness 加余量；同回合和 VLM 预算下 M3 不超过 M2；收益全来自缩短重查步长（Long-WAM）；停止规则。

E. **开工前要定的四件事**，各选一个并说对预算的影响：无物体位姿时停止和后置条件怎么写（夹爪 + 停滞、RGB 差分 + 开集检测器、VERITAS 式开局画路径点）；VLM 每次看不看图（每原语都看、选择性取图、只在开局看，或 VISTA 式：默认只给最后一帧，历史帧无损存着，VLM 用 inspect 按需回看和放大）；规划模型和每回合调用上限；评测期记忆冻结还是继续积累。

F. **待核实**，按影响排：
1. ee 和 joint 动作能否在一回合内切换（已知按运行固定）；π0.5 权重输出哪种。不能切换就给替代方案。
2. depth 关闭时 ee_pose 原语的 3D 目标从哪来，给两条路线选一条；规则是否允许开 depth。
3. π0.5 checkpoint 能否获得；分任务成功率。已知 Pi-05 榜单总分 11.44、SR 6.93%；PhysicalRSI 的分任务数（如 build_tower 28.67%）是它整个系统的成绩，不是底座。拿不到权重列为第一阻塞项，不改训练路线；底座在 30%–70% 的任务不足 4 个时给降级规则。
4. 夹爪状态是指令回显还是实测开度。
5. pi-mono、机器人服务、openpi、model.py 的进程边界；规划调用落在哪个 get_action、超时设多少；Python 侧能否经 RPC 或 MCP 同步等结果。
6. 单回合墙钟；seed 和布局的关系，能否自采开发布局。

# 文献范围

Harness VLA 2607.08448、Zetta 2608.16590、PhyAgentOS 2607.16636、CaP-X 2603.22435、VERITAS 2606.18247、ASPIRE 2607.00272、Meta-Harness 2603.28052、HarnessEvolve 2609.00829、RRSI 2609.24972、DGM 2505.22954、EmbodiedSWE 2609.27308、HexaAnything 2609.35432、EmbodiRSI 2609.38905、Spotter 2609.36808、Show-Harness 2609.10522、PyRUA-Lean 2610.01939、PhysEvo 2610.08995、EmbodiedRSI 2610.10498、Long-WAM 2610.10528、COAP 2610.12369、RoboRSI 2610.12424、RoboHarness 2607.18060、ARLI 2608.23831、RoboDojo 2607.04434、XPolicyLab 2608.09892、Astra 报告 2609.24170、π*0.6 / RECAP 2511.14759、VISTA 2610.02200（非具身，只借无损视觉记忆和 inspect / read_pixels 工具的设计，§3.1 与 Fig. 5 消融）。编号先核对标题再引用；EmbodiRSI 和 EmbodiedRSI 是两篇。新工作只补 2026-09 以后直接相关的。

代码：Physical-Intelligence/openpi、RoboDojo-Benchmark/RoboDojo、XPolicyLab/XPolicyLab、PhysicalRSI/PhysicalRSI、zqc3117/Spotter、joshhhhhan/VISTA。

# 输出格式

中文，约 10000 字以内，以表和伪代码为主：1 决策摘要（≤600 字）；2 架构（进程、回路、谁和谁说话）；3 进化回路规格（八行表、伪代码、威胁表）；4 接口规格（节点 schema、判决 JSON、MCP 工具签名、lineage 收据、仓库目录树）；5 里程碑表；6 证伪条件；7 风险表，每条配对策，有文献引节号或表号，没有写"工程对策"；8 不可行的决策、事实核查、未决问题，按影响排序。

引用给 arXiv 号加节号或表号；凭记忆的数字标"约"；找不到写"未查到"，不要编。五五开选一个并说理由。
