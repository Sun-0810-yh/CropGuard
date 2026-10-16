# 农智云 · 农作物害虫智能检测识别与大模型防控决策系统

> 基于 **YOLOv8s** 的 27 类农作物害虫检测系统，配套 PyQt5 桌面 GUI、手机端网页、离线大模型（RAG + 防幻觉）问答与喷洒联动。
> 项目曾获 **2026 睿抗机器人开发者大赛（CAIR 赛道）省赛一等奖**，晋级国赛。

> 🆕 **第一次接手这个项目？先看 [新手使用指南.md](新手使用指南.md)** —— 从装环境到手机连上去，一步步照着敲。
> 🖱️ **不想用命令行？看 [PyCharm运行指南.md](PyCharm运行指南.md)** —— 项目已内置 6 个一键运行配置，点鼠标就行。


---

## 1. 项目简介

系统对农作物实拍图像/视频/摄像头画面（含**手机摄像头**）进行害虫目标检测，识别 27 类常见农作物害虫，
并根据检测结果自动生成防治建议、语音播报、按片区联动喷洒。核心能力：

- **多源检测**：图片、目录批处理、视频逐帧、USB 工业相机实时流、手机摄像头实时上传
- **相机共享 + 自愈**：GUI/网页/手机共用同一路采集（`tool/camera_source.py`），断流自动重开，多个客户端同时看不再互相抢设备
- **AI 问答（RAG + 防幻觉）**：本地 Ollama `qwen2.5:7b` + 内置知识库检索，**知识库没覆盖就明确拒答**，回答带"依据"条目
- **语音反馈**：离线 TTS（多后端自动兜底），**每次检测都会播报**（不是只响第一次），文案与真实动作一致
- **喷洒联动**：风险 → 片区喷洒 + 语音；自然语言指令需**人工确认**后才执行（大模型不直接驱动硬件）
- **手机端一页搞定**：拍照识别（HTTP 可用）、实时识别（HTTPS）、提问大模型、远程喷洒、历史回看
- **可配置**：模型、设备、UI、AI、相机、语音、RAG、手机端均通过 `config/configs.yaml` 配置

---

## 2. 快速开始

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
# 只跑服务器（不用 GUI）时用更精简的清单：
pip install -r requirements-server.txt
```

> **有 NVIDIA 显卡想用 GPU 推理**：先按上面装完，再用 CUDA 版 torch 覆盖（以 CUDA 11.8 为例）：
> ```bash
> pip install torch==2.0.1 torchvision==0.15.2 --index-url https://download.pytorch.org/whl/cu118
> ```
> 然后确认 `nvidia-smi` 能看到显卡。

### 2.3 修改设备（无显卡必须做）

打开 `config/configs.yaml`，把 `MODEL.DEVICE` 从 `'cuda:0'` 改成 `'cpu'`。

### 2.4 启动方式

```bash
python main.py                      # 桌面 GUI（检测 + 语音 + 大模型问答）
python api_server.py                # Web 服务（PC 首页 + 手机端 /m + REST/WS）
python api_server.py --https        # 额外开 HTTPS 8443（手机"实时模式"必须用它）
```

启动后终端会直接打印**手机入口地址**，例如：

```
手机会话页: http://10.30.129.219:8080/m
手机监测页: http://10.30.129.219:8080/stream
```

PC 浏览器打开 `http://127.0.0.1:8080/` 首页，页面下方会显示手机入口的**二维码（手写实现，无额外依赖）**，
手机扫码即可进入 `/m`，不用手输 IP。

> 模型权重已内置在 `runs/train/pest27_final4/weights/best.pt`，**无需下载任何模型**。
> ⚠️ GUI 与服务器**不要同时打开同一个 USB 相机**（Windows 下设备独占）；现场演示只开一个，推荐开服务器。

---

## 3. 现场手册（比赛/演示当天照这个做）

### 3.1 起服务

```bash
# 1) 拉起本地大模型（缺了不影响检测，但问答会降级为知识库原文）
ollama serve

# 2) 起服务（要用手机实时模式就加 --https）
python api_server.py --https
```

