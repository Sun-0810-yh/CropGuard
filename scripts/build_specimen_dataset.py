# -*- coding: utf-8 -*-
"""把手拍的实体昆虫模型照片做成 YOLO 微调数据集（自动框 + 混入旧数据防遗忘）。

用法:
    python scripts/build_specimen_dataset.py --src <照片根目录> --old <dataset_full 目录>

照片根目录下每个子文件夹 = 一种标本（文件夹名见 FOLDER_TO_CLASS），支持 jpg/png/HEIC。
原始照片只读不改；产出到 datasets/specimen_mix/（已 gitignore）：
  images|labels/{train,val}/、data.yaml、review/（每张自动框的缩略图，训练前人工过目）

自动框：标本放在浅色平面上拍 → 按"与背景色的差异"分割，取靠近画面中心的最大连通域外接框。
划分：每个文件夹按拍摄时间排序，**最后 15% 连拍整段**做验证（随机划分会把同一连拍的近似帧泄到验证集）。
"""
import argparse
import os
import random
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

# 标本 → 模型 27 类里最接近的一类（按科就近；中文名备案不动，这里只决定训练标签）
FOLDER_TO_CLASS = {
    '华麦蝽': 'Miridae',
    '大青叶蝉': 'Cicadella_viridis',
    '枯叶蛾': 'flax_budworm',
    '玉米螟': 'corn_borer',
    '电光叶蝉': 'Cicadellidae',
    '黑尾叶蝉': 'Cicadellidae',
    '盗毒蛾': 'Prodenia_litura',
    # ⚠️ 2026-10-28 那批照片里「蚂蚱」与「蜡螟蛾」两个文件夹装反了：
    #    「蚂蚱」文件夹里是棕色蛾子、「蜡螟蛾」文件夹里是蚂蚱 → 按**照片实际内容**映射
    '蚂蚱': 'peach_borer',        # 实为蜡螟蛾（螟蛾科）
    '蛴螬': 'grub',
    '蜡螟蛾': 'Locustoidea',      # 实为蚂蚱
    '长鼻蜡蝉': 'Lycorma_delicatula',
}

LONG_SIDE = 1280
VAL_FRAC = 0.15
IMG_EXT = {'.jpg', '.jpeg', '.png', '.heic', '.heif'}


def read_image(path):
    if path.suffix.lower() in ('.heic', '.heif'):
        from PIL import Image, ImageOps
        import pillow_heif
        pillow_heif.register_heif_opener()
        im = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
        return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
    # cv2.imdecode 默认按 EXIF 旋转，和 ultralytics 读图口径一致
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def resize_long(img, long_side=LONG_SIDE):
    h, w = img.shape[:2]
    s = long_side / max(h, w)
    return img if s >= 1 else cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)


