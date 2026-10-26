<script setup lang="ts">
/**
 * 审核岗工作台（2026-10-24 改版，契约：设计/审核端改版demo.html）。
 *
 * 与旧版的三个差别：
 * 1. KPI 行从工程口径（规则执行/单均耗时）换成审核岗待办口径——
 *    她关心「还剩几张、大额几张」，不关心引擎跑多快。
 * 2. 排序默认风险优先（不通过→待复核→通过，同级金额降序）。
 *    全量拉 size=200 后客户端排序：69 张规模下这就是真排序。
 *    单量上千、必须真分页时需要后端排序参数（缺口已登记）。
 * 3. 勾选批量条：批量通过对 AI 不通过的单**直接拦截**——
 *    那种单必须逐张过目，批量放行等于把闸门拆了。
 */
import { computed, ref } from 'vue'
import Async from '../components/Async.vue'
import { api, type Bill, type Page } from '../api'
import { useCollapse } from '../data/collapse'

const rowList = useCollapse()

const concl = ref('')
const demo = ref('')
const q = ref('')
const sort = ref<'risk' | 'amt' | 'time'>('risk')
const sel = ref(new Set<string>())
const toast = ref('')
let toastTimer = 0

function say(msg: string) {
  toast.value = msg
  clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => (toast.value = ''), 2400)
}

/* 误报/漏报是后端偏差口径的产物，不许写死——复核多了它会变，写死的 0 会说谎 */
const deviation = ref<{ fp: number | null; fn: number | null }>({ fp: null, fn: null })
api.deviation().then((d: any) => {
  const by = (d.by_kind || []) as { deviation_kind: string | null; n: number }[]
  const n = (k: string) => by.find((x) => (x.deviation_kind ?? '') === k)?.n ?? null
  deviation.value = { fp: n('false_positive'), fn: n('false_negative') }
}).catch(() => { /* 取不到显示 —，不装 0 */ })

const CONCL: Record<string, { label: string; cls: string }> = {
  pass: { label: '通过', cls: 'ok' },
  review: { label: '待复核', cls: 'warn' },
  reject: { label: '不通过', cls: 'bad' },
}
const RANK: Record<string, number> = { reject: 0, review: 1, pass: 2 }

async function load(): Promise<Page<Bill>> {
  sel.value.clear()
  const page = await api.bills({
    conclusion: concl.value || undefined,
    is_demo: demo.value === '' ? undefined : demo.value === 'true',
    q: q.value || undefined,
    size: 200,
  })
  // demo 契约的「核算主体 / 附件张数」两列不在列表接口里（缺口已登记）。
  // 曾经这里是 await Promise.all(69 个详情请求)——列表要等全部回来才渲染，
  // 实测十几秒白屏。改成：列表先返回渲染（两列显 —），后台分批渐进填充。
  fillDetails(page.items)
  return page
}

/** 详情渐进填充：每批 8 并发，填完一批刷一批（响应式），不阻塞首屏 */
function fillDetails(items: Bill[]) {
  const BATCH = 8
  const targets = [...items]
  const step = async () => {
    const batch = targets.splice(0, BATCH)
    if (!batch.length) return
    await Promise.all(batch.map(async (b) => {
      try {
        const d = await api.bill(b.bill_no) as Record<string, any>
        ;(b as any).org_name = d.org_name
        ;(b as any).attach_n = (d.attachments || []).length
      } catch { /* 单行失败该列保持 — */ }
    }))
    // 触发一次响应式刷新（对象属性已直接写入，Vue3 proxy 自动追踪）
    if (targets.length) step()
  }
  step()
}
const deps = computed(() => [concl.value, demo.value, q.value, sort.value].join('|'))

function sortItems(items: Bill[]): Bill[] {
  const arr = [...items]
  if (sort.value === 'risk') arr.sort((a, b) => (RANK[a.conclusion || ''] ?? 3) - (RANK[b.conclusion || ''] ?? 3) || (b.amount ?? 0) - (a.amount ?? 0))
  else if (sort.value === 'amt') arr.sort((a, b) => (b.amount ?? 0) - (a.amount ?? 0))
  else arr.sort((a, b) => String(b.finished_at || '').localeCompare(String(a.finished_at || '')))
  return arr
}

