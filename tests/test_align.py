"""时长对齐与碎段合并的纯逻辑单测：不需要 ffmpeg / 网络 / API key。"""
from easydub.align import (apply_onset_shift, calibrate_cps, plan_alignment,
                           reclassify_spill)
from easydub.models import Segment
from easydub.services.asr import _join_words, merge_segments


def _seg(start, end, translated, audio_duration, action):
    return Segment(start=start, end=end, text="中文", translated=translated,
                   audio_duration=audio_duration, action=action)


class TestApplyOnsetShift:
    def test_shifts_both_bounds_preserving_slot(self):
        segs = [_seg(10.0, 12.0, "a", 1.0, "fit"),
                _seg(20.0, 21.5, "b", 1.0, "fit")]
        n = apply_onset_shift(segs, 0.12)
        assert n == 2
        assert segs[0].start == 9.88 and segs[0].end == 11.88
        assert segs[0].slot == 2.0  # 槽位不变，预算/对齐逻辑不受扰

    def test_borrows_only_available_gap(self):
        # 句间空隙只有 0.08s：只能借到空隙 - min_gap
        segs = [_seg(0.0, 1.0, "a", 1.0, "fit"),
                _seg(1.08, 2.0, "b", 1.0, "fit")]
        apply_onset_shift(segs, 0.12)
        assert segs[0].start == 0.0          # 首段前面没空间（留 min_gap）
        assert segs[1].start == 1.05         # 只能借到空隙 - min_gap
        assert segs[1].end == 1.97

    def test_zero_shift_noop(self):
        segs = [_seg(1.0, 2.0, "a", 1.0, "fit")]
        assert apply_onset_shift(segs, 0.0) == 0
        assert segs[0].start == 1.0

    def test_negative_shift_moves_later_within_next_gap(self):
        # whisper 时间戳偏早 → 后移：不能越过下一段起点（留 min_gap）
        segs = [_seg(1.0, 2.0, "a", 1.0, "fit"),
                _seg(2.10, 3.0, "b", 1.0, "fit")]
        apply_onset_shift(segs, -0.07)
        assert segs[0].start == 1.05 and segs[0].end == 2.05  # 只能后移 0.05
        assert segs[1].start == 2.17 and segs[1].end == 3.07


class TestCalibrateCps:
    def test_downward_revision_from_measured_median(self):
        # 译文 14 字符、实测 1.4s → 10 字符/秒，比表值 14 慢 → 预算收紧
        segs = [_seg(0, 2, "a" * 14, 1.4, "fit"),
                _seg(2, 4, "b" * 14, 1.4, "fit"),
                _seg(4, 6, "c" * 14, 1.4, "atempo")]
        assert calibrate_cps(segs, 14) == 10.0

    def test_faster_than_table_keeps_table_value(self):
        # 实测比表快：预算放宽会引入溢出风险，保守沿用表值
        segs = [_seg(0, 2, "a" * 20, 1.0, "fit")]
        assert calibrate_cps(segs, 14) == 14

    def test_floor_at_60_percent_of_table(self):
        # 病态慢（极短句被垫尾撑大）：下修不超过表值的 60%
        segs = [_seg(0, 2, "hi", 2.0, "fit")]
        assert calibrate_cps(segs, 14) == 8.4

    def test_no_usable_segments_falls_back(self):
        segs = [_seg(0, 2, "a", 1.0, "overflow")]
        assert calibrate_cps(segs, 14) == 14


class TestReclassifySpill:
    def test_short_overflow_into_gap_becomes_spill(self):
        # 槽位 0.24s 的"go"溢出 0.14s，下一句在 3s 后：借空隙放完，非真冲突
        segs = [_seg(50.40, 50.64, "go", 0.38, "overflow"),
                _seg(53.52, 54.56, "but ask", 1.0, "fit")]
        assert reclassify_spill(segs, total=60.0) == 1
        assert segs[0].action == "spill"

    def test_collision_with_next_dub_stays_overflow(self):
        # 音频尾部撞上下一句配音开头：真冲突，保留 overflow
        segs = [_seg(10.0, 11.0, "long text", 2.5, "overflow"),
                _seg(12.0, 13.0, "next", 1.0, "fit")]
        assert reclassify_spill(segs, total=60.0) == 0
        assert segs[0].action == "overflow"

    def test_last_segment_capped_by_video_end(self):
        # 末段溢出超过视频结尾：音频会被截断，是真冲突
        segs = [_seg(58.0, 59.0, "tail", 2.0, "overflow")]
        assert reclassify_spill(segs, total=60.0) == 0

    def test_last_segment_spilling_within_video_ends_ok(self):
        segs = [_seg(58.0, 59.0, "tail", 1.2, "overflow")]
        assert reclassify_spill(segs, total=60.0) == 1

    def test_skips_audioless_followers(self):
        # 下一段还没合成音频时，找更后面的配音起点做碰撞判断
        segs = [_seg(10.0, 11.0, "mid", 1.3, "overflow"),
                _seg(11.2, 12.0, "no audio yet", None, ""),
                _seg(14.0, 15.0, "next dub", 1.0, "fit")]
        assert reclassify_spill(segs, total=60.0) == 1
        assert segs[0].action == "spill"


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
