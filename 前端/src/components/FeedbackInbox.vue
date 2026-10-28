<script setup lang="ts">
/**
 * 异议收件箱（B-P0-1，2026-10-27，规则库第四页签「异议」）。
 *
 * 客户排期表 #2 后半句「意见回流至规则库侧汇总处理」的消费侧：审核岗在
 * 复核报告勾「不认可这条命中」（可附逐条文字意见，A-P0-1）→ 逐条回流
 * feedback_log → 本页按规则聚合呈现 → 规则管理员处置（维持 / 转修订）。
 * 回流链此前断在中间：审核岗提了异议，规则管理员看不到汇总。
 *
 * 结构（列表页 + 详情抽屉，业内定式）：
 *   聚合表（规则 / 异议次数 / 待处置 / 带意见 / 最近时间 / 状态）
 *   → 点行开抽屉（width 860）：
 *      上段 = 异议明细列表（单据链接直达报告页 / 审核员 / 当时判定原文 /
 *              意见原话——note 有则显示无则不渲染，历史行天然兼容）
 *      中段 = 该规则八要素摘要
 *      下段 = 逐条处置动作条（维持规则·说明必填 / 确认为规则问题·返回修订预填）
 *
 * 数据源全部取库：计数 / 状态 / 明细零写死；处置动作写 feedback_log 处置三列
 * （迁移 0048，只加不改）+ sys_op_log（category=rule）——留痕三问可答。
 */
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { api } from '../api'
import type { FeedbackAgg, FeedbackDetail } from '../api'
import Async from './Async.vue'

const props = defineProps<{ open: boolean }>()

const FB_STATE: Record<string, { text: string; color: string }> = {
  pending: { text: '待处置', color: 'orange' },
  keep: { text: '已维持', color: 'default' },
  revise: { text: '已修订', color: 'green' },
}

/* ── 聚合列表 ── */
const fbState = ref<'all' | 'pending' | 'keep' | 'revise'>('all')
const inbox = ref<{ items: FeedbackAgg[]; total: number; pending: number } | null>(null)
/** reload 序号：处置完一条后强制重取（Async deps 触发） */
const reload = ref(0)

const aggFiltered = computed(() => {
  const items = inbox.value?.items || []
  // 后端已按 state 过滤过；这里兜底（直接切 state 时 Async 不重发也没关系）
  return fbState.value === 'all' ? items : items.filter((x) => x.state === fbState.value)
})

const aggCols = [
  { title: '规则', key: 'rule', width: 250 },
  { title: '异议次数', dataIndex: 'n', width: 90, align: 'center' as const },
  { title: '待处置', dataIndex: 'pending_n', width: 80, align: 'center' as const },
  { title: '带意见', dataIndex: 'note_n', width: 80, align: 'center' as const },
  { title: '最近提出', dataIndex: 'last_at', width: 130 },
  { title: '处置状态', key: 'state', width: 100, align: 'center' as const },
  { title: '', key: 'act', width: 70, align: 'center' as const },
]

/* ── 明细抽屉 ── */
const drawerRule = ref<string | null>(null)
const detail = ref<FeedbackDetail | null>(null)
const detailErr = ref('')
const detailLoading = ref(false)

async function openRule(rid: string) {
  drawerRule.value = rid
  detailLoading.value = true
  detailErr.value = ''
  detail.value = null
  try {
    detail.value = await api.feedbackDetail(rid)
  } catch (e) {
    detailErr.value = e instanceof Error ? e.message : String(e)
  } finally {
    detailLoading.value = false
  }
}

const ruleBrief = computed(() => {
  const r = (detail.value?.rule || {}) as Record<string, any>
  return [
    { label: '规则名称', v: r.rule_name || '—' },
    { label: '审核维度', v: r.dimension || '—' },
    { label: '风险类别', v: r.risk_class || '—' },
    { label: '控制类型', v: r.control_type || '—' },
    { label: '判定逻辑', v: r.logic || '—' },
    { label: '适用范围', v: r.scope || '—' },
    { label: '例外', v: r.exception || '—' },
    { label: '可执行', v: r.compiled ? '是' : '否' },
  ]
})

/* ── 处置动作（逐条） ──
 * 处置粒度 = 单条异议（不是整个规则聚合行）——同一规则的多条异议可能
 * 一条维持一条转修订；后端按 feedback id 处置，聚合状态随之变。
 * 五环：点击前（未处置行才有按钮，已处置行展示结果）/ 点击中 loading /
 * 成功 toast+刷新+抽屉内状态即时变 / 失败人话 detail / 完成后引导
 * （revise 弹修订预填去向提示）。 */
const resolvingId = ref<number | null>(null)
const keepNote = ref<Record<number, string>>({})

