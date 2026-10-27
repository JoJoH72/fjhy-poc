<script setup lang="ts">
/**
 * 偏差与回流（领导查询，2026-10-27 AntD 换装版）：判定质量 + 数据缺口 + 审计一致性。
 *
 * 全部真实接口：/api/deviation（四类判定与口径注）、
 * /api/audit/quarantine（装载隔离区）、/api/audit/consistency（一致性自检）。
 * demo 里的「待人工成因（G-04 等）」后端没有对应接口——成因清单在
 * 《数据接入要求单》文档里，页面上不造数。误报按规则的分布接口有，
 * 但当前为 0 条，所以整块不渲染。
 *
 * 2026-10-25 重做：判定条 + 四类卡片一张卡讲完；隔离区按「缺口 + 来源 + 原因」归组，
 * 明细按需展开并给出影响面；一致性拆成「硬约束」与「对账信息」两段，带方向说明。
 */
import { ref } from 'vue'
import Async from '../components/Async.vue'
import { api } from '../api'
import MarkText from '../components/MarkText.vue'

interface Deviation {
  by_kind: { deviation_kind: string | null; n: number }[]
  false_positive_by_rule: { rule_id: string; n: number }[]
  kinds: Record<string, string>
  note: string
}
interface QRow {
  id: number; loaded_at: string; source: string; target: string
  reason: string; gap_id: string | null; resolved: boolean
  row_data: Record<string, unknown> | null
}
interface Quarantine { items: QRow[]; unresolved?: number }
interface Consistency { ok: boolean; counts: Record<string, number>; problems: string[] }

const KIND_ORDER = ['null', 'needs_review_only', 'false_positive', 'false_negative'] as const
const META: Record<string, { name: string; pos: string; cls: string; desc: string }> = {
  null: {
    name: '完全一致', pos: 'AI 与人工结论相同', cls: 'ok',
    desc: '一致，无需人工改判',
  },
  needs_review_only: {
    name: '仅待人工', pos: 'AI 转人工 · 人工放行', cls: 'warn',
    desc: '不是误报，是没把握',
  },
  false_positive: {
    name: '误报', pos: 'AI 命中 · 人工放行', cls: 'bad',
    desc: '真误报，代价最高',
  },
  false_negative: {
    name: '漏报', pos: 'AI 放行 · 人工退回', cls: 'bad',
    desc: '把风险放过去了',
  },
}
const SEG_COLOR: Record<string, string> = { ok: '#52c41a', warn: '#faad14', bad: '#ff4d4f' }

/** 卡片说明直接用后端 kinds 的口径原文；原文太短（如「一致」两个字）时用本地一句补足 */
function tileDesc(d: Deviation, k: string): string {
  const src = String(d.kinds?.[k] ?? '').replace(/\*\*/g, '')
  return src.length >= 8 ? src : META[k].desc
}

function nOf(d: Deviation, kind: string): number {
  const row = (d.by_kind || []).find((x) => (x.deviation_kind ?? 'null') === kind)
  return row ? row.n : 0
}
const totalOf = (d: Deviation) => (d.by_kind || []).reduce((s, x) => s + x.n, 0)
const rateOf = (d: Deviation) => {
  const t = totalOf(d)
  return t ? Math.round((nOf(d, 'null') / t) * 1000) / 10 : 0
}

/** 判定条的分段：只有非零的才占宽度，零值在图例里显示为 0 */
function segsOf(d: Deviation) {
  return KIND_ORDER.map((k) => ({
    key: k,
    name: META[k].name,
    cls: META[k].cls,
    n: nOf(d, k),
  }))
}

/* ── 隔离区：同一张单的明细行要归成一组 ── */
interface QGroup {
  key: string; gap: string; source: string; reason: string; rows: QRow[]; resolved: boolean
}
function groupsOf(items: QRow[]): QGroup[] {
  const map = new Map<string, QGroup>()
  for (const it of items) {
    const gap = it.gap_id || '未编号'
    const key = `${gap}|${it.source}|${it.reason}`
    let g = map.get(key)
    if (!g) {
      g = { key, gap, source: it.source, reason: it.reason, rows: [], resolved: it.resolved }
      map.set(key, g)
    }
    g.rows.push(it)
  }
  return [...map.values()]
}

