/* ============================================================
 * CropGuard 现场测评页 /eval 逻辑
 *  - 样本来源：服务器目录（EVAL.sample_dir）/ 本地文件夹 / 本地多选图片
 *  - 计时口径：第一张样本开始识别起表 -> 最后一张识别完成停表（与 GUI 一致）
 *  - 得分：走 /api/eval/score（服务端与 GUI 同一份纯函数），本地仅做实时预览兜底
 *  - 动效：扫描线 / 卡片 flip-in / 计时环 / 用时条生长 / 数字滚动 / 撒花
 * ============================================================ */
'use strict';

const $ = (id) => document.getElementById(id);

const S = {
  cfg: null,            // /api/eval/config
  images: [],           // [{name, url, file|null, size}]
  records: [],          // [{no,name,time,detections,consum,wall,elapsed,image,risk,verdict,err}]
  running: false,
  aborted: false,
  t0: 0,                // 起表（performance.now）
  tStop: 0,             // 停表
  rafId: 0,
  curIdx: -1,
  scoreDebounce: 0,
  lastScoreText: {},
};

/* ---------------- 基础工具 ---------------- */
const fmt1 = (v) => (Math.round(v * 10) / 10).toFixed(1);
const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
};
function setMsg(text, cls) {
  const m = $('runMsg');
  m.textContent = text || '';
  m.className = 'run-msg' + (cls ? ' ' + cls : '');
}
function elapsedSec(now) {
  if (!S.t0) return 0;
  const end = S.tStop || (now || performance.now());
  return Math.max(0, (end - S.t0) / 1000);
}
function popNumber(nodeId, text) {
  const n = $(nodeId);
  if (S.lastScoreText[nodeId] === text) return;
  S.lastScoreText[nodeId] = text;
  n.textContent = text;
  n.classList.remove('pop');
  void n.offsetWidth;               // 重启动画
  n.classList.add('pop');
}

/* ---------------- 配置 / 样本加载 ---------------- */
async function boot() {
  try {
    const cfg = await (await fetch('/api/eval/config')).json();
    S.cfg = cfg;
    $('limitTime').textContent = fmt0(cfg.limit);
    $('accFull').textContent = fmt0(cfg.acc_full);
    $('timeFull').textContent = fmt0(cfg.time_full);
    $('totalFull').textContent = fmt0(cfg.total_full);
    $('targetN').textContent = cfg.target;
    $('ringBig').textContent = fmt1(cfg.limit);
    const srcTxt = cfg.sample_dir_exists && cfg.sample_count
      ? `服务器样本目录：<b>${cfg.sample_dir}</b>（${cfg.sample_count} 张）`
      : `服务器样本目录不可用（<code>${cfg.sample_dir}</code>${cfg.sample_dir_exists ? ' 为空' : ' 不存在'}）`;
    $('srcStatus').innerHTML = srcTxt + ' · 目标 <b>' + cfg.target + '</b> 样本 / <b>' + fmt0(cfg.limit) + '</b> 秒';
    if (cfg.sample_count) {
      $('btnLoadSrv').disabled = false;
      loadServerSamples();          // 现场默认直接载入
    } else {
      setMsg('服务器没有样本目录 —— 用「选择文件夹」选现场那 20 张，或把图片放进 ' + cfg.sample_dir, 'warn');
    }
  } catch (e) {
    setMsg('读取测评配置失败: ' + e.message, 'warn');
  }
}

async function loadServerSamples() {
  try {
    const r = await (await fetch('/api/eval/samples')).json();
    S.images = (r.samples || []).map((s) => ({
      name: s.name,
      url: '/api/eval/sample_image?name=' + encodeURIComponent(s.name),
      file: null, size: s.size,
    }));
    afterLoad(`已从服务器载入 ${S.images.length} 张样本`);
  } catch (e) {
    setMsg('载入服务器样本失败: ' + e.message, 'warn');
  }
}

function loadClientFiles(fileList) {
  const files = Array.from(fileList || []).filter((f) => f.type.startsWith('image/'));
  if (!files.length) { setMsg('没有可用的图片文件', 'warn'); return; }
  files.sort((a, b) => (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name));
  S.images = files.map((f) => ({
    name: f.webkitRelativePath || f.name,
    url: URL.createObjectURL(f),
    file: f, size: f.size,
  }));
  /* 显示短名（去掉相对路径前缀） */
  afterLoad(`已从本机载入 ${S.images.length} 张样本`);
}

