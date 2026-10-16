# CropGuard 农智云 害虫检测系统

## 项目位置
- 工作目录: `<项目根目录>`（下文示例统一写作 `D:\CropGuard`）
- GitHub源码(仅代码): `<参考仓库>\Pest-detection-master`
- GitHub仓库: https://github.com/Stara-AI/Pest-detection
- 本项目仓库(开源): https://github.com/Sun-0810-yh/CropGuard（公开，含 best.pt，数据集未上传）
  - ⚠️ 2026-10-08 清理删掉了被 git 跟踪的 runs/train/pest27_final4/weights/best.onnx（与 睿抗国赛项目—农智云/best.onnx 同一份）。要恢复公开仓库里的 ONNX：`git restore runs/train/pest27_final4/weights/best.onnx`，或对比赛目录那份 `git add -f`
- 测试子仓库(公开，可独立克隆运行): https://github.com/Sun-0810-yh/CropGuard_Test
  - 内容 = `tests/`(迷你框架+60用例) + `scripts/ask_eval.py` + 被测模块(`feedback/ llm/ tool/ config/ prompts/ main.py`)；
    不含权重/数据集/`serve/`；本地源在 `_dist/CropGuard_Test/`（已加进主仓库 .gitignore）
  - 缺 `ultralytics`/`PyQt5` 时 `test_gui_adapter_has_full_interface` 走 `requires()` → SKIP（59 passed + 1 skipped，退出码 0）

## Python 环境
- GPU环境: `<conda根>\envs\pest-gpu\python.exe`（示例 `D:\Miniconda\envs\pest-gpu\python.exe`）
- 系统Python: `<系统Python>\python.exe` (3.14, CPU PyTorch)
- Conda管家: `<conda根>`（示例 `D:\Miniconda`）
- PyCharm解释器: 右下角选 pest-gpu

