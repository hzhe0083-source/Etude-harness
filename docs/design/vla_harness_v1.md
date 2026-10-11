# VLA Harness v1 设计（可开工版）

日期 2026-10-11。引用格式：arXiv 号 + 节/表号；凭记忆的数字标"约"；未查到写"未查到"。本机核对过的代码事实标 [code]，路径见第 8 节。

## 1 决策摘要

**做什么。** 冻结 π0.5（官方 checkpoint `ckpt/RoboDojo/Pi_05/RoboDojo-sim-arx_x5-joint-0/59999`，14 维关节动作，块长 50）只做接触段；代码原语 `ee_move`（以实测 `ee_pose` 加增量生成绝对 `*_ee_pose` 小步伺服；`delta_ee_pose` 键只校验不执行 [code]）和 `gripper` 做非接触移动；代码验证器出五类判决；pi-mono 只在节点边界经 MCP 出节点序列。接入走 XPolicyLab：`deploy.py` 原样用官方 forwarder，我们的 harness 住在 policy server 进程里，拿不到环境对象。

**四个任务。** π0.5 官方分任务 SR 在 30–70% 的只有 2 个（put_bottles_into_dustbin 69.3、stack_bowls 38.0），触发降级规则：带宽放到 20–70%，再加 make_kong 26.7、fold_clothes 24.0、build_tower 24.0。选 **开发 = {stack_bowls, put_bottles_into_dustbin}，留出 = {build_tower, make_kong}**，fold_clothes 作替补（布料后置条件 RGB 难判）。数据来源：官方榜 Per-Task 页 SR 开关，2026-10-11 读取。

**两条回路，v1 最小进化面。** 记忆回路只进化节点序列库（无 LLM 编辑者，入库条件 = 离线官方 judge 成功 ∧ 全节点 Pass，坐标一律换成 `perceive("…")`）。harness 回路一次只开一个面，顺序：规划 prompt → 恢复规则 → SKILL.md。阈值、原语和验证器代码、门、打分器、禁读检查全部冻结（DGM 2505.22954 §3 固定元层；EmbodiedSWE 2609.27308 Table 14 把改阈值列为作弊）。VLM 自写笔记放 v1.1。

**门。** L0 静态（schema、AST 禁读、泄漏正则、diff 预算）→ L1 medoid 布局重跑 → L2 历史回归 → L3 留出配对 ΔSR（McNemar 单侧 p≤0.05 且 b−c≥ε）→ RRSI 成本规则。ε 不直接用 COAP 的 4：先用 A/A 重跑校准，预计 50 回合上 ε≈8–10（第 3 节）。

**评测期记忆冻结**，VLM 默认只看最后一帧（VISTA 式 inspect 回看），规划用 claude-sonnet-5-5、编辑用 claude-opus-5-5，每回合 ≤12 次调用。

**事实核查改变了四条决策（第 8 节）：** 单卡 16 GB 放不下 Isaac Sim + π0.5，π0.5 必须另卡；夹爪观测是指令回显，实测开度要走白名单访问器；ee/joint 在代码层可逐动作字典切换（不是按运行固定）；自采布局无官方工具，用公开 seed 0/1。

## 2 架构

| 进程 | 语言 / 位置 | 职责 | 和谁说话 |
|---|---|---|---|
| P4 评测客户端 | RoboDojo `main.py`，Isaac Sim 5.1，Docker（官方镜像仅仿真侧） | 跑官方布局、官方 judge、写 `_result.json`、录三路视频 | 经 ws+msgpack 调 P2 的 `update_obs/get_action/reset`；`deploy.py` = 官方 demo forwarder + 两行白名单增强（把 `step_lim` 和实测夹爪开度写进 `obs["additional_info"]`），冻结并记哈希 |
| P2 harness server | Python，XPolicyLab `policy/Etude/model.py` | 节点执行器、代码验证器、perceive 检测器、记忆只读、trace 写盘、调用计数 | 向 P3 要动作块；在节点边界经 MCP(stdio) 被 P1 调用，同时用 `pi --mode rpc` 驱动 P1 |
| P3 openpi policy server | `serve_policy.py`（XPolicyLab `policy/Pi_05/openpi/` 已 vendored），ws+msgpack，可在另一台机器 | 冻结 π0.5 推理 | 只和 P2；官方 Pi_05 适配器是进程内加载 openpi，这里多一跳是为了把 π0.5 放到第二张卡 |
| P1 规划器 | pi-mono 会话，`.pi/mcp.json` 指向 P2 | 出节点序列、读判决、按需 inspect | 工具只有 P2 暴露的 7 个；`tool_call` 扩展拦截其他一切 |
| P5 离线打分 | Docker，只读挂载 harness | 跑官方 `summarize`，产出门用聚合；审计样本目录对 P1/P6 不可读 | 读 P4 产物；写 `gate/` |
| P6 编辑者 | 另一个 pi-mono 会话，git worktree | 只能写 `evolvable/`；读 E 划分的 trace | 读 P5 的 E 聚合；不能读 V/T |
| 记忆库 | `memory_db/<task>/sequences.jsonl`，append-only | 过门序列 | P2 只读；写入者是离线脚本 |

