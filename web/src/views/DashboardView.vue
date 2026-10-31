<script setup lang="ts">
// 数据看板：一页讲清"模型有多准、现场有多快、系统哪些在线"。数字全部来自 build_dashboard.py 生成的快照，
// 联动预案与在线状态来自实时接口。快照缺哪块就在那块写明原因和生成命令。
import { computed, onMounted, ref } from 'vue'
import { fetchSnapshot, type ClassAP, type Snapshot } from '../lib/dashboard'
import { api, type CameraState } from '../lib/api'
import { groupLabel, latinName } from '../lib/format'
import { useStation } from '../composables/station'
import Icon from '../components/Icon.vue'
import LineChart from '../components/LineChart.vue'

const { state, zoneOf, nodesOnline } = useStation()
const snap = ref<Snapshot | null>(null)
const loadError = ref('')
const cams = ref<Record<string, CameraState>>({})

async function load() {
  loadError.value = ''
  try { snap.value = await fetchSnapshot() } catch (e) { loadError.value = (e as Error).message }
  try { cams.value = (await api.cameraHealth()).pool || {} } catch { /* 忽略 */ }
}
onMounted(load)

const tr = computed(() => (snap.value?.training.available ? snap.value.training : null))
const val = computed(() => (snap.value?.validation.available ? snap.value.validation : null))
const smp = computed(() => (snap.value?.samples.available ? snap.value.samples : null))
const rag = computed(() => (snap.value?.rag.available ? snap.value.rag : null))
const tests = computed(() => (snap.value?.tests.available ? snap.value.tests : null))
const cn = (k: string | null) => (k ? snap.value?.model.chinese?.[k] || k : '—')
const reason = (b: { available: boolean; reason?: string } | undefined) => (b && !b.available ? b.reason : '')
const pct = (v: number, d = 1) => `${(v * 100).toFixed(d)}%`

/* ---------------- 训练曲线 ---------------- */
const curveTab = ref<'metric' | 'train' | 'val'>('metric')
const curves = computed(() => {
  const t = tr.value
  if (!t) return []
  if (curveTab.value === 'metric') return [
    { key: 'map50', label: 'mAP@0.5', values: t.map50 },
    { key: 'map50_95', label: 'mAP@0.5:0.95', values: t.map50_95 },
    { key: 'precision', label: '精确率', values: t.precision },
    { key: 'recall', label: '召回率', values: t.recall },
  ]
  const p = curveTab.value === 'train' ? 'train' : 'val'
  return [
    { key: 'box', label: 'box 损失', values: t[`${p}_box`] },
    { key: 'cls', label: 'cls 损失', values: t[`${p}_cls`] },
    { key: 'dfl', label: 'dfl 损失', values: t[`${p}_dfl`] },
  ]
})

/* ---------------- 逐类 mAP ---------------- */
const sortBy = ref<'ap' | 'id'>('ap')
const onlyTop = ref(false)
const hoverCls = ref<ClassAP | null>(null)
const classes = computed(() => {
  const list = (val.value?.classes || []).slice()
  list.sort(sortBy.value === 'ap' ? (a, b) => b.ap50 - a.ap50 : (a, b) => a.id - b.id)
  return onlyTop.value ? list.filter((c) => c.ap50 >= 0.95) : list
})
const top95 = computed(() => (val.value?.classes || []).filter((c) => c.ap50 >= 0.95))
const best = computed(() => (val.value?.classes || []).reduce<ClassAP | null>((a, c) => (!a || c.ap50 > a.ap50 ? c : a), null))

/* ---------------- 联动 / 状态 ---------------- */
const plans = computed(() =>
  Object.entries(state.config?.risk_map || {}).map(([level, m]) => ({
    level, act: m.group && m.duration > 0 ? `${groupLabel(m.group, zoneOf.value)} 喷淋 ${m.duration} 秒` : '不喷淋',
  })),
)
const camList = computed(() => Object.values(cams.value))
const generated = computed(() => (snap.value?.generated_at || '').replace('T', ' '))

