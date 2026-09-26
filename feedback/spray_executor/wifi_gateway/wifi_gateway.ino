// ============================================================
// 农智云 · ESP-NOW 喷淋网络 —— 无线网关固件 (wifi_gateway.ino)
// ============================================================
// 方案丙：Server --HTTP--> 本网关 --ESP-NOW--> 喷洒节点
//
// 与 gateway.ino 的唯一区别：把「USB 串口读指令」换成「WiFi 收 HTTP 指令」，
// 从而去掉 Server 到网关的那根 USB 线，实现「田间无线」。
//
// ★ 喷洒节点固件 (spray_node.ino) 完全不用改 ★
//
// ------------------------------------------------------------
// 服务器端 config/configs.yaml 配置：
//   SPRAY:
//     transport: 'http'
//     http_targets:
//       - { group: 'gateway', url: 'http://192.168.4.60/spray' }   # 改成本网关实际 IP
//
// ⚠️ ESP-NOW 与 WiFi 共用射频，两者必须在同一信道：
//    本网关连上 AP 后，收发电商用的就是 AP 的信道。
//    节点 spray_node.ino 用 WiFi.disconnect() 不连 AP，射频停在默认信道 1。
//    => 最简做法：把 AP 的 2.4G 信道固定为 1。上电后串口会打印实际信道，便于核对。
//
// 烧录：Arduino IDE 选「ESP32 Dev Module」。烧完拔掉 USB，改用充电宝供电（须为"小电流不休眠"款）。
// WebServer 库随 ESP32 Arduino 核心自带，无需额外安装。
// ============================================================

#include <WiFi.h>
#include <WebServer.h>
#include <esp_now.h>

// ==================== 配置（烧录前修改） ====================
const char*   WIFI_SSID = "CropGuard-AP";   // 便携 AP 的 SSID
const char*   WIFI_PASS = "12345678";       // 便携 AP 的密码
const uint16_t HTTP_PORT = 80;

// 固定 IP：便于服务器在 configs.yaml 里写死地址（推荐开启）
const bool USE_STATIC_IP = true;
IPAddress STATIC_IP (192, 168, 4, 60);
IPAddress GATEWAY_IP(192, 168, 4, 1);
IPAddress SUBNET_MASK(255, 255, 255, 0);
// =============================================================

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

// 广播地址：所有节点都接收
uint8_t broadcastMac[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// 板载状态灯（通常 GPIO2）
const int LED_PIN = 2;

WebServer server(HTTP_PORT);
volatile uint32_t sentCount = 0;

// ---------- 发送回调：闪灯表示已发出 ----------
void onDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  bool ok = (status == ESP_NOW_SEND_SUCCESS);
  digitalWrite(LED_PIN, ok ? HIGH : LOW);
  if (ok) sentCount++;
}

// ---------- 添加广播 peer ----------
void registerBroadcastPeer() {
  esp_now_peer_info_t peer;
  memset(&peer, 0, sizeof(peer));
  memcpy(peer.peer_addr, broadcastMac, 6);
  peer.channel = 0;        // 0 = 跟随当前 WiFi 信道（连上 AP 后即为 AP 信道）
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) {
    // 已存在则忽略（重复添加返回 ESP_ERR_ESPNOW_EXIST）
  }
}

// ---------- 解析指令里的 "node":N，返回节点 id（无则 -1） ----------
int parseNodeId(const String &cmd) {
  int p = cmd.indexOf("\"node\"");
  if (p < 0) return -1;
  int colon = cmd.indexOf(':', p);
  if (colon < 0) return -1;
  int i = colon + 1;
  while (i < (int)cmd.length() && (cmd[i] == ' ' || cmd[i] == ':')) i++;
  int val = 0;
  bool neg = false;
  if (i < (int)cmd.length() && cmd[i] == '-') { neg = true; i++; }
  while (i < (int)cmd.length() && isdigit(cmd[i])) {
    val = val * 10 + (cmd[i] - '0');
    i++;
  }
  return neg ? -val : val;
}

uint8_t* findMacById(int id) {
  for (int i = 0; i < NODE_COUNT; i++) {
    if (nodeTable[i].id == id) return nodeTable[i].mac;
  }
  return nullptr;
}

// ---------- 下发一帧：有 node 且登记了 MAC 走单播，否则广播 ----------
void forwardCommand(const String &cmd) {
  if (cmd.length() == 0 || cmd.length() > 250) return;   // ESP-NOW 单帧上限约 250 字节

  int nodeId = parseNodeId(cmd);
  uint8_t *target = (nodeId > 0) ? findMacById(nodeId) : nullptr;

  if (target != nullptr) {
    esp_now_peer_info_t peer;
    memset(&peer, 0, sizeof(peer));
    memcpy(peer.peer_addr, target, 6);
    peer.channel = 0;
    peer.encrypt = false;
    esp_now_add_peer(&peer);
    esp_now_send(target, (uint8_t *)cmd.c_str(), cmd.length());
    Serial.print("UNICAST -> ");
  } else {
    esp_now_send(broadcastMac, (uint8_t *)cmd.c_str(), cmd.length());
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
  String html = "<h3>CropGuard 无线网关在线</h3>";
  html += "<p>IP: " + WiFi.localIP().toString() + "</p>";
  html += "<p>信道: " + String(WiFi.channel()) + "（须与节点一致，建议 AP 固定信道 1）</p>";
  html += "<p>RSSI: " + String(WiFi.RSSI()) + " dBm</p>";
  html += "<p>已下发帧数: " + String((uint32_t)sentCount) + "</p>";
  html += "<p>POST <code>/spray</code> body 例：<code>{\"group\":\"g1\",\"duration\":2}</code></p>";
  server.send(200, "text/html; charset=utf-8", html);
}

void handleNotFound() {
  server.send(404, "application/json", "{\"ok\":false,\"err\":\"not found\"}");
}

// ---------- 初始化 ----------
void setup() {
  Serial.begin(115200);
  delay(200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  WiFi.mode(WIFI_STA);
  if (USE_STATIC_IP) {
    WiFi.config(STATIC_IP, GATEWAY_IP, SUBNET_MASK);
  }
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  Serial.print("Connecting WiFi");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WIFI_FAIL（检查 SSID/密码/AP 是否开机）");
    // 不 return：仍启动 HTTP 服务，便于现场排查
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
  registerBroadcastPeer();

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
  delay(2);
}
