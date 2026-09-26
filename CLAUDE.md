# CropGuard 农智云 害虫检测系统

## 项目位置
- 工作目录: D:\Dev Projects\Pycharm\yolov8
- GitHub源码(仅代码): D:\Dev Projects\Pycharm\Pest-detection-master
- GitHub仓库: https://github.com/Stara-AI/Pest-detection
- 本项目仓库(开源): https://github.com/Sun-0810-yh/CropGuard（公开，含 best.pt/best.onnx，数据集未上传）

## Python 环境
- GPU环境: D:\Dev Env\Python\Conda\envs\pest-gpu\python.exe
- 系统Python: D:\Dev Env\Python\python.exe (3.14, CPU PyTorch)
- Conda管家: D:\Dev Env\Miniconda
- PyCharm解释器: 右下角选 pest-gpu

## 竞赛背景 (2026 睿抗 CAIR赛道)
- 省赛评分: 20张实拍图全对(35分) + 180秒内跑完(15分) + 评估报告含Loss/mAP收敛曲线(10分) + GUI(10分) + PPT(15分)
- 平台部署: 只上传 best.onnx，必须 imgsz=640（480 会失败）
- 20张比赛图: F:\Competition_RK\pictures20\（用户自行挑选，全部来自原始实拍池）
- 平台实拍问题: 相纸打印 + 摄像头拍照导致置信度从 0.8 掉到 0.45；斜着放比正着放置信度更高
- 时间线: 省赛一等奖，已晋级 2026 睿抗 CAIR 国赛（总决赛）

## 数据集 (当前)
- 27类精选: dataset_full/（从102类筛出，阈值每类≥150训练图）
- 训练/验证: 11,876 / 2,046
- data.yaml: dataset_full/data.yaml
- 遗留: dataset_102/（102类原始数据集，已停用但保留）

## 训练状态 (当前)
- 最佳模型: runs/train/pest27_final4/weights/best.pt
- mAP@0.5: 76.0%（8类>95%）
- ONNX: runs/train/pest27_final4/weights/best.onnx（640，平台可用）
- 续训: python train_640.py --resume

## 关键文件
- 训练脚本: train_640.py（摄像头场景增强：亮度/色相/旋转±25°/透视/缩放）
- 翻拍续训: train_finetune.py（复制比赛图x3份加PRINT_前缀 + 强退化增强，15轮）
- 配置: config/configs.yaml（WEIGHT 已指向 pest27_final4；AI.active_model=local 本地 Ollama qwen2.5:7b，离线合规）
- GUI入口: main.py（PyCharm 运行或 `python main.py`）
- 推理工具: tool/tools.py
- 本地LLM: llm/local_llm.py（Ollama，不可用时降级 knowledge_base 兜底）
- 信号反馈: feedback/feedback.py（pyttsx3 语音 + Arduino 声光，可选）

## 启动方式
- GUI: 运行 main.py（PyCharm 或 `python main.py`）
- 训练: `python train_640.py`（新建）或 `python train_640.py --resume`（续训）

## 清理记录 (2026-09-21)
- 已删旧模型/训练产物: yolov8n.pt(+partial)、runs/train/pest27_v2(480)、pest102_run1、pest27_soup.pt、weights/yolov8s、YOLOv8/
- 已删 102 类遗留脚本/配置: train_102.py、train.py、val.py、启动训练.bat、config/traindata.yaml、config/traindata_102.yaml
- 保留(重要): dataset_102、dataset_full、competition_all_sorted、pictures20、selected_real_scenes、睿抗国赛项目—农智云(PPT/规则)
- README.md 已重写（面向他人本地运行）

## 注意事项
- Windows 下训练脚本必须用 if __name__=='__main__': 包起来（multiprocessing）
- hsv_s 最大 1.0，不能超过
- 更换模型后记得改 config/configs.yaml 的 WEIGHT，否则 GUI 会加载旧模型
- generate_ppt.py 曾误删，需从原完整包恢复（原包非GitHub，是CSDN/博客下载，含UI.py+weights+icon）
