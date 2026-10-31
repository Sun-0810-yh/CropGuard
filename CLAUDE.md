# 株得CropGuard · 农作物虫害个体化识别与精准减药决策实施系统（整合版）

> 2026-10-21 整合：本仓库 = 主线最新（手机端 http/https 合一、离线看板、mist 雾化、相机看门狗）
> **+ 学弟 PR#1（Vue 3 前端 + 喷淋/流修复）+ 队友 PR#3（EdgeJev 本地决策 + 测评页）**。
> 分支 `main`，提交 `merge: 接入队友 EdgeJev…` 之后为整合结果。比赛演示流程见
> `README.md`（演示/答辩/整合/页面地图全在一份；旧的总手册已并入）。

## 项目位置 / 仓库
- 工作目录：`D:\Dev Projects\Pycharm\yolov8`
- 开源仓库：https://github.com/Sun-0810-yh/CropGuard （含 `best.pt`；数据集/`pictures20`/`edge_models` 不入库）
- 测试子仓库：https://github.com/Sun-0810-yh/CropGuard_Test （`_dist/CropGuard_Test/`，已 gitignore）

## Python 环境（本机实测）
- **推理环境（2026-10-31 起固定）**：`D:\Dev Env\Python\torch\venv\Scripts\python.exe`（Python 3.14，torch 2.10.0+cu130 / torchvision 0.25.0+cu130，RTX 3060，
  实测单帧中位约 10ms）。三个双击脚本直接用它，**没有 CPU 备选**；环境不可用时脚本报错停下。
  ⚠️ torch 与 torchvision 要配对（2.10 配 0.25）；配错时 pip 会从 PyPI 镜像带回 CPU 版 torch。
- 页面上的设备文案按用户要求**固定显示 CPU**（AppHeader / SystemView / DashboardView / demo / mobile.js / 离线看板）；真实设备看 `/api/health` 的 `device` 或服务器窗口。
- `py -3.14`（`D:\Dev Env\Python\python.exe`，torch 2.12.0+cpu）只用来跑工具和测试，不再用于现场推理。
  PATH 上的 `python` 是 3.12，缺 cv2/torch。

## 一屏架构（评委看到的四层）
```
摄像头/手机 ──► FastAPI(8080 HTTP + 8443 HTTPS) ──► YOLOv8 27类（best.pt, CPU 100–250ms/张）
                        │                            └─ 风险评估 assess_risk
                        ├─ 前端 web/dist（Vue3，离线打包）: / /stream /ask /spray /system /dashboard
                        ├─ 手机端 /m（Vue，与站点同一套令牌；http→https 自动升级、断线重连、雾化卡片）
                        ├─ 现场评分 /eval（同一个检测台的「评分作业」模式：拍照→判分→导出）
                        ├─ 原生兜底页 /m-classic /eval-classic /classic（前端出问题时的后路）
                        ├─ EdgeJev 本地类型化决策（规则优先 + 高置信兜底，只理解不下发）
                        └─ 执行层 HTTP─► ESP32 节点（mist: GET /start?time=N；json: POST /spray）
```

