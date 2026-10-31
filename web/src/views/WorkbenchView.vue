<script setup lang="ts">
// 检测台：一条样本 = 一次采集（拍照 / 图片 / 文件夹 / 视频抓帧 / 服务器样本）。
//
// 两种模式：
//   * 评分作业（/eval 或 ?mode=eval）= GUI 的现场流程：标本摆好 → 摄像头实时画面里拍一张
//     → 后端识别 → 带框快照飞入样本栏 → 满 20 张停表 → 去评测（/eval/review）判对错 → 导出 CSV。
//     计时从第一张快照落位开始；未检出的不进样本栏；多于 20 张的不计数。
//   * 自由检测（/）= 上传/拖拽/粘贴/文件夹/视频，随便识别，不判分。
//
// 与 GUI 同一套规则（main.py _record_detection）：
//   · 只有"采集"（拍照/图片/文件夹/显式抓帧）算样本；实时画面与视频连续帧不算；
//   · **没检出目标不写行**（评分作业不进样本栏；自由检测保留缩略图并标记"不计分"）；
//   · 一次采集 = 一条，不做同目标合并；满 20 自动停表。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, type DetectResult, type LanInfo } from '../lib/api'
import { areaRatio, clock, groupLabel, riskKey, sortByConf, timeOf } from '../lib/format'
import { flyImage } from '../lib/fly'
import { renderBoxed } from '../lib/boxedImage'
import { setChatContext } from '../composables/chat'
import { toast, useStation } from '../composables/station'
import {
  b64ToObjectUrl, counts, currentId, doneCount, elapsedSec, enterMode, exportReport, finish, nextSeq, peekSeq,
  resetSession, reviewId, rubric, running, samples, scheduleScore, score, session, startTimer, started,
  type Sample, type SampleSource,
} from '../composables/evalSession'
import AdviceBody from '../components/AdviceBody.vue'
import CameraCapture from '../components/CameraCapture.vue'
import DetectionStage from '../components/DetectionStage.vue'
import EvalPanel from '../components/EvalPanel.vue'
import HoldButton from '../components/HoldButton.vue'
import Icon from '../components/Icon.vue'
import RiskMeter from '../components/RiskMeter.vue'
import SampleStrip from '../components/SampleStrip.vue'
import SpecimenCard from '../components/SpecimenCard.vue'

type InputMode = 'camera' | 'image' | 'folder' | 'video' | 'phone'

const INPUTS: { id: InputMode; label: string; icon: string }[] = [
  { id: 'camera', label: '摄像头', icon: 'camera' },
  { id: 'image', label: '图片', icon: 'image' },
  { id: 'folder', label: '文件夹', icon: 'folder' },
  { id: 'video', label: '视频', icon: 'live' },
  { id: 'phone', label: '手机', icon: 'qr' },
]

const route = useRoute()
const router = useRouter()
const { state, zoneOf, refreshHistory } = useStation()
const lan = ref<LanInfo | null>(null)

/* ==================== 样本与计时：状态在 composables/evalSession（与评测子页共用） ==================== */
const activeDet = ref<number | null>(null)
const busy = ref(false)
const pending: { s: Sample; blob: Blob }[] = []
const elapsed = elapsedSec

/* ==================== 模式 / 输入方式 ==================== */
// 跳去别的页面时路由先变、组件后卸载：这段间隙要保持原模式，否则评分样本会被当成自由检测清掉
let lastMode: 'eval' | 'free' = 'free'
const mode = computed<'eval' | 'free'>(() => {
  if (route.path !== '/' && route.path !== '/eval') return lastMode
  lastMode = (route.meta.mode === 'eval' || route.query.mode === 'eval') ? 'eval' : 'free'
  return lastMode
})
const input = ref<InputMode>('image')

const reviewSample = computed(() => samples.value.find((s) => s.id === reviewId.value) || null)
const current = computed(() => samples.value.find((s) => s.id === currentId.value) || null)
const stageSample = computed(() => {
  if (input.value === 'camera' || input.value === 'video') return reviewSample.value || null
  return current.value
})

function switchMode(next: 'eval' | 'free') {
  if (next === mode.value) return
  if (samples.value.length && !window.confirm('切换模式会清空本轮样本，继续？')) return
  pending.length = 0
  activeDet.value = null
  resetSession()
  void router.replace(next === 'eval' ? '/eval' : '/')
}
watch(mode, (m) => {
  enterMode(m)
  input.value = m === 'eval' ? 'camera' : 'image'
  reviewId.value = null
}, { immediate: true })
watch(input, () => { reviewId.value = null })

const conf = ref(0.4)
// 评分作业默认优先出框（EVAL.conf_default），滑块随时可调
watch([() => state.config, mode], ([c, m]) => {
  const v = m === 'eval' ? (c?.eval_conf_default ?? c?.conf_default) : c?.conf_default
  if (v) conf.value = v
}, { immediate: true })
// 滑块同时控制实时框：看到的和拍到的用同一门槛。喷淋/语音仍按配置门槛，不受影响。
let liveConfTimer = 0
watch([conf, input], ([c, i]) => {
  if (i !== 'camera') return
  window.clearTimeout(liveConfTimer)
  liveConfTimer = window.setTimeout(() => { void api.evalLiveConf(c).catch(() => { /* 下次拖动再试 */ }) }, 150)
}, { immediate: true })
const target = computed(() => state.config?.eval_target || 20)
const limit = computed(() => state.config?.eval_time_limit || 180)
const full = computed(() => mode.value === 'eval' && doneCount.value >= target.value)
const stripItems = computed(() => [...counts.value].reverse())
const flyingId = ref<number | null>(null)
const stripRef = ref<InstanceType<typeof SampleStrip> | null>(null)

