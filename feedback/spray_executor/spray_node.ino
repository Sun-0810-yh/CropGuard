// ============================================================
// 农智云 · ESP-NOW 喷淋网络 —— 喷头节点固件 (spray_node.ino)
// ============================================================
// 作用：ESP-NOW 接收网关广播/单播的喷淋指令，按自身 group 过滤，
//       继电器闭合 N 秒 -> 微型水泵喷 -> 断开。
//
// 每个节点烧录前需修改下方「节点配置」：
//   NODE_ID    ：节点编号（1~20，唯一）
//   NODE_GROUP ：所属分组（"g1"/"g2"/...），与 Server configs.yaml 的 SPRAY.nodes 一致
//
// 接线（见 国赛硬件采购清单.md / 国赛设备联动联调方案.md）：
//   ESP32 GPIO13 -> 继电器 IN
//   ESP32 5V/VIN -> 继电器 VCC
//   ESP32 GND    -> 继电器 GND
//   5V电源 +     -> 继电器 COM
//   继电器 NO    -> 水泵 红线(+)
//   水泵 黑线(-) -> 5V电源 -（与 ESP32 共地）
// ============================================================

#include <esp_now.h>
#include <WiFi.h>

// ==================== 节点配置（烧录前修改） ====================
#define NODE_ID 1
const char* NODE_GROUP = "g1";
// =============================================================

// 继电器触发引脚
const int RELAY_PIN = 13;
// 继电器触发有效电平：多数 1 路光耦继电器模块为「低电平触发」，此时用 LOW；
// 若你的模块为高电平触发，改为 HIGH。
const int RELAY_ACTIVE = LOW;
const int RELAY_IDLE   = (RELAY_ACTIVE == LOW) ? HIGH : LOW;

// 板载状态灯
const int LED_PIN = 2;

// 非阻塞计时：到该毫秒时间戳前保持喷淋
unsigned long sprayUntil = 0;

// 网关 MAC（广播地址）；若网关做单播，则此处仅用于登记，收包按广播地址过滤
uint8_t gatewayMac[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// ---------- 极简 JSON 字段解析（指令体小且由我方生成，够用可靠） ----------
// 提取 "group":"xxx"
String parseGroup(const uint8_t *data, int len) {
  String s;
  s.reserve(len);
  for (int i = 0; i < len; i++) s += (char)data[i];
  int p = s.indexOf("\"group\"");
  if (p < 0) return "";
  int c1 = s.indexOf(':', p);
  if (c1 < 0) return "";
  int q1 = s.indexOf('"', c1 + 1);
  if (q1 < 0) return "";
  int q2 = s.indexOf('"', q1 + 1);
  if (q2 < 0) return "";
  return s.substring(q1 + 1, q2);
}

// 提取 "duration":N
int parseDuration(const uint8_t *data, int len) {
  String s;
  s.reserve(len);
  for (int i = 0; i < len; i++) s += (char)data[i];
  int p = s.indexOf("\"duration\"");
  if (p < 0) return 0;
  int c = s.indexOf(':', p);
  if (c < 0) return 0;
  int i = c + 1;
  while (i < (int)s.length() && (s[i] == ' ' || s[i] == ':')) i++;
  int val = 0;
  bool neg = false;
  if (s[i] == '-') { neg = true; i++; }
  while (i < (int)s.length() && isdigit(s[i])) {
    val = val * 10 + (s[i] - '0');
    i++;
  }
  return neg ? -val : val;
}

// ---------- 接收回调 ----------
void OnDataRecv(const uint8_t *mac_addr, const uint8_t *data, int len) {
  String group = parseGroup(data, len);
  int duration = parseDuration(data, len);

  // 仅 "all" 或本节点分组执行
  if (group != "all" && group != String(NODE_GROUP)) {
    return;
  }

  if (duration > 0) {
    if (duration > 30) duration = 30;  // 安全上限
    sprayUntil = millis() + (unsigned long)duration * 1000UL;
    digitalWrite(RELAY_PIN, RELAY_ACTIVE);
    digitalWrite(LED_PIN, HIGH);
    Serial.print("SPRAY_ON ");
    Serial.print(group);
    Serial.print(" ");
    Serial.println(duration);
  } else {
    // duration<=0 视为立即停止
    sprayUntil = 0;
    digitalWrite(RELAY_PIN, RELAY_IDLE);
    digitalWrite(LED_PIN, LOW);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, RELAY_IDLE);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESPNOW_INIT_FAIL");
    return;
  }
  esp_now_register_recv_cb(OnDataRecv);

  // 添加广播 peer，以便接收广播
  esp_now_peer_info_t peer;
  memset(&peer, 0, sizeof(peer));
  memcpy(peer.peer_addr, gatewayMac, 6);
  peer.channel = 1;
  peer.encrypt = false;
  esp_now_add_peer(&peer);

  Serial.print("NODE_READY id=");
  Serial.print(NODE_ID);
  Serial.print(" group=");
  Serial.println(NODE_GROUP);
}

void loop() {
  // 非阻塞计时：到点断开继电器
  if (sprayUntil != 0 && millis() >= sprayUntil) {
    sprayUntil = 0;
    digitalWrite(RELAY_PIN, RELAY_IDLE);
    digitalWrite(LED_PIN, LOW);
    Serial.println("SPRAY_OFF");
  }
  delay(5);
}
