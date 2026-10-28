# -*- coding: utf-8 -*-
"""应用级共享状态：配置、模型、反馈设备、相机池、推理泵状态、喷淋历史。

本模块在 import 时完成一次性初始化（加载 YOLO、打开串口/TTS 等），
api_server.py / 其它入口统一从这里取实例，避免各自重复初始化。
"""
import os
import threading
from datetime import datetime

import numpy as np
from ultralytics import YOLO

from tool.parser import get_config

from feedback.feedback import Feedback
from feedback.tts import TTSEngine
from feedback.spray import SprayController
from app import spray_targets as spray_targets_file

from tool.camera_source import (
    get_source as cam_get_source,
    release_source as cam_release_source,
    pool_state as cam_pool_state,
    spec_from_config,
)

# ---------------------------------------------------------------- 配置
config = get_config('./config/configs.yaml')
cfg_model = config.MODEL
weights_path = cfg_model.WEIGHT
conf_thres = float(cfg_model.CONF)
imgsz = int(cfg_model.IMGSIZE)
# 设备可用环境变量临时覆盖（config 里通常写死 'cuda:0'）：
#   CPU-only 机器上用  $env:CG_DEVICE='cpu'  起服务，就不用改配置（也不会误提交 CPU 配置）
device = str(os.environ.get('CG_DEVICE') or cfg_model.DEVICE)
try:
    import torch
    if str(device).startswith('cuda') and not torch.cuda.is_available():
        # 与 main.py 同口径：配置写 cuda 但这台机器没卡/没装 CUDA 版 torch → 回退 CPU，
        # 否则服务一启动就在预热推理处崩掉
        print('[WARN] CUDA 不可用，自动回退到 CPU 推理')
        device = 'cpu'
except ImportError:
    pass
chinese_name = config.get('CONFIG', {}).get('chinese_name', {})

# ---------------------------------------------------------------- 模型加载
if not os.path.exists(weights_path):
    # 绝不回退到 'yolov8s.pt'：本地早已删除，ultralytics 会**联网下载 COCO 80 类模型**，
    # 离线现场直接卡死；就算下到了，类别也和 27 类害虫对不上，检测结果全是错的。
    raise SystemExit(
        f"[ERROR] 权重文件不存在: {weights_path}\n"
        f"        请把 config/configs.yaml 的 MODEL.WEIGHT 指向现有权重"
        f"（默认 runs/train/pest27_final4/weights/best.pt）")

print(f"[INFO] Loading model: {weights_path}")
print(f"[INFO] Device: {device}")
yolo_model = YOLO(weights_path)
# 预热
yolo_model.predict(np.zeros((300, 300, 3), dtype=np.uint8), device=device)
print(f"[INFO] Model loaded. Classes: {len(yolo_model.names)}")
print(f"[INFO] Classes: {list(yolo_model.names.values())}")

# ---------------------------------------------------------------- 信号反馈 & 喷淋执行（国赛形态C 物理闭环）
cfg_feedback = config.get('FEEDBACK', {}) or {}
cfg_tts = cfg_feedback.get('tts', {}) or {}
_tts_engine = TTSEngine(
    enabled=bool(cfg_feedback.get('enabled', True)) and str(cfg_tts.get('engine', 'auto')) != 'none',
    engine=str(cfg_tts.get('engine', 'auto') or 'auto'),
    rate=float(cfg_tts.get('rate', 150) or 150),
    volume=float(cfg_tts.get('volume', 1.0) or 1.0),
    dedup_sec=float(cfg_tts.get('dedup_sec', 0.5) or 0),
)
feedback = Feedback(
    serial_port=cfg_feedback.get('serial_port') or None,
    baud=int(cfg_feedback.get('baud', 9600)),
    enabled=bool(cfg_feedback.get('enabled', True)),
    speak_interval=float(cfg_feedback.get('speak_interval', 3)),
    tts=_tts_engine,
    announce_no_risk=bool(cfg_feedback.get('announce_no_risk', False)),
)
print(f"[INFO] TTS engine={feedback.status()['tts'].get('engine')} "
      f"ready={feedback.status()['tts'].get('ready')}")
