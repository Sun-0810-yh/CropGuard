#pragma once

// ============================================================
// 声光报警：串口单字符协议 -> 三色灯 + 蜂鸣器
// ------------------------------------------------------------
// 协议与 feedback/feedback.py 的 RISK_LED 完全一致（9600 波特率、单字节）：
//   'R' 高风险   -> 红灯 + 蜂鸣
//   'Y' 中风险   -> 黄灯
//   'G' 低/无风险 -> 绿灯
// 行为对齐 feedback/arduino_alarm.ino：每次切换先全灭 + 停蜂鸣，
// 但引脚换成 ESP32 安全脚位（原 Arduino 的 D12 = ESP32 GPIO12 有上电跳线风险）。
// ============================================================

#include <Arduino.h>

#include "cg_buzzer.h"
#include "cg_config.h"

namespace cg {

enum class AlarmLevel { kNone, kLow, kMedium, kHigh };

class Alarm {
public:
    void begin() {
        pinMode(ALARM_RED_PIN, OUTPUT);
        pinMode(ALARM_YELLOW_PIN, OUTPUT);
        pinMode(ALARM_GREEN_PIN, OUTPUT);
        _buzzer.begin();
        show(AlarmLevel::kNone);   // 上电默认绿灯（与 arduino_alarm.ino 一致）
    }

    void show(AlarmLevel level) {
        digitalWrite(ALARM_RED_PIN, LOW);
        digitalWrite(ALARM_YELLOW_PIN, LOW);
        digitalWrite(ALARM_GREEN_PIN, LOW);
        _buzzer.off();

        switch (level) {
            case AlarmLevel::kHigh:
                digitalWrite(ALARM_RED_PIN, HIGH);
                _buzzer.tone(ALARM_BUZZER_FREQ);
                break;
            case AlarmLevel::kMedium:
                digitalWrite(ALARM_YELLOW_PIN, HIGH);
                break;
            default:   // kLow / kNone 都是绿灯
                digitalWrite(ALARM_GREEN_PIN, HIGH);
                break;
        }

#if CG_ALARM_VERBOSE
        Serial.print("ALARM=");
        Serial.println(name(level));
#endif
    }

    // 轮询串口指令（放在 loop() 里，非阻塞）
    void pollSerial() {
        while (Serial.available() > 0) {
            switch ((char)Serial.read()) {
                case 'R': case 'r': show(AlarmLevel::kHigh); break;
                case 'Y': case 'y': show(AlarmLevel::kMedium); break;
                case 'G': case 'g': show(AlarmLevel::kNone); break;
                default: break;   // 忽略换行等杂字符
            }
        }
    }

private:
    static const char* name(AlarmLevel level) {
        switch (level) {
            case AlarmLevel::kHigh:   return "R(high)";
            case AlarmLevel::kMedium: return "Y(medium)";
            case AlarmLevel::kLow:    return "G(low)";
            default:                  return "G(none)";
        }
    }

    Buzzer _buzzer;
};

}  // namespace cg
