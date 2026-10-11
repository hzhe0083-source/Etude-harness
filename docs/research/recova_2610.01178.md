# Recova: Agent-Guided Failure Recovery for Autonomous Robotic Manipulation（arXiv 2610.01178v1）

提交 2026-10-01。来源：arxiv.org/html/2610.01178，2026-10-11 读。页面约 9.2 万字符，偏移 0 一次读完，正文、附录 A–E 和参考文献都在。三位读者（method / appendix / experiments）各写一份声明，核查员逐条对原文；本卡只用核查通过的内容，被驳回的两条已按更正改写（见文末"核查记录"）。Table 1、Table 3、Fig. 4、技能计数、ref [41]、LIBERO-Pro 协议这几处由写卡人再对一次原文。

## 一句话

把失败恢复从任务策略里拆出来，作为一个可扩展的技能集。真机上由一个 VLM 监控器（四种查询）判断是否完成、是否需要干预，并给出恢复指令 z；z 已注册时，学到的恢复策略做一次有界的自主恢复，再由复原查询验收，否则由人演示。成功、演示和恢复片段分进两个数据集，按 DAgger 轮次微调 π0.5 任务策略和恢复策略。仿真上用代码程序做恢复：LIBERO-Pro 平均 78.8%，最强基线 71.7%（Tab. 2）；MolmoSpaces 平均 64.9%，最强基线 38.0%（Tab. 1）。真机四任务从 23.8% 到 77.5%（DAgger），再到 87.5%（+恢复技能）（Tab. 3）。**要训练权重**，与我们冻结 π0.5 的路线在这一点上不同。

## 问题设定（§1，§3.1）

- 失败后场景常处于任务策略接不下去的状态，单纯重试任务策略往往解决不了底下的障碍（§1 第 2 段；Abstract）。
- 恢复流程分五步：诊断 → 选择或开发纠正动作 → 验证 → 保留成功方案 → 用数据改进对应策略（§1 第 4 段）。
- 形式化（§3.1）：任务策略 π_θ(a_t|o_t,g) 一直执行到满足完成条件 C_g。恢复技能由自然语言指令 z 指定，执行者可以是恢复策略 ρ_φ(a_t|o_t,z)，也可以是 code-as-policy 程序（组合场景查询和运动原语）。𝒦 是某任务已知的恢复指令集，𝒦_ρ ⊆ 𝒦 是已训练技能的注册表（App. A）。恢复以 z 而不是 g 为条件，因此技能与任务解耦，可以跨任务复用。
- 三种角色，一个技能可兼具几种（§3.1，Fig. 3）：**Restorative**，让场景回到可工作状态（扶正倒下的物体）；**Enabling**，为下一步做准备（把物体挪到够得着的地方）；**Adaptive**，换一种做法而不是重复失败的动作（换抓取位姿、从另一方向开烤箱）。

## 机制

### 监控器四查询（§3.3；App. A；App. B Fig. 6；§4.2）

所有查询共用一个系统提示，temperature 0，只返回 JSON（App. B）。每张图是四路相机同一时刻拼成的 448×336 合成图：上排 top、front（提示词里叫 bottom），下排两个腕部相机（App. B）。模型是 Gemini 3.8 Flash，能看到全部四个视角（§4.2 Setup；附录未写型号）。

| 查询 | 输入 | 输出 | 判据要点 | 调用时机 |
|---|---|---|---|---|
| M_req 完成要求 | g + 四个初始视图 | `{requirement}` | 1–3 句，只描述最终场景，只能用相机图像客观核查，**包括哪些东西不能变** | C_g 为空时调用一次，机器人静止；跨采集会话缓存（Alg. 1 第 11–12 行；App. C.2） |
| M_done 完成 | g、C_g、初始与当前合成图 | `{complete, reason}` | 要严格：**部分进展或视角模糊一律 false**；被移动过的物体逐个检查，离开原位又不在合法目标位置的，判未完成 | 每 N_c=600 步（20 s），机器人静止 |
| M_int 干预 | 初始合成图、H_t（最近两张快照，相隔 1 s）、V_t（本轮最近 3 次判决）、𝒦 | `{intervene, reason, scene_intact, recovery}` | 只在快照清楚显示以下五种情况之一时才 intervene：物体掉落/倒下/被碰翻；同一个原子动作卡住约 30 s 以上；多余物体被误移且散放；手牌倾斜或未朝前（麻将任务专用）；场景被扰乱到无法继续。有意的中间摆放不算失败。场景没被扰动时 scene_intact=true、recovery 留空；否则给一条祈使句，**与已知技能匹配时逐字复用** | 每 N_i=300 步（10 s），**异步**执行，任务策略不停（App. A） |
| M_res 场景复原 | z + 初始/当前合成图 | `{restored, reason}` | 以初始图像定义合法的任务前状态；当前布局与之一致或属于等价可工作的起始状态即判复原，不要求物体精确回到原位姿 | 恢复过程中每 N_i 步检查一次，到 T_rec 再查一次，机器人静止（Alg. 1 第 41–42 行） |