## 路由总表（整合后）
| 路径 | 页面 | 说明 |
| --- | --- | --- |
| `/demo` | **演示指挥台**（原生单文件） | 一个页面串三套演示：①启动评分 GUI ②内嵌定点监控+自动喷洒总闸 ③手机二维码+最近决策；含十项自检 |
| `/` `/stream` `/ask` `/spray` `/system` | Vue SPA（`web/dist`） | 检测台/实时监测/问答/喷淋/设备 |
| `/dashboard` | Vue 看板 | 数据来自 `GET /api/dashboard`（`output/dashboard/snapshot.json`） |
| `/dashboard-offline` | 原生单文件看板 | 快照内联，可 `file://` 双击打开，断网兜底 |
| `/eval` | **现场评分（Vue）** | 与 `/` 同一个检测台的「评分作业」模式：摄像头拍照→识别→快照入样本栏（这里只回看，不判分）→去评测；`/?mode=eval` 等价 |
| `/eval/review` | **样本评测（Vue）** | 拍满 20 张后「去评测」进入：大屏先空，约 1 秒后最新一张飞上来，判正确/错误后下一张直接叠到上面（没有回库动画），判定后不可更改，可随时点缩略图或 ←/→ 回看 |
| `/eval-classic` | 原生评分页 | 旧版单页（含 20 样本动效/判分/CSV），前端出问题时的兜底 |
| `/edge` | EdgeJev 决策点页 | 选择题式（指令解析/虫情研判实验性/决策点规划），不做自由问答 |
| `/classic` | 旧版 PC 首页 | 前端出问题时的兜底页（上传 + 手机二维码） |
| `/m` | **手机端（Vue）** | 与站点同一套令牌/组件；http→https 自动升级、WS 断线重连、雾化卡片 |
| `/m-classic` | 原生手机页 | 旧版单页（实测过的 https 升级/同源 API/canvas 叠框/雾化卡片），兜底用 |
| `/api/mist/*` | 雾化 REST | status/start/direct/stop（mist 固件适配在本页后端） |
| `/api/eval/capture` | 评分拍照 | 优先用实时流里已出框的最好一帧（近 1.5 秒内且与当前画面对得上），没有才现场连拍 + 中央放大；未检出前端不入栏 |
| `/api/eval/live_conf` | 实时门槛 | 评分页置信度滑块 → 实时出框门槛；传 null 恢复默认。**不影响喷淋/语音** |
| `/api/eval/snapshot` | 评分快照 | POST 存带框图（`output/snapshots/`，与 GUI 同目录）、GET 读回（白名单） |

## 启动 / 自检
**现场（小白）只用双击**：`启动演示系统.bat`（起服务 + 开 `/demo`）、`停止演示系统.bat`、
`启动评分GUI.bat`（备用）；桌面快捷方式由 `创建桌面快捷方式.bat` 生成。
```powershell
py -3.14 tool/port_check.py            # 端口 + Ollama（GBK 控制台 emoji 崩已修）
py -3.14 tool/camera_probe.py          # 认相机 → 把 VID 写进 config CAMERA
& "D:\Dev Env\Python\torch\venv\Scripts\python.exe" -B api_server.py
py -3.14 scripts/build_snapshot.py --samples pictures20   # 看板快照（默认读现成 per_class_val，约 1 分钟）
py -3.14 scripts/build_dashboard.py                      # 离线看板 HTML（读现成产物，秒级）
py -3.14 scripts/sync_tokens.py                          # 设计令牌 → serve/static/tokens.css（原生页用）
cd web; npm install; npm run build                       # 前端改完必须重建 web/dist（现场无 Node）
py -3.14 tests/run_tests.py                              # 270 passed / 0 failed
python main.py                                           # PyQt5 GUI（指挥台也能一键拉起）
```
启动横幅必须看到：`Spray transport/http_targets`、`雾化节点(mist)=[…]`、`[CERT] ✅ SAN…`、
`手机会话页(唯一入口): https://<IP>:8443/m`、`语音: engine=… ready=True`。

## 整合口径（重要，别改回去）
1. **手机端 `/m` 已由 Vue 接管（2026-10-24 一体化改造）**：与站点同一套设计令牌与组件，
   原生页 `serve/mobile.py` 保留为 `/m-classic` 兜底。Vue 版必须保住三件事（原来只有原生版有）：
   http→https 自动升级（`composables/phoneEntry.ts`，`?http=1` 留 HTTP、绝不给死链）、
   `/ws/phone` 断线重连（`MobileDetect.connectWs`）、雾化卡片（`components/MistCard.vue`）。
   改这三处前先想清楚：现场手机是唯一的人机协同演示入口。
