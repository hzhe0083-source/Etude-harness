# VISTA: A Visual Harness for Reasoning in an Interactive World（arXiv 2610.02200v1）

Qiushi Han, Keya Hu, Linlu Qiu, Cathy Wu, Kaiming He（MIT）。提交 2026-10-01。代码 github.com/joshhhhhan/VISTA，MIT 许可。
来源：arxiv.org/html/2610.02200 和仓库 README，2026-10-11 读；只读到正文前 10 万字符，§6–7 和附录 A.5 后半没有读到。

## 一句话

给通用多模态模型加一层不训练的视觉 harness：观测直接给图，历史帧原样无损存着，模型按需回看、放大、读像素。ARC-AGI-3 的 25 个公开游戏上，Claude Opus 5.0 的 RHAE 从 40.68 到 100.00（§4.2）。非具身，环境是回合制游戏和静态图。

## 机制（§3.1，Fig. 3；App. A.4，Algorithm 1）

| 部件 | 做什么 |
| --- | --- |
| 视觉观测 | 渲染图直接给模型，不转成文字网格；ARC-AGI-3 用 64×64 帧 8× 最近邻放大到 512×512 PNG，每帧约 308 image token，文字网格约 4K（§4.3，App. A.2） |
| 无损视觉记忆 | 每一帧（含动画中间帧）按 turn 和 frame 编号原样存，独立于模型上下文 |
| inspect(question, views) | 空间上放大任一帧的矩形区域；时间上取回早先 turn 的帧，回放一个动作怎么展开 |
| read_pixels(question, views) | 把区域切成格子，返回精确像素值 |
| history | 事件或尝试记录 |
| 笔记 | GUIDE.md（长期）、WORKING.md（工作）由模型自己写；接近上下文上限时写交接摘要，开新上下文继续，笔记、记忆、动作历史都保留 |
| play | 唯一改变环境的工具，每 turn 恰一个动作；控制器强制串行 |

每 turn：收当前帧、可用动作和两份笔记 → 可选若干次 inspect / read_pixels → 预测结果、改笔记 → play 一次；prompt 要求 play 前写预期、play 后写观察到的变化（§3.2，Fig. 2，Prompt 1）。同一 prompt 用于所有游戏。harness 本身没有训练的模型。Claude Code 经 MCP 结果直接收图；Codex 由控制器在同一 turn 另外给图（App. A.4）。

## 消融（§4.3，Fig. 5；GPT-5.6 Sol，RHAE）

| 配置 | RHAE |
| --- | --- |
| 官方实现（文字网格，外部参照，访问路径不同） | 13.33 |
| (i) 改成 PNG 图 | 47.32 |
| (ii) 放宽动作和时间上限 | 51.66 |
| (iii) 连续对话 + 压缩 | 65.82 |
| (iv) 笔记 GUIDE / WORKING | 70.05 |
| (v) 无损记忆 + inspect | 94.10 |
| (vi) read_pixels，完整 VISTA | 99.00 |

- 最大的一步是 (v)：+24.05。
- 文字网格对图：成绩相当，动作略多；每局 token 71.9M 对 30.7M（Fig. 6）。
- 上下文 780K 时每局 105.7M token，200K 时 30.7M；降到 100K 只掉 0.4（Fig. 7a）。
- 放大倍数 4× 比 8× 省约 75% image token，成绩相当；2× 掉分，16× 无益（Fig. 7b）。
- 开源 GLM-5.3 Flash 加完整 VISTA 到 66.93（Fig. 8）。

## 其他基准（§5）

GameWorld 34 个浏览器游戏 170 任务、AI GameStore 10 个游戏、BabyVision 39 道视觉追踪题（41.0% → 63.2%）。都胜过同模型的官方基线；具体数字在 Fig. 11，正文没有。

## 作者自述的局限（§6–7，经二手摘要）

依赖模型已有能力；模型发布晚于公开基准，游戏可能在训练数据里；私有集没测；大上下文 token 涨得快；未来工作才碰具身任务。

## 对我们的 harness 的关系

1. 和纪要"原始 trace 全留，记忆只是索引"同一个原则，VISTA 给了实现形态：帧按 turn/frame 编号无损存，模型用 inspect 按需取，不是每次都喂。这是"VLM 每次看不看图"这个未定问题的第四个选项：默认只给最后一帧，历史帧可查。
2. GUIDE.md / WORKING.md 是模型自写的持久笔记，和 PhyAgentOS 的 KNOWLEDGE.md / LESSONS.md 一类，是一个可进化面候选。VISTA 没有验收门，笔记直接生效。
3. 不借的：游戏 prompt、play 的回合制假设（我们是 25 Hz 动作块）、read_pixels（我们的图是相机 RGB，不是网格）。它的分数是在回合制、可完整回放的环境上得的，不能推到连续控制。
