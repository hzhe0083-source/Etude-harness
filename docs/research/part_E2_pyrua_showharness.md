# E2 部分：PyRUA-Lean 与 Show-Harness（A 层卡片，事实底稿）

- 抓取日期：2026-10-10。节号、表号、图号都指 arXiv v1。
- 只整理事实，不提方案。推断的内容标"推断"，论文没报告的写"未报告"。
- 本地文件都在本目录：
  - `2610.01939.html` / `.txt`：PyRUA-Lean 的 arXiv HTML 全文和提取出的文本。
  - `2609.10522.html` / `.txt`：Show-Harness 的 arXiv HTML 全文和提取出的文本。
  - `2609.10522.pdf` / `.pdf.txt`：Show-Harness 的 PDF 和提取出的文本。HTML 版里 Table 2 没渲染出来，只剩 nicematrix 占位符，所以另抓了 PDF。Table 2 的数字取自 PDF。Fig. 6–10 是矢量图，数值是用 PyMuPDF 按坐标从 PDF 文字层读出来的，标"读图"。
  - `pyrua_guide.md`：PyRUA-Lean 仓库的 `docs/guide.md`，只用来补工程细节，引用时标"仓库文档"。

---

## 0. 论文定位

| 系统 | arXiv | 标题 | 提交 | 证据级别 |
|---|---|---|---|---|
| PyRUA-Lean | 2610.01939（cs.CV） | Fewer Tokens, Better Action: GPT-6 Astra Robot Agents with 14% Higher Success Rate but 65% Fewer Tokens | 2026-10-01 | **全文**（HTML） |
| Show-Harness | 2609.10522（cs.RO） | Show-Harness: Just a VLM Agent Can Play Robots | 2026-09-09 | **全文**（HTML + PDF） |

两篇之间的关系（事实）：
- PyRUA-Lean 在相关工作里引用了 Show-Harness（其参考文献 [8]）。
- 两篇都和 Harness VLA（RPent，arXiv 2607.08448）有关。PyRUA-Lean 建在 RPent 的机器人栈和原语实现上，基线就是 RPent 的 tool-calling agent。Show-Harness 把 Harness VLA 当作对比基线，表里写作 "H-VLA"。

---

## 1. 与讨论方表格的核对

| # | 讨论方表格 | 论文原文（出处） | 结论 |
|---|---|---|---|
| P1 | 仿真：LIBERO-PRO + RoboTwin 2.0 + RoboCasa365 共 700 个实例 | LIBERO-PRO 40 任务、RoboTwin 2.0 50 任务、RoboCasa365 50 任务，每个任务 5 个 seed，共 200 + 250 + 90 + 160 = 700 个实例，1,400 个回合（§4.1，App F Table 7） | 一致 |
| P2 | 63.1% → 71.7% | 一致（Table 2）。标题里的 "14%" 是**相对**提升，绝对值是 +8.6 个百分点（§4.2） | 一致 |
| P3 | LIBERO-PRO 83% → 94% | 83.0 → 94.0（Table 2） | 一致 |
| P4 | 两边都做成的任务里 token 少 65% | 788k → 276k（Table 2，"All" 行），即 −65%。需要加四条限定：①指**输入 token**，含缓存输入；②是"每个已解回合"的整回合均值，成功之后的调用也计入；③是四个子集合并的数，作者说明合并数主要由 LIBERO-PRO 的长回合决定，各子集分别是 4.5× / 1.7× / 1.5× / 2.0×，折合 −78% / −41% / −34% / −51%；④对照方是同一个 GPT-6 Astra、同一套原语、同样 LLM 调用预算的 RPent tool-calling agent（§4.1–4.2） | 数值一致，口径需要加限定 |
| P5 | 没做真机 | "physical-robot performance remain untested"（§5） | 一致 |
| P6 | 团队：北大（周大权组）、NUS、NVIDIA | 单位共四个：1 Peking University，2 National University of Singapore，3 NVIDIA，4 **Impossible Research**（Xiuyu Li、Haiwen Feng）。通讯作者 Daquan Zhou。NUS 只出现在第二作者 Jianxin Bi 的双重署名里 | **漏了 Impossible Research** |
| P7 | agent 每步都调一次模型，token 太多；改成写 Python，一段代码执行多步再回报 | 机制一致（§3）。限定：平均每个 cell 执行 2.1 个动作原语，但 35% 的 cell 仍然是"走一步看一眼"，在 RoboTwin 2.0 上这个比例是 54%（App C Table 6） | 一致，需加限定 |
| S1 | 让 VLM 每步只选"前移、下降、抓"这类小动作 | 一致（§3.2）。另有三点：①还有 ROTATE_CW/CCW（带轴）、RELEASE、DONE；②有 Action Chunking，目标较远时 VLM 一次给出几步，开环执行（§3.3）；③子任务计划由同一个 VLM 以 planner 角色在开局生成 | 一致，需补充 |
| S2 | 没做仿真基准 | 论文没有任何仿真基准成绩。但用了 ManiSkill + RoboLab 的 230 个仿真回合做 sim-to-real 训练数据（§5.1，App 7.2） | 一致 |
| S3 | 真机 Franka 10 个抓放任务：零样本 89% | Table 2 Cross-Task 的 ZS 平均是 89.0，ZS 用的是 Gemini-3.1 Pro，medium thinking。10 个任务 = 5 种物体 × 2 种容器（§5.1）。**论文没写 Cross-Task 用的是哪台臂**；§5.1 硬件部分先介绍 Franka，插件消融明确是在 Franka 上做的，所以推断是 Franka | 数值一致；"Franka"是推断 |
| S4 | π0.5 39% | 39.0（Table 2）。注意这个 π0.5 是在同一批真机示范上**微调过的**，不是零样本（§5.1 Baselines） | 数值一致，条件不同 |
| S5 | 最好的基线 57% | 57.0，是 RATS（code-as-policy agent，参考文献 [98] "Playful agentic robot learning"）。H-VLA 是 50.0（Table 2） | 一致 |
| S6 | 换背景、光照、视角 100%，π0.5 40% | Cross-Environment 一共 5 个设置：背景、光照、视角、干扰物、sim-to-real，每个设置 20 次。ZS 的 100.0 是前 4 个设置（**含干扰物**）80 次全部成功，ZS 没有跑 sim-to-real（表中为 "–"）。π0.5 的 40.0 是 5 个设置共 100 次的平均，**含 sim-to-real 的 0/20**。按同口径（前 4 个设置）算，π0.5 是 40/80 = **50%**；只看背景、光照、视角三项，π0.5 是 30/60 = 50%，ZS 是 60/60 = 100%（Table 2；同口径数字是我按表中的分子分母算的） | **不一致**：40% 和 100% 的分母不一样，同口径下 π0.5 是 50% |
| S7 | Franka + AgileX 93%，π0.5 41% | ZS 是 (48 + 45)/100 = 93.0，π0.5 是 (22 + 19)/100 = 41.0。这一组**只用了 5 个 Plate 任务**，每台臂每个任务 10 次（Table 2） | 数值一致，任务数是 5 个不是 10 个 |
| S8 | 团队：NUS Show Lab（Mike Shou 组） | Show Lab, National University of Singapore；通讯作者 Mike Zheng Shou | 一致 |

