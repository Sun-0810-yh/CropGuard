# -*- coding: utf-8 -*-
"""实时框稳定器 + 一物一框去重 + 抓拍最佳帧（纯逻辑，不碰模型，可离线测试）。

现场问题：实物的置信度常在门槛上下晃，框闪一下就没，点拍照时那一帧又没过线，
180 秒 20 张根本来不及。这里做三件事：

1. **留框**：目标短暂漏检时框再留约 1 秒；类别按近期结果投票，不来回跳。
2. **换物清框**：漏检后框内画面明显变了（拿走/换标本），提前清掉，不带到下一张。
3. **看到框就能拍到框**：缓存最近约 1.5 秒置信度最好的真实推理帧，拍照直接用它。
   缓存帧与当前画面对不上（物体已换）时会被跳过，绝不会把上一只标本记给这一只。

检测结果统一是 format_data 的 ``[类名, 置信度, [x1, y1, x2, y2]]``。
留住的框只用于显示，**不参与喷淋/语音联动**（见 app/inference.py）。
"""
import threading
from collections import deque

import cv2
import numpy as np

HOLD_SEC = 1.0            # 漏检后框最多再留多久
GONE_GRACE_SEC = 0.4      # 漏检超过这么久且框内画面变了 → 判定物体已走，提前清框
MATCH_IOU = 0.3           # 新检测与已有目标按 IoU 关联
SMOOTH_NEW = 0.6          # 框位置平滑：新框权重（10 帧/秒下跟手且不抖）
VOTE_WINDOW = 7           # 类别投票窗口（约 0.7 秒）
BEST_WINDOW_SEC = 1.5     # 抓拍缓存窗口
BEST_MAX_FRAMES = 15      # 缓存帧数上限（1280×720 每帧约 2.7MB）
PATCH_DIFF_LIMIT = 18.0   # 框内灰度平均差（0–255），超过算画面变了
DEDUP_IOU = 0.5           # 去重：重叠比例
DEDUP_CONTAIN = 0.8       # 去重：小框被大框包住的比例
KEEP_RATIO = 0.6          # 保持门槛 = 出框门槛 × 该比例
KEEP_FLOOR = 0.1          # 保持门槛下限


def keep_conf(show_conf):
    """已有目标的保持门槛：比出框门槛低，减少卡线目标的闪断。"""
    return max(KEEP_FLOOR, float(show_conf) * KEEP_RATIO)


def _iou_and_contain(a, b):
    """返回 (IoU, 交集占较小框的比例)。"""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0, 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    small = min(area_a, area_b)
    return (inter / union if union > 0 else 0.0), (inter / small if small > 0 else 0.0)


def dedupe_boxes(results, iou=DEDUP_IOU, contain=DEDUP_CONTAIN):
    """一物一框：同一物体的重叠框只留置信度最高的；分开的多个物体全部保留。"""
    kept = []
    for r in sorted(results or [], key=lambda x: float(x[1]), reverse=True):
        duplicate = False
        for k in kept:
            i, c = _iou_and_contain(r[2], k[2])
            if i >= iou or c >= contain:
                duplicate = True
                break
        if not duplicate:
            kept.append(r)
    return kept


def _patch(frame, box):
    """框内区域的小灰度图（比较"这块画面变没变"用）。"""
    if frame is None:
        return None
    h, w = frame.shape[:2]
    x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
    x2, y2 = min(w, int(box[2])), min(h, int(box[3]))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    crop = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    return cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)


def _changed(a, b):
    if a is None or b is None:
        return False
    return float(np.mean(np.abs(a - b))) > PATCH_DIFF_LIMIT


class _Track:
    __slots__ = ('box', 'conf', 'votes', 'last_seen', 'patch')

    def __init__(self, result, now, patch):
        self.box = [float(v) for v in result[2]]
        self.conf = float(result[1])
        self.votes = deque([(result[0], self.conf)], maxlen=VOTE_WINDOW)
        self.last_seen = now
        self.patch = patch

    def absorb(self, result, now, patch):
        self.box = [SMOOTH_NEW * float(n) + (1.0 - SMOOTH_NEW) * o
                    for n, o in zip(result[2], self.box)]
        self.conf = float(result[1])
        self.votes.append((result[0], self.conf))
        self.last_seen = now
        self.patch = patch

    def name(self):
        tally = {}
        for n, c in self.votes:
            tally[n] = tally.get(n, 0.0) + c
        return max(tally, key=tally.get)

    def as_result(self):
        return [self.name(), self.conf, [round(v, 1) for v in self.box]]


