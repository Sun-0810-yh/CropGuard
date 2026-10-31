// ==================== 基础工具 ====================
const HTTP_PORT = "__HTTP_PORT__";
const HTTPS_PORT = "__HTTPS_PORT__";
const HTTPS_ENABLED = __HTTPS_ENABLED__;   // 服务器上 HTTPS 监听是否真的在服务
const IS_HTTPS = location.protocol === 'https:';
// 接口永远走**同源**：HTTP 页同源调 HTTP，HTTPS 页同源调 HTTPS。
// （旧实现让 HTTPS 页去 fetch  http://host:8080 —— 浏览器按"混合内容"整块拦掉，
//   于是 https 页像"另一个坏掉的页面"：拍照/问答/雾化/历史全废，只有 wss 能通）
const API_BASE = "";
const WS_BASE = (IS_HTTPS ? "wss://" : "ws://") + location.host + "/ws/phone";
// 手机端唯一入口：http 打开也会自动升到这里（http/https 合成同一个页面）
const HTTPS_URL = 'https://' + location.hostname + ':' + HTTPS_PORT + '/m';
const STAY_HTTP = /[?&]http=1/.test(location.search);   // 逃生阀：留在 HTTP 拍照模式
let mode = 'photo';
let lastContext = null;
let liveWS = null, liveStream = null, liveTimer = null, liveBusy = false;

function $(id) { return document.getElementById(id); }
function setStatus(online, text) {
  const el = $('status');
  el.className = 'status' + (online ? ' online' : '');
  el.innerHTML = '<span class="dot"></span>' + text;
}
function clsOfRisk(level) {
  if (level === '高风险') return 'high';
  if (level === '中风险') return 'medium';
  if (level === '低风险') return 'low';
  return 'none';
}
async function post(path, body) {
  const r = await fetch(API_BASE + path, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {})
  });
  return await r.json();
}

// ==================== HTTP → HTTPS 合一 ====================
// 目标：手机不管输 http 还是 https 地址，最后都落在**同一个页面**上。
// 做法：先探测 https 能不能用（自签证书没被信任时，后台探测会失败），
//       能用才 location.replace 升级；不能用就留在 HTTP 页 + 给可点的链接，
//       绝不出现"跳过去打不开、返回又被跳回来"的死循环。
function probeHttps(timeoutMs) {
  // no-cors 探测：只关心"连不连得上"，不看响应体（自签证书/跨端口都适用）
  return new Promise(resolve => {
    const ctl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    const timer = setTimeout(() => { if (ctl) ctl.abort(); resolve(false); }, timeoutMs || 1500);
    fetch(HTTPS_URL.replace(/\/m$/, '/api/health') + '?probe=1',
          {mode: 'no-cors', cache: 'no-store', signal: ctl ? ctl.signal : undefined})
      .then(() => { clearTimeout(timer); resolve(true); })
      .catch(() => { clearTimeout(timer); resolve(false); });
  });
}
function showSecureTip(html) {
  const tip = $('secureTip');
  tip.innerHTML = html;
  tip.style.display = 'block';
}
if (!IS_HTTPS) {
  if (!HTTPS_ENABLED) {
    showSecureTip('当前服务器是 <b>纯 HTTP</b> 启动（--no-https 或证书不可用），' +
      '浏览器不允许网页调用手机摄像头，所以<b>实时模式不可用</b>；' +
      '下面的<b>拍照识别</b>照常可用。要用实时模式：重启服务器时不要加 --no-https。' +
      '<br>仅拍照的 HTTP 入口：http://' + location.hostname + ':' + HTTP_PORT + '/m');
  } else if (STAY_HTTP) {
    showSecureTip('已按 <b>?http=1</b> 留在 HTTP 拍照模式。要实时模式就打开 ' +
      '<a href="' + HTTPS_URL + '" style="color:#ffd08a;font-weight:bold">' + HTTPS_URL + '</a>');
  } else {
    showSecureTip('正在检查 HTTPS 实时模式…');
    probeHttps().then(ok => {
      if (ok) { location.replace(HTTPS_URL); return; }   // 同一个页面，只是换成 HTTPS
      showSecureTip('服务器已开 HTTPS，但本机还没信任自签证书，后台探测没通过。' +
        '点 <a href="' + HTTPS_URL + '" style="color:#ffd08a;font-weight:bold">' + HTTPS_URL + '</a> ' +
        '打开（首次会提示"不是私密连接"，选「高级 → 继续前往」即可）；' +
        '也可以继续用下面的<b>拍照识别</b>，它不需要 HTTPS。');
    });
  }
  $('btnModeLive').setAttribute('title', '需要 HTTPS');
}

