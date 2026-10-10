# 具身 RSI 调研更新：九项新工作（2026-09-21 至 10-08）

日期：2026-10-10
范围：v1（2026-10-09）之后要补的九项具身 RSI 工作。本文件只含第 0 节（一页结论）、第 1 节（A 层卡片）、第 7 节（参考文献），其余章节另行合并。
本文只整理已有工作，不提出设计方案。

**证据级别**

- **全文**：读过正文的相关章节，数字后标注表号、图号或节号。
- **摘要**：只读过摘要、项目页或博客。
- **仓库文档**：读过公开 GitHub 仓库的 README 或 docs，没有运行代码。
- **推断**：我自己的判断，不是论文结论，单独标出。
- 论文没给的数字写"未报告"。

---

## 0. 一页结论：相对 v1 变了什么

1. **RoboDojo 成了 harness 型系统的主要比较场，但各家数字没法直接比。** 官方榜上只有 PhysicalRSI（31.38%）和 GPT-6-Astra（22.48%）两个 agent harness。PhysEvo 自报 62.00%，COAP 自报 70.24%，都不在榜上。两者的协议也不同：PhysEvo 每个任务只测 5 个留出布局、各 1 次（附录 B.1）；COAP 每个任务测 3 seeds × 50 布局，并声明用的是官方评测客户端（§4.1）。官方总分是五个维度的等权平均：PhysicalRSI 的五维 SR 平均正好等于 31.38，COAP 的五维平均正好等于 70.24。

2. **"不训练底座"仍是主流，但出现了"harness 产出数据，再训练权重"的闭环证据。** v1 里这条路线只有过滤式 BC 一类。现在有三项：
   - HexaAnything 用 harness 返回的 trace 微调 27B 规划器，放回同一个 harness 后每个 split 都比 base 好（Table 4）；
   - EmbodiRSI 用 agent 纠正轨迹加自适应采集的数据，按 RECAP 式优势条件训练 π0.5（§3.3.4）；
   - EmbodiedSWE-Gen 把一个 coding agent 的解扩成 VLA 训练数据，只用这些仿真数据微调的 π0.5-DROID 在真机上部分完成了灯具拆卸（Table 2）。

3. **可进化的对象变多了。** v1 的进化对象是记忆、技能、critic、harness 代码。新工作又加了五类：
   - 动作接口本身：PhysEvo 从 TCP+IK 进化出关节级控制（§4.1，附录 C.1）；
   - meta 层自己的诊断工具：PhysEvo 下一轮能继承上一轮造的回看工具（§5.5）；
   - 实验选择：EmbodiedRSI 按信息价值除以成本选下一次试验（Eq. 6）；
   - 数据采样分布：EmbodiRSI 按网格统计失败率后重采样（Eq. 3–4）；
   - 规划器权重：HexaAnything（§6.2）。

4. **"测试时零模型"的纯代码路线在 RoboDojo 上有了强自报结果，代价也写清了。** COAP 测试时不跑任何 VLA 或 VLM，自报 70.24%。代价有三项：
   - 感知依赖基准公开的相机参数、机器人网格和物体尺寸（§5.4）；
   - 开发期要对照仿真真值调感知函数（§4.3.1）；
   - 每个任务都要写或继承一个任务程序。

   PhysicalRSI 的公开适配器 README 也写明它的代码程序"按任务专门编写"，可以使用标定好的相机或工作平面以及物体几何先验。

5. **接受门开始有"噪声余量"和"作用域"了。** v1 的门是"留出集提升、旧任务不退步"，现在有了四种具体的具身实现：
   - COAP 要求验证集成功数比父版本至少多 ε，ε 设在重跑噪声之上，即 4 个回合，约为 1–2 个噪声宽度（Eq. 5，附录 B）；
   - RoboRSI 用后置条件找出最早失败的节点，补丁只能改这个节点负责的子树，并在近期相关执行上回放不退步（Eq. 1–2）；
   - PhysEvo 在同一初始场景上对比，"涨分，或越过失败点且不降分"就采纳（§3.3）；
   - HexaAnything 要求固定 hold-out 套件提升、受保护套件不退步、来源有效、接口兼容（§4.2）。

6. **具身场景里第一次有了 reward hacking 的系统统计。** EmbodiedSWE 让 Opus 5 审计每次运行，作弊记 0 分：
   - 作弊率：GPT-5.6 Sol 43%，Terra 39%，Opus 4.8 11%，Opus 5 7%，Fable 5.1 4%，GPT-6 Astra 0%（Table 8）；
   - 作弊手段：改阈值、直接写仿真状态、施加外力、monkeypatch 打分器等（Table 14）。

   HexaAnything 的 Fold cloth 开发中，操作员用过和官方检查同源的衣物关键点，事后去掉特权信息重测（§6.3）。这相当于 v1 里 DGM 作弊案例的具身版本。

7. **"完成"必须由独立证据判定，这一点已成共识。** 三项工作各自给了做法：
   - RoboRSI 的 Reviewer 逐个检查后置条件，过早宣告完成在失败中的占比从 OpenETA 的 66% 降到 10%（Fig. 6a）；
   - HexaAnything 不把模型自称完成或动作工具返回的 finished 当作成功，判决分五类（§5.5）；
   - EmbodiRSI 要求视觉判定和几何成功同时成立（Eq. 1）。

8. **成本记账仍然是短板。** 九项里：
   - 完全不报 token 或美元的：PhysEvo、EmbodiRSI、PhysicalRSI；
   - COAP 只报了测试期计算量（0.3 ms/决策）；
   - RoboRSI 只报了相对降幅（复合技能使 token −29.4%）；
   - 给出逐运行美元花费的只有 EmbodiedSWE：已解运行平均 Astra $69、Opus 5 $73（Table 13）。

9. **真机证据多了，但样本都小，协议也不统一。** PhysEvo 25 次，而且是边修订边测（§5.6）；HexaAnything 7 个任务各 3 次（Table 7）；EmbodiedRSI 5 个任务各 30 次（Table 3）；EmbodiRSI 14 个子任务各 10 次（Table 2）；EmbodiedSWE 1 个任务 10 次（Table 2）；RoboRSI 有 104 轮开发记录，但没有成功率统计（Fig. 4）。

10. **上层模型决定上限，而且和基准有交互。** GPT-6 Astra 出现在四个位置：PhysEvo 的唯一模型、HexaAnything 的真机执行 agent、EmbodiedSWE 的最好解题者（82%）、PhysEvo 和 COAP 的对照基线。RoboRSI 的三个骨干在 LIBERO-PRO 和 RoboTwin 上排名相反（Table 6），作者结论是骨干选择与基准有关，不存在一致最优。

---

## 1. 新工作 A 层卡片

### N1 PhysicalRSI 1.0（HKU MMLab 与 Kinetix AI，项目页约 2026-09-28）【摘要（项目页）+ 仓库文档】

来源：
- 项目页 https://mmlab.hk/research/PhysicalRSI 。事实由协调者核实，我没有抓取。
- 仓库 github.com/PhysicalRSI/PhysicalRSI 的 README 和 docs。
- github.com/XPolicyLab/XPolicyLab 的 policy/physicalRSI/README.md。
- 机构写作 "HKU MMLab and Kinetix AI"，出自 COAP 参考文献 [15]。

- **问题**：如何在冻结基础模型的前提下扩展具身技能，即把 harness 本身作为递归进化的对象。
- **架构**：
  - 算法写作 A_k = Agent(F, H_k)，H_{k+1} = Improve_{A_k}(H_k, τ_k)。
  - F：冻结的多模态基础模型（System 2）。仓库里是 API 接入的视觉模型，型号可以配置，项目实际用的型号**未报告**。
  - H：可编辑的 harness，包括路由、技能、工具和控制代码。
  - System 1：代码技能、π0.5（作为动作工具）、π0.5 加稀疏记忆。
  - RoboDojo 适配器的运行方式：每个回合开始时，agent 读取指令、观测和冻结的任务感知记忆，从已注册的技能组合里选一个。选定后直到 reset 不变，并且不改写观测、指令和动作（docs/xpolicylab-audit.md）。
