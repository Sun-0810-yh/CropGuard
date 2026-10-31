<script setup lang="ts">
// 单行可滑动的样本栏（评分作业 / 评测子页共用）：不换行、不另开一栏；
// 靠右边缘的缩略图逐渐缩小，提示后面还有；鼠标滚轮可以横向滑动。
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Sample } from '../composables/evalSession'
import { riskKey } from '../lib/format'

const props = withDefaults(defineProps<{
  items: Sample[]
  activeId?: number | null
  /** 正在飞行的样本：先占位但不显示，落位后再现身 */
  hiddenId?: number | null
  disabled?: boolean
}>(), { activeId: null, hiddenId: null, disabled: false })
const emit = defineEmits<{ (e: 'select', s: Sample): void }>()

const ZOOM_EDGE = 170      // 右边缘多宽的范围内开始缩小（px）
const ZOOM_MIN = 0.35

const groupEl = ref<{ $el: HTMLElement } | null>(null)
const scrollEl = () => groupEl.value?.$el || null

function applyZoom() {
  const box = scrollEl()
  if (!box) return
  const right = box.getBoundingClientRect().right
  box.querySelectorAll<HTMLElement>('.st-thumb').forEach((el) => {
    const inner = el.firstElementChild as HTMLElement | null
    if (!inner) return
    const gap = right - el.getBoundingClientRect().right
    const k = gap >= ZOOM_EDGE ? 1 : Math.max(ZOOM_MIN, (gap + el.offsetWidth) / (ZOOM_EDGE + el.offsetWidth))
    inner.style.transform = k === 1 ? '' : `scale(${k.toFixed(3)})`
  })
}

function onWheel(e: WheelEvent) {
  const box = scrollEl()
  if (!box || box.scrollWidth <= box.clientWidth || !e.deltaY) return
  e.preventDefault()
  box.scrollLeft += e.deltaY
}

function thumbRect(id: number) {
  const el = scrollEl()?.querySelector<HTMLElement>(`[data-sample="${id}"] .st-img`)
  return el?.getBoundingClientRect() || null
}

/** 回到队尾的飞行终点：队尾在可视范围外时落到右边缘 */
function tailRect(id: number) {
  const box = scrollEl()
  const rect = thumbRect(id)
  if (!box || !rect) return rect
  const bound = box.getBoundingClientRect()
  if (rect.left + rect.width <= bound.right) return rect
  return new DOMRect(bound.right - rect.width, rect.top, rect.width, rect.height)
}

function toStart() {
  const box = scrollEl()
  if (box) box.scrollLeft = 0
}

defineExpose({ thumbRect, tailRect, toStart })

let ro: ResizeObserver | null = null
onMounted(() => {
  applyZoom()
  const box = scrollEl()
  if (box && 'ResizeObserver' in window) {
    ro = new ResizeObserver(applyZoom)
    ro.observe(box)
  }
})
onBeforeUnmount(() => ro?.disconnect())
watch(() => props.items.map((s) => s.id).join(','), () => void nextTick(applyZoom))
</script>

<template>
  <div class="st" @wheel="onWheel">
    <TransitionGroup ref="groupEl" tag="div" name="st" class="st-row" @scroll.passive="applyZoom">
      <button
        v-for="s in items"
        :key="s.id"
        type="button"
        class="st-thumb"
        :class="{ on: s.id === activeId, hide: s.id === hiddenId }"
        :disabled="disabled"
        :data-sample="s.id"
        :aria-label="`样本 ${s.no}`"
        @click="emit('select', s)"
      >
        <span class="st-in">
          <span class="st-img">
            <img :src="s.boxedUrl || s.url" alt="" draggable="false">
            <span v-if="s.result" class="st-risk" :class="`bg-${riskKey(s.result.risk.level)}`" />
            <span v-if="s.verdict" class="st-badge" :class="s.verdict === '正确' ? 'ok' : 'bad'">{{ s.verdict === '正确' ? '✓' : '✗' }}</span>
          </span>
          <span class="st-cap">
            <span class="st-no mono">{{ s.no }}</span>
            <span class="st-name">{{ s.result?.detections[0]?.chinese_name || '' }}</span>
          </span>
        </span>
      </button>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.st { min-width: 0; }
.st-row {
  display: flex; flex-wrap: nowrap; gap: 12px; overflow-x: auto; overflow-y: hidden; padding: 4px 4px 8px;
  scrollbar-width: thin;
}
.st-thumb {
  flex: 0 0 118px; width: 118px; padding: 0; border: 0; background: none; cursor: pointer; text-align: left;
}
.st-thumb:disabled { cursor: default; }
.st-thumb.hide { visibility: hidden; }
.st-move { transition: transform .4s cubic-bezier(.3,.7,.2,1); }
.st-leave-active { position: absolute; opacity: 0; }
.st-in { display: flex; flex-direction: column; gap: 6px; transform-origin: left center; transition: transform .12s linear; }
.st-img {
  position: relative; display: block; aspect-ratio: 4 / 3; overflow: hidden; background: var(--inset);
  outline: 2px solid transparent; outline-offset: 2px; transition: outline-color .15s;
}
.st-thumb:not(:disabled):hover .st-img { outline-color: var(--line-strong); }
.st-thumb.on .st-img { outline-color: var(--signal); }
.st-img img { display: block; width: 100%; height: 100%; object-fit: cover; }
.st-risk { position: absolute; left: 0; right: 0; bottom: 0; height: 4px; }
.bg-high { background: var(--risk-high); }
.bg-mid { background: var(--risk-mid); }
.bg-low { background: var(--risk-low); }
.bg-none { background: var(--risk-none); }
.st-badge {
  position: absolute; right: 4px; top: 4px; width: 18px; height: 18px; display: grid; place-items: center;
  border-radius: 50%; font-size: 11px; font-weight: 700; color: #fff;
}
.st-badge.ok { background: var(--brand); }
.st-badge.bad { background: var(--risk-high); }
.st-cap { display: flex; gap: 6px; font-size: 12px; min-width: 0; }
.st-no { color: var(--ink-3); }
.st-name { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (prefers-reduced-motion: reduce) {
  .st-move, .st-in { transition: none; }
}
</style>
