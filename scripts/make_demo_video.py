"""生成对比演示（D11）：
1) 左源片右成品拼屏（TED zh，画面+口型+双语字幕）
2) BGM 闪避 A/B 音频对比（Nike en：BGM OFF → BGM ON，句间音乐可听）

用法: .venv/bin/python scripts/make_demo_video.py
产物: artifacts/demo/{compare_ted_zh.mp4, demo_bgm_ab.mp4, demo.gif}
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "The Sneaky Language Tricks Cults Use to Influence You  Amanda Montell  TED - TED (720p, h264).mp4"
CUT = ROOT / "samples" / "ted_src40.mp4"
DUB = ROOT / "artifacts" / "ted_seg40" / "ted_seg40.dub.zh.mp4"
NIKE = ROOT / "WHY DO IT  NIKE - Nike (720p, h264).mp4"
NIKE_DIR = ROOT / "artifacts" / "WHY DO IT  NIKE - Nike (720p, h264)"
NIKE_DUB = NIKE_DIR / "WHY DO IT  NIKE - Nike (720p, h264).dub.en.mp4"
NIKE_PURE = NIKE_DIR / "dub_track.en.wav"   # 纯配音轨（无 BGM）——BGM OFF 版音源
OUT_DIR = ROOT / "artifacts" / "demo"

# BGM A/B 窗口：含 7.9-12.3s 的纯音乐空隙 + 12.3s 起的旁白，差异最可听
WIN_START, WIN_END, WIN_LEN = 8.0, 14.0, 6.0


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 源片 40s 切段（与成品对齐）
    _run([
        "ffmpeg", "-y", "-ss", "100", "-to", "140", "-i", str(SRC),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-an", str(CUT),
    ])

    # 2) 拼屏：左源片（原声）右成品（中文配音+双语字幕），带标签
    out = OUT_DIR / "compare_ted_zh.mp4"
    _run([
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
    ])

    # 3) BGM 闪避 A/B：同一画面，前半 BGM OFF（纯配音）后半 BGM ON（闪避混音）
    ab = OUT_DIR / "demo_bgm_ab.mp4"
    seg_v = OUT_DIR / "_ab_video.mp4"
    a_off = OUT_DIR / "_ab_bgm_off.m4a"
    a_on = OUT_DIR / "_ab_bgm_on.m4a"
    part1 = OUT_DIR / "_ab_p1.mp4"
    part2 = OUT_DIR / "_ab_p2.mp4"
    _run(["ffmpeg", "-y", "-ss", str(WIN_START), "-t", str(WIN_LEN),
          "-i", str(NIKE), "-an", "-c:v", "libx264", "-crf", "20",
          "-preset", "fast", str(seg_v)])
    _run(["ffmpeg", "-y", "-ss", str(WIN_START), "-t", str(WIN_LEN),
          "-i", str(NIKE_PURE), "-c:a", "aac", "-b:a", "160k", str(a_off)])
    _run(["ffmpeg", "-y", "-ss", str(WIN_START), "-t", str(WIN_LEN),
          "-i", str(NIKE_DUB), "-map", "0:a", "-c:a", "aac", "-b:a", "160k",
          str(a_on)])
    _run(["ffmpeg", "-y", "-i", str(seg_v), "-i", str(a_off),
          "-vf", "drawtext=text='BGM OFF':fontsize=40:fontcolor=white:"
                 "box=1:boxcolor=black@0.5:x=10:y=10",
          "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-c:a", "aac",
          "-shortest", str(part1)])
    _run(["ffmpeg", "-y", "-i", str(seg_v), "-i", str(a_on),
          "-vf", "drawtext=text='BGM ON (auto-ducking)':fontsize=40:"
                 "fontcolor=white:box=1:boxcolor=black@0.5:x=10:y=10",
          "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-c:a", "aac",
          "-shortest", str(part2)])
    lst = OUT_DIR / "_ab_concat.txt"
    lst.write_text(f"file '{part1}'\nfile '{part2}'\n", encoding="utf-8")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-c:a", "aac",
          str(ab)])
    lst.unlink()
    for p in (seg_v, a_off, a_on, part1, part2):
        p.unlink(missing_ok=True)

    # 4) README 用 GIF：取拼屏 15-21s（口型段），480px 宽 10fps
    gif = OUT_DIR / "demo.gif"
    _run([
        "ffmpeg", "-y", "-ss", "15", "-to", "21", "-i", str(out),
        "-vf", "fps=10,scale=480:-1:flags=lanczos,split[s0][s1];"
               "[s0]palettegen[p];[s1][p]paletteuse",
        str(gif),
    ])

    print(f"对比视频: {out}\nBGM A/B: {ab}\nGIF: {gif}")


if __name__ == "__main__":
    main()