---

## 2. PyRUA-Lean（arXiv 2610.01939）【全文】

来源：
- arXiv HTML 全文 https://arxiv.org/html/2610.01939 。
- 仓库 github.com/DAGroup-PKU/PyRUA-Lean（README，`docs/guide.md`）。
- 项目页 dagroup-pku.github.io/PyRUA-Lean 。

### 2.1 A 层卡片

- **问题**（§1）：
  - VLM agent 通过工具调用操作机器人原语时，凡是依赖上一步结果的操作，通常都要再调一次 VLM。
  - 中间结果会不断累积在对话里，后续每次调用都要重新处理。基线 RPent 的运动原语每走一步还会自动返回多张相机图。
  - 论文要回答的是：VLM agent 怎样组合机器人原语，既能可靠完成任务，又能通过选择性观测和上下文管理减少 token。
- **架构**（§3，Fig. 2，Table 1）：
  - 机器人被包装成一个 Python 对象 `robo`。agent 只有一个工具 `python(code)`，能看到一份 API 参考。每次交互，VLM 写一个代码 cell，runtime 执行，执行反馈决定下一个 cell 怎么写（§3.1）。
  - 有一个持久的 Python 命名空间，回合内跨 cell 保留变量和 helper 函数（§3.1）。
  - 原语和 RPent 一一对应，两个 agent 用的是同一份 RPent 实现（Table 1）：
    - 手臂运动：move_to / move_pose / move_delta / rotate_wrist / rotate_pitch
    - 夹爪：set_gripper / release / scripted_grasp
    - VLA：pi0_pick / pi0_doubled / lingbot_act / rldx_skill / rldx_arm
    - 底盘：navigate_to / move_base
    - 感知：segment / back_project / back_project_batch / sample_world_xyz / query_world_map
    - 观测：`robo.state()`、`robo.done`（成功标志）、`robo.show(camera)`（按需取图）、`robo.world_map()`
  - 两个机制（§3.2–3.3）：
    - 反馈驱动的原语组合：cell 内部做条件检查和局部重试，不额外调用 LLM。
    - 选择性观测：只有代码显式 print 的内容和 `robo.show` 请求的图像，才在 cell 结束时一起返回给 VLM。
  - 对照基线 RPent tool-calling（Table 1）：一次工具调用执行一个原语；每次 move 都自动返回三张相机图和状态。
  - 规划器是 GPT-6 Astra，high reasoning effort，两个 agent 都跑在 Codex CLI 里。Codex CLI 保留完整对话，接近 272K token 时才压缩摘要（§3.3，§4.1）。
  - 冻结的 VLA 按基准分配：LIBERO-PRO 用 π0.5，RoboTwin 2.0 用 LingBot-VLA，RoboCasa365 用 RLDX-1。分割都用 SAM 3（§4.1）。
  - 运行保护（§3.1）：
    - 未捕获的异常会终止当前 cell 并返回 traceback，异常之前赋值的变量仍然保留。
    - 每个作用于机器人的原语执行前，如果任务已经成功，runtime 就终止这个 cell。
    - 外部 watchdog 强制执行两小时的回合上限。
    - 单个 cell 没有执行时限。Codex CLI 在 300 s 后返回工具调用超时，但不取消 cell，后续 cell 排队等待。
- **什么在进化**：不进化，纯推理时系统。评测时两边都关掉了跨回合记忆（§3.1，§4.1，App E）。
- **改进信号**：无。
- **接受门控**：无。
- **关键结果**：
  - 成功率（Table 2，tool → code）：
    - LIBERO-PRO 83.0 → 94.0
    - RoboTwin 2.0 60.0 → 69.2
    - RC365 atomic 78.9 → 86.7
    - RC365 composite 34.4 → 39.4
    - 合计 63.1 → 71.7
  - 两边都成功的实例，每个已解回合的均值（Table 2，"All"）：
    - LLM 调用 17.0 → 8.7（−49%）
    - 输入 token 788k → 276k（−65%）
    - 美元 1.63 → 0.74
    - 推断：两边都成功的实例共 406 个（由成功率和"只有一方成功"的数目反推，论文没有直接给出）。
  - 只有 code 做成的实例 96 个，只有 tool 做成的 36 个（§4.2）：
    - 这 96 个里，有 52 个是 tool 把调用预算用完了，44 个是 tool 自己结束回合并报告失败（§4.5，App D）。
    - 那 44 个里约四分之一，code agent 只调了 VLA，VLA 在它那一次成功了，在 tool 那一次没成功（App D）。
  - Token 节省的分解（§4.3，Fig. 5）：LIBERO-PRO 的 4.48× = 调用次数少 2.55× × 每次调用 token 少 1.76×。RC365 atomic 上，code 每次调用的 token 反而更多。
  - 事后按 token 截断重算（Fig. 3）：在 LIBERO-PRO 上达到 tool 的最终成功率，code 每回合只需 564k token，tool 需要 3.18M。这是在已记录轨迹上回溯计算的，不是预先设定预算重跑。
  - 只把 tool 基线改成按需取图（Table 8，LIBERO-PRO）：
    - 成功率 83.0 → 68.5。
    - 在三种配置都成功的 131 个实例上，调用次数 19.9 → 22.8，token 都是 1.07M。
    - code 在同样这些实例上是 7.4 次调用、222k token。
  - 消融（Table 3）：
    - 去掉 VLA：LIBERO 73.0 → 80.5，RoboTwin 42.8 → 68.8，RC365 atomic 45.6 → 65.6，RC365 composite 7.5 → 20.0。
    - 去掉操作指南：LIBERO 84.0 → 88.0，RoboTwin 64.0 → 66.8。
    - 两者都去掉：LIBERO 74.0 → 82.0，RoboTwin 60.4 → 66.4。
    - RoboTwin 无 VLA 时，再去掉指南，tool 的成功率反而从 42.8 升到 60.4（§4.4）。
  - RoboDojo 小规模试验（§4.2，3 个任务）：
    - 成功率 26.7%（8/30）对 3.4%（1/29）。
    - 每次成功所耗的总 token 从 11.56M 降到 1.07M（−90.7%）；29 对配对回合的总 token 降 25.3%。
