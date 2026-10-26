<script setup lang="ts">
/**
 * 系统 · 智能体工作台。五段：运行拓扑 / 契约 / 协同与信号 / 运行 / 存储。
 *
 * 做它的理由:全站 13 个 Agent 散在三处——契约在 config/agents/*.yaml、
 * 运行时状态在 agent_state、运行痕迹在 agent_signal_log 与 cycle_log，
 * 而编排分散在四条链上，每条链的入口挂在它自己那一栏的页面里。
 * **结果是没有一个地方能回答「现在有几个智能体、各自什么状态」。**
 *
 * **这一页只读。** 不改参数、不投信号、不停 Agent：
 * 改参数要走 improve 的 bounds 校验与 improve_log，不能从页面绕过去；
 * 手工投信号会产生一条「没有上游」的协同记录，审计时说不清是谁触发的。
 * 能启动的只有那三条链，而且启动的是**同一个编排**（app/agent_job.py），
 * 不是这一页新造的能力。
 *
 * 页面上三条口径必须写出来，不写就是在说谎：
 *   · 契约 13 个，agent_state 也 13 行——两种编排方式的差别写在 facts 里
 *   · 心跳不适用——**空不等于坏，等于没开**（没有常驻调度器在跑）
 *   · 三条手工链的信号 2026-10-19 之前一条都没落库（实测的 bug，已修）
 *
 * 版面（2026-10-25 重做）：
 *   页签在页首（不塞进卡片标题里），第一屏是 4 张指标卡 + 运行拓扑，
 *   点节点开右侧详情抽屉；契约/信号/运行/存储四段保持原来的取数逻辑不变。
 *   上一版把页签做进卡头、拓扑节点被全局按钮高度压成 30px，三层泳道互相压字。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { pro } from '../data/prose'
import { api } from '../api'
import { useCollapse } from '../data/collapse'
import type { AgentChain, AgentDetail, AgentOverview, AgentRow, StorageRow } from '../api'

const sigList = useCollapse(), storeList = useCollapse(), byAgentList = useCollapse()

type Tab = 'all' | 'contract' | 'bus' | 'run' | 'store'
const tab = ref<Tab>('all')

const ov = ref<AgentOverview | null>(null)
const det = ref<AgentDetail | null>(null)
const pick = ref('')
const drawerId = ref('')
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

/** 取某个智能体的契约详情（抽屉与契约页签共用一份，避免重复请求） */
async function loadDetail(id: string) {
  pick.value = id
  showYaml.value = false
  if (det.value?.agent_id !== id) det.value = await api.agentDetail(id)
}

const openDrawer = (id: string) => run('d', async () => {
  drawerId.value = id
  await loadDetail(id)
})

function closeDrawer() {
  drawerId.value = ''
}

/** 契约页签里选智能体 */
const selectAgent = (id: string) =>
  run('d', async () => {
    drawerId.value = ''
    await loadDetail(id)
    tab.value = 'contract'
  })

function pickTab(t: Tab) {
  tab.value = t
  drawerId.value = ''
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

/** 轮询:启动一条链之后盯着它，跑完自动停。 */
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
      // 轮询失败不清空表格:清空会让页面在网络抖一下时整个塌掉
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
    msg.value = r.ok ? `已启动「${c.chain_name}」 ${r.job_id}`
      : (r.reason || '已有作业在运行')
    if (r.ok) poll(c.chain_id)
  })

const LAYER_CN: Record<string, string> = { L0: '感知层', L1: '执行层', L2: '协同层' }
const LAYERS = ['L0', 'L1', 'L2'] as const
const ST_CN: Record<string, string> = {
  idle: '未开始', running: '运行中', done: '已完成', failed: '失败',
}
/** 链名在节点上要短：卡片只有 158px 宽，写全称「本体构建（业务 / 流程本体）」没有意义 */
const CHAIN_SHORT: Record<string, string> = {
  review: '审核链', extract: '抽取链', onto_align: '规则本体链', onto_build: '业务本体链',
}
const TRIG_CN: Record<string, string> = {
  subscribe: '订阅信号', heartbeat: '心跳', interval: '定时', event: '事件', message: '消息投递',
}

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
/** 「素材感知（源头、规则资产装载）」→ 主名；括号补充只留在 title 里，
    卡片宽 164px，括号挤在同一行会把主名压成省略号 */
