# D 部分：成熟 LLM agent 系统的系统层级模块（事实底稿）

- 抓取日期：2026-10-10。代码文件行号以当天各仓库 `main` 分支为准。
- 每条事实后的〔键〕对应文末"来源索引"中的官方文档页 URL、仓库文件路径或论文节号。
- 只整理事实。"没查到"写"未查到"。最后一节"与机器人系统的对应"整段为推断，已标注。

---

## 0. 名称澄清与仓库现状

- **"pi agent" 有两个含义，下面分开写：**
  - **pi（编码 agent）**：Mario Zechner 的 `badlogic/pi-mono`。GitHub 已把它重定向到 `earendil-works/pi`，npm 包名改为 `@earendil-works/pi-coding-agent`〔P-README；GitHub API `repos/badlogic/pi-mono` 返回 `full_name: earendil-works/pi`〕。
  - **π（Physical Intelligence）**：`Physical-Intelligence/openpi`，是 π0、π0-FAST、π0.5 的开源推理与微调栈，不是 LLM agent〔O-README〕。
- **OpenHands**：`All-Hands-AI/OpenHands` 已重定向到 `OpenHands/OpenHands`，后者现在是 "Agent Canvas"（自托管控制台，可运行 OpenHands、Claude Code、Codex、Pi 等多种 agent）〔OH-canvas〕。agent 内核在 `OpenHands/software-agent-sdk`，`All-Hands-AI/agent-sdk` 也重定向到这里〔OH-sdk；GitHub API〕。下表 OpenHands 一行按 software-agent-sdk（V1）填写。
- **SWE-agent**：README 声明它已被 mini-swe-agent 取代，并建议今后改用 mini-swe-agent〔SA-README〕。
- **Terminal-Bench / Terminus-2**：`laude-institute/terminal-bench` 已重定向到 `harbor-framework/terminal-bench-1`。Terminus-2 目前有两份实现：`terminal-bench-1` 的 `terminal_bench/agents/terminus_2/` 和 `harbor-framework/harbor` 的 `src/harbor/agents/terminus_2/`〔T2-tb, T2-harbor〕。**未核对 RRSI 论文用的是哪一份、哪个版本。**
- **Codex**：仓库 `docs/*.md` 基本都是指向官网的一两行存根。官网 `developers.openai.com/codex/llms.txt` 的索引指向 `learn.chatgpt.com/docs/*.md`〔CX-repo, CX-llms〕。
- **Claude Code**：`docs.anthropic.com/en/docs/claude-code/overview` 会重定向到 `code.claude.com/docs/en/overview`（curl 实测）。

---

## 1. 模块对照表

为控制表格宽度，10 个模块列拆成三张表，每张表每个系统仍占一行。

### 表 1：主循环 / 上下文 / 权限与沙箱