- **优点**（论文证据）：
  - 4 个子集的成功率全部提高，成本全部降低（Table 2）。
  - 不用 VLA 或不用指南时，token 节省依然存在（Table 3）。
  - 能在代码里做几何计算和脚本化恢复，例如：
    - 对倒下的摩卡壶用盖子点云拟合平面，把它扶正（Fig. 4 / Fig. 12）；
    - 用三个把手位置求出铰链圆心，沿圆弧拉开柜门（Fig. 13）。
  - 每个原语都写进调用台账，回合可以复盘（仓库文档）。
- **缺点与风险**：
  - 只测了 GPT-6 Astra 一个规划器、RPent 一套原语栈，只在仿真里测；每个实例每个 agent 只跑一次。组合、持久状态、选择性反馈三者各自贡献多少，没有拆开（§5）。
  - 预算按 LLM 调用次数给。52/96 个"只有 code 成功"的实例是 tool 把调用预算用完了；作者也承认成功率差距不能全部归因于恢复逻辑（§4.5）。
  - 在以 VLA 为主的任务上收益最小（RC365 atomic）。code agent 的 API 说明更长，首个 prompt 的中位数是：
    - LIBERO-PRO 22k 对 20k；
    - RoboTwin 21k 对 15k；
    - RC365 24k 对 17k。
    计入失败回合后，RC365 atomic 上两边成本之比只有约 1.1×（§4.5，App E）。
  - agent 在线可以读仿真的成功谓词 `robo.done`，并在代码分支里使用（见 2.2 的第 5 问）。
  - 延迟没有测，作者的理由是共享网关上的调用延迟波动太大（App E）。
  - 单个 cell 没有执行时限（§3.1）。
- **可复用的模块**（列事实，不做取舍）：
  - 同一套原语提供两种接口，tool 和 `robo` 方法一一对应（Table 1）。
  - 持久命名空间。
  - 选择性观测：`print` 加 `robo.show`，在 cell 结束时统一返回。
  - 任务成功后终止 cell 的守卫；仓库里的实现是抛出 `EpisodeFinished`，这是一个 `except Exception` 吞不掉的 `BaseException`（仓库文档）。
  - 调用台账 `calls.jsonl`（仓库文档）。
  - 严格配对比较：`compare` 拒绝未匹配或重复的 (backend, task, seed)（仓库文档）。
  - token 分解：调用次数比 × 每次调用 token 比（Fig. 5）。
  - 按 token 预算回溯截断的成功率曲线（Fig. 3）。
  - 按标价计费、计入缓存的成本记账（App E）。
- **工程成熟度**：
  - 开源：是。github.com/DAGroup-PKU/PyRUA-Lean，Apache-2.0，2026-09-25 创建。
  - 依赖：
    - RPent 机器人栈（github.com/RLinf/RPent），先按要用的机器人装好 RPent；
    - Codex CLI 0.155.1，或经 Claude Agent SDK 运行的 Claude Code（README）。
  - VLM：GPT-6 Astra。
  - VLA：π0.5 / LingBot-VLA / RLDX-1。分割：SAM 3。
  - 仿真器：LIBERO-PRO、RoboTwin 2.0、RoboCasa365。
  - 读仿真真值：是。成功标志 `robo.done` 在线可读，详见 2.2 第 5 问。
  - 仓库默认参数和论文描述不同：
    - 仓库里 `--images` 默认是 `on-motion`，只要动了就附两张图；`--feedback` 默认是 `rich`，附加状态和台账。
    - 论文描述的是按需取图、只返回 print 的内容。
    - 论文没写主实验用的是哪组开关。

### 2.2 VLM 与 VLA 的接口

1. **VLM 的输出是什么**：
   - 每次调用输出一个 **Python 代码 cell**（§3.1）。cell 里包含：
     - 末端目标坐标（如 `move_to([x, y, z], ...)`）；
     - 给 VLA 的一句自然语言指令（如 `pi0_pick('pick up the black bowl on the stove', max_chunks=20)`）；
     - 分割查询；
     - 条件分支和重试循环；
     - 用 NumPy 做的几何计算；
     - 何时取哪路相机图（`robo.show`）（App A 代码清单，App C）。
   - 不是"自然语言子目标"，也不是"对动作块做接受或拒绝"。
   - 回合结束时调用 `finish(status, summary)`（仓库文档）。Table 4 里这一步记为 "finish"，随后还有一次 "final message"。
2. **VLA 的输入是什么**：
   - VLA 是冻结的，作为 `robo` 的一个方法被代码调用（Table 1，§4.1）。
   - 指令文本由 VLM 在调用时现写，长度约为一句话（例："pick up the black bowl between the plate and the ramekin"，App C 代码清单）。同一回合内可以换着说法写。
   - 有的 VLA 调用不带参数，如 RC365 的 `robo.rldx_arm()`。这时 VLA 的指令从哪里来，论文**未报告**；推断是用任务原指令或 RPent 的默认值。
   - 给 VLA 传记忆或历史：**未报告**，代码里没有这类参数。
   - 可调参数有 `max_chunks`（动作块数上限）等。
   - 返回结构化结果，例如 `Pick(success, peak_lift_m, ...)`，其中 `success` 是 RPent 的抬升启发式（仓库文档）。