const open = ref<Set<string>>(new Set())
function toggle(k: string) {
  const next = new Set(open.value)
  next.has(k) ? next.delete(k) : next.add(k)
  open.value = next
}

/** 明细行的列：后端 row_data 的键不固定，按「单据号 / 部门 / 项目 / 金额」优先排 */
const COL_CN: Record<string, string> = {
  bill_no: '单据号', dept: '部门', item: '费用项目', amount: '金额',
  person: '姓名', days: '天数', city: '城市', date: '日期',
}
function colsOf(rows: QRow[]): string[] {
  const seen: string[] = []
  for (const r of rows) for (const k of Object.keys(r.row_data || {})) if (!seen.includes(k)) seen.push(k)
  const pref = ['bill_no', 'dept', 'item', 'amount']
  return [...pref.filter((k) => seen.includes(k)), ...seen.filter((k) => !pref.includes(k))]
}

/** 这一组隔离行的影响面：几个部门、几个费用项目、合计多少钱。 */
function impactOf(rows: QRow[]) {
  const depts = new Set<string>(), items = new Set<string>()
  let sum = 0, has = false
  for (const r of rows) {
    const d = r.row_data || {}
    if (d.dept) depts.add(String(d.dept))
    if (d.item) items.add(String(d.item))
    const n = Number(d.amount)
    if (d.amount !== undefined && d.amount !== null && Number.isFinite(n)) { sum += n; has = true }
  }
  return { depts: depts.size, items: items.size, sum, has }
}
const money = (n: number) =>
  '¥' + n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
/** 金额列一律两位小数；别的列原样 */
function cell(v: unknown, col: string) {
  if (v === null || v === undefined || v === '') return '—'
  if (col === 'amount') {
    const n = Number(v)
    return Number.isFinite(n) ? n.toFixed(2) : String(v)
  }
  return String(v)
}

/* ── 一致性自检：硬约束（必须为 0）与对账信息（不要求相等）分开 ── */
function cOf(c: Consistency, k: string): number {
  return Number(c.counts?.[k] ?? 0)
}
</script>

