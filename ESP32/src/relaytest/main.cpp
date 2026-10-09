// ============================================================
// 继电器接线自检（env:relaytest）—— 不依赖 WiFi
// ============================================================
// 用途：现场接完线、热点还没通时，先确认继电器控制侧接对了。
//
// 接线（对着 1 路光耦隔离模块的丝印）：
//     ESP32 3V3  -> DC+      （给线圈供电）
//     ESP32 GND  -> DC-      （共地）
//     ESP32 GPIO13 -> IN
//     模块上的 H/L 跳线帽拨到 **L**（低电平触发，与固件默认一致）
//
// 现象：每 2 秒一次「咔哒」——吸合 1 秒、断开 1 秒；模块上的小灯同步亮灭。
//      ESP32 板载 LED（GPIO2）也会同步闪。
//
// ⚠️ 如果负载侧（COM/NO）已经接了雾化模块：跑这个固件前**必须确认雾化片浸没在水中**，
//    否则它会以 1 秒的节奏干烧，几秒就报废。
//
// 烧录：pio run -e relaytest -t upload --upload-port COM5
// ============================================================

#include <Arduino.h>

#include "cg_config.h"

void setup() {
    Serial.begin(115200);
    delay(300);

    pinMode(RELAY_PIN, OUTPUT);
    digitalWrite(RELAY_PIN, RELAY_IDLE_LEVEL);   // 先断开，避免上电瞬间误吸合
#if SPRAY_SIG_PIN >= 0
    pinMode(SPRAY_SIG_PIN, OUTPUT);              // 雾化模块 S 信号脚
    digitalWrite(SPRAY_SIG_PIN, SIG_IDLE_LEVEL);
#endif
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    Serial.println();
    Serial.println("=== 继电器接线自检（env:relaytest）===");
    Serial.print("RELAY_PIN   = GPIO");
    Serial.println(RELAY_PIN);
#if SPRAY_SIG_PIN >= 0
    Serial.print("S 信号脚    = GPIO");
    Serial.print(SPRAY_SIG_PIN);
    Serial.print("（有效电平 = ");
    Serial.print(SIG_ACTIVE == LOW ? "低" : "高");
    Serial.println("）");
#endif
    Serial.print("固件触发极性 = ");
    Serial.println(RELAY_ACTIVE == LOW
                       ? "低电平触发 -> 模块 H/L 跳线帽请拨到 L"
                       : "高电平触发 -> 模块 H/L 跳线帽请拨到 H");
    Serial.println("接线        : 3V3 -> DC+   GND -> DC-   GPIO13 -> IN");
    Serial.println("现象        : 每 2 秒一次咔哒（吸合 1 秒 / 断开 1 秒），模块小灯同步亮灭");
    Serial.println("提示        : 先只听声音确认控制侧；负载侧 COM/NO 等确认后再接");
    Serial.println("              若已接雾化模块，务必先确认雾化片浸没在水中！");
    Serial.println();
}

void loop() {
    digitalWrite(RELAY_PIN, RELAY_ACTIVE);
#if SPRAY_SIG_PIN >= 0
    digitalWrite(SPRAY_SIG_PIN, SIG_ACTIVE);      // 同时给雾化模块 S 信号
#endif
    digitalWrite(LED_PIN, HIGH);
    Serial.println("RELAY_ON   <- 应该听到咔哒一声");
    delay(1000);

    digitalWrite(RELAY_PIN, RELAY_IDLE_LEVEL);
#if SPRAY_SIG_PIN >= 0
    digitalWrite(SPRAY_SIG_PIN, SIG_IDLE_LEVEL);
#endif
    digitalWrite(LED_PIN, LOW);
    Serial.println("RELAY_OFF  <- 应该再咔哒一声");
    delay(1000);
}
