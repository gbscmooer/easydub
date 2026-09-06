"""无声视频健壮闭环的 e2e：真实 ffmpeg，不依赖 ASR/LLM/网络。

用户完全可能上传"没有音轨"或"纯静音"的视频——规格要求零段落
正常出片，而不是 ffmpeg 报错炸掉整条流水线。
"""
import subprocess
from pathlib import Path

from easydub.models import load_segments, save_segments
from easydub.pipeline import run


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-1500:]


def _make_no_audio_video(dst):
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2",
          "-an", "-c:v", "libx264", "-preset", "ultrafast", str(dst)])


def _make_silent_audio_video(dst):
    _run(["ffmpeg", "-y",
          "-f", "lavfi", "-i", "color=c=red:s=320x240:d=2",
          "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
          "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
          "-c:a", "aac", str(dst)])


def test_no_audio_stream_video_completes(tmp_path):
    video = tmp_path / "noaudio.mp4"
    _make_no_audio_video(video)
    out = run(video, "en", workdir=str(tmp_path / "work"),
              asr_provider="local", asr_model="tiny")
    assert Path(out).exists() and Path(out).stat().st_size > 0
    segs = load_segments(tmp_path / "work" / "noaudio" / "segments.json")
    assert segs == []


def test_silent_video_zero_segments_passthrough_bgm(tmp_path):
    video = tmp_path / "silent.mp4"
    _make_silent_audio_video(video)
    # 预置空段缓存：本地 whisper 对纯静音也返回空，这里跳过模型加载直测下游
    (tmp_path / "work" / "silent").mkdir(parents=True)
    save_segments([], tmp_path / "work" / "silent" / "segments.json")
    out = run(video, "en", workdir=str(tmp_path / "work"),
              asr_provider="local", asr_model="tiny")
    assert Path(out).exists() and Path(out).stat().st_size > 0