中位延迟（四工位采集会话）：干预 4.5 s，完成 5.0 s，复原 5.5 s；M_req 的延迟未查到（App. B）。论文没有报告监控器的准确率或误判率（未查到）。

### Algorithm 1 门控（App. A "Real-Robot Orchestration"）

默认参数在 Alg. 1 第 1 行：N_c=600，N_i=300，T_max=5400（3 min），T_rec=1500（50 s），c_max=2，均为 30 Hz 控制步（App. A；App. C.1–C.2）。

| 规则 | 内容 | 出处 |
|---|---|---|
| 连续干预熔断 | 同一任务连续干预 c ≥ c_max=2 次时，下一次尝试直接是完整人类演示（存入 ℰ_k，c←0），"avoids cycles of repeated failure"。c 只在任务成功（第 24 行）或任务演示（第 7、28 行）后清零；Recover 路径不改 c（第 29–30、36–50 行），所以自主恢复成功不清零 c。最后这一点是从算法推出来的，原文没有直接写 | Alg. 1 第 3、6–8、24–30 行；§3.3 Decide；App. C.2 |
| 完成优先 | 第 23 行 `if d = 1 then ⊳ completion takes precedence`：τ 存入 𝒮_k，c←0。否则若干预判决 (intact, z) 已到达，则 c+1、丢弃 τ、回 home（第 25–26 行）。干预判决到达时完成查询还没返回的，机器人停住，等完成判决出来再处理 | Alg. 1 第 23–26 行；App. A 正文 |
| intact 分支 | intact（停滞，场景没被扰动）：人从当前状态完成 g，演示存入 ℰ_k，c←0。不 intact：调用 Recover(z)，下一次尝试再交给 π_θ | Alg. 1 第 27–30 行 |
| Recover(z) | **仅当 z ∈ 𝒦_ρ** 时做一次有界自主尝试：ρ_φ 最多跑 T_rec 步，每 N_i 步或到 T_rec 时停住机器人调 M_res；通过则 τ′ 存入 𝒜_k 并返回。没按时复原，或 z ∉ 𝒦_ρ：回 home，人只做恢复部分，片段标 z 后存入 ℛ_k | Alg. 1 第 36–50 行（条件在第 37 行） |
| 过期判决 | 每个判决带所属轮次和提交步号；来自已结束轮次的判决、早于人工调整场景的判决一律丢弃 | App. A 正文 |
| 失败前缀 | 被干预的 τ 丢弃（第 26 行），到 T_max 也丢弃（第 33 行）；失败片段不进训练，但连同监控器的提示和判决留在 session 日志里，"every accepted recovery can be audited without using failed segments as supervision"（理由只出现在 App. C.5） | Alg. 1；§3.4；App. A 末段；App. C.5 |
| 兜底标签 | 没有恢复指令时，人工恢复片段统一标为 "Restore the scene to its pre-task state." | App. A；App. C.2 |

验收只看 VLM 的 M_res，没有人工复核恢复（操作员可在保存前改标签，App. C.2）；论文没说 𝒜_k 是否要求随后的任务尝试也成功（未查到）。

### 恢复程序与数字孪生（§3.2，§3.3 末段，§4.1）

