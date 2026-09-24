// ============================================================
// 农智云 · ESP-NOW 喷淋网络 —— 网关固件 (gateway.ino)
// ============================================================
// 作用：USB 串口接收 Server 发来的喷淋指令（JSON），
//       通过 ESP-NOW 广播/单播给所有喷头节点，不依赖现场路由器。
//
// 串口协议（每行一条 JSON，与 Server 端 feedback/spray.py 约定）：
//   {"group":"all","duration":3}   -> 全体喷头喷 3 秒（广播）
//   {"group":"g1","duration":2}    -> g1 组喷头喷 2 秒（广播）
//   {"node":1,"duration":2}        -> 单播给 1 号节点（可选，需填 MAC 表）
//
// 烧录：Arduino IDE 选「ESP32 Dev Module」，波特率任意（代码写死 115200）。
// 接线：仅用 USB 数据线连 Server 笔记本，无需其它外设。
// ============================================================

#include <esp_now.h>
#include <WiFi.h>

// ---------- 节点 MAC 登记表（可选单播用） ----------
// 广播模式无需填写；如需「单播 + ACK」增强可靠，按节点 MAC 填写下表。
// 现场演示默认用广播即可，无需配置任何 MAC。
struct NodeInfo {
  uint8_t id;
  uint8_t mac[6];
};

NodeInfo nodeTable[] = {
  // {1, {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x01}},
  // {2, {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x02}},
  // {3, {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x03}},
};
const int NODE_COUNT = sizeof(nodeTable) / sizeof(nodeTable[0]);

// 广播地址（所有节点都接收）
uint8_t broadcastMac[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
const uint8_t ESP_NOW_CHANNEL = 1;

// 状态指示灯（板载 LED，通常 GPIO2）
const int LED_PIN = 2;

volatile bool sendOk = false;

// ---------- 发送回调 ----------
void OnDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  sendOk = (status == ESP_NOW_SEND_SUCCESS);
  digitalWrite(LED_PIN, status == ESP_NOW_SEND_SUCCESS ? HIGH : LOW);
}

// ---------- 添加广播 peer ----------
void registerBroadcastPeer() {
  esp_now_peer_info_t peer;
  memset(&peer, 0, sizeof(peer));
  memcpy(peer.peer_addr, broadcastMac, 6);
  peer.channel = ESP_NOW_CHANNEL;
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) {
    // 已存在则忽略（重复调用会返回 ESP_ERR_ESPNOW_EXIST）
  }
}

// ---------- 解析指令里的 "node":N，返回节点 id（无则 -1） ----------
int parseNodeId(String &cmd) {
  int p = cmd.indexOf("\"node\"");
  if (p < 0) return -1;
  int colon = cmd.indexOf(':', p);
  if (colon < 0) return -1;
  int start = colon + 1;
  while (start < (int)cmd.length() && (cmd[start] == ' ' || cmd[start] == ':')) start++;
  int val = 0;
  bool neg = false;
  if (cmd[start] == '-') { neg = true; start++; }
  while (start < (int)cmd.length() && isdigit(cmd[start])) {
    val = val * 10 + (cmd[start] - '0');
    start++;
  }
  return neg ? -val : val;
}

// ---------- 根据 id 查 MAC ----------
uint8_t* findMacById(int id) {
  for (int i = 0; i < NODE_COUNT; i++) {
    if (nodeTable[i].id == id) return nodeTable[i].mac;
  }
  return nullptr;
}

// ---------- 下发一帧 ----------
void sendCommand(String cmd) {
  if (cmd.length() == 0 || cmd.length() > 250) return;  // ESP-NOW 单帧上限约 250 字节

  int nodeId = parseNodeId(cmd);
  uint8_t *target = (nodeId > 0) ? findMacById(nodeId) : nullptr;

  if (target != nullptr) {
    // 单播到指定节点（需先 add peer）
    esp_now_peer_info_t peer;
    memset(&peer, 0, sizeof(peer));
    memcpy(peer.peer_addr, target, 6);
    peer.channel = ESP_NOW_CHANNEL;
    peer.encrypt = false;
    esp_now_add_peer(&peer);
    esp_now_send(target, (uint8_t *)cmd.c_str(), cmd.length());
  } else {
    // 广播到全部节点（默认路径）
    esp_now_send(broadcastMac, (uint8_t *)cmd.c_str(), cmd.length());
  }

  // 回执到 Server 串口，便于调试
  Serial.print("SENT:");
  Serial.println(cmd);
}

// ---------- 初始化 ----------
void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();  // ESP-NOW 用 WiFi 射频，不连任何 AP

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESPNOW_INIT_FAIL");
    return;
  }
  esp_now_register_send_cb(OnDataSent);
  registerBroadcastPeer();
  Serial.println("GATEWAY_READY");
}

// ---------- 主循环：读串口逐行转发 ----------
void loop() {
  if (Serial.available() > 0) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      sendCommand(line);
    }
  }
  delay(5);
}
