"""数据契约：Segment 是贯穿全流水线的唯一结构。

ASR 产出它，翻译只补 translated 字段，TTS 只补音频字段，
对齐只补 tempo/action 字段。每个阶段读上一阶段的 json、写自己的 json，
因此可以断点续跑，也可以单独重跑某一阶段。
"""
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class Segment:
    start: float                      # 槽位起点（秒）
    end: float                        # 槽位终点（秒），end - start 即原语音时长
    text: str                         # 原文（ASR 识别）
    translated: Optional[str] = None  # 译文
    audio_path: Optional[str] = None  # TTS 合成的单段音频
    audio_duration: Optional[float] = None
    tempo: float = 1.0                # 对齐变速系数（>1 表示加速）
    action: str = ""                  # 对齐动作: fit / atempo / overflow / empty

    @property
    def slot(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        return cls(**d)


def save_segments(segments: List[Segment], path) -> None:
    Path(path).write_text(
        json.dumps([s.to_dict() for s in segments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_segments(path) -> List[Segment]:
    data = json.loads(open(path, encoding="utf-8").read())
    return [Segment.from_dict(d) for d in data]