/* ==================== 采集 → 识别队列 ==================== */
function readSize(url: string) {
  return new Promise<{ w: number; h: number }>((resolve) => {
    const img = new Image()
    img.onload = () => resolve({ w: img.naturalWidth, h: img.naturalHeight })
    img.onerror = () => resolve({ w: 0, h: 0 })
    img.src = url
  })
}

async function capture(blob: Blob, name: string, source: SampleSource, wh?: { w: number; h: number }) {
  if (mode.value === 'eval') {
    await evalCapture(() => api.detect(blob, conf.value, name), name, source, blob)
    return
  }
  if (!session.start) { session.start = performance.now(); session.now = session.start }
  const url = URL.createObjectURL(blob)
  const size = wh || await readSize(url)
  const id = nextSeq()
  const s: Sample = {
    id, no: String(id).padStart(2, '0'), name,
    url, w: size.w, h: size.h, status: 'queued',
    verdict: null, counts: false, elapsed: null, source,
    tAt: performance.now(),
  }
  samples.value.push(s)
  if (currentId.value == null) currentId.value = s.id
  pending.push({ s, blob })
  void pump()
}

/**
 * 评分作业：后端推理成功才入栏（未检出不显示、不占序号），之后带框图从画面飞到样本栏最左。
 * 动画只在真实结果回来之后播放；计时从第一张落位开始，后面的照常继续拍，不用等动画。
 */
async function evalCapture(run: () => Promise<DetectResult>, name: string, source: SampleSource, blob?: Blob) {
  if (session.stop || full.value) {
    toast(full.value ? `已满 ${target.value} 张，点「去评测」继续` : '本轮已结束，点「新一轮」重新开始', 'warn')
    return
  }
  busy.value = true
  try {
    const r = await run()
    if (!r.success) throw new Error(r.error || '识别失败')
    if (!r.detections.length) {
      toast('这张没有检出目标，不计入样本。调整摆放或调低置信度后再拍', 'warn')
      return
    }
    if (session.stop || full.value) return
    const raw = r.raw_image ? b64ToObjectUrl(r.raw_image) : URL.createObjectURL(blob as Blob)
    const size = r.image_size ? { w: r.image_size[0], h: r.image_size[1] } : await readSize(raw)
    // 框和类别来自后端；缩略图、动画、快照按实时画面的样式画，视觉一致
    const boxed = await renderBoxed(raw, r.detections)
    if (session.stop || full.value) return
    const id = nextSeq()
    const first = !session.start
    const s: Sample = {
      id, no: String(doneCount.value + 1).padStart(2, '0'), name,
      url: raw, boxedUrl: boxed || b64ToObjectUrl(r.result_image), w: size.w, h: size.h, status: 'done', result: r,
      verdict: null, counts: true, source, tAt: performance.now(),
      elapsed: first ? 0 : Math.max(0, (performance.now() - session.start) / 1000),
    }
    samples.value.push(s)
    currentId.value = s.id
    // 不在这里调大模型生成建议：逐张调用会和推理抢 CPU，拖慢 180 秒内的拍摄（结果已带知识库版）
    void saveSnapshot(s)
    setChatContext(r.detections, r.risk)
    if (full.value && !first) finish()
    void flyToStrip(s).then(() => {
      if (first && samples.value.includes(s)) {
        startTimer()
        if (full.value) finish()
      }
    })
  } catch (e) {
    toast(`识别失败：${(e as Error).message}`, 'bad')
  } finally {
    busy.value = false
  }
}

async function flyToStrip(s: Sample) {
  const from = (document.querySelector('[data-fly-source] .frame') || document.querySelector('.stage-panel'))?.getBoundingClientRect()
  flyingId.value = s.id
  await nextTick()
  stripRef.value?.toStart()
  await flyImage(s.boxedUrl, from, stripRef.value?.thumbRect(s.id))
  if (flyingId.value === s.id) flyingId.value = null
}

function goReview() {
  if (!doneCount.value) return
  if (doneCount.value < target.value
    && !window.confirm(`只拍了 ${doneCount.value} 张（目标 ${target.value} 张），结束拍照去评测？`)) return
  if (running.value) finish()
  void router.push('/eval/review')
}

