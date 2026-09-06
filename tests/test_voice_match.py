"""音色自适应单测：f0 检测用 ffmpeg 合成正弦波实测，选声映射是纯逻辑。"""
import subprocess

from easydub.services.voice_match import FEMALE_F0, estimate_f0, pick_voice


def _tone_wav(freq: float, dur: float, dst, sr: int = 16000):
    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={dur}",
         "-ar", str(sr), "-ac", "1", "-sample_fmt", "s16", str(dst)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-2000:]


class TestEstimateF0:
    def test_male_range_tone(self, tmp_path):
        wav = tmp_path / "male.wav"
        _tone_wav(110, 2.0, wav)   # 典型男声基频
        f0 = estimate_f0(wav)
        assert f0 is not None and abs(f0 - 110) < 5

    def test_female_range_tone(self, tmp_path):
        wav = tmp_path / "female.wav"
        _tone_wav(210, 2.0, wav)   # 典型女声基频
        f0 = estimate_f0(wav)
        assert f0 is not None and abs(f0 - 210) < 6

    def test_silence_returns_none(self, tmp_path):
        wav = tmp_path / "sil.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-t", "2",
                        "-i", "anullsrc=r=16000:cl=mono",
                        "-sample_fmt", "s16", str(wav)],
                       capture_output=True, text=True, check=True)
        assert estimate_f0(wav) is None


class TestPickVoice:
    def test_female_f0_picks_female_voice(self):
        assert pick_voice("edge", "en", 210) == "en-US-JennyNeural"

    def test_male_f0_picks_male_voice(self):
        assert pick_voice("edge", "zh", 110) == "zh-CN-YunxiNeural"

    def test_boundary_at_160(self):
        assert pick_voice("edge", "en", FEMALE_F0 - 1).endswith("AndrewNeural")
        assert pick_voice("edge", "en", FEMALE_F0).endswith("JennyNeural")

    def test_unknown_lang_falls_back_to_en_pair(self):
        assert pick_voice("edge", "fr", 210) == "en-US-JennyNeural"

    def test_missing_f0_returns_none(self):
        assert pick_voice("edge", "en", None) is None

    def test_provider_without_pairs_returns_none(self):
        assert pick_voice("minimax", "en", 210) is None
