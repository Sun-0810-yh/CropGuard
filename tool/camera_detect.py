# -*- coding: utf-8 -*-
"""外接摄像头实时/拍照昆虫识别（独立脚本，不依赖 PyQt，可直接命令行跑）。

用法：
    python tool/camera_detect.py                  # 实时预览识别（用 config 里的 camera_num）
    python tool/camera_detect.py --cam 1          # 指定摄像头索引
    python tool/camera_detect.py --photo          # 拍照模式：按 空格 拍一张再识别
    python tool/camera_detect.py --image a.jpg    # 只识别一张图片（不开摄像头）
    python tool/camera_detect.py --cam 1 --conf 0.3 --imgsz 640

快捷键（预览窗口需处于焦点）：
    q / ESC   退出
    空格      拍照并识别（图片存到 output/camera_shots/）
    s         保存当前带框画面

说明：
    摄像头在独立线程里持续排空缓冲，主线程永远只推理“最新一帧”。
    这样即使推理比采集慢，画面也不会越跑越卡（与 main.py 的 CameraCapture 同思路）。
"""
import argparse
import os
import sys
import threading
import time
from datetime import datetime

import cv2
import numpy as np
import yaml

# 允许 `python tool/camera_detect.py` 直接运行时也能 import 到项目内的模块
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tool.tools import assess_risk, compute_color_for_labels  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except Exception:
    _PIL_OK = False

# 中文字体候选（Windows 自带）
_CN_FONTS = [
    r'C:\Windows\Fonts\msyh.ttc',
    r'C:\Windows\Fonts\msyhbd.ttc',
    r'C:\Windows\Fonts\simhei.ttf',
    r'C:\Windows\Fonts\simsun.ttc',
]
_font_cache = {}


def _cn_font(size):
    """取一个能渲染中文的字体，取不到就返回 None（退化为英文绘制）。"""
    if not _PIL_OK:
        return None
    if size in _font_cache:
        return _font_cache[size]
    font = None
    for p in _CN_FONTS:
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p, size)
                break
            except Exception:
                continue
    _font_cache[size] = font
    return font


def draw_chinese(img, text, xy, color, size=22):
    """用 PIL 在 OpenCV 图上画中文（cv2.putText 不支持中文）。"""
    font = _cn_font(size)
    if font is None:
        cv2.putText(img, text, xy, cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    color[::-1] if len(color) == 3 else color, 2, cv2.LINE_AA)
        return img
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    # 先画一层深色描边，浅色背景上也看得清
    ox, oy = xy
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        d.text((ox + dx, oy + dy), text, font=font, fill=(0, 0, 0))
    d.text((ox, oy), text, font=font, fill=(color[2], color[1], color[0]))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


# ==================== 摄像头采集线程 ====================
class CameraCapture:
    """后台线程持续读摄像头，只保留最新一帧。"""

    def __init__(self, index, width=1280, height=720, fourcc='MJPG'):
        self.index = index
        self.width = width
        self.height = height
        self.fourcc = fourcc
        self._cap = None
        self._frame = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._fail = 0

    def _open(self):
        cap = None
        if os.name == 'nt':
            # Windows 下 MSMF 后端长跑容易卡死，优先 DirectShow（与 main.py 一致）
            cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            cap = cv2.VideoCapture(self.index)
        if cap is not None and cap.isOpened():
            # 顺序要紧：先 FOURCC(MJPG) 再分辨率，否则可能拿到不支持的组合
            if self.fourcc:
                try:
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.fourcc))
                except Exception:
                    pass
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # 只留 1 帧，避免积压
            except Exception:
                pass
        return cap

    def start(self):
        self._cap = self._open()
        if self._cap is None or not self._cap.isOpened():
            return False
        self._thread = threading.Thread(target=self._loop, name='CamCapture', daemon=True)
        self._thread.start()
        return True

    def _loop(self):
        while not self._stop.is_set():
            cap = self._cap
            if cap is None or not cap.isOpened():
                time.sleep(0.1)
                continue
            try:
                ok, frame = cap.read()
            except Exception:
                ok, frame = False, None
            if not ok or frame is None:
                self._fail += 1
                time.sleep(0.01)
                if self._fail >= 60:      # 约 1 秒读不到就重开，从断流中自愈
                    self._fail = 0
                    self._reopen()
                continue
            self._fail = 0
            with self._lock:
                self._frame = frame

    def _reopen(self):
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        self._cap = self._open()

    def read(self):
        with self._lock:
            return (self._frame is not None), (None if self._frame is None else self._frame.copy())

    def stop(self):
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        self._cap = None
        with self._lock:
            self._frame = None