async function resolve(item: { id: number; bill_no?: string | null }, decision: 'keep' | 'revise') {
  const note = (keepNote.value[item.id] || '').trim()
  if (decision === 'keep' && !note) {
    message.warning('维持规则须填写说明——提出异议的审核员看得到这段话')
    return
  }
  resolvingId.value = item.id
  try {
    const r = await api.feedbackResolve(item.id, { decision, note: note || undefined })
    message.success(decision === 'keep'
      ? '已维持规则，处置留痕见操作日志（规则类）'
      : `已确认为规则问题。修订理由预填：${r.prefill || '（无文字意见）'}`)
    // 明细就地更新 + 聚合重取（页签计数同步变）
    if (detail.value) {
      const it = detail.value.items.find((x) => x.id === item.id)
      if (it) {
        it.decision = decision
        it.resolved_note = note || it.resolved_note
        // resolved_by 显示会话账号——后端为准，这里先乐观置位
      }
      await openRuleRefresh()
    }
    reload.value++
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    resolvingId.value = null
    delete keepNote.value[item.id]
  }
}

/** 处置成功后重取明细（保持滚动位置不重建抽屉） */
async function openRuleRefresh() {
  if (!drawerRule.value) return
  try {
    detail.value = await api.feedbackDetail(drawerRule.value)
  } catch { /* 抽屉数据已有，静默失败不打扰——聚合重取会兜底 */ }
}

/* 页签未激活不出手（Tabs 非激活 pane 不渲染也不加载） */
watch(() => props.open, (v) => { if (v) reload.value++ }, { immediate: true })
</script>

