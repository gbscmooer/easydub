"""subtitles 滤镜参数转义的单测：不需要 ffmpeg / 网络 / API key。"""
from easydub.media import _subtitles_arg


class TestSubtitlesArg:
    def test_plain_path(self, tmp_path):
        srt = tmp_path / "subs.srt"
        assert _subtitles_arg(srt) == f"subtitles='{srt}'"

    def test_special_chars_survive_filtergraph(self, tmp_path):
        # 逗号/空格/括号必须被单引号包住，否则 filtergraph 按逗号切断滤镜
        srt = tmp_path / "WHY DO IT  NIKE - Nike (720p, h264)" / "subs.srt"
        arg = _subtitles_arg(srt)
        assert arg.startswith("subtitles='") and arg.endswith("'")
        assert ", h264" in arg

    def test_windows_colon_escaped(self, tmp_path):
        arg = _subtitles_arg(tmp_path / "a b" / "subs.srt")
        assert "\\:" not in arg  # 相对路径没有冒号
        arg = _subtitles_arg("C:\\work\\subs.srt")
        assert "C\\:/work/subs.srt" in arg

    def test_quote_in_path_falls_back_to_safe_copy(self, tmp_path):
        d = tmp_path / "it's here"
        d.mkdir()
        srt = d / "subs.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        arg = _subtitles_arg(srt)
        assert "it's" not in arg and "easydub_subs_" in arg
