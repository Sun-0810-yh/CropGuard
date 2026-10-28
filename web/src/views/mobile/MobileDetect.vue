<script setup lang="ts">
// 手机识别：拍照档（HTTP 就能用）/ 实时档（需 HTTPS：getUserMedia 只在安全上下文开放）。
// 实时档把手机画面压缩成 JPEG 二进制帧经 /ws/phone 上传，服务器只回 JSON，框在本地叠加。
import { computed, onBeforeUnmount, ref } from 'vue'
import { api, wsUrl, type DetectResult, type Detection, type Risk } from '../../lib/api'
import { latinName, parseAdvice, riskKey, sortByConf, timeOf } from '../../lib/format'
import { setChatContext } from '../../composables/chat'
import { toast, useStation } from '../../composables/station'
import DetectionStage from '../../components/DetectionStage.vue'
import Icon from '../../components/Icon.vue'
import RiskMeter from '../../components/RiskMeter.vue'
import SpecimenCard from '../../components/SpecimenCard.vue'

const { state } = useStation()
const mode = ref<'photo' | 'live'>('photo')
const secure = window.isSecureContext
const httpsUrl = computed(() => {
  const p = state.config?.https_port
  return p ? `https://${location.hostname}:${p}/m` : ''
})

/* ---------------- 拍照 ---------------- */
const fileInput = ref<HTMLInputElement | null>(null)
const photo = ref<{ url: string; w: number; h: number } | null>(null)
const result = ref<DetectResult | null>(null)
const busy = ref(false)
let shot = 0

async function onPick(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  input.value = ''
  if (!f) return
  if (photo.value) URL.revokeObjectURL(photo.value.url)
  const url = URL.createObjectURL(f)
  const size = await new Promise<{ w: number; h: number }>((res) => {
    const im = new Image()
    im.onload = () => res({ w: im.naturalWidth, h: im.naturalHeight })
    im.onerror = () => res({ w: 0, h: 0 })
    im.src = url
  })
  photo.value = { url, ...size }
  result.value = null
  busy.value = true
  try {
    const r = await api.detect(f, undefined, 'phone.jpg')
    if (!r.success) throw new Error(r.error || '识别失败')
    result.value = r
    shot += 1
    if (r.detections.length) setChatContext(r.detections, r.risk)
  } catch (err) {
    toast(`识别失败：${(err as Error).message}`, 'bad')
  } finally {
    busy.value = false
  }
}
const dets = computed(() => sortByConf(result.value?.detections || []))
const measures = computed(() => {
  const f = parseAdvice(result.value?.advice || '').find((b) => b.kind === 'field' && b.label === '防治')
  return f?.text || ''
})

/* ---------------- 实时 ---------------- */
const video = ref<HTMLVideoElement | null>(null)
const live = ref(false)
const liveDets = ref<Detection[]>([])
const liveRisk = ref<Risk | null>(null)
const liveInfo = ref('未开始')
const sent = ref({ w: 0, h: 0 })
let stream: MediaStream | null = null
let ws: WebSocket | null = null
let timer = 0
let waiting = false
let waitT = 0
let retryT = 0
let retryN = 0

const requesting = ref(false)

/** getUserMedia 的失败原因翻成现场能照着做的话 */
function cameraError(e: unknown) {
  const name = (e as { name?: string })?.name || ''
  if (name === 'NotAllowedError' || name === 'SecurityError') return '没有拿到相机权限：点地址栏左侧的锁/设置图标，允许本页使用相机后重试'
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return '没找到可用的摄像头'
  if (name === 'NotReadableError' || name === 'AbortError') return '摄像头被别的应用占用（关掉相机、视频通话等应用后重试）'
  if (name === 'Timeout') return '一直没有等到相机权限确认：看看浏览器是否弹出了权限提示，或者换用拍照识别'
  return `打不开摄像头：${(e as Error)?.message || name || '未知原因'}`
}

async function startLive() {
  if (!secure) { toast('实时识别要用 https 地址打开本页', 'bad'); return }
  if (live.value || requesting.value) return
  if (!navigator.mediaDevices?.getUserMedia) { toast('这个浏览器不支持网页调用摄像头，请换用系统浏览器或拍照识别', 'bad'); return }
  requesting.value = true
  liveInfo.value = '正在请求摄像头权限…'
  try {
    // 权限提示被忽略/被拦截时 getUserMedia 可能一直不返回：10 秒没结果就给出提示，不让页面干等
    stream = await Promise.race([
      navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      }),
      new Promise<never>((_, rej) => window.setTimeout(() => rej(Object.assign(new Error('timeout'), { name: 'Timeout' })), 10000)),
    ])
  } catch (e) {
    liveInfo.value = '未开始'
    toast(cameraError(e), 'bad')
    return
  } finally {
    requesting.value = false
  }
  live.value = true
  if (video.value) video.value.srcObject = stream
  connectWs()
}

