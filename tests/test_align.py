"""时长对齐与碎段合并的纯逻辑单测：不需要 ffmpeg / 网络 / API key。"""
from easydub.align import plan_alignment
from easydub.services.asr import _join_words, merge_segments


class TestPlanAlignment:
    def test_audio_shorter_fits(self):
        # 外语音频比槽位短：原速、准时开始
        plan = plan_alignment(slot=4.0, tts_duration=3.2)
        assert plan == {"tempo": 1.0, "action": "fit", "overrun": 0.0}

    def test_speed_up_within_limit(self):
        # 超时但在 1.25 限幅内：加速压回
        plan = plan_alignment(slot=3.5, tts_duration=4.0)
        assert plan["action"] == "atempo"
        assert abs(plan["tempo"] - 4.0 / 3.5) < 1e-3
        assert plan["overrun"] == 0.0

    def test_overflow_beyond_limit(self):
        # 变速到上限仍放不下：记录溢出，供提示词重译
        plan = plan_alignment(slot=3.0, tts_duration=5.0)
        assert plan["action"] == "overflow"
        assert plan["tempo"] == 1.25
        assert abs(plan["overrun"] - 1.0) < 1e-6

    def test_invalid_inputs(self):
        assert plan_alignment(slot=0, tts_duration=3.0)["action"] == "empty"
        assert plan_alignment(slot=3.0, tts_duration=0)["action"] == "empty"


class TestMergeSegments:
    def test_merge_close_short_segments(self):
        raw = [
            {"start": 0.0, "end": 1.2, "text": "今天"},
            {"start": 1.3, "end": 2.0, "text": "真开心"},
            {"start": 5.0, "end": 6.0, "text": "第二句"},
        ]
        merged = merge_segments(raw)
        assert len(merged) == 2
        assert merged[0]["text"] == "今天真开心"
        assert merged[0]["end"] == 2.0

    def test_no_merge_when_gap_too_big(self):
        raw = [
            {"start": 0.0, "end": 1.0, "text": "第一句"},
            {"start": 3.0, "end": 4.0, "text": "第二句"},
        ]
        assert len(merge_segments(raw)) == 2

    def test_no_merge_when_too_long(self):
        raw = [
            {"start": 0.0, "end": 1.0, "text": "长" * 60},
            {"start": 1.2, "end": 2.0, "text": "续"},
        ]
        assert len(merge_segments(raw)) == 2

    def test_drop_empty(self):
        raw = [{"start": 0.0, "end": 1.0, "text": " "}]
        assert merge_segments(raw) == []


class TestJoinWords:
    def test_english_words_get_spaces(self):
        assert _join_words(["Why", "do", "it"]) == "Why do it"

    def test_cjk_sticks(self):
        assert _join_words(["何必", "冒险"]) == "何必冒险"

    def test_mixed(self):
        assert _join_words(["做", "IT", "工作"]) == "做IT工作"

    def test_punctuation_attached(self):
        assert _join_words(["line", ",", "lose"]) == "line, lose"
