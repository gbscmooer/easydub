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