<template>
  <Async :load="() => api.feedbackInbox()" :deps="reload"
    @loaded="(d) => (inbox = d)">
    <template #default="{ data }">
      <div class="fb-note dim">
        汇总审核岗复核时勾选「不认可这条命中」的回流记录，按规则聚合；
        点开规则行看每条异议的单据与意见原话，处置动作进操作日志。
      </div>
      <div class="toolbar">
        <a-radio-group v-model:value="fbState" size="small" button-style="solid">
          <a-radio-button value="all">全部 {{ data.total }}</a-radio-button>
          <a-radio-button value="pending">待处置 {{ data.pending }}</a-radio-button>
          <a-radio-button value="keep">已维持</a-radio-button>
          <a-radio-button value="revise">已修订</a-radio-button>
        </a-radio-group>
        <span class="dim">带文字意见的异议，点开可见原话</span>
      </div>

      <a-table v-if="aggFiltered.length" :data-source="aggFiltered" row-key="rule_id"
        size="small" :pagination="aggFiltered.length > 15 ? { pageSize: 15 } : false"
        :columns="aggCols">
        <template #bodyCell="{ column, record: r }">
          <template v-if="column.key === 'rule'">
            <router-link :to="`/asset/rules/${encodeURIComponent(r.rule_id)}`">{{ r.rule_id }}</router-link>
            <span v-if="r.rule_name" class="dim fb-rname">{{ r.rule_name }}</span>
          </template>
          <template v-else-if="column.dataIndex === 'pending_n'">
            <b v-if="r.pending_n > 0" class="fb-pend">{{ r.pending_n }}</b>
            <span v-else class="dim">0</span>
          </template>
          <template v-else-if="column.dataIndex === 'note_n'">
            <span v-if="r.note_n > 0">{{ r.note_n }}</span>
            <span v-else class="dim">0</span>
          </template>
          <template v-else-if="column.dataIndex === 'last_at'">
            <span class="dim">{{ r.last_at || '—' }}</span>
          </template>
          <template v-else-if="column.key === 'state'">
            <a-tag :color="FB_STATE[r.state]?.color || 'default'">
              {{ FB_STATE[r.state]?.text || r.state }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'act'">
            <a size="small" class="fb-open" @click.prevent="openRule(r.rule_id)">明细</a>
          </template>
        </template>
      </a-table>

      <!-- 空态：说清数据从哪来、去哪产生（四态纪律），不摆干巴巴的「暂无数据」 -->
      <div v-else class="fb-empty">
        <a-empty :image-style="{ height: '48px' }">
          <template #description>
            <div class="fb-empty-t">暂无异议记录</div>
            <div class="dim">审核岗在复核报告的证据展开区勾选「不认可此命中」并提交后，
              异议会回流到这里按规则汇总。</div>
          </template>
        </a-empty>
      </div>

      <!-- 聚合抽屉：上段明细 / 中段八要素摘要 / 下段处置动作 -->
      <a-drawer :open="!!drawerRule" width="860" placement="right"
        :body-style="{ padding: '0', display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
        @close="drawerRule = null">
        <template #title>
          <span>异议明细 · </span>
          <router-link v-if="drawerRule" :to="`/asset/rules/${encodeURIComponent(drawerRule)}`"
            @click="drawerRule = null">{{ drawerRule }}</router-link>
        </template>

        <div v-if="detailLoading" class="fb-loading">正在加载异议明细…</div>
        <div v-else-if="detailErr" style="padding: 20px">
          <a-alert type="error" show-icon message="取异议明细失败" :description="detailErr" />
        </div>
        <template v-else-if="detail">
          <!-- 上段：异议明细列表 -->
          <div class="fb-list">
            <div v-if="!detail.items.length" class="dim" style="padding: 12px 0">该规则没有回流异议</div>
            <div v-for="it in detail.items" :key="it.id" class="fb-item" :class="{ done: !!it.decision }">
              <div class="fb-item-head">
                <router-link v-if="it.run_id" class="fb-bill"
                  :to="`/review/report/${encodeURIComponent(it.run_id)}`"
                  :title="'直达当时那份审核报告'">{{ it.bill_no }}</router-link>
                <span v-else class="fb-bill">{{ it.bill_no || '—' }}</span>
                <span class="dim">{{ it.user_label || '—' }} · {{ it.at || '—' }}</span>
                <span class="grow"></span>
                <a-tag v-if="it.decision" :color="FB_STATE[it.decision]?.color || 'default'">
                  {{ FB_STATE[it.decision]?.text }}
                </a-tag>
                <a-tag v-else color="orange">待处置</a-tag>
              </div>
              <!-- 当时判定原文（标注可下钻：凭什么说命中） -->
              <div v-if="it.finding_message" class="fb-msg">
                <span class="dim">当时判定：</span><MarkText :text="it.finding_message" />
              </div>
              <!-- 逐条意见原话（A-P0-1）：有则显示，无则不渲染（历史兼容） -->
              <div v-if="it.note" class="fb-note-line">
                <a-tag color="blue" class="fb-note-tag">意见</a-tag>{{ it.note }}
              </div>
              <div v-else class="dim fb-no-note">未附文字意见</div>
              <!-- 处置区：未处置给动作；已处置给结果 -->
              <div v-if="!it.decision" class="fb-resolve">
                <a-input v-model:value="keepNote[it.id]" size="small" :maxlength="200"
                  class="fb-keep-input" aria-label="处置说明"
                  placeholder="处置说明（维持必填）：为什么维持——提出异议的审核员看得到" />
                <a-button size="small" :loading="resolvingId === it.id"
                  @click="resolve(it, 'keep')">维持规则</a-button>
                <a-button size="small" type="primary" ghost :loading="resolvingId === it.id"
                  title="记处置意图并生成修订理由预填；改规则口径走规则修订流程"
                  @click="resolve(it, 'revise')">确认为规则问题</a-button>
              </div>
              <div v-else class="fb-resolved">
                <span class="dim">{{ it.resolved_at || '' }} · {{ it.resolved_by || '' }}：
                  {{ it.resolved_note || (it.decision === 'revise' ? '确认为规则问题，转规则修订' : '维持规则') }}
                </span>
              </div>
            </div>
          </div>

          <!-- 中段：八要素摘要 -->
          <div class="fb-rule">
            <div class="fb-rule-t">规则摘要</div>
            <div class="fb-kv">
              <template v-for="f in ruleBrief" :key="f.label">
                <span class="k">{{ f.label }}</span><span class="v">{{ f.v }}</span>
              </template>
            </div>
          </div>
        </template>
      </a-drawer>
    </template>
  </Async>
</template>

<style scoped>
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.grow { flex: 1; }
.fb-note { margin-bottom: 10px; line-height: 1.6; }
.fb-rname { margin-left: 8px; }
.fb-pend { color: #d46b08; }
.fb-open { font-size: 12px; cursor: pointer; }
.fb-empty { padding: 32px 0 16px; }
.fb-empty-t { font-size: 13px; color: rgba(0, 0, 0, .65); margin-bottom: 4px; }
.fb-empty .dim { line-height: 1.7; max-width: 460px; margin: 0 auto; }
.fb-loading { padding: 40px; text-align: center; color: rgba(0, 0, 0, .45); }

/* 抽屉：明细列表区滚动，规则摘要钉底 */
.fb-list { flex: 1; overflow-y: auto; padding: 12px 20px; }
.fb-item { border: 1px solid #f0f0f0; border-radius: 6px; padding: 10px 12px; margin-bottom: 10px; }
.fb-item.done { background: #fafafa; }
.fb-item-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 12.5px; }
.fb-bill { font-weight: 500; }
.fb-msg { margin-top: 6px; font-size: 12.5px; line-height: 1.7; word-break: break-all; }
.fb-note-line { margin-top: 6px; font-size: 12.5px; line-height: 1.7; padding: 6px 8px;
  background: #f0f7ff; border-radius: 6px; word-break: break-all; }
.fb-note-tag { flex: none; transform: scale(.85); transform-origin: left center; }
.fb-no-note { margin-top: 6px; font-size: 12px; }
.fb-resolve { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.fb-keep-input { flex: 1; min-width: 260px; }
.fb-resolved { margin-top: 8px; font-size: 12.5px; line-height: 1.6; }
.fb-rule { border-top: 1px solid #f0f0f0; background: #fafafa; padding: 12px 20px; }
.fb-rule-t { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.fb-kv { display: grid; grid-template-columns: 76px 1fr 76px 1fr; gap: 6px 14px; font-size: 12.5px; }
.fb-kv .k { color: rgba(0, 0, 0, .45); }
.fb-kv .v { word-break: break-all; }
</style>
