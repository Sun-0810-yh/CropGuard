"""
CropGuard Web API Server
27 类农作物害虫检测：REST/WebSocket 接口 + 前端单页应用（web/dist）托管

启动方式:
    python api_server.py
    # 或指定端口:
    python api_server.py --port 8080 --host 0.0.0.0
"""

import os
import sys
import json
import time
import asyncio
import base64
import subprocess
import threading
import traceback
from datetime import datetime
import cv2
import numpy as np

# 控制台按 UTF-8 输出：Windows 默认 GBK 时，横幅里的 ✅/⚠️ 会直接
# UnicodeEncodeError 把服务打挂（实测）。errors=replace 保证永不因字符集崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 尝试导入FastAPI（可选依赖）
try:
    from fastapi import (FastAPI, File, UploadFile, Form, HTTPException, WebSocket,
                         WebSocketDisconnect, Request)
    from fastapi.responses import JSONResponse, Response, HTMLResponse, FileResponse
    from fastapi.concurrency import run_in_threadpool
    from fastapi.staticfiles import StaticFiles
    import uvicorn
    HAS_WEB = True
except ImportError:
    HAS_WEB = False
    print("[WARN] FastAPI/uvicorn not installed. Install: pip install fastapi uvicorn python-multipart websockets")

# 应用级共享状态（模型、反馈设备、相机池、历史记录）
from app.state import (
    config, yolo_model, device, weights_path, conf_thres, imgsz,
    feedback, spray, cfg_spray, cfg_cameras, camera_num, cfg_camera,
    camera_device, camera_spec,
    _CAM_SOURCES, _cam_kwargs, _camera_key, resolve_camera,
    _cam_subscribe, _cam_unsubscribe,
    cam_pool_state,
    spray_history, _last_result, _record_history,
)
# 推理与风险联动
from app.inference import (
    _predict_and_format, _with_advice, _linkage,
    _ensure_pump, _latest_result, _remember_sample,
    capture_snapshot, set_live_conf,
    set_auto_spray as set_auto_spray_linkage,
    auto_spray_enabled as auto_spray_linkage_enabled,
)

# 防治建议：知识库秒出兜底 + RAG 接地生成（Ollama）
from llm import knowledge_base
# 问答网关（RAG + 防幻觉）：GUI 聊天 / 网页 / 手机端共用这一个入口
from llm import ai_gateway
from llm.retriever import gate_score
# 前端单页应用（web/dist）——PC 端新前端；手机端仍走下面这套原生页面（/m）
from serve import spa
# 页面渲染（旧版 PC 首页 / 手机页 / 离线看板；保留为兜底与 /m 唯一入口）
from serve import mobile as mobile_page
from serve import pages as static_pages
# EdgeJev 本地类型化决策（离线 CPU 推理，部署见 README.md 第 9 节）
from app import edge_engine
# 指挥台「节点地址」面板的现场覆盖文件（不改 configs.yaml，见 app/spray_targets.py）
from app import spray_targets as spray_targets_file
# 现场测评评分口径（与 GUI 共用同一份纯函数）
from app.eval_score import rubric_from_config as _rubric_from_config, score_with_rubric

# ==================== FastAPI App ====================
app = FastAPI(
    title="CropGuard - 农作物害虫智能检测系统",
    description="27 类农作物害虫检测 REST API",
    version="2.0.0"
)

# ---------------- 前端页面：/ /stream /ask /spray /system /m 都交给前端路由 ----------------
if os.path.isdir(spa.ASSETS):
    app.mount("/assets", StaticFiles(directory=spa.ASSETS), name="assets")

# 原生页（/m-classic、/demo、/eval-classic）共用的静态资源：设计令牌等。
# tokens.css 由 scripts/sync_tokens.py 从 web/src/styles/tokens.css 生成，
# 保证「站点 + 手机端 + 指挥台」是同一套视觉（见 tests/test_design_tokens.py）。
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'serve', 'static')
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


def _spa_page():
    return spa.index_response()


for _path in spa.ROUTES:
    app.add_api_route(_path, _spa_page, methods=["GET"], include_in_schema=False)


@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    return spa.static_file('favicon.svg') or Response(status_code=404)


@app.get("/dashboard-offline", response_class=HTMLResponse)
async def dashboard_offline():
    """离线快照看板（**单文件、零联网请求**，可直接双击 serve/pages/dashboard.html 看）。

    新前端（Vue）的 `/dashboard` 是实时看板；这一页保留为兜底：
    断网 / 浏览器打不开前端 / 只想投屏一页静态数据时用它。
    快照由 scripts/build_dashboard.py 从真实产物生成（只改写 CG-SNAPSHOT 标记区）。
    """
    return static_pages.load('dashboard')


@app.get("/api/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "model_loaded": True,
        "classes": len(yolo_model.names),
        "device": device,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/info")
async def info():
    """模型信息"""
    chinese_name = config.get('CONFIG', {}).get('chinese_name', {})
    return {
        "model": weights_path,
        "device": device,
        "num_classes": len(yolo_model.names),
        "classes": {str(k): v for k, v in yolo_model.names.items()},
        "chinese_names": {k: chinese_name.get(v, v) for k, v in yolo_model.names.items()},
        "default_conf_threshold": conf_thres,
        "image_size": imgsz,
    }


@app.post("/api/detect")
async def detect(file: UploadFile = File(...), conf: float = Form(None)):
    """
    上传图片进行害虫检测
    - file: 图片文件 (multipart upload)
    - conf: 置信度阈值 (可选, 默认使用配置文件值)
    """
    if conf is None:
        conf = conf_thres

    try:
        # 读取图片
        contents = await file.read()
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            raise HTTPException(status_code=400, detail="无法解析图片文件")

        # 推理是 CPU/GPU 重活：丢线程池，别阻塞事件循环（否则实时流/WS 全部卡住）
        res = await run_in_threadpool(_predict_and_format, img, conf)
        _remember_sample(res)
        return _with_advice(res)

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )


@app.post("/api/detect_b64")
async def detect_base64(payload: dict):
    """
    Base64图片检测
    - image: Base64编码的图片字符串
    - conf: 置信度阈值 (可选)
    """
    image_b64 = payload.get("image", "")
    try:
        conf = float(payload.get("conf", conf_thres) or conf_thres)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="conf 必须是 0~1 的数字")

    if not image_b64:
        raise HTTPException(status_code=400, detail="缺少 'image' 字段")

    # 去除 data:image/...;base64, 前缀
    if ',' in image_b64:
        image_b64 = image_b64.split(',', 1)[1]

    try:
        try:
            img_bytes = base64.b64decode(image_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Base64 格式不正确")
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            raise HTTPException(status_code=400, detail="无法解码Base64图片")

        res = await run_in_threadpool(_predict_and_format, img, conf)
        _remember_sample(res)
        return _with_advice(res)

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )


@app.post("/api/advice")
def advice(payload: dict):
    """防治建议：统一走 RAG + 本地 Ollama（与 /api/ask、GUI 聊天同一入口）。

    body: {"detections": [...], "risk": {"level": "...", "detail": "..."}}
    返回: {"success", "source": "llm"|"knowledge_base"|"refused", "advice": "文本", ...}

    故意用普通 def（不是 async def）：FastAPI 会把它丢进线程池，
    避免本地大模型那几秒阻塞事件循环、把推理接口一起拖慢。
    """
    dets = payload.get("detections") or []
    risk = payload.get("risk") or {}
    if not dets:
        return {
            "success": True,
            "source": "knowledge_base",
            "advice": knowledge_base.NO_PEST_MSG,
        }
    names = "、".join(
        f"{d.get('chinese_name') or d.get('class_name')}"
        f"(置信度:{float(d.get('confidence') or 0):.2f})"
        for d in dets
    )
    question = f"检测到 {names}，风险等级 {risk.get('level', '')}，请给出防治建议。"
    try:
        res = ai_gateway.answer(question, context=payload)
    except Exception as exc:
        traceback.print_exc()
        return {
            "success": False,
            "source": "error",
            "advice": f"建议生成失败：{type(exc).__name__}: {str(exc)[:160]}",
        }
    # 保持旧字段 advice 兼容
    res["advice"] = res.pop("answer", "")
    return res


# ==================== 手机端页面 /m ====================
_cfg_phone = config.get('PHONE', {}) or {}
# 手机"实时模式"是否自动联动喷淋：默认关（方案二要求人工确认；演示第三套也必须手动）。
# 演示指挥台 /demo 可一键切换，无需改配置。
_phone_auto_spray = bool(_cfg_phone.get('live_auto_spray', False))
# 演示指挥台「打开评分 GUI」启动的子进程（None=没启动过 / 已退出）
_gui_proc = None
# 最近一次"一句话 → 决策"的结果（演示台上的"决策依据"卡片读它）
_last_command = {}
# 真实监听端口（由 __main__ 里用 --port/--https-port 写入），
# 供 /m 页面与 /qr 生成正确的手机访问地址。
# https_on 只有 HTTPS 监听**真的**起来了才为 True：页面据此决定要不要升级到 HTTPS，
# 绝不能拿配置里的 8443 当"能用"（那样 http 页会给出一个打不开的死链）。
_srv_ports = {'http': None, 'https': None, 'https_on': False}