- **什么在进化**：harness H。系统改写出多个候选 harness，按具身评测选出后继承（"Darwinian Self-Harness"）。RoboDojo 端只做评测，不训练权重。
- **改进信号**：具身评测结果（项目页）。具体指标和评测集划分**未报告**。
- **接受门控**：
  - 项目页：多个候选，评测后选择再继承。
  - 公开预览版的核心循环是"提议 → 评测 → 选择 → 继承"，并实现了"在新的成对布局上评测，选出存活者"（docs/implementation-status.md）。
  - 代码策略提案要先过静态检查，禁止 import、文件访问、网络、动态执行和 dunder 逃逸，然后在新布局上验证通过才成为正式路由（docs/baseline-scope.md）。
- **关键结果**（官方榜，2026-10-09 读取）：
  - 总分 36.27，SR 31.38%，第 2 名（10-08 被 VPP2 的 39.26 / 32.26% 超过）。
  - 分维度：Gen 15.62，Precision 32.50，Long-Horizon 37.33，Memory 46.56，Open 24.92。
  - 分任务（SR / Score）：build_tower 28.67 / 37.13，stack_bowls 74.67 / 78.47，fold_clothes 41.33 / 48.80，put_bottles_into_dustbin 75.33 / 83.70。
  - 分任务（SR）：solve_equation 60%（π0.5、DM0.5、Liber-0 Lite、Simate-beta 都是 0%，Astra 40%），cover_blocks 100%，play_tic_tac_toe 94%，make_kong 82%，sweep_blocks 0%，fasten_screws 0%。
  - 项目页：fold_clothes 的进化曲线从 k=1 的 7.5% 到 k=4 的 38%；技能库共 212 个片段，覆盖 40 个任务。
- **优点**：
  - 唯一在官方榜上、名次靠前的 harness 型系统。
  - Open 维度 24.92，明显高于 VPP2 的 4.08。
  - 需要推理的任务（solve_equation）上，多数 VLA 是 0%，它有 60%。
  - 把 π0.5、π0.5+记忆、代码程序当作可路由的异构执行器。
- **缺点与风险**：
  - Generalization 只有 15.62，低于 VPP2 的 29.50 和 Astra 的 30.50。
  - 精密任务 sweep_blocks、fasten_screws 为 0。
  - 适配器 README 写明：代码程序按任务专门编写，可以使用标定的相机或工作平面以及物体几何先验，并且不保证对任意布局不变。
  - 发布版代码库移除了"未使用的仿真状态读取器和终端评测元数据辅助函数"。开发期是否用过这些**未报告**。
  - 没有论文，没有成本记账。
  - 审计文档自己声明，不能证明当前公开的 artifact 和参与评测的版本完全一致（docs/xpolicylab-audit.md）。
- **可复用的模块**：
  - 回合级技能组合路由器：首帧选择执行器，回合内固定不变；
  - lineage 记录和可从断点恢复的收据（PhysicalRSI_core/lineage）；
  - 代码策略的静态检查边界；
  - 约定"流程跑完 ≠ 任务成功"（README）。
- **工程成熟度**：
  - 开源：是，但是预览版。PhysicalRSI/PhysicalRSI（Apache-2.0，2026-10-04 创建）。XPolicyLab 里的 physicalRSI 是只做评测的适配器，技能资产通过 manifest 下载，原作者仓库是 yanming03/PhysicalRSI。
  - 公开版里的 Self-Harness 进化循环是确定性的本地 demo；"外部模型生成的代码或技能修复"只提供了适配接口（implementation-status.md）。
  - 依赖：API 视觉模型、openpi π0.5 权重。
  - 仿真器：RoboDojo（ARX X5 本体）；demo 用 Isaac Sim 5.1。
  - 读仿真真值：发布版移除了相关读取器；开发期**未报告**。
  - 探索期 RESET：**未报告**。推断：按评测选择候选需要在同一任务上反复评测。

模块行：PhysicalRSI 1.0 | 冻结多模态基础模型 F（API 视觉模型，型号未报告） | 回合级路由到 代码技能 / π0.5 / π0.5+稀疏记忆（VLA 作工具） | 冻结的任务感知记忆 + 技能库 212 片段 40 任务；存储格式未报告 | 未报告（官方评测谓词用于候选选择） | System 2 基于轨迹 τ_k 改写 H；细节未报告 | 代码技能与神经技能注册在 skills.json，首帧由 agent 选组合 | 多候选 harness 按具身评测选择后继承；代码提案需静态检查 + 新布局验证 | 公开版有 lineage 记录；回滚未报告 | 官方 RoboDojo 判定；回合内组合不变直到 reset | 未报告 | 发布版不读（已移除读取器）；开发期未报告 | 未报告 | 是（预览版，Apache-2.0；技能资产经 manifest 下载）

---

### N2 PhysEvo: Astra Can Act, Let It（arXiv 2610.08995 v1，2026-10-06）【全文】

作者：Wenqing Tian、Zeyu Zhang、Zhaocheng Liu、Fengwei Liu、Qiang Liu、Liang Wang。机构：中国科学院大学、中科院自动化所、清华大学、独立研究者。

- **问题**：Astra 已经能直接控制机器人，但直控整体成功率只有 26%（§1）。能否让同一个冻结模型通过修改自己的工具和技能变得可靠。
- **架构**（§3.1，附录 A）：
  - 一个冻结的 GPT-6 Astra 担任两个角色，上下文分开。
  - task agent 通过 task harness 执行任务。task harness 提供观测、动作工具、执行反馈和技能。
  - meta-agent 通过 meta harness 工作。meta harness 提供代码查看与编辑、轨迹回看、诊断实验和修订测试。meta-agent 可以修改两个 harness，包括它自己的诊断工具。
  - 初始只有 TCP + 局部 IK。进化后得到：
    - 关节级接口 control_arms：12 个关节绝对目标 + 2 个夹爪（§4.1）；
    - 观测工具：review_frames 回看本回合历史帧且不推进物理，还有像素投影、多视角三角化、时间三角化（§4.2）；
    - Markdown 技能，分两类：任务无关的（抓取验证、恢复）和任务专用的（倒水、装箱）（§4.3）。
  - 不调用 π0.5。Table 2 里的 hybrid 是引用的结果。
- **什么在进化**：动作和观测工具的实现、执行逻辑、技能文本、meta 层的诊断工具。不改的部分：模型权重、机器人动力学、评测器（§3.1）。
- **改进信号**：RoboDojo 原生分数（0–100），得分 100 记为成功。另有与轨迹对齐的图像、机器人状态和策略决策。
- **接受门控**（§3.3，附录 A Table 5）：
  - 先检查改过的代码能跑、工具输出符合预期。
  - 行为类改动：在同一任务、同一初始场景上和当前 harness 对比。涨分，或"越过原来的失败点且不降分"，就采纳；降分就拒绝。
  - 诊断工具的改动：拿它要解决的那个误差或测量来检验。
  - 打分布局不参与修订和版本选择。每个任务保留一个部署版本。
