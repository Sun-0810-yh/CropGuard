"""
27类 640训练脚本 (摄像头优化版)
用法: python train_640.py         # 新建训练
      python train_640.py --resume # 断点续训

增强策略模拟比赛现场:
  - 亮度大幅变化 → 模拟灯光强弱
  - 饱和度/色相变化 → 模拟不同光源色温
  - 旋转±25° → 模拟纸张/摄像头角度
  - 透视变形 → 模拟斜拍
  - 缩放50% → 模拟远近距离
  - 模糊 → 模拟对焦不准
  - Mosaic → 模拟复杂背景
"""
import sys
from ultralytics import YOLO

AUGS = dict(
    # 光线/色彩 —— 模拟各种灯光和打印偏色
    hsv_h=0.03,    # 色相 ±3% (比默认翻倍)
    hsv_s=1.0,     # 饱和度 ±100%
    hsv_v=0.8,     # 亮度 ±80% (暗光↔强光)
    # 角度/位置 —— 模拟摄像头视角偏差
    degrees=25.0,   # 旋转 ±25°
    translate=0.15, # 平移 ±15%
    scale=0.6,      # 缩放 ±60% (远近距离)
    shear=5.0,      # 剪切 ±5°
    perspective=0.001, # 透视变形(模拟斜拍)
    # 翻转
    flipud=0.5,
    fliplr=0.5,
    # 组合增强
    mosaic=1.0,
    mixup=0.15,
    erasing=0.15,   # 随机遮挡(模拟部分模糊)
)

if __name__ == '__main__':
    if '--resume' in sys.argv:
        model = YOLO('runs/train/pest27_final4/weights/last.pt')
        model.train(resume=True, data='dataset_full/data.yaml', epochs=100,
                    batch=16, imgsz=640, device='cuda:0', workers=2,
                    patience=20, cos_lr=True, close_mosaic=10,
                    project='runs/train', name='pest27_final4', **AUGS)
    else:
        model = YOLO('yolov8s.pt')
        model.train(data='dataset_full/data.yaml', epochs=100,
                    batch=16, imgsz=640, device='cuda:0', workers=2,
                    patience=20, cos_lr=True, close_mosaic=10,
                    project='runs/train', name='pest27_final4', **AUGS)

    print('Done!')