| 系统 | 1 主循环与工具调用协议 | 2 上下文管理 | 3 权限与沙箱 |
|---|---|---|---|
| **pi（编码 agent）** | 用系统提示、活动分支、工具定义构造请求；provider 流式返回文本和工具调用；Pi 执行每个调用并记录结果，算一轮；有工具结果或排队消息就开下一轮〔P-how〕。工具调用走各 provider 原生函数调用，经 `pi-ai` 统一多 provider API〔P-README〕。默认 `toolExecution: "parallel"`：先顺序预检，再并发执行，toolResult 按 assistant 原顺序写回；批内任一工具标 `executionMode: "sequential"` 时整批串行〔P-agent〕。默认工具 `read/bash/edit/write`，另有 `grep/find/ls/powershell`；`codemode` 工具让模型写 JS 并行调用其他工具，只有脚本输出回到模型〔P-cli, P-set〕。`steer`（当前 assistant 轮之后进入）和 `followUp`（agent 做完后进入）两种消息队列〔P-how, P-agent〕。 | 自动压缩在 `contextTokens > contextWindow − reserveTokens`（默认 16384）时触发：从尾部回溯保留 `keepRecentTokens`（默认 20k），更早的部分交给 LLM 生成结构化摘要，写成 `CompactionEntry`，原条目仍留在会话文件里〔P-comp〕。检查时机是工具结果追加后、下一次模型请求前；provider 报上下文溢出时做一次"压缩后重试"；`/compact [指令]` 可手动触发〔P-comp〕。`/tree` 离开某分支时可以对它做分支摘要〔P-sess〕。技能只把名称和描述放进系统提示，正文按需读取〔P-skill〕。 | **没有内置权限系统**：以启动进程的 OS 权限运行，不会逐个工具调用询问〔P-README, P-sec〕。"project trust" 只决定是否加载 `.pi/` 下的设置、扩展、技能、MCP 等（默认 `defaultProjectTrust: "ask"`，决定存于 `~/.pi/agent/trust.json`），不限制工具调用〔P-sec〕。隔离靠外部手段：plain Docker、Docker Sandboxes、OpenShell，或 Gondolin 扩展（只把内置工具和 `!` 命令路由进本地 micro-VM）〔P-cont〕。权限门、受保护路径、危险命令确认、sandbox 都以**示例扩展**提供：`permission-gate.ts`、`protected-paths.ts`、`confirm-destructive.ts`、`sandbox/`〔P-ex〕。 |
| **π / openpi** | 不是 LLM agent。客户端 `Runtime` 每一步依次调用 `environment.get_observation()`、`agent.get_action(obs)`、`environment.apply_action(action)`、各 `Subscriber.on_step`，并按 `max_hz` 睡眠限频〔O-runtime〕。**远程推理协议**：<br>• 传输：websocket（`websockets` 库，`compression=None`，`max_size=None`），载荷为 msgpack（带 numpy 扩展）。<br>• 交互：连接后服务器先发 metadata，之后一问一答，obs 字典进、action 字典出。`actions` 形状为 `(action_horizon, action_dim)`，另附 `server_timing.infer_ms` 和 `prev_total_ms`。<br>• 出错：服务端先发文本 traceback，再以 INTERNAL_ERROR 关闭连接。<br>• 其他：`/healthz` 健康检查，默认端口 8000，客户端可带 `Authorization: Api-Key` 头〔O-server, O-client, O-serve〕。<br>**观测格式**：键例如 `observation/image`、`observation/wrist_image`、`observation/state`、`prompt`；图像在客户端 resize 到 224 并转 uint8，state 不用预先归一化，由服务端处理〔O-remote〕。<br>**动作分块**：`ActionChunkBroker` 每次调用只返回 chunk 里的一步，chunk 用完才重新推理〔O-broker〕。<br>**频率**：示例中 ALOHA 真机 horizon 25、`max_hz=50`；ALOHA 仿真 10、`max_hz=50`；DROID 每 8 步重新查询，15 Hz；LIBERO 每 5 步重规划〔O-ex〕。<br>**模型**：`Pi0Config` 默认 `action_horizon=50`、`action_dim=32`；`pi05_droid` 为 15，`pi0_fast_droid` 为 10〔O-cfg〕；flow matching 采样默认积分 10 步〔O-pi0〕。固定推理时延的官方数字：未查到（README 只给显存需求，推理 >8 GB）〔O-README〕。 | `Policy.infer(obs)` 只接收当前这一帧观测字典；客户端和服务端都没查到历史缓冲或记忆机制〔O-policy, O-server〕。语言 token 上限 `max_token_len`：π0 为 48，π0.5 为 200〔O-cfg〕。 | 未查到权限或安全层。服务端默认绑定 `0.0.0.0`，鉴权只有客户端可选的 Api-Key 头〔O-server, O-client〕。`Runtime` 跑完所有 episode 后调用 `environment.reset()`，源码注释说明这在真机上用于回到 home 位姿〔O-runtime〕。 |
| **Claude Code** | 模型回复文本和/或若干 `tool_use`；harness 执行后把结果作为 user 消息回填；一直循环到某次回复不再调用工具〔CC-loop〕。SDK 消息类型：`SystemMessage`（init/compact_boundary 等）、`AssistantMessage`（每个 content block 一条）、`UserMessage`（工具结果）、`StreamEvent`、`ResultMessage`（含 usage、cost、session_id）〔CC-loop〕。**并行**：只读工具（Read/Glob/Grep 及标为只读的 MCP 工具）可并发；改状态的工具（Edit/Write/Bash）串行；自定义工具默认串行，设 `readOnlyHint` 后才可并行〔CC-loop〕。内置工具：Read/Edit/Write、Glob/Grep、Bash、WebSearch/WebFetch、ToolSearch、Agent/Skill/AskUserQuestion/TaskCreate/TaskUpdate〔CC-loop〕。`max_turns` 和 `max_budget_usd` 可截断循环〔CC-loop〕。运行中输入的消息会在当前工具调用结束后、同一轮内被读取〔CC-how〕。 | 接近上限时先清除旧工具输出，不够再做摘要；`/compact [focus]` 手动压缩；CLAUDE.md 里的 "Compact Instructions" 段会影响摘要内容；单个大输出导致反复压缩时，会停下来报 thrashing 错误〔CC-how〕。有 `PreCompact`/`PostCompact` hook；SDK 压缩后发出 `compact_boundary` 消息〔CC-hooks, CC-loop〕。CLAUDE.md 每次请求都重新注入〔CC-loop〕。MCP 工具定义默认延迟加载（tool search）；技能正文按需加载；子代理用独立上下文〔CC-how〕。auto memory 的 `MEMORY.md` 在会话开始加载前 200 行或 25KB〔CC-how, CC-mem〕。 | **权限模式**：`default`（Manual，只有读取免询问）、`acceptEdits`、`plan`、`auto`（由另一个分类器模型在后台审查）、`dontAsk`（未预批准的一律拒绝）、`bypassPermissions`（文档要求只在隔离的容器或 VM 中使用）〔CC-modes〕。v2.1.283 起，交互式终端和 VS Code 的内置起始模式是 `auto`；`claude -p` 和 SDK 在会拉取 feature flag 的会话中起始为 `default`〔CC-modes〕。<br>**规则**：按 deny → ask → allow 的顺序，首个匹配决定结果；managed settings 优先级最高，其他层不能覆盖；PreToolUse hook 返回 allow 也绕不过 deny/ask 规则〔CC-perm〕。批准者是用户（Manual 模式）或分类器（auto 模式）。<br>**Bash 沙箱**（默认关闭）：macOS 用 Seatbelt，Linux/WSL2 用 bubblewrap+socat；写入限于工作目录和临时目录；读取默认覆盖机器大部分位置；网络经本地代理按域名白名单放行，白名单初始为空。沙箱只包住 shell 命令，Read/Edit/WebFetch、hooks、本地 MCP 服务器都在沙箱外〔CC-sbx〕。 |
| **OpenAI Codex CLI** | 协议文档：核心通过 Submission Queue / Event Queue 与 UI 通信。一个 Task 由多个 Turn 组成；每个 Turn 向 Model（Responses API）发请求、流式接收、执行命令或打补丁，输出作为下一个 Turn 的输入；某个 Turn 没有输出时 Task 结束；每个 Session 同时最多一个 Task。该文档自注可能与代码不完全一致〔CX-proto〕。工具 handler 包括 `unified_exec`（exec_command/write_stdin）、`apply_patch`、`plan`、`view_image`、`tool_search`、MCP、`multi_agents`（spawn_agent/send_input/wait_agent/close_agent）、`request_user_input`、`request_permissions` 等〔CX-tools〕。**并行**：每个工具在注册表里声明是否支持并行；支持的取共享读锁并发执行，其余取独占写锁串行〔CX-par〕。CLI 可在运行中 steer 当前 turn〔CX-cli〕。 | 自动压缩阈值 `model_auto_compact_token_limit`，不设则用模型默认值；`compact_prompt` 可覆盖压缩提示；`/compact` 手动压缩〔CX-cfgref, CX-cmd〕；有 `PreCompact`/`PostCompact` hook〔CX-hooks〕。AGENTS.md 指令链在启动时构建一次，总量上限 `project_doc_max_bytes` = 32 KiB〔CX-agents〕。Memories 默认关闭：从历史 rollout 中抽取，在后续会话注入，抽取和合并所用模型可配置〔CX-cfgref, CX-mem〕。技能采用渐进披露：先只给名称和描述，选中后才读 SKILL.md〔CX-skill〕。 | **两层控制**：`sandbox_mode`（`read-only` / `workspace-write` / `danger-full-access`）× `approval_policy`（`on-request` / `never` / `granular`；`untrusted` 已废弃）〔CX-sec〕。<br>**默认值**：受版本控制的目录用 Auto 预设（workspace-write + on-request），非版本控制目录用 read-only；workspace-write 默认关网；可写根下的 `.git`、`.agents`、`.codex` 递归只读〔CX-sec〕。<br>**OS 实现**：macOS 用 Seatbelt（`/usr/bin/sandbox-exec`）；Linux/WSL2 用 bubblewrap，旧式策略可走 Landlock；Windows 用原生沙箱〔CX-sbx, CX-core〕。<br>**细粒度控制**：`network_proxy` 按域名 allow/deny；exec policy 规则决定哪些命令可在沙箱外运行；带 destructive 注解的 MCP/应用工具调用总要批准〔CX-sec, CX-llms〕。<br>**批准者**：用户；设 `approvals_reviewer = "auto_review"` 时，由一个独立 reviewer 代理审查越出沙箱的请求〔CX-rev〕。`codex exec` 默认 read-only 沙箱〔CX-exec〕。 |
| **OpenHands（software-agent-sdk V1）** | `Agent.step()` 单步模型，依次：处理待确认动作 → 调用 condenser → 用事件历史请求 LLM（超出窗口就发 `CondensationRequest`）→ 工具调用解析为 `ActionEvent`、文本解析为 `MessageEvent` → 需要确认时置 `WAITING_FOR_CONFIRMATION` → 执行得到 `ObservationEvent`。Agent 本身无状态，事件构成只追加的日志〔OH-agent, OH-ev〕。LLM 经 LiteLLM 调用，支持 Chat Completions 与 Responses API〔OH-llm〕。工具由 Pydantic 的 Action/Observation 加 Executor 组成〔OH-tool〕；示例工具有 TerminalTool、FileEditorTool、TaskTrackerTool〔OH-sdk〕。**并行**：`tool_concurrency_limit` 默认 1（串行），大于 1 属实验特性〔OH-par〕。`max_iterations` 默认 500〔OH-state〕。 | Condenser 有 NoOp、LLMSummarizing（默认实现）、Pipeline 三种。`LLMSummarizingCondenser(max_size, keep_first)` 在事件数超过 `max_size` 时触发：保留前 `keep_first` 个事件，其余替换为 LLM 摘要〔OH-cond〕。上下文窗口溢出时发出 CondensationRequest〔OH-agent〕。技能：AGENTS.md 始终加载；另有关键词触发和 AgentSkills 渐进披露〔OH-skill〕。 | **确认策略**：`AlwaysConfirm` / `NeverConfirm` / `ConfirmRisky`，会话默认 `NeverConfirm`〔OH-sec, OH-state〕。`LLMSecurityAnalyzer` 让 LLM 在生成动作时标注风险（LOW/MEDIUM/HIGH/UNKNOWN），配合 ConfirmRisky 决定是否请用户批准〔OH-sec〕。<br>**执行环境**：`LocalWorkspace`（进程级隔离）和 `RemoteWorkspace`（经 agent-server，容器或 VM 级隔离）；有 Docker、Apptainer、API-based sandbox 的部署指南〔OH-ws, OH-llms〕。<br>Agent Canvas 的"无沙箱"安装方式会让 agent 拥有完整文件系统访问权限〔OH-canvas〕。 |
| **SWE-agent** | `Agent.forward()` 请求模型并执行动作；`SWEEnv` 是 SWE-ReX 的薄包装，在本地 Docker 或远端（Modal/AWS）容器里开 shell 会话执行命令〔SA-arch〕。解析器可选 function_calling、thought_action、XML、JSON、bash code block 等〔SA-parse〕。`FunctionCallingParser` 要求每次回复**恰好一个**工具调用，否则报错并重问（`max_requeries` 默认 3），所以是单步执行、无并行〔SA-parse, SA-agents〕。默认配置由 bash 工具、`edit_anthropic` 编辑器和 `review_on_submit_m` 组成〔SA-default〕。 | History processors 全是规则式，不调用 LLM：default、last_n_observations、tag_tool_call_observations、closed_window、cache_control、remove_regex、image_parsing〔SA-hist〕。默认配置只用 `cache_control`（`last_n_messages: 2`）〔SA-default〕。每个动作后执行 `state` 命令得到 JSON（如 open_file、working_dir），填入下一步的提示模板〔SA-tools〕。 | 没有审批机制，隔离靠容器〔SA-arch〕。**命令过滤**：前缀黑名单（vim、vi、emacs、nano、nohup、gdb、less、`tail -f`、`python -m venv`、make）加独立命令黑名单（单独的 python、bash、sh 等交互式命令），命中时返回错误模板〔SA-tooly〕。**超时**：单条命令 30 s，总执行 1800 s，连续超时 3 次即终止〔SA-tooly〕。 |
| **Terminus-2** | 模型每轮输出 JSON（或 XML），字段为 `analysis`、`plan`、`commands:[{keystrokes, duration}]`、`task_complete`。harness 按顺序把 keystrokes 发进 tmux 会话（在 Docker 容器内），每条等待 `duration`（上限 60 s），再把终端输出返回给模型〔T2-prompt, T2-tb, T2-paper App. F〕。唯一的工具是一个无头终端，只用 Bash 命令和按键，可以操作 vim、方向键等〔T2-paper §3.1, App. F〕。一轮可发一批命令；不用函数调用 API，没有并行。终端输出截断到 10,000 字节，保留首尾〔T2-tb〕。 | 剩余 token 少于 8000 时主动摘要；harbor 版中 `proactive_summarization_threshold` 默认 8000，`enable_summarize` 默认 True〔T2-tb, T2-harbor〕。遇到上下文超限错误时，先删消息腾出约 4000 token 再摘要〔T2-tb〕。**摘要流程共三次调用**：当前代理写交接摘要 → 新代理看摘要和当前屏幕，至少提 5 个问题 → 用旧上下文回答。之后历史重置为 system、提问、回答交接三部分〔T2-tb〕。 | 运行在任务的 Docker 容器里〔T2-paper App. F〕。审批或权限分级：未查到。 |