// ==================== 模式切换 ====================
function setMode(m) {
  mode = m;
  $('btnModePhoto').className = (m === 'photo') ? 'active' : '';
  $('btnModeLive').className = (m === 'live') ? 'active' : '';
  $('photoPane').style.display = (m === 'photo') ? '' : 'none';
  $('livePane').style.display = (m === 'live') ? '' : 'none';
  $('resultWrap').style.display = (m === 'photo') ? $('resultWrap').style.display : 'none';
  if (m === 'photo') stopLive();
}

// ==================== 拍照识别 ====================
$('fileInput').addEventListener('change', async (ev) => {
  const file = ev.target.files && ev.target.files[0];
  if (!file) return;
  setStatus(true, '已选图，正在上传识别…');
  const form = new FormData();
  form.append('file', file, 'phone.jpg');
  try {
    const resp = await fetch(API_BASE + '/api/detect', {method: 'POST', body: form});
    const data = await resp.json();
    if (!data.success) { alert('识别失败: ' + (data.error || '未知错误')); setStatus(true, '识别失败'); return; }
    renderResult(data);
    setStatus(true, '识别完成 · ' + (data.count || 0) + ' 个目标');
  } catch (e) {
    alert('上传失败: ' + e.message);
    setStatus(false, '上传失败');
  } finally {
    ev.target.value = '';
  }
});

function renderResult(data) {
  const risk = data.risk || {};
  const bar = $('riskBar');
  bar.className = 'risk ' + clsOfRisk(risk.level);
  bar.textContent = '风险等级：' + (risk.level || '--') + (risk.detail ? '（' + risk.detail + '）' : '');

  if (data.result_image && mode === 'photo') {
    const wrap = $('resultWrap'), img = $('resultImage');
    wrap.style.display = '';
    // 结果图也按真实比例显示（和实时画面一致，避免竖拍照片被塞进 4:3 里变小）
    img.onload = () => {
      if (img.naturalWidth && img.naturalHeight) {
        wrap.style.aspectRatio = img.naturalWidth + ' / ' + img.naturalHeight;
      }
      img.onload = null;
    };
    img.src = 'data:image/jpeg;base64,' + data.result_image;
  }
  const list = data.detections || [];
  renderDetList(list, $('detList'), risk.level);       // 按置信度降序 + 进度条（与实时共用）
  if (data.advice) {
    $('adviceBox').style.display = '';
    $('adviceBox').textContent = data.advice;
  }
  lastContext = {detections: list, risk: risk};
}

// ==================== 实时模式（手机摄像头 → 服务器） ====================
// 协议口径（见 api_server.py 的 /ws/phone）：**服务端只回检测 JSON**，不回传图像；
// bbox 是"手机上传那一帧"的绝对像素坐标，所以框由本机 canvas 叠在预览上 —— 省流量。
// 下面 CG-GEOMETRY 两段是纯函数（不碰 DOM），便于用 node 单测坐标映射。

// CG-GEOMETRY-BEGIN
// object-fit:contain 时视频在容器里的**实际显示矩形**（黑边必须减掉，否则框会整体偏移）
function fitRect(viewW, viewH, vidW, vidH) {
  const vw = Number(viewW), vh = Number(viewH), iw = Number(vidW), ih = Number(vidH);
  if (!(vw > 0) || !(vh > 0) || !(iw > 0) || !(ih > 0)) return null;
  const scale = Math.min(vw / iw, vh / ih);
  const w = iw * scale, h = ih * scale;
  return {x: (vw - w) / 2, y: (vh - h) / 2, w: w, h: h};
}
// 上传帧里的像素框 → 显示矩形里的像素框（先 clamp 到帧内，模型偶尔会给出越界值）
function boxToViewRect(bbox, frameW, frameH, rect) {
  if (!bbox || bbox.length < 4 || !rect) return null;
  const fw = Number(frameW), fh = Number(frameH);
  if (!(fw > 0) || !(fh > 0)) return null;
  let x1 = Number(bbox[0]), y1 = Number(bbox[1]), x2 = Number(bbox[2]), y2 = Number(bbox[3]);
  if (![x1, y1, x2, y2].every(v => isFinite(v))) return null;
  const lx = Math.max(0, Math.min(x1, x2)), rx = Math.min(fw, Math.max(x1, x2));
  const ty = Math.max(0, Math.min(y1, y2)), by = Math.min(fh, Math.max(y1, y2));
  const sx = rect.w / fw, sy = rect.h / fh;
  return {x: rect.x + lx * sx, y: rect.y + ty * sy, w: (rx - lx) * sx, h: (by - ty) * sy};
}
// CG-GEOMETRY-END

