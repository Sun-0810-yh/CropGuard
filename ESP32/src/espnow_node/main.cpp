// ============================================================
// 方案丙 · ESP-NOW 喷洒节点（移植自 spray_node.ino；原 .ino 已于 2026-10-08 删除）
// ============================================================
// 链路：网关 --ESP-NOW--> 本节点 --GPIO13--> 继电器 --> 水泵
// 节点不连任何 AP：射频停在 ESPNOW_CHANNEL（默认 1），
// 因此 AP/网关侧的信道必须也是 1，否则收不到包。
//   —— 手机热点做不到锁信道，所以主路线是方案甲（http_node）；
//      本固件保留用于「能锁信道 1 的路由器」场景与 ESP-NOW 技术演示。
//
// 节点身份来自 -DNODE_ID（见 platformio.ini：espnow_node_g1 / _g2）。
//
// 编译：pio run -e espnow_node_g1 -t upload
// ============================================================

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <string.h>

#include "cg_config.h"
#include "cg_protocol.h"
#include "cg_relay.h"

#if !defined(ESP_ARDUINO_VERSION_MAJOR)
#  include <esp_arduino_version.h>
#endif

cg::Relay relay;

uint8_t broadcastMac[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// ---------- 收包处理：解析 -> 过滤分组 -> 驱动继电器 ----------
void handleCommand(const uint8_t* mac, const uint8_t* data, int len) {
    (void)mac;

    char json[256];
    const int n = (len < (int)sizeof(json) - 1) ? len : (int)sizeof(json) - 1;
    memcpy(json, data, (size_t)n);
    json[n] = '\0';

    char group[16] = {0};
    cg::parseGroup(json, group, sizeof(group));
    const int duration = cg::parseDuration(json);

    // 仅 "all" 或本节点分组执行
    if (strcmp(group, "all") != 0 && strcmp(group, NODE_GROUP) != 0) {
        Serial.print("SKIP group=");
        Serial.println(group);
        return;
    }

    if (duration > 0) {
        relay.start(duration);
    } else {
        relay.stop();   // duration<=0 视为立即停止
    }
}

// ---------- ESP-NOW 回调：核心 2.x 与 3.x 的签名不同，双分支兜底 ----------
#if ESP_ARDUINO_VERSION_MAJOR >= 3
void onDataRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
    handleCommand(info != nullptr ? info->src_addr : nullptr, data, len);
}
#else
void onDataRecv(const uint8_t* mac, const uint8_t* data, int len) {
    handleCommand(mac, data, len);
}
#endif

void setup() {
    Serial.begin(115200);
    delay(200);

    relay.begin();   // 上电先断开继电器

    WiFi.mode(WIFI_STA);
    WiFi.disconnect();   // 不连 AP：射频停在 ESPNOW_CHANNEL

    if (esp_now_init() != ESP_OK) {
        Serial.println("ESPNOW_INIT_FAIL");
        return;
    }
    esp_now_register_recv_cb(onDataRecv);

    // 添加广播 peer，以便接收网关的广播帧
    esp_now_peer_info_t peer;
    memset(&peer, 0, sizeof(peer));
    memcpy(peer.peer_addr, broadcastMac, 6);
    peer.channel = ESPNOW_CHANNEL;
    peer.encrypt = false;
    esp_now_add_peer(&peer);

    Serial.print("NODE_READY id=");
    Serial.print(NODE_ID);
    Serial.print(" group=");
    Serial.print(NODE_GROUP);
    Serial.print("  channel=");
    Serial.println(ESPNOW_CHANNEL);
    Serial.println("提示: 本机 MAC（做单播时登记到网关的 nodeTable）= " + WiFi.macAddress());
}

void loop() {
    relay.poll();   // 非阻塞计时：到点断继电器
    delay(5);
}