class LiveStabilizer:
    """一路相机一个实例。update() 在推理泵调，take_best() 在抓拍请求里调（有锁）。"""

    def __init__(self, hold_sec=HOLD_SEC):
        self.hold_sec = float(hold_sec)
        self._tracks = []
        self._best = deque(maxlen=BEST_MAX_FRAMES)
        self._lock = threading.Lock()

    def update(self, frame, results, now, show_conf):
        """喂入这一帧的原始检测（已按保持门槛过滤），返回要显示的稳定框。"""
        results = dedupe_boxes(results)
        with self._lock:
            unmatched = self._match(frame, results, now)
            for r in unmatched:
                if float(r[1]) >= float(show_conf):
                    self._tracks.append(_Track(r, now, _patch(frame, r[2])))
            self._expire(frame, now)
            self._drop_overlapping()
            self._remember(frame, results, now)
            return [t.as_result() for t in self._tracks]

    def _match(self, frame, results, now):
        """先按 IoU 配对；还没配上的再按距离配（物体动得快时 IoU 会掉到 0）。"""
        free = list(self._tracks)
        rest = list(results)
        pairs = sorted(
            ((_iou_and_contain(t.box, r[2])[0], ti, ri)
             for ti, t in enumerate(free) for ri, r in enumerate(rest)),
            key=lambda x: x[0], reverse=True)
        used_t, used_r = set(), set()
        for score, ti, ri in pairs:
            if score < MATCH_IOU or ti in used_t or ri in used_r:
                continue
            used_t.add(ti)
            used_r.add(ri)
            free[ti].absorb(rest[ri], now, _patch(frame, rest[ri][2]))
        left_t = [t for i, t in enumerate(free) if i not in used_t]
        left_r = [r for i, r in enumerate(rest) if i not in used_r]
        still = []
        for r in left_r:
            nearest = None
            for t in left_t:
                reach = max(t.box[2] - t.box[0], t.box[3] - t.box[1])
                dist = np.hypot((t.box[0] + t.box[2] - r[2][0] - r[2][2]) / 2.0,
                                (t.box[1] + t.box[3] - r[2][1] - r[2][3]) / 2.0)
                if dist <= reach and (nearest is None or dist < nearest[0]):
                    nearest = (dist, t)
            if nearest:
                nearest[1].absorb(r, now, _patch(frame, r[2]))
                left_t.remove(nearest[1])
            else:
                still.append(r)
        return still

    def _expire(self, frame, now):
        alive = []
        for t in self._tracks:
            age = now - t.last_seen
            if age > self.hold_sec:
                continue
            if age >= GONE_GRACE_SEC and _changed(t.patch, _patch(frame, t.box)):
                continue
            alive.append(t)
        self._tracks = alive

    def _drop_overlapping(self):
        """留住的旧框与新框压在同一物体上时只留一个（宁可少一个，绝不一物两框）。"""
        kept = []
        for t in sorted(self._tracks, key=lambda x: x.conf, reverse=True):
            if all(max(_iou_and_contain(t.box, k.box)[0] / DEDUP_IOU,
                       _iou_and_contain(t.box, k.box)[1] / DEDUP_CONTAIN) < 1.0 for k in kept):
                kept.append(t)
        self._tracks = kept

    def _remember(self, frame, results, now):
        while self._best and now - self._best[0][0] > BEST_WINDOW_SEC:
            self._best.popleft()
        if not self._tracks:
            self._best.clear()      # 屏幕上已经没框了，缓存不能再拿来充数
            return
        if frame is not None and results:
            self._best.append((now, frame, results))

    def take_best(self, now, latest_frame):
        """取缓存里最好的一帧并清空缓存；与当前画面对不上的帧跳过（防把上一只记给这一只）。

        返回 (frame, results)；没有可用帧返回 None，由调用方现场连拍兜底。
        """
        with self._lock:
            pick = None
            for ts, frame, results in self._best:
                if now - ts > BEST_WINDOW_SEC:
                    continue
                top = max(results, key=lambda r: float(r[1]))
                if _changed(_patch(frame, top[2]), _patch(latest_frame, top[2])):
                    continue
                if pick is None or float(top[1]) > pick[0]:
                    pick = (float(top[1]), frame, results)
            self._best.clear()
            return (pick[1], pick[2]) if pick else None