**回路。** 快回路：P4→P2→P3，25 Hz，每块 50 步 2 s，节点内不等 VLM。慢回路：P2 在节点边界阻塞调用 P1，仿真暂停等结果；但官方评测客户端 `eval_env.py` 构造 `WsModelClient` 时不传 `request_timeout_s`，每次 `update_obs/get_action` 固定 120 s 上限，`deploy.yml` 里的值只被 XPolicyLab 的 debug 客户端读取 [code]（PhysicalRSI 的 1800 s 是它自己的私有 worker）。所以规划器 RPC 的硬超时设 90 s；超过 90 s 仍无结果时 `get_action` 返回 1 步保持块（重复当前实测关节目标）让回合继续，规划在后台线程续跑，下一次 `get_action` 再取结果；超时次数计入成本。离线：P5 打分 → 记忆写入 / 门。

**边界落地。** RoboDojo 把环境对象传给策略方 `deploy.py`，和 judge 同进程 [code]；所以隔离的对象是"写入者"：P2/P6 在另一个进程，`deploy.py` 冻结为官方 forwarder 加两行增强（只许访问 `get_obs/take_action/is_episode_end/get_running_env_idx_list/get_obs_batch/take_action_batch/step_lim/robot_manager.get_end_effector_real_val`，用显式 allow-list 而非只靠 deny-list）并记哈希；AST 检查对 `deploy.py`、P2 代码、`evolvable/` 全量跑，禁 PhysicalRSI `PRIVILEGED_ATTRIBUTES` 11 项（scene_manager、layout_manager、reward_manager、get_label_pose、get_instance_pose、get_instance_metadata、get_scene_object、get_functional_points、env_seeds、end_flag、take_action_cnt）再加 `success`、`mark_env_unstable`、`robot_manager.planner`、资产与布局文件。允许：三路 RGB、`*_arm_joint_state`、`*_ee_pose`、`*_ee_joint_state`（指令回显）、`step_lim`（预算）、白名单访问器 `gripper_width_measured()`（包 `get_end_effector_real_val`，真机有指尖编码器，ASPIRE 2607.00272 App. E.2 经验法则）。

## 3 进化回路规格

### 3.0 进化面排序（收益 / 作弊风险 / 评测成本 / 可回滚性）

| 候选面 | 收益 | 作弊风险 | 评测成本 | 可回滚 | v1 |
|---|---|---|---|---|---|
| 节点序列库 | 高：Harness VLA 2607.08448 §2.2 成功序列重放；RoboHarness 2607.18060 Fig. 4 去记忆桥 −26 pp | 中：坐标、布局泄漏 | 低：只要官方 Pass，不过门 | 高：按行删 | 记忆回路 |
| 规划 prompt | 中高：HarnessEvolve 2609.00829、RRSI 2609.24972 的主要进化对象；Astra 报告 2609.24170 §3.2 wiki recipe 进 prompt 即改结果 | 中：塞任务名、布局提示 | 高：每候选过 L3 | 高：git | harness 面 1 |
| 恢复规则（r、回退、块预算默认值） | 中：Zetta 2608.16590 §2.6.1 plans/；Spotter 2609.36808 Table 4 固定重试无增益，规则要带条件；Recova 2610.01178 Tab. 3 恢复技能 +10 pp，但那是训练出的恢复策略，仿真表无同策略无恢复对照、基线照抄，不能当冻结+代码恢复的预期收益 | 低中 | 高 | 高 | 面 2 |
| SKILL.md 原语说明 | 中：PhysEvo 2610.08995 App. A 工具与说明同版本；PyRUA-Lean 2610.01939 Table 3 去指南 −2～4 pp | 低 | 高 | 高 | 面 3 |
| VLM 自写笔记 GUIDE/LESSONS | 不明：VISTA 2610.02200 Fig. 5 (iv) +4.2；PhyAgentOS 2607.16636 §6.2 单调增长无剪枝 | 中：自述入库 | 中 | 中 | v1.1 |
| 验证器阈值 | 低 | 高：EmbodiedSWE 2609.27308 Table 14 | 高：放宽需漏检率证明 | 高 | 冻结，只收紧 |
| 原语与验证器代码、门、打分器、禁读检查 | — | 极高：DGM 2505.22954 §3 与附录（删日志绕过检测） | — | — | 冻结 + 3.5 节越权自测 |

适应度只用官方 `summarize` 的 SR（V 上配对差，Score 作平手裁决）加成本项（VLM token/回合、墙钟/回合）；验证器判决与过程分只记录，不参与选择。

### 3.1 八行表