/** 建 /ws/phone 连接；断线自动重连（相机流不重开，避免手机端反复弹权限） */
function connectWs() {
  const sock = new WebSocket(wsUrl('/ws/phone'))
  sock.binaryType = 'arraybuffer'
  ws = sock
  sock.onopen = () => {
    retryN = 0
    liveInfo.value = '识别中'
    window.clearInterval(timer)
    timer = window.setInterval(capture, state.config?.frame_interval_ms || 400)
  }
  sock.onmessage = (ev) => {
    waiting = false
    window.clearTimeout(waitT)
    let d: { error?: string; warning?: string; pong?: boolean; detections?: Detection[]; risk?: Risk; elapsed?: number }
    try { d = JSON.parse(ev.data) } catch { return }
    if (d.error) { liveInfo.value = `服务器错误：${d.error}`; return }
    if (d.warning || d.pong) return
    liveDets.value = sortByConf(d.detections || [])
    liveRisk.value = d.risk || null
    liveInfo.value = `${d.risk?.level || '--'} · ${liveDets.value.length} 个目标 · ${d.elapsed ?? '--'} s`
    if (liveDets.value.length) setChatContext(liveDets.value, d.risk!)
  }
  sock.onclose = () => {
    window.clearInterval(timer)
    if (!live.value) return
    if (retryN >= 5) { stopLive(); toast('实时连接断开，已停止识别', 'bad'); return }
    retryN += 1
    liveInfo.value = `连接断开，2 秒后重连（第 ${retryN} 次）`
    retryT = window.setTimeout(connectWs, 2000)
  }
  sock.onerror = () => { try { sock.close() } catch { /* 忽略 */ } }
}
function capture() {
  const v = video.value
  if (!ws || ws.readyState !== 1 || waiting || !v || !v.videoWidth) return
  const max = state.config?.upload_max_side || 960
  const k = Math.min(1, max / Math.max(v.videoWidth, v.videoHeight))
  const w = Math.round(v.videoWidth * k)
  const h = Math.round(v.videoHeight * k)
  const cv = document.createElement('canvas')
  cv.width = w
  cv.height = h
  cv.getContext('2d')!.drawImage(v, 0, 0, w, h)
  cv.toBlob((blob) => {
    if (!blob || !ws || ws.readyState !== 1) return
    waiting = true
    sent.value = { w, h }
    ws.send(blob)
    waitT = window.setTimeout(() => { waiting = false }, 1500)
  }, 'image/jpeg', 0.7)
}
function stopLive() {
  live.value = false
  window.clearInterval(timer)
  window.clearTimeout(retryT)
  retryN = 0
  const s = ws
  ws = null
  if (s) { try { s.close() } catch { /* 已关闭 */ } }
  stream?.getTracks().forEach((t) => t.stop())
  stream = null
  if (video.value) video.value.srcObject = null
  liveDets.value = []
  liveInfo.value = '未开始'
  waiting = false
}
const boxes = computed(() => {
  const { w, h } = sent.value
  if (!w || !h) return []
  return liveDets.value.map((d) => ({
    label: `${d.label_en || latinName(d.class_name)} ${d.confidence.toFixed(2)}`,
    style: {
      left: `${(d.bbox[0] / w) * 100}%`, top: `${(d.bbox[1] / h) * 100}%`,
      width: `${((d.bbox[2] - d.bbox[0]) / w) * 100}%`, height: `${((d.bbox[3] - d.bbox[1]) / h) * 100}%`,
    },
  }))
})
function setMode(m: 'photo' | 'live') {
  if (m === 'photo') stopLive()
  mode.value = m
}
function onHidden() { if (document.hidden && live.value) stopLive() }
document.addEventListener('visibilitychange', onHidden)
onBeforeUnmount(() => {
  stopLive()
  document.removeEventListener('visibilitychange', onHidden)
  if (photo.value) URL.revokeObjectURL(photo.value.url)
})

const shownRisk = computed(() => (mode.value === 'live' ? liveRisk.value : result.value?.risk) || null)
</script>

