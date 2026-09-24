// 农智云 分级声光报警灯 —— Arduino 固件
// 通过 USB 串口接收电脑发来的指令：
//   'R' = 红灯 + 蜂鸣（高风险）
//   'Y' = 黄灯（中风险）
//   'G' = 绿灯（低风险 / 无风险）
// 接线：红/黄/绿 LED 分别接 D9/D10/D11（各串 220Ω 电阻到 GND），
//       有源蜂鸣器接 D12（正极）与 GND。

const int RED = 9;
const int YELLOW = 10;
const int GREEN = 11;
const int BUZZER = 12;

void setup() {
  pinMode(RED, OUTPUT);
  pinMode(YELLOW, OUTPUT);
  pinMode(GREEN, OUTPUT);
  pinMode(BUZZER, OUTPUT);
  Serial.begin(9600);
  digitalWrite(GREEN, HIGH);  // 上电默认绿灯
}

void loop() {
  if (Serial.available() > 0) {
    char c = Serial.read();
    digitalWrite(RED, LOW);
    digitalWrite(YELLOW, LOW);
    digitalWrite(GREEN, LOW);
    noTone(BUZZER);

    switch (c) {
      case 'R':
        digitalWrite(RED, HIGH);
        tone(BUZZER, 1000);
        break;
      case 'Y':
        digitalWrite(YELLOW, HIGH);
        break;
      case 'G':
      default:
        digitalWrite(GREEN, HIGH);
        break;
    }
  }
}
