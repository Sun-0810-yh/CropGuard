#pragma once

// ============================================================
// 板卡公共配置：身份 / 网段 / 引脚 / 安全上限
// ------------------------------------------------------------
// 所有量都可被 platformio.ini 的 build_flags 用 -D 覆盖，
// 所以现场调参（换引脚、换网段、继电器极性反了）都不需要改这里。
//
// 引脚选型原则（ESP32-WROOM-32）：
//   避开 GPIO6-11    —— 接内部 flash，占用即启动失败
//   避开 GPIO12      —— MTDI 上电跳线，拉高会切 1.8V flash 导致变砖
//   避开 GPIO34-39   —— 仅输入，不能驱动继电器/蜂鸣器
// ============================================================

#include <Arduino.h>

#include "cg_secrets.h"

// ---------------- 板卡身份：由 -DNODE_ID=n 推导 ----------------
#ifndef NODE_ID
#  define NODE_ID 1
#endif

#if NODE_ID == 1
#  define NODE_GROUP "g1"
#  define NODE_NAME "片区A"
#  ifndef NODE_IP_LAST
#    define NODE_IP_LAST 61
#  endif
#elif NODE_ID == 2
#  define NODE_GROUP "g2"
#  define NODE_NAME "片区B"
#  ifndef NODE_IP_LAST
#    define NODE_IP_LAST 62
#  endif
#else
#  define NODE_GROUP "g9"
#  define NODE_NAME "未命名节点"
#  ifndef NODE_IP_LAST
#    define NODE_IP_LAST 69
#  endif
#endif

// ---------------- 网段 ----------------
// 安卓热点常见 192.168.43.x；iPhone 热点常见 172.20.10.x（此时把 NET_C 改成 20）。
// 改这一处，所有 env 一起生效；IP 末位仍由 NODE_ID 决定（61 = 片区A，62 = 片区B）。
#ifndef NET_A
#  define NET_A 192
#endif
#ifndef NET_B
#  define NET_B 168
#endif
#ifndef NET_C
#  define NET_C 43
#endif
#ifndef NET_GW_LAST
#  define NET_GW_LAST 1
#endif
// 方案丙无线网关自己的 IP 末位（须与 configs.yaml 的 http_targets 一致）
#ifndef GW_IP_LAST
#  define GW_IP_LAST 60
#endif

// ---------------- 静态 IP 还是 DHCP ----------------
// 1 = DHCP 自动获取（推荐：热点网段不确定时最省事，串口会打印拿到的真实 IP）
// 0 = 用上面的 NET_A/B/C + NODE_IP_LAST 静态 IP（网段确定时更可预测）
//
// 实测教训（2026-10-08）：手机热点若处于「共享已连接的 WiFi / 桥接」模式，
// 会把上游网络（如校园网 10.141.169.x）的地址直接发给接入设备。
// 此时板子用写死的 192.168.43.61 仍能连上 WiFi 并打印 WIFI_OK，
// 但这个地址在网里根本不存在 -> 服务器 ping 不到、curl 超时。
// 判断方法：看服务器从哪个 DHCP 服务器拿地址（手机当热点时应是 x.x.x.1）。
#ifndef USE_DHCP
#  define USE_DHCP 0
#endif

// ---------------- 喷洒节点硬件 ----------------
#ifndef RELAY_PIN
#  define RELAY_PIN 13           // 继电器 IN
#endif
#ifndef RELAY_ACTIVE
#  define RELAY_ACTIVE LOW       // 多数 1 路光耦模块为低电平触发；极性反了就改成 HIGH
#endif
#ifndef RELAY_IDLE_LEVEL
#  define RELAY_IDLE_LEVEL ((RELAY_ACTIVE) == LOW ? HIGH : LOW)
#endif
// 雾化模块的 S 信号脚（HE-30 模块的信号输入；官方接法是接单片机的一个 IO）。
// 设成 -1 = 不启用（有些接法只靠继电器通断电源就能工作）。
// ⚠️ 默认 D33：**绝不要用 D12** —— 那是 ESP32 的 MTDI 上电跳线脚，
//    上电被拉高会让芯片切到 1.8V flash 电压，导致启动失败甚至变砖。
#ifndef SPRAY_SIG_PIN
#  define SPRAY_SIG_PIN 33
#endif
// S 的有效电平：默认与继电器一致（喷淋时拉低）。若实测相位相反就改成 HIGH。
// 两个极性各自独立，因为继电器是低电平有效、模块 S 的有效电平未知。
#ifndef SIG_ACTIVE
#  define SIG_ACTIVE LOW
#endif
#ifndef SIG_IDLE_LEVEL
#  define SIG_IDLE_LEVEL ((SIG_ACTIVE) == LOW ? HIGH : LOW)
#endif
#ifndef LED_PIN
#  define LED_PIN 2              // 板载状态灯
#endif

// ---------------- 声光报警硬件 ----------------
// 语义与 feedback/arduino_alarm.ino 一致，引脚换成 ESP32 安全脚位
// （原来是 Arduino 的 D9/D10/D11/D12，在 ESP32 上 GPIO12 有上电跳线风险）。
#ifndef ALARM_RED_PIN
#  define ALARM_RED_PIN 25
#endif
#ifndef ALARM_YELLOW_PIN
#  define ALARM_YELLOW_PIN 26
#endif
#ifndef ALARM_GREEN_PIN
#  define ALARM_GREEN_PIN 27
#endif
#ifndef ALARM_BUZZER_PIN
#  define ALARM_BUZZER_PIN 32
#endif
#ifndef ALARM_BUZZER_FREQ
#  define ALARM_BUZZER_FREQ 1000
#endif
#ifndef ALARM_BAUD
#  define ALARM_BAUD 9600        // 必须与 configs.yaml 的 FEEDBACK.baud 一致
#endif
#ifndef CG_ALARM_VERBOSE
#  define CG_ALARM_VERBOSE 1     // 串口回显 ALARM=R/Y/G，便于现场核对
#endif

// ---------------- 安全上限 / 服务端口 ----------------
#ifndef MAX_SPRAY_SEC
#  define MAX_SPRAY_SEC 30       // 单次喷淋硬上限，防误触
#endif
#ifndef HTTP_PORT
#  define HTTP_PORT 80
#endif
#ifndef ESPNOW_CHANNEL
#  define ESPNOW_CHANNEL 1       // 方案丙：须与 AP 信道一致（手机热点做不到，故主路线走方案甲）
#endif

// ---------------- BLE 冗余通道（Phase 3，此处只预留接口） ----------------
// 打开后（-DENABLE_BLE=1）节点除 WiFi 外还广播 BLE GATT 服务，
// 服务器可用 Python bleak 在 WiFi/热点不可用时下发同样的 JSON 指令。
// 实现见 cg_ble.h（Phase 3 落地）；**只用于方案甲 HTTP 节点**，
// ESP-NOW 节点不要开（BLE + ESP-NOW + WiFi 三开有已知共存毛病）。
#ifndef ENABLE_BLE
#  define ENABLE_BLE 0
#endif

// ---------------- 可选：让 HTTP 节点同时当声光报警器 ----------------
// 打开后（-DENABLE_SERIAL_ALARM=1），节点除 WiFi 收 HTTP 外，
// 还从 USB 串口读 'R'/'Y'/'G' 驱动三色灯；两条链路互不冲突。
// 代价：该节点必须一直 USB 连着服务器，改用充电宝后报警失效。
#ifndef ENABLE_SERIAL_ALARM
#  define ENABLE_SERIAL_ALARM 0
#endif
