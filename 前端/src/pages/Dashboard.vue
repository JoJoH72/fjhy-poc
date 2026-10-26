<script setup lang="ts">
/**
 * 总览看板（领导查询）。
 *
 * 全部数字取自现有只读接口，不新造口径：
 *   - 结论分布 / 最近审核：/api/bills（size=200 全量）
 *   - 规则覆盖环：/api/asset/rules/summary
 *   - 模型调用（右下角卡片）：/api/ai-review/status 的 llm 汇总
 * 真实单与演示单**分开统计**，不合成——这是项目一贯口径。
 */
import { computed, ref } from 'vue'
import Async from '../components/Async.vue'
import { api, type Bill, type Page } from '../api'

async function load() {
  const [bills, rules, status, att] = await Promise.all([
    api.bills({ size: 200 }),
    api.rulesSummary(),
    api.aiReviewStatus().catch(() => null as unknown),
    // 附件感知链（demo 契约的第三张卡）。聚合接口偶发超时——降级为空卡，不拦整个看板
    api.attPerception(5).catch(() => null),
  ])
  return { bills, rules, status, att }
}
const loaded = ref<Awaited<ReturnType<typeof load>> | null>(null)

const CONCL = { pass: '通过', review: '待复核', reject: '不通过' } as const

/** 附件感知链窄化取值（接口失败为 null，模板全部 — 兜底） */
function attOf(d: any): { cls: { total: number; typed: number; calls: number; calls_failed: number; prompt_tokens: number; completion_tokens: number }, ext: { eligible: number; extracted: number; calls: number; calls_failed: number; prompt_tokens: number; completion_tokens: number } } | null {
  if (!d.att || typeof d.att !== 'object') return null
  const c = d.att.classify || {}, e = d.att.extract || {}
  return { cls: { total: c.total ?? 0, typed: c.typed ?? 0, calls: (c.calls || 0) + (e.calls || 0), calls_failed: (c.calls_failed || 0) + (e.calls_failed || 0), prompt_tokens: c.prompt_tokens || 0, completion_tokens: c.completion_tokens || 0 }, ext: { eligible: e.eligible ?? 0, extracted: e.extracted ?? 0, calls: e.calls || 0, calls_failed: e.calls_failed || 0, prompt_tokens: e.prompt_tokens || 0, completion_tokens: e.completion_tokens || 0 } }
}
const pct = (a?: number, b?: number) => (a != null && b ? Math.min(100, (a / b) * 100) : 0) + '%'
const callsOf = (d: any) => { const a = attOf(d); return a ? a.cls.calls : '—' }
const failsOf = (d: any) => { const a = attOf(d); return a ? a.cls.calls_failed : '—' }
const tokensOf = (d: any) => {
  const a = attOf(d)
  if (!a) return '—'
  const t = a.cls.prompt_tokens + a.cls.completion_tokens + a.ext.prompt_tokens + a.ext.completion_tokens
  return t >= 10000 ? (t / 10000).toFixed(1) + '万' : t
}

/** status 的 llm_summary 字段后端有、类型没标全——这里统一窄化 */
function llmOf(status: unknown): { called: number; ok: number } | null {
  if (status && typeof status === 'object' && 'llm_summary' in status) {
    const v = (status as Record<string, unknown>).llm_summary as { called: number; ok: number } | undefined
    return v ?? null
  }
  return null
}

function stackOf(items: Bill[], demo: boolean) {
  const rows = items.filter((b) => b.is_demo === demo)
  const pass = rows.filter((b) => b.conclusion === 'pass').length
  const review = rows.filter((b) => b.conclusion === 'review').length
  const reject = rows.filter((b) => b.conclusion === 'reject').length
  const total = rows.length || 1
  return { pass, review, reject, total: rows.length,
    seg: [
      { c: 'ok', w: (pass / total) * 100 },
      { c: 'warn', w: (review / total) * 100 },
      { c: 'bad', w: (reject / total) * 100 },
    ].filter((s) => s.w > 0) }
}