let pumping = false
async function pump() {
  if (pumping) return
  pumping = true
  busy.value = true
  try {
    for (;;) {
      const item = pending.shift()
      if (!item) break
      const { s } = item
      s.status = 'running'
      try {
        const r = await api.detect(item.blob, conf.value, s.name)
        if (!r.success) throw new Error(r.error || '识别失败')
        s.result = r
        s.status = 'done'
        s.counts = r.detections.length > 0
        // 计时：拍照按"按下那一刻"算（GUI 同口径）；图片/文件夹按完成时刻
        s.elapsed = Math.max(0, (((s.tAt ?? performance.now()) - session.start) / 1000))
        if (s.counts) {
          void saveSnapshot(s)
          if (s.id === currentId.value && !reviewId.value) void loadAdvice(s)
          if (r.detections.length) setChatContext(r.detections, r.risk)
        } else {
          toast(`样本 ${s.no} 没有检出目标，不计入评分（可调低阈值后重拍）`, 'warn')
        }
        if (mode.value === 'eval' && doneCount.value >= target.value) finish()
      } catch (e) {
        s.status = 'error'
        s.error = (e as Error).message
        toast(`样本 ${s.no} 识别失败：${s.error}`, 'bad')
      }
    }
  } finally {
    pumping = false
    busy.value = false
  }
}

async function loadAdvice(s: Sample) {
  if (!s.result?.detections.length) return
  try {
    const a = await api.advice(s.result.detections, s.result.risk)
    if (a.advice && a.source === 'llm') {
      s.result.advice = a.advice
      s.result.advice_source = a.source
    }
  } catch { /* 大模型不可用：保留知识库版 */ }
}

/** 拍照即存：带框截图落 output/snapshots/（与 GUI 同目录），失败不影响评分 */
async function saveSnapshot(s: Sample) {
  const img = s.result?.result_image
  if (!img) return
  try {
    const r = await api.saveSnapshot(s.boxedUrl?.startsWith('data:') ? s.boxedUrl : `data:image/jpeg;base64,${img}`)
    if (r.success && r.url) s.snapshotUrl = r.url
  } catch { /* 忽略 */ }
}

async function redetect() {
  const s = stageSample.value
  if (!s) return
  try {
    const blob = await (await fetch(s.url)).blob()
    s.status = 'running'
    const r = await api.detect(blob, conf.value, s.name)
    if (mode.value === 'eval' && !r.detections.length) {
      // 已经入栏的样本不能因为重测变成"未检出"：保留原结果，让它继续能判分
      s.status = 'done'
      toast('这个阈值下没有检出目标，保留原来的结果', 'warn')
      return
    }
    s.result = r
    s.status = 'done'
    s.counts = r.detections.length > 0
    s.elapsed = Math.max(0, (((s.tAt ?? performance.now()) - session.start) / 1000))
    if (s.boxedUrl) {
      if (!s.boxedUrl.startsWith('data:')) URL.revokeObjectURL(s.boxedUrl)
      s.boxedUrl = (await renderBoxed(s.url, r.detections)) || b64ToObjectUrl(r.result_image)
    }
    if (s.counts) void saveSnapshot(s)
    scheduleScore()
    toast(s.counts ? '重新识别完成' : '这个阈值下没有检出目标', s.counts ? 'ok' : 'warn')
  } catch (e) {
    s.status = 'error'
    s.error = (e as Error).message
    toast(`重新识别失败：${s.error}`, 'bad')
  }
}

function newRound() {
  pending.length = 0
  activeDet.value = null
  flyingId.value = null
  resetSession()
}

function setVerdict(v: '正确' | '错误') {
  const s = stageSample.value
  if (!s || !s.counts) { toast('先选一条有检出的样本，再判对错', 'warn'); return }
  s.verdict = s.verdict === v ? null : v
  scheduleScore()
}

/* ==================== 图片 / 文件夹 / 粘贴 / 拖拽 ==================== */
const fileInput = ref<HTMLInputElement | null>(null)
const dirInput = ref<HTMLInputElement | null>(null)
const dragging = ref(false)

async function addFiles(files: FileList | File[] | null | undefined, source: 'image' | 'folder') {
  const list = Array.from(files || []).filter((f) => f.type.startsWith('image/'))
  if (!list.length) { toast('请选择图片文件（JPG / PNG）', 'bad'); return }
  for (const f of list) await capture(f, f.name || `样本 ${peekSeq() + 1}`, source)
}
function onPick(e: Event) {
  const el = e.target as HTMLInputElement
  void addFiles(el.files, 'image')
  el.value = ''
}
function onPickDir(e: Event) {
  const el = e.target as HTMLInputElement
  void addFiles(el.files, 'folder')
  el.value = ''
}
function onDrop(e: DragEvent) {
  dragging.value = false
  if (input.value !== 'image' && input.value !== 'folder') return
  void addFiles(e.dataTransfer?.files, 'image')
}
function onPaste(e: ClipboardEvent) {
  if (input.value !== 'image' && input.value !== 'folder') return
  const files = Array.from(e.clipboardData?.items || [])
    .filter((i) => i.type.startsWith('image/'))
    .map((i) => i.getAsFile())
    .filter((f): f is File => !!f)
  if (files.length) void addFiles(files, 'image')
}

/* ==================== 摄像头 ==================== */
function onCameraCapture(p: { blob: Blob; w: number; h: number }) {
  const n = doneCount.value + 1
  if (mode.value === 'eval') {
    void evalCapture(() => api.evalCapture(conf.value), `拍照 ${String(n).padStart(2, '0')}`, 'camera')
    return
  }
  void capture(p.blob, `拍照 ${String(n).padStart(2, '0')}`, 'camera', { w: p.w, h: p.h })
}

