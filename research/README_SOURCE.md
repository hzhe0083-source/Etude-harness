# Research OS: vendored copy

来源：`~/Documents/a2r/research/`（a2r 仓库 origin `https://github.com/Shiki42/a2r.git`，HEAD `706ea662cd88f036a9684e73d63535eee064b85b`；`research/tools` 等文件最后一次提交 `29edb9604069ddcb9ec4b269ab45ca55d8b50993`，复制时 a2r 工作树对这些文件无未提交改动）。

复制日期：2026-10-11。

复制了：`AGENTS.md`、`RATIONALE.md`、`SCHEMA.json`、`requirements.txt`、`tools/research`（保留可执行位）、`tools/research_os/*.py`。

没有复制：`ledger.json`、`WORK/`、`WORKSTATE/`、`EVENTS/`、`ARTIFACTS/`、`.lock`、`guide.html`、`__pycache__/`。本仓库的台账还不存在：`research init` 需要人类授权原话（`--human/--quote/--channel`），由用户本人执行，代理不得代填。

本地改动：无。`AGENTS.md` 里的“当前入口：E-1 仿真数据集、E-2 真机来源数据集、E-3 π0.5 训练”和 R48/R49 的例子是 a2r 的内容，原样保留作规则参考；Etude 的 Experiment 编号由本仓库台账在 `research init` 之后自行分配。

依赖：`jsonschema`（已在 `pyproject.toml`）；`huggingface_hub` 只在 HF 归档命令里惰性导入，本仓库未装（不改 `pyproject.toml`），需要 `run-upload` 等命令时另行安装 `research/requirements.txt` 里的固定版本。

用法：`uv run python research/tools/research --help`；写入需 `RESEARCH_ACTOR=agent:<id>`。Run 只执行已 commit 且已推送 origin 的源码（`capture.py` 的 `git_capture`），在 detached worktree 中运行，命令必须是受 Git 管理的入口脚本（禁止 `-c` 内联），核心指标写 `$RESEARCH_METRICS`（评测：`successes`、`trials`、`success_rate`），产物写 `$RESEARCH_OUTPUTS`，原始日志在 `runs/research/<Run>/attempt-N/`（已被 `.gitignore` 的 `runs/` 覆盖）。

## 留给用户执行的命令模板（代理不得代填 `--human/--quote/--channel`）

```bash
# 1) 初始化台账（人类原话）
RESEARCH_ACTOR=agent:<id> uv run python research/tools/research init \
  --human <你的id> --quote "<你的原话>" --channel <claude-code|slack|...> \
  --goal "<目标>" --metric success_rate --baseline "<基线，如 pi05 joint-0/59999 官方流程>" \
  --constraint "<硬约束，可重复>"

# 2) 代理可做（scout 实验，无需授权）；committed 契约要人类授权
RESEARCH_ACTOR=agent:<id> uv run python research/tools/research hypothesis create --help
RESEARCH_ACTOR=agent:<id> uv run python research/tools/research experiment create --hypothesis H-<n> --mode scout --kind feasibility --intent "<意图>"

# 3) 评测 Run：源码须已 commit 且已 push origin；入口是受 Git 管理的脚本
RESEARCH_ACTOR=agent:<id> uv run python research/tools/research run --experiment E-<n> \
  --kind evaluation --archive-to model=<OWNER/NAME> --checkpoint model=<OWNER/NAME@REV[:path]> \
  --parameters runs/params/<run>.json -- scripts/<entry>.sh <args>
#    --parameters 是 JSON 文件路径（cmd_runs.py 用 Path(...).read_text() 读），不是内联 JSON；
#    文件内容如 {"task":"stack_bowls","seed":0,"exec_steps":25}，放在 git-ignored 的 runs/params/
#    （etude.evolution.research_bridge.run_command / experiments/expctl.py run-argv --write-params 会生成）
#    入口脚本把 {"successes":k,"trials":n,"success_rate":k/n} 写入 $RESEARCH_METRICS
```

`Receipt.research_run`（`etude/core/types.py`）记录对应 Run 的标签。
