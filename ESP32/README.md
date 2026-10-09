# CropGuard 农智云 · ESP32 固件工程（PlatformIO）

把项目里「识别 → 物理执行 / 信号反馈」那一段落到 ESP32 开发板上的固件工程。
**不是重写**：这里的每个角色都是从项目里已经跑通的 Arduino `.ino` 移植过来的，
协议、端点、安全行为一一对齐，只把工程形态换成 PlatformIO（VSCode 里点按钮就能编译烧录）。

> 那些原始 `.ino` 已于 **2026-10-08 目录清理时删除**，本工程现在是**唯一固件源**
> （迁移对照表见 §8）。各文件头部注释里保留了移植来源，便于回溯行为差异。

```
                    ┌────────── 服务器笔记本 ──────────┐
                    │ api_server.py :8080             │
                    │   YOLO 推理 + assess_risk       │
                    │   risk_map 查表 -> 喷淋时长      │
                    └───┬──────────────────────┬──────┘
        WiFi HTTP POST  │                      │  USB 串口（9600，单字符）
        {"group","duration"}                    │  'R' / 'Y' / 'G'
                        ▼                      ▼
              ┌──────────────────┐    ┌────────────────────┐
              │ 片区A / 片区B 节点 │    │ 声光报警器（env:alarm）│
              │ env:http_node_g* │    │ 红灯+蜂鸣/黄/绿       │
              │ GPIO13 -> 继电器  │    └────────────────────┘
              └──────────────────┘
```

---

## 1. 三步上手

### 第 1 步：填 WiFi 热点

`ESP32/include/cg_secrets.h` 里改两个值（或按里面的说明用 `cg_secrets.local.h` 覆盖，后者不会进 git）：

```cpp
#define WIFI_SSID "你的手机热点名"
#define WIFI_PASS "热点密码"
```

顺手确认网段：`ESP32/include/cg_config.h` 里默认 `NET_A/B/C = 192/168/43`，
即节点 IP 是 `192.168.43.61`（片区A）和 `.62`（片区B）。

- 安卓热点常见 `192.168.43.x` → 不用改。
- iPhone 热点常见 `172.20.10.x` → 把 `NET_C` 改成 `20`，并且要同步改 `config/configs.yaml` 的 `SPRAY.http_targets`。
- 手机上看热点网关地址最准：热点设置里一般能看到「网关/IP 地址」，取前三段即可。

### 第 2 步：在 VSCode 里打开本目录（重要）

PlatformIO 要求 **`platformio.ini` 就是 VSCode 打开的文件夹根目录**。
所以不要打开整个 `yolov8` 仓库，而是：

> VSCode → `File` → `Open Folder…` → 选 `D:\Dev Projects\Pycharm\yolov8\ESP32`

打开后左下角出现 PlatformIO 图标，点它 → `Project Tasks` 里能看到全部 env。

### 第 3 步：选 env，编译 + 烧录

页面左侧 `Project Tasks` → 选某个 env → `General → Upload`；
或点底部状态栏的 ✓（编译）、→（烧录）、🔌（串口监视器）。
命令行等价写法（`pio` 不在 PATH，用全路径）：

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -e http_node_g1 -t upload
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" device monitor -e http_node_g1
```

**第一次烧录建议先烧 `selftest`**（不需要接继电器、不需要热点，还能验证板子与工具链都正常）：

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -e selftest -t upload --upload-port COM3
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" device monitor -e selftest
```

期望看到 `SELFTEST_PASS`（失败 0 项）。串口监视器是 115200。

> ⚠️ `--upload-port` 换成你实际的 COM 口（设备管理器里看 CH340/CP2102 那个）。
> 若上传在 460800 波特率下失败/超时（CH340 常见），把 `platformio.ini` 的 `[env]` 里
> `upload_speed` 改成 `115200` 再试。

> ℹ️ 本工程开了 `build_cache_dir`（8 个 env 共享框架目标文件），**重编一个 env 只要几秒**，
> 代价是 `.pio/build/` 下**只保留最近编译的那个 env**，看不到其它 env 的 `firmware.bin` 是正常的
> —— 用 `-t upload` 时会自动重编再烧。