| 行 | 记忆回路 | harness 回路 |
|---|---|---|
| 进化面 | `memory_db/<task>/sequences.jsonl`（只增删条目、改字段值） | 一次一个：`evolvable/prompts/planner.md` → `evolvable/rules/recovery.yaml`（r、回退策略、块预算默认值）→ `evolvable/skills/SKILL.md` |
| 编辑者 | 无 LLM；离线脚本 `memory/writer.py` | pi-mono 会话（claude-opus-5-5），每轮 1 个候选、1 个面、diff ≤40 行或 ≤3 条规则（RRSI 2609.24972 Eq. 4 的 L0 退火：3→1） |
| 证据 | E 划分回合的官方 judge（P5 离线）+ 节点判决 | E 划分的 trace、判决、按布局官方分；按"最早失败节点"聚类（RoboRSI 2610.12424 Eq. 1；HarnessEvolve 2609.00829 Table 4 按原因聚类） |
| 提案 | 成功序列去数值后入库；同任务同指令哈希去重，保留更短者（Harness VLA 2607.08448 App. A） | 一个 patch + 一句假设 + 目标 medoid 布局列表 |
| 门 | 官方成功 ∧ 全节点 Pass ∧ schema 通过（无数值 target） | L0 静态 → L1 medoid 重跑（≤5 回合，Zetta 2608.16590 §2.5.2）→ L2 历史回归集 F（≤20 回合，≤1 次失守）→ L3 配对留出（V=100 回合，b−c≥ε 且 McNemar p≤0.05）→ 成本 ΔC≤β0+β1·ΔSR（RRSI §3.3） |
| 回滚 | 按 `seq_id` 删行 + lineage 收据；原始 trace 不删 | git worktree 不合并即回滚；合并后可按收据 `commit` revert |
| 防作弊 | schema 拒数值与布局 id；评测期只读（哈希审计） | AST 禁读、泄漏正则（任务名/布局号/坐标）、阈值文件不在白名单、V/T 结果对 P6 不可读、候选不得原样重交 |
| 预算 | 每回合 0 次额外 VLM；离线打分 0 GPU（读 `_result.json`） | 每轮 ≈125 回合 + 编辑会话；公式见 3.4 |

### 3.2 划分与证据可用时段

| 划分 | 内容 | 谁能看 | 何时 |
|---|---|---|---|
| E | 开发任务 seed 0 布局 0–49（Gen 任务 25 std+25 random） | P6 看 trace 与官方分；记忆从这里积累 | 开发期 |
| V | 开发任务 seed 1 布局 0–49 | 只有 P5；P6 只收 accept/reject 一位 | 每个候选一次 |
| A/A | 现任 harness 在 V 上重跑 3 次 | 给 ε | M0、M1 各一次 |
| T | 开发任务 seed 2 + 留出任务 seed 0/1/2 | 只看一次 | M3 结束 |
| 备用 | seed 0 第 50–54 个布局（官方只用前 50 [code]） | smoke | 任意 |

同一 seed 的 A/A 重跑布局完全相同，剩余方差只来自 PhysX 非确定性和 flow-matching 采样，所以 δ_AA 量的是系统噪声而非布局噪声。协议 Hidden Verification 一节把"公开布局上手工适配"判为无效，在公开 seed 上积累的序列必须在 seed 2 和 random 变体上证明泛化（第 6 节条件 3）。官方 judge 只在 P5 离线给门用；评测期（T）记忆冻结，不入库。若开记忆入库只凭验证器 Pass，误判率 FPR 会按 FPR×失败回合数的比例写入错误序列，并被后续检索放大；M0 先测 FPR，v1 不开。

### 3.3 伪代码

```python
# P2: 每块一次，由 deploy.py 调用；obs 已由 update_obs 存入 self.hist
def get_action(self):
    if self.seq is None or self.cursor >= len(self.seq):
        self.seq = self.plan()                 # 节点边界：阻塞 MCP/RPC → P1，≤90 s（官方客户端硬上限 120 s），超时返回 1 步保持块并在后台续等；计 1 次调用
    n = self.seq[self.cursor]
    if n.executor == "vla":
        a = pi05.infer(self.hist[-1], prompt=n.instruction)[: n.stop.exec_steps]   # 默认 25
    elif n.executor == "ee_move":
        a = servo(perceive(n.args.target, view="wrist"), self.hist[-1])           # ≤k 条绝对 *_ee_pose = 实测位姿 ⊕ 增量
    else:
        a = gripper(n.args.cmd)
    if self.node_done(n):                      # 停滞块数 / max_chunks / 夹爪实测 / Blocked / SafetyStop
        v = verifier.check(n, self.hist)       # 代码判决；InsufficientEvidence 时最多 1 次 inspect 二审
        trace.log(n, v); self.cursor = self.advance(n, v)   # Pass→+1；Fail→r 次原地重试→退回上一已验证节点；退回后先用代码复验锚点后置条件与 scene_intact（非目标物体对 t=0 关键帧 not_moved，0 次 VLM；失败节点和将重跑节点要动的对象豁免，锚点自身依赖的对象除外），不成立则换更早锚点或 replan（请求带实测 scene_intact 与 anchor_invalidated）（Recova 2610.01178 Alg. 1 第 41–42 行）
    return a

# 记忆写入（离线，无 LLM）
for ep in episodes(E):
    if P5.official(ep).success and all(v.kind == "Pass" for v in ep.verdicts):
        store.append(task, strip_numeric(ep.seq), evidence=ep.refs, lineage=ep.id)

# harness 回路一轮
def evolve_round(k, surface):
    patch, hypothesis, medoids = P6.propose(surface, evidence(E), budget=b_k)
    if not L0(patch): return receipt("reject", "L0")
    wt = git.worktree(incumbent, patch)
    if not L1(wt, medoids) or not L2(wt, F): return receipt("reject", "L1/L2")
    b, c = paired_runs(wt, incumbent_cache, V)          # b: 候选胜, c: 现任胜
    ok = (b - c >= eps) and mcnemar_one_sided(b, c) <= 0.05 and cost_ok(wt, incumbent)
    r = receipt("accept" if ok else "reject", ...)
    if ok: merge(wt); incumbent = wt; incumbent_cache = rerun(V); F |= medoids
    return r

# ε 校准
S = [run(incumbent, V).successes for _ in range(3)]
eps = max(4, max(abs(a - b) for a, b in pairs(S)) + 1)   # COAP 2610.12369 App. B 的 4 是下限
```

