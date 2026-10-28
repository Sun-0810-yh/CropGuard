# -*- coding: utf-8 -*-
"""手机端 http/https 合一（同源接口 + 一键升级）与证书 SAN 覆盖的用例。

背景（现场真实故障）：跑 `api_server.py` 没加 `--https` → 8443 根本没监听，
但 /m 页面照样渲染 `https://<IP>:8443/m` 链接 → 手机点进去打不开；
即使 `--https` 起来了，证书 SAN 里是**旧网段**的 IP，手机报"证书名称不符"；
更关键的是 https 页里 `API_BASE` 指向 `http://host:8080`，
浏览器按"混合内容"把 fetch 整块拦掉 → https 页像是"另一个坏掉的页面"。

本文件守住三条契约：
1. 页面接口**永远同源**（`const API_BASE = ""`），不许再出现 `"http://"+location.hostname`；
2. HTTPS 没真的起来时不渲染 https 链接（不出死链）；起来了才给唯一入口 + 自动升级；
3. 证书必须能按**当前**局域网 IP 自动重签（SAN 覆盖判断是纯函数，好测）。
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

from _registry import SkipTest, requires, test_case

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / 'api_server.py'
INDEX = ROOT / 'serve' / 'pages' / 'index.html'
TMP_DIR = ROOT / 'output' / '_test_tmp' / 'phone_https'

HOST = '172.23.153.11'
CANON = f'https://{HOST}:8443/m'


def _render(https_enabled):
    from serve import mobile as mobile_page
    return mobile_page.render(host=HOST, http_port=8080, https_port=8443,
                              https_enabled=https_enabled, mist_url='http://192.168.43.61',
                              mist_max_sec=5, mist_direct_sec=300)


# ==================== 1. 同源接口（防混合内容回归） ====================
@test_case
def test_mobile_page_api_base_is_same_origin():
    """两种状态下接口都必须同源 —— 否则 HTTPS 页的 fetch 会被浏览器整块拦掉。"""
    for enabled in (True, False):
        html = _render(enabled)
        assert 'const API_BASE = "";' in html, f'https_enabled={enabled} 时接口不同源'
        assert '"http://" + location.hostname' not in html, \
            '页面又把接口指回 http://host:8080 了（混合内容会被拦）'


@test_case
def test_mobile_page_no_leftover_placeholders():
    """渲染层占位符一个都不能漏（漏了会在页面上显示 __XXX__）。"""
    for enabled in (True, False):
        html = _render(enabled)
        leaked = re.findall(r'__[A-Z_]+__', html)
        assert not leaked, f'占位符没替换: {leaked}'


# ==================== 2. HTTPS 状态决定"唯一入口"与死链 ====================
@test_case
def test_mobile_page_https_on_advertises_single_entry():
    """HTTPS 真的在服务时：页面给唯一入口地址，并带自动升级逻辑。"""
    html = _render(True)
    assert 'const HTTPS_ENABLED = true;' in html
    assert "const HTTPS_URL = 'https://' + location.hostname + ':' + HTTPS_PORT + '/m';" in html
    assert 'location.replace(HTTPS_URL)' in html, '缺少 http → https 一键升级'
    assert 'probeHttps' in html, '升级前必须先探测 https 是否可用（否则会跳进打不开的页面）'
    assert "?http=1" in html, '缺少"留在 HTTP"逃生阀'


@test_case
def test_mobile_page_https_off_never_links_dead_https():
    """HTTPS 没起来时：不许出现 https 地址（那就是打不开的死链），并说清怎么开。"""
    html = _render(False)
    assert 'const HTTPS_ENABLED = false;' in html
    assert CANON not in html, 'HTTPS 未启用却渲染了 https 地址（死链）'
    assert '--no-https' in html, '应提示怎么开启 HTTPS'
    assert '纯 HTTP' in html, '应说明当前是纯 HTTP 启动'


@test_case
def test_mobile_page_https_on_does_not_hardcode_dead_url():
    """HTTPS 开启时地址也是按 location.hostname 拼的，不能写死某个现场 IP。"""
    html = _render(True)
    assert '192.168.43.10:8443' not in html, '不要把测试用的 IP 写进页面'


@test_case
def test_mobile_inline_js_parses():
    """内嵌 JS 必须能解析（踩过坑：少一个 `)` 会让整段脚本不执行，页面只剩外壳）。"""
    node = shutil.which('node')
    if not node:
        raise SkipTest('未安装 node，跳过内嵌 JS 语法检查')
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for enabled in (True, False):
            js = re.search(r'<script>(.*?)</script>', _render(enabled), re.S).group(1)
            path = TMP_DIR / f'mobile_{int(enabled)}.js'
            path.write_text(js, encoding='utf-8')
            proc = subprocess.run([node, '--check', str(path)],
                                  capture_output=True, text=True, encoding='utf-8', errors='replace')
            assert proc.returncode == 0, \
                f'https_enabled={enabled} 的内嵌 JS 语法错误：\n{proc.stdout}\n{proc.stderr}'
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)


# ==================== 3. 证书 SAN 覆盖（换网段必须自动重签） ====================
@test_case
def test_cert_refresh_decision_is_pure():
    """SAN 覆盖/过期判断是纯函数：无需证书文件即可验证，避免现场"换了 IP 还用旧证书"。"""
    from tool import gen_cert as gc
    now = 1_700_000_000.0
    day = 86400

    ok_info = {'sans': ['127.0.0.1', HOST], 'not_before_ts': now - 10 * day,
               'not_after_ts': now + 800 * day}
    assert gc.needs_refresh_from_info(ok_info, [HOST], now=now) == (False, 'ok')

    # 证书里没有手机要用的那个 IP → 必须重签（这就是"证书名称不符"的根因）
    need, reason = gc.needs_refresh_from_info(ok_info, [HOST, '10.9.9.9'], now=now)
    assert need and reason == 'san_missing:10.9.9.9', reason

    # 读不出证书 → 按"该重签"处理，不能当没事
    assert gc.needs_refresh_from_info(None, [HOST], now=now) == (True, 'undecidable')

    expired = dict(ok_info, not_after_ts=now - day)
    assert gc.needs_refresh_from_info(expired, [HOST], now=now) == (True, 'expired')

    soon = dict(ok_info, not_after_ts=now + 10 * day)
    assert gc.needs_refresh_from_info(soon, [HOST], min_days=30, now=now) == (True, 'expiring_10d')

    future = dict(ok_info, not_before_ts=now + 10 * day)
    assert gc.needs_refresh_from_info(future, [HOST], now=now) == (True, 'not_yet_valid')


@test_case
def test_cert_generation_writes_current_lan_ips_into_san():
    """真生成一次证书：SAN 必须含 127.0.0.1 与传入的 IP，且能被 ssl 解析回来。"""
    requires('cryptography')
    from tool import gen_cert as gc
    cert_dir = TMP_DIR / 'certs'
    shutil.rmtree(cert_dir, ignore_errors=True)
    try:
        cert, key, err = gc.generate(str(cert_dir), days=365, force=True, extra_names=['10.1.2.3'])
        assert err is None, err
        info = gc.cert_info(cert)
        assert info, '生成出来的证书解析不回来'
        assert '127.0.0.1' in info['sans'], info['sans']
        assert '10.1.2.3' in info['sans'], info['sans']
        assert gc.needs_refresh(cert, ips=['127.0.0.1', '10.1.2.3'])[0] is False
        # 再换一个 IP：必须判定为要重签（现场换热点场景）
        need, reason = gc.needs_refresh(cert, ips=['10.9.9.9'])
        assert need and reason == 'san_missing:10.9.9.9', reason
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)


@test_case
def test_cert_reuses_when_san_covers_current_ip():
    """SAN 已覆盖当前 IP 时不要白白重签（避免每次启动都换证书、手机又要重新信任）。"""
    requires('cryptography')
    from tool import gen_cert as gc
    cert_dir = TMP_DIR / 'reuse'
    shutil.rmtree(cert_dir, ignore_errors=True)
    try:
        cert1, _k, err = gc.generate(str(cert_dir), days=365, force=True, extra_names=[HOST])
        assert err is None, err
        first = Path(cert1).read_bytes()
        cert2, _k2, err2 = gc.generate(str(cert_dir), days=365, force=False, extra_names=[HOST])
        assert err2 is None, err2
        assert Path(cert2).read_bytes() == first, 'SAN 已覆盖却重签了'
        assert gc.needs_refresh(cert2, ips=[HOST])[0] is False
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)


# ==================== 4. 服务端契约（源码级，防删改） ====================
@test_case
def test_api_server_https_contract():
    """HTTPS 默认开 + 就绪自检 + /api/lan 报真实状态，这几处是对外契约。"""
    text = API.read_text(encoding='utf-8')
    needles = [
        '"--no-https"',                        # 纯 HTTP 开关
        'dest="https", action="store_true", default=True',   # 默认开 HTTPS
        'def _start_https(args):',
        'def _https_selfcheck(',
        'def _port_busy(',
        "_srv_ports['https_on'] = _https_server is not None",
        '"https_enabled": https_on',
        'https_enabled=_https_on()',
        'url = url or _phone_url()',
    ]
    missing = [n for n in needles if n not in text]
    assert not missing, f'api_server.py 里缺少这些契约点：{missing}'


@test_case
def test_index_page_shows_single_phone_entry():
    """PC 首页要给出唯一手机入口（地址+二维码取自 /api/lan，避免手输错 IP）。"""
    text = INDEX.read_text(encoding='utf-8')
    for needle in ('phoneUrl', 'phoneQr', "'/api/lan'", '/qr?url=', 'phone_url'):
        assert needle in text, f'PC 首页缺手机入口元素: {needle}'


# ==================== 5. 实时模式叠框（后端只回 JSON，框由前端 canvas 画） ====================
@test_case
def test_mobile_live_canvas_and_controls_present():
    """实时画面必须有检测框叠层与配套控件 —— 这条挡住"框不见了"的回归。

    背景：/ws/phone 刻意不回传图像（省流量），检测框由前端 canvas 自己画；
    曾经这里只有文字列表、没有画框代码，于是手机实时画面永远"没有框"。
    """
    html = _render(True)
    needles = [
        'id="liveCanvas"',          # 画布
        'id="liveWrap"',            # 容器（比例自适应/放大都作用在它身上）
        'function drawDetections(',
        'function drawBoxesOn(',
        'CG-GEOMETRY-BEGIN',        # 几何纯函数的可测边界
        'CG-GEOMETRY-END',
        'id="chkBoxes"',            # 显示检测框开关
        'id="btnCam"',              # 前后摄像头
        'id="btnShot"',             # 截图（带框）
        'id="btnZoom"',             # 放大
        'id="liveStats"',           # 状态小面板
        'id="statCount"', 'id="statRisk"', 'id="statServer"', 'id="statRtt"', 'id="statFps"',
        'liveSentFrame',            # 结果坐标空间靠它映射
        'function scheduleReconnect(',   # 断线自动重连
    ]
    missing = [n for n in needles if n not in html]
    assert not missing, f'实时卡片缺少这些元素/函数：{missing}'


@test_case
def test_mobile_geometry_helpers_map_boxes_correctly():
    """框坐标映射必须是纯函数且算得对（原来"框会歪"的最大风险就在这）。

    覆盖三种现场情况：
    * 16:9 摄像头放进 4:3 容器 → 上下黑边（contain），框要减掉偏移；
    * bbox 越界 → clamp 到帧内，不能画到画面外；
    * 尺寸为 0 / bbox 非法 → 返回 null（不抛异常，不画）。
    """
    node = shutil.which('node')
    if not node:
        raise SkipTest('未安装 node，跳过几何函数数值校验')
    js = re.search(r'<script>(.*?)</script>', _render(True), re.S).group(1)
    block = re.search(r'// CG-GEOMETRY-BEGIN(.*?)// CG-GEOMETRY-END', js, re.S)
    assert block, '渲染结果里找不到 CG-GEOMETRY 标记段（几何函数被搬走或删了）'

    driver = block.group(1) + '''
function near(a, b) { return Math.abs(a - b) < 0.01; }
function chkEq(o, ref, tag) {
  if (!o) throw new Error(tag + ': 返回了 null');
  Object.keys(ref).forEach(k => {
    if (!near(o[k], ref[k])) throw new Error(tag + '.' + k + ' = ' + o[k] + '，期望 ' + ref[k]);
  });
}
// 16:9 视频放进 400x300 的容器：等比 400x225，上下各留 37.5
const r = fitRect(400, 300, 1280, 720);
chkEq(r, {x: 0, y: 37.5, w: 400, h: 225}, 'fitRect');
// 上传帧 960x540 里的框 → 显示坐标
chkEq(boxToViewRect([96, 54, 192, 108], 960, 540, r), {x: 40, y: 60, w: 40, h: 22.5}, 'boxToViewRect');
// 越界 → clamp 到整帧
chkEq(boxToViewRect([-50, -50, 2000, 2000], 960, 540, r),
      {x: 0, y: 37.5, w: 400, h: 225}, 'clamp');
// 非法输入 → null（调用方不画框）
if (fitRect(400, 300, 0, 0) !== null) throw new Error('vidW/vidH=0 应返回 null');
if (fitRect(0, 0, 1280, 720) !== null) throw new Error('viewW/viewH=0 应返回 null');
if (boxToViewRect([1, 2, 3, 4], 0, 0, r) !== null) throw new Error('frameW/frameH=0 应返回 null');
if (boxToViewRect(null, 960, 540, r) !== null) throw new Error('bbox=null 应返回 null');
console.log('GEOMETRY_OK');
'''
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / 'geom_check.js'
    try:
        path.write_text(driver, encoding='utf-8')
        proc = subprocess.run([node, str(path)],
                              capture_output=True, text=True, encoding='utf-8', errors='replace')
        assert proc.returncode == 0, f'几何函数校验失败：\n{proc.stdout}\n{proc.stderr}'
        assert 'GEOMETRY_OK' in proc.stdout, proc.stdout
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)


# 极简 DOM/canvas 桩：让**真实的 mobile.js** 在 node 里跑起来，
# 把 strokeRect/fillText 记下来就能断言"到底画没画、画到哪"。
# 现场机器没有浏览器自动化，这是唯一能自动守住"叠框"这条链路的办法。
_DOM_STUB = r"""
global.__calls = {strokeRect: [], fillRect: [], fillText: []};
function __ctx() {
  return {
    setTransform(){}, clearRect(){},
    measureText(t){ return {width: String(t).length * 7}; },
    strokeRect(...a){ global.__calls.strokeRect.push(a); },
    fillRect(...a){ global.__calls.fillRect.push(a); },
    fillText(...a){ global.__calls.fillText.push(a); },
    set lineWidth(v){}, set strokeStyle(v){}, set font(v){}, set fillStyle(v){}
  };
}
const __els = {};
function __el(id) {
  if (!__els[id]) {
    __els[id] = {
      id: id, style: {}, checked: true, textContent: '', innerHTML: '',
      width: 0, height: 0,
      clientWidth: (id === 'liveWrap' ? 400 : 320),
      clientHeight: (id === 'liveWrap' ? 300 : 240),
      classList: {toggle(){}, add(){}, remove(){}, contains(){ return false; }},
      getContext(){ return __ctx(); }, addEventListener(){}, setAttribute(){},
      removeAttribute(){}, appendChild(){}, remove(){}, click(){}
    };
  }
  return __els[id];
}
global.__els = __els;
global.window = {devicePixelRatio: 1, isSecureContext: true, addEventListener(){}, open(){}};
global.location = {protocol: 'https:', host: 'h:8443', hostname: 'h', search: '',
                   replace(){}, href: ''};
