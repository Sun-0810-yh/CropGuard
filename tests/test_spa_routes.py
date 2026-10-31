# -*- coding: utf-8 -*-
"""页面路由回归：/eval、/m 由 Vue 接管；原生页留 /eval-classic、/m-classic 兜底。

2026-10-24 的一体化改造把「评分作业」并进检测台（/eval），手机端改成 Vue 版（/m）。
原生页面**不删**，改名当兜底：前端出问题时评委还能继续演示。
这里锁三件事：
1. `serve/spa.py` 的 ROUTES 必须把新路径交给 SPA；
2. api_server 里必须真的注册了 /eval-classic、/m-classic；
3. 设计令牌 /static/tokens.css 是站点与原生页共用的（挂载不能丢）。
"""
import ast
import os

from _registry import test_case

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _api_source():
    with open(os.path.join(ROOT, 'api_server.py'), 'r', encoding='utf-8') as f:
        return f.read()


def _routes():
    src = _api_source()
    tree = ast.parse(src)
    paths = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            fn = deco.func
            if isinstance(fn, ast.Attribute) and fn.attr in ('get', 'post', 'websocket'):
                if deco.args and isinstance(deco.args[0], ast.Constant):
                    paths.add(deco.args[0].value)
    return paths


@test_case
def test_spa_routes_cover_eval_and_mobile():
    from serve import spa
    for p in ('/', '/stream', '/ask', '/spray', '/system', '/dashboard', '/eval', '/eval/review', '/m'):
        assert p in spa.ROUTES, f'{p} 不在 SPA 路由里（现有 {spa.ROUTES}）'


@test_case
def test_classic_fallbacks_still_registered():
    paths = _routes()
    for p in ('/eval-classic', '/m-classic'):
        assert p in paths, f'兜底页 {p} 没注册（原生页不能删）'
    # 主路径不能再被原生页抢走
    assert '/eval' not in paths, '/eval 应该是 SPA 的路由，不能有同名原生端点'
    assert '/m' not in paths, '/m 应该是 SPA 的路由，不能有同名原生端点'


@test_case
def test_snapshot_endpoints_registered():
    paths = _routes()
    assert '/api/eval/snapshot' in paths, '缺拍照即存接口'
    src = _api_source()
    assert "app.mount(\"/static\"" in src, '原生页共用的 /static 挂载丢了（tokens.css 会 404）'


@test_case
def test_frontend_router_has_eval_route():
    with open(os.path.join(ROOT, 'web', 'src', 'router.ts'), 'r', encoding='utf-8') as f:
        src = f.read()
    assert "path: '/eval'" in src, '前端路由缺 /eval'
    assert "path: '/eval/review'" in src, '前端路由缺评测子页 /eval/review'
    assert "path: '/m'" in src, '前端路由缺 /m'
    assert "mode: 'eval'" in src, '评分模式要靠路由 meta 传给检测台'