## 竞赛背景 (2026 睿抗 CAIR赛道)
- 省赛评分: 20张实拍图全对(35分) + 180秒内跑完(15分) + 评估报告含Loss/mAP收敛曲线(10分) + GUI(10分) + PPT(15分)
- 平台部署: 只上传 best.onnx，必须 imgsz=640（480 会失败）
- 20张比赛图: `<比赛图目录>\pictures20\`（自行挑选，全部来自原始实拍池）
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
- ONNX: 睿抗国赛项目—农智云/best.onnx（640，平台可用；runs 下的重复副本已于 2026-10-08 删除，两份 SHA256 相同）
- 续训: python train_640.py --resume

## 关键文件
- 训练脚本: train_640.py（摄像头场景增强：亮度/色相/旋转±25°/透视/缩放；从头训练需 COCO 权重 yolov8s.pt）
- 翻拍续训: train_finetune.py（复制比赛图x3份加PRINT_前缀 + 强退化增强，15轮）
- 配置: config/configs.yaml（WEIGHT 已指向 pest27_final4；AI.active_model=local 本地 Ollama qwen2.5:7b，离线合规）
- GUI入口: main.py（PyCharm 运行或 `python main.py`），界面类 UI.py
- 推理工具: tool/tools.py
- 相机采集: tool/camera_source.py（**共享单例 + 看门狗自愈**；GUI/服务器/手机共用，别再加第二套 VideoCapture）
- 相机探针: tool/camera_probe.py（现场认索引）；独立命令行识别: tool/camera_detect.py
- 本地LLM: llm/local_llm.py（Ollama，不可用时降级 knowledge_base 兜底）
- AI 网关: llm/ai_gateway.py（**唯一 AI 出入口**：RAG 检索 → 接地提示词 → Ollama → 后校验）
- 检索/防幻觉: llm/retriever.py（TF-IDF 字符 n-gram）、llm/kb_chunks.py、llm/guard.py
- 接地提示词: prompts/templates/grounded_expert.txt（取代旧 1500 字"报告型"模板）
- 手机端页面: serve/mobile.py（`GET /m`，含「🌫 雾化控制」卡片）；二维码: tool/qr_min.py；证书: tool/gen_cert.py
- EdgeJev 适配层: app/edge_engine.py（**只做理解、绝不驱动硬件**；规则优先 + 置信度闸门兜底）
- EdgeJev 运行包: edgejev/（内置上游包，Apache-2.0）；模型: edge_models/jev-int8（构建脚本 scripts/build_edge_model.py）
- EdgeJev 推理入口页: serve/pages/edge.html（`GET /edge`）；部署文档: docs/edgejev.md；校准记录: edge_models/README.md
- 测评页 /eval: serve/eval_page.py + serve/templates/eval.html + serve/static/eval.{css,js}（`GET /eval`）
- 测评评分纯函数: app/eval_score.py（**GUI 与 Web 共用同一份口径**，main.py 里是薄包装）
- 校准探针: scripts/edge_probes/（改 EdgeJev 问法/阈值前先跑这个，别拍脑袋）
- 信号反馈: feedback/feedback.py（播报语义）+ feedback/tts.py（**专用线程 + 多后端真校验**）
- 喷淋控制: feedback/spray.py（serial/http/mqtt + stop；http 目标分 `protocol: 'json'|'mist'`）
- 硬件固件: ESP32/（PlatformIO 工程，8 个 env，见 ESP32/README.md）
- 测试: tests/run_tests.py（内置迷你框架，零依赖；`python tests/run_tests.py`）
- RAG 评测: scripts/ask_eval.py（库内召回 / 库外拒答 / 越权指令拦截）

## 启动方式
- **解释器（本机实测）**：PATH 上的 `python` 是 3.12.10，**缺 cv2/torch，跑不了服务**；
  能用的是 `py -3.14`（`D:\Dev Env\Python\python.exe`，torch **2.12.0+cpu、无 CUDA**）。
  config 里 `MODEL.DEVICE: 'cuda:0'` → CPU 机器上必须覆盖：
  `$env:CG_DEVICE='cpu'; py -3.14 api_server.py`（`app/state.py` 读 `CG_DEVICE` 环境变量，
  所以**不用改配置**，GPU 机器上照旧走 `cuda:0`）。
- GUI: 运行 main.py（PyCharm 或 `python main.py`）
- Web + 手机端: `py -3.14 api_server.py`（PC 首页 + `/m` 手机页 + REST/WS）；
  手机"实时模式"再加 `--https`（HTTPS 8443，自签证书自动生成）
- 启动横幅要确认三行：`Spray transport/http_targets`、`雾化节点(mist)=[...] 持续模式兜底=Ns`、
  `手机会话页: http://<本机IP>:8080/m`（这个 IP 就是要填进手机浏览器的地址）
- 训练: `python train_640.py`（新建）或 `python train_640.py --resume`（续训）
- 喷淋联调: `python api_server.py`（**唯一**接 SprayController 的入口；GUI 只做语音+串口反馈）
- 测试: `python tests/run_tests.py`；RAG 评测: `python scripts/ask_eval.py`（`--llm` 才真调大模型）
- 测评页: `http://<IP>:8080/eval`（20 样本/180s；样本目录 EVAL.sample_dir，默认 ./pictures20）
- EdgeJev 入口: `http://<IP>:8080/edge`；状态: `GET /api/edge/status`
- EdgeJev 模型构建（唯一需联网的一步，任何机器做一次即可）: `python scripts/build_edge_model.py`
- 固件: VSCode 打开 ESP32/ 目录（不是仓库根）→ PlatformIO 选 env → Upload

