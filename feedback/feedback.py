"""信号反馈模块：语音播报 + ESP32 声光报警，构成「识别→反馈」物理闭环。

国赛要求「系统必须根据识别结果触发物理动作或信号控制」。本模块提供两级反馈：

1. **语音播报**（`feedback/tts.py`，离线 TTS：win32com SAPI / pyttsx3 / PowerShell 三级兜底）
2. **声光报警**（pyserial 串口驱动三色 LED + 蜂鸣器；固件见 `ESP32/src/alarm`）

二者可独立工作：没有报警器时，语音播报仍满足「信号反馈」要求。

## 播报语义（2026-10-08 之后的行为，与旧版不同）

旧版 `trigger()` 每次检测都调用，靠 `speak_interval` 节流，结果是：
低风险/无风险也反复念、连续检测时后几句被丢掉、文案一成不变。
现在改为「**事件驱动 + 最新优先**」：

* **风险等级或目标集合发生变化** → 立刻播报（打断上一句，`flush=True`）；
* **状态没变** → 按 `speak_interval` 节流，避免机器人式复读；
* **无风险** 默认只在"从有风险变成无风险"时报一次（`announce_no_risk` 可开）；
* 文案带上"已喷哪个片区、喷了几秒"，与真实动作一致（由 `_linkage` 传入）。
"""
import threading
import time

try:
    import serial
except ImportError:
    serial = None

from feedback.tts import TTSEngine

# 风险等级 -> 串口指令（与 ESP32/src/alarm 固件约定）
RISK_LED = {
    '高风险': 'R',  # 红灯 + 蜂鸣
    '中风险': 'Y',  # 黄灯
    '低风险': 'G',  # 绿灯
    '无风险': 'G',  # 绿灯
}


def _speak_text(risk_level, detected_names, spray_info=''):
    """根据风险等级与已执行动作生成播报文案。

    口语化、一句一个意思（以句号结尾）；**不念害虫名**（2026-10-28 用户要求：只报"发现害虫"），
    所以句子是有限集合，全部提前合成成神经语音（scripts/build_voice_cache.py），现场离线也是真人声。
    detected_names 保留参数只为兼容调用方，不进文案。
    """
    tail = f'{spray_info.rstrip("。")}。' if spray_info else ''
    if risk_level == '高风险':
        return '注意啦，这里发现了害虫，数量还不少。' + (tail or '建议马上处理一下。')
    if risk_level == '中风险':
        return '发现了害虫，建议尽快防治一下。' + tail
    if risk_level == '低风险':
        return '看到了少量害虫，先观察着就行。' + tail
    return '没发现害虫，庄稼长得挺好。'


def spray_phrase(group, duration):
    """喷淋动作的播报句：'我已经给A区喷了3秒药'（与 _speak_text 的缓存口径一致）。"""
    zone = {'all': '所有区域', '': '所有区域', None: '所有区域', 'g1': 'A区', 'g2': 'B区'}.get(group, f'{group}区')
    return f'我已经给{zone}喷了{int(duration)}秒药'


class Feedback:
    """反馈管理器：语音与声光均异步执行，失败静默，不阻塞检测主流程。"""

    def __init__(self, serial_port=None, baud=9600, enabled=True, speak_interval=3.0,
                 tts=None, announce_no_risk=False):
        self.enabled = enabled
        self.serial_port = serial_port
        self.baud = baud
        self.speak_interval = float(speak_interval or 0)
        self.announce_no_risk = bool(announce_no_risk)

        self._serial = None
        self._last_speak_time = 0.0
        self._last_key = None
        # 语音引擎：专用线程 + 队列，内部多后端兜底（见 feedback/tts.py）
        if isinstance(tts, TTSEngine):
            self.tts = tts
        elif tts is None and enabled:
            self.tts = TTSEngine(enabled=True)
        else:
            self.tts = tts if tts is not None else TTSEngine(enabled=False)

        if enabled:
            self._init_serial()

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

    def trigger(self, risk_level, detected_names, risk_detail='', spray_info=''):
        """检测完成后触发反馈。

        spray_info: 例如 '我已经给A区喷了3秒药'（见 spray_phrase），会拼进播报文案（可选）。
        返回是否真的下发了播报（便于调用方做日志/测试）。
        """
        if not self.enabled:
            return False

        self._send_led(risk_level)

        names = [str(n) for n in (detected_names or []) if n]
        key = (risk_level, tuple(sorted(set(names))))
        now = time.time()
        changed = key != self._last_key

        if risk_level == '无风险' and not self.announce_no_risk:
            self._last_key = key
            return False

        if not changed and (now - self._last_speak_time) < self.speak_interval:
            return False

        self._last_key = key
        self._last_speak_time = now
        text = _speak_text(risk_level, names, spray_info)
        # flush=True：新事件优先，不排队背诵旧文案
        return bool(self.tts.speak(text, flush=True))

    def status(self):
        return {
            'enabled': self.enabled,
            'serial': bool(self._serial and self._serial.is_open),
            'speak_interval': self.speak_interval,
            'announce_no_risk': self.announce_no_risk,
            'tts': self.tts.status() if self.tts is not None else {'enabled': False},
        }

    def close(self):
        if self.tts is not None:
            self.tts.close()
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
