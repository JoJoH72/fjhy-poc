<script setup lang="ts">
/**
 * 审核工作台（2026-10-26 AntD 换装版）：单据队列 + 筛选 + 批量操作。
 * KPI 行保留审核岗待办口径；表格换 a-table（列配置、分页、行选择）。
 * 批量通过仍拦截 AI 不通过单（逐张复核是闸门，不许批量放行）。
 */
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
import Async from '../components/Async.vue'
import { api, type Bill, type Page } from '../api'
import * as session from '../data/session'

/** 复核操作只属于审核岗；领导对本页只读（隐藏批量条与勾选） */
const canReview = computed(() => session.current()?.role === 'auditor')

const concl = ref<string | undefined>(undefined)
const demo = ref<string | undefined>(undefined)
const claimF = ref<string | undefined>(undefined)
const q = ref('')
const sort = ref<'risk' | 'amt' | 'time'>('risk')
const sel = ref<string[]>([])
const loading = ref(false)
const items = ref<Bill[]>([])
const total = ref(0)
const page = ref(1)
const size = 10   // 一页一屏内：列表页不靠拉长页面看数据，翻页解决（满高标准）

const CONCL: Record<string, { text: string; color: string }> = {
  pass: { text: '通过', color: 'success' },
  review: { text: '待复核', color: 'warning' },
  reject: { text: '不通过', color: 'error' },
}
const RANK: Record<string, number> = { reject: 0, review: 1, pass: 2 }

/* 误报/漏报取后端偏差口径，取不到显示 —，不装 0 */
const deviation = ref<{ fp: number | null; fn: number | null }>({ fp: null, fn: null })
api.deviation().then((d: any) => {
  const by = (d.by_kind || []) as { deviation_kind: string | null; n: number }[]
  const n = (k: string) => by.find((x) => (x.deviation_kind ?? '') === k)?.n ?? null
  deviation.value = { fp: n('false_positive'), fn: n('false_negative') }
}).catch(() => { })

async function load() {
  loading.value = true
  sel.value = []
  try {
    const p = await api.bills({
      conclusion: concl.value,
      is_demo: demo.value === undefined ? undefined : demo.value === 'true',
      q: q.value || undefined,
      claim: claimF.value,
      page: page.value, size,
    })
    items.value = p.items
    total.value = p.total
  } finally { loading.value = false }
}
load()

const sorted = computed(() => {
  const arr = [...items.value]
  if (sort.value === 'risk') arr.sort((a, b) => (RANK[a.conclusion || ''] ?? 3) - (RANK[b.conclusion || ''] ?? 3) || (b.amount ?? 0) - (a.amount ?? 0))
  else if (sort.value === 'amt') arr.sort((a, b) => (b.amount ?? 0) - (a.amount ?? 0))
  else arr.sort((a, b) => String(b.finished_at || '').localeCompare(String(a.finished_at || '')))
  return arr
})

const kpi = computed(() => {
  const it = items.value
  const todo = it.filter((b) => b.conclusion && b.conclusion !== 'pass')
  const realTodo = todo.filter((b) => !b.is_demo).length
  const big = todo.filter((b) => (b.amount ?? 0) >= 10000).length
  const today = new Date().toISOString().slice(0, 10)
  const todayNew = it.filter((b) => (b.finished_at || '').startsWith(today)).length
  return { todo: todo.length, realTodo, big, todayNew, total: total.value }
})

async function batchPass() {
  const picked = items.value.filter((b) => sel.value.includes(b.bill_no))
  const bad = picked.filter((b) => b.conclusion === 'reject')
  if (bad.length) {
    message.warning(`选中含 AI 不通过的 ${bad.length} 张，需逐张复核`)
    return
  }
  const who = session.current()?.user || '未署名'
  let ok = 0
  for (const b of picked.filter((x) => x.run_id)) {
    try { await api.submitManual(b.run_id!, { decision: 'agree', reviewer: who }); ok++ } catch { }
  }
  message.success(`已复核通过 ${ok} 张`)
  await load()
}

const fmtAmt = (v: number | null) =>
  v != null ? v.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '—'
const dt = (s: string | null) => (s || '').slice(5, 16).replace('T', ' ')

const columns = [
  { title: '单据编号', dataIndex: 'bill_no', width: 230 },
  { title: '核算主体', dataIndex: 'org_name', width: 170, ellipsis: true },
  { title: '金额（元）', dataIndex: 'amount', width: 115, align: 'right' as const },
  { title: 'AI 结论', dataIndex: 'conclusion', width: 92 },
  { title: '命中/待人工', key: 'hits', width: 105, align: 'center' as const },
  { title: '认领人', dataIndex: 'claim_label', width: 90 },
  { title: '完成时间', dataIndex: 'finished_at', width: 130 },
  { title: '操作', key: 'op', width: 70 },
]
const rowSelection = computed(() => canReview.value ? {
  selectedRowKeys: sel.value,
  onChange: (keys: (string | number)[]) => { sel.value = keys.map(String) },
} : undefined)
</script>

