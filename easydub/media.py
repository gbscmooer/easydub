"""媒体层：所有视频/音频操作的唯一出口，全部走 ffmpeg/ffprobe 命令。

集中在这里的好处：命令可追溯（报错时打印完整命令）、便于调试、
后续接 Dify 时这层原样复用。
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple


def _run(cmd: List[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{proc.stderr[-2000:]}")
    return proc.stdout


def probe_duration(path) -> float:
    out = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(path),
    ])
    return float(json.loads(out)["format"]["duration"])


def extract_audio(video, dst_wav) -> Path:
    """抽出 16k 单声道 wav，供 ASR 使用。"""
    _run([
        "ffmpeg", "-y", "-i", str(video), "-vn",
        "-ac", "1", "-ar", "16000", str(dst_wav),
    ])
    return Path(dst_wav)


def trim_silence(src, dst, head: float = 0.05, tail: float = 0.10) -> Path:
    """剪掉 TTS 音频两端的静音，只留少量垫尾。

    edge-tts 等云 TTS 固定带 0.2s 头静音 + 最长近 1s 尾静音，
    短句的"配音时长"被撑大一倍以上，直接毁掉逐段对齐。
    """
    src, dst = str(src), str(dst)
    tmp = dst + ".trim.mp3"
    edge = f"silenceremove=start_periods=1:start_threshold=-45dB:start_silence={head}"
    _run([
        "ffmpeg", "-y", "-i", src, "-af",
        f"{edge},areverse,{edge},areverse",
        "-c:a", "libmp3lame", "-q:a", "4", tmp,
    ])
    Path(tmp).replace(dst)
    return Path(dst)


def change_tempo(src, dst, tempo: float) -> Path:
    """变速不变调。atempo 单实例支持 0.5~2.0，本项目限幅 0.85~1.25。"""
    tempo = max(0.5, min(2.0, tempo))
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-filter:a", f"atempo={tempo:.4f}", str(dst),
    ])
    return Path(dst)


def build_dub_track(clips: List[Tuple[float, str]], total: float, dst) -> Path:
    """把若干段 (起始秒, 音频文件) 放到一条 total 秒的静音底轨上。

    用 adelay 定位 + amix 叠加（normalize=0 防止多段混音时被平均降音量）。
    """
    dst = Path(dst)
    if not clips:
        _run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=mono", str(dst),
        ])
        return dst

    cmd: List[str] = ["ffmpeg", "-y"]
    for _, p in clips:
        cmd += ["-i", str(p)]
    cmd += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=mono"]

    filters, parts = [], []
    for i, (start, _) in enumerate(clips):
        ms = int(start * 1000)
        filters.append(
            f"[{i}:a]aresample=44100,aformat=channel_layouts=mono,"
            f"adelay={ms}:all=1[s{i}]"
        )
        parts.append(f"[s{i}]")
    filters.append("".join(parts) + f"amix=inputs={len(clips)}:normalize=0[m]")

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[m]", "-t", f"{total:.3f}", str(dst),
    ]
    _run(cmd)
    return dst


def _fmt_ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def gen_srt(segments, path, bilingual: bool = True) -> Path:
    lines: List[str] = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_ts(seg.start)} --> {_fmt_ts(seg.end)}")
        if seg.translated and seg.translated != seg.text:
            lines.append(seg.translated)
            if bilingual:
                lines.append(seg.text)
        else:
            lines.append(seg.text)
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return Path(path)


def _has_subtitles_filter() -> bool:
    """当前 ffmpeg 构建是否带 libass（subtitles 滤镜）。"""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                         capture_output=True, text=True).stdout
    return " subtitles " in out


_LANG_CODE = {"en": "eng", "ja": "jpn", "ko": "kor", "es": "spa", "zh": "zho"}


def _subtitles_arg(srt) -> str:
    """构造 subtitles= 滤镜参数。

    filtergraph 解析（第一级）按 , ; [ ] 切分、单引号包裹的内容原样保留；
    选项解析（第二级）按 : 切分、支持 \\ 转义。因此：反斜杠统一成 /，
    冒号按第二级转义，整体用单引号包住逗号/空格/括号。
    单引号本身无法放进引号串，这类路径退化为复制到无特殊字符的临时文件。
    """
    p = Path(srt)
    if "'" in str(p):
        tmp = Path(tempfile.gettempdir()) / \
            f"easydub_subs_{abs(hash(str(p))) % 10**10}.srt"
        shutil.copy(p, tmp)
        p = tmp
    inner = str(p).replace("\\", "/").replace(":", r"\:")
    return f"subtitles='{inner}'"


def mux(video, audio, dst, srt: Optional[Path] = None,
        lang: str = "en") -> Path:
    """替换音轨输出成品。

    字幕优先烧录（需 libass，要重编码视频）；构建里没有 libass 时
    自动降级为 mov_text 软字幕轨——播放器里可开关，视频流保持 copy。
    """
    cmd = ["ffmpeg", "-y", "-i", str(video), "-i", str(audio)]
    if srt is not None and _has_subtitles_filter():
        cmd += ["-vf", _subtitles_arg(srt),
                "-c:v", "libx264", "-crf", "18", "-preset", "fast"]
        no_subs = False
    elif srt is not None:
        cmd += ["-i", str(srt)]
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-map", "2:s:0"]
        cmd += ["-c:v", "copy", "-c:s", "mov_text",
                "-metadata:s:s:0", f"language={_LANG_CODE.get(lang, 'und')}"]
        no_subs = False
    else:
        cmd += ["-c:v", "copy"]
        no_subs = True

    if no_subs:
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-c:a", "aac", "-b:a", "192k", str(dst)]
    _run(cmd)
    return Path(dst)
