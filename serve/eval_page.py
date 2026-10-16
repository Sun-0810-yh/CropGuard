# -*- coding: utf-8 -*-
"""现场测评页（`GET /eval`）的渲染入口。

页面资源拆成三份独立文件，便于维护（与手机端 /m 同一套做法）：
- templates/eval.html：页面骨架
- static/eval.css：样式与动效
- static/eval.js：交互逻辑

本模块只负责读取并拼接成完整 HTML。评测口径参数不在这里写死 ——
页面启动后从 `GET /api/eval/config` 拉取（与 GUI/评分函数同源，防止口径漂移）。
"""
import os

_DIR = os.path.dirname(os.path.abspath(__file__))


def _read_file(*parts):
    path = os.path.join(_DIR, *parts)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def render(config=None):
    """返回完整 HTML（占位符替换）。

    config 目前仅用于标题兜底展示；页面实时数据走 /api/eval/*。
    """
    html = _read_file('templates', 'eval.html')
    css = _read_file('static', 'eval.css')
    js = _read_file('static', 'eval.js')
    html = html.replace('{{STYLE}}', css)
    html = html.replace('{{SCRIPT}}', js)
    return html