> **首次编译会联网下载工具链**（xtensa 编译器 + Arduino 核心，数百 MB），要几分钟。
> 下载完成后编译只要十几秒。

---

## 2. env 一览：哪块板烧哪个

| env | 角色 | 烧给谁 | 串口监视器波特率 |
| --- | --- | --- | --- |
| `http_node_g1` | **方案甲 HTTP 节点**，片区A（`g1`，IP `…61`） | 板1 | 115200 |
| `http_node_g2` | 方案甲 HTTP 节点，片区B（`g2`，IP `…62`） | 板2 | 115200 |
| `alarm` | 声光报警器（红灯+蜂鸣 / 黄 / 绿） | 板3（USB 常连服务器） | **9600** |
| `selftest` | 板载协议自检（不用接任何外设） | 任意板（验完换回业务 env） | 115200 |
| `espnow_node_g1` / `_g2` | 方案丙 ESP-NOW 节点（备用） | 备用 | 115200 |
| `espnow_gateway` | 方案丙 USB 串口网关（备用/降级） | 备用 | 115200 |
| `wifi_gateway` | 方案丙无线网关（备用，**要求 AP 能锁信道 1**） | 备用 | 115200 |

**为什么主路线是方案甲**：现场用手机热点当 AP，而手机热点的 2.4G 信道不可控；
方案丙（ESP-NOW）要求网关与节点同信道，会收不到包。方案甲直接按 IP 寻址，
彻底摆脱信道约束，而且两个片区天然故障隔离（见 `国赛方案.md` §5）。

三块板的分工建议：板1/板2 做喷洒节点（现场用充电宝供电 → 真无线），板3 做声光报警（USB 取电）。
要现场演示 ESP-NOW 亮点时，把板3 改烧 `espnow_gateway`（USB 版，不依赖信道）。

---

## 3. 接线：喷洒节点（每片区一套，零焊接）

**现场已确认的硬件**：ESP32 Dev Module + 1 路 **3.3V 光耦隔离继电器（低电平触发）** + **5V 超声雾化片 + 驱动板**。

```
ESP32 GPIO13 ──────────► 继电器 IN          （低电平吸合；上电默认断开）
继电器 VCC   ──────────► 独立 3.3V / 5V 电源 +   （★ 见下方注意事项，别从 GPIO 取）
ESP32 GND    ──────────► 电源 −             （与 ESP32 共地）
5V 电源 +    ──────────► 继电器 COM
继电器 NO    ──────────► 雾化驱动板 VCC +
驱动板 GND   ──────────► 5V 电源 −          （与 ESP32 共地）
```

- **必须用 COM + NO**（常开）：接 `NC` 会"上电就喷"。
- **必须共地**：雾化电源负极与 ESP32 GND 连一起，否则继电器一动作 ESP32 就复位。
- **雾化模块独立 5V/2A 供电**：超声雾化片 300–400mA，从 ESP32 板载 5V 取电会烧板或反复重启。
- **继电器 VCC 别"顺便"从 ESP32 板载 3.3V 取**（线圈约 70–90mA）。优先接独立 3.3V/5V；
  只有 USB 供电且无独立电源时才可临时从板载 3.3V 取，取完必须看串口——
  一旦出现 `Brownout detector was triggered` 就立刻改独立供电。
- 极性已确认是**低电平触发**，与 `cg_config.h` 默认 `RELAY_ACTIVE = LOW` 一致，**不用改代码**。
  万一现场反了：在该 env 的 `build_flags` 里加 `-DRELAY_ACTIVE=HIGH`，同样不用改源码。

### 3.1 超声雾化片的两个真实风险（必读）

1. **干烧**：雾化片必须浸没在水中；通电但无水或水位过低，几秒内就会烧片。
   固件侧的保护：上电继电器默认断开、单次硬上限收紧到 **5 秒**（两个节点 env 都带 `-DMAX_SPRAY_SEC=5`）、
   自动喷淋按 `SPRAY.min_interval` 节流。**加水这一步只能靠人** —— 每次演示前确认水面没过雾化片。
2. **雾看不见**：超声雾化在赛场灯光下几乎拍不出来，会让"外部设备联动"的物理证据变弱。
   零成本改善：深色背板 + 侧向补光斜射 + 摄像头/手机近距离机位，让雾气在暗背景前可见；
   或把雾化片放在浅盘里让雾气贴着水面铺开。

