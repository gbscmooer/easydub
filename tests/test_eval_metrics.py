"""评测指标纯函数单测。"""
from easydub.eval_metrics import cer, cost_estimate, run_metrics, subtitle_offset


class TestCer:
    def test_perfect(self):
        assert cer("你好世界", "你好世界") == 0.0

    def test_partial(self):
        # 4 字错 1 字
        assert cer("你好世界", "你好世甲") == 0.25

    def test_whitespace_ignored(self):
        assert cer("你 好 世 界", "你好世界") == 0.0

    def test_empty_expected(self):
        assert cer("", "") == 0.0
        assert cer("", "abc") == 1.0


class TestSubtitleOffset:
    def test_matched_pairs(self):
        segs = [{"start": 0.41}, {"start": 3.52}]
        lines = [{"start": 0.40}, {"start": 3.50}]
        off = subtitle_offset(segs, lines)
        assert off == {"mean": 0.015, "max": 0.02}

    def test_none_when_count_mismatch(self):
        assert subtitle_offset([{"start": 1}], [{"start": 1}, {"start": 2}]) is None

    def test_none_when_segment_merged(self):
        # 断句合并后第一段起点对不上，超出容差 → 不计入
        assert subtitle_offset([{"start": 0.4}, {"start": 2.0}],
                               [{"start": 0.4}, {"start": 5.0}]) is None


class TestRunMetrics:
    def test_rates(self):
        report = {"summary": {"total": 4, "fit": 2, "atempo": 1, "overflow": 1,
                              "overflow_before_retry": 2},
                  "segments": [{"start": 0}]}
        m = run_metrics(report, wall_seconds=30.0, video_seconds=10.0)
        assert m["overflow_rate"] == 0.25
        assert m["match_rate"] == 0.75
        assert m["rt_factor"] == 3.0


class TestCost:
    def test_free_channels(self):
        c = cost_estimate(asr_seconds=60, llm_calls=2, tts_chars=100,
                          tts_provider="edge")
        assert c["tts"] == 0.0
        assert c["total"] > 0

    def test_total_is_sum(self):
        c = cost_estimate(10, 1, 10, "edge", lipsync_seconds=5)
        assert abs(c["total"] - (c["asr"] + c["llm"] + c["tts"]
                                 + c["lipsync"])) < 1e-9


class TestNormalize:
    def test_cn_digits(self):
        from easydub.eval_metrics import normalize_zh
        assert normalize_zh("两件八折") == "2件8折"

    def test_punct_stripped(self):
        from easydub.eval_metrics import normalize_zh
        assert normalize_zh("你好，世界！") == "你好世界"

    def test_cer_after_normalize(self):
        assert cer("全场两件八折", "全场2件8折") == 0.0