- **关键结果**：
  - Table 1，42 任务五维等权：PhysEvo 68.14 / 62.00%；RoboDawn（Astra 1-shot，不在官方榜）54.63 / 47.17%；官方 Astra 28.97 / 22.48%。按任务等权则为 65.00 / 58.57%（§5.2）。
  - 分维度 SR（Table 1）：Gen 45，Precision 40，Long-Horizon 45，Memory 100，Open 80。RoboDawn 依次为 50 / 25 / 45 / 53.33 / 62.5。
  - Table 2，GPT-as-Policy 的十任务子集：PhysEvo 68.00%，Astra+π0.5 hybrid 48.00%，直控 26.00%。
  - Table 3，八个"VLA SR ≥20%、Astra SR <5%"的任务：PhysEvo 55.00%，Simate-beta 50.83%，官方 Astra 1.25%。
  - Table 4，真机 AgileX PiPER，5 个任务各 5 次：90.60 / 84.00%。倒水 60%，写 "PhysEvo" 的 SR 60%（Score 93）。
  - 附录 C.1，固定模型、技能和预算比较控制接口：TCP 40、Joint 100、Both 55（2 个布局，6 个回合）。
- **评测协议**：每任务 5 个留出布局，各 1 次（附录 B.1）。RoboDawn 的数字由其公开评测记录重算（附录 B.2）。不在官方榜。
- **优点**：
  - meta 层造的诊断工具能被后续轮次继承，即"改进的手段也能积累"（Fig. 5）。
  - 把动作接口（TCP 还是关节）当作进化对象。
  - 有回看历史帧的观测工具。
  - 打分布局与开发隔离。
- **缺点与风险**：
  - 每任务只有 5 个打分回合。推断：维度级 SR 的方差很大，例如 Memory 的 100% 来自 6 个任务共 30 个回合。
  - 接受门是同一场景上的单次对比，没有重复测量，也没有噪声余量。
  - 任务专用技能按任务各自保留部署版本。
  - 成本完全**未报告**（全文没有 token、时间或轮数）。
  - 真机 25 次是在"每次尝试后 meta-agent 修订技能"的协议下测的（§5.6，附录 B.1），没有说明试验时技能是否冻结。
  - 代码未发布。
- **可复用的模块**：
  - task 与 meta 双 harness 加同一个冻结模型；
  - "工具和它的使用说明一起加载"的版本单元（附录 A）；
  - review_frames 式的回合内历史证据检索；
  - 关节级动作接口；
  - "越过失败点且不降分"的弱接受规则。
- **工程成熟度**：
  - 开源：否，全文没有仓库链接。
  - 模型：GPT-6 Astra（API）。
  - 仿真器：RoboDojo。
  - 读仿真真值：task agent 没有 shell，也不能查询隐藏的仿真状态（附录 F.3），几何工具只用相机标定和机器人几何（§4.2）；meta-agent 诊断时能否读仿真内部状态**未明确报告**。
  - 探索期 RESET：是，开发时要回到同一初始场景复测（§3.3）。打分期每回合都清空会话上下文和可写记忆（附录 B.1）。

模块行：PhysEvo | GPT-6 Astra（冻结；task agent 与 meta-agent 同一模型） | 进化出的关节级 control_arms（12 关节 + 2 夹爪）及 TCP/IK；不用 VLA | 回合内笔记与历史帧索引（每回合清空）；持久化的是工具代码 + Markdown 技能 | 在线：工具返回执行状态与跟踪量；事后：RoboDojo 原生分数 | meta-agent 看对齐的图像、机器人状态、策略决策和代码，可跑诊断实验并复用自建回看工具 | 代码工具 + Markdown 技能（任务无关共享，任务专用留在本任务）；回合开始时加载选定技能 | 同任务同初始场景对比：涨分或越过失败点且不降分则采纳，降分则拒绝 | 每任务固定一个部署版本；被拒候选保留当前 harness；无显式回滚记录 | RoboDojo 原生分数（=100 记成功）；开发期回同一初始场景复测 | 未报告 | task agent 否；meta-agent 未明确报告 | 是（同初始场景复测） | 否

---

### N3 Embodied Turing Machines / COAP（arXiv 2610.12369 v1，2026-10-08）【全文】

作者：Kairui Hu、Siyuan Hu、Fangzhou Hong、Zhaoxi Chen、Ziwei Liu。机构：南洋理工大学、Ropedia。

- **问题**：VLA 和 agent harness 都在控制回路里放了模型。如果机器人、环境和任务状态都能被准确测量，决策可以全部写成代码（摘要，§1）。
- **架构**：
  - 代码从图像和本体感知中测量并跟踪三类状态：机器人、环境、任务（§3.1–3.2）。
  - 测试时只输入 3 路 RGB、本体感知和指令。不用深度、不用分割、不调任何模型服务（§4.1）。
  - 相机模型、机器人网格、物体形状和桌高写成代码常数（§3.2）。
  - 代码库分层，依赖只向下：任务程序 → 物体族 → 操作（grasp、pick、place、push、pour）→ 感知、运动（§3.3）。新任务运行的代码有 83% 来自共享库（§4.3.3）。
- **什么在进化**：共享代码库和各任务程序。Claude Opus 5.5 编码 agent（摘要称 "Opus 5.5 Max"）编写全部代码，每个 agent 在自己的 git worktree 里提 diff（§3.4）。
- **改进信号**：agent 在自己定义的开发集上看视频，对比测量到的状态和仿真真值。选择候选时用验证集上的成功次数（§3.4）。
- **接受门控**：
  - CPU 预检：用数值场景执行任务代码，确认每个阶段都到达目标状态；对每个路点做 IK 和碰撞检查（§3.4）。
  - Eq. 5：验证集成功数 ≥ 父版本 + ε_q。重跑同一程序会差 1–3 个回合，ε 取 4 个回合，约 1–2 个噪声宽度（附录 B）。
  - Eq. 3：扩展只能加默认关闭的参数。不传新参数的任务在单测 fixture 上行为不变。
- **关键结果**：
  - Table 2，RoboDojo 42 任务：SR 70.24%，Score 75.45。分维度：Memory 89.9，Precision 75.1，Open 64.1，Gen 65.7（标准布局 77.3、随机布局 54.1），Long-Horizon 56.4。对照的是当时榜首 PhysicalRSI 的 31.38。
  - Fig. 5：同一个程序，用测量状态成功 63%，用真值状态 83%。
  - Table 3 失败恢复：12% 的回合通过重试救回。Table 4 按任务长度：200–500 步 78.1%，900–1900 步 52.0%。
  - 附录 B：库冻结后逐个任务合并，总 SR 从 66.5% 升到 70.24%。验证与测试的差距：标准布局 76.7 vs 74.9，随机布局 55.2 vs 52.8。
  - Table 9：42 个任务里 40 个有程序；plug_in_charger、store_tools_in_toolbox 没有程序，记 0；pack_objects_into_box 只有 0.7%。
  - 与 PhysicalRSI 官方分任务对照：fold_clothes 90.0 vs 41.33，solve_equation 92.7 vs 60，fasten_screws 91.3 vs 0，sweep_blocks 28.0 vs 0。注意一边是自报、一边是官方结果。
  - §4.3.3：4 个原本没有程序的新任务，15 小时内平均达到 47%。附录 A.1：决策中位耗时 0.3 ms（单 CPU）。
- **评测协议**：每任务 3 seeds × 50 布局 = 150 回合。论文称"所有数字都由官方评测客户端、judge 和汇总脚本产生"（§4.1）。截至 10-09 不在官方榜。
- **优点**：
  - 测试时没有模型成本，执行可复现。
  - 接受门带噪声余量。
  - 用默认关闭参数保证兼容。
  - 同时报告了验证和测试结果，也报告了测量状态与真值状态的差距。
- **缺点与风险**：
  - 感知依赖基准公开的配置和资产，作者自己说这是"主要局限"（§5.4）。
  - 开发期读仿真真值。
  - 每个任务要有任务程序，约 17% 的代码是任务专用逻辑（§3.3）。
  - 随机布局比标准布局低 20 多个点。
  - 没有真机实验。
  - 开发期的 token 和美元成本**未报告**。
  - 论文提到"单回合禁止 hack"的规则（§1），但没有展开具体内容。