def _lan_ips():
    """列出本机局域网 IPv4（手机要用它访问服务器，现场直接打印出来）。"""
    ips = []
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))          # 不会真的发包，只为拿到默认出口网卡
            ips.append(s.getsockname()[0])
        except Exception:
            pass
        finally:
            s.close()
    except Exception:
        pass
    try:
        import socket
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith('127.') and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def _https_on():
    """HTTPS 监听是否真的在服务（不是"配置里写了 8443"）。"""
    return bool(_srv_ports.get('https_on'))


def _phone_ports(request_host_port=None):
    """返回 (http_port, https_port)。

    HTTP 端口优先取"真实运行端口"（`--port` 指定的那个），
    其次才用请求头里的端口 —— 这样用 `--port 8090` 启动时，
    手机页里内嵌的接口地址会自动跟着变成 8090（否则页面调接口会连错端口）。
    """
    https_port = int(_srv_ports.get('https') or _cfg_phone.get('https_port', 8443) or 8443)
    # 优先用真实运行端口（__main__ 写入的 --port）；
    # 只有拿不到时（如 uvicorn api_server:app 方式启动）才回退请求头端口。
    # ⚠️ 不能反过来：用 https 端口打开 /m 时，请求头端口是 8443，
    #    若用它当 HTTP 端口，页面里的 http 接口地址会全部连错。
    http_port = int(_srv_ports.get('http') or 0)
    if not http_port and request_host_port:
        try:
            http_port = int(str(request_host_port).split(':')[-1])
        except Exception:
            pass
    return http_port or 8080, https_port


def _phone_url(ip=None, scheme=None):
    """手机端**唯一入口**地址（http/https 合一的那个）。

    HTTPS 可用就给 https（实时模式必须安全上下文），否则退回 http 拍照模式。
    """
    host = ip or (_lan_ips() or ['127.0.0.1'])[0]
    http_port, https_port = _phone_ports()
    if scheme is None:
        scheme = 'https' if _https_on() else 'http'
    port = https_port if scheme == 'https' else http_port
    return f'{scheme}://{host}:{port}/m'


@app.get("/api/lan")
async def lan_info(request=None):
    """给 PC 首页/手机页用：本机可被手机访问的地址清单。

    `https_enabled` 只在 HTTPS 监听真的起来后才为 true；未启用时
    `phone_urls_https` 返回空列表 —— 免得页面给出一个打不开的地址。
    """
    ips = _lan_ips()
    ports = _phone_ports()
    https_on = _https_on()
    first = (ips or ['127.0.0.1'])[0]
    return {
        "success": True,
        "hosts": ips,
        "http_port": ports[0],
        "https_port": ports[1],
        "https_enabled": https_on,
        "phone_url": _phone_url(first),
        "phone_url_http": f"http://{first}:{ports[0]}/m",
        "phone_urls": [f"http://{ip}:{ports[0]}/m" for ip in ips],
        "phone_urls_https": [f"https://{ip}:{ports[1]}/m" for ip in ips] if https_on else [],
    }


@app.get("/qr")
async def qr_image(url: str = None, size: int = 6):
    """把网址渲染成二维码 PNG（纯 PIL 手写实现，见 tool/qr_min.py）。

    现场用法：PC 首页把手机入口的二维码显示出来，手机一扫就进 /m，不用手输 IP。
    不传 url 时默认用"手机端唯一入口"（HTTPS 可用就是 https，扫码即实时模式）。
    """
    url = url or _phone_url()
    try:
        from io import BytesIO
        from tool.qr_min import to_pil
        img = to_pil(url, box_size=max(1, min(int(size or 6), 20)), border=3)
        buf = BytesIO()
        img.save(buf, format='PNG')
        return Response(content=buf.getvalue(), media_type='image/png')
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f'二维码生成失败: {exc}')


@app.get("/m-classic", response_class=HTMLResponse)
async def mobile_home(request: Request):
    """手机会话页（原生兜底版）：拍照识别 / 实时识别 / 问答 / 喷洒控制 / 雾化控制。

    2026-10-24 起手机端主入口 `/m` 由 Vue 接管（与站点同一套视觉），
    这一份原实现保留为 `/m-classic`：前端出问题时仍有"实测过"的后路
    （http→https 自动升级、同源 API、canvas 叠框、雾化卡片都在这一页里）。
    """
    host = (request.url.hostname or '')
    http_port, https_port = _phone_ports(request.url.port)
    mist_url = ''
    mist_targets = spray.mist_targets()
    if mist_targets:
        mist_url = str(mist_targets[0].get('url') or '')
    html = mobile_page.render(
        host=host, http_port=http_port, https_port=https_port,
        https_enabled=_https_on(),
        frame_interval_ms=int(_cfg_phone.get('frame_interval_ms', 400) or 400),
        upload_max_side=int(_cfg_phone.get('upload_max_side', 960) or 960),
        mist_url=mist_url,
        mist_max_sec=int(cfg_spray.get('max_duration', 5) or 5),
        mist_direct_sec=int(spray.direct_max_sec or 0),
    )
    return HTMLResponse(html)


@app.get("/classic", response_class=HTMLResponse)
async def classic_index():
    """旧版 PC 首页（原生 HTML）—— 前端重写前的那套页面，保留为兜底入口。

    新前端在 `/`（Vue）。现场万一构建产物/浏览器出问题，这一页仍能上传检测、
    看手机二维码，而且它不依赖 web/dist。
    """
    return static_pages.load('index')


@app.get("/demo", response_class=HTMLResponse)
async def demo_console():
    """演示指挥台：一个页面串起三套演示（20 样本评分 / 定点自动喷洒 / 手机联动）。

    刻意做成**单文件原生页**（不依赖 web/dist、不需要 Node）：
    现场只开这一个页面，按钮都在这，状态灯都在这，步骤也写在这。
    """
    return static_pages.load('demo')


@app.get("/api/client_config")
async def client_config(request: Request):
    """前端运行参数（原先由服务端模板写进 /m 页面，现在统一走接口）。"""
    http_port, https_port = _phone_ports(request.url.port)
    cfg_eval = config.get('EVAL', {}) or {}
    risk_map = {}
    for level, m in (cfg_spray.get('risk_map') or {}).items():
        m = m or {}
        try:
            dur = int(m.get('duration') or 0)
        except (TypeError, ValueError):
            dur = 0
        risk_map[str(level)] = {"group": m.get('group'), "duration": dur}
    return {
        "http_port": http_port,
        "https_port": https_port if _srv_ports.get('https') else None,
        "frame_interval_ms": int(_cfg_phone.get('frame_interval_ms', 400) or 400),
        "upload_max_side": int(_cfg_phone.get('upload_max_side', 960) or 960),
        "conf_default": conf_thres,
        "imgsz": imgsz,
        "model": os.path.basename(os.path.dirname(os.path.dirname(str(weights_path)))) or str(weights_path),
        "max_spray": int(cfg_spray.get('max_duration', 5) or 5),
        "eval_target": int(cfg_eval.get('target_samples', 20) or 20),
        "eval_time_limit": int(cfg_eval.get('time_limit_sec', 180) or 180),
        "eval_conf_default": float(cfg_eval.get('conf_default', conf_thres) or conf_thres),
        "refuse_below": float((config.get('CHAT', {}) or {}).get('refuse_below', 0.18)),
        "risk_map": risk_map,
    }


# ==================== 数据看板（快照由 scripts/build_dashboard.py 从真实产物生成） ====================
_DASH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output', 'dashboard')


@app.get("/api/dashboard")
def dashboard_snapshot():
    """看板快照：训练曲线、验证集逐类 mAP、实拍样本实测、RAG 评测、自检。没生成过就说明怎么生成。"""
    path = os.path.join(_DASH_DIR, 'snapshot.json')
    if not os.path.isfile(path):
        return {"available": False,
                "reason": "还没有生成看板快照：在项目根目录运行 python scripts/build_dashboard.py"}
    try:
        with open(path, encoding='utf-8') as f:
            snap = json.load(f)
    except Exception as exc:
        return {"available": False, "reason": f"快照文件读取失败：{exc}"}
    snap["available"] = True
    return snap


@app.get("/api/dashboard/thumb/{name}")
def dashboard_thumb(name: str):
    """样本缩略图。只认 build_dashboard.py 生成的 NN.jpg，不允许读别的路径。"""
    import re
    from fastapi.responses import FileResponse
    if not re.fullmatch(r'\d{1,4}\.jpg', name or ''):
        raise HTTPException(status_code=404)
    path = os.path.join(_DASH_DIR, 'thumbs', name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'no-cache'})