看日志里的四行关键信息，缺谁补谁：

```
[INFO] 相机 ... backend=dshow size=1280x720      # 相机通了
[TTS] 后端就绪 engine=...                        # 语音通了
[INFO] Spray transport=http available=True       # 喷洒链路通了
手机会话页: http://<本机IP>:8080/m                # 手机地址
```

### 3.2 三张处置卡

| 现场状况 | 一句话处理 |
| ---- | ---- |
| **相机打不开 / 画面卡住** | `python tool/camera_probe.py` 重认索引 → 改 `CONFIG.camera_num`；运行中卡死看 `GET /api/camera/health`，必要时 `POST /api/camera/restart` 强制重开。注意别让 GUI 和服务器同时占相机。 |
| **手机实时模式起不来** | 必须是 `https://<IP>:8443/m`（`getUserMedia` 要求安全上下文）；首次点"继续访问"或手动信任自签证书；实在不行**退回拍照模式**（HTTP 也能用），演示不会空场。 |
| **语音不响** | 看启动日志 `[TTS] 后端就绪 engine=?`：本机实测 `SAPI.SPVoice` 不可用、`System.Speech`（powershell 后端）可用，auto 会自动选后者。若显示 `无可用语音后端`，用 `GET /api/feedback/status` 看 `init_errors`。 |

### 3.3 演示动线（建议 3 分钟）

1. PC 首页上传一张样本图 → 显示检测框 + 风险 + 知识库建议；
2. 手机扫码进 `/m` → 拍照识别 → 结果回到手机；
3. 手机点"实时模式"（HTTPS）→ 手机画面实时上传识别；
4. 手机提问"蚜虫怎么防治"→ 回答带"依据"；再问"今天天气怎么样"→ **明确拒答**（这就是防幻觉的证据）；
5. 手机输入"给 A 片区喷 3 秒"→ 解析 → 点确认 → 继电器动作 + 语音播报"已给片区A喷淋3秒"。

### 3.4 先跑一遍自检（强烈建议）

```bash
python tests/run_tests.py     # 60 个用例：检索/防幻觉/指令安全/语音队列/相机采集
python scripts/ask_eval.py    # RAG 评测：库内召回率、库外拒答率、越权拦截率
```

---

## 4. 手机端 / 服务器 / GUI 的接口一览

| 接口 | 说明 |
| ---- | ---- |
| `GET /` | PC 演示页（上传检测 + 建议 + 手机入口二维码） |
| `GET /eval` | **现场测评页**：20 样本 / 180 秒，动效计时 + 每张用时 + 评委判分 + 报告导出（与 GUI 同口径） |
| `GET /edge` | **EdgeJev 推理入口**：本地类型化决策（指令解析对照 / 虫情研判 / 通用 systemone） |
| `GET /m` | **手机端主页**：拍照识别 / 实时识别 / 提问 / 喷洒 / 历史 |
| `GET /stream` | 手机监测页（旧版，保留兼容） |
| `POST /api/detect` | 上传图片检测（multipart）→ 带框结果图 + 风险 + 知识库建议 |
| `WS /ws/stream` | 服务器相机实时流（共享采集源，多客户端可同时看） |
| `WS /ws/phone` | **手机上传二进制 JPEG 帧 → 回检测 JSON**（不回图，省流量） |
| `POST /api/ask` | 自由问答（RAG + 防幻觉）：返回 `answer` / `citations` / `guard` |
| `GET /api/kb/search?q=` | 只做检索：现场展示召回片段与分数 |
| `POST /api/ask_command` | 自然语言 → 喷洒意图（**默认只解析，需人工确认**；`engine: auto\|edge\|rule`） |
| `POST /api/spray` | 手动喷洒（`{"group":"g1","duration":3,"via":"phone"}`） |
| `GET /api/edge/status` | EdgeJev 引擎状态（形态/模型/精度/延迟）；`?refresh=true` 重探 |
| `POST /api/edge/systemone` | 通用类型化决策（官方 `/v1/systemone` 同构协议） |
| `POST /api/edge/parse_command` | EdgeJev 指令解析（返回 edge 直译 / 规则解析 / 最终口径三份对照） |
| `POST /api/edge/advise` | 检测结果 → 处置研判（实验性，仅供参考） |
| `GET /api/eval/config` | 测评页口径 + 样本目录状态（页面据此切换"服务器载入/上传模式"） |
| `GET /api/eval/samples` / `GET /api/eval/sample_image?name=` | 服务器侧样本清单 / 读取单张 |
| `POST /api/eval/score` | 按现场口径算分（与 GUI 同一份纯函数） |
| `POST /api/eval/report` | 导出评分报告（csv，格式与 GUI 导出对齐，写入 `EVAL.report_dir`） |
| `GET /api/camera/health` | 相机状态 / 帧龄 / 自愈次数 / 实测 fps |
| `POST /api/camera/restart` | 强制重开相机（现场抢救） |
| `GET /api/feedback/status` | 语音与串口状态（排查"到底响不响"） |
| `GET /api/history` | 联动/喷洒历史（含来源通道） |
| `GET /qr?url=` | 生成二维码 PNG（入口地址） |