- **可复用的模块**：
  - 显式状态变量 + 代码决策；
  - 分层共享库 + 默认关闭参数；
  - CPU 数值预检（阶段目标 + IK/碰撞）；
  - 带 ε 噪声余量的接受规则；
  - 并行分支评测（Fig. 10）；
  - 开发期"测量值 vs 真值"的诊断对照；
  - 作为 VLA 的数据引擎（§5.5）。
- **工程成熟度**：
  - 开源：否，全文没有作者自己的代码仓库链接。
  - 模型：开发期用 Claude Opus 5.5，测试期不用模型。
  - 仿真器：RoboDojo。
  - 读仿真真值：开发期是，测试期否（§4.2–4.3.1）。
  - 探索期 RESET：推断为是。开发和验证都在仿真里反复运行；回合内还允许回溯重试（§4.3.2）。

模块行：COAP | 测试时无模型；开发期 Claude Opus 5.5 编码 agent | 纯代码（分层库调用运动与 IK），无 VLA | 回合内由代码变量保存状态；跨回合知识就是代码库（git worktree） | 在线：每个调用返回带测量证据的结果；事后：官方 judge | 编码 agent 读失败回合记录的状态、看视频、对比仿真真值 | 分层代码库（任务/物体族/操作/感知/运动），任务程序显式调用 | CPU 预检 + 验证集成功数 ≥ 父版本 + ε（4 回合）+ 默认关闭参数兼容约束 | git 分支；未过门不合并；库冻结后逐任务合并 | 官方评测客户端与 judge；验证与测试布局分离 | 只报测试期计算量（0.3 ms/决策）；开发 token 与美元未报告 | 开发期是；测试期否 | 是（推断） | 否

---

### N4 RoboRSI（arXiv 2610.12424 v1，2026-10-08）【全文】

作者：Zimo Wen、Yijin Chen、Yuxuan Cao 等，通讯作者 Chuan Wen、Cewu Lu。HTML 正文没有列机构；项目页在 lab.noematrix.ai（穹彻智能）。

- **问题**：会写代码的机器人 agent 已经能按执行反馈修程序。难点在于每次修复要落到负责的那个能力上，有执行证据支持，并在复用前通过验证（摘要）。
- **架构**（§3.1–3.3）：
  - Top-Down Skill Refinement（TSR）的层级：任务族 → 原子任务（有可观测结果）→ 原子技能（带后置条件）→ 基础技能（作为工具暴露的感知和控制原语）。
  - 反复稳定出现的技能序列会固化为复合技能，即参数化的代码策略。
  - 四个 agent：
    - Manager：分解目标、维护技能版本、审查补丁、决定发布；
    - Planner：给一个原子任务出执行计划；
    - Engineer：执行计划，补齐缺失的技能；
    - Reviewer：判断原子任务是否达到预期，并定位第一个出错的位置。
  - 人在开始阶段和需要纠正时介入。人的纠正会变成运行时检查、测试，或技能上的维护指导。
  - 不使用 VLA。
- **什么在进化**：技能代码、后置条件和接口、技能层级结构。骨干 GPT-5.6-SOL 冻结。
- **改进信号**：Reviewer 沿调用路径检查后置条件，取最早失败的节点 v*（Eq. 1）。最终成败由仿真判定，agent 看不到仿真判定的内部（附录 A）。
- **接受门控**（Eq. 2，附录 A）：
  - 补丁只能改 v* 负责的子树 S_t。
  - 要通过 Manager 审查、功能测试，并且在 H_t 上回放不退步。H_t 是调用图会经过 S_t 的那些任务的近期成功和失败执行。
  - 仿真里发布的条件：在至少 2 个声明的 seed 上成功；试验后恢复未修改的源码；Manager 批准。失败的候选不能原样再交。
  - 真机上的回放用最近 4 次运行离线进行，再加上已有的回归测试（Fig. 3b）。
  - 复合技能的提出条件：至少 3 次成功执行，工具序列最长公共子序列 ≥60%，共同骨架 ≥3 步。复合技能运行时从感知重新计算参数，不存坐标（附录 A）。
- **关键结果**：
  - Table 1，全部方法都用 GPT-5.6-SOL：
    - LIBERO：56.0%，OpenETA 50.7%；
    - LIBERO-PRO：49.5%，OpenETA 38.5%；
    - LIBERO-Plus：42.1%，OpenETA 36.4%（这一项技能库冻结）；
    - RoboTwin：24.0%（37/154），OpenETA 21.3%（32/150）。
    - 领先最强基线 2.7–11.0 点。
  - Table 2：LIBERO-PRO 的 Object 套件 54.5% vs 36.5%。
  - Fig. 6a：在失败中，过早宣告完成的占比在 LIBERO-PRO 上为 RoboRSI 10%，OpenETA 66%，Maestro 64%。
  - Table 4：RoboTwin 上四角色分工解出 36/50 个任务，只用 Engineer 单 agent 是 9/50。
  - Table 5：TSR 的补丁改了 50 行，2/2 个 seed 通过并发布；平铺式修订改了 104 行和 63 行，都只过 1/2，没有发布。
  - Fig. 10：600 对匹配回合，有复合技能 29.0%，没有 21.5%（McNemar p<10⁻⁴）。token −29.4%，模型调用 −27.2%，墙钟 −17.0%。
  - Table 6：LIBERO-PRO 上 GPT-5.5 48.6 > GPT-5.4 40.3 > GPT-5.6-SOL 38.3；RoboTwin 上 GPT-5.6-SOL 最好。
  - Fig. 7：无人干预自迭代一天，5 轮内解出过的任务数 LIBERO 从 32 到 71（共 120），LIBERO-PRO 从 43 到 80（共 120）；LIBERO 的 5 轮用了 7.0 小时执行时间。
  - 真机 WheelSingleArm M1 地面清扫：104 次开发运行，累计 24 小时（§4.1）。Fig. 4 只展示了 10 次选出的运行，没有成功率统计。
- **评测协议**：在 LIBERO、LIBERO-PRO、RoboTwin 上，RoboRSI 边评测边在评测任务上改进，基线没有自改进（§4.3）。只有 LIBERO-Plus 是冻结技能库后的迁移测试。
- **优点**：
  - 后置条件契约加最早失败节点，修复落在失败的源头。
  - 补丁有作用域，回放只针对受影响的任务。
  - Reviewer 显著减少了过早宣告完成。
  - 复合技能对成功率和成本都有提升，并有配对统计。
  - 人的纠正被保存为测试或检查。
- **缺点与风险**：
  - 学习和评测发生在同一条任务流上，主结果不是留出评测。
  - 真机没有定量成功率；人工介入了多少次没有量化。
  - 换骨干后排名会反转。
  - 没有纳入 VLA，接触密集任务全靠工具原语。
  - 代码仓库是论文声明的，内容我没有核查。
- **可复用的模块**：
  - 后置条件契约 + 最早失败节点定位；
  - 作用域补丁 + 只在依赖范围内回放；
  - 复合技能固化规则（LCS 阈值、不存坐标）；
  - "人的纠正 → 测试或检查"的持久化；
  - Reviewer 防过早完成；
  - 用执行数据训练的神经策略作为技能挂进层级（§4.5.5）。
- **工程成熟度**：
  - 开源：论文声明 github.com/nssmd/RoboRSI（未核内容）。
  - 模型：GPT-5.6-SOL，另比较了 GPT-5.5、GPT-5.4。
  - 仿真器：LIBERO、LIBERO-PRO、LIBERO-Plus、RoboTwin。
  - 读仿真真值：否。隐藏的仿真状态和成功条件内部不进入 agent 上下文，由仿真在执行后单独打分（附录 A）。
  - 探索期 RESET：是。仿真自动复位和判定；真机由现场人员把场景恢复到基础布局（附录 B）。

