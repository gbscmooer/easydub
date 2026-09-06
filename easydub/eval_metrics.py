"""评测指标（GOAL §6.1）：纯函数，输入来自 report.json / meta.json / 管线计时。

E1 消融、E4 多语言、成本核算的数字全部出自这里，论文/简历直接引用。
"""
from typing import Dict, List, Optional


_CN_DIGITS = {"零": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4",
              "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
_PUNCT = "，。！？；：、！？.,!?;:\"'()（）[]【】—-…·"


def normalize_zh(text: str) -> str:
    """CER 前的归一化：中文数字→阿拉伯数字、去标点、去空白。

    ASR 输出与文稿的"信息性差异"只剩真正的识别错字；
    "两件八折"vs"2件8折"这类形式差异不再污染 CER。
    """
    out = []
    for ch in text:
        if ch in _PUNCT or ch.isspace():
            continue
        out.append(_CN_DIGITS.get(ch, ch))
    return "".join(out)


def cer(expected: str, recognized: str) -> float:
    """字符错误率：Levenshtein 距离 / 期望字符数（忽略空白与形式差异）。"""
    a = list(normalize_zh(expected))
    b = list(normalize_zh(recognized))
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (ca != cb))
        prev = cur
    return round(prev[-1] / len(a), 4)


def subtitle_offset(segments: List[dict], lines: List[dict],
                    tol: float = 0.8) -> Optional[Dict]:
    """字幕起点 vs 源语音起点的偏移（秒）。

    仅当 ASR 段数与已知台词行数一致时才可逐行配对（断句合并后无法对齐，
    返回 None 表示本次不计入）。offset > tol 的行视为断句错位，也不计入。
    """
    if len(segments) != len(lines):
        return None
    offs = [abs(s["start"] - l["start"]) for s, l in zip(segments, lines)]
    if offs and max(offs) > tol:
        return None
    if not offs:
        return None
    return {"mean": round(sum(offs) / len(offs), 3),
            "max": round(max(offs), 3)}


def run_metrics(report: dict, wall_seconds: float, video_seconds: float,
                lines: Optional[List[dict]] = None,
                asr_text: str = None) -> Dict:
    """把一次 run 的 report.json + 计时 + 素材文稿折成一行指标。"""
    summary = report["summary"]
    total = max(summary["total"], 1)
    overflow_rate = round(summary["overflow"] / total, 4)
    # spill = 溢出但借句间空隙容纳，不撞下一句，也算对齐成功
    matched = summary["fit"] + summary["atempo"] + summary.get("spill", 0)
    match_rate = round(matched / total, 4)

    out = {
        "segments": summary["total"],
        "overflow_rate": overflow_rate,
        "match_rate": match_rate,
        "wall_seconds": round(wall_seconds, 1),
        "rt_factor": round(wall_seconds / video_seconds, 2) if video_seconds else None,
    }
    if asr_text is not None and lines is not None:
        expected = "".join(l["text"] for l in lines)
        out["cer"] = cer(expected, asr_text)
    if lines is not None:
        out["subtitle_offset"] = subtitle_offset(report["segments"], lines)
    return out


# 单价表（¥，按供应商价目表自行修订；0 表示免费通道）
# fish-audio ASR 按秒计费约 ¥0.0008/s；DeepSeek 约 ¥1/M tokens（粗估 1 调用=1.5k tok）
PRICES = {
    "asr_per_second": 0.0008,   # openrouter fish-audio/transcribe-1
    "llm_per_call": 0.002,      # deepseek-chat，一次全量翻译粗估
    "tts_per_char": {"edge": 0.0, "openrouter": 0.0, "minimax": 0.0002},
    "lipsync_per_second": 0.0,  # 自建 GPU 电费忽略；sync.so 对比时填官方价
}


def cost_estimate(asr_seconds: float, llm_calls: int, tts_chars: int,
                  tts_provider: str, lipsync_seconds: float = 0.0) -> Dict:
    item = {
        "asr": round(asr_seconds * PRICES["asr_per_second"], 4),
        "llm": round(llm_calls * PRICES["llm_per_call"], 4),
        "tts": round(tts_chars * PRICES["tts_per_char"].get(tts_provider, 0), 4),
        "lipsync": round(lipsync_seconds * PRICES["lipsync_per_second"], 4),
    }
    item["total"] = round(sum(item.values()), 4)
    return item