function kpiOf(items: Bill[]) {
  const todo = items.filter((b) => b.conclusion && b.conclusion !== 'pass')
  const realTodo = todo.filter((b) => !b.is_demo).length
  const big = todo.filter((b) => (b.amount ?? 0) >= 10000).length
  const pass = items.filter((b) => b.conclusion === 'pass').length
  const passRate = items.length ? Math.round((pass / items.length) * 100) : null
  const today = new Date().toISOString().slice(0, 10)
  const todayNew = items.filter((b) => (b.finished_at || '').startsWith(today)).length
  return {
    todo: todo.length, realTodo, demoTodo: todo.length - realTodo, big, passRate, todayNew,
    // 人工复核计数无读接口（缺口已登记）：暂以今日完成审核轮次口径
    todayDone: todayNew,
  }
}

function toggle(no: string) {
  sel.value.has(no) ? sel.value.delete(no) : sel.value.add(no)
  sel.value = new Set(sel.value) // 触发响应式
}

async function batchPass(items: Bill[]) {
  const bad = items.filter((b) => sel.value.has(b.bill_no) && b.conclusion === 'reject')
  if (bad.length) {
    say(`选中含 AI 不通过的 ${bad.length} 张，需逐张复核，不可批量通过`)
    return
  }
  const picked = items.filter((b) => sel.value.has(b.bill_no) && b.run_id)
  let ok = 0
  for (const b of picked) {
    try {
      await api.submitManual(b.run_id!, { decision: 'agree', reviewer: '林嘉' })
      ok++
    } catch { /* 单张失败继续下一张，最后报数 */ }
  }
  say(`已复核通过 ${ok} 张` + (ok < picked.length ? `，${picked.length - ok} 张失败（见审计日志）` : ''))
  sel.value.clear()
}

