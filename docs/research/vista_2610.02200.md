# VISTA: A Visual Harness for Reasoning in an Interactive World（arXiv 2610.02200v1）

作者 Qiushi Han、Keya Hu、Linlu Qiu（三人为共同一作）、Cathy Wu、Kaiming He，五人同属 MIT。v1 是唯一版本，2026-10-01 提交，主类 cs.AI，交叉 cs.CV，PDF 23 页。arXiv 备注称其为技术报告，早期版本 2026-08-05 以博客形式发在 vista-research.github.io。论文本身 CC BY 4.0。代码在 github.com/joshhhhhan/VISTA，MIT 许可；这一点只来自 README 徽章、LICENSE 文件（版权人写作 VISTA contributors）和 GitHub API 的许可字段，论文正文没写代码许可。来源：arXiv 摘要页、HTML 全文（按字符偏移分段读到末尾；旧卡只读到 10 万字符处）、PDF，以及仓库 README 和 LICENSE，均于 2026-10-11 读。三位读者分段读（大致是前部、ARC-AGI-3 实验、尾部；范围有重叠，附录 A.1–A.5 和 Tab. 2 都至少有两位读者读过），每位读者之后由一位严格核查员逐条对原文。本卡只用核查通过的内容：9 条被更正的声明按更正后的版本写入，没有被驳回的声明，详见文末"核查记录"；之后又做了一轮终审，改动列在核查记录末尾。标"推算"的数字是写卡人用论文里的数算出来的，论文没写。

## 1 一句话

给通用多模态模型套一层不训练、纯机械的 harness：观测直接给渲染图；环境返回的每一帧（含一个动作产生的动画中间帧）按 turn 和 frame 编号原样存着；每走一步之前，模型可以不耗游戏动作地多次调用 inspect 回看任意旧帧、放大任意区域，用 read_pixels 读精确像素值，最后用 play 走一步。ARC-AGI-3 的 25 个公开游戏上，Claude Opus 5.0 从官方实现的 40.68（high）到 100.00（xhigh），共 7,302 个动作，比首次游玩的人类参考 17,135 少 57.4%；GPT-5.6 Sol 从 13.33 到 99.00（Tab. 1；§4.2）。GPT-5.6 Sol 的累加消融里，可直接比较的几步中最大的一步是加无损记忆和 inspect（同时改成每步只自动给最后一帧），70.05 → 94.10（Fig. 5）。同一 harness 用于 GameWorld、AI GameStore、BabyVision（只测了 GPT-5.6 Sol），都高于同模型的最小 harness 基线（Fig. 11）。**非具身**：ARC-AGI-3 是回合制，游戏只经 play 推进、控制器串行，环境等模型想完再走（App. A.4）；论文里未见连续控制或思考时间受限的实验，§6–7 也没谈。

## 2 问题设定

- **论点**（Abstract 首句；§1）：多模态模型本来就有很强的推理能力，合适的 harness 能在多种交互环境里把它释放出来。
- **定位**（§1 第 2–3 段；§2）：ARC-AGI-3 通常被当成语言问题：给 LLM 文本或结构化状态，或者用代码方法把环境写成可执行的符号程序（Schema、ewma sv、Retrodict、Tycho）。VISTA 把它重新当成视觉问题，让多模态模型直接看图。作者认为，环境视觉上越复杂，显式写出状态、转移和目标条件的程序可能越难。
- **动机**（§1 第 3 段；§3 引言）：VLM 通常把一张图只编码一次，这次编码未必保留推理后来需要的细节；上下文快满时，旧观测可能被删掉或换成文字摘要，留下什么未必由模型决定；最要紧的是，编码那一刻模型并不知道哪些视觉信息以后会有用。VISTA 的回答是保留原始观测，并给模型取回和细看的工具，相关信息就不必在第一次编码时抓住、也不必一直留在上下文里。
- **与此前游戏 agent 的区别**（§2）：此前的 agent 通常只保留有界的视觉上下文，或者有选择地存储、压缩过去的经验；VISTA 无损保留完整的视觉交互历史，允许回看任意旧帧和区域。
- **任务 ARC-AGI-3**（§4.1）：规则和目标都不明示；评 25 个公开游戏。每一步看当前画面、选一个可用动作，再从结果推断物体角色、转移规则、隐藏状态和可能的目标。每个游戏有多关，各关共享机制但会加新概念、物体、布局或约束，所以要跨关保留并运用知识。动作集是 RESET 和 ACTION1–7，其中 ACTION6 是在 (x, y) 点击（App. A.4）；其余动作的含义、调色板大小、每个游戏的关数未查到。官方帧为 64×64（§4.3）。
- **预算**（App. A.1–A.2）：每局动作上限 Opus 5.0 与 GPT-5.6 Sol 为 2,000，GLM-5.3 Flash 320B 为 3,000；VISTA 协议每局 48 h。官方实现原本按每段动作不超过人类基线 5 倍、运行不超过 12 h 停止。
- **模型**（§4.1；App. A.1）：Claude Opus 5.0（xhigh，经 Claude Code CLI）和 GPT-5.6 Sol（max，经 Codex CLI），两者在同一协议下各自独立评测；开源的 GLM-5.3 Flash 320B 也走 Codex CLI，以 GLM 作模型后端。作者自己的运行都经订阅制 CLI 访问模型，不直接调 API（A.1）；组织方发表的官方基线经 API（A.2）。GLM 的推理强度和上下文上限未查到。
- **人类参考**（App. A.6）：来自组织方对 486 名普通公众的研究，未按专门训练或技能筛选。每个游戏由 10 名首次接触的人测试，不给任务说明；每场 90 分钟，单个游戏软限 20 分钟、硬限 30 分钟。至少 2 人独立通关全部关卡的游戏才收录。每关的人类基线取首次通关者动作数的上中位数。
- **官方基线**（App. A.1）：每个观测给文字网格加可用动作列表；一个动作产生多帧时最多保留 7 个中间状态；上下文窗口 175K token，超出就丢最早的 turn，因此没有长期记忆。

## 3 机制

### 3.1 部件与工具（§3.1，Fig. 3；App. A.4 Algorithm 1）

| 部件 | 内容 | 出处 |
|---|---|---|
| 视觉观测 | 直接给渲染图，保留物体的外观和空间关系，模型可以用上已学到的视觉先验。Fig. 3(a) 对比：64×64 文字网格里一格是一个数字，渲染图里一格是一个像素 | §3.1；Fig. 3(a) |
| 无损视觉记忆 | 环境返回的每一帧都按原样存下，包括动画中间帧；按 turn 和 frame 编号，整局都可取，与模型当前上下文里还剩什么无关。Fig. 3(b)：每帧全分辨率保存 | §3.1；Fig. 3(b) |
| 视觉 inspect | 空间上，取原帧任意矩形区域的放大图；时间上，取任意早先 turn 的多帧作比较，或回放一个动作经过的中间帧。每个 turn 看不看、看什么由模型自己决定 | §3.1；Fig. 3(c) |

作者把三者合起来称为 "an explicit attention mechanism over the interaction history"（§3.1）。harness 本身是纯机械的：不含任何训练过的模型，只执行模型的工具调用、返回图或像素值，对无效请求报错（§3.2）。工具只能经结构化函数调用触发，模型输出的普通文本触发不了工具（App. A.4）。

| 组 | 签名（Algorithm 1） | 作用 | 改什么 |
|---|---|---|---|
| Action | `play(action, x=None, y=None) → (frame, n_frames, available_actions)` | 恰好执行一个游戏动作。动作集随基准而定：ARC-AGI-3 为 RESET、ACTION1..7（ACTION6 为点击 (x, y)）；GameWorld 和 AI GameStore 用各自的输入，prompt 里改名为 act。只返回最后一帧，中间帧全部归档 | **唯一**改变环境的工具 |
| Visual memory | `inspect(question, views) → images` | question 是这些视图要回答的视觉问题。每个 view 为 `{label, turn, frame=final, region=full}`，region 为 `{x, y, w, h}`；每个 view 返回一张图。可取任意归档帧，裁剪后放大 | 只读 |
| Visual memory | `read_pixels(question, views) → (palette, samples[])` | 参数同 inspect；把区域切成 rows × columns 个格，以数字列表返回每格的像素值 | 只读 |
| History | `history(view, start_turn=None, end_turn=None, limit=64) → records[]` | `view="events"`：[start, end) 内的精确动作和结果记录；`view="attempts"`：本关每次尝试一条摘要。一次尝试从进关或 RESET 开始，到 GAME_OVER、WIN 或 TERMINAL 结束 | 只读 |
| Notes | `read_guide() → text`；`write_guide(content)` | GUIDE.md：跨关、跨会话可复用的游戏知识 | read 只读；write 只改笔记 |
| Notes | `read_working() → text`；`write_working(content)` | WORKING.md：本关的临时状态和计划；RESET 和上下文重启后保留，升关时清空 | read 只读；write 只改笔记 |
| Notes | `save_compact_checkpoint(guide, working_memory)` | 原子保存压缩后续跑所需的状态：WORKING.md 写入 working_memory；guide 非空才写 GUIDE.md，否则不动 | 只改笔记 |

**Algorithm 1 的几处细节**（App. A.4）：

