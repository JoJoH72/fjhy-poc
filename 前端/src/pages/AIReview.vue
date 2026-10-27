<script setup lang="ts">
/**
 * AI 智能审核（2026-10-27 AntD 换装版）：运行面板 + 结果明细。
 *
 * 保住的四个真实行为（原版踩坑换来的，不许丢）：
 * 1. 跑的过程中显示已等秒数 + 「转入后台」——同步接口可能挂很久，
 *    三个字不动和卡死长得一样。取消只停等待，服务端那一条会跑完落库。
 * 2. KPI 一律取自 /status（库里现存结论），不取组件内存——
 *    否则刷新/切页后 KPI 消失，看的人以为结果丢了。
 * 3. 比率分母是「已审」，口径后端算；算不出显示「—」不显示 0%。
 * 4. 时效卡标注「不含模型」——模型一介入耗时翻百倍，不标像变慢了。
 *
 * 「自动预审开启」状态说明（demo 契约）：后端感知环节对新单自动派审
 * （心跳/触发器），本页按钮是「立即重跑一轮」——两者并存，页面如实标注。
 */
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'

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

const CONCL: Record<string, { text: string; color: string }> = {
  pass: { text: '通过', color: 'success' },
  review: { text: '待复核', color: 'warning' },
  reject: { text: '不通过', color: 'error' },
}

const RUN_STEPS = ['单据快照与规则判定', '结论落库', '模型补判（待人工项）', '汇总刷新']

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
      lastProgress.value = '已转入后台执行，完成后可见。补判可续跑，再点一次接着往下跑。'
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

const fmtAmt = (v: number | null) =>
  v != null ? v.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '—'

const cols = [
  { title: '单据编号', dataIndex: 'bill_no', width: 220, ellipsis: true },
  { title: '摘要', dataIndex: 'summary' },
  { title: '金额（元）', dataIndex: 'amount', width: 130, align: 'right' as const },
  { title: '结论', dataIndex: 'conclusion', width: 96, align: 'center' as const },
  { title: '执行', dataIndex: 'rule_executed', width: 76, align: 'center' as const },
  { title: '命中 / 待人工', key: 'hits', width: 120, align: 'center' as const },
  { title: '操作', key: 'op', width: 90, align: 'center' as const },
]

onUnmounted(stopTick)
onMounted(loadStatus)
</script>