3. **时域**：
   - 每个 cell 调用一次 VLM。每个已解回合的平均调用次数：code 8.7，tool 17.0（Table 2）。
   - 一个 cell 覆盖多少步（App C Table 6，统计 11,145 个 cell）：
     - 平均 2.1 个动作原语。动作原语包括移动、抓取、夹爪命令和 VLA 调用。
     - 45% 的 cell 执行 2 个及以上。
     - 35% 的 cell 只走一步就看，RoboTwin 2.0 上是 54%；在 RoboTwin 上 agent 经常让 VLA 一次只跑一个 chunk，然后检查。
     - 作为对比，tool-calling 平均每次调用 0.65 个动作工具，18,585 次调用里只有 45 次同时调了两个动作工具。
   - 每个原语本身横跨多个仿真步，例如 `move_to(..., max_steps=100)`、`pi0_pick(..., max_chunks=20)`。每个 cell 的仿真步数论文**未报告**；仓库台账记录了精确步数。
   - **回报时机由 VLM 写代码时决定**：cell 执行完就回报，cell 的边界就是回报点。这时把代码里 print 的内容和 `robo.show` 请求的图一起返回（§3.3）。以下情况会提前回报：
     - 抛出异常，包括 `assert` 失败；
     - 任务成功守卫终止了 cell；
     - Codex CLI 的 300 s 工具调用超时，这时 cell 并不取消（§3.1）。
   - 84% 的 cell 请求了图像，20% 的 cell 只在任务还没完成时才取图（Table 6）。
4. **停止条件**：
   - 单个原语靠自身参数和返回值停：
     - `move_to` 有 `tol`、`max_steps`，`Move.reached` 定义为 `final_dist_m <= tol`；
     - VLA 跑到 `max_chunks` 上限为止（仓库文档）。
     - VLA 内部能否提前停、怎么停，论文**未报告**。
   - 一段原语序列要不要继续，由 VLM 写进代码的条件决定，例如 `if approach.reached and not robo.done:`。循环里也有退出条件，例如 `if robo.done or result.status != 'cap': break`。这个 `'cap'` 状态的含义论文**未报告**；推断是"VLA 跑到了 chunk 上限"。
   - 系统层面三道硬停：任务成功守卫、2 小时 watchdog、回合 LLM 调用预算（40 次，RC365 composite 为 100 次）（§3.1，§4.1）。
5. **验证者**：
   - 没有显式的子任务结构，也就没有"子任务完成"判定者。
   - 在线证据有四类：
     - 原语返回值：`Move.reached`；`Pick.success`，即 RPent 的抬升启发式。
     - 代码里的感知断言，例如 `assert held_bowl.found and held_bowl.world_xyz[2] > 1.05`。
     - **仿真基准的成功谓词 `robo.done`**：Table 1 列为"success flag"；仓库文档写明"task predicate fires"时为真。RC365 上 agent 还读了 `robo.success_criteria()`（App C 代码清单）。
     - VLM 看自己请求的图像。
   - 最终成绩由基准自带的成功检查判定（§4.1）。agent 调 `finish` 时的自我报告不计分；tool 基线"自己结束并报告失败"的情况在 App D 有统计。
   - VLA 返回的 `Pick.success` 是 RPent 的启发式，不是 VLA 自述。agent 是否采信它，看代码怎么写；示例里常再用分割复核（App A，cell 3–4）。
6. **失败后的处理**：
   - cell 内部：
     - 按条件链跳过后续动作：19% 的 cell 做这类条件依赖，LIBERO-PRO 上是 45%。
     - 循环重试 VLA：8% 的 cell，RC365 atomic 上是 18%。例：`for attempt in range(3): result = robo.rldx_arm() ...`（App C）。
   - cell 抛异常：返回 traceback，下一个 cell 修改后重跑。例：Fig. 12 中平面拟合因半精度数组报错，下一次调用先转换类型再重跑（App D）。
   - VLM 直接接管 VLA 的工作：
     - VLA 抓空或物体掉落后，code agent 自己合上夹爪，用经典原语把恢复步骤写成脚本，逐步检查；
     - 有的回合还从点云里算出工具不直接提供的量，例如盖子平面、铰链圆心（App D）。
     - 接管时输出的是 Python 代码：经典运动/夹爪原语加 NumPy 几何计算。
   - 同样情况下 tool 基线的做法：再次请求 VLA，例如对掉落的壶连调 4 次 Pi0；或者在预算还剩大半时直接结束（App D，Fig. 12）。
7. **交还**：
   - 没有形式化的交还条件。是否、何时再调用 VLA，由 VLM 在后续 cell 里自己决定。例：Fig. 13 中代码沿圆弧拉开柜门之后，第 34 次调用里的 VLA 完成了任务（App D）。
8. **记忆**：
   - 回合内有两处：持久命名空间，存变量和 agent 自定义的 helper 函数（2% 的 cell 定义 helper，7% 的 cell 调用之前定义的 helper）；Codex CLI 的完整对话，接近 272K 时压缩（§3.1，§3.3，Table 6）。
   - 跨回合：**无**，评测时关闭。基线原有的 memory、recipe、audit 指令已从 prompt 和指南里删掉。基线的工具描述仍可能让它在成功后把 recipe 或 audit 文件写到本回合目录，但检查轨迹没有发现跨回合读取（App E）。
   - 跨回合复用"验证过的例程"被列为未来工作（§5）。
9. **成本**：
   - 报告了：每回合 LLM 调用次数；累计输入 token，含缓存输入；按 2026-09 GPT-6 Astra 标价折算的美元。标价为：输入 $10/M，缓存输入 $1/M，缓存写入 $12.50/M，输出（含推理）$50/M（App E）。
   - **−65% 的算法**：
     - 范围：两边都成功的实例，推断共 406 个。
     - 计量：每个回合整回合的累计输入 token，含缓存，含成功后的调用。
     - 比较：两边各取均值后相比，788k 对 276k，1 − 276/788 ≈ 65%。
     - 对照方：RPent tool-calling，同一个 GPT-6 Astra，同一套原语和冻结 VLA，同样的实例和 LLM 调用预算（§4.1–4.2，Table 2）。
   - 计入失败回合后的输入 token 比值：LIBERO-PRO 3.1×，RoboTwin 1.8×，RC365 composite 1.6×；RC365 atomic 的成本比约 1.1×（App E）。
   - 输出 token（含推理）在任何一个基准上都不超过账单的 17%（App E）。
   - 延迟：**未报告**。作者只计调用次数，不计时，理由是网关延迟波动太大（App E）。