// 类别配色：与 PC 端 tool/tools.py:compute_color_for_labels 同款（黄金比例色相 + s=0.7 v=0.95），
// 保证同一个模型在 PC 与手机上是同一套颜色。
function colorTupleForLabel(label) {
  const golden = 0.618033988749895;
  const hue = ((Number(label) || 0) * golden) % 1;
  const s = 0.7, v = 0.95;
  const i = Math.floor(hue * 6), f = hue * 6 - i;
  const p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s);
  const table = [[v, t, p], [q, v, p], [p, v, t], [p, q, v], [t, p, v], [v, p, q]];
  return (table[i % 6] || table[0]).map(x => Math.round(x * 255));
}
function cssRgb(tuple) { return 'rgb(' + tuple.join(',') + ')'; }
// 与 PC 的 _text_color_for 同口径：按背景亮度选黑字/白字（浅底黑字、深底白字）
function textColorOn(tuple) {
  const luma = 0.114 * tuple[2] + 0.587 * tuple[1] + 0.299 * tuple[0];
  return luma > 140 ? '#000' : '#fff';
}

let liveFacing = 'environment';        // 前置/后置（现场：拍叶片用后置，自检用前置）
let liveActive = false;                // 用户是否已开始实时（断线重连只认它）
let liveSentFrame = null;              // {w,h,t0} 最后发出的那一帧（结果的坐标空间）
let lastLiveDets = null;               // 最近一次检测结果（转屏/放大后重绘用）
let liveAckTimes = [];                 // 最近若干次回包时间 → FPS
let liveRtts = [];                     // 最近若干次往返毫秒
let liveReconnect = 0, liveReconnectTimer = null;
const LIVE_MAX_RECONNECT = 6;

function setLiveHint(text) {
  const el = $('liveOverlay');
  if (el) el.textContent = text;
}
function boxesShown() {
  const c = $('chkBoxes');
  return !c || c.checked;
}
function onToggleBoxes() {
  if (!boxesShown()) clearCanvas(); else drawDetections(lastLiveDets);
  setLiveHint(boxesShown() ? '已开启检测框叠加' : '已隐藏检测框（识别仍在继续）');
}

function clearCanvas() {
  const cv = $('liveCanvas');
  if (!cv) return;
  const ctx = cv.getContext('2d');
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, cv.width, cv.height);
}
function syncCanvasSize() {
  const cv = $('liveCanvas'), wrap = $('liveWrap');
  if (!cv || !wrap) return null;
  const dpr = window.devicePixelRatio || 1;
  const cw = wrap.clientWidth, ch = wrap.clientHeight;
  if (!(cw > 0) || !(ch > 0)) return null;
  if (cv.width !== Math.round(cw * dpr) || cv.height !== Math.round(ch * dpr)) {
    cv.width = Math.round(cw * dpr);
    cv.height = Math.round(ch * dpr);
  }
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cw, ch);
  return {ctx: ctx, w: cw, h: ch};
}
// 把框画到 ctx 的 rect 区域内（屏幕叠加与截图共用；单位是 ctx 的当前坐标系）
function drawBoxesOn(ctx, dets, frameW, frameH, rect, lineWidth, fontPx) {
  (dets || []).forEach((d, i) => {
    const b = boxToViewRect(d.bbox, frameW, frameH, rect);
    if (!b || b.w <= 0 || b.h <= 0) return;
    const color = colorTupleForLabel(i);
    ctx.lineWidth = lineWidth;
    ctx.strokeStyle = cssRgb(color);
    ctx.strokeRect(b.x, b.y, b.w, b.h);

    const text = String(d.label_en || d.class_name || '') +
                 ' ' + (Number(d.confidence) || 0).toFixed(2);
    ctx.font = 'bold ' + fontPx + 'px -apple-system,"Microsoft YaHei",sans-serif';
    const tw = ctx.measureText(text).width;
    const labelH = fontPx + 4;
    let ly = b.y - labelH;
    if (ly < 0) ly = Math.min(b.y + 2, Math.max(0, rect.y + rect.h - labelH));
    const lx = Math.max(rect.x, Math.min(b.x, rect.x + rect.w - tw - 8));
    ctx.fillStyle = cssRgb(color);
    ctx.fillRect(lx, ly, tw + 8, labelH);
    ctx.fillStyle = textColorOn(color);
    ctx.fillText(text, lx + 4, ly + fontPx);
  });
}
function drawDetections(dets) {
  if (mode !== 'live') return;
  const sized = syncCanvasSize();
  if (!sized) return;
  const list = dets || [];
  if (!list.length || !boxesShown()) return;          // 0 目标 → 画布已是干净的
  const video = $('phoneVideo');
  const frameW = (liveSentFrame && liveSentFrame.w) || video.videoWidth;
  const frameH = (liveSentFrame && liveSentFrame.h) || video.videoHeight;
  const rect = fitRect(sized.w, sized.h, video.videoWidth || frameW, video.videoHeight || frameH);
  if (!rect) return;
  drawBoxesOn(sized.ctx, list, frameW, frameH, rect, 2, 12);
}