const LOOP = [
  ['外接相机识别', 'tool/camera_source.py', '共享采集源，无设备时如实报原因'],
  ['摄像头卡死自愈', '看门狗换 handle', '无新帧 / 画面不变即重开'],
  ['语音播报', 'feedback/tts.py', '专用线程 + 多后端真出声校验'],
  ['自由提问', 'llm/ai_gateway.py', '网页、手机、GUI 同一答案'],
  ['防幻觉', '召回闸门 + 接地提示 + 后校验', '库外问题直接拒答'],
  ['手机实时识别', 'WS /ws/phone', '二进制帧上传，只回检测 JSON'],
  ['手机指令喷洒', 'POST /api/ask_command', '只解析，人工确认后才下发'],
]
</script>

<template>
  <main class="page">
    <div class="head">
      <div>
        <h1>数据看板</h1>
        <p class="muted sub">{{ snap?.available ? `快照生成于 ${generated} · 模型 ${snap.model.run} · ${snap.model.num_classes} 类 · imgsz ${snap.model.imgsz}` : '读取快照…' }}</p>
      </div>
      <button type="button" class="btn btn-sm" @click="load"><Icon name="refresh" :size="14" />刷新</button>
    </div>

    <div v-if="loadError || (snap && !snap.available)" class="panel panel-pad missing">
      <h2>看板快照还没有生成</h2>
      <p>{{ loadError || snap?.reason }}</p>
      <pre class="mono">python scripts/build_dashboard.py --data dataset_full/data.yaml --samples pictures20</pre>
      <p class="muted">脚本会跑一遍验证集（CPU 上约 6~8 分钟）、20 张实拍样本、RAG 评测和单元测试，结果写到 output/dashboard/。</p>
    </div>

    <template v-if="snap?.available">
      <!-- ================= 核心指标 ================= -->
      <section class="panel kpis">
        <div class="kpi">
          <div class="k">验证集 mAP@0.5</div>
          <div class="v num">{{ val ? pct(val.map50) : '—' }}</div>
          <div class="s">{{ val ? `mAP@0.5:0.95 ${val.map50_95.toFixed(3)} · 验证 ${val.val_images} 张` : reason(snap.validation) }}</div>
        </div>
        <div class="kpi">
          <div class="k">实拍样本 Top1 命中</div>
          <div class="v num">{{ smp ? `${smp.correct}/${smp.labeled}` : '—' }}</div>
          <div class="s">{{ smp ? `准确率 ${pct(smp.correct / Math.max(1, smp.labeled), 0)} · 阈值 ${smp.conf}` : reason(snap.samples) }}</div>
        </div>
        <div class="kpi">
          <div class="k">平均单张耗时</div>
          <div class="v num">{{ smp ? smp.avg_ms.toFixed(0) : '—' }}<small v-if="smp"> ms</small></div>
          <div class="s">{{ smp ? `${smp.device.toUpperCase()} · ${smp.count} 张共 ${smp.total_s} s（限时 180 s）` : '' }}</div>
        </div>
        <div class="kpi">
          <div class="k">识别类别</div>
          <div class="v num">{{ snap.model.num_classes }}<small> 类</small></div>
          <div class="s">{{ val ? `训练 ${val.train_images} 张 · 验证 ${val.val_images} 张` : '' }}</div>
        </div>
        <div class="kpi">
          <div class="k">mAP@0.5 ≥ 0.95 的类别</div>
          <div class="v num">{{ val ? top95.length : '—' }}<small v-if="val"> 类</small></div>
          <div class="s">{{ best ? `最高 ${cn(best.name)} ${best.ap50.toFixed(3)}` : '' }}</div>
        </div>
        <div class="kpi">
          <div class="k">精确率 / 召回率</div>
          <div class="v num">{{ val ? val.precision.toFixed(3) : '—' }}<small v-if="val"> / {{ val.recall.toFixed(3) }}</small></div>
          <div class="s">{{ val ? `验证集全类平均 · ${snap.model.device.toUpperCase()}` : '' }}</div>
        </div>
      </section>

      <!-- ================= 训练曲线 + 逐类 mAP ================= -->
      <div class="row">
        <section class="panel panel-pad grow2">
          <div class="panel-head">
            <h2>训练收敛曲线</h2>
            <span v-if="tr" class="aside mono">{{ tr.epoch.length }} 轮 · 最佳 mAP@0.5 在第 {{ tr.best_epoch }} 轮</span>
          </div>
          <template v-if="tr">
            <div class="tabs" role="tablist">
              <button type="button" role="tab" :aria-selected="curveTab === 'metric'" :class="{ on: curveTab === 'metric' }" @click="curveTab = 'metric'">mAP / 精确率 / 召回率</button>
              <button type="button" role="tab" :aria-selected="curveTab === 'train'" :class="{ on: curveTab === 'train' }" @click="curveTab = 'train'">训练损失</button>
              <button type="button" role="tab" :aria-selected="curveTab === 'val'" :class="{ on: curveTab === 'val' }" @click="curveTab = 'val'">验证损失</button>
            </div>
            <LineChart :x="tr.epoch" :series="curves" :y-min="curveTab === 'metric' ? 0 : null" :y-max="curveTab === 'metric' ? 1 : null" :height="300" />
            <p class="src mono">来源：{{ tr.source }}</p>
          </template>
          <p v-else class="empty">{{ reason(snap.training) }}</p>
        </section>

        <section class="panel panel-pad grow1">
          <div class="panel-head">
            <h2>逐类 mAP@0.5</h2>
            <span class="aside">{{ val ? `${val.num_classes} 类 · 验证集` : '' }}</span>
          </div>
          <template v-if="val">
            <div class="cls-tools">
              <div class="tabs small" role="tablist">
                <button type="button" :class="{ on: sortBy === 'ap' }" @click="sortBy = 'ap'">按 mAP 排序</button>
                <button type="button" :class="{ on: sortBy === 'id' }" @click="sortBy = 'id'">按类别编号</button>
              </div>
              <label class="chk"><input v-model="onlyTop" type="checkbox">只看 ≥ 0.95</label>
            </div>
            <div class="cls-list" @mouseleave="hoverCls = null">
              <div v-for="(c, i) in classes" :key="c.id" class="cls" :class="{ on: hoverCls?.id === c.id }" @mouseenter="hoverCls = c">
                <span class="rank mono">{{ sortBy === 'ap' ? i + 1 : c.id }}</span>
                <span class="cname"><b>{{ cn(c.name) }}</b><span class="latin">{{ latinName(c.name) }}</span></span>
                <span class="bar"><span :style="{ width: `${c.ap50 * 100}%` }" :class="{ top: c.ap50 >= 0.95 }" /><i class="mark95" /></span>
                <span class="mono val">{{ c.ap50.toFixed(3) }}</span>
              </div>
            </div>
            <div class="cls-foot mono">
              <template v-if="hoverCls">{{ cn(hoverCls.name) }} · P {{ hoverCls.p.toFixed(3) }} · R {{ hoverCls.r.toFixed(3) }} · mAP@0.5:0.95 {{ hoverCls.ap50_95.toFixed(3) }}</template>
              <template v-else>竖线为 0.95；鼠标移到某一类上看精确率、召回率</template>
            </div>
          </template>
          <p v-else class="empty">{{ reason(snap.validation) }}</p>
        </section>
      </div>

      <!-- ================= 实拍样本 ================= -->
      <section class="panel panel-pad">
        <div class="panel-head">
          <h2>实拍样本实测</h2>
          <span v-if="smp" class="aside mono">命中 {{ smp.correct }}/{{ smp.labeled }} · 平均 {{ smp.avg_ms.toFixed(0) }} ms · 共 {{ smp.total_s }} s · conf {{ smp.conf }} / imgsz {{ smp.imgsz }}</span>
        </div>
        <div v-if="smp" class="samples">
          <figure v-for="it in smp.items" :key="it.no" class="sample" :class="{ miss: it.expected && !it.correct }">
            <div class="thumb"><img :src="`/api/dashboard/thumb/${it.thumb}`" :alt="it.expected_cn || it.file" loading="lazy"></div>
            <figcaption>
              <div class="sn"><b>{{ it.pred_cn || '未检出' }}</b><span class="mono">{{ it.conf ? it.conf.toFixed(2) : '' }}</span></div>
              <div class="latin">{{ it.pred ? latinName(it.pred) : '' }}</div>
              <div class="sm mono">
                <span class="verdict" :class="it.correct ? 'ok' : 'bad'"><Icon :name="it.correct ? 'check' : 'close'" :size="13" :stroke="2.4" />{{ it.expected ? (it.correct ? '命中' : `应为 ${it.expected_cn}`) : '无标注' }}</span>
                <span>{{ it.ms.toFixed(0) }} ms</span>
              </div>
            </figcaption>
          </figure>
        </div>
        <p v-else class="empty">{{ reason(snap.samples) }}</p>
        <p v-if="smp" class="src">判定口径：模型 Top1 类别 = 文件名里记录的类别。缩略图最长边 240px，原图不出现在网页上。</p>
      </section>

      <!-- ================= 软件闭环 + 评测 ================= -->
      <div class="row">
        <section class="panel panel-pad grow2">
          <div class="panel-head"><h2>软件端闭环</h2><span class="aside">已实现并实测</span></div>
          <div class="table-wrap">
            <table class="tbl">
              <thead><tr><th>需求</th><th>落点</th><th>结论</th></tr></thead>
              <tbody>
                <tr v-for="r in LOOP" :key="r[0]"><td class="strong">{{ r[0] }}</td><td class="mono small">{{ r[1] }}</td><td>{{ r[2] }}</td></tr>
              </tbody>
            </table>
          </div>
        </section>
        <section class="panel panel-pad grow1">
          <div class="panel-head"><h2>问答评测与自检</h2></div>
          <div v-if="rag" class="kv">
            <span>库内召回</span><b class="mono">{{ rag.in_domain_hit }} / {{ rag.in_domain_total }}</b>
            <span>库外拒答（不生成）</span><b class="mono">{{ rag.out_domain_refused }} / {{ rag.out_domain_total }}</b>
            <span>越权指令拦截</span><b class="mono">{{ rag.unsafe_blocked }} / {{ rag.unsafe_total }}</b>
            <span>拒答阈值</span><b class="mono">{{ rag.threshold }}</b>
            <span>知识库规模</span><b class="mono">{{ rag.kb?.chunks ?? '—' }} 片段 · {{ rag.kb?.pests ?? '—' }} 类 · {{ rag.kb?.aliases ?? '—' }} 别名</b>
          </div>
          <p v-else class="empty">{{ reason(snap.rag) }}</p>
          <div class="kv test-row">
            <span>单元测试</span>
            <b v-if="tests" class="mono" :class="tests.failed ? 'risk-high' : ''">{{ tests.passed }} 通过 · {{ tests.failed }} 失败{{ tests.skipped ? ` · ${tests.skipped} 跳过` : '' }}</b>
            <b v-else class="muted">{{ reason(snap.tests) }}</b>
          </div>
          <p class="src mono">python scripts/ask_eval.py · python tests/run_tests.py</p>
        </section>
      </div>
    </template>

    <!-- ================= 联动预案 + 实时状态（实时接口，不依赖快照） ================= -->
    <div class="row">
      <section class="panel panel-pad grow1">
        <div class="panel-head"><h2>联动预案</h2><span class="aside">config/configs.yaml · SPRAY.risk_map</span></div>
        <div class="table-wrap">
          <table class="tbl">
            <thead><tr><th>风险等级</th><th>联动动作</th></tr></thead>
            <tbody><tr v-for="p in plans" :key="p.level"><td class="strong">{{ p.level }}</td><td>{{ p.act }}</td></tr></tbody>
          </table>
        </div>
        <p class="src">拓扑：相机与手机只连服务器；喷洒节点只接受服务器指令。单次通电上限 {{ state.config?.max_spray ?? 5 }} 秒。</p>
      </section>
      <section class="panel panel-pad grow2">
        <div class="panel-head"><h2>实时状态</h2><span class="aside">每 8 秒刷新</span></div>
        <div class="status-grid">
          <div class="st">
            <div class="st-h"><span class="dot" :class="state.health ? 'ok' : 'bad'" />识别模型</div>
            <div class="mono small">{{ state.health ? `${state.health.classes} 类 · cpu` : '服务器未连接' }}</div>
          </div>
          <div class="st">
            <div class="st-h"><span class="dot" :class="camList.some((c) => c.ok) ? 'ok' : camList.length ? 'bad' : ''" />相机</div>
            <div class="mono small">{{ camList.length ? camList.map((c) => `${c.device || c.name} ${c.state}${c.fps ? ` ${c.fps}fps` : ''}`).join(' / ') : '当前没有页面在用相机' }}</div>
          </div>
          <div class="st">
            <div class="st-h"><span class="dot" :class="state.feedback?.tts?.ready ? 'ok' : 'warn'" />语音播报</div>
            <div class="mono small">{{ state.feedback?.tts?.engine || '不可用' }} · 已播 {{ state.feedback?.tts?.spoken ?? 0 }} 句</div>
          </div>
          <div class="st">
            <div class="st-h"><span class="dot" :class="(state.spray?.nodes?.length || 0) && nodesOnline === state.spray?.nodes?.length ? 'ok' : nodesOnline ? 'warn' : 'bad'" />喷洒节点</div>
            <div class="mono small">{{ state.spray?.transport?.toUpperCase() }} · {{ nodesOnline }}/{{ state.spray?.nodes?.length || 0 }} 在线</div>
            <div v-for="n in state.spray?.nodes || []" :key="n.group" class="mono tiny">{{ n.name || n.group }} {{ n.online ? `${n.latency_ms} ms` : '离线' }}</div>
          </div>
        </div>
      </section>
    </div>
  </main>