### 表 2：子代理 / 技能与扩展 / 验证与自检 / 会话持久化与恢复

| 系统 | 4 子代理 | 5 技能与扩展（含加载时机） | 6 验证与自检 | 7 会话持久化与恢复 |
|---|---|---|---|---|
| **pi（编码 agent）** | 内置不提供，README 原文 "skips features like sub-agents and plan mode"〔P-README〕。示例扩展 `subagent/`：每个子代理是一个独立的 `pi` 子进程，有独立上下文窗口，可并行，流式显示进度，按代理统计 turns/tokens/cost；附带 scout/planner/reviewer/worker 四个代理定义，以及 implement、implement-and-review 等工作流模板〔P-ex〕。 | **扩展**：TypeScript 模块，启动时经 jiti 在 Pi 进程内加载，位置为 `~/.pi/agent/extensions/` 和 `.pi/extensions/`（后者需 project trust）；可注册工具、命令、快捷键、provider、MCP 服务器、事件处理器、渲染器〔P-ext, P-conf〕。<br>**事件**：`tool_call`（可改参数或阻断）、`tool_result`（多个处理器依次改写）、`context`、`before_agent_start`、`turn_end` / `agent_before_settle`（可请求再跑一轮）、`provider_stream_event`、`user_bash`、`session_start` / `session_shutdown` 等〔P-ext〕；agent-core 层另有 `beforeToolCall` / `afterToolCall`〔P-agent〕。<br>**技能**：遵循 Agent Skills 规范；启动时扫描，把 name/description/path 写进系统提示，任务匹配时由模型读取 SKILL.md；`/skill:name` 可强制加载〔P-skill〕。<br>**MCP**：stdio 或 streamable HTTP，配置在 `~/.pi/agent/mcp.json` 和 `.pi/mcp.json`〔P-mcp〕。<br>另有 prompt 模板和主题；以上都可打包成 Pi package，经 npm/git 分发〔P-README〕。 | 内置的独立 reviewer 或自动测试：未查到。安全文档建议人工审查 diff，同时说明看 transcript、审查改动都"不构成安全边界"〔P-sec〕。示例 subagent 扩展里有 reviewer 代理和 implement-and-review 工作流（示例，非内置）〔P-ex〕。开发侧：`packages/evals` 用 vitest-evals 做行为评测，包括在容器中对比 with_docs / without_docs〔P-evals〕。 | **格式**：JSONL，路径 `~/.pi/agent/sessions/--<path>--/<timestamp>_<session-id>.jsonl`。首行是 session header，其余条目以 `id/parentId` 构成树，分支都在同一文件里；当前格式版本 v3〔P-fmt〕。系统提示和工具集的变化也以系统消息补丁的形式记入〔P-fmt〕。<br>**恢复与分支**：`--continue` / `--resume` / `/resume`；`/tree` 在文件内换分支，`/fork` 从某条早先的用户消息开新会话，`/clone` 复制活动分支〔P-sess〕。<br>**文件回滚**：没有内置，会话树只回退对话；有 `git-checkpoint.ts` 示例扩展〔P-ex〕。<br>`/export` 导出 HTML 或 JSONL，`/share` 上传分享〔P-sess〕。 |
| **π / openpi** | 不适用；未查到。 | 每个机器人平台通过 `XxxInputs` / `XxxOutputs` 变换（如 `policies/libero_policy.py`）以及 `DataConfig` / `TrainConfig`（`training/config.py`）接入〔O-README〕。客户端 `Subscriber` 有三个钩子：`on_episode_start`、`on_step`、`on_episode_end`，只能观察，不能阻断〔O-runtime〕。 | 推理期自检：未查到。评测以示例脚本提供，LIBERO 推荐 Docker 化的 policy server + eval 脚本流程〔O-README〕。 | `serve_policy.py --record` 会用 `PolicyRecorder` 包住策略，每步把输入和输出 `np.save` 到 `policy_records/step_N`〔O-serve, O-policy〕；配套 `examples/policy_records.ipynb`〔O-tree〕。训练 checkpoint 写进 `checkpoints/`；PyTorch 训练支持 `--resume`〔O-README〕。 |
| **Claude Code** | **派发**：通过 `Agent` 工具。子代理有独立上下文窗口、自定义系统提示、工具白/黑名单、独立权限模式，完成后只把摘要返回主对话〔CC-sub〕。<br>**内置**：Explore 和 Plan（只读，跳过 CLAUDE.md），general-purpose 等。<br>**自定义**：`.claude/agents/*.md`，frontmatter 字段包括 tools、disallowedTools、model、permissionMode、mcpServers、hooks、maxTurns、skills、memory、isolation 等〔CC-sub〕。<br>**隔离与嵌套**：`isolation: worktree` 给子代理一个临时 git worktree；默认最多嵌套 3 层（`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`）；fork 子代理继承完整对话，但也只回传结果；支持后台子代理〔CC-sub〕。<br>更大规模的并行另有 agent teams、dynamic workflows、agent view〔CC-sub, CC-llms〕。 | **Skills**：`.claude/skills/<name>/SKILL.md`，遵循 Agent Skills 标准，并扩展了调用控制、在子代理中执行、动态上下文注入。会话开始时只加载描述，使用时才加载正文；`.claude/commands/` 已并入 skills〔CC-skill〕。<br>**Hooks**：配置在 settings.json、插件、技能或代理的 frontmatter 中。共 33 个事件（按 2026-10-10 文档计数），包括 SessionStart、UserPromptSubmit、PreToolUse、PermissionRequest、PostToolUse、PostToolUseFailure、PostToolBatch、SubagentStart/Stop、Stop、PreCompact/PostCompact、InstructionsLoaded、FileChanged、SessionEnd 等。处理器有 5 种：command、http、mcp_tool、prompt（单轮模型判定）、agent（起子代理核查，实验性）。匹配的 hooks 并行执行。exit 2 在 PreToolUse 上阻断调用，在 Stop 上阻止结束〔CC-hooks〕。<br>**MCP**：stdio、HTTP（streamable）、SSE（已弃用）；项目级 `.mcp.json`，用户级 `~/.claude.json`；SDK 宿主可注册进程内的 `sdk` 型服务器〔CC-mcp〕。<br>**Plugins**：清单为 `.claude-plugin/plugin.json`，可打包 skills、agents、hooks、MCP servers、mods，经 marketplace 安装〔CC-plug〕。 | 文档把循环描述为 gather context → take action → verify results 三个阶段；验证做不做、怎么做（如跑测试）由模型决定〔CC-how〕。<br>可配置的独立检查有：<br>• Stop hook：exit 2 阻止结束。<br>• prompt / agent 型 hook：交给另一个模型或子代理判定〔CC-hooks〕。<br>• `/goal`：每轮结束后由一个模型判断完成条件是否满足〔CC-goal〕。<br>• `/code-review`（本地）和 ultrareview（云端多 reviewer 代理，每条发现都独立复现验证）〔CC-ur〕。<br>• GitHub Code Review 产品〔CC-llms〕。 | **Transcript**：每条消息、工具调用和结果写入 `~/.claude/projects/<project>/<session-id>.jsonl`。条目是内部格式，会随版本变化；官方建议用 `/export`、`-p --output-format json/stream-json` 或 SDK 读取〔CC-sess〕。<br>**恢复与分叉**：`--continue` / `--resume` 用同一个 session ID 继续追加；`--fork-session` / `/branch` 生成新 ID〔CC-how, CC-sess〕。<br>**Checkpoint**：每个开启新一轮的用户 prompt 之前，对编辑工具改过的文件做快照；保留最近 100 个，约 30 天后清理。`Esc Esc` 或 `/rewind` 可以只恢复代码、只恢复对话或两者都恢复，也可以从某个点起做摘要。Bash 改动的文件和外部修改不被跟踪；对远程系统的副作用无法回滚〔CC-ckpt, CC-how〕。 |
| **OpenAI Codex CLI** | 主线程按需派生多个子代理并行工作，最后由主线程汇总结果〔CX-sub〕。<br>**内置**：`default`、`worker`、`explorer`。<br>**自定义**：`~/.codex/agents/` 或 `.codex/agents/` 下的 TOML 文件，必填 name、description、developer_instructions；可设 model、sandbox_mode、mcp_servers 等，不设的项继承父会话〔CX-sub〕。<br>**并发与权限**：`agents.max_concurrent_threads_per_session` 限制并发数；子代理继承当前沙箱策略和父 turn 的运行时覆盖；非交互模式下，需要新批准的动作直接失败并回报给父工作流〔CX-sub〕。`/agent` 可切换查看各代理线程〔CX-sub〕。 | **Skills**：从 cwd 向上到仓库根的 `.agents/skills`，以及用户、管理员、系统级位置〔CX-skill〕。<br>**Hooks**：`hooks.json` 或 `config.toml` 中的 `[hooks]` 表。事件有 SessionStart、UserPromptSubmit、PreToolUse、PermissionRequest、PostToolUse、PreCompact、PostCompact、SubagentStart/Stop、Stop、Interrupt、SessionEnd；同一事件的多个 command hook 并发启动；非托管 hook 要先审查并信任才会运行〔CX-hooks〕。<br>**MCP**：stdio 和 streamable HTTP，配置在 `config.toml`；项目级 `.codex/config.toml` 只对受信项目生效〔CX-mcp〕。<br>**Plugins**：可打包 MCP 和 hooks〔CX-hooks〕。<br>**嵌入**：通过 app-server 协议（旧的 Codex MCP server 已移除）〔CX-llms〕。 | • `/review`：启动一个专门的 reviewer，读取选定 diff，输出分级问题，不修改工作树〔CX-rev〕。<br>• auto-review：由独立 reviewer 代理审越界的批准请求〔CX-rev〕。<br>• Stop hook：可在 turn 结束时运行自定义校验〔CX-hooks〕。<br>• `/goal`：设定持续目标〔CX-cmd〕。<br>• 云端 PR code review〔CX-rev〕。 | **Rollout**：JSONL 文件 `~/.codex/sessions/rollout-<timestamp>-<conversation_id>.jsonl`，另有 `archived_sessions` 目录〔CX-roll〕；另有 `history.jsonl`（由 `history.persistence` 控制）〔CX-cfgadv〕。<br>**恢复与分叉**：`codex resume`（默认限当前目录，可加 `--last` / `--all`）、`/resume`、`/fork`、`codex exec resume`〔CX-cmd〕。<br>**回滚**：app-server 的 `thread/revert` 只截断持久化历史，**不回滚文件**〔CX-revert〕；CLI 文档建议在任务前后自己打 Git checkpoint〔CX-cli〕。内置的文件级 checkpoint/rewind：未查到。 |
| **OpenHands（V1 SDK）** | `TaskToolSet`：父代理用 prompt 加子代理类型调用 task 工具；TaskManager 创建或恢复子会话，同步跑完后把结果返回，可凭 task ID 恢复〔OH-task〕。`tool_concurrency_limit` 大于 1 时可并行委派〔OH-par〕。 | **Skills**：扩展版 AgentSkills，可加关键词触发；触发类型有 always、keyword、task、path；兼容解析 `.cursorrules`、`agents.md`〔OH-skill〕。<br>**Hooks**：PreToolUse（exit 2 阻断）、PostToolUse、UserPromptSubmit（可阻断）、Stop（可阻断）、SessionStart、SessionEnd；仓库内配置文件为 `.openhands/hooks.json`〔OH-hooks, OH-repo〕。<br>**MCP**：`openhands/sdk/mcp`，支持同步和异步执行〔OH-mcp〕。<br>**插件**：marketplace，默认源为 `OpenHands/extensions`〔OH-sdk〕。<br>`.openhands/setup.sh` 在每次开始处理仓库时运行〔OH-repo〕。 | • Critic（实验）：LLM critic 给动作和历史打 0–1 的成功概率，低于阈值时自动追加提示重试〔OH-crit〕。<br>• Stuck detector（默认开启）：同一动作-观测重复 4 次以上、同一动作报错 3 次以上、连续独白 3 条以上、两种动作交替 6 轮以上、反复上下文溢出时停止〔OH-stuck〕。<br>• Stop hook 质量闸门〔OH-repo〕。<br>• 评测 harness 在独立仓库 `OpenHands/benchmarks`，覆盖 SWE-Bench、SWE-Bench Pro、GAIA、Commit0、OpenAgentSafety、ProgramBench〔OH-bench〕。 | `persistence_dir/<conversation-id>/base_state.json` 加 `events/event-NNNNN-<event-id>.json`（每个事件一个 JSON 文件）；用同一 ID 和同一目录即可恢复〔OH-pers〕。暂停/恢复、fork 有专门指南；agent-server 提供下载 trajectory 的接口〔OH-llms〕。文件回滚：未查到。 |
| **SWE-agent** | 未查到子代理。`RetryAgent` 会多次尝试，再由 `ScoreRetryLoop` 或 `ChooserRetryLoop` 调用 reviewer/chooser 模型打分或挑选〔SA-rev〕。 | **Tool bundle**：`bin/` 下的可执行文件，加 `config.yaml`（signature、docstring、arguments）和 `install.sh`，安装进容器使用〔SA-tools〕。另有提示模板和 demonstrations；全部由单个 YAML 驱动〔SA-README, SA-default〕。MCP、hooks、skills：未查到。 | • ACI 设计：编辑命令附带 linter，语法错误的编辑被拒绝；文件查看器每次显示 100 行；搜索命令只列出有匹配的文件；空输出时返回固定提示语〔SA-aci；SA-paper §3〕。<br>• `review_on_submit_m`：第一次 submit 时展示 diff 和检查清单（重跑复现脚本、删掉复现脚本、还原测试文件），要求再 submit 一次〔SA-default〕。<br>• Reviewer / Chooser，见第 4 列〔SA-rev〕。 | 每个实例输出 `<instance_id>.traj`（JSON，每步记录 response、thought、action、observation、state、query），外加 `config.yaml` 和分级日志；批量运行还有 `preds.json`、`run_batch_exit_statuses.yaml`〔SA-traj〕。`run-replay` 可重放轨迹中的动作，`traj-to-demo` 把轨迹转成演示，另有 inspector 查看器〔SA-cli〕。运行中断后的恢复：未查到。 |
| **Terminus-2** | 没有任务委派。harbor 版把摘要用的三次 LLM 调用称为 "summarization subagents"，单独计量 token 和 cost〔T2-harbor〕。 | 未查到；Terminus-2 自身没有 skills、hooks、MCP 机制。 | `task_complete: true` 要连续确认两次：第一次之后返回终端输出，要求再确认一次〔T2-tb〕。真正的判分由任务自带的测试脚本完成〔T2-paper §2.3〕。 | **terminal-bench-1 版**：每个 episode 一个目录 `episode-N/{debug.json, prompt.txt, response.txt}`，并用 asciinema 录屏、打标记〔T2-tb〕。**harbor 版**：写 ATIF 格式的 `trajectory.json`；`linear_history` 选项会在摘要时把轨迹拆成多个文件〔T2-harbor, T2-atif〕。 |

