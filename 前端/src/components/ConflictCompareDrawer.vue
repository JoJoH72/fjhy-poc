<script setup lang="ts">
/**
 * 冲突对比与裁决抽屉（2026-10-27 P0-1，客户点名功能）。
 *
 * 客户投诉原话：「冲突标注点不开，看不到两条规则的对比原文」。
 * 本抽屉把一条 conflicts_with 关系摆成四段：
 *   ① 概览条（冲突类型/对方/缺口/裁决状态）
 *   ② 左右并排原文栏（本条规则+来源条款原文 vs 对方条款原文，数值高亮）
 *   ③ 八要素差异对照表（差异行高亮，其余要素合并为一行）
 *   ④ 裁决 footer（保留本条/保留对方/人工合并 + 结论必填 + 会话人署名）
 *
 * 裁决走 POST /relations/resolve：note 追加 resolved 段（原依据不动）、
 * needs_confirm 置 false、op_log 落「裁决规则冲突」——谁拍的板可追溯。
 * merge 不改规则：改口径必须经修订+发布（ck_release_signed 纪律）。
 */
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { api } from '../api'
import type { CompareClause, CompareSideRule, RelationCompare } from '../api'
import * as session from '../data/session'

const props = defineProps<{
  open: boolean
  ruleId: string
  /** 对方定位串（relations 行的 other_rule 原值） */
  other: string
}>()
const emit = defineEmits<{ 'update:open': [v: boolean]; resolved: [] }>()

const data = ref<RelationCompare | null>(null)
const err = ref('')
const loading = ref(false)
const decision = ref<'left' | 'right' | 'merge'>('left')
const resolution = ref('')
const submitting = ref(false)