## 软件端最小闭环（2026-10-08 第二批 · 已实现并实测）
| 需求 | 落点 | 实测结论 |
| --- | --- | --- |
| 外接相机识别 | `tool/camera_source.py` + `api_server.ws_stream` | 无设备时如实报 `dead + last_error`，不静默；有设备走 DSHOW/MJPG |
| 语音只响一次 | `feedback/tts.py` | 根因＝pyttsx3 SAPI5 的 COM 回调跨线程（`_busy` 永真）+ `_speak_lock` 永锁 + 异常被吞；改**专用线程内建引擎 + 真出声校验 + 多后端兜底**；本机 `SAPI.SPVoice` 不可用、`System.Speech`(powershell) 可用，auto 自动选中；连续 3 句播报通过 |
| 摄像头卡死 | 同上（看门狗：无新帧/画面指纹不变 → 换 handle；跨客户端引用计数共享） | 无相机时 `/api/camera/health` 显示 dead；`POST /api/camera/restart` 现场抢救 |
| 用户自由提问 | `llm/ai_gateway.answer` → `POST /api/ask` + GUI 聊天（`ChatWorker`） | GUI 与网页同一答案；`/api/kb/search` 可现场展示召回 |
| 防幻觉 | 召回闸门(`CHAT.refuse_below`=0.18，低于阈值**不调模型**) + 接地提示词 + `temperature=0.2`/固定 seed + `llm/guard.py` 后校验 | 库外问题直接拒答；编造药名/剂量被标注"请核实" |
| RAG（原待定项→已做） | `llm/kb_chunks.py` + `llm/retriever.py` + `scripts/ask_eval.py` | 110 片段 / 27 类 / 61 别名；评测：库内召回 20/20、库外拒答 10/10、越权拦截 4/4 |
| 手机实时+拍照 | `serve/mobile.py`（`/m`）+ `WS /ws/phone` | 二进制帧上传实测 0.15s 回检测 JSON；拍照档纯 HTTP 可用（`capture` 不需要 HTTPS） |
| 手机提问 → 控制喷洒 | `POST /api/ask_command`（**只解析**）→ 人工确认 → `/api/spray` | 危险词拦截、"100 秒"钳到 5 秒、未指定片区拒绝；大模型绝不直接驱动硬件 |
| 测试 | `tests/`（迷你框架）+ `scripts/ask_eval.py` | 60 用例全绿（约 38 秒）；ask_eval 三项 100% |

### 这一批踩过的坑（下次别重踩）
1. `python tests/run_tests.py` 时 runner 是 `__main__`，测试里 `from run_tests import ...` 是**另一个模块实例**
   → 装饰器状态必须放 `tests/_registry.py` 共享；
2. 测试写临时文件**不要用 `%TEMP%`**（沙箱可能只允许工作区写，实测 `cv2.imwrite` 报 permission denied），
   也不要用长路径 + `cv2.VideoWriter`（Windows 报 `CAP_IMAGES: can't find starting number`）→ 用工作区内短路径 + 图片序列；
3. 手写 QR 时**定时图案要最先放**（否则定位图案的分隔符会覆盖第 8 个模块），
   且**填充与掩码必须共用同一张"功能区"表**（分开判定会把格式信息区当数据取反，扫码器读不出）；
   本机 OpenCV 的 `QRCodeDetector` **本身失效**（连它自己 `QRCodeEncoder` 生成的码都解不出），别用它做验证；
4. `openssl.exe` 必须带 `-config`（本机是 `<conda根>\Library\ssl\openssl.cnf`），
   且 `-config` 要放在 `req` 子命令**后面**；`New-SelfSignedCertificate` 这条路在本机被策略拒绝；
5. 本机 `SAPI.SPVoice`（win32com / pyttsx3 都走它）建对象成功但 `Speak` 报 COM 错 →
   语音后端**必须做"真出声"校验**，不能只看对象是否创建成功；
6. 相机"设备不在"是常态（硬件没配齐），所有相机相关入口都要能在无设备时给出可读原因，不能抛异常。