### 表 3：成本与可观测性 / 配置与项目记忆 / 许可与语言

| 系统 | 8 成本与可观测性 | 9 配置文件与项目记忆 | 10 开源许可与语言 |
|---|---|---|---|
| **pi（编码 agent）** | 每条 assistant 消息带 `Usage`：input、output、cacheRead、cacheWrite、reasoning、totalTokens，以及分项 cost〔P-msg〕。页脚显示上下文占用、累计用量和费用；`/session` 显示 token 和 cost〔P-usage, P-sess〕。`--mode json` 输出 JSONL 事件流（agent_start、turn_start、message_* 等）；RPC 模式通过 stdin/stdout 收发 JSONL〔P-json〕。`pi-telemetry` 包只定义 span/attribute/event 契约和一个内存参考实现，**不带 exporter**，由应用自行适配 OTel、Sentry 等〔P-tel〕。`/debug` 写出 `pi-debug.log`〔P-usage〕。 | **上下文文件**：`AGENTS.override.md`、`AGENTS.md`、`CLAUDE.md`，从 agent 目录、工作目录及其各级父目录加载，不受 project trust 控制〔P-conf, P-sec〕。<br>**系统提示**：`SYSTEM.md` 替换，`APPEND_SYSTEM.md` 追加。<br>**目录**：用户级 `~/.pi/agent/`（settings.json、mcp.json、models.json、auth.json、extensions/、skills/、prompts/、themes/）；项目级 `.pi/`，结构相同〔P-conf〕。 | MIT；TypeScript（Node ≥ 22.19）〔P-README〕。 |
| **π / openpi** | 每次推理返回 `policy_timing.infer_ms` 和 `server_timing`〔O-policy, O-server〕。训练日志输出到控制台和 W&B（`wandb_enabled` 默认 True，`log_interval` 100，`save_interval` 1000）〔O-traincfg〕。 | Python dataclass 配置，按名字选用（如 `get_config("pi05_droid")`、`scripts/train.py pi05_libero`）；训练前要先跑 `scripts/compute_norm_stats.py` 生成归一化统计〔O-README〕。<br>**微调入口**：`scripts/train.py`（JAX，有 LoRA 配置变体）；`scripts/train_pytorch.py`（不支持 π0-FAST、LoRA、FSDP、EMA、混合精度）。数据须先转成 LeRobot 格式。显存需求：LoRA >22.5 GB，全量 >70 GB〔O-README〕。 | Apache-2.0，另附 `LICENSE_GEMMA.txt`；Python，JAX 和 PyTorch 两套实现〔O-README, O-tree〕。 |
| **Claude Code** | `/usage` 显示本会话 token 用量和按列表价在本地估算的费用〔CC-cost〕。`ResultMessage` 和 `-p --output-format json` 带 `total_cost_usd` 及分模型明细，也是客户端估算〔CC-loop, CC-head〕。OpenTelemetry 通过 `CLAUDE_CODE_ENABLE_TELEMETRY=1` 开启，输出三类数据：<br>• 指标：如 `claude_code.token.usage`、`claude_code.cost.usage`、`claude_code.lines_of_code.count`。<br>• 事件（logs 协议）。<br>• traces（beta）：span 如 `claude_code.interaction`、`claude_code.llm_request`、`claude_code.tool`、`claude_code.hook`〔CC-mon〕。 | **CLAUDE.md 层级**：<br>• managed：如 Linux 的 `/etc/claude-code/CLAUDE.md`<br>• 用户级：`~/.claude/CLAUDE.md`<br>• 项目级：`./CLAUDE.md` 或 `./.claude/CLAUDE.md`<br>• 本地：`CLAUDE.local.md`<br>工作目录及以上各级的文件在启动时加载，子目录的按需加载；`@path` 导入最多 4 跳；没有 CLAUDE.md 时读 AGENTS.md；`.claude/rules/` 放按路径生效的规则。auto memory 由 Claude 自己写（分 user、feedback、project 等类型）。官方明确 CLAUDE.md 是"上下文而非强制配置"，要强制执行需用 PreToolUse hook〔CC-mem〕。<br>**设置文件**：`~/.claude/settings.json`、`.claude/settings.json`、`.claude/settings.local.json`、managed settings〔CC-perm, CC-dir〕。 | Claude Code 本体是专有软件：`anthropics/claude-code` 的 LICENSE.md 写明 "All rights reserved"，受商业条款约束〔CC-lic〕。实现语言官方文档未说明，只提到 Agent SDK 捆绑了 "native Claude Code binary"〔CC-loop〕。Agent SDK Python 版为 MIT；沙箱运行时 `@anthropic-ai/sandbox-runtime` 为 Apache-2.0、TypeScript〔CC-lic〕。 |
| **OpenAI Codex CLI** | `/status` 显示 token 用量和剩余上下文〔CX-cmd〕。`[otel]` 默认关闭，开启后导出日志事件（`codex.api_request`、`codex.tool_decision`、`codex.tool_result` 等）、指标（如 `codex.tool.call`、`codex.tool.call.duration_ms`）和 traces；`log_user_prompt` 默认不记录用户提示〔CX-cfgadv, CX-cfgref〕。默认会向 OpenAI 发送匿名的用量和健康数据〔CX-cfgadv〕。`codex exec --json` 输出 JSONL 事件（thread.started、turn.started/completed/failed、item.*、error）〔CX-exec〕。实验性的 `features.rollout_budget` 可设 token 预算〔CX-cfgref〕。 | **配置文件**：`~/.codex/config.toml`（用户级）、`.codex/config.toml`（受信项目）、`requirements.toml`（托管约束）〔CX-cfgref, CX-repo〕。<br>**AGENTS.md 加载顺序**：全局先读 `~/.codex/AGENTS.override.md`，没有则读 `AGENTS.md`；项目从仓库根逐级走到 cwd，每个目录最多取一个文件（顺序为 override → AGENTS.md → fallback 文件名）；越靠近 cwd 的越靠后拼接，因此优先。`/init` 可生成 AGENTS.md〔CX-agents, CX-cmd〕。 | Apache-2.0；Rust（`codex-rs`）〔CX-repo〕。 |
| **OpenHands（V1 SDK）** | `llm.metrics` 记录每次调用的 token、cost、latency；`conversation.conversation_stats` 汇总会话内所有 LLM（含 condenser 用的）的费用〔OH-met〕。内置 OpenTelemetry tracing，覆盖 agent step、工具调用、LLM 调用和会话生命周期，可发往任意 OTLP 后端〔OH-obs〕。 | 仓库根的 `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` 由 `load_project_skills` 自动发现〔OH-skillg〕。`.openhands/` 目录下放 setup.sh、hooks.json 和 skills（旧称 microagents）〔OH-repo〕。 | MIT。SDK 与 agent-server 为 Python；`OpenHands/OpenHands`（Agent Canvas）为 TypeScript〔OH-sdk, OH-canvas, GitHub API〕。 |
| **SWE-agent** | `InstanceStats` 记录 instance_cost、tokens_sent、tokens_received、api_calls；可设 `per_instance_cost_limit`、`total_cost_limit`、`per_instance_call_limit`；经 LiteLLM 计费〔SA-models〕。`quick-stats` 汇总各实例的 exit_status〔SA-cli〕。 | 单个 YAML 配置（如 `config/default.yaml`）。项目级 Markdown 记忆文件：未查到。 | MIT；Python〔GitHub API〕。 |
| **Terminus-2** | harbor 版记录 n_input_tokens、n_output_tokens、n_cache_tokens 和 cost；`collect_rollout_details` 可收集 token IDs 与 logprobs，用于 RL〔T2-harbor〕。 | 项目记忆文件：未查到。参数经 harbor 的 `--ak` 传入（如 max_turns、enable_summarize）〔T2-harbor, T2-pre〕。 | Apache-2.0；Python（两个仓库都是）〔GitHub API〕。 |

