"""喷淋执行控制器：风险 → 片区/分组喷淋时长映射，下发指令到喷洒设备。

国赛形态C「物理反馈/外部设备联动」的喷淋执行链路，支持三种传输方式：

    serial : Server --USB串口--> 网关 ESP32 --ESP-NOW--> 节点（现状，含一根 USB 线）
    http   : Server --HTTP--> 目标设备（方案甲：节点直连 WiFi；方案丙：无线网关转发）
    mqtt   : Server --MQTT--> 目标设备（方案乙/丙，需 broker）

「田间无线」推荐 **方案丙**：transport=http，http_targets 只配一项 ``group='gateway'``，
全部指令发给无线网关，网关再经 ESP-NOW 广播/单播给节点——**节点固件（ESP32/src/espnow_node）零改动**。

与 feedback.Feedback 同一风格：异步执行、失败静默、不阻塞推理主流程。
链路不可用（无串口/无目标地址/broker 未连）时静默降级，仅推理不做喷淋。
"""
import json
import threading
import time

try:
    import serial
except ImportError:
    serial = None

try:
    import requests
except ImportError:
    requests = None


class SprayController:
    """喷淋控制器：按风险等级/手动触发下发指令，支持 serial / http / mqtt 三种传输。"""

    def __init__(self, serial_port=None, baud=115200, enabled=True,
                 risk_map=None, min_interval=3.0,
                 transport='serial', http_targets=None, http_timeout=1.0,
                 mqtt=None):
        self.enabled = bool(enabled)
        self.serial_port = serial_port
        self.baud = int(baud or 115200)
        self.risk_map = risk_map or {}
        self.min_interval = float(min_interval)

        # ---------- 传输方式 ----------
        self.transport = str(transport or 'serial').lower()
        if self.transport not in ('serial', 'http', 'mqtt'):
            self.transport = 'serial'

        # http 目标表：{group: url}；group='gateway' 表示方案丙的无线网关（承接全部指令）
        self.http_targets = {}
        for item in (http_targets or []):
            try:
                g = str(item.get('group', '') or '').strip()
                u = str(item.get('url', '') or '').strip()
            except AttributeError:
                continue
            if g and u:
                self.http_targets[g] = u
        self.http_timeout = float(http_timeout or 1.0)

        # mqtt 配置
        mqtt = mqtt or {}
        self.mqtt_host = str(mqtt.get('host', '') or '')
        self.mqtt_port = int(mqtt.get('port', 1883) or 1883)
        self.mqtt_topic = str(mqtt.get('topic', 'cropguard/spray') or 'cropguard/spray')
        self.mqtt_qos = int(mqtt.get('qos', 0) or 0)

        self._serial = None
        self._mqtt = None
        self._lock = threading.Lock()
        # 自动喷淋节流时间戳，按分组独立计时（多片区不会互相压制）
        self._last_auto_spray = {}

        if self.enabled:
            self._init_transport()

    # ==================== 初始化 ====================

    def _init_transport(self):
        """按传输方式初始化；失败静默（链路不可用时降级为纯推理）。"""
        if self.transport == 'serial':
            self._init_serial()
        elif self.transport == 'mqtt':
            self._init_mqtt()
        # http 无需预先连接，调用时直接 POST

    def _init_serial(self):
        """打开网关串口，失败静默（无网关时降级为纯推理）。"""
        if serial is None or not self.serial_port:
            return
        try:
            self._serial = serial.Serial(self.serial_port, self.baud, timeout=0.1)
        except Exception:
            self._serial = None

    def _init_mqtt(self):
        """初始化 MQTT 客户端（需 paho-mqtt）；失败静默。"""
        if not self.mqtt_host:
            return
        try:
            import paho.mqtt.client as mqtt_client
        except ImportError:
            return
        try:
            client = mqtt_client.Client()
            client.connect(self.mqtt_host, self.mqtt_port, keepalive=30)
            client.loop_start()
            self._mqtt = client
        except Exception:
            self._mqtt = None

    @property
    def available(self):
        """链路是否可用（按传输方式判定）。"""
        if not self.enabled:
            return False
        if self.transport == 'serial':
            return bool(self._serial and self._serial.is_open)
        if self.transport == 'http':
            return bool(self.http_targets) and requests is not None
        if self.transport == 'mqtt':
            return bool(self._mqtt)
        return False

    # ==================== 下发 ====================

    def _targets_for(self, group):
        """按分组取出 http 目标地址列表。

        方案丙：只配了 'gateway' 时，所有指令都发网关，由网关 ESP-NOW 转发。
        方案甲：按 group 精确寻址，实现片区间故障隔离。
        """
        group = group or 'all'
        if group in self.http_targets:
            return [self.http_targets[group]]
        if 'gateway' in self.http_targets:
            return [self.http_targets['gateway']]
        if group == 'all':
            return list(self.http_targets.values())
        return []

    def _write(self, command: dict) -> bool:
        """写一条指令到目标设备（按传输方式分派）。"""
        if not self.available:
            return False
        try:
            if self.transport == 'serial':
                line = json.dumps(command, ensure_ascii=False) + '\n'
                with self._lock:
                    self._serial.write(line.encode('utf-8'))
                return True

            if self.transport == 'http':
                urls = self._targets_for(command.get('group'))
                if not urls:
                    return False
                ok = False
                for url in urls:
                    try:
                        resp = requests.post(url, json=command, timeout=self.http_timeout)
                        ok = ok or (200 <= resp.status_code < 300)
                    except Exception:
                        continue
                return ok

            if self.transport == 'mqtt':
                payload = json.dumps(command, ensure_ascii=False)
                info = self._mqtt.publish(self.mqtt_topic, payload, qos=self.mqtt_qos)
                return info is not None
        except Exception:
            return False
        return False

    def _send_async(self, command: dict):
        """后台线程发指令，不阻塞推理主流程。"""
        threading.Thread(target=self._write, args=(command,), daemon=True).start()

    # ==================== 对外接口 ====================

    def spray(self, risk_level, detected_names=None, group_override=None):
        """按风险等级自动喷淋。

        group_override: 指定片区分组（如 'g2'），用于"多路定点监测 · 按片区定向喷淋"；
                        None 时使用 risk_map 中该风险等级配置的 group。
        返回 True 表示已下发指令；低/无风险、链路不可用、节流命中时返回 False。
        """
        if not self.enabled or not self.available:
            return False

        mapping = self.risk_map.get(risk_level)
        if not mapping:
            return False

        group = group_override or mapping.get('group')
        duration = mapping.get('duration', 0)
        if not group or not duration or int(duration) <= 0:
            return False

        # 自动喷淋节流：按分组独立计时，避免多片区互相压制
        now = time.time()
        if now - self._last_auto_spray.get(group, 0.0) < self.min_interval:
            return False
        self._last_auto_spray[group] = now

        self._send_async({'group': group, 'duration': int(duration)})
        return True

    def manual_spray(self, group='all', duration=2):
        """手动喷淋（Client 按钮），不受自动节流限制。

        group: 'all' / 'g1' / 'g2' ...（对应片区A/片区B）；duration: 秒。
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
        if self._mqtt is not None:
            try:
                self._mqtt.loop_stop()
                self._mqtt.disconnect()
            except Exception:
                pass
