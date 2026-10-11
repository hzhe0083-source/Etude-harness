# Research OS v1 — 小台账与 HF 原始记录

> 来源：a2r `research/AGENTS.md`（origin https://github.com/Shiki42/a2r.git，research/tools 最后提交 29edb96）。本仓库只改写了“R47–R50：创建边界”一节（含“当前入口”）为 Etude 的入口与单元格定义；其余各节原样保留，未改一字。映射与命令详见 docs/research_os_usage.md；命令模板由 `uv run python -m etude.evolution.research_bridge` 生成。

## 保存边界

- Git 中唯一科研状态源是 research/ledger.json，保存当前记录，历史交给 Git。只通过 research/tools/research 更新；不要手改台账。
- 台账不超过 5 MiB，每条记录不超过 32 KiB；核心配置最多 20 个标量字段。禁止日志、逐 step 曲线、逐样本结果、完整发布索引、大数组或运行叙事混入。
- 数据集核心结果：发布条数、任务种类数（不能用资产数替代）、资产覆盖、固定 HF 数据版本。
- 训练核心指标只保留 train_loss、validation_loss；配置和 seed 单独保存，checkpoint 使用固定 HF revision。
- 评测必须记录被评测 checkpoint 的 repo/revision/path、评测代码 repo/commit、关键评测条件、successes、trials、success_rate（0–1，必须等于 successes/trials）。
- stdout、stderr、完整 metrics、环境、验收报告、索引和清单，连同主要产物保存在对应 HF 仓库。台账只记录 repo/type/40 位 revision/path，禁止 main 等浮动引用。
- 不再创建 ARTIFACTS、EVENTS 或 OBJECTS。原始执行记录暂存在被 Git 忽略的 runs/research/<Run ID>/attempt-N/；Handoff 是忽略的本地临时状态，没有证据效力。
- 更正直接更新当前记录，但不得删除失败 Run、捏造缺失指标或抹掉来源缺口。日志归档不提升科学有效性。

## R47–R50：创建边界（Etude 改写；a2r 原文的入口为 E-1 仿真数据集、E-2 真机来源数据集、E-3 π0.5 训练）

创建 Experiment 或 Run 前，先查询现有 Experiment、View、目标单元格和当前 Run。
当前入口（逻辑名 → 新台账按 scripts/research_bootstrap.sh 顺序创建时的别名；实际别名以台账为准，并写回 experiments/<M>/config.yaml 的 research.experiment / aa_experiment / t_experiment）：

- 假设：H-1 = 条件 2（harness H1 胜过 π0.5 每 N 步重查的最佳 N）；H-2 = 条件 3（只读节点序列记忆）；H-3 = 条件 4（harness 回路逐面进化）。
- E-M0 → E-1：π0.5 每 N 步重查扫描（条件 1，H-1 的对照）。E-M0-AA → E-2：π0.5@N* 在 V 上 A/A。
- E-M1 → E-3：harness H1（条件 2）。E-M1-AA → E-4：H1 在 V 上 A/A。
- E-M2 → E-5：记忆开 vs 打乱记忆（条件 3）。
- E-M3 → E-6：逐面进化的门评测（条件 4）。E-M3-T → E-7：最终 T，只看一次。

单元格定义（view 文件由 experiments 组件维护，格式见 docs/research_os_usage.md §2）：

- 每个 Experiment 恰好一个 view：experiments/<M>/view.json（主实验）、view_aa.json（A/A）、view_T.json（T）。cell 的 metric 一律 success_rate；slot = cell.match ∪ {rows.key, cols.key, replicates.key 各取一值}，值一律字符串。
- 视图外的辅助槽：slot 加 stage=<名>（E-M0 的 verifier_audit；E-M3 的 L1、L2），轴值仍须在 view 内；`research cell` 不显示它们。
- **R47**：复用同一科学问题的 Experiment。机器、worker、num_envs、重试、候选编号都不是另建 Experiment 的理由；新候选、新面在 E-M3 的既有行列里取单元格，超出时先 `experiment extend`（committed view 扩展需人类授权，A6）。
- **R48 数据**：本项目不产数据集；记忆库 memory_db/ 是离线脚本产物，不作为 dataset Run，入库的回合以其评测 Run 为证据。
- **R49 训练**：本项目不训练；π0.5 权重冻结，checkpoint 用固定 HF revision（官方数据集仓库 RoboDojo-Benchmark/RoboDojo@35efbc7d…:ckpt/RoboDojo/Pi_05/RoboDojo-sim-arx_x5-joint-0/59999 的用户自有 model 镜像，见 usage §4）。
- **R50 评测**：一次 eval 扫描单元格（task×N×seed、task×condition×seed）、一次 A/A 重跑（rep）、一个候选的一道门（L1/L2/L3）各是一个 Run，slot 唯一。失败 Run 不删；改代码重跑须先把旧 Run 标 invalid，再在原槽用 --supersedes 新建。候选代码先 commit 到 cand/<id> 分支并 push origin，才能 `research run`。smoke（备用布局 50–54）、reader/schema/视频检查是原 Run 的原始记录，不另立实验。

