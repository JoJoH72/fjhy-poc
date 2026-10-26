<script setup lang="ts">
/**
 * AI 智能审核（2026-10-24 改版）：运行面板 + 结果明细。
 *
 * 改版保住的四个真实行为（原版踩坑换来的，不许丢）：
 * 1. 跑的过程中显示已等秒数 + 「不等了」——同步接口可能挂很久，
 *    三个字不动和卡死长得一样。取消只停等待，服务端那一条会跑完落库。
 * 2. KPI 一律取自 /status（库里现存结论），不取组件内存——
 *    否则刷新/切页后 KPI 消失，看的人以为结果丢了。
 * 3. 比率分母是「已审」，口径后端算；算不出显示「—」不显示 0%。
 * 4. 时效卡标注「不含模型」——模型一介入耗时翻百倍，不标像变慢了。
 *
 * 「自动预审开启」状态说明（demo 契约）：后端感知智能体对新单自动派审
 * （心跳/触发器），本页按钮是「立即重跑一轮」——两者并存，页面如实标注。
 */
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import { useCollapse } from '../data/collapse'

const billList = useCollapse()

type HitRow = {
  run_id: string
  rule_id: string
  rule_name: string | null
  control_type: string
  message: string
  needs_human: boolean
}

type BillRow = {
  bill_no: string
  amount: number | null
  summary: string | null
  run_id: string | null
  conclusion: 'pass' | 'review' | 'reject' | null
  hit_count: number | null
  pending_count: number | null
  rule_executed: number | null
  finished_at: string | null
  hits: HitRow[]
}

type StatusData = {
  total: number
  reviewed: number
  reject: number
  review: number
  pass: number
  llm_assisted: number
  llm_summary?: Record<string, unknown>
  rates?: ReviewRates
  timing?: ReviewTiming
  bills: BillRow[]
}

type ReviewRates = {
  denominator: number
  pass_rate: number | null
  review_rate: number | null
  reject_rate: number | null
  note: string
}

type ReviewTiming = {
  bills: number
  excluded_nonpositive: number
  p50_ms: number | null
  p90_ms: number | null
  min_ms: number | null
  max_ms: number | null
  llm_involved_findings: number
  includes_llm: boolean
  scope: string
}

const status = ref<StatusData | null>(null)
const busy = ref(false)
const err = ref('')
const elapsed = ref(0)
let tick: ReturnType<typeof setInterval> | null = null
let aborter: AbortController | null = null
const lastProgress = ref('')
const remaining = ref(0)
const canceled = ref(false)

function stopTick() {
  if (tick) { clearInterval(tick); tick = null }
}

function cancelRun() {
  // 只中断等待，不中断服务端——说成「已停止审核」就是假话
  canceled.value = true
  aborter?.abort()
}

function hhmmss(s: number) {
  const m = Math.floor(s / 60)
  return m ? `${m} 分 ${s % 60} 秒` : `${s} 秒`
}

function rate(v: number | null | undefined) {
  return v == null ? '—' : (v * 100).toFixed(1) + '%'
}

function ms(v: number | null | undefined) {
  if (v == null) return '—'
  return v >= 1000 ? (v / 1000).toFixed(1) + 's' : Math.round(v) + 'ms'
}

const CONCL: Record<string, { label: string; cls: string }> = {
  pass: { label: '通过', cls: 'ok' },
  review: { label: '待复核', cls: 'warn' },
  reject: { label: '不通过', cls: 'bad' },
}

async function loadStatus() {
  try {
    status.value = await api.aiReviewStatus() as StatusData
  } catch {
    // 初始加载失败不弹错——可能是还没生成过演示数据
  }
}

async function runReview() {
  if (busy.value) return
  busy.value = true
  err.value = ''
  canceled.value = false
  lastProgress.value = ''
  elapsed.value = 0
  stopTick()
  tick = setInterval(() => { elapsed.value += 1 }, 1000)
  aborter = new AbortController()
  try {
    const res = await api.aiReviewBatch(aborter.signal)
    if (!res.ok) {
      err.value = res.error || '审核失败'
      return
    }
    remaining.value = res.llm?.remaining ?? 0
    lastProgress.value = res.llm?.progress_note || ''
    await loadStatus()
  } catch (e) {
    if (canceled.value) {
      lastProgress.value = '已转入后台执行，完成后可见；期间可浏览其他页面。'
        + '**本轮服务端会跑完并落库**；补判可续跑，再点一次接着往下跑。'
      await loadStatus()
    } else {
      err.value = e instanceof Error ? e.message : String(e)
    }
  } finally {
    stopTick()
    aborter = null
    busy.value = false
  }
}

onUnmounted(stopTick)
onMounted(loadStatus)
</script>