<template>
  <div class="dv">
    <!-- ① 判定质量 -->
    <Async :load="() => api.deviation() as unknown as Promise<Deviation>">
      <template #default="{ data }">
        <a-card size="small" class="dv-panel">
          <template #title>AI × 人工 交叉判定</template>
          <template #extra>
            <span class="dim">{{ totalOf(data) }} 条判定记录</span>
            <a-tag v-if="!nOf(data, 'false_positive')" color="success" style="margin-left: 8px">零误报</a-tag>
            <a-tag v-if="!nOf(data, 'false_negative')" color="success">零漏报</a-tag>
          </template>
          <div class="dv-hint">每张单可能同时有「结论」与「命中」两类反馈，所以记录数不等于单据数</div>

          <div class="dv-stack">
            <div class="dv-bar" role="img"
                 :aria-label="`判定分布：${segsOf(data).map((s) => s.name + ' ' + s.n).join('，')}`">
              <i v-for="s in segsOf(data).filter((x) => x.n > 0)" :key="s.key"
                 :style="{ flexGrow: s.n, background: SEG_COLOR[s.cls] }" :title="`${s.name} ${s.n}`">
                <span>{{ s.n }}</span>
              </i>
            </div>
            <div class="dv-legend">
              <span v-for="s in segsOf(data)" :key="s.key">
                <i :style="{ background: SEG_COLOR[s.cls] }"></i>{{ s.name }} <b>{{ s.n }}</b>
              </span>
              <div class="grow"></div>
              <span class="dv-rate" title="一致条数 ÷ 判定记录数；误报与漏报单列，不合成准确率">一致率 <b>{{ rateOf(data) }}%</b></span>
            </div>
          </div>

          <a-row class="dv-tiles" :gutter="0">
            <a-col v-for="k in KIND_ORDER" :key="k" :xs="12" :md="12" :xl="6" class="dv-tile-wrap">
              <div class="dv-tile" :class="META[k].cls" :title="META[k].pos">
                <div class="dv-tile-top">
                  <span class="dv-tile-name">{{ META[k].name }}</span>
                  <a-tag v-if="!nOf(data, k)" color="success" class="dv-zero">零</a-tag>
                </div>
                <div class="dv-tile-v">{{ nOf(data, k) }}<small> / {{ totalOf(data) }}</small></div>
                <div class="dv-tile-d"><MarkText :text="tileDesc(data, k)" /></div>
              </div>
            </a-col>
          </a-row>

          <div v-if="data.note" class="dv-note"><MarkText :text="data.note" /></div>
        </a-card>

        <a-card v-if="(data.false_positive_by_rule || []).length" size="small" class="dv-panel" title="误报按规则排序">
          <template #extra><span class="dim">点规则看它在规则资产里的口径</span></template>
          <a-table :data-source="data.false_positive_by_rule" row-key="rule_id" size="small"
            :pagination="false"
            :columns="[
              { title: '规则', dataIndex: 'rule_id', width: 260 },
              { title: '误报次数', dataIndex: 'n', width: 120, align: 'right' },
            ]">
            <template #bodyCell="{ column, record: r }">
              <template v-if="column.dataIndex === 'rule_id'">
                <router-link :to="`/asset/rules/${r.rule_id}`">{{ r.rule_id }}</router-link>
              </template>
            </template>
          </a-table>
        </a-card>
      </template>
    </Async>

    <!-- ② 隔离区 + 一致性 -->
    <a-row :gutter="[16, 16]">
      <a-col :xs="24" :xl="13">
        <Async :load="() => api.quarantine() as unknown as Promise<Quarantine>">
          <template #default="{ data: q }">
            <a-card size="small">
              <template #title>装载隔离区</template>
              <template #extra>
                <a-tag color="warning">未处理 {{ q.unresolved ?? q.items.length }} 行</a-tag>
                <span class="dim" style="margin-left: 8px">{{ groupsOf(q.items).length }} 组缺口</span>
              </template>

              <a-empty v-if="!q.items.length" :image-style="{ height: '40px' }" description="隔离区为空：没有装不进去的行" />

              <div v-else class="dv-groups">
                <div v-for="g in groupsOf(q.items)" :key="g.key" class="dv-group">
                  <div class="dv-g-top">
                    <a-tag color="warning" class="dv-gap">{{ g.gap }}</a-tag>
                    <span class="dv-g-cnt">{{ g.rows.length }} 行</span>
                    <a-tag :color="g.resolved ? 'success' : 'warning'">{{ g.resolved ? '已处理' : '未处理' }}</a-tag>
                    <div class="grow"></div>
                    <a-button size="small" @click="toggle(g.key)">
                      {{ open.has(g.key) ? '收起明细' : '看明细' }}
                    </a-button>
                  </div>
                  <div class="dv-g-src">{{ g.source }}</div>
                  <div class="dv-g-reason">{{ g.reason }}</div>
                  <div class="dv-g-meta">
                    <span v-if="impactOf(g.rows).depts">涉及 <b>{{ impactOf(g.rows).depts }}</b> 个部门</span>
                    <span v-if="impactOf(g.rows).items">· <b>{{ impactOf(g.rows).items }}</b> 个费用项目</span>
                    <span v-if="impactOf(g.rows).has">· 合计 <b>{{ money(impactOf(g.rows).sum) }}</b> 未进入审核</span>
                  </div>
                  <div v-if="open.has(g.key)" class="dv-tbl-wrap">
                    <table class="dv-tbl">
                      <thead>
                        <tr>
                          <th v-for="c in colsOf(g.rows)" :key="c" :class="{ amt: c === 'amount' }">
                            {{ COL_CN[c] || c }}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="r in g.rows" :key="r.id">
                          <td v-for="c in colsOf(g.rows)" :key="c" :class="{ amt: c === 'amount' }">
                            {{ cell(r.row_data?.[c], c) }}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
              <div class="dv-policy">
                <b>为什么隔离而不是丢掉：</b>静默丢弃这几行费用，往后没有任何规则看得到它们；
                凭明细凭空造一张主表，按核算主体、业务类型判的规则会拿到编出来的值。两种都不做——
                主表补上这张单之后再装。
              </div>
            </a-card>
          </template>
        </Async>
      </a-col>

      <a-col :xs="24" :xl="11">
        <Async :load="() => api.consistency() as unknown as Promise<Consistency>">
          <template #default="{ data: c }">
            <a-card size="small">
              <template #title>审计一致性自检</template>
              <template #extra>
                <a-tag :color="c.ok ? 'success' : 'error'">{{ c.ok ? '通过' : '有异常' }}</a-tag>
              </template>

              <div class="dv-ck">
                <div class="dv-ck-lt">硬约束 <em>必须为 0，不为 0 就是代码绕过了某个阶段</em></div>
                <div class="dv-ck-row">
                  <span class="dv-ck-name">轮次缺 Act 记录</span>
                  <span class="dv-ck-v" :class="{ bad: cOf(c, 'runs_without_act') }">{{ cOf(c, 'runs_without_act') }}</span>
                  <span class="dv-ck-ok" :class="{ bad: cOf(c, 'runs_without_act') }">{{ cOf(c, 'runs_without_act') ? '异常' : '✓' }}</span>
                </div>
                <div class="dv-ck-row">
                  <span class="dv-ck-name">未脱敏快照</span>
                  <span class="dv-ck-v" :class="{ bad: cOf(c, 'unmasked') }">{{ cOf(c, 'unmasked') }}</span>
                  <span class="dv-ck-ok" :class="{ bad: cOf(c, 'unmasked') }">{{ cOf(c, 'unmasked') ? '异常' : '✓' }}</span>
                </div>
                <div class="dv-ck-row">
                  <span class="dv-ck-name">卡住超 1 小时的周期</span>
                  <span class="dv-ck-v" :class="{ bad: cOf(c, 'stuck') }">{{ cOf(c, 'stuck') }}</span>
                  <span class="dv-ck-ok" :class="{ bad: cOf(c, 'stuck') }">{{ cOf(c, 'stuck') ? '异常' : '✓' }}</span>
                </div>

                <div class="dv-ck-lt" style="margin-top: 16px">
                  对账 <em>不要求相等：业务数据可清理，审计台账按设计不可删</em>
                </div>
                <div class="dv-ck-pair">
                  <span class="dv-ck-name">审核轮次 review_run</span>
                  <span class="dv-ck-v">{{ cOf(c, 'runs') }}</span>
                </div>
                <div class="dv-ck-pair">
                  <span class="dv-ck-name">对应 Act 记录</span>
                  <span class="dv-ck-v">{{ cOf(c, 'logged_runs') }}</span>
                  <span class="dv-ck-delta">+{{ cOf(c, 'orphan_act_records') }}</span>
                </div>
                <div class="dv-ck-pair">
                  <span class="dv-ck-name">在途信号 agent_signal</span>
                  <span class="dv-ck-v">{{ cOf(c, 'signals') }}</span>
                </div>
                <div class="dv-ck-pair">
                  <span class="dv-ck-name">对应信号台账</span>
                  <span class="dv-ck-v">{{ cOf(c, 'logged_signals') }}</span>
                  <span class="dv-ck-delta">+{{ Math.max(0, cOf(c, 'logged_signals') - cOf(c, 'signals')) }}</span>
                </div>

                <div v-if="(c.problems || []).length" class="dv-ck-prob">
                  <div v-for="(p, i) in c.problems" :key="i">{{ p }}</div>
                </div>
                <div v-else class="dv-ck-foot">
                  台账比业务表多出的部分来自历史轮次——业务数据被重新生成过，
                  审计记录按设计不删，所以只作为信息列出，不算失败。
                </div>
              </div>
            </a-card>
          </template>
        </Async>
      </a-col>
    </a-row>
  </div>