# ==================== 本地大模型问答（RAG + 防幻觉） ====================
_chat_cfg = config.get('CHAT', {}) or {}
_rag_cfg = config.get('RAG', {}) or {}
# EdgeJev 本地类型化决策（见 configs.yaml 的 EDGE 段）
_edge_cfg = config.get('EDGE', {}) or {}


@app.post("/api/ask")
def ask(payload: dict):
    """自由提问（网页 / 手机 / GUI 同一个网关）。

    body:
      {"question": "...",
       "history": [{"role":"user|assistant","content":"..."}],   # 可选
       "context": {"detections": [...], "risk": {...}}}          # 可选；缺省用最近一次检测

    返回：{"success","answer","source":"llm|knowledge_base|refused",
           "citations":[...],"engine","elapsed","guard":{...}}

    这里刻意用**普通 def**（不是 async def）：FastAPI 会丢进线程池执行，
    本地 7B 一次要几秒到几十秒，不能阻塞事件循环去拖慢推理与推流。
    """
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="缺少 'question' 字段")
    history = payload.get("history") or []
    context = payload.get("context") or None
    if context is None and _last_result.get("result"):
        # 没显式给上下文时，用最近一次检测结果，做到"结合当前虫情回答"
        context = _last_result["result"]
    try:
        return ai_gateway.answer(question, history=history, context=context)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={
            "success": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            "source": "error", "answer": "问答服务异常，请稍后重试。",
        })


@app.get("/api/kb/search")
def kb_search(q: str, top_k: int = None):
    """只做检索（不调用大模型）：现场展示 RAG 召回与分数用。"""
    try:
        r = ai_gateway.get_retriever()
        chunks = r.search(q or '', top_k=int(top_k or _rag_cfg.get('top_k', 4)))
        return {
            "success": True,
            "question": q,
            "rewritten": r.rewrite(q or '')[0],
            "detected_pests": r.detect_pests(q or ''),
            "refuse_below": float(_chat_cfg.get('refuse_below', 0.18)),
            # 闸门真实判据是 gate_score（见 llm/retriever.gate_score），预览口径必须一致
            "gate_score": gate_score(chunks),
            "will_refuse": (not chunks) or gate_score(chunks) < float(_chat_cfg.get('refuse_below', 0.18)),
            "chunks": [{"id": c['id'], "title": c['title'], "type": c['type'],
                        "cn": c['cn'], "score": c['score'],
                        "raw_score": c.get('raw_score', c['score']),
                        "gate_score": c.get('gate_score', c.get('raw_score', c['score'])),
                        "text": c['text'],
                        "source": c.get('source', '')} for c in chunks],
            "stats": r.stats(),
        }
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


@app.post("/api/ask_command")
def ask_command(payload: dict):
    """自然语言 → 喷洒意图（**默认只解析不执行**，需人工确认）。

    body: {"text": "给片区A喷3秒", "execute": false, "engine": "auto|edge|rule"}
      * execute=false（默认）：只返回解析结果，前端弹确认按钮；
      * execute=true：确认后真正下发（等价于 /api/spray），并写历史。
      * engine：解析引擎。auto（默认，配置 EDGE.prefer_for_commands 决定是否先用
        EdgeJev）/ edge（强制 EdgeJev）/ rule（强制规则解析）。

    安全要点（与引擎无关，永远不变）：解析出来的意图必须过安全网关 ——
    分组在白名单内、时长被钳制（SPRAY.max_duration）、危险表述拒绝、
    人工确认后才真正下发；类型化决策（EdgeJev）与规则解析都只是"理解"，不驱动硬件。
    """
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="缺少 'text' 字段")
    max_duration = int((cfg_spray.get('max_duration') or 5))

    # 既有规则解析始终算一份：规则成功就用规则（确定性）；
    # 规则拿不准时由 EdgeJev 带置信度闸门兜底（见 app/edge_engine.combine_command）。
    rule_parsed = ai_gateway.parse_command(text, max_duration=max_duration)

    engine_pref = str(payload.get("engine") or
                      ('auto' if _edge_cfg.get('prefer_for_commands', True) else 'rule')).lower()
    if engine_pref not in ('auto', 'edge', 'rule'):
        engine_pref = 'auto'
    parsed = dict(rule_parsed)
    parsed['parse_engine'] = 'rule'
    edge_info = None
    # 规则已经给出可用结果时不再跑模型：省一次推理、也避免"模型把规则结论改掉"的错觉。
    # 只有规则拿不准（valid=false / 没片区）才让 EdgeJev 带置信度闸门兜底。
    rule_done = bool(rule_parsed.get('valid') and rule_parsed.get('group'))
    want_edge = (engine_pref == 'edge') or (engine_pref == 'auto' and not rule_done)
    if want_edge:
        edge_out = {}
        if edge_engine.available():
            edge_out = edge_engine.parse_command(
                text, max_duration=max_duration,
                parse_duration=ai_gateway._parse_duration,
                block_words=ai_gateway._BLOCK_WORDS,
                rule_parser=ai_gateway.parse_command)   # 内部算 combined（规则优先 + 兜底闸门）
            if (edge_out or {}).get('success'):
                parsed = edge_out.get('combined') or parsed
                engine_used = parsed.get('engine')
                parsed['parse_engine'] = engine_used if engine_used in ('edge', 'rule') else 'rule'
                parsed['parse_latency_ms'] = edge_out.get('latency_ms')
                edge_info = {'mode': edge_out.get('mode'),
                             'latency_ms': edge_out.get('latency_ms'),
                             'intent': (edge_out.get('parsed') or {}).get('intent')}
            elif engine_pref == 'edge':
                # 强制 edge 但引擎没起来：如实报告，不假装解析成功
                return {"success": False, "error": (edge_out or {}).get('error') or 'EdgeJev 不可用',
                        "available": False, "engine": "edge"}
        elif engine_pref == 'edge':
            return {"success": False,
                    "error": (edge_engine.status(probe=False) or {}).get('last_error') or 'EdgeJev 不可用',
                    "available": False, "engine": "edge"}

    # 白名单跨传输形态：http 用 http_targets，serial/mqtt 用 nodes 登记表（见 spray.allowed_groups）
    allowed_groups = spray.allowed_groups
    if parsed.get('group') and parsed['group'] not in allowed_groups:
        parsed.update(valid=False, reason=f'分组 {parsed["group"]} 不在白名单',
                      reply='该片区未配置喷洒节点，无法执行。')

    executed = False
    if bool(payload.get("execute")) and parsed.get('valid') and parsed.get('group'):
        # 同步等节点应答：executed=True 只代表指令**真的送达**，不是"已丢给后台线程"
        if parsed.get('intent') == 'stop':
            executed, reason = spray.stop_detail(parsed['group'])
        else:
            executed, reason = spray.manual_spray_detail(parsed['group'], parsed['duration'])
        if not executed and reason:
            parsed['reply'] = f'指令未送达：{reason}'
        if executed:
            _record_history('语音指令喷洒', [], group=parsed['group'],
                            duration=parsed.get('duration') or 0,
                            source='phone-chat', via='chat')
    parsed.update(executed=bool(executed), available=bool(spray.available))
    if edge_info is not None:
        parsed['edge'] = edge_info
    # 记下最近一次决策（演示台"决策依据"卡片 / 答辩回看用；只在内存里）
    try:
        _last_command.clear()
        _last_command.update({
            'text': text, 'intent': parsed.get('intent'), 'group': parsed.get('group'),
            'duration': parsed.get('duration'), 'valid': bool(parsed.get('valid')),
            'reply': parsed.get('reply'), 'parse_engine': parsed.get('parse_engine'),
            'parse_latency_ms': parsed.get('parse_latency_ms'),
            'edge': edge_info, 'executed': bool(executed),
            'ts': datetime.now().strftime('%H:%M:%S'),
        })
    except Exception:
        pass
    return {"success": True, **parsed}


