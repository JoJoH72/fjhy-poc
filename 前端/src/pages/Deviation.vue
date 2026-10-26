<script setup lang="ts">
/**
 * 偏差与回流（领导查询）：判定质量 + 数据缺口 + 审计一致性。
 *
 * 全部真实接口：/api/deviation（四类判定与口径注）、
 * /api/audit/quarantine（装载隔离区）、/api/audit/consistency（一致性自检）。
 * demo 里的「待人工成因（G-04 等）」后端没有对应接口——成因清单在
 * 《数据接入要求单》文档里，页面上不造数。误报按规则的分布接口有，
 * 但当前为 0 条，所以整块不渲染。
 *
 * 2026-10-25 重做：上一版四个象限卡的文字互相压、八条一致性指标只剩光秃秃的
 * 数字没有标签、隔离区把同一张单的八行明细平铺成八行「一模一样」的记录。
 * 现在：判定条 + 四类卡片一张卡讲完；隔离区按「缺口 + 来源 + 原因」归组，
 * 明细按需展开并给出影响面；一致性拆成「硬约束」与「对账信息」两段，带方向说明。
 *
 * **类名全部带 dv- 前缀**：design.css 里已有 `.v2 .qbar`（候选审核的批量条），
 * 同名会吃到它的 padding 与对齐，判定条被撑出上下留白、绿段缩成一条。scoped
 * 不能解决问题——同特异度时只比源码顺序，未声明的属性照样被全局规则接管。
 */
import { computed, ref } from 'vue'
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

/** 卡片说明直接用后端 kinds 的口径原文；原文太短（如「一致」两个字）时用本地一句补足，
    不重复写「AI 命中 · 人工放行」——那句话已经含在口径原文里了 */
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

/* ── 隔离区：同一张单的明细行要归成一组，不然八行看起来像八条不同的问题 ── */
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

/** 明细行的列：后端 row_data 的键不固定，按「单据号 / 部门 / 项目 / 金额」优先排，
    其余键兜底跟在后面——写死列名会在换一份源文件时整列空白 */
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

