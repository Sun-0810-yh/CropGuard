# -*- coding: utf-8 -*-
"""前端单页应用（web/ → 构建产物 web/dist）的托管。

页面源码在 web/src（Vue 3 + Vite + TypeScript），构建产物 web/dist **随仓库提交**：
比赛现场没有外网、也不装 Node，`python api_server.py` 直接托管 dist 即可。
改了前端源码后在 web/ 下执行 `npm run build` 重新生成。
"""
import os

from fastapi.responses import FileResponse, HTMLResponse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, 'web', 'dist')
INDEX = os.path.join(DIST, 'index.html')
ASSETS = os.path.join(DIST, 'assets')

# 前端路由：这些路径都返回同一个 index.html，由前端路由渲染对应页面。
# ⚠️ 2026-10-24 起 /eval（现场评分）与 /m（手机端）也由 Vue 接管：
#    评分并进检测台的「评分作业」模式；手机端重写后与站点同一套视觉。
#    原生页仍保留，但改挂 /eval-classic、/m-classic 作为兜底（前端出问题时的后路）。
#    /dashboard-offline 依然是原生单文件快照，不在这里。
ROUTES = ('/', '/stream', '/ask', '/spray', '/system', '/dashboard', '/eval', '/eval/review', '/m')

_MISSING = """<!doctype html><meta charset="utf-8"><title>株得CropGuard</title>
<body style="font:15px/1.7 system-ui,'Microsoft YaHei',sans-serif;max-width:640px;margin:48px auto;padding:0 16px">
<h1 style="font-size:20px">前端页面还没有构建</h1>
<p>找不到 <code>web/dist/index.html</code>。在项目根目录执行：</p>
<pre style="background:#eef1ec;padding:12px">cd web
npm install
npm run build</pre>
<p>接口本身可以正常使用，见 <a href="/docs">/docs</a>。</p></body>"""


def available():
    return os.path.isfile(INDEX)


def index_response():
    """返回 SPA 入口页。index.html 不缓存（每次构建资源文件名会变），资源文件本身可长期缓存。"""
    if not available():
        return HTMLResponse(_MISSING, status_code=503)
    return FileResponse(INDEX, media_type='text/html',
                        headers={'Cache-Control': 'no-cache'})


def static_file(name):
    """dist 根目录下的单个静态文件（如 favicon.svg）；不允许跳出 dist。"""
    path = os.path.normpath(os.path.join(DIST, name))
    if not path.startswith(DIST) or not os.path.isfile(path):
        return None
    return FileResponse(path)