global.navigator = {mediaDevices: {getUserMedia(){ return Promise.reject(new Error('stub')); }}};
global.document = {getElementById: __el, addEventListener(){},
                   createElement(){ return __el('__tmp' + Math.random()); },
                   body: {appendChild(){}}};
global.alert = function () {};
global.confirm = function () { return false; };
global.setInterval = function () { return 0; };
global.clearInterval = function () {};
global.fetch = function () {
  return Promise.resolve({json(){ return Promise.resolve({device: 'cpu', classes: 27}); }});
};
global.WebSocket = function () {
  return {readyState: 1, send(){}, close(){}, set onopen(v){}, set onmessage(v){},
          set onclose(v){}, set onerror(v){}};
};
"""

_DRAW_CHECKS = r"""
function near(a, b) { return Math.abs(a - b) < 0.01; }
function chkRect(a, ref, tag) {
  if (!a) throw new Error(tag + ': 没画框');
  if (!(near(a[0], ref[0]) && near(a[1], ref[1]) && near(a[2], ref[2]) && near(a[3], ref[3])))
    throw new Error(tag + ': strokeRect=' + JSON.stringify(a) + '，期望 ' + JSON.stringify(ref));
}
setMode('live');
const __video = document.getElementById('phoneVideo');
__video.videoWidth = 1280; __video.videoHeight = 720;
liveSentFrame = {w: 960, h: 540, t0: 0};   // 上传帧尺寸（结果 bbox 的坐标系）

