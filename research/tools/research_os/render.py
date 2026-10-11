"""HTML for experiment views and the repository master view. Output is disposable: never commit it."""

from html import escape

from .views import cell_state

STATE_LABEL = {"done": "已有结果", "running": "运行中", "interrupted": "中途终止", "waiting": "等待上游",
               "pending": "未开始", "rerun": "需要重跑"}
STATE_ORDER = ("done", "running", "interrupted", "rerun", "waiting", "pending")
HF_BASE = {"model": "https://huggingface.co/", "dataset": "https://huggingface.co/datasets/"}

CSS = """
:root{--bg:#F7F4EE;--card:#fff;--ink:#1C1B18;--muted:#7A746B;--line:#E4DED3;
--done:#DDEFE3;--done-b:#2E7D4F;--running:#FBF0C9;--running-b:#B88A00;--interrupted:#F8DEDB;--interrupted-b:#B3261E;
--rerun:#FBE3CC;--rerun-b:#C25E00;--waiting:#DCE8F7;--waiting-b:#3B6EA8;--pending:#ECEAE6;--pending-b:#9A948A}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1C1B18;--card:#26241F;--ink:#EDE7DA;
--muted:#A39C90;--line:#3A372F;--done:#1F3A2A;--running:#3D3415;--interrupted:#45201D;--rerun:#46301A;
--waiting:#1E2E42;--pending:#2E2C28}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 "Inter","Noto Sans SC","PingFang SC",system-ui,sans-serif}
main{max-width:1200px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:28px;margin:0 0 4px} h2{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 16px}
section.exp{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;margin-top:22px;overflow-x:auto}
table{border-collapse:separate;border-spacing:6px;min-width:100%}
th{text-align:left;font-weight:600;padding:6px 10px;white-space:nowrap}
td.cell{border-radius:10px;padding:12px 14px;min-width:120px;border-left:5px solid;font-weight:600}
td.cell a{color:inherit}
td.cell .note{display:block;font-weight:400;font-size:13px;color:var(--muted)}
""" + "".join(f"td.{s}{{background:var(--{s});border-color:var(--{s}-b)}}" for s in STATE_ORDER) + """
.legend{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0 14px;font-size:14px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:14px;height:14px;border-radius:4px;display:inline-block;border-left:4px solid}
""" + "".join(f".legend i.{s}{{background:var(--{s});border-color:var(--{s}-b)}}" for s in STATE_ORDER) + """
details{margin-top:12px;font-size:14px} summary{cursor:pointer;color:var(--muted)}
.runs div{padding:6px 0;border-top:1px solid var(--line)}
code{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-size:13px}
nav a{margin-right:14px}
.added{font-size:12px;font-weight:400;color:var(--muted);margin-left:4px}
.dataset-card{border:1px solid var(--line);border-radius:12px;padding:22px;margin-top:14px}
.dataset-top{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
.dataset-name{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.dataset-name a,.dataset-name strong{font-size:21px;font-weight:650;color:var(--ink)}
.dataset-toggle{border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);padding:6px 12px;cursor:pointer;font:inherit;font-size:13px}
.dataset-toggle:hover{border-color:var(--muted)}
.dataset-status{color:var(--muted);font-size:13px}
.dataset-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;margin-top:20px}
.dataset-stats strong{display:block;font-size:26px;letter-spacing:-.5px}
.dataset-stats span{font-size:13px;color:var(--muted)}
.dataset-panel{margin-top:20px;padding-top:16px;border-top:1px solid var(--line)}
.dataset-panel p{margin:0 0 12px}
.dataset-progress{width:100%;height:6px;accent-color:var(--done-b);margin-top:16px}
@media(max-width:600px){.dataset-stats{grid-template-columns:1fr;gap:12px}.dataset-card{padding:16px}}

"""


def hf_url(ref):
    path = ref.get("path")
    suffix = f"/blob/{ref['revision']}/{path}" if path else f"/tree/{ref['revision']}"
    return f"{HF_BASE[ref['type']]}{ref['repo']}{suffix}"


def _link(url, text):
    return f'<a href="{escape(url)}">{escape(text)}</a>'


def _value_text(summary):
    mean, ci = summary["mean"], summary["ci"]
    percent = 0 <= mean <= 1
    text = f"{mean * 100:.0f}%" if percent else f"{mean:.3g}"
    if ci is not None:
        text += f" ± {ci * 100:.0f}" if percent else f" ± {ci:.2g}"
    return text


def _cell_anchor(view, cell):
    """Attribute-safe anchor: row/col values are free text from the contract."""
    return escape(f"cell-{view['experiment']}-{cell['row']}-{cell['col']}", quote=True)


