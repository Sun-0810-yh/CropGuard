<script setup lang="ts">
// 现场评分面板（纯展示）：倒计时 / 进度 / 准确度 / 时效性 / 总分 / 三个按钮。
// 口径不在这里算：分数一律来自 /api/eval/score（与桌面 GUI 同一份纯函数），避免"页面自己算一套"。
import { computed } from 'vue'
import type { EvalRubric, EvalScore } from '../lib/api'
import Icon from './Icon.vue'

const props = withDefaults(defineProps<{
  score: EvalScore | null
  counted: number
  elapsed: number          // 本轮已用秒数
  limit: number            // 限时（默认 180）
  target: number           // 目标样本数（默认 20）
  started: boolean
  running: boolean
  rubric?: Partial<EvalRubric> | null
}>(), { score: null, limit: 180, target: 20, started: false, running: false, rubric: null })

const emit = defineEmits<{
  (e: 'finish'): void
  (e: 'export'): void
  (e: 'reset'): void
}>()

const accFull = computed(() => props.rubric?.acc_full ?? 20)
const timeFull = computed(() => props.rubric?.time_full ?? 10)
const totalFull = computed(() => props.rubric?.total_full ?? 30)

const over = computed(() => props.started && props.elapsed > props.limit)
const timerText = computed(() => {
  if (!props.started) return '--:--'
  const left = props.limit - props.elapsed
  if (props.running && left >= 0) {
    const s = Math.ceil(left)
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
  }
  if (props.running) return `+${Math.floor(-left)}s`
  const s = Math.max(0, Math.round(props.elapsed))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
})

const fmt = (v: number | undefined | null) => (v == null ? '--' : v.toFixed(1))
</script>

<template>
  <div class="panel panel-pad eval">
    <div class="panel-head">
      <h2>现场评分</h2>
      <span class="tag">{{ target }} 样本 · {{ limit }} 秒</span>
    </div>

    <div class="timer" :class="{ over, idle: !started }">{{ timerText }}</div>
    <div class="timer-cap">{{ running ? '倒计时' : started ? '本轮用时' : '第一张快照落位后开始计时' }}</div>

    <div class="rows">
      <div class="r"><span>样本</span><b class="mono">{{ counted }} <em>/ {{ target }}</em></b></div>
      <div class="r"><span>已评判</span><b class="mono">{{ score?.judged ?? 0 }}</b></div>
      <div class="r"><span>准确度</span><b class="mono">{{ fmt(score?.acc) }} <em>/ {{ accFull }}</em></b></div>
      <div class="r"><span>时效性</span><b class="mono">{{ fmt(score?.time_score) }} <em>/ {{ timeFull }}</em></b></div>
    </div>

    <div class="total">
      <span>总分</span>
      <b class="mono">{{ fmt(score?.score ?? 0) }}</b>
      <em class="mono">/ {{ totalFull }}</em>
    </div>

    <div class="acts">
      <button type="button" class="btn" :disabled="!running" @click="emit('finish')">
        <Icon name="stop" :size="15" />结束本轮
      </button>
      <button type="button" class="btn btn-primary" :disabled="!counted" @click="emit('export')">
        导出 CSV
      </button>
      <button type="button" class="btn btn-sm" @click="emit('reset')">新一轮</button>
    </div>
    <p class="hint">准确度 20 + 时效性 10；未评判不计入正确。口径与桌面 GUI 同一函数。</p>
  </div>
</template>

<style scoped>
.eval { display: flex; flex-direction: column; gap: 10px; }
.timer {
  font-family: var(--font-num); font-size: 40px; line-height: 1.1; letter-spacing: .02em;
  color: var(--ink); font-variant-numeric: tabular-nums;
}
.timer.idle { color: var(--ink-3); }
.timer.over { color: var(--risk-high); }
.timer-cap { font-size: 12px; color: var(--ink-3); margin-top: -6px; }
.rows { display: flex; flex-direction: column; gap: 2px; margin-top: 4px; }
.r { display: flex; justify-content: space-between; align-items: baseline; padding: 6px 0; border-top: 1px solid var(--line); font-size: 13px; }
.r span { color: var(--ink-2); }
.r b { font-family: var(--font-mono); font-size: 15px; color: var(--ink); font-weight: 500; }
.r em { font-style: normal; font-size: 12px; color: var(--ink-3); }
.total { display: flex; align-items: baseline; gap: 8px; padding-top: 10px; border-top: 1px solid var(--line-strong); }
.total span { font-size: 13px; color: var(--ink-2); }
.total b { font-family: var(--font-num); font-size: 30px; color: var(--brand); }
.total em { font-style: normal; font-size: 13px; color: var(--ink-3); }
.acts { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; }
.hint { font-size: 12px; color: var(--ink-3); line-height: 1.6; }
</style>