// 检测列表（拍照/实时共用）：按置信度降序 + 进度条；整列颜色跟当帧风险等级走
function renderDetList(list, el, riskLevel) {
  if (!el) return;
  const dets = (list || []).slice().sort((a, b) => (Number(b.confidence) || 0) - (Number(a.confidence) || 0));
  if (!dets.length) { el.innerHTML = '<div class="empty">未检测到害虫目标</div>'; return; }
  const cls = clsOfRisk(riskLevel);
  const shown = dets.slice(0, 10);
  el.innerHTML = shown.map(d => {
    const conf = Math.round((Number(d.confidence) || 0) * 1000) / 10;
    return '<div class="det ' + cls + '">' +
           '<div class="line"><span>' + escapeHtml(d.chinese_name || d.class_name || '') + '</span>' +
           '<span class="conf">' + conf.toFixed(1) + '%</span></div>' +
           '<div class="bar"><i style="width:' + Math.max(2, Math.min(100, conf)) + '%"></i></div></div>';
  }).join('') + (dets.length > shown.length
    ? '<div class="empty">…还有 ' + (dets.length - shown.length) + ' 个目标</div>' : '');
}

function liveFps() {
  if (liveAckTimes.length < 2) return 0;
  const span = (liveAckTimes[liveAckTimes.length - 1] - liveAckTimes[0]) / 1000;
  return span > 0 ? (liveAckTimes.length - 1) / span : 0;
}
function resetLiveStats() {
  liveAckTimes = []; liveRtts = [];
  const c = $('statCount'), r = $('statRisk'), s = $('statServer'), t = $('statRtt'), f = $('statFps');
  if (c) c.textContent = '目标 --';
  if (r) { r.className = 'chip none'; r.textContent = '风险 --'; }
  if (s) s.textContent = '服务端 --';
  if (t) t.textContent = '往返 --';
  if (f) f.textContent = 'FPS --';
}
function liveStatsRender(data) {
  const risk = (data && data.risk) || {};
  const list = (data && data.detections) || [];
  const c = $('statCount'), r = $('statRisk'), s = $('statServer'), t = $('statRtt'), f = $('statFps');
  if (c) c.textContent = '目标 ' + list.length;
  if (r) { r.className = 'chip ' + clsOfRisk(risk.level); r.textContent = '风险 ' + (risk.level || '--'); }
  if (s) s.textContent = '服务端 ' + (data && data.elapsed != null ? data.elapsed : '--') + 's';
  if (t) {
    const avg = liveRtts.length ? liveRtts.reduce((a, b) => a + b, 0) / liveRtts.length : null;
    t.textContent = '往返 ' + (avg == null ? '--' : (avg / 1000).toFixed(2) + 's');
  }
  if (f) f.textContent = 'FPS ' + liveFps().toFixed(1);
}

function openCamera(facing) {
  return navigator.mediaDevices.getUserMedia({
    video: {facingMode: {ideal: facing}, width: {ideal: 1280}, height: {ideal: 720}},
    audio: false
  });
}

async function startLive() {
  if (!window.isSecureContext) {
    // 顶层导航（location.href）才会弹出可点的证书提示；后台 fetch 不会
    if (HTTPS_ENABLED) {
      if (confirm('实时模式需要 HTTPS 打开本页面。\n现在跳到 ' + HTTPS_URL + ' ？\n'
                  + '（首次会提示"不是私密连接"，选「高级 → 继续前往」）')) {
        location.href = HTTPS_URL;
      }
    } else {
      alert('实时模式需要 HTTPS，但服务器这次是纯 HTTP 启动（--no-https 或证书不可用）。\n'
            + '重启服务器时不要加 --no-https，再访问 ' + HTTPS_URL + '\n'
            + '现在可以继续用「拍照模式」，它不需要 HTTPS。');
    }
    return;
  }
  if (liveActive && liveStream) return;            // 已经在跑
  try {
    liveStream = await openCamera(liveFacing);
  } catch (e) {
    alert('无法打开手机摄像头: ' + e.message + '\n请检查浏览器相机权限。');
    return;
  }
  const video = $('phoneVideo');
  video.srcObject = liveStream;
  video.onloadedmetadata = () => {
    // 按摄像头真实比例显示（去掉固定 4:3 的黑边），并立刻按新尺寸重绘
    if (video.videoWidth && video.videoHeight) {
      $('liveWrap').style.aspectRatio = video.videoWidth + ' / ' + video.videoHeight;
    }
    drawDetections(lastLiveDets);
  };
  liveActive = true;
  liveReconnect = 0;
  resetLiveStats();
  openWS();
}