### 2.3 模块行

模块行：PyRUA-Lean | GPT-6 Astra（high reasoning effort，经 Codex CLI；每个代码 cell 调用一次，已解回合平均 8.7 次） | 代码：Python cell 调用 `robo` 上的 RPent 原语（经典运动、夹爪、感知原语，加上冻结 VLA π0.5 / LingBot-VLA / RLDX-1 作为可调用函数） | 回合内：持久 Python 命名空间（变量、helper 函数）+ Codex CLI 完整对话（约 272K 时压缩）；跨回合：无（评测中关闭） | 在线：cell 内代码检查原语返回值（Move.reached、Pick.success 抬升启发式），读仿真成功谓词 robo.done，VLM 看自选图像；事后：基准自带的成功检查 | VLM 在 cell 结束时读 print 输出、traceback 和自选图像；cell 内由代码分支判断；论文的失败分析（App D）由作者人工读轨迹 | 无跨回合技能库；原语 API 固定，API 参考写在 prompt 里；回合内 agent 自定义 helper，按名字从命名空间调用 | 无（不进化） | 无（不进化）；异常前已赋值的变量保留 | 每个 task×seed 跑一次（每任务 5 个 seed）；按基准成功检查判定；预算为 40 次 LLM 调用（RC365 composite 100 次）加 2 小时；仿真器或 VLA 服务器停止响应的回合重跑 | LLM 调用次数、输入 token（含缓存）、按标价折算的美元；在两边都成功的实例上取整回合均值，App E 给出计入失败回合的比值；不计延迟 | 是：robo.done（基准成功谓词）在线可读，用于代码分支，任务成功后 runtime 终止 cell；RC365 上可读 robo.success_criteria()；世界坐标来自 back_project / world_map（推断：由仿真渲染的深度反投影得到） | 无探索期；回合内不复位 | 是（Apache-2.0，github.com/DAGroup-PKU/PyRUA-Lean；依赖 RPent 机器人栈和 Codex CLI 或 Claude Code）

---

## 3. Show-Harness（arXiv 2609.10522）【全文】

来源：
- arXiv HTML 全文 https://arxiv.org/html/2609.10522 。
- PDF https://arxiv.org/pdf/2609.10522 ，用于 Table 2 和图中数值。
- 仓库 github.com/showlab/Show-Harness（README）。
- HF 上的 showlab/Show-Harness-VLMs 和 showlab/Show-Harness-Data。

### 3.1 A 层卡片

- **问题**（§1）：
  - 基础 VLM 掌握了大量操作所需的知识，但这些知识很难转化成机器人行为。
  - VLA 把 VLM 拉去回归具身专属的连续动作，语义知识被压成不透明的映射，换任务、环境、本体时往往要重新适配。
  - 分层或程序化系统把物理控制交给下游控制器或系统专属机制，削弱了语义意图和物理执行之间的直接联系。
  - 论文要找的是：一个对 VLM 有语义、又足够细粒度、可以直接做物理控制的动作空间。
- **架构**（§3，Fig. 3，式 1–4，Table 1）：
  - 感知 → 推理 → 动作的循环。第 t 步的观测是 o_t = (多视角图像 I_t, 本体状态 p_t)。
  - 推理插件集合 P 把指令、观测和精简的交互历史整理成上下文 c_t（式 1）。
  - 中心 VLM 从离散语义动作集 A 中选出一个单元 a_t（式 2）。
  - 具身专属、与模型无关的解释器 g_E 把 a_t 确定性地落地成控制量 u_t（式 3）。
  - 动作单元（§3.2）：
    - MV_FWD / MV_BACK / MV_LEFT / MV_RIGHT / MV_UP / MV_DOWN：方向相对当前参考视角定义；
    - ROTATE_CW / ROTATE_CCW 配 x / y / z 轴；
    - GRASP、RELEASE、DONE；
    - 双臂时加 STILL（App 7.4.1）。
  - 解释器（式 4）：
    - 更新 6-DoF 笛卡尔位姿设定点，平移和旋转的增量都经过标定，坐标系映射是 R_E。
    - 投影 Π_E 强制工作空间限制和每步平移/旋转上限。
    - 夹爪单元不改位姿，直接映射为开/合命令。
    - 违反工作空间、桌面高度等安全边界的动作在执行前就被拦下。
    - 底层控制器：Franka 用阻抗控制跟踪笛卡尔设定点；AgileX 用 IK 加流式关节目标；仿真里用 OSC 命令。
  - 插件（Table 1）：
    - 感知：Multi-View Guidance、Proprioception。
    - 推理：Subtask Planning、Situated Planning、Action Chunking、Adaptive Step、Visual Prompt。
    - 动作：Action History、Failure Recovery。
  - 两种模式（§3.4）：
    - ZS：闭源前沿 VLM 直接控制，不微调；默认 Gemini-3.1 Pro，medium thinking。
    - FT：小型开源 VLM 用 LoRA 微调后直接输出动作单元，默认 Qwen3.5-2B，rank-64 LoRA，约更新 3% 参数。FT 只用最小上下文：指令、多视角图像、短动作历史（式 5）。
  - GUMI（§4）：基于 GUI 的采集界面。每个动作单元对应一个按钮或按键，人可以用键盘操作，computer-use agent 可以操作同一个 GUI。每步记录 (o_t, a_t)，同时保留对应的底层命令和轨迹。
- **什么在进化**：不进化。ZS 是纯推理时系统。FT 是一次性离线 LoRA 监督微调，部署时不更新。
- **改进信号**：
  - ZS：无。
  - FT：在 GUMI 示范上做 token 级交叉熵（式 5）。示范来自人用键盘操作，以及前沿 VLM 经浏览器界面的 rollout（§5.1）。
  - 其中 19 个 Franka 回合（594 步）是专门录的抓取恢复片段（App 7.2）。
  - 是否只保留成功回合：**未报告**。