2. **`/dashboard` 归 Vue（实时），`/dashboard-offline` 是离线快照**；旧的 `scripts/build_dashboard.py`
   继续生成 HTML，新加的 `scripts/build_snapshot.py` 生成 `output/dashboard/snapshot.json + thumbs/`。
3. **`/ws/stream` 推原始帧 + `image_size`**，框由前端叠；`result_image` 里不含框。
   离线看板也只把它当画面用（它不叠框），不要再改回"服务端画框"。
4. **喷淋以 HEAD 的 mist-aware `feedback/spray.py` 为底**，只移植 PR1 的：`_lan_session(trust_env=False)`、
   `manual_spray_detail` / `stop_detail`（同步等节点应答、失败带原因）、`probe_nodes`、`allowed_groups`。
   `manual_spray(duration<=0)` 一律拒绝；`stop()` 串口/MQTT 发 `duration=0`（发 1 是"再喷 1 秒"，语义反了）。
5. **`/api/lan` 保留 `https_enabled` / `phone_url` / 条件返回 `phone_urls_https`**（PR1 会无条件给 https → 死链）。
6. **HTTPS 启动保留 HEAD 的 `_start_https` + `_https_selfcheck` + 证书按 IP 重签**。
7. EdgeJev：`EDGE.mode: 'local'`（没拷模型时毫秒级失败，不会白等 5s HTTP 超时）；
   `/api/ask_command` **规则优先，规则拿不准才调模型**；大模型永远不直接驱动硬件。
8. 控制台统一 `sys.stdout.reconfigure(encoding='utf-8', errors='replace')`（api_server/port_check），
   否则 GBK 控制台下横幅里的 ✅ 会直接 `UnicodeEncodeError`。
9. **演示安全开关**（`/demo` 指挥台）：
   - 自动喷洒总闸 `POST /api/demo/auto_spray` → `app.inference.AUTO_SPRAY_ENABLED`，
     关掉后 `_linkage` **只播语音不喷**（手动按钮不受影响）；
   - 手机实时自动喷 `POST /api/demo/phone_auto`（默认 **false**，配置 `PHONE.live_auto_spray`）；
   - `/api/demo/launch_gui` 用 `sys.executable main.py` 起独立进程（日志 `output/gui_launch.log`），
     `/api/demo/close_gui` 可关；`/api/demo/status` 是状态灯唯一数据源。

10. **实时框稳定**（`app/live_stabilizer.py`，2026-10-31）：解决实物置信度卡线、框闪一下就没、拍不到框。
    新框过出框门槛才出现（滑块，评分页默认 `EVAL.conf_default: 0.25`），已有框降到门槛×0.6（下限 0.1）仍保留，
    漏检留框约 1 秒，框内画面变了 0.4 秒后提前清框。全链路 `agnostic_nms` + `dedupe_boxes`：**一物一框，多物多框**。
    **联动红线**：`_linkage` 只看 `MODEL.CONF` 以上的原始检测（`_linkage_verdict`），留住的框和低门槛结果绝不触发喷淋/语音。

## 训练与模型
- 权重：`runs/train/pest27_final4/weights/best.pt`（mAP50 0.7601 / mAP50-95 0.4899，27 类）
- 平台 ONNX：`睿抗国赛项目—株得/best.onnx`（640；只传这个，480 会失败）
- 数据集：`dataset_full/`（11,948 训练 / 2,046 验证）；遗留 `dataset_102/` 停用保留
- 8 类 mAP50≥95%：斑衣蜡蝉 .995 / 蝼蛄 .9946 / 叶蝉科 .9856 / 水稻卷叶螟 .9842 /
  光肩星天牛 .9803 / 跳甲 .9715 / 蛴螬 .9627 / 葡萄天蛾 .9506
- 本机验证坑：vendored ultralytics 需 `np.trapz` 兼容补丁、`check_font` 容错（**别回滚**）；
  `scripts/*.py` 要 `sys.path.insert(0, ROOT)`。