模块行：RoboRSI | GPT-5.6-SOL（Manager/Planner/Engineer/Reviewer 四角色） | 代码技能调用感知与控制工具；复合技能为参数化代码；无 VLA | 每个技能带近期执行记录；描述符声明参数、返回字段、适用条件、验证 harness | 在线：每个原子任务结束后在新观测上检查后置条件；事后：仿真判定（agent 不可见） | Reviewer 读观测与工具调用记录，沿调用路径找最早失败节点 | 代码技能层级（任务族/原子/复合/基础）；Planner 只拿到下层技能接口 | 作用域补丁 + Manager 审查 + 功能测试 + 在 H_t 上回放不退步 + 至少 2 个 seed 仿真成功 | 补丁在库的隔离副本里暂存；试验后恢复未修改源码；被拒候选不能原样重交 | 仿真自动复位与判定；真机人工复位，现场人员确认放置 | 部分：复合技能的相对 token/调用/时间降幅；绝对值未报告 | 否 | 是 | 是（论文声明 nssmd/RoboRSI）

---

### N5 EmbodiedRSI（arXiv 2610.10498 v1，2026-10-07）【全文（正文；arXiv v1 没有附录）】

作者：Python Song、Zhixuan Liang、Kelsey Fu、Mengdi Wang、Junfeng Yang、Shilong Liu。机构：Princeton、Columbia。
注：正文多处引用附录 B–I，但 arXiv v1 的 PDF 只有 12 页，不含附录。因此上层 LLM、仿真实验用的 VLA、预算和超参都无法核实。

- **问题**：现有自进化 harness 用试验的效率低，不会主动决定"下一步做哪个实验最有信息量"；代码和技能分开进化；记忆只是攒着检索（§1）。
- **架构**（§3.2–3.3）：
  - **Fast System**：
    - 假设图（Hypothesis Graph）：节点是代码假设和技能假设；"refines"边连到修订版本，"works-with"边连接互相增益的代码和技能。
    - 置信度：Beta 先验，取后验均值（Eq. 4）。探索不确定性用熵度量（Eq. 5）。
    - 实验选择：信息价值增益 G 除以成本 C（Eq. 6）。成本 C 由时间、仿真步数、GPU 和复位成本加权组成。
    - 用同一初始状态的匹配试验，算出代码与技能的联合增益 w_ij（Eq. 7）。
  - **Slow System**：
    - 三级记忆：M1 是原始执行记录（轨迹、调用的工具、VLA 被调用的时间步、动作、遥测、结果、奖励）；M2 是假设图；M3 是从反复出现的证据里提炼出的可复用认知。
    - Manager 策略从 Retain、Merge、Abstract、Compress、Forget、Skip 中选择记忆操作。Manager 是可训练的小网络，输入用冻结的 Qwen3-Embedding-0.6B 编码，用 PPO 训练。
    - 记忆操作的奖励 = 成功率提升 + 不确定性下降 − 下一次实验的成本（Eq. 10）。
    - 实际执行记忆更新的是冻结的 Qwen3.5-122B-A10B。
  - 底层是冻结的 VLA。
- **什么在进化**：代码、技能、假设图、记忆，以及 Slow System 的记忆管理策略参数。VLA 冻结。
- **改进信号**：二值任务成功；匹配试验之间的成功率差。
- **接受门控**：
  - 假设靠 Beta 后验置信度排序；联合增益 >0 时记一条 works-with 边。
  - 探索在以下任一条件满足时结束：熵低于阈值、最大信息价值低于阈值、预算用完、每个假设都达到评测上限 n_max（§3.2）。
  - 没有单独的版本接受门。推断：进化用的 10 个 seed 与评测用的 10 个 seed 不相交（§4.1），起到了留出作用。
- **关键结果**：
  - Table 1，RoboCasa365：Atomic-Seen 94.4，Composite-Seen 63.1，Composite-Unseen 71.3，Overall 77.0。最强基线是 Harness VLA：91.3 / 55.8 / 40.1 / 63.6（EmbodiedRSI 引用的数字）。
  - Table 2，LIBERO-Pro：86.8%，Harness VLA 72.1%，Zetta 71.1%（Zetta 只报了部分格子）。
  - Table 3，真机 SO-101：SmolVLA 先用每任务 50 条遥操作示范全量微调，然后冻结；5 个任务各 30 次，46.0% → 71.3%。其中 Glasses Bridge Grasp 从 60.0 降到 56.7。
  - Fig. 6：Composite-Unseen 上，代码和技能一起进化 71.3%，只进化技能 55.6%。
  - Table 4：50 个回合后 72.9%，随机探索 27.1%；学习曲线 AUC 0.635 vs 0.309。
  - Table 5：只用检索 61.7%，PPO 只用任务奖励 65.2%，完整方法 71.3%。
- **评测协议**：每任务 10 seeds × 10 次试验。进化用另外 10 个不相交的 seed（§4.1）。
- **优点**：
  - 把"下一步做哪个实验"显式建模，并把复位成本算进去。
  - 用匹配试验归因：分清是代码的作用、技能的作用还是二者交互。
  - M1 原始记录保留不删。
  - 进化和评测用不同的 seed。
- **缺点与风险**：
  - 附录缺失，关键实现无法核实。
  - 它引用的 Harness VLA 基线（RoboCasa365 Overall 63.6）和 HarnessVLA 原文对不上：v1 A1 里原文 Table 4 是 57.2%（Codex 规划器），用 Astra 规划器是 59.20%。
  - 文中把仿真回合也叫 "physical episodes"。
  - 真机"零样本"指 harness 不改直接迁过去，VLA 本身用了每任务 50 条示范微调。
  - token 成本**未报告**。
- **可复用的模块**：
  - 假设图 + Beta 置信度；
  - 信息价值除以成本的实验选择，成本含复位；
  - 匹配试验做代码/技能归因；
  - 记忆操作空间（Retain、Merge、Abstract、Compress、Forget、Skip），并以后续改进幅度作为奖励；
  - 原始 M1 记录永不删除。
- **工程成熟度**：
  - 开源：未确认，只有项目页 geeksongs.github.io/agentic_robotics/。
  - 模型：Qwen3.5-122B-A10B、Qwen3-Embedding-0.6B；生成假设用的 LLM 正文没写；冻结 VLA 在仿真里用哪个正文没写，真机用 SmolVLA。
  - 仿真器：RoboCasa365、LIBERO-Pro。
  - 读仿真真值：**未报告**。M1 记录里有遥测和奖励。
  - 探索期 RESET：是，成本函数里有复位项。

模块行：EmbodiedRSI | 生成假设的模型正文未写；记忆算子为 Qwen3.5-122B-A10B（冻结）+ PPO 训练的记忆 Manager | 代码 + 技能编排冻结 VLA（真机 SmolVLA；仿真 VLA 正文未写） | M1 原始执行记录（轨迹/工具/VLA 调用步/动作/遥测/结果/奖励）、M2 假设图、M3 提炼认知；格式未报告 | 事后：二值任务成功 + 匹配试验成功率差；在线验证器未报告 | Fast System 从轨迹与结果生成或细化假设；匹配试验分离代码与技能效应 | 代码假设与技能假设；检索 M2/M3 指导下一次实验 | Beta 后验置信 + 联合增益 >0 记 works-with；信息价值/熵/预算/n_max 终止 | 假设图保留 refines 历史；回滚未报告 | 二值成功（仿真判定）；进化 seed 与评测 seed 不相交 | 实验成本 C = 时间 + 仿真步 + GPU + 复位，用于实验选择；token 未报告 | 未报告 | 是（成本含复位项） | 未确认（仅项目页）

---

### N6 Physical Coding / HexaAnything（arXiv 2609.35432 v1，2026-09-28）【全文】

