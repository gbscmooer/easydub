"""生成示例素材：中文广告配音 + 纯色视频，用于无真实素材时跑通流水线。

用法: .venv/bin/python scripts/make_sample.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from easydub.media import build_dub_track, probe_duration  # noqa: E402
from easydub.services.tts import EdgeTTS  # noqa: E402

LINES = [
    "云杉咖啡，来自长白山的唤醒。",
    "每一杯，都是现磨的新鲜。",
    "今天下单，立减二十元。",
]


def main() -> None:
    samples = ROOT / "samples"
    samples.mkdir(exist_ok=True)
    tmp = samples / "_lines"
    tmp.mkdir(exist_ok=True)

    clips, t = [], 0.3
    for i, line in enumerate(LINES):
        p = tmp / f"zh_{i}.mp3"
        EdgeTTS(lang="zh").synth(line, p)
        clips.append((t, str(p)))
        t += probe_duration(p) + 0.4
    total = t + 0.5

    wav = samples / "sample_zh.wav"
    build_dub_track(clips, total, wav)
    out = samples / "sample.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x16324f:s=1280x720:d={total:.2f}:r=30",
        "-i", str(wav),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(out),
    ], check=True, capture_output=True)
    print(f"示例视频已生成: {out}")


if __name__ == "__main__":
    main()
