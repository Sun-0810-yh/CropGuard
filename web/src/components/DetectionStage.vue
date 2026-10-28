<script setup lang="ts">
// 检测舞台：原图 + 前端叠加检测框（框坐标是原图像素，按百分比定位，任意缩放都对得上）。
// variant=board：标本衬板（浅色网格底，检测台用）；variant=dark：暗场（实时画面用）。
import { computed } from 'vue'
import type { Detection } from '../lib/api'
import { latinName } from '../lib/format'

const props = withDefaults(defineProps<{
  src?: string | null
  width?: number
  height?: number
  detections?: Detection[]
  variant?: 'board' | 'dark'
  maxHeight?: string
  active?: number | null
  busy?: boolean
}>(), {
  src: null, width: 0, height: 0, detections: () => [], variant: 'board',
  maxHeight: '560px', active: null, busy: false,
})
const emit = defineEmits<{ (e: 'update:active', v: number | null): void }>()

const ratio = computed(() => (props.width && props.height ? props.width / props.height : 4 / 3))

const boxes = computed(() => {
  const W = props.width || 1
  const H = props.height || 1
  return props.detections.map((d, i) => {
    const [x1, y1, x2, y2] = d.bbox
    const top = (y1 / H) * 100
    return {
      i,
      label: d.label_en || latinName(d.class_name),
      conf: d.confidence.toFixed(2),
      style: {
        left: `${(x1 / W) * 100}%`,
        top: `${top}%`,
        width: `${((x2 - x1) / W) * 100}%`,
        height: `${((y2 - y1) / H) * 100}%`,
      },
      // 框贴着上沿时标签放进框内，避免被裁掉
      inside: top < 7,
    }
  })
})
</script>

<template>
  <div class="stage" :class="variant">
    <div
      v-if="src"
      class="frame"
      :style="{ aspectRatio: `${width || 4} / ${height || 3}`, width: `min(100%, calc(${maxHeight} * ${ratio}))` }"
    >
      <img :src="src" alt="" draggable="false">
      <div
        v-for="b in boxes"
        :key="b.i"
        class="box"
        :class="{ on: active === b.i, dim: active != null && active !== b.i }"
        :style="b.style"
        @mouseenter="emit('update:active', b.i)"
        @mouseleave="emit('update:active', null)"
      >
        <span class="label" :class="{ inside: b.inside }">{{ b.label }} <span class="mono">{{ b.conf }}</span></span>
      </div>
      <div v-if="busy" class="busy"><span class="scanline" />正在识别…</div>
    </div>
    <div v-else class="placeholder" :style="{ minHeight: `min(${maxHeight}, 60vw)` }">
      <slot name="empty" />
    </div>
    <slot />
  </div>
</template>

<style scoped>
.stage {
  position: relative; display: grid; place-items: center; padding: 28px;
}
.stage.board {
  background-color: var(--inset);
  background-image:
    linear-gradient(var(--grid) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid) 1px, transparent 1px);
  background-size: 24px 24px;
  background-position: -1px -1px;
}
.stage.dark { background: var(--stage); padding: 0; }
.frame { position: relative; max-width: 100%; }
.board .frame { box-shadow: var(--shadow-lift); }
.frame img { display: block; width: 100%; height: 100%; object-fit: contain; user-select: none; }
.box {
  position: absolute; border: 2px solid var(--signal); transition: opacity .15s, border-width .1s;
}
.box.on { border-width: 3px; }
.box.dim { opacity: .35; }
.label {
  position: absolute; left: -2px; bottom: 100%;
  display: flex; align-items: center; gap: 6px; height: 24px; padding: 0 8px;
  background: var(--signal); color: #16201A; font-size: 12.5px; font-weight: 700; white-space: nowrap;
}
.label .mono { font-weight: 500; }
.label.inside { bottom: auto; top: -2px; }
.busy {
  position: absolute; inset: 0; display: grid; place-items: center; overflow: hidden;
  background: rgba(15, 20, 17, 0.45); color: #F2F4EF; font-size: 13px; letter-spacing: 0.06em;
}
.scanline {
  position: absolute; left: 0; right: 0; height: 2px; background: var(--signal);
  box-shadow: 0 0 14px var(--signal); animation: scan 1.1s ease-in-out infinite alternate;
}
@keyframes scan { from { top: 4%; } to { top: 96%; } }
.placeholder { width: 100%; display: grid; place-items: center; }
@media (max-width: 640px) {
  .stage.board { padding: 14px; }
}
</style>