- 写笔记是整文件替换，没有追加或补丁操作。
- read_pixels 的签名里没有 rows/columns 参数，palette 的格式也没定义（未查到）；它的伪代码注释和 inspect 的一样，写"每个 view 返回一张图"，和返回值 (palette, samples[]) 对不上。
- inspect 的放大倍数或输出分辨率、一次调用最多几个 view，论文都没给（未查到）。Fig. 10b 的约 13× 只是一个例子，Fig. 14 的 BabyVision 例子里是 2× 和 4×。
- history 只出现在 Algorithm 1 和 README；§3 正文举例时只点了 inspect、read_pixels、play。
- 回合循环本身没有伪代码，Algorithm 1 只覆盖工具接口；循环见 §3.2 正文和 Fig. 3(d)。
- §3.2 指向 A.4 的伪代码和说明；agent 实际收到的工具说明文字是否就是这些，未查到。act 的签名没给，Fig. 2 只有 `act W+D 0.5 s` 这类例子。
- history 在 GameWorld、AI GameStore、BabyVision 上是否开放，未查到。笔记在 GameWorld、AI GameStore 上照用（Prompt 2 要求用 GUIDE.md 和 WORKING.md；Fig. 14 的 Minecraft 例子在 turn 5 写了 GUIDE.md），BabyVision 去掉了持久笔记（App. A.5）。

### 3.2 每个 turn（§3.2；Fig. 3(d)）

一个 agent 从头玩到尾。一个 game turn 里可以和 harness 来回多次，最后恰好执行一个游戏动作（§3.2）。

1. **观察**：当前帧和可用动作。
2. **（可选）读或改笔记**：Fig. 3(d) 把 GUIDE.md 标为游戏的持久规则，WORKING.md 标为本关草稿。
3. **（可选）inspect / read_pixels**：不耗游戏动作。Fig. 2 和 Fig. 14 的轨迹里，inspect 与 read_pixels 都标为不耗动作，每个 play 或 act 记 1 个游戏动作。
4. **推理**：自由形式的自然语言，提出假设、预测这一步的结果、做计划，并修改笔记。
5. **行动**：play 执行一个动作，Fig. 3(d) 称之为 "the only step that costs an action"。返回的最后一帧成为下一 turn 的观测。

看不看笔记、用不用工具都由 agent 决定（§3.2）。Prompt 1 要求每次 play 之前说出预期，之后列出全部可见变化，不论是否在预期之内（App. A.5）。

例子（Fig. 2 左，第 3/9 关，turn 67，规则和目标都没给）：一次点击 play 计 1 个游戏动作，返回 6 帧，归档为 t67 f0–f5，最后一帧成为当前观测；接着一次 inspect 同一区域在 t66 f27、t67 f0、t67 f2、t67 f5 的样子，不计动作；到 turn 79，agent 把推断出的机制写进 GUIDE.md。

### 3.3 控制器规则（App. A.4）

- 一个宿主侧控制器处理全部工具调用。它持有环境连接、归档帧、GUIDE.md 和 WORKING.md，是唯一能推进游戏的组件。
- 串行：上一个动作的结果交还给 agent 之后，才接受新的 play。
- 每张返回的图都标上 turn 和 frame 编号。
- 隔离：Claude Code 下，工具经 MCP server 暴露，所有内置工具关闭，agent 没有 shell 和文件系统；Codex 下，同一组工具直接注册到 app server。两种运行时都在沙箱容器里，拿不到环境凭证。
- 对无效请求报错（§3.2）；具体错误信息，以及被串行规则拒绝的 play 会怎样，未查到。

### 3.4 笔记与上下文交接（§3.2；App. A.2 (iv)；App. A.4）

- GUIDE.md 存 agent 对游戏的理解和跨关可能仍有用的信息；WORKING.md 是当前状态的草稿（§3.2）。
- 上下文快满时，控制器暂停普通工具调用，直到 agent 存好续跑所需的状态：Codex 下 agent 调 save_compact_checkpoint；Claude Code 下由一个压缩前 hook 要求它调 write_working。然后在新上下文里续跑，新上下文含 GUIDE.md、WORKING.md、动作历史和当前帧（A.4）。§3.2 补充说视觉记忆也一直可用。
- 消融 (iii) 用的是模型原生压缩，保留旧 turn 的压缩表示；到 (iv) 才改成写交接摘要进 WORKING.md、开新上下文（App. A.2）。
- 触发阈值（"接近上限"具体是多少）、交接摘要的内容和格式、hook 发出的确切消息、Codex 原生压缩与 save_compact_checkpoint 怎么配合，都未查到。

### 3.5 图怎么送到不同 agent

| 情形 | 图怎么到模型 | 出处 |
|---|---|---|
| Claude Code（Opus 5.0） | 图直接放在 MCP 工具结果里 | App. A.4 |
| Codex（GPT-5.6 Sol） | Codex 的工具输出只能是文本，控制器在同一个 turn 里另外送图。GLM 也走 Codex CLI，按理同此（我们的推断，论文没单独说） | App. A.4；A.1 |
| 每步自动给的图 | (v)–(vi)（(vi) 即完整 VISTA）：每个动作后只自动给最后一帧；(i)–(iv) 照官方协议，每个动作自动给至多 7 帧 | Fig. 5 caption；A.2 |
| 图的格式 | 消融默认 512×512 PNG，由 64×64 官方帧 8× 最近邻放大，每帧约 308 image token；Opus 主实验的分辨率没有单独写（未查到） | §4.3 |
| 官方 ARC-AGI-3 基线 | 不给图，给文字网格 | A.1 |
| GameWorld 官方基线 | 截图；每次请求带前两步的截图、推理和动作 | A.1 |
| AI GameStore 官方基线 | 每个游戏秒决策一次，附 0.2 s 间隔采的 5 帧 | A.1 |
| BabyVision 官方基线 | 图和问题一次给全，一次作答 | A.1 |

### 3.6 Prompt（App. A.5）

- 所有基准共用一个短模板，只在动作空间、任务目标和笔记三处按基准改。ARC-AGI-3 的所有游戏用同一个 Prompt 1。
- **Prompt 1（ARC-AGI-3）**：用尽量少的动作通关；建立并使用一个紧凑、可修订的游戏与状态模型，随证据更新；play 前说预期，play 后说全部可见变化；在 GUIDE.md 里保留简洁、持久、可修订的理解，需要时用 WORKING.md 作草稿。它没有点名 inspect、read_pixels 或 history。
- **Prompt 2（GameWorld、AI GameStore 共用）**：与 Prompt 1 同构，措辞对环境中立，追求给定的目标，play 换成 act。GameWorld 的系统提示另外原样附上基准自己的任务提示，里面写明每个任务的目标。
- **Prompt 3（BabyVision）**：更短，没有持久笔记，因为题目彼此独立、都是单张静态图。要求作答前先收集题目真正需要的证据：细节要紧处用 inspect 放大，判断依赖颜色时用 read_pixels 测精确颜色；每看一个视图先说它要解决什么，再检查解决了没有；保留一段简短、可修订的图像描述；证据只支持一个答案时才作答。

### 3.7 发布的代码（README）

- CLI 名为 vista，四个 profile：arc3、gameworld、aigamestore、babyvision；两个后端：codex、claude。ARC-AGI-3 有 online、competition 两种模式，经 ENVIRONMENTS_DIR 提供本地游戏文件时还有 offline。
- 环境要求：Linux x86_64、Python 3.12、Docker Engine，以及 Codex CLI 0.145.0 或 Claude Code 2.1.220；ARC-AGI-3 批量跑需要 tmux，在线和竞赛模式需要 ARC Prize API key（ARC_API_KEY，ARC_BASE_URL=https://three.arcprize.org）。Claude Code 用 Claude Code 2.1.220 的 setup-token 命令生成 CLAUDE_CODE_OAUTH_TOKEN，写进 .env。每个运行时一个 Docker 镜像：arc3-codex-player:0.1（Dockerfile.codex-player）、arc3-claude-player:0.1（Dockerfile.claude-player）。
- README 示例：Codex 用 `--model gpt-5.6-sol --effort max`，Claude 用 `--model opus --effort xhigh`，批量脚本 `-j 2`。GameWorld、AI GameStore、BabyVision 的示例只给了 codex 后端。
- 批量脚本覆盖 GameWorld 全部 170 个任务（34 个游戏）、AI GameStore 全部 10 个游戏、BabyVision 的 39 道 Maze 与 Connect the Lines 题；环境安装脚本在 scripts/env_setup/。
- README 链了两张 ARC Prize 成绩卡：Codex CLI / GPT-5.6 Sol / max 为 RHAE 99，Claude Code / Opus 5.0 / xhigh 为 RHAE 100。
- README 列的工具与 Algorithm 1 一致：play、inspect、read_pixels、history，加 GUIDE.md、WORKING.md 两份笔记。
- README 没提发布运行日志、轨迹或逐游戏输出（未查到）。BibTeX key 为 han2026vista。

## 4 主要数字（ARC-AGI-3）

### 4.1 按模型：官方实现 vs VISTA（Tab. 1；§4.2；Tab. 2；Fig. 8）

| 模型 | 官方实现（不用程序） | VISTA | 差（推算） | VISTA 总动作 | 对人类参考 17,135 |
|---|---|---|---|---|---|
| Claude Opus 5.0 | 40.68（high；†由官方公布的 25 个公开游戏逐游戏分算出） | **100.00**（xhigh） | +59.32 | 7,302 | 0.43×，少 57.4% |
| GPT-5.6 Sol | 13.33（max；10,619 动作，完成 4% 的游戏，Fig. 5） | **99.00**（max） | +85.67 | 9,126 | 0.53×，少 46.7%（推算） |
| GLM-5.3 Flash 320B | 1.89（作者用原代码库跑的官方实现） | **66.93** | +65.04 | 未查到 | 未查到 |