/* ==================== 视频（连续识别不记样本，抓帧才记） ==================== */
const videoEl = ref<HTMLVideoElement | null>(null)
const videoFile = ref<HTMLInputElement | null>(null)
const videoUrl = ref('')
const videoName = ref('')
const videoMsg = ref('')
const videoLive = ref(false)
let videoTimer = 0

function onVideoPick(e: Event) {
  const el = e.target as HTMLInputElement
  const f = el.files?.[0]
  el.value = ''
  if (!f) return
  if (videoUrl.value) URL.revokeObjectURL(videoUrl.value)
  videoUrl.value = URL.createObjectURL(f)
  videoName.value = f.name
  videoMsg.value = ''
}

function grabVideoFrame(): Promise<Blob | null> {
  const v = videoEl.value
  if (!v || !v.videoWidth) return Promise.resolve(null)
  const cv = document.createElement('canvas')
  cv.width = v.videoWidth
  cv.height = v.videoHeight
  cv.getContext('2d')?.drawImage(v, 0, 0)
  return new Promise((resolve) => cv.toBlob((b) => resolve(b), 'image/jpeg', 0.9))
}

async function videoCapture(asSample: boolean) {
  const b = await grabVideoFrame()
  if (!b) { toast('视频还没出画面', 'warn'); return }
  const v = videoEl.value
  const name = `${videoName.value || '视频'}@${v ? v.currentTime.toFixed(1) : '?'}s`
  if (asSample) {
    await capture(b, name, 'video', { w: v?.videoWidth || 0, h: v?.videoHeight || 0 })
    return
  }
  // 连续识别：只回报这一帧的结果（不写样本），用来边看边找目标
  try {
    const r = await api.detect(b, conf.value, name)
    videoMsg.value = r.detections.length
      ? `这一帧：${r.detections.map((d) => `${d.chinese_name} ${(d.confidence * 100).toFixed(0)}%`).join('、')}`
      : '这一帧没有检出目标'
  } catch (e) {
    videoMsg.value = `识别失败：${(e as Error).message}`
  }
}

function toggleVideoLive() {
  videoLive.value = !videoLive.value
  window.clearInterval(videoTimer)
  if (videoLive.value) videoTimer = window.setInterval(() => void videoCapture(false), 1200)
}

/* ==================== 手机 ==================== */
const phoneUrl = computed(() => {
  const l = lan.value
  return l?.phone_urls_https?.[0] || l?.phone_urls?.[0] || ''
})
const qrSrc = computed(() => (phoneUrl.value
  ? `/qr?size=6&url=${encodeURIComponent(phoneUrl.value)}`
  : '/qr?size=6'))

/* ==================== 从服务器载入 20 张（无相机彩排兜底） ==================== */
async function loadServerSamples() {
  try {
    const r = await api.evalSamples()
    if (!r.count) { toast(`样本目录是空的：${r.dir}`, 'bad'); return }
    for (const { name } of r.samples) {
      if (full.value) break
      const blob = await (await fetch(api.evalSampleUrl(name))).blob()
      await capture(blob, name, 'server')
    }
    toast(`已载入 ${doneCount.value} 张服务器样本`, 'ok')
  } catch (e) {
    toast(`载入失败：${(e as Error).message}`, 'bad')
  }
}

/* ==================== 右侧结果 ==================== */
const dets = computed(() => sortByConf(stageSample.value?.result?.detections || []))
const top = computed(() => dets.value[activeDet.value ?? 0] || dets.value[0] || null)
const risk = computed(() => stageSample.value?.result?.risk || null)
const area = computed(() => (stageSample.value ? areaRatio(dets.value, stageSample.value.w, stageSample.value.h) : 0))
const plan = computed(() => {
  const m = state.config?.risk_map?.[risk.value?.level || '']
  return m && m.group && m.duration > 0 ? m : null
})
const adviceText = computed(() => stageSample.value?.result?.advice || '')
const adviceTag = computed(() => {
  const s = stageSample.value
  if (!s?.result?.detections.length) return null
  if (s.result.advice_source === 'llm') return { text: '本地大模型 · 依据知识库', note: '' }
  return { text: '知识库 · 离线', note: '本地大模型未启动时显示知识库原文' }
})

async function sprayPlan() {
  if (!plan.value) return
  try {
    const r = await api.spray(plan.value.group!, plan.value.duration, 'web')
    if (r.success) toast(`已下发：${groupLabel(plan.value.group, zoneOf.value)} 喷淋 ${r.duration} 秒`)
    else toast(r.reason || '节点没有应答，指令未送达', 'bad')
  } catch (e) {
    toast(`下发失败：${(e as Error).message}`, 'bad')
  }
  refreshHistory()
}

function viewSample(s: Sample) {
  if (input.value === 'camera' || input.value === 'video') reviewId.value = s.id
  else currentId.value = s.id
}

function onKey(e: KeyboardEvent) {
  if ((e.target as HTMLElement)?.tagName === 'INPUT') return
  const i = samples.value.findIndex((s) => s.id === (currentId.value ?? -1))
  if (e.key === 'ArrowRight' && i < samples.value.length - 1) currentId.value = samples.value[i + 1].id
  if (e.key === 'ArrowLeft' && i > 0) currentId.value = samples.value[i - 1].id
  if (mode.value === 'eval') return      // 评分作业只在评测子页判分
  if (e.key === 'y' || e.key === 'Y') setVerdict('正确')
  if (e.key === 'n' || e.key === 'N') setVerdict('错误')
}