// 960x540 里的 [96,54,192,108] → 400x300 容器（16:9 上下黑边 37.5）应为 [40,60,40,22.5]
drawDetections([{chinese_name: '蚜虫', class_name: 'aphids', label_en: 'Aphids', confidence: 0.87,
                 bbox: [96, 54, 192, 108]}]);
if (global.__calls.strokeRect.length !== 1)
  throw new Error('应画 1 个框，实际 ' + global.__calls.strokeRect.length);
chkRect(global.__calls.strokeRect[0], [40, 60, 40, 22.5], '首帧');
if (!global.__calls.fillText.some(a => String(a[0]).indexOf('Aphids 0.87') === 0))
  throw new Error('标签文字不对：' + JSON.stringify(global.__calls.fillText));

// 没有目标 → 不画框（画布只清空）
let before = global.__calls.strokeRect.length;
drawDetections([]);
if (global.__calls.strokeRect.length !== before) throw new Error('0 目标不该画框');

// 关掉「显示检测框」→ 也不画
global.__els['chkBoxes'].checked = false;
drawDetections([{chinese_name: '蚜虫', confidence: 0.9, bbox: [96, 54, 192, 108]}]);
if (global.__calls.strokeRect.length !== before) throw new Error('关框后不该画框');
global.__els['chkBoxes'].checked = true;