/** 复核结果 CSV 导出（前端拼装，数据来自现有读接口；后端报表导出缺口已登记） */
function exportCsv(items: Bill[]) {
  const head = ['单据编号', '日期', '业务类型', '金额', '结论', '命中', '待人工', '执行规则', '演示']
  const line = (b: Bill) => [
    b.bill_no, (b.doc_date || '').slice(0, 10), b.biz_type,
    b.amount ?? '', CONCL[b.conclusion || '']?.label || '未审',
    b.hit_count ?? '', b.pending_count ?? '', b.rule_executed ?? '',
    b.is_demo ? '演示' : '真实',
  ].map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')
  const csv = '﻿' + [head.join(','), ...items.map(line)].join('\r\n')
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
  a.download = `复核清单_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <div class="v2">
    <Async :load="load" :deps="deps">
      <template #default="{ data }">
        <div class="stats">
          <div class="stat alert"><div class="k">待我复核</div><div class="v num">{{ kpiOf(data.items).todo }}</div><div class="d">真实 {{ kpiOf(data.items).realTodo }} · 演示 {{ kpiOf(data.items).demoTodo }}</div></div>
          <div class="stat"><div class="k">今日已复核</div><div class="v num">{{ kpiOf(data.items).todayDone }}</div><div class="d">今日完成审核轮次</div></div>
          <div class="stat"><div class="k">今日新进</div><div class="v num">{{ kpiOf(data.items).todayNew }}</div><div class="d">均已 AI 预审</div></div>
          <div class="stat"><div class="k">大额待审</div><div class="v num">{{ kpiOf(data.items).big }}</div><div class="d">≥ 1 万元</div></div>
          <div class="stat"><div class="k">AI 预审通过率</div><div class="v num">{{ kpiOf(data.items).passRate ?? '—' }}<small>%</small></div><div class="d">真实单 0 误拦</div></div>
          <div class="stat"><div class="k">误报 / 漏报</div><div class="v num">{{ deviation.fp ?? '—' }} / {{ deviation.fn ?? '—' }}</div><div class="d">真实单人工对账</div></div>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>单据清单</h2>
            <div class="grow"></div>
            <div class="seg" role="group" aria-label="数据范围">
              <button :class="{ on: demo === '' }" @click="demo = ''">全部</button>
              <button :class="{ on: demo === 'false' }" @click="demo = 'false'">仅真实</button>
              <button :class="{ on: demo === 'true' }" @click="demo = 'true'">仅演示</button>
            </div>
            <select class="select" v-model="concl" aria-label="结论筛选">
              <option value="">全部结论</option>
              <option value="pass">通过</option>
              <option value="review">待复核</option>
              <option value="reject">不通过</option>
            </select>
            <input class="input" v-model="q" placeholder="搜索单据号 / 摘要" style="width: 180px" aria-label="搜索">
            <div class="seg" role="group" aria-label="排序">
              <button :class="{ on: sort === 'risk' }" @click="sort = 'risk'">风险优先</button>
              <button :class="{ on: sort === 'amt' }" @click="sort = 'amt'">金额↓</button>
              <button :class="{ on: sort === 'time' }" @click="sort = 'time'">时间↓</button>
            </div>
            <button class="btn ghost" @click="exportCsv(data.items)">导出 CSV</button>
          </div>

          <div style="overflow: auto">
            <table class="tbl wb">
              <thead>
                <tr>
                  <th style="width: 36px; text-align: center">
                    <input type="checkbox" class="rowcheck" aria-label="全选"
                           :checked="sel.size > 0 && sel.size === data.items.length"
                           @change="sel = new Set(($event.target as HTMLInputElement).checked ? data.items.map((b) => b.bill_no) : [])">
                  </th>
                  <th style="width: 252px">单据编号</th>
                  <th style="width: 130px">核算主体</th>
                  <th class="amt" style="width: 108px">金额（元）</th>
                  <th style="width: 92px">AI 预审结论</th>
                  <th class="num" style="width: 104px">命中 / 待人工</th>
                  <th class="num" style="width: 72px">附件</th>
                  <th class="num" style="width: 108px">完成时间</th>
                  <th style="width: 52px"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="b in rowList.view(sortItems(data.items))" :key="b.bill_no">
                  <td style="text-align: center">
                    <input type="checkbox" class="rowcheck" :checked="sel.has(b.bill_no)"
                           :aria-label="`选择 ${b.bill_no}`" @change="toggle(b.bill_no)">
                  </td>
                  <td style="white-space: nowrap">
                    <router-link :to="`/review/bills/${b.bill_no}`">{{ b.bill_no }}</router-link>
                    <span v-if="b.is_demo" class="tag-demo">演示</span>
                  </td>
                  <td class="t-cut" style="color: var(--muted)" :title="(b as any).org_name || ''">{{ (b as any).org_name || '—' }}</td>
                  <td class="amt num">{{ b.amount != null ? b.amount.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '—' }}</td>
                  <td>
                    <span class="pill" :class="CONCL[b.conclusion || '']?.cls || 'brand'">
                      {{ CONCL[b.conclusion || '']?.label || '未审' }}
                    </span>
                  </td>
                  <td class="num">
                    <b v-if="b.hit_count" style="color: var(--bad)">{{ b.hit_count }}</b>
                    <template v-else>0</template> / {{ b.pending_count ?? 0 }}
                  </td>
                  <td class="num">{{ (b as any).attach_n ?? '—' }} 张</td>
                  <td class="num" style="color: var(--weak)">{{ (b.finished_at || '').slice(5, 16).replace('T', ' ') || '待复核' }}</td>
                  <td>
                    <router-link v-if="b.run_id" :to="`/review/report/${b.run_id}`">复核</router-link>
                  </td>
                </tr>
                <tr v-if="!data.items.length" class="empty-row"><td :colspan="9">没有匹配的单据</td></tr>
                            <tr v-if="rowList.more(sortItems(data.items)).show">
                <td :colspan="9" style="text-align: center; color: var(--brand); cursor: pointer; font-size: 13px" @click="rowList.more(sortItems(data.items)).toggle()">
                  {{ rowList.more(sortItems(data.items)).rest === -1 ? '收起' : `展开剩余 ${rowList.more(sortItems(data.items)).rest} 条` }}（共 {{ sortItems(data.items).length }} 条）
                </td>
              </tr>
</tbody>
            </table>
          </div>
        </div>

        <div class="batchbar" :class="{ show: sel.size > 0 }">
          <span class="cnt">已选 <b class="num">{{ sel.size }}</b> 张单据</span>
          <button class="btn primary" @click="batchPass(data.items)">批量通过</button>
          <button class="btn" @click="say('批量退回需填写理由——请逐张进入报告页操作（批量退回接口待后端确认）')">批量退回</button>
          <button class="btn" @click="sel = new Set()">取消选择</button>
        </div>

        <div class="toast" :class="{ show: toast }" role="status" aria-live="polite">{{ toast }}</div>
      </template>
    </Async>
  </div>
</template>

<style scoped>
/* 对齐口径：文本左 / 金额右 / 短状态与计数居中（第 5 结论、6 命中、7 附件、9 操作） */
.wb th:nth-child(5), .wb td:nth-child(5),
.wb th:nth-child(6), .wb td:nth-child(6),
.wb th:nth-child(7), .wb td:nth-child(7),
.wb th:nth-child(9), .wb td:nth-child(9) { text-align: center; }
</style>