## 4. 接线：声光报警器（暂不做，代码保留）

> 当前方案**不做声光报警**：第三块 ESP32 留作备件，`alarm` env 只保编译、不现场烧。
> 对应的"场景化反馈"分数由 **喷淋物理动作 + 语音播报** 承担（规则是"物理动作 **或** 信息反馈"二选一）。
> 下面接法保留，将来要做时照接即可。

```
ESP32 GPIO25 ── 220Ω ──► 红灯 ──► GND
ESP32 GPIO26 ── 220Ω ──► 黄灯 ──► GND
ESP32 GPIO27 ── 220Ω ──► 绿灯 ──► GND
ESP32 GPIO32 ─────────► 蜂鸣器 ──► GND      （或经三极管模块）
```

- 为什么换引脚：原 Arduino 版固件（`arduino_alarm.ino`，已于 2026-10-08 删除）用 Arduino 的 D9–D12，其中 **D12 对应 ESP32 的 GPIO12**
  （MTDI 上电跳线，被拉高会导致启动失败甚至变砖），所以报警灯改用 25/26/27/32。
- 蜂鸣器若从 GPIO 直取电流，可能把板子拉欠压复位 → 优先用**带三极管/光耦的蜂鸣器模块**，或串限流电阻。
- 上电默认绿灯（与 `arduino_alarm.ino` 一致）；三个灯是「亮一个」互斥语义。

---

## 5. 服务器端要改的地方

`config/configs.yaml`：

```yaml
FEEDBACK:
  enabled: true
  serial_port: 'COM5'    # 声光报警板的 COM 口；没有硬件时保持 null，只语音播报
  baud: 9600

SPRAY:
  enabled: true
  transport: 'http'      # 方案甲
  http_targets:
    - { group: 'g1', url: 'http://192.168.43.61/spray' }   # 片区A 节点
    - { group: 'g2', url: 'http://192.168.43.62/spray' }   # 片区B 节点
```

⚠️ **只有 `api_server.py` 会驱动喷淋**（`main.py` 的 PyCharm GUI 只接语音 + 串口反馈）。
所以喷淋联调要走：

```powershell
python api_server.py
# 启动日志里应出现: [INFO] Spray transport=http available=True
```

---

## 6. 验证（从「不用外设」到「整套闭环」）

### 6.1 先跑自检（不用接继电器、不用热点）

```powershell
pio run -e selftest -t upload
pio device monitor
```

期望：`SELFTEST_PASS`（失败 0 项）。
它验证的是 JSON 解析与 `feedback/spray.py` 下发载荷完全对齐（含空值、负数、截断串、
超大数饱和、200 字节长帧等边界）。**这一步不过就别接水泵**。

### 6.2 单节点：串口 + 浏览器 + 指令

```powershell
pio run -e http_node_g1 -t upload
pio device monitor            # 期望: WIFI_OK IP=192.168.43.61
                              #       NODE_READY id=1 group=g1  POST http://192.168.43.61/spray
```

浏览器打开 `http://192.168.43.61/` → 能看到分组、IP、RSSI、状态、已执行次数。然后（PowerShell 里用 `curl.exe`，别用 `curl`）：

```powershell
curl.exe http://192.168.43.61/status
curl.exe -X POST http://192.168.43.61/spray -H "Content-Type: application/json" -d '{\"group\":\"g1\",\"duration\":2}'
curl.exe -X POST http://192.168.43.61/stop
```

| 发什么 | 期望现象 |
| --- | --- |
| `{"group":"g1","duration":2}` | 串口 `SPRAY_ON group=g1 duration=2`，继电器吸合 2 秒后 `SPRAY_OFF` |
| `{"group":"g2","duration":2}` | 串口 `SKIP group=g2`，**继电器不动**（分组隔离） |
| `{"group":"all","duration":3}` | 两个节点都喷 3 秒 |
| `{"duration":60}` | 实际只喷 30 秒（安全钳制） |
| `{"duration":0}` 或 `POST /stop` | 立刻断 |

### 6.3 整套闭环