## 手写 sketch 雾化接入（2026-10-12 · 已实现并实测）
用户自写的 Arduino 固件 `sketch_oct12a.ino`（继电器 GPIO22 + 模拟雾化板按键 GPIO23，
接手机热点 `vivoX300`）接口与项目 PlatformIO 固件不同。**服务器侧做协议适配，手机页经服务器控制**——
不改动已烧录的固件，也不让手机直连节点（沿用"节点只接受服务器指令"的拓扑）。

| 项 | 落点 | 说明 |
| --- | --- | --- |
| 接口协议 | `feedback/spray.py`（target 项 `protocol: 'json'\|'mist'`） | `mist` = `GET /start?time=N`（定时）/ `GET /startDirect`（持续）/ `GET /stop`；`json` = 项目固件 `POST /spray` + `POST /stop` |
| **"永远回 200"** | 同上 `_write_mist` | 该固件连"雾化正在运行中，请先关闭！"也回 200 → **必须读响应体判成败**，只看状态码会把"拒绝启动"当成功 |
| 持续模式兜底 | 同上 `_arm_direct_timer` / `_direct_timeout` | `/startDirect` 固件不自停 → 服务器 `SPRAY.direct_max_sec`（默认 **300s**）到点自动 `/stop`；人工停止、成功的定时喷雾都会撤掉计时器 |
| 节点寻址 | `SPRAY.http_targets` + `SPRAY.status_timeout` | 每项可写 `protocol`；mist 固件**无分组过滤**（片区隔离靠 IP 寻址），地址是占位 `192.168.43.61/62`，按节点串口打印的 IP 改 |
| 手机页 | `serve/templates/mobile.html` + `static/mobile.js` 的「🌫 雾化控制」卡片 | 定时喷雾（上限 5s，后端再钳）/ 持续喷雾（确认框写明兜底秒数）/ 停止 / 每 3s 拉状态显示「定时模式 剩余 Ns、持续模式（兜底 Ns）、空闲、离线+原因」/ 节点自带页面链接 |
| REST | `GET /api/mist/status`、`POST /api/mist/start`、`/api/mist/direct`、`/api/mist/stop` | 都用**普通 `def`**（FastAPI 丢线程池），不阻塞推理/推流事件循环 |
| 测试 | `tests/test_mist_protocol.py`（26 条） | 用**真 loopback HTTP 假节点**复刻固件脾气：钳制、"200 但拒绝"、兜底自动停、计时器撤销、json 协议回归、手机页渲染 |

既有入口同样能驱动 mist 节点：`POST /api/spray`（手机页 A/B 区按钮）、语音指令 `POST /api/ask_command`
与自动联动 `risk_map` 都按目标协议分派（对 mist 节点就是"定时喷雾 N 秒"语义）。

## EdgeJev 部署 + 测评动效页（2026-10-16 · 已实现并实测）

| 需求 | 落点 | 实测结论 |
| --- | --- | --- |
| EdgeJev 部署到 CropGuard | `edgejev/`（内置运行包）+ `edge_models/jev-int8`（INT8 335MB）+ `app/edge_engine.py` + `scripts/build_edge_model.py` | 本机 CPU 单次决策 40–250ms；官方路由示例命中 billing 0.91–0.99；`GET /api/edge/status` 显示 available=true, mode=local |
| 前端推理入口 | `serve/pages/edge.html`（`GET /edge`，三 Tab：指令解析对照 / 虫情研判(实验性) / 通用 systemone）+ 首页导航入口 | 无头 Edge 实测：引擎在线、解析对照与 systemone 均渲染正常（截图在 output/_test_tmp/edge_full.png） |
| 指令解析接入 | `/api/ask_command?engine=auto`（默认）：**规则优先** + EdgeJev 高置信兜底（combine_command） | "把泵关了吧/把水停了" 兜底为 stop 生效；"B区再来两秒吧" 等误判被 0.85 闸门拦住；危险词拒绝永不被翻案 |
| 测评动效页 | `serve/eval_page.py` + `templates/eval.html` + `static/eval.{css,js}`（`GET /eval`）；接口 `/api/eval/*`；评分共用 `app/eval_score.py` | 无头 Edge 全流程通过：载入样本→连跑→计时环→判分→导出 csv（总分实时、与 GUI 口径一致）|
| 规则侧顺带修复 | `llm/ai_gateway.py` 停止词表扩充（关闭/关掉/关了/关一下…）+ **危险词检查前置** + 停止也读片区 | 修复"A区的雾化关掉"被误判成喷洒；"关闭安全限制"不会被当成停止指令 |
| TTS 测试修复 | `feedback/tts.py` 补回 `_build_backend` 构建缝（BackendRegistry 委托） | 7 条语音用例从 AttributeError 恢复通过；测试夹具同步签名 |