</template>

<style scoped>
.page { max-width: 1440px; margin: 0 auto; padding: 24px 32px 48px; display: flex; flex-direction: column; gap: 24px; }
.head { display: flex; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
.head h1 { font-size: 22px; font-weight: 700; }
.head .sub { font-size: 12.5px; }
.head .btn { margin-left: auto; }
.missing pre { margin: 12px 0; padding: 12px; background: var(--inset); border-radius: var(--r); overflow-x: auto; font-size: 12.5px; }
.missing p { font-size: 13.5px; }

.kpis { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); }
.kpi { padding: 16px 18px 18px; border-left: 1px solid var(--line); min-width: 0; }
.kpi:first-child { border-left: 0; }
.kpi .k { font-size: 12px; color: var(--ink-3); }
.kpi .v { font-size: 38px; margin: 8px 0 6px; }
.kpi .v small { font-size: 18px; color: var(--ink-2); }
.kpi .s { font-size: 12px; color: var(--ink-2); line-height: 1.5; overflow-wrap: anywhere; }
@media (max-width: 1180px) {
  .kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .kpi:nth-child(4) { border-left: 0; }
  .kpi:nth-child(n + 4) { border-top: 1px solid var(--line); }
}
@media (max-width: 640px) {
  .page { padding: 16px; gap: 16px; }
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .kpi:nth-child(n) { border-left: 1px solid var(--line); border-top: 1px solid var(--line); }
  .kpi:nth-child(odd) { border-left: 0; }
  .kpi:nth-child(-n + 2) { border-top: 0; }
}

