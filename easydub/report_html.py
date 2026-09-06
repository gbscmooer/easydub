"""把 report.<lang>.json 渲染成交互式 HTML 报告（零依赖，内联样式）。

为什么不用前端框架：报告是一次性只读产物，单文件 HTML 可以直接发给人看、
直接挂到 artifacts 目录，也方便截图进演示材料。时间轴条带按动作着色，
悬停单段可看原文/译文/槽位/配音时长——对齐质量一眼可读。
"""
import html
import json
from pathlib import Path

_ACTION_COLOR = {
    "fit": "#3fb96f",      # 绿：原速准时
    "atempo": "#4f8ff7",   # 蓝：变速压回
    "spill": "#e8a13c",    # 琥珀：借句间空隙
    "overflow": "#e5534b", # 红：真冲突
}
_ACTION_LABEL = {"fit": "原速", "atempo": "变速", "spill": "借空隙",
                 "overflow": "溢出", "empty": "空"}


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _timeline(segments, total: float) -> str:
    """按时间比例排布的条带图：每段一个绝对定位色块，悬停看详情。"""
    total = max(total, 0.001)
    blocks = []
    for s in segments:
        left = min(s["start"] / total * 100, 99.5)
        width = max((s["end"] - s["start"]) / total * 100, 0.3)
        color = _ACTION_COLOR.get(s["action"], "#888")
        tip = (f"段{s['i']} [{s['start']:.2f}-{s['end']:.2f}]s "
               f"槽位{s['slot']}s 配音{s['tts']}s {_ACTION_LABEL.get(s['action'], s['action'])}\\n"
               f"原文：{s['text']}\\n译文：{s['translated']}")
        blocks.append(
            f'<div class="blk" style="left:{left:.2f}%;width:{width:.2f}%;'
            f'background:{color}" title="{_esc(tip)}"><span>{s["i"]}</span></div>')
    return "".join(blocks)


def _rows(segments) -> str:
    trs = []
    for s in segments:
        action = s["action"]
        color = _ACTION_COLOR.get(action, "#888")
        trs.append(
            f"<tr><td>{s['i']}</td>"
            f"<td>{s['start']:.2f}–{s['end']:.2f}s</td>"
            f"<td>{s['slot']:.2f}s</td><td>{s['tts']:.2f}s</td>"
            f"<td>{s['tempo']:.2f}×</td>"
            f'<td><span class="badge" style="background:{color}">'
            f"{_ACTION_LABEL.get(action, _esc(action))}</span></td>"
            f"<td>{_esc(s['translated'])}</td>"
            f"<td class='src'>{_esc(s['text'])}</td></tr>")
    return "".join(trs)


_CSS = """
body{font-family:system-ui,'PingFang SC','Microsoft YaHei',sans-serif;
     max-width:960px;margin:0 auto;padding:32px 20px;color:#e8eaf0;
     background:#0f1115}
h1{font-size:22px}.sub{color:#9aa1af}
.cards{display:flex;gap:12px;margin:20px 0;flex-wrap:wrap}
.card{background:#181c26;border-radius:10px;padding:12px 18px;min-width:86px}
.card b{display:block;font-size:24px}.card span{color:#9aa1af;font-size:13px}
.timeline{position:relative;height:44px;background:#181c26;border-radius:8px;
          margin:12px 0 4px;overflow:hidden}
.blk{position:absolute;top:6px;bottom:6px;border-radius:5px;color:#fff;
     font-size:11px;display:flex;align-items:center;justify-content:center;
     cursor:default}
.blk span{overflow:hidden;white-space:nowrap}
.axis{display:flex;justify-content:space-between;color:#9aa1af;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:14px;margin-top:18px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid #232837}
th{color:#9aa1af;font-weight:500}
.badge{color:#fff;border-radius:5px;padding:2px 8px;font-size:12px}
td.src{color:#9aa1af}
.meta{color:#9aa1af;font-size:13px;margin-top:6px;line-height:1.8}
"""


def write_html_report(report: dict, dst) -> Path:
    """report dict → 单文件 HTML。返回写入路径。"""
    summary = report["summary"]
    total = summary["total"]
    scale = max((s["end"] for s in report["segments"]), default=1.0) or 1.0
    cards = [
        ("总段数", total, ""), ("原速", summary["fit"], "#3fb96f"),
        ("变速", summary["atempo"], "#4f8ff7"),
        ("借空隙", summary.get("spill", 0), "#e8a13c"),
        ("溢出", summary["overflow"],
         "#e5534b" if summary["overflow"] else "#3fb96f"),
    ]
    cards_html = "".join(
        f'<div class="card"><b style="color:{c or "inherit"}">{v}</b>'
        f"<span>{_esc(k)}</span></div>" for k, v, c in cards)
    meta_bits = [f"TTS {report.get('tts_provider', '?')}",
                 f"音色 {report.get('voice') or '默认'}",
                 f"背景音乐 {'保留+闪避' if report.get('keep_bgm') else '关闭'}",
                 f"重译 {report.get('retry_rounds', 0)} 轮"]
    if report.get("measured_cps"):
        meta_bits.append(f"实测语速 {report['measured_cps']} 字符/秒")
    timings = report.get("stage_timings") or {}
    for stage in ("asr", "translate", "tts_align", "mix", "lipsync"):
        if stage in timings:
            meta_bits.append(f"{stage} {timings[stage]}s")
    src_name = Path(report.get("video", "")).name

    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>EasyDub 对齐报告 — {_esc(src_name)}</title><style>{_CSS}</style></head>
<body>
<h1>对齐质量报告</h1>
<p class="sub">{_esc(src_name)} → 目标语言 {report.get("target_lang", "?")}</p>
<p class="meta">{" · ".join(_esc(b) for b in meta_bits)}</p>
<div class="cards">{cards_html}</div>
<h2>时间轴</h2>
<div class="timeline">{_timeline(report["segments"], scale)}</div>
<div class="axis"><span>0s</span><span>{scale:.1f}s</span></div>
<h2>逐段明细</h2>
<table><thead><tr><th>#</th><th>时间</th><th>槽位</th><th>配音</th>
<th>变速</th><th>动作</th><th>译文</th><th>原文</th></tr></thead>
<tbody>{_rows(report["segments"])}</tbody></table>
</body></html>"""
    dst = Path(dst)
    dst.write_text(page, encoding="utf-8")
    return dst


def write_html_report_file(report_path, dst=None) -> Path:
    """从 report.<lang>.json 文件生成同名 .html（dst 缺省同目录）。"""
    report_path = Path(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return write_html_report(report, dst or report_path.with_suffix(".html"))