- **接受门控**：无。解释器的安全边界是执行层的拦截，不是对更新的接受门。
- **关键结果**：
  - Table 2（真机；ZS 为 Gemini-3.1 Pro medium，FT 为 Qwen3.5-2B）：
    - Cross-Task（10 个任务 × 10 次）：π0.5 39.0，GR00T 35.0，H-VLA 50.0，G-VLA 13.0，CaP-X 44.0，RATS 57.0，**ZS 89.0**，FT 86.0。
      - 训练时没见过的物体：Teddy 和 Chess。ZS 在 Chess → Plate 和 Chess → Bowl 上都是 10/10，π0.5 都是 1/10。
      - ZS 最低的一项是 Banana → Bowl，6/10。
    - Cross-Environment（每个设置 20 次）：ZS 在背景、光照、视角、干扰物四个设置上都是 20/20，平均 100.0。FT 平均 88.0，含 sim-to-real 13/20。π0.5 平均 40.0，GR00T 34.0，两者的 sim-to-real 都是 0/20。
    - Cross-Embodiment（5 个 Plate 任务，每台臂每个任务 10 次）：ZS 93.0（Franka 48/50，AgileX 45/50），FT 87.0，π0.5 41.0，RATS 52.0，H-VLA 49.0。
  - 物理适应（§5.3.1，Fig. 6）：
    - 只把解释器步长从 2 cm 改成 1 cm：叠块和插销任务上 ZS 60% → 82%，FT 40% → 65%。π0.5 用同样示范只有 18%，加了细粒度训练数据后到 62%。
    - 旋转外推：每个旋转单元转 15°。FT 在训练没见过的 90° 上达到 70%，π0.5 是 20%。
    - 双臂联合决策：两个任务的成功率都提高，碰撞降为 0。读图：tidy table，单臂 80、各臂独立 70（1 次碰撞）、联合 90；pass banana，各臂独立 30（3 次碰撞）、联合 70。
  - 语义适应（§5.3.2）：
    - 藏块和拼 "SHOW" 两个任务：ZS 加 Situated Planning 达到 85%；FT 10%，用 Gemini 生成的子任务指令后到 70%；π0.5 0%，用同样指令后 5%。
    - 按视频示范的顺序收纳：ZS 有示范时 20/20，无示范时 20%。
  - VLM 选择（§5.4.1，Fig. 7）：
    - 超过 98% 的回复是合法的动作单元。
    - 提高思考档位主要减少步数，成功率提升不大，墙钟时间可能增加；例：GPT-5.6-sol 为 3.4×，论文没写和哪一档比。
    - Chess-cannon 任务的能力拆解（读图，依次为 Gemini-3.1 Pro / GPT-5.6-sol / Opus 5）：
      - 计划正确：95 / 85 / 80
      - 抓取精确：90 / 55 / 40
      - 放置正确：70 / 30 / 15
      - 给出坐标后的放置：80 / 45 / 35
      - 作者结论：错误集中在细粒度抓放，计划不是主要瓶颈。
  - 插件 leave-one-out（Fig. 9，Franka，ZS，5 个 Plate 任务，默认配置 96%；读图）：
    - 只用全局视角：58
    - 去掉本体状态：68
    - 去掉子任务规划：60
    - 去掉 Action History：76，步数从 30 变为 39
    - 去掉 Failure Recovery：72
    - Action Chunking：关掉时 96%、每回合 32 次调用；默认的选择性分块 96%、26 次（−19%）；始终分块 74%、20 次（−38%）。
    - Adaptive Step：只用细步长 82%、38 步；只用粗步长 70%、22 步；自适应 96%、30 步。
    - Visual Prompt：杯柄抓取 40 → 85，只加标记点是 35。
    - Situated Planning：藏块搜索 35 → 85。
  - 动作空间表示（§5.4.4，Fig. 10）：
    - (A) 语义名 + 约定：20/20，25 步
    - (B) 只有语义名：18/20，34 步
    - (C) 任意符号 + 约定：19/20，27 步
    - (D) 只有任意符号：1/20，49 步；推断出的映射只有 23.3% 正确
  - FT 规模（Fig. 8b，读图，Qwen3.5 0.8B / 2B / 9B）：
    - 叠块/插销：50 / 65 / 75
    - 网球：70 / 80 / 60
    - 延迟：27 / 39 / 79 ms
- **优点**（论文证据）：
  - 前沿 VLM 不经微调就在三类泛化上超过所有基线，FT 小模型也超过所有基线（Table 2）。
  - 换本体只需换解释器，模型面对的词表和 prompt 不变（§3.2）。
  - 精度需求通过解释器步长调整，不用重训（§5.3.1）。
  - 每个插件都做了消融（Fig. 9）。
  - sim-to-real 上，FT 只用仿真数据做到 13/20，两个 VLA 都是 0/20。
  - FT 训练成本低：单张 H200 不到 2 小时，24 GB 级显卡就能训（App 7.1）。
- **缺点与风险**：
  - 只在平行夹爪的单臂和双臂上测过，任务以抓放为主（§6）。
  - 每个任务只跑 10 次。
  - 成功由谁判定、真机怎么复位：**未报告**。
  - Agentic 基线（H-VLA、G-VLA、CaP-X、RATS）各用的是哪个 VLM、什么配置：**未报告**。
  - ZS 对比的 π0.5 / GR00T 是在同一批示范上微调过的；Cross-Environment 各方法的平均分母不一样（见 §1 的 S6）。
  - 错误集中在细粒度抓取和放置（Fig. 7b）。
  - 每回合约 30 步、每回合约 26 次前沿 VLM 调用；每步的墙钟时间和 token 都**未报告**。
  - 始终分块会掉到 74%，说明开环分块只适合离目标较远的阶段（Fig. 9iv）。
  - Situated Planning 和 Visual Prompt 是"按需启用"（§5.1），由谁决定启用：**未报告**；推断是实验者按场景手动开关。
