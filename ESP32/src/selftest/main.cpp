// ============================================================
// 板载协议自检（env:selftest）
// ============================================================
// 为什么要有这个 env：
//   本机没有主机 C++ 编译器，跑不了 PlatformIO 的 native 单元测试；
//   而 JSON 解析是整条链路里最容易"悄悄解析错"的一环。
//   于是把断言放到板子上跑：不需要继电器、水泵、热点，
//   只要板子能烧进去，就能证明协议解析与 feedback/spray.py 对齐。
//
// 用法：
//   pio run -e selftest -t upload
//   pio device monitor            # 期望看到 SELFTEST_PASS
//
// 自检期间不碰继电器/报警引脚（只闪板载 LED），
// 所以可以在还没接任何外设时先跑这一遍。
// ============================================================

#include <Arduino.h>
#include <stdio.h>
#include <string.h>

#include "cg_config.h"
#include "cg_protocol.h"

namespace {

int gPass = 0;
int gFail = 0;

void report(const char* label, const char* detail) {
    ++gFail;
    Serial.print("  FAIL  ");
    Serial.print(label);
    if (detail != nullptr) {
        Serial.print("  ->  ");
        Serial.print(detail);
    }
    Serial.println();
}

void expectGroup(const char* json, const char* want, const char* label) {
    char got[24] = {0};
    cg::parseGroup(json, got, sizeof(got));
    if (strcmp(got, want) == 0) {
        ++gPass;
        return;
    }
    char detail[80];
    snprintf(detail, sizeof(detail), "want=\"%s\" got=\"%s\"", want, got);
    report(label, detail);
}

void expectDuration(const char* json, int want, const char* label) {
    const int got = cg::parseDuration(json);
    if (got == want) {
        ++gPass;
        return;
    }
    char detail[48];
    snprintf(detail, sizeof(detail), "want=%d got=%d", want, got);
    report(label, detail);
}

void expectNodeId(const char* json, int want, const char* label) {
    const int got = cg::parseNodeId(json);
    if (got == want) {
        ++gPass;
        return;
    }
    char detail[48];
    snprintf(detail, sizeof(detail), "want=%d got=%d", want, got);
    report(label, detail);
}

// ---- 真实载荷：与 feedback/spray.py 下发的 JSON 完全一致 ----
void testGroup() {
    Serial.println("[1] parseGroup");
    expectGroup("{\"group\":\"g1\",\"duration\":2}", "g1", "基本(片区A)");
    expectGroup("{\"group\":\"g2\",\"duration\":2}", "g2", "基本(片区B)");
    expectGroup("{\"group\":\"all\",\"duration\":3}", "all", "全体广播");
    expectGroup("{\"group\": \"g2\", \"duration\": 2}", "g2", "冒号后带空格");
    expectGroup("{\"duration\":2,\"group\":\"all\"}", "all", "字段顺序颠倒");
    expectGroup("{\"duration\":2}", "", "字段缺失");
    expectGroup("{\"group\":null}", "", "值为 null");
    expectGroup("{\"group\":\"\"}", "", "值为空串");
    expectGroup("{\"group\":\"g", "", "截断串");
    expectGroup("{\"group\":\"g1\"", "g1", "结尾缺少大括号");

    // 缓冲区不足时必须安全截断，不能越界
    char small[4] = {0};
    cg::parseGroup("{\"group\":\"groupLong\"}", small, sizeof(small));
    if (strcmp(small, "gro") == 0) {
        ++gPass;
    } else {
        report("缓冲区截断到 outSize-1", small);
    }
}

void testDuration() {
    Serial.println("[2] parseDuration");
    expectDuration("{\"group\":\"g1\",\"duration\":2}", 2, "基本");
    expectDuration("{\"duration\": 12}", 12, "冒号后带空格");
    expectDuration("{\"duration\":0}", 0, "零 = 立即停");
    expectDuration("{\"duration\":30}", 30, "上限值");
    expectDuration("{\"duration\":-1}", -1, "负数 = 立即停");
    expectDuration("{\"group\":\"g1\"}", 0, "字段缺失");
    expectDuration("{\"duration\":}", 0, "值为空");
    expectDuration("{\"duration\":\"3\"}", 0, "数字被引号包住");
    expectDuration("{\"duration\":2.7}", 2, "小数截断为整数");
    expectDuration("{\"duration\":99999999}", cg::kNumberMax, "超大数饱和不溢出");
}

void testNodeId() {
    Serial.println("[3] parseNodeId");
    expectNodeId("{\"node\":1,\"duration\":2}", 1, "单播 1 号");
    expectNodeId("{\"node\":2,\"duration\":2}", 2, "单播 2 号");
    expectNodeId("{\"node\": 3 ,\"duration\":1}", 3, "带空格");
    expectNodeId("{\"group\":\"g1\"}", -1, "字段缺失 = -1");
    expectNodeId("{\"node\":-1}", -1, "显式 -1");
}

// ---- 长帧：ESP-NOW 单帧上限约 250 字节，验一下长指令不被截错 ----
void testLongFrame() {
    Serial.println("[4] 长指令（模拟 ESP-NOW 250 字节帧）");
    char json[272];
    strcpy(json, "{\"pad\":\"");
    for (int i = 0; i < 200; ++i) strcat(json, "x");
    strcat(json, "\",\"group\":\"g2\",\"duration\":5}");

    expectGroup(json, "g2", "长帧里的 group");
    expectDuration(json, 5, "长帧里的 duration");
    expectNodeId(json, -1, "长帧里无 node 字段");
}

}  // namespace

void setup() {
    Serial.begin(115200);
    delay(300);

    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    Serial.println();
    Serial.println("=== CropGuard ESP32 协议自检（env:selftest）===");
    Serial.print("固件身份常量：NODE_ID=");
    Serial.print(NODE_ID);
    Serial.print("  NODE_GROUP=");
    Serial.print(NODE_GROUP);
    Serial.print("  NODE_NAME=");
    Serial.print(NODE_NAME);
    Serial.print("  NODE_IP_LAST=");
    Serial.println(NODE_IP_LAST);
    Serial.println();

    testGroup();
    testDuration();
    testNodeId();
    testLongFrame();

    Serial.println();
    Serial.print("SELFTEST_");
    Serial.println(gFail == 0 ? "PASS" : "FAIL");
    Serial.print("通过 ");
    Serial.print(gPass);
    Serial.print(" 项，失败 ");
    Serial.print(gFail);
    Serial.println(" 项");
    if (gFail == 0) {
        Serial.println("=> 协议解析与 feedback/spray.py 对齐，可以烧业务 env 了。");
    } else {
        Serial.println("=> 解析行为与预期不符，先别接水泵。");
    }
}

void loop() {
    // 自检完成后心跳闪灯，现场一眼看出板子还活着
    digitalWrite(LED_PIN, (millis() / 500) % 2 == 0 ? HIGH : LOW);
    delay(20);
}