@app.get("/api/feedback/status")
async def feedback_status():
    """语音/串口反馈状态：现场排查"到底响不响"用。"""
    try:
        return {"success": True, "feedback": feedback.status()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ==================== EdgeJev 本地类型化决策（离线 CPU 推理） ====================
# 部署与设计见 app/edge_engine.py 与 README.md 第 9 节。
# ⚠️ 定位（2026-10-16 与用户对齐）：EdgeJev 的作用是**在系统预设的固定选项集上做"选择题"**
#    （choice / score / noul），把"理解/分流"变成可枚举、可校准、可审计的决策；
#    **不提供自由问答式的语言性决策**。因此对外只暴露"决策点"（见 /api/edge/decision_points），
#    通用 systemone 仅作开发/协议同构用（页面不暴露）。
#   GET  /api/edge/status           —— 引擎状态（模型/形态/延迟）
#   GET  /api/edge/decision_points  —— 决策点目录（选项集的单一事实源，页面据此渲染）
#   POST /api/edge/parse_command    —— 决策点①喷洒指令解析（只解析，不执行）
#   POST /api/edge/advise           —— 决策点②虫情处置研判（辅助参考，不驱动硬件）
#   POST /api/edge/systemone        —— 低级协议接口（开发用，页面不暴露）
# 刻意用普通 def：http 形态会同步访问本地服务，FastAPI 丢线程池，别堵事件循环。

@app.get("/api/edge/status")
def edge_status(refresh: bool = False):
    """EdgeJev 引擎状态：可用性/形态/模型信息/累计延迟统计 + 未部署时的构建指引。"""
    try:
        return edge_engine.status(refresh=bool(refresh))
    except Exception as exc:
        traceback.print_exc()
        return {"success": False, "available": False, "error": str(exc)}


@app.get("/api/edge/decision_points")
def edge_decision_points():
    """系统用 EdgeJev 做的"选择题"决策点目录（选项集/用途/状态的单一事实源）。

    status: active（已接入）/ experimental（可试跑，仅供参考）/ planned（规划中，待校准接入）。
    """
    try:
        return {"success": True, "points": edge_engine.decision_points()}
    except Exception as exc:
        traceback.print_exc()
        return {"success": False, "error": str(exc)}


@app.post("/api/edge/systemone")
def edge_systemone(payload: dict):
    """通用类型化决策 —— **开发/协议同构用**（官方 /v1/systemone 同构协议），页面不暴露。

    用途：团队开发**新决策点**时先用它试跑、校准（scripts/edge_probes 的同一口径），
    达标后再定义进 app/edge_engine.decision_points() 并接入链路。
    注意本模型定位是"固定选项集上的选择题"，不做自由问答式（语言性）决策。

    body: {"state": "文本或 JSON", "questions": {"qid": {"type": "choice|score|noul",
           "instructions": "...", "criteria": {...}|[...]}}}
    """
    state = payload.get("state")
    questions = payload.get("questions")
    if state is None or not isinstance(questions, dict) or not questions:
        raise HTTPException(status_code=400, detail="需要 'state' 与 'questions'（对象）")
    if len(questions) > 12:
        raise HTTPException(status_code=400, detail="单次最多 12 个问题")
    for qid, q in questions.items():
        if not isinstance(q, dict) or q.get("type") not in ("choice", "score", "noul") \
                or not q.get("instructions"):
            raise HTTPException(
                status_code=400,
                detail=f"问题 {qid} 格式不对：需要 type(choice|score|noul) + instructions")
    try:
        out = edge_engine.decide(state, questions,
                                 timeout=float(_edge_cfg.get('timeout') or 5.0))
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
    if not out.get("success") and not out.get("available"):
        return {"success": False, "available": False, "error": out.get("error"),
                "hint": "先跑 python scripts/build_edge_model.py 构建模型，见 /edge 页"}
    return out


@app.post("/api/edge/parse_command")
def edge_parse_command(payload: dict):
    """EdgeJev 喷洒指令解析（**只解析，不执行**；执行必须走人工确认 + /api/spray）。

    body: {"text": "把A区的水阀开一小会儿"}
    返回: {success, parsed{intent/group/duration/valid/reply...}, answers(原始决策), latency_ms}
    """
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="缺少 'text' 字段")
    max_duration = int((cfg_spray.get('max_duration') or 5))
    try:
        out = edge_engine.parse_command(
            text, max_duration=max_duration,
            parse_duration=ai_gateway._parse_duration,
            block_words=ai_gateway._BLOCK_WORDS,
            rule_parser=ai_gateway.parse_command)   # 内部同时给出 rule / parsed / combined
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
    return out


@app.get("/edge", response_class=HTMLResponse)
async def edge_page():
    """EdgeJev 决策点页（选择题式：指令解析 / 虫情研判 / 决策点规划；不做自由问答）。"""
    return static_pages.load('edge')


@app.post("/api/edge/advise")
def edge_advise(payload: dict = None):
    """虫情处置研判（类型化决策，辅助参考）。不改动自动联动的既有风险分级口径。"""
    payload = payload or {}
    dets = payload.get("detections")
    risk = payload.get("risk")
    if dets is None and _last_result.get("result"):
        dets = _last_result["result"].get("detections")
        risk = risk or _last_result["result"].get("risk")
    try:
        return edge_engine.decide_detection(dets or [], risk=risk)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


# ==================== Web 测评页 /eval（20 样本 / 180 秒，与 GUI 同口径） ====================
# 现场口径：准确度 20 + 时效性 10 = 30（见 configs.yaml EVAL 段与 app/eval_score.py）。
# 页面逻辑在 serve/templates/eval.html + serve/static/eval.js，这里只提供数据与落盘。


def _eval_sample_dir():
    """样本目录（相对路径按仓库根解析，和权重路径口径一致）。"""
    p = str(_rubric_from_config(config).get('sample_dir') or './pictures20')
    return p if os.path.isabs(p) else os.path.join(os.path.dirname(os.path.abspath(__file__)), p)


def _eval_report_dir():
    p = str(_rubric_from_config(config).get('report_dir') or './output/eval_reports')
    return p if os.path.isabs(p) else os.path.join(os.path.dirname(os.path.abspath(__file__)), p)


@app.post("/api/eval/live_conf")
def eval_live_conf(payload: dict = None):
    """评分页置信度滑块 → 实时出框门槛；conf 传 null 恢复配置默认值。

    只影响实时框显示和抓拍，喷淋/语音仍按配置门槛判断。
    """
    payload = payload or {}
    try:
        return {"success": True, "conf": set_live_conf(payload.get("conf"))}
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"success": False, "error": "conf 必须是数字"})


@app.post("/api/eval/capture")
def eval_capture(payload: dict = None):
    """拍照：优先用实时流里已出框的最好一帧，没有才现场连拍。普通 def，跑在线程池。"""
    payload = payload or {}
    key = _camera_key(payload.get("cam"))
    src = _CAM_SOURCES.get(key)
    if src is None:
        return JSONResponse(status_code=409, content={"success": False, "error": "相机还没打开，等实时画面出来再拍"})
    try:
        conf = payload.get("conf")
        res = capture_snapshot(key, src, None if conf is None else float(conf))
        if res is None:
            return JSONResponse(status_code=409, content={"success": False, "error": "摄像头当前没有画面"})
        _remember_sample(res)
        return _with_advice(res)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.get("/eval-classic", response_class=HTMLResponse)
async def eval_page():
    """现场测评页（原生兜底版，20 样本 / 180 秒）。

    2026-10-24 起 `/eval` 由 Vue 接管（并进检测台的「评分作业」模式）；
    这一份原生页保留为 `/eval-classic`，前端出问题或想单独投屏时仍可用。
    """
    from serve import eval_page as _eval_page
    return _eval_page.render(config=config)


@app.get("/api/eval/config")
async def eval_config():
    """测评页口径参数 + 样本目录状态（页面据此决定"服务器载入"还是"上传模式"）。"""
    from app.eval_score import list_sample_images
    rubric = _rubric_from_config(config)
    samples = list_sample_images(_eval_sample_dir())
    return {
        'success': True,
        **rubric,
        'sample_dir': _eval_sample_dir(),
        'sample_dir_exists': os.path.isdir(_eval_sample_dir()),
        'sample_count': len(samples),
        'samples': samples[:200],
        'report_dir': _eval_report_dir(),
    }


@app.get("/api/eval/samples")
async def eval_samples():
    """服务器侧样本清单（页面「从服务器载入」用）。"""
    from app.eval_score import list_sample_images
    samples = list_sample_images(_eval_sample_dir())
    return {'success': True, 'dir': _eval_sample_dir(), 'count': len(samples),
            'samples': samples}


@app.get("/api/eval/sample_image")
def eval_sample_image(name: str):
    """读一张服务器侧样本图（只在样本目录白名单内取，防目录穿越）。"""
    from fastapi.responses import FileResponse
    from app.eval_score import is_image_name
    base = os.path.basename(name or '')
    if not base or base != name or not is_image_name(base):
        raise HTTPException(status_code=400, detail='非法文件名')
    full = os.path.join(_eval_sample_dir(), base)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail=f'样本不存在: {base}')
    return FileResponse(full)


@app.post("/api/eval/score")
def eval_score_api(payload: dict):
    """按现场口径算分（与 GUI 同一份纯函数；records 只读 verdict 字段）。"""
    rubric = _rubric_from_config(config)
    records = payload.get('records') or []
    elapsed = payload.get('elapsed') or 0
    return {'success': True, 'score': score_with_rubric(records, elapsed, rubric),
            'rubric': {k: rubric[k] for k in ('target', 'pps', 'limit', 'time_full',
                                              'penalty', 'acc_full', 'total_full')}}


