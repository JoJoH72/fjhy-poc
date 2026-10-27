<script setup lang="ts">
/**
 * 系统 · 运行监控（2026-10-27 AntD 换装版）。五段：运行拓扑 / 契约 / 协同与信号 / 运行 / 存储。
 *
 * 做它的理由：全站 13 个处理环节散在三处——契约在 config/agents/*.yaml、
 * 运行时状态在 agent_state、运行痕迹在 agent_signal_log 与 cycle_log，
 * 而编排分散在四条链上。**结果是没有一个地方能回答「现在有几个环节、各自什么状态」。**
 *
 * **这一页只读。** 不改参数、不投信号、不停环节：
 * 改参数要走 improve 的 bounds 校验与 improve_log，不能从页面绕过去；
 * 手工投信号会产生一条「没有上游」的协同记录，审计时说不清是谁触发的。
 * 能启动的只有那三条链，而且启动的是**同一个编排**（app/agent_job.py）。
 *
 * 页面上三条口径必须写出来，不写就是在说谎：
 *   · 契约 13 个，agent_state 也 13 行——两种编排方式的差别写在 facts 里
 *   · 心跳不适用——**空不等于坏，等于没开**（没有常驻调度器在跑）
 *   · 三条手工链的信号 2026-10-19 之前一条都没落库（实测的 bug，已修）
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { pro } from '../data/prose'
import { api } from '../api'
import type { AgentChain, AgentDetail, AgentOverview, AgentRow, StorageRow } from '../api'

type Tab = 'all' | 'contract' | 'bus' | 'run' | 'store'
const tab = ref<Tab>('all')

const ov = ref<AgentOverview | null>(null)
const det = ref<AgentDetail | null>(null)
const pick = ref('')
const drawerOpen = ref(false)
const checks = ref<{ items: { check: string; ok: boolean; detail: string }[]
                     total: number; failed: number; note: string } | null>(null)
const showAllChecks = ref(false)
const showYaml = ref(false)
const sig = ref<Awaited<ReturnType<typeof api.agentSignals>> | null>(null)
const chains = ref<AgentChain[]>([])
const chainNote = ref('')
const store = ref<{ items: StorageRow[]; note: string } | null>(null)

const busy = ref('')
const err = ref('')
const msg = ref('')
let timer: number | undefined

async function run(tag: string, fn: () => Promise<void>) {
  busy.value = tag
  err.value = ''
  try {
    await fn()
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = ''
  }
}

const loadOv = () => run('ov', async () => { ov.value = await api.agentOverview() })
const loadChains = () =>
  run('ch', async () => {
    const r = await api.agentChains()
    chains.value = r.items
    chainNote.value = r.note
  })

/** 取某个环节的契约详情（抽屉与契约页签共用一份，避免重复请求） */
async function loadDetail(id: string) {
  pick.value = id
  showYaml.value = false
  if (det.value?.agent_id !== id) det.value = await api.agentDetail(id)
}

const openDrawer = (id: string) => run('d', async () => {
  drawerOpen.value = true
  await loadDetail(id)
})

/** 契约页签里选环节 */
const selectAgent = (id: string) =>
  run('d', async () => {
    drawerOpen.value = false
    await loadDetail(id)
    tab.value = 'contract'
  })

function pickTab(t: Tab) {
  tab.value = t
  drawerOpen.value = false
  if (t === 'all' && !ov.value) loadOv()
  if (t === 'contract' && !det.value && ov.value?.items.length)
    run('d', () => loadDetail(ov.value!.items[0].agent_id))
  if (t === 'bus' && !sig.value) {
    run('s', async () => { sig.value = await api.agentSignals(50) })
  }
  if (t === 'run' && !chains.value.length) loadChains()
  if (t === 'store' && !store.value)
    run('st', async () => { store.value = await api.agentStorage() })
}

const loadChecks = () =>
  run('k', async () => { checks.value = await api.agentChecks() })

/** 轮询：启动一条链之后盯着它，跑完自动停。 */
function poll(chainId: string) {
  stop()
  timer = window.setInterval(async () => {
    try {
      const p = await api.agentChainProgress(chainId)
      const row = chains.value.find((c) => c.chain_id === chainId)
      if (row) {
        row.status = p.status
        row.pct = p.pct
        row.job_id = p.job_id
        row.elapsed = p.elapsed
        row.signals_in_job = (p.signals || []).length
        row.error = p.error
      }
      if (p.status !== 'running') {
        stop()
        await loadChains()
      }
    } catch {
      // 轮询失败不清空表格：清空会让页面在网络抖一下时整个塌掉
      stop()
    }
  }, 1000)
}
function stop() {
  if (timer !== undefined) {
    window.clearInterval(timer)
    timer = undefined
  }
}

