"""生成 5 条评测素材（GOAL §4.3）：纯口播/短句密集/低BGM/人脸出镜/长句慢语速。

每条素材附带 meta.json（已知台词与预期时间轴），供 eval.py 算 CER 与
字幕偏移——合成素材的"标准答案"只有自己能提供。

用法: .venv/bin/python scripts/make_eval_set.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from easydub.media import build_dub_track, probe_duration  # noqa: E402
from easydub.services.tts import EdgeTTS  # noqa: E402

OUT = ROOT / "samples" / "eval_set"

# 各素材的台词（中文口播，符合 GOAL 输入设定）
SCRIPTS = {
    "c1_pure": {  # ① 纯口播
        "lines": [
            "轻衫Home，把家穿在身上。",
            "新疆长绒棉，透气亲肤。",
            "全场两件八折，三件七折。",
            "点击下方链接，立即抢购。",
        ],
        "gap": 0.5, "rate": None,
    },
    "c2_dense": {  # ② 短句密集
        "lines": [
            "快！", "再快一点！", "就是现在。",
            "五折。", "只有今天。", "手慢无。",
            "买它。", "别犹豫。",
        ],
        "gap": 0.12, "rate": None,
    },
    "c3_bgm": {  # ③ 低音量 BGM（其余同 c1，混入底噪音乐）
        "lines": [
            "山泉气泡水，喝得到整片森林。",
            "零糖零卡，气泡够劲。",
            "整箱二十四瓶，直接半价。",
        ],
        "gap": 0.5, "rate": None, "bgm": True,
    },
    "c5_slow": {  # ⑤ 长句慢语速
        "lines": [
            "如果你也曾经在深夜里独自加班，看着窗外的车流，觉得生活好像被困在了某个循环里，那么这支视频想送给你。",
            "改变并不需要惊天动地，它可以只是明天早起十分钟，只是把手机放下五分钟，只是开始写下第一行日记。",
            "从今天起，做时间的朋友，把每一个微小的坚持，都变成未来回头看时的惊喜。",
        ],
        "gap": 0.6, "rate": "-20%",
    },
}
# ④ 人脸出镜：从 TED 真人素材切 30s（含出镜与切出镜头），无中文文稿
TED = ROOT / "The Sneaky Language Tricks Cults Use to Influence You  Amanda Montell  TED - TED (720p, h264).mp4"


def synth_clip(name: str, cfg: dict) -> dict:
    tmp = OUT / "_lines" / name
    tmp.mkdir(parents=True, exist_ok=True)
    clips, meta_lines, t = [], [], 0.4
    for i, line in enumerate(cfg["lines"]):
        p = tmp / f"zh_{i}.mp3"
        EdgeTTS(lang="zh", rate=cfg.get("rate")).synth(line, p)
        dur = probe_duration(p)
        meta_lines.append({"text": line, "start": round(t, 3),
                           "end": round(t + dur, 3)})
        clips.append((t, str(p)))
        t += dur + cfg["gap"]
    total = t + 0.4

    wav = OUT / f"{name}_zh.wav"
    build_dub_track(clips, total, wav)

    if cfg.get("bgm"):
        # 低音量 BGM：两个正弦音叠成"音乐感"底噪，-26dB 左右
        bgm = OUT / f"{name}_bgm.wav"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=220:duration={total:.2f}",
            "-f", "lavfi", "-i", f"sine=frequency=277:duration={total:.2f}",
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2,volume=0.045,aformat=channel_layouts=mono,"
            "aresample=44100",
            str(bgm),
        ], check=True, capture_output=True)
        mixed = OUT / f"{name}_mix.wav"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(wav), "-i", str(bgm),
            "-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0",
            "-ac", "1", str(mixed),
        ], check=True, capture_output=True)
        wav = mixed

    out = OUT / f"{name}.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        f"testsrc2=s=1280x720:d={total:.2f}:r=25",
        "-i", str(wav),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-shortest",
        str(out),
    ], check=True, capture_output=True)
    return {"name": name, "face": False, "lines": meta_lines}


def cut_face_clip() -> dict:
    name = "c4_face"
    out = OUT / f"{name}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-ss", "114", "-to", "144", "-i", str(TED),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-ar", "48000", str(out),
    ], check=True, capture_output=True)
    return {"name": name, "face": True, "lines": []}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    metas = [synth_clip(k, v) for k, v in SCRIPTS.items()]
    metas.append(cut_face_clip())
    (OUT / "meta.json").write_text(
        json.dumps(metas, ensure_ascii=False, indent=2), encoding="utf-8")
    for m in metas:
        d = probe_duration(OUT / f"{m['name']}.mp4")
        print(f"{m['name']}: {d:.1f}s, {len(m['lines'])} 行台词")
    print(f"评测素材就绪: {OUT}")


if __name__ == "__main__":
    main()