</template>

<style scoped>
.dv-panel { margin-bottom: 16px; }
.grow { flex: 1; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.dv-hint { font-size: 11.5px; color: rgba(0, 0, 0, .4); margin: -8px 0 8px; }

.dv-stack { padding: 0 0 4px; }
.dv-bar {
  display: flex; align-items: stretch; height: 26px; padding: 0;
  border-radius: 6px; overflow: hidden; background: #f5f5f5; gap: 2px;
}
.dv-bar i {
  display: flex; align-items: center; justify-content: center;
  min-width: 0; border-radius: 2px;
}
.dv-bar i span { font-size: 11.5px; font-weight: 600; color: #fff; }
.dv-legend { display: flex; align-items: center; gap: 18px; margin-top: 10px; font-size: 12px; color: rgba(0, 0, 0, .55); flex-wrap: wrap; }
.dv-legend span { display: inline-flex; align-items: center; gap: 6px; }
.dv-legend i { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
.dv-legend b { color: rgba(0, 0, 0, .85); font-weight: 600; }
.dv-rate b { font-size: 16px; color: rgba(0, 0, 0, .85); margin-left: 4px; }

.dv-tiles { margin-top: 14px; border-top: 1px solid #f0f0f0; }
.dv-tile-wrap { min-width: 0; }
.dv-tile { padding: 16px 20px 12px 0; position: relative; }
.dv-tile::before { content: ''; position: absolute; left: 0; right: 12px; top: -1px; height: 2px; border-radius: 2px; }
.dv-tile.ok::before { background: #52c41a; }
.dv-tile.warn::before { background: #faad14; }
.dv-tile.bad::before { background: #ff4d4f; }
.dv-tile-top { display: flex; align-items: center; gap: 8px; }
.dv-tile-name { font-size: 13px; font-weight: 600; }
.dv-tile-v { font-size: 28px; font-weight: 700; line-height: 1.15; margin: 6px 0; letter-spacing: -.5px; font-variant-numeric: tabular-nums; }
.dv-tile-v small { font-size: 13px; font-weight: 400; color: rgba(0, 0, 0, .4); margin-left: 4px; letter-spacing: 0; }
.dv-tile-d { font-size: 11.5px; color: rgba(0, 0, 0, .45); line-height: 1.55; }
.dv-note {
  margin-top: 14px; padding: 10px 14px; border-radius: 6px;
  background: #fafafa; color: rgba(0, 0, 0, .55); font-size: 12px; line-height: 1.7;
}

.dv-groups { display: flex; flex-direction: column; }
.dv-group { padding: 14px 0 16px; }
.dv-group + .dv-group { border-top: 1px solid #f0f0f0; }
.dv-g-top { display: flex; align-items: center; gap: 10px; }
.dv-g-cnt { font-size: 15px; font-weight: 600; }
.dv-g-src { font-size: 12.5px; margin-top: 8px; overflow-wrap: anywhere; }
.dv-g-reason { font-size: 12px; color: rgba(0, 0, 0, .55); margin-top: 3px; line-height: 1.6; }
.dv-g-meta { font-size: 11.5px; color: rgba(0, 0, 0, .45); margin-top: 8px; }
.dv-g-meta b { color: rgba(0, 0, 0, .8); font-weight: 600; }
.dv-tbl-wrap { margin-top: 10px; overflow-x: auto; }
.dv-tbl { width: 100%; background: #fafafa; border-radius: 6px; border-collapse: collapse; }
.dv-tbl th, .dv-tbl td { padding: 7px 10px; font-size: 12px; text-align: left; border-bottom: 1px solid #f0f0f0; }
.dv-tbl th.amt, .dv-tbl td.amt { text-align: right; font-variant-numeric: tabular-nums; }
.dv-policy {
  margin-top: 12px; padding: 12px 14px; border-radius: 8px;
  background: #fafafa; border: 1px solid #f0f0f0;
  font-size: 11.5px; color: rgba(0, 0, 0, .45); line-height: 1.75;
}
.dv-policy b { color: rgba(0, 0, 0, .6); font-weight: 600; }

.dv-ck { padding: 0; }
.dv-ck-lt { font-size: 12px; color: rgba(0, 0, 0, .45); margin: 10px 0 6px; }
.dv-ck-lt em { font-style: normal; opacity: .8; margin-left: 6px; }
.dv-ck-row, .dv-ck-pair {
  display: grid; grid-template-columns: 1fr 60px 40px; align-items: center;
  padding: 7px 0; border-bottom: 1px solid #f0f0f0; font-size: 12.5px;
}
.dv-ck-pair { grid-template-columns: 1fr 70px 50px; }
.dv-ck-name { color: rgba(0, 0, 0, .6); }
.dv-ck-v { text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; }
.dv-ck-v.bad { color: #ff4d4f; }
.dv-ck-ok { text-align: right; color: #52c41a; font-size: 12px; }
.dv-ck-ok.bad { color: #ff4d4f; }
.dv-ck-delta { text-align: right; font-size: 11.5px; color: rgba(0, 0, 0, .4); }
.dv-ck-foot { margin-top: 12px; font-size: 11.5px; color: rgba(0, 0, 0, .45); line-height: 1.7; }
.dv-ck-prob { margin-top: 12px; font-size: 12px; color: #ff4d4f; line-height: 1.6; }
</style>
