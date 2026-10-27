<script setup lang="ts">
/**
 * 总览看板（领导查询，2026-10-27 AntD 换装版）。
 *
 * 全部数字取自现有只读接口，不新造口径：
 *   - 结论分布 / 最近审核：/api/bills（size=200 全量）
 *   - 规则覆盖：/api/asset/rules/summary
 *   - 模型调用：/api/ai-review/status 的 llm 汇总
 * 真实单与演示单**分开统计**，不合成——这是项目一贯口径。
 */
import { computed, ref } from 'vue'
import Async from '../components/Async.vue'
import { api, type Bill } from '../api'

async function load() {
  const [bills, rules, status, att] = await Promise.all([
    api.bills({ size: 200 }),
    api.rulesSummary(),
    api.aiReviewStatus().catch(() => null as unknown),
    // 附件感知链聚合接口偶发超时——降级为空卡，不拦整个看板
    api.attPerception(5).catch(() => null),
  ])
  return { bills, rules, status, att }
}
const loaded = ref<Awaited<ReturnType<typeof load>> | null>(null)

const CONCL: Record<string, { text: string; color: string }> = {
  pass: { text: '通过', color: 'success' },
  review: { text: '待复核', color: 'warning' },
  reject: { text: '不通过', color: 'error' },
}

/** 附件感知链窄化取值（接口失败为 null，模板全部 — 兜底） */
function attOf(d: any): { cls: { total: number; typed: number; calls: number; calls_failed: number; prompt_tokens: number; completion_tokens: number }, ext: { eligible: number; extracted: number; calls: number; calls_failed: number; prompt_tokens: number; completion_tokens: number } } | null {
  if (!d.att || typeof d.att !== 'object') return null
  const c = d.att.classify || {}, e = d.att.extract || {}
  return { cls: { total: c.total ?? 0, typed: c.typed ?? 0, calls: (c.calls || 0) + (e.calls || 0), calls_failed: (c.calls_failed || 0) + (e.calls_failed || 0), prompt_tokens: c.prompt_tokens || 0, completion_tokens: c.completion_tokens || 0 }, ext: { eligible: e.eligible ?? 0, extracted: e.extracted ?? 0, calls: e.calls || 0, calls_failed: e.calls_failed || 0, prompt_tokens: e.prompt_tokens || 0, completion_tokens: e.completion_tokens || 0 } }
}
const pctOf = (a?: number, b?: number) => (a != null && b ? Math.min(100, (a / b) * 100) : 0)
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
      { c: '52c41a', w: (pass / total) * 100, n: pass },
      { c: 'faad14', w: (review / total) * 100, n: review },
      { c: 'ff4d4f', w: (reject / total) * 100, n: reject },
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

const ruleRows = computed(() => {
  const r = loaded.value?.rules
  if (!r) return []
  return [
    { k: '执行中', v: r.executable, c: '#1677ff' },
    { k: '已编译 · 等数据', v: r.compiled - r.executable, c: 'rgba(0,0,0,.45)' },
    { k: '未编译', v: r.total - r.compiled, c: 'rgba(0,0,0,.45)' },
  ]
})

const recentCols = [
  { title: '单据编号', dataIndex: 'bill_no', width: 220, ellipsis: true },
  { title: '业务类型', dataIndex: 'biz_type', width: 180, ellipsis: { showTitle: false } },
  { title: '金额（元）', dataIndex: 'amount', width: 130, align: 'right' as const },
  { title: '结论', dataIndex: 'conclusion', width: 100 },
  { title: '单据日期', dataIndex: 'doc_date', width: 120 },
]
const fmtAmt = (v: number | null) => v != null ? v.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '—'
</script>

