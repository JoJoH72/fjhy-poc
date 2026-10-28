<script setup lang="ts">
/**
 * 抽取与审定（2026-10-27 P0-4，规划 §2.2 核心交互版）。
 *
 * 旧 Extract.vue 随死代码退役被删后，四步工作流的「②跑 AI → ③审定」在界面上
 * 断了——后端链路完好（92 条款 / 21 草稿 / 40 明细），页面没了。本组件把它
 * 挂回制度文件页第六页签，兑现 navTree 注释宣称的「文件库+抽取合并」。
 *
 * 三段：
 *   待审队列条 —— 状态过滤 + 增量抽取按钮（带份数与费用预告；0 份置灰）
 *   并排审定卡 —— 左条款原文 / 右八要素草稿，数值幻觉红标；审定人=会话人只读
 *   口径冲突   —— B 场景（doclib_conflict）列表，行点开对比裁决
 *                 （复用 ConflictCompareDrawer 的 B 场景形态见 §P0-7 后续）
 *
 * 审定纪律（与驳回/裁决同源）：采纳驳回都留痕、驳回必填原因、署名跟会话走。
 */
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { api } from '../api'
import type { ExConflict, ExDraft, ExProgress } from '../api'
import * as session from '../data/session'

// ── 增量抽取 ────────────────────────────────────────────────────────────────
const inc = ref<{ total: number; will_call_model: number; candidates_known: number
                   candidates_unknown_files: string[]; blocked_total: number
                   cost_note: string; note: string } | null>(null)
const incErr = ref('')
const running = ref(false)
const runNote = ref('')

async function loadInc() {
  try { inc.value = await api.exIncremental() } catch (e) {
    incErr.value = e instanceof Error ? e.message : String(e)
  }
}

const who = computed(() => session.current()?.user || '')

async function runExtract() {
  running.value = true
  runNote.value = ''
  try {
    const r = await api.exRun({ only_changed: true })
    message.success('增量抽取已启动（后台执行）')
    runNote.value = `任务 ${r.job_id || ''} 已启动，进度见下方「抽取进度」`
    await loadProgress()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    running.value = false
  }
}

// ── 进度 ────────────────────────────────────────────────────────────────────
const prog = ref<ExProgress | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

async function loadProgress() {
  try {
    const p = await api.exProgress()
    prog.value = p
    if (p.status === 'running' && !timer) {
      timer = setInterval(async () => {
        const p2 = await api.exProgress()
        prog.value = p2
        if (p2.status !== 'running' && timer) { clearInterval(timer); timer = null; loadDrafts() }
      }, 3000)
    }
  } catch { /* 进度取不到不致命，下一轮再试 */ }
}

onMounted(() => { loadInc(); loadProgress(); loadDrafts(); loadConflicts() })

// ── 草稿审定 ────────────────────────────────────────────────────────────────
const drafts = ref<ExDraft[]>([])
const dState = ref('待审')
const busy = ref('')
const rejectId = ref('')      // 正在驳回的草稿
const rejectReason = ref('')