function afterLoad(msg) {
  resetSession(false);
  renderGrid();
  renderStrip();
  const n = S.images.length;
  const tgt = S.cfg ? S.cfg.target : 20;
  setMsg(msg + (n < tgt ? `（少于目标 ${tgt} 张，得分按实际正确数计）` : n > tgt ? `（超出目标 ${tgt} 张，只测评前 ${tgt} 张）` : ''),
         n < tgt ? 'warn' : 'ok');
  $('btnStart').disabled = !n;
  $('btnReset').disabled = !n;
  $('gridEmpty').style.display = n ? 'none' : '';
}

function renderStrip() {
  const strip = $('strip');
  strip.innerHTML = '';
  S.images.slice(0, 24).forEach((im) => {
    const t = el('img', 'thumb');
    t.src = im.url; t.title = im.name;
    strip.appendChild(t);
  });
  if (S.images.length > 24) strip.appendChild(el('span', '', ` …共 ${S.images.length} 张`));
}

/* ---------------- 网格 ---------------- */
function renderGrid() {
  const grid = $('grid');
  grid.innerHTML = '';
  const n = S.cfg ? Math.min(S.cfg.target, Math.max(S.images.length, 0)) : S.images.length;
  for (let i = 0; i < n; i++) grid.appendChild(buildCell(i));
  $('gridEmpty').style.display = n ? 'none' : '';
  $('gridStat').textContent = n ? `共 ${n} 张（目标 ${S.cfg ? S.cfg.target : 20}）` : '';
}

function buildCell(i) {
  const im = S.images[i];
  const cell = el('div', 'cell pending');
  cell.id = 'cell-' + i;
  cell.innerHTML =
    `<span class="no-idx">#${String(i + 1).padStart(2, '0')}</span>` +
    `<img class="pic" src="${im.url}" alt="">` +
    `<div class="body">` +
    `  <div class="nm" title="${esc(im.name)}">${esc(shortName(im.name))}</div>` +
    `  <div class="dn">待测</div>` +
    `  <div class="tm"></div>` +
    `  <div class="verdicts" style="visibility:hidden">` +
    `    <button class="vbtn ok" onclick="setVerdict(${i}, '正确')">✓ 正确</button>` +
    `    <button class="vbtn no" onclick="setVerdict(${i}, '错误')">✗ 错误</button>` +
    `  </div>` +
    `</div>`;
  return cell;
}

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const shortName = (s) => String(s).split(/[\\/]/).pop();
const fmt0 = (v) => String(Math.round(v));

function updateCell(i) {
  const cell = $('cell-' + i);
  const rec = S.records[i];
  if (!cell || !rec) return;
  cell.classList.remove('pending', 'running');
  cell.classList.add('done');
  const dn = cell.querySelector('.dn');
  if (rec.err) {
    dn.innerHTML = '<span style="color:#a33">识别失败</span>';
  } else {
    const names = (rec.detections || []).map((d) => `${esc(d.chinese_name || d.class_name)} ${(d.confidence * 100).toFixed(0)}%`);
    dn.innerHTML = names.length ? esc(names.slice(0, 2).join('、')) + (names.length > 2 ? '…' : '')
                                : '<span style="color:#2e7d32">无目标</span>';
  }
  dn.title = rec.err ? rec.err : (rec.detections || []).map((d) => `${d.chinese_name || d.class_name} ${(d.confidence * 100).toFixed(1)}%`).join('、');
  const tm = cell.querySelector('.tm');
  tm.innerHTML = `<b>${rec.wall}s</b><span class="wall">推理 ${rec.consum}s</span>`;
  const v = cell.querySelector('.verdicts');
  v.style.visibility = 'visible';
  syncVerdictBtns(i);
  const risk = cell.querySelector('.risk');
  if (rec.risk && !rec.err) {
    const lvl = rec.risk.level || '';
    const cls = lvl === '高风险' ? 'high' : lvl === '中风险' ? 'medium' : (lvl === '低风险' ? 'low' : 'none');
    const b = el('span', 'risk ' + cls, esc(lvl));
    if (!risk) cell.appendChild(b); else risk.replaceWith(b);
  }
  cell.querySelector('.pic').src = rec.image || cell.querySelector('.pic').src;
}