def load_cfg():
    path = os.path.join(ROOT, 'config', 'configs.yaml')
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def main():
    ap = argparse.ArgumentParser(description='外接摄像头实时/拍照昆虫识别')
    ap.add_argument('--cam', type=int, default=None, help='摄像头索引，默认取 config 的 CONFIG.camera_num')
    ap.add_argument('--image', type=str, default=None, help='只识别一张图片，不开摄像头')
    ap.add_argument('--photo', action='store_true', help='拍照模式：按空格拍照识别（默认也是空格拍照）')
    ap.add_argument('--conf', type=float, default=None, help='置信度阈值，默认取 config 的 MODEL.CONF')
    ap.add_argument('--imgsz', type=int, default=None, help='推理尺寸，默认取 config 的 MODEL.IMGSIZE')
    ap.add_argument('--width', type=int, default=1280, help='采集宽度')
    ap.add_argument('--height', type=int, default=720, help='采集高度')
    ap.add_argument('--save-dir', type=str, default=os.path.join('output', 'camera_shots'))
    ap.add_argument('--no-show', action='store_true', help='不开预览窗口（无显示器/远程时用）')
    args = ap.parse_args()

    from ultralytics import YOLO

    cfg = load_cfg()
    model_cfg = cfg.get('MODEL', {}) or {}
    camera_num = args.cam if args.cam is not None else int((cfg.get('CONFIG', {}) or {}).get('camera_num', 0))
    weight = model_cfg.get('WEIGHT', './runs/train/pest27_final4/weights/best.pt')
    conf = args.conf if args.conf is not None else float(model_cfg.get('CONF', 0.4))
    imgsz = args.imgsz if args.imgsz is not None else int(model_cfg.get('IMGSIZE', 640))
    device = model_cfg.get('DEVICE', 'cuda:0')
    cn_map = (cfg.get('CONFIG', {}) or {}).get('chinese_name', {}) or {}

    weight_path = weight if os.path.isabs(weight) else os.path.join(ROOT, weight.lstrip('./'))
    os.makedirs(args.save_dir, exist_ok=True)

    print(f'[INFO] 模型   : {weight_path}')
    print(f'[INFO] 设备   : {device}   conf={conf}  imgsz={imgsz}')
    print(f'[INFO] 保存目录: {os.path.abspath(args.save_dir)}')

    model = YOLO(weight_path)

    def infer(frame):
        """跑一次推理，返回 (画好框的图, 结果列表, 风险等级, 风险颜色, 风险说明)。"""
        r = model.predict(frame, conf=conf, imgsz=imgsz, device=device, verbose=False)[0]
        names = r.names
        boxes = r.boxes.xyxy.cpu().numpy().tolist()
        confs = r.boxes.conf.cpu().numpy().tolist()
        clss = r.boxes.cls.cpu().numpy().tolist()

        results = []
        vis = frame.copy()
        for i, (box, c, k) in enumerate(zip(boxes, confs, clss)):
            en = names[int(k)]
            cn = cn_map.get(en, en)
            results.append([en, round(c, 2), box])
            color = compute_color_for_labels(i)
            x1, y1, x2, y2 = [int(v) for v in box]
            cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)
            label = f'{cn} {c:.2f}'
            ty = max(y1 - 26, 2)
            cv2.rectangle(vis, (x1, ty), (x1 + 12 * len(label) + 8, ty + 24), color, cv2.FILLED)
            vis = draw_chinese(vis, label, (x1 + 4, ty + 1), (255, 255, 255), size=20)

        level, rcolor, detail = assess_risk(results, vis.shape[:2])
        return vis, results, level, rcolor, detail

    def report(results, level, detail):
        if results:
            txt = ', '.join(f'{cn_map.get(r[0], r[0])}({r[1]:.2f})' for r in results)
        else:
            txt = '无目标'
        print(f'  -> [{level}] {len(results)} 个目标: {txt}')
        print(f'     {detail}')

    def save(img, tag='shot'):
        name = datetime.now().strftime(f'%Y%m%d_%H%M%S_{tag}.jpg')
        p = os.path.join(args.save_dir, name)
        cv2.imwrite(p, img)
        print(f'  [保存] {os.path.abspath(p)}')
        return p

    # ---------- 单张图片模式 ----------
    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            print(f'[ERROR] 读不到图片: {args.image}')
            return 1
        vis, results, level, rcolor, detail = infer(img)
        report(results, level, detail)
        save(vis, 'image')
        if not args.no_show:
            cv2.imshow('CropGuard - insect detect', vis)
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        return 0

    # ---------- 摄像头模式 ----------
    cam = CameraCapture(camera_num, args.width, args.height)
    if not cam.start():
        print(f'[ERROR] 打不开摄像头 {camera_num}。')
        print('        1) 先跑 `python tool/camera_probe.py` 确认可用索引；')
        print('        2) 关闭占用摄像头的程序（微信/QQ/浏览器/相机 App）；')
        print('        3) 确认 Windows 设置 → 隐私和安全性 → 相机 已允许桌面应用访问。')
        return 1
    print(f'[INFO] 摄像头 {camera_num} 已打开，按 q/ESC 退出，空格拍照，s 保存。')

    fps, last_t, last_res = 0.0, time.time(), (None, None, None)
    shot_count = 0
    try:
        while True:
            ok, frame = cam.read()
            if not ok or frame is None:
                if not args.no_show and cv2.waitKey(10) & 0xFF in (ord('q'), 27):
                    break
                time.sleep(0.02)
                continue

            # 隔帧推理，保证预览流畅（推理比显示慢）
            vis, results, level, rcolor, detail = infer(frame)
            last_res = (results, level, detail)

            now = time.time()
            fps = 0.9 * fps + 0.1 / max(now - last_t, 1e-6)
            last_t = now

            vis = draw_chinese(vis, f'FPS {fps:4.1f}  摄像头{camera_num}', (10, 10), (0, 255, 255), 22)
            vis = draw_chinese(vis, f'风险: {level}', (10, 44), rcolor, 30)
            vis = draw_chinese(vis, detail, (10, 84), (255, 255, 255), 20)

            if not args.no_show:
                cv2.imshow('CropGuard - insect detect', vis)

            # 每检测到新结果就打印一次（限流，避免刷屏）
            if results and shot_count != len(results):
                report(results, level, detail)
                shot_count = len(results)

            if args.no_show:
                time.sleep(0.01)
                continue

            key = cv2.waitKey(1) & 0xFF
            if key in (ord('q'), 27):
                break
            if key == ord('s'):
                save(vis, 'live')
            if key == 32:  # 空格：拍照识别并保存
                print('[拍照] 识别结果:')
                r, lv, dt = last_res
                if r is not None:
                    report(r, lv, dt)
                save(vis, 'photo')
    finally:
        cam.stop()
        cv2.destroyAllWindows()
    return 0


if __name__ == '__main__':
    sys.exit(main())