---

## 2. π（Physical Intelligence）补充：agent 层、自我改进层、RECAP / π\*0.6 是否开源

- openpi 只包含 π0、π0-FAST、π0.5 三个模型，其中 π0.5 仅支持 flow matching 头〔O-README〕。训练配置清单里没有 π0.6 / π\*0.6 条目；value function、advantage 相关代码：未查到〔O-traincfg；O-tree〕。
- `openpi_client.runtime.Agent` 的接口是"给观测、返回动作"，实现类是包装策略的 `PolicyAgent`。它不是 LLM agent 层，也没有规划或自我改进逻辑〔O-runtime〕。
- **π\*0.6 / RECAP 论文**（arXiv 2511.14759v2）：
  - §IV-A：分布式价值函数，预测"到完成还需多少步"，以语言为条件（Eq. 1）。
  - §IV-B：优势条件化。用价值函数估计优势，二值化为 I_t = 𝟙(A > ε) 作为策略的条件输入（Eq. 3）；人类纠正数据强制 I_t = True。
  - §IV-C 与 Algorithm 1：迭代流程为"用上一轮策略采数据 → 并入数据集 → 训练价值函数 → 训练策略"。
  - §V-D：数据采集期间有专家遥操作介入纠正。
  - §V-A：动作为 50 Hz 的关节角加夹爪指令。
  - 在抓到的论文正文中**没有找到代码或权重发布声明**。注意：这一点是经 WebFetch 工具摘要得出的，没有逐字核对全文。PI 博客 `pi.website/blog/pistar06` 抓取失败（返回 403 或 429）。
  - **结论（事实层面）**：π\*0.6 / RECAP 不在 openpi 中；是否经其他渠道发布：未查到。
- **同组织其他仓库**〔PI-org〕：
  - `openpi-basic-control`：C++ 机械臂控制库，每条臂一个 `pi_control_node` 进程，经 ZeroMQ 与 Python 主进程通信，支持 Yam、ARX_X5、FR3。README 自称是最小示例，可能不维护；GitHub 上未标注 license。
  - `pi-data-sharing`：LeRobot 数据集校验器，校验 `custom_metadata.csv`，并交叉检查"intervention only for eval episodes"（介入只能出现在评测 episode 中）。
  - `real-time-chunking-kinetix`：两篇 RTC（real-time chunking）论文的仿真实验代码。
  - openpi 主仓库中**未查到 RTC 实现**：对 `pi0.py`、`policy.py`、`pi0_pytorch.py`、`action_chunk_broker.py`、`droid/main.py` 检索 rtc / real-time / inpaint / guidance 等关键词，命中均为 0。

---

## 3. 收敛的设计（≥3 个系统以相近形式出现）