function nameMain(s: string) {
  const i = s.indexOf('（')
  return i > 0 ? s.slice(0, i) : s
}
/** 抽屉里的触发方式：把契约里的对象拍平成一行可读文字。只列启用的——
    「message 未启用」这种占位行对读的人没有信息量，契约页签里能看到全量。 */
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

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && drawerId.value) closeDrawer()
}

onMounted(() => {
  loadOv()
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  stop()
})
</script>

<template>
  <div class="v2">
  <div class="sysbar">
    <div class="tabs" role="tablist">
      <button :class="{ on: tab === 'all' }" @click="pickTab('all')">运行拓扑</button>
      <button :class="{ on: tab === 'contract' }" @click="pickTab('contract')">契约</button>
      <button :class="{ on: tab === 'bus' }" @click="pickTab('bus')">协同与信号</button>
      <button :class="{ on: tab === 'run' }" @click="pickTab('run')">运行</button>
      <button :class="{ on: tab === 'store' }" @click="pickTab('store')">存储</button>
    </div>
    <div class="grow"></div>
    <span v-if="ov" class="pill num" :class="ov.totals.failed ? 'bad' : 'ok'">
      契约校验 {{ ov.totals.checks }} 项 · 失败 {{ ov.totals.failed }}
    </span>
    <button class="btn ghost" :disabled="!!busy" @click="loadOv">刷新</button>
  </div>

  <div v-if="err" class="err">{{ err }}</div>
  <div v-if="msg" class="ok-note">{{ msg }}</div>

  <!-- ① 运行拓扑 -->
  <template v-if="tab === 'all'">
    <div v-if="ov" class="stats hub-stats">
      <div class="stat">
        <div class="k">运行中（近 24 小时）</div>
        <div class="v num">{{ stats.live }}<small> / {{ stats.total }}</small></div>
        <div class="d">感知 {{ stats.l0 }} · 执行 {{ stats.l1 }} · 协同 {{ stats.l2 }}</div>
      </div>
      <div class="stat">
        <div class="k">周期累计</div>
        <div class="v num">{{ stats.cycles }}</div>
        <div class="d">四条编排链合计</div>
      </div>
      <div class="stat">
        <div class="k">信号累计</div>
        <div class="v num">{{ stats.signals }}</div>
        <div class="d">已落 agent_signal_log</div>
      </div>
      <div class="stat" :class="{ alert: stats.failed > 0 }">
        <div class="k">契约校验</div>
        <div class="v num">{{ stats.checks }}<small> 项</small></div>
        <div class="d">{{ stats.failed ? `失败 ${stats.failed} 项` : '全部通过' }}</div>
      </div>
    </div>

    <div v-if="ov" class="card">
      <div class="card-head">
        <h2>运行拓扑</h2>
        <span class="hint">感知只发现，执行出结论，协同做归并；点节点看这一条的运行情况</span>
        <div class="grow"></div>
        <div class="lg">
          <span><i class="dg run"></i>24 小时内有周期</span>
          <span><i class="dg idle"></i>待命</span>
        </div>
      </div>

      <div class="arch">
        <template v-for="lay in LAYERS" :key="lay">
          <div class="lane">
            <div class="lane-head"><span class="lt">{{ LAYER_CN[lay] }}</span></div>
            <div class="lane-body">
              <button v-for="a in agentsOf(lay)" :key="a.agent_id" class="node"
                      :class="{ sel: drawerId === a.agent_id }"
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
          共 <b>{{ stats.total }}</b> 个智能体，其中 <b>{{ stats.live }}</b> 个近 24 小时跑过周期；
          POC 没有常驻调度器，其余按需触发——<b>空不等于坏，等于没开</b>。
        </span>
        <span v-if="triple.length" class="foot-two">
          声明不下结论的 {{ triple.length }} 个：
          <button v-for="a in triple" :key="a.agent_id" class="chip" @click="openDrawer(a.agent_id)">
            {{ nameMain(a.agent_name) }}
          </button>
        </span>
      </div>
    </div>
  </template>

  <!-- ② 契约 ---------------------------------------------------------------->
  <template v-if="tab === 'contract'">
    <div class="card" style="margin-bottom:16px">
      <div class="card-head"><h2>选择智能体</h2><div class="grow"></div>
        <span class="hint">按层列出，共 {{ ov?.items.length || 0 }} 个</span></div>
      <div class="pickgroups">
        <div v-for="lay in LAYERS" :key="lay" class="pick-group">
          <div class="pick-lt">{{ LAYER_CN[lay] }}</div>
          <div class="chips">
            <button v-for="a in agentsOf(lay)" :key="a.agent_id" class="chip"
                    :class="{ on: pick === a.agent_id }" @click="selectAgent(a.agent_id)">
              {{ nameMain(a.agent_name) }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <template v-if="det">
      <div class="card" style="margin-bottom:16px">
        <div class="card-head">
          <h2>{{ det.agent_name }}</h2>
          <span class="pill brand">{{ LAYER_CN[det.layer] || det.layer }}</span>
          <span class="hint mono">{{ det.agent_id }} · v{{ det.agent_version }}</span>
          <div class="grow"></div>
          <span class="hint">{{ det.chain_name || '—' }}</span>
        </div>
        <div class="kvg">
          <div><span class="k">职责</span><span class="v" style="white-space:normal">{{ det.desc || '—' }}</span></div>
          <div><span class="k">执行策略</span><span class="v">{{ det.execution_strategy }}</span></div>
          <div><span class="k">运行时参数来源</span><span class="v">{{ det.state.params_from }}</span></div>
          <div>
            <span class="k">上次心跳</span>
            <span class="v">{{ det.state.last_beat_at ? fmt(det.state.last_beat_at) : '不适用' }}</span>
          </div>
        </div>
        <div v-if="!det.state.last_beat_at" class="note" style="margin:0 20px 16px">
          POC 里没有常驻调度器在跑，心跳为空是<b>没开</b>而不是坏了；
          心跳仍是契约必填项——LISTEN/NOTIFY 是尽力而为的，断连期间的通知收不到且不报错。
        </div>
      </div>

      <div class="card" style="margin-bottom:16px">
        <div class="card-head"><h2>触发方式</h2></div>
        <table class="tbl">
          <thead>
            <tr>
              <th style="width: 140px">类型</th><th style="width: 90px">启用</th>
              <th style="width: 220px">通道 / 间隔</th><th>为什么这么设</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(t, k) in det.triggers" :key="k">
              <td>
                {{ TRIG_CN[k] || k }}
                <span class="mono muted" style="font-size:11px"> {{ k }}</span>
              </td>
              <td>
                <span class="tag" :class="t.enabled ? 't-pass' : 't-na'">
                  {{ t.enabled ? '开' : '关' }}</span>
              </td>
              <td class="mono" style="font-size: 12px">
                {{ (t.channels as string[] | undefined)?.join('、')
                  || (t.interval_seconds ? t.interval_seconds + ' 秒' : '—') }}
              </td>
              <td style="font-size: 12px"><MarkText :text="t.why as (string | undefined)" /></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card" style="margin-bottom:16px">
        <div class="card-head">
          <h2>S → D → A → F → I</h2>
          <span class="hint">读什么、怎么判、写哪里、量什么、能调什么</span>
        </div>
        <div class="sdafi">
          <div class="blk">
            <b>S 读</b>
            <div class="muted" style="font-size: 12px">
              只允许读这些表（执行点 app/runtime/pipeline.py）
            </div>
            <div><code v-for="t in det.sdafi.S.tables" :key="t" class="chip">{{ t }}</code></div>
            <pre v-if="det.sdafi.S.select" class="pre">{{ det.sdafi.S.select }}</pre>
          </div>
          <div class="blk">
            <b>D 判</b>
            <div v-if="det.sdafi.D.no_judgement" class="tag t-na">声明不下结论</div>
            <div class="muted" style="font-size: 12px">{{ det.sdafi.D.kind }}</div>
            <div>
              <code v-for="v in det.sdafi.D.conclusion_values" :key="v" class="chip">{{ v }}</code>
            </div>
            <pre v-if="det.sdafi.D.pending_why" class="pre">判不出时:{{ det.sdafi.D.pending_counts_as }}
{{ det.sdafi.D.pending_why }}</pre>
            <pre v-if="det.sdafi.D.no_judgement_why" class="pre">{{ det.sdafi.D.no_judgement_why }}</pre>
          </div>
          <div class="blk">
            <b>A 写</b>
            <div class="muted" style="font-size: 12px">
              <b>唯一允许写库的阶段</b>,且只能写这些表
            </div>
            <div><code v-for="t in det.sdafi.A.writes" :key="t" class="chip">{{ t }}</code></div>
            <div v-if="det.sdafi.A.signal.channel" style="font-size: 12px; margin-top: 4px">
              投信号 → <code>{{ det.sdafi.A.signal.channel }}</code>
            </div>
            <pre v-if="det.sdafi.A.idempotency" class="pre">幂等键 {{ det.sdafi.A.idempotency }}
{{ det.sdafi.A.idempotency_why }}</pre>
          </div>
          <div class="blk">
            <b>F 量</b>
            <table v-if="det.sdafi.F.metrics.length" class="tbl">
              <tbody>
                <tr v-for="m in det.sdafi.F.metrics" :key="m.metric_name">
                  <td class="mono" style="width: 150px; font-size: 11px">
                    {{ m.metric_name }}
                  </td>
                  <td style="font-size: 12px">{{ m.desc }}</td>
                  <td class="mono muted" style="width: 130px; font-size: 11px">
                    {{ m.source }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="blk">
            <b>I 调</b>
            <table v-if="det.sdafi.I.params.length" class="tbl">
              <thead>
                <tr>
                  <th style="width: 150px">参数</th><th style="width: 60px">默认</th>
                  <th style="width: 100px">允许区间</th><th>为什么是这个区间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in det.sdafi.I.params" :key="p.param_name">
                  <td class="mono" style="font-size: 11px">{{ p.param_name }}</td>
                  <td class="num">{{ p.default }}</td>
                  <td class="mono" style="font-size: 11px">[{{ p.lo }}, {{ p.hi }}]</td>
                  <td style="font-size: 12px"><MarkText :text="p.why" /></td>
                </tr>
              </tbody>
            </table>
            <div style="margin-top: 6px">
              <b style="color: var(--hit)">禁止自调（{{ det.sdafi.I.forbidden.length }}）</b>
              <div v-for="f in det.sdafi.I.forbidden" :key="f" style="font-size: 12px">
                · {{ f }}
              </div>
            </div>
          </div>
        </div>
        <div class="note" style="margin:12px 20px 16px"><MarkText :text="det.note" /></div>
      </div>

      <div class="card" style="margin-bottom:16px">
        <div class="card-head">
          <h2>契约原文</h2>
          <span class="hint">{{ det.contract_file.lines }} 行，其中 {{ det.contract_file.comment_lines }} 行是注释——注释就是口径本身</span>
          <div class="grow"></div>
          <span class="hint mono">{{ det.contract_file.file }}</span>
          <button class="btn ghost" @click="showYaml = !showYaml">
            {{ showYaml ? '收起原文' : '看原文' }}
          </button>
        </div>
        <pre v-if="showYaml" class="yaml">{{ det.contract_file.text }}</pre>
      </div>
    </template>

    <div class="card">
      <div class="card-head">
        <h2>契约校验</h2>
        <span v-if="checks" class="hint">
          {{ checks.total }} 项，失败
          <b :style="{ color: checks.failed ? 'var(--bad)' : 'var(--ok)' }">{{ checks.failed }}</b>
        </span>
        <div class="grow"></div>
        <button v-if="checks" class="btn ghost" @click="showAllChecks = !showAllChecks">
          {{ showAllChecks ? '只看失败' : '看全部' }}
        </button>
        <button class="btn ghost" :disabled="!!busy" @click="loadChecks">现跑一遍</button>
      </div>
      <div v-if="checks" class="checks">
        <div v-if="!showAllChecks && !checks.failed" class="ok-line">
          {{ checks.total }} 项全过。<b>和启动时跑的是同一套</b>——另写一份校验就会出现
          「启动能过、页面报错」这种谁也说不清的状态。
        </div>
        <table v-else class="tbl">
          <thead><tr><th style="width: 70px">结果</th><th style="width: 340px">检查项</th>
            <th>说明</th></tr></thead>
          <tbody>
            <tr v-for="(k, i) in checks.items.filter((x) => showAllChecks || !x.ok)"
                :key="i">
              <td><span class="tag" :class="k.ok ? 't-pass' : 't-hit'">
                {{ k.ok ? '通过' : '失败' }}</span></td>
              <td style="font-size: 12px">{{ k.check }}</td>
              <td class="muted" style="font-size: 12px"><MarkText :text="k.detail" /></td>
            </tr>
          </tbody>
        </table>
        <div class="note" style="margin:0"><MarkText :text="checks.note" /></div>
      </div>
    </div>
  </template>

  <!-- ③ 协同与信号 ---------------------------------------------------------->
  <template v-if="tab === 'bus'">
    <div v-if="sig" class="card" style="margin-bottom:16px">
      <div class="card-head"><h2>数据池</h2>
        <span class="hint">Agent 之间零直接调用：上游写进池子、发信号，下游从池子里读</span></div>
      <div class="kvlist">
        <div v-for="f in sig.facts" :key="f.fact" class="kvrow">
          <b>{{ f.fact }}</b><span><MarkText :text="f.why" /></span>
        </div>
      </div>
      <table v-if="sig.pool.length" class="tbl">
        <thead><tr><th style="width: 260px">通道</th><th style="width: 100px">状态</th>
          <th class="num" style="width: 80px">条数</th><th>最近一条</th></tr></thead>
        <tbody>
          <tr v-for="(p, i) in sig.pool" :key="i">
            <td class="mono" style="font-size: 12px">{{ p.channel }}</td>
            <td>
              <span class="tag" :class="p.status === 'pending' ? 't-pend' : 't-pass'">
                {{ p.status }}</span>
            </td>
            <td class="num">{{ p.n }}</td>
            <td class="muted mono" style="font-size: 12px">
              {{ p.last?.toString().slice(0, 19) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="sig" class="card" style="margin-bottom:16px">
      <div class="card-head"><h2>谁投过信号</h2></div>
      <table class="tbl">
        <thead><tr><th style="width: 300px">智能体</th><th class="num" style="width: 80px">条数</th>
          <th style="width: 170px">第一条</th><th>最近一条</th></tr></thead>
        <tbody>
          <tr v-for="a in byAgentList.view(sig.by_agent)" :key="a.agent_id">
            <td class="mono" style="font-size: 12px">{{ a.agent_id }}</td>
            <td class="num">{{ a.n }}</td>
            <td class="muted mono" style="font-size: 12px">
              {{ a.first?.toString().slice(0, 19) }}
            </td>
            <td class="muted mono" style="font-size: 12px">
              {{ a.last?.toString().slice(0, 19) }}
            </td>
          </tr>
          <tr v-if="byAgentList.more(sig.by_agent).show">
            <td :colspan="4" class="morecell" @click="byAgentList.more(sig.by_agent).toggle()">
              {{ byAgentList.more(sig.by_agent).rest === -1 ? '收起' : `展开剩余 ${byAgentList.more(sig.by_agent).rest} 条` }}（共 {{ sig.by_agent.length }} 条）
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="sig" class="card">
      <div class="card-head"><h2>信号台账</h2><div class="grow"></div>
        <span class="hint">最近 {{ sig.items.length }} 条</span></div>
      <table class="tbl">
        <thead>
          <tr>
            <th style="width: 150px">时间</th><th style="width: 210px">智能体</th>
            <th style="width: 110px">动作</th><th style="width: 190px">目标</th>
            <th style="width: 160px">写了哪些表</th><th>幂等键</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sigList.view(sig.items)" :key="s.id">
            <td class="mono" style="font-size: 12px">
              {{ s.emitted_at.toString().slice(5, 19) }}
            </td>
            <td class="mono" style="font-size: 12px" :title="s.agent_id">{{ s.agent_id }}</td>
            <td style="font-size: 12px">{{ s.action }}</td>
            <td class="mono t-cut" style="font-size: 12px" :title="s.target || ''">{{ s.target }}</td>
            <td class="mono muted t-cut" style="font-size: 11px" :title="writtenText(s.written_tables)">
              {{ writtenText(s.written_tables) }}
            </td>
            <td class="mono muted t-cut" style="font-size: 11px" :title="s.idempotency_key">{{ s.idempotency_key }}</td>
          </tr>
          <tr v-if="sigList.more(sig.items).show">
            <td :colspan="6" class="morecell" @click="sigList.more(sig.items).toggle()">
              {{ sigList.more(sig.items).rest === -1 ? '收起' : `展开剩余 ${sigList.more(sig.items).rest} 条` }}（共 {{ sig.items.length }} 条）
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>

  <!-- ④ 运行 ---------------------------------------------------------------->
  <template v-if="tab === 'run'">
    <div class="note lead"><MarkText :text="chainNote" /></div>
    <div v-for="c in chains" :key="c.chain_id" class="card chaincard">
      <div class="card-head">
        <h2>{{ c.chain_name }}</h2>
        <span class="tag" :class="c.kind === '常驻编排' ? 't-pass' : 't-na'">{{ c.kind }}</span>
        <span v-if="c.status" class="tag" :class="c.status === 'running' ? 't-pend'
          : c.status === 'failed' ? 't-hit' : 't-na'">
          {{ ST_CN[c.status] || c.status }}
        </span>
        <span v-if="c.pct !== undefined && c.status === 'running'" class="hint num">{{ c.pct }}%</span>
        <div class="grow"></div>
        <a class="btn ghost" :href="c.page">去那一栏 →</a>
        <button v-if="c.kind !== '常驻编排'" class="btn ghost"
                :disabled="!!busy || c.status === 'running'" @click="startChain(c)">
          {{ c.status === 'running' ? '运行中…' : '在这里启动' }}
        </button>
        <span v-else class="hint">常驻，本页不启停</span>
      </div>
      <div class="chainbody">
        <div class="note" style="margin:0 0 12px"><MarkText :text="c.note" /></div>
        <div class="chainflow">
          <template v-for="(a, i) in c.agents" :key="a.agent_id">
            <span v-if="i" class="arrow">→</span>
            <button class="chip" @click="selectAgent(a.agent_id)">
              <span class="muted">{{ a.layer }}</span> {{ nameMain(a.agent_name) }}
            </button>
          </template>
        </div>
        <div class="hint" style="margin-top: 10px">
          <span v-if="c.signals !== undefined">台账 {{ c.signals }} 条信号</span>
          <span v-if="c.signals_in_job !== undefined">（本次作业 {{ c.signals_in_job }} 条）</span>
          <span v-if="c.elapsed"> · 上次用时 {{ c.elapsed }} 秒</span>
          <span v-if="c.job_id"> · 作业 {{ c.job_id }}</span>
        </div>
        <div v-if="c.error" class="err" style="margin-top: 8px">{{ c.error }}</div>
        <div v-if="c.log_errors && c.log_errors.length" class="err" style="margin-top: 8px">
          <b>信号落库失败 {{ c.log_errors.length }} 条</b>（链照常跑完，但台账缺了行）：
          <div v-for="(e, i) in c.log_errors" :key="i" style="font-size: 12px">
            {{ e.from }} → {{ e.channel }}：{{ e.error }}
          </div>
        </div>
      </div>
    </div>
  </template>

  <!-- ⑤ 存储 ---------------------------------------------------------------->
  <template v-if="tab === 'store'">
    <div v-if="store" class="card">
      <div class="card-head"><h2>存储</h2><div class="grow"></div>
        <span class="hint">{{ store.items.length }} 张表 / 文件</span></div>
      <table class="tbl">
        <thead>
          <tr>
            <th style="width: 200px">表 / 文件</th><th class="num" style="width: 70px">行数</th>
            <th style="width: 230px">存的是什么</th><th style="width: 160px">谁写的</th>
            <th style="width: 130px">重算时</th><th>为什么这么存</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in storeList.view(store.items)" :key="t.table_name">
            <td class="mono" style="font-size: 12px">{{ t.table_name }}</td>
            <td class="num">{{ t.rows ?? '—' }}</td>
            <td style="font-size: 12px"><MarkText :text="pro(t.what)" /></td>
            <td class="muted" style="font-size: 12px">{{ pro(t.written_by) }}</td>
            <td style="font-size: 12px">{{ t.on_recalc }}</td>
            <td class="muted" style="font-size: 12px"><MarkText :text="pro(t.why)" /></td>
          </tr>
          <tr v-if="storeList.more(store.items).show">
            <td :colspan="6" class="morecell" @click="storeList.more(store.items).toggle()">
              {{ storeList.more(store.items).rest === -1 ? '收起' : `展开剩余 ${storeList.more(store.items).rest} 条` }}（共 {{ store.items.length }} 条）
            </td>
          </tr>
        </tbody>
      </table>
      <div class="note" style="margin:12px 20px 16px"><MarkText :text="store.note" /></div>
    </div>
  </template>

  <!-- 节点详情抽屉 -->
  <template v-if="drawerId">
    <div class="dr-mask" @click.self="closeDrawer"></div>
    <aside class="dr" role="dialog" aria-modal="true" aria-label="智能体详情">
      <div class="dr-head">
        <div>
          <div class="dr-name">{{ cur?.agent_name || '—' }}</div>
          <div class="dr-sub">
            {{ LAYER_CN[cur?.layer || ''] || cur?.layer }} · {{ cur?.chain_name || '—' }}
            · v{{ cur?.agent_version }}
          </div>
        </div>
        <button class="dr-x" aria-label="关闭" @click="closeDrawer">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
        </button>
      </div>

      <div class="dr-body">
        <div class="dr-sec">
          <div class="dr-k">职责</div>
          <div class="dr-v">{{ cur?.desc || '—' }}</div>
        </div>

        <div class="dr-metrics">
          <div><b class="num">{{ cur?.cycles || 0 }}</b><span>周期累计</span></div>
          <div><b class="num">{{ cur?.signals || 0 }}</b><span>信号累计</span></div>
          <div><b class="num">{{ cur?.reads.length || 0 }}</b><span>可读表</span></div>
          <div><b class="num">{{ cur?.writes.length || 0 }}</b><span>可写表</span></div>
        </div>

        <div v-if="!det" class="dr-loading">读取契约…</div>
        <template v-else>
          <div class="dr-sec">
            <div class="dr-k">触发方式</div>
            <div class="dr-tags">
              <span v-for="t in triggerRows(det)" :key="t.key" class="tag"
                    :class="t.enabled ? 't-pass' : 't-na'">
                {{ t.label }}<em v-if="t.detail"> · {{ t.detail }}</em>
              </span>
            </div>
          </div>

          <div v-if="det.sdafi.I.params.length" class="dr-sec">
            <div class="dr-k">可调参数（改参数要走 improve，本页只读）</div>
            <table class="tbl dr-tbl">
              <thead><tr><th>参数</th><th class="num">默认</th><th>区间</th></tr></thead>
              <tbody>
                <tr v-for="p in det.sdafi.I.params" :key="p.param_name">
                  <td class="mono">{{ p.param_name }}</td>
                  <td class="num">{{ p.default }}</td>
                  <td class="mono">[{{ p.lo }}, {{ p.hi }}]</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="det.sdafi.I.forbidden.length" class="dr-sec">
            <div class="dr-k">禁止自调</div>
            <div class="dr-tags">
              <span v-for="f in det.sdafi.I.forbidden" :key="f" class="tag t-hit">{{ f }}</span>
            </div>
          </div>

          <div class="dr-sec">
            <div class="dr-k">最近周期</div>
            <div class="dr-v mono">{{ fmt(cur?.last_cycle_at) }}</div>
          </div>
        </template>
      </div>

      <div class="dr-foot">
        <button class="btn ghost" @click="closeDrawer">关闭</button>
        <button class="btn primary" @click="selectAgent(drawerId)">查看完整契约</button>
      </div>
    </aside>
  </template>
  </div>
</template>

<style scoped>
/* 页签行：与内容区同宽、不塞进卡片，右侧挂契约校验结论 */
.sysbar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.sysbar .tabs { margin-bottom: 0; }
.hub-stats { grid-template-columns: repeat(4, minmax(0, 1fr)); }
@media (max-width: 900px) { .hub-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.hint { font-size: 12px; color: var(--weak); line-height: 1.5; }
.lg { display: flex; gap: 14px; font-size: 12px; color: var(--muted); }
.lg span { display: inline-flex; align-items: center; gap: 6px; }
.dg { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dg.run { background: var(--ok); }
.dg.idle { background: var(--weak); }

/* 深色下用亮一档的蓝：--brand 被契约测试钉死为深蓝，不能改绑定，
   深蓝落在 #151c26 的卡片上等于没有颜色 */
[data-theme='dark'] .lane-head .lt,
[data-theme='dark'] .node .chain { color: #7cb2f0; }
[data-theme='dark'] .node .chain.k2 { color: #d9b25c; }

/* 拓扑脚注：运行口径 + 不下结论的感知体 */.arch-foot {
  margin: 4px 20px 0; padding: 12px 0 16px; border-top: 1px dashed var(--panel-2);
  font-size: 12px; color: var(--weak); display: flex; flex-direction: column; gap: 8px;
  line-height: 1.6;
}
.arch-foot b { color: var(--text); font-weight: 600; }
.foot-two { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

/* 契约页签的智能体选择器 */
.pickgroups { padding: 4px 20px 16px; }
.pick-group { padding: 10px 0; }
.pick-group + .pick-group { border-top: 1px solid var(--panel-2); }
.pick-lt { font-size: 12px; color: var(--weak); margin-bottom: 8px; }

/* 详情抽屉 */
.dr-mask { position: fixed; inset: 0; background: rgba(10, 16, 24, .38); z-index: 70; }
.dr {
  position: fixed; top: 0; right: 0; bottom: 0; width: 430px; max-width: 94vw;
  background: var(--panel); border-left: 1px solid var(--line);
  box-shadow: var(--shadow-2); z-index: 71; display: flex; flex-direction: column;
  animation: drin .18s ease-out;
}
@keyframes drin { from { transform: translateX(18px); opacity: .5; } to { transform: none; opacity: 1; } }
.dr-head {
  display: flex; align-items: flex-start; gap: 12px;
  padding: 18px 20px 14px; border-bottom: 1px solid var(--panel-2);
}
.dr-name { font-size: 16px; font-weight: 600; color: var(--text); }
.dr-sub { font-size: 12px; color: var(--weak); margin-top: 3px; }
.dr-head .dr-x {
  margin-left: auto; padding: 0; width: 28px; min-height: 28px; height: 28px;
  border-radius: 6px; border: 0; background: none; color: var(--weak);
  display: grid; place-items: center;
}
.dr-head .dr-x:hover { background: var(--panel-2); color: var(--text); border-color: transparent; }
.dr-body { flex: 1; overflow: auto; padding: 16px 20px 8px; }
.dr-sec { margin-bottom: 18px; }
.dr-k { font-size: 12px; color: var(--weak); margin-bottom: 6px; }
.dr-v { font-size: 13px; color: var(--text); line-height: 1.65; }
.dr-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 18px; }
.dr-metrics > div { background: var(--panel-2); border-radius: var(--r-s); padding: 10px 12px; min-width: 0; }
.dr-metrics b { display: block; font-size: 18px; color: var(--text); line-height: 1.2; }
.dr-metrics span { font-size: 11px; color: var(--weak); }
.dr-tags { display: flex; gap: 6px; flex-wrap: wrap; }
.dr-tags .tag em { font-style: normal; opacity: .8; }
.dr-tbl td, .dr-tbl th { padding: 6px 8px; font-size: 12px; }
.dr-loading { font-size: 13px; color: var(--weak); padding: 6px 0 16px; }
.dr-foot {
  padding: 12px 20px; border-top: 1px solid var(--panel-2);
  display: flex; gap: 8px; justify-content: flex-end;
}

/* 契约表格里的长文本 */
.checks { padding-bottom: 4px; }
.ok-line {
  margin: 14px 20px; padding: 10px 14px; border-radius: var(--r-s);
  background: var(--ok-bg); color: var(--ok); font-size: 12.5px;
}
.morecell { text-align: center; color: var(--brand); cursor: pointer; font-size: 13px; }

/* 协同与信号：口径清单 */
.kvlist { padding: 4px 20px 12px; }
.kvrow { display: grid; grid-template-columns: 280px 1fr; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--panel-2); font-size: 12.5px; }
.kvrow:last-child { border-bottom: 0; }
.kvrow b { color: var(--text); font-weight: 600; }
.kvrow span { color: var(--muted); line-height: 1.6; }

/* 运行页签：链卡 */
.note.lead { margin-bottom: 16px; }
.chainbody { padding: 16px 20px; }
.chainflow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.chainflow .arrow { color: var(--weak); font-size: 12px; }
.chainflow .chip { display: inline-flex; align-items: center; gap: 4px; }

/* S → D → A → F → I */
.sdafi { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; margin: 16px 20px 0; }
.sdafi .blk { background: var(--panel-2); border-radius: var(--r-s); padding: 12px 14px; min-width: 0; }
.sdafi .blk > b { font-size: 14px; display: block; margin-bottom: 6px; color: var(--text); }
.sdafi .blk .chip { cursor: default; background: var(--panel); border: 1px solid var(--line); border-radius: 3px; padding: 1px 6px; margin: 2px 4px 2px 0; font-size: 11px; display: inline-block; }
.pre {
  margin: 6px 0 0; padding: 6px 8px; background: var(--panel); border: 1px solid var(--line);
  border-radius: 4px; font-size: 11px; line-height: 1.55; white-space: pre-wrap;
  color: var(--muted); overflow-wrap: anywhere;
}
.yaml {
  margin: 0 20px 16px; max-height: 440px; overflow: auto; background: var(--panel-2);
  border: 1px solid var(--line); border-radius: 4px; padding: 10px 12px;
  font-size: 11.5px; line-height: 1.55; white-space: pre; color: var(--muted);
}
.ok-note {
  background: var(--ok-bg); border: 1px solid #abefc6; border-radius: 8px;
  padding: 8px 12px; margin-bottom: 12px; font-size: 12.5px; color: var(--ok);
}
</style>