.row { display: flex; flex-wrap: wrap; gap: 24px; align-items: stretch; }
.grow2 { flex: 999 1 560px; min-width: 0; }
.grow1 { flex: 1 1 380px; min-width: 0; }
@media (min-width: 1100px) { .grow1 { max-width: 480px; } }

.tabs { display: inline-flex; gap: 2px; padding: 3px; background: var(--inset); border-radius: var(--r-lg); margin-bottom: 12px; flex-wrap: wrap; }
.tabs button { min-height: 32px; padding: 0 12px; border: 0; border-radius: var(--r); background: transparent; color: var(--ink-2); cursor: pointer; font-size: 12.5px; }
.tabs button.on { background: var(--panel); color: var(--ink); font-weight: 700; box-shadow: 0 1px 2px rgba(22, 32, 26, 0.12); }
.tabs.small { margin-bottom: 0; }
.src { margin-top: 10px; font-size: 11.5px; color: var(--ink-3); overflow-wrap: anywhere; }

.cls-tools { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.chk { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink-2); cursor: pointer; }
.chk input { accent-color: var(--brand); }
.cls-list { max-height: 344px; overflow-y: auto; margin: 0 -6px; padding: 0 6px; }
.cls { display: grid; grid-template-columns: 22px minmax(0, 1.1fr) minmax(60px, 1fr) 46px; align-items: center; gap: 10px; padding: 5px 4px; border-radius: var(--r-sm); }
.cls.on { background: var(--inset); }
.rank { font-size: 11px; color: var(--ink-3); text-align: right; }
.cname { display: flex; flex-direction: column; min-width: 0; line-height: 1.3; }
.cname b { font-family: var(--font-serif); font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cname .latin { font-size: 10.5px; color: var(--ink-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bar { position: relative; height: 10px; background: var(--inset); border-radius: 0 4px 4px 0; }
.bar > span { display: block; height: 100%; background: var(--line-strong); border-radius: 0 4px 4px 0; }
.bar > span.top { background: var(--brand); }
.mark95 { position: absolute; left: 95%; top: -3px; width: 1px; height: 16px; background: var(--ink-2); }
.val { font-size: 12px; text-align: right; }
.cls-foot { margin-top: 10px; font-size: 11.5px; color: var(--ink-3); min-height: 18px; }

.samples { display: grid; grid-template-columns: repeat(auto-fill, minmax(124px, 1fr)); gap: 14px; }
.sample { margin: 0; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.thumb { aspect-ratio: 4 / 3; overflow: hidden; background: var(--inset); }
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.sample.miss .thumb { outline: 2px solid var(--risk-high); outline-offset: 2px; }
.sn { display: flex; justify-content: space-between; gap: 6px; font-size: 13px; }
.sn b { font-family: var(--font-serif); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sn .mono { font-size: 12px; color: var(--ink-2); }
.sample .latin { font-size: 10.5px; color: var(--ink-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sm { display: flex; justify-content: space-between; gap: 6px; font-size: 11.5px; color: var(--ink-3); }
.verdict { display: inline-flex; align-items: center; gap: 3px; font-family: var(--font-cn); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.verdict.ok { color: var(--risk-low); }
.verdict.bad { color: var(--risk-high); }

.table-wrap { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; font-weight: 400; font-size: 12px; color: var(--ink-3); padding: 0 12px 8px 0; white-space: nowrap; }
.tbl td { padding: 9px 12px 9px 0; border-top: 1px solid var(--inset); vertical-align: top; }
.strong { font-weight: 700; white-space: nowrap; }
.small { font-size: 12px; }
.tiny { font-size: 11.5px; color: var(--ink-3); }
.kv { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 9px 16px; font-size: 13px; }
.kv > span { color: var(--ink-3); }
.kv > b { font-weight: 500; text-align: right; }
.test-row { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
.status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px 20px; }
.st-h { display: flex; align-items: center; gap: 7px; font-weight: 700; font-size: 13px; margin-bottom: 4px; }
.st .small { overflow-wrap: anywhere; }
</style>