1. **项目级 Markdown 指令文件，按目录层级发现**
   - Claude Code：CLAUDE.md 多级加载，没有 CLAUDE.md 时读 AGENTS.md〔CC-mem〕。
   - Codex：AGENTS.md，从仓库根到 cwd 逐级拼接，上限 32 KiB〔CX-agents〕。
   - pi：AGENTS.md / CLAUDE.md，从 agent 目录、cwd 和各级父目录加载〔P-conf〕。
   - OpenHands：仓库根的 AGENTS.md / CLAUDE.md / GEMINI.md 自动加载〔OH-skillg〕。
   - 共同点：三家（CC、Codex、pi）支持 `*.override.md` 或 `.local.md` 一类的个人覆盖文件〔CC-mem, CX-agents, P-conf〕。
   - 不具备的系统：SWE-agent、Terminus-2（未查到）。
2. **SKILL.md 技能目录，先给描述、按需加载正文（Agent Skills 规范）**：Claude Code〔CC-skill〕、Codex〔CX-skill〕、pi〔P-skill〕、OpenHands（另加关键词触发）〔OH-skill〕。
3. **同名生命周期 hooks（PreToolUse / PostToolUse / UserPromptSubmit / Stop / SessionStart / SessionEnd），exit 2 或显式拒绝即可阻断**：Claude Code〔CC-hooks〕、Codex〔CX-hooks〕、OpenHands〔OH-hooks〕。pi 的形式不同，用进程内扩展事件：`tool_call` 可阻断，`agent_before_settle` 可请求继续〔P-ext〕。
4. **MCP 客户端（stdio + streamable HTTP），分用户级和项目级配置，项目级需信任**：Claude Code〔CC-mcp〕、Codex（项目级仅受信项目）〔CX-mcp〕、pi（项目级需 trust）〔P-mcp〕、OpenHands〔OH-mcp〕。
5. **接近上下文上限时，用 LLM 摘要替换旧历史，保留最近部分；另有手动 `/compact`**：
   - Claude Code（先清旧工具输出再摘要）〔CC-how〕。
   - Codex（`model_auto_compact_token_limit`）〔CX-cfgref〕。
   - pi（`reserveTokens` / `keepRecentTokens`，原条目保留在文件里）〔P-comp〕。
   - OpenHands（`LLMSummarizingCondenser`，`keep_first` 加摘要）〔OH-cond〕。
   - Terminus-2（剩余 <8000 token 时摘要，三段式交接）〔T2-tb〕。
   - SWE-agent 走的是规则式裁剪，不用 LLM 摘要〔SA-hist〕。
   - CC、Codex、pi 都把压缩做成可挂钩的事件：PreCompact/PostCompact，或 pi 的 `session_before_compact`〔CC-hooks, CX-hooks, P-comp〕。
6. **"技术边界"与"批准策略"分成两层，配合分级模式**：
   - Claude Code：权限模式加 deny/ask/allow 规则，另有 OS 级 Bash 沙箱〔CC-modes, CC-perm, CC-sbx〕。
   - Codex：`sandbox_mode` × `approval_policy`〔CX-sec〕。
   - OpenHands：确认策略加风险分析器，另有容器 workspace〔OH-sec, OH-ws〕。
   - 两家用同一套 OS 机制：Linux 上 bubblewrap，macOS 上 Seatbelt〔CC-sbx, CX-sbx〕。
   - **用另一个模型代替人做批准**：CC 的 auto 模式分类器〔CC-modes〕、Codex 的 auto-review reviewer 代理〔CX-rev〕、OpenHands 的 LLMSecurityAnalyzer 风险标注〔OH-sec〕。
   - 反例：pi 明确不内置权限，只做 trust 加文档化的容器方案〔P-sec〕。SWE-agent 和 Terminus-2 只靠容器（SWE-agent 另有命令黑名单）〔SA-tooly, T2-paper〕。
7. **子代理使用独立上下文，只回传结果或摘要**：Claude Code〔CC-sub〕、Codex（主线程汇总）〔CX-sub〕、OpenHands TaskToolSet〔OH-task〕、pi（示例扩展，独立进程）〔P-ex〕。
   - 子代理权限：CC 可单独设置，也继承父会话规则；Codex 继承父沙箱策略〔CC-sub, CX-sub〕。
   - 内置"只读探索型"子代理：CC 的 Explore/Plan、Codex 的 explorer〔CC-sub, CX-sub〕。
8. **追加式会话日志加 resume / fork**：
   - Claude Code：`~/.claude/projects/*.jsonl`〔CC-sess〕。
   - Codex：`~/.codex/sessions/rollout-*.jsonl`〔CX-roll〕。
   - pi：`~/.pi/agent/sessions/*.jsonl`，树结构〔P-fmt〕。
   - OpenHands：每个事件一个 JSON 文件〔OH-pers〕。
   - 评测类 harness 是"每实例一个轨迹文件"：SWE-agent 的 `.traj`〔SA-traj〕、Terminus-2 的 ATIF `trajectory.json`〔T2-atif〕。
   - 共同限制：**对话回退不等于文件回退**。只有 Claude Code 内置文件快照，而且不覆盖 Bash 改动〔CC-ckpt〕；Codex 的 `thread/revert` 不回滚文件〔CX-revert〕；pi 只有 git-checkpoint 示例〔P-ex〕。
9. **机器可读的 headless 事件流（JSONL 或 SDK）**：Claude Code（`-p --output-format stream-json` / Agent SDK）〔CC-head, CC-loop〕、Codex（`exec --json`）〔CX-exec〕、pi（`--mode json`、RPC、SDK）〔P-json〕、OpenHands（REST/WebSocket agent-server）〔OH-sdk〕。
10. **按会话的 token 与费用计量，并可设预算或步数上限**：
    - Claude Code：`max_turns`、`max_budget_usd`〔CC-loop〕。
    - Codex：实验性的 `rollout_budget`〔CX-cfgref〕。
    - pi：每条消息带 Usage.cost〔P-msg〕。
    - OpenHands：`max_iterations` 500，加 `conversation_stats`〔OH-state, OH-met〕。
    - SWE-agent：`per_instance_cost_limit`〔SA-models〕。
    - Terminus-2：max_turns，加 token/cost 统计〔T2-harbor〕。
11. **OpenTelemetry 导出**：Claude Code〔CC-mon〕、Codex〔CX-cfgadv〕、OpenHands〔OH-obs〕。pi 只提供 telemetry 契约，不带 exporter〔P-tel〕。
12. **"结束前闸门"：agent 想停下时，再过一道检查**：
    - Claude Code：Stop hook exit 2 会阻止结束〔CC-hooks〕。
    - Codex：Stop hook 做自定义校验〔CX-hooks〕。
    - OpenHands：Stop hook 质量闸门〔OH-repo〕。
    - SWE-agent：`review_on_submit_m` 要求看过 diff 后再次 submit〔SA-default〕。
    - Terminus-2：`task_complete` 二次确认〔T2-tb〕。
    - pi：`agent_before_settle` 可要求再跑一轮〔P-ext〕。
13. **由独立模型担任 reviewer / critic / 评判者**：
    - Claude Code：`/goal` 评判模型、prompt/agent hook、ultrareview〔CC-goal, CC-hooks, CC-ur〕。
    - Codex：`/review` 专门 reviewer〔CX-rev〕。
    - OpenHands：critic 给 0–1 分并触发重试〔OH-crit〕。
    - SWE-agent：Reviewer/Chooser 在多次尝试间挑选〔SA-rev〕。
14. **运行中插话（steering）**：Claude Code（工具调用结束后同轮读取）〔CC-how〕、pi（steer / followUp 队列）〔P-agent〕、Codex（steer 当前 turn）〔CX-cli〕、OpenHands（运行中发消息有专门指南）〔OH-llms〕。
15. **计划 / 只读模式**：Claude Code 的 `plan` 模式加内置 Plan 子代理〔CC-modes, CC-sub〕；Codex 的 `/plan` 和 `read-only` 沙箱〔CX-cmd, CX-sec〕；pi 有 plan-mode 示例扩展（非内置）〔P-ex〕。
16. **停滞与重复检测**：OpenHands stuck detector〔OH-stuck〕；Claude Code 自动压缩 thrashing 时停止〔CC-how〕；SWE-agent 连续超时 3 次、格式错误重问 3 次即止〔SA-tooly, SA-agents〕。三家的形式差别较大。

---

## 4. 与机器人系统的对应（**推断**，以下整段均为推断，非文档事实）

> 每条格式：收敛模块 → 机器人控制回路里最接近的对应物；哪里对不上。

1. **项目级 Markdown 指令文件** → 对应机器人侧的任务与场景说明、机器人配置（标定、关节限位、工作空间），以及送给 VLA 的语言 prompt。**对不上**：
   - LLM 侧把它当软性上下文。CC 文档明说是 "context, not enforced configuration"〔CC-mem〕。机器人的限位、力矩这类约束必须由控制器硬执行。
   - VLA 的语言输入只有几十到两百 token（π0 48、π0.5 200）〔O-cfg〕，装不下长文档。
