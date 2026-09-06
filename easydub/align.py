"""时长对齐：音画同步问题的核心。

策略是"双向夹逼"——
  生成端（translator）：提示词里按槽位秒数给译文限长，从源头控制 TTS 时长；
  后处理端（本模块）：对外语音频做变速不变调，把超时部分压回槽位。

纯函数、无 IO，方便单测和以后做 AB 实验。
"""
from typing import Dict, List

# 变速限幅：超过 1.25 倍速听感明显发急，低于 0.85 倍拖沓
MAX_TEMPO = 1.25
MIN_TEMPO = 0.85


def plan_alignment(slot: float, tts_duration: float,
                   max_tempo: float = MAX_TEMPO,
                   allow_atempo: bool = True) -> Dict:
    """给定槽位时长和外语音频时长，决定对齐动作。

    返回 {tempo, action, overrun}：
      fit      音频本就比槽位短，原速准时开始
      atempo   超时但可变速压回，无溢出
      overflow 变速到上限仍放不下，溢出 overrun 秒（记录到报告，供提示词重译）

    allow_atempo=False 用于 E1 消融实验：关掉变速端，纯看生成端限长的效果。
    """
    if not tts_duration or tts_duration <= 0 or slot <= 0:
        return {"tempo": 1.0, "action": "empty", "overrun": 0.0}

    ratio = tts_duration / slot
    if ratio <= 1.0:
        return {"tempo": 1.0, "action": "fit", "overrun": 0.0}
    if not allow_atempo:
        return {
            "tempo": 1.0,
            "action": "overflow",
            "overrun": round(tts_duration - slot, 2),
        }
    if ratio <= max_tempo:
        return {"tempo": round(ratio, 4), "action": "atempo", "overrun": 0.0}
    return {
        "tempo": max_tempo,
        "action": "overflow",
        "overrun": round(tts_duration / max_tempo - slot, 2),
    }


def find_overflow(segments, cps: float = 14) -> List[dict]:
    """找出配音超时的段落，并算出重译时的字符数预算。

    输入：Segment 列表（通常读自 segments_final.json）
    输出：每个超时段一个 {index, slot, text, budget}；
          budget = 槽位秒数 × cps，是重译时译文的字符数上限。
          英语旁白约每秒 14 字符（含空格），所以槽位 2.32 秒 → 预算 32 字符。
    """
    plans: List[dict] = []
    for i, seg in enumerate(segments):
        if seg.action != "overflow":
            continue
        plans.append({
            "index": i,
            "slot": round(seg.slot, 2),
            "text": seg.translated,
            "budget": int(seg.slot * cps),
        })
    return plans


def apply_onset_shift(segments, shift: float, min_gap: float = 0.05) -> int:
    """按 ASR 通道的时间轴偏差整体平移段起止，返回改动的段数。

    云 ASR（fish transcribe 等）受语音软起音影响，起点普遍比真实开口晚
    （eval 实测 ~0.15s）→ shift>0 前移补偿；本地 whisper 的时间戳倾向
    早于真实开口（E5 实测 ~0.08s）→ shift<0 后移补偿。起止同步平移
    （保槽位时长，预算与对齐逻辑不受扰）。
    前移最多借完与上一段的空隙、后移最多借完与下一段的空隙，均留
    min_gap 防贴脸。
    """
    n = 0
    for i, seg in enumerate(segments):
        if shift > 0:
            prev_end = segments[i - 1].end if i else 0.0
            allowed = max(0.0, seg.start - prev_end - min_gap)
            d = min(shift, allowed)
        elif shift < 0:
            nxt = segments[i + 1].start if i + 1 < len(segments) \
                else seg.end + 10.0
            allowed = max(0.0, nxt - seg.end - min_gap)
            d = max(shift, -allowed)
        else:
            continue
        if d:
            seg.start = round(seg.start - d, 3)
            seg.end = round(seg.end - d, 3)
            n += 1
    return n


def calibrate_cps(segments, base_cps: float) -> float:
    """用本次 TTS 实测时长反推实际语速（字符/秒），收紧重译预算。

    静态 CPS 表是拍脑袋值，换 TTS 供应商/音色就漂（es 实测就比表慢，
    导致重译预算偏松、反复溢出）。取 fit/atempo 段的
    len(译文)/实测时长 中位数：只往下修（实测更慢 → 预算更紧），
    最多下修 40% 防病态值；实测比表快时保守沿用表值。
    """
    vals = sorted(
        len(s.translated) / s.audio_duration
        for s in segments
        if s.action in ("fit", "atempo") and s.translated and s.audio_duration
    )
    if not vals:
        return base_cps
    measured = vals[len(vals) // 2]
    return round(max(base_cps * 0.6, min(base_cps, measured)), 2)


def reclassify_spill(segments, total: float = None) -> int:
    """把"溢出但撞不到下一句"的段从 overflow 改判为 spill，返回改判数。

    overflow 音频本来就会自然延后到句间空隙里播完（build_dub_track 按
    起始秒定位），只有当音频尾部撞上下一句配音开头（或视频结尾）才是真
    冲突。超短槽位（如 0.24s 的"go"）翻译救不了，但往往借空隙就够——
    这类段不该烧 LLM 重译，也不该计入溢出率。
    """
    n = 0
    for i, seg in enumerate(segments):
        if seg.action != "overflow":
            continue
        audio_end = seg.start + (seg.audio_duration or 0)
        limit = total if total is not None else float("inf")
        for nxt in segments[i + 1:]:
            if nxt.audio_duration:
                limit = min(limit, nxt.start)
                break
        if audio_end <= limit - 0.05:
            seg.action = "spill"
            n += 1
    return n
