// ============================================================
// 方案甲 · HTTP 喷洒节点（移植自 http_node.ino；原 .ino 已于 2026-10-08 删除，行为以此为准）
// ============================================================
// 链路：服务器 --WiFi HTTP POST--> 本节点 --GPIO13--> 继电器 --> 水泵
//
// 为什么是方案甲（国赛方案.md §5 已定）：
//   现场用手机热点当 AP，而手机热点的 2.4G 信道不可控；
//   ESP-NOW（方案丙）要求网关与节点同信道，会收不到包。
//   方案甲直接按 IP 寻址，彻底摆脱信道约束，而且片区天然故障隔离。
//
// 服务器端 config/configs.yaml：
//   SPRAY:
//     transport: 'http'
//     http_targets:
//       - { group: 'g1', url: 'http://192.168.43.61/spray' }   # 本节点（片区A）
//       - { group: 'g2', url: 'http://192.168.43.62/spray' }   # 另一片区
//
// 接线（与 spray_node.ino 完全一致，共 5 根线，零焊接）：
//   ESP32 GPIO13 -> 继电器 IN
//   ESP32 5V/VIN -> 继电器 VCC
//   ESP32 GND    -> 继电器 GND
//   充电宝 5V +  -> 继电器 COM
//   继电器 NO    -> 水泵 红线(+)
//   水泵 黑线(-) -> 充电宝 5V −（与 ESP32 共地）
//
// 端点：
//   GET  /        状态页（浏览器直接打开即可确认在线）
//   GET  /status  探活 JSON
//   POST /spray   {"group":"g1","duration":2}
//   POST /stop    立即停止
//
// 编译：pio run -e http_node_g1 -t upload    （片区B 换成 -e http_node_g2）
// ============================================================

#include <Arduino.h>
#include <WebServer.h>
#include <WiFi.h>
#include <string.h>

#include "cg_config.h"
#include "cg_protocol.h"
#include "cg_relay.h"

#if ENABLE_SERIAL_ALARM
#  include "cg_alarm.h"
#endif

WebServer server(HTTP_PORT);
cg::Relay relay;

#if ENABLE_SERIAL_ALARM
// 变量名不能叫 alarm：会与 POSIX 的 alarm() 函数冲突（见 src/alarm/main.cpp 的说明）
cg::Alarm alarmUnit;
#endif

IPAddress STATIC_IP(NET_A, NET_B, NET_C, NODE_IP_LAST);
IPAddress GATEWAY_IP(NET_A, NET_B, NET_C, NET_GW_LAST);
IPAddress SUBNET_MASK(255, 255, 255, 0);

unsigned long lastWifiCheck = 0;

// ==================== WiFi ====================
void connectWifi() {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);                 // 关省电，降低响应延迟
#if USE_DHCP
    Serial.println("IP 模式: DHCP（由热点/路由器分配）");
#else
    WiFi.config(STATIC_IP, GATEWAY_IP, SUBNET_MASK);
    Serial.print("IP 模式: 静态 -> ");
    Serial.println(STATIC_IP);
#endif
    WiFi.begin(WIFI_SSID, WIFI_PASS);

    Serial.print("Connecting WiFi");
    const unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
        delay(300);
        Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.print("WIFI_OK  IP=");
        Serial.println(WiFi.localIP());
        Serial.print("        网关=");
        Serial.print(WiFi.gatewayIP());
        Serial.print("  掩码=");
        Serial.print(WiFi.subnetMask());
        Serial.print("  RSSI=");
        Serial.print(WiFi.RSSI());
        Serial.println(" dBm");
        Serial.println("        ^^^ 把这个 IP 填进 config/configs.yaml 的 SPRAY.http_targets");
    } else {
        Serial.println("WIFI_FAIL（检查 SSID/密码/热点是否开启；仍会继续尝试重连）");
    }
}

