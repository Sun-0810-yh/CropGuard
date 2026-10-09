// ============================================================
// 声光报警器（arduino_alarm.ino 的 ESP32 版；原 .ino 已于 2026-10-08 删除）
// ============================================================
// 接在服务器的 USB 口上，作为 feedback/feedback.py 的物理反馈执行器：
//   feedback.py 每次检测完成后往串口写一个字节：
//     'R' 高风险   -> 红灯 + 蜂鸣
//     'Y' 中风险   -> 黄灯
//     'G' 低/无风险 -> 绿灯
//   （见 feedback/feedback.py 的 RISK_LED 与 _send_led）
//
// 与 arduino_alarm.ino 的差异只有两处：
//   1) 引脚从 Arduino 的 D9/D10/D11/D12 换成 ESP32 安全脚位
//      —— 原 D12 对应 ESP32 GPIO12（MTDI 上电跳线，拉高会导致启动失败）；
//   2) 蜂鸣器用 ledc 硬件 PWM，不依赖 tone()。
// 协议、闪烁语义、上电默认绿灯全部保持不变。
//
// 服务器端 config/configs.yaml：
//   FEEDBACK:
//     enabled: true
//     serial_port: 'COM5'    # 改成这块板子的实际 COM 口
//     baud: 9600             # 必须与这里一致
//
// 编译：pio run -e alarm -t upload   （monitor_speed 已配成 9600）
// ============================================================

#include <Arduino.h>

#include "cg_alarm.h"
#include "cg_config.h"

// 变量名不能叫 alarm：会与 POSIX 的 alarm() 函数（经 unistd.h -> pthread -> Arduino.h 引入）冲突
cg::Alarm alarmUnit;

void setup() {
    Serial.begin(ALARM_BAUD);
    delay(200);

    alarmUnit.begin();   // 上电默认绿灯

    Serial.println();
    Serial.println("=== CropGuard 声光报警器（EN）===");
    Serial.print("ALARM_READY  baud=");
    Serial.print(ALARM_BAUD);
    Serial.print("  R/Y/G/BZ = GPIO ");
    Serial.print(ALARM_RED_PIN);
    Serial.print("/");
    Serial.print(ALARM_YELLOW_PIN);
    Serial.print("/");
    Serial.print(ALARM_GREEN_PIN);
    Serial.print("/");
    Serial.println(ALARM_BUZZER_PIN);
    Serial.println("协议: 'R' 高风险(红灯+蜂鸣) / 'Y' 中风险(黄灯) / 'G' 低·无风险(绿灯)");
}

void loop() {
    alarmUnit.pollSerial();
    delay(5);
}