onMounted(async () => {
  document.addEventListener('paste', onPaste)
  document.addEventListener('keydown', onKey)
  try { lan.value = await api.lan() } catch { /* 局域网信息读不到不影响检测 */ }
})
onBeforeUnmount(() => {
  document.removeEventListener('paste', onPaste)
  document.removeEventListener('keydown', onKey)
  window.clearInterval(videoTimer)
  window.clearTimeout(liveConfTimer)
  // 离开页面恢复默认门槛，别影响 /stream；自由检测的样本和以前一样离开就清空
  void api.evalLiveConf(null).catch(() => { /* 服务已停 */ })
  if (mode.value === 'free') resetSession()
  if (videoUrl.value) URL.revokeObjectURL(videoUrl.value)
})
</script>

<template>
  <main class="page">
    <section class="main-col">
      <!-- 模式 + 输入方式 -->
      <div class="panel modebar">
        <div class="seg" role="tablist" aria-label="业务模式">
          <button type="button" class="seg-btn" :class="{ on: mode === 'eval' }" @click="switchMode('eval')">评分作业</button>
          <button type="button" class="seg-btn" :class="{ on: mode === 'free' }" @click="switchMode('free')">自由检测</button>
        </div>
        <div class="seg" role="tablist" aria-label="输入方式">
          <button
            v-for="m in INPUTS"
            :key="m.id"
            type="button"
            class="seg-btn"
            :class="{ on: input === m.id }"
            :aria-selected="input === m.id"
            @click="input = m.id"
          >
            <Icon :name="m.icon" :size="15" />{{ m.label }}
          </button>
        </div>
        <label class="conf" for="conf">置信度
          <input id="conf" v-model.number="conf" type="range" min="0.1" max="0.9" step="0.05">
          <span class="mono">{{ conf.toFixed(2) }}</span>
        </label>
      </div>

      <!-- 采集 / 识别舞台 -->
      <div
        class="panel stage-panel"
        :class="{ dragging }"
        @dragover.prevent="dragging = true"
        @dragleave.self="dragging = false"
        @drop.prevent="onDrop"
      >
        <!-- 回看时只隐藏不卸载：卸载会断流、释放相机，返回时要等冷启动约 7 秒 -->
        <CameraCapture
          v-if="input === 'camera'"
          v-show="!reviewSample"
          :busy="busy"
          :count="doneCount"
          :target="target"
          :full="full"
          @capture="onCameraCapture"
        >
          <template v-if="mode === 'eval'" #actions>
            <button type="button" class="btn" :class="{ 'btn-primary': full }" :disabled="!doneCount" @click="goReview">
              <Icon name="check" :size="16" />去评测
            </button>
          </template>
        </CameraCapture>

        <div v-if="input === 'video' && !reviewSample" class="video-box">
          <div class="row">
            <button type="button" class="btn" @click="videoFile?.click()"><Icon name="folder" :size="16" />选择视频</button>
            <input ref="videoFile" type="file" accept="video/*" hidden @change="onVideoPick">
            <span class="mono hint">{{ videoName || '选一个本地视频（MP4 / MOV）' }}</span>
            <button type="button" class="btn btn-sm" :disabled="!videoUrl" @click="toggleVideoLive">
              <Icon name="play" :size="14" />{{ videoLive ? '停止连续识别' : '连续识别（不记样本）' }}
            </button>
            <button type="button" class="btn btn-sm btn-primary" :disabled="!videoUrl" @click="videoCapture(true)">
              <Icon name="camera" :size="14" />抓这一帧记样本
            </button>
          </div>
          <video v-if="videoUrl" ref="videoEl" :src="videoUrl" controls playsinline class="video" />
          <p v-else class="empty">边播边识别；连续识别只回报结果、不写样本，点「抓这一帧」才记一条。</p>
          <p v-if="videoMsg" class="mono hint">{{ videoMsg }}</p>
        </div>

        <div v-else-if="input === 'phone'" class="phone-box">
          <img class="qr" :src="qrSrc" alt="手机端二维码" width="168" height="168">
          <div class="phone-info">
            <p class="phone-t">手机连同一个 WiFi / 热点，扫码打开 <span class="mono">/m</span></p>
            <p class="mono hint">{{ phoneUrl || '读取局域网地址中…' }}</p>
            <a class="btn btn-sm" :href="phoneUrl || '/m'" target="_blank" rel="noopener">
              <Icon name="external" :size="14" />打开手机页
            </a>
            <p class="hint">手机端拍的每一张同样进评分（手机页有自己的样本流程）。</p>
          </div>
        </div>

        <DetectionStage
          v-else-if="input !== 'camera' || reviewSample"
          v-model:active="activeDet"
          :src="stageSample?.url"
          :width="stageSample?.w"
          :height="stageSample?.h"
          :detections="dets"
          :busy="stageSample?.status === 'running' || stageSample?.status === 'queued'"
          max-height="min(560px, 62vh)"
        >
          <template #empty>
            <button type="button" class="drop" @click="input === 'folder' ? dirInput?.click() : fileInput?.click()">
              <Icon :name="input === 'folder' ? 'folder' : 'image'" :size="40" :stroke="1.4" />
              <span class="drop-t1">{{ input === 'folder' ? '选择一个文件夹' : '把样本图片拖到这里' }}</span>
              <span class="drop-t2">或点击选择 · 也可以直接 Ctrl+V 粘贴截图<br>一次可以选多张，按顺序识别并计时</span>
            </button>
          </template>
        </DetectionStage>

        <input ref="fileInput" type="file" accept="image/*" multiple hidden @change="onPick">
        <input ref="dirInput" type="file" webkitdirectory directory multiple hidden @change="onPickDir">

        <div v-if="reviewSample" class="review-bar">
          <span class="mono">回看：样本 {{ reviewSample.no }} · {{ reviewSample.name }}</span>
          <button type="button" class="btn btn-sm" @click="reviewId = null"><Icon name="refresh" :size="14" />返回实时</button>
          <button v-if="mode === 'eval'" type="button" class="btn btn-sm" :class="{ 'btn-primary': full }" :disabled="!doneCount" @click="goReview">
            <Icon name="check" :size="14" />去评测
          </button>
        </div>

        <!-- 评分作业的判分只在评测子页（/eval/review）做，这里只回看 -->
        <div v-if="stageSample?.counts && mode !== 'eval'" class="judge-row">
          <span class="judge-t">判分 {{ stageSample.no }}</span>
          <button type="button" class="btn btn-sm" :class="{ 'btn-primary': stageSample.verdict === '正确' }" @click="setVerdict('正确')">
            <Icon name="check" :size="15" />正确
          </button>
          <button type="button" class="btn btn-sm" :class="{ 'btn-primary': stageSample.verdict === '错误' }" @click="setVerdict('错误')">
            <Icon name="close" :size="15" />错误
          </button>
          <button type="button" class="btn btn-sm" :disabled="busy" @click="redetect">
            <Icon name="refresh" :size="14" />重新识别
          </button>
          <span class="hint">再点一次取消 · 快捷键 Y / N</span>
        </div>

        <div v-if="stageSample" class="foot mono">
          <template v-if="stageSample.result">
            <span>推理 {{ stageSample.result.inference_time.toFixed(2) }} s</span>
            <span v-if="stageSample.elapsed != null">第 {{ stageSample.elapsed.toFixed(1) }} s</span>
            <span>{{ stageSample.result.detections.length ? `${stageSample.result.detections.length} 个目标` : '未检出害虫' }}</span>
            <a v-if="stageSample.snapshotUrl" class="push link" :href="stageSample.snapshotUrl" target="_blank" rel="noopener">已存快照</a>
          </template>
          <span v-else-if="stageSample.status === 'error'" class="risk-high">识别失败：{{ stageSample.error }}</span>
          <span v-else>识别中…</span>
        </div>
      </div>

      <!-- 本轮样本 -->
      <div class="panel panel-pad">
        <div class="session-head">
          <h2>本轮样本</h2>
          <span class="num count">{{ doneCount }} <span class="of">/ {{ target }}</span></span>
          <span class="mono timing">用时 {{ clock(elapsed) }} · 限时 {{ clock(limit) }}</span>
          <button v-if="mode === 'eval'" type="button" class="btn btn-sm" @click="loadServerSamples">从服务器载入</button>
          <button v-if="samples.length" type="button" class="btn btn-sm" @click="newRound">新一轮</button>
        </div>
        <div class="progress" :title="`已完成 ${doneCount} / ${target}`">
          <div class="fill" :style="{ width: `${Math.min(100, (doneCount / target) * 100)}%` }" />
          <div v-if="session.start" class="mark" :class="{ over: elapsed > limit }" :style="{ left: `${Math.min(100, (elapsed / limit) * 100)}%` }" />
        </div>
        <SampleStrip
          v-if="mode === 'eval' && stripItems.length"
          ref="stripRef"
          :items="stripItems"
          :active-id="reviewId"
          :hidden-id="flyingId"
          @select="viewSample"
        />
        <div v-else-if="mode === 'free' && samples.length" class="strip">
          <button
            v-for="s in samples"
            :key="s.id"
            type="button"
            class="thumb"
            :class="{ on: s.id === (reviewId ?? currentId) }"
            :aria-label="`样本 ${s.no}`"
            @click="viewSample(s)"
          >
            <span class="ti">
              <img :src="s.url" alt="">
              <span v-if="s.status === 'running' || s.status === 'queued'" class="tq">{{ s.status === 'running' ? '识别中' : '排队' }}</span>
              <span v-if="s.result" class="tr" :class="`bg-${riskKey(s.result.risk.level)}`" />
              <span v-if="s.verdict" class="tv-badge" :class="s.verdict === '正确' ? 'ok' : 'bad'">{{ s.verdict === '正确' ? '✓' : '✗' }}</span>
              <span v-if="s.status === 'done' && !s.counts" class="tq none">未检出·不计分</span>
            </span>
            <span class="tc">
              <span class="tn">{{ s.result ? (s.result.detections[0]?.chinese_name || '未检出') : s.status === 'error' ? '失败' : `样本 ${s.no}` }}</span>
              <span class="mono tv">{{ s.elapsed != null ? `${s.elapsed.toFixed(1)}s` : '' }}</span>
            </span>
          </button>
        </div>
        <p v-else class="empty">
          还没有样本。{{ mode === 'eval' ? '把标本摆到镜头前，看到框后点「拍照并记录」，第一张快照落位后开始计时，满 ' + target + ' 张停表。' : '拖图片进来或选择输入方式。' }}
          <template v-if="mode === 'free'">←/→ 切换样本，Y/N 判对错。</template>
        </p>
      </div>
    </section>

    <aside class="side-col">
      <EvalPanel
        v-if="mode === 'eval'"
        :score="score"
        :counted="doneCount"
        :elapsed="elapsed"
        :limit="limit"
        :target="target"
        :started="started"
        :running="running"
        :rubric="rubric"
        @finish="finish"
        @export="exportReport"
        @reset="newRound"
      />

      <div class="panel panel-pad">
        <div class="eyebrow">风险判定</div>
        <div class="risk-line">
          <span class="risk-lv" :class="risk ? `risk-${riskKey(risk.level)}` : 'muted'">{{ risk?.level || '等待样本' }}</span>
          <span class="risk-dt">{{ risk?.detail || '识别完成后按数量、置信度和覆盖面积判定' }}</span>
        </div>
        <RiskMeter :level="risk?.level || null" />
        <div v-if="risk" class="link-rows">
          <div class="lr">
            <span class="lk">联动预案</span>
            <span v-if="plan">{{ groupLabel(plan.group, zoneOf) }} 喷淋 {{ plan.duration }} 秒</span>
            <span v-else class="muted">该等级不喷淋</span>
          </div>
          <div class="lr">
            <span class="lk">说明</span>
            <span class="muted">检测台只识别不自动喷淋；实时监测和手机实时识别会按预案自动联动。</span>
          </div>
          <HoldButton v-if="plan" :disabled="!state.spray?.available" @fire="sprayPlan">按住 1 秒，按预案喷淋</HoldButton>
        </div>
      </div>

      <SpecimenCard
        v-if="top && stageSample"
        :det="top"
        :no="String(stageSample.id).padStart(4, '0')"
        :origin="mode === 'eval' ? '评分作业' : '检测台'"
        :time="timeOf(stageSample.result?.timestamp)"
        :area="dets.length === 1 ? area : null"
        :elapsed="stageSample.result?.inference_time ?? null"
      >
        <div v-if="dets.length > 1" class="others">
          <div class="eyebrow">本图共 {{ dets.length }} 个目标 · 总覆盖 {{ (area * 100).toFixed(1) }}%</div>
          <button
            v-for="(d, i) in dets"
            :key="i"
            type="button"
            class="other"
            :class="{ on: (activeDet ?? 0) === i }"
            @mouseenter="activeDet = i"
            @focus="activeDet = i"
            @click="activeDet = i"
          >
            <span>{{ d.chinese_name }}</span><span class="mono">{{ (d.confidence * 100).toFixed(1) }}%</span>
          </button>
        </div>
      </SpecimenCard>

      <div class="panel panel-pad">
        <div class="panel-head">
          <h2>防治建议</h2>
          <span v-if="adviceTag" class="tag">{{ adviceTag.text }}</span>
        </div>
        <AdviceBody v-if="adviceText && dets.length" :text="adviceText" />
        <p v-else class="empty">识别到害虫后，先给出知识库方案（离线、秒出），本地大模型就绪时再生成综合方案替换。</p>
        <p v-if="adviceText && dets.length" class="fine">药剂与剂量以当地植保部门发布为准。</p>
      </div>
    </aside>
  </main>