2. **SKILL.md 按需加载** → 对应技能库或原语库，比如 openpi 按任务发布的 expert checkpoint〔O-README〕。**对不上**：
   - LLM 侧加载一个技能的成本是 token；机器人换技能是换权重或换策略，有显存占用和切换延迟。
   - "根据描述路由到技能"在 LLM 里靠文本匹配，在机器人里还取决于视觉状态。
3. **生命周期 hooks（PreToolUse / PostToolUse / Stop）** → 对应动作下发前的安全过滤或动作屏蔽、执行后的状态检查、episode 结束判定。openpi 的 `Subscriber.on_step` 只能观察，不能阻断〔O-runtime〕。**对不上**：
   - LLM hooks 是同步跑外部进程，CC 默认超时 600 s〔CC-hooks〕。
   - 示例中的 50 Hz 控制回路每步只有 20 ms 预算〔O-ex〕。
4. **MCP 工具接口** → 对应设备驱动、ROS 服务，或 openpi 那种"obs → action chunk"的 websocket RPC〔O-server〕。**对不上**：
   - MCP 是请求-响应的离散调用，没有时序和频率保证。
   - 机器人控制要求定频、低延迟、流式，而且服务端异常的处理方式是断开连接〔O-server〕。
5. **LLM 摘要式上下文压缩** → 对应机器人策略的历史帧窗口或 episode 记忆。openpi 推理只用当前帧〔O-policy〕，相当于没有这一层。**对不上**：
   - 摘要是有损的自然语言，已知失败模式是"早期指令丢失"〔CC-how〕。
   - 机器人的世界状态由物理环境保存，可以重新观测；但"已试过哪些动作、哪些失败了"不在观测里，需要另外显式记录。
6. **分级权限加 OS 沙箱、用模型代替人批准** → 对应仿真与真机的分级、速度和力限幅、工作空间边界、急停，以及人类遥操作接管（π\*0.6 §V-D 中的介入）。**对不上**：
   - 软件沙箱能完全拦住副作用，CC 还能回滚文件〔CC-ckpt〕；物理动作不可撤销，这和 CC 文档"对远程系统的副作用无法回滚"〔CC-how〕是同一类问题。
   - LLM 的批准是阻塞式等人；15–50 Hz 的回路〔O-ex〕无法逐步人工审批，只能用阈值或分类器，形式上接近 CC 的 auto 分类器和 OpenHands 的风险分级〔CC-modes, OH-sec〕。
7. **子代理隔离上下文、只回传摘要** → 对应分层控制：高层规划器给低层策略下发子任务。**对不上**：子代理回传的是文字结论；低层策略"回传"的是物理世界的变化，高层只能重新感知，拿不到执行者自己的报告。
8. **追加式会话日志加 resume/fork** → 对应 episode 数据集（LeRobot 格式、openpi `PolicyRecorder` 每步存的 npy〔O-policy〕）和 rosbag。**对不上**：
   - transcript 可以在同一上下文里 fork 或重放；SWE-agent 的 `run-replay` 依赖确定性环境〔SA-cli〕。
   - 物理 episode 无法回到同一初始状态，重放同样的动作也得不到同样的观测。
9. **headless 事件流** → 对应 policy server 加客户端 Runtime 的观测/动作流〔O-server, O-runtime〕。**对不上**：LLM 事件流以"一轮"为粒度、可以缓冲；控制流以固定周期为粒度，迟到的动作就是错的动作。
10. **token / cost 计量与预算上限** → 对应推理时延（`infer_ms`〔O-server〕）、控制频率抖动、`max_episode_steps`〔O-runtime〕、人工介入次数（`pi-data-sharing` 中的 intervention 字段〔PI-org〕）。**对不上**：机器人的主要成本是机时、硬件磨损和人力介入，不是 token；到达步数上限后的 `reset()` 本身就是一个有风险的物理动作〔O-runtime〕。
11. **OpenTelemetry trace** → 对应控制回路的时间戳日志和推理计时字段〔O-server〕。**对不上**：LLM trace 的 span 是"一次 LLM 请求或一次工具调用"；机器人需要逐控制周期、跨进程（策略、驱动、传感器）对齐时间戳，span 粒度不一样。
12. **结束前闸门（Stop hook、二次确认、review_on_submit）** → 对应任务成功检测器或 reward/value 模型；π\*0.6 价值函数预测"到完成还要多少步"〔PI-star §IV-A〕。**对不上**：代码任务可以跑测试做确定性判分〔T2-paper §2.3〕；机器人的成功判定通常靠学习得到的检测器或人工标注，会误判。
13. **独立 reviewer / critic** → 对应 π\*0.6 的价值函数打分加优势条件化〔PI-star §IV-A/B〕，和 OpenHands critic 的 0–1 成功概率〔OH-crit〕形式相近。**对不上**：LLM critic 在提交前起作用，低分可以撤回重试；机器人里低分动作已经执行，打分只能事后变成训练数据的优势标签〔PI-star §IV-B〕。
14. **工具调用的"返回值"** → 对应执行之后的传感器观测。**对不上**：观测不确定、有噪声、有延迟；用动作分块时，观测返回的频率低于动作频率。例如 DROID 示例每 8 步、约 0.53 s 才重新查询一次策略〔O-ex〕，中间这几步是开环执行。
15. **运行中插话（steering）** → 对应遥操作员实时介入纠正（π\*0.6 §V-D）。**对不上**：LLM 的插话在一批工具调用结束后的轮次边界才生效〔CC-how, P-how〕；机器人的接管要在动作级、几乎即时完成。
16. **计划 / 只读模式** → 对应"只在仿真里执行"或"只做感知、不发动作"的模式。**对不上**：只读模式下 LLM 仍能读取真实文件系统；机器人的"只读"通常意味着连真实观测都拿不到（仿真），或者需要被动观察时的安全姿态。
17. **停滞与重复检测** → 对应抓取反复失败、长时间没有进展的检测。**对不上**：LLM 侧靠比较文本化的动作和观测是否完全相同〔OH-stuck〕；机器人的连续状态几乎不会完全相同，"重复"要靠阈值或学习模型来定义。

---

## 5. 来源索引

### pi（编码 agent）
仓库：<https://github.com/earendil-works/pi>（原 `badlogic/pi-mono`，已重定向）
- P-README：`README.md`
- P-cagent：`packages/coding-agent/README.md`
- P-how：`packages/coding-agent/docs/how-pi-works.md`
- P-agent：`packages/agent/README.md`（并行/串行 L111–116，beforeToolCall/afterToolCall L118–120，steer/followUp）
- P-cli：`packages/coding-agent/docs/cli.md`（默认工具 L128，codemode L155–174，tool_search L182）
- P-set：`packages/coding-agent/docs/settings.md`（defaultTools L40–44）
- P-comp：`packages/coding-agent/docs/compaction.md`
- P-sess：`packages/coding-agent/docs/sessions.md`
- P-fmt：`packages/coding-agent/docs/session-format.md`
- P-conf：`packages/coding-agent/docs/configuration.md`
- P-sec：`packages/coding-agent/docs/security.md`
- P-cont：`packages/coding-agent/docs/containerization.md`
- P-ext：`packages/coding-agent/docs/extensions.md`
- P-skill：`packages/coding-agent/docs/skills.md`
- P-mcp：`packages/coding-agent/docs/mcp.md`
- P-json：`packages/coding-agent/docs/json.md`
- P-msg：`packages/coding-agent/docs/message-types.md`（Usage L72–84）
- P-usage：`packages/coding-agent/docs/usage.md`
- P-tel：`packages/telemetry/README.md`
- P-evals：`packages/evals/README.md`
- P-ex：`packages/coding-agent/examples/extensions/`（目录列表）及 `subagent/README.md`