- Opus 这一对的推理强度不同：官方基线 high，VISTA xhigh（Tab. 1）。官方 Opus 基线的动作数未查到。
- 消融说明里，官方实现经 API 访问，VISTA 的消融经 Codex CLI 订阅，所以官方分只作外部参照（App. A.2）。
- 作者声称，就他们所知，VISTA 是第一个不用程序合成、在 ARC-AGI-3 上达到满分或接近满分的系统（Tab. 1 caption；§4.2）；§1 的措辞是"第一个基于视觉的系统"。

Tab. 1 全表（25 个公开游戏）：

| 系统 | 用程序 | 模型 | 推理强度 | RHAE |
|---|---|---|---|---|
| 官方实现 | 否 | GPT-5.6 Sol | max | 13.33 |
| 官方实现 | 否 | Opus 5.0† | high | 40.68 |
| Schema | 是 | GPT-5.6 Sol | xhigh → max | 95.35 |
| Schema | 是 | Opus 4.8 → Fable 5 | max | 98.98 |
| ewma_sv_v1.6 | 是 | GPT-5.6 Sol | xhigh | 98.97 |
| Retrodict | 是 | GPT-5.6 Sol | max | 99.86 |
| Tycho | 是 | GPT-5.6 Sol | max | 100.00 |
| Tycho | 是 | Opus 5.0 | xhigh | 100.00 |
| **VISTA** | 否 | GPT-5.6 Sol | max | 99.00 |
| **VISTA** | 否 | Opus 5.0 | xhigh | 100.00 |

§4.2 说对比的系统大多是同期工作，它们通常把观测表示成文本数字网格，并常用代码世界模型（agent 把环境抽象成可在探索中执行、检查的符号程序）。Tycho 两行都是 100.00，所以 VISTA 在分数上并不独占满分，区别在于不用程序（我们的读法）。

### 4.2 RHAE 定义（§4.1；App. A.3 Eq. (1)–(2)）

- **每关**：S_{ℓ,g} = min(1.15, (h_{ℓ,g} / a_{ℓ,g})²)。a 是 agent 在该关的动作数，h 是首次通关人类动作数的上中位数；未通关为 0。§4.1 的文字说法：动作数等于人类基线得 100%，更少最多得 115%，更多得部分分。
- **每局**：关的权重 w_ℓ = ℓ，后面的关更重。通了前 k 关（共 n 关）时，S_g = min(Σ_{ℓ=1..k} w_ℓ / Σ_{ℓ=1..n} w_ℓ，Σ_{ℓ=1..n} w_ℓ S_{ℓ,g} / Σ_{ℓ=1..n} w_ℓ)。后一项是关分的加权平均，前一项把局分封顶在加权通关比例。
- **报告值**：25 个公开游戏的 S_g 取均值再乘 100；另报总动作数，用来区分同分运行的效率。用官方 ARC-AGI-3 评分器计算。
- **推算出的性质**：全部通关时前一项为 1，所以单局最高 1、总分最高 100；单关最高 1.15，可以抵消别的关比人慢，因此 100.00 不等于每一关都不比人慢。分数到顶以后，效率差异只能看总动作数。

### 4.3 逐游戏（App. B.2 Tab. 2）

人类动作 = 该游戏各关人类基线（首次通关者上中位数）之和；比 = agent 总动作 ÷ 人类动作。Opus 5.0 每局都是 100.00，表中只列动作。

| 游戏 | 人类 | Opus 5.0 动作（比） | GPT-5.6 Sol RHAE / 动作（比） |
|---|---|---|---|
| ar25 | 748 | 256（0.34×） | 100.00 / 260（0.35×） |
| bp35 | 651 | 521（0.80×） | 100.00 / 534（0.82×） |
| cd82 | 171 | 112（0.65×） | 100.00 / 100（0.58×） |
| cn04 | 789 | 225（0.29×） | 100.00 / 200（0.25×） |
| dc22 | 1,228 | 547（0.45×） | 100.00 / 560（0.46×） |
| ft09 | 208 | 75（0.36×） | 100.00 / 77（0.37×） |
| g50t | 879 | 338（0.38×） | 100.00 / 486（0.55×） |
| ka59 | 730 | 303（0.42×） | 100.00 / 396（0.54×） |
| lf52 | 1,339 | 1,008（0.75×） | 100.00 / 936（0.70×） |
| lp85 | 388 | 104（0.27×） | 100.00 / 97（0.25×） |
| **ls20** | 776 | 458（0.59×） | **75.03** / 1,135（**1.46×**） |
| m0r0 | 1,107 | 219（0.20×） | 100.00 / 224（0.20×） |
| r11l | 233 | 75（0.32×） | 100.00 / 93（0.40×） |
| re86 | 1,255 | 628（0.50×） | 100.00 / 582（0.46×） |
| s5i5 | 638 | 254（0.40×） | 100.00 / 293（0.46×） |
| sb26 | 213 | 125（0.59×） | 100.00 / 124（0.58×） |
| sc25 | 350 | 153（0.44×） | 100.00 / 228（0.65×） |
| sk48 | 1,070 | 413（0.39×） | 100.00 / 605（0.57×） |
| sp80 | 518 | 119（0.23×） | 100.00 / 184（0.36×） |
| su15 | 361 | 99（0.27×） | 100.00 / 123（0.34×） |
| tn36 | 317 | 143（0.45×） | 100.00 / 225（0.71×） |
| tr87 | 414 | 140（0.34×） | 100.00 / 209（0.50×） |
| tu93 | 462 | 193（0.42×） | 100.00 / 197（0.43×） |
| vc33 | 447 | 182（0.41×） | 100.00 / 272（0.61×） |
| wa30 | 1,843 | 612（0.33×） | 100.00 / 986（0.53×） |
| **合计** | 17,135 | 7,302（0.43×） | 99.00 / 9,126（0.53×） |

- ls20 是两个模型里唯一低于 100 的游戏，也是唯一比值大于 1 的；GPT-5.6 Sol 均值 (24×100 + 75.03)/25 = 99.00。
- m0r0 是两个模型比值最低的游戏（0.20×）；lf52 是 Opus 唯一超过 1,000 动作的游戏；wa30 的人类参考最大。
- 平均每局动作：GPT-5.6 Sol 约 365，Opus 约 292（推算）。

### 4.4 重复运行（App. B.1）

GPT-5.6 Sol（max）三次：99.00、98.90、99.23，即 99.04 ± 0.17（均值 ± 标准差）。Tab. 1/2 的 99.00 不是三次均值 99.04，而等于列出的第一次，所以表里应是单次运行（推算，论文没明说）；§5 的 GameWorld、BabyVision 数字则等于三次均值（见第 6 节）。Opus 的重复运行没有报告；消融（Fig. 5–9）每个配置跑了几次，未查到。

### 4.5 轨迹例子

- **Fig. 4**（镜像机制，游戏 ID 未查到）：第 1 关 VISTA 16 个动作，人类 32；第 3 关 41 对 75，第 3 关多了可移动的水平镜像线和可选的方块。
- **Fig. 12–13**（BP35）：第 1 关 16 个动作，首次人类 21；turn 7 写进 GUIDE.md 的规则是：任一动作之后泡泡会自动上浮，直到被岩石或绿块挡住；点绿块会去掉它；点水是白费一步的空操作。第 3 关 34 对 44，新增一种点一下消失、再点恢复的橙块；turn 73 agent 确认了这个开关，并决定用它搭天花板，挡住会把自己送进紫色尖刺的上升。
- **Fig. 10**（§4.4）：(a) 记忆里已存 1,472 帧；棋盘重置后，一次 inspect 从前 22 个 turn 里取回 4 帧（turn 125、128、139、145，在 turn 147 查看）来重建地图。(b) 一次 inspect 把三个小块放大约 13×，看紫色标记在哪一侧，以判断各块朝向，这在整张 512×512 帧里很难看清。

### 4.6 这些数字的限度

- RHAE 到 100 封顶（见 4.2）；Opus 每局都是 100.00，效率差异只体现在动作数。
- Opus 主实验的上下文上限、图分辨率、token 用量都未查到；所有 token 数和 Fig. 5–7、9 的消融都来自 GPT-5.6 Sol；只有 Fig. 8 用 GLM-5.3 Flash 320B。
- 每局的人类参考来自 10 名首次接触的人，基线取上中位数（App. A.6）；比较对象是"首次"玩家，不是熟练玩家。
- 只有 25 个公开游戏的结果，没有私有集（§6）。

## 5 消融（§4.3；App. A.2；默认 GPT-5.6 Sol、max、200K 上下文、512×512 图）

### 5.1 Fig. 5 累加消融

| 步 | 改了什么 | RHAE | 总动作 | 完成游戏 % | ΔRHAE（推算） |
|---|---|---|---|---|---|
| 官方实现 | 文字网格；经 API；只作外部参照 | 13.33 | 10,619 | 4 | — |
| (i) | 文字网格换成 PNG 图 | 47.32 | 16,343 | 36 | +33.99（跨访问方式，不可直接比） |
| (ii) | 放宽上限到每局 2,000 动作、48 h（官方：每段 5× 人类、12 h） | 51.66 | 22,544 | 44 | +4.34 |
| (iii) | 连续对话：一次对话里可多次 play，满了用原生压缩 | 65.82 | 28,626 | 60 | +14.16 |
| (iv) | 加 GUIDE.md / WORKING.md，并在提示里要求建立紧凑、可修订的游戏模型；快满时写交接摘要、开新上下文 | 70.05 | 23,702 | 76 | +4.23 |
| (v) | 加无损视觉记忆和由模型决定的 inspect；每个动作后只自动给最后一帧 | 94.10 | 12,261 | 96 | **+24.05** |
| (vi) | 加精确像素读数 read_pixels（选定像素的 RGB 数值）= 完整 VISTA | 99.00 | 9,126 | 100 | +4.90 |