</template>

<style scoped>
.page {
  display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start;
  max-width: 1440px; margin: 0 auto; padding: 24px 32px 40px;
}
.main-col { flex: 999 1 640px; min-width: 0; display: flex; flex-direction: column; gap: 24px; }
.side-col { flex: 1 1 360px; min-width: 0; max-width: 100%; display: flex; flex-direction: column; gap: 24px; }
@media (min-width: 1100px) { .side-col { max-width: 420px; } }

/* 模式 / 输入方式 */
.modebar { display: flex; align-items: center; flex-wrap: wrap; gap: 12px 18px; padding: 12px 16px; }
.seg { display: inline-flex; border: 1px solid var(--line-strong); border-radius: var(--r); overflow: hidden; }
.seg-btn {
  display: inline-flex; align-items: center; gap: 6px; padding: 7px 12px; border: 0; background: var(--paper);
  color: var(--ink-2); font-size: 13px; cursor: pointer; border-right: 1px solid var(--line);
}
.seg-btn:last-child { border-right: 0; }
.seg-btn:hover { background: var(--inset); }
.seg-btn.on { background: var(--brand); color: var(--brand-ink); }
.conf { display: flex; align-items: center; gap: 10px; margin-left: auto; font-size: 13px; color: var(--ink-2); }
.conf input { width: 110px; accent-color: var(--brand); }

