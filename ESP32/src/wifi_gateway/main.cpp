// ============================================================
// 方案丙 · 无线网关（移植自 wifi_gateway.ino；原 .ino 已于 2026-10-08 删除）
// ============================================================
// 链路：服务器 --HTTP POST--> 本网关 --ESP-NOW--> 喷洒节点
// 与 espnow_gateway 的唯一区别：把「USB 串口读指令」换成「WiFi 收 HTTP」，
// 从而去掉服务器到网关的那根 USB 线。节点固件（spray_node）零改动。
//
// 服务器端 config/configs.yaml：
//   SPRAY:
//     transport: 'http'
//     http_targets:
//       - { group: 'gateway', url: 'http://192.168.43.60/spray' }   # 本网关 IP
//
// ⚠️ ESP-NOW 与 WiFi 共用射频，两者必须同信道：
//    本网关连上 AP 后，收发的就是 AP 的信道；而节点 spray_node 用
//    WiFi.disconnect() 不连 AP，射频停在默认信道 1。
//    => 必须把 AP 的 2.4G 信道固定为 1（手机热点做不到）。
//    上电后串口会打印实际信道，便于核对。
//
// 编译：pio run -e wifi_gateway -t upload
// ============================================================

#include <Arduino.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp_now.h>
#include <string.h>

#include "cg_config.h"
#include "cg_protocol.h"

#if !defined(ESP_ARDUINO_VERSION_MAJOR)
#  include <esp_arduino_version.h>
#endif

WebServer server(HTTP_PORT);

IPAddress STATIC_IP(NET_A, NET_B, NET_C, GW_IP_LAST);
IPAddress GATEWAY_IP(NET_A, NET_B, NET_C, NET_GW_LAST);
IPAddress SUBNET_MASK(255, 255, 255, 0);

// ---------- 节点 MAC 登记表（可选·单播用；广播模式留空即可） ----------
struct NodeInfo {
    uint8_t id;
    uint8_t mac[6];
};

NodeInfo nodeTable[] = {
    // {1, {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x01}},   // 片区A
    // {2, {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x02}},   // 片区B
};
const int NODE_COUNT = sizeof(nodeTable) / sizeof(nodeTable[0]);

uint8_t broadcastMac[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

unsigned long sentCount = 0;
unsigned long lastWifiCheck = 0;

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
    const bool ok = (status == ESP_NOW_SEND_SUCCESS);
    digitalWrite(LED_PIN, ok ? HIGH : LOW);
    if (ok) ++sentCount;
}

// ---------- 下发一帧：有 node 且登记了 MAC 走单播，否则广播 ----------
void forwardCommand(const String& raw) {
    String cmd = raw;
    cmd.trim();
    if (cmd.length() == 0 || cmd.length() > 250) return;   // ESP-NOW 单帧上限约 250 字节

    const int nodeId = cg::parseNodeId(cmd.c_str());
    uint8_t* target = (nodeId > 0) ? findMacById(nodeId) : nullptr;

    if (target != nullptr) {
        esp_now_peer_info_t peer;
        memset(&peer, 0, sizeof(peer));
        memcpy(peer.peer_addr, target, 6);
        peer.channel = 0;   // 0 = 跟随当前 WiFi 信道
        peer.encrypt = false;
        esp_now_add_peer(&peer);
        esp_now_send(target, (const uint8_t*)cmd.c_str(), (size_t)cmd.length());
        Serial.print("UNICAST -> ");
    } else {
        esp_now_send(broadcastMac, (const uint8_t*)cmd.c_str(), (size_t)cmd.length());
        Serial.print("BROADCAST -> ");
    }
    Serial.println(cmd);
}

// ---------- HTTP 处理 ----------

// POST /spray   body: {"group":"g1","duration":2}
void handleSpray() {
    if (!server.hasArg("plain")) {
        server.send(400, "application/json", "{\"ok\":false,\"err\":\"empty body\"}");
        return;
    }
    String body = server.arg("plain");
    body.trim();
    if (body.length() == 0) {
        server.send(400, "application/json", "{\"ok\":false,\"err\":\"empty body\"}");
        return;
    }
    forwardCommand(body);
    server.send(200, "application/json", "{\"ok\":true}");
}

// GET /  便于浏览器直接确认网关在线
void handleRoot() {
    String html = "<!doctype html><meta charset='utf-8'>";
    html += "<h3>CropGuard 无线网关在线</h3>";
    html += "<p><b>IP:</b> " + WiFi.localIP().toString() + "</p>";
    html += "<p><b>信道:</b> " + String(WiFi.channel()) +
            "（须与节点一致，建议 AP 固定信道 1）</p>";
    html += "<p><b>RSSI:</b> " + String(WiFi.RSSI()) + " dBm</p>";
    html += "<p><b>已下发帧数:</b> " + String(sentCount) + "</p>";
    html += "<p>POST <code>/spray</code> body 例：<code>{\"group\":\"g1\",\"duration\":2}</code></p>";
    server.send(200, "text/html; charset=utf-8", html);
}

void handleNotFound() {
    server.send(404, "application/json", "{\"ok\":false,\"err\":\"not found\"}");
}

// ==================== 初始化 ====================
void setup() {
    Serial.begin(115200);
    delay(200);

    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    WiFi.mode(WIFI_STA);
    WiFi.config(STATIC_IP, GATEWAY_IP, SUBNET_MASK);
    WiFi.begin(WIFI_SSID, WIFI_PASS);

    Serial.print("Connecting WiFi");
    const unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
        delay(300);
        Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WIFI_FAIL（检查 SSID/密码/AP 是否开机）");
        // 不 return：HTTP 仍启动，便于现场浏览器排查
    } else {
        Serial.print("WIFI_OK  IP=");
        Serial.print(WiFi.localIP());
        Serial.print("  CHANNEL=");
        Serial.println(WiFi.channel());
        Serial.println(">>> 若 CHANNEL 不是 1，请把 AP 信道固定为 1（节点固件写死信道 1）");
    }

    if (esp_now_init() != ESP_OK) {
        Serial.println("ESPNOW_INIT_FAIL");
        return;
    }
    esp_now_register_send_cb(onDataSent);

    // 广播 peer：channel = 0 表示跟随当前 WiFi 信道（连上 AP 后即 AP 信道）
    esp_now_peer_info_t peer;
    memset(&peer, 0, sizeof(peer));
    memcpy(peer.peer_addr, broadcastMac, 6);
    peer.channel = 0;
    peer.encrypt = false;
    esp_now_add_peer(&peer);

    server.on("/", HTTP_GET, handleRoot);
    server.on("/spray", HTTP_POST, handleSpray);
    server.onNotFound(handleNotFound);
    server.begin();

    Serial.println("GATEWAY_READY（HTTP 模式）");
    Serial.print("POST 地址: http://");
    Serial.print(WiFi.localIP());
    Serial.println("/spray");
}

void loop() {
    server.handleClient();

    // WiFi 掉线看门狗：每 10 秒检查一次（网关必须一直在线）
    if (millis() - lastWifiCheck > 10000) {
        lastWifiCheck = millis();
        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("WIFI_LOST -> reconnecting");
            WiFi.disconnect();
            WiFi.begin(WIFI_SSID, WIFI_PASS);
        }
    }

    delay(2);
}
