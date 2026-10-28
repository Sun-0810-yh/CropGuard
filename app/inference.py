# -*- coding: utf-8 -*-
"""推理与风险联动：检测、格式化、防治建议、共享推理泵。

所有函数都假设 `app.state` 已完成初始化（模型、反馈设备、相机配置）。
"""
import asyncio
import base64
import threading
import time
import traceback
from datetime import datetime

import cv2
import numpy as np

from feedback.feedback import spray_phrase
from llm import knowledge_base
from tool.tools import format_data, draw_info, assess_risk, box_label
from app.state import (
    yolo_model, device, imgsz, conf_thres, chinese_name,
    spray, feedback, cfg_spray, cfg_camera, cfg_cameras,
    _CAM_SOURCES, _pump_lock, _pump_state, _last_result,
    _record_history,
)


_model_lock = threading.Lock()


# ---------------------------------------------------------------- 单次推理
def _predict_and_format(img, conf=None, draw=True):
    """检测 + 风险评估 + 结果图，返回统一 JSON 结构（供 REST 与 WebSocket 复用）。

    draw=False 时 result_image 是**原始帧**：实时流由前端按 bbox 叠框，
    帧与框来自同一次推理，任意缩放都对得上，也省掉服务器画框的开销。
    """
    if conf is None:
        conf = conf_thres
    t_start = time.time()
    # ultralytics 的 predictor 不是线程安全的：推理泵、/ws/phone、/api/detect
    # 都在线程池里并发调用同一个模型，不串行化会出现结果串台/偶发异常。
    with _model_lock:
        results = yolo_model.predict(img, imgsz=imgsz, conf=float(conf), device=device,
                                     verbose=False)
    formatted = format_data(results)
    inference_time = round(time.time() - t_start, 3)

    risk_level, risk_color, risk_detail = assess_risk(formatted, img.shape[:2])

    result_img = draw_info(img.copy(), formatted) if draw else img
    _, buffer = cv2.imencode('.jpg', result_img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    result_b64 = base64.b64encode(buffer).decode('utf-8')

    detections = []
    for r in formatted:
        cls_name = r[0]
        cn_name = chinese_name.get(cls_name, cls_name)
        detections.append({
            "class_name": cls_name,
            "chinese_name": cn_name,
            "label_en": box_label(cls_name),
            "confidence": round(r[1], 4),
            "bbox": [round(x, 1) for x in r[2]],
        })

    res = {
        "success": True,
        "count": len(detections),
        "detections": detections,
        "risk": {"level": risk_level, "detail": risk_detail},
        "inference_time": inference_time,
        "result_image": result_b64,
        "image_size": [int(img.shape[1]), int(img.shape[0])],
        "timestamp": datetime.now().isoformat(),
    }
    return res


def _remember_sample(res):
    """记下用户主动提交的最近一次样本（/api/detect），供 /api/ask 未传 context 时兜底。

    以前每次推理都覆盖这里：相机推理泵每秒 5 次、手机实时帧也在写，
    结果问"这个虫怎么防治"拿到的是别的客户端刚推理的画面（常常是"无虫"）。
    """
    _last_result["result"] = {"detections": res.get("detections") or [],
                              "risk": res.get("risk") or {}}


def _kb_advice_from_result(res):
    """用检测结果拼知识库防治报告（秒回、离线、无网络依赖）。"""
    pests = [
        (d.get("class_name") or d.get("chinese_name"), float(d.get("confidence") or 0))
        for d in (res.get("detections") or [])
    ]
    risk = res.get("risk") or {}
    return knowledge_base.get_advice(pests, risk.get("level", ""), risk.get("detail", ""))


def _with_advice(res):
    """给检测结果挂上知识库版防治建议（失败静默，不影响检测本身）。

    注意：只挂在 /api/detect 与 /api/detect_b64 上，
    WebSocket 实时流那条路**不能挂**（每帧生成建议会拖垮推理）。
    """
    try:
        res["advice"] = _kb_advice_from_result(res)
        res["advice_source"] = "knowledge_base"
    except Exception:
        res["advice"] = ""
        res["advice_source"] = "none"
    return res


# ---------------------------------------------------------------- 风险联动
#: 演示安全开关：关掉后自动联动**只播语音、不下发喷淋**（手动按钮不受影响）。
#: 现场由"演示指挥台"（/demo）或 POST /api/demo/auto_spray 切换。
AUTO_SPRAY_ENABLED = True


def set_auto_spray(enabled):
    """打开/关闭"自动联动是否真的喷"（语音永远照播）。返回当前状态。"""
    global AUTO_SPRAY_ENABLED
    AUTO_SPRAY_ENABLED = bool(enabled)
    return AUTO_SPRAY_ENABLED


def auto_spray_enabled():
    """当前自动联动是否真的会喷（演示指挥台状态灯用）。"""
    return bool(AUTO_SPRAY_ENABLED)


def _linkage(risk_level, detections, zone_group=None):
    """风险联动：语音播报 + 喷淋执行（均异步、失败静默、不阻塞推理）。

    zone_group: 触发该风险的片区对应分组（多路定点监测时由摄像头绑定）；
                None 时用 risk_map 配置的分组。
    返回是否已下发喷淋指令，用于历史记录。
    """
    names = [d.get('chinese_name') or d.get('class_name') for d in detections]

    mapping = cfg_spray.get('risk_map', {}).get(risk_level, {}) if cfg_spray.get('risk_map') else {}
    group = mapping.get('group')
    duration = mapping.get('duration', 0)

    # 片区定向：摄像头绑定了分组、且映射不是"全体"时，只喷该片区
    spray_group = zone_group if (zone_group and group and group != 'all') else group

    sprayed = False
    if AUTO_SPRAY_ENABLED and risk_level in ('高风险', '中风险'):
        try:
            sprayed = spray.spray(risk_level, names, group_override=spray_group)
        except Exception:
            sprayed = False
    if sprayed:
        _record_history(risk_level, detections, group=spray_group, duration=duration, source='auto')

    # 语音播报：文案要与真实动作一致（"已给片区A喷淋3秒"），先算好再播
    if sprayed:
        spray_info = spray_phrase(spray_group or 'all', duration)
    else:
        spray_info = ''
    try:
        feedback.trigger(risk_level, names, '', spray_info=spray_info)
    except Exception:
        pass
    return sprayed


# ---------------------------------------------------------------- 共享推理泵（多客户端只跑一份推理）
def _zone_group_of(key):
    """相机 key → 片区分组（CONFIG.cameras 里 id 对应的 group）。"""
    cam_id = str(key[1] if isinstance(key, tuple) and len(key) > 1 else key).strip().lower()
    for c in cfg_cameras:
        if str(c.get('id', '')).strip().lower() == cam_id:
            return str(c.get('group', '') or '')
    return ''


async def _infer_pump(stop_event):
    """后台推理循环：每轮把**每一路**在线相机各推理一次，结果按相机 key 分开存。

    旧实现每轮只取 `_CAM_SOURCES` 的第一路：双片区时 B 区永远没有结果，
    而 B 区客户端读到的又是 A 区的全局结果（跨区污染：A 区虫情触发 B 区画面/播报）。
    """
    infer_fps = float(cfg_camera.get('infer_fps', 5) or 5)
    interval = 1.0 / max(infer_fps, 0.5)
    loop = asyncio.get_running_loop()
    while not stop_event.is_set():
        with _pump_lock:
            items = list(_CAM_SOURCES.items())
        if not items:
            # 没有相机（比如 /ws/phone 只用手机画面）时退化为空转
            await asyncio.sleep(0.3)
            continue
        t_round = time.time()
        for key, src in items:
            if stop_event.is_set():
                break
            ok, frame = src.read()
            if not ok or frame is None:
                continue
            try:
                result = await loop.run_in_executor(None, _predict_and_format, frame, None, False)
            except Exception:
                traceback.print_exc()
                await asyncio.sleep(0.5)
                continue
            risk_level = result['risk']['level']
            if risk_level in ('高风险', '中风险'):
                await loop.run_in_executor(
                    None, _linkage, risk_level, result['detections'], _zone_group_of(key))
            now = time.time()
            with _pump_lock:
                slot = _pump_state['by_key'].setdefault(key, {'seq': 0})
                slot['last'] = result
                slot['last_ts'] = now
                slot['seq'] += 1
                _pump_state['last'] = result
                _pump_state['last_ts'] = now
                _pump_state['seq'] += 1
                _pump_state['count'] += 1
        # 已下线的相机（客户端都走了）别把旧结果留给下一个订阅者
        with _pump_lock:
            for k in [k for k in _pump_state['by_key'] if k not in _CAM_SOURCES]:
                _pump_state['by_key'].pop(k, None)
        await asyncio.sleep(max(0.0, interval - (time.time() - t_round)))


def _ensure_pump():
    """确保推理泵在跑（在事件循环里调用）。"""
    with _pump_lock:
        task = _pump_state.get('task')
        if task is not None and not task.done():
            return task
        stop_event = asyncio.Event()
        _pump_state['stop'] = stop_event
        task = asyncio.create_task(_infer_pump(stop_event))
        _pump_state['task'] = task
        print('[INFO] 共享推理泵已启动', flush=True)
        return task


def _latest_result(key=None, max_age=None):
    """取共享推理的最新结果 (result, age, seq)。

    key 给定时只取**该路相机**的结果（多片区互不串台）；None 取全局最新一条。
    """
    with _pump_lock:
        if key is None:
            result = _pump_state.get('last')
            ts = _pump_state.get('last_ts') or 0.0
            seq = _pump_state.get('seq', 0)
        else:
            slot = _pump_state['by_key'].get(key) or {}
            result = slot.get('last')
            ts = slot.get('last_ts') or 0.0
            seq = slot.get('seq', 0)
    age = time.time() - ts if ts else None
    if result is None:
        return None, None, seq
    return result, age, seq