.stage-panel { overflow: hidden; padding: 16px 20px 0; }
.stage-panel.dragging { outline: 2px dashed var(--signal-ink); outline-offset: -6px; }
.stage-panel :deep(.stage) { margin: 0 auto; }
.drop {
  display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 40px 24px;
  border: 1.5px dashed var(--line-strong); border-radius: var(--r-lg); background: var(--panel);
  color: var(--ink-2); cursor: pointer; text-align: center; max-width: 440px;
}
.drop:hover { border-color: var(--brand); color: var(--brand); }
.drop-t1 { font-family: var(--font-serif); font-size: 17px; font-weight: 700; color: var(--ink); }
.drop-t2 { font-size: 13px; color: var(--ink-3); line-height: 1.7; }

.review-bar {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 12px; padding: 8px 12px;
  background: var(--inset); border-radius: var(--r); font-size: 13px;
}
.review-bar .btn:first-of-type { margin-left: auto; }
.judge-row {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 10px; padding: 10px 0 0;
  border-top: 1px solid var(--line);
}
.judge-t { font-size: 13px; color: var(--ink-2); }
.foot { display: flex; flex-wrap: wrap; gap: 6px 24px; padding: 12px 0; font-size: 12px; color: var(--ink-2); }
.push { margin-left: auto; }
.link { color: var(--brand); text-decoration: none; }
.link:hover { text-decoration: underline; }