async function load() {
  loading.value = true
  err.value = ''
  data.value = null
  try {
    data.value = await api.relationCompare(props.ruleId, props.other)
    decision.value = 'left'
    resolution.value = ''
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

// immediate：父组件用 :key 重建本组件时 open 初值即 true（watch 等不到变化）
watch(() => props.open, (v) => { if (v) load() }, { immediate: true })

/** 会话人署名（只读展示）——署名跟会话走，不留第二个来源 */
const who = computed(() => {
  const s = session.current()
  return s?.user || ''
})

const KIND_COLOR: Record<string, string> = {
  '阈值不一致': 'orange', '控制类型不一致': 'red', '适用范围不一致': 'blue',
}
const kindLabel = computed(() => {
  const n = data.value?.note || {}
  return String(n.kind || (n.their_value ? '阈值不一致' : '口径冲突'))
})

const noteWhy = computed(() => String((data.value?.note || {}).why || ''))
const noteTheirText = computed(() => String((data.value?.note || {}).their_text || ''))
const noteTheirValue = computed(() => String((data.value?.note || {}).their_value || ''))
const noteOurValue = computed(() => String((data.value?.note || {}).our_value || ''))
const noteResolution = computed(() => String((data.value?.note || {}).resolution || ''))
const resolvedInfo = computed(() => (data.value?.note || {}).resolved as
  { decision: string; resolution: string; resolved_by: string; resolved_at: string } | undefined)

/** 原文数值 <mark> 高亮：把 nums 的位置区间切成分段文本 */
function markBody(cl: CompareClause | null): { text: string; hit: boolean }[] {
  if (!cl || !cl.body) return []
  const nums = [...(cl.nums || [])].sort((a, b) => a.at - b.at)
  const out: { text: string; hit: boolean }[] = []
  let pos = 0
  for (const n of nums) {
    if (n.at > pos) out.push({ text: cl.body.slice(pos, n.at), hit: false })
    const end = n.at + n.value.length
    out.push({ text: cl.body.slice(n.at, end), hit: true })
    pos = end
  }
  if (pos < cl.body.length) out.push({ text: cl.body.slice(pos), hit: false })
  return out
}

/** 八要素差异对照行 */
interface DiffRow { label: string; left: string; right: string; diff: boolean }
const fmt = (v: unknown): string =>
  v === null || v === undefined || v === '' ? '—'
    : typeof v === 'object' ? JSON.stringify(v)
      : String(v)

function sideOf(): { l: CompareSideRule | null; r: CompareSideRule | null; rLabel: string } {
  const d = data.value
  if (!d) return { l: null, r: null, rLabel: '' }
  const l = d.left
  if (d.right.kind === 'rule') return { l, r: d.right.rule, rLabel: d.right.rule.rule_id }
  return { l, r: null, rLabel: d.right.label }
}

const diffRows = computed<DiffRow[]>(() => {
  const { l, r } = sideOf()
  if (!l) return []
  const note = data.value?.note || {}
  const rows: DiffRow[] = []
  const push = (label: string, lv: string, rv: string) =>
    rows.push({ label, left: lv, right: rv, diff: lv !== rv })
  // 对方是条款（非规则）时：阈值类冲突把数值分歧摆在第一行；
  // 控制类型/适用范围在 note 无结构化字段时以原文呈现，不硬造。
  const rv = (v: string) => (r ? v : (r ? '' : ''))
  push('① 规则名称', fmt(l.rule_name), r ? fmt(r.rule_name) : '（非库内规则）')
  push('② 审核维度', fmt(l.dimension), r ? fmt(r.dimension) : '—')
  push('③ 风险类别', fmt(l.risk_class), r ? fmt(r.risk_class) : '—')
  if (noteTheirValue.value || noteOurValue.value) {
    push('阈值', noteOurValue.value || '—', noteTheirValue.value || '—')
  }
  push('控制类型', fmt(l.control_type), r ? fmt(r.control_type) : '—')
  push('⑤ 适用范围', fmt(l.scope), r ? fmt(r.scope) : '—')
  push('⑥ 判定逻辑', fmt(l.logic), r ? fmt(r.logic) : '—')
  push('⑦ 例外', fmt(l.exception), r ? fmt(r.exception) : '—')
  push('⑧ 优先级', fmt(l.priority), r ? fmt(r.priority) : '—')
  push('生效期',
    `${l.effective_from || '—'} ~ ${l.effective_to || '长期'}`,
    r ? `${r.effective_from || '—'} ~ ${r.effective_to || '长期'}` : '—')
  return rows
})
const nDiff = computed(() => diffRows.value.filter((x) => x.diff).length)
const sameNote = computed(() =>
  diffRows.value.length && !nDiff.value ? '其余要素一致' : '')

async function submit() {
  if (!resolution.value.trim()) {
    message.warning('裁决须填写结论（留痕口径）')
    return
  }
  submitting.value = true
  try {
    await api.relationResolve(props.ruleId, {
      decision: decision.value,
      resolution: resolution.value.trim(),
      by: who.value,
    })
    message.success(decision.value === 'merge'
      ? '裁决已留痕；人工合并需修订规则并随版本发布后生效'
      : '裁决已留痕（操作日志可查）')
    emit('resolved')
    emit('update:open', false)
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <a-drawer :open="open" width="1080" placement="right" :mask-closable="false"
    :body-style="{ padding: '0', display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
    title="冲突对比与裁决"
    @close="emit('update:open', false)">
    <div v-if="loading" style="padding: 40px; text-align: center; color: rgba(0,0,0,.45)">
      正在加载两侧口径…
    </div>
    <div v-else-if="err" style="padding: 24px">
      <a-alert type="error" show-icon message="取对比数据失败" :description="err" />
    </div>
    <template v-else-if="data">
      <!-- ① 概览条 -->
      <div class="overview">
        <a-tag :color="KIND_COLOR[kindLabel] || 'red'">{{ kindLabel }}</a-tag>
        <span class="ov-pair">
          <router-link :to="`/asset/rules/${encodeURIComponent(data.rule_id)}`">
            {{ data.rule_id }}
          </router-link>
          <span class="dim"> vs </span>
          <b>{{ data.right.label }}</b>
        </span>
        <a-tag v-if="data.relation.gap_id" color="default">缺口 {{ data.relation.gap_id }}</a-tag>
        <a-tag v-if="resolvedInfo" color="success">
          已裁决 · {{ resolvedInfo.resolved_by }} · {{ resolvedInfo.resolved_at?.slice(0, 10) }}
        </a-tag>
        <a-tag v-else-if="data.relation.needs_confirm" color="warning">需业务侧裁决</a-tag>
      </div>

      <!-- ② 左右并排原文 -->
      <div class="split">
        <div class="pane">
          <div class="pane-head">
            <b>本条规则</b>
            <router-link class="pane-link" :to="`/asset/rules/${encodeURIComponent(data.rule_id)}`">
              {{ data.rule_id }} 详情 →
            </router-link>
          </div>
          <div class="pane-body">
            <template v-if="data.left">
              <div class="rname">{{ data.left.rule_name }}</div>
              <div class="meta-row">
                <a-tag :color="data.left.control_type === '禁止' ? 'error' : 'warning'">
                  {{ data.left.control_type }}
                </a-tag>
                <span class="dim">{{ data.left.dimension }} · 优先级 {{ data.left.priority }}</span>
                <span class="dim">生效 {{ data.left.effective_from || '—' }} ~
                  {{ data.left.effective_to || '长期' }}</span>
              </div>
              <div v-if="data.left.source_clause_full" class="quote">
                <div class="q-head">
                  <code>{{ data.left.source_clause_full.file_name }}</code>
                  <span>{{ data.left.source_clause_full.clause_no }}</span>
                  <a-tag :color="data.left.source_clause_full.text_source === 'OCR' ? 'warning' : 'success'"
                    style="margin-left: auto">
                    {{ data.left.source_clause_full.text_source || data.left.source_clause?.confidence }}
                  </a-tag>
                </div>
                <p class="q-body">
                  <template v-for="(seg, i) in markBody(data.left.source_clause_full)" :key="i">
                    <mark v-if="seg.hit">{{ seg.text }}</mark>
                    <template v-else>{{ seg.text }}</template>
                  </template>
                </p>
                <div v-if="data.left.source_clause_full.fallback_text" class="q-note">
                  条款库未命中该条款号，以上为装载时备份的出处原文
                </div>
              </div>
            </template>
          </div>
        </div>

        <div class="pane">
          <div class="pane-head"><b>对方口径</b></div>
          <div class="pane-body">
            <!-- 对方=库内规则 -->
            <template v-if="data.right.kind === 'rule' && data.right.rule">
              <div class="rname">{{ data.right.rule.rule_name }}</div>
              <div class="meta-row">
                <a-tag :color="data.right.rule.control_type === '禁止' ? 'error' : 'warning'">
                  {{ data.right.rule.control_type }}
                </a-tag>
                <span class="dim">{{ data.right.rule.dimension }} · 优先级
                  {{ data.right.rule.priority }}</span>
              </div>
              <div v-if="data.right.rule.source_clause_full" class="quote">
                <div class="q-head">
                  <code>{{ data.right.rule.source_clause_full.file_name }}</code>
                  <span>{{ data.right.rule.source_clause_full.clause_no }}</span>
                </div>
                <p class="q-body">
                  <template v-for="(seg, i) in markBody(data.right.rule.source_clause_full)"
                    :key="i">
                    <mark v-if="seg.hit">{{ seg.text }}</mark>
                    <template v-else>{{ seg.text }}</template>
                  </template>
                </p>
              </div>
            </template>
            <!-- 对方=制度条款出处（可能多段） -->
            <template v-else-if="data.right.kind === 'clause'">
              <div v-for="s in data.right.sources" :key="s.code + s.clause_no" class="quote">
                <div class="q-head">
                  <code>{{ s.code }}</code>
                  <span>{{ s.clause_no }}</span>
                  <a-tag v-if="s.found" color="success" style="margin-left: auto">条款库全文</a-tag>
                  <a-tag v-else-if="s.clause" color="warning" style="margin-left: auto">装载备份</a-tag>
                  <a-tag v-else color="default" style="margin-left: auto">原文未入库</a-tag>
                </div>
                <p v-if="s.clause" class="q-body">
                  <template v-for="(seg, i) in markBody(s.clause)" :key="i">
                    <mark v-if="seg.hit">{{ seg.text }}</mark>
                    <template v-else>{{ seg.text }}</template>
                  </template>
                </p>
                <div v-else class="q-note">
                  出处文件未切条款或不在比对集内，无原文可显示
                </div>
              </div>
              <!-- 数值分歧（note 的 their/our value）：原文缺失时的差异主证据 -->
              <div v-if="noteTheirText || (noteTheirValue && noteOurValue)" class="quote">
                <div class="q-head"><b>装载时记下的分歧</b></div>
                <p v-if="noteTheirText" class="q-body">{{ noteTheirText }}</p>
                <div v-if="noteTheirValue && noteOurValue" class="valdiff">
                  本条 <mark>{{ noteOurValue }}</mark>
                  <span class="dim"> vs </span>
                  对方 <mark>{{ noteTheirValue }}</mark>
                </div>
              </div>
            </template>
            <div v-if="noteWhy" class="why">
              <b>裁决依据（装载时记）：</b><MarkText :text="noteWhy" />
              <div v-if="noteResolution" class="dim" style="margin-top: 2px">
                暂定结论：{{ noteResolution }}
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ③ 差异对照表 -->
      <div class="diffblk">
        <div class="diff-title">
          八要素对照
          <a-tag v-if="nDiff" color="orange">{{ nDiff }} 项不同</a-tag>
          <span v-else class="dim">各要素一致，分歧仅在原文表述</span>
        </div>
        <table class="diff-table">
          <tbody>
            <tr v-for="row in diffRows" :key="row.label" :class="{ diffr: row.diff }">
              <td class="dl">{{ row.label }}</td>
              <td>{{ row.left }}</td>
              <td>{{ row.right }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="sameNote" class="dim" style="margin-top: 6px">{{ sameNote }}</div>
      </div>
    </template>

    <!-- ④ 裁决 footer -->
    <template #footer>
      <div v-if="data && !resolvedInfo" class="resolve">
        <a-radio-group v-model:value="decision" class="rg">
          <a-radio value="left">保留本条规则</a-radio>
          <a-radio value="right">保留对方口径</a-radio>
          <a-radio value="merge">人工合并（经发布生效）</a-radio>
        </a-radio-group>
        <div class="rg-row">
          <a-textarea v-model:value="resolution" :rows="2" style="flex: 1"
            placeholder="裁决结论（必填）：所依据的口径与理由，随留痕可查" />
        </div>
        <div class="rg-row">
          <span class="dim">裁决人：<b>{{ who }}</b>（会话登录人）</span>
          <div style="flex: 1"></div>
          <a-button :disabled="submitting" @click="emit('update:open', false)">取消</a-button>
          <a-button type="primary" :loading="submitting" :disabled="!resolution.trim()"
            @click="submit">提交裁决</a-button>
        </div>
      </div>
      <div v-else-if="resolvedInfo" class="resolved-note">
        <a-tag color="success">已裁决</a-tag>
        {{ resolvedInfo.resolved_by }} 于 {{ resolvedInfo.resolved_at }} 裁决：
        {{ resolvedInfo.resolution }}
      </div>
    </template>
  </a-drawer>
</template>

<style scoped>
.overview {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 12px 20px; border-bottom: 1px solid #f0f0f0; background: #fafafa;
}
.ov-pair { font-size: 13px; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }

.split { display: grid; grid-template-columns: 1fr 1fr; border-bottom: 1px solid #f0f0f0; }
.pane { min-width: 0; display: flex; flex-direction: column; max-height: 40vh; }
.pane + .pane { border-left: 1px solid #f0f0f0; }
.pane-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 20px; font-size: 13px; border-bottom: 1px dashed #f0f0f0;
}
.pane-link { font-size: 12px; }
.pane-body { padding: 12px 20px; overflow-y: auto; }
.rname { font-size: 14px; font-weight: 600; margin-bottom: 6px; }
.meta-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }

.quote {
  background: #fafafa; border: 1px solid #f0f0f0; border-radius: 6px;
  padding: 8px 10px; margin-bottom: 10px;
}
.q-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 12px; }
.q-head code { background: #f0f0f0; padding: 1px 6px; border-radius: 4px; font-size: 11px; }
.q-body { margin: 0; font-size: 12.5px; line-height: 1.8; white-space: pre-wrap; word-break: break-all; }
.q-body mark { background: #fffbe6; padding: 0 1px; }
.q-note { font-size: 11px; color: rgba(0, 0, 0, .4); margin-top: 4px; }
.valdiff { font-size: 13px; margin-top: 4px; }
.valdiff mark { background: #fffbe6; font-weight: 600; padding: 0 2px; }
.why { font-size: 12px; color: rgba(0, 0, 0, .55); line-height: 1.7;
  border-left: 3px solid #e5e7eb; padding-left: 8px; margin-top: 4px; }

.diffblk { padding: 12px 20px; overflow-y: auto; flex: 1; }
.diff-title { font-size: 13px; font-weight: 600; margin-bottom: 8px;
  display: flex; align-items: center; gap: 8px; }
.diff-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.diff-table td {
  border: 1px solid #f0f0f0; padding: 6px 10px; vertical-align: top;
  word-break: break-all; max-width: 430px;
}
.diff-table .dl { width: 110px; color: rgba(0, 0, 0, .45); background: #fafafa; }
.diff-table tr.diffr td { background: #fffbe6; }
.diff-table tr.diffr td.dl { border-left: 3px solid #fa8c16; }

.resolve { display: flex; flex-direction: column; gap: 8px; }
.rg :deep(label) { font-size: 13px; }
.rg-row { display: flex; align-items: center; gap: 8px; }
.resolved-note { font-size: 13px; line-height: 1.7; }
</style>
