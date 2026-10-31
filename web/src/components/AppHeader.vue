<script setup lang="ts">
import { computed } from 'vue'
import { useStation } from '../composables/station'
import BrandMark from './BrandMark.vue'
import ThemeButton from './ThemeButton.vue'

const { state, nodesOnline } = useStation()

const NAV = [
  { to: '/', label: '检测台' },
  { to: '/eval', label: '现场评分' },
  { to: '/stream', label: '实时监测' },
  { to: '/ask', label: '农技问答' },
  { to: '/spray', label: '喷淋控制' },
  { to: '/dashboard', label: '数据看板' },
  { to: '/system', label: '设备与接入' },
]

const model = computed(() => {
  if (state.online === false) return { tone: 'bad', text: '服务器未连接' }
  if (!state.health) return { tone: '', text: '连接中' }
  return { tone: 'ok', text: `模型 ${state.health.classes} 类 · CPU` }
})
const spray = computed(() => {
  const s = state.spray
  if (!s) return { tone: '', text: '喷淋 --' }
  const total = s.nodes?.length || 0
  if (!s.available) return { tone: 'warn', text: `喷淋 ${s.transport.toUpperCase()} · 未配置` }
  if (!total) return { tone: 'ok', text: `喷淋 ${s.transport.toUpperCase()}` }
  const n = nodesOnline.value
  return { tone: n === total ? 'ok' : n ? 'warn' : 'bad', text: `喷淋 ${s.transport.toUpperCase()} · ${n}/${total} 节点在线` }
})
const voice = computed(() => {
  const t = state.feedback?.tts
  if (!t) return { tone: '', text: '语音 --' }
  return t.ready ? { tone: 'ok', text: '语音 就绪' } : { tone: 'warn', text: '语音 不可用' }
})
</script>

<template>
  <header class="header">
    <RouterLink to="/" class="brand" aria-label="株得 首页">
      <BrandMark />
      <span class="name">株得</span>
      <span class="en">CROPGUARD</span>
    </RouterLink>
    <nav class="nav" aria-label="主导航">
      <RouterLink v-for="n in NAV" :key="n.to" :to="n.to" class="nav-link" exact-active-class="on">{{ n.label }}</RouterLink>
    </nav>
    <div class="readouts mono">
      <span class="ro"><span class="dot" :class="model.tone" />{{ model.text }}</span>
      <RouterLink to="/spray" class="ro"><span class="dot" :class="spray.tone" />{{ spray.text }}</RouterLink>
      <span class="ro"><span class="dot" :class="voice.tone" />{{ voice.text }}</span>
      <ThemeButton />
    </div>
  </header>
</template>

<style scoped>
.header {
  position: sticky; top: env(safe-area-inset-top, 0px); z-index: 30;
  display: flex; align-items: center; flex-wrap: wrap; gap: 0 28px;
  padding: 0 32px; min-height: var(--header-h);
  background: var(--panel); border-bottom: 1px solid var(--line);
}
.brand { display: flex; align-items: center; gap: 10px; text-decoration: none; color: var(--ink); }
.name { font-family: var(--font-num); font-size: 21px; font-weight: 400; font-synthesis: none; letter-spacing: 0.06em; }
.en { font-family: var(--font-num); font-size: 15px; font-weight: 400; font-synthesis: none; letter-spacing: 0.12em; color: var(--ink-3); }
.nav { display: flex; gap: 2px; overflow-x: auto; scrollbar-width: none; max-width: 100%; }
.nav-link {
  padding: 20px 14px 18px; color: var(--ink-2); text-decoration: none; white-space: nowrap;
  border-bottom: 2px solid transparent; transition: color .15s, border-color .15s;
}
.nav-link:hover { color: var(--ink); }
.nav-link.on { color: var(--ink); font-weight: 500; border-bottom-color: var(--signal-ink); }
.readouts {
  margin-left: auto; display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  font-size: 12px; color: var(--ink-2); padding: 8px 0;
}
.ro { display: inline-flex; align-items: center; gap: 7px; color: inherit; text-decoration: none; white-space: nowrap; }
a.ro:hover { color: var(--ink); }
@media (max-width: 1180px) {
  .header { padding: 0 20px; }
  .readouts { margin-left: 0; width: 100%; border-top: 1px solid var(--line); }
}
@media (max-width: 640px) {
  .header { padding: 0 16px; gap: 0 16px; }
  .en { display: none; }
  .nav-link { padding: 16px 10px 14px; }
}
</style>