---

## 5. 模型说明

| 项目 | 内容 |
| ---- | ---- |
| 网络 | YOLOv8s（含可选 SE 注意力变体） |
| 类别数 | 27 类农作物害虫 |
| 输入尺寸 | **imgsz = 640**（部署平台强制要求，480 会失败） |
| 最佳权重 | `runs/train/pest27_final4/weights/best.pt` |
| ONNX | `睿抗国赛项目—农智云/best.onnx`（平台部署用；2026-10-08 清理时删掉了 `runs/` 下的重复副本，两份 SHA256 相同。若要随开源仓库发布，需对该文件 `git add -f`） |

**27 类名称**（`dataset_full/data.yaml` 中定义，`configs.yaml` 的 `chinese_name` 提供中文映射）：

```
叶蝉科、盲椿科、芫菁、蚜虫、蝼蛄、蝗虫、玉米螟、金针虫、豆类芫菁、
蛴螬、亚麻蕾虫、斜纹夜蛾、甜菜夜蛾、斑衣蜡蝉、跳甲、花盲蝽、
黑尾切叶虫、葡萄天蛾、桃蛀螟、丽缘红姬甲、甘蓝夜蛾、苜蓿盲蝽、
草地螟、光肩星天牛、绿蝉、黄切叶虫、水稻卷叶螟
```

---

## 6. 目录结构

```
yolov8/
├─ main.py                   # GUI 主程序（检测 + 语音 + 大模型问答）
├─ UI.py                     # Qt 界面类（含问答输入区）
├─ api_server.py             # FastAPI 服务：PC 页 / 手机页 / REST / WebSocket / HTTPS
├─ config/
│  ├─ configs.yaml           # 主配置（模型/设备/UI/AI/相机/语音/RAG/手机端）
│  └─ certs/                 # HTTPS 自签证书（--https 首次运行自动生成）
├─ serve/
│  └─ mobile.py              # 手机端页面（/m）HTML
├─ tool/
│  ├─ camera_source.py       # ★ 共享相机采集 + 看门狗自愈（GUI/服务器/手机共用）
│  ├─ camera_probe.py        # 摄像头索引探针（现场排查用）
│  ├─ camera_detect.py       # 独立命令行识别工具
│  ├─ gen_cert.py            # 自签 HTTPS 证书生成（调 Miniconda 自带 openssl）
│  ├─ qr_min.py              # 手写二维码生成（纯 PIL，零依赖）
│  └─ tools.py               # 绘制/结果格式化/风险分级
├─ llm/
│  ├─ ai_gateway.py          # ★ AI 唯一出入口：检索→接地提示词→Ollama→后校验
│  ├─ retriever.py           # ★ TF-IDF 字符 n-gram 检索（RAG 召回）
│  ├─ kb_chunks.py           # 知识分块（由 knowledge_base.py 程序化生成）
│  ├─ guard.py               # ★ 防幻觉守卫（编造药名/剂量检测 + 安全提示）
│  ├─ knowledge_base.py      # 27 类害虫知识库
│  └─ local_llm.py           # Ollama 客户端
├─ feedback/
│  ├─ tts.py                 # ★ 离线语音引擎（专用线程 + 多后端真校验兜底）
│  ├─ feedback.py            # 播报语义（事件驱动 + 最新优先）
│  └─ spray.py               # 喷洒控制器（serial/http/mqtt + stop）
├─ prompts/templates/
│  └─ grounded_expert.txt    # 接地提示词（取代原 1500 字"报告型"提示词）
├─ scripts/ask_eval.py       # RAG + 防幻觉评测（答辩举证）
├─ tests/                    # 测试（内置迷你框架，零依赖；兼容 pytest）
├─ ESP32/                    # 喷洒节点 / 声光报警固件（PlatformIO，见 ESP32/README.md）
├─ dataset_full/             # 27 类数据集（训练用）
└─ 睿抗国赛项目—农智云/       # 竞赛 PPT 与规则文档
```