读法：

- 官方实现经 API，(i)–(vi) 都经 Codex CLI 订阅，模型和推理强度相同，所以只有 (i)–(vi) 之间能直接比（App. A.2）。在可比的五步里 (v) 最大；(i) 到 (vi) 合计 +51.68（推算）。
- (v) 一次改了三件事：无损记忆、inspect，以及把每步自动给的图从至多 7 帧减到 1 帧。论文没拆开，所以这 +24.05 不能单独记在"只给最后一帧"或"inspect"名下。
- (iv) 同时加了笔记和提示词里的建模要求，+4.23 不能只归于笔记。
- (i) 动作少，部分原因是运行在动作或时间上限处就停了，游戏没做完，和低完成率一致（§4.3）。作者推测 (i) 的增益部分来自 token 效率：每张图约 308 image token，等价的 64×64 网格约 4K 文本 token；一次 7 帧的请求文本约 28K、图约 2.2K，留给推理的上下文更多（A.2 (i)，原文是假设的口吻）。
- (ii) 之后再继续放宽上限，没有明显收益（A.2）。
- 动作数先升后降：(iii) 最多；(iv)→(v) 少了 11,441，(v)→(vi) 又少 3,135（推算）。

### 5.2 Fig. 6 文字网格 vs 图（完整 VISTA 里只换观测形式）

RHAE 96.75 对 99.00；总动作 10,614 对 9,126；每局 token 71.9M 对 30.7M（约 2.3 倍，推算）。§4.2 的结论：图的成绩相当，动作更少，token 少得多。Fig. 6(a) 展示的文字网格是从整张 64×64 网格裁出的一部分。在完整 harness 里换回文字网格只掉 2.25（推算），远小于 Fig. 5 里官方 → (i) 的 +33.99；我们的推断是，harness 齐全以后，观测是图还是文字已不是主要因素，主要差在 token。

### 5.3 Fig. 7(a) 最大上下文（决定何时压缩、开新上下文）

| 上下文 | 100K | 200K（默认） | 500K | 780K |
|---|---|---|---|---|
| RHAE | 98.6 | 99.0 | 94.4 | 93.9 |
| 总动作 | 8,395 | 9,126 | 12,391 | 12,321 |
| 每局 token | 17.3M | 30.7M | 84.1M | 105.7M |

默认 200K 最好；100K 只低 0.4，token 约为一半（§4.3）。上下文更大反而更差、更贵：500K、780K 分别低 4.6、5.1（推算）。

### 5.4 Fig. 7(b) 放大倍数（相对 64×64 帧；默认 8× 是最近邻放大，其他倍数的插值方式未查到）

| 倍数 | 2× | 4× | 8×（默认） | 16× |
|---|---|---|---|---|
| 边长 | 128 | 256 | 512 | 1,024 |
| 每帧 image token | ≈20 | ≈77 | ≈308 | ≈1,229 |
| RHAE | 96.6 | 99.7 | 99.0 | 88.3 |
| 总动作 | 12,590 | 8,849 | 9,126 | 15,331 |
| 每局 token | 39.8M | 28.2M | 30.7M | 71.1M |

- 正文：4× 比 8× 每帧少 75% image token，成绩相当；2× 变差；16× 没有额外好处、每局 token 更多，作者的解释是多出来的 image token 占掉上下文，却没改善视觉推理。图上 16× 比 8× 低 10.7（推算）。
- 每帧 image token 降 75%，每局总 token 只降约 8%（28.2M 对 30.7M，推算）。我们的推断：每局 token 的大头不是观测图。
- 4× 的 99.7 高于 8× 的 99.0，但消融每个配置跑几次未查到，B.1 的标准差 0.17 只针对默认配置，不宜把这 0.7 读成 4× 更好。

### 5.5 Fig. 8 开源模型（GLM-5.3 Flash 320B）

官方实现（作者用原代码库跑，最小 harness）1.89；去掉视觉 harness 的 VISTA（只去掉 inspect 和 read_pixels 两个工具）13.27；完整 VISTA 66.93。去掉这两个工具掉 53.66（推算）。作者称这给出一个完全开源、可复现的系统。动作上限每局 3,000。各配置的动作数和完成率、GLM 的推理强度和上下文上限都未查到；去掉工具的配置是否同时改回每步自动给 7 帧也没写，所以不能和 Fig. 5 的台阶直接对照。

### 5.6 Fig. 9 3D 渲染

同样的游戏渲染成 3D：RHAE 84.12、20,640 个动作，对比 2D 默认的 99.00、9,126（§4.3 默认设置）。动作约为 2.26 倍（推算）。作者说 3D 需要多得多的交互，但仍远高于官方的文字基线。Fig. 9(a) 只展示了 SP80 和 LF52；3D 怎么生成、25 个游戏是否都渲染了，未查到。

## 6 其他基准（§5；App. A.1、A.3、A.6、B.1、B.3；全部只用 GPT-5.6 Sol）

§5 用这三个基准检验"几乎不改就能迁移"：两个交互游戏基准，一个静态视觉推理基准；加 BabyVision 是为了看没有动态环境交互时 inspect 的作用。每个基准都和同模型的官方实现比，另给人类和此前最好结果作参照（§5 引言）。Opus 5.0 和 GLM-5.3 Flash 在这三个基准上没有结果。

| 基准 | 规模与输入 | 指标 | 人类 | 官方实现 | VISTA | 此前最好（虚线） |
|---|---|---|---|---|---|---|
| GameWorld | 34 个浏览器游戏、170 个任务，分街机、平台、解谜、跑酷、模拟五类；只给官方任务提示、可用控制和截图；经 computer-use 接口直接用键鼠玩 | 成功率 SR % | 55.3 | 40.0 | **63.3** | 20.0 |
| GameWorld | 同上 | 进度 PG % | 64.1 | 62.9 | **79.3** | 39.8 |
| AI GameStore | 10 个公开浏览器游戏；只给公开的游戏描述、控制和画面，要靠交互摸清玩法和目标 | 人类中位归一化分的几何平均 | 100（定义） | 47.3 | **140.3** | 9.0 |
| BabyVision | 全集 388 道静态视觉推理题、22 个子类；本文只取 Visual Tracking 里的两类：迷宫 20 道、连线 19 道，共 39 道；可自选要细看的区域和要读的像素 | 准确率 % | 92.3 | 41.0 | **63.2** | 25.7 |

- **推算差**：VISTA 对官方实现 SR +23.3、PG +16.4、AI GameStore 约 2.97 倍、BabyVision +22.2；对人类 SR +8.0、PG +15.2、AI GameStore +40.3，BabyVision 低 29.1。
- **作者结论**（§5.2；§1）：三个基准上都稳定超过对应的最小 harness 基线；GameWorld 和 AI GameStore 超过首次玩的人类（§1 的说法是 GameWorld 超过新手基线、AI GameStore 超过人类中位数）。BabyVision 没有交互历史，41.0% → 63.2% 归功于 inspect 让 agent 能在单张图里挑区域细看。
- **指标**（App. A.3）：GameWorld 的 SR 是 170 个任务中成功的比例；PG 是每个任务在运行中达到的最高归一化进度 p_i ∈ [0, 1] 的均值，部分完成也有分；两者都由基准官方评测器从序列化的游戏状态算出。AI GameStore 各游戏计分方式不同，按官方协议用公布的人类中位数 m_g 归一：n_g = clip(100 × r_g / m_g, 1, 10⁴)，人类中位即 100，再对 10 个游戏取几何平均。BabyVision 用精确匹配加 LLM 判官判对错，跑失败的算错；LLM 判官是哪个模型未查到。
- **基线怎么跑**（App. A.1）：作者能用已发表结果就用，否则按报告的 agent 配置重跑官方实现。GameWorld：官方 computer-use agent 换成 GPT-5.6 Sol，按官方交互与评测协议，看截图用键鼠，每次请求带前两步的截图、推理和动作，每任务至多 100 个动作。AI GameStore：复现官方评测循环，每个游戏秒决策一次，附 0.2 s 间隔采的 5 帧，请求里只有这 5 帧和上一次决策的草稿、动作与结果；一次运行 120 个决策，模型推理时游戏暂停。BabyVision：官方一次作答设置，图和问题同一个请求给出，不额外细看、不交互，最终答案用基准参考代码抽取。
- **人类参考**（App. A.6）：GameWorld 有两名计算机科学研究生，各自在每任务 100 动作的预算下做了全部 170 个任务；新手事先没接触过这些游戏和任务，专家事先学过规则、练过操作；论文以新手为首次人类参考，即 Fig. 11 的人类柱，专家的数字没报。AI GameStore：经 Prolific 招 106 人（平均年龄 38.81），在定制界面上玩，每人随机分到 10 个游戏、每个 2 分钟，参考值是每个游戏玩家分数的中位数，归一为 100。BabyVision：16 名至少本科学历的成人做完全部 388 题，迷宫 95.0%、连线 89.5%；按本文 20/19 题加权正是 92.3（(20×95.0 + 19×89.5)/39 = 92.32）。
- **重复运行**（App. B.1，主评测各跑三次）：GameWorld SR 64.7 / 64.7 / 60.6（63.33 ± 2.37），PG 81.0 / 80.0 / 76.9（79.30 ± 2.14）；BabyVision 61.5 / 61.5 / 66.7（63.23 ± 3.00）。AI GameStore 三次的汇总分是 119.99 / 148.58 / 140.06；报告值按基准官方的多次运行协议算（先对每个游戏三次的原始分取平均，再归一、汇总），应即 Fig. 11 的 140.3；三次汇总分直接平均是 136.21（推算）。折成题数（推算）：GameWorld 64.7%、60.6% 约为 110/170、103/170，官方 40.0% 为 68/170；BabyVision 61.5% = 24/39，66.7% = 26/39，官方 41.0% = 16/39。
- **例子**（Fig. 2 右；Fig. 14）：GameWorld 的动作是带时长的按键或鼠标输入（0.3 s、0.5 s、0.6 s、2 s 等），每次记 1 个游戏动作，inspect 和 read_pixels 不计。
  - Fireboy and Watergirl（turn 11，第 3 关，计时 00:07；任务是 Fireboy 收 10 颗红钻、Watergirl 收 10 颗蓝钻，每个动作换一个角色）：Watergirl 走 `act W+D 0.5 s`，返回 1 帧并归档，计时仍 00:07；随后 inspect 两个虚线区域（两个角色的落点），返回 2 张放大图，不计动作；再给 Fireboy `act wait 0.4 s`。
  - Minecraft Clone：收集至少 5 块铁矿，用 21 个动作。turn 5 观察到一次长按沿一条射线连挖了几块对齐的泥土和草，打出约 5 格深的竖井，并在 GUIDE.md 记下：对有轮廓的草长按 2 s 会沿射线连续挖掘。turn 14 inspect 物品栏计数和一条黄褐色斑点的矿脉，认出第 9 格是铁矿堆；turn 21 挖到第 5 块，任务完成。
  - Pac-Man：得分至少 1500，用 42 个动作。turn 36（1010 分）inspect 36 个 turn 之前开局帧的一条区域，确认外侧通道向上延伸，再按上 0.6 s；豆子吃掉就消失，只有开局帧有完整地图，agent 在 turn 13 也回看过它。
  - BabyVision 770（连线，把动物 A–C 连到栖息地 1–3）：turn 2 以 4× inspect 两个迷宫区域，turn 4–5 以 4× inspect 一个路口、再用 54×108 的采样读一张迷宫示意的像素，答 A-2、B-3、C-1，正确；同一模型不用工具一次作答时答 A-2、B-1、C-3，把驼鹿和兔子对调了。
  - BabyVision 666（迷宫，哪个黄色入口通到绿色出口）：turn 1 以 2× inspect 整张图，turn 2–3 先用 33×33 网格探测读像素，再分别按竖向（16×17）和横向（17×16）边界读墙，答 B（左下入口），正确；同一模型不用工具一次作答答 C。