function syncVerdictBtns(i) {
  const cell = $('cell-' + i);
  if (!cell) return;
  const rec = S.records[i] || {};
  const ok = cell.querySelector('.vbtn.ok');
  const no = cell.querySelector('.vbtn.no');
  ok.classList.toggle('on', rec.verdict === '正确');
  no.classList.toggle('on', rec.verdict === '错误');
}

function setVerdict(i, v) {
  const rec = S.records[i];
  if (!rec) return;
  rec.verdict = (rec.verdict === v) ? null : v;
  syncVerdictBtns(i);
  refreshScore();
}

/* ---------------- 主流程 ---------------- */
async function start() {
  if (S.running || !S.images.length) return;
  S.running = true; S.aborted = false; S.t0 = 0; S.tStop = 0;
  S.records = [];
  S.lastScoreText = {};
  barMax = 1;
  const list = S.images.slice(0, S.cfg.target);
  renderGrid();
  $('btnStart').disabled = true;
  $('btnStop').disabled = false;
  $('btnReset').disabled = true;
  $('btnExport').disabled = true;
  $('timerPanel').classList.remove('overtime');
  $('totalVerdict').style.display = 'none';
  setMsg(`开始测评：共 ${list.length} 张，第一张起表…`, 'ok');
  $('raceState').textContent = '计时中…';
  tickLoop();

  for (let i = 0; i < list.length; i++) {
    if (S.aborted) break;
    S.curIdx = i;
    $('currentPanel').hidden = false;
    await runOne(i, list[i]);
    await sleep(140);               // 演示节奏：留一点时间让每张卡"落地"
  }
  finish();
}

async function runOne(i, im) {
  const cell = $('cell-' + i);
  if (cell) cell.classList.add('running');
  $('curImg').src = im.url;
  $('curScan').style.display = '';
  $('curName').textContent = `#${String(i + 1).padStart(2, '0')} · ${shortName(im.name)}`;
  $('curMeta').innerHTML = '<span class="cur-spinner"></span>识别中…';

  if (!S.t0) { S.t0 = performance.now(); }     // 第一张开始识别 -> 起表

  const started = performance.now();
  let data = null, errMsg = '';
  try {
    let blob = im.file;
    if (!blob) {
      const r = await fetch(im.url);
      if (!r.ok) throw new Error('样本读取失败 HTTP ' + r.status);
      blob = await r.blob();
    }
    const fd = new FormData();
    fd.append('file', blob, shortName(im.name) || ('sample_' + (i + 1) + '.jpg'));
    fd.append('conf', $('conf').value);
    const resp = await fetch('/api/detect', { method: 'POST', body: fd });
    data = await resp.json();
    if (!data || !data.success) errMsg = (data && data.error) || '检测接口返回失败';
  } catch (e) {
    errMsg = e.message || String(e);
  }
  const wall = (performance.now() - started) / 1000;

  const rec = {
    no: i + 1,
    name: shortName(im.name),
    time: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
    detections: data ? (data.detections || []) : [],
    consum: data ? (data.inference_time != null ? data.inference_time : '') : '',
    wall: wall.toFixed(2),
    elapsed: fmt1(elapsedSec()),
    image: data ? ('data:image/jpeg;base64,' + data.result_image) : '',
    risk: data ? data.risk : null,
    verdict: null,
    err: errMsg || null,
  };
  S.records[i] = rec;
  updateCell(i);
  addBar(rec);
  $('curScan').style.display = 'none';
  $('curMeta').innerHTML = rec.err
    ? `<span style="color:#a33">识别失败：${esc(rec.err)}</span>`
    : `<b>${rec.wall}s</b>（服务端推理 ${rec.consum}s） · ${rec.detections.length} 个目标 · 累计 <b>${rec.elapsed}s</b>`;
  refreshScore();
}

