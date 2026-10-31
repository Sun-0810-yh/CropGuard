# 株得 CropGuard

> **农作物虫害个体化识别与精准减药决策实施系统** —— YOLOv8s 27 类害虫检测 + 现场评分 + 实时监测自动喷洒 + 离线大模型问答。
>
> 2026 睿抗机器人开发者大赛（CAIR 赛道）省赛一等奖 · 晋级国赛。
>
> **当前版本：v0.1（初版集成 · 闭环 demo 可运行 · 未优化，已知问题见文末）**

---

## 目录

1. [30 秒看懂](#1-30-秒看懂)
2. [快速开始](#2-快速开始)
3. [页面地图](#3-页面地图)
4. [三套演示](#4-三套演示)
5. [评分系统（20 样本 / 180 秒）](#5-评分系统20-样本--180-秒)
6. [实时监测与自动喷洒](#6-实时监测与自动喷洒)
7. [手机端](#7-手机端)
8. [AI 问答与防幻觉](#8-ai-问答与防幻觉)
9. [EdgeJev 本地决策](#9-edgejev-本地决策)
10. [数据看板（口径要分清）](#10-数据看板口径要分清)
11. [27 类害虫对照表](#11-27-类害虫对照表)
12. [系统架构](#12-系统架构)
13. [配置说明](#13-配置说明)
14. [目录结构](#14-目录结构)
15. [测试与自检](#15-测试与自检)
16. [常见问题](#16-常见问题)
17. [已知问题（v0.1）](#17-已知问题v01)
18. [许可与致谢](#18-许可与致谢)

---

## 1. 30 秒看懂

- **识别**：单张图/视频/摄像头 → 逐虫出框 + 虫种 + 置信度（27 类），CPU 约 100–250ms/张。
- **决策**：风险分级（无/低/中/高）→ 按 `risk_map` 查表决定喷哪个片区、喷几秒；**剂量来自表，不来自模型即兴**。
- **执行**：服务器 HTTP 下发到 ESP32 节点（手写 sketch：`/start?time=N`、`/startDirect`、`/stop`）。
- **人机协同**：手机拍照/实时上传 → 服务器识别 → 大模型给建议 → 人工确认后才喷。
- **离线**：模型、知识库、语音、问答全部本机运行，不调用任何外部 API。

---

## 2. 快速开始

### 2.1 环境

| 项目 | 要求 |
| --- | --- |
| 系统 | Windows 10/11（现场演示机）；Ubuntu 20.04+ 也可运行 |
| Python | 本机实测 **3.14**（`D:\Dev Env\Python\python.exe`）或 conda `pest-gpu` 环境；**带 torch / opencv / fastapi** |
| 显卡 | 现场推理固定用 GPU 环境 `D:\Dev Env\Python\torch\venv`（RTX 3060），双击脚本直接用它，不做 CPU 备选 |

```powershell
py -3.14 -c "import torch, cv2, fastapi; print('ok')"    # 先确认解释器可用
pip install -r requirements-server.txt                   # 只跑服务
pip install -r requirements.txt                          # 连桌面 GUI 一起（PyQt5）
```

### 2.2 三种启动方式（任选一种，**不要同时开两个**）

**A. 双击（现场最稳）**

| 文件 | 作用 |
| --- | --- |
| `启动演示系统.bat` | 起服务（HTTP 8080 + HTTPS 8443）→ 自动打开 `/demo` 演示指挥台 |
| `停止演示系统.bat` | 关掉占用 8080/8443 的 Python 进程（不动 Ollama） |
| `启动评分GUI.bat` | 只开桌面评分 GUI（备用，不常驻） |
| `创建桌面快捷方式.bat` | 在桌面生成「株得演示」快捷方式 |

**B. PyCharm（图形化，推荐调试用）**

1. `Run → Edit Configurations… → + → Python`
2. 按下面填：

   | 字段 | 值 |
   | --- | --- |
   | Name | `CropGuard 服务器` |
   | Script path | `<仓库>\api_server.py` |
   | Parameters | 留空（默认 8080/8443）；换端口写 `--port 8080` |
   | Working directory | `<仓库根目录>`（**必须是根目录**，配置/模型都是相对路径） |
   | Python interpreter | 带 torch 的那个（3.14 或 pest-gpu） |
   | Environment variables | `CG_DEVICE=cpu`（有显卡可不填） |
   | 勾选 | *Emulate terminal in output console* |

3. `Shift+F10` 运行；日志在下方 Run 窗口；`Shift+F9` 可断点调试；红色方块停止。

**C. 命令行**

```powershell
& "D:\Dev Env\Python\torch\venv\Scripts\python.exe" -B api_server.py     # 服务（HTTP 8080 + HTTPS 8443）
py -3.14 api_server.py --no-https                # 只跑 HTTP（手机只能用拍照模式）
py -3.14 main.py                                 # 桌面 GUI（与服务器二选一，别同时占相机）
```

### 2.3 启动后先看这几行

```
[INFO] 摄像头：camera_device=vid_0c45 camera_num=0 → 使用 'vid_0c45'（按设备标识解析…）
[INFO] Spray transport=http available=True protocols={'g1': 'mist'}
[INFO] 雾化节点(mist)=['http://<节点IP>'] 持续模式兜底=300s
[CERT] ✅ SAN 已覆盖当前局域网 IP: <本机IP>
手机会话页(唯一入口): https://<本机IP>:8443/m
语音: engine=powershell ready=True
```

缺谁补谁，见[常见问题](#16-常见问题)。

### 2.4 权重与数据（不在仓库里的东西）

| 资源 | 位置 | 说明 |
| --- | --- | --- |
| 检测权重 `best.pt` | `runs/train/pest27_final4/weights/best.pt` | **运行时必需**，随仓库提供 |
| ONNX 导出件 | `runs/.../best.onnx`、`睿抗国赛项目—株得/best.onnx` | 平台提交用（480 会失败，必须 640） |
| EdgeJev 模型 | `edge_models/jev-int8/`（369MB） | **不入库**；换机整目录拷贝或跑 `scripts/build_edge_model.py` |
| 比赛 20 图 | `pictures20/` | 不入库；看板/彩排要用 |
| 真实测试集 | `real_testset20/` | 不入库；`scripts/eval_real_testset.py` 评测用 |
| 训练集 | `dataset_full/` | 不入库；只有重训才需要 |

---

## 3. 页面地图

| 路径 | 页面 | 一句话 |
| --- | --- | --- |
| `/demo` | **演示指挥台**（原生深色） | 一个页面串三套演示 + 十项自检，双击启动默认打开 |
| `/` | **检测台**（Vue） | 图片 / 文件夹 / 视频 / 摄像头 / 手机 五种输入 + 结果 + 建议 |
| `/eval` | **现场评分**（Vue） | 同一个检测台的「评分作业」模式：拍照 → 判分 → 30 分 → 导出 CSV |
| `/stream` | **实时监测**（Vue） | 定点监控 + 自动/手动喷洒总闸 + 风险时间线 |
| `/ask` | 农技问答 | RAG + 防幻觉，库外问题明确拒答 |
| `/spray` | 喷淋控制 | 节点状态、手动喷淋、雾化卡片、一句话控制 |
| `/system` | 设备与接入 | 局域网地址/二维码、相机状态、接口清单 |
| `/dashboard` | 数据看板 | 离线能力快照（训练/验证/评测），不是现场成绩 |
| `/dashboard-offline` | 离线看板 | 单文件快照，`file://` 可开 |
| `/m` | **手机端**（Vue） | 拍照/实时识别、问答、喷洒、记录；HTTP 打开会自动升 HTTPS |
| `/edge` | EdgeJev | 选择题式：指令解析 / 决策点规划 |
| `/classic` `/eval-classic` `/m-classic` | 原生兜底页 | 前端出问题时的后路 |

---

## 4. 三套演示

### ① 20 个实体样本 · 180 秒（计分）

打开 `/eval` → 摄像头画面 → 标本摆好 → 看到框后点「**拍照并记录（n/20）**」→ 带框快照飞入样本栏（第一张落位后开始计时，未检出不入栏）→ 满 20 张自动停表 → 「**去评测**」进 `/eval/review` → 图片逐张飞上大屏，评委判正确/错误后下一张直接叠放上来（判定后不可更改，可随时回看） → 「导出 CSV」。置信度滑块同时控制实时框和拍照，不影响喷淋/语音。
细节见[第 5 节](#5-评分系统20-样本--180-秒)。

### ② 定点监控 → 自动喷洒

`/stream`（或 `/demo` 第二张卡的「开始定点监控」）→ 放 3–5 只大体型虫 → 1–2 秒内风险变「高风险」→ 自动喷 + 语音 + 联动记录。
**自动喷洒总闸**可随时关（关掉只播语音不喷），手动按钮不受影响。

### ③ 手机 → 大模型 → 决策 → 手动喷洒

手机扫码进 `/m` → 拍照/实时识别 → 问「这个虫怎么防治？」→ 说「给 A 片区喷 3 秒」→ 解析 → **人工确认** → 设备动作。

### 幕间硬约束

- **相机独占**：同一时刻只能有一个程序用相机（网站、GUI、微信）。切演示前先关掉上一个。
- 换页面不用改配置；改配置要重启服务（除 `/demo` 里的「节点地址」是热更新）。

---

## 5. 评分系统（20 样本 / 180 秒）

**口径**（与桌面 GUI 共用同一份纯函数 `app/eval_score.py`，测试钉死）：

- 准确度 20 分 = min(正确数, 20) × 1 分
- 时效性 10 分 = 10 − max(0, 用时 − 180s) × 0.5
- **未评判不计入正确**（只少分，不会被误判成对）
- 满分 30 分；`/api/eval/score` 返回实时分数

**作业规则**（照搬 GUI）：

| 规则 | 说明 |
| --- | --- |
| 只有"采集"算样本 | 拍照 / 图片 / 文件夹 / 显式抓帧；**实时预览与视频连续帧不写样本** |
| 没检出目标不写行 | 该张标为「未检出·不计分」，不影响得分 |
| 一次采集 = 一条 | 不做同目标合并，每张都要评委单独判 |
| 计时起点 | **按下快门那一刻**（第一张样本） |
| 停表 | 满 20 张自动停；也可点「结束本轮」 |
| 超过 20 张 | 分数只计前 20 张，其余仍可检测 |

**产物**：

- 带框快照：`output/snapshots/eval_*.jpg`（与 GUI 同目录），页面可点开回看
- 评分报告：`output/eval_reports/eval_report_web_*.csv`（逐样本 + 汇总）
- 没相机时彩排：`/eval` 里点「从服务器载入」直接读 `pictures20/`

---

## 6. 实时监测与自动喷洒

- 画面：服务器相机 → `/ws/stream` 推原始帧 + 检测框，前端叠框，多客户端共享同一路采集。
- 自动：`app/inference.py` 的推理泵对**高/中风险**调 `_linkage` → 按 `SPRAY.risk_map` 下发，受总闸 `AUTO_SPRAY_ENABLED` 控制。
- 手动：`/api/spray {group, duration}`；停止 `/api/spray/stop`；单次时长被 `SPRAY.max_duration`（默认 5s）钳制。
- 边界：**评分页不触发喷洒**（`/api/detect` 不调 `_linkage`），只有实时监测/手机实时会联动。

---

## 7. 手机端

- 唯一入口：`https://<本机IP>:8443/m`（HTTP 打开会自动升级；不想升级加 `?http=1`）。
- 首次提示"不是私密连接" → 「高级 → 继续前往」（自签证书，按当前 IP 自动重签）。
- HTTPS 没起来时页面会明说原因，**不会给死链**；拍照模式在 HTTP 下照常可用。
- 能力：拍照识别、实时识别（`/ws/phone` 上传帧，本地叠框）、问答、喷洒（含雾化卡片）、联动记录。
- 断线自动重连；切到后台自动停流省电。

---

## 8. AI 问答与防幻觉

- 只走**本地 Ollama**（`http://127.0.0.1:11434`，默认 `qwen2.5:7b`）；没装也能跑，降级为知识库原文。
- 四道闸：召回分数 < `CHAT.refuse_below` 直接拒答 → 接地提示词只许用给定片段 → temperature=0.2 + 固定 seed → 后校验编造药名/剂量。
- 输出带 `citations`（依据）与 `source`；问答留痕 `output/qa_history.jsonl`。
- 示例：问「今天天气怎么样」→ **"知识库未涵盖该问题，我不做推测性回答。"**

---

## 9. EdgeJev 本地决策

- 位置：`edgejev/`（上游 yzfly/edgejev，Apache-2.0）+ 适配层 `app/edge_engine.py` + `edge_models/jev-int8/`（ONNX int8，CPU）。
- 定位：**类型化决策/意图解析**（13 类 intent），只"听懂 + 给建议"，**不直接驱动硬件**。
- 入口：`POST /api/ask_command`（规则优先，规则拿不准才调它）、`/edge` 页面、`/api/edge/*`；手机/网页的"一句话控制"也会用到。
- **完全离线**，不联网。
- 口径：零样本能力弱，演示只用固定 3 条——「把泵关了吧」「给片区A喷3秒」「一直喷不要停」。

---

## 10. 数据看板（口径要分清）

看板是**离线证据板**，不是现场成绩。数据由 `scripts/build_snapshot.py` 生成 `output/dashboard/snapshot.json`，`/dashboard` 只负责画：

| 区块 | 来源 | 回答的问题 |
| --- | --- | --- |
| 训练收敛 | `runs/.../results.csv` | 训练过程是否正常 |
| 逐类 mAP | 验证集逐类指标 | 27 类各自什么水平 |
| 实拍样本 | `pictures20/`（20 张，文件名带真实类别） | 比赛 20 图 Top1 命中率（当前 20/20，平均 78.7ms） |
| RAG 评测 | `scripts/ask_eval.py` 产物 | 域内召回、域外拒答、越权拦截 |
| 测试/联动/实时态 | 测试套件 / config / 设备探测 | 系统当下是否就绪 |

> ⚠️ 现场拍摄的 20 张**不进看板**，它们只进 `/eval` 的评分与 CSV。答辩时把两套口径分开讲。

---

## 11. 27 类害虫对照表

| # | 英文类别 | 中文 | 通俗叫法 | # | 英文类别 | 中文 | 通俗叫法 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | Cicadellidae | 叶蝉科 | 浮尘子 | 14 | flea_beetle | 跳甲 | 菜蚤 |
| 1 | Miridae | 盲蝽科 | — | 15 | tarnished_plant_bug | 花盲蝽 | 美洲牧草盲蝽 |
| 2 | blister_beetle | 芫菁 | 斑蝥 | 16 | black_cutworm | 黑切叶虫 | 黑地老虎 |
| 3 | aphids | 蚜虫 | 腻虫 | 17 | Ampelophaga | 葡萄天蛾 | 葡萄天蛾 |
| 4 | mole_cricket | 蝼蛄 | 土狗子 | 18 | peach_borer | 桃蛀螟 | 桃蠹螟 |
| 5 | Locustoidea | 蝗虫 | 蚂蚱 | 19 | lytta_polita | 丽缘红姬甲 | 绿芫菁近缘 |
| 6 | corn_borer | 玉米螟 | 玉米钻心虫 | 20 | cabbage_army_worm | 甘蓝夜蛾 | — |
| 7 | wireworm | 金针虫 | 叩头虫幼虫 | 21 | alfalfa_plant_bug | 苜蓿盲蝽 | — |
| 8 | legume_blister_beetle | 豆类芫菁 | 豆斑蝥 | 22 | army_worm | 粘虫 | 行军虫 |
| 9 | grub | 蛴螬 | 金龟子幼虫 | 23 | Potosiabre_vitarsis | 光肩星天牛 | 天牛 |
| 10 | flax_budworm | 亚麻蕾虫 | — | 24 | Cicadella_viridis | 绿蝉 | 大青叶蝉 |
| 11 | Prodenia_litura | 斜纹夜蛾 | 莲纹夜蛾 | 25 | yellow_cutworm | 黄切叶虫 | 黄地老虎 |
| 12 | beet_army_worm | 甜菜夜蛾 | 贪夜蛾 | 26 | rice_leaf_roller | 水稻卷叶螟 | 稻纵卷叶螟 |
| 13 | Lycorma_delicatula | 斑衣蜡蝉 | 花蹦蹦 |  |  |  |  |

**挑样本/买标本的建议**：

- mAP50 **≥0.95 的 8 类**最稳：斑衣蜡蝉 .995 / 蝼蛄 .9946 / 叶蝉科 .9856 / 水稻卷叶螟 .9842 / 光肩星天牛 .9803 / 跳甲 .9715 / 蛴螬 .9627 / 葡萄天蛾 .9506。
- 体型大、纹理独特最好拍：蝼蛄、斑衣蜡蝉、葡萄天蛾、蛴螬、水稻卷叶螟、黑切叶虫。
- 同科易混（同场最多留 1–2 只）：叶蝉 ↔ 绿蝉；盲蝽 ↔ 花盲蝽 ↔ 苜蓿盲蝽；芫菁 ↔ 豆类芫菁 ↔ 丽缘红姬甲；螟蛾一家。
- ⚠️ 两个已知坑别选：蝗虫（mAP50 仅 .66，且样本标签有误）、`Potosiabre_vitarsis`（中文名光肩星天牛，图像更像花金龟，标签可疑）。

---

## 12. 系统架构

```
摄像头 / 手机 / 图片
        │
        ▼
   FastAPI 服务（8080 HTTP + 8443 HTTPS）
        ├── YOLOv8s 27 类检测（CPU 100–250ms/张）→ 风险评估 assess_risk
        ├── Web 前端 web/dist（Vue3，离线打包）：/ /eval /stream /ask /spray /system /dashboard /m
        ├── 原生页：/demo 指挥台 · /edge · /classic /eval-classic /m-classic（兜底）
        ├── 本地决策 EdgeJev（ONNX，规则优先）
        ├── RAG 问答（Ollama + 知识库 + 防幻觉）
        └── 执行层 HTTP → ESP32 节点（mist: /start?time=N；json: /spray）
```

---

## 13. 配置说明（`config/configs.yaml`）

| 配置段 | 关键项 | 说明 |
| --- | --- | --- |
| `MODEL` | `WEIGHT` / `DEVICE` / `CONF` / `IMGSIZE` | 权重、设备（`cuda:0`/`cpu`）、阈值、640 |
| `CONFIG.camera_device` | `'vid_0c45'` | **优先**：按设备标识选相机（索引会重排，标识不会） |
| `CONFIG.camera_num` | `0` | 兜底索引（仅在 `camera_device` 留空时生效） |
| `CAMERA` | `probe_timeout: 12.0` | 打开后等首帧秒数；外接 USB 冷启动实测 7.4s，别调小 |
| `SPRAY` | `transport: http` + `http_targets` | 节点地址/协议（`mist` 手写 sketch，`json` 项目固件） |
| `SPRAY.max_duration` | `5` | 单次喷淋上限（雾化片怕连烧） |
| `SPRAY.direct_max_sec` | `300` | 「持续喷雾」时服务器兜底关闭秒数 |
| `EVAL` | `target_samples: 20` / `time_limit_sec: 180` | 评分口径 |
| `PHONE` | `https_port: 8443` | 手机 HTTPS 端口（与 8080 同时监听） |
| `EDGE` | `mode: 'local'` | EdgeJev 本地推理 |

**现场改喷淋节点地址不用改 YAML**：`/demo` 第二张卡「节点地址」→ 保存并应用 → 热更新并落 `config/spray_targets.local.json`（启动时优先于 YAML，不入库）。

---

## 14. 目录结构

```
yolov8/
├─ api_server.py              # FastAPI 服务：页面 + REST + WS + HTTPS
├─ main.py / UI.py            # 桌面 GUI（评分/检测/语音/问答）
├─ 启动演示系统.bat ...        # 现场双击入口
├─ config/configs.yaml        # 主配置
├─ app/                       # 共享状态、推理、风险评估、评分口径、快照、Edge 适配
├─ serve/                     # SPA 托管 + 演示指挥台 + 原生兜底页 + 手机/评分模板
├─ web/                       # Vue3 前端源码（src）与构建产物（dist，现场直接用）
├─ tool/                      # 相机采集/探针、证书、二维码、绘制与风险工具
├─ llm/                       # RAG 检索、防幻觉、知识库、Ollama 客户端
├─ feedback/                  # 语音、执行反馈、喷淋控制器
├─ edgejev/                   # EdgeJev 决策引擎（Apache-2.0）
├─ scripts/                   # 看板快照、离线看板、Edge 构建、评测、令牌同步
├─ tests/                     # 自检（迷你框架，零依赖，兼容 pytest）
├─ ESP32/                     # 喷洒节点/声光报警固件（PlatformIO）
├─ docs/                      # 答辩大纲 · Docker 方案 · 标本采购单（翻拍续训/验收用）
├─ pictures20/ real_testset20/ # 样本（不入库）
└─ runs/ output/              # 权重与运行产物（截图/报告/看板快照）
```

---

## 15. 测试与自检

```powershell
py -3.14 tests/run_tests.py            # 全量自检：270 passed / 0 failed（含真模型冒烟）
py -3.14 tests/run_tests.py camera_pick  # 只跑名字匹配的用例
py -3.14 scripts/ask_eval.py           # RAG 评测：库内召回 20/20、库外拒答 10/10、越权拦截 4/4
py -3.14 tool/camera_check.py          # 相机问题第一站：枚举 + 索引×后端×格式 + 结论
py -3.14 tool/port_check.py            # 端口/Ollama 自检
```

前端改动后必须重建（现场无 Node）：

```powershell
cd web; npm install; npm run build     # 产物落 web/dist，随仓库提交
py -3.14 scripts/sync_tokens.py        # 设计令牌同步给原生页
```

---

## 16. 常见问题

| 现象 | 处理 |
| --- | --- |
| 相机打不开 | 先 `tool/camera_check.py`；确认没被 GUI/微信/其它标签占用；外接 USB 首次要等 7–15 秒 |
| 报"能打开但 N 秒内没出画面" | USB 带宽/曝光预热问题：换 USB 口（别用扩展坞）或调大 `CAMERA.probe_timeout` |
| 8080 被占用 | `py -3.14 tool/port_check.py --kill`，或换端口 `--port 8090` |
| 手机打不开页面 | 手机和电脑必须同一 WiFi/热点；用启动横幅打印的 IP |
| 手机实时模式起不来 | 必须 HTTPS；首次点「高级 → 继续前往」；HTTPS 没起来就用拍照模式 |
| 语音不响 | 看 `/api/feedback/status` 的 `tts.init_errors`；auto 会挑能出声的后端 |
| 喷淋节点显示离线 | 看 `/demo` 节点地址是否还是占位 IP（`172.23.153.231`）；手写 sketch 没有 `/status`，探活走 `/` |
| 看板空白 | 先 `py -3.14 scripts/build_snapshot.py --samples pictures20` |
| 页面白屏 | 用 `/classic`、`/eval-classic`、`/m-classic` 兜底，或重启服务 |
| 中文乱码 | 控制台 `chcp 65001`；页面不受影响 |

---

## 17. 已知问题（v0.1）

- 页面在部分窄屏/大屏比例下排版仍需微调；
- 评分超过 20 张时只计前 20 张（有意设计），但页面未强提示"已满"；
- EdgeJev 零样本能力弱，只适合固定 3 条指令；
- 自动喷洒交互仍是 v1（自动/手动总闸 + 手动喷 + 停止），阈值/片区选择未做成可配置面板；
- 桌面 GUI 未统一为网站风格（作为备用入口保留）；
- 手机端实时识别的帧率/延迟依赖现场 WiFi，弱网下会掉帧。

---

## 18. 许可与致谢

- 本项目代码见 [LICENSE](LICENSE)。
- `edgejev/` 来自 [yzfly/edgejev](https://github.com/yzfly/edgejev)（Apache-2.0）。
- 字体：更纱黑体 SC / 思源宋体 / 得意黑，均为 SIL OFL 许可，已裁剪为 woff2 随前端打包。
- 感谢队友在 Vue 前端（PR#1）与 EdgeJev 接入（PR#3）上的工作。