- **未查到**：各条"此前最好"虚线出自哪个系统；§5 上 GPT-5.6 Sol 的推理强度、上下文和压缩设置；VISTA 自己在 GameWorld、AI GameStore 上的动作或决策预算（A.6 暗示 GameWorld 也是每任务 100 个动作，120 个决策只对 AI GameStore 基线写明）；VISTA 推理时 GameWorld、AI GameStore 是否暂停；逐游戏分数和 GameWorld 按类别的拆分；AI GameStore 第 10 个游戏的名字（Fig. 11 只列了 9 个）；官方基线的重复次数和方差；BabyVision 其余 20 个子类或全集的结果；显著性检验；§5 的 token、费用和墙钟。

## 7 成本

| 项 | 数值 | 出处 |
|---|---|---|
| 每帧 image token | 2× ≈20，4× ≈77，8× ≈308，16× ≈1,229 | §4.3；Fig. 7 |
| 64×64 文字网格 | ≈4,000 token | §4.3；A.2 |
| 一次 7 帧请求 | 文本 ≈28K，图 ≈2.2K | A.2 (i) |
| 每局 token（完整 VISTA，默认设置） | 30.7M | Fig. 6；Fig. 7 |
| 25 局合计 | ≈767.5M（推算：30.7M × 25） | — |
| 每个游戏动作 | ≈84K token（推算：767.5M ÷ 9,126） | — |
| 上下文 100K | 每局 17.3M，约 51.5K token/动作（推算） | Fig. 7(a) |
| 上下文 780K | 每局 105.7M | Fig. 7(a) |
| 文字网格观测 | 每局 71.9M | Fig. 6 |
| 上下文上限 | 消融默认 200K（GPT-5.6 Sol）；官方基线 175K 滑动窗口；Opus 主实验未查到 | §4.3；A.1 |
| 墙钟 | 只有上限：VISTA 协议每局 48 h，官方实现 12 h；实际用时未查到 | A.2 |
| 美元 | 未查到；作者经订阅制 CLI 访问模型，不直接调 API | A.1 |
| Opus 主实验 token | 未查到 | — |
| §5 三个基准的 token、费用、墙钟 | 未查到 | — |

token 的计法（例如是否含缓存读取）未查到。

## 8 局限

### 8.1 作者自述（§6–7 原文）

§6 只有一段，两条：

1. 模型已有的能力仍是成功的关键因素，VISTA 是把这些能力引出来的一个简单而有效的办法（引 SWE-agent、Screenshots or tools?、OpenForgeRL）。作者认为，相对基线的大幅领先说明视觉 harness 设计是和模型改进互补的方向。
2. 可能的污染：所用模型发布晚于这些公开基准，不能排除游戏在训练数据里；在私有集这类新游戏上评测，更能检验泛化。论文没有私有集结果。

§7：简单的视觉 harness 足以让现有多模态模型在交互视觉环境里完成复杂任务，做法是给原始视觉观测、无损保存、允许按原始细节回看；这一简单设计就足以让 agent 满分通关 25 个公开 ARC-AGI-3 游戏；同一 harness 几乎不用改就能推广到多种视觉游戏和谜题；作者认为它有潜力成为游戏之外的通用 harness。§7 唯一的未来工作：在更复杂、更真实的任务上评测，例如更接近物理世界的具身任务（引 RT-2）。这是 §6–7 里唯一一次提到具身。

§6–7 没有谈：算力或 token 成本、墙钟延迟、实时或连续控制的约束、失败案例，也没有任何具身实验。

### 8.2 我们的看法（论文没说的部分）

1. **分数封顶**：按 Eq. (1)–(2)，全通关时 RHAE 最高 100，单关最多 1.15 可以互相抵消（推算）；Opus 的 100.00 和 Tycho 的 100.00 在分数上分不开，只能比动作数，而 Tab. 1 没有动作数一栏。
2. **消融的因果粒度**：Fig. 5 是累加阶梯，关键两步都捆绑了多项改动——(iv) 是笔记加建模提示，(v) 是记忆加 inspect 加"只给最后一帧"；每个配置跑几次未写，只有默认配置有三次重复（SD 0.17）。
3. **设置不对称**：Fig. 5–7、9 的消融全在 GPT-5.6 Sol 上做（Fig. 8 用 GLM）；Opus 主实验的上下文、分辨率、token 都没写；Opus 的官方基线是 high，VISTA 是 xhigh。
4. **贵**：默认配置每局约 30.7M token，约 84K token/动作（推算）；每步思考时间只受每局 48 h 约束；没有美元数字，且走订阅 CLI，无法直接折算。
5. **环境会等、能重来**：ARC-AGI-3 回合制，控制器严格串行；agent 可以 RESET 重来本关（history 里有 attempts 的概念）。GameWorld、AI GameStore 推理时是否暂停没写。论文没有一个思考时间受限的设置。
6. **视觉越复杂，增益越小**：3D 渲染 84.12、动作约 2.26 倍；BabyVision 63.2 仍比人类低 29.1；开源 GLM 只到 66.93。
7. **BabyVision 的取样**：388 道里只取了 39 道两类追踪题，恰好是 inspect 和 read_pixels 最能派上用场的类型（我们的推断），不代表整个基准。
8. **参照不可核**："此前最好"的虚线出处没写。
9. **污染**：作者自己承认，私有集没测。

## 9 与我们 v1 的关系（逐条）

1. **设计文档里的 VISTA 引用核对**（`docs/design/vla_harness_v1.md`）：
   - 第 15 行"VLM 默认只看最后一帧（VISTA 式 inspect 回看）"：与 (v)–(vi) 的设置一致（Fig. 5 caption；A.2），准确。小差别：VISTA 每次 play 自动返回最后一帧（Alg. 1），我们默认不附图，planner 要自己调 observe（见第 4 条）。
   - 第 45 行（§3.0 进化面表，笔记行）"VISTA 2610.02200 Fig. 5 (iv) +4.2"：数值对（65.82 → 70.05，+4.23 RHAE，推算，GPT-5.6 Sol），口径不全：(iv) 同时在提示里加了建模要求；"GUIDE/LESSONS"把 VISTA 的 GUIDE/WORKING 和 PhyAgentOS 的 KNOWLEDGE/LESSONS（底稿 B）混成一个名字；VISTA 的笔记只在一局（一个游戏、同一个 agent）之内使用、不过门，不能直接当跨回合、过门笔记的收益证据（见第 6 条）。见建议 V3。
   - 第 176 行（§4 MCP 表 inspect 行）把我们的签名 `(frame_ref, region{x,y,w,h}, zoom≤4) → image` 挂在"VISTA 2610.02200 §3.1"名下：VISTA 的签名是 `inspect(question, views) → images`（App. A.4 Algorithm 1），一次可取多帧，没有 zoom 参数，放大倍数没给（Fig. 10b 例子约 13×，Fig. 14 例子 2×、4×），zoom≤4 是我们自己的选择；"历史帧无损存"出自 §3.1 不错，但 VISTA 存的是每一帧含动画中间帧，我们存节点边界加每秒 1 帧（设计第 211 行）。见 V1。
   - 第 283 行（§8.3 "VLM 看图"行）"VISTA Fig. 5 中 (v) 是最大一步"：只在可直接比较的 (i)–(vi) 里成立（+24.05，推算）；官方 → (i) 是 +33.99，但访问方式不同；而且 (v) 捆绑了三件事，单凭它不能证明"默认只给最后一帧"本身有用。见 V2。
   - 相关的代码注释：`etude/verifier/verdict.py` 文档串把 InsufficientEvidence 时的二审叫作 VISTA-style second opinion。VISTA 没有裁判或验证器，inspect 是行动模型自己用的工具，这个二审是我们的扩展。见 V4。