### 这一批的坑（下次别重踩）
1. **EdgeJev 是"路由/分类"向小模型**：自定义类型化问题属零样本（官方模型卡明说 near-chance）。
   问法必须英文 instructions + 动词短语 criteria；**永远规则优先、模型仅高置信兜底**；
   int8 动态量化**合批会改答案**——线上固定同一套题（顺序不变）保可复现；fp32 并未更准（实测反而略差）还大 4 倍。
2. `hf-mirror.com` 可用、`huggingface.co` 直连被墙；`HF_HUB_DISABLE_XET=1` 必须设（镜像不支持 Xet）。
   构建依赖里 **onnxscript 不能漏**（torch dynamo 导出硬依赖）。
3. 无头浏览器验证（headless-web-verify skill）：`--window-size` 在本机 Edge 155 不生效，
   要 `Emulation.setDeviceMetricsOverride`（1500x1300）才拿到桌面版式截图；后台启动要用
   "Start-Process + 进程存活轮询"的 PowerShell 任务，否则 Edge 被随会话回收。
4. 本机 `.venv`（uv, py3.12）跑服务/测试：`CG_DEVICE=cpu ./.venv/Scripts/python.exe api_server.py`；
   模型 ckpt 还要 `dill`、vendored ultralytics 还要 `timm`（已装进 .venv，但不在 requirements 里，注意补）。
5. 课程/测试脚本里 `%` 模板串容易炸（`%.1f` 会被外层 `%` 吃掉）→ 生成代码用 `.replace()` 占位更稳。

## 当前路线（2026-10-08 对齐 · 清理后重定）
主线：**先跑通单节点闭环，再扩面**。功能按"能不能现场演示"排序，不按"功能多少"排序。

### 硬件口径（2026-10-08 二次对齐）
| 设备 | 数量 | 用途 |
| --- | --- | --- |
| ESP32 Dev Module | 3（**2 用 + 1 备用**） | 片区A / 片区B 各一块，**同时支持 WiFi 与蓝牙**；实测芯片 `ESP32-D0WD-V3` rev v3.1，460800 波特率烧录正常 |
| 1 路 3.3V 继电器 | 3（2 用 + 1 备用） | **光耦隔离、低电平触发**（= `RELAY_ACTIVE` 默认 LOW，无需改代码）；GPIO13 控雾化通断 |
| 雾化模块套装 | 2 | **5V 超声雾化片 + 驱动板**（一件）；一路继电器控它。**必须浸没**，单次已收紧到 5 秒（`-DMAX_SPRAY_SEC=5`） |
| 工业摄像头 + 支架 + 补光灯（整套） | 2 | 定点标准化采集（A 档） |
| 声光报警器 | — | **暂不做**；`alarm` env 代码保留、不现场烧 |

### 两条业务线（比赛要求形态C：明确的外部设备联动）
- **方案一 定点自动**：工业摄像头A 常驻采集 → 推理 → 判定「大量害虫」(高风险) → 自动喷洒对应片区
- **方案二 手持人工确认**：农户手机拍照/视频上传 → 服务器检测 → 手机网页确认 → 喷洒对应片区
- 共同拓扑：**相机与手机都只连服务器；节点只接受服务器指令**（手机不直连节点）

