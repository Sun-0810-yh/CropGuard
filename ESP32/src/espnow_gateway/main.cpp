// ============================================================
// 方案丙 · ESP-NOW 网关（移植自 gateway.ino；原 .ino 已于 2026-10-08 删除）
// ============================================================
// 链路：服务器 --USB 串口--> 本网关 --ESP-NOW--> 喷洒节点
// 串口协议（每行一条 JSON，与 feedback/spray.py 约定）：
//   {"group":"all","duration":3}   -> 全体节点喷 3 秒（广播，默认路径）
//   {"group":"g1","duration":2}    -> g1 组节点喷 2 秒（广播 + 节点侧过滤）
//   {"node":1,"duration":2}        -> 单播给 1 号节点（需在下面登记 MAC）
//
// 服务器端 config/configs.yaml：
//   SPRAY:
//     transport: 'serial'
//     serial_port: 'COM4'      # 本网关的 COM 口
//     baud: 115200             # 与这里一致
//
// ⚠️ 本路线要占一根 USB 线。国内赛「田间无线」口径下，主路线是方案甲
//    （http_node）；本固件作为降级/演示备用保留。
//
// 编译：pio run -e espnow_gateway -t upload
// ============================================================

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <string.h>

#include "cg_config.h"
#include "cg_protocol.h"

#if !defined(ESP_ARDUINO_VERSION_MAJOR)
#  include <esp_arduino_version.h>
#endif

// ---------- 节点 MAC 登记表（可选·单播用；广播模式留空即可） ----------
struct NodeInfo {
    uint8_t id;
    uint8_t mac[6];
};

// 需要单播时，把节点上电时打印的 MAC 填进来（与 configs.yaml 的 SPRAY.nodes 对应）
NodeInfo nodeTable[] = {
    // {1, {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x01}},   // 片区A
    // {2, {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x02}},   // 片区B
};
const int NODE_COUNT = sizeof(nodeTable) / sizeof(nodeTable[0]);

uint8_t broadcastMac[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

uint8_t* findMacById(int id) {
    for (int i = 0; i < NODE_COUNT; ++i) {
        if (nodeTable[i].id == id) return nodeTable[i].mac;
    }
    return nullptr;
}

// ---------- 发送回调：闪灯表示已发出 ----------
#if ESP_ARDUINO_VERSION_MAJOR >= 3
void onDataSent(const wifi_tx_info_t* info, esp_now_send_status_t status) {
    (void)info;
#else
void onDataSent(const uint8_t* mac, esp_now_send_status_t status) {
    (void)mac;
#endif
    digitalWrite(LED_PIN, status == ESP_NOW_SEND_SUCCESS ? HIGH : LOW);
}

// ---------- 下发一帧：有 node 且登记了 MAC 走单播，否则广播 ----------
void sendCommand(const String& raw) {
    String cmd = raw;
    cmd.trim();
    if (cmd.length() == 0 || cmd.length() > 250) return;   // ESP-NOW 单帧上限约 250 字节

    const int nodeId = cg::parseNodeId(cmd.c_str());
    uint8_t* target = (nodeId > 0) ? findMacById(nodeId) : nullptr;

    if (target != nullptr) {
        esp_now_peer_info_t peer;
        memset(&peer, 0, sizeof(peer));
        memcpy(peer.peer_addr, target, 6);
        peer.channel = ESPNOW_CHANNEL;
        peer.encrypt = false;
        esp_now_add_peer(&peer);   // 已存在会返回 ESP_ERR_ESPNOW_EXIST，忽略即可
        esp_now_send(target, (const uint8_t*)cmd.c_str(), (size_t)cmd.length());
        Serial.print("UNICAST -> ");
    } else {
        esp_now_send(broadcastMac, (const uint8_t*)cmd.c_str(), (size_t)cmd.length());
        Serial.print("BROADCAST -> ");
    }
    Serial.println(cmd);
}

void setup() {
    Serial.begin(115200);
    delay(200);

    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    WiFi.mode(WIFI_STA);
    WiFi.disconnect();   // ESP-NOW 用射频，不连任何 AP

    if (esp_now_init() != ESP_OK) {
        Serial.println("ESPNOW_INIT_FAIL");
        return;
    }
    esp_now_register_send_cb(onDataSent);

    esp_now_peer_info_t peer;
    memset(&peer, 0, sizeof(peer));
    memcpy(peer.peer_addr, broadcastMac, 6);
    peer.channel = ESPNOW_CHANNEL;
    peer.encrypt = false;
    esp_now_add_peer(&peer);

    Serial.print("GATEWAY_READY  channel=");
    Serial.print(ESPNOW_CHANNEL);
    Serial.print("  registered_nodes=");
    Serial.println(NODE_COUNT);
    Serial.println("发送回调会闪板载 LED：亮=发送成功");
}

void loop() {
    if (Serial.available() > 0) {
        String line = Serial.readStringUntil('\n');
        sendCommand(line);
    }
    delay(5);
}