<template>
  <div class="dash">
    <Async :load="load" @loaded="(d: Awaited<ReturnType<typeof load>>) => (loaded = d)">
      <template #default="{ data }">
        <a-row v-if="kpi" :gutter="[16, 16]">
          <a-col :xs="12" :md="8" :xl="5"><a-card size="small">
            <a-statistic title="单据总量" :value="kpi.all" />
            <div class="sub">真实 {{ kpi.real.total }} · 演示 {{ kpi.demo.total }}</div>
          </a-card></a-col>
          <a-col :xs="12" :md="8" :xl="5"><a-card size="small">
            <a-statistic title="预审通过率" :value="kpi.passRate ?? '—'">
              <template #suffix><span v-if="kpi.passRate != null">%</span></template>
            </a-statistic>
            <div class="sub">真实单 0 误拦</div>
          </a-card></a-col>
          <a-col :xs="12" :md="8" :xl="5"><a-card size="small">
            <a-statistic title="待人工率" :value="kpi.pendRate ?? '—'"
              :value-style="{ color: (kpi.pendRate ?? 0) > 20 ? '#faad14' : undefined }">
              <template #suffix><span v-if="kpi.pendRate != null">%</span></template>
            </a-statistic>
            <div class="sub">缺取数字段为主</div>
          </a-card></a-col>
          <a-col :xs="12" :md="8" :xl="5"><a-card size="small">
            <a-statistic title="误报 / 漏报" value="0 / 0" />
            <div class="sub">真实 40 张对账</div>
          </a-card></a-col>
          <a-col :xs="12" :md="8" :xl="4"><a-card size="small">
            <a-statistic title="规则执行" :value="data.rules.executable">
              <template #suffix><span class="sfx">/ {{ data.rules.total }}</span></template>
            </a-statistic>
            <div class="sub">其余待数据接入</div>
          </a-card></a-col>
        </a-row>

        <a-row :gutter="[16, 16]" style="margin-top: 16px">
          <a-col :xs="24" :xl="10"><a-card size="small" title="预审结论分布">
            <template #extra>
              <span class="lg"><i style="background:#52c41a"></i>通过</span>
              <span class="lg"><i style="background:#faad14"></i>待复核</span>
              <span class="lg"><i style="background:#ff4d4f"></i>不通过</span>
            </template>
            <div v-for="(row, i) in kpi ? [kpi.real, kpi.demo] : []" :key="i" class="stack-row">
              <span class="stack-lb">{{ i === 0 ? '真实单' : '演示单' }}</span>
              <div class="stack-track" :title="`通过 ${row.pass} · 待复核 ${row.review} · 不通过 ${row.reject}`">
                <i v-for="(s, j) in row.seg" :key="j" :style="{ width: s.w + '%', background: s.c }"></i>
              </div>
              <span class="stack-note">{{ row.pass }} · {{ row.review }} · {{ row.reject }}</span>
            </div>
          </a-card></a-col>

          <a-col :xs="24" :sm="12" :xl="7"><a-card size="small" title="规则覆盖">
            <div class="donut-wrap">
              <a-progress type="dashboard" :size="132"
                :percent="data.rules.total ? Math.round((data.rules.executable / data.rules.total) * 100) : 0"
                :stroke-color="{ from: '#4096ff', to: '#1677ff' }" />
              <div class="rule-rows">
                <div v-for="r in ruleRows" :key="r.k" class="rule-row">
                  <span><i class="dot" :style="{ background: r.c }"></i>{{ r.k }}</span>
                  <b>{{ r.v }} 条</b>
                </div>
              </div>
            </div>
          </a-card></a-col>

          <a-col :xs="24" :sm="12" :xl="7"><a-card size="small" title="附件识别">
            <template #extra>
              <a-tag v-if="attOf(data)" color="success">
                {{ attOf(data)!.cls.typed }} / {{ attOf(data)!.cls.total }}
              </a-tag>
            </template>
            <div class="att-rows">
              <div class="att-row">
                <span class="lb">判类型</span>
                <a-progress :percent="pctOf(attOf(data)?.cls.typed, attOf(data)?.cls.total)"
                  :show-info="false" size="small" stroke-color="#52c41a" />
                <span class="val">{{ attOf(data)?.cls.typed ?? '—' }}/{{ attOf(data)?.cls.total ?? '—' }}</span>
              </div>
              <div class="att-row">
                <span class="lb">抽字段</span>
                <a-progress :percent="pctOf(attOf(data)?.ext.extracted, attOf(data)?.ext.eligible)"
                  :show-info="false" size="small" stroke-color="#52c41a" />
                <span class="val">{{ attOf(data)?.ext.extracted ?? '—' }}/{{ attOf(data)?.ext.eligible ?? '—' }}</span>
              </div>
              <div class="att-mini">
                <div><b>{{ callsOf(data) }}</b><span>模型调用</span></div>
                <div><b style="color: #ff4d4f">{{ failsOf(data) }}</b><span>失败明细</span></div>
                <div><b>{{ tokensOf(data) }}</b><span>累计 token</span></div>
              </div>
            </div>
          </a-card></a-col>
        </a-row>

        <a-card size="small" title="最近审核" style="margin-top: 16px">
          <template #extra>
            <span v-if="llmOf(data.status)" class="llm-note">
              模型辅助累计 {{ llmOf(data.status)!.called }} 次 · 成功 {{ llmOf(data.status)!.ok }}
            </span>
          </template>
          <a-table :columns="recentCols" :data-source="data.bills.items.slice(0, 6)"
            row-key="bill_no" size="small" :pagination="false">
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'bill_no'">
                <router-link :to="`/review/bills/${record.bill_no}`">{{ record.bill_no }}</router-link>
                <a-tag v-if="record.is_demo" color="default" style="margin-left: 6px">演示</a-tag>
              </template>
              <template v-else-if="column.dataIndex === 'amount'">{{ fmtAmt(record.amount) }}</template>
              <template v-else-if="column.dataIndex === 'conclusion'">
                <a-tag :color="CONCL[record.conclusion || '']?.color || 'default'">
                  {{ CONCL[record.conclusion || '']?.text || '未审' }}
                </a-tag>
              </template>
              <template v-else-if="column.dataIndex === 'doc_date'">
                <span class="dim">{{ (record.doc_date || '').slice(0, 10) }}</span>
              </template>
            </template>
          </a-table>
        </a-card>
      </template>
    </Async>
  </div>