<template>
  <div class="aireview">
    <!-- 运行面板：空闲=汇总+按钮；运行中=阶段进度 -->
    <a-card size="small">
      <div class="run-head">
        <a-spin v-if="busy" size="small" style="margin-right: 8px" />
        <span class="rt">
          {{ busy ? `正在审核 · ${elapsed}s` : status?.reviewed ? `已审 ${status.reviewed} / ${status.total} 张` : '尚未执行审核' }}
        </span>
        <a-tooltip title="新单自动进入审核；本按钮为立即重跑一轮">
          <a-tag color="success" style="margin-left: 8px; cursor: help">自动预审开启</a-tag>
        </a-tooltip>
        <div class="grow"></div>
        <a-button v-if="!busy" type="primary" @click="runReview">
          {{ remaining ? `接着跑（还剩 ${remaining} 条）` : '开始智能审核' }}
        </a-button>
        <a-button v-else @click="cancelRun">转入后台</a-button>
      </div>

      <!-- 运行中：四相进度（真实语义映射：单据快照→规则判定→结论落库→模型补判→汇总刷新） -->
      <template v-if="busy">
        <a-steps :current="2" size="small" style="margin-top: 16px"
          :items="RUN_STEPS.map(t => ({ title: t }))" />
        <div class="dim" style="margin-top: 12px">
          已等 <b>{{ hhmmss(elapsed) }}</b> · 本轮服务端继续执行并落库
        </div>
      </template>

      <!-- 空闲：KPI 五卡（全部取自 /status 库存结论） -->
      <template v-else>
        <a-row :gutter="[16, 16]" style="margin-top: 4px">
          <a-col :xs="12" :md="8" :xl="5"><a-card size="small" class="kpi">
            <a-statistic title="不通过" :value="status?.reject ?? 0"
              :value-style="{ color: '#ff4d4f' }" />
            <div class="sub">占比 {{ rate(status?.rates?.reject_rate) }}</div>
          </a-card></a-col>
          <a-col :xs="12" :md="8" :xl="5"><a-card size="small" class="kpi">
            <a-statistic title="待复核" :value="status?.review ?? 0"
              :value-style="{ color: '#faad14' }" />
            <div class="sub">占比 {{ rate(status?.rates?.review_rate) }}</div>
          </a-card></a-col>
          <a-col :xs="12" :md="8" :xl="5"><a-card size="small" class="kpi">
            <a-statistic title="通过" :value="status?.pass ?? 0"
              :value-style="{ color: '#52c41a' }" />
            <div class="sub">占比 {{ rate(status?.rates?.pass_rate) }}</div>
          </a-card></a-col>
          <a-col :xs="12" :md="8" :xl="5"><a-card size="small" class="kpi">
            <a-statistic title="模型辅助" :value="status?.llm_assisted ?? 0" />
            <div class="sub">待人工项补判</div>
          </a-card></a-col>
          <a-col :xs="12" :md="8" :xl="4"><a-card size="small" class="kpi">
            <a-statistic title="单均耗时" :value="ms(status?.timing?.p50_ms)" />
            <div class="sub">{{ status?.timing && !status.timing.includes_llm ? '不含模型' : '含模型' }}</div>
          </a-card></a-col>
        </a-row>
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-top: 12px" />
        <div v-if="lastProgress" class="prog-note">
          <MarkText :text="lastProgress" />
        </div>
      </template>
    </a-card>

    <!-- 结果明细 -->
    <a-card size="small" style="margin-top: 16px">
      <template #title>审核结果明细</template>
      <template #extra>
        <span class="dim">比率分母为已审 {{ status?.rates?.denominator ?? '—' }} 张</span>
        <router-link to="/review/bills" style="margin-left: 16px">去工作台复核</router-link>
      </template>
      <a-table :columns="cols" :data-source="status?.bills || []" row-key="bill_no"
        size="small" :pagination="{ pageSize: 10, showTotal: (t: number) => `共 ${t} 条` }">
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'bill_no'">
            <!-- 标注可下钻（P0-2）：单号直达报告页（有 run_id 时）——从结果明细
                 想看「为什么命中」是主路径，去单据详情是次路径（报告页里有链接）。 -->
            <router-link v-if="record.run_id"
              :to="`/review/report/${encodeURIComponent(record.run_id)}`">{{ record.bill_no }}</router-link>
            <router-link v-else
              :to="`/review/bills/${encodeURIComponent(record.bill_no)}`">{{ record.bill_no }}</router-link>
          </template>
          <!-- 摘要允许换行：单行截断会把「含住宿」这类关键信息吃掉。上限两行。 -->
          <template v-else-if="column.dataIndex === 'summary'">
            <div class="sum" :title="record.summary || ''">{{ record.summary || '—' }}</div>
          </template>
          <template v-else-if="column.dataIndex === 'amount'">{{ fmtAmt(record.amount) }}</template>
          <template v-else-if="column.dataIndex === 'conclusion'">
            <a-tag v-if="record.conclusion && CONCL[record.conclusion]"
              :color="CONCL[record.conclusion].color">{{ CONCL[record.conclusion].text }}</a-tag>
            <a-tag v-else color="default">未审核</a-tag>
          </template>
          <template v-else-if="column.dataIndex === 'rule_executed'">{{ record.rule_executed ?? '—' }}</template>
          <template v-else-if="column.key === 'hits'">
            <!-- 标注可下钻（P0-2）：命中/待人工红字可点直达报告页 -->
            <template v-if="record.run_id">
              <router-link :to="`/review/report/${encodeURIComponent(record.run_id)}`"
                v-if="(record.hit_count ?? 0) > 0" style="color: #ff4d4f; font-weight: 600">{{ record.hit_count }}</router-link>
              <template v-else>0</template>
              <span style="color: rgba(0,0,0,.35)"> / </span>
              <router-link :to="`/review/report/${encodeURIComponent(record.run_id)}`"
                :style="(record.pending_count ?? 0) > 0 ? 'color: #d46b08' : ''">{{ record.pending_count ?? 0 }}</router-link>
            </template>
            <template v-else>0 / {{ record.pending_count ?? 0 }}</template>
          </template>
          <template v-else-if="column.key === 'op'">
            <router-link v-if="record.run_id"
              :to="`/review/report/${encodeURIComponent(record.run_id)}`">复核</router-link>
            <span v-else class="dim">—</span>
          </template>
        </template>
        <template #emptyText>
          <a-empty :image-style="{ height: '40px' }" description="暂无审核结果" />
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<style scoped>
.run-head { display: flex; align-items: center; }
.run-head .rt { font-size: 15px; font-weight: 600; }
.grow { flex: 1; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.sub { font-size: 12px; color: rgba(0, 0, 0, .45); margin-top: 2px; }
.kpi :deep(.ant-statistic-title) { font-size: 13px; }
.kpi :deep(.ant-statistic-content) { font-size: 22px; }
/* 摘要两行封顶（与 AntD ellipsis rows=2 同款做法）：不设上限一条长摘要
   能把行高顶到五行，锁死一行又会吃掉「含住宿」这类关键信息。 */
.sum {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; white-space: normal; word-break: break-word; line-height: 1.55;
}
.prog-note { margin-top: 10px; font-size: 12.5px; color: rgba(0, 0, 0, .65); }
</style>
