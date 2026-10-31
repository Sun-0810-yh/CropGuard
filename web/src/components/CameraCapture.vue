<script setup lang="ts">
// 摄像头输入（检测台的「拍照作业」）：服务器相机的实时画面 + 一键拍照。
//
// 画面走 /ws/stream（与实时监测同一个实现，见 composables/liveStream.ts）。
// 规则对齐 GUI：**实时画面本身不计样本**；按下拍照才把当前帧交给检测台，
// 检出目标才算一条样本（检测台负责落盘快照 + 计时）。
// data-fly-source：拍照动画的起点（见 lib/fly.ts）。
// 画面铺满底框（max-height 只是给个足够大的上限，实际高度由宽度和画幅决定）。
import { ref } from 'vue'
import { useLiveStream } from '../composables/liveStream'
import { toast } from '../composables/station'
import { api } from '../lib/api'
import DetectionStage from './DetectionStage.vue'
import Icon from './Icon.vue'

const props = withDefaults(defineProps<{ busy?: boolean; count?: number; target?: number; full?: boolean }>(), {
  busy: false, count: 0, target: 20, full: false,
})
const emit = defineEmits<{ (e: 'capture', p: { blob: Blob; w: number; h: number }): void }>()

const { conn, frameUrl: frame, frameBlob, size, dets, cam, age, errorMsg, warnMsg, connect } = useLiveStream()
const restarting = ref(false)

function shoot() {
  if (!frameBlob.value) { toast('画面还没出来，等相机就绪再拍', 'warn'); return }
  emit('capture', { blob: frameBlob.value, w: size.value[0], h: size.value[1] })
}

async function restart() {
  restarting.value = true
  try {
    const r = await api.restartCamera(null)
    toast(r.success ? '相机已重启' : `重启没有成功：${r.state?.last_error || '设备无响应'}`, r.success ? 'ok' : 'bad')
    if (r.success) connect()
  } catch (e) {
    toast(`重启失败：${(e as Error).message}`, 'bad')
  } finally {
    restarting.value = false
  }
}
</script>

<template>
  <div class="cam">
    <div class="cam-bar">
      <span class="conn">
        <span class="dot" :class="{ ok: conn === 'on', warn: conn === 'wait', bad: conn === 'error' || conn === 'off' }" />
        {{ conn === 'on' ? '已连接' : conn === 'wait' ? '正在打开相机…' : conn === 'off' ? '已断开，2 秒后重连' : '相机不可用' }}
      </span>
      <span class="mono meta">
        {{ cam?.device || '' }}{{ cam?.size ? ` · ${cam.size}` : '' }}{{ cam?.fps != null ? ` · ${cam.fps} fps` : '' }}
        <template v-if="age != null"> · 推理 {{ age.toFixed(1) }} s 前</template>
      </span>
    </div>

    <div v-if="frame" data-fly-source>
      <DetectionStage
        :src="frame"
        :width="size[0]"
        :height="size[1]"
        :detections="dets"
        variant="dark"
        max-height="2000px"
      />
    </div>
    <div v-else class="cam-empty">
      <Icon name="live" :size="34" :stroke="1.4" />
      <template v-if="errorMsg">
        <p class="ce-t">{{ errorMsg }}</p>
        <p class="ce-s">检查相机是否插好、是否被桌面 GUI / 微信占用；也可以点下面重启。</p>
        <button type="button" class="btn" :disabled="restarting" @click="restart">
          <Icon name="refresh" :size="16" />{{ restarting ? '正在重启…' : '重启相机' }}
        </button>
      </template>
      <p v-else class="ce-t">正在打开相机（外接 USB 冷启动最长约 15 秒）…</p>
    </div>

    <div v-if="warnMsg && frame" class="cam-warn">
      <span>{{ warnMsg }}</span>
      <button type="button" class="btn btn-sm" :disabled="restarting" @click="restart">重启相机</button>
    </div>

    <div class="shoot-row">
      <button type="button" class="btn btn-primary shoot" :disabled="!frame || props.busy || props.full" @click="shoot">
        <Icon name="camera" :size="18" />
        <span>{{ props.full ? `已满 ${props.target} 张` : props.busy ? '识别中…' : `拍照并记录（${props.count} / ${props.target}）` }}</span>
      </button>
      <span class="shoot-hint">看到框就按；实时画面本身不计入样本。</span>
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.cam { display: flex; flex-direction: column; gap: 12px; }
.cam-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; font-size: 13px; color: var(--ink-2); }
.conn { display: inline-flex; align-items: center; gap: 7px; }
.meta { margin-left: auto; font-size: 12px; color: var(--ink-3); }
.cam-empty {
  min-height: min(420px, 46vh); display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 10px; padding: 28px; text-align: center; background: var(--stage); border-radius: var(--r-lg); color: #AEB8B0;
}
.ce-t { color: var(--stage-ink); font-size: 15px; max-width: 520px; }
.ce-s { font-size: 13px; max-width: 520px; }
.cam-empty .btn { background: transparent; color: var(--stage-ink); border-color: #3A453E; }
.cam-warn {
  display: flex; align-items: center; gap: 12px; padding: 8px 12px; border-radius: var(--r);
  background: var(--warn-bg); color: var(--warn-ink); border: 1px solid var(--warn-line); font-size: 13px;
}
.cam-warn span { flex: 1; }
.shoot-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.shoot { min-width: 260px; justify-content: center; padding: 12px 20px; font-size: 15px; }
.shoot-hint { flex: 1 1 auto; font-size: 12px; color: var(--ink-3); }
</style>
