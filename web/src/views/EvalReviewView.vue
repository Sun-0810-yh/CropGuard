<script setup lang="ts">
// 评测子页面（/eval/review）：20 张拍完后在这里逐张判对错。
//
// 流程（原型第 12–16 页）：大屏先空 → 约 1 秒后样本栏最左一张飞上大屏 → 人工判正确/错误
// → 下一张待评的直接飞上来叠在当前这张上面（判过的没有回库动画，直接排到样本栏队尾）。
// 判定后**不能更改**；任何时候都可以点缩略图或用 ←/→ 回看，不必先判当前这张。
// 大屏用 DetectionStage 叠框，和实时画面同一个样式；框和类别来自后端推理结果。
// 计分口径不变，分数来自 /api/eval/score。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { clock } from '../lib/format'
import { flyImage } from '../lib/fly'
import { useStation } from '../composables/station'
import {
  counts, doneCount, elapsedSec, enterMode, exportReport, finish, resetSession, rubric, running,
  scheduleScore, score, type Sample, type Verdict,
} from '../composables/evalSession'
import DetectionStage from '../components/DetectionStage.vue'
import Icon from '../components/Icon.vue'
import SampleStrip from '../components/SampleStrip.vue'

const UP_DELAY_MS = 1000

const router = useRouter()
const { state } = useStation()
enterMode('eval')

/** viewing = 回看已判过的（按钮锁定）；done = 全部判完（仍可回看） */
type Phase = 'waiting' | 'flying' | 'judging' | 'viewing' | 'done'
const phase = ref<Phase>('waiting')
const onStage = ref<Sample | null>(null)
const flyingId = ref<number | null>(null)
const judgedOrder = ref<number[]>([])
const stageEl = ref<HTMLElement | null>(null)
const strip = ref<InstanceType<typeof SampleStrip> | null>(null)
let timer = 0

const target = computed(() => state.config?.eval_target || 20)
const judged = computed(() => counts.value.filter((s) => s.verdict).length)
const top = computed(() => onStage.value?.result?.detections[0] || null)
const fmt = (v: number | undefined | null) => (v == null ? '--' : v.toFixed(1))

/** 从最新到第 1 张：上一张/下一张按这个顺序走 */
const ordered = computed(() => [...counts.value].reverse())
const pendingList = computed(() => ordered.value.filter((s) => !s.verdict))
/** 样本栏：待评的在前（当前这张在大屏上，不重复显示），判过的按判定先后排在队尾 */
const stripItems = computed(() => {
  const judgedItems = judgedOrder.value
    .map((id) => counts.value.find((s) => s.id === id))
    .filter((s): s is Sample => !!s)
  return [...pendingList.value.filter((s) => s.id !== onStage.value?.id), ...judgedItems]
    .filter((s) => s.id !== flyingId.value)
})
const stepIndex = computed(() => ordered.value.findIndex((s) => s.id === onStage.value?.id))

/** 大屏里图片实际占的区域：飞行动画落在这里，落定时才不跳 */
function stageRect(s: Sample) {
  const r = stageEl.value?.getBoundingClientRect()
  if (!r || !s.w || !s.h) return r
  const k = Math.min(r.width / s.w, r.height / s.h)
  const w = s.w * k
  const h = s.h * k
  return new DOMRect(r.left + (r.width - w) / 2, r.top + (r.height - h) / 2, w, h)
}

function bringUp(s: Sample) {
  window.clearTimeout(timer)
  const from = strip.value?.thumbRect(s.id)
  flyingId.value = s.id
  phase.value = 'flying'
  void flyImage(s.boxedUrl || s.url, from, stageRect(s)).then(() => {
    onStage.value = s
    flyingId.value = null
    if (!s.verdict) phase.value = 'judging'
    else phase.value = pendingList.value.length ? 'viewing' : 'done'
  })
}

/** 判定后不能更改；这张留在大屏上，下一张待评的直接叠上来 */
function judge(v: Verdict) {
  const s = onStage.value
  if (!s || s.verdict || phase.value !== 'judging') return
  s.verdict = v
  judgedOrder.value = [...judgedOrder.value, s.id]
  scheduleScore()
  const next = pendingList.value[0]
  if (next) bringUp(next)
  else phase.value = 'done'
}

