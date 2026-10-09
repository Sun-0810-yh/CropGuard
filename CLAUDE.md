# CropGuard 农智云 害虫检测系统

## 项目位置
- 工作目录: D:\Dev Projects\Pycharm\yolov8
- GitHub源码(仅代码): D:\Dev Projects\Pycharm\Pest-detection-master
- GitHub仓库: https://github.com/Stara-AI/Pest-detection
- 本项目仓库(开源): https://github.com/Sun-0810-yh/CropGuard（公开，含 best.pt，数据集未上传）
  - ⚠️ 2026-10-08 清理删掉了被 git 跟踪的 runs/train/pest27_final4/weights/best.onnx（与 睿抗国赛项目—农智云/best.onnx 同一份）。要恢复公开仓库里的 ONNX：`git restore runs/train/pest27_final4/weights/best.onnx`，或对比赛目录那份 `git add -f`

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
- ONNX: 睿抗国赛项目—农智云/best.onnx（640，平台可用；runs 下的重复副本已于 2026-10-08 删除，两份 SHA256 相同）
- 续训: python train_640.py --resume

## 关键文件
- 训练脚本: train_640.py（摄像头场景增强：亮度/色相/旋转±25°/透视/缩放；从头训练需 COCO 权重 yolov8s.pt）
- 翻拍续训: train_finetune.py（复制比赛图x3份加PRINT_前缀 + 强退化增强，15轮）
- 配置: config/configs.yaml（WEIGHT 已指向 pest27_final4；AI.active_model=local 本地 Ollama qwen2.5:7b，离线合规）
- GUI入口: main.py（PyCharm 运行或 `python main.py`），界面类 UI.py
- 推理工具: tool/tools.py
- 本地LLM: llm/local_llm.py（Ollama，不可用时降级 knowledge_base 兜底）
- 信号反馈: feedback/feedback.py（pyttsx3 语音 + 串口声光）；喷淋控制: feedback/spray.py（serial/http/mqtt）
- 硬件固件: ESP32/（PlatformIO 工程，8 个 env，见 ESP32/README.md）

## 启动方式
- GUI: 运行 main.py（PyCharm 或 `python main.py`）
- 训练: `python train_640.py`（新建）或 `python train_640.py --resume`（续训）
- 喷淋联调: `python api_server.py`（**唯一**接 SprayController 的入口；GUI 只做语音+串口反馈）
- 固件: VSCode 打开 ESP32/ 目录（不是仓库根）→ PlatformIO 选 env → Upload

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
  芯片 `ESP32-D0WD-V3` rev v3.1，MAC `20:9b:a9:97:8c:60`（做 ESP-NOW 单播时登记用），
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
    节点 `http_node_g1` DHCP 拿到 `10.141.169.231`（网关 `10.141.169.143`、RSSI −37dBm）→
    `GET /status` 200；`POST /spray {"group":"g1","duration":2}` → 串口 `SPRAY_ON group=g1 duration=2` + `SPRAY_OFF`
    （**继电器吸合 2 秒后自动断开**）；`{"group":"g2"}` → `SKIP group=g2`（**片区隔离，继电器不动**）；
    `{"group":"g1","duration":60}` → 实际 `duration=5`（**安全钳制**）；空 body → 400、未知路径 → 404。
    ⚠️ **关键坑（已解）**：手机热点若处于「共享已连接的 WiFi / 桥接」模式，会把上游网络地址直接发给接入设备
    （本次是校园网 `10.141.169.x`，DHCP 服务器 `10.141.169.143`）。此时节点用写死的 `192.168.43.61`
    会「`WIFI_OK` 但服务器 ping 不到、curl 超时」→ 已加 `USE_DHCP` 开关，两个节点 env 默认 `-DUSE_DHCP=1`，
    **节点 IP 以串口打印的为准**，并同步填进 `SPRAY.http_targets`（现已填 `10.141.169.231`）。
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