function openWS() {
  if (!liveActive) return;
  if (liveWS && (liveWS.readyState === 0 || liveWS.readyState === 1)) return;
  try {
    liveWS = new WebSocket(WS_BASE);
    liveWS.binaryType = 'arraybuffer';
  } catch (e) {
    setLiveHint('WebSocket 连接失败: ' + e.message);
    return;
  }
  liveWS.onopen = () => {
    liveReconnect = 0;
    setStatus(true, '实时识别中（手机画面 → 服务器）');
    setLiveHint('实时识别中…');
    if (!liveTimer) liveTimer = setInterval(captureAndSend, __FRAME_INTERVAL__);
  };
  liveWS.onmessage = (ev) => {
    liveBusy = false;                              // 收到结果才允许发下一帧（单帧背压）
    const now = performance.now();
    liveAckTimes.push(now);
    if (liveAckTimes.length > 8) liveAckTimes.shift();
    if (liveSentFrame && liveSentFrame.t0) {
      liveRtts.push(now - liveSentFrame.t0);
      if (liveRtts.length > 8) liveRtts.shift();
    }
    let data = null;
    try { data = JSON.parse(ev.data); } catch (e) { return; }
    if (!data) return;
    if (data.error) { setLiveHint('服务器错误：' + data.error); return; }
    if (data.warning) { setLiveHint(data.warning); return; }

    const risk = data.risk || {};
    lastLiveDets = data.detections || [];
    const bar = $('riskBar');
    bar.className = 'risk ' + clsOfRisk(risk.level);
    bar.textContent = '风险等级：' + (risk.level || '--') + (risk.detail ? '（' + risk.detail + '）' : '');
    renderDetList(lastLiveDets, $('detList'), risk.level);
    if (lastLiveDets.length) lastContext = {detections: lastLiveDets, risk: risk};
    liveStatsRender(data);
    drawDetections(lastLiveDets);                  // ← 画框就在这一句
    setLiveHint('识别中 · ' + (data.ts || '') +
                (risk.detail ? ' · ' + risk.detail : ''));
  };
  liveWS.onclose = () => {
    liveWS = null;
    if (!liveActive) { setLiveHint('已断开'); setStatus(false, '实时连接已断开'); return; }
    scheduleReconnect();
  };
  liveWS.onerror = () => { setLiveHint('连接错误，准备重连…'); };
}

function scheduleReconnect() {
  if (liveReconnect >= LIVE_MAX_RECONNECT) {
    setLiveHint('实时连接已断开，重试 ' + LIVE_MAX_RECONNECT + ' 次仍未连上；'
                + '请检查服务器/网络后点「开始实时识别」');
    setStatus(false, '实时连接已断开');
    return;
  }
  liveReconnect += 1;
  const delay = Math.min(5000, 500 * Math.pow(2, liveReconnect - 1));
  setLiveHint('连接断开，' + (delay / 1000).toFixed(1) + ' 秒后重连'
              + '（第 ' + liveReconnect + '/' + LIVE_MAX_RECONNECT + ' 次）…');
  setStatus(false, '重连中(第 ' + liveReconnect + ' 次)');
  clearTimeout(liveReconnectTimer);
  liveReconnectTimer = setTimeout(() => { if (liveActive) openWS(); }, delay);
}

function captureAndSend() {
  if (!liveWS || liveWS.readyState !== 1 || liveBusy) return;
  const video = $('phoneVideo');
  if (!video.videoWidth) return;
  const maxSide = __UPLOAD_MAX_SIDE__;
  const scale = Math.min(1, maxSide / Math.max(video.videoWidth, video.videoHeight));
  const w = Math.round(video.videoWidth * scale), h = Math.round(video.videoHeight * scale);
  const cv = document.createElement('canvas');
  cv.width = w; cv.height = h;
  cv.getContext('2d').drawImage(video, 0, 0, w, h);
  cv.toBlob((blob) => {
    if (!blob || !liveWS || liveWS.readyState !== 1) return;
    liveBusy = true;
    // 结果的 bbox 就是这一帧的像素坐标：记下尺寸，回来时才能映射到显示坐标
    liveSentFrame = {w: w, h: h, t0: performance.now()};
    liveWS.send(blob);                     // 直接发二进制 JPEG（比 base64 省 ~33%）
    // 1.5 秒还没回结果就解锁，避免卡死
    setTimeout(() => { liveBusy = false; }, 1500);
  }, 'image/jpeg', 0.7);
}

async function switchCamera() {
  if (!liveActive || !liveStream) {
    alert('先点「开始实时识别」，运行中再切换前后摄像头。');
    return;
  }
  const next = (liveFacing === 'environment') ? 'user' : 'environment';
  let stream = null;
  try {
    stream = await openCamera(next);
  } catch (e) {
    alert('切换摄像头失败：' + e.message + '\n继续使用当前摄像头。');
    return;
  }
  const old = liveStream;
  liveStream = stream;
  $('phoneVideo').srcObject = stream;              // 先挂新流，再停旧流
  old.getTracks().forEach(t => t.stop());
  liveFacing = next;
  liveSentFrame = null; lastLiveDets = null;
  clearCanvas();
  setLiveHint('已切到' + (next === 'user' ? '前置' : '后置') + '摄像头，等待识别结果…');
}

