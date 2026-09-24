# 农智云 · 农作物害虫智能检测识别与大模型防控决策系统

> 基于 **YOLOv8s** 的 27 类农作物害虫检测系统，配套 PyQt5 桌面 GUI、离线大模型防治建议与声光信号反馈。
> 项目曾获 **2026 睿抗机器人开发者大赛（CAIR 赛道）省赛一等奖**，晋级国赛。

---

## 1. 项目简介

系统对农作物实拍图像/视频/摄像头画面进行害虫目标检测，识别 27 类常见农作物害虫，并根据检测结果自动生成防治建议。核心能力：

- **多源检测**：图片、目录批处理、视频逐帧、USB 摄像头实时流
- **可视化**：中文类名映射、检测框渲染、按类别归档结果
- **AI 防治建议**：优先调用**本地 Ollama 大模型**（离线，国赛合规），不可用时降级到内置知识库兜底
- **信号反馈**：识别后语音播报（pyttsx3）+ 可选 Arduino 声光报警
- **可配置**：模型、设备（CPU/GPU）、UI、AI、反馈均通过 `config/configs.yaml` 配置

---

## 2. 快速开始（让对方在自己电脑跑起来）

### 2.1 环境要求

| 项目 | 要求 |
| ---- | ---- |
| 操作系统 | Windows 10/11（推荐），Ubuntu 20.04+ 也可运行 |
| Python | **3.8 ~ 3.11**（推荐 3.10） |
| GPU | 可选。有 NVIDIA 显卡则推理更快；无 GPU 也能用 CPU 运行 |

> 注意：项目依赖 `torch==2.0.1`，不支持 Python 3.12 及以上，请务必用 3.8~3.11。

### 2.2 安装依赖

```bash
# 1) 创建并激活虚拟环境（Windows）
python -m venv .venv
.venv\Scripts\activate

# 2) 安装依赖（CPU 版 PyTorch，开箱即用）
pip install -r requirements.txt
```

> **有 NVIDIA 显卡想用 GPU 推理**：先按上面装完，再用 CUDA 版 torch 覆盖（以 CUDA 11.8 为例）：
> ```bash
> pip install torch==2.0.1 torchvision==0.15.2 --index-url https://download.pytorch.org/whl/cu118
> ```
> 然后确认 `nvidia-smi` 能看到显卡。

### 2.3 修改设备（无显卡必须做）

打开 `config/configs.yaml`，把第 6 行：

```yaml
DEVICE: 'cuda:0'
```

改成：

```yaml
DEVICE: 'cpu'
```

（有 GPU 则保持 `'cuda:0'` 不变。）

### 2.4 启动

```bash
python main.py
```

即可打开桌面 GUI，选择图片/视频/摄像头开始检测。

> 模型权重已内置在 `runs/train/pest27_final4/weights/best.pt`，**无需下载任何模型**。

---

## 3. 模型说明

| 项目 | 内容 |
| ---- | ---- |
| 网络 | YOLOv8s（含可选 SE 注意力变体） |
| 类别数 | 27 类农作物害虫 |
| 输入尺寸 | **imgsz = 640**（部署平台强制要求，480 会失败） |
| 最佳权重 | `runs/train/pest27_final4/weights/best.pt` |
| ONNX | `runs/train/pest27_final4/weights/best.onnx`（平台部署用） |

**27 类名称**（`dataset_full/data.yaml` 中定义，`configs.yaml` 的 `chinese_name` 提供中文映射）：

```
叶蝉科、盲蝽科、芫菁、蚜虫、蝼蛄、蝗虫、玉米螟、金针虫、豆类芫菁、
蛴螬、亚麻蕾虫、斜纹夜蛾、甜菜夜蛾、斑衣蜡蝉、跳甲、花盲蝽、
黑尾切叶虫、葡萄天蛾、桃蛀螟、丽缘红姬甲、甘蓝夜蛾、苜蓿盲蝽、
草地螟、光肩星天牛、绿蝉、黄切叶虫、水稻卷叶螟
```

---

## 4. 目录结构

```
yolov8/
├─ main.py                   # GUI 主程序（启动入口）
├─ UI.py                     # Qt Designer 生成的界面类
├─ api_server.py             # FastAPI 推理服务（无 GUI 场景可选）
├─ config/
│  └─ configs.yaml           # 主配置（模型/设备/UI/AI/反馈）
├─ tool/                     # 绘制、结果格式化、导出等工具函数
├─ llm/                      # 本地大模型客户端（Ollama）+ 知识库兜底
├─ feedback/                 # 语音播报 + Arduino 声光反馈
├─ prompts/                  # 大模型提示词模板
├─ ultralytics/              # YOLOv8 框架源码（含 SE 注意力）
├─ icon/ img/ fonts/         # 界面资源（背景图、主图、字体）
├─ runs/train/pest27_final4/ # 当前最佳模型（best.pt / best.onnx）
├─ dataset_full/             # 27 类数据集（当前训练用）
├─ dataset_102/              # 102 类数据集（遗留，见第 8 节）
├─ competition_all_sorted/   # 竞赛整理数据
├─ pictures20/               # 20 张竞赛实拍图
├─ selected_real_scenes/     # 精选实拍场景
├─ 睿抗国赛项目—农智云/       # 竞赛 PPT 与规则文档
├─ output/                   # 检测结果归档（按类别名）
└─ requirements.txt          # 依赖清单
```