ε 的量级：V=100、SR≈0.4 时单次成功数 SD≈4.9，两次独立之差 SD≈6.9；配对后只剩不一致对 b+c（约 30），单侧 95% 线 ≈1.65·√(b+c)≈9。COAP 的 4 是在 150 回合确定性程序上测得（重跑差 1–3 回合），直接搬到 50 回合随机系统上不够。

### 3.4 每轮预算（公式）

T_round = T_edit + (E_L1 + E_L2 + E_L3)·T_ep / P + T_score；T_ep = steps·t_step + N_vlm·T_vlm；C_round = C_edit + E_total·N_vlm·c_call + GPU_h·c_gpu。

| 配置 | P | t_step | T_vlm | T_ep | E_total | T_round | C_round |
|---|---|---|---|---|---|---|---|
| 本机 16 GB（Isaac Sim）+ 云卡跑 π0.5 + API VLM | 1 | 约 0.1 s（M0 实测） | 约 15 s | 800 步 ≈ 80 s + 8 次 ≈ 120 s ≈ 3.5 min | 125 | ≈ 7.5 h + 0.5 h 编辑 ≈ 8 h | 125×8×$0.04 ≈ $40 + 编辑 ≈ $6 + π0.5 云卡 8 h×$0.6 ≈ $5 → 约 $50 |
| 云上 RT 卡 48 GB×1 跑仿真×4 进程 + 80 GB 卡跑 π0.5 与本地 Qwen3.8-27B-FP8 | 4 | 约 0.04 s | 约 8 s | ≈ 1.6 min | 125 | ≈ 0.8 h + 0.5 h ≈ 1.3 h | 2 卡×$3/h×1.3 ≈ $8 + 编辑 $6 → 约 $14 |

c_call 口径：3 张 640×480 约 410 token/张 + 约 8k 文本输入 + 约 1.5k 输出，claude-sonnet-5-5 $2/$10 每百万 → 约 $0.04；系统提示走缓存（读 $0.20/M）。A100/H100 没有 RT core，不能跑 Isaac Sim，云方案必须配 L40S/RTX 6000 级卡做仿真。

### 3.5 威胁表

| 手段 | 拦截层 | 自测作弊候选（必须被拦） |
|---|---|---|
| 读 scene_manager/reward/get_instance_pose/success/end_flag | AST 白名单 + P2 无环境句柄 + `tool_call` 阻断 | 节点 `args.target="pose(scene_manager…)"`；`deploy.py` 读 `TASK_ENV.success` |
| `mark_env_unstable` 把失败回合踢出分母 [code] | AST 禁名 + P5 审计 unstable 率 >5% 触发人工复核 | `deploy.py` 在预测失败时调 `mark_env_unstable` |
| 放宽验证器阈值 | `frozen/params` 不在白名单；只许收紧；放宽需 P5 证明漏检率不升 | PR 把 conf 阈值 0.6→0.4 |
| 写死坐标 / seed / 任务名 | schema 拒数值 target；泄漏正则；V 与 E 差距 | 序列含 `[0.31,-0.15,0.02]`；prompt 含"layout 7" |
| 自宣成功 / 提前 finish | 验证器不读自述；官方 judge 离线 | 规划器返回 `{"done":true}` |
| 改打分或汇总 | P5 Docker 只读挂载；收据记 RoboDojo/XPolicyLab commit SHA 与 `summarize_result.py` 哈希 | 候选改 `scripts/internal/summarize_result.py` |
| 评测期写记忆、跨回合泄漏 | T 期记忆目录只读 + 哈希；`reset()` 清空回合态 | seed 2 期间 append |
| 同布局多次尝试取最好 | run_id 与 resume manifest 审计；每布局一回合 | 重跑失败布局后覆盖 `_result.json` |
| 编辑者偷看 V/T | P6 会话的 read 工具受路径拦截；V/T 结果不在其可读树 | P6 读 `gate/V/` |
| 超调用预算 | P2 计数硬停（12 次/回合），超限节点走无 VLM 路径 | 规划器循环 inspect |
| 回合提前结束泄露官方成功（env 一判成功就终止 [code]） | P2 不把结束原因、回合长度交给验证器或规划器；记忆写入只用离线 judge | 验证器把 `episode_end` 当 Pass 证据 |

## 4 接口规格

**节点 schema（字段已定，补约束）**