---

## 7. 配置说明（`config/configs.yaml`）

| 配置段 | 关键项 | 说明 |
| ------ | ------ | ---- |
| `MODEL.WEIGHT` | `./runs/train/pest27_final4/weights/best.pt` | 推理权重路径 |
| `MODEL.DEVICE` | `'cuda:0'` / `'cpu'` | 推理设备，无显卡改 `'cpu'` |
| `MODEL.CONF` / `IMGSIZE` | `0.4` / `640` | 置信度阈值 / 推理分辨率 |
| `CONFIG.camera_num` | `1` | 摄像头索引（用 `tool/camera_probe.py` 确认） |
| `CAMERA.backend` | `'auto'` | 优先 DirectShow（MSMF 长跑易卡死） |
| `CAMERA.stale_sec` | `2.0` | 多久没新帧就判"卡死"并换 handle |
| `CAMERA.infer_fps` / `stream_fps` | `5` / `15` | 推理节奏 / 推流节奏（解耦） |
| `FEEDBACK.tts.engine` | `'auto'` | win32com → pyttsx3 → powershell 自动挑能出声的 |
| `FEEDBACK.speak_interval` | `3` | 同状态复读间隔（状态一变立刻播） |
| `SPRAY.transport` | `'http'` | 节点直连（方案甲）；`http_targets` 填节点 IP |
| `SPRAY.max_duration` | `5` | 单次喷洒上限（雾化片怕连烧，固件再钳一次，默认 5 秒） |
| `CHAT.refuse_below` | `0.18` | 检索分数低于此值**拒绝生成**（防幻觉硬闸门） |
| `RAG.retriever` / `top_k` | `'tfidf'` / `4` | 召回实现与条数（预留 `embedding` 升级位） |
| `PHONE.https_port` | `8443` | `--https` 时 HTTPS 端口，与 8080 同时监听 |

---

## 8. 离线大模型与防幻觉（重点）

国赛要求**离线运行、禁止调用外部在线推理**，因此只走本地 Ollama：

- 服务地址：`http://127.0.0.1:11434`，模型 `qwen2.5:7b`（见 `llm/local_llm.py`）
- 不装 Ollama 也能跑：自动降级为**知识库原文**，来源标注清楚，不会静默乱答

**防幻觉做了四道闸**（实现见 `llm/ai_gateway.py` / `llm/guard.py`）：

1. **召回闸门**：检索最高分 < `CHAT.refuse_below` 时**连大模型都不调**，直接返回统一拒答话术；
2. **接地提示词**：`prompts/templates/grounded_expert.txt` 只允许用给定片段作答，禁止编药名剂量
   （旧的"生成 1500 字报告"提示词是幻觉根源，已废弃）；