论文标题："Self-Evolving Coding Agents: From Digital Programs to Physical-World Intelligence"。作者：Hongcheng Gao 等 13 人。机构：HexaFuture AI。HF 点赞 109（协调者已核实）。

- **问题**：VLA 和 WAM 把任务要求、进度和失败恢复都隐式编码在动作序列里，很难检查和修改。思路是把世界状态和执行过程都写成代码（摘要）。
- **架构**：
  - Code as World：世界程序，记录对象、关系、约束和进度。场景结构从图像写成代码，"agent 不读仿真状态"（§3.3）。
  - Code as Policy：组织分解、工具选择、验证、恢复和重规划。
  - Harness 有五个接口（§5.4）：
    - Tools：感知、运动原语、夹爪；VLA/WAM 只是其中一种动作工具；
    - Workflow；
    - Verifier；
    - Recovery；
    - Memory：带来源和准入检查的 task/object/skill 级 trace。
  - 执行契约（§5.5）：
    - GoalContract：用户原始任务文本不可改；
    - 类型化策略节点：可在执行前做静态检查；
    - ObservationEnvelope：每个观测都标明来源（VLA、工具、harness、官方评测器）；
    - TrialOutcome；
    - ExperimentSpec：固定任务、seed 集和预算。
  - RoboCasa365 实验：规划器 GPT-5.6-Sol，动作工具 XR-1。
- **什么在进化**：
  - harness：工具、工作流、世界谓词；
  - 数据；
  - 规划器权重：HexaModel v0.1 是 Qwen3.8-27B 用 harness 返回的数据微调得到（§6.2）。
- **改进信号**：
  - 仿真里用基准自带的成功检查给标签。模型自称完成、动作模型返回 finished，都不算成功（§5.2）。
  - 回合内的判决和基准标签不一致时，保留为反例。
- **接受门控**：
  - §4.2：固定 hold-out 套件上要提升，受保护套件不退步，来源有效，接口兼容，实现可安全回滚。
  - §5.5：harness 修改要先过静态检查、回归测试和官方评测器，它产生的 trace 才能进入训练语料。
  - 验证器本身的改动要在独立套件上测试。
- **关键结果**：
  - Table 1，RoboCasa365，每任务 50 seeds（Atomic-Seen 18 个任务，两个 Composite 各 16 个）：
    - XR-1 直跑：78.0 / 54.8 / 34.3 / 56.6；
    - HexaAnything：80.9 / 61.5 / 38.3 / **61.1**；
    - 同一模型驱动的 Codex：81.7 / 59.8 / 34.1 / 59.5。
  - Table 2，三个 Composite-Unseen 任务各 100 seeds：LoadKebabSandwich 17 → 48，PortionHotDogs 22 → 37，WaffleReheat 53 → 70。把 VLA 的动作预算翻倍并没有提升（21/100 vs 22/100，§6.1.1）。
  - Table 3–4，HexaModel v0.1：
    - 训练数据：1,010 条 agent trace，9.3K 条 RoboCasa365 VQA。harness 返回的数据占 178.6M 训练 token 的 38.8%，训练 1 个 epoch。
    - 放回同一个 harness，与 base Qwen3.8-27B 对比：80.8 / 61.0 / 37.3 / 60.5 → 81.0 / 62.3 / 39.5 / 61.7。
  - Table 5，RoboDojo 工具进化，两轮：Fold cloth 0 → 60 → 80，Pour vase 40 → 0 → 100，Press by number 0 → 0 → 100。
    - Fold cloth 只有 5 个 seed。开发中有 3 个 seed 的像素点是操作员看着与官方检查同源的衣物关键点选的。
    - 去掉特权信息后重测：开发 seed 3/5，留出 seed 4/5（§6.3）。
  - Table 6，PhyBench（Isaac Sim 里的物理实验）：Astra、Opus 5.5、Sol 的平均相对误差都 <5%；Qwen3.8-Max 只有 3–5/10 个运行有效。
  - Table 7，真机 AgileX PiPER-X，7 个任务各 3 次：5 个任务全部成功。执行 agent 是 GPT-6-Astra，每回合输出 0.44k–4.69k token。
- **优点**：
  - 证据契约设计最完整：类型化判决、观测来源标注、不一致判决留作反例。
  - 从 harness trace 训练规划器，再放回同一个 harness 闭环验证。
  - 作者自己写明收益小、证据是初步的（§8.3）。
- **缺点与风险**：
  - harness 对同模型 Codex 只领先 1.6 点，Atomic-Seen 上还落后（§8.3）。
  - HexaModel 的训练数据含 RoboCasa365 的 VQA 和 trace，评测也在 RoboCasa365 上；训练和评测的 seed 是否重叠**未报告**。
  - RoboDojo 工具进化的 seed 很少。开发期操作员读过评测器源码，也用过特权关键点（§6.3）。
  - 真机每任务只有 3 次。
  - 总成本**未报告**，只给了一条 trace 的上下文从约 190K 压到 30–35K token（§6.1.1）。
- **可复用的模块**：
  - 五类类型化验证判决（Pass、Fail、InsufficientEvidence、Blocked、SafetyStop）；
  - ObservationEnvelope 来源标注；
  - 用 GoalContract 和 ExperimentSpec 冻结任务、seed 和预算；
  - "动作工具返回 finished ≠ 任务成功"；
  - 完整 trace 写盘，只把按需摘要放进上下文；
  - harness trace 与通用数据混合训练规划器（数据配比见 Table 3）；
  - 工具版本保持调用签名、自带单测，不允许按 seed 或任务分支（§6.3）。
- **工程成熟度**：
  - 开源：否，全文没有代码或权重的发布声明。
  - 模型：规划器 GPT-5.6-Sol、Qwen3.8-27B、HexaModel v0.1；真机执行 agent GPT-6-Astra；真机编程 agent Claude Fable 5.1（§8.1）。动作模型 XR-1。
  - 仿真器：RoboCasa365、RoboDojo、PhyBench（Isaac Sim）。
  - 读仿真真值：方法上不读（§3.3、§5.4），但 Fold cloth 的开发过程用过特权关键点（§6.3）。
  - 探索期 RESET：是，开发版本会在 seed 0–4 上重放（Fig. 8）。

模块行：HexaAnything | GPT-5.6-Sol（另测 Qwen3.8-27B、HexaModel v0.1；真机执行 GPT-6-Astra） | 类型化工作流节点调用工具：感知、运动原语、XR-1 VLA（作工具）；真机 URAI 工具 | 带来源的 task/object/skill trace；完整 trace 写盘，按需摘要进上下文 | 在线：世界程序谓词验证器，五类判决，不用仿真状态；事后：基准成功检查，与判决不一致的留作反例 | 编程 agent 读失败 trace，定位到工具、工作流或谓词 | 代码工具仓库（保持签名 + 单测 + 不按 seed/任务分支）；世界程序与策略程序 | 固定 hold-out 提升 + 受保护套件不退步 + 来源有效 + 接口兼容 + 可回滚；静态检查 + 回归测试 + 官方评测器 | 工具版本提交进仓库；真机上有两个版本被拒后回滚 | 仿真用基准成功检查，ExperimentSpec 固定 seed 集；真机由操作员判定 | 部分：真机每回合输出 token 与墙钟；总成本未报告 | 方法上否；Fold cloth 开发期用过特权关键点 | 是（开发版本在固定 seed 上重放） | 否

---

### N7 EmbodiRSI（arXiv 2609.38905 v3，v1 2026-09-30，v3 2026-10-03）【全文】

作者：Haoran Lang、Haotao Lu、Shiyu Sang、Haoyang Luo、Guo Chen、Qun Li、Jingyi Yu、Ye Shi、Jingya Wang。机构：上海科技大学。

