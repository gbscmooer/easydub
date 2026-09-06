"""人脸检测分段：找出视频里"有人脸出镜"的时间段，只对这些段做口型同步。

纯 OpenCV Haar 级联（无需 GPU / 无新模型下载），抽样判帧 + 区间聚类。
空镜 / 产品镜头自动落到"无人脸段"，lip-sync 阶段跳过、保留原画面。

**静态脸过滤**：幻灯片/海报上的照片人脸也会被检出（TED 实测 ratio 高达
0.33，比真说话人还大），但它们帧间零动量——用相邻采样帧在脸框内的
像素差（motion）区分"活人"与"照片"。实测两者间隔 15 倍以上。
"""
from pathlib import Path
from typing import List, Optional, Tuple

import cv2

# Haar 级联随 opencv 包自带，无需额外下载
_CASCADE = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"

# 脸框内相邻采样帧的平均像素差下限（0-255 灰度）：照片脸实测 <0.1，
# 说话人实测 24-58，取 1.5 两头都有 15 倍余量
MIN_MOTION = 1.5


def face_qualifies(ratio: float, motion: Optional[float],
                   min_ratio: float = 0.13,
                   min_motion: float = MIN_MOTION) -> bool:
    """单帧单脸的出镜判定：够大且"活着"（帧间有动量）。

    motion=None 表示首帧采样（无前帧可比）——先放行，交给聚类
    min_len 兜底：照片脸的第二帧 motion 必然过低，凑不满段长。
    """
    if ratio < min_ratio:
        return False
    return motion is None or motion >= min_motion


def _face_boxes(frame, detector, min_ratio: float
                ) -> List[Tuple[int, int, int, int]]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = detector.detectMultiScale(gray, scaleFactor=1.15,
                                      minNeighbors=5, minSize=(96, 96))
    w_frame = frame.shape[1]
    return [tuple(f) for f in faces if f[2] / w_frame >= min_ratio]


def _box_motion(gray_prev, gray_cur, box) -> float:
    x, y, w, h = [max(v, 0) for v in box]
    diff = cv2.absdiff(gray_cur, gray_prev)[y:y + h, x:x + w]
    return float(diff.mean())


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
    """抽样检测"活人脸"，返回 [(start, end)] 出镜段（秒）。"""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video}")
    detector = cv2.CascadeClassifier(_CASCADE)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_step = max(1, int(fps * step))

    hits: List[Tuple[float, bool]] = []  # (秒, 该帧是否有活人脸)
    gray_prev = None
    i = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % frame_step == 0:
            ok, frame = cap.retrieve()
            t = i / fps
            present = False
            if ok:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                for box in _face_boxes(frame, detector, min_ratio=0.13):
                    motion = None if gray_prev is None \
                        else _box_motion(gray_prev, gray, box)
                    if face_qualifies(box[2] / frame.shape[1], motion):
                        present = True
                        break
                gray_prev = gray
            hits.append((t, present))
        i += 1
    cap.release()
    return cluster_hits(hits, step=step, min_len=min_len, gap=gap)