if bool(cfg_feedback.get('tts_selftest', False)):
    # 启动念一句：现场一眼就能确认"到底响不响"（不响则日志给出后端与错误原因）
    if TTSEngine.wait_backend(_tts_engine, timeout=5.0):
        _tts_engine.speak('语音播报自检，株得已就绪', flush=True)

cfg_spray = config.get('SPRAY', {}) or {}
spray = SprayController(
    serial_port=cfg_spray.get('serial_port') or None,
    baud=int(cfg_spray.get('baud', 115200)),
    enabled=bool(cfg_spray.get('enabled', True)),
    risk_map=cfg_spray.get('risk_map', {}) or {},
    min_interval=float(cfg_spray.get('min_interval', 3)),
    transport=cfg_spray.get('transport', 'serial'),
    http_targets=cfg_spray.get('http_targets', []) or [],
    http_timeout=float(cfg_spray.get('http_timeout', 1.0) or 1.0),
    mqtt=cfg_spray.get('mqtt', {}) or {},
    max_duration=int(cfg_spray.get('max_duration', 5) or 5),
    default_protocol=str(cfg_spray.get('default_protocol', 'json') or 'json'),
    direct_max_sec=cfg_spray.get('direct_max_sec', 300),
    status_timeout=float(cfg_spray.get('status_timeout', 1.5) or 1.5),
    nodes=cfg_spray.get('nodes', []) or [],
)
_protocols = {g: spray.protocol_of(g) for g in spray.http_targets}
print(f"[INFO] Spray transport={spray.transport} available={spray.available} "
      f"protocols={_protocols}")

# 现场在 /demo 里改过节点地址？覆盖文件优先于 configs.yaml（见 app/spray_targets.py）
_local_targets = spray_targets_file.load()
if _local_targets:
    _applied, _errs = spray.set_http_targets(_local_targets)
    if _applied:
        print(f"[INFO] 喷淋节点地址来自本地覆盖 {spray_targets_file.PATH}："
              f"{[(t['group'], t['url'], t['protocol']) for t in _applied]}")
    for _e in _errs:
        print(f"[WARN] 覆盖文件里的节点地址被忽略：{_e}")

print(f"[INFO] 雾化节点(mist)={[t['url'] for t in spray.mist_targets()]} "
      f"持续模式兜底={spray.direct_max_sec}s")

# ---------------------------------------------------------------- 摄像头源解析（多路片区方案 + 单摄回退）
cfg_cameras = config.get('CONFIG', {}).get('cameras') or []
_cfg_conn = config.get('CONFIG', {}) or {}
camera_num = int(_cfg_conn.get('camera_num', 0))
# 设备标识优先（'vid_0c45' / 'USB Camera'），数字索引只在标识为空时兜底。
# ⚠️ 以前 web/演示台这条路只读 camera_num：配置里写了 camera_device 也没人认
#    （只有 PyQt GUI main.py 认），于是演示台静默打开了"另一台相机"——画面正常，
#    但拍的不是对着标本的那台，现场很难发现。见 tool/camera_source.spec_from_config。
camera_device = str(_cfg_conn.get('camera_device') or '').strip()
camera_spec = spec_from_config(_cfg_conn)
cfg_camera = config.get('CAMERA', {}) or {}
print(f"[INFO] 摄像头：camera_device={camera_device or '(空)'} camera_num={camera_num} "
      f"→ 使用 {camera_spec!r}"
      + ("（按设备标识解析，匹配不上就报错，绝不换相机）"
         if camera_device else "（数字索引，插拔/重启后可能变）"))

# 相机 key -> CameraSource：当前**有订阅者**的相机（推理泵按它逐路推理）。
# 引用计数由 camera_source 维护；这里的增删必须走 _cam_subscribe/_cam_unsubscribe，
# 保证"最后一个订阅者离开"时才摘掉，不会出现一个客户端断开、另一个就没检测了。
_CAM_SOURCES = {}
_cam_subs = {}
_pump_lock = threading.Lock()


