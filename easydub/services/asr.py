"""ASR 服务：两个 provider，输出统一的带时间轴 Segment 列表。

- local      faster-whisper 本地推理（免费、离线），碎段多，需要合并
- openrouter OpenRouter 网关的云端转录（fish-audio/transcribe-1 等），
             verbose_json 带段级/字级时间轴，整段返回，需要按句切开
"""
import os
from pathlib import Path
from typing import List

import httpx

# HuggingFace 国内镜像；显式设置过 HF_ENDPOINT 时不覆盖
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# 碎段合并阈值：间隔小于 0.6s 且合并后不超长的相邻段视为同一句
MAX_GAP = 0.6
MAX_CHARS = 60

# 句末标点：字级时间戳切分长段的依据
_PUNCT = "。！？!?；;"


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _need_space(a: str, b: str) -> bool:
    """两个词之间要不要补空格：任一侧是中日文就不加；标点贴前词；其余加空格。"""
    if _is_cjk(a) or _is_cjk(b):
        return False
    return b.isalnum() or b in "'’"


def _join_words(words) -> str:
    out = ""
    for w in words:
        w = str(w)
        if out and _need_space(out[-1], w[0]) and not w[0].isspace():
            out += " "
        out += w
    return out


def merge_segments(raw: List[dict]) -> List[dict]:
    """Whisper 的原始分段太碎，直接逐段翻译会丢上下文且 TTS 次数爆炸。"""
    merged: List[dict] = []
    for seg in raw:
        text = seg["text"].strip()
        if not text:
            continue
        if merged:
            last = merged[-1]
            gap = seg["start"] - last["end"]
            joined_len = len(last["text"]) + len(text)
            if gap <= MAX_GAP and joined_len <= MAX_CHARS:
                sep = "" if _is_cjk(last["text"][-1]) else " "
                last["text"] += sep + text
                last["end"] = seg["end"]
                continue
        merged.append({"start": seg["start"], "end": seg["end"], "text": text})
    return merged


def transcribe(audio_path, model_size: str = "base") -> List[dict]:
    from faster_whisper import WhisperModel  # 延迟导入，未装依赖时其余功能可用

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segs, _info = model.transcribe(
        str(audio_path), vad_filter=True, beam_size=5,
    )
    return merge_segments([
        {"start": s.start, "end": s.end, "text": s.text} for s in segs
    ])


def _split_by_words(raw: List[dict], words: List[dict],
                    pause: float = 0.35) -> List[dict]:
    """用字级时间戳把长段切开，逐句独立翻译/配音/对齐，节奏才对得上原片。

    两种断句依据，优先级：
    1. 句末标点（部分模型 word 流带标点）
    2. 字间隙 > pause 秒（fish 等中文转录不带标点，但句间停顿会留在时间轴上）
    """
    if not words:
        return raw
    out: List[dict] = []
    for seg in raw:
        in_seg = [w for w in words
                  if float(w["start"]) >= seg["start"] - 0.05
                  and float(w["end"]) <= seg["end"] + 0.05]
        if len(in_seg) < 2:
            out.append(seg)
            continue
        pieces, cur, cur_start = [], [], in_seg[0]["start"]
        for i, w in enumerate(in_seg):
            cur.append(str(w["word"]))
            at_punct = str(w["word"]).strip().endswith(tuple(_PUNCT))
            next_gap = (float(in_seg[i + 1]["start"]) - float(w["end"])
                        if i + 1 < len(in_seg) else 0.0)
            if at_punct or next_gap > pause:
                pieces.append({"start": cur_start, "end": float(w["end"]),
                               "text": _join_words(cur).strip()})
                cur, cur_start = [], float(in_seg[i + 1]["start"]) \
                    if i + 1 < len(in_seg) else float(w["end"])
        if cur:
            pieces.append({"start": cur_start, "end": seg["end"],
                           "text": _join_words(cur).strip()})
        out.extend(pieces or [seg])
    return [p for p in out if p["text"]]


def transcribe_openrouter(audio_path, api_key: str,
                          model: str = "fish-audio/transcribe-1",
                          base_url: str = "https://openrouter.ai/api/v1") -> List[dict]:
    """OpenAI 兼容转录接口（multipart）。必须 verbose_json 拿时间轴。

    注意网关对上传体积有上限，长音频先过 media.extract_audio 压成 16k 单声道。
    """
    if not api_key:
        raise ValueError("缺少 OPENROUTER_API_KEY")
    resp = httpx.post(
        f"{base_url.rstrip('/')}/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": (Path(audio_path).name,
                        Path(audio_path).read_bytes(), "audio/wav")},
        data={"model": model, "response_format": "verbose_json"},
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    raw = [
        {"start": float(s["start"]), "end": float(s["end"]),
         "text": s["text"].strip()}
        for s in data.get("segments", []) if s.get("text", "").strip()
    ]
    # 云端段落已是句级粒度，只切不并
    return _split_by_words(raw, data.get("words") or [])