function finish() {
  if (S.t0 && !S.tStop) S.tStop = performance.now();
  S.running = false;
  $('btnStart').disabled = !S.images.length;
  $('btnStop').disabled = true;
  $('btnReset').disabled = !S.images.length;
  $('btnExport').disabled = !S.records.filter(Boolean).length;
  $('curScan').style.display = 'none';
  const used = elapsedSec();
  const limit = S.cfg.limit;
  if (S.aborted) {
    setMsg(`已停止（完成 ${S.records.filter(Boolean).length} 张，用时 ${fmt1(used)}s）`, 'warn');
    $('raceState').textContent = '已停止';
  } else if (used <= limit) {
    setMsg(`完成：${S.records.filter(Boolean).length} 张总用时 ${fmt1(used)}s，低于 ${fmt0(limit)}s 要求 ✓`, 'ok');
    $('raceState').textContent = '已完成';
    confetti();
  } else {
    setMsg(`完成：总用时 ${fmt1(used)}s，超出 ${fmt0(limit)}s 限时 ${fmt1(used - limit)}s`, 'warn');
    $('raceState').textContent = '已完成（超时）';
  }
  refreshScore(true);
  tickRender();                     // 收尾一帧，冻结显示
  setTimeout(() => { $('currentPanel').hidden = true; }, 1600);
}

function stopRun() {
  if (!S.running) return;
  S.aborted = true;
  setMsg('正在停止（等当前这张识别完）…', 'warn');
}

function resetSession(clearImages) {
  S.running = false; S.aborted = false; S.t0 = 0; S.tStop = 0;
  S.records = []; S.curIdx = -1; S.lastScoreText = {};
  barMax = 1;
  cancelAnimationFrame(S.rafId);
  $('timerPanel').classList.remove('overtime');
  $('currentPanel').hidden = true;
  $('totalVerdict').style.display = 'none';
  $('trackFill').style.width = '0%';
  $('trackFill').classList.remove('overtime');
  $('totalTime').textContent = '0.0';
  $('totalTime').classList.remove('overtime');
  $('ringBig').textContent = fmt1(S.cfg ? S.cfg.limit : 180);
  $('ringBig').classList.remove('overtime');
  $('ringSub').textContent = '秒剩余 · 待开始';
  $('ringFg').style.strokeDashoffset = 0;
  $('raceState').textContent = '点「开始测评」后，第一张样本自动起表';
  $('aheadText').textContent = '尚未开始';
  $('bars').innerHTML = '';
  $('barsEmpty').style.display = '';
  $('scoreAcc').textContent = '0.0';
  $('scoreTime').textContent = fmt1(S.cfg ? S.cfg.time_full : 10);
  $('scoreTotal').textContent = '0.0';
  $('judgeStat').innerHTML = `样本 0 / <span id="targetN">${S.cfg ? S.cfg.target : 20}</span> · 已评判 0 · 正确 0`;
  $('btnExport').disabled = true;
  $('btnStop').disabled = true;
  if (clearImages) { S.images = []; }
  renderGrid();
  setMsg('');
}

/* ---------------- 计时渲染 ---------------- */
function tickLoop() {
  cancelAnimationFrame(S.rafId);
  const step = () => {
    tickRender();
    if (S.running) S.rafId = requestAnimationFrame(step);
  };
  S.rafId = requestAnimationFrame(step);
}

