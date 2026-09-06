"""Agent 决策重译（M4，求职差异点）：规则引擎给出溢出事实，LLM 逐段决策处置。

与 pipeline 内置规则重译的区别：决策权在 Agent——它看到槽位/配音时长/译文全文，
可以判断"这段槽位太短，物理上不可能放下，接受溢出比硬缩更合理"，也可以
给出语义优先的新译文，而规则只会机械砍预算。

用法:
  .venv/bin/python scripts/agent.py artifacts/c1_pure_T1_free_en/c1_pure --lang en
产出:
  - 更新后的 segments_final/report（溢出数下降）
  - artifacts/agent_log.md 追加决策日志（面试 demo 用）
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from easydub.align import plan_alignment  # noqa: E402
from easydub.config import Settings  # noqa: E402
from easydub.media import probe_duration, trim_silence  # noqa: E402
from easydub.models import Segment, load_segments, save_segments  # noqa: E402
from easydub.services.tts import make_tts  # noqa: E402
from easydub.services.translator import LLMTranslator  # noqa: E402

DECISION_PROMPT = """你是视频配音质检 Agent。以下是配音超出原时间槽位的段落，
逐段决定处置动作并输出 JSON：
- "retranslate"：给出语义等价但更短的新译文（数据里附了字符预算，TTS 语速按
  每秒字符数估算）；除非砍掉会破坏语义，否则优先选它
- "accept"：槽位短到物理上不可能放下任何语音（<0.8s，必须 accept）
  或译文已极简无从再删，接受溢出

段落（JSON）：
{segments}

只输出 JSON 数组：[{{"i": 段号, "action": "retranslate|accept",
"new_text": "新译文(retranslate 时必填)", "reason": "一句话理由"}}]
"""


def decide(translator: LLMTranslator, overflows: list) -> dict:
    items = [{
        "i": p["index"], "slot": round(p["slot"], 2),
        "budget_chars": max(2, int(p["slot"] * 14)),
        "tts_seconds": round(p["tts"], 2),
        "原文": segments_text.get(p["index"], ""),
        "超长译文": p["translated"],
    } for p in overflows]
    raw = translator._chat(DECISION_PROMPT.format(
        segments=json.dumps(items, ensure_ascii=False)))
    m = __import__("re").search(r"\[.*\]", raw, __import__("re").S)
    decisions = json.loads(m.group(0))
    return {d["i"]: d for d in decisions}


def apply_and_save(run_dir: Path, lang: str, settings: Settings,
                   segments: list) -> dict:
    """把（已被 Agent 决策修改过的）segments 重算对齐并更新报告。返回新汇总。"""
    report_p = run_dir / f"report.{lang}.json"
    report = json.loads(report_p.read_text(encoding="utf-8"))

    tts = make_tts(report.get("tts_provider", "edge"), lang, None, settings)
    tts_dir = run_dir / f"tts_{lang}"
    tts_dir.mkdir(exist_ok=True)
    for i, seg in enumerate(segments):
        h = hashlib.md5(seg.translated.encode("utf-8")).hexdigest()[:8]
        audio = tts_dir / f"seg_{i:03d}_{h}.mp3"
        if not audio.exists():
            tts.synth(seg.translated, audio)
            trim_silence(audio, audio)
        seg.audio_path = str(audio)
        seg.audio_duration = probe_duration(audio)
        plan = plan_alignment(seg.slot, seg.audio_duration)
        seg.tempo, seg.action = plan["tempo"], plan["action"]
        report["segments"][i].update(
            tts=round(seg.audio_duration, 2), tempo=seg.tempo,
            action=seg.action, translated=seg.translated)

    actions = [s.action for s in segments]
    report["summary"] = {
        "total": len(segments), "fit": actions.count("fit"),
        "atempo": actions.count("atempo"), "spill": actions.count("spill"),
        "overflow": actions.count("overflow"),
        "overflow_before_retry": report["summary"]["overflow_before_retry"],
        "agent": True,
    }
    save_segments(segments, run_dir / f"segments_final.{lang}.json")
    report_p.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return report["summary"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--lang", default="en")
    args = ap.parse_args()

    settings = Settings()
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    report = json.loads(
        (run_dir / f"report.{args.lang}.json").read_text(encoding="utf-8"))
    overflows = [s for s in report["segments"] if s["action"] == "overflow"]
    print(f"溢出段 {len(overflows)} 个")
    if not overflows:
        return

    global segments_text
    segments_text = {s["i"]: s["text"] for s in report["segments"]}

    translator = LLMTranslator(settings.llm_api_key, settings.llm_base_url,
                               settings.llm_model, args.lang)
    overflows_for_llm = [{
        "index": s["i"], "slot": s["slot"], "tts": s["tts"],
        "translated": s["translated"],
    } for s in overflows]
    decisions = decide(translator, overflows_for_llm)

    segments = load_segments(run_dir / f"segments_final.{args.lang}.json")
    log_lines = [f"\n## {run_dir.name} ({args.lang})\n"]
    applied = 0
    for i, d in sorted(decisions.items()):
        seg = segments[i]
        reason = d.get("reason", "")
        if d["action"] == "retranslate" and d.get("new_text"):
            new_text = d["new_text"]
            # 验证：Agent 译文超字符预算时回退规则重译（Agent 决策 + 规则安全网）
            budget = max(2, int(seg.slot * 14 * 0.9))
            if len(new_text) > budget:
                fallback = translator.retranslate(
                    seg.text, seg.translated, budget)
                log_lines.append(
                    f"- 段{i} Agent 译文「{new_text}」超预算 {budget} 字符，"
                    f"回退规则重译「{fallback}」")
                new_text = fallback
            seg.translated = new_text
            applied += 1
        log_lines.append(
            f"- 段{i} [{d['action']}] {reason} "
            f"槽位 {seg.slot:.2f}s 原译文「{report['segments'][i]['translated']}」"
            f"→ 新译文「{seg.translated}」")
    summary = apply_and_save(run_dir, args.lang, settings, segments)
    # 兜底：字符预算是"语速"的代理指标，对数字/缩写会失真（"20%"只算 3 字符，
    # TTS 却读 "twenty percent"）。按 TTS 实测时长反推预算，最多两轮。
    rnd = 0
    while summary["overflow"] > 0 and rnd < 2:
        rnd += 1
        for i, seg in enumerate(segments):
            if seg.action != "overflow":
                continue
            budget = max(2, int(len(seg.translated) * seg.slot * 1.2
                                / seg.audio_duration))
            new_text = translator.retranslate(seg.text, seg.translated, budget)
            if len(new_text) > budget:  # LLM 不严格遵守预算是已知问题，硬校验
                new_text = translator.retranslate(
                    seg.text, new_text, budget)
            log_lines.append(
                f"- 段{i} 兜底第{rnd}轮（实测 {seg.audio_duration:.2f}s/"
                f"槽位 {seg.slot:.2f}s，反推预算 {budget}）→「{new_text}」")
            seg.translated = new_text
        summary = apply_and_save(run_dir, args.lang, settings, segments)
    log_lines.append(f"- 结果：溢出 {len(overflows)} → {summary['overflow']}，"
                     f"应用 retranslate {applied} 段")
    (ROOT / "artifacts" / "agent_log.md").parent.mkdir(exist_ok=True)
    with open(ROOT / "artifacts" / "agent_log.md", "a",
              encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")
    print(f"Agent 决策 {len(decisions)} 条（retranslate {applied}），"
          f"溢出 {len(overflows)} → {summary['overflow']}")


if __name__ == "__main__":
    main()
