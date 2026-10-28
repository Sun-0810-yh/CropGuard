# -*- coding: utf-8 -*-
"""按标本文件夹统计验证集识别率（微调前后对比用）。

只看 datasets/specimen_mix/images/val 里 SPEC_ 开头的标本图（每个文件夹按时间最后 15% 的连拍）。
  有框：最高分框 conf ≥ --conf
  类对：最高分框的类别 = 训练时映射的类别
用法: python scripts/eval_specimen.py --weights a.pt [b.pt ...]
"""
import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def evaluate(weights, val_dir, conf, device):
    from ultralytics import YOLO
    m = YOLO(weights)
    stat = collections.defaultdict(lambda: [0, 0, 0])   # folder -> [总数, 有框, 类对]
    for f in sorted(val_dir.glob('SPEC_*.jpg')):
        # SPEC_<类名>_<文件夹>_<原文件名>；类名本身可能含下划线 → 用 names 匹配
        rest = f.stem[len('SPEC_'):]
        cls = next(n for n in sorted(m.names.values(), key=len, reverse=True) if rest.startswith(n + '_'))
        folder = rest[len(cls) + 1:].split('_IMG')[0]
        r = m.predict(str(f), imgsz=640, conf=0.05, verbose=False, device=device)[0]
        s = stat[folder]
        s[0] += 1
        if len(r.boxes):
            i = int(r.boxes.conf.argmax())
            if float(r.boxes.conf[i]) >= conf:
                s[1] += 1
                s[2] += m.names[int(r.boxes.cls[i])] == cls
    return stat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--weights', nargs='+', required=True)
    ap.add_argument('--val', default=str(ROOT / 'datasets/specimen_mix/images/val'))
    ap.add_argument('--conf', type=float, default=0.25)
    ap.add_argument('--device', default='0')
    args = ap.parse_args()
    results = [(w, evaluate(w, Path(args.val), args.conf, args.device)) for w in args.weights]
    folders = sorted({f for _, st in results for f in st})
    hdr = '文件夹'.ljust(8) + ''.join(f' | {Path(w).parts[-3][:18]:>18s} 有框/类对' for w, _ in results)
    print(hdr)
    tot = [[0, 0, 0] for _ in results]
    for f in folders:
        line = f.ljust(8)
        for k, (_, st) in enumerate(results):
            n, b, c = st[f]
            tot[k] = [tot[k][0] + n, tot[k][1] + b, tot[k][2] + c]
            line += f' | {b:>3d}/{n:<3d} {c:>3d}/{n:<3d}'.rjust(29)
        print(line)
    print('合计'.ljust(8) + ''.join(f' | {b:>3d}/{n:<3d} {c:>3d}/{n:<3d}'.rjust(29) for n, b, c in tot))


if __name__ == '__main__':
    main()