function tickRender() {
  if (!S.cfg || !S.t0) return;
  const used = elapsedSec();
  const limit = S.cfg.limit;
  const left = limit - used;
  const C = 2 * Math.PI * 88;

  $('ringFg').style.strokeDasharray = C;
  const frac = Math.max(0, Math.min(1, left / limit));
  $('ringFg').style.strokeDashoffset = C * (1 - frac);
  $('ringFg').style.stroke = left <= 0 ? '' : (left < 30 ? '#e8890c' : '#3f7a41');
  const over = used > limit;
  $('ringBig').textContent = over ? '+' + fmt1(used - limit) : fmt1(Math.max(0, left));
  $('ringBig').classList.toggle('overtime', over);
  $('ringSub').textContent = over ? '已超时（转正计时）' : '秒剩余 · ' + (S.running ? '计时中' : '已停表');
  $('timerPanel').classList.toggle('overtime', over && S.running);

  $('totalTime').textContent = fmt1(used);
  $('totalTime').classList.toggle('overtime', over);
  const f = Math.min(1, limit ? used / limit : 0);
  $('trackFill').style.width = (f * 100).toFixed(1) + '%';
  $('trackFill').classList.toggle('overtime', over);

  if (S.running) {
    $('aheadText').innerHTML = over
      ? `已用 <b class="bad">${fmt1(used)}s</b>，超时 <b class="bad">${fmt1(used - limit)}s</b>（时效性按秒扣分中）`
      : `已用 ${fmt1(used)}s · 还剩 <b class="ok">${fmt1(left)}s</b>`;
  } else if (S.tStop) {
    $('aheadText').innerHTML = over
      ? `总用时 <b class="bad">${fmt1(used)}s</b> · 超出限时 <b class="bad">${fmt1(used - limit)}s</b>`
      : `总用时 <b class="ok">${fmt1(used)}s</b> · 提前 <b class="ok">${fmt1(limit - used)}s</b> 完成（限时 ${fmt0(limit)}s）`;
    const chip = $('totalVerdict');
    chip.style.display = '';
    chip.className = 'verdict-chip ' + (over ? 'bad' : 'ok');
    chip.textContent = over ? `超时 +${fmt1(used - limit)}s` : `✓ 提前 ${fmt1(limit - used)}s`;
  }
}

/* ---------------- 得分（服务端口径） ---------------- */
function localScore() {
  const recs = S.records.filter(Boolean);
  const judged = recs.filter((r) => r.verdict).length;
  const correct = recs.filter((r) => r.verdict === '正确').length;
  const used = elapsedSec();
  const cfg = S.cfg || { pps: 1, time_full: 10, penalty: .5, limit: 180, target: 20, acc_full: 20, total_full: 30 };
  const acc = Math.min(correct, cfg.target) * cfg.pps;
  const ts = Math.max(0, cfg.time_full - Math.max(0, used - cfg.limit) * cfg.penalty);
  return {
    total: recs.length, judged, correct,
    acc: Math.round(acc * 10) / 10,
    time_score: Math.round(ts * 10) / 10,
    score: Math.round((acc + ts) * 10) / 10,
    elapsed: Math.round(used * 10) / 10,
  };
}

function refreshScore(force) {
  const local = localScore();
  renderScore(local);
  clearTimeout(S.scoreDebounce);
  S.scoreDebounce = setTimeout(async () => {
    try {
      const r = await (await fetch('/api/eval/score', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          records: S.records.filter(Boolean).map((x) => ({ verdict: x.verdict })),
          elapsed: local.elapsed,
        }),
      })).json();
      if (r && r.success) renderScore({ ...r.score, judged: r.score.judged });
    } catch (e) { /* 离线/异常时保留本地预览值 */ }
  }, force ? 0 : 150);
}

function renderScore(s) {
  popNumber('scoreAcc', fmt1(s.acc));
  popNumber('scoreTime', fmt1(s.time_score));
  popNumber('scoreTotal', fmt1(s.score));
  $('judgeStat').innerHTML =
    `样本 ${s.total} / ${S.cfg ? S.cfg.target : 20} · 已评判 ${s.judged} · 正确 ${s.correct}`;
}

/* ---------------- 用时条 ---------------- */
let barMax = 1;
function addBar(rec) {
  $('barsEmpty').style.display = 'none';
  const wrap = $('bars');
  const v = parseFloat(rec.wall) || 0;
  barMax = Math.max(barMax, v);
  const bar = el('div', 'bar' + (rec.err ? ' err' : ''));
  const h = Math.max(4, Math.round((v / barMax) * 86));
  bar.style.height = h + 'px';
  bar.innerHTML = `<span class="tip">#${rec.no} ${rec.wall}s（推理 ${rec.consum}s）</span>`;
  wrap.appendChild(bar);
  /* 已存在的条按新基准重排 */
  const recs = S.records.filter(Boolean);
  Array.from(wrap.children).forEach((b, i) => {
    const r = recs[i];
    if (!r) return;
    const hv = Math.max(4, Math.round((parseFloat(r.wall) / barMax) * 86));
    b.style.height = hv + 'px';
  });
}