### BLE 的定位（重要，别做错方向）
BLE 走 **服务器 → 节点**（Python `bleak`），**不做手机直连节点**：
手机直连必须用 Web Bluetooth，而 iOS Safari 不支持（见 `国赛购买清单.md` 第 219 行），
且需要页面拿到节点 BLE 地址、离开 ~10m 就断链、无法留痕。
服务器直连零手机约束、可写 history、可做"WiFi 主 + BLE 备"降级。

- **Phase 0 固件可编译**：✅ **已完成并上板验证（2026-10-08）** —— 8 个 env 全部 `SUCCESS`（`pio run` 退出码 0），
  `ENABLE_SERIAL_ALARM=1` 那条可选路径也单独验证过。产物在 `ESP32/.pio/build/<env>/firmware.bin`。
  **真机验证**：片区A 板烧 `selftest` → 串口输出 `SELFTEST_PASS`（29 项断言、0 失败）；
  芯片 `ESP32-D0WD-V3` rev v3.1，MAC `<板子MAC>`（做 ESP-NOW 单播时登记用），
  串口设备为 **COM5**（COM3/COM4 是系统蓝牙虚拟串口，别搞混），460800 波特率烧录一次成功。
  - 踩过的坑与修法（下次别重踩）：
    1. **平台钉 `espressif32@7.1.3`**（Arduino 核心 2.0.17），别升级；
    2. 共享头文件必须放 **`ESP32/include/`**（PlatformIO 只自动加这个目录），放 `src/common/` 会 `fatal error: cg_protocol.h not found`；
    3. **全局变量不能叫 `alarm`** —— 与 POSIX `alarm()` 冲突（经 unistd.h→pthread→Arduino.h 引入），已改名 `alarmUnit`；
    4. `platformio.ini` 加了 `build_cache_dir = .pio/build_cache`，8 个 env 共享框架目标文件（每个 env 3–9 秒）；
    5. 本机系统代理 `127.0.0.1:65532` 已失效但会被 `requests`/PlatformIO 自动使用 → 必须设 `NO_PROXY` 绕开；
       pio 自己的镜像列表含 `github.com`（被拒）与 `usc1.contabostorage.com`（DNS 不通），**下载基本得靠绕代理直连**；
    6. 工具链（115MB）与框架（735MB）是**手工喂进** `~/.platformio/packages/` 的（`ESP32/tools/fetch_toolchain.py` 下载、
       `install_toolchain.py` 写缓存），框架 `.piopm` 必须**无 BOM**，否则报 `InvalidJSONFile`。
- **Phase 1 单节点闭环（当前重点）**：烧 `http_node_g1` + `alarm` → `python api_server.py`，验收四条：
  1. `curl` POST `/spray` → 继电器吸合 2 秒后自动断开
  2. 组不匹配（把 `g2` 发给 `g1` 节点）→ 继电器**不动**
  3. 高风险样本 → 语音播报（声光报警器**暂不做**；`alarm` env 保留代码、不烧）
  4. 拔 USB 改充电宝供电 → 仍能远程触发（真无线）
  - **进度（2026-10-08）**：✅ **继电器控制侧已真机验证通过** —— 1 路光耦隔离模块（丝印 `DC+/DC−/IN`、`COM/NO/NC`、`H/L` 跳线帽）
    接 `3V3→DC+`、`GND→DC−`、`GPIO13→IN`，**跳线帽拨 `L`**（低电平触发，与 `RELAY_ACTIVE` 默认一致）；
    用 `env:relaytest`（不依赖 WiFi，每 2 秒吸合/断开循环）验证：每 2 秒咔哒一次、板载 LED 同步闪烁。
    雾化负载侧（`COM`+`NO`）尚未接线。
  - **进度（2026-10-08 晚）**：✅ **HTTP 链路 + 继电器真机闭环已跑通**（Phase 1 验收前两条已证）：
    节点 `http_node_g1` DHCP 拿到 `<节点IP>`（网关 `<热点网关>`、RSSI −37dBm）→
    `GET /status` 200；`POST /spray {"group":"g1","duration":2}` → 串口 `SPRAY_ON group=g1 duration=2` + `SPRAY_OFF`
    （**继电器吸合 2 秒后自动断开**）；`{"group":"g2"}` → `SKIP group=g2`（**片区隔离，继电器不动**）；
    `{"group":"g1","duration":60}` → 实际 `duration=5`（**安全钳制**）；空 body → 400、未知路径 → 404。
    ⚠️ **关键坑（已解）**：手机热点若处于「共享已连接的 WiFi / 桥接」模式，会把上游网络地址直接发给接入设备
    （网段由上游 DHCP 决定，不一定是热点网段）。此时节点用写死的 `192.168.43.61`
    会「`WIFI_OK` 但服务器 ping 不到、curl 超时」→ 已加 `USE_DHCP` 开关，两个节点 env 默认 `-DUSE_DHCP=1`，
    **节点 IP 以串口打印的为准**，并同步填进 `SPRAY.http_targets`（按现场串口打印的 IP 填）。
    诊断口诀：看服务器从哪个 DHCP 服务器拿到地址 —— 手机当热点时应是 `x.x.x.1`，若是别的地址说明热点在桥接。