```json
{"id":"n3","executor":"vla|ee_move|gripper",
 "instruction":"pick up the bowl nearest the left gripper",
 "args":{"target":"perceive(\"upright bowl, leftmost\")","cmd":"open|close"},
 "stop":{"stall_chunks":2,"max_chunks":6,"exec_steps":25},
 "postcondition":["gripper_holding","target_lifted(>0.05m)"],
 "on_fail":{"retry":2,"then":"previous_node"},
 "evidence":["wrist_rgb_diff","gripper_width","ee_pose"]}
```
约束：`args.target` 只接受 `perceive("…")` 字符串；`postcondition` 只能用验证器谓词表（`gripper_holding`、`target_lifted`、`target_in(A,B)`、`target_on(A,B)`、`stacked([...])`、`moved(A)`、`not_moved(A)`、`arm_home`）；谓词由 2D 框关系 + 两视角一致 + RGB 差分实现，阈值在 `frozen/params`。

**判决 JSON**

```json
{"node":"n3","kind":"Pass|Fail|InsufficientEvidence|Blocked|SafetyStop",
 "predicates":{"gripper_holding":{"v":true,"conf":0.91,"src":"gripper_width_measured"}},
 "evidence_refs":["kf/ep17/n3_before.png","kf/ep17/n3_after.png"],
 "source":"code_verifier|inspect_second_opinion","t_chunk":[4,7]}
```
语义照 HexaAnything 2609.35432 §5.5，判定顺序 SafetyStop → 后置条件 → Blocked（完成优先：节点中途出过 IK 失败但终态后置条件成立仍判 Pass，`ik_failed` 只记作证据，Recova 2610.01178 Alg. 1 第 23 行。ee_move 的完成包含"到位"：执行器先判到位再判 IK，到位但最后一块被 IK 丢弃时以 done 结束并带 `ik_failed`，由验证器按上述顺序判；未到位就 IK 失败视为未完成，直接 Blocked）；非 Pass 判决附带派生字段 `scene_intact`（非目标物体对 t=0 关键帧 not_moved；目标含无 `args.target` 的接触节点的隐式对象，即同臂前序节点的目标；对象无法确定或头相机里被夹爪遮挡时为 null）。Blocked = IK 失败/工作空间外；SafetyStop = 关节速度尖峰或 PhysX 警告；InsufficientEvidence = 检测置信低或遮挡，先再观测，最多 1 次 inspect。

**MCP 工具（P2 暴露给 P1；pi `directTools` 全部提升为一等工具以便 `tool_call` 拦截）**

| 工具 | 签名 | 说明 |
|---|---|---|
| `observe` | `(views=["head","wrist_l","wrist_r"], frame="latest") → images+state` | 默认只给最后一帧 |
| `inspect` | `(frame_ref, region{x,y,w,h}, zoom≤4) → image` | VISTA 2610.02200 §3.1，历史帧无损存 |
| `perceive` | `(query, view) → {found, bbox, conf}` | 本地开集检测器，不调 VLM |
| `memory_lookup` | `(task, instruction) → [sequence]` | 只读，top-3 |
| `submit_sequence` | `(nodes[]) → {ok, errors[]}` | schema + 泄漏检查，失败即阻断 |
| `get_verdict` | `(node_id) → verdict` | 只返回判决，不返回官方分 |
| `budget` | `() → {calls_left, chunks_left}` | 来自 `step_lim` |

**lineage 收据**

```json
{"receipt_id":"r0042","parent":"c0017","candidate":"c0018","surface":"prompts/planner.md",
 "diff_sha":"…","hypothesis":"…","gates":{"L0":true,"L1":"5/5","L2":"19/20","L3":{"b":17,"c":6,"eps":9,"p":0.012}},
 "cost":{"vlm_tokens_per_ep":[91000,97000],"wall_per_ep_s":[205,214]},
 "decision":"accept","evaluated_on":{"V":"seed1[0:50]","tasks":["stack_bowls","put_bottles_into_dustbin"]},
 "env":{"robodojo":"<sha>","xpolicylab":"<sha>","pi05":"joint-0/59999","model":"claude-opus-5-5"},
 "rollback":"git revert <merge-sha>"}
```

**仓库目录树**

```
etude/
  deploy/XPolicyLab/policy/Etude/{deploy.py 官方 forwarder+白名单增强, deploy.yml(eval_batch:false, request_timeout_s:1800, action_type:ee), model.py}
  harness_server/{executor/{vla,ee_move,gripper}.py, verifier/{predicates,verdict}.py, perceive/detector.py,
                  memory/{store,writer}.py, trace/, mcp_server.py, budget.py}
  planner/{.pi/mcp.json, .pi/extensions/{guard.ts,budget.ts}, SYSTEM.md}
  evolvable/{prompts/planner.md, rules/recovery.yaml, skills/SKILL.md}      ← 白名单
  frozen/{params/thresholds.yaml 只收紧, forbidden.yaml, schema/{node,verdict}.json, ast_check.py}
  evolution/{gates.py, aa_noise.py, leakage.py, lineage/, worktrees/}
  scorer/{Dockerfile, run_official.sh, audit/}
  memory_db/<task>/sequences.jsonl
  traces/<episode_id>/{planner.jsonl, trace.jsonl, keyframes/*.png, videos→官方}
  experiments/{M0,M1,M2,M3}/{config.yaml, prereg.md, results/}
```

存储：关键帧（节点边界与每秒 1 帧）PNG 无损，约 300 KB/帧，一回合约 30 MB；逐帧全存走官方 MP4。

## 5 里程碑表