---

## 5. 配置说明（`config/configs.yaml`）

| 配置段 | 关键项 | 说明 |
| ------ | ------ | ---- |
| `MODEL.WEIGHT` | `./runs/train/pest27_final4/weights/best.pt` | 推理权重路径 |
| `MODEL.DEVICE` | `'cuda:0'` / `'cpu'` | 推理设备，无显卡改 `'cpu'` |
| `MODEL.CONF` | `0.4` | 置信度阈值 |
| `MODEL.IMGSIZE` | `640` | 推理分辨率 |
| `AI.active_model` | `'local'` | 本地 Ollama；可改 `deepseek`/`qwen`/`zhipu` 等在线模型 |
| `AI.models.*` | 各模型 api_key | 用在线模型时需自行填 key |
| `CONFIG.camera_num` | `0` | 摄像头编号（无画面就改成 1/2/3 试） |
| `CONFIG.chinese_name` | 类别→中文映射 | 检测结果中文显示 |
| `FEEDBACK.enabled` | `true` | 是否开启语音+声光反馈 |
| `FEEDBACK.serial_port` | `null` | Arduino 串口（如 `COM3`），无 Arduino 保持 `null` 仅语音 |

---

## 6. 离线大模型（AI 防治建议）

国赛要求**离线运行、禁止调用外部在线推理**，因此系统默认走本地 Ollama：

- 服务地址：`http://127.0.0.1:11434`
- 模型：`qwen2.5:7b`（见 `llm/local_llm.py`）

**使用步骤**（可选，不装也能用，会自动降级到知识库）：

1. 安装 [Ollama](https://ollama.com/) 并启动；
2. 拉取模型：`ollama pull qwen2.5:7b`；
3. 启动系统即可自动调用；若 Ollama 未启动，系统会用内置知识库（`llm/knowledge_base.py`）兜底，保证离线可用。

---

## 7. 训练（可选，二次开发用）

当前 27 类模型训练脚本为 `train_640.py`：

```bash
python train_640.py           # 新建训练（以 yolov8s.pt 为预训练）
python train_640.py --resume  # 断点续训（从 pest27_final4 的 last.pt）
```

- 数据：`dataset_full/data.yaml`（27 类，train/val 划分）
- 翻拍续训（模拟比赛打印+摄像头场景的增强）：`train_finetune.py`
- 增强策略已在脚本中内置（亮度/色相/旋转/透视/缩放/模糊/Mosaic 等），模拟比赛现场光照与角度。

---

## 8. 遗留文件说明（已清理）

本次整理已删除以下**旧模型、训练产物与 102 类遗留脚本/配置**（均不影响运行）：

| 已删除 | 说明 |
| ------ | ---- |
| `yolov8n.pt` + `.partial` 片段 | 未使用的 yolov8n 预训练模型 |
| `runs/train/pest27_v2/` | 旧 480 分辨率模型（平台不兼容，勿用） |
| `runs/train/pest102_run1/` | 早期 102 类训练产物 |
| `runs/train/pest27_soup.pt` | 模型融合实验文件 |
| `weights/yolov8s/` | 早期 102 类权重备份 |
| `train_102.py`、`train.py`、`val.py`、`启动训练.bat` | 102 类训练/验证脚本 |
| `config/traindata.yaml`、`config/traindata_102.yaml` | 102 类数据集配置 |

仅保留 **102 类数据集** `dataset_102/`（数据本身不删）。当前项目主线为 27 类（`dataset_full/` + `train_640.py`），运行本项目**不依赖任何 102 类文件**；如需复现 102 类训练，可基于 `dataset_102/` 自行按 IP102 格式重新组织。

---

## 9. 常见问题（FAQ）

| 现象 | 解决 |
| ---- | ---- |
| 报错 `CUDA ... not available` | 无 GPU 或 torch 是 CPU 版；把 `DEVICE` 改 `'cpu'` |
| `ImportError: No module named 'torch'` | 依赖未装全，`pip install -r requirements.txt` |
| 摄像头打开无画面 | 改 `CONFIG.camera_num` 为 0/1/2/3 逐个试；确认无其他程序占用 |
| 模型加载失败 / 找不到权重 | 确认 `runs/train/pest27_final4/weights/best.pt` 存在，且 `WEIGHT` 路径正确 |
| AI 建议为空 | Ollama 未启动时自动走知识库兜底，仍可输出基础建议；在线模型需自行填 `api_key` |
| 中文乱码 | 命令行执行前 `chcp 65001`；GUI 内不受影响 |
| Python 3.12 装不上 | 本项目依赖 torch 2.0.1，请改用 Python 3.8~3.11 |

---

## 10. 技术栈

YOLOv8（Ultralytics）· PyQt5 · pyttsx3（语音）· pyserial（Arduino）· Ollama（本地 LLM）· OpenCV · PyTorch