def _cell_html(view, cell):
    if view["kind"] == "dataset":
        results = [entry["dataset_result"] for entry in cell["entries"] if entry["dataset_result"] is not None]
        accepted = sum(result["accepted_samples"] for result in results)
        published = sum(result["published_samples"] for result in results)
        text = f"{accepted:,} 条" if results else ("数量未记录" if cell["state"] == "done" else STATE_LABEL[cell["state"]])
        note = f"已发布 {published:,} 条" if results else STATE_LABEL[cell["state"]]
        if results and cell["state"] != "done":
            note += " · " + STATE_LABEL[cell["state"]]
        if len(results) < cell["expected"] and results:
            note += f" · 已记录 {len(results)}/{cell['expected']} 个单元"
        return f'<td class="cell {cell["state"]}">{escape(text)}<span class="note">{escape(note)}</span></td>'
    state = cell["state"]
    summary = cell["summary"]
    counted = f"{summary['n'] if summary else 0}/{cell['expected']}" if cell["expected"] > 1 else ""
    if summary is None:
        body = escape(STATE_LABEL[state])
    elif cell["expected"] == 1:
        body = _link(f"#run-{cell['entries'][0]['run']}", _value_text(summary))
    else:
        body = _link(f"#{_cell_anchor(view, cell)}", _value_text(summary))
    note = escape(" · ".join(part for part in (counted, STATE_LABEL[state] if summary else "") if part))
    return f'<td class="cell {state}">{body}<span class="note">{note}</span></td>'


def _added(axis_value):
    """Marks a row or column appended after the view was registered (R44)."""
    return '<span class="added" title="登记后经授权追加">后加</span>' if axis_value["added"] else ""


def _row_label(row):
    marks = row["products"]
    if len(marks) == 1:
        return _link(hf_url(marks[0]["hf"]), row["label"]) + _added(row)
    links = " ".join(_link(hf_url(mark["hf"]), str(index)) for index, mark in enumerate(marks, 1))
    return escape(row["label"]) + (f" <sup>{links}</sup>" if links else "") + _added(row)


def _added_replicates(view):
    added = view["added_replicates"]
    return f'<span class="added">后加重复：{escape(", ".join(added))}</span>' if added else ""


def _table(view):
    head = "".join(f"<th>{escape(col['label'])}{_added(col)}</th>" for col in view["cols"])
    body = "".join(f"<tr><th>{_row_label(row)}</th>{''.join(_cell_html(view, c) for c in row['cells'])}</tr>"
                   for row in view["rows"])
    return f"<table><thead><tr><th></th>{head}</tr></thead><tbody>{body}</tbody></table>"


def _legend():
    items = "".join(f'<span><i class="{s}"></i>{STATE_LABEL[s]}</span>' for s in STATE_ORDER)
    return f'<div class="legend">{items}</div>'


def _cell_details(view):
    parts = []
    for cell in (c for row in view["rows"] for c in row["cells"] if c["expected"] > 1):
        lines = "".join(f"<div>{escape(str(e['replicate']))}：{_entry_text(e)}</div>" for e in cell["entries"])
        parts.append(f'<div id="{_cell_anchor(view, cell)}"><b>{escape(cell["row"])} × {escape(cell["col"])}</b>'
                     f'<div class="runs">{lines}</div></div>')
    return "".join(parts)


def _entry_text(entry):
    if entry["dataset_result"] is not None:
        result = entry["dataset_result"]
        return _link(f"#run-{entry['run']}",
                     f"{result['accepted_samples']} 条（已发布 {result['published_samples']} 条）")
    if entry["value"] is not None:
        return _link(f"#run-{entry['run']}", f"{entry['value']:.4g}")
    return escape(STATE_LABEL[entry["state"]])


def _run_html(state, run_id):
    run = state.records[run_id]
    facts = [f"<b>{escape(run['alias'])}</b> {escape(str(run.get('slot', {})))} · {escape(run['status'])}"]
    if "capture" in run:
        label = "评测代码" if run["run_kind"] == "evaluation" else "代码"
        facts.append(f"{label} <code>{escape(run['capture']['git']['commit'][:12])}</code>")
    facts += [f"输入 {_link(hf_url(ref), ref['repo'])}" for ref in run["hf_inputs"]]
    facts += [f"产出 {escape(name)} {_link(hf_url(ref), ref['repo'])}" for name, ref in run["outputs"].items()]
    if "dataset_result" in run:
        result = run["dataset_result"]
        facts.append(f"补录数据结果：有效 {result['accepted_samples']} 条，已发布 {result['published_samples']} 条")
        if "hf" in result:
            facts.append(f"数据集 {_link(hf_url(result['hf']), result['hf']['repo'])}")
        facts.append(f"结果证据 {_link(hf_url(result['evidence']), 'HF')}")
    if run["metrics"]:
        facts.append(escape(", ".join(f"{k}={v:.4g}" for k, v in sorted(run["metrics"].items()))))
    facts.append(f"有效性：{escape(run['validity'])}")
    facts.append(f"执行次数：{run['attempt']} · 证据归档：{escape(run['archive_status'])}")
    if run.get("capture_gaps"):
        facts.append(f"捕获缺口：{escape(', '.join(run['capture_gaps']))}")
    if run.get("deprecation_reason"):
        facts.append(f"废弃原因：{escape(run['deprecation_reason'])}")
    if run.get("failure_reason"):
        facts.append(f"失败原因：{escape(run['failure_reason'])}")
    if run.get("checkpoint"):
        facts.append(f"评测 checkpoint {_link(hf_url(run['checkpoint']), run['checkpoint']['repo'])}")
    if run.get("evidence"):
        facts.append(f"原始记录 {_link(hf_url(run['evidence']), 'HF')}")
    if run.get("provenance_gaps"):
        facts.append(f"历史缺口：{escape(', '.join(run['provenance_gaps']))}")
    if run.get("supersedes"):
        facts.append(f"替换了 {escape(state.label(run['supersedes']))}")
    return f'<div id="run-{run_id}">{" · ".join(facts)}</div>'