const startChain = (c: AgentChain) =>
  run('go', async () => {
    const r = await api.agentChainRun(c.chain_id)
    msg.value = r.ok ? `已启动「${c.chain_name}」 ${r.job_id}` : (r.reason || '已有作业在运行')
    if (r.ok) { message.success(msg.value); poll(c.chain_id) }
    else message.warning(msg.value)
  })

const LAYER_CN: Record<string, string> = { L0: '感知层', L1: '执行层', L2: '协同层' }
const LAYERS = ['L0', 'L1', 'L2'] as const
const ST_CN: Record<string, string> = {
  idle: '未开始', running: '运行中', done: '已完成', failed: '失败',
}
const ST_COLOR: Record<string, string> = {
  idle: 'default', running: 'processing', done: 'success', failed: 'error',
}
/** 链名在节点上要短：卡片只有 158px 宽 */
const CHAIN_SHORT: Record<string, string> = {
  review: '审核链', extract: '抽取链', onto_align: '规则本体链', onto_build: '业务本体链',
}
const TRIG_CN: Record<string, string> = {
  subscribe: '订阅信号', heartbeat: '心跳', interval: '定时', event: '事件', message: '消息投递',
}

const drawerId = computed(() => (drawerOpen.value ? pick.value : ''))
const triple = computed(() => ov.value?.items.filter((x) => x.no_judgement) || [])
const cur = computed(() => ov.value?.items.find((x) => x.agent_id === drawerId.value) || null)

/**
 * 「活着」= 24 小时内跑过周期。
 * 不能拿「有 agent_state 行」当运行中：那样 13 个全是运行中，
 * 一个都没在跑的页面反而自称 13/13，比不显示更误导。
 */
function live(a: AgentRow | null): boolean {
  if (!a?.last_cycle_at || !a.cycles) return false
  const t = Date.parse(a.last_cycle_at)
  return Number.isFinite(t) && Date.now() - t < 24 * 3600 * 1000
}
function liveTitle(a: AgentRow): string {
  return a.last_cycle_at
    ? `最近周期 ${a.last_cycle_at.replace('T', ' ').slice(0, 19)}`
    : '还没有跑过周期'
}

/** 拓扑指标卡：口径全部来自 /api/agents 现有字段，不拼后端没有的指标 */
const stats = computed(() => {
  const items = ov.value?.items || []
  const by = ov.value?.totals.by_layer || {}
  return {
    total: items.length,
    l0: by.L0 || 0, l1: by.L1 || 0, l2: by.L2 || 0,
    cycles: items.reduce((s, x) => s + (x.cycles || 0), 0),
    signals: items.reduce((s, x) => s + (x.signals || 0), 0),
    checks: ov.value?.totals.checks ?? 0,
    failed: ov.value?.totals.failed ?? 0,
    live: items.filter(live).length,
  }
})

function agentsOf(lay: string) {
  return (ov.value?.items || []).filter((x) => x.layer === lay)
}
function chainShort(a: AgentRow) {
  return CHAIN_SHORT[a.chain_id || ''] || a.chain_name || '—'
}
/** 「素材感知（源头、规则资产装载）」→ 主名；括号补充只留在 title 里 */
function nameMain(s: string) {
  const i = s.indexOf('（')
  return i > 0 ? s.slice(0, i) : s
}
/** 抽屉里的触发方式：只列启用的——「message 未启用」占位行没有信息量 */
function triggerRows(d: AgentDetail) {
  return Object.entries(d.triggers)
    .map(([k, v]) => {
      const o = (v || {}) as Record<string, unknown>
      const ch = (o.channels as string[] | undefined) || []
      const iv = o.interval_seconds as number | undefined
      return {
        key: k,
        label: TRIG_CN[k] || k,
        detail: ch.length ? ch.join('、') : iv ? `每 ${iv} 秒` : '',
        enabled: !!o.enabled,
      }
    })
    .filter((t) => t.enabled)
}
const fmt = (s?: string | null) => (s ? s.replace('T', ' ').slice(0, 19) : '—')
/** 写库台账 map → 「表 ×行数」，直接甩 JSON 在表格里没人愿意读 */
function writtenText(w: Record<string, number> | null) {
  if (!w) return '—'
  const parts = Object.entries(w).map(([t, n]) => (n ? `${t} ×${n}` : t))
  return parts.length ? parts.join('、') : '—'
}

/** 契约页签触发方式表数据源 */
function trigRows(d: AgentDetail) {
  return Object.entries(d.triggers).map(([k, t]) => ({
    k, label: TRIG_CN[k] || k,
    enabled: !!t.enabled,
    detail: (t.channels as string[] | undefined)?.join('、')
      || (t.interval_seconds ? t.interval_seconds + ' 秒' : '—'),
    why: (t.why as string | undefined) || '',
  }))
}

onMounted(() => {
  loadOv()
})
onBeforeUnmount(() => {
  stop()
})