- **可复用的模块**（列事实）：
  - 语义动作词表，加上具身专属解释器：增量设定点、投影 Π_E、安全边界拦截（式 4）。
  - 插件化的 prompt 注入：每个插件一个开关，仓库称关闭时与没有该插件"字节级一致"（README）。
  - Planner 的 JSON 子目标模式：id / target / affordance / motion / description / completion，要求完成条件"能仅从 2D 图像判断"（App 7.4.1）。
  - Controller 的模板：STAGE / TARGET / AFFORD / DONE WHEN 字段，加上按视角选方向的规则。
  - 按腕部相机是否看见目标来切换步长（2 cm / 4 cm）和是否分块。
  - 恢复提示片段：空抓、抓取丢失、抓取未确认、夹爪开度未稳定。
  - 下降停滞提示：拿实测位移和指令位移比较。
  - 反振荡规则。
  - GUMI 采集界面。
  - 示范视频分析 prompt（DemoVideoAnalyst）。
- **工程成熟度**：
  - 开源：是。github.com/showlab/Show-Harness，Apache-2.0，2026-09-07 创建。仓库包含 core / interpreters / plugins / prompts / gumi / train。
  - HF 上放了 6 个 LoRA 适配器（Show-Harness-VLMs）和示范数据集（Show-Harness-Data）。
  - VLM：
    - ZS 默认 Gemini-3.1 Pro；还测了 GPT-5.6-sol、GPT-5.6-luna、Opus 5、Gemini-3.6 Flash。
    - FT 默认 Qwen3.5-2B；还测了 0.8B / 9B 和 InternVL 系列。
    - 本地模型跑在单张 RTX 5090 上。
  - **不用 VLA**。π0.5 和 GR00T 只是基线。
  - 仿真器：ManiSkill 和 RoboLab 只用来采 sim-to-real 训练数据。README 另称支持 Isaac Lab 和 LIBERO，但论文里没有这些平台的结果。
  - 读仿真真值：否，评测全部在真机上。

### 3.2 VLM 与 VLA 的接口

（Show-Harness 里没有 VLA。下面的"底层"指具身解释器加上各本体的控制器。）

1. **VLM 的输出是什么**：
   - Controller 角色：每次调用输出**一个离散语义动作单元**，格式为 JSON `{"decision": "ONE_ACTION", "reasoning": "one visual sentence"}`。prompt 要求"想一句视觉描述，然后提交"（App 7.4.1）。FT 模式只输出一个动作 token（App 7.4.2）。
   - 分块时：目标还远（腕部视角里看不到）时，输出 `PLAN: M1, M2, ...`，只允许 MV_ 类单元，几步动作开环执行（§3.3，App 7.4.1）。
   - Planner 角色：开局输出 JSON 子目标列表，字段是 id、target、affordance、motion、description、completion（App 7.4.1）。
   - 其他角色：Visual Prompt 用一次专门的模型调用，把含糊的目标转成视觉标记（§3.3）；视频分析角色把示范视频转成文字形式的操作大纲（App 7.4.1）。
   - 不输出末端目标坐标，也不输出代码。
2. **VLA 的输入是什么**：不用 VLA。底层的情况：
   - 解释器 g_E 接收动作单元，按式 4 更新 6-DoF 位姿设定点。平移增量：默认 2 cm（腕部能看见目标）或 4 cm（看不见），细粒度任务用 1 cm。旋转每单元 15°（§3.2，§5.1，§5.3.1）。
   - Π_E 截断到工作空间和每步上限，违反安全边界的动作在执行前被拦下。
   - 夹爪单元直接映射为开/合命令。
   - 控制器：Franka 用笛卡尔阻抗控制，AgileX 用 IK 加流式关节目标，仿真用 OSC（§3.2）。
   - 底层不接收语言、记忆或历史。本体状态被翻译成文字后放进 **VLM** 的 prompt，不进控制器（Table 1）。
3. **时域**：
   - Controller 默认**每个单元调用一次**。
   - Action Chunking 打开时，只有"WRIST: NO"（目标在远处）的阶段一次给出 {step_num} 步并开环执行；"WRIST: YES" 时恢复单步（App 7.4.1）。{step_num} 的取值**未报告**。
   - Planner 在开局调用一次。Situated Planning 在需要的信息变得可观测时，有选择地重新规划（§3.3）。
   - 量级（Fig. 9，Plate 任务，读图）：默认配置约 30 步/回合、26 次 controller 调用/回合；不分块时 32 次。回合上限 50 步（§5.1）。
   - 底层每执行一个单元就回到 VLM，开环分块时是执行完一块再回到 VLM。每个单元执行多长时间、控制器跑多少个周期，**未报告**。
4. **停止条件**：
   - 单个单元的位移是有界的，由解释器确定性地执行，本身就是"走一步就停"。分块在执行完计划的几步后结束。
   - Proprioception 插件注入下降停滞提示："Last MV_DOWN lowered x of y cm → already in contact, do NOT MV_DOWN again"（App 7.4.1）。
   - 夹爪开度没稳定时，注入"等待后再动"（recovery/note_unsettled）。
   - 回合层面：VLM 输出 DONE 时结束，prompt 规定"DONE WHEN 已经在图像里可见"才允许输出 DONE；或者到 50 步上限，超时算失败（§5.1）。
5. **验证者**：
   - **子任务完成由 VLM 自己判定**："subtask transitions belong to the model"。每一步它都拿 planner 写的可视完成条件和当前图像对照，没满足就继续动作，满足了就宣告子任务完成（§3.3）。
   - Planner prompt 要求完成条件"必须能仅从原始 2D 图像判断"，并且移动类阶段要以稳定的空间关系结束，不能以夹爪事件结束（App 7.4.1）。
   - 抓取的确认：prompt 要求 AgentView 和腕部视角都确认后才 GRASP。Failure Recovery 插件自动检测抓空（§3.3，§5.4.3）。检测信号论文正文没写；推断是夹爪开度，依据是恢复提示里有 "width and images show a real hold" 和 "Closed width is unsettled"。
   - VLM 自己宣告的 DONE 会结束回合。实验成功率由谁、看什么判定：**未报告**。
