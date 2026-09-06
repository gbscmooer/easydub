"""说话人音色自适应：按源语音基频（f0）推断性别，自动选配男女声。

原实现所有语言固定男声默认音色——女声说话人配出男声配音，观感突兀。
用轻量 FFT 自相关法估 f0，只依赖 numpy，不引入 librosa/crepe 等重依赖。

方法：40ms 帧长、10ms 步进；只保留 RMS ≥ 0.25×最响帧的帧（语音浊音段），
对每帧做 FFT 自相关，在 [70, 350] Hz 频带内找归一化
峰值 >0.5 的基频候选，取全体候选的中位数。合成素材/真人语音实测
男声 ~110Hz、女声 ~210Hz，分界取 160Hz。
"""
import statistics
import wave
from pathlib import Path

import numpy as np

FEMALE_F0 = 160.0  # Hz：成年女声典型 165~255，男声 85~155

# 各语言 (男声, 女声)。男声与 tts.VOICES 的默认保持一致；
# 其余 provider（minimax/openrouter）音色体系不带性别语义，不参与自动选声
VOICE_PAIRS = {
    "edge": {
        "en": ("en-US-AndrewNeural", "en-US-JennyNeural"),
        "zh": ("zh-CN-YunxiNeural", "zh-CN-XiaoxiaoNeural"),
        "ja": ("ja-JP-KeitaNeural", "ja-JP-NanamiNeural"),
        "es": ("es-ES-AlvaroNeural", "es-ES-ElviraNeural"),
        "ko": ("ko-KR-InJoonNeural", "ko-KR-SunHiNeural"),
    },
}


def _read_wav_mono(path) -> tuple:
    """读 16-bit PCM wav，返回 (采样率, 单声道 float 数组)；不支持则抛。"""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("只支持 16-bit PCM wav")
        sr = w.getframerate()
        ch = w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    x = x.astype(np.float32)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return sr, x


def _frame_f0(fr: np.ndarray, sr: int, lag_min: int, lag_max: int,
              nfft: int = 2048) -> float | None:
    """单帧 FFT 自相关基频：[lag_min, lag_max] 内归一化峰值 >0.5 才算浊音帧。"""
    fr = fr - fr.mean()
    spec = np.fft.rfft(fr, nfft)
    ac = np.fft.irfft(spec * np.conj(spec), nfft)[:lag_max + 1]
    if ac[0] <= 0:
        return None
    ac = ac / ac[0]
    k = int(np.argmax(ac[lag_min:lag_max + 1])) + lag_min
    return sr / k if ac[k] > 0.5 else None


def estimate_f0(wav_path, fmin: float = 70.0, fmax: float = 350.0):
    """wav → 基频中位数（Hz）；无浊音帧（如纯静音/纯噪声）返回 None。"""
    sr, x = _read_wav_mono(wav_path)
    frame, hop = int(0.04 * sr), int(0.01 * sr)
    if len(x) < frame:
        return None
    lag_min, lag_max = int(sr / fmax), int(sr / fmin)

    n = (len(x) - frame) // hop + 1
    idx = np.arange(n)[:, None] * hop + np.arange(frame)[None, :]
    frs = x[idx]                                    # (n, frame)
    rms = np.sqrt((frs ** 2).mean(axis=1))
    # 门限相对最响帧：语音里只剔近静音帧（保留浊音段）；恒幅音也全保留。
    # 用"中位数×1.5"会在恒幅信号（测试音/持续 BGM）上误杀全部帧
    loud = rms >= 0.25 * rms.max() if rms.max() > 0 else None
    if loud is None or not loud.any():
        return None

    f0s = [f for f in (_frame_f0(frs[i], sr, lag_min, lag_max)
                       for i in np.nonzero(loud)[0]) if f]
    return round(statistics.median(f0s), 1) if f0s else None


def pick_voice(provider: str, lang: str, f0: float | None):
    """f0 → 音色 id。f0 缺失/无配对表时返回 None（调用方沿用默认音色）。"""
    pairs = VOICE_PAIRS.get(provider)
    if not pairs or f0 is None:
        return None
    male, female = pairs.get(lang, pairs.get("en"))
    return female if f0 >= FEMALE_F0 else male


def auto_pick_voice(video, segments, out_dir, lang: str = "en",
                    provider: str = "edge", max_probes: int = 3):
    """切最长的几段语音探测说话人基频，返回 (voice_id, f0 或 None)。

    用最长的段（≥1s）而不是整条音轨：广告 BGM 的基频会污染估计，
    纯语音段最稳。多段取中位数，单段探测失败（转场/噪声）不致命。
    """
    from ..media import extract_audio_slice

    picks = sorted(segments, key=lambda s: s.slot, reverse=True)[:max_probes]
    f0s = []
    for i, seg in enumerate(picks):
        if seg.slot < 1.0:
            continue
        probe = Path(out_dir) / f"voice_probe_{i}.wav"
        try:
            probe.parent.mkdir(parents=True, exist_ok=True)
            extract_audio_slice(video, max(0.0, seg.start), seg.end, probe)
            f0 = estimate_f0(probe)
        except Exception:
            continue
        if f0:
            f0s.append(f0)
    f0 = statistics.median(f0s) if f0s else None
    return pick_voice(provider, lang, f0), f0