@app.post("/api/eval/report")
def eval_report(payload: dict):
    """导出评分报告到 EVAL.report_dir（格式与 GUI 导出的 csv 对齐：逐样本 + 汇总）。

    body: {"records": [{no,name,time,detections:[{chinese_name,confidence}],
           consum,elapsed,verdict}], "elapsed": 87.3}
    """
    from tool.tools import writecsv
    from app.eval_score import build_report_rows
    rubric = _rubric_from_config(config)
    records = payload.get('records') or []
    if not records:
        raise HTTPException(status_code=400, detail='还没有样本，无法导出评分报告')
    elapsed = payload.get('elapsed') or 0
    rows, score = build_report_rows(records, elapsed, rubric)

    try:
        out_dir = _eval_report_dir()
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        name = f'eval_report_web_{stamp}.csv'
        path = os.path.join(out_dir, name)
        writecsv(rows, path)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={'success': False, 'error': str(exc)})
    print(f'[EVAL] Web 测评报告已导出：{path}（总分 {score["score"]:.1f} / {rubric["total_full"]:.0f}）',
          flush=True)
    return {'success': True, 'path': path, 'name': name,
            'url': f'/api/eval/report/download?name={name}', 'score': score}


@app.get("/api/eval/report/download")
def eval_report_download(name: str):
    """下载导出的评分报告（只在 report_dir 内取，防目录穿越）。"""
    from fastapi.responses import FileResponse
    base = os.path.basename(name or '')
    if not base or base != name:
        raise HTTPException(status_code=400, detail='非法文件名')
    full = os.path.join(_eval_report_dir(), base)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail='报告不存在')
    return FileResponse(full, filename=base, media_type='text/csv')


# ==================== 评分快照（拍照即存，供回看） ====================
# 评分作业的样本是"实时画面里拍一张"，必须把带框的结果图落盘，否则刷新页面就没法回看。
# 纯逻辑（dataURL 校验 / 安全路径）在 app/eval_snapshot.py，可被单测直接覆盖；
# 目录与 GUI 保持一致（config CAMERA.history_dir，默认 ./output/snapshots），
# 这样评委用 GUI 拍的、用网页拍的，都躺在同一个文件夹里。
def _eval_snapshot_dir():
    """评分快照目录（与 GUI 的 _save_snapshot 同一个目录）。"""
    p = str((cfg_camera or {}).get('history_dir') or './output/snapshots')
    return p if os.path.isabs(p) else os.path.join(_ROOT, p)


@app.post("/api/eval/snapshot")
def eval_snapshot_save(payload: dict = None):
    """把一张带框图落盘。body: {"image": "data:image/jpeg;base64,..."}。

    写盘失败**不抛 500**：返回 success=false + 原因，评分记录照常进行（快照只用于回看）。
    """
    from app import eval_snapshot
    payload = payload or {}
    try:
        got = eval_snapshot.save(payload.get('image'), _eval_snapshot_dir())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        traceback.print_exc()
        return {'success': False, 'url': None, 'name': None,
                'error': f'快照保存失败：{type(exc).__name__}: {exc}'}
    return {'success': True, 'name': got['name'], 'path': got['path'],
            'url': f'/api/eval/snapshot?name={got["name"]}'}


@app.get("/api/eval/snapshot")
def eval_snapshot_get(name: str):
    """读回一张快照（只在快照目录内取，防目录穿越）。"""
    from fastapi.responses import FileResponse
    from app import eval_snapshot
    try:
        full = eval_snapshot.path_of(name, _eval_snapshot_dir())
    except ValueError as exc:
        raise HTTPException(status_code=404 if '不存在' in str(exc) else 400, detail=str(exc))
    return FileResponse(full)


@app.get("/api/history")
async def history():
    """联动/喷淋历史回看。"""
    return {"history": spray_history}


def _check_group(group):
    """分组白名单：手动喷淋/停止与聊天指令同一口径（spray.allowed_groups）。"""
    group = str(group or 'all').strip()
    if group not in spray.allowed_groups:
        raise HTTPException(status_code=400,
                            detail=f"分组 {group} 不在白名单 {sorted(spray.allowed_groups)}")
    return group


# 仓库里的占位地址：现场必须按节点串口打印的 IP 改（见 configs.yaml 的 SPRAY.http_targets 注释）
SPRAY_PLACEHOLDER_IPS = ('172.23.153.231',)


def _spray_placeholder(rows):
    """地址表是不是"还没改过"：含仓库占位 IP，或两个分组指向同一台地址。"""
    urls = [str((r or {}).get('url', '')) for r in (rows or [])]
    if any(ip in u for u in urls for ip in SPRAY_PLACEHOLDER_IPS):
        return True
    return len(urls) > 1 and len(set(urls)) == 1


@app.get("/api/spray/targets")
def spray_targets_get():
    """当前 http 节点地址表（/demo 的「节点地址」面板数据源）。

    placeholder=True 表示还是 configs.yaml 里的占位地址，页面会直接提示去改。
    """
    rows = spray.http_target_rows()
    return {
        "success": True,
        "transport": spray.transport,
        "targets": rows,
        "placeholder": _spray_placeholder(rows),
        "overlay": spray_targets_file.exists(),
        "protocols": sorted(spray.PROTOCOLS),
    }


@app.post("/api/spray/targets")
def spray_targets_apply(payload: dict = None):
    """现场热改节点地址：不碰 configs.yaml，也不用重启服务。

    body: {"targets": [{"group": "g1", "url": "http://10.130.161.50", "protocol": "mist"}, ...],
           "persist": true}     # persist=False 只改内存（临时试一下，重启即恢复）

    为什么要有这个端点：节点是 DHCP 拿地址、只印在串口上；改 YAML 要重启服务，
    比赛现场重启风险高，而且容易把 YAML 里的方案说明注释写坏。
    persist=True 时落到 config/spray_targets.local.json，下次启动自动生效。
    """
    payload = payload or {}
    persist = bool(payload.get("persist", True))
    applied, errors = spray.set_http_targets(payload.get("targets") or [])
    saved = ''
    if applied and persist:
        try:
            saved = spray_targets_file.save(applied)
        except Exception as exc:
            errors.append(f'写入覆盖文件失败（本次已生效，重启会丢）：{exc}')
    # max_age=0：刚改完地址必须立刻真连一次，不能拿旧缓存糊弄现场
    nodes = spray.probe_nodes(max_age=0) if applied else []
    print(f"[SPRAY] 节点地址已更新：{[(t['group'], t['url']) for t in applied]} "
          f"persist={persist} saved={saved or '-'}", flush=True)
    return {
        "success": bool(applied),
        "applied": applied,
        "errors": errors,
        "saved": saved,
        "transport": spray.transport,
        "nodes": nodes,
        "placeholder": _spray_placeholder(applied),
    }


@app.get("/api/spray/status")
def spray_status():
    """喷淋链路概况：页面据此渲染片区按钮（不再在前端写死 g1/g2）。

    available 只表示"链路已配置"；节点此刻在不在线看 nodes（http 节点 GET /status 探活，3 秒缓存）。
    普通 def：探活要等网络，丢线程池。
    """
    groups = sorted(g for g in spray.allowed_groups if g != 'all')
    return {
        "success": True,
        "available": bool(spray.available),
        "transport": spray.transport,
        "groups": groups,
        "max_duration": int(cfg_spray.get('max_duration', 5) or 5),
        "zones": [{"id": c.get('id'), "name": c.get('name') or c.get('id'),
                   "group": c.get('group')} for c in cfg_cameras],
        "nodes": spray.probe_nodes() if spray.enabled else [],
    }


@app.post("/api/spray")
def manual_spray(payload: dict):
    """手动喷淋端点（Client 按钮 / 语音指令确认后）。

    body: {"group": "all"|"g1"|"g2", "duration": 秒, "via": "web|phone|chat"}

    普通 def：下发走 HTTP/串口，可能阻塞到超时，丢线程池执行。
    """
    group = _check_group(payload.get("group", "all"))
    via = str(payload.get("via") or "manual")
    max_duration = int(cfg_spray.get('max_duration', 5) or 5)
    try:
        duration = int(payload.get("duration", 2))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="duration 必须是正整数（秒）")
    if duration <= 0:
        # 传 0/负数的语义是"别喷"，绝不能默默改成默认值去执行
        raise HTTPException(status_code=400, detail="duration 必须为正数；要停止喷淋请用 /api/spray/stop")
    if duration > max_duration:
        duration = max_duration
    # 同步等节点应答：success 只在指令真的送达时为 True（以前丢给后台线程就报成功）
    ok, reason = spray.manual_spray_detail(group, duration)
    if ok:
        _record_history('手动喷淋', [], group=group, duration=duration, source='manual', via=via)
    return {
        "success": ok,
        "reason": reason,
        "gateway_available": spray.available,
        "group": group,
        "duration": duration,
        "max_duration": max_duration,
    }