function toggleZoom() {
  const wrap = $('liveWrap');
  wrap.classList.toggle('zoom');
  const on = wrap.classList.contains('zoom');
  $('btnZoom').textContent = on ? '⤡ 退出放大' : '⛶ 放大';
  // 等浏览器完成布局，再按新尺寸重绘（否则框还用旧矩形）
  setTimeout(() => drawDetections(lastLiveDets), 80);
}

// 截图（带框）：直接把"当前帧 + 当前框"画到离屏 canvas，现场可贴 PPT/答辩举证
function saveShot() {
  const video = $('phoneVideo');
  if (!video.videoWidth) { alert('还没有画面：先点「开始实时识别」。'); return; }
  const frameW = (liveSentFrame && liveSentFrame.w) || video.videoWidth;
  const frameH = (liveSentFrame && liveSentFrame.h) || video.videoHeight;
  const cv = document.createElement('canvas');
  cv.width = frameW; cv.height = frameH;
  const ctx = cv.getContext('2d');
  ctx.drawImage(video, 0, 0, frameW, frameH);
  if (boxesShown() && lastLiveDets && lastLiveDets.length) {
    // 离屏图上没有黑边：整帧就是显示矩形
    const fontPx = Math.max(14, Math.round(frameW / 34));
    drawBoxesOn(ctx, lastLiveDets, frameW, frameH,
                {x: 0, y: 0, w: frameW, h: frameH},
                Math.max(2, Math.round(frameW / 400)), fontPx);
  }
  cv.toBlob((blob) => {
    if (!blob) { alert('截图失败（可能内存不足），请重试。'); return; }
    const d = new Date();
    const pad = n => String(n).padStart(2, '0');
    const name = 'cropguard_' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) +
                 '_' + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds()) + '.jpg';
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    if ('download' in a) {
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
    } else {
      window.open(url, '_blank');                  // iOS：新标签打开后长按保存
      alert('已在新标签打开截图，长按图片即可保存到相册。');
    }
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }, 'image/jpeg', 0.92);
}

function stopLive() {
  liveActive = false;                              // 先关重连意愿，再关连接
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
  clearTimeout(liveReconnectTimer); liveReconnectTimer = null;
  liveReconnect = 0;
  if (liveWS) {
    try { liveWS.onclose = null; liveWS.onerror = null; liveWS.close(); } catch (e) {}
    liveWS = null;
  }
  if (liveStream) { liveStream.getTracks().forEach(t => t.stop()); liveStream = null; }
  $('phoneVideo').srcObject = null;
  lastLiveDets = null; liveSentFrame = null;
  clearCanvas();
  resetLiveStats();
  const wrap = $('liveWrap');
  if (wrap) wrap.classList.remove('zoom');
  $('btnZoom').textContent = '⛶ 放大';
  setLiveHint('未开始');
  if (mode === 'live') setStatus(true, '已停止实时识别');
}

// 显示尺寸变化时按缓存结果重绘（转屏 / 放大 / 窗口缩放）
window.addEventListener('resize', () => { if (mode === 'live') drawDetections(lastLiveDets); });
window.addEventListener('orientationchange', () => {
  setTimeout(() => { if (mode === 'live') drawDetections(lastLiveDets); }, 300);
});

// ==================== 大模型问答 ====================
async function askLLM() {
  const q = $('chatInput').value.trim();
  if (!q) return;
  $('chatInput').value = '';
  addChat('user', q);
  const thinking = addChat('assistant', '正在检索知识库并调用本地大模型…');
  try {
    const data = await post('/api/ask', {question: q, context: lastContext});
    thinking.remove();
    if (!data.success) { addChat('assistant', '问答失败：' + (data.error || '未知错误')); return; }
    const item = addChat('assistant', data.answer, data.citations, data);
    if (data.source === 'refused') item.classList.add('refuse');
  } catch (e) {
    thinking.remove();
    addChat('assistant', '请求失败：' + e.message);
  }
}
function addChat(who, text, citations, meta) {
  const el = document.createElement('div');
  el.className = 'chat-item';
  let html = '<div><span class="who">' + (who === 'user' ? '你' : 'AI') + '：</span>' +
             escapeHtml(text).replace(/\n/g, '<br>') + '</div>';
  const tips = [];
  if (meta) {
    const src = {llm: '本地大模型（已接地）', knowledge_base: '知识库兜底',
                 refused: '拒答（知识库未涵盖）'}[meta.source] || meta.source;
    if (src) tips.push('来源：' + src);
    if (meta.engine) tips.push('模型：' + meta.engine);
    if (meta.elapsed != null) tips.push('耗时 ' + meta.elapsed + 's');
    const g = meta.guard || {};
    if (g.unsupported && g.unsupported.length) tips.push('⚠已标注可疑药名：' + g.unsupported.join('、'));
    if (g.dosage_unsupported && g.dosage_unsupported.length) tips.push('⚠已标注可疑剂量');
    if (g.top_score != null) tips.push('召回分 ' + g.top_score);
  }
  if (citations && citations.length) {
    tips.push('依据：' + citations.map(c => c.title).join('；'));
  }
  if (tips.length) html += '<div class="meta">' + escapeHtml(tips.join(' | ')) + '</div>';
  el.innerHTML = html;
  $('chatBox').appendChild(el);
  el.scrollIntoView({block: 'end'});
  return el;
}
function clearChat() { $('chatBox').innerHTML = ''; }
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