6. **失败后的处理**：
   - Failure Recovery："resetting the gripper state and rolling back to the relevant grasp subtask"，即张开夹爪，计划状态退回抓取子任务（§3.3）。
   - 注入的恢复提示（App 7.4.1）：
     - 抓空："不要在边或角上再试，把目标重新放到中间，再确认深度"；
     - 抓取丢失："回到 GRASP 阶段"。
   - Action History 规则：出现 GRASP(empty) 后不要原地再抓，优先 MV_UP / BACK / DOWN / FWD；不要选和上一步相反的方向。
   - 没有"接管"：VLM 本来就逐步控制，没有别的执行器可以交出去或接回来。
   - FT 模式靠训练数据里 19 个抓取恢复片段学恢复行为：抓空后先抬起再重新接近（App 7.2）。
7. **交还**：不适用，因为没有 VLA 或技能执行器。唯一的"模式切换"是分块和单步之间：腕部视角里看到目标后，回到单步（App 7.4.1）。
8. **记忆**：
   - 回合内：
     - Action History，最近 5 个动作，新的在前，带 GRASP(empty) 之类的标注（§5.1，App 7.4.1）；
     - 子任务计划的状态：当前 STAGE / TARGET / AFFORD / DONE WHEN；
     - 恢复提示。
   - (D) 设置里，agent 记下自己推断出的符号映射，"for later use"（§5.4.4）。这份映射是只在回合内用，还是跨回合保留，论文**未报告**。
   - 跨回合：**未报告**，没有描述任何跨回合记忆。
   - 视频示范被转成文字大纲，插进 planner prompt。这是每个任务的条件输入，不是持久记忆（App 7.4.1）。
9. **成本**：
   - token 和美元：**未报告**。
   - 报告了：
     - 每回合步数（Fig. 7a、Fig. 9）；
     - Plate 任务上每回合的模型调用次数：32 / 26 / 20（Fig. 9iv）；
     - 相对墙钟：思考档位提高后最多 3.4×（GPT-5.6-sol）；
     - FT 模型延迟：27 / 39 / 79 ms（0.8B / 2B / 9B，Fig. 8b 读图）。这个延迟是否就是每次决策的延迟，论文没写明。
   - 前沿 VLM 每步的 API 延迟：**未报告**。

### 3.3 模块行

模块行：Show-Harness | ZS：Gemini-3.1 Pro（medium thinking），同一个 VLM 兼任 planner（开局调用一次，Situated Planning 时有选择地重新规划）和 controller（每个单元或每个开环块调用一次）；FT：Qwen3.5-2B + LoRA，只做 controller，没有独立的 planner | 原语：离散语义动作单元（MV_FWD/BACK/LEFT/RIGHT/UP/DOWN、ROTATE_CW/CCW 加轴、GRASP、RELEASE、DONE，双臂加 STILL）→ 具身专属的确定性解释器（2/4/1 cm、15°，带工作空间投影和安全拦截）→ 阻抗控制 / IK / OSC；不用 VLA | 回合内：最近 5 个动作（文本，新的在前）+ 子任务计划 JSON（stage、target、affordance、completion）+ 恢复提示；跨回合：未报告 | 在线：VLM 每步对照计划中的可视完成条件自行判断；Failure Recovery 插件自动检测抓空（信号推断为夹爪开度）；事后：成功判定方式未报告 | Failure Recovery 插件（抓空、抓取丢失、抓取未确认、开度未稳定）+ Proprioception 的下降停滞提示（实测位移和指令位移比较）+ Action History 的反振荡规则 | 无；固定动作词表 + 插件 prompt 片段 | 无（不进化） | 无版本；失败时计划状态退回抓取子任务（不是版本回滚） | 每任务 10 次，物体位置随机；回合上限 50 步，超时算失败；复位方式和成功判定者未报告 | 报告了每回合步数、模型调用次数（32/26/20）、相对墙钟（3.4×）和 FT 模型延迟（ms）；没有 token 和美元 | 否（真机评测；仿真只用来采 sim-to-real 训练数据） | 无探索期；(D) 消融里 agent 试探未知符号、记下映射（是否跨回合未报告），无复位 | 是（Apache-2.0，github.com/showlab/Show-Harness；HF 上有 LoRA 权重和示范数据）

---

## 4. 九个接口问题的并列速查（只列事实，出处见上面两节）

| 问题 | PyRUA-Lean | Show-Harness |
|---|---|---|
| 1 VLM 输出 | Python 代码 cell：末端坐标、VLA 语言指令、条件分支、重试、几何计算、取图请求 | 一个离散动作单元（目标远时为几个单元的开环块）；开局输出子目标 JSON |
| 2 底层输入 | 冻结 VLA 作为函数被调用，指令约一句话、由 VLM 现写；不传记忆；另有经典运动原语 | 无 VLA；解释器把单元变成有界的位姿增量，再交给阻抗控制 / IK / OSC |
| 3 时域 | 每个 cell 调用一次 VLM；平均 2.1 个动作原语/cell；35% 的 cell 走一步就看；回报点由 VLM 写的代码决定 | 每个单元调用一次（远处时每块调用一次）；约 30 步/回合、26 次调用/回合 |
| 4 停止 | 原语自带参数（tol、max_steps、max_chunks）+ 代码条件 + 成功守卫 / watchdog / 调用预算 | 单元本身有界；VLM 输出 DONE；50 步上限；停滞和开度提示 |
| 5 验证 | 原语返回值 + 代码断言 + **仿真成功谓词 robo.done** + VLM 自选图像；最终按基准判定 | VLM 每步对照可视完成条件自判；插件检测抓空；最终判定方式未报告 |
| 6 失败处理 | cell 内重试或分支；异常返回 traceback；VLM 用经典原语写脚本恢复，取代 VLA | 夹爪复位 + 退回抓取子任务 + 恢复提示；没有接管概念 |
| 7 交还 | 没有形式化条件，由 VLM 在后续 cell 自行决定是否再调 VLA | 不适用；分块和单步之间按腕部视角是否看见目标切换 |
| 8 记忆 | 回合内：命名空间 + 完整对话；跨回合：关闭 | 回合内：最近 5 个动作 + 计划状态；跨回合：未报告 |
| 9 成本 | 调用次数、输入 token（含缓存）、美元；不计延迟 | 步数、调用次数、相对墙钟、FT 延迟；不计 token 和美元 |
