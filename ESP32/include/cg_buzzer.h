#pragma once

// ============================================================
// 蜂鸣器：ledc 硬件 PWM 封装
// ------------------------------------------------------------
// 不用 Arduino 的 tone()：它在核心 3.x 上的可用性与实现都变过，
// 而 ledc 两条路径都稳定。这里按核心大版本分支：
//   2.x（本工程实际使用的 2.0.17）：ledcSetup + ledcAttachPin + ledcWriteTone(通道, 频率)
//   3.x（保险分支）               ：ledcAttach(pin, freq, res) + ledcWriteTone(引脚, 频率)
// 关声统一用 duty=0，不依赖"频率传 0 会怎样"的实现细节。
// ============================================================

#include <Arduino.h>

#include "cg_config.h"

#if !defined(ESP_ARDUINO_VERSION_MAJOR)
#  include <esp_arduino_version.h>
#endif

namespace cg {

class Buzzer {
public:
    void begin() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
        ledcAttach(ALARM_BUZZER_PIN, ALARM_BUZZER_FREQ, _resolutionBits);
#else
        ledcSetup(_channel, ALARM_BUZZER_FREQ, _resolutionBits);
        ledcAttachPin(ALARM_BUZZER_PIN, _channel);
#endif
        off();
    }

    // freq == 0 表示关声
    void tone(unsigned long freq) {
        if (freq == 0) {
            off();
            return;
        }
#if ESP_ARDUINO_VERSION_MAJOR >= 3
        ledcWriteTone(ALARM_BUZZER_PIN, freq);
#else
        ledcWriteTone(_channel, freq);
#endif
    }

    void off() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
        ledcWrite(ALARM_BUZZER_PIN, 0);
#else
        ledcWrite(_channel, 0);
#endif
    }

private:
    // 用普通成员而不是 static const：避免 -O0 下静态常量被 odr-use 时的链接坑
    uint8_t _resolutionBits = 10;   // 10 bit 足够蜂鸣器用
#if ESP_ARDUINO_VERSION_MAJOR < 3
    uint8_t _channel = 0;           // 2.x 的 ledc API 按"通道"寻址，蜂鸣器独占通道 0
#endif
};

}  // namespace cg