## R20–R24、R40–R43：执行与可追溯性

- 新实验只允许执行**已经 commit 且已推送 origin 的源码与配置**。启动器拒绝脏工作树与未跟踪文件；只豁免 ledger、WORK、WORKSTATE 的记录变动。
- 使用独立 detached checkout 执行该 commit，输出写在源码目录外。脚本和配置必须受 Git 管理；禁止外部临时脚本、内联 shell/Python 程序代替版本化入口。依赖锁文件及输入数据校验值仍需记录。明确声明为 --input 的外部数据清单可以读取，但前后校验必须一致；这不允许外部脚本。
- 每个新 Run 必须声明 --kind dataset|training|evaluation 与 --archive-to model|dataset=OWNER/NAME。原始记录的目标仓库应是主要产物所属仓库；评测使用被评测模型仓库。
- 评测计划还必须有 --checkpoint model=OWNER/NAME@REV[:path]。运行的 capture.git 就是**评测代码版本**，与模型 checkpoint 的 HF revision 分开保存。
- 命令向 $RESEARCH_METRICS 写核心指标，向 $RESEARCH_OUTPUTS 写 {产品名: "model|dataset=OWNER/NAME@REV[:path]"}。完整明细可写到 $RESEARCH_RUN_DIR，也可用 --record NAME=PATH 收集外部验收报告；权重和数据本体写到主要产物目录，不放进原始记录目录。
- 原始日志流式落盘。执行结束先保存退出码和核心结果，再上传原始记录、逐文件校验并登记 HF revision。archive_status=pending|uploaded 独立于执行状态；上传失败保留本地文件并显式报错。
- research run-upload E3-R001 只重试上传，不重跑训练。主机中断后先 run-interrupt，再上传现存日志。上一 attempt 归档后才能 resume/restart。
- 外部中断且定义不变：同一个 Run resume/restart。需要改代码或数据：旧 Run 标 invalid，在原单元格用 --supersedes 创建新 Run。
- 执行状态沿用 planned/running/completed/interrupted；退出码为 0 且已声明产物全部登记才 completed。科学有效性是独立的 valid/unknown/invalid；缺少核心结果、来源不完整或失败时不能自动 valid。
- 数据结果通过 run-dataset --file 核心结果.json --evidence dataset=OWNER/NAME@REV:path 更新；数据集总量通过 experiment dataset 更新。总量必须来自固定版本的完整元数据，不能把局部 Run 数量相加冒充全量。
- 没有重新运行历史实验。迁移记录的 provenance_gaps 保留旧的未提交代码、缺少 loss 等事实，旧 diff 与执行记录归档到 HF。

## 身份、依赖、表格

- 稳定 ID 是 ULID；人类使用 H-n、E-n、E{实验号}-R{序号}、O-n、C-n、D-n、P-n。编号由 CLI 分配。
- 表格按注册的 slot 计算，每个 slot 一个当前 Run；新对照条件用新单元格。只有已 invalid 或依赖失效的 Run 可以被 supersede。
- --depends-on 记录产物依赖；依赖失效会传播需要重跑的状态。
- 认领通过 research take/release 的独立 WORK 表完成。调度、任务归属、进度叙事不写进科研台账。
- HTML 视图按需生成，不提交生成页面。多人编辑 ledger.json 使用正常 Git 合并并运行 validate，禁止 union 合并整个 JSON，也不能用旧 EVENTS 恢复当前状态。

## 科学记录与授权

- 复用 Hypothesis、Experiment、Run、Observation、Claim、Decision、Project、Policy 的语义与现有命令。
- Observation 引用实际 Run；Claim 的 supporting/contradicting/context 区分证据用途。scout/debug 只作为 context，不能自动升级成 committed。
- committed 实验契约、Claim 的 supported/refuted、基于证据的 Decision、事后把 Run 改为 valid、Policy 授予/撤销、committed View 扩展仍需要对应人类授权或允许的 Policy。不得伪造批准。
- 人类授权记录必须保留原话。当前有效授权与必要的请求保存在小台账，普通操作事件不累计。
- 变更 committed 契约会标 post_hoc_amended；不能用事后修改契约提升结论。
- verifier 必须独立于 Claim 作者；Policy 要求验证时必须遵守。验证只读证据图和 HF 原始记录。
- Handoff 不可作为证据。无可核验证据的事实标未知，不把状态描述当实验结论。

## 常用命令

    research/tools/research list experiment
    research/tools/research show E3-R001
    research/tools/research view --out /tmp/research.html
    research/tools/research validate
    research/tools/research --help

写入需设置 RESEARCH_ACTOR=agent:<id>。依赖为 Python 3.10+、jsonschema；HF 操作另需 huggingface_hub。
