"""
比赛平台海康相机 + ONNX 模型推理（优化版）
放到 anomaly_detection_lite 同目录下运行

优化点（针对“帧率不足”和“运行一会就卡死”）：
1. 独立采集线程：后台持续拉流，只保留最新一帧，避免海康 SDK 内部缓冲堆积导致卡死。
2. 采集与推理解耦：主线程非阻塞取帧，取到的永远是最新帧，画面不产生累积延迟。
3. 自动丢帧：推理慢于采集时自动跳过旧帧，不会越拖越慢。
4. 看门狗自动重连：连续 N 秒拿不到新帧时自动重建相机，从断流中恢复。
5. 周期性 gc 清理：长时间运行防止内存缓慢上涨导致的卡顿。
6. 模型预热：首次推理前先跑一帧空数据，避免第一帧卡顿。
"""
import gc
import os
import sys
import threading
import time

# 平台依赖路径（放 anomaly_detection_lite 目录下时，优先用脚本自身目录，绝对路径兜底）
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, '/home/uw/桌面/anomaly_detection_lite')

import cv2
import numpy as np
from Modules.get_camera_image import CameraImage
from ultralytics import YOLO

# === 配置 ===
MODEL_PATH = 'best.onnx'        # 你的 ONNX 模型
CONF = 0.4                      # 置信度阈值
IMGSZ = 640                     # 输入尺寸（跟训练一致）
DEVICE = 'cpu'                  # ONNX 推理设备，一般走 CPU
SHOW_FPS = True                 # 是否在画面上显示实时帧率
WATCHDOG_TIMEOUT = 5.0          # 连续多少秒拿不到新帧就重连相机
GC_EVERY = 300                  # 每推理多少帧做一次 gc（长时间运行防内存上涨）
DISPLAY_WIDTH = 0               # 显示宽度上限，0=原尺寸；画面卡时可设 1280


class CameraStream:
    """后台线程持续拉流，只保留最新一帧；主线程随时取最新帧，永不阻塞。"""

    def __init__(self, video='hk', watchdog_timeout=WATCHDOG_TIMEOUT):
        self.video = video
        self.watchdog_timeout = watchdog_timeout
        self._lock = threading.Lock()
        self._frame = None
        self._cam_type = None
        self._last_frame_time = time.time()
        self._stopped = threading.Event()
        self._reconnect_requested = False
        # 相机对象只由采集线程创建/释放，避免跨线程读写冲突
        self._cam = CameraImage(video=video)
        self._thread = threading.Thread(target=self._update, name='CameraCapture', daemon=True)

    def start(self):
        self._thread.start()
        return self

    # ---- 采集线程 ----
    def _update(self):
        while not self._stopped.is_set():
            with self._lock:
                if self._reconnect_requested:
                    self._reconnect_requested = False
                    do_reconnect = True
                else:
                    do_reconnect = False
            if do_reconnect:
                self._do_reconnect()

            try:
                cam_type, frame = self._cam.get_cam_image()
            except Exception:
                # 单次读取异常不致命（例如重连瞬间），稍后重试
                time.sleep(0.05)
                continue

            if frame is None:
                time.sleep(0.01)  # 防止空转占满 CPU
                continue

            with self._lock:
                # copy 一份：防止底层复用同一缓冲导致主线程读到半帧/花屏
                self._frame = frame.copy()
                self._cam_type = cam_type
                self._last_frame_time = time.time()

    def _do_reconnect(self):
        """重建相机，恢复推流（仅在采集线程内调用）。"""
        try:
            self._cam.release()
        except Exception:
            pass
        with self._lock:
            self._cam = CameraImage(video=self.video)
            self._frame = None
            self._cam_type = None
            self._last_frame_time = time.time()

    # ---- 主线程接口 ----
    def read(self):
        """返回 (cam_type, frame) 或 (None, None)。非阻塞，永远返回最新帧。"""
        with self._lock:
            if self._frame is None:
                return None, None
            return self._cam_type, self._frame

    def is_stale(self):
        """判断是否已经超时没有新帧（用于主线程触发看门狗）。"""
        with self._lock:
            last = self._last_frame_time
        return (time.time() - last) > self.watchdog_timeout

    def request_reconnect(self):
        with self._lock:
            self._reconnect_requested = True

    def stop(self):
        self._stopped.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
        try:
            self._cam.release()
        except Exception:
            pass


def main():
    # 加载模型
    model = YOLO(MODEL_PATH)

    # 预热：首帧推理最慢（加载 session / 算子），先跑一帧空数据
    try:
        model(np.zeros((IMGSZ, IMGSZ, 3), dtype=np.uint8),
              conf=CONF, imgsz=IMGSZ, device=DEVICE, verbose=False)
        print("模型预热完成")
    except Exception as e:
        print("模型预热跳过:", e)

    stream = CameraStream(video='hk').start()
    print("开始检测，按 q 退出...")

    fps = 0.0
    fps_frames = 0
    total_frames = 0
    last_t = time.time()

    try:
        while True:
            cam_type, frame = stream.read()

            if frame is None:
                # 还没拿到首帧，或正在重连中
                if stream.is_stale():
                    print("[看门狗] 超过 %.0f 秒无新帧，重连相机..." % WATCHDOG_TIMEOUT)
                    stream.request_reconnect()
                time.sleep(0.01)
                if cv2.waitKey(1) == ord('q'):
                    break
                continue

            # 推理（采集线程此刻仍在后台拉新帧，互不阻塞）
            results = model(frame, conf=CONF, imgsz=IMGSZ, device=DEVICE, verbose=False)
            annotated = results[0].plot()

            # 帧率统计
            fps_frames += 1
            total_frames += 1
            now = time.time()
            dt = now - last_t
            if dt >= 1.0:
                fps = fps_frames / dt
                fps_frames = 0
                last_t = now

            if SHOW_FPS:
                cv2.putText(annotated, 'FPS: %.1f' % fps, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)

            # 可选：限制显示尺寸，减轻高分辨率画面的 imshow 压力（推理仍用原图）
            if DISPLAY_WIDTH > 0 and annotated.shape[1] > DISPLAY_WIDTH:
                scale = DISPLAY_WIDTH / annotated.shape[1]
                annotated = cv2.resize(annotated, None, fx=scale, fy=scale,
                                       interpolation=cv2.INTER_AREA)

            cv2.imshow('CropGuard 害虫检测', annotated)

            # 周期性 gc，避免长时间运行内存缓慢上涨
            if total_frames % GC_EVERY == 0:
                gc.collect()

            if cv2.waitKey(1) == ord('q'):
                break
    finally:
        stream.stop()
        cv2.destroyAllWindows()
        print("已退出")


if __name__ == '__main__':
    main()