/** 回看：不必先判当前这张；没判的留在待评队列里 */
function view(s: Sample) {
  if (phase.value === 'flying' || s.id === onStage.value?.id) return
  bringUp(s)
}

function step(delta: number) {
  const s = ordered.value[stepIndex.value + delta]
  if (s && stepIndex.value >= 0) view(s)
}

function continueJudging() {
  const next = pendingList.value[0]
  if (next) view(next)
}

function newRound() {
  if (counts.value.length && !window.confirm('开始新一轮会清空这 20 张样本和评判，继续？')) return
  resetSession()
  void router.push('/eval')
}

function onKey(e: KeyboardEvent) {
  if ((e.target as HTMLElement)?.tagName === 'INPUT') return
  if (e.key === 'y' || e.key === 'Y') judge('正确')
  if (e.key === 'n' || e.key === 'N') judge('错误')
  if (e.key === 'ArrowLeft') step(-1)
  if (e.key === 'ArrowRight') step(1)
}

onMounted(() => {
  document.addEventListener('keydown', onKey)
  if (running.value) finish()
  judgedOrder.value = ordered.value.filter((s) => s.verdict).map((s) => s.id)
  scheduleScore()
  const first = pendingList.value[0]
  if (first) timer = window.setTimeout(() => bringUp(first), UP_DELAY_MS)
  else if (counts.value.length) phase.value = 'done'
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  window.clearTimeout(timer)
})
</script>

<template>
  <main class="page">
    <section v-if="!doneCount" class="panel panel-pad none">
      <h2>还没有可评测的样本</h2>
      <p class="muted">这一轮的样本只保存在当前页面，刷新后需要重新拍照。</p>
      <RouterLink class="btn btn-primary" to="/eval"><Icon name="camera" :size="16" />回到拍照</RouterLink>
    </section>

    <template v-else>
      <section class="panel stage-col">
        <div class="stage-head">
          <h2>样本评测</h2>
          <span class="muted small">从最新一张到第 1 张</span>
          <span class="nav">
            <button type="button" class="btn btn-sm" :disabled="phase === 'flying' || stepIndex <= 0" @click="step(-1)">上一张</button>
            <button type="button" class="btn btn-sm" :disabled="phase === 'flying' || stepIndex < 0 || stepIndex >= ordered.length - 1" @click="step(1)">下一张</button>
            <RouterLink class="btn btn-sm" to="/eval">返回拍照</RouterLink>
          </span>
        </div>
        <div ref="stageEl" class="rv-stage">
          <DetectionStage
            v-if="onStage"
            :src="onStage.url"
            :width="onStage.w"
            :height="onStage.h"
            :detections="onStage.result?.detections || []"
            variant="dark"
            max-height="min(560px, 62vh)"
          />
          <p v-else-if="phase === 'waiting'" class="rv-tip">下一张马上上来…</p>
          <p v-if="phase === 'done'" class="rv-tip over">评测完成，点缩略图可以回看</p>
        </div>
      </section>

      <aside class="panel panel-pad side">
        <div class="eyebrow">最终用时</div>
        <div class="final mono">{{ clock(elapsedSec) }}</div>
        <div class="rows">
          <div class="r"><span>已评判</span><b class="mono">{{ judged }} <em>/ {{ Math.min(doneCount, target) }}</em></b></div>
          <div class="r"><span>准确度</span><b class="mono">{{ fmt(score?.acc) }} <em>/ {{ rubric?.acc_full ?? 20 }}</em></b></div>
          <div class="r"><span>时效性</span><b class="mono">{{ fmt(score?.time_score) }} <em>/ {{ rubric?.time_full ?? 10 }}</em></b></div>
        </div>
        <div class="total"><span>总分</span><b class="mono">{{ fmt(score?.score ?? 0) }}</b><em class="mono">/ {{ rubric?.total_full ?? 30 }}</em></div>

        <div class="cur">
          <template v-if="onStage && phase !== 'flying'">
            <span class="mono">样本 {{ onStage.no }}</span>
            <b v-if="top">{{ top.chinese_name }}</b>
            <span v-if="top" class="mono muted">{{ (top.confidence * 100).toFixed(1) }}%</span>
            <span v-if="onStage.verdict" class="locked" :class="onStage.verdict === '正确' ? 'ok' : 'bad'">已判：{{ onStage.verdict }}</span>
          </template>
          <span v-else class="muted">等待下一张</span>
        </div>

        <div class="judge">
          <button type="button" class="btn jb" :disabled="phase !== 'judging'" @click="judge('错误')">
            <Icon name="close" :size="18" />错误
          </button>
          <button type="button" class="btn btn-primary jb" :disabled="phase !== 'judging'" @click="judge('正确')">
            <Icon name="check" :size="18" />正确
          </button>
        </div>
        <button v-if="phase === 'viewing'" type="button" class="btn" @click="continueJudging">继续评判（还有 {{ pendingList.length }} 张）</button>
        <p class="hint">快捷键 Y 正确 / N 错误，←/→ 回看。判定后不可更改。口径与桌面 GUI 同一函数。</p>
        <div class="acts">
          <button type="button" class="btn" @click="exportReport">导出 CSV</button>
          <button type="button" class="btn btn-sm" @click="newRound">新一轮</button>
        </div>
      </aside>

      <section class="panel panel-pad strip-col">
        <div class="stage-head">
          <h2>待评样本</h2>
          <span class="muted small">点缩略图回看，不必先判当前这张；判完的排到队尾；可左右滑动</span>
        </div>
        <SampleStrip
          ref="strip"
          :items="stripItems"
          :active-id="onStage?.id ?? null"
          :disabled="phase === 'flying'"
          @select="view"
        />
      </section>
    </template>
  </main>