/* 视频 / 手机 */
.video-box, .phone-box { display: flex; flex-direction: column; gap: 12px; }
.row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.video { width: 100%; max-height: min(520px, 56vh); background: #000; border-radius: var(--r); }
.phone-box { flex-direction: row; align-items: flex-start; gap: 20px; flex-wrap: wrap; }
.qr { width: 168px; height: 168px; padding: 8px; background: #fff; border: 1px solid var(--line); image-rendering: pixelated; flex: none; }
.phone-info { flex: 1 1 240px; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.phone-t { font-size: 14px; color: var(--ink); }

/* 本轮样本 */
.session-head { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px 16px; margin-bottom: 14px; }
.count { font-size: 22px; }
.of { font-size: 16px; color: var(--ink-3); }
.timing { margin-left: auto; font-size: 12px; color: var(--ink-2); }
.progress { position: relative; height: 4px; background: var(--inset); margin-bottom: 16px; }
.progress .fill { height: 100%; background: var(--brand); transition: width .3s; }
.progress .mark { position: absolute; top: -4px; width: 2px; height: 12px; background: var(--signal-ink); transform: translateX(-1px); }
.progress .mark.over { background: var(--risk-high); }
.strip { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 12px; }
.thumb { display: flex; flex-direction: column; gap: 6px; padding: 0; border: 0; background: none; cursor: pointer; text-align: left; min-width: 0; }
.ti {
  position: relative; display: block; aspect-ratio: 1 / 1; overflow: hidden; background: var(--inset);
  outline: 2px solid transparent; outline-offset: 2px; transition: outline-color .15s;
}
.thumb:hover .ti { outline-color: var(--line-strong); }
.thumb.on .ti { outline-color: var(--signal); }
.ti img { display: block; width: 100%; height: 100%; object-fit: cover; }
.tq {
  position: absolute; inset: 0; display: grid; place-items: center; font-size: 11px; padding: 4px; text-align: center;
  background: rgba(15, 20, 17, 0.55); color: #F2F4EF;
}
.tq.none { background: rgba(15, 20, 17, 0.35); }
.tq { font-size: 11px; }
.tv-badge {
  position: absolute; right: 4px; top: 4px; width: 18px; height: 18px; display: grid; place-items: center;
  border-radius: 50%; font-size: 11px; font-weight: 700; color: #fff;
}
.tv-badge.ok { background: var(--brand); }
.tv-badge.bad { background: var(--risk-high); }
.tr { position: absolute; left: 0; right: 0; bottom: 0; height: 4px; }
.bg-high { background: var(--risk-high); }
.bg-mid { background: var(--risk-mid); }
.bg-low { background: var(--risk-low); }
.bg-none { background: var(--risk-none); }
.tc { display: flex; justify-content: space-between; gap: 6px; font-size: 12px; min-width: 0; }
.tn { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tv { color: var(--ink-2); }

.risk-line { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 12px; margin: 6px 0 14px; }
.risk-lv { font-family: var(--font-serif); font-size: 30px; font-weight: 700; line-height: 1.2; }
.risk-dt { font-size: 13px; color: var(--ink-2); }
.link-rows { display: flex; flex-direction: column; gap: 8px; margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--line); font-size: 13px; }
.lr { display: flex; gap: 10px; }
.lk { flex: none; width: 56px; color: var(--ink-3); }
.link-rows :deep(.hold) { margin-top: 6px; }

.others { margin-top: 14px; padding-top: 12px; border-top: 1px dashed var(--line-strong); display: flex; flex-direction: column; gap: 2px; }
.others .eyebrow { margin-bottom: 4px; }
.other {
  display: flex; justify-content: space-between; gap: 10px; padding: 6px 8px; margin: 0 -8px;
  border: 0; background: none; cursor: pointer; text-align: left; border-radius: var(--r-sm); font-size: 13px;
}
.other:hover, .other.on { background: var(--inset); }
.fine { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--line); font-size: 12px; color: var(--ink-3); }

@media (max-width: 640px) {
  .page { padding: 16px; gap: 16px; }
  .conf { margin-left: 0; }
  .timing { margin-left: 0; width: 100%; }
}
</style>