// ==================== HTTP 处理 ====================

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

    char group[16] = {0};
    cg::parseGroup(body.c_str(), group, sizeof(group));
    const int duration = cg::parseDuration(body.c_str());

    // 分组过滤：只执行 "all" 或本节点分组
    if (strcmp(group, "all") != 0 && strcmp(group, NODE_GROUP) != 0) {
        Serial.print("SKIP group=");
        Serial.println(group);
        // 必须回 2xx：feedback/spray.py 只看状态码
        server.send(200, "application/json",
                    "{\"ok\":true,\"skipped\":true,\"reason\":\"group mismatch\"}");
        return;
    }

    if (duration > 0) {
        relay.start(duration);
    } else {
        relay.stop();   // duration<=0 视为立即停止
    }

    const String resp = "{\"ok\":true,\"node\":" + String(NODE_ID) +
                        ",\"group\":\"" + String(NODE_GROUP) +
                        "\",\"duration\":" + String(duration) + "}";
    server.send(200, "application/json", resp);
}

// POST /stop   立即停止（也可用 {"duration":0} 走 /spray）
void handleStop() {
    relay.stop();
    server.send(200, "application/json", "{\"ok\":true,\"stopped\":true}");
}

// GET /  状态页
void handleRoot() {
    String html = "<!doctype html><meta charset='utf-8'>";
    html += "<h3>CropGuard 喷洒节点在线</h3>";
    html += "<p><b>节点:</b> id=" + String(NODE_ID) + "（" + String(NODE_NAME) + "）</p>";
    html += "<p><b>分组:</b> " + String(NODE_GROUP) + "</p>";
    html += "<p><b>IP:</b> " + WiFi.localIP().toString() +
            "　<b>RSSI:</b> " + String(WiFi.RSSI()) + " dBm</p>";
    html += "<p><b>状态:</b> " + String(relay.spraying() ? "喷淋中" : "待机") +
            "　<b>已执行:</b> " + String(relay.count()) + " 次</p>";
    html += "<p>POST <code>/spray</code> body 例：<code>{\"group\":\"g1\",\"duration\":2}</code></p>";
    html += "<p>POST <code>/stop</code> 立即停止</p>";
    server.send(200, "text/html; charset=utf-8", html);
}

// GET /status  给服务器/脚本探活
void handleStatus() {
    const String json = "{\"ok\":true,\"node\":" + String(NODE_ID) +
                        ",\"name\":\"" + String(NODE_NAME) +
                        "\",\"group\":\"" + String(NODE_GROUP) +
                        "\",\"ip\":\"" + WiFi.localIP().toString() +
                        "\",\"rssi\":" + String(WiFi.RSSI()) +
                        ",\"spraying\":" + String(relay.spraying() ? "true" : "false") +
                        ",\"count\":" + String(relay.count()) + "}";
    server.send(200, "application/json", json);
}

void handleNotFound() {
    server.send(404, "application/json", "{\"ok\":false,\"err\":\"not found\"}");
}

// ==================== 初始化 ====================
void setup() {
    Serial.begin(115200);
    delay(200);

    relay.begin();          // 上电先断开继电器
#if ENABLE_SERIAL_ALARM
    alarmUnit.begin();
#endif

    connectWifi();

    server.on("/", HTTP_GET, handleRoot);
    server.on("/status", HTTP_GET, handleStatus);
    server.on("/spray", HTTP_POST, handleSpray);
    server.on("/stop", HTTP_POST, handleStop);
    server.onNotFound(handleNotFound);
    server.begin();

    Serial.print("NODE_READY id=");
    Serial.print(NODE_ID);
    Serial.print(" group=");
    Serial.print(NODE_GROUP);
    Serial.print("  POST http://");
    Serial.print(WiFi.localIP());
    Serial.println("/spray");
}

void loop() {
    server.handleClient();

    // WiFi 掉线看门狗：每 10 秒检查一次，掉线就重连
    if (millis() - lastWifiCheck > 10000) {
        lastWifiCheck = millis();
        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("WIFI_LOST -> reconnecting");
            WiFi.disconnect();
            WiFi.begin(WIFI_SSID, WIFI_PASS);
        }
    }

    relay.poll();           // 非阻塞计时：到点断继电器

#if ENABLE_SERIAL_ALARM
    alarmUnit.pollSerial();     // 可选：USB 串口的 R/Y/G 声光指令
#endif

    delay(2);
}
