"""喷淋执行控制器：风险 → 分组喷淋时长映射，串口发 JSON 指令给 ESP-NOW 网关。

国赛形态C「物理反馈/外部设备联动」的喷淋执行链路：
    Server(本模块) --USB串口--> 网关 ESP32 --ESP-NOW 广播--> 喷头节点 ESP32
    喷头节点按自身 group 过滤，继电器闭合 -> 微型水泵喷 -> 断开。

与 feedback.Feedback 同一风格：异步执行、失败静默、不阻塞推理主流程。
无网关（serial_port=None）时静默降级，仅推理不做喷淋。
"""
import json
import threading
import time

try:
    import serial
except ImportError:
    serial = None


class SprayController:
    """喷淋控制器：维护一个串口到网关，风险/手动喷淋均异步下发 JSON 指令。"""

    def __init__(self, serial_port=None, baud=115200, enabled=True,
                 risk_map=None, min_interval=3.0):
        self.enabled = bool(enabled)
        self.serial_port = serial_port
        self.baud = int(baud or 115200)
        self.risk_map = risk_map or {}
        self.min_interval = float(min_interval)
        self._serial = None
        self._lock = threading.Lock()
        self._last_auto_spray = 0.0
        if self.enabled and self.serial_port:
            self._init_serial()

    def _init_serial(self):
        """打开网关串口，失败静默（无网关时降级为纯推理）。"""
        if serial is None:
            return
        try:
            self._serial = serial.Serial(self.serial_port, self.baud, timeout=0.1)
        except Exception:
            self._serial = None

    @property
    def available(self):
        """网关串口是否可用。"""
        return bool(self._serial and self._serial.is_open)

    def _write(self, command: dict) -> bool:
        """写一行 JSON 指令到网关。"""
        if not self.available:
            return False
        try:
            line = json.dumps(command, ensure_ascii=False) + '\n'
            with self._lock:
                self._serial.write(line.encode('utf-8'))
            return True
        except Exception:
            return False

    def _send_async(self, command: dict):
        """后台线程发指令，不阻塞推理主流程。"""
        threading.Thread(target=self._write, args=(command,), daemon=True).start()

    def spray(self, risk_level, detected_names=None):
        """按风险等级自动喷淋。

        返回 True 表示已下发指令；低/无风险、无网关、节流命中时返回 False。
        """
        if not self.enabled or not self.available:
            return False

        mapping = self.risk_map.get(risk_level)
        if not mapping:
            return False

        group = mapping.get('group')
        duration = mapping.get('duration', 0)
        if not group or not duration or int(duration) <= 0:
            return False

        # 自动喷淋节流：避免连续推理反复喷
        now = time.time()
        if now - self._last_auto_spray < self.min_interval:
            return False
        self._last_auto_spray = now

        self._send_async({'group': group, 'duration': int(duration)})
        return True

    def manual_spray(self, group='all', duration=2):
        """手动喷淋（Client 按钮），不受自动节流限制。

        group: 'all' / 'g1' / 'g2' ...；duration: 秒。
        """
        if not self.enabled or not self.available:
            return False
        group = group or 'all'
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            duration = 2
        if duration <= 0:
            duration = 2
        if duration > 30:
            duration = 30  # 安全上限，避免误触长时间喷水
        self._send_async({'group': group, 'duration': duration})
        return True

    def close(self):
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