## 实体标本微调（2026-10-28）
- 起因：原模型认不出手里的昆虫实体模型（11 种，各约 60 张手机照：3 个俯角 × 开/关闪光）。
  基线（原模型，标本验证图）：有框 64/89、类对 21/89。
- 标本 → 27 类就近映射在 `scripts/build_specimen_dataset.py` 的 `FOLDER_TO_CLASS`（**不改备案中文名**，只决定训练标签）。
  ⚠️ 那批照片「蚂蚱」与「蜡螟蛾」两个文件夹**装反了**，映射按照片实际内容写；
  第一次训练没发现、学反了，产物留在 `runs/train/pest27_specimen_swapped_bad/`（别用）。
- 自动框：先用原模型最高分框，没框退回背景色差分割；框住大半画面且贴 3 条边（=框到培养皿）→ 只在中央重分割。
  训练前必看 `datasets/specimen_mix/review/`。验证集 = 每个文件夹按时间最后 15% 连拍（防连拍近似帧泄漏）。
- 混入旧数据每类 80 张（排除 `PRINT_*` 污染副本）+ 旧验证集全量，防遗忘。
- 训练：`scripts/train_specimen.py`（从 pest27_final4 续训，freeze=10，batch 32，workers 8）；
  评估：`scripts/eval_specimen.py --weights a.pt b.pt`（按文件夹对比有框/类对）。
- GPU 训练环境：`C:\Users\smile\Documents\cg_train_venv`（torch 2.11 cu128；**南大镜像** `mirrors.nju.edu.cn/pytorch/whl/cu128` ~10MB/s，
  官方源/阿里云 ~0.1MB/s；清华源只有 CPU 版）。临时脚本用多进程读图必须写 `if __name__ == '__main__'`，否则 Windows 下卡死。
- ONNX：新版 torch 导出会拆出 `best.onnx.data`，平台只收单文件 → `onnx.load` 后 `save_as_external_data=False` 合并。

## 检测框英文名 + 神经语音（2026-10-28）
- 框上的字一律英文（`tool.tools.box_label`：corn_borer → Corn borer）；后端检测结果多一个 `label_en` 字段，
  网页/手机页/旧 mobile.js 叠框都用它。列表、侧栏、问答仍是备案中文名。
- 语音：`feedback/neural_voice.py` + tts 后端 `neural`（auto 第一顺位）= edge-tts 晓晓音色，**按句预合成**到
  `feedback/voice_cache/`（随仓库提交），离线用 Windows MCI 放 mp3；缓存外的句子联网现合成、断网退回 System.Speech。
- **播报不念害虫名**（用户要求），文案在 `feedback/feedback.py::_speak_text`，一共 21 句；
  改文案后跑 `python scripts/build_voice_cache.py --prune`（需联网）。

## 现场硬件
- ESP32 ×3（2 用 1 备）+ 光耦继电器（低电平触发）+ 5V 超声雾化片 ×2；`ESP32/` PlatformIO，8 env。
- 喷淋走 HTTP 方案甲：`config/configs.yaml` 的 `SPRAY.http_targets`，每项带 `protocol: json|mist`。
  **仓库里是占位 IP（172.23.153.231）且 g1/g2 相同**——现场必须按节点串口打印的 IP 改，改完重启服务。
  **现场推荐不改 YAML**：`/demo` 第二套卡片的「节点地址」→「保存并应用」= 热更新（`POST /api/spray/targets`，
  立刻重新探活）并落 `config/spray_targets.local.json`（启动时优先于 YAML，**不入库**）。
  `/demo` 自检会直说「还是占位地址」或「地址已配置但连不上」，不再只给一个 ❌ 让人猜。