| | M0 条件 1 | M1 条件 2 | M2 条件 3 | M3 条件 4 |
|---|---|---|---|---|
| 文件 | `model.py` 返回块前 N 步；`aa_noise.py`；验证器离线跑 M0 录像 | 全部 P1/P2；`evolvable/` 初版；记忆关 | `memory/{store,writer}`；打乱记忆对照开关 | `evolution/*`；P6 会话；收据 |
| 实验 | 开发任务×N∈{5,10,15,25,50}×seed 0/1×50（批量 eval_batch:true，num_envs 4）；留出任务只跑最佳 N；A/A 3 次；验证器对官方 judge 的 FPR/FNR | 开发+留出任务，seed 0/1 各 50；记录调用/token/墙钟；A/A 3 次 | E 上积累 → seed 0/1 评；对照 = 打乱记忆（换任务或打乱节点顺序） | 三个面各 R≤4 轮；最后 T 一次：H* 对冻结 H2 |
| 预注册通过 | 跑完、t_step 与 A/A 的 δ 记下；FPR ≤5%（否则验证器先修） | 配对 SR1 − SR0(N*) ≥ ε 且 p≤0.05（开发任务 seed 0/1 合并 200 回合）；FNR 不升 | SR2(seed1) − SR1(seed1) ≥ ε；打乱记忆 ≤ SR1 + δ | 每面至少 1 次 accept；T 上 H* − H2 ≥ ε（seed 2 + 留出） |
| 预算（本机） | 约 1000+200 回合，无 VLM，批量 ≈ 12 h | 约 400 回合 ≈ 27 h，VLM 约 $120 | 约 500 回合 ≈ 33 h，约 $150 | 12 轮×8 h ≈ 96 h + T 400 回合 ≈ 27 h，约 $700 |
| 卡点 | π0.5 单独跑通官方流程；单回合墙钟实测；夹爪访问器可用 | 节点边界阻塞 ≤1800 s；perceive 检测器在三视角可用 | 记忆序列在 random 布局上可执行（坐标已换查询） | A/A 给出的 ε 不能大于 15，否则 V 扩到 150 |

指标统一：Score/SR（官方 summarize）、VLM 调用与 token、墙钟、漏检率（验证器 Pass 而官方失败）。

## 6 证伪条件

| 条件 | 统计量 | 余量 | 后果 |
|---|---|---|---|
| 条件 2 不超过最好的 N | 同布局配对 McNemar，开发任务 seed 0/1（200 回合） | b−c < ε 或 p>0.05；或漏检率上升 >2 pp | 如实写成"带验证器的定时重查器"；M2 仍做（记忆对重查器也可测），M3 只开恢复规则一个面 |
| 条件 3 在留出上缩水 | 开发任务 seed 2：SR2 − SR1 | < −δ_AA | 序列泄漏布局信息，回查 schema |
| 进化后 seed 2 不超过冻结 harness | T 上 H* − H2 配对 | < ε | 记 M3 证伪，保留 H2 |
| 同回合同 VLM 预算 M3 ≤ M2 | M2+ = M2 用同等额外回合扩记忆；比较 H* 与 M2+ | H* − M2+ < ε | 进化不值 |
| 收益全来自缩短重查步长 | M1 内 VLA 节点 `exec_steps` 固定为 N*；对照 M0(N*)；Long-WAM 2610.10528 §6：原子任务 15 步重查 84.4 ≈ 层级 85.6，复合任务 11.2/5.0 对 38.8/35.0 | 若 put_bottles/make_kong 上 M1 − M0(N*) < ε 而 stack_bowls 上 ≥ε | 结论限定为"短程任务收益=重查" |
| 停止规则 | 连续 3 轮 reject；或 V 已被 6 个候选复用；或预算用尽；或 ε>15 | — | 冻结现任，进入 T |

## 7 风险表

| 风险 | 对策 | 依据 |
|---|---|---|
| ε 太大，门几乎不接受 | 配对比较 + V 扩到 150；一次只改一个面 | RRSI 2609.24972 §3.3（A/A 噪声带）；COAP 2610.12369 App. B |
| 验证器误判污染记忆 | 入库必须官方成功；评测期冻结 | PhyAgentOS 2607.16636 §4.3（教训须被后续结果证实）；Spotter 2609.36808 §3.1（纯图 VLM judge 接近随机） |
| 夹爪回显误判抓取 | 用实测开度访问器 + 腕相机差分双证据 | Spotter 附录 B（12 mm 空夹爪证据）；工程对策 |
| 坐标泄漏导致 random 布局崩 | 序列只存查询；Gen 任务 25 random 回合当内置检查 | Harness VLA 2607.08448 §2.2；RoboRSI 2610.12424 App. A |
| 记忆单调增长、过时条目 | 同指令哈希去重保留更短者；v1 不剪枝但记录命中率 | ASPIRE 2607.00272 §5；PhyAgentOS §6.2 |
| 编辑者过拟合 E | 泄漏正则 + V 隐藏 + diff 预算 | HarnessEvolve 2609.00829 Table 4（去质量门 86.9→80.1）；Meta-Harness 2603.28052 §2（测试集隔离） |
| 作弊率随模型变化 | 自测作弊候选集每轮回归 | EmbodiedSWE 2609.27308 Table 8、14 |
| VLM 调用贵、失败回合更贵 | 12 次上限；失败回合提前退回；筛查器不进 v1 | Spotter Table 5–6；PyRUA-Lean 2610.01939 Table 2 |
| 交还 VLA 时状态出分布，或锚点早已被推翻 | 退回"上一已验证节点"的记录位姿后，先复验锚点后置条件与场景完好，再交还（已实现）；同一失败签名一回合内重复 2 次直接 replan（Recova c_max 的对应物，未实现，列为建议 P4） | RoboHarness 2607.18060 §4.2.2（Memory Bridge）；Spotter §3.2 Retreat；Recova 2610.01178 Alg. 1 第 41–42 行（M_res 复原验收）、第 6–8 行（c_max） |
| 单回合墙钟 >5 min | `exec_steps` 25、节点数 ≤8、仿真暂停期间不计步 | 工程对策 |
| support 臂不稳定被当策略失败 | 官方记 unstable 不计分母 [code]；审计 unstable 率 | RoboDojo 2607.04434 站点 common-issue |