<template>
  <div class="v2">
    <!-- 运行面板：空闲=汇总+按钮；运行中=进度四相 -->
    <div class="runpanel" :class="{ done: !busy && status?.reviewed }">
      <div class="run-head">
        <span class="rt">
          <span v-if="busy" class="spin" aria-hidden="true"></span>
          {{ busy ? `正在审核 · ${elapsed}s` : status?.reviewed ? `已审 ${status.reviewed} / ${status.total} 张` : '尚未执行审核' }}
        </span>
        <span class="pill ok" style="margin-left: 4px">自动预审开启</span>
        <span style="font-size: 12px; color: var(--weak)">新单自动进入审核，无需手动触发</span>
        <div class="grow"></div>
        <button v-if="!busy" class="btn primary lg" @click="runReview">
          {{ remaining ? `接着跑（还剩 ${remaining} 条）` : '开始智能审核' }}
        </button>
        <button v-else class="btn ghost" @click="cancelRun">转入后台</button>
      </div>

      <!-- 运行中：四相进度（真实语义映射：单据快照→规则判定→结论落库→模型补判） -->
      <template v-if="busy">
        <div class="runbar"><i style="width: 40%"></i></div>
        <ul class="runsteps">
          <li class="rs done"><span class="ic">✓</span>单据快照与规则判定（毫秒级）<span class="t num">已过 {{ hhmmss(Math.max(elapsed - 2, 0)) }}</span></li>
          <li class="rs done"><span class="ic">✓</span>结论落库<span class="t">完成</span></li>
          <li class="rs run"><span class="ic"></span>模型补判「待人工」项（单轮有时间上限）<span class="t">进行中…</span></li>
          <li class="rs"><span class="ic"></span>汇总刷新<span class="t"></span></li>
        </ul>
        <div style="font-size: 12px; color: var(--weak)">
          已等 <b>{{ hhmmss(elapsed) }}</b> · 转入后台后本轮继续执行并落库
        </div>
      </template>

      <!-- 空闲：KPI 六卡（全部取自 /status 库存结论） -->
      <template v-else>
        <div class="res-row">
          <div class="res bad"><b>{{ status?.reject ?? 0 }}</b>不通过</div>
          <div class="res warn"><b>{{ status?.review ?? 0 }}</b>待复核</div>
          <div class="res ok"><b>{{ status?.pass ?? 0 }}</b>通过</div>
          <div class="res"><b>{{ status?.llm_assisted ?? 0 }}</b>模型辅助</div>
          <div class="res"><b>{{ ms(status?.timing?.p50_ms) }}</b>单均耗时<span style="font-size: 11px; color: var(--weak)">{{ status?.timing && !status.timing.includes_llm ? ' · 不含模型' : '' }}</span></div>
        </div>
        <div v-if="err" style="margin-top: 10px; color: var(--bad); font-size: 13px">{{ err }}</div>
        <div v-if="lastProgress" style="margin-top: 10px; font-size: 12.5px; color: var(--muted)">
          <MarkText :text="lastProgress" />
        </div>
      </template>
    </div>

    <!-- 结果明细 -->
    <div class="card">
      <div class="card-head">
        <h2>审核结果明细</h2>
        <div class="grow"></div>
        <span style="font-size: 12px; color: var(--weak)">
          比率分母为已审：通过 {{ rate(status?.rates?.pass_rate) }} ·
          待人工 {{ rate(status?.rates?.review_rate) }} ·
          不通过 {{ rate(status?.rates?.reject_rate) }}
        </span>
        <router-link to="/review/bills" style="font-size: 13px">去工作台复核</router-link>
      </div>
      <div style="overflow: auto">
        <table class="tbl">
          <thead>
            <tr>
              <!-- 用百分比而不是像素：像素总和一旦大于容器（1080 宽时容器只有 1046），
                   表格不能比列宽之和更窄，就会顶出一条横向滚动条。
                   百分比永远等于容器宽度，比例在 1080 / 1440 下都成立。 -->
              <th style="width: 23%">单据编号</th><th style="width: 29%">摘要</th>
              <th class="amt" style="width: 9%">金额（元）</th><th class="c" style="width: 9%">结论</th>
              <th class="c" style="width: 8%">执行</th><th class="c" style="width: 12%">命中 / 待人工</th>
              <th class="c" style="width: 10%"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in billList.view(status?.bills || [])" :key="b.bill_no">
              <td>
                <router-link :to="`/review/bills/${encodeURIComponent(b.bill_no)}`">{{ b.bill_no }}</router-link>
              </td>
              <!-- 摘要允许换行：单行截断会把「含住宿」这类关键信息吃掉。
                   上限两行——不设上限时一条长摘要能把行高顶到五行，整张表就没法扫了。 -->
              <td><div class="sum" :title="b.summary || ''">{{ b.summary || '—' }}</div></td>
              <td class="amt num">{{ b.amount != null ? b.amount.toLocaleString() : '—' }}</td>
              <td class="c">
                <span v-if="b.conclusion && CONCL[b.conclusion]" class="pill" :class="CONCL[b.conclusion].cls">{{ CONCL[b.conclusion].label }}</span>
                <span v-else class="pill brand">未审核</span>
              </td>
              <td class="c">{{ b.rule_executed ?? '—' }}</td>
              <td class="c">
                <b v-if="(b.hit_count ?? 0) > 0" style="color: var(--bad)">{{ b.hit_count }}</b>
                <template v-else>0</template> / {{ b.pending_count ?? 0 }}
              </td>
              <td class="c">
                <router-link v-if="b.run_id" :to="`/review/report/${encodeURIComponent(b.run_id)}`">复核</router-link>
              </td>
            </tr>
            <tr v-if="!status || !status.bills.length" class="empty-row">
              <td :colspan="7">点击「开始智能审核」，系统将生成演示数据并执行完整三层审核</td>
            </tr>
                        <tr v-if="billList.more(status?.bills || []).show">
                <td :colspan="7" style="text-align: center; color: var(--brand); cursor: pointer; font-size: 13px"
                    @click="billList.more(status?.bills || []).toggle()">
                  {{ billList.more(status?.bills || []).rest === -1 ? '收起' : `展开剩余 ${billList.more(status?.bills || []).rest} 条` }}（共 {{ (status?.bills || []).length }} 条）
                </td>
              </tr>
</tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 文本列：最多两行，超出省略号，悬停看全文（与 Ant Design ellipsis rows=2 同款做法）。
   不设上限时一条长摘要能把行高顶到五行，整张表就没法扫；锁死一行又会把
   「含住宿」「补助非整数倍」这类关键信息吃掉。两行是折中。 */
.sum {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; white-space: normal; word-break: break-word; line-height: 1.55;
}
</style>
