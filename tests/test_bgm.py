"""背景音乐闪避混音的单测：滤镜图是纯函数；闪避效果用本地 ffmpeg 实测。

闪避判定方法：BGM 用 4kHz 正弦、配音用 300Hz 正弦，混音后用带通滤波器
单独测 4kHz 频段能量——配音触发闪避时该频段应显著下降，频段隔离避免了
配音本身音量对测量的干扰。
"""
import re
import subprocess

from easydub.media import bgm_filtergraph, mix_with_bgm, probe_duration


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-2000:]


def _tone(freq: int, dur: float, vol: float, dst):
    _run(["ffmpeg", "-y", "-f", "lavfi",
          "-i", f"sine=frequency={freq}:duration={dur}",
          "-af", f"volume={vol}", "-ar", "44100", "-ac", "2", str(dst)])


def _silence(dur: float, dst):
    _run(["ffmpeg", "-y", "-f", "lavfi", "-t", str(dur),
          "-i", "anullsrc=r=44100:cl=stereo", str(dst)])


def _band_mean_volume(wav, freq: int, span: int = 500) -> float:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(wav), "-af",
         f"bandpass=f={freq}:width_type=h:w={span},volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True)
    m = re.search(r"mean_volume: ([-\d.]+) dB", proc.stderr)
    assert m, proc.stderr[-2000:]
    return float(m.group(1))


class TestBgmFiltergraph:
    def test_dub_is_split_for_two_consumers(self):
        # 配音既要当闪避侧链又要参与混合：filtergraph 每条流只能被消费一次
        g = bgm_filtergraph()
        assert "asplit=2" in g
        assert g.count("[dmix]") == 2

    def test_bgm_is_main_and_dub_is_sidechain(self):
        # 被压的是原声（main 在前），触发信号是配音（sidechain 在后）
        assert "[bg][dsc]sidechaincompress" in bgm_filtergraph()


class TestMixWithBgm:
    def test_ducking_reduces_bgm_band_during_speech(self, tmp_path):
        bgm = tmp_path / "bgm.wav"
        dub_silent = tmp_path / "dub_silent.wav"
        dub_loud = tmp_path / "dub_loud.wav"
        # ffmpeg sine 源默认振幅仅 ~0.09（-21dB），配音要高增益才能深压
        _tone(4000, 4.0, 0.4, bgm)       # BGM：4kHz 正弦
        _silence(4.0, dub_silent)        # 无配音：闪避不触发
        _tone(300, 4.0, 7.0, dub_loud)   # 配音：300Hz 大声，持续触发闪避

        quiet = mix_with_bgm(dub_silent, bgm, tmp_path / "mix_quiet.wav")
        loud = mix_with_bgm(dub_loud, bgm, tmp_path / "mix_loud.wav")

        assert abs(probe_duration(quiet) - 4.0) < 0.1
        assert abs(probe_duration(loud) - 4.0) < 0.1
        band_quiet = _band_mean_volume(quiet, 4000)
        band_loud = _band_mean_volume(loud, 4000)
        # 说话时 BGM 频段能量应大幅下降（ratio=8、深压缩 → 预期 >15dB）
        assert band_quiet - band_loud > 10, \
            f"闪避未生效：无配音 {band_quiet}dB vs 有配音 {band_loud}dB"

    def test_bgm_survives_in_gaps(self, tmp_path):
        # 句间（配音为静音）BGM 应基本原样保留，而不是被整条丢掉
        bgm = tmp_path / "bgm.wav"
        dub = tmp_path / "dub.wav"
        _tone(4000, 3.0, 0.4, bgm)
        _silence(3.0, dub)
        mixed = mix_with_bgm(dub, bgm, tmp_path / "mix.wav")
        raw = _band_mean_volume(bgm, 4000)
        got = _band_mean_volume(mixed, 4000)
        assert abs(got - raw) < 3, f"句间 BGM 能量异常：原 {raw}dB vs 混后 {got}dB"