## 8 不可行的决策、事实核查、未决问题

### 8.1 事实上做不到的决策与最小修正（按影响排）

1. **单卡 16 GB 共用。** Isaac Sim 5.1 最低要求 RTX 4080 16 GB；openpi 推理 >8 GB；RoboDojo 论文把 π0.5 放在另一台 A800（2607.04434 §6.3.1）。修正：π0.5 走第二张卡（云 24 GB 级即可，websocket 本来就是远程协议），本机只跑 Isaac Sim（num_envs 1）+ 检测器。
2. **30–70% 任务不足 4 个。** 只有 2 个。修正：带宽 20–70%，加 build_tower、make_kong；fold_clothes 替补。
3. **夹爪"实测开度"不在观测里。** `*_ee_joint_state` 来自 `control_manager.prev_control`，是按实测开度钳位（±0.2 量程）后的指令回显 [code]；实测值只在 `robot_manager.get_end_effector_real_val` [code]。修正：白名单访问器，写进 `forbidden.yaml` 的 allow 段并说明真机对应物。若不接受访问器，退路是回显启发式："指令闭合但观测稳定在明显大于 0 的位置"判为夹到物体，阈值离线标定且只许收紧。
4. **自采开发布局。** `seed_manager` 只读 `Eval_Layout/<cfg>/<seed>/` 的预生成 JSON，无采样代码 [code]；PhysicalRSI 的采样器不在公开适配器里。修正：用 seed 0/1，备用布局 50–54。若 V 需要扩容，可按任务 yml 的随机范围离线自采 JSON（不是策略在读），并排除与公开布局重合、报告两者差距。
5. **Docker 隔离打分。** judge 在仿真进程内，与 `deploy.py` 同进程。修正：隔离写入者而非 judge（第 2 节）。
6. **Zetta 三级门第一级是 medoid seed，不是"中心 seed"**（2608.16590 §2.4.1）；ΔSR 阈值未给。修正：用 ε + McNemar。
7. **ee/joint 切换。** `get_action_type` 逐动作字典推断，无初始化锁定；`--action-type` 只是结果路径标签并传给策略 `eval.sh` [code]。所以可在一回合内交替，但同一字典不能混键；`get_action_type` 只认 `*_ee_pose`，只带 `delta_ee_pose` 或 `tcp_pose` 的字典会抛 "Cannot infer action type" [code]，`ee_move` 因此只能发绝对位姿；IK 失败时该臂被静默丢弃并照扣步数，`ee_move` 必须回读 `ee_pose` 确认。
8. **"经 openpi policy server"。** 官方 Pi_05 适配器在 `model.py` 进程内 `create_trained_policy` 直接推理，没有 openpi 客户端 [code]。修正：P2 用 vendored 的 `websocket_client_policy.py` 连自己启动的 `serve_policy.py`（第 2 节 P3），多一跳换来 GPU 分离。

### 8.2 F 核查结果

