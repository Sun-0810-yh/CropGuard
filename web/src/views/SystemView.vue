<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type CameraState, type LanInfo } from '../lib/api'
import { toast, useStation } from '../composables/station'
import Icon from '../components/Icon.vue'

const { state } = useStation()
const lan = ref<LanInfo | null>(null)
const cams = ref<Record<string, CameraState>>({})
const restarting = ref(false)

const phoneUrls = computed(() => {
  const l = lan.value
  if (!l) return []
  return [...(l.https_port ? l.phone_urls_https : []), ...l.phone_urls]
})
const qrSrc = computed(() => (phoneUrls.value[0] ? `/qr?size=6&url=${encodeURIComponent(phoneUrls.value[0])}` : '/qr?size=6'))

async function loadCams() {
  try { cams.value = (await api.cameraHealth()).pool || {} } catch { /* 忽略 */ }
}
async function restart() {
  restarting.value = true
  try {
    const r = await api.restartCamera(null)
    toast(r.success ? '相机已重启' : `重启没有成功：${r.state?.last_error || '设备无响应'}`, r.success ? 'ok' : 'bad')
  } catch (e) {
    toast(`重启失败：${(e as Error).message}`, 'bad')
  } finally {
    restarting.value = false
    loadCams()
  }
}
async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast('已复制地址')
  } catch {
    toast('浏览器不允许复制，请手动选中地址', 'bad')
  }
}
onMounted(async () => {
  try { lan.value = await api.lan() } catch { /* 忽略 */ }
  loadCams()
})

const API = [
  ['POST', '/api/detect', '上传图片检测（multipart：file，可选 conf）'],
  ['POST', '/api/ask', '知识库问答（检索 + 拒答闸门 + 后校验）'],
  ['GET', '/api/kb/search?q=', '只看检索召回与闸门分，不调大模型'],
  ['POST', '/api/ask_command', '自然语言 → 喷洒意图（只解析，需确认）'],
  ['POST', '/api/spray', '手动喷淋 {"group":"g1","duration":2}'],
  ['POST', '/api/spray/stop', '紧急停止'],
  ['GET', '/api/spray/status', '喷淋链路与节点在线状态'],
  ['GET', '/api/camera/health', '相机池：帧龄、重开次数、fps'],
  ['WS', '/ws/stream?cam=A', '定点相机实时流'],
  ['WS', '/ws/phone', '手机上传帧实时识别'],
]
</script>