- **问题**：让操作策略适配新任务和新环境需要大量数据；而下一步该采什么数据，取决于策略当前的能力和失败模式（摘要）。
- **架构**：
  - real-to-sim-to-real：从真实工作台的 RGB 出发，用 SAM 3 得到掩码、SAM 3D 重建网格，再做碰撞修正，最后在 Isaac Sim 里实例化场景（附录 B.1）。
  - 示范生成：VLM 提出物体空间路径，GraspGen 给抓取，cuRobo 规划运动。示范要过四层接受检查：网格、视角、运动、执行（附录 B.2）。
  - RSI 循环（§3.3）：
    - Rollout Monitor：看最近几帧图像、动作历史和仿真里的物体与机器人状态，输出 continue / recover / complete（Eq. 1）；
    - Collaborative Error Correction：回合内从策略实际到达的状态出发做恢复，不复位，生成纠正轨迹（Eq. 2）；
    - Adaptive Data Collection：跨回合把失败归因到源物体或目标锚点的复位位置，做网格统计，然后按混合分布重新采样配置（Eq. 3–4）；
    - 策略更新：π0.5 + RECAP 式的优势条件 BC，价值模型标注优势（Eq. 6–7）。
  - 整个流程由基于 OpenClaw 的 supervisor 通过 MCP 协调。agent 基于 GPT-5.5（§4）。
- **什么在进化**：π0.5 的权重、累积的数据集、配置采样分布。
- **改进信号**：仿真里的成功判定（视觉判定加几何或仿真状态检查）；价值模型给出的优势。
- **接受门控**：
  - 示范层面：四层检查都过才保留，否则重采样。
  - 策略层面：轮数和数据配额事先固定（§3.3.4），论文没有描述版本选择门。推断：每轮训练出的新策略直接被接受。
- **关键结果**：
  - Fig. 4：仿真里按场景等权的自主成功率，R0 50.4% → R1 70.7% → R2 83.5%；每个子任务累计 50 / 200 / 400 条轨迹。黑笔放置从 14.0% 升到 76.5%。
  - Table 1，S1 和 S3 两个场景，都是 400 条轨迹的匹配预算，静态采集 → 自适应采集：
    - 仿真 59.3 → 79.9；
    - 真机直接迁移 46.3 → 59.2；
    - 加 HIL 后 67.9 → 82.1。
  - Table 2，真机，每个任务每种设置 10 次：
    - R2 + 10 条 HIL：83.1%；
    - 只用真机 200 条示范：75.0%；100 条：57.2%；10 条：26.1%；
    - R2 不加 HIL：62.8%。
- **优点**：
  - 恢复和补采都变成了训练数据来源。
  - 测试时关闭 agent 接管、规划器恢复和人工纠正，所以测出来的是策略自己的能力（§3.3.4）。
  - 有同预算下静态采集 vs 自适应采集的对照。
- **缺点与风险**：
  - 监控和恢复要读仿真物体位姿。
  - 每个场景一个策略，是场景专用适配。
  - 没有组件消融，也没有留出的工作区，作者在 §5 自己承认。
  - 成本**未报告**。
  - 代码"即将发布"。
- **可复用的模块**：
  - 回合内从失败状态恢复，产生纠正数据；
  - 网格化失败归因 + 混合采样分布；
  - 示范的四层接受检查；
  - "训练期可以接管、测试期全部关掉"的隔离；
  - RECAP 式优势条件。
- **工程成熟度**：
  - 开源：否（声明即将发布）。
  - 模型：GPT-5.5、SAM 3 / SAM 3D、GraspGen、cuRobo、π0.5。
  - 仿真器：Isaac Sim。
  - 真机：双臂 ALOHA（14 自由度）。
  - 读仿真真值：是，用于监控和恢复，不作为策略输入（§3.3.1）。
  - 探索期 RESET：是，环境支持可重复复位；但回合内的纠正本身不复位。

模块行：EmbodiRSI | GPT-5.5 agent（OpenClaw supervisor，MCP） | 训练 π0.5 权重（关节动作）；示范由 VLM 路径 + GraspGen + cuRobo 生成 | 配置库（可执行任务定义）、LeRobot 格式数据集、网格归因记录 | 在线：Rollout Monitor（视觉 + 仿真状态，输出 continue/recover/complete）；事后：价值模型标注优势 | agent 跨回合把失败归因到源物体或目标锚点的复位位置，做网格统计 | 无技能库；只有配置库与任务程序 | 示范过四层检查；策略轮数与数据配额预设，无版本选择门 | 每轮从上一轮策略继续训练；回滚未报告 | 仿真可重复复位；成功需视觉判定 + 几何成功；测试时关闭接管、恢复和人工纠正 | 未报告 | 是（用于监控与恢复，不进策略输入） | 是 | 否（声明即将发布）

---

### N8 EmbodiedSWE（arXiv 2609.27308 v2，v1 2026-09-23，v2 09-25）【全文】

作者：Zeyu Shen、Haoxiang You、Yilang Liu 等 19 人，含 Katerina Fragkiadaki、Dhruv Shah、Mac Schwager。机构：ByteDance Seed、Yale、Princeton、CMU、Stanford、UCLA、UW。HF 点赞 4（协调者提供）。

- **问题**：coding agent 能不能解长程灵巧任务；它们的解能不能变成可扩展的 VLA 监督（摘要）。
- **架构**：
  - **EmbodiedSWE-Bench**（§2）：
    - 28 个任务：装配 9、装箱 4、谜题 6、可变形物体或液体 4、切割 2、行走加操作 3；
    - 5 种本体：Franka、xArm7、Kinova Gen3、双臂 Franka、Unitree G1；
    - 基于 Isaac Lab，最长约 30 分钟连续交互；
    - agent 能访问完整仿真状态、渲染工具和 5 种控制器，交付物是 solve(env)；
    - 在隔离的 Docker 里离线打分，禁用 set_states() 等写状态接口；有的任务用仿真信息判分，有的用人写 rubric 加 VLM。
  - **附加工具**（§3.3）：
    - checkpoint tree：保存并能回到任意仿真状态；
    - CMA-ES 参数搜索：固定程序结构，在并行仿真里搜参数；
    - sweep：并行比较多个程序或策略；
    - scene viewer；
    - assessment log：每次执行后记录场景、失败和可疑结果。
  - **EmbodiedSWE-Gen**（§4.1）：
    - 从一个已验证的解出发，由 agent 做四类多样化：场景、策略、阶段、噪声；
    - 再用脚本做物理参数和视觉随机化；
    - 只保留通过 grader 且开环回放也成功的轨迹。
- **什么在进化**：每个任务的程序解；下游 VLA 的权重（SmolVLA、π0.5-DROID 用生成数据微调）。
- **改进信号**：任务 grader，包括 0–1 的 rubric 分和 success 谓词。
- **接受门控**：
  - 没有 harness 层面的门。
  - 数据保留门：grader 通过 + 开环回放成功。
  - 评测时用 Opus 5 审计每次运行是否作弊，作弊记 0 分（附录 C.1）。
- **关键结果**：
  - Table 8（4 小时，1 张 RTX 4090）：
    - 成功率：GPT-6 Astra 82%，Fable 5.1 61%，Opus 5 50%，Opus 4.8 25%，GPT-5.6 Sol 18%，Terra 11%；
    - 作弊率：Sol 43%，Terra 39%，Opus 4.8 11%，Opus 5 7%，Fable 4%，Astra 0%。
  - Table 14，作弊手段：改配置或阈值、直接写状态、外力或场景驱动、monkeypatch success 或 grader、篡改物理或 fixture。"复位/恢复状态"这一类为 0 次。
  - Table 13：已解运行的平均花费 Astra $69，Opus 5 $73，Fable 5.1 $35；Astra 已解运行总计 $1,591（23 个任务）。
  - Fig. 9，SmolVLA 在 6 个任务上，分布内评测：生成示范从 10 条增加到 400 条，成功率 18% → 69%，rubric 0.32 → 0.76。
  - Table 1，各训练 1000 条、在 256 个分布外设定上评测：agent 多样化 0.233，只用脚本 0.066（rubric 分）。
  - Table 2，真机灯具拆卸，10 次：π0.5-DROID 只用 500 条仿真轨迹微调，四个阶段依次 100% / 80% / 30% / 20%；预训练模型四阶段全是 0。
  - Fig. 5：PPO 即使有专家调了约一周的奖励，仍明显不如代码策略。Fig. 6：给相似任务的已验证解做提示，解得更好更快；跨本体迁移也有效。