// ==================== 喷洒控制 ====================
async function parseCommand() {
  const t = $('cmdInput').value.trim();
  if (!t) return;
  const data = await post('/api/ask_command', {text: t, execute: false});
  const box = $('cmdConfirm');
  if (!data.valid) {
    box.className = 'confirm show';
    box.innerHTML = '⚠ 未执行：' + escapeHtml(data.reply || data.reason || '无法解析');
    return;
  }
  const label = data.intent === 'stop' ? '停止喷洒'
              : ('执行喷洒：' + (data.group === 'all' ? '全体' : data.group) + ' / ' + data.duration + ' 秒');
  box.className = 'confirm show';
  box.innerHTML = escapeHtml(data.reply) +
    '<div class="row"><button id="btnExec" class="danger">✅ ' + escapeHtml(label) + '</button>' +
    '<button class="ghost" onclick="hideConfirm()">取消</button></div>';
  $('btnExec').onclick = () => executeCommand(t);
}
async function executeCommand(text) {
  const data = await post('/api/ask_command', {text: text, execute: true});
  $('cmdConfirm').className = 'confirm show';
  $('cmdConfirm').innerHTML = data.executed
    ? '✅ 已下发喷洒指令（' + escapeHtml(String(data.group)) + ' / ' + data.duration + ' 秒）'
    : '⚠ 指令未下发：' + (data.available ? '执行失败' : '节点链路不可用（喷雾节点未在线）');
  refreshHistory();
}
function hideConfirm() { $('cmdConfirm').className = 'confirm'; }
async function manualSpray(group, duration) {
  const name = group === 'all' ? '全体片区' : group;
  if (!confirm('确认给 ' + name + ' 喷洒 ' + duration + ' 秒？')) return;
  const data = await post('/api/spray', {group: group, duration: duration, via: 'phone'});
  $('sprayHint').textContent = data.success
    ? '✅ 已下发（' + name + ' / ' + duration + 's）'
    : '⚠ 下发失败：喷淋链路不可用（检查 SPRAY.http_targets 与节点是否在线）';
  refreshHistory();
}

// ==================== 雾化控制（ESP32 直连） ====================
// 对应手写固件 sketch_oct12a.ino 的接口：GET /start?time=N、GET /startDirect、GET /stop。
// 手机不直连节点：这里的每个动作都发给服务器，由服务器（feedback/spray.py 的 mist 协议适配）
// 下发到节点 —— 这样手机换网络、节点换 IP 都不影响，而且能留历史。
const MIST_URL = "__MIST_URL__";
const MIST_MAX_SEC = __MIST_MAX_SEC__;
const MIST_DIRECT_SEC = __MIST_DIRECT_SEC__;
const MIST_MODE_TEXT = {timed: '定时模式', direct: '持续模式', idle: '空闲', unknown: '运行中'};

if (MIST_URL) {
  $('mistNodeLink').href = MIST_URL.replace(/\/+$/, '') + '/';
} else {
  $('mistNodeLink').removeAttribute('href');
  $('mistNodeLink').textContent = '（未配置 mist 节点）';
}
$('mistSec').max = MIST_MAX_SEC;
$('mistMaxSec').textContent = MIST_MAX_SEC;
$('mistDirectSec').textContent = MIST_DIRECT_SEC > 0 ? MIST_DIRECT_SEC : '未启用';

function mistHint(text, ok) {
  const el = $('mistState');
  el.className = 'risk ' + (ok === false ? 'high' : 'none');
  el.textContent = text;
}

async function mistStart() {
  const sec = parseInt($('mistSec').value, 10) || 3;
  mistHint('正在下发定时喷雾…', null);
  try {
    const d = await post('/api/mist/start', {seconds: sec, group: 'all'});
    mistHint(d.success
      ? '✅ 已启动定时喷雾 ' + d.duration + ' 秒' + (d.clamped ? '（已按上限钳制）' : '')
      : '⚠ 未启动：' + (d.error || '节点未响应'), !!d.success);
  } catch (e) { mistHint('⚠ 请求失败：' + e.message, false); }
  refreshMist();
  refreshHistory();
}