/** 这一组隔离行的影响面：几个部门、几个费用项目、合计多少钱。
    领导看隔离区关心的不是「有 8 行」，是「多少钱、多少条规则现在看不见它们」。 */
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
  <div class="v2">
    <!-- ① 判定质量 -->
    <Async :load="() => api.deviation() as unknown as Promise<Deviation>">
      <template #default="{ data }">
        <div class="card dv-panel">
          <div class="dv-head">
            <div class="dv-h1">
              <h2>AI × 人工 交叉判定</h2>
              <span class="dv-count num">{{ totalOf(data) }} 条判定记录</span>
              <div class="grow"></div>
              <span class="dv-flags">
                <span v-if="!nOf(data, 'false_positive')" class="pill ok">零误报</span>
                <span v-if="!nOf(data, 'false_negative')" class="pill ok">零漏报</span>
              </span>
            </div>
            <div class="dv-hint">每张单可能同时有「结论」与「命中」两类反馈，所以记录数不等于单据数</div>
          </div>

          <div class="dv-stack">
            <div class="dv-bar" role="img"
                 :aria-label="`判定分布：${segsOf(data).map((s) => s.name + ' ' + s.n).join('，')}`">
              <i v-for="s in segsOf(data).filter((x) => x.n > 0)" :key="s.key"
                 :class="s.cls" :style="{ flexGrow: s.n }" :title="`${s.name} ${s.n}`">
                <span class="num">{{ s.n }}</span>
              </i>
            </div>
            <div class="dv-legend">
              <span v-for="s in segsOf(data)" :key="s.key">
                <i :class="s.cls"></i>{{ s.name }} <b class="num">{{ s.n }}</b>
              </span>
              <div class="grow"></div>
              <span class="dv-rate" title="一致条数 ÷ 判定记录数；误报与漏报单列，不合成准确率">一致率 <b class="num">{{ rateOf(data) }}%</b></span>
            </div>
          </div>

          <div class="dv-tiles">
            <div v-for="k in KIND_ORDER" :key="k" class="dv-tile" :class="META[k].cls" :title="META[k].pos">
              <div class="dv-tile-top">
                <span class="dv-tile-name">{{ META[k].name }}</span>
                <span v-if="!nOf(data, k)" class="dv-zero">零</span>
              </div>
              <div class="dv-tile-v num">{{ nOf(data, k) }}<small> / {{ totalOf(data) }}</small></div>
              <div class="dv-tile-d"><MarkText :text="tileDesc(data, k)" /></div>
            </div>
          </div>

          <div v-if="data.note" class="dv-note"><MarkText :text="data.note" /></div>
        </div>

        <div v-if="(data.false_positive_by_rule || []).length" class="card" style="margin-bottom: 16px">
          <div class="card-head">
            <h2>误报按规则排序</h2>
            <div class="grow"></div>
            <span class="dv-hint">点规则看它在规则资产里的口径</span>
          </div>
          <table class="tbl">
            <thead><tr><th style="width: 220px">规则</th><th class="num" style="width: 110px">误报次数</th></tr></thead>
            <tbody>
              <tr v-for="r in data.false_positive_by_rule" :key="r.rule_id">
                <td><router-link :to="`/asset/rules/${r.rule_id}`">{{ r.rule_id }}</router-link></td>
                <td class="num">{{ r.n }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </Async>

    <!-- ② 隔离区 + 一致性 -->
    <div class="dv-grid">
      <Async :load="() => api.quarantine() as unknown as Promise<Quarantine>">
        <template #default="{ data: q }">
          <div class="card">
            <div class="card-head">
              <h2>装载隔离区</h2>
              <span class="pill warn num">未处理 {{ q.unresolved ?? q.items.length }} 行</span>
              <div class="grow"></div>
              <span class="dv-hint">{{ groupsOf(q.items).length }} 组缺口</span>
            </div>

            <div v-if="!q.items.length" class="dv-empty">隔离区为空：没有装不进去的行。</div>

            <div v-else class="dv-groups">
              <div v-for="g in groupsOf(q.items)" :key="g.key" class="dv-group">
                <div class="dv-g-top">
                  <span class="dv-gap num">{{ g.gap }}</span>
                  <span class="dv-g-cnt num">{{ g.rows.length }} 行</span>
                  <span class="pill" :class="g.resolved ? 'ok' : 'warn'">{{ g.resolved ? '已处理' : '未处理' }}</span>
                  <div class="grow"></div>
                  <button class="btn ghost" @click="toggle(g.key)">
                    {{ open.has(g.key) ? '收起明细' : '看明细' }}
                  </button>
                </div>
                <div class="dv-g-src">{{ g.source }}</div>
                <div class="dv-g-reason">{{ g.reason }}</div>
                <div class="dv-g-meta">
                  <span v-if="impactOf(g.rows).depts">涉及 <b class="num">{{ impactOf(g.rows).depts }}</b> 个部门</span>
                  <span v-if="impactOf(g.rows).items">· <b class="num">{{ impactOf(g.rows).items }}</b> 个费用项目</span>
                  <span v-if="impactOf(g.rows).has">· 合计 <b class="num">{{ money(impactOf(g.rows).sum) }}</b> 未进入审核</span>
                </div>
                <table v-if="open.has(g.key)" class="tbl dv-tbl">
                  <thead>
                    <tr>
                      <th v-for="c in colsOf(g.rows)" :key="c" :class="{ num: c === 'amount' }">
                        {{ COL_CN[c] || c }}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="r in g.rows" :key="r.id">
                      <td v-for="c in colsOf(g.rows)" :key="c" :class="{ num: c === 'amount', mono: c === 'bill_no' }">
                        {{ cell(r.row_data?.[c], c) }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
            <div class="dv-policy">
              <b>为什么隔离而不是丢掉：</b>静默丢弃这几行费用，往后没有任何规则看得到它们；
              凭明细凭空造一张主表，按核算主体、业务类型判的规则会拿到编出来的值。两种都不做——
              主表补上这张单之后再装。
            </div>
          </div>
        </template>
      </Async>

      <Async :load="() => api.consistency() as unknown as Promise<Consistency>">
        <template #default="{ data: c }">
          <div class="card">
            <div class="card-head">
              <h2>审计一致性自检</h2>
              <div class="grow"></div>
              <span class="pill" :class="c.ok ? 'ok' : 'bad'">{{ c.ok ? '通过' : '有异常' }}</span>
            </div>

            <div class="dv-ck">
              <div class="dv-ck-lt">硬约束 <em>必须为 0，不为 0 就是代码绕过了某个阶段</em></div>
              <div class="dv-ck-row">
                <span class="dv-ck-name">轮次缺 Act 记录</span>
                <span class="dv-ck-v num" :class="{ bad: cOf(c, 'runs_without_act') }">{{ cOf(c, 'runs_without_act') }}</span>
                <span class="dv-ck-ok" :class="{ bad: cOf(c, 'runs_without_act') }">{{ cOf(c, 'runs_without_act') ? '异常' : '✓' }}</span>
              </div>
              <div class="dv-ck-row">
                <span class="dv-ck-name">未脱敏快照</span>
                <span class="dv-ck-v num" :class="{ bad: cOf(c, 'unmasked') }">{{ cOf(c, 'unmasked') }}</span>
                <span class="dv-ck-ok" :class="{ bad: cOf(c, 'unmasked') }">{{ cOf(c, 'unmasked') ? '异常' : '✓' }}</span>
              </div>
              <div class="dv-ck-row">
                <span class="dv-ck-name">卡住超 1 小时的周期</span>
                <span class="dv-ck-v num" :class="{ bad: cOf(c, 'stuck') }">{{ cOf(c, 'stuck') }}</span>
                <span class="dv-ck-ok" :class="{ bad: cOf(c, 'stuck') }">{{ cOf(c, 'stuck') ? '异常' : '✓' }}</span>
              </div>

              <div class="dv-ck-lt" style="margin-top: 16px">
                对账 <em>不要求相等：业务数据可清理，审计台账按设计不可删</em>
              </div>
              <div class="dv-ck-pair">
                <span class="dv-ck-name">审核轮次 review_run</span>
                <span class="dv-ck-v num">{{ cOf(c, 'runs') }}</span>
              </div>
              <div class="dv-ck-pair">
                <span class="dv-ck-name">对应 Act 记录</span>
                <span class="dv-ck-v num">{{ cOf(c, 'logged_runs') }}</span>
                <span class="dv-ck-delta num">+{{ cOf(c, 'orphan_act_records') }}</span>
              </div>
              <div class="dv-ck-pair">
                <span class="dv-ck-name">在途信号 agent_signal</span>
                <span class="dv-ck-v num">{{ cOf(c, 'signals') }}</span>
              </div>
              <div class="dv-ck-pair">
                <span class="dv-ck-name">对应信号台账</span>
                <span class="dv-ck-v num">{{ cOf(c, 'logged_signals') }}</span>
                <span class="dv-ck-delta num">+{{ Math.max(0, cOf(c, 'logged_signals') - cOf(c, 'signals')) }}</span>
              </div>

              <div v-if="(c.problems || []).length" class="dv-ck-prob">
                <div v-for="(p, i) in c.problems" :key="i">{{ p }}</div>
              </div>
              <div v-else class="dv-ck-foot">
                台账比业务表多出的部分来自历史轮次——业务数据被重新生成过，
                审计记录按设计不删，所以只作为信息列出，不算失败。
              </div>
            </div>
          </div>
        </template>
      </Async>
    </div>
  </div>
</template>

<style scoped>
/* ── ① 判定质量：一张卡讲完 ───────────────────────────── */
.dv-panel { margin-bottom: 16px; }
.dv-head { padding: 16px 20px 12px; }
.dv-h1 { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.dv-h1 h2 { font-size: 15px; font-weight: 600; color: var(--text); margin: 0; white-space: nowrap; }
.dv-count { font-size: 12.5px; color: var(--muted); white-space: nowrap; }
.dv-flags { display: flex; gap: 8px; flex: none; align-items: center; }
.dv-hint { font-size: 11.5px; color: var(--weak); margin-top: 4px; }
.card-head .dv-hint { margin-top: 0; }

.dv-stack { padding: 0 20px 4px; }
.dv-bar {
  display: flex; align-items: stretch; height: 26px; padding: 0;
  border-radius: 6px; overflow: hidden; background: var(--panel-2); gap: 2px;
}
.dv-bar i {
  display: flex; align-items: center; justify-content: center;
  min-width: 0; transition: flex-grow .3s; border-radius: 2px;
}
.dv-bar i span { font-size: 11.5px; font-weight: 600; color: #fff; }
.dv-bar i.ok { background: linear-gradient(180deg, #2f9a63, #1e7e4e); }
.dv-bar i.warn { background: linear-gradient(180deg, #d8b866, #b9912f); }
.dv-bar i.bad { background: linear-gradient(180deg, #d05340, #c0351f); }

.dv-legend { display: flex; align-items: center; gap: 18px; margin-top: 10px; font-size: 12px; color: var(--muted); flex-wrap: wrap; }
.dv-legend span { display: inline-flex; align-items: center; gap: 6px; }
.dv-legend i { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
.dv-legend i.ok { background: var(--ok); }
.dv-legend i.warn { background: var(--gold); }
.dv-legend i.bad { background: var(--bad); }
.dv-legend b { color: var(--text); font-weight: 600; margin-left: 2px; }
.dv-rate { font-size: 12.5px; color: var(--muted); }
.dv-rate b { font-size: 16px; color: var(--text); margin-left: 4px; }

.dv-tiles { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--panel-2); margin-top: 14px; }
.dv-tile { padding: 18px 20px; min-width: 0; position: relative; }
.dv-tile + .dv-tile { border-left: 1px solid var(--panel-2); }
.dv-tile::before { content: ''; position: absolute; left: 20px; right: 20px; top: 0; height: 2px; border-radius: 2px; }
.dv-tile.ok::before { background: var(--ok); }
.dv-tile.warn::before { background: var(--gold); }
.dv-tile.bad::before { background: var(--bad); }
.dv-tile-top { display: flex; align-items: center; gap: 8px; }
.dv-tile-name { font-size: 13px; font-weight: 600; color: var(--text); }
.dv-zero { font-size: 10.5px; line-height: 16px; padding: 0 6px; border-radius: 4px; color: var(--ok); background: var(--ok-bg); }
.dv-tile-v { font-size: 30px; font-weight: 700; line-height: 1.15; color: var(--text); margin: 6px 0 6px; letter-spacing: -.5px; }
.dv-tile-v small { font-size: 13px; font-weight: 400; color: var(--weak); margin-left: 4px; letter-spacing: 0; }
.dv-tile-d { font-size: 11.5px; color: var(--weak); line-height: 1.55; }
.dv-note {
  margin: 0 20px 18px; padding: 10px 14px; border-radius: var(--r-s);
  background: var(--panel-2); color: var(--muted); font-size: 12px; line-height: 1.7;
}

/* ── ② 两列 ─────────────────────────────────────────── */
.dv-grid { display: grid; grid-template-columns: 1.3fr 1fr; gap: 16px; align-items: start; }

/* 隔离区 */
.dv-groups { padding: 4px 0 8px; }
.dv-group { padding: 14px 20px 16px; }
.dv-group + .dv-group { border-top: 1px solid var(--panel-2); }
.dv-g-top { display: flex; align-items: center; gap: 10px; }
.dv-gap { font-size: 12px; font-weight: 600; color: var(--warn); background: var(--warn-bg); border-radius: 4px; padding: 1px 8px; }
.dv-g-cnt { font-size: 15px; font-weight: 600; color: var(--text); }
.dv-g-src { font-size: 12.5px; color: var(--text); margin-top: 8px; overflow-wrap: anywhere; }
.dv-g-reason { font-size: 12px; color: var(--muted); margin-top: 3px; line-height: 1.6; }
.dv-g-meta { font-size: 11.5px; color: var(--weak); margin-top: 8px; }
.dv-g-meta b { color: var(--text); font-weight: 600; }
.dv-tbl { margin-top: 10px; background: var(--panel-2); border-radius: var(--r-s); }
.dv-tbl th, .dv-tbl td { padding: 7px 10px; font-size: 12px; }
.dv-empty { padding: 28px 20px; text-align: center; color: var(--weak); font-size: 12.5px; }
.dv-policy {
  margin: 0 20px 18px; padding: 12px 14px; border-radius: 8px;
  background: var(--panel-2); border: 1px solid var(--line);
  font-size: 11.5px; color: var(--weak); line-height: 1.75;
}
.dv-policy b { color: var(--muted); font-weight: 600; }

/* 一致性 */
.dv-ck { padding: 4px 20px 18px; }
.dv-ck-lt { font-size: 12px; color: var(--weak); margin: 10px 0 6px; }
.dv-ck-lt em { font-style: normal; color: var(--weak); opacity: .8; margin-left: 6px; }
.dv-ck-row, .dv-ck-pair {
  display: grid; grid-template-columns: 1fr 60px 34px; align-items: center;
  padding: 7px 0; border-bottom: 1px solid var(--panel-2); font-size: 12.5px;
}
.dv-ck-pair { grid-template-columns: 1fr 70px 44px; }
.dv-ck-row:last-child, .dv-ck-pair:last-child { border-bottom: 0; }
.dv-ck-name { color: var(--muted); }
.dv-ck-v { text-align: right; color: var(--text); font-weight: 600; }
.dv-ck-v.bad { color: var(--bad); }
.dv-ck-ok { text-align: right; color: var(--ok); font-size: 12px; }
.dv-ck-ok.bad { color: var(--bad); }
.dv-ck-delta { text-align: right; font-size: 11.5px; color: var(--weak); }
.dv-ck-foot { margin-top: 12px; font-size: 11.5px; color: var(--weak); line-height: 1.7; }
.dv-ck-prob { margin-top: 12px; font-size: 12px; color: var(--bad); line-height: 1.6; }

@media (max-width: 1180px) {
  .dv-grid { grid-template-columns: 1fr; }
  .dv-tiles { grid-template-columns: repeat(2, 1fr); row-gap: 18px; }
  .dv-tile:nth-child(3) { border-left: 0; }
}
</style>
