"""
CropGuard Web API Server
102类农业害虫检测 REST API 服务
用于生产环境部署（Linux服务器/Docker）

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
import traceback
from datetime import datetime
from io import BytesIO

import cv2
import numpy as np
from ultralytics import YOLO

# 尝试导入FastAPI（可选依赖）
try:
    from fastapi import FastAPI, File, UploadFile, Form, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse, HTMLResponse
    from fastapi.staticfiles import StaticFiles
    import uvicorn
    HAS_WEB = True
except ImportError:
    HAS_WEB = False
    print("[WARN] FastAPI/uvicorn not installed. Install: pip install fastapi uvicorn python-multipart websockets")

# 加载配置
from tool.parser import get_config
from tool.tools import format_data, draw_info, assess_risk

config = get_config('./config/configs.yaml')
cfg_model = config.MODEL
weights_path = cfg_model.WEIGHT
conf_thres = float(cfg_model.CONF)
imgsz = int(cfg_model.IMGSIZE)
device = cfg_model.DEVICE
chinese_name = config.get('CONFIG', {}).get('chinese_name', {})

# 加载模型
if not os.path.exists(weights_path):
    print(f"[WARN] Weight file {weights_path} not found, using yolov8s.pt")
    weights_path = 'yolov8s.pt'

print(f"[INFO] Loading model: {weights_path}")
print(f"[INFO] Device: {device}")
yolo_model = YOLO(weights_path)
# 预热
yolo_model.predict(np.zeros((300, 300, 3), dtype=np.uint8), device=device)
print(f"[INFO] Model loaded. Classes: {len(yolo_model.names)}")
print(f"[INFO] Classes: {list(yolo_model.names.values())}")

# ==================== 信号反馈 & 喷淋执行（国赛形态C 物理闭环） ====================
from feedback.feedback import Feedback
from feedback.spray import SprayController

cfg_feedback = config.get('FEEDBACK', {}) or {}
feedback = Feedback(
    serial_port=cfg_feedback.get('serial_port') or None,
    baud=int(cfg_feedback.get('baud', 9600)),
    enabled=bool(cfg_feedback.get('enabled', True)),
    speak_interval=float(cfg_feedback.get('speak_interval', 3)),
)

cfg_spray = config.get('SPRAY', {}) or {}
spray = SprayController(
    serial_port=cfg_spray.get('serial_port') or None,
    baud=int(cfg_spray.get('baud', 115200)),
    enabled=bool(cfg_spray.get('enabled', True)),
    risk_map=cfg_spray.get('risk_map', {}) or {},
    min_interval=float(cfg_spray.get('min_interval', 3)),
)
camera_num = int(config.get('CONFIG', {}).get('camera_num', 0))

# 联动/喷淋历史（Client 页「历史回看」用，内存环形缓存）
spray_history = []


def _record_history(risk_level, detections, group=None, duration=None, source='auto'):
    """记录一条联动/喷淋历史。"""
    names = []
    for d in (detections or []):
        n = d.get('chinese_name') or d.get('class_name') if isinstance(d, dict) else str(d)
        if n:
            names.append(str(n))
    spray_history.append({
        'ts': datetime.now().strftime('%H:%M:%S'),
        'source': source,
        'risk': risk_level,
        'group': group,
        'duration': duration,
        'detections': names[:8],
    })
    if len(spray_history) > 50:
        del spray_history[:len(spray_history) - 50]


def _predict_and_format(img, conf=None):
    """检测 + 风险评估 + 结果图，返回统一 JSON 结构（供 REST 与 WebSocket 复用）。"""
    if conf is None:
        conf = conf_thres
    t_start = time.time()
    results = yolo_model.predict(img, imgsz=imgsz, conf=conf, device=device)
    formatted = format_data(results)
    inference_time = round(time.time() - t_start, 3)

    risk_level, risk_color, risk_detail = assess_risk(formatted, img.shape[:2])

    result_img = draw_info(img.copy(), formatted)
    _, buffer = cv2.imencode('.jpg', result_img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    result_b64 = base64.b64encode(buffer).decode('utf-8')

    detections = []
    for r in formatted:
        cls_name = r[0]
        cn_name = chinese_name.get(cls_name, cls_name)
        detections.append({
            "class_name": cls_name,
            "chinese_name": cn_name,
            "confidence": round(r[1], 4),
            "bbox": [round(x, 1) for x in r[2]],
        })

    return {
        "success": True,
        "count": len(detections),
        "detections": detections,
        "risk": {"level": risk_level, "detail": risk_detail},
        "inference_time": inference_time,
        "result_image": result_b64,
        "timestamp": datetime.now().isoformat(),
    }


def _linkage(risk_level, detections):
    """风险联动：语音播报 + 喷淋执行（均异步、失败静默、不阻塞推理）。

    返回喷淋分组/时长（若触发），用于历史记录。
    """
    names = [d.get('chinese_name') or d.get('class_name') for d in detections]
    try:
        feedback.trigger(risk_level, names, '')
    except Exception:
        pass

    mapping = cfg_spray.get('risk_map', {}).get(risk_level, {}) if cfg_spray.get('risk_map') else {}
    group = mapping.get('group')
    duration = mapping.get('duration', 0)
    sprayed = False
    if risk_level in ('高风险', '中风险'):
        try:
            sprayed = spray.spray(risk_level, names)
        except Exception:
            sprayed = False
    if sprayed:
        _record_history(risk_level, detections, group=group, duration=duration, source='auto')
    return sprayed

# ==================== FastAPI App ====================
app = FastAPI(
    title="CropGuard - 农作物害虫智能检测系统",
    description="102类农业害虫检测 REST API",
    version="1.0.0"
)

# ==================== HTML 演示页面 ====================
HTML_PAGE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CropGuard - 农作物害虫智能检测系统</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif;
            background: linear-gradient(135deg, #1a3a1a 0%, #2d5a2d 50%, #1a3a1a 100%);
            min-height: 100vh;
            color: #333;
        }
        .header {
            background: rgba(48,77,49,0.95);
            color: white;
            padding: 20px;
            text-align: center;
            box-shadow: 0 2px 10px rgba(0,0,0,0.3);
        }
        .header h1 { font-size: 24px; margin-bottom: 5px; }
        .header p { font-size: 14px; opacity: 0.85; }
        .container { max-width: 1200px; margin: 30px auto; padding: 0 20px; }
        .upload-section {
            background: white;
            border-radius: 15px;
            padding: 30px;
            margin-bottom: 20px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
        }
        .upload-area {
            border: 2px dashed #ccc;
            border-radius: 10px;
            padding: 40px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
            margin-bottom: 20px;
        }
        .upload-area:hover, .upload-area.dragover {
            border-color: #2d5a2d;
            background: rgba(45,90,45,0.05);
        }
        .upload-area input { display: none; }
        .upload-icon { font-size: 48px; margin-bottom: 10px; }
        .btn {
            background: rgb(48,77,49);
            color: white;
            border: none;
            padding: 12px 30px;
            border-radius: 25px;
            font-size: 16px;
            cursor: pointer;
            transition: all 0.3s;
            display: inline-block;
            text-decoration: none;
        }
        .btn:hover { background: rgb(60,95,60); transform: translateY(-1px); }
        .btn:disabled { background: #999; cursor: not-allowed; }
        .result-section {
            background: white;
            border-radius: 15px;
            padding: 30px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
            display: none;
        }
        .result-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-top: 20px;
        }
        @media (max-width: 768px) { .result-grid { grid-template-columns: 1fr; } }
        .result-image {
            border-radius: 10px;
            overflow: hidden;
            border: 1px solid #eee;
        }
        .result-image img { width: 100%; height: auto; display: block; }
        .result-info {
            padding: 20px;
            background: #f9f9f9;
            border-radius: 10px;
        }
        .detection-item {
            padding: 12px;
            margin: 8px 0;
            background: white;
            border-radius: 8px;
            border-left: 4px solid #2d5a2d;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .detection-item .name { font-weight: bold; font-size: 16px; }
        .detection-item .conf { color: #666; font-size: 14px; }
        .risk-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-weight: bold;
            font-size: 14px;
            margin: 10px 0;
        }
        .risk-high { background: #ffe0e0; color: #c00; }
        .risk-medium { background: #fff3e0; color: #e65100; }
        .risk-low { background: #e8f5e9; color: #2e7d32; }
        .risk-none { background: #e8f5e9; color: #2e7d32; }
        .loading { text-align: center; padding: 40px; display: none; }
        .loading .spinner {
            border: 3px solid #f3f3f3;
            border-top: 3px solid #2d5a2d;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto 15px;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .api-section {
            background: white;
            border-radius: 15px;
            padding: 20px 30px;
            margin-top: 20px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
        }
        .api-section code {
            background: #f5f5f5;
            padding: 2px 8px;
            border-radius: 4px;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 13px;
        }
        .preview-img {
            max-width: 200px;
            max-height: 150px;
            border-radius: 8px;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🌿 CropGuard - 农作物害虫智能检测系统</h1>
        <p>基于 YOLOv8 + SE注意力机制 | 102类农业害虫识别 | AI防治建议</p>
    </div>

    <div class="container">
        <div class="upload-section">
            <h2 style="margin-bottom:15px;">📤 上传图片进行检测</h2>
            <div class="upload-area" id="uploadArea" onclick="document.getElementById('fileInput').click()">
                <div class="upload-icon">🖼️</div>
                <p>点击或拖拽图片到此处</p>
                <p style="color:#999;font-size:12px;margin-top:5px;">支持 JPG, PNG, JFIF 格式</p>
                <input type="file" id="fileInput" accept="image/*" onchange="handleFileSelect(event)">
            </div>
            <img id="preview" class="preview-img" style="display:none;">
            <div style="margin-top:15px;">
                <button class="btn" id="detectBtn" onclick="detectPests()" disabled>🔍 开始检测</button>
                <label style="margin-left:15px;font-size:14px;">
                    置信度阈值: <input type="range" id="confThreshold" min="0.1" max="0.9" step="0.05" value="0.4" style="vertical-align:middle;">
                    <span id="confValue">0.4</span>
                </label>
            </div>
        </div>

        <div class="loading" id="loading">
            <div class="spinner"></div>
            <p>正在检测中，请稍候...</p>
        </div>

        <div class="result-section" id="resultSection">
            <h2>📊 检测结果</h2>
            <div id="riskInfo"></div>
            <div class="result-grid">
                <div class="result-image">
                    <h3 style="margin-bottom:10px;">检测可视化</h3>
                    <img id="resultImage" alt="检测结果">
                </div>
                <div class="result-info">
                    <h3 style="margin-bottom:10px;">检测详情</h3>
                    <div id="detectionList"></div>
                    <div id="stats" style="margin-top:15px;color:#666;font-size:14px;"></div>
                </div>
            </div>
        </div>

        <div class="api-section">
            <h3>📡 API 接口</h3>
            <p style="margin-top:10px;">除了本页面，您也可以直接调用 REST API:</p>
            <p style="margin:8px 0;"><a href="/stream" style="color:#2d5a2d;font-weight:bold;">📱 打开移动端实时监测页 /stream</a>（实时画面 + 喷淋联动）</p>
            <p style="margin:8px 0;"><code>POST /api/detect</code> - 上传图片检测 (multipart/form-data, field: <code>file</code>)</p>
            <p style="margin:8px 0;"><code>POST /api/detect_b64</code> - Base64图片检测 (JSON: <code>{"image": "base64..."}</code>)</p>
            <p style="margin:8px 0;"><code>POST /api/spray</code> - 手动喷淋 (JSON: <code>{"group":"all","duration":2}</code>)</p>
            <p style="margin:8px 0;"><code>GET /api/history</code> - 联动/喷淋历史</p>
            <p style="margin:8px 0;"><code>WS /ws/stream</code> - 摄像头实时流</p>
            <p style="margin:8px 0;"><code>GET /api/health</code> - 健康检查</p>
            <p style="margin:8px 0;"><code>GET /api/info</code> - 模型信息</p>
        </div>
    </div>

    <script>
        let selectedFile = null;

        // 拖拽上传
        const uploadArea = document.getElementById('uploadArea');
        uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
        uploadArea.addEventListener('dragleave', () => { uploadArea.classList.remove('dragover'); });
        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('dragover');
            const file = e.dataTransfer.files[0];
            if (file && file.type.startsWith('image/')) {
                selectedFile = file;
                showPreview(file);
            }
        });

        // 置信度滑块
        document.getElementById('confThreshold').addEventListener('input', function() {
            document.getElementById('confValue').textContent = this.value;
        });

        function handleFileSelect(event) {
            const file = event.target.files[0];
            if (file) { selectedFile = file; showPreview(file); }
        }

        function showPreview(file) {
            const reader = new FileReader();
            reader.onload = (e) => {
                const preview = document.getElementById('preview');
                preview.src = e.target.result;
                preview.style.display = 'block';
            };
            reader.readAsDataURL(file);
            document.getElementById('detectBtn').disabled = false;
        }

        async function detectPests() {
            if (!selectedFile) return;

            const loading = document.getElementById('loading');
            const resultSection = document.getElementById('resultSection');
            const detectBtn = document.getElementById('detectBtn');
            const conf = document.getElementById('confThreshold').value;

            loading.style.display = 'block';
            resultSection.style.display = 'none';
            detectBtn.disabled = true;

            const formData = new FormData();
            formData.append('file', selectedFile);
            formData.append('conf', conf);

            try {
                const resp = await fetch('/api/detect', { method: 'POST', body: formData });
                const data = await resp.json();

                if (data.success) {
                    // 显示结果图
                    document.getElementById('resultImage').src = 'data:image/jpeg;base64,' + data.result_image;

                    // 显示风险等级
                    const risk = data.risk;
                    let riskClass = 'risk-none';
                    if (risk.level === '高风险') riskClass = 'risk-high';
                    else if (risk.level === '中风险') riskClass = 'risk-medium';
                    else if (risk.level === '低风险') riskClass = 'risk-low';
                    document.getElementById('riskInfo').innerHTML =
                        `<div style="margin:10px 0;">
                            <span class="risk-badge ${riskClass}">${risk.level}</span>
                            <span style="margin-left:10px;">${risk.detail}</span>
                        </div>`;

                    // 显示检测列表
                    const list = document.getElementById('detectionList');
                    if (data.detections.length === 0) {
                        list.innerHTML = '<p style="color:#999;">未检测到害虫目标</p>';
                    } else {
                        list.innerHTML = data.detections.map((d, i) =>
                            `<div class="detection-item">
                                <div class="name">${d.chinese_name || d.class_name}</div>
                                <div class="conf">置信度: ${(d.confidence*100).toFixed(1)}% | 位置: [${d.bbox.map(Math.round).join(', ')}]</div>
                            </div>`
                        ).join('');
                    }

                    // 显示统计
                    document.getElementById('stats').innerHTML =
                        `检测耗时: ${data.inference_time}s | 检测到 ${data.count} 个目标`;

                    resultSection.style.display = 'block';
                } else {
                    alert('检测失败: ' + data.error);
                }
            } catch (err) {
                alert('请求失败: ' + err.message);
            } finally {
                loading.style.display = 'none';
                detectBtn.disabled = false;
            }
        }
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    """Web演示页面"""
    return HTML_PAGE


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

        return _predict_and_format(img, conf)

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
    conf = payload.get("conf", conf_thres)

    if not image_b64:
        raise HTTPException(status_code=400, detail="缺少 'image' 字段")

    # 去除 data:image/...;base64, 前缀
    if ',' in image_b64:
        image_b64 = image_b64.split(',', 1)[1]

    try:
        img_bytes = base64.b64decode(image_b64)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            raise HTTPException(status_code=400, detail="无法解码Base64图片")

        return _predict_and_format(img, conf)

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )


# ==================== 移动端实时监测页（C/S Client） ====================
STREAM_PAGE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>农智云 · 实时监测</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif;
            background: #0f1a0f;
            color: #e8f5e9;
            min-height: 100vh;
            padding-bottom: env(safe-area-inset-bottom);
        }
        .header {
            background: rgba(48,77,49,0.95);
            padding: 12px 16px;
            text-align: center;
            position: sticky; top: 0; z-index: 10;
            box-shadow: 0 2px 8px rgba(0,0,0,0.4);
        }
        .header h1 { font-size: 18px; }
        .header .status { font-size: 12px; opacity: 0.85; margin-top: 2px; }
        .status .dot { display:inline-block; width:8px; height:8px; border-radius:50%;
            background:#f44336; margin-right:5px; }
        .status.online .dot { background:#4caf50; }
        .video-wrap {
            margin: 12px; border-radius: 14px; overflow: hidden;
            background: #000; position: relative; aspect-ratio: 4/3;
        }
        .video-wrap img { width: 100%; height: 100%; object-fit: contain; display:block; }
        .risk-bar {
            margin: 0 12px; padding: 10px 14px; border-radius: 10px;
            background: #1e2a1e; font-weight: bold; font-size: 15px;
        }
        .risk-bar.high { background: #4a1515; color: #ff8a80; }
        .risk-bar.medium { background: #4a2f12; color: #ffcc80; }
        .risk-bar.low { background: #14341a; color: #a5d6a7; }
        .risk-bar.none { background: #14341a; color: #a5d6a7; }
        .panel {
            margin: 12px; padding: 14px; border-radius: 14px; background: #1e2a1e;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }
        .panel h3 { font-size: 14px; margin-bottom: 10px; color: #a5d6a7; }
        .spray-btns { display: flex; gap: 10px; flex-wrap: wrap; }
        .spray-btns button {
            flex: 1; min-width: 80px; padding: 12px 8px; border: none; border-radius: 10px;
            font-size: 15px; font-weight: bold; cursor: pointer; color: #fff;
            background: rgb(48,77,49); transition: transform 0.1s;
        }
        .spray-btns button:active { transform: scale(0.96); }
        .spray-btns button.warn { background: #b3541e; }
        .spray-btns button.all { background: #a31d1d; }
        .det-item {
            display: flex; justify-content: space-between; align-items: center;
            padding: 8px 10px; margin: 6px 0; background: #142014;
            border-radius: 8px; border-left: 3px solid #4caf50; font-size: 14px;
        }
        .det-item .conf { color: #9e9e9e; font-size: 12px; }
        .hist-item {
            padding: 8px 10px; margin: 6px 0; background: #142014;
            border-radius: 8px; font-size: 12px; line-height: 1.5;
        }
        .hist-item .tag {
            display:inline-block; padding: 1px 8px; border-radius: 8px; font-weight: bold;
            margin-right: 6px; color:#fff;
        }
        .tag.high { background:#a31d1d; } .tag.medium { background:#b3541e; }
        .tag.low { background:#2e7d32; } .tag.manual { background:#455a64; }
        .empty { color: #6b7a6b; text-align:center; padding: 10px; font-size: 13px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🌿 农智云 · 实时监测终端</h1>
        <div class="status" id="status"><span class="dot"></span>连接中...</div>
    </div>

    <div class="video-wrap"><img id="stream" alt="实时画面"></div>

    <div class="risk-bar none" id="riskBar">风险等级：无风险</div>

    <div class="panel">
        <h3>💦 手动喷淋</h3>
        <div class="spray-btns">
            <button class="all" onclick="spray('all', 3)">全体喷</button>
            <button class="warn" onclick="spray('g1', 2)">G1 组喷</button>
            <button onclick="spray('g2', 1)">G2 组喷</button>
        </div>
    </div>

    <div class="panel">
        <h3>🐛 当前检测</h3>
        <div id="detList"><div class="empty">等待检测结果...</div></div>
    </div>

    <div class="panel">
        <h3>🕘 联动/喷淋历史</h3>
        <div id="histList"><div class="empty">暂无记录</div></div>
    </div>

    <script>
        var ws = null;
        var statusEl = document.getElementById('status');

        function setStatus(online, text) {
            statusEl.className = 'status' + (online ? ' online' : '');
            statusEl.innerHTML = '<span class="dot"></span>' + text;
        }

        function connect() {
            var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
            ws = new WebSocket(proto + location.host + '/ws/stream');
            ws.onopen = function() { setStatus(true, '已连接 · 实时监测中'); };
            ws.onmessage = function(ev) {
                var data = JSON.parse(ev.data);
                if (data.error) { setStatus(false, data.error); return; }
                if (data.result_image) {
                    document.getElementById('stream').src = 'data:image/jpeg;base64,' + data.result_image;
                }
                renderRisk(data.risk);
                renderDetections(data.detections);
            };
            ws.onclose = function() { setStatus(false, '已断开，重连中...'); setTimeout(connect, 2000); };
            ws.onerror = function() { ws.close(); };
        }

        function renderRisk(risk) {
            var level = risk && risk.level ? risk.level : '无风险';
            var bar = document.getElementById('riskBar');
            bar.className = 'risk-bar ' + clsOfRisk(level);
            bar.textContent = '风险等级：' + level + (risk && risk.detail ? '（' + risk.detail + '）' : '');
        }

        function clsOfRisk(level) {
            if (level === '高风险') return 'high';
            if (level === '中风险') return 'medium';
            if (level === '低风险') return 'low';
            return 'none';
        }

        function renderDetections(list) {
            var el = document.getElementById('detList');
            if (!list || list.length === 0) { el.innerHTML = '<div class="empty">未检测到害虫目标</div>'; return; }
            el.innerHTML = list.map(function(d) {
                var name = d.chinese_name || d.class_name;
                var conf = (d.confidence * 100).toFixed(1) + '%';
                return '<div class="det-item"><span>' + name + '</span><span class="conf">' + conf + '</span></div>';
            }).join('');
        }

        async function spray(group, duration) {
            try {
                var resp = await fetch('/api/spray', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({group: group, duration: duration})
                });
                var data = await resp.json();
                if (data.success) { refreshHistory(); }
                else { alert('喷淋未下发：网关未连接'); }
            } catch (e) { alert('请求失败: ' + e.message); }
        }

        async function refreshHistory() {
            try {
                var resp = await fetch('/api/history');
                var data = await resp.json();
                var list = data.history || [];
                var el = document.getElementById('histList');
                if (list.length === 0) { el.innerHTML = '<div class="empty">暂无记录</div>'; return; }
                el.innerHTML = list.slice().reverse().map(function(h) {
                    var tag = h.source === 'manual' ? 'manual' : clsOfRisk(h.risk);
                    var label = h.source === 'manual' ? '手动' : h.risk;
                    var det = (h.detections && h.detections.length) ? h.detections.join('、') : '';
                    var act = h.group ? ' → ' + (h.group === 'all' ? '全体' : h.group) + '喷 ' + h.duration + 's' : '';
                    return '<div class="hist-item"><span class="tag ' + tag + '">' + label + '</span>' +
                        '<span>' + h.ts + '</span>' + act + '<br>' + det + '</div>';
                }).join('');
            } catch (e) {}
        }

        connect();
        refreshHistory();
        setInterval(refreshHistory, 2000);
    </script>
</body>
</html>
"""


