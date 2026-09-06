"""人脸段聚类与静态脸过滤的纯逻辑单测：不需要 ffmpeg / cv2 数据。"""
from easydub.services.facedetect import cluster_hits, face_qualifies


class TestFaceQualifies:
    def test_photo_face_rejected_by_low_motion(self):
        # TED 实测：幻灯片照片脸 ratio 高达 0.33 但 motion <0.1——静态，不出口型
        assert face_qualifies(0.33, 0.08) is False

    def test_live_speaker_passes(self):
        # 说话人实测 motion 24-58
        assert face_qualifies(0.15, 24.0) is True

    def test_first_sample_without_history_passes(self):
        # 首帧无前帧可比：放行，由聚类 min_len 兜底（照片第二帧必低动量）
        assert face_qualifies(0.33, None) is True

    def test_too_small_face_rejected(self):
        assert face_qualifies(0.05, 30.0) is False


class TestClusterHits:
    def test_merge_across_small_gaps(self):
        # 出镜-低头(1s)-再出镜：同一连续段
        hits = [(0.0, True), (0.5, True), (1.0, False), (1.5, False),
                (2.0, True), (2.5, True)]
        segs = cluster_hits(hits, step=0.5, gap=1.5)
        assert len(segs) == 1
        assert segs[0][0] == 0.0
        assert segs[0][1] == 3.0

    def test_split_across_big_gaps(self):
        hits = [(0.0, True), (0.5, True), (1.0, False), (5.0, False),
                (5.5, True), (6.0, True)]
        segs = cluster_hits(hits, step=0.5, gap=1.0)
        assert len(segs) == 2

    def test_drop_short_segments(self):
        hits = [(0.0, True), (10.0, True), (10.5, True), (11.0, True)]
        segs = cluster_hits(hits, step=0.5, min_len=1.0)
        assert len(segs) == 1  # 开头 0.5s 孤帧被丢弃
        assert segs[0][0] == 10.0

    def test_all_absent(self):
        assert cluster_hits([(0.0, False), (0.5, False)]) == []