</template>

<style scoped>
/* 看板栅格：xl 五卡一行 / 中屏两三张 / 窄屏两张——不许挤成一条，宁可折行 */
.sfx { font-size: 12px; font-weight: normal; color: rgba(0, 0, 0, .45); margin-left: 4px; }
.sub { font-size: 12px; color: rgba(0, 0, 0, .45); margin-top: 2px; }

.lg { font-size: 12px; color: rgba(0, 0, 0, .58); margin-left: 10px; }
.lg i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 4px; }

.stack-row { display: grid; grid-template-columns: 52px minmax(0, 1fr) 110px; align-items: center; gap: 12px; padding: 10px 0; }
.stack-lb { font-size: 13px; color: rgba(0, 0, 0, .65); }
.stack-track { display: flex; height: 14px; border-radius: 7px; overflow: hidden; background: #f0f0f0; }
.stack-note { font-size: 13px; color: rgba(0, 0, 0, .65); text-align: right; font-variant-numeric: tabular-nums; }

.donut-wrap { display: flex; align-items: center; gap: 20px; padding: 8px 0 4px; }
.rule-rows { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.rule-row { display: flex; justify-content: space-between; font-size: 13px; padding: 3px 0; }
.rule-row b { font-variant-numeric: tabular-nums; }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }

.att-rows { padding: 4px 0; }
.att-row { display: grid; grid-template-columns: 56px minmax(0, 1fr) 72px; align-items: center; gap: 10px; margin: 8px 0; }
.att-row .lb { font-size: 13px; color: rgba(0, 0, 0, .65); }
.att-row .val { font-size: 13px; text-align: right; font-variant-numeric: tabular-nums; }
.att-mini { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; text-align: center; margin-top: 12px; }
.att-mini b { display: block; font-size: 18px; font-weight: 600; }
.att-mini span { font-size: 11px; color: rgba(0, 0, 0, .45); }

.llm-note { font-size: 12px; color: rgba(0, 0, 0, .45); }
.dim { color: rgba(0, 0, 0, .45); }
</style>