/* ---------------- 导出 ---------------- */
async function exportReport() {
  const recs = S.records.filter(Boolean);
  if (!recs.length) return;
  $('btnExport').disabled = true;
  setMsg('正在导出评分报告…');
  try {
    const r = await (await fetch('/api/eval/report', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        records: recs.map((x) => ({
          no: x.no, name: x.name, time: x.time, detections: x.detections,
          consum: x.consum, elapsed: x.elapsed, verdict: x.verdict,
        })),
        elapsed: elapsedSec(),
      }),
    })).json();
    if (r && r.success) {
      setMsg(`评分报告已导出：${r.path}（总分 ${r.score.score} / ${S.cfg.total_full}）`, 'ok');
      window.open(r.url, '_blank');
    } else {
      throw new Error((r && r.error) || '导出失败');
    }
  } catch (e) {
    setMsg('服务端导出失败（' + e.message + '），改用浏览器下载…', 'warn');
    clientCsv(recs);
  } finally {
    $('btnExport').disabled = false;
  }
}

function clientCsv(recs) {
  const used = elapsedSec();
  const s = localScore();
  const rows = [['编号', '图片路径', '输入时间', '识别结果(中文)', '目标数量', '推理耗时', '本轮用时(s)', '评判']];
  recs.forEach((x) => rows.push([
    x.no, x.name, x.time,
    (x.detections || []).map((d) => `${d.chinese_name || d.class_name}(${(d.confidence).toFixed(2)})`).join('、') || '无目标',
    (x.detections || []).length, x.consum, x.elapsed, x.verdict || '未评判',
  ]));
  rows.push([]);
  rows.push(['===== 现场评测汇总（口径：准确度 20 + 时效性 10 = 30）=====']);
  rows.push(['采集样本数', `${s.total} / ${S.cfg.target}`]);
  rows.push(['已评判', s.judged]);
  rows.push(['识别正确', s.correct]);
  rows.push(['准确度得分', `${s.acc} / ${S.cfg.acc_full}`]);
  rows.push(['本轮总用时(s)', fmt1(used)]);
  rows.push(['基准时间(s)', fmt0(S.cfg.limit)]);
  rows.push(['超时(s)', fmt1(Math.max(0, used - S.cfg.limit))]);
  rows.push(['时效性得分', `${s.time_score} / ${S.cfg.time_full}`]);
  rows.push(['总分', `${s.score} / ${S.cfg.total_full}`]);
  const csv = '\ufeff' + rows.map((r) => r.join(',')).join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = 'eval_report_web.csv';
  a.click();
}

/* ---------------- 撒花 ---------------- */
function confetti() {
  const layer = el('div', 'confetti-layer');
  document.body.appendChild(layer);
  const colors = ['#f6c453', '#7ee787', '#6cc26f', '#ffd8a8', '#8fd18f', '#fff3bf'];
  for (let i = 0; i < 120; i++) {
    const c = el('div', 'confetti');
    c.style.left = Math.random() * 100 + 'vw';
    c.style.background = colors[i % colors.length];
    c.style.setProperty('--dur', (2 + Math.random() * 1.6) + 's');
    c.style.setProperty('--delay', (Math.random() * 0.6) + 's');
    c.style.setProperty('--rot', (360 + Math.random() * 720) + 'deg');
    layer.appendChild(c);
  }
  setTimeout(() => layer.remove(), 4200);
}

/* ---------------- 事件绑定 ---------------- */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
$('conf').addEventListener('input', () => { $('confVal').textContent = $('conf').value; });
$('btnStart').addEventListener('click', start);
$('btnStop').addEventListener('click', stopRun);
$('btnReset').addEventListener('click', () => {
  if (S.running && !confirm('测评还在进行，确定要重置吗？')) return;
  resetSession(false);
  afterLoad(`已重置（保留 ${S.images.length} 张样本）`);
});
$('btnLoadSrv').addEventListener('click', loadServerSamples);
$('btnExport').addEventListener('click', exportReport);
$('dirInput').addEventListener('change', (e) => loadClientFiles(e.target.files));
$('fileInput').addEventListener('change', (e) => loadClientFiles(e.target.files));
document.addEventListener('keydown', (e) => {
  if (e.key === ' ' && !S.running && !$('btnStart').disabled) { e.preventDefault(); start(); }
});

boot();
