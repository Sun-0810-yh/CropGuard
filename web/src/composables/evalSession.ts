// 本轮样本的共享状态：拍照页（检测台）和评测子页（/eval/review）用同一份。
//
// 放在模块级而不是组件里：从拍照页跳到评测子页时组件会卸载，20 张样本和用时不能丢。
// 分数一律来自 /api/eval/score（与桌面 GUI 同一份纯函数），这里不自己算。
import { computed, reactive, ref, watch } from 'vue'
import { api, type DetectResult, type EvalRubric, type EvalScore } from '../lib/api'
import { clock, timeOf } from '../lib/format'
import { toast } from './station'

export type SampleSource = 'camera' | 'image' | 'folder' | 'video' | 'phone' | 'server'
export type Verdict = '正确' | '错误'

export interface Sample {
  id: number
  no: string
  name: string
  /** 原图：大屏叠框、重新识别都用它（带框图再推理会把框当成目标） */
  url: string
  /** 后端画好框的图：评分作业的缩略图、飞入动画、评测大屏用它 */
  boxedUrl?: string | null
  w: number
  h: number
  status: 'queued' | 'running' | 'done' | 'error'
  result?: DetectResult
  error?: string
  verdict: Verdict | null
  /** 有检出目标才算一条评分样本（GUI 规则：没检出目标不写行） */
  counts: boolean
  /** 距本轮计时起点的秒数 */
  elapsed: number | null
  source: SampleSource
  snapshotUrl?: string | null
  tAt?: number
}

export const samples = ref<Sample[]>([])
export const currentId = ref<number | null>(null)
export const reviewId = ref<number | null>(null)
export const counts = computed(() => samples.value.filter((s) => s.counts))
export const doneCount = computed(() => counts.value.length)

export const session = reactive({ start: 0, stop: 0, now: 0 })
export const started = computed(() => session.start > 0)
export const running = computed(() => started.value && !session.stop)
export const elapsedSec = computed(() => {
  if (!session.start) return 0
  const end = session.stop || session.now || session.start
  return Math.max(0, (end - session.start) / 1000)
})

export const score = ref<EvalScore | null>(null)
export const rubric = ref<Partial<EvalRubric> | null>(null)

let seq = 0
export const nextSeq = () => { seq += 1; return seq }
export const peekSeq = () => seq

export function b64ToObjectUrl(b64: string) {
  const bin = atob(b64)
  const buf = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i)
  return URL.createObjectURL(new Blob([buf], { type: 'image/jpeg' }))
}

/* ==================== 计时 ==================== */
window.setInterval(() => {
  if (session.start && !session.stop) session.now = performance.now()
  if (running.value) scheduleScore()
}, 500)

export function startTimer() {
  if (session.start) return
  session.start = performance.now()
  session.now = session.start
}

export function finish() {
  if (!session.start || session.stop) return
  session.stop = performance.now()
  session.now = session.stop
  toast(`本轮结束：${doneCount.value} 张样本 · ${clock(elapsedSec.value)}`, 'ok')
  void refreshScore()
}

/* ==================== 评分（服务端口径，不自己算） ==================== */
let scoring = false
let scoreTimer = 0

export async function refreshScore() {
  if (!scoring || !doneCount.value) { score.value = null; return }
  try {
    const r = await api.evalScore(counts.value.map((s) => ({ verdict: s.verdict })), elapsedSec.value)
    score.value = r.score
    rubric.value = r.rubric
  } catch { /* 离线/异常：保留上一次分数 */ }
}
export function scheduleScore() {
  window.clearTimeout(scoreTimer)
  scoreTimer = window.setTimeout(refreshScore, 250)
}
watch(doneCount, scheduleScore)
watch(() => counts.value.map((s) => s.verdict).join(','), scheduleScore)

export async function exportReport() {
  const recs = counts.value.map((s) => ({
    no: s.no,
    name: s.name,
    time: timeOf(s.result?.timestamp),
    detections: (s.result?.detections || []).map((d) => ({ chinese_name: d.chinese_name, confidence: d.confidence })),
    consum: s.result?.inference_time ?? 0,
    elapsed: s.elapsed ?? 0,
    verdict: s.verdict,
  }))
  if (!recs.length) { toast('还没有可导出的样本', 'warn'); return }
  try {
    const r = await api.evalReport(recs, elapsedSec.value)
    const a = document.createElement('a')
    a.href = r.url
    a.download = r.name
    a.click()
    toast(`报告已导出：${r.name}`, 'ok')
  } catch (e) {
    toast(`导出失败：${(e as Error).message}`, 'bad')
  }
}

/* ==================== 重置 / 模式切换 ==================== */
function revoke(list: Sample[]) {
  list.forEach((s) => {
    URL.revokeObjectURL(s.url)
    if (s.boxedUrl && !s.boxedUrl.startsWith('data:')) URL.revokeObjectURL(s.boxedUrl)
  })
}

export function resetSession() {
  revoke(samples.value)
  samples.value = []
  currentId.value = null
  reviewId.value = null
  seq = 0
  session.start = session.stop = session.now = 0
  score.value = null
}

/**
 * 进入某个模式时调用。评分作业的样本从 /eval 暂时切去自由检测再切回来不能丢，
 * 所以先停放；自由检测的样本和以前一样，离开就清空。
 */
type Parked = { samples: Sample[]; currentId: number | null; seq: number; start: number; stop: number; now: number }
let owner: 'eval' | 'free' = 'free'
let parked: Parked | null = null

export function enterMode(mode: 'eval' | 'free') {
  scoring = mode === 'eval'
  if (owner === mode) return
  if (owner === 'eval' && samples.value.length) {
    parked = { samples: samples.value, currentId: currentId.value, seq, start: session.start, stop: session.stop, now: session.now }
    samples.value = []
    currentId.value = null
    reviewId.value = null
    seq = 0
    session.start = session.stop = session.now = 0
    score.value = null
  } else {
    resetSession()
  }
  owner = mode
  if (mode === 'eval' && parked) {
    samples.value = parked.samples
    currentId.value = parked.currentId
    seq = parked.seq
    session.start = parked.start
    session.stop = parked.stop
    session.now = parked.now
    parked = null
    scheduleScore()
  }
}