2. **inspect：设计、实现、VISTA 三方对照。**
   - 实现与设计一致：`etude/mcp/server.py` `HarnessTools.inspect(frame_ref, region, zoom=2.0, view="head")`；`MAX_ZOOM = 4.0`；输出边长上限 `MAX_INSPECT_SIDE_PX = 1280`；用 LANCZOS 放大，以 PNG（`encode_png`）返回。`view` 是比设计多出来的参数（`docs/design/implementation_notes.md` 第 172 行已记）。frame_ref 可以是 `"latest"`、内存关键帧标签，或本回合 `keyframes/` 下的 PNG；`_resolve_ref_image` 拒绝其他回合的路径。
   - 与 VISTA 的差别：我们一次只看一帧的一个区域，也没有 question 参数；VISTA 一次可给多个 view（Fig. 10a 一次取 4 帧）。我们每次规划的工具调用上限 40（`frozen/params/thresholds.yaml` `budget.max_tool_calls_per_plan`），每回合 LLM 请求上限 48（`budget.vlm_requests_per_episode`），跨时间比较 4 帧就得调 4 次。
   - `planner/SYSTEM.md` 只把 inspect 说成"最新图看不清时放大局部"，`TOOL_DESCRIPTIONS["inspect"]` 只写放大存储帧的一个区域；`etude/planner/request.py` `build_prompt` 会列出较早的关键帧、说可以用 observe 或 inspect 打开，但只列最近 8 个，也没说可以拿来回放失败过程。VISTA 的两个定性例子一个跨时间（Fig. 10a）、一个看空间细节（Fig. 10b），GameWorld 的 Pac-Man 例子也是跨时间。见 V5、V6。

3. **无损帧记忆：存了什么，planner 找得到什么。**
   - 盘上：`etude/trace/writer.py` `FileTraceWriter` 在节点边界（验证器经 `keyframe_fn=trace.keyframe` 存 `<node>_before_t…`、`<node>_after_t…`）和每秒 1 帧（`tick`，`keyframe_hz=1.0`）存三路相机，`save_png` 用 PIL 写无损 PNG。这和设计 §4 存储行一致。25 Hz 下每秒 1 帧就是 1/25 的帧（推算），VISTA 存每一帧。
   - 内存：`etude/mcp/state.py` `HarnessState` 只留最近 `DEFAULT_MAX_KEYFRAMES = 64` 个关键帧；`etude/adapter/harness_build.py` `HarnessRunner.get_action` 每发一个块调一次 `add_keyframe(f"t{t}")`，超出丢最旧的。
   - 可发现性：`etude/planner/request.py` `build_prompt` 只列最近 `max_keyframe_refs: 8` 个内存标签（`planner/config.yaml`），外加上一条判决 JSON 里的 before/after PNG 引用。盘上的每秒 1 帧 planner 打得开，但 prompt 里不列路径，它不知道有哪些。VISTA 每张图都标 turn 和 frame 编号，地址可以预测。
   - 设计里"逐帧全存走官方 MP4"：录像不在 inspect 的可读路径里。
   - 小结：原则一致（原始 trace 全留，`docs/prompts/deep_research_v1_prompt.md` 第 19 行），但我们的"无损"只在编码意义上成立，不是 VISTA 那种完整。

4. **默认看图预算和 (v) 的发现对不对得上。**
   - 默认只看最新帧这一半对得上：`planner/config.yaml` `prompt.attach_last_frame: false`，prompt 不附图；planner 要自己调 `observe`，默认返回最新帧的三路图（`HarnessTools.observe`）；`planner/SYSTEM.md` 也要求默认只看最新帧。差别在于 VISTA 每次 play 自动返回最后一帧，我们要多一次 observe 往返（`attach_last_frame: true` 可以省掉）。
   - 但 (v) 的增益来自 VISTA 的条件：每局平均约 365 个动作（GPT-5.6 Sol，推算），200K 上下文下要多次压缩、旧图会掉出上下文（我们的推断），回看才值钱。我们一回合至多 12 次规划，`etude/planner/rpc_driver.py` `PiRpcPlanner` 每回合只起一个 pi 会话（`start_episode` / `reset`），上下文远到不了上限（我们的推断，M1 量 token 时核对）。所以不能把 +24.05 当成我们的预期收益。能借的是两件事：看局部细节（空间），以及回放一个节点执行时发生了什么（时间）。后者正是 planner 平时看不到的，因为节点执行期间不调 VLM。
   - 量级：我们每次规划约 1.2k 图 token（设计 §8.3，估计），图是 640×480 相机原图；VISTA 每帧约 308 token，是放大的 64×64 网格。

5. **验证器的 inspect 二审在默认装配里没接线。** 设计 §3.3 伪代码注释和 §4 判决语义都写"InsufficientEvidence 时最多 1 次 inspect"。代码里机制都在：`CodeVerifier(inspect_hook=…)`、`NodeRunner(second_opinion=…)`、阈值 `verifier.max_inspect_per_node: 1`。但默认装配路径（`verifier.kind: code`）的 `etude/adapter/harness_build.py` `make_verifier` 只传 `keyframe_fn`，`HarnessRunner.from_config` 构造 `NodeRunner` 时也不传 `second_opinion`；于是 `etude/runner.py` `NodeRunner._dispatch` 在复观测之后直接落到 `retry_then_fallback`。只有 `python -m etude.verifier` 的演示传了 hook。`docs/design/implementation_notes.md` 第 154 行已记下两个钩子都没接、二审只能由 P1 自己调 inspect，但主设计文档 §3.3、§4 仍写成会发生。见 V4。

6. **自写笔记该不该做成过门的进化面。** VISTA 的笔记不过门：`write_guide` / `write_working` 直接整文件覆盖，论文没有描述任何审核或验收（App. A.4）。但它只在一局之内使用：一个 agent 从头玩到尾（§3.2）；GUIDE.md 跨关、跨 RESET 和上下文重启保留，WORKING.md 升关就清空；GUIDE.md 会不会跨游戏或跨运行保留，论文没说（未查到）。跨关在结构上有点像我们同一任务的跨回合（各关共享机制、又加新东西，§4.1），但 VISTA 那里是同一个 agent 连续写读、不过门，评测期也不冻结（我们的读法）。我们设计 §3.0 把笔记当跨回合进化面的候选（v1.1），引 (iv) +4.2 当收益证据；这条证据最多支持"同一 agent 局内跨关、不过门的笔记"，不能直接当"跨回合、过门、评测期冻结的笔记"的收益。代码里目前没有任何笔记实现，`evolvable/` 下只有 prompts、rules、skills。如果 v1.1 做跨回合笔记，写入会带进后续回合乃至评测，就得照记忆回路的规矩过门、评测期冻结（设计 §3.2 关于误判按 FPR 写入并被检索放大的论述同样适用）；VISTA 那种不过门，只适用于回合内的草稿。回合内草稿对我们价值可能很小：四个任务的回合只有 24–42 s 仿真时间（推算：设计 §8.2 F6 的 step_lim ÷ 25 Hz），至多 12 次规划，且在同一个会话里。见 V8。

7. **预测与对照。** Prompt 1 要求 play 前说预期、play 后列出全部可见变化（包括预期外的）。我们节点的 `postcondition` 已经承担"说预期"这一半，而且由代码判，比 VISTA 的自述更硬；缺的是另一半：失败后先描述实际发生了什么。`evolvable/prompts/planner.md` 第 7 条只说读判决、换做法。见 V7。

8. **隔离和工具形态已经一致。** VISTA 在 Claude Code 下经 MCP 给工具、关掉所有内置工具、在沙箱里拿不到环境凭证，单一控制器是唯一能推进游戏的组件（A.4）。我们这边：`planner/config.yaml` 的 `--no-builtin-tools`、`--no-context-files`、`--no-skills`，`planner/.pi/extensions/guard.ts` 拦截 7 个工具以外的调用，P2 拿不到环境对象（设计 §2）。我们的 `submit_sequence` 对应它的 play，是 planner 唯一能影响执行的工具。这部分不用改。

9. **不迁移的：上采样、read_pixels、压缩交接、RESET 与 attempts、游戏 prompt 的措辞。** Fig. 7(b) 的结论来自 64×64 离散网格的最近邻放大，16× 反而更差；我们是 640×480 相机 RGB，没有可以放大的网格，`inspect` 的 LANCZOS 放大也不增加信息。read_pixels 把区域切格返回像素值，在 ARC-AGI-3 上读的是调色板网格的格值，在 BabyVision 上用来测颜色、探迷宫墙（Fig. 14）；我们需要的像素级判断（物体动没动）已经在代码谓词里（`frozen/params/thresholds.yaml` `rgb_diff.not_moved_max`），planner 不需要读数。压缩检查点：一回合一个会话、至多 48 次请求，用不上。RESET 与 attempts 历史：我们一个布局一个回合（设计 §8.2 F6），场景不能回到起点，`previous_node` 只是把末端伺服回锚点位姿、再用代码复验锚点后置条件，场景本身回不到起点。游戏 prompt 的措辞不借，只借 Prompt 1 的预期—对照结构（第 7 条、V7）。

