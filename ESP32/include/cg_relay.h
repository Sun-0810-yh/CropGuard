#pragma once

// ============================================================
// 继电器 + 板载状态灯：非阻塞喷淋计时
// ------------------------------------------------------------
// 对齐已验证的 http_node.ino / spray_node.ino 行为，安全设计：
//   * begin() 先把继电器置为断开 —— 配 COM+NO 接线，上电绝不误喷
//   * 单次喷淋钳制在 MAX_SPRAY_SEC（默认 30 秒）
//   * 到点用 millis() 差值判断，天然兼容 49.7 天溢出
//   * 全程非阻塞：HTTP handler 里不做任何 delay，
//     保证 feedback/spray.py 的 1 秒超时永远够用
// ============================================================

#include <Arduino.h>

#include "cg_config.h"

namespace cg {

class Relay {
public:
    void begin() {
        pinMode(RELAY_PIN, OUTPUT);
        digitalWrite(RELAY_PIN, RELAY_IDLE_LEVEL);   // 先断开，再谈其它
#if SPRAY_SIG_PIN >= 0
        pinMode(SPRAY_SIG_PIN, OUTPUT);              // 雾化模块 S 信号脚
        digitalWrite(SPRAY_SIG_PIN, SIG_IDLE_LEVEL);
#endif
        pinMode(LED_PIN, OUTPUT);
        digitalWrite(LED_PIN, LOW);
        _until = 0;
        _spraying = false;
        _count = 0;
    }

    // 开始喷淋 seconds 秒；<=0 忽略，超过上限按上限钳制（可重复调用，后到覆盖前者）
    void start(int seconds) {
        if (seconds <= 0) return;
        if (seconds > MAX_SPRAY_SEC) seconds = MAX_SPRAY_SEC;

        _until = millis() + (unsigned long)seconds * 1000UL;
        _spraying = true;
        digitalWrite(RELAY_PIN, RELAY_ACTIVE);
#if SPRAY_SIG_PIN >= 0
        digitalWrite(SPRAY_SIG_PIN, SIG_ACTIVE);     // 同时给模块 S 信号
#endif
        digitalWrite(LED_PIN, HIGH);
        ++_count;

        Serial.print("SPRAY_ON  group=");
        Serial.print(NODE_GROUP);
        Serial.print("  duration=");
        Serial.println(seconds);
    }

    // 立即停止；重复调用静默
    void stop() {
        if (!_spraying && _until == 0) return;
        _until = 0;
        _spraying = false;
        digitalWrite(RELAY_PIN, RELAY_IDLE_LEVEL);
#if SPRAY_SIG_PIN >= 0
        digitalWrite(SPRAY_SIG_PIN, SIG_IDLE_LEVEL);
#endif
        digitalWrite(LED_PIN, LOW);
        Serial.println("SPRAY_OFF");
    }

    // 放在 loop() 里：到点自动断开
    void poll() {
        if (_until != 0 && (long)(millis() - _until) >= 0) stop();
    }

    bool spraying() const { return _spraying; }
    unsigned long count() const { return _count; }

    // 剩余秒数（向上取整）；未喷淋时为 0
    unsigned long remainingSec() const {
        if (_until == 0) return 0;
        const long left = (long)(_until - millis());
        if (left <= 0) return 0;
        return (unsigned long)((left + 999L) / 1000L);
    }

private:
    unsigned long _until = 0;    // 到该 millis 时间戳为止保持吸合
    bool _spraying = false;
    unsigned long _count = 0;    // 已执行次数，状态页显示
};

}  // namespace cg
