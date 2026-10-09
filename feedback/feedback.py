"""信号反馈模块：语音播报 + Arduino 声光报警，构成「识别→反馈」物理闭环。

国赛要求「系统必须根据识别结果触发物理动作或信号控制」。本模块提供两级反馈：
1. 语音播报（pyttsx3，Windows SAPI 离线 TTS，零硬件依赖）
2. 声光报警（pyserial 串口驱动三色 LED + 蜂鸣器；固件见 ESP32/src/alarm）

二者可独立工作：没有报警器时，语音播报仍满足「信号反馈」要求。
"""
import time
import threading

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None

try:
    import serial
except ImportError:
    serial = None

# 风险等级 -> 串口指令（与 Arduino 固件约定）
RISK_LED = {
    '高风险': 'R',  # 红灯 + 蜂鸣
    '中风险': 'Y',  # 黄灯
    '低风险': 'G',  # 绿灯
    '无风险': 'G',  # 绿灯
}


def _speak_text(risk_level, detected_names):
    """根据风险等级与检测结果生成播报文案。"""
    names = '、'.join(detected_names) if detected_names else '未知害虫'
    if risk_level == '高风险':
        return f'警告，检测到{names}，风险等级高，请立即采取防治措施'
    if risk_level == '中风险':
        return f'提示，检测到{names}，风险等级中，建议及时防治'
    if risk_level == '低风险':
        return f'检测到{names}，风险等级低，建议持续观察'
    return '未检测到害虫，作物生长状况良好'


class Feedback:
    """反馈管理器：语音与声光均异步执行，失败静默，不阻塞检测主流程。"""

    def __init__(self, serial_port=None, baud=9600, enabled=True, speak_interval=3.0):
        self.enabled = enabled
        self.serial_port = serial_port
        self.baud = baud
        self.speak_interval = speak_interval
        self._serial = None
        self._tts = None
        self._last_speak_time = 0.0
        self._speak_lock = threading.Lock()
        if enabled:
            self._init_tts()
            self._init_serial()

    def _init_tts(self):
        if pyttsx3 is None:
            return
        try:
            self._tts = pyttsx3.init()
        except Exception:
            self._tts = None

    def _init_serial(self):
        if serial is None or not self.serial_port:
            return
        try:
            self._serial = serial.Serial(self.serial_port, self.baud, timeout=0.1)
        except Exception:
            self._serial = None

    def _send_led(self, risk_level):
        """LED 灯实时切换（每次检测都更新，反映当前风险）。"""
        code = RISK_LED.get(risk_level)
        if code and self._serial and self._serial.is_open:
            try:
                self._serial.write(code.encode())
            except Exception:
                pass

    def _speak(self, text):
        if self._tts is None:
            return
        # pyttsx3 的 runAndWait 不允许并发调用（会抛 "loop already running"，
        # 甚至卡死线程），这里加锁串行化，避免长时间运行线程越积越多
        with self._speak_lock:
            try:
                self._tts.say(text)
                self._tts.runAndWait()
            except Exception:
                pass

    def trigger(self, risk_level, detected_names, risk_detail=''):
        """检测完成后触发反馈。LED 实时，语音按时间节流。"""
        if not self.enabled:
            return

        self._send_led(risk_level)

        now = time.time()
        if now - self._last_speak_time >= self.speak_interval:
            self._last_speak_time = now
            text = _speak_text(risk_level, detected_names)
            threading.Thread(target=self._speak, args=(text,), daemon=True).start()

    def close(self):
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