# ==================== 雾化控制（手写 sketch 固件接口） ====================
# 手写固件（sketch_oct12a.ino）的接口与项目固件不同：
#   GET /start?time=N 定时喷雾 / GET /startDirect 持续喷雾（不自停）/ GET /stop 停止
# 而且它**永远回 200**（连"雾化正在运行中，请先关闭"也是 200），所以适配层读响应体判成败
# （见 feedback/spray.py 的 _write_mist）。
#
# ⚠️ 这几个端点刻意用**普通 def**：内部会同步访问节点 HTTP，FastAPI 会把它丢进线程池，
#    不会阻塞事件循环把推理/推流一起拖慢（与 /api/ask 同一个理由）。

@app.get("/api/mist/status")
def mist_status(group: str = None):
    """读回雾化节点状态（GET 节点的状态页并解析"定时/持续/空闲"与剩余秒数）。

    连不上时**如实返回 reachable=false + 原因**，不静默（现场排查靠它）。
    """
    try:
        return {"success": True, **spray.mist_status(group or "all")}
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={
            "success": False, "error": f"{type(exc).__name__}: {exc}"})


@app.post("/api/mist/start")
def mist_start(payload: dict):
    """定时喷雾：body {"seconds": 3, "group": "all"}。

    seconds 由后端按 SPRAY.max_duration 钳制（雾化片怕连续通电），返回里带 clamped 标记。
    """
    payload = payload or {}
    group = str(payload.get("group") or "all")
    try:
        seconds = int(payload.get("seconds", payload.get("duration", 3)))
    except (TypeError, ValueError):
        seconds = 3
    res = spray.mist_start(seconds, group)
    if res.get("success"):
        _record_history('雾化·定时', [], group=group, duration=res.get("duration") or 0,
                        source='mist', via='phone-mist')
    return {"success": bool(res.get("success")), **res}


@app.post("/api/mist/direct")
def mist_direct(payload: dict = None):
    """持续喷雾（固件的 /startDirect 不会自停）。

    服务器武装兜底计时器，SPRAY.direct_max_sec 秒后自动 GET /stop ——
    手机掉线、页面被关掉、人走开都不会让继电器一直吸合。
    """
    payload = payload or {}
    group = str(payload.get("group") or "all")
    res = spray.mist_start_direct(group)
    if res.get("success"):
        _record_history('雾化·持续', [], group=group, duration=None,
                        source='mist', via='phone-mist')
    return {"success": bool(res.get("success")), **res}


@app.post("/api/mist/stop")
def mist_stop(payload: dict = None):
    """停止雾化（同步等结果，并撤掉持续模式兜底计时器）。"""
    payload = payload or {}
    group = str(payload.get("group") or "all")
    res = spray.mist_stop(group)
    if res.get("success"):
        _record_history('雾化·停止', [], group=group, duration=None,
                        source='mist', via='phone-mist')
    return {"success": bool(res.get("success")), **res}


@app.post("/api/spray/stop")
def spray_stop(payload: dict = None):
    """立即停止喷淋（紧急停止入口）。

    body: {"group": "all"|"g1"|"g2"}（缺省 all）
    http 节点走固件 /stop；serial/mqtt 走 duration=0（固件协议：<=0 即停止）。
    """
    payload = payload or {}
    group = _check_group(payload.get("group", "all"))
    ok, reason = spray.stop_detail(group)
    _record_history('手动停止喷淋', [], group=group, duration=0, source='manual',
                    via=str(payload.get("via") or "manual"))
    return {"success": bool(ok), "reason": reason, "group": group,
            "gateway_available": spray.available}


# ==================== 演示指挥台 /demo 的接口 ====================
# 设计目标：让第一次上台的人只记一个地址（/demo）和三个大按钮。
_ROOT = os.path.dirname(os.path.abspath(__file__))
_GUI_LOG = os.path.join(_ROOT, 'output', 'gui_launch.log')