<template>
  <div class="wb">
    <a-row :gutter="[16, 16]">
      <a-col :span="5"><a-card size="small"><a-statistic title="待复核" :value="kpi.todo"
        :suffix="`（真实 ${kpi.realTodo}）`" :value-style="{ color: kpi.todo ? '#cf1322' : undefined }" /></a-card></a-col>
      <a-col :span="5"><a-card size="small"><a-statistic title="今日新进" :value="kpi.todayNew" /></a-card></a-col>
      <a-col :span="5"><a-card size="small"><a-statistic title="大额待审（≥1万）" :value="kpi.big" /></a-card></a-col>
      <a-col :span="4"><a-card size="small"><a-statistic title="单据总量" :value="kpi.total" /></a-card></a-col>
      <a-col :span="5"><a-card size="small"><a-statistic title="误报/漏报"
        :value="`${deviation.fp ?? '—'} / ${deviation.fn ?? '—'}`" /></a-card></a-col>
    </a-row>

    <a-card size="small" style="margin-top: 16px">
      <div class="toolbar">
        <a-select v-model:value="concl" style="width: 120px" placeholder="全部结论"
          allow-clear :options="[
            { value: 'pass', label: '通过' },
            { value: 'review', label: '待复核' },
            { value: 'reject', label: '不通过' },
          ]" @change="load" />
        <a-select v-model:value="demo" style="width: 110px" placeholder="全部数据"
          allow-clear :options="[
            { value: 'false', label: '仅真实' },
            { value: 'true', label: '仅演示' },
          ]" @change="load" />
        <a-select v-model:value="claimF" style="width: 120px" placeholder="认领状态"
          allow-clear :options="[
            { value: 'none', label: '未认领' },
            { value: 'mine', label: '我认领的' },
          ]" @change="load" />
        <a-input-search v-model:value="q" style="width: 220px" placeholder="单据号 / 摘要"
          allow-clear @search="load" />
        <a-radio-group v-model:value="sort" button-style="solid" size="small">
          <a-radio-button value="risk">风险优先</a-radio-button>
          <a-radio-button value="amt">金额</a-radio-button>
          <a-radio-button value="time">时间</a-radio-button>
        </a-radio-group>
      </div>

      <a-table :columns="columns" :data-source="sorted" row-key="bill_no" size="small"
        :loading="loading" :row-selection="rowSelection"
        :pagination="{
          current: page, pageSize: size, total,
          showSizeChanger: false, showTotal: (t: number) => `共 ${t} 条`,
          onChange: (p: number) => { page = p; load() },
        }"
        :scroll="{ x: 1080 }">
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'bill_no'">
            <router-link :to="`/review/bills/${record.bill_no}`">{{ record.bill_no }}</router-link>
            <a-tag v-if="record.is_demo" style="margin-left: 6px">演示</a-tag>
          </template>
          <template v-else-if="column.dataIndex === 'amount'">{{ fmtAmt(record.amount) }}</template>
          <template v-else-if="column.dataIndex === 'conclusion'">
            <a-tag :color="CONCL[record.conclusion || '']?.color || 'default'">
              {{ CONCL[record.conclusion || '']?.text || '未审' }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'hits'">
            <span :style="record.hit_count ? 'color: #cf1322; font-weight: 600' : ''">{{ record.hit_count ?? 0 }}</span>
            / {{ record.pending_count ?? 0 }}
          </template>
          <template v-else-if="column.dataIndex === 'claim_label'">
            {{ record.claim_label || '—' }}
          </template>
          <template v-else-if="column.dataIndex === 'finished_at'">{{ dt(record.finished_at) || '—' }}</template>
          <template v-else-if="column.key === 'op'">
            <router-link v-if="record.run_id" :to="`/review/report/${record.run_id}`">复核</router-link>
          </template>
        </template>
      </a-table>

      <!-- 批量操作条：只对有复核权限的角色显示（领导只读视图不出现操作按钮，
           否则「看得见点不得」比没有更误导；服务端另有 403 闸兜底） -->
      <div v-if="canReview" style="margin-top: 12px">
        <a-space>
          <span class="dim">已选 {{ sel.length }} 项</span>
          <a-button type="primary" :disabled="!sel.length" @click="batchPass">批量通过</a-button>
          <a-tooltip title="退回需填写理由，请逐张在报告页办理">
            <a-button disabled>批量退回</a-button>
          </a-tooltip>
        </a-space>
      </div>
    </a-card>
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
.dim { color: rgba(0, 0, 0, 0.45); font-size: 13px; }
</style>
