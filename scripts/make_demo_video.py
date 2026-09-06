"""生成对比演示（D11）：左源片右成品拼屏视频 + README 用 GIF。

用法: .venv/bin/python scripts/make_demo_video.py
输入: 已跑好的 TED 40s 成品（artifacts/ted_seg40/*.dub.zh.mp4）
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "The Sneaky Language Tricks Cults Use to Influence You  Amanda Montell  TED - TED (720p, h264).mp4"
CUT = ROOT / "samples" / "ted_src40.mp4"
DUB = ROOT / "artifacts" / "ted_seg40" / "ted_seg40.dub.zh.mp4"
OUT_DIR = ROOT / "artifacts" / "demo"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # 1) 源片 40s 切段（与成品对齐）
    subprocess.run([
        "ffmpeg", "-y", "-ss", "100", "-to", "140", "-i", str(SRC),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-an", str(CUT),
    ], check=True, capture_output=True)

    # 2) 拼屏：左源片（原声）右成品（中文配音+双语字幕），带标签
    out = OUT_DIR / "compare_ted_zh.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(CUT), "-i", str(DUB),
        "-filter_complex",
        "[0:v]scale=640:-1,drawtext=text='SRC':fontsize=36:fontcolor=white:"
        "box=1:boxcolor=black@0.5:x=10:y=10[l];"
        "[1:v]scale=640:-1,drawtext=text='DUB zh':fontsize=36:fontcolor=white:"
        "box=1:boxcolor=black@0.5:x=10:y=10[r];"
        "[l][r]hstack=inputs=2[v]",
        "-map", "[v]", "-map", "1:a?",
        "-c:v", "libx264", "-crf", "21", "-preset", "fast",
        "-c:a", "aac", "-shortest", str(out),
    ], check=True, capture_output=True)

    # 3) README 用 GIF：取 15-21s（口型段），480px 宽 10fps
    gif = OUT_DIR / "demo.gif"
    subprocess.run([
        "ffmpeg", "-y", "-ss", "15", "-to", "21", "-i", str(out),
        "-vf", "fps=10,scale=480:-1:flags=lanczos,split[s0][s1];"
               "[s0]palettegen[p];[s1][p]paletteuse",
        str(gif),
    ], check=True, capture_output=True)

    print(f"对比视频: {out}\nGIF: {gif}")


if __name__ == "__main__":
    main()
