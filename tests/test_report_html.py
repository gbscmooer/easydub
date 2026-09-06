"""HTML 报告生成器的单测：结构完整、动作着色、转义安全。"""
from easydub.report_html import write_html_report

REPORT = {
    "video": "demo.mp4",
    "target_lang": "en",
    "tts_provider": "edge",
    "voice": "en-US-JennyNeural",
    "retry_rounds": 1,
    "measured_cps": 13.2,
    "keep_bgm": True,
    "summary": {"total": 3, "fit": 1, "atempo": 1, "spill": 1,
                "overflow": 0, "overflow_before_retry": 1},
    "segments": [
        {"i": 0, "start": 0.4, "end": 2.4, "slot": 2.0, "tts": 1.8,
         "tempo": 1.0, "action": "fit", "text": "原文<script>",
         "translated": "Hello <b>world</b>"},
        {"i": 1, "start": 3.0, "end": 4.5, "slot": 1.5, "tts": 1.7,
         "tempo": 1.13, "action": "atempo", "text": "第二句",
         "translated": "Second line"},
        {"i": 2, "start": 5.0, "end": 5.3, "slot": 0.3, "tts": 0.5,
         "tempo": 1.25, "action": "spill", "text": "短句",
         "translated": "Go"},
    ],
}


def test_html_contains_summary_and_meta(tmp_path):
    out = write_html_report(REPORT, tmp_path / "report.en.html")
    html = out.read_text(encoding="utf-8")
    assert "report.en.html" not in html  # 占位：确保写入的是页面本体
    assert "对齐质量报告" in html
    assert "en-US-JennyNeural" in html and "13.2" in html
    assert "保留+闪避" in html
    assert html.count('class="card"') == 5  # 总数/原速/变速/借空隙/溢出


def test_html_timeline_blocks_and_escape(tmp_path):
    out = write_html_report(REPORT, tmp_path / "r.html")
    html = out.read_text(encoding="utf-8")
    # 三段 → 三个色块，颜色随动作区分
    assert html.count('class="blk"') == 3
    assert "#3fb96f" in html and "#4f8ff7" in html and "#e8a13c" in html
    # 用户可见文本必须转义，杜绝注入
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>world</b>" not in html and "&lt;b&gt;world" in html