1. `configs.yaml` 按 §5 改好 → `python api_server.py` → 页面点「全体喷 / G1 组喷 / G2 组喷」→ 只有目标片区出水。
2. 自动闭环：片区内放高风险样本 → 仅该片区自动喷（时长 = `risk_map`），同时红灯 + 蜂鸣 + 语音播报。
3. 真无线：拔掉节点 USB，改充电宝供电 → 页面仍可访问、仍能触发。
4. 兜底：断开喷淋链路，语音 + 声光仍正常（保住场景化反馈分）。

---

## 7. 排错表

| 现象 | 最可能原因 | 处理 |
| --- | --- | --- |
| `pio run` 报 `platforms.lock` / 权限错 | PlatformIO 包目录不可写 | 用普通用户身份跑，或给 `%USERPROFILE%\.platformio` 写权限 |
| 找不到 COM 口 | 缺 CH340 / CP2102 驱动，或线是纯充电线 | 装驱动；换**带数据**的 USB 线 |
| 编译报 `undefined reference` / API 不存在 | 平台被升到 Arduino 核心 3.x | 把 `platform = espressif32@7.1.3` 改回钉死值 |
| 串口乱码 | 波特率不匹配 | `alarm` 是 9600，其它 env 是 115200（`monitor_speed` 已配好） |
| 上电就喷，停不下来 | 继电器接了 NC，或触发极性反了 | 改接 COM+NO；`-DRELAY_ACTIVE=HIGH` |
| 收到指令但泵不转 | 泵电源没接、COM/NO 接错 | 量继电器端子；确认泵独立 5V/2A |
| 继电器一动作 ESP32 就重启 | 未共地，或泵从板载 5V 取电 | 泵负极与 ESP32 GND 相连；泵独立供电 |
| 浏览器打不开节点页面 | 手机/服务器/节点不在同一网段 | 三者在同一热点；核对 `cg_config.h` 的 `NET_*` |
| `api_server` 日志 `available=False` | `http_targets` 为空 | 按 §5 填两条地址 |
| 报警板被卡在复位态 | 某些 CH340 板子在串口打开时会拉 DTR/RTS 复位 | 报警板改用不带自动复位的 USB-TTL；或让 `pyserial` 打开时不置 DTR/RTS（需改 `feedback.py`，本次未改） |
| 喷淋让推理卡顿 | 写操作阻塞 | 已异步；仍卡就调大 `SPRAY.min_interval` |

---

## 8. 与 Arduino `.ino` 原始固件的关系

原始的 5 个 `.ino` 已于 **2026-10-08 目录清理时删除**，内容已完整移植进本工程：

| 原 `.ino`（已删） | 现在的 env |
| --- | --- |
| `feedback/arduino_alarm.ino` | `alarm` |
| `feedback/spray_executor/http_node/http_node.ino` | `http_node_g1` / `http_node_g2` |
| `feedback/spray_executor/spray_node.ino` | `espnow_node_g1` / `espnow_node_g2` |
| `feedback/spray_executor/gateway.ino` | `espnow_gateway` |
| `feedback/spray_executor/wifi_gateway/wifi_gateway.ino` | `wifi_gateway` |

- 行为差异只有两处：报警灯引脚从 Arduino 的 D9–D12 换成 ESP32 安全脚位（见 §4）、蜂鸣器改用 ledc。
- 想找回原 `.ino`：`git restore feedback/`（其中 `http_node.ino` 从未提交过，无法恢复，但已完整移植）。
- 注意：**不再有"Arduino IDE 退路"**——固件只在本工程里维护。

## 9. 已知限制

- 只做了「识别 → 执行」的下行链路；节点不回传传感器数据（不需要，喷淋本身有 `/status` 可探活）。
- `espnow_node` / `espnow_gateway` / `wifi_gateway` 是备用路线：编译可用，现场按需再烧。
- 不做 OTA、不做 WiFiManager 配网门户、不做 MQTT（`configs.yaml` 的 mqtt 分支保留未启用）。
- 想让 HTTP 节点在 USB 连着服务器时**顺便当声光报警器**（省一块板），
  在 `platformio.ini` 那个 env 的 `build_flags` 末尾加 `-DENABLE_SERIAL_ALARM=1`；
  代价是改用充电宝后该报警链路失效。