<template>
  <main class="page">
    <section class="main-col">
      <div class="panel panel-pad">
        <div class="panel-head"><h2>手机接入</h2><span class="aside">手机与本机连同一个 WiFi</span></div>
        <div class="phone">
          <img class="qr" :src="qrSrc" alt="手机端入口二维码" width="168" height="168">
          <div class="urls">
            <p class="lead">扫码进入手机端：拍照识别、实时识别、问答、喷洒。</p>
            <div v-for="u in phoneUrls.slice(0, 3)" :key="u" class="url">
              <span class="mono">{{ u }}</span>
              <button type="button" class="btn btn-sm" @click="copy(u)">复制</button>
            </div>
            <p v-if="!phoneUrls.length" class="empty">没有找到局域网地址。确认电脑已连上 WiFi 或手机热点。</p>
            <p class="note">实时识别要用 https 地址打开（浏览器只在安全连接下允许网页用摄像头）。第一次打开会提示证书不受信任，选"继续访问"。拍照识别用 http 地址也可以。</p>
          </div>
        </div>
      </div>

      <div class="panel panel-pad">
        <div class="panel-head">
          <h2>相机</h2>
          <button type="button" class="btn btn-sm push" :disabled="restarting" @click="restart"><Icon name="refresh" :size="14" />{{ restarting ? '正在重启…' : '重启相机' }}</button>
        </div>
        <div v-if="Object.keys(cams).length" class="kv-grid">
          <div v-for="(c, k) in cams" :key="k" class="cam">
            <div class="cam-h"><span class="dot" :class="c.ok ? 'ok' : 'bad'" /><b>{{ c.device || c.name || k }}</b><span class="mono muted">{{ c.state }}</span></div>
            <div class="kv mono">
              <span>分辨率</span><span>{{ c.size || '--' }}</span>
              <span>帧率</span><span>{{ c.fps ?? '--' }} fps</span>
              <span>帧龄</span><span>{{ c.frame_age != null ? `${c.frame_age} s` : '--' }}</span>
              <span>自愈次数</span><span>{{ c.reopens ?? 0 }}</span>
            </div>
            <p v-if="c.last_error" class="err">{{ c.last_error }}</p>
          </div>
        </div>
        <p v-else class="empty">当前没有页面在用相机。打开"实时监测"后这里会显示相机状态。</p>
      </div>

      <div class="panel panel-pad">
        <div class="panel-head"><h2>接口</h2><a class="aside ext" href="/docs" target="_blank" rel="noopener">完整接口文档 <Icon name="external" :size="13" /></a></div>
        <div class="table-wrap">
          <table class="api">
            <tbody>
              <tr v-for="a in API" :key="a[1]">
                <td><span class="verb mono" :class="a[0].toLowerCase()">{{ a[0] }}</span></td>
                <td class="mono path">{{ a[1] }}</td>
                <td>{{ a[2] }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <aside class="side-col">
      <div class="panel panel-pad">
        <div class="panel-head"><h2>识别模型</h2></div>
        <div class="kv">
          <span>权重</span><span class="mono">{{ state.config?.model || '--' }}</span>
          <span>类别数</span><span class="mono">{{ state.health?.classes ?? '--' }}</span>
          <span>推理设备</span><span class="mono">{{ state.health ? 'cpu' : '--' }}</span>
          <span>输入尺寸</span><span class="mono">{{ state.config?.imgsz ?? '--' }}</span>
          <span>默认阈值</span><span class="mono">{{ state.config?.conf_default ?? '--' }}</span>
        </div>
      </div>
      <div class="panel panel-pad">
        <div class="panel-head"><h2>语音播报</h2></div>
        <div class="kv">
          <span>状态</span><span><span class="dot" :class="state.feedback?.tts?.ready ? 'ok' : 'warn'" style="display:inline-block;margin-right:6px" />{{ state.feedback?.tts?.ready ? '就绪' : '不可用' }}</span>
          <span>引擎</span><span class="mono">{{ state.feedback?.tts?.engine || '--' }}</span>
          <span>已播报</span><span class="mono">{{ state.feedback?.tts?.spoken ?? 0 }} 句</span>
          <span>失败</span><span class="mono">{{ state.feedback?.tts?.failed ?? 0 }} 句</span>
          <span>声光报警串口</span><span class="mono">{{ state.feedback?.serial ? '已连接' : '未连接' }}</span>
        </div>
        <ul v-if="state.feedback?.tts?.init_errors?.length" class="errs">
          <li v-for="e in state.feedback.tts.init_errors" :key="e">{{ e }}</li>
        </ul>
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
.side-col { flex: 1 1 340px; min-width: 0; max-width: 100%; display: flex; flex-direction: column; gap: 24px; }
@media (min-width: 1100px) { .side-col { max-width: 400px; } }
.phone { display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; }
.qr { width: 168px; height: 168px; padding: 8px; background: #fff; border: 1px solid var(--line); image-rendering: pixelated; flex: none; }
.urls { flex: 1 1 280px; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.lead { font-size: 14px; }
.url { display: flex; align-items: center; gap: 10px; justify-content: space-between; padding: 6px 6px 6px 12px; background: var(--inset); border-radius: var(--r); }
.url .mono { font-size: 12.5px; overflow-wrap: anywhere; }
.note { font-size: 12px; color: var(--ink-3); line-height: 1.7; }
.kv-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
.cam-h { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.cam-h .mono { margin-left: auto; font-size: 12px; }
.kv { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 8px 16px; font-size: 13px; }
.kv > span:nth-child(odd) { color: var(--ink-3); }
.kv > span:nth-child(even) { overflow-wrap: anywhere; }
.kv.mono > span { font-size: 12.5px; }
.err, .errs { margin-top: 8px; font-size: 12px; color: var(--risk-high); }
.errs { padding-left: 18px; }
.push { margin-left: auto; }
.ext { display: inline-flex; align-items: center; gap: 4px; color: var(--brand); text-decoration: none; }
.table-wrap { overflow-x: auto; }
.api { width: 100%; border-collapse: collapse; font-size: 13px; }
.api td { padding: 8px 14px 8px 0; border-top: 1px solid var(--inset); vertical-align: baseline; }
.api tr:first-child td { border-top: 0; }
.path { font-size: 12.5px; white-space: nowrap; }
.verb { display: inline-block; min-width: 44px; text-align: center; padding: 1px 6px; font-size: 11px; font-weight: 500; border-radius: var(--r-sm); background: var(--brand-soft); color: var(--brand); }
.verb.post { background: var(--warn-bg); color: var(--warn-ink); }
.verb.ws { background: var(--inset); color: var(--ink-2); }
@media (max-width: 640px) { .page { padding: 16px; gap: 16px; } }
</style>