3. **参数约束**：`temperature=0.2` + 固定 `seed`（同问同答，演示可复现）、`num_predict=400`；
4. **后校验**：`llm/guard.py` 扫描回答里的农药名与剂量，**片段里没有的一律标注"请核实"**，
   输出始终带 `citations`（依据）与 `source`（来源）。问答留痕写在 `output/qa_history.jsonl`。

一条真实输出示例（`POST /api/ask {"question":"蚜虫怎么防治"}`）：

```
- 蚜虫主要危害棉花、小麦、蔬菜、果树等多种作物。
- 发现初期虫量上升即进入防治窗口，优先采用农业与物理措施，如黄板诱杀有翅蚜、银灰膜避蚜。
- 化学防治需按当地植保部门推荐药剂与安全间隔期执行，药剂选用吡虫啉、啶虫脒等。
依据：蚜虫的防治措施,蚜虫的监测与预防
（安全提示：…喷洒动作需人工确认后执行。）
```

而问"今天天气怎么样"时返回：**"知识库未涵盖该问题，我不做推测性回答。"**

> 知识库内容目前是"通用植保框架"，比赛前应逐条对照权威资料校对（`llm/knowledge_base.py`）。

---

## 9. 喷洒联动与安全边界

- **自动联动**：中/高风险 → 按 `SPRAY.risk_map` 的片区/时长下发（多路相机按片区定向），同时语音播报；
- **手动/手机**：`POST /api/spray`，手机端有二次确认弹窗；
- **自然语言指令**：`POST /api/ask_command` 只做**意图解析 + 白名单校验**（分组、时长钳制、危险词拦截），
  必须人工点"执行"才真正下发 —— **大模型的自由文本永远不直接驱动硬件**；
- **双重上限**：后端 `SPRAY.max_duration`（默认 5 秒）+ 固件 `-DMAX_SPRAY_SEC=5`，雾化片不会连续通电。

安全测试：`tests/test_intent.py` 覆盖"必须指定片区/时长钳制/危险词拒绝"，`scripts/ask_eval.py` 输出拦截率。

---

## 10. 训练（可选，二次开发用）

```bash
python train_640.py           # 新建训练（以 yolov8s.pt 为预训练）
python train_640.py --resume  # 断点续训（从 pest27_final4 的 last.pt）
```

- 数据：`dataset_full/data.yaml`（27 类，train/val 划分）
- 翻拍续训（模拟比赛打印+摄像头场景的增强）：`train_finetune.py`
- Windows 下训练脚本必须用 `if __name__ == '__main__':` 包起来（multiprocessing）

---

## 11. 常见问题（FAQ）

| 现象 | 解决 |
| ---- | ---- |
| 报错 `CUDA ... not available` | 把 `MODEL.DEVICE` 改 `'cpu'` |
| 摄像头打不开 | `python tool/camera_probe.py` 重认索引 → 改 `CONFIG.camera_num`；确认没被其他程序（含 GUI）占用 |
| 画面"跑一会就卡死" | 已由 `tool/camera_source.py` 的看门狗处理；仍卡就 `POST /api/camera/restart`，并看 `/api/camera/health` |
| 多个手机同时看实时流报"打不开相机" | 已修：`/ws/stream` 改为共享采集源；若日志仍报设备忙，说明是别的程序占着相机 |
| 语音只响第一次 | 已修（`feedback/tts.py` 专用线程 + COM 修复）。若日志显示 `无可用语音后端`，看 `/api/feedback/status` 的 `init_errors` |
| 手机实时模式打不开摄像头 | 必须 HTTPS（`--https`，用 `https://<IP>:8443/m`）并信任自签证书；否则用拍照模式 |
| 手机打不开页面 | 手机与服务器需在同一 WiFi/热点网段；用启动日志打印的那个 IP |
| 大模型回答"知识库未涵盖" | 这是**设计行为**（防幻觉）：该问题超出 27 类害虫知识库，可扩充 `llm/knowledge_base.py` 后重试 |
| 问答很慢 | 7B 在 6GB 显存上单次约 4~10 秒；可换 `qwen2.5:3b`（改 `llm/local_llm.py` 的 `MODEL_NAME`） |
| 中文乱码 | 命令行执行前 `chcp 65001`；GUI 与网页不受影响 |
| Python 3.12 装不上 | 本项目依赖 torch 2.0.1，请改用 Python 3.8~3.11 |