- 编码 agent 根据真机录像（相机、轨迹，有标定时用标定）重建 MuJoCo 孪生，拟合物体位姿和接触参数（§3.2）。
- agent 在孪生里尝试任务：成功的尝试组成任务数据集；失败时诊断原因、为 z 命名，把技能做成两种形式，一是技能库里的程序，二是把扰动场景、z 和纠正动作配对的恢复 rollout。这些新指令就是 𝒦 的初值（§3.2）。
- **真机上只有学到的恢复策略执行自主恢复**；程序在孪生里开发和测试，用来扩充技能库，Alg. 1 里没有直接调用程序（§3.3 末段）。摘要里的 "invokes a learned or programmatic recovery" 指两个场景合起来：程序分支只在仿真基准里用过。
- 每个任务 6–7 个恢复技能，§4.2 说"25 in total"，App. D 的 Fig. 8/9 说 "24 recovery skills"，**两处不一致**。
- 仿真基准（§4.1 "Benchmark implementation"）：MolmoAct2 任务策略配程序化恢复技能。策略执行预算用完，或（仅 LIBERO-Pro）进度停滞时，由程序接管。**程序开发和评测用不相交的随机 seed**，评测用 held-out 初始物体配置；程序把任务做完也计成功。仿真用的监控器型号、重试次数、预算数值均未查到。

### DAgger 分组（§3.4，Eq. (1)；App. A；App. C.2–C.3）

| 集合 | 内容 | 进入 |
|---|---|---|
| 𝒮_k | 无人接管完成的任务 rollout | D_task |
| ℰ_k | 人类任务演示（含操作员随时接管、阈值触发的演示和 intact 接管） | D_task |
| ℛ_k | 人类恢复演示，每条带 z | D_rec |
| 𝒜_k | 通过场景复原检查的自主恢复 | D_rec |

- 第 k 轮内两个策略都不变（"During round k, both policies remain fixed"）。Eq. (1)：D_task^{k+1}=D_task^k∪𝒮_k∪ℰ_k，D_rec^{k+1}=D_rec^k∪ℛ_k∪𝒜_k，两个数据集分开存放。
- 新技能的流程：M_int 提出新 z → 人演示，作为第一个训练样本 → z 加入 𝒦，并引导孪生里的程序开发 → 恢复策略在新演示上训练后，z 才进入 𝒦_ρ，从此可以自主执行（§3.4 末段；App. C.2）。**"登记"和"注册"分两步**。
- 训练：任务策略和恢复策略都从 π0.5 base checkpoint 微调，配方相同。动作 14 维（每臂 6 关节 + 1 夹爪），块长 50（30 Hz 下 1.67 s）。图像为 top 加双腕，224×224。分阶段训练（App. C.3，Tab. 5）。

## 主要数字

### Table 2 LIBERO-Pro（列：Obj-Pos / Obj-Task / Goal-Pos / Goal-Task / Spat-Pos / Spat-Task → Avg.，Avg. 为六列非加权平均）

| 方法 | 数字 | Avg. |
|---|---|---|
| CaP-Agent0 | 27.0 / 31.0 / 29.0 / 16.0 / 13.0 / 23.0 | 23.2 |
| RATs | 61.0 / 63.0 / 43.0 / 36.0 / 29.0 / 31.0 | 43.8 |
| ASPIRE [41] | 98.0 / 95.0 / 81.0 / 45.0 / 51.0 / 60.0 | 71.7 |
| π0 | 全 0.0 | 0.0 |
| π0.5 | 17.0 / 1.0 / 38.0 / 0.0 / 20.0 / 1.0 | 12.8 |
| MolmoAct2-LIBERO | 52.0 / 24.0 / 20.0 / 21.0 / 27.0 / 57.0 | 33.5 |
| **Recova** | 91.2 / 87.2 / 82.2 / 67.0 / 62.2 / 83.0 | **78.8** |

比 ASPIRE 高 7.1 pp；Goal-Task 高 22.0、Spatial-Task 高 23.0；Object 套件上 ASPIRE 仍更强，论文没解释原因（§4.1 "LIBERO-Pro results"）。每个任务评 50 个初始状态，沿用 [41] 的做法（§4.1）。Table 2 在 HTML 里排在 §4 标题之后、§4.1 之前。

### Table 1 MolmoSpaces（Pick / P&P / Open / Close → Avg.）

| 方法 | 数字 | Avg. |
|---|---|---|
| CaP-Agent0 | 23.0 / 11.0 / 14.0 / 36.0 | 21.0 |
| RATs | 37.0 / 22.0 / 20.0 / 73.0 | 38.0 |
| π0 | 14.0 / 7.0 / 10.0 / 47.0 | 19.5 |
| π0-FAST | 20.0 / 10.0 / 9.0 / 35.0 | 18.5 |
| π0.5 | 28.0 / 10.0 / 18.0 / 62.0 | 29.5 |
| MolmoAct2-DROID | 41.0 / 32.0 / 3.8 / 67.0 | 36.0 |
| **Recova** | 78.7 / 40.5 / 56.5 / 83.9 | **64.9** |