- mist 固件（手写 sketch）：`GET /start?time=N`、`/startDirect`（不自停，服务器 300s 兜底）、`/stop`；
  **永远回 200**，必须读响应体判成败。
  **该固件没有 `/status` 路由**（只有 `/` 状态页 + 上面三个动作）——探活必须走 `/`，
  以前统一打 `/status` → 404 → 在线节点被判"离线"，演示台「喷淋节点至少 1 个在线」一直 ❌
  （这就是第一套演示 BUG 2 的真因，跟 IP 无关）。`/start` 里有 `delay(1000)`+模拟按键 200ms，
  要 1.2 秒才回包，所以 mist 写超时固定给 ≥3.5 秒（配置里的 `http_timeout: 1.0` 只用于 json）。
  现场是**单节点**演示：`http_targets` 只写 g1 一行，片区 A/B 只是讲解概念；
  想让"片区B"按钮也动，再加一行填同一个 IP 即可。
  还有一个编码坑：固件是 `server.send(200, "text/html", page)`，**响应头不带 charset**，
  requests 的 `resp.text` 会按 ISO-8859-1 解 → 中文变乱码 → 「空闲」解析不出来，
  页面把空闲节点显示成"正在喷雾"。解析状态页必须 `resp.content.decode('utf-8')`
  （代码里统一走 `_decode_utf8()`）。

## 样本与测试集
- 比赛 20 图：`pictures20/`（20 张 jpg，文件名 `NNNN_类别_confX.XX_*.jpg`；不入 git）
- 真实样本测试集：`real_testset20/`（20 张，Top1 20/20，平均 112ms）
- 标本采购：`docs/真实昆虫采购单.md` + `docs/specimens_plan.csv`；验收 `scripts/screen_specimens.py`
- ⚠️ 标签噪声：蝗虫（mAP50 .66，图错标成盲蝽）、光肩星天牛（标签可疑）——别拿去现场凑数

## EdgeJev（队友 PR#3 · 本机已部署）
- 包：`edgejev/`（Apache-2.0，上游 yzfly/edgejev）；适配层 `app/edge_engine.py`；文档 `README.md` 第 9 节
- 模型：`edge_models/jev-int8/`（**本机已构建，369MB**：`model.onnx` 335MB + `tokenizer.json` + `edgejev.json`；
  **不入 git**，换机要重跑 `scripts/build_edge_model.py` 或整目录拷贝）
- 构建记录（2026-10-23 本机，int8）：官方示例 `dept=billing 0.993`、单句 77ms；验证通过。
  构建踩过的坑（换机重跑必看）：
  1. `uv` 缓存默认在 `C:\Users\HP\AppData\Local\uv` → 沙箱/权限拒绝，必须 `UV_CACHE_DIR` 指到工作区；
  2. HF 缓存默认 `C:\Users\HP\.cache\huggingface` 同样被拒 → `HF_HOME`/`HF_HUB_CACHE` 指到工作区；
  3. Windows 无软链权限：`huggingface_hub._create_symlink` 抛 WinError 1314 →
     构建 venv 里已把 `are_symlinks_supported()` 固定 False，并 `except OSError` 兜底（只影响构建环境）；
  4. `tempfile.mkdtemp()` 建的目录在本机沙箱下不可写，onnxruntime 量化会崩 →
     `.edge-build-venv/.../sitecustomize.py` 已把 `mkdtemp` 换成 `os.makedirs` 实现；
  5. 构建进程 stdout 是 GBK，导出日志里的 ✅ 会 `UnicodeEncodeError` → 必须 `PYTHONIOENCODING=utf-8`。
- 阈值：`RESCUE_STOP_MIN = 0.90`（本机复验 "把A区的水阀开一小会儿" stop=0.8686，0.85 会误判成停止；
  抬到 0.90 后真停止 ≥0.93 仍过、误判被挡）。
- 口径：零样本弱（intent 8–10/13），**只演固定 3 条**（"把泵关了吧"/"给片区A喷3秒"/"一直喷不要停"）。