</template>

<style scoped>
.page {
  display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start;
  max-width: 1440px; margin: 0 auto; padding: 24px 32px 40px;
}
.none { flex: 1 1 100%; display: flex; flex-direction: column; align-items: flex-start; gap: 12px; }
.stage-col { flex: 999 1 640px; min-width: 0; padding: 16px 20px 20px; }
.side { flex: 1 1 320px; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
@media (min-width: 1100px) { .side { max-width: 420px; } }
.strip-col { flex: 1 1 100%; min-width: 0; }
.stage-head { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 14px; margin-bottom: 12px; }
.small { font-size: 12px; }
.nav { display: inline-flex; flex-wrap: wrap; gap: 8px; margin-left: auto; }
.rv-stage {
  position: relative; display: grid; place-items: center; width: 100%; height: min(560px, 62vh);
  overflow: hidden; background: var(--stage); border-radius: var(--r);
}
.rv-stage :deep(.stage) { width: 100%; height: 100%; }
.rv-tip { color: var(--stage-ink); font-size: 15px; padding: 16px; text-align: center; }
.rv-tip.over { position: absolute; left: 12px; bottom: 12px; padding: 8px 12px; background: rgba(15, 20, 17, .78); border-radius: var(--r); }
.final { font-family: var(--font-num); font-size: 40px; line-height: 1.1; color: var(--ink); font-variant-numeric: tabular-nums; }
.rows { display: flex; flex-direction: column; gap: 2px; margin-top: 4px; }
.r { display: flex; justify-content: space-between; align-items: baseline; padding: 5px 0; font-size: 13px; color: var(--ink-2); border-bottom: 1px solid var(--line); }
.r b { font-size: 15px; color: var(--ink); font-weight: 600; }
.r em, .total em { font-style: normal; color: var(--ink-3); font-weight: 400; font-size: 12px; }
.total { display: flex; align-items: baseline; gap: 8px; padding-top: 6px; font-size: 13px; color: var(--ink-2); }
.total b { font-size: 26px; color: var(--ink); font-weight: 700; margin-left: auto; }
.cur { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 10px; min-height: 44px; padding: 10px 12px; background: var(--inset); border-radius: var(--r); font-size: 14px; }
.locked { font-size: 13px; font-weight: 600; }
.locked.ok { color: var(--brand); }
.locked.bad { color: var(--risk-high); }
.judge { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 4px; }
.jb { justify-content: center; padding: 14px 12px; font-size: 16px; }
.hint { font-size: 12px; color: var(--ink-3); }
.acts { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
@media (max-width: 640px) { .page { padding: 16px; gap: 16px; } }
</style>
