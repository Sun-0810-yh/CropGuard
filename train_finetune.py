"""
打印翻拍专项续训
用法: python train_finetune.py
作用: 针对20张比赛图片，加打印/摄像头退化增强，快速续训15轮
"""
import sys, os, shutil, random
from ultralytics import YOLO

if __name__ == '__main__':
    # 1. 把20张比赛图复制到训练集（多复制3份加大权重）
    SRC = 'F:/Competition_RK/pictures20'
    DST = 'dataset_full/images/train'
    LABEL_SRC = 'dataset_102/labels/val'
    LABEL_DST = 'dataset_full/labels/train'

    copied = 0
    for f in os.listdir(SRC):
        if not f.endswith('.jpg'):
            continue
        parts = f.split('_')
        orig_name = parts[-1]
        src_img = os.path.join(SRC, f)
        for i in range(3):
            new_name = f'PRINT_{i}_{orig_name}'
            shutil.copy2(src_img, os.path.join(DST, new_name))
            label_file = orig_name.replace('.jpg', '.txt')
            label_path = os.path.join(LABEL_SRC, label_file)
            if os.path.exists(label_path):
                shutil.copy2(label_path, os.path.join(LABEL_DST, new_name.replace('.jpg', '.txt')))
            copied += 1

    print(f'已添加 {copied} 份训练样本（20张图 x3份）')

    # 2. 强退化增强 + 续训
    AUGS = dict(
        hsv_h=0.05, hsv_s=1.0, hsv_v=1.0,
        degrees=20.0, translate=0.15, scale=0.7, shear=3.0,
        perspective=0.001, flipud=0.3, fliplr=0.5,
        mosaic=0.5, mixup=0.1, erasing=0.3,
    )

    model = YOLO('runs/train/pest27_final4/weights/last.pt')
    model.train(
        resume=True, data='dataset_full/data.yaml',
        epochs=15, batch=16, imgsz=640, device='cuda:0',
        workers=2, patience=10, cos_lr=True, close_mosaic=5,
        lr0=0.001, project='runs/train', name='pest27_finetune',
        **AUGS
    )

    # 3. 导出ONNX
    model = YOLO('runs/train/pest27_finetune/weights/best.pt')
    model.export(format='onnx', imgsz=640, simplify=True)
    print('Done! ONNX: runs/train/pest27_finetune/weights/best.onnx')