比 RATs 高 26.9 pp。表中没有 ASPIRE 行。每类的回合数、场景和步数预算未查到。

### 口径差异（两张仿真表都适用）

- "Baseline rows reproduce published results"（Tab. 1/2 caption）；"Baseline results are taken from prior publications, while Recova results are obtained from our own evaluation"（§4.1）。**基线是照抄的已发表数字，不是同协议复现。**
- 没有逐基线注明数字出自哪篇。ASPIRE 引为 [41]，即 arXiv 2607.00272。
- **没有"同一 MolmoAct2 不加恢复"的对照行**：LIBERO-Pro 表里是 MolmoAct2-LIBERO，MolmoSpaces 表里是 MolmoAct2-DROID；Recova 在两个基准内部各用哪个 checkpoint 未写明。
- Recova 的成功包括恢复程序完成任务的回合（§4.1）。
- 没有专门的仿真消融。App. E 给出 16 个挑选出来的定性例子（每个基准 8 个），同一初始状态下关恢复失败、开恢复成功。

### Table 3 真机（双臂 YAM，每任务每配置 20 次，操作员按 Tab. 4 判据人工评判，App. C.4）

| 配置 | Pencil box | Stack rings | Draw tile | Discard tile | Avg. |
|---|---|---|---|---|---|
| Base task policy | 20.0 | 35.0 | 25.0 | 15.0 | 23.8 |
| + DAgger | 70.0 | 75.0 | 85.0 | 80.0 | 77.5 |
| + Recovery skills | 85.0 | 90.0 | 90.0 | 85.0 | 87.5 |

恢复技能在铅笔盒、叠环上各 +15，两个麻将任务各 +5，作者的解释是"a single misplaced ring or pencil blocks the remaining steps"（§4.2）。真机成功不由监控器判定。

### Fig. 4 / §4.2 DAgger rounds

抽麻将牌任务跑 4 轮，每轮 7–8 个回合。人工接管从第 1 轮的 7/8（87.5%）降到第 4 轮的 0/7（0%）；任务策略完成占比从 12.5% 升到 85.7%。第 2、3 轮的数值正文没给（只在图里）。Fig. 4 中栏另有一段 14 分钟会话：四站完成 31 个回合中的 22 个，人工控制占各站时间合计的 11%（读者摘录，写卡人未再核）。**样本量很小。**

## 成本与延迟

| 项 | 数值 | 出处 |
|---|---|---|
| 监控器单次判决中位延迟 | 干预 4.5 s / 完成 5.0 s / 复原 5.5 s | App. B |
| 调用频率 | 完成每 20 s 一次（停机），干预每 10 s 一次（异步） | Alg. 1 第 1 行；App. C.2 |
| 单次恢复上限 | 1500 步 = 50 s | 同上 |
| 回合上限 | 5400 步 = 3 min | App. C.1 |
| 图像 | 四路拼成一张 448×336 | App. B |
| token / 美元成本 | 未报告 | — |
| 训练 | 每阶段 2000–5000 步，8×L40，1–3 h；AdamW，lr 2.5e-5→2.5e-6 余弦，batch 64，EMA 0.99 | App. C.3，Tab. 5 |
| 人工 | 一名操作员同时管四个工位 | App. C.1 |

## 与我们 v1 的关系（逐条）