def _decisions(view):
    if not view["decisions"]:
        return ""
    items = "".join(f"<li>{escape(d['decision'])}（{escape(d['status'])}）</li>" for d in view["decisions"])
    return f"<h3>相关决定</h3><ul>{items}</ul>"


def _dataset_card(view):
    card = view["dataset_card"]
    panel_id = f"dataset-info-{view['experiment']}"
    if card is None:
        name = f'<strong>{escape(view["title"])}</strong>'
        status = STATE_LABEL[cell_state([c["state"] for row in view["rows"] for c in row["cells"]])]
        stats = '<div class="dataset-stats"><div><strong>—</strong><span>已发布轨迹 · 尚未记录</span></div><div><strong>—</strong><span>资产覆盖 · 尚未记录</span></div><div><strong>—</strong><span>计划目标 · 尚未设定</span></div></div>'
        description = "<p>来源：" + "、".join(escape(row["label"]) for row in view["rows"]) + "</p>"
        progress = ""
    else:
        target = card["planned_assets"] * card["samples_per_asset"]
        met = card["published_assets"] >= card["planned_assets"] and card["published_samples"] >= target
        name = _link(hf_url(card["hf"]), card["title"])
        status = "已达计划目标" if met else "已发布 · 未达计划目标"
        stats = (f'<div class="dataset-stats"><div><strong>{card["published_samples"]:,}</strong><span>已发布轨迹 / 条</span></div>'
                 f'<div><strong>{card["published_assets"]} / {card["planned_assets"]}</strong><span>已发布资产 / 计划资产</span></div>'
                 f'<div><strong>{card["task_count"]}</strong><span>任务种类</span></div></div>')
        description = (f'<p>{escape(card["description"])}</p><p>计划采集 {card["planned_assets"]} 个资产，每个资产 {card["samples_per_asset"]} 条，'
                       f'预计目标为 {target:,} 条；这是按采集配额计算的计划值，实际发布量以验收通过的数据为准。</p>'
                       f'<p>当前发布 {card["published_samples"]:,} 条，覆盖 {card["published_assets"]} 个资产。'
                       f'资产明细与全部轨迹见 {_link(hf_url(card["hf"]), "Hugging Face 数据集")}。</p>')
        progress = f'<progress class="dataset-progress" value="{card["published_assets"]}" max="{card["planned_assets"]}" aria-label="已发布资产覆盖"></progress>'
    toggle = ("const panel=document.getElementById(this.getAttribute('aria-controls'));"
              "panel.hidden=!panel.hidden;this.setAttribute('aria-expanded',String(!panel.hidden));"
              "this.textContent=panel.hidden?'任务说明 ⌄':'收起说明 ⌃';")
    button = (f'<button type="button" class="dataset-toggle" aria-expanded="false" aria-controls="{panel_id}" '
              f'onclick="{escape(toggle, quote=True)}">任务说明 ⌄</button>')
    panel = (f'<div class="dataset-panel" id="{panel_id}" hidden>{description}'
             f'<details><summary>已登记的生产单元明细（{len(view["rows"])} 个）</summary>{_table(view)}</details></div>')
    return (f'<article class="dataset-card"><div class="dataset-top"><div class="dataset-name">{name}{button}</div>'
            f'<span class="dataset-status">{status}</span></div>{stats}{progress}{panel}</article>')


def experiment_section(state, view):
    exp = state.records[view["experiment"]]
    runs = "".join(_run_html(state, ref) for ref in exp["runs"])
    if view["kind"] == "dataset":
        body = _dataset_card(view)
    else:
        hypothesis = f'<p class="sub">假设：{escape(view["hypothesis"])}</p>' if view["hypothesis"] else ""
        body = (f'{hypothesis}<p class="sub">指标：{escape(view["metric"])}{_added_replicates(view)}</p>'
                f'{_legend()}{_table(view)}{_decisions(view)}'
                f'<details><summary>每格明细</summary>{_cell_details(view)}</details>')
    return (f'<section class="exp" id="exp-{view["experiment"]}"><h2>{escape(view["alias"])} · {escape(view["title"])}</h2>'
            f'{body}<details><summary>运行明细（给 agent 看）</summary><div class="runs">{runs}</div></details></section>')


def page(title, sections, nav=""):
    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title>'
            f'<style>{CSS}</style></head><body><main><h1>{escape(title)}</h1>{nav}{"".join(sections)}</main></body></html>')


def master_page(state, views):
    nav = "<nav>" + "".join(_link(f"#exp-{v['experiment']}", v["title"]) for v in views) + "</nav>"
    return page("实验总表", [experiment_section(state, view) for view in views], nav)