/** 行键：数据源无稳定业务键时按记录对象发号（单参，AntD4 弃用双参 rowKey） */
const rowSeq = new WeakMap<object, string>()
let rowSeqN = 0
const rowKeyOf = (r: unknown): string => {
  if (r && typeof r === 'object') {
    let k = rowSeq.get(r)
    if (!k) { k = 'r' + ++rowSeqN; rowSeq.set(r as object, k) }
    return k
  }
  return String(r)
}
</script>

<template>
  <div class="ah">
  <a-card size="small">
    <a-tabs v-model:active-key="tab" @change="(k: any) => pickTab(k as Tab)">
      <template #rightExtra>
        <a-tag v-if="ov" :color="ov.totals.failed ? 'error' : 'success'">
          契约校验 {{ ov.totals.checks }} 项 · 失败 {{ ov.totals.failed }}
        </a-tag>
        <a-button size="small" :loading="busy === 'ov'" :disabled="!!busy && busy !== 'ov'" style="margin-left: 8px" @click="loadOv">刷新</a-button>
      </template>

      <!-- ① 运行拓扑 -->
      <a-tab-pane key="all" tab="运行拓扑">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <template v-if="ov">
          <a-row :gutter="[16, 16]">
            <a-col :xs="12" :md="6"><a-card size="small" class="kpi">
              <a-statistic title="运行中（近 24 小时）" :value="stats.live">
                <template #suffix><span class="sfx">/ {{ stats.total }}</span></template>
              </a-statistic>
              <div class="sub">感知 {{ stats.l0 }} · 执行 {{ stats.l1 }} · 协同 {{ stats.l2 }}</div>
            </a-card></a-col>
            <a-col :xs="12" :md="6"><a-card size="small" class="kpi">
              <a-statistic title="周期累计" :value="stats.cycles" />
              <div class="sub">四条编排链合计</div>
            </a-card></a-col>
            <a-col :xs="12" :md="6"><a-card size="small" class="kpi">
              <a-statistic title="信号累计" :value="stats.signals" />
              <div class="sub">已落信号台账</div>
            </a-card></a-col>
            <a-col :xs="12" :md="6"><a-card size="small" class="kpi">
              <a-statistic title="契约校验" :value="stats.checks" suffix="项"
                :value-style="{ color: stats.failed ? '#ff4d4f' : undefined }" />
              <div class="sub">{{ stats.failed ? `失败 ${stats.failed} 项` : '全部通过' }}</div>
            </a-card></a-col>
          </a-row>

          <a-card size="small" style="margin-top: 16px" title="运行拓扑">
            <template #extra>
              <span class="lg"><i class="dg run"></i>24 小时内有周期</span>
              <span class="lg"><i class="dg idle"></i>待命</span>
            </template>
            <div class="arch">
              <template v-for="lay in LAYERS" :key="lay">
                <div class="lane">
                  <div class="lane-head"><span class="lt">{{ LAYER_CN[lay] }}</span></div>
                  <div class="lane-body">
                    <button v-for="a in agentsOf(lay)" :key="a.agent_id" class="node"
                            :aria-label="`${a.agent_name} 详情`"
                            @click="openDrawer(a.agent_id)">
                      <div class="top">
                        <span class="chain" :class="{ k2: a.chain_id === 'extract' }"
                              :title="a.chain_name || ''">{{ chainShort(a) }}</span>
                        <span class="dot" :class="live(a) ? 'run' : 'idle'" :title="liveTitle(a)"></span>
                      </div>
                      <div class="nm" :title="a.agent_name">{{ nameMain(a.agent_name) }}</div>
                      <div class="ds" :title="a.desc || ''">{{ a.desc || '—' }}</div>
                      <div class="io">
                        <span>周期 <b>{{ a.cycles || 0 }}</b></span>
                        <span>信号 <b>{{ a.signals || 0 }}</b></span>
                      </div>
                    </button>
                  </div>
                </div>
                <div v-if="lay !== 'L2'" class="flow">
                  <span class="wire"></span>
                  <span class="sig">{{ lay === 'L0' ? '任务分发' : '产出移交' }}</span>
                  <span class="wire"></span>
                </div>
              </template>
            </div>
            <div class="arch-foot">
              <span>
                共 <b>{{ stats.total }}</b> 个处理环节 · <b>{{ stats.live }}</b> 个近 24 小时有周期
              </span>
              <span v-if="triple.length" class="foot-two">
                声明不下结论的 {{ triple.length }} 个：
                <a-button v-for="a in triple" :key="a.agent_id" size="small" style="margin-right: 4px"
                          @click="openDrawer(a.agent_id)">{{ nameMain(a.agent_name) }}</a-button>
              </span>
            </div>
          </a-card>
        </template>
      </a-tab-pane>

      <!-- ② 契约 -->
      <a-tab-pane key="contract" tab="契约">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <a-card size="small" class="blk" title="选择处理环节">
          <template #extra><span class="dim">按层列出，共 {{ ov?.items.length || 0 }} 个</span></template>
          <div class="pickgroups">
            <div v-for="lay in LAYERS" :key="lay" class="pick-group">
              <div class="pick-lt">{{ LAYER_CN[lay] }}</div>
              <div class="chips">
                <a-button v-for="a in agentsOf(lay)" :key="a.agent_id"
                          :type="pick === a.agent_id ? 'primary' : 'default'" size="small"
                          style="margin: 0 6px 6px 0"
                          @click="selectAgent(a.agent_id)">{{ nameMain(a.agent_name) }}</a-button>
              </div>
            </div>
          </div>
        </a-card>

        <template v-if="det">
          <a-card size="small" class="blk">
            <template #title>{{ det.agent_name }}</template>
            <template #extra>
              <a-tag color="blue">{{ LAYER_CN[det.layer] || det.layer }}</a-tag>
              <span class="dim" style="margin-left: 8px">{{ det.agent_id }} · v{{ det.agent_version }} · {{ det.chain_name || '—' }}</span>
            </template>
            <div class="kvg">
              <div><span class="k">职责</span><span class="v" style="white-space:normal">{{ det.desc || '—' }}</span></div>
              <div><span class="k">执行策略</span><span class="v">{{ det.execution_strategy }}</span></div>
              <div><span class="k">运行时参数来源</span><span class="v">{{ det.state.params_from }}</span></div>
              <div><span class="k">上次心跳</span>
                <span class="v">{{ det.state.last_beat_at ? fmt(det.state.last_beat_at) : '不适用' }}</span>
              </div>
            </div>
          </a-card>

          <a-card size="small" class="blk" title="触发方式">
            <a-table :data-source="trigRows(det)" row-key="k" size="small" :pagination="false"
              :columns="[
                { title: '类型', key: 'label', width: 170 },
                { title: '启用', key: 'on', width: 80, align: 'center' },
                { title: '通道 / 间隔', dataIndex: 'detail', width: 200 },
                { title: '为什么这么设', key: 'why' },
              ]">
              <template #bodyCell="{ column, record: t }">
                <template v-if="column.key === 'label'">
                  {{ t.label }} <span class="dim">{{ t.k }}</span>
                </template>
                <template v-else-if="column.key === 'on'">
                  <a-tag :color="t.enabled ? 'success' : 'default'">{{ t.enabled ? '开' : '关' }}</a-tag>
                </template>
                <template v-else-if="column.key === 'why'">
                  <span class="dim"><MarkText :text="t.why" /></span>
                </template>
              </template>
            </a-table>
          </a-card>

          <a-card size="small" class="blk">
            <template #title>S → D → A → F → I</template>
            <template #extra><span class="dim">读什么、怎么判、写哪里、量什么、能调什么</span></template>
            <div class="sdafi">
              <div class="sblk">
                <b>S 读</b>
                <div class="dim">只允许读这些表</div>
                <div><code v-for="t in det.sdafi.S.tables" :key="t" class="chip">{{ t }}</code></div>
                <pre v-if="det.sdafi.S.select" class="pre">{{ det.sdafi.S.select }}</pre>
              </div>
              <div class="sblk">
                <b>D 判</b>
                <div v-if="det.sdafi.D.no_judgement"><a-tag color="default">声明不下结论</a-tag></div>
                <div class="dim">{{ det.sdafi.D.kind }}</div>
                <div><code v-for="v in det.sdafi.D.conclusion_values" :key="v" class="chip">{{ v }}</code></div>
                <pre v-if="det.sdafi.D.pending_why" class="pre">判不出时:{{ det.sdafi.D.pending_counts_as }}
{{ det.sdafi.D.pending_why }}</pre>
                <pre v-if="det.sdafi.D.no_judgement_why" class="pre">{{ det.sdafi.D.no_judgement_why }}</pre>
              </div>
              <div class="sblk">
                <b>A 写</b>
                <div class="dim"><b>唯一允许写库的阶段</b>，且只能写这些表</div>
                <div><code v-for="t in det.sdafi.A.writes" :key="t" class="chip">{{ t }}</code></div>
                <div v-if="det.sdafi.A.signal.channel" style="font-size: 12px; margin-top: 4px">
                  投信号 → <code>{{ det.sdafi.A.signal.channel }}</code>
                </div>
                <pre v-if="det.sdafi.A.idempotency" class="pre">幂等键 {{ det.sdafi.A.idempotency }}
{{ det.sdafi.A.idempotency_why }}</pre>
              </div>
              <div class="sblk">
                <b>F 量</b>
                <table v-if="det.sdafi.F.metrics.length" class="mini-tbl">
                  <tbody>
                    <tr v-for="m in det.sdafi.F.metrics" :key="m.metric_name">
                      <td class="code">{{ m.metric_name }}</td>
                      <td>{{ m.desc }}</td>
                      <td class="dim code">{{ m.source }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div class="sblk">
                <b>I 调</b>
                <table v-if="det.sdafi.I.params.length" class="mini-tbl">
                  <thead><tr><th>参数</th><th>默认</th><th>区间</th><th>为什么</th></tr></thead>
                  <tbody>
                    <tr v-for="p in det.sdafi.I.params" :key="p.param_name">
                      <td class="code">{{ p.param_name }}</td>
                      <td class="num">{{ p.default }}</td>
                      <td class="code">[{{ p.lo }}, {{ p.hi }}]</td>
                      <td><MarkText :text="p.why" /></td>
                    </tr>
                  </tbody>
                </table>
                <div style="margin-top: 6px">
                  <b style="color: #ff4d4f">禁止自调（{{ det.sdafi.I.forbidden.length }}）</b>
                  <div v-for="f in det.sdafi.I.forbidden" :key="f" style="font-size: 12px">· {{ f }}</div>
                </div>
              </div>
            </div>
            <div class="blk-note"><MarkText :text="det.note" /></div>
          </a-card>

          <a-card size="small" class="blk" title="契约原文">
            <template #extra>
              <span class="dim">{{ det.contract_file.lines }} 行 · 注释 {{ det.contract_file.comment_lines }} 行 · {{ det.contract_file.file }}</span>
              <a-button size="small" style="margin-left: 8px" @click="showYaml = !showYaml">
                {{ showYaml ? '收起原文' : '看原文' }}
              </a-button>
            </template>
            <pre v-if="showYaml" class="yaml">{{ det.contract_file.text }}</pre>
          </a-card>
        </template>

        <a-card size="small" title="契约校验">
          <template #extra>
            <span v-if="checks" class="dim">
              {{ checks.total }} 项，失败
              <b :style="{ color: checks.failed ? '#ff4d4f' : '#52c41a' }">{{ checks.failed }}</b>
            </span>
            <a-button v-if="checks" size="small" style="margin-left: 8px" @click="showAllChecks = !showAllChecks">
              {{ showAllChecks ? '只看失败' : '看全部' }}
            </a-button>
            <a-button size="small" :loading="busy === 'k'" :disabled="!!busy && busy !== 'k'" style="margin-left: 4px" @click="loadChecks">现跑一遍</a-button>
          </template>
          <div v-if="checks">
            <a-alert v-if="!showAllChecks && !checks.failed" type="success" show-icon style="margin-bottom: 12px"
              :message="`${checks.total} 项全部通过`" />
            <a-table v-else :data-source="checks.items.filter((x) => showAllChecks || !x.ok)"
              :row-key="rowKeyOf" size="small" :pagination="false"
              :columns="[
                { title: '结果', key: 'ok', width: 80, align: 'center' },
                { title: '检查项', dataIndex: 'check', width: 340 },
                { title: '说明', key: 'detail' },
              ]">
              <template #bodyCell="{ column, record: k }">
                <template v-if="column.key === 'ok'">
                  <a-tag :color="k.ok ? 'success' : 'error'">{{ k.ok ? '通过' : '失败' }}</a-tag>
                </template>
                <template v-else-if="column.key === 'detail'">
                  <span class="dim"><MarkText :text="k.detail" /></span>
                </template>
              </template>
            </a-table>
            <div class="blk-note"><MarkText :text="checks.note" /></div>
          </div>
        </a-card>
      </a-tab-pane>

      <!-- ③ 协同与信号 -->
      <a-tab-pane key="bus" tab="协同与信号">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <template v-if="sig">
          <a-card size="small" class="blk" title="数据池">
            <template #extra><span class="dim">上游写入 · 发信号 · 下游读取</span></template>
            <div class="kvlist">
              <div v-for="f in sig.facts" :key="f.fact" class="kvrow">
                <b>{{ f.fact }}</b><span class="dim"><MarkText :text="f.why" /></span>
              </div>
            </div>
            <a-table v-if="sig.pool.length" :data-source="sig.pool"
              :row-key="rowKeyOf" size="small" :pagination="false"
              :columns="[
                { title: '通道', dataIndex: 'channel', width: 260 },
                { title: '状态', key: 'st', width: 100, align: 'center' },
                { title: '条数', dataIndex: 'n', width: 80, align: 'right' },
                { title: '最近一条', key: 'last' },
              ]">
              <template #bodyCell="{ column, record: p }">
                <template v-if="column.key === 'st'">
                  <a-tag :color="p.status === 'pending' ? 'warning' : 'success'">{{ p.status }}</a-tag>
                </template>
                <template v-else-if="column.key === 'last'">
                  <span class="dim">{{ p.last?.toString().slice(0, 19) }}</span>
                </template>
              </template>
            </a-table>
          </a-card>

          <a-card size="small" class="blk" title="谁投过信号">
            <a-table :data-source="sig.by_agent" row-key="agent_id" size="small"
              :pagination="sig.by_agent.length > 10 ? { pageSize: 10 } : false"
              :columns="[
                { title: '处理环节', dataIndex: 'agent_id', width: 300 },
                { title: '条数', dataIndex: 'n', width: 80, align: 'right' },
                { title: '第一条', key: 'first', width: 170 },
                { title: '最近一条', key: 'last2', width: 170 },
              ]">
              <template #bodyCell="{ column, record: a }">
                <template v-if="column.key === 'first'"><span class="dim">{{ a.first?.toString().slice(0, 19) }}</span></template>
                <template v-else-if="column.key === 'last2'"><span class="dim">{{ a.last?.toString().slice(0, 19) }}</span></template>
              </template>
            </a-table>
          </a-card>

          <a-card size="small" title="信号台账">
            <template #extra><span class="dim">最近 {{ sig.items.length }} 条</span></template>
            <a-table :data-source="sig.items" row-key="id" size="small"
              :pagination="sig.items.length > 10 ? { pageSize: 10 } : false"
              :columns="[
                { title: '时间', key: 'at', width: 150 },
                { title: '环节', dataIndex: 'agent_id', width: 210, ellipsis: true },
                { title: '动作', dataIndex: 'action', width: 110 },
                { title: '目标', dataIndex: 'target', width: 190, ellipsis: true },
                { title: '写了哪些表', key: 'wt', width: 170, ellipsis: true },
                { title: '幂等键', dataIndex: 'idempotency_key', ellipsis: true },
              ]">
              <template #bodyCell="{ column, record: s }">
                <template v-if="column.key === 'at'">
                  <span class="dim">{{ s.emitted_at.toString().slice(5, 19) }}</span>
                </template>
                <template v-else-if="column.key === 'wt'">
                  <span class="dim" :title="writtenText(s.written_tables)">{{ writtenText(s.written_tables) }}</span>
                </template>
              </template>
            </a-table>
          </a-card>
        </template>
      </a-tab-pane>

      <!-- ④ 运行 -->
      <a-tab-pane key="run" tab="运行">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <div class="blk-note"><MarkText :text="chainNote" /></div>
        <a-card v-for="c in chains" :key="c.chain_id" size="small" class="blk">
          <template #title>{{ c.chain_name }}</template>
          <template #extra>
            <a-tag :color="c.kind === '常驻编排' ? 'success' : 'default'">{{ c.kind }}</a-tag>
            <a-tag v-if="c.status" :color="ST_COLOR[c.status] || 'default'">{{ ST_CN[c.status] || c.status }}</a-tag>
            <span v-if="c.pct !== undefined && c.status === 'running'" class="dim" style="margin-left: 6px">{{ c.pct }}%</span>
            <a size="small" style="margin-left: 8px" :href="c.page">去那一栏 →</a>
            <a-button v-if="c.kind !== '常驻编排'" size="small" style="margin-left: 4px"
                      :loading="busy === 'go'" :disabled="!!busy && busy !== 'go' || c.status === 'running'"
                      @click="startChain(c)">
              {{ c.status === 'running' ? '运行中…' : '在这里启动' }}
            </a-button>
            <span v-else class="dim" style="margin-left: 6px">常驻，本页不启停</span>
          </template>
          <div class="blk-note"><MarkText :text="c.note" /></div>
          <div class="chainflow">
            <template v-for="(a, i) in c.agents" :key="a.agent_id">
              <span v-if="i" class="arrow">→</span>
              <a-button size="small" @click="selectAgent(a.agent_id)">
                <span class="dim">{{ a.layer }}</span> {{ nameMain(a.agent_name) }}
              </a-button>
            </template>
          </div>
          <div class="dim" style="margin-top: 10px">
            <span v-if="c.signals !== undefined">台账 {{ c.signals }} 条信号</span>
            <span v-if="c.signals_in_job !== undefined">（本次作业 {{ c.signals_in_job }} 条）</span>
            <span v-if="c.elapsed"> · 上次用时 {{ c.elapsed }} 秒</span>
            <span v-if="c.job_id"> · 作业 {{ c.job_id }}</span>
          </div>
          <a-alert v-if="c.error" type="error" show-icon :message="c.error" style="margin-top: 8px" />
          <a-alert v-if="c.log_errors && c.log_errors.length" type="error" show-icon style="margin-top: 8px">
            <template #message>
              <b>信号落库失败 {{ c.log_errors.length }} 条</b>（链照常跑完，但台账缺了行）：
              <div v-for="(e, i) in c.log_errors" :key="i" style="font-size: 12px">
                {{ e.from }} → {{ e.channel }}：{{ e.error }}
              </div>
            </template>
          </a-alert>
        </a-card>
      </a-tab-pane>

      <!-- ⑤ 存储 -->
      <a-tab-pane key="store" tab="存储">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <a-card v-if="store" size="small" title="存储">
          <template #extra><span class="dim">{{ store.items.length }} 张表 / 文件</span></template>
          <a-table :data-source="store.items" row-key="table_name" size="small"
            :pagination="store.items.length > 10 ? { pageSize: 10 } : false"
            :columns="[
              { title: '表 / 文件', dataIndex: 'table_name', width: 210 },
              { title: '行数', key: 'rows', width: 70, align: 'right' },
              { title: '存的是什么', key: 'what', width: 230 },
              { title: '谁写的', key: 'by', width: 160 },
              { title: '重算时', dataIndex: 'on_recalc', width: 130 },
              { title: '为什么这么存', key: 'why' },
            ]">
            <template #bodyCell="{ column, record: t }">
              <template v-if="column.key === 'rows'">{{ t.rows ?? '—' }}</template>
              <template v-else-if="column.key === 'what'"><span class="dim"><MarkText :text="pro(t.what)" /></span></template>
              <template v-else-if="column.key === 'by'"><span class="dim">{{ pro(t.written_by) }}</span></template>
              <template v-else-if="column.key === 'why'"><span class="dim"><MarkText :text="pro(t.why)" /></span></template>
            </template>
          </a-table>
          <div class="blk-note"><MarkText :text="store.note" /></div>
        </a-card>
      </a-tab-pane>
    </a-tabs>
  </a-card>

  <!-- 节点详情抽屉 -->
  <a-drawer v-model:open="drawerOpen" width="440" :title="cur?.agent_name || '处理环节详情'">
    <div class="dr-sub">
      {{ LAYER_CN[cur?.layer || ''] || cur?.layer }} · {{ cur?.chain_name || '—' }} · v{{ cur?.agent_version }}
    </div>
    <div class="dr-sec">
      <div class="dr-k">职责</div>
      <div>{{ cur?.desc || '—' }}</div>
    </div>

    <div class="dr-metrics">
      <div><b>{{ cur?.cycles || 0 }}</b><span>周期累计</span></div>
      <div><b>{{ cur?.signals || 0 }}</b><span>信号累计</span></div>
      <div><b>{{ cur?.reads.length || 0 }}</b><span>可读表</span></div>
      <div><b>{{ cur?.writes.length || 0 }}</b><span>可写表</span></div>
    </div>

    <div v-if="!det" class="dim">读取契约…</div>
    <template v-else>
      <div class="dr-sec">
        <div class="dr-k">触发方式</div>
        <div class="dr-tags">
          <a-tag v-for="t in triggerRows(det)" :key="t.key" :color="t.enabled ? 'success' : 'default'">
            {{ t.label }}<em v-if="t.detail" style="font-style: normal; opacity: .8"> · {{ t.detail }}</em>
          </a-tag>
        </div>
      </div>

      <div v-if="det.sdafi.I.params.length" class="dr-sec">
        <div class="dr-k">可调参数（改参数要走改进流程，本页只读）</div>
        <a-table :data-source="det.sdafi.I.params" row-key="param_name" size="small" :pagination="false"
          :columns="[
            { title: '参数', dataIndex: 'param_name' },
            { title: '默认', dataIndex: 'default', width: 70, align: 'right' },
            { title: '区间', key: 'range', width: 110 },
          ]">
          <template #bodyCell="{ column, record: p }">
            <template v-if="column.key === 'range'">[{{ p.lo }}, {{ p.hi }}]</template>
          </template>
        </a-table>
      </div>

      <div v-if="det.sdafi.I.forbidden.length" class="dr-sec">
        <div class="dr-k">禁止自调</div>
        <div class="dr-tags">
          <a-tag v-for="f in det.sdafi.I.forbidden" :key="f" color="error">{{ f }}</a-tag>
        </div>
      </div>

      <div class="dr-sec">
        <div class="dr-k">最近周期</div>
        <div class="code">{{ fmt(cur?.last_cycle_at) }}</div>
      </div>
    </template>

    <template #footer>
      <a-button style="margin-right: 8px" @click="drawerOpen = false">关闭</a-button>
      <a-button type="primary" @click="selectAgent(pick)">查看完整契约</a-button>
    </template>
  </a-drawer>
  </div>