def auto_box(img):
    """返回 (x1, y1, x2, y2) 或 None。在 1/4 分辨率上分割，按背景色差取前景。"""
    h, w = img.shape[:2]
    small = cv2.resize(img, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
    # 背景色 = 画面外圈中位数（标本基本都在中间）
    m = max(2, min(sh, sw) // 12)
    border = np.concatenate([lab[:m].reshape(-1, 3), lab[-m:].reshape(-1, 3),
                             lab[:, :m].reshape(-1, 3), lab[:, -m:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    # 色度差加权更高：阴影主要是变暗（L 差）、色度几乎不变，压低 L 权重少把影子框进去
    d = np.sqrt(0.35 * (lab[..., 0] - bg[0]) ** 2 + (lab[..., 1] - bg[1]) ** 2 + (lab[..., 2] - bg[2]) ** 2)
    # 局部背景不均匀（光照渐变）→ 用大核模糊的差异图做基线
    base = cv2.blur(d, (sw // 3 | 1, sh // 3 | 1))
    d = np.clip(d - 0.6 * base, 0, None)
    thr = max(12.0, float(np.percentile(d, 90)) * 0.6)
    mask = (d > thr).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    n, lbl, stats, cents = cv2.connectedComponentsWithStats(mask)
    best, best_score = None, 0.0
    cx, cy = sw / 2, sh / 2
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < sh * sw * 0.003:
            continue
        # 贴边的大块多半是亚克力盒边/手指，降权
        touches = (x <= 1) + (y <= 1) + (x + bw >= sw - 1) + (y + bh >= sh - 1)
        dist = np.hypot(cents[i][0] - cx, cents[i][1] - cy) / np.hypot(cx, cy)
        score = area * (1.0 - 0.7 * dist) * (0.25 if touches else 1.0)
        if score > best_score:
            best, best_score = i, score
    if best is None:
        return None
    x, y, bw, bh, _ = stats[best]
    # 把与主体相邻的小块（触角、足）并进来
    x1, y1, x2, y2 = x, y, x + bw, y + bh
    pad_x, pad_y = bw * 0.15, bh * 0.15
    for i in range(1, n):
        if i == best:
            continue
        ix, iy, iw, ih, ia = stats[i]
        if ia < 4:
            continue
        if ix >= x1 - pad_x and iy >= y1 - pad_y and ix + iw <= x2 + pad_x and iy + ih <= y2 + pad_y:
            x1, y1, x2, y2 = min(x1, ix), min(y1, iy), max(x2, ix + iw), max(y2, iy + ih)
    s = 4
    box = (x1 * s, y1 * s, x2 * s, y2 * s)
    bw_, bh_ = box[2] - box[0], box[3] - box[1]
    if bw_ * bh_ > 0.8 * w * h or bw_ < 0.03 * w or bh_ < 0.03 * h:
        return None
    return box


def center_box(img, roi=0.55):
    """只在画面中央 roi 比例区域内分割（标本放在培养皿里时，外圈的皿壁会抢走分割）。"""
    h, w = img.shape[:2]
    x0, y0 = int(w * (1 - roi) / 2), int(h * (1 - roi) / 2)
    b = auto_box(img[y0:h - y0, x0:w - x0])
    return None if b is None else (b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0)


def model_box(model, img, min_conf):
    """用现有模型取置信度最高的一个框（不管类别）；没有就 None。"""
    if model is None:
        return None
    r = model.predict(img, imgsz=640, conf=min_conf, verbose=False)[0]
    if not len(r.boxes):
        return None
    i = int(r.boxes.conf.argmax())
    return tuple(int(v) for v in r.boxes.xyxy[i].tolist()), float(r.boxes.conf[i])


def to_yolo(box, w, h, cls_id):
    x1, y1, x2, y2 = box
    return f'{cls_id} {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} {(x2 - x1) / w:.6f} {(y2 - y1) / h:.6f}\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True, help='标本照片根目录（每种一个子文件夹）')
    ap.add_argument('--old', required=True, help='旧 27 类 dataset_full 目录（含 data.yaml）')
    ap.add_argument('--out', default='datasets/specimen_mix')
    ap.add_argument('--old-per-class', type=int, default=80, help='每类混入多少张旧训练图')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--weights', default='', help='用这个模型的最高分框当标注（培养皿/插针/白底白虫这类分割不准的场景），没有框再退回分割')
    ap.add_argument('--model-conf', type=float, default=0.15)
    args = ap.parse_args()

    src, old, out = Path(args.src), Path(args.old), Path(args.out)
    names = yaml.safe_load(open(old / 'data.yaml', encoding='utf-8'))['names']
    names = [names[i] for i in sorted(names)] if isinstance(names, dict) else list(names)
    cls_id = {n: i for i, n in enumerate(names)}
    for c in FOLDER_TO_CLASS.values():
        assert c in cls_id, c

    if out.exists():
        shutil.rmtree(out)
    for sub in ('images/train', 'images/val', 'labels/train', 'labels/val', 'review'):
        (out / sub).mkdir(parents=True, exist_ok=True)

    model = None
    if args.weights:
        from ultralytics import YOLO
        model = YOLO(args.weights)

    stats = {}
    for folder in sorted(p for p in src.iterdir() if p.is_dir()):
        cname = FOLDER_TO_CLASS.get(folder.name)
        if cname is None:
            print(f'[跳过] 未映射的文件夹: {folder.name}')
            continue
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMG_EXT)
        n_val = max(1, round(len(files) * VAL_FRAC))
        kept = dark = nobox = 0
        for k, f in enumerate(files):
            split = 'val' if k >= len(files) - n_val else 'train'
            img = read_image(f)
            if img is None:
                continue
            img = resize_long(img)
            if img.mean() < 35:          # 闪光没打上的全黑帧
                dark += 1
                continue
            h, w = img.shape[:2]
            mb = model_box(model, img, args.model_conf)
            box = mb[0] if mb else auto_box(img)
            # 框住了大半个画面 = 框到了培养皿/盒子：只在中央区域（皿内）重新分割
            if box is not None and (box[2] - box[0]) * (box[3] - box[1]) > 0.4 * w * h:
                edges = (box[0] < 0.02 * w) + (box[1] < 0.02 * h) + (box[2] > 0.98 * w) + (box[3] > 0.98 * h)
                if edges >= 3:
                    box = center_box(img)
            if box is None:
                nobox += 1
                continue
            h, w = img.shape[:2]
            stem = f'SPEC_{cname}_{folder.name}_{f.stem}'
            cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tofile(str(out / f'images/{split}/{stem}.jpg'))
            (out / f'labels/{split}/{stem}.txt').write_text(to_yolo(box, w, h, cls_id[cname]), encoding='utf-8')
            rv = cv2.resize(img, (w // 5, h // 5))
            cv2.rectangle(rv, (box[0] // 5, box[1] // 5), (box[2] // 5, box[3] // 5), (0, 0, 255), 2)
            cv2.imencode('.jpg', rv)[1].tofile(str(out / f'review/{folder.name}_{f.stem}.jpg'))
            kept += 1
        stats[folder.name] = (cname, len(files), kept, dark, nobox)
        print(f'{folder.name:6s} -> {cname:20s} 共{len(files)} 保留{kept} 黑帧{dark} 无框{nobox}', flush=True)

    # 混入旧数据：每类 N 张训练图 + 全部旧验证图（排除 PRINT_ 前缀的污染副本）
    rng = random.Random(args.seed)
    by_cls = {}
    for lf in (old / 'labels/train').glob('*.txt'):
        if lf.stem.startswith('PRINT_'):
            continue
        lines = lf.read_text(encoding='utf-8').split()
        if not lines:
            continue
        by_cls.setdefault(int(float(lines[0])), []).append(lf)

    def copy_pair(lf, split):
        for ext in ('.jpg', '.jpeg', '.png', '.JPG', '.PNG'):
            im = old / 'images' / lf.parent.name / (lf.stem + ext)
            if im.exists():
                shutil.copy2(im, out / f'images/{split}/{im.name}')
                shutil.copy2(lf, out / f'labels/{split}/{lf.name}')
                return True
        return False

    n_old = 0
    for c, lst in by_cls.items():
        if c >= len(names):
            continue
        rng.shuffle(lst)
        n_old += sum(copy_pair(lf, 'train') for lf in lst[:args.old_per_class])
    n_old_val = sum(copy_pair(lf, 'val') for lf in (old / 'labels/val').glob('*.txt')
                    if not lf.stem.startswith('PRINT_'))
    print(f'旧数据: 训练 {n_old} 张, 验证 {n_old_val} 张')

    data = {'path': str(out.resolve()), 'train': 'images/train', 'val': 'images/val',
            'nc': len(names), 'names': {i: n for i, n in enumerate(names)}}
    yaml.safe_dump(data, open(out / 'data.yaml', 'w', encoding='utf-8'), allow_unicode=True, sort_keys=False)
    print('写出', out / 'data.yaml')


if __name__ == '__main__':
    sys.exit(main())
