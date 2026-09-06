"""人脸检测分段：找出视频里"有人脸出镜"的时间段，只对这些段做口型同步。

纯 OpenCV Haar 级联（无需 GPU / 无新模型下载），抽样判帧 + 区间聚类。
空镜 / 产品镜头自动落到"无人脸段"，lip-sync 阶段跳过、保留原画面。
"""
from pathlib import Path
from typing import List, Tuple

import cv2

# Haar 级联随 opencv 包自带，无需额外下载
_CASCADE = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


def _face_ratio_at(frame, detector) -> float:
    """返回该帧最大人脸宽占比（0 = 无人脸）。"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = detector.detectMultiScale(gray, scaleFactor=1.15,
                                      minNeighbors=5, minSize=(96, 96))
    if len(faces) == 0:
        return 0.0
    w = max(f[2] for f in faces)
    return w / frame.shape[1]


def cluster_hits(hits: List[Tuple[float, bool]], step: float = 0.5,
                 min_len: float = 1.0, gap: float = 1.5
                 ) -> List[Tuple[float, float]]:
    """把 (秒, 是否人脸) 抽样序列聚成出镜段。

    无人脸间隔 < gap 的相邻命中合并；短于 min_len 的段丢弃。
    """
    segs: List[List[float]] = []
    for t, present in hits:
        if not present:
            continue
        if segs and t - segs[-1][1] <= gap:
            segs[-1][1] = t + step
        else:
            segs.append([t, t + step])
    return [(s, e) for s, e in segs if e - s >= min_len]


def detect_face_segments(video, step: float = 0.5, min_len: float = 1.0,
                         gap: float = 1.0) -> List[Tuple[float, float]]:
    """抽样检测人脸，返回 [(start, end)] 出镜段（秒）。"""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video}")
    detector = cv2.CascadeClassifier(_CASCADE)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_step = max(1, int(fps * step))

    hits: List[Tuple[float, bool]] = []  # (秒, 该帧是否有人脸)
    i = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % frame_step == 0:
            ok, frame = cap.retrieve()
            t = i / fps
            hits.append((t, ok and _face_ratio_at(frame, detector) >= 0.13))
        i += 1
    cap.release()
    return cluster_hits(hits, step=step, min_len=min_len, gap=gap)