async function loadDrafts() {
  try {
    const r = await api.exDrafts(dState.value ? { state: dState.value, limit: 200 } : { limit: 200 })
    drafts.value = r.items
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

const EIGHT_FIELDS: { key: string; label: string }[] = [
  { key: '规则名称', label: '① 规则名称' },
  { key: '审核维度', label: '② 审核维度' },
  { key: '风险等级', label: '③ 风险类别' },
  { key: '适用范围', label: '⑤ 适用范围' },
  { key: '判定逻辑', label: '⑥ 判定逻辑' },
  { key: '例外情形', label: '⑦ 例外' },
  { key: '处置动作', label: '处置动作' },
  { key: '规则类型', label: '规则类型' },
]

/** 草稿字段命中数值红标：number_check.suspect 的 field 名对到八要素字段 */
function fieldSuspect(d: ExDraft, key: string): string[] {
  return (d.number_check?.suspect || [])
    .filter((s) => s.field === key || key.includes(s.field))
    .map((s) => s.value)
}

const review = (d: ExDraft, state: string) =>
  (async () => {
    busy.value = d.draft_id
    try {
      await api.exReview(d.draft_id, state, who.value,
        state === '驳回' ? rejectReason.value : undefined)
      message.success(`已${state}（审定人 ${who.value}，留痕可查）`)
      if (state === '驳回') { rejectId.value = ''; rejectReason.value = '' }
      await loadDrafts()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      busy.value = ''
    }
  })()

const startReject = (d: ExDraft) => {
  rejectId.value = rejectId.value === d.draft_id ? '' : d.draft_id
  rejectReason.value = ''
}

// ── 口径冲突（B 场景）──────────────────────────────────────────────────────
const conflicts = ref<ExConflict[]>([])
const confState = ref('待裁决')
/** 展开对比的冲突行：conflict_id → 两侧条款全文（薄接口 clauses?clause_id= 反查） */
const confOpen = ref('')
const confClauses = ref<Record<string, { clause_no: string; body: string
                                         file_name: string; text_source: string }>>({})

async function loadConflicts() {
  try {
    const r = await api.ontoConflicts(confState.value || undefined)
    conflicts.value = r.items
  } catch { conflicts.value = [] }
}

/** 展开一条冲突：取 left/right 两条条款全文并排（客户要的「对比原文」在 B 场景的形态） */
async function openConflict(c: ExConflict) {
  confOpen.value = confOpen.value === c.conflict_id ? '' : c.conflict_id
  if (confOpen.value !== c.conflict_id) return
  for (const cid of [c.left_clause, c.right_clause]) {
    if (confClauses.value[cid]) continue
    try {
      confClauses.value[cid] = await api.exClause(cid)
    } catch { /* 反查失败时展开区显示「原文未取到」 */ }
  }
}

/** 条款原文数值 <mark> 高亮（与 A 场景同一逻辑：数值+单位形态） */
function clauseMarks(body: string): { text: string; hit: boolean }[] {
  const re = /(\d+(?:\.\d+)?)\s*(元|天|日|人|次|公里|%|％)/g
  const out: { text: string; hit: boolean }[] = []
  let pos = 0
  for (const m of body.matchAll(re)) {
    const i = m.index ?? 0
    if (i > pos) out.push({ text: body.slice(pos, i), hit: false })
    out.push({ text: m[0], hit: true })
    pos = i + m[0].length
  }
  if (pos < body.length) out.push({ text: body.slice(pos), hit: false })
  return out
}

/** B 场景裁决：已裁决必填结论。复用 ontoResolveConflict。 */
const confResolving = ref('')
const confDecision = ref<Record<string, string>>({})
const confText = ref<Record<string, string>>({})

const resolveConflict = (c: ExConflict) =>
  (async () => {
    confResolving.value = c.conflict_id
    try {
      await api.ontoResolveConflict(c.conflict_id, '已裁决', who.value,
        JSON.stringify({ decision: confDecision.value[c.conflict_id] || 'left',
                         text: confText.value[c.conflict_id] || '' }))
      message.success('冲突已裁决，该归并组解锁采纳（操作日志留痕）')
      await loadConflicts()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      confResolving.value = ''
    }
  })()

const ignoreConflict = (c: ExConflict) =>
  (async () => {
    confResolving.value = c.conflict_id
    try {
      await api.ontoResolveConflict(c.conflict_id, '已忽略', who.value, '不再提示该分歧')
      message.success('已忽略（下次抽取不再检出）')
      await loadConflicts()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      confResolving.value = ''
    }
  })()

const KIND_COLOR: Record<string, string> = {
  '阈值不一致': 'orange', '控制类型不一致': 'red', '适用范围不一致': 'blue',
}
</script>

<template>
  <div class="ext">
    <!-- ① 增量抽取队列条 -->
    <div class="runbar">
      <template v-if="inc">
        <a-button type="primary" :loading="running" :disabled="!!running || inc.total === 0"
          :title="inc.total === 0 ? (inc.note || '没有待抽取的文件') : inc.cost_note"
          @click="runExtract">
          增量抽取（{{ inc.total }} 份 · 最多 {{ inc.will_call_model }} 条候选 · 出网计费）
        </a-button>
        <span v-if="inc.total === 0" class="dim">
          当前没有待抽取的文件——{{ inc.blocked_total ? `另有 ${inc.blocked_total} 份取不到正文，先去文件清单办 OCR` : '全部文件都已抽取过' }}
        </span>
        <span v-else class="dim">
          {{ inc.candidates_known }} 条候选已知
          <template v-if="inc.candidates_unknown_files.length">
            ；另有 {{ inc.candidates_unknown_files.length }} 份切完条款才知道
          </template>
        </span>
      </template>
      <a-alert v-else-if="incErr" type="error" show-icon :message="`增量预览失败：${incErr}`" />
      <span v-else class="dim">读取增量范围…</span>
    </div>

    <!-- ② 抽取进度（有任务时显示） -->
    <a-alert v-if="prog && prog.status === 'running'" type="info" show-icon
      style="margin-bottom: 12px"
      :message="`抽取进行中 ${prog.pct}%（${prog.elapsed ?? 0}s）——${
        (prog.agents || []).filter(a => a.status === 'running').map(a => a.agent_name).join('、') || '启动中'}`" />

    <!-- ③ 草稿并排审定 -->
    <div class="queuebar">
      <a-radio-group v-model:value="dState" @change="loadDrafts">
        <a-radio-button value="待审">待审</a-radio-button>
        <a-radio-button value="采纳">已采纳</a-radio-button>
        <a-radio-button value="驳回">已驳回</a-radio-button>
        <a-radio-button value="">全部</a-radio-button>
      </a-radio-group>
      <span class="dim">{{ drafts.length }} 条</span>
      <span class="dim" style="margin-left: auto">审定人：<b>{{ who }}</b>（会话登录人）</span>
    </div>

    <a-empty v-if="!drafts.length" :image-style="{ height: '40px' }"
      :description="dState === '待审' ? '暂无待审草稿（可跑增量抽取）' : '该状态下无草稿'" />

    <div v-for="d in drafts" :key="d.draft_id" class="dcard">
      <div class="dcard-head">
        <code class="fname">{{ d.file_name }}</code>
        <b>{{ d.clause_no }}</b>
        <a-tag :color="d.rulable === false ? 'default' : 'processing'">
          {{ d.rulable === false ? '不可规则化' : 'AI 草稿' }}
        </a-tag>
        <a-tag :color="d.review_state === '采纳' ? 'success' : d.review_state === '驳回' ? 'error' : 'default'">
          {{ d.review_state }}
        </a-tag>
        <span v-if="d.reviewer" class="dim">{{ d.reviewer }} 审</span>
        <span v-if="d.truncated" class="dim" title="长条款发送模型时截断，草稿可能仅覆盖前半段">已截断</span>
      </div>
      <div class="dcard-body">
        <div class="dpane">
          <div class="dp-title">条款原文</div>
          <pre class="dp-pre">{{ d.clause_body || '（原文未取到）' }}</pre>
        </div>
        <div class="dpane">
          <div class="dp-title">八要素草稿
            <span v-if="d.model" class="dim">{{ d.model }} · {{ d.latency_ms }}ms</span>
          </div>
          <div class="kv8">
            <template v-for="f in EIGHT_FIELDS" :key="f.key">
              <b>{{ f.label }}</b>
              <span :class="{ sus: fieldSuspect(d, f.key).length }"
                :title="fieldSuspect(d, f.key).length
                  ? `数值 ${fieldSuspect(d, f.key).join('、')} 在原文未找到，疑似模型幻觉，须人工核对` : ''">
                {{ (d.draft as any)[f.key] || '—' }}
                <a-tag v-if="fieldSuspect(d, f.key).length" color="error">数值待核</a-tag>
              </span>
            </template>
            <b>缺口</b>
            <span class="dim">{{ (d.draft as any)['缺口'] || '—' }}</span>
          </div>
          <div v-if="(d.number_check?.suspect || []).length" class="susbar">
            数值待核：{{ d.number_check.suspect.map(s => `${s.field}=${s.value}`).join('、') }}
            在条款原文中未找到，采纳前须核对制度原文
          </div>
        </div>
      </div>
      <div v-if="d.review_state === '待审'" class="dcard-foot">
        <a-button type="primary" size="small" :disabled="!!busy"
          @click="review(d, '采纳')">采 纳</a-button>
        <a-button v-if="rejectId !== d.draft_id" size="small" :disabled="!!busy"
          @click="startReject(d)">驳 回</a-button>
        <template v-else>
          <a-input v-model:value="rejectReason" size="small" style="width: 320px"
            placeholder="驳回原因（必填）：无原因的驳回在下次抽取会再次出现" />
          <a-button danger size="small" :disabled="!!busy || !rejectReason.trim()"
            @click="review(d, '驳回')">确认驳回</a-button>
          <a-button size="small" @click="rejectId = ''">取消</a-button>
        </template>
        <span class="dim" style="margin-left: auto">采纳仅标记草稿；进入规则库需在规则库页采纳入本体</span>
      </div>
      <div v-else-if="d.reject_reason" class="dcard-foot dim">
        驳回原因：{{ d.reject_reason }}
      </div>
    </div>

    <!-- ④ 口径冲突（B 场景）-->
    <h3 class="sec">口径冲突（多源制度条款之间的分歧）</h3>
    <div class="queuebar">
      <a-radio-group v-model:value="confState" @change="loadConflicts">
        <a-radio-button value="待裁决">待裁决</a-radio-button>
        <a-radio-button value="已裁决">已裁决</a-radio-button>
        <a-radio-button value="已忽略">已忽略</a-radio-button>
        <a-radio-button value="">全部</a-radio-button>
      </a-radio-group>
      <span class="dim">{{ conflicts.length }} 条</span>
    </div>
    <a-empty v-if="!conflicts.length" :image-style="{ height: '40px' }"
      :description="confState === '待裁决'
        ? '当前没有待裁决的口径冲突（统一口径重算后会重新检出）' : '该状态下没有冲突'" />
    <div v-for="c in conflicts" :key="c.conflict_id" class="ccard">
      <div class="ccard-head" style="cursor: pointer" @click="openConflict(c)">
        <a-tag :color="KIND_COLOR[c.kind] || 'red'">{{ c.kind }}</a-tag>
        <b>{{ c.group_key }}</b>
        <a-tag v-if="c.state === '已裁决'" color="success">{{ c.state }} · {{ c.resolved_by }}</a-tag>
        <a-tag v-else-if="c.state === '已忽略'" color="default">{{ c.state }}</a-tag>
        <a-tag v-else color="warning">{{ c.state }}</a-tag>
        <span class="dim" style="margin-left: auto">
          {{ confOpen === c.conflict_id ? '收起对比 ▲' : '对比两条条款原文 ▼' }}
        </span>
      </div>
      <div class="cdetail"><MarkText :text="c.detail" /></div>

      <!-- 并排原文对比（客户点名诉求在 B 场景的形态：两条条款全文+数值高亮） -->
      <div v-if="confOpen === c.conflict_id" class="csplit">
        <div v-for="cid in [c.left_clause, c.right_clause]" :key="cid" class="cpane">
          <template v-if="confClauses[cid]">
            <div class="dp-title">
              <code class="fname">{{ confClauses[cid].file_name }}</code>
              <b>{{ confClauses[cid].clause_no }}</b>
              <a-tag :color="confClauses[cid].text_source === 'OCR' ? 'warning' : 'success'">
                {{ confClauses[cid].text_source }}
              </a-tag>
            </div>
            <pre class="dp-pre"><template v-for="(seg, i) in clauseMarks(confClauses[cid].body)" :key="i"><mark v-if="seg.hit">{{ seg.text }}</mark><template v-else>{{ seg.text }}</template></template></pre>
          </template>
          <div v-else class="dim">原文未取到（条款可能已随文件清理）</div>
        </div>
      </div>

      <div v-if="c.resolution" class="dim cres">{{ c.resolved_by }}：{{ c.resolution }}</div>
      <div v-if="c.state === '待裁决'" class="ccard-foot" @click.stop>
        <a-radio-group v-model:value="confDecision[c.conflict_id]" size="small">
          <a-radio value="left">按左条款</a-radio>
          <a-radio value="right">按右条款</a-radio>
          <a-radio value="merge">人工合并</a-radio>
        </a-radio-group>
        <a-input v-model:value="confText[c.conflict_id]" size="small" style="width: 300px"
          placeholder="裁决结论（必填）" />
        <a-button type="primary" size="small" :loading="confResolving === c.conflict_id"
          :disabled="!(confText[c.conflict_id] || '').trim()"
          @click="resolveConflict(c)">裁决</a-button>
        <a-button size="small" :disabled="confResolving === c.conflict_id"
          @click="ignoreConflict(c)">忽略</a-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ext { display: flex; flex-direction: column; gap: 0; }
.runbar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.queuebar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.sec { font-size: 14px; font-weight: 600; margin: 20px 0 10px; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }

.dcard { border: 1px solid #f0f0f0; border-radius: 6px; margin-bottom: 12px; overflow: hidden; }
.dcard-head { display: flex; align-items: center; gap: 8px; padding: 8px 12px;
  background: #fafafa; border-bottom: 1px solid #f0f0f0; font-size: 12.5px; flex-wrap: wrap; }
.fname { font-size: 11px; background: #f0f0f0; padding: 1px 6px; border-radius: 4px;
  max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dcard-body { display: grid; grid-template-columns: 1fr 1fr; }
.dpane { min-width: 0; padding: 10px 12px; }
.dpane + .dpane { border-left: 1px solid #f0f0f0; }
.dp-title { font-size: 12.5px; font-weight: 600; margin-bottom: 6px;
  display: flex; gap: 8px; align-items: baseline; }
.dp-pre { margin: 0; font-size: 12px; line-height: 1.7; white-space: pre-wrap;
  word-break: break-all; background: #fafafa; border-radius: 4px; padding: 8px 10px;
  max-height: 260px; overflow-y: auto; }
.kv8 { display: grid; grid-template-columns: 78px 1fr; gap: 3px 10px; font-size: 12.5px; }
.kv8 b { color: rgba(0, 0, 0, .45); font-weight: 500; }
.kv8 .sus { background: #fff1f0; border-radius: 3px; padding: 1px 4px; }
.susbar { margin-top: 8px; font-size: 12px; color: #cf1322; background: #fff1f0;
  border-radius: 4px; padding: 6px 8px; line-height: 1.6; }
.dcard-foot { display: flex; align-items: center; gap: 8px; padding: 8px 12px;
  border-top: 1px solid #f0f0f0; flex-wrap: wrap; }

.ccard { border: 1px solid #f0f0f0; border-radius: 6px; padding: 10px 12px; margin-bottom: 10px; }
.ccard-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 13px; }
.cdetail { font-size: 12.5px; line-height: 1.7; }
.cres { margin-top: 4px; }
.ccard-foot { display: flex; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: wrap; }

/* 冲突并排原文对比（B 场景） */
.csplit { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 8px 0 2px; }
.cpane { min-width: 0; border: 1px solid #f0f0f0; border-radius: 6px; padding: 8px 10px; }
.cpane .dp-pre { max-height: 220px; }
.cpane mark { background: #fffbe6; }
</style>