@app.get("/stream", response_class=HTMLResponse)
async def stream_page():
    """移动端实时监测页（C/S Client）。"""
    return STREAM_PAGE


@app.get("/api/history")
async def history():
    """联动/喷淋历史回看。"""
    return {"history": spray_history}


@app.post("/api/spray")
async def manual_spray(payload: dict):
    """手动喷淋端点（Client 按钮）。

    body: {"group": "all"|"g1"|"g2", "duration": 秒}
    """
    group = payload.get("group", "all")
    try:
        duration = int(payload.get("duration", 2))
    except (TypeError, ValueError):
        duration = 2
    ok = spray.manual_spray(group, duration)
    _record_history('手动喷淋', [], group=group, duration=duration, source='manual')
    return {
        "success": ok,
        "gateway_available": spray.available,
        "group": group,
        "duration": duration,
    }


@app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket):
    """摄像头实时流：采集 → 推理 → 推 JPEG + 结果 + 风险；中/高风险自动联动喷淋 + 语音。"""
    await websocket.accept()

    cap = cv2.VideoCapture(camera_num)
    if not cap.isOpened():
        await websocket.send_text(json.dumps({"error": "无法打开摄像头"}, ensure_ascii=False))
        await websocket.close()
        return

    frame_interval = 1.0 / 15.0  # 目标约 15 FPS
    last_push = 0.0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                await asyncio.sleep(0.05)
                continue

            now = time.time()
            if now - last_push < frame_interval:
                await asyncio.sleep(0.005)
                continue

            result = _predict_and_format(frame)
            last_push = time.time()

            # 中/高风险联动：喷淋 + 语音（均异步，不阻塞推理）
            risk_level = result["risk"]["level"]
            if risk_level in ('高风险', '中风险'):
                _linkage(risk_level, result["detections"])

            await websocket.send_text(json.dumps(result, ensure_ascii=False))
            await asyncio.sleep(0.001)
    except WebSocketDisconnect:
        pass
    except Exception:
        traceback.print_exc()
    finally:
        cap.release()


# ==================== 程序入口 ====================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CropGuard Web API Server")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()

    if not HAS_WEB:
        print("[ERROR] 需要安装 FastAPI 和 uvicorn:")
        print("  pip install fastapi uvicorn python-multipart")
        sys.exit(1)

    print(f"""
============================================================
  CropGuard - 农作物害虫智能检测系统 Web API
============================================================
  模型: {weights_path}
  类别数: {len(yolo_model.names)}
  设备: {device}
  服务地址: http://{args.host}:{args.port}
  Web演示: http://{args.host}:{args.port}/
  移动端监测: http://{args.host}:{args.port}/stream
  API文档: http://{args.host}:{args.port}/docs
============================================================
""")

    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