1. **判决种类：五类代码判决 vs 四个 VLM 查询。** Recova 的判决全是 VLM 二值：complete、intervene、scene_intact、restored。M_done 把"部分进展或视角模糊"并进 false（App. B Fig. 6），我们把这种情况单列为 InsufficientEvidence，先再观测，最多 1 次 inspect（设计 §4）。我们分得更细，这一点不改。Recova 没有 Blocked、SafetyStop 的对应物，也没报告监控器误判率，所以"VLM 当裁判"的可靠性在它那里没有证据。我们设计 §3.2 / §7 坚持代码验证器、M0 先测 FPR，这一立场不受影响。
2. **"完成优先于干预"。** Alg. 1 第 23 行。我们节点内不异步查，判决在节点边界一次性给出，这本身就是"停滞后先查后置条件，满足就 Pass"，等价于完成优先。但 `etude/verifier/verdict.py` `check()` 的顺序是 SafetyStop → **Blocked** → 后置条件：节点中途任何一帧出过 `ik_failed`，即使结束时后置条件全满足，也判 Blocked → replan，白花一次 VLM 调用。Recova 的优先级说明，"目标已达成"应压过"过程中出过异常"，安全类除外。见补丁 P3。
3. **复原检查 vs 我们的"退回上一已验证节点"。** Recova 恢复后必须过 M_res，以回合初始图像为参照，判"可工作"而不是"精确复位"（§3.3 Verify；Alg. 1 第 41–42 行）。我们的 `previous_node`（`etude/runner.py` `_dispatch`/`_retreat_step`）只把 ee 位姿伺服回锚点，`retreat_end` 后直接重试失败节点，**不复验锚点节点的后置条件，也不检查场景是否还完好**。锚点的 Pass 是早先的判决，失败过程中可能已被推翻（碗掉了、瓶子倒了）。按 Recova App. A 的过期判决规则，场景变过之后旧判决应作废。见补丁 P1（必要）。
4. **intact / not-intact 两分。** M_int 把"卡住但场景完好"和"场景被扰乱"分开，分别走人工完成和 Recover(z)（Alg. 1 第 27–30 行）。我们的 `recovery.yaml` 只按判决种类和 `reason` 分流，没有"场景是否完好"这个信号。我们可以用代码算这个信号：非目标物体对回合初始关键帧做 `not_moved`，0 次 VLM。有了它，规划器才能在 replan 时区分 Restorative（先复原）和 Adaptive（换做法）。见补丁 P2、P5。
5. **失败前缀排除 vs 记忆只收全 Pass 回合。** Recova 丢弃失败前缀，但保留成功的接续部分（演示和恢复分开入库），失败段只留日志供审计（App. A 末段；App. C.5）。我们的 `etude/memory/writer.py` 要求官方成功 ∧ **所有判决** Pass，"retries included"：任何一次 Fail 后重试成功的回合整条不入库。这比 Recova 严，避免了 Recova 只凭 VLM M_res 就写入 𝒜_k 的误判风险，但也扔掉了"失败 → 恢复动作 → Pass"这类数据，而恰恰是这类数据能教恢复规则。日志保留这一侧我们已经一致：traces/ 全留。见补丁 P6（建议，v1.1）。
6. **c_max=2 vs r=2。** 两者语义不同，不能直接对标。Recova 的 c 是**任务级、跨尝试的连续干预计数**，只在任务成功或演示后清零，自主恢复成功也不清零。我们的 r 是**节点级原地重试**，该节点 Pass 后清零（`runner._advance` 里 `self.retries.pop`）；回合级的熔断是 `max_backtracks: 2`、`MAX_RETREATS_PER_NODE = 1`、12 次 VLM 调用和 step_lim。我们的回路有上界，不会像 Recova 那样无限循环，但"同一失败类型在一回合内反复出现"没有计数。见补丁 P4（建议）。
7. **监控延迟 4.5–5.5 s 对我们 90 s 规划等待的参考。** Recova 单次判决约 5 s：单张 448×336 拼图、temperature 0、JSON、Flash 级模型。我们一次规划调用估计约 15 s（设计 §3.4），是带工具循环的多轮会话，90 s 硬超时约为 Recova 单判决的 18 倍，余量足够。可借鉴的是输入形态：三路 640×480 约 3×410 token，拼成一张图可以省 token（补丁 P7，建议）。另外，Recova 的时间尺度（检查间隔 10–20 s，恢复上限 50 s，卡住阈值约 30 s）**比我们整个回合还长**：step_lim 600–1050 步，25 Hz 下是 24–42 s（设计 §8.2 F6）。所以它的异步定时监控不适合直接搬过来，我们在节点边界做代码判决的粒度是对的；它的数字不能直接当 `max_chunks` 或恢复预算的参照。
8. **训练权重的边界。** Recova 的真机增益大部分来自 DAgger 微调（+53.7 pp）。恢复技能那 +10 pp 也是**训练出来的恢复策略** ρ_φ 带来的（App. C.3），程序分支真机上没用过（§3.3 末段）。我们冻结 π0.5，能对标的只有它的仿真设置：冻结任务策略 + 程序化恢复。但那一组没有同策略有/无恢复的对照，基线又是照抄的，所以**没有一个干净的"恢复程序带来多少"的数字可以引用**。v1 要自己做"冻结 π0.5 有/无 harness、同 seed 配对"的对照，设计 §5 M1 已经覆盖。
9. **seed 划分。** "Program development and evaluation use disjoint random seeds"，加上 held-out 初始物体配置（§4.1）。这与我们 E（seed 0）/ V（seed 1）/ T（seed 2 + 留出任务）的划分同构，我们还多了 A/A 噪声估计和配对门。Recova 没报告重复运行的方差（未查到）。
10. **注册两步走。** 新 z 先进 𝒦（候选），训练、验证后才进 𝒦_ρ（可自主执行）（§3.4；App. C.2）。这对应我们 harness 回路"编辑者提案 → L0–L3 门 → 合并"。M_int 要求"匹配已知技能时逐字复用"，它的作用是让技能集不随措辞发散，我们的 `rules[]` 用结构化 `when` 匹配，没有这个问题。
11. **不借的**：数字孪生 real-to-sim（我们直接用 RoboDojo 仿真）；人在回路（ℰ_k/ℛ_k 演示、操作员改标签）；异步定时监控；恢复策略训练。