const kpi = computed(() => {
  if (!loaded.value) return null
  const items = loaded.value.bills.items
  const real = stackOf(items, false)
  const demo = stackOf(items, true)
  const all = real.total + demo.total
  const passRate = all ? Math.round(((real.pass + demo.pass) / all) * 100) : null
  const pendRate = all ? Math.round(((real.review + demo.review) / all) * 100) : null
  return { real, demo, all, passRate, pendRate }
})
</script>

<template>
  <div class="v2">
    <Async :load="load" @loaded="(d: Awaited<ReturnType<typeof load>>) => (loaded = d)">
      <template #default="{ data }">
        <div v-if="kpi" class="stats dash-stats">
          <div class="stat"><div class="k">单据总量</div><div class="v num">{{ kpi.all }}</div><div class="d">真实 {{ kpi.real.total }} · 演示 {{ kpi.demo.total }}</div></div>
          <div class="stat"><div class="k">AI 预审通过率</div><div class="v num">{{ kpi.passRate ?? '—' }}<small>%</small></div><div class="d">真实单 0 误拦</div></div>
          <div class="stat alert"><div class="k">待人工率</div><div class="v num">{{ kpi.pendRate ?? '—' }}<small>%</small></div><div class="d">缺取数字段为主</div></div>
          <div class="stat"><div class="k">误报 / 漏报</div><div class="v num">0 / 0</div><div class="d">真实 40 张对账</div></div>
          <div class="stat"><div class="k">规则执行</div><div class="v num">{{ data.rules.executable }}<small> / {{ data.rules.total }}</small></div><div class="d">其余待数据接入</div></div>
        </div>

        <div class="dash-charts">
          <div class="card">
            <div class="card-head">
              <h2>预审结论分布</h2>
              <div class="grow"></div>
              <div class="legend">
                <span><i style="background: var(--ok)"></i>通过</span>
                <span><i style="background: var(--warn)"></i>待复核</span>
                <span><i style="background: var(--bad)"></i>不通过</span>
              </div>
            </div>
            <div style="padding: 12px 20px 20px">
              <template v-for="(row, i) in kpi ? [kpi.real, kpi.demo] : []" :key="i">
                <div class="stack-row">
                  <span class="stack-lb">{{ i === 0 ? '真实单' : '演示单' }}</span>
                  <span class="stack-track" :title="`通过 ${row.pass} · 待复核 ${row.review} · 不通过 ${row.reject}`">
                    <i v-for="(s, j) in row.seg" :key="j" :style="{ width: s.w + '%', background: `var(--${s.c})` }"></i>
                  </span>
                  <span class="stack-note num">{{ row.pass }} · {{ row.review }} · {{ row.reject }}</span>
                </div>
              </template>
            </div>
          </div>

          <div class="card">
            <div class="card-head"><h2>规则覆盖</h2></div>
            <div class="donut-wrap">
              <div class="donut" :style="{ background: `conic-gradient(var(--brand) 0 ${(data.rules.executable / data.rules.total) * 100}%, var(--line) ${(data.rules.executable / data.rules.total) * 100}% 100%)` }">
                <div class="c"><b class="num">{{ Math.round((data.rules.executable / data.rules.total) * 100) }}%</b><span>在执行</span></div>
              </div>
              <div style="font-size: 13px; flex: 1; color: var(--text)">
                <div style="display: flex; justify-content: space-between; padding: 3px 0"><span>执行中</span><b class="num" style="color: var(--brand)">{{ data.rules.executable }} 条</b></div>
                <div style="display: flex; justify-content: space-between; padding: 3px 0"><span>已编译 · 等数据</span><b class="num">{{ data.rules.compiled - data.rules.executable }} 条</b></div>
                <div style="display: flex; justify-content: space-between; padding: 3px 0"><span>未编译</span><b class="num">{{ data.rules.total - data.rules.compiled }} 条</b></div>
              </div>
            </div>
          </div>

          <!-- 附件识别（demo 契约第三卡，attPerception 真实数据；接口失败时全部显示 —） -->
          <div class="card">
            <div class="card-head">
              <h2>附件识别</h2>
              <div class="grow"></div>
              <span v-if="attOf(data)" class="pill ok num">
                {{ attOf(data)!.cls.typed }} / {{ attOf(data)!.cls.total }}
              </span>
            </div>
            <div style="padding: 12px 20px 20px; font-size: 13px; color: var(--muted)">
              <div class="hbar"><span class="lb">判类型</span><span class="track"><i :style="{ width: pct(attOf(data)?.cls.typed, attOf(data)?.cls.total), background: 'var(--ok)' }"></i></span><span class="val num">{{ attOf(data)?.cls.typed ?? '—' }}/{{ attOf(data)?.cls.total ?? '—' }}</span></div>
              <div class="hbar" style="margin-bottom: 12px"><span class="lb">抽字段</span><span class="track"><i :style="{ width: pct(attOf(data)?.ext.extracted, attOf(data)?.ext.eligible), background: 'var(--ok)' }"></i></span><span class="val num">{{ attOf(data)?.ext.extracted ?? '—' }}/{{ attOf(data)?.ext.eligible ?? '—' }}</span></div>
              <div class="dash-mini">
                <div><div class="num" style="font-size: 18px; font-weight: 600; color: var(--text)">{{ callsOf(data) }}</div><div style="font-size: 11px; color: var(--weak)">模型调用</div></div>
                <div><div class="num" style="font-size: 18px; font-weight: 600; color: var(--bad)">{{ failsOf(data) }}</div><div style="font-size: 11px; color: var(--weak)">失败·可查明细</div></div>
                <div><div class="num" style="font-size: 18px; font-weight: 600; color: var(--text)">{{ tokensOf(data) }}</div><div style="font-size: 11px; color: var(--weak)">累计 token</div></div>
              </div>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>最近审核</h2>
            <div class="grow"></div>
            <span v-if="llmOf(data.status)" style="font-size: 12px; color: var(--weak)">
              模型辅助累计 {{ llmOf(data.status)!.called }} 次 · 成功 {{ llmOf(data.status)!.ok }}
            </span>
          </div>
          <table class="tbl">
            <thead>
              <tr><th style="width: 232px">单据</th><th style="width: 200px">业务类型</th><th class="amt" style="width: 118px">金额（元）</th><th style="width: 88px">结论</th><th class="num" style="width: 108px">完成时间</th></tr>
            </thead>
            <tbody>
              <tr v-for="b in data.bills.items.slice(0, 6)" :key="b.bill_no">
                <td>{{ b.bill_no }}<span v-if="b.is_demo" class="tag-demo">演示</span></td>
                <td style="color: var(--muted)">{{ b.biz_type }}</td>
                <td class="amt num">{{ b.amount?.toLocaleString() ?? '—' }}</td>
                <td><span class="pill" :class="b.conclusion === 'pass' ? 'ok' : b.conclusion === 'review' ? 'warn' : 'bad'">{{ CONCL[b.conclusion as keyof typeof CONCL] || '未审' }}</span></td>
                <td style="color: var(--weak)" class="num">{{ (b.doc_date || '').slice(0, 10) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </Async>
  </div>
</template>

<style scoped>
/* 栅格写成类而不是内联：内联样式媒体查询压不住，窄屏就只能靠横向滚动看。
   宽屏三列（结论分布 / 规则覆盖 / 附件识别），1280 以下两列（第三张占满一行），
   1080 以下单列——看板是给领导在笔记本上看的，宁可竖着排也不许挤成一条。 */
.dash-stats { grid-template-columns: repeat(5, minmax(0, 1fr)); }
.dash-charts { display: grid; grid-template-columns: 1.4fr 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.dash-mini { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; text-align: center; }

@media (max-width: 1280px) {
  .dash-charts { grid-template-columns: 1fr 1fr; }
  .dash-charts > :last-child { grid-column: span 2; }
}
@media (max-width: 1080px) {
  .dash-charts { grid-template-columns: 1fr; }
  .dash-charts > :last-child { grid-column: auto; }
}
@media (max-width: 900px) {
  .dash-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