def _cam_kwargs():
    """采集参数（config.CAMERA 段，缺省值保证老配置也能跑）。"""
    return {
        'width': int(cfg_camera.get('width', 1280) or 0),
        'height': int(cfg_camera.get('height', 720) or 0),
        'fourcc': cfg_camera.get('fourcc', 'MJPG') or None,
        'backend': str(cfg_camera.get('backend', 'auto') or 'auto'),
        'stale_sec': float(cfg_camera.get('stale_sec', 2.0) or 2.0),
        'probe_timeout': float(cfg_camera.get('probe_timeout', 8.0) or 5.0),
    }


def _cam_subscribe(key, source, name=None):
    """订阅一路相机（引用计数 +1），并登记到 _CAM_SOURCES 供推理泵使用。"""
    src = cam_get_source(key, source=source, name=name, **_cam_kwargs())
    with _pump_lock:
        _CAM_SOURCES[key] = src
        _cam_subs[key] = _cam_subs.get(key, 0) + 1
    return src


def _cam_unsubscribe(key):
    """退订（引用计数 -1）；本进程的最后一个订阅者离开才从 _CAM_SOURCES 摘掉。"""
    with _pump_lock:
        n = _cam_subs.get(key, 0) - 1
        if n <= 0:
            _cam_subs.pop(key, None)
            _CAM_SOURCES.pop(key, None)
        else:
            _cam_subs[key] = n
    cam_release_source(key)


def _camera_key(cam_id=None):
    """相机缓存键：多路按片区 id，单摄固定 'default'。"""
    if cfg_cameras:
        return ('cam', str(cam_id or '').strip().lower() or 'default')
    return ('cam', 'default')


def _normalize_source(src):
    """规范化摄像头 source：纯数字串转 int，其余按路径/RTSP URL 处理。"""
    if isinstance(src, str):
        s = src.strip()
        return int(s) if s.isdigit() else s
    return src


def resolve_camera(cam_id=None):
    """解析摄像头源，返回 (source, group, label)。

    - 配了 CONFIG.cameras 时：按 id 匹配（如 'A'/'B'）；未命中则取第一路
    - 未配 cameras 时：回退单摄 camera_num，group/label 为空
    """
    if cfg_cameras:
        chosen = None
        if cam_id:
            target = str(cam_id).strip().lower()
            for c in cfg_cameras:
                if str(c.get('id', '')).strip().lower() == target:
                    chosen = c
                    break
        if chosen is None:
            chosen = cfg_cameras[0]
        return (
            _normalize_source(chosen.get('source', 0)),
            str(chosen.get('group', '') or ''),
            str(chosen.get('name') or chosen.get('id') or ''),
        )
    return camera_spec, '', ''


# ---------------------------------------------------------------- 联动/喷淋历史（Client 页「历史回看」用，内存环形缓存）
spray_history = []


def _record_history(risk_level, detections, group=None, duration=None, source='auto',
                    via=''):
    """记录一条联动/喷淋历史。

    via: 该指令的发出通道（web/manual/phone/chat/auto），前端历史列表会显示。
    """
    names = []
    for d in (detections or []):
        n = d.get('chinese_name') or d.get('class_name') if isinstance(d, dict) else str(d)
        if n:
            names.append(str(n))
    spray_history.append({
        'ts': datetime.now().strftime('%H:%M:%S'),
        'source': source,
        'via': via or source,
        'risk': risk_level,
        'group': group,
        'duration': duration,
        'detections': names[:8],
    })
    if len(spray_history) > 50:
        del spray_history[:len(spray_history) - 50]


# ---------------------------------------------------------------- 共享推理泵状态（多客户端只跑一份推理）
_pump_state = {'task': None, 'stop': None, 'last': None, 'last_ts': 0.0,
               'seq': 0, 'count': 0,
               # 按相机 key 分开的最新结果：{key: {'last', 'last_ts', 'seq'}}
               'by_key': {}}
_last_result = {"result": None}          # 最近一次检测结果，供 /api/ask "结合当前虫情提问"用