## 对 v1 的补丁建议（不直接改文件，供决策）

"必要"指不改会让现有机制按自己的定义出错；"建议"指可选的增强。涉及 `evolvable/` 的项，若在 M1 开跑前写进初版，不过门；开跑后只能作为编辑者候选走 L0–L3 门。

| # | 目标 | 改成什么 | 依据 | 必要性 |
|---|---|---|---|---|
| P1 | 设计 §3.3 伪代码与 §7"交还 VLA 时状态出分布"一行；`etude/runner.py` `_retreat_step` 中 `retreat_end` 之后 | 退回锚点位姿后，先对锚点节点的后置条件重跑一次 `verifier.check`（代码，0 次 VLM），并检查非目标物体相对回合初始关键帧 `not_moved`。都通过才重试失败节点；否则作废该锚点（`anchors.pop()`），继续往前找下一个仍然成立的锚点，或直接 `replan`，并在请求里带 `scene_intact=false` | Recova §3.3 Verify、Alg. 1 第 41–42 行（恢复后必须复原验收，以初始图像为参照）；App. A（场景变过之后的旧判决丢弃） | **必要**：现在"last_verified"可能是一个已被推翻的判决，重试会在被扰乱的场景上耗步数 |
| P2 | `etude/verifier`（predicates.py / verdict.py）；`frozen/schema/verdict.json` | 新增只读派生字段 `scene_intact`：回合 t=0 关键帧作为 I_0 参照，对检测到的非目标物体做 `not_moved`（复用 `rgb_diff.not_moved_max`，不新增阈值）。所有非 Pass 判决都写进 verdict JSON，不改判决种类 | Recova App. B Fig. 6 M_int 的 scene_intact；Alg. 1 第 27–30 行 intact 分支；M_req"anything that must NOT have changed" | 建议（P1 的依赖；做 P1 就需要） |
| P3 | `etude/verifier/verdict.py` `check()` 顺序；设计 §4"判决 JSON"语义段 | 顺序改为 SafetyStop → 后置条件 → Blocked：后置条件全部满足且置信达标时判 Pass，`ik_failed` 记进 predicates 作证据；只有后置条件不满足时才因 `ik_failed` 判 Blocked。SafetyStop 保持最先 | Recova Alg. 1 第 23 行 "completion takes precedence"；App. A（干预判决到达时等完成判决） | 建议（验证器代码属冻结层，须在 M0 前由开发者改，并补一条单测：中途 ik_failed 但终态满足 → Pass） |
| P4 | `evolvable/rules/recovery.yaml` `fallback`；设计 §3.1 八行表"进化面"格 | 写清 r（节点级、Pass 清零）和回合级熔断的语义；可选新增 `max_same_failure_per_episode: 2`：同一失败签名（节点 executor + 首个为 false 的谓词 + reason）在一回合内出现 2 次后，不再原地重试或回退，直接 replan 并在请求里标"重复失败"。不要用 c_max=2 去对标 r=2 | Recova Alg. 1 第 6–8 行、App. C.2（c_max=2，任务级、跨尝试，"avoids cycles of repeated failure"；自主恢复不清零） | 建议 |
| P5 | `etude/runner.py` `_plan` 的 `req`；`evolvable/prompts/planner.md` | replan 请求附带 `failure_class ∈ {stall_intact, scene_disturbed, blocked}`（来自 P2 的 scene_intact 和 Fail 的 reason）。planner.md 加一段：scene_disturbed 时先出 Restorative / Enabling 节点，再续原序列；stall_intact 时出 Adaptive 节点（换抓取侧、换接近方向），不要原样重发失败节点 | Recova §3.1、Fig. 3（Restorative / Enabling / Adaptive）；§1 第 2 段（单纯重试不能解除障碍） | 建议（prompt 部分属 harness 面 1） |
| P6 | 设计 §3.1"记忆回路"与 §3.3 记忆写入；`etude/memory/writer.py` | v1 不改主库规则。v1.1 加第二类条目 `memory_db/<task>/recoveries.jsonl`，按失败签名索引"失败节点 → 恢复动作/节点 → Pass"。入库条件 = 官方成功 ∧ 每个节点的**最后一次**判决为 Pass ∧ 恢复后锚点复验通过（P1），失败尝试只留 traces/。**不学** Recova 只凭 VLM M_res 就入库 | Recova §3.4 Eq. (1)（D_task / D_rec 分开）；App. A 末段、App. C.5（失败前缀不作监督，只审计）；§3.4（𝒜_k 只要过复原检查，误判风险见我们设计 §3.2） | 建议（v1.1） |
| P7 | 设计 §4 MCP `observe`；§3.4 c_call 口径 | 可选参数 `composite=true`：三路拼成单张图（例如 2×2 网格、单格 320×240），供 `get_verdict` 之后的规划调用使用；c_call 估算里加一栏对照 | Recova App. B（448×336 四路拼图、temperature 0、JSON；单判决中位 4.5–5.5 s） | 建议（先在 M1 记录 token 再决定） |
| P8 | 设计 §3.0 进化面表"恢复规则"一行的收益格；§7 风险表 | 把 Recova 加进证据，并注明口径：真机 +10 pp（Tab. 3）来自**训练出来的**恢复策略，仿真增益没有同策略无恢复对照，基线照抄（Tab. 1/2 caption；§4.1）；不能当作冻结 + 代码恢复的预期收益 | Recova Tab. 1–3；§3.3 末段；§4.1 | 建议（措辞修正，防止误引） |
| P9 | `frozen/params/thresholds.yaml` | **不改**。Recova 的 30 s 卡住阈值、T_rec 50 s、N_i 10 s 都长于我们整个回合（24–42 s），不适合用来调 `stall_chunks` 或 `max_chunks`；阈值层冻结、只收紧的规则不变 | Recova App. B Fig. 6、Alg. 1 第 1 行；我们设计 §8.2 F6 | 无需改（记录结论） |
| P10 | `etude/runner.py` `_PlanJob` | 后台规划任务带上 `episode_id` 和发起时的 `len(self.verdicts)` / `frame.t`；取结果时，若回合已变，或期间发生过回退（P1）或复原，就丢弃结果并重新发起。目前代码只在 `reset()` 时丢弃旧任务 | Recova App. A（判决带轮次与提交步号；已结束轮次或人工调整场景之前的判决丢弃） | 建议（目前规划期间执行冻结，风险低） |

## 核查记录

- 被驳回 1："完成检查和恢复检查时机器人静止" → 应为"完成检查和**场景复原检查**（restoration check）时机器人静止"（App. C.2）；"任务策略在干预检查期间继续动作"出自 App. A，不是 App. C.2。所有数字（600/300/5400/1500/30 Hz）核对无误。
- 被驳回 2："Recover 只给一次有界自主尝试" → 补上条件：**仅当 z ∈ 𝒦_ρ**（Alg. 1 第 37 行）；z 不在注册表时不做自主尝试，直接回 home 由人恢复（第 48–50 行）。
- "自主恢复成功不清零 c"是从 Alg. 1 推出来的，原文没有直接写。
- 技能数：§4.2 写 25，App. D 写 24，原文本身不一致。
- 全文检索不到 RoboRSI、Harness VLA（2607.08448）、2610.12424；全文里的 "harness" 指真机控制软件（App. A、C.2），所以论文里没有与这些工作的对比。