// 越界 bbox → clamp 到整个显示矩形
drawDetections([{chinese_name: 'x', confidence: 0.5, bbox: [-99, -99, 99999, 99999]}]);
chkRect(global.__calls.strokeRect[global.__calls.strokeRect.length - 1],
        [0, 37.5, 400, 225], 'clamp');

console.log('DRAW_OK');
process.exit(0);
"""


@test_case
def test_mobile_live_draw_path_really_draws_boxes():
    """把真实 mobile.js 放进 node（配极简 DOM/canvas 桩），断言它**真的画框了**。

    这条是"手机实时没画框"事故的回归闸门：以前 onmessage 只更新文字，
    没有任何 strokeRect，用桩一跑就能发现。
    """
    node = shutil.which('node')
    if not node:
        raise SkipTest('未安装 node，跳过叠框运行时校验')
    js = re.search(r'<script>(.*?)</script>', _render(True), re.S).group(1)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / 'draw_check.js'
    try:
        path.write_text(_DOM_STUB + '\n' + js + '\n' + _DRAW_CHECKS, encoding='utf-8')
        proc = subprocess.run([node, str(path)],
                              capture_output=True, text=True, encoding='utf-8', errors='replace')
        assert proc.returncode == 0, f'叠框链路校验失败：\n{proc.stdout}\n{proc.stderr}'
        assert 'DRAW_OK' in proc.stdout, proc.stdout
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)


