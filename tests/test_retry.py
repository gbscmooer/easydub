from easydub.align import find_overflow
from easydub.models import Segment
from easydub.services.http import retry_call


def make_seg(start, end, translated, action):
    """造一段假数据，免得测试要跑真的视频。"""
    return Segment(start=start, end=end, text="中文原话",
                   translated=translated, action=action)


def test_finds_overflow_and_budget():
    segs = [
        # 真实发生的那段：2.32 秒的槽位，49 字符的英文
        make_seg(0.48, 2.80, "Y" * 49, "overflow"),
        make_seg(4.24, 6.24, "Every cup freshly ground for you", "fit"),
    ]
    result = find_overflow(segs)
    assert len(result) == 1        # 只有第一段超时
    assert result[0]["index"] == 0
    assert result[0]["budget"] == 32   # int(2.32 * 14)


def test_empty_when_no_overflow():
    segs = [
        make_seg(4.24, 6.24, "hi", "fit"),
        make_seg(0.0, 1.0, "yo", "atempo"),   # 变速不算超时
    ]
    assert find_overflow(segs) == []


def test_retry_call_succeeds_after_transient_failure():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise TimeoutError("WSS 瞬断")
        return "ok"

    assert retry_call(flaky, tries=3, backoff=0) == "ok"
    assert len(calls) == 2


def test_retry_call_exhausts_and_raises():
    def always_bad():
        raise TimeoutError("still down")

    try:
        retry_call(always_bad, tries=2, backoff=0)
        raise AssertionError("应当抛出最后一次异常")
    except TimeoutError:
        pass