</template>

<style scoped>
.blk { margin-bottom: 16px; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.sub { font-size: 12px; color: rgba(0, 0, 0, .45); margin-top: 2px; }
.sfx { font-size: 12px; font-weight: normal; color: rgba(0, 0, 0, .45); margin-left: 4px; }
.kpi :deep(.ant-statistic-title) { font-size: 13px; }
.kpi :deep(.ant-statistic-content) { font-size: 22px; }
.blk-note { font-size: 12px; color: rgba(0, 0, 0, .55); margin: 8px 0 12px; line-height: 1.7; }
.kvg { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 0 32px; }
.kvg .k { display: inline-block; width: 110px; color: rgba(0, 0, 0, .45); font-size: 13px; padding: 6px 0; }
.kvg .v { font-size: 13px; }
.code { font-family: Consolas, "Courier New", monospace; font-size: 12px; }

.lg { font-size: 12px; color: rgba(0, 0, 0, .55); margin-left: 12px; }
.lg i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 4px; }
.dg.run { background: #52c41a; }
.dg.idle { background: #bfbfbf; }

/* 拓扑三层泳道（保留手绘结构——AntD 无对应组件） */
.arch { display: flex; flex-direction: column; gap: 0; }
.lane { }
.lane-head { padding: 8px 0 6px; }
.lt { font-size: 13px; font-weight: 600; color: rgba(0, 0, 0, .7); }
.lane-body { display: grid; grid-template-columns: repeat(auto-fill, minmax(158px, 1fr)); gap: 8px; }
.node {
  text-align: left; border: 1px solid #e5e7eb; border-radius: 8px; background: #fff;
  padding: 8px 10px; cursor: pointer; height: auto; min-height: 96px;
  display: flex; flex-direction: column; gap: 3px; font-size: 13px;
}
.node:hover { border-color: #91caff; background: #f6fffd; }
.node .top { display: flex; align-items: center; justify-content: space-between; }
.node .chain { font-size: 11px; color: #1677ff; font-weight: 500; }
.node .chain.k2 { color: #d46b08; }
.node .dot { width: 8px; height: 8px; border-radius: 50%; }
.node .dot.run { background: #52c41a; }
.node .dot.idle { background: #d9d9d9; }
.node .nm { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.node .ds { font-size: 11px; color: rgba(0, 0, 0, .45); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.node .io { display: flex; gap: 10px; font-size: 11px; color: rgba(0, 0, 0, .55); margin-top: auto; }
.flow { display: flex; align-items: center; gap: 8px; padding: 6px 24px; }
.flow .wire { flex: 1; height: 1px; background: #e5e7eb; }
.flow .sig { font-size: 11px; color: rgba(0, 0, 0, .35); }
.arch-foot {
  margin-top: 8px; padding-top: 12px; border-top: 1px dashed #f0f0f0;
  font-size: 12px; color: rgba(0, 0, 0, .5); display: flex; flex-direction: column; gap: 8px; line-height: 1.6;
}
.arch-foot b { color: rgba(0, 0, 0, .8); font-weight: 600; }
.foot-two { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

/* 契约页签的选择器 */
.pickgroups { padding: 4px 0 12px; }
.pick-group { padding: 8px 0; }
.pick-group + .pick-group { border-top: 1px solid #f0f0f0; }
.pick-lt { font-size: 12px; color: rgba(0, 0, 0, .45); margin-bottom: 6px; }

/* SD-AF-I 五块 */
.sdafi { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.sblk { background: #fafafa; border-radius: 6px; padding: 12px 14px; min-width: 0; font-size: 13px; }
.sblk > b { font-size: 14px; display: block; margin-bottom: 6px; }
.sblk .chip { background: #fff; border: 1px solid #e5e7eb; border-radius: 3px; padding: 1px 6px; margin: 2px 4px 2px 0; font-size: 11px; display: inline-block; }
.pre {
  margin: 6px 0 0; padding: 6px 8px; background: #fff; border: 1px solid #e5e7eb;
  border-radius: 4px; font-size: 11px; line-height: 1.55; white-space: pre-wrap;
  color: rgba(0, 0, 0, .55); overflow-wrap: anywhere;
}
.mini-tbl { width: 100%; border-collapse: collapse; }
.mini-tbl th, .mini-tbl td { padding: 5px 6px; font-size: 12px; text-align: left; border-bottom: 1px solid #f0f0f0; }
.mini-tbl .num { text-align: right; }
.yaml {
  max-height: 440px; overflow: auto; background: #fafafa;
  border: 1px solid #f0f0f0; border-radius: 6px; padding: 10px 12px;
  font-size: 11.5px; line-height: 1.55; white-space: pre; color: rgba(0, 0, 0, .6);
}

/* 协同与信号：口径清单 */
.kvlist { padding: 4px 0 12px; }
.kvrow { display: grid; grid-template-columns: 280px 1fr; gap: 12px; padding: 8px 0; border-bottom: 1px solid #f0f0f0; font-size: 12.5px; }
.kvrow:last-child { border-bottom: 0; }
.kvrow b { font-weight: 600; }

/* 运行页签 */
.chainflow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.chainflow .arrow { color: rgba(0, 0, 0, .3); font-size: 12px; }

/* 抽屉 */
.dr-sub { font-size: 12px; color: rgba(0, 0, 0, .45); margin-bottom: 16px; }
.dr-sec { margin-bottom: 18px; font-size: 13px; }
.dr-k { font-size: 12px; color: rgba(0, 0, 0, .45); margin-bottom: 6px; }
.dr-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 18px; }
.dr-metrics > div { background: #fafafa; border-radius: 6px; padding: 10px 12px; min-width: 0; }
.dr-metrics b { display: block; font-size: 18px; line-height: 1.2; font-variant-numeric: tabular-nums; }
.dr-metrics span { font-size: 11px; color: rgba(0, 0, 0, .45); }
.dr-tags { display: flex; gap: 6px; flex-wrap: wrap; }
</style>