10. **回合制、可重来 vs 25 Hz 加 90 s。** VISTA 的控制器严格串行，环境等模型想完，每步思考时间只受每局 48 h 的约束，平均每个游戏动作约 84K token（推算）。我们的 25 Hz 快回路里没有 VLM，planner 只在节点边界被阻塞调用；官方客户端每次调用硬上限 120 s，我们等 90 s 后返回 1 步保持块（`planner/config.yaml` `timeout.wait_in_get_action_s: 90`；设计 §2）。VISTA 的一个动作对应我们的一个节点（若干动作块），它的动画中间帧对应我们节点执行期间的帧。所以它"每步多次回看"在我们这里只能发生在节点边界，次数受 90 s 和工具调用上限约束；它的分数来自环境会等、能重来的设置，推不到连续控制。论文自己也没有任何思考时间受限或实时的实验（§6–7 未谈）。

## 10 建议（不直接改文件，供决策）

"必要"指不改的话，设计里的引用或描述会与论文或代码不符；"建议"指可选的增强。涉及 `evolvable/` 的项，若在 M1 开跑前写进初版，不过门；开跑后只能作为编辑者候选走 L0–L3 门。`etude/` 下的 P2 代码不在进化白名单里，由开发者在 M1 前改。

| # | 目标 | 改成什么 | 依据 | 必要性 |
|---|---|---|---|---|
| V1 | 设计 §4 MCP 工具表 `inspect` 行（第 176 行）说明栏 | "签名是我们定的；VISTA 的是 `inspect(question, views) → images`（App. A.4 Algorithm 1），一次可取多帧多区域。论文没给放大倍数上限（Fig. 10b 例子约 13×，Fig. 14 例子 2×、4×），zoom≤4 是我们的选择。'历史帧无损存'取自 §3.1，但 VISTA 存每一帧含中间帧，我们存节点边界加每秒 1 帧（见本节存储行）" | Alg. 1；§3.1；Fig. 10b；Fig. 14 | **必要**：现在把我们的签名和上限挂在 VISTA 名下 |
| V2 | 设计 §8.3 "VLM 看图"行理由栏（第 283 行） | "VISTA Fig. 5 中 (iv)→(v) +24.05 RHAE（推算，GPT-5.6 Sol），是可直接比较的 (i)–(vi) 里最大的一步；官方→(i) +33.99 更大，但访问方式不同，只作外部参照（App. A.2）。(v) 同时做了三件事：每步只自动给最后一帧（此前至多 7 帧）、无损记忆、inspect，论文没拆开。GLM-5.3 Flash 上只去掉 inspect 和 read_pixels，66.93 → 13.27（Fig. 8）" | Fig. 5 及 caption；App. A.2；Fig. 8 | **必要**："最大一步"需要限定范围，且 (v) 不能单独证明"默认只给最后一帧" |
| V3 | 设计 §3.0 进化面表笔记行（第 45 行） | 名称改为"VLM 自写笔记（VISTA 的 GUIDE/WORKING；PhyAgentOS 的 KNOWLEDGE/LESSONS）"；收益格改为"不明：VISTA Fig. 5 (iv) +4.23 RHAE（推算，65.82→70.05，GPT-5.6 Sol；同一步还在提示里加了建模要求；累加消融，没有'完整 VISTA 去掉笔记'的对照；VISTA 的笔记只在一局之内由同一 agent 写读，不过门）" | Fig. 5 (iv)；App. A.2；App. A.4 Notes | **必要**：口径 |
| V4 | 设计 §3.3 伪代码注释与 §4 判决语义里的"最多 1 次 inspect"；`etude/adapter/harness_build.py` `make_verifier` 与 `HarnessRunner.from_config` | 二选一。(a) 接线：给 `NodeRunner` 传 `second_opinion`（或给 `CodeVerifier` 传 `inspect_hook`），复用 MCP `inspect` 的同一套裁剪代码，计 1 次 VLM 调用，受 12 次上限约束。(b) 不接线：设计写明"v1 默认装配未接线，复观测后直接 `retry_then_fallback`"。两种都把"VISTA 式二审"改称"我们的扩展" | 代码现状见第 9 节第 5 条；VISTA 无裁判（§3.2；App. A.4） | **必要**：设计写的行为和默认装配不一致 |
| V5 | `etude/mcp/server.py` `HarnessTools.inspect` 及其 MCP 包装；设计 §4 inspect 签名 | 加多视图形式：`inspect(views=[{frame_ref, region, view}], zoom, question?)`，每个 view 返回一张图，整体算一次工具调用；保留现有单帧参数。`question` 只写进 trace，事后能看出模型想用 inspect 确认什么 | Alg. 1（每个 view 一张图）；Fig. 10a（一次取 22 个 turn 内的 4 帧）；我们 `budget.max_tool_calls_per_plan` = 40 | 建议（先看 M0/M1 的 `planner.jsonl`：`HarnessState.log_tool` 已记下每次 inspect） |
| V6 | `etude/planner/request.py` `build_prompt`（`reason="node_failed"` 时）；`planner/config.yaml` `prompt.max_keyframe_refs` | 失败重规划时，按时间顺序列出失败节点这次尝试的 before/after PNG 和期间的每秒 1 帧引用，不只列最近 8 个内存标签；并在 `TOOL_DESCRIPTIONS["inspect"]` 里说明可以用它回放失败过程 | §3.1（跨时间回放一个动作的中间帧）；Fig. 14 Pac-Man（回看 36 个 turn 前的开局帧）；每秒 1 帧的 PNG 已在盘上（`FileTraceWriter.tick`） | 建议 |
| V7 | `evolvable/prompts/planner.md` 第 7 条 | 失败后先写两句对照：失败节点的后置条件原本预期什么；before/after 两帧之间实际看得到的全部变化（包括预期外的）；然后再出恢复节点 | Prompt 1（play 前说预期、play 后列全部可见变化）；我们的 postcondition 已承担"说预期"那一半 | 建议（面 1） |
| V8 | 设计 §3.0 笔记行的 v1.1 定义 | 分成两种：回合内草稿（类 WORKING.md，`reset()` 清空，不过门；我们一回合至多 12 次规划、同一会话，收益可能很小）；跨回合笔记（类 GUIDE.md / LESSONS.md，是进化面，必须过 L0–L3、评测期冻结）。VISTA 的不过门只适用于前一种 | App. A.4 Notes（整文件覆盖，未描述审核）；§3.2（一个 agent 玩完整局）；WORKING.md 升关清空；我们设计 §3.2（误判写入被检索放大）；PhyAgentOS 的教训须被后续结果证实（设计 §7 引 2607.16636 §4.3） | 建议（v1.1） |
| V9 | 设计 §5 M1 实验格 | 可选加一组"去掉 inspect"的配对对照：只从 MCP 工具表拿掉 inspect，其余不变，同布局配对 | Fig. 8 的做法（只去 inspect 和 read_pixels，GLM 66.93→13.27）；Fig. 5 的 (v) 捆绑三件事，回答不了 inspect 单独值多少 | 建议（M1 预算允许时，约多 200 回合） |
| V10 | 设计 §3.5 威胁表"超调用预算"行（自测候选"规划器循环 inspect"，第 143 行），或 §7 风险表"VLM 调用贵"行的依据栏 | 补一句量级：VISTA 默认配置每局约 30.7M token，约 84K token/动作（推算），每步思考不设时限；我们一次规划约 1.2k 图 token 加约 8k 文本，限 90 s。不要指望在我们的预算下复现 VISTA 式"每步多次回看" | Fig. 6；Fig. 7；App. A.2；设计 §3.4 | 建议（措辞） |
| V11 | read_pixels、上采样倍数、压缩检查点、RESET/attempts 历史 | 不借，记录结论 | Fig. 7(b) 是 64×64 网格最近邻放大的结论；read_pixels 在 ARC-AGI-3 上读调色板网格、在 BabyVision 上测颜色；我们是 640×480 相机 RGB，像素级判断在代码谓词里（`rgb_diff.not_moved_max`）；一个布局一个回合，回合内没有 RESET（设计 §8.2 F6） | 无需改 |

## 11 核查记录

### 被更正的声明（9 条，本卡按更正后的版本写）

1. **front:META-12**：README 没说 CLAUDE_CODE_OAUTH_TOKEN 是"订阅式认证、不是 API key"，那是读者的推断。本卡只写 README 的意思（由 setup-token 生成、写进 .env）；"模型经订阅 CLI 访问"另出自 App. A.1。
2. **front:INTRO-9**：Fig. 2 右只展示了 W+D 那一步的结果（返回 1 帧并归档），没展示 `act wait 0.4 s` 返回什么，所以去掉"每个 act 都返回 1 帧"；面板本身没写游戏名，Fig. 11 叫它 Fireboy。
3. **tail:S5-26**：同一幅图。W+D 0.5 s 是 Watergirl 的动作（WASD 控制，图中写明轮到 Watergirl），不是 Fireboy 的；"每个 act 返回 1 帧"同样去掉。
4. **front:B-3**：App. A.1 的"用原代码库得到"只指 Fig. 8 里官方实现那一条（1.89）；另两条（13.27、66.93）是作者自己的 VISTA 运行。
5. **front:MOD-2**：漏了 Tycho 在 Tab. 1 的第二行（Opus 5.0，xhigh，100.00）；其余数值无误。
6. **arc:T1c**：同一处遗漏。HTML 表里 Tycho 跨两行，所以"其他程序合成系统都用 GPT-5.6 Sol"不成立。
7. **arc:R4**：原声明丢了论文的限定词"通常"（对比系统通常用文本网格）；"同期工作""文本网格"两句在 §4.2 正文，不在 Tab. 1 标题；§1 的新颖性措辞是"第一个基于视觉的系统"。
8. **arc:A2b**：token 效率是作者的假设（原文是"我们假设它有贡献"的口吻），不是已经证明的归因；数字无误。
9. **tail:S7-42**："唯一的未来工作"只在 §7 范围内成立；§6 另有"用私有集检验泛化"的前瞻建议。RT-2 在 PDF 和 HTML 里都是 [10]。