---

## 12. 技术栈

YOLOv8（Ultralytics）· PyQt5 · FastAPI + Uvicorn · OpenCV · PyTorch · Ollama（本地 LLM，RAG/防幻觉）·
scikit-learn（TF-IDF 检索）· System.Speech / pyttsx3（离线 TTS）· pyserial（ESP32 联动）· 自签 HTTPS + 手写二维码 ·
**EdgeJev（本地类型化决策，ONNX Runtime + INT8）**

> 说明：手机端是**纯浏览器页面**（无 App、无 Web Bluetooth），因此 iOS 也能用；
> 喷洒节点由**服务器**通过 HTTP 直接下发（见 `国赛方案.md` 拓扑），手机只连服务器。

---

## 13. 现场测评页 `/eval`（动效版 20 样本 / 180 秒）

把 GUI 里那套"现场评分面板"做到了网页上，**同一套评分口径**（`app/eval_score.py`，GUI 与 Web 共用）：

* **样本来源**：服务器目录 `EVAL.sample_dir`（默认 `./pictures20`，打开页面自动载入）或本机
  「选择文件夹 / 选择图片」（浏览器直接传，不依赖服务器文件）；
* **计时口径**：第一张样本开始识别起表 → 最后一张识别完成停表；倒计时环 180s 归零后转正计时并报警；
* **动效**：卡片 flip-in、识别扫描线、用时条生长、数字滚动、限时内完成撒花；
* **视觉重点**（对齐现场要求）：每张用时（用时 + 服务端推理耗时）、**总用时 / 180s** 大字对比、
  「提前 N 秒 / 超时 N 秒」判定条；
* **评委判分**：逐张 ✓正确/✗错误（未评判不计入正确），得分实时刷新（准确度 20 + 时效性 10 = 30）；
* **导出**：`⤓ 导出评分报告` → 服务端写 csv 到 `EVAL.report_dir`（与 GUI 导出列完全对齐），并自动下载。

打开方式：起服务后访问 `http://<IP>:8080/eval`（首页顶部导航也有入口）。

---

## 14. EdgeJev 本地类型化决策（离线 CPU 推理）

把「一句话 → 结构化决策」放到本机完成：**断网可用、运行时不依赖 torch、数据不出本机**。
上游项目 [EdgeJev](https://github.com/yzfly/edgejev)（已内置到 `edgejev/` 目录），
模型为 laya-multilingual（mmBERT-base 322M，INT8）。

```bash
# 唯一需要联网的一步（在任何有网的机器上做一次；产物 ~335MB 自包含可拷贝）
python scripts/build_edge_model.py

# 起服务即自动生效（本地进程内直调；也可换成 edgejev serve 的 HTTP 形态）
python api_server.py
```

* **推理入口页**：`/edge` —— 指令解析（规则 × EdgeJev 对照）/ 虫情研判（实验性）/ 通用 systemone 实验台；
* **指令兜底**：`/api/ask_command` 默认「规则优先 + EdgeJev 兜底（置信度闸门）」——
  规则没识别到、而模型高置信时才补位（如"把泵关了吧"→ 停止意图），
  **安全闸门（片区白名单 / 时长钳制 / 危险词 / 人工确认）完全不变，模型无权翻案**；
* **实测**（本机 CPU：单次 40–250ms；官方路由示例 dept 命中 0.91–0.99）与诚实的
  能力边界（零样本 typed-decisions 偏弱、只宜兜底不宜主判）见
  [`docs/edgejev.md`](docs/edgejev.md) 与 [`edge_models/README.md`](edge_models/README.md)；
* 配置：`config/configs.yaml` 的 `EDGE` 段（`enabled/model_dir/mode/base_url/timeout/prefer_for_commands`）；
* 测试：`tests/test_edge_engine.py`（20 条，含真模型冒烟，无模型时 SKIP）。