<template>
  <div class="m-detect">
    <div class="seg" role="tablist">
      <button type="button" role="tab" :aria-selected="mode === 'photo'" :class="{ on: mode === 'photo' }" @click="setMode('photo')">拍照识别</button>
      <button type="button" role="tab" :aria-selected="mode === 'live'" :class="{ on: mode === 'live' }" @click="setMode('live')">实时识别</button>
    </div>

    <template v-if="mode === 'photo'">
      <div v-if="photo" class="shot">
        <DetectionStage :src="photo.url" :width="photo.w" :height="photo.h" :detections="dets" variant="dark" max-height="52vh" :busy="busy" />
      </div>
      <button v-else type="button" class="hero" @click="fileInput?.click()">
        <Icon name="camera" :size="34" :stroke="1.5" />
        <span class="h1">拍照 / 选择图片</span>
        <span class="h2">照片上传到服务器识别，返回害虫种类、风险和防治建议</span>
      </button>
      <input ref="fileInput" type="file" accept="image/*" capture="environment" hidden @change="onPick">
    </template>

    <template v-else>
      <div v-if="!secure" class="notice">
        <div>浏览器只允许 https 页面使用摄像头。</div>
        <div class="sub">请用 <a v-if="httpsUrl" :href="httpsUrl">{{ httpsUrl }}</a><span v-else>https 地址</span> 打开本页；第一次提示证书不受信任时选"继续访问"。拍照识别不受影响。</div>
      </div>
      <div class="live-view" :style="{ aspectRatio: sent.w ? `${sent.w} / ${sent.h}` : '4 / 3' }">
        <video ref="video" playsinline muted autoplay />
        <div v-for="(b, i) in boxes" :key="i" class="lbox" :style="b.style"><span>{{ b.label }}</span></div>
        <span class="linfo mono">{{ liveInfo }}</span>
      </div>
      <div class="row">
        <button v-if="!live" type="button" class="btn btn-primary btn-lg grow" :disabled="!secure || requesting" @click="startLive"><Icon name="play" :size="16" />{{ requesting ? '正在请求摄像头权限…' : '开始实时识别' }}</button>
        <button v-else type="button" class="btn btn-lg grow" @click="stopLive"><Icon name="stop" :size="16" />停止</button>
      </div>
      <p class="hint">约每 {{ ((state.config?.frame_interval_ms || 400) / 1000).toFixed(1) }} 秒上传一帧；高、中风险会自动联动喷淋和语音。</p>
    </template>

    <div v-if="shownRisk" class="risk-row panel">
      <RiskMeter :level="shownRisk.level" compact />
      <div class="rr">
        <div class="rl" :class="`risk-${riskKey(shownRisk.level)}`">{{ shownRisk.level }}</div>
        <div class="rd">{{ shownRisk.detail }}</div>
      </div>
      <span v-if="mode === 'photo' && result" class="mono rt">{{ result.inference_time.toFixed(2) }} s</span>
    </div>

    <template v-if="mode === 'photo'">
      <SpecimenCard v-for="(d, i) in dets.slice(0, 3)" :key="i" :det="d" compact :no="`${String(shot).padStart(4, '0')}${dets.length > 1 ? `-${i + 1}` : ''}`" origin="手机" :time="timeOf(result?.timestamp)">
        <div v-if="i === 0 && measures" class="measure">{{ measures }}</div>
      </SpecimenCard>
      <p v-if="result && !dets.length" class="empty">没有检出害虫。换个角度、靠近一点再拍。</p>
      <button v-if="photo" type="button" class="btn btn-primary btn-lg again" :disabled="busy" @click="fileInput?.click()">
        <Icon name="camera" :size="20" />{{ busy ? '识别中…' : '再拍一张' }}
      </button>
    </template>
  </div>
</template>

<style scoped>
.m-detect { display: flex; flex-direction: column; gap: 12px; }
.seg { display: grid; grid-template-columns: 1fr 1fr; padding: 3px; background: var(--inset); border-radius: var(--r-lg); }
.seg button { min-height: 40px; border: 0; border-radius: var(--r); background: transparent; color: var(--ink-2); cursor: pointer; }
.seg button.on { background: var(--panel); color: var(--ink); font-weight: 700; box-shadow: 0 1px 2px rgba(22, 32, 26, 0.14); }
.hero {
  display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 44px 20px;
  border: 1.5px dashed var(--line-strong); border-radius: var(--r-lg); background: var(--panel);
  color: var(--brand); cursor: pointer; text-align: center;
}
.hero .h1 { font-family: var(--font-serif); font-size: 18px; font-weight: 700; color: var(--ink); }
.hero .h2 { font-size: 13px; color: var(--ink-3); }
.shot { border-radius: var(--r-lg); overflow: hidden; }
.live-view { position: relative; background: var(--stage); border-radius: var(--r-lg); overflow: hidden; max-height: 56vh; margin: 0 auto; width: 100%; }
.live-view video { display: block; width: 100%; height: 100%; object-fit: contain; }
.lbox { position: absolute; border: 2px solid var(--signal); }
.lbox span { position: absolute; left: -2px; bottom: 100%; padding: 0 6px; background: var(--signal); color: #16201A; font-size: 12px; font-weight: 700; white-space: nowrap; }
.linfo { position: absolute; left: 10px; bottom: 10px; padding: 3px 8px; border-radius: var(--r-sm); background: var(--overlay); color: var(--stage-ink); font-size: 12px; }
.row { display: flex; gap: 8px; }
.grow { flex: 1; }
.hint { font-size: 12px; color: var(--ink-3); }
.risk-row { display: flex; align-items: center; gap: 12px; padding: 12px 14px; }
.rr { min-width: 0; }
.rl { font-family: var(--font-serif); font-size: 19px; font-weight: 700; line-height: 1.25; }
.rd { font-size: 12px; color: var(--ink-2); }
.rt { margin-left: auto; font-size: 12px; color: var(--ink-2); white-space: nowrap; }
.measure { margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--line-strong); font-size: 13px; line-height: 1.7; color: var(--body-ink); }
.again { min-height: 52px; font-size: 16px; }
</style>