async function mistDirect() {
  const cap = MIST_DIRECT_SEC > 0
    ? '服务器会在 ' + MIST_DIRECT_SEC + ' 秒后自动停止'
    : '服务器不做兜底，必须手动点「停止雾化」';
  if (!confirm('确认开启持续喷雾？\n' + cap + '。')) return;
  mistHint('正在下发持续喷雾…', null);
  try {
    const d = await post('/api/mist/direct', {group: 'all'});
    mistHint(d.success ? '✅ 持续喷雾已开启（' + cap + '）'
                       : '⚠ 未开启：' + (d.error || '节点未响应'), !!d.success);
  } catch (e) { mistHint('⚠ 请求失败：' + e.message, false); }
  refreshMist();
  refreshHistory();
}

async function mistStop() {
  mistHint('正在停止…', null);
  try {
    const d = await post('/api/mist/stop', {group: 'all'});
    mistHint(d.success ? '⏹ 已停止雾化' : '⚠ 停止失败：' + (d.error || '节点未响应'), !!d.success);
  } catch (e) { mistHint('⚠ 请求失败：' + e.message, false); }
  refreshMist();
  refreshHistory();
}

async function refreshMist() {
  if (document.hidden) return;                 // 页面在后台就别去轮询节点了
  try {
    const r = await fetch(API_BASE + '/api/mist/status?group=all');
    const data = await r.json();
    if (!data.success) { mistHint('⚠ 状态读取失败：' + (data.error || ''), false); return; }
    const nodes = data.nodes || [];
    if (!nodes.length) {
      mistHint('未配置雾化节点：在 SPRAY.http_targets 里加一项 protocol: mist', false);
      return;
    }
    const parts = nodes.map(n => {
      const addr = String(n.url || '').replace(/^https?:\/\//, '');
      if (!n.reachable) return addr + ' 离线（' + (n.error || '不可达') + '）';
      let s = addr + ' · ' + (MIST_MODE_TEXT[n.mode] || n.mode || '--');
      if (n.remain != null) s += ' 剩余 ' + n.remain + 's';
      if (n.direct_remaining != null) s += '（兜底 ' + n.direct_remaining + 's 后停）';
      return s;
    });
    const anyUp = nodes.some(n => n.reachable);
    const running = nodes.some(n => n.reachable && n.running);
    mistHint('节点状态：' + parts.join('；'), anyUp && !running ? true : (anyUp ? null : false));
  } catch (e) {}
}

// ==================== 历史 ====================
async function refreshHistory() {
  try {
    const r = await fetch(API_BASE + '/api/history');
    const data = await r.json();
    const list = (data.history || []).slice().reverse();
    const el = $('histList');
    if (!list.length) { el.innerHTML = '<div class="empty">暂无记录</div>'; return; }
    el.innerHTML = list.map(h => {
      const tag = h.source === 'manual' ? 'manual'
                : (h.source === 'mist' ? 'mist' : clsOfRisk(h.risk));
      const label = h.source === 'manual' ? '手动' : (h.risk || '');
      const act = h.group
        ? ' → ' + (h.group === 'all' ? '全体' : h.group) + (h.duration ? ' 喷 ' + h.duration + 's' : '')
        : '';
      const det = (h.detections && h.detections.length) ? '<br>' + escapeHtml(h.detections.join('、')) : '';
      return '<div class="hist"><span class="tag ' + tag + '">' + escapeHtml(label) + '</span>' +
             escapeHtml(h.ts || '') + escapeHtml(act) + det + '</div>';
    }).join('');
  } catch (e) {}
}

// ==================== 启动 ====================
(async function init() {
  // 实时卡片的提示文案直接反映真实参数（占位符由服务端注入，别写死数字）
  const iv = $('liveIntervalMs'), ms = $('liveMaxSide');
  if (iv) iv.textContent = __FRAME_INTERVAL__;
  if (ms) ms.textContent = __UPLOAD_MAX_SIDE__;
  try {
    const r = await fetch(API_BASE + '/api/health');
    const data = await r.json();
    setStatus(true, '已连接 · cpu · ' + (data.classes || 0) + ' 类');
  } catch (e) {
    setStatus(false, IS_HTTPS
      ? 'HTTPS 接口不可达（证书可能没被信任，或服务未启动）'
      : '服务器未连接（检查是否同一 WiFi）');
  }
  refreshHistory();
  setInterval(refreshHistory, 4000);
  refreshMist();
  setInterval(refreshMist, 3000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshMist();
    if (document.hidden && mode === 'live') stopLive();
  });
})();