def _port_open(host, port, timeout=0.3):
    """轻量探活（不依赖 requests；Ollama 状态灯用）。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _gui_state():
    """评分 GUI（main.py）是否还活着。"""
    global _gui_proc
    if _gui_proc is None:
        return {'running': False, 'pid': None, 'exit_code': None}
    code = _gui_proc.poll()
    if code is not None:
        _gui_proc = None
        return {'running': False, 'pid': None, 'exit_code': code}
    return {'running': True, 'pid': _gui_proc.pid, 'exit_code': None}


@app.get("/api/demo/status")
def demo_status():
    """指挥台状态灯：一眼看出哪一环没就绪（现场排错全靠它）。"""
    pool = {}
    try:
        pool = cam_pool_state()
    except Exception:
        pool = {}
    cam = next(iter(pool.values()), {}) if pool else {}
    spray_st = {'available': bool(spray.available), 'transport': spray.transport,
                'groups': sorted(g for g in spray.allowed_groups if g != 'all'),
                'nodes': [], 'auto': auto_spray_linkage_enabled(),
                'targets': spray.http_target_rows(),
                'placeholder': _spray_placeholder(spray.http_target_rows()),
                'overlay': spray_targets_file.exists()}
    try:
        if spray.enabled:
            spray_st['nodes'] = spray.probe_nodes()
    except Exception as exc:
        spray_st['error'] = f'{type(exc).__name__}: {exc}'
    tts = {}
    try:
        tts = feedback.status().get('tts', {}) or {}
    except Exception:
        tts = {}
    edge = {'available': False, 'mode': 'none'}
    try:
        st = edge_engine.status() or {}
        edge = {'available': bool(st.get('available')), 'mode': st.get('mode'),
                'avg_ms': st.get('avg_ms'), 'hint': st.get('hint') or st.get('last_error') or ''}
    except Exception as exc:
        edge['error'] = f'{type(exc).__name__}: {exc}'
    snap = os.path.join(_ROOT, 'output', 'dashboard', 'snapshot.json')
    return {
        'success': True,
        'time': datetime.now().strftime('%H:%M:%S'),
        'model': {'classes': len(yolo_model.names), 'device': device,
                  'weights': os.path.basename(str(weights_path))},
        'camera': {'open': bool(pool), 'state': cam.get('state'),
                   'fps': cam.get('fps'), 'last_error': cam.get('last_error'),
                   # 正在用哪台：标识 / 落在哪个索引 / 设备名（演示台显示，现场一眼核对）
                   'device': cam.get('device'), 'index': cam.get('index'),
                   'spec': cam.get('source_spec', camera_spec),
                   'configured_spec': camera_spec},
        'spray': spray_st,
        'tts': {'ready': bool(tts.get('ready')), 'engine': tts.get('engine')},
        'ollama': {'up': _port_open('127.0.0.1', 11434)},
        'edge': edge,
        'dashboard': {'snapshot': os.path.isfile(snap)},
        'phone': {'auto_spray': bool(_phone_auto_spray), 'url': _phone_url()},
        'gui': _gui_state(),
    }


@app.post("/api/demo/auto_spray")
def demo_auto_spray(payload: dict = None):
    """总闸：自动联动要不要真的喷（关掉后只播语音，手动按钮不受影响）。"""
    payload = payload or {}
    enabled = bool(payload.get('enabled', True))
    return {'success': True, 'auto_spray': set_auto_spray_linkage(enabled)}


@app.post("/api/demo/phone_auto")
def demo_phone_auto(payload: dict = None):
    """手机实时模式是否自动喷（默认关：方案二/第三套都要求人工确认）。"""
    global _phone_auto_spray
    payload = payload or {}
    _phone_auto_spray = bool(payload.get('enabled', False))
    return {'success': True, 'phone_auto_spray': _phone_auto_spray}


@app.get("/api/demo/last_command")
def demo_last_command():
    """最近一次"一句话 → 决策"的结果（含规则/EdgeJev 用了哪个）。"""
    return {'success': True, 'last': dict(_last_command)}


@app.post("/api/demo/launch_gui")
def demo_launch_gui():
    """一键打开 20 样本评分 GUI（main.py）。已经开着就直接返回。"""
    global _gui_proc
    st = _gui_state()
    if st['running']:
        return {'success': True, 'already': True, **st}
    script = os.path.join(_ROOT, 'main.py')
    if not os.path.isfile(script):
        return {'success': False, 'error': f'找不到 {script}'}
    try:
        os.makedirs(os.path.dirname(_GUI_LOG), exist_ok=True)
        log = open(_GUI_LOG, 'ab', buffering=0)
        _gui_proc = subprocess.Popen(
            [sys.executable, script], cwd=_ROOT,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    except Exception as exc:
        traceback.print_exc()
        return {'success': False, 'error': f'{type(exc).__name__}: {exc}'}
    time.sleep(0.8)
    st = _gui_state()
    return {'success': st['running'], 'already': False, **st,
            'log': os.path.relpath(_GUI_LOG, _ROOT)}


@app.post("/api/demo/close_gui")
def demo_close_gui():
    """关掉评分 GUI（演示结束/相机被占用时用）。"""
    global _gui_proc
    st = _gui_state()
    if not st['running']:
        return {'success': True, 'already': True, **st}
    try:
        _gui_proc.terminate()
        try:
            _gui_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _gui_proc.kill()
    except Exception as exc:
        return {'success': False, 'error': f'{type(exc).__name__}: {exc}'}
    _gui_proc = None
    return {'success': True, 'already': False, 'running': False, 'pid': None, 'exit_code': None}


@app.get("/api/camera/health")
async def camera_health(cam: str = None):
    """相机健康：状态/帧龄/重开次数/实测 fps。

    不传 cam 时返回整个相机池（多路片区时能一眼看到哪路死了）。
    """
    pool = cam_pool_state()
    if cam:
        return {"success": True, "key": str(_camera_key(cam)), "pool": pool}
    return {"success": True, "pool": pool, "camera_num": camera_num,
            # 现场核对"到底用哪台相机"：标识 → 索引 → 设备名（见 tool/camera_source）
            "camera_device": camera_device, "camera_spec": camera_spec,
            "configured_zones": [c.get('id') for c in cfg_cameras]}


@app.post("/api/camera/restart")
def camera_restart(payload: dict = None):
    """手动强制重开相机（现场抢救用）：换 handle + 重建采集线程。

    普通 def：开设备/等首帧要好几秒，不能卡住事件循环。
    没人订阅时只做"能不能打开"的验证，用完即释放（不再泄漏一份引用计数）。
    """
    payload = payload or {}
    cam_id = payload.get("cam")
    key = _camera_key(cam_id)
    source, _zone_group, zone_label = resolve_camera(cam_id)
    src = _cam_subscribe(key, source=source, name=zone_label or str(source))
    try:
        ok = src.restart()
        # 与 /ws/stream 同口径：外接 USB 冷启动可能 7 秒以上才出图，只等 3 秒会误报"重启失败"
        src.wait_first_frame(timeout=float(cfg_camera.get('probe_timeout', 12.0) or 12.0))
        return {"success": bool(ok), "key": str(key), "state": src.state()}
    finally:
        _cam_unsubscribe(key)


@app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket):
    """摄像头实时流：共享采集 → 推理 → 推 JPEG + 结果 + 风险；中/高风险自动联动喷淋 + 语音。

    可选 query 参数 cam（片区 id，如 'A'/'B'）：多路定点监测时选择片区摄像头。

    与旧版本的区别（重要）：
    * 多个客户端（手机 + PC）共享同一个采集源，不再各开一个 VideoCapture；
    * 掉线/黑屏由看门狗自愈，客户端不会被卡死（只会收到 camera_down 提示）。
    """
    await websocket.accept()

    cam_id = websocket.query_params.get('cam') or websocket.query_params.get('zone')
    source, zone_group, zone_label = resolve_camera(cam_id)
    key = _camera_key(cam_id)

    loop = asyncio.get_running_loop()
    try:
        # 每个连接订阅一次，与 finally 里的退订一一对应。
        # 打开设备可能要好几秒（冷启动/多策略解析）→ 放线程池，别卡住其它连接
        cam = await loop.run_in_executor(
            None, lambda: _cam_subscribe(key, source=source, name=zone_label or str(source)))
    except Exception as exc:
        traceback.print_exc()
        await websocket.send_text(json.dumps(
            {"error": f"无法打开摄像头 {zone_label or source}: {exc}"},
            ensure_ascii=False))
        await websocket.close()
        return

    wait_sec = float(cfg_camera.get('probe_timeout', 12.0) or 12.0)
    got = await loop.run_in_executor(None, cam.wait_first_frame, wait_sec)
    if not got:
        st = cam.state()
        # 有 last_error 就原样下发：里面已经区分了"打不开"和"能打开但没画面"，别再套一层括号
        err = (st.get('last_error')
               or f'无法打开摄像头 {zone_label or source}：{wait_sec:.0f} 秒内没拿到首帧；'
                  '可调大 config/configs.yaml 的 CAMERA.probe_timeout，或检查 USB 口 / 是否被占用')
        await websocket.send_text(json.dumps({"error": err, "camera": st}, ensure_ascii=False))
        await websocket.close()
        _cam_unsubscribe(key)
        return

    stream_fps = float(cfg_camera.get('stream_fps', 15) or 15)
    # 共享推理泵：多个客户端只跑一份推理，避免 GPU 重复计算
    _ensure_pump()

    last_push = 0.0
    last_seq = -1
    try:
        while True:
            ok, frame = cam.read()
            if not ok or frame is None:
                # 看门狗正在自愈：告诉客户端一声，等帧，不退出
                st = cam.state()
                if st.get('state') != 'ok':
                    await websocket.send_text(json.dumps(
                        {"warning": "摄像头暂时无帧，正在自动重连",
                         "camera": st}, ensure_ascii=False))
                await asyncio.sleep(0.2)
                continue

            # 只读**本路相机**的推理结果：多片区时 A 区虫情不会出现在 B 区画面上
            result, age, seq = _latest_result(key)
            if result is None or seq == last_seq:
                # 两次推理之间（推流 15fps、推理 5fps）推最新画面 + **最近一次**检测结果。
                # 以前这里一律发"无风险 / 0 个目标"，页面上的风险等级在真实值与无风险之间来回闪。
                now = time.time()
                if now - last_push >= 1.0 / stream_fps:
                    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if result is not None:
                        last = {"count": result.get("count", 0),
                                "detections": result.get("detections") or [],
                                "risk": result.get("risk")}
                    else:
                        last = {"count": 0, "detections": [],
                                "risk": {"level": "无风险", "detail": "正在推理中…"}}
                    payload = {
                        "success": True, **last,
                        "result_image": base64.b64encode(buffer).decode('utf-8'),
                        "image_size": [int(frame.shape[1]), int(frame.shape[0])],
                        "timestamp": datetime.now().isoformat(),
                        "camera": cam.state(),
                        "result_age": round(age, 2) if age is not None else None,
                    }
                    if zone_label:
                        payload["zone"] = zone_label
                    await websocket.send_text(json.dumps(payload, ensure_ascii=False))
                    last_push = now
                await asyncio.sleep(0.02)
                continue

            now = time.time()
            if now - last_push >= 1.0 / stream_fps:
                payload = dict(result)
                if zone_label:
                    payload["zone"] = zone_label
                payload["camera"] = cam.state()
                payload["result_age"] = round(age, 2) if age is not None else None
                await websocket.send_text(json.dumps(payload, ensure_ascii=False))
                last_push = now
                last_seq = seq

            await asyncio.sleep(0.005)
    except WebSocketDisconnect:
        pass
    except Exception:
        traceback.print_exc()
    finally:
        # 只减引用计数：最后一个订阅者离开才真正关设备、才从推理泵里摘掉。
        # （旧代码第一个客户端断开就把 key 从 _CAM_SOURCES 删了，另一个客户端还在看，
        #   推理泵却从此不再推理这一路 —— 画面还在、检测框永远不动。）
        _cam_unsubscribe(key)

# ==================== 手机端实时上传（二进制帧） ====================
@app.websocket("/ws/phone")
async def ws_phone(websocket: WebSocket):
    """手机摄像头实时识别：手机上传 JPEG 二进制帧，服务端只回检测 JSON。

    为什么不用 HTTP 轮询：手机 2~3 帧/秒上传，用 WebSocket 省掉每帧的握手开销，
    而且能天然做"单帧背压"（客户端收到结果才发下一帧）。
    为什么**不回传图像**：手机流量与延迟都金贵，检测框由前端自己叠在预览上
    —— 见 `serve/static/mobile.js` 的 `drawDetections()`（canvas 叠加，已实现）；
    bbox 是"上传那一帧"的绝对像素坐标，前端按 `sentFrame` 尺寸 + `object-fit:contain`
    的实际显示矩形映射（`fitRect` / `boxToViewRect`）。改这里要同步改前端。

    协议：客户端每帧发一个二进制消息（JPEG/PNG 原始字节）；
         服务端回文本 JSON：{success, count, detections, risk, elapsed, ts}
         出问题时回 {"error": "..."} 或 {"warning": "..."}。
    """
    await websocket.accept()
    n_frames = 0
    try:
        while True:
            msg = await websocket.receive()
            if msg.get('type') == 'websocket.disconnect':
                break
            data = msg.get('bytes')
            if not data:
                # 文本消息：当作心跳/控制
                txt = msg.get('text') or ''
                if txt.strip() in ('ping', '"ping"'):
                    await websocket.send_text(json.dumps({"pong": True}))
                continue

            nparr = np.frombuffer(data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                await websocket.send_text(json.dumps(
                    {"error": "无法解析手机上传的图像帧"}, ensure_ascii=False))
                continue

            t0 = time.time()
            try:
                result = await asyncio.get_running_loop().run_in_executor(
                    None, _predict_and_format, img)
            except Exception as exc:
                traceback.print_exc()
                await websocket.send_text(json.dumps(
                    {"error": f"推理失败: {exc}"}, ensure_ascii=False))
                continue

            n_frames += 1
            payload = {
                "success": True,
                "count": result["count"],
                "detections": result["detections"],
                "risk": result["risk"],
                "elapsed": round(time.time() - t0, 3),
                "frame_no": n_frames,
                "ts": datetime.now().strftime('%H:%M:%S'),
            }
            # 手机实时流：默认**不自动喷**（方案二要求人工确认；演示第三套也要手动）。
            # 需要自动时由 /demo 指挥台打开 _phone_auto_spray。
            risk_level = result["risk"]["level"]
            if _phone_auto_spray and risk_level in ('高风险', '中风险'):
                # 联动里有喷淋 HTTP 下发（可能阻塞到超时）→ 不等它，先把结果回给手机
                asyncio.get_running_loop().run_in_executor(
                    None, _linkage, risk_level, result["detections"])
            await websocket.send_text(json.dumps(payload, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    except Exception:
        traceback.print_exc()
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ==================== HTTPS 启动辅助 ====================
def _port_busy(host, port):
    """preflight：这个端口能不能 bind。True=被占用/不可用。"""
    import socket as _socket
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        s.bind((host or '0.0.0.0', int(port)))
        return False
    except OSError:
        return True
    finally:
        try:
            s.close()
        except Exception:
            pass


def _https_selfcheck(port, timeout=8):
    """起完 HTTPS 后真发一次请求（跳过证书校验）。

    只看"线程还在跑"是不够的 —— 端口被占时 uvicorn 会静默退出，
    必须真连一次才能说 HTTPS 可用。
    """
    import ssl as _ssl
    import urllib.request
    try:
        ctx = _ssl._create_unverified_context()
        with urllib.request.urlopen(f'https://127.0.0.1:{int(port)}/api/health',
                                    context=ctx, timeout=timeout) as r:
            return True, f'HTTP {r.status}'
    except Exception as exc:
        return False, f'{type(exc).__name__}: {str(exc)[:120]}'


def _start_https(args):
    """起 HTTPS 监听：证书 → 端口 preflight → 线程 → 等就绪 → 自检。

    返回 (uvicorn.Server | None, 原因字符串)。失败时一定把已经起来的监听关掉，
    绝不让"8443 在听但 _srv_ports['https_on'] 是 False"这种自相矛盾的状态出现。
    """
    from tool.gen_cert import ensure_cert
    cert_file, key_file, err = ensure_cert(args.cert_dir, days=1095,
                                           extra_ips=list(args.cert_extra_ip or []))
    if err:
        return None, f'证书不可用（{err}）'
    if _port_busy(args.host, args.https_port):
        who = ''
        try:
            from tool.port_check import who_listens
            procs = who_listens(args.https_port)
            if procs:
                who = '，占用进程 PID=' + ', '.join(p[0] for p in procs)
        except Exception:
            pass
        return None, (f'HTTPS 端口 :{args.https_port} 已被占用{who}。'
                      f'处理：python tool/port_check.py --kill，或换 --https-port')

    errors = []
    try:
        cfg = uvicorn.Config(app, host=args.host, port=args.https_port,
                             ssl_keyfile=key_file, ssl_certfile=cert_file,
                             log_level='info')
    except Exception as exc:
        return None, f'uvicorn 配置失败: {type(exc).__name__}: {exc}'
    srv = uvicorn.Server(cfg)

    def _run():
        try:
            srv.run()
        except Exception as exc:                      # 进程/绑定级失败
            errors.append(f'{type(exc).__name__}: {exc}')

    threading.Thread(target=_run, name='HTTPS', daemon=True).start()
    deadline = time.time() + 8
    while time.time() < deadline and not getattr(srv, 'started', False) and not errors:
        time.sleep(0.1)

    def _shutdown(reason):
        try:
            srv.should_exit = True
        except Exception:
            pass
        return None, reason

    if errors:
        return _shutdown('HTTPS 线程启动失败：' + errors[0])
    if not getattr(srv, 'started', False):
        return _shutdown('8 秒内没就绪（端口被占 / 证书不被 ssl 接受，看上面的 uvicorn 日志）')
    ok, detail = _https_selfcheck(args.https_port)
    if not ok:
        return _shutdown(f'端口在听但自检失败：{detail}')
    return srv, ''


# ==================== 程序入口 ====================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CropGuard Web API Server")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    parser.add_argument("--https", dest="https", action="store_true", default=True,
                        help="开 HTTPS（手机实时模式需要；默认已开，保留是为了兼容旧命令）")
    parser.add_argument("--no-https", dest="https", action="store_false",
                        help="只跑 HTTP（手机实时模式不可用，拍照模式照常）")
    parser.add_argument("--https-port", type=int,
                        default=int((config.get('PHONE', {}) or {}).get('https_port', 8443) or 8443),
                        help="HTTPS 端口")
    parser.add_argument("--cert-dir",
                        default=str((config.get('PHONE', {}) or {}).get('cert_dir', './config/certs')),
                        help="证书目录（不存在会自动生成自签证书）")
    parser.add_argument("--cert-extra-ip", action="append", default=[],
                        help="额外写进证书 SAN 的 IP（手机走别的网段时用，可多次）")
    args = parser.parse_args()

    if not HAS_WEB:
        print("[ERROR] 需要安装 FastAPI 和 uvicorn:")
        print("  pip install fastapi uvicorn python-multipart")
        sys.exit(1)

    _ips = _lan_ips() or ['<本机IP>']
    _fb_st = feedback.status()
    # 把真实端口告诉页面层：这样 `--port 8090` 启动时，手机页里内嵌的
    # 接口地址会自动跟着变成 8090（否则页面会去连 8080，调接口全失败）
    _srv_ports['http'] = int(args.port)
    https_wanted = bool(args.https)
    if args.reload and '--https' not in sys.argv:
        # 热重载下 uvicorn 会另起子进程，HTTPS 线程会失控 → 开发模式默认只跑 HTTP
        https_wanted = False
        _https_reason = '开发模式 --reload 下默认不开（要开就显式写 --https）'
    else:
        _https_reason = '已用 --no-https 关闭' if not https_wanted else ''
    _srv_ports['https'] = int(args.https_port) if https_wanted else None

    if https_wanted and int(args.https_port) == int(args.port):
        print(f"[ERROR] --https-port({args.https_port}) 与 --port({args.port}) 相同："
              f"一个端口只能二选一（TLS 或明文）。请换端口。", flush=True)
        sys.exit(2)

    if https_wanted:
        _https_server, _https_err = _start_https(args)
        _srv_ports['https_on'] = _https_server is not None
    else:
        _srv_ports['https_on'] = False
        _https_err = _https_reason

    _listeners = f"http://{args.host}:{args.port}"
    if _srv_ports['https_on']:
        _listeners += f"  +  https://{args.host}:{args.https_port}"
    _canon = _phone_url(_ips[0])
    if _srv_ports['https_on']:
        _https_note = '（HTTPS 就绪：实时摄像头 / 拍照 / 问答 / 雾化 都在同一个页面里）'
    else:
        _https_note = f'⚠ HTTPS 未启用：{_https_err} → 手机实时模式不可用，拍照模式照常'
    print(f"""