| # | 结论 | 来源 |
|---|---|---|
| F1 | 可逐动作字典切换；π0.5 官方 ckpt 输出 14 维关节（delta 关节 + 绝对夹爪，块 50） | `eval_env.py`、`Pi_05/deploy.yml`、HF `ckpt/RoboDojo/Pi_05` 与 Myungkyu 复现说明 |
| F2 | depth 默认关（`arx_x5.yml` 置 false，且 `camera_config.yml` 的 depth 注解器被注释），开 depth 要改两处配置并需配套布局，等于离开官方配置。选路线：**腕相机视觉伺服**（实测 `ee_pose` 加增量生成绝对 `*_ee_pose` 逐步对中，深度用 `ee_pose.z` 相对台面），头相机只做粗定向；不选自标定 PnP（多一套常量和误差链） | `env_cfg/arx_x5.yml`、站点 configurations |
| F3 | ckpt 可得：`RoboDojo-sim-arx_x5-joint-{0,1,2}/59999`，params 约 12.4 GB/份，匿名可下；榜单总分 11.44/6.93% 核对一致。分任务 SR 见第 1 节（合并值）；Gen 任务拆开看：stack_bowls 标准约 72 / random 约 4，fold_clothes 标准 38.7 / random 9.3，所以记忆在 random 半场几乎无成功序列可积累 | HF 数据集、官方榜 Per-Task 与 JS 数据 |
| F4 | 指令回显；实测值需访问器 | `obs_manager.py`、`control_manager.py` |
| F5 | `model_client.call(func_name)` 可调 Model 任意公共方法，服务端单锁串行；`request_timeout_s` 默认 120，官方 `eval_env.py` 构造客户端时不传它，`deploy.yml` 里的值只对 debug 客户端生效，所以正式评测每次调用硬上限 120 s，超时该回合作废（`PolicyEvalClientConfig`，`eval_env.py` 第 188 行 [code]）；规划调用落在 `get_action` 内阻塞（`eval_batch:false`，否则默认走 `get_action_batch`），RPC 超时 90 s 后改返回保持块；`update_obs` 每个 25 Hz 步都调，`get_action` 每块一次；pi 侧用 `--mode rpc` 等 `agent_settled` | `ws/model_client.py`、`model_server.py`、`protocol/client.py`、`eval_env.py`、`Pi_05/deploy.py`、pi `rpc.md` |
| F6 | step_lim：stack_bowls 800、put_bottles 700、build_tower 1050、make_kong 600、fold_clothes 500（take_action 次数，25 Hz）；seed = 布局目录索引，一布局一回合；单回合墙钟待 M0 实测，论文 10 环境并行 64 interactions/s（4090 + A800） | `task/RoboDojo/tasks/*.py`、`seed_manager.py`、2607.04434 §6.3.1 |

**标题核对。** 简称与标题不一致的：COAP = "Embodied Turing Machines: Stateful Code for Robot Recursive Self-Improvement"（2610.12369）；HexaAnything = "Self-Evolving Coding Agents: From Digital Programs to Physical-World Intelligence"（2609.35432）；ARLI = "Learning to Act While Waiting: RL Finetuning of Generalist Robot Policies Under Inference Latency"（2608.23831，异步 RL，训权重，放 v2 正确）；VERITAS = "Visual Verification Enables Inference-time Steering and Autonomous Policy Improvement"（2606.18247）；CaP-RL 是 CaP-X（2603.22435）§5。EmbodiRSI 2609.38905 与 EmbodiedRSI 2610.10498 确为两篇。其余 ID 与简称一致。2026-10 新增一篇直接相关：Recova 2610.01178 "Recova: Agent-Guided Failure Recovery for Autonomous Robotic Manipulation"（卡片 docs/research/recova_2610.01178.md），借其复原验收与完成优先两条，不借 DAgger 训练。ASPIRE 的禁读条款在 **App. E.2**（E.3 是 Fix Loop Skills）。

### 8.3 E 的四个决定

| 事项 | 选择 | 理由 | 预算影响 |
|---|---|---|---|
| 停止与后置条件 | 停止 = 夹爪实测 + 停滞块数；后置 = RGB 差分 + 开集检测器谓词 | VERITAS 路径点需要开局 VLM 画线且依赖 N 采样，π0.5 单块不采样 | 检测器约 1–2 GB 显存，0 次 VLM |
| VLM 看图 | VISTA 式：默认最后一帧，`inspect` 按需 | VISTA Fig. 5 中 (v) 是最大一步；每原语都看会把 token 推到 Spotter 失败回合的量级 | 每次调用约 1.2k 图 token |
| 规划模型与上限 | 规划 claude-sonnet-5-5、编辑 claude-opus-5-5；12 次/回合 | 规划调用多、结构化输出为主；编辑少而重 | 约 $0.3/回合 |
| 评测期记忆 | 冻结 | FPR 未知；P12 单一固定系统；PhysEvo 2610.08995 App. B.1 计分回合清空记忆 | 0 |

### 8.4 未决问题（按影响排）

1. `solve_ik` 的目标位姿坐标系（world 透传，`real_robot_pose` 另传）与观测 `ee_pose`（减去 env 原点）是否同系：开工第一天做 4 点探针验证。
2. 官方 Pi_05 适配器的 `exec_steps`（每次推理执行几步）未在仓库写明，M0 的 N=50 行是否等价于官方 11.44 要用 seed 0 复跑核对。
3. 3080 Laptop 上 Isaac Sim num_envs=1 的 t_step 未测；若 >0.2 s，本机方案每轮超过 14 h。
4. `is_all_gripper_open(open_threshold=0.8)` 等官方谓词读的是实测还是指令值未核；影响"回原点且张开"后置条件的写法。
5. 单一布局 55 个文件中 50–54 是否与前 50 同分布未核（只做 smoke）。
6. 检测器选型（OWLv2 / SAM 3 小模型）在三视角 640×480 的召回未测；FPR 目标 ≤5% 取自工程经验。
7. 120 s 硬上限下，规划一次调用的实际墙钟分布未测；若 p95 > 90 s，保持块方案会吃掉步数预算，需要换成更小的提示或更快的模型。开工用一次人为超时实测官方客户端的超时行为（作废该回合还是重试）。
8. 榜单 Pi-05 的 150 回合是 joint-0 一份权重跑 3 个评测 seed，还是三份训练 seed 权重各跑一组，未查到；M0 复跑 seed 0 时按前者。