### 被驳回的声明

无。

### 论文没写、本卡标"未查到"的（汇总）

§1 没有列明的贡献清单；没有回合循环的伪代码；交接触发阈值、交接摘要格式、压缩前 hook 的确切消息、Codex 原生压缩与 save_compact_checkpoint 的配合；Opus 主实验的上下文、分辨率和 token；inspect 的放大倍数和每次最多 view 数；read_pixels 的 rows/columns 参数和 palette 格式；act 的签名；history 在其他基准上是否开放；agent 实际收到的工具说明文字；无效请求和被拒 play 的处理；美元和实际墙钟；论文正文的代码许可；运行日志或轨迹是否发布；每个消融配置跑几次（Tab. 1/2 的 99.00 推算为单次）；基线、消融、GLM、3D 的逐游戏结果；GLM 各配置的动作数、完成率、推理强度、上下文；Fig. 6、7、9 的完成率；3D 怎么生成、是否覆盖 25 个游戏；每个游戏的关数；ACTION1–5、ACTION7 的含义和调色板大小；官方 Opus 基线的动作数；Fig. 4 的游戏 ID；§5 各"此前最好"的出处；§5 的推理强度、上下文、VISTA 自身预算、推理时是否暂停；AI GameStore 逐游戏分数和第 10 个游戏名；GameWorld 按类别拆分；专家玩家的数字；官方基线的重复次数和方差；Opus 的重复运行；Opus 和 GLM 在 §5 的结果；§5 的成本；BabyVision 的 LLM 判官型号和其余子类结果；显著性检验。核查到的附录是 A.1–A.6 和 B.1–B.3，Fig. 14（B.3）在 PDF 最后一页 p.23；是否另有更广影响声明或单独的讨论节，未查到。

### 相对旧卡的改动

1. **来源**：旧卡只读了 HTML 前 10 万字符，§6–7 靠二手摘要；新卡读完 HTML 全文和 PDF，并按"三位读者 + 逐位严格核查"只用核查通过的内容。
2. **元数据**：补共同一作、同属 MIT、唯一版本、cs.AI/cs.CV、23 页、技术报告与 2026-08-05 博客；区分论文许可（CC BY 4.0）和代码许可（MIT，只来自 README/LICENSE）。
3. **一句话**："环境是回合制游戏和静态图"改为"ARC-AGI-3 是回合制"：GameWorld、AI GameStore 的动作带时长，VISTA 推理时是否暂停未查到，旧说法过宽。补 Opus 两边推理强度不同（high 对 xhigh）和 GPT-5.6 Sol 的 13.33 → 99.00。
4. **每个 turn 的流程**：旧卡说每个 turn 都"收到两份笔记"；实际上读笔记是可选的（Fig. 3(d)；§3.2），只有上下文交接后的新上下文才自动带上 GUIDE.md 和 WORKING.md（A.4）。
5. **工具表**：补全 Algorithm 1 的签名和返回值（play 的三元组、inspect 的 view 结构、read_pixels 的返回、history 的参数和 attempts 定义、四个笔记工具和 save_compact_checkpoint），以及 Algorithm 1 自身的几处缺口。
6. **512×512、8× 最近邻**：旧卡写成 VISTA 的通用设置；论文只说这是消融的默认，Opus 主实验未写。
7. **消融"最大的一步是 (v)：+24.05"**：加限定——只在可比的 (i)–(vi) 里成立；官方 → (i) 是 +33.99 但访问方式不同；(v) 捆绑三项改动；(iv) 捆绑笔记和建模提示。补动作数、完成率两列。
8. **Fig. 6**：补具体数字 96.75 对 99.00、10,614 对 9,126。
9. **Fig. 7(b)**："16× 无益"改为给出数字（88.3、15,331 动作、71.1M token，比 8× 低 10.7，推算）。
10. **Fig. 8**：补官方实现 1.89 和去掉视觉 harness 的 13.27。
11. **新增**：Fig. 9（3D）、Fig. 10 和 Fig. 4、12–13 的轨迹例子、Tab. 1 全表（含 Tycho 第二行）、RHAE 公式、Tab. 2 逐游戏表、B.1 重复运行、人类参考细节、控制器规则、上下文交接、三个 prompt、README 细节、成本表。
12. **其他基准**：旧卡说"具体数字在 Fig. 11，正文没有"；BabyVision 的 41.0% → 63.2% 其实在 §5.2 正文里。新卡补全 Fig. 11 全部数字、指标定义、基线协议、人类参考、三次重复和 Fig. 14 例子。
13. **局限**：旧卡把"大上下文 token 涨得快"列为作者自述的局限，那是 §4.3 的消融结果，§6 只有两条局限，没有谈成本；"私有集没测"改为"作者建议用私有集检验泛化，论文没有私有集结果"。新卡把作者自述和我们的看法分开写。
14. **与 v1 的关系**：旧卡说"VLM 每次看不看图"是未定问题，VISTA 是第四个选项；设计 §8.3 已经选了 VISTA 式，新卡改为对照实现（inspect 签名、帧存储、可发现性、二审未接线）。旧卡说笔记"是一个可进化面候选"；新卡指出 VISTA 的笔记是局内记忆，(iv) 的证据只支持局内笔记。旧卡第 3 条的"不借的"保留并补了依据（Fig. 7(b)、F6、90 s），只有"游戏 prompt"一项改为：措辞不借，借 Prompt 1 的预期—对照结构（V7）。旧卡第 1 条的"记忆只是索引"出自纪要，仓库里查不到这句，新卡改引 `docs/prompts/deep_research_v1_prompt.md` 第 19 行的"原始 trace 全留"。
15. **新增**第 10 节建议表和本节核查记录。

### 终审改动（2026-10-11，相对上一稿）

1. **来源段**：三位读者的范围原写成互不重叠的三段，按声明编号看范围有重叠，改为"分段读、范围有重叠"。
2. **非具身**：补依据（游戏只经 play 推进、控制器串行，App. A.4）；"论文没有"改为"未见"。
3. **论点、动机、局限 1**：按原文口吻改。论点是"合适的 harness 能释放已有能力"，不是"缺的是 harness"；旧观测"可能"被删或摘要；模型能力"仍是关键因素"，不是"很大程度上依赖"。
4. **模型访问**：订阅制 CLI 只指作者自己的运行；组织方发表的官方基线经 API（A.2）。
5. **工具表**：read_guide、read_working 只读，原写"只改笔记"。
6. **Algorithm 1 细节**：read_pixels 的注释是"和 inspect 一样"，去掉"照抄"的断言；"工具说明没附在文中"与 §3.2 指向 A.4 的说法冲突，改为"agent 实际收到的说明是否就是 A.4 的文字，未查到"。
7. **笔记在其他基准**：原写"论文没说"，其实 Prompt 2 要求 GameWorld、AI GameStore 用 GUIDE.md 和 WORKING.md，Fig. 14 的 Minecraft 例子也写了 GUIDE.md；只有 history 是否开放未查到。
8. **(v)–(vi)**：原写"(v)–(vi) 即完整 VISTA"，只有 (vi) 是完整 VISTA。
9. **单次还是均值**：Tab. 1/2 的 99.00 不等于三次均值 99.04，推算为单次运行；§5 的 GameWorld、BabyVision 数字等于均值。原写"未查到"。
10. **消融模型**：第 4.6 节和第 8.2 节原写"全部消融都在 GPT-5.6 Sol 上"，Fig. 8 是 GLM。
11. **Fig. 7(b)**：最近邻只对默认 8× 写明，其他倍数的插值方式未查到。
12. **与 v1 的关系**：第 15 行补"VISTA 自动给最后一帧，我们要调 observe"的差别；第 45 行和第 6 条把"局内记忆、不跨回合"改成更准的说法（GUIDE.md 跨关、跨 RESET 保留，跨关在结构上像我们同任务跨回合，但同一 agent、不过门、不冻结），并恢复旧卡里 PhyAgentOS 的 KNOWLEDGE.md；第 5 条补 `docs/design/implementation_notes.md` 第 154 行已记下二审未接线；第 2 条补同文件第 172 行已记 `view` 参数；第 9 条把 read_pixels 的作用补上 BabyVision 的用法，`previous_node` 补"代码复验锚点"，并写明游戏 prompt 只借结构不借措辞；回合时长标"推算，仿真时间"；"200K 下多次压缩"标为推断。
13. **建议表**：V3 名称恢复 KNOWLEDGE/LESSONS；V8 依据补 PhyAgentOS 教训须被后续结果证实；V10 的目标原写"§7 风险表'规划器循环 inspect'行"，这句其实在 §3.5 威胁表"超调用预算"行（设计第 143 行），已改；V11 的 read_pixels 说法同第 9 条。
14. **未查到汇总**：去掉"笔记工具在其他基准上是否开放"和"Tab. 1/2 是单次还是均值"；"没有附录 C、更广影响声明"改为"核查到的附录是 A.1–A.6、B.1–B.3，是否另有更广影响声明未查到"。
15. **相对旧卡的改动**第 14 条：补"游戏 prompt"一项的变化，以及旧卡"记忆只是索引"在仓库里查不到。