============================================================
  CropGuard - 农作物害虫智能检测系统 Web API
============================================================
  模型: {weights_path}
  类别数: {len(yolo_model.names)}
  设备: {device}
  喷淋: transport={spray.transport} available={spray.available}
  语音: engine={_fb_st['tts'].get('engine')} ready={_fb_st['tts'].get('ready')}
  监听: {_listeners}
  Web演示: http://{args.host}:{args.port}/
  数据看板: http://{args.host}:{args.port}/dashboard
  离线快照: http://{args.host}:{args.port}/dashboard-offline
  现场测评: http://{args.host}:{args.port}/eval
  EdgeJev:  http://{args.host}:{args.port}/edge
  手机监测页: http://{_ips[0]}:{args.port}/stream
  API文档: http://{args.host}:{args.port}/docs
------------------------------------------------------------
  手机会话页(唯一入口): {_canon}
  {_https_note}
  （http 打开也会自动升到上面这个地址；不想升级就加 ?http=1 留在 HTTP 拍照模式）
============================================================
""", flush=True)

    # HTTP 与 HTTPS 共享同一个 app（相机/模型/历史都是单实例）；HTTP 跑主线程，
    # HTTPS 由 _start_https 起的守护线程负责（见上面的 _srv_ports['https_on']）。
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