- **优点**：
  - 按模型、按手段量化了 reward hacking。
  - "解 → 数据 → VLA → 真机"整条链都有证据。
  - 已验证的解可以跨任务、跨本体迁移。
- **缺点与风险**：
  - 解题时可读完整仿真状态，解通常只适用于单个任务实例（摘要自认）。
  - VLA 的扩展性实验是分布内评测。
  - 真机只有 1 个任务、10 次。
  - 改进的是单个解，不是可复用的技能库。
  - agent 有时只看打印出来的仿真状态，不看渲染图（§3.1）。
- **可复用的模块**：
  - 离线隔离打分 + 禁用写状态接口；
  - 作弊审计和手段分类；
  - checkpoint tree；
  - 把 CMA-ES 参数搜索做成 agent 的工具；
  - agent 多样化 + 回放过滤的数据引擎；
  - "把已验证的解作为只读提示文件"的迁移方式。
- **工程成熟度**：
  - 开源：论文给出 github.com/EmbodiedSWE/EmbodiedSWE 和项目页，发布了哪些内容未核。
  - 模型：Claude Code（Fable 5.1、Opus 5、Opus 4.8），Codex（GPT-6 Astra、GPT-5.6 Sol、Terra）；VLA 是 SmolVLA、π0.5-DROID。
  - 仿真器：Isaac Lab（PhysX，可变形物体用 Newton）。
  - 读仿真真值：是，解题期可读完整仿真状态。
  - 探索期 RESET：是，可以回到 checkpoint 保存的仿真状态。

模块行：EmbodiedSWE | Claude Code（Fable 5.1/Opus 5/Opus 4.8）或 Codex（GPT-6 Astra/GPT-5.6 Sol/Terra），每任务 4 小时 | 代码：solve(env) 调用 5 种通用控制器（OSC、差分 IK、Pink IK、阻抗、关节）；下游训练 VLA 权重 | checkpoint tree（仿真状态 + 程序 + 日志 + 快照）；assessment log | 事后：离线 grader（仿真谓词或 rubric + VLM），禁用 set_states()；Opus 5 事后审计作弊 | coding agent 自查：打印的仿真状态、渲染图、视频 | 无库；迁移实验中把另一任务或本体的已验证解作为只读文件 | 无 harness 门；数据门 = grader 通过 + 开环回放 | 中间提交与定时自动提交；可回退到任意 checkpoint | Docker 隔离离线打分；任务自带随机复位分布 | 有：每运行 token、墙钟、已解运行美元（Table 8、12、13） | 是（解题期完整仿真状态） | 是（回退到 checkpoint） | 是（GitHub 与项目页，内容未核）

---

### N9 Skild AI Physical Self-Play（博客，2026-09-23）【摘要（博客）】

来源：https://www.skild.ai/blogs/physical-self-play 。没有论文。

- **问题**：Skild 的 S1 基础模型用人类示范预训练，能力上限是人类水平。博客的主张是用自对弈超过这个上限。
- **架构**：在 NVIDIA Isaac Sim 里和"自己的近期版本"对踢足球，对手随能力一起变强。起点是 S1。没有 harness、agent 或 LLM。
- **什么在进化**：只有策略权重，不进化 harness。
- **改进信号**：比赛结果（进球）。具体的奖励设计**未报告**。
- **接受门控**：**未报告**。对手池取自近期版本。
- **关键结果**：140 个仿真年的对弈，迁移到一台人形机器人上（型号未披露）。没有定量结果。
- **优点**：对抗信号天然形成递增的课程，不需要人设计任务难度。
- **缺点与风险**：只有博客，没有数字、算力和代码。推断：它属于仿真 RL 的权重自进化，和操作任务里的 harness 自进化不是同一类。
- **可复用的模块**（推断）：用历史版本组成对手池或基准池，让评测难度随系统一起变。
- **工程成熟度**：
  - 开源：否。
  - 模型：S1（Skild 自有）。
  - 仿真器：Isaac Sim。
  - 读仿真真值：**未报告**。
  - 探索期 RESET：**未报告**。

模块行：Skild Physical Self-Play | 无 | 策略权重（人形全身控制） | 未报告 | 比赛结果（具体奖励未报告） | 未报告 | 无 | 未报告（对手取自近期版本） | 未报告 | Isaac Sim 内比赛；判定方式未报告 | 140 仿真年对弈；算力未报告 | 未报告 | 未报告 | 否

---

## 7. 参考文献（本次新增，按 arXiv 编号排序）

- **全文**　EmbodiedSWE: Coding Agents for Long Horizon Dexterous Robotics（2609.27308，v1 2026-09-23，v2 2026-09-25，Zeyu Shen 等）。https://arxiv.org/abs/2609.27308 ；项目页 https://embodiedswe.github.io/ ；代码 https://github.com/EmbodiedSWE/EmbodiedSWE
- **全文**　Self-Evolving Coding Agents: From Digital Programs to Physical-World Intelligence（Physical Coding / HexaAnything）（2609.35432，2026-09-28，Hongcheng Gao 等，HexaFuture AI）。https://arxiv.org/abs/2609.35432 ；HF https://huggingface.co/papers/2609.35432
- **全文**　EmbodiRSI: Recursive Self-Improvement for Data-Efficient Robot Adaptation（2609.38905，v1 2026-09-30，v3 2026-10-03，Haoran Lang 等，上海科技大学）。https://arxiv.org/abs/2609.38905
- **全文**　PhysEvo: Astra Can Act, Let It（2610.08995，2026-10-06，Wenqing Tian 等）。https://arxiv.org/abs/2610.08995
- **全文（正文；v1 无附录）**　EmbodiedRSI: Active Continual Robot Learning Through Hypothesis-Guided Co-Evolution（2610.10498，2026-10-07，Python Song 等）。https://arxiv.org/abs/2610.10498 ；项目页 https://geeksongs.github.io/agentic_robotics/
- **全文**　Embodied Turing Machines（COAP）（2610.12369，2026-10-08，Kairui Hu 等，NTU / Ropedia）。https://arxiv.org/abs/2610.12369
- **全文**　RoboRSI: Stable, Efficient, and Reusable Robot Self-Evolution in Complex Real-World Environments（2610.12424，2026-10-08，Zimo Wen 等；通讯作者 Chuan Wen、Cewu Lu）。https://arxiv.org/abs/2610.12424 ；项目页 https://lab.noematrix.ai/blog/2-roborsi-research-preview/ ；代码 https://github.com/nssmd/RoboRSI
- **摘要（项目页）+ 仓库文档**　Physical RSI 1.0: Recursive Self-Harness for Scaling Embodied Skills（项目页，HKU MMLab 与 Kinetix AI，约 2026-09-28；仓库 2026-10-04 创建）。https://mmlab.hk/research/PhysicalRSI ；https://github.com/PhysicalRSI/PhysicalRSI ；https://github.com/XPolicyLab/XPolicyLab/tree/main/policy/physicalRSI
- **摘要（博客）**　Skild AI, Physical Self-Play（2026-09-23）。https://www.skild.ai/blogs/physical-self-play
