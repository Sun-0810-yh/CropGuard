# -*- coding: utf-8 -*-
"""实体标本微调：从 pest27_final4/best.pt 出发，在 datasets/specimen_mix 上续训。

前置：python scripts/build_specimen_dataset.py --src <照片根目录> --old <dataset_full> --weights <best.pt>
用法：python scripts/train_specimen.py [--epochs 40] [--device 0|cpu]
产物：runs/train/pest27_specimen/weights/best.pt（不覆盖 pest27_final4，可随时回退）
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=str(ROOT / 'datasets/specimen_mix/data.yaml'))
    ap.add_argument('--weights', default=str(ROOT / 'runs/train/pest27_final4/weights/best.pt'))
    ap.add_argument('--epochs', type=int, default=40)
    ap.add_argument('--batch', type=int, default=32)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--device', default='0')
    ap.add_argument('--name', default='pest27_specimen')
    args = ap.parse_args()

    from ultralytics import YOLO
    model = YOLO(args.weights)
    model.train(
        data=args.data, epochs=args.epochs, imgsz=640, batch=args.batch, device=args.device,
        workers=args.workers, project=str(ROOT / 'runs/train'), name=args.name, exist_ok=True,
        optimizer='AdamW', lr0=0.0008, cos_lr=True, warmup_epochs=2, patience=15,
        freeze=10,           # 冻住骨干前 10 层：少量标本图只调检测头，不把 27 类的老本事练丢
        close_mosaic=8,
        # 与 train_640.py 同一套"摄像头场景"增强（亮度/色相/旋转/透视/缩放）
        hsv_h=0.03, hsv_s=0.8, hsv_v=0.6, degrees=25.0, translate=0.15, scale=0.6,
        perspective=0.001, fliplr=0.5, flipud=0.2, mosaic=1.0, mixup=0.1,
        amp=True, plots=True, verbose=True,
    )


if __name__ == '__main__':
    if sys.platform.startswith('win'):
        import multiprocessing
        multiprocessing.freeze_support()
    main()