- **Phase 2 双片区**：加 `http_node_g2`；验收 片区隔离 + 20 样本 180 秒内 + 实测时长与 `risk_map` 一致。
- **Phase 3 BLE 冗余通道（两片区都要，Phase 1 之后再动）**：固件 `cg_ble.h`
  （GATT：`CMD` 写 JSON / `STATE` 读+notify，广播名 `CropGuard-g1|g2`）+ 服务器 `feedback/ble_link.py`
  （bleak，Windows 原生，不需要手机 App、绕开 iOS Safari 不支持 Web Bluetooth 的限制）
  + `SPRAY.transport: 'http+ble'`（WiFi 主通道，失败降级蓝牙）。
  接口已在 `cg_config.h` 预留 `ENABLE_BLE`；**ESP-NOW 节点不要开 BLE**（三开有共存毛病）。
- **Phase 4 文档口径**：`国赛方案.md` 按仓库实况整体重写（现在仍是"方案丙为默认推荐"的旧口径）。

已定口径（不要再改）：
- GUI 主线 `main.py` + `UI.py`；固件唯一源 `ESP32/`（`.ino` 已删）；喷淋走**方案甲 HTTP**
- `dataset_102` 留原地（不删、不外移）；备用 ESP-NOW 三个 env 保留、现场可烧
- 明确不做：MQTT、OTA、ESP32-CAM 图像采集、旋转机构、WiFiManager 配网门户、升级 ultralytics

外部阻塞：`CropGuard/`（另一会话进程占用，删不掉）。

## 清理记录 (2026-10-08 · 目录对齐)
- 删除 GUI v2 线: main_v2.py、UI_v2.py（均未提交 git，**永久删除**；主线定为 main.py + UI.py）
- 删除冗余模型: runs/…/best.onnx（与比赛目录那份 SHA256 相同）、best.onnx.data（best.onnx 内无 external_data 引用）、best.onnx.bak、yolov8s.pt
- 删除死代码: auto_copy_model.sh（源路径 pest102_run1 已不存在）、platform_camera.py（全仓库零引用）
- 删除 5 个 Arduino `.ino`（已移植进 ESP32/）及 feedback/spray_executor/ 整棵子树
- 删除: .venv/（Python 3.14 残留）、img/111.mp4、fonts/Arial.ttf、空目录 weights/ 与 prompts/templates/、30 个 __pycache__
- 未动（有争议）: dataset_102（777MB）、output/、runs/detect/
- 未完成: CropGuard/（espidf 脚手架）被另一进程占用删不掉；其 scripts/pio_seed_cache.py 已抢救到 ESP32/tools/
- 共释放约 188 MB

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