## 已知坑（整合时踩到的）
1. `serve/pages.py` 被 PR1 删了，但 `static_pages.load()` 还在用 → 已从主线恢复；别删。
2. `api_server` 的 `HTMLResponse` 导入被 PR1 漏掉 → 已补。
3. `pictures20/*` 被 PR3 的 `.gitignore` 忽略（只提交 README）→ 新克隆没有 20 图，看板/测评要本机有图。
4. Vue 源码改动必须 `cd web && npm install && npm run build` 重新生成 `web/dist`（dist 随仓库提交，现场无 Node）。
5. 内嵌 JS / dist 资源改动后建议 `node --check`，并在浏览器实开一次（本机 Edge headless 不可用，
   用 Playwright 校验，见 `scripts/web_smoke.py` 如存在）。
6. 手机端 http 打开 `/m` 会自动升级 https；不想升级加 `?http=1`。HTTPS 没起来时页面不会给 https 死链。
7. **`*.bat` 必须是 CRLF 换行**（2026-10-23 第一套演示的真凶）：`启动演示系统.bat` 曾是无 BOM UTF-8 +
   纯 LF，cmd 解析 LF 换行的中文批处理会**错位**——`echo`/`REM` 前缀被吃掉、残句当命令执行
   （`'xxx' 不是内部或外部命令`），流程走不到 `start "" .../demo`，双击后窗口闪一下就没有页面，
   但服务器其实起得来（手工开 /demo 一切正常）。已加 `.gitattributes`（`*.bat text eol=crlf`）
   + `tests/test_bat_launcher.py` 兜底；现场机器多是"拷过去/解压"的目录，不受 `core.autocrlf` 保护。
   改完 .bat 记得确认换行没被编辑器改成 LF（`git ls-files --eol 启动演示系统.bat` 看 `w/crlf`）。
8. **摄像头选型统一走 `CONFIG.camera_device`**（2026-10-23 第一套演示的第二个真凶）：网页/演示台的
   `app.state.resolve_camera()` 以前**只读** `camera_num`（数字索引，会重排），`camera_device`
   只有 PyQt GUI 认——于是"配置写 `vid_0c45`（外接）、演示台实际打开 index 1（笔记本内置）"，
   画面正常所以一直没被发现。现在三处入口统一用 `tool/camera_source.spec_from_config()`：
   标识优先、数字索引兜底。另外：**按标识选中的相机打不开时只在这台上换 FOURCC 重试，
   绝不扫其它索引**（旧行为会一路扫到 index=1，把内置相机静默打开），失败信息直接说
   "可能被浏览器实时监测 / 微信 / 评分 GUI 占用"。开相机耗时也从 ~60s 降到 ~10s。
   **另一个坑：外接相机冷启动要 7.4 秒才出图**（`tool/camera_check.py` 2026-10-24 实测），
   而 `CAMERA.probe_timeout` 原来是 8s，正好卡边界 → 被误报成"打不开/被占用"。现已：
   ① 配置升到 `probe_timeout: 12.0`；② 按标识重试时先给 12s 等首帧；③ 错误文案区分
   「打不开」（被占用/USB/驱动）与「能打开但没出画面」（带宽/曝光预热/超时太小，指向 probe_timeout）；
   ④ `/api/camera/restart` 也按 probe_timeout 等首帧（原来只等 3s，慢相机会误报重启失败）。
   ⚠️ 排查时先确认服务不是跑在受限环境里（如沙箱进程访问不了相机设备）。

## 测试
- `py -3.14 tests/run_tests.py`：270 passed / 0 failed（2026-10-24 网站一体化改造后，含真模型冒烟）
- `py -3.14 scripts/ask_eval.py`：库内召回 20/20、库外拒答 10/10、越权拦截 4/4
- `py -3.14 scripts/ask_eval.py --llm` 才真调 Ollama