### π / openpi
仓库：<https://github.com/Physical-Intelligence/openpi>
- O-README：`README.md`
- O-remote：`docs/remote_inference.md`
- O-server：`src/openpi/serving/websocket_policy_server.py`
- O-client：`packages/openpi-client/src/openpi_client/websocket_client_policy.py`
- O-broker：`packages/openpi-client/src/openpi_client/action_chunk_broker.py`
- O-runtime：`packages/openpi-client/src/openpi_client/runtime/{runtime.py, agent.py, subscriber.py}`
- O-serve：`scripts/serve_policy.py`（port 8000，`--record`）
- O-policy：`src/openpi/policies/policy.py`（`policy_timing` L103，`PolicyRecorder` L113–134）
- O-cfg：`src/openpi/models/pi0_config.py`（L25–39）；`src/openpi/training/config.py`（各配置的 action_horizon：L603、617、631、745）
- O-traincfg：`src/openpi/training/config.py`（ema L490，log_interval L514，save_interval L516，wandb L526）
- O-pi0：`src/openpi/models/pi0.py`（`sample_actions` 的 `num_steps=10`，L217–228）
- O-ex：`examples/aloha_real/main.py`、`examples/aloha_sim/main.py`、`examples/droid/main.py`（`DROID_CONTROL_FREQUENCY = 15`，`open_loop_horizon = 8`）、`examples/libero/main.py`（`replan_steps = 5`）
- O-tree：GitHub API `git/trees/main?recursive=1`（含 `LICENSE_GEMMA.txt`、`examples/policy_records.ipynb`）
- PI-star：π\*0.6 论文 arXiv 2511.14759v2，<https://arxiv.org/html/2511.14759v2>，§IV-A、§IV-B、§IV-C（Algorithm 1）、§V-A、§V-D
- PI-org：<https://github.com/Physical-Intelligence> 下的 `openpi-basic-control/README.md`、`pi-data-sharing/README.md`、`real-time-chunking-kinetix/README.md`

### Claude Code（<https://code.claude.com/docs/en/>）
- CC-how：`how-claude-code-works`
- CC-loop：`agent-sdk/agent-loop`
- CC-mem：`memory`
- CC-hooks：`hooks`
- CC-sub：`sub-agents`
- CC-perm：`permissions`
- CC-modes：`permission-modes`
- CC-sbx：`sandboxing`
- CC-ckpt：`checkpointing`
- CC-sess：`sessions`
- CC-skill：`skills`
- CC-mcp：`mcp`
- CC-plug：`plugins/overview`
- CC-cost：`costs`
- CC-mon：`monitoring-usage`
- CC-head：`headless`
- CC-goal：`goal`
- CC-ur：`ultrareview`
- CC-dir：`claude-directory`
- CC-llms：<https://code.claude.com/docs/llms.txt>（页面索引，含 agent teams、workflows、Code Review 等页面标题与摘要）
- CC-lic：<https://github.com/anthropics/claude-code/blob/main/LICENSE.md>；GitHub API：`anthropics/claude-agent-sdk-python`（MIT）、`anthropics/sandbox-runtime`（Apache-2.0）

### OpenAI Codex CLI
- CX-repo：<https://github.com/openai/codex>（`README.md`、`docs/*.md` 存根、`docs/config.md`）
- CX-llms：<https://developers.openai.com/codex/llms.txt>（索引指向 learn.chatgpt.com）
- CX-sec：<https://learn.chatgpt.com/docs/agent-approvals-security.md>
- CX-sbx：<https://learn.chatgpt.com/docs/sandboxing.md>
- CX-agents：<https://learn.chatgpt.com/docs/agent-configuration/agents-md.md>
- CX-sub：<https://learn.chatgpt.com/docs/agent-configuration/subagents.md>
- CX-hooks：<https://learn.chatgpt.com/docs/hooks.md>
- CX-cfgref：<https://learn.chatgpt.com/docs/config-file/config-reference.md>
- CX-cfgadv：<https://learn.chatgpt.com/docs/config-file/config-advanced.md>
- CX-cmd：<https://learn.chatgpt.com/docs/developer-commands.md?surface=cli>
- CX-cli：<https://learn.chatgpt.com/docs/codex/cli.md>
- CX-exec：<https://learn.chatgpt.com/docs/non-interactive-mode.md>
- CX-skill：<https://learn.chatgpt.com/docs/build-skills.md>
- CX-mcp：<https://learn.chatgpt.com/docs/extend/mcp.md>
- CX-mem：<https://learn.chatgpt.com/docs/customization/memories.md>
- CX-rev：<https://learn.chatgpt.com/docs/code-review.md>；<https://learn.chatgpt.com/docs/sandboxing/auto-review.md>
- CX-proto：`codex-rs/docs/protocol_v1.md`
- CX-par：`codex-rs/core/src/tools/parallel.rs`（L141–216）；`codex-rs/core/src/tools/router.rs`（L246）
- CX-tools：`codex-rs/core/src/tools/handlers/`（目录列表）
- CX-roll：`codex-rs/rollout/src/recorder.rs`（L1、L82、L101）；`codex-rs/rollout/src/lib.rs`（L86–87）
- CX-revert：`codex-rs/app-server-protocol/schema/typescript/v2/ThreadRevertParams.ts`
- CX-core：`codex-rs/core/README.md`

### OpenHands
- OH-canvas：<https://github.com/OpenHands/OpenHands>（`README.md`）
- OH-sdk：<https://github.com/OpenHands/software-agent-sdk>（`README.md`）
- OH-state：`openhands-sdk/openhands/sdk/conversation/state.py`（`NeverConfirm` L123，`max_iterations=500` L461，`stuck_detection=True` L462）
- OH-agent：<https://docs.openhands.dev/sdk/arch/agent.md>
- OH-ev：<https://docs.openhands.dev/sdk/arch/events.md>
- OH-cond：<https://docs.openhands.dev/sdk/arch/condenser.md>；<https://docs.openhands.dev/sdk/guides/context-condenser.md>
- OH-sec：<https://docs.openhands.dev/sdk/arch/security.md>；<https://docs.openhands.dev/sdk/guides/security.md>
- OH-ws：<https://docs.openhands.dev/sdk/arch/workspace.md>
- OH-tool：<https://docs.openhands.dev/sdk/arch/tool-system.md>
- OH-llm：<https://docs.openhands.dev/sdk/arch/llm.md>
- OH-par：<https://docs.openhands.dev/sdk/guides/parallel-tool-execution.md>
- OH-task：<https://docs.openhands.dev/sdk/guides/task-tool-set.md>
- OH-hooks：<https://docs.openhands.dev/sdk/guides/hooks.md>
- OH-skill：<https://docs.openhands.dev/sdk/arch/skill.md>
- OH-skillg：<https://docs.openhands.dev/sdk/guides/skill.md>
- OH-mcp：<https://docs.openhands.dev/sdk/arch/mcp.md>
- OH-crit：<https://docs.openhands.dev/sdk/guides/critic.md>
- OH-stuck：<https://docs.openhands.dev/sdk/guides/agent-stuck-detector.md>
- OH-pers：<https://docs.openhands.dev/sdk/guides/convo-persistence.md>
- OH-met：<https://docs.openhands.dev/sdk/guides/metrics.md>
- OH-obs：<https://docs.openhands.dev/sdk/guides/observability.md>
- OH-repo：<https://docs.openhands.dev/openhands/usage/customization/repository.md>
- OH-bench：<https://github.com/OpenHands/benchmarks>（`README.md`）
- OH-llms：<https://docs.openhands.dev/llms.txt>（页面索引，含 fork、pause/resume、send-message-while-running、Docker/Apptainer/API sandbox、download-trajectory 等页面标题）

### SWE-agent
仓库：<https://github.com/SWE-agent/SWE-agent>
- SA-README：`README.md`
- SA-aci：`docs/background/aci.md`
- SA-arch：`docs/background/architecture.md`
- SA-tools：`docs/config/tools.md`
- SA-traj：`docs/usage/trajectories.md`
- SA-cli：`docs/usage/cli.md`
- SA-default：`config/default.yaml`
- SA-parse：`sweagent/tools/parsing.py`（单工具调用校验 L444–447）
- SA-tooly：`sweagent/tools/tools.py`（blocklist L37–62，超时 L139–150）
- SA-hist：`sweagent/agent/history_processors.py`
- SA-models：`sweagent/agent/models.py`（成本上限 L73–78，`InstanceStats` L292–298）
- SA-rev：`sweagent/agent/reviewer.py`
- SA-agents：`sweagent/agent/agents.py`（`max_requeries` L158）
- SA-paper：arXiv 2405.15793，§2 "The Agent-Computer Interface"、§3 "SWE-agent: Designing an ACI for Software Engineering"

### Terminus-2 / Terminal-Bench
- T2-tb：<https://github.com/harbor-framework/terminal-bench-1>（原 `laude-institute/terminal-bench`），`terminal_bench/agents/terminus_2/terminus_2.py`（max_episodes L52–60，完成确认 L114–130，episode 日志 L137–150，输出截断 L167，回卷 L205–228，摘要 L230–302，主动摘要 L303–316，duration 上限 L484–485）
- T2-prompt：同仓库 `terminal_bench/agents/prompt-templates/terminus-json-plain.txt`
- T2-harbor：<https://github.com/harbor-framework/harbor>，`src/harbor/agents/terminus_2/terminus_2.py`（SubagentMetrics L74–80，Options L83–158，默认参数 L262–287）
- T2-paper：Terminal-Bench 论文 arXiv 2601.11868v1，§2.3 Verification、§3.1 Terminus 2、Appendix F Terminus 2
- T2-atif：<https://docs.harborframework.com/agents/atif.md>
- T2-pre：<https://docs.harborframework.com/agents/pre-integrated-agents.md>
