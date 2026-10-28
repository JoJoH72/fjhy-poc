<script setup lang="ts">
/**
 * 单据与附件（2026-10-28 Wave 3 版：B-P0-2 附件人工订正 + A-P1-2 大图层复用）。
 *
 * 附件这一块的来历（2026-10-21）：内嵌在 xlsx 里的 PNG（219 张，覆盖全部
 * 40 张真实单）抽出来了，页面要能看到原图——审核岗复核「这张发票对不对」
 * 靠的是看票面，不是看文件名。两条口径写在页面上：
 *
 * 1. **附件类型为空表示「还没判」，不是「没有类型」。**
 * 2. **「申报张数」与「实际附件数」是两个指标，并列显示，谁也不覆盖谁。**
 *
 * 2026-10-28 B-P0-2（客户排期 #3「人工修改与补录，修改留痕」）三入口：
 *   ① 改附件类型：附件表 attach_type 单元格就地「订正」→ a-select（决策表
 *      细类，数据源 /attachments/classify/status 的 type_menu，不写死）→
 *      POST /attachments/{id}/reclassify。订正后显示「人工订正」蓝标 +
 *      tooltip（谁/何时/原判什么）；AI 原值不覆盖。
 *   ② 改识别字段：操作列「订正字段」开抽屉（560px）——AI 值灰字、人工值
 *      正常色并列；逐字段改、value 置空=还原 AI 值；POST /attachments/{id}/fields。
 *   ③ 补录：未判类型行的「人工定类」（与批量 AI 补判并排、视觉区分）。
 *   scope 红线：改判不自动重跑审核——改完是否重新判定是另一个动作。
 *   权限：auditor + admin（后端双闸，engineer 403）。
 *
 * 2026-10-28 A-P1-2：大图查看器换成共享 ImageViewer（缩放/拖拽/旋转/翻张），
 * 与 ReviewReport 同款交互——同一动作同一模式。缩略图/文件名点击进入。
 */
import { computed, createVNode, ref } from 'vue'
import { useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import { ExclamationCircleOutlined } from '@ant-design/icons-vue'
import Async from '../components/Async.vue'
import ImageViewer from '../components/ImageViewer.vue'
import { api, type Attachment, type AttachmentClassifyItem, type AttFields } from '../api'
import * as session from '../data/session'

const route = useRoute()
const no = computed(() => String(route.params.no))

/** 展开看大图的那一张（A-P1-2：ImageViewer 共享组件）。 */
const lbOpen = ref(false)
const lbStart = ref<number | null>(null)
/** 大图层清单：openLarge 时从本单附件装配（有图的那些） */
const lbItems = ref<{ id: number; file_name: string }[]>([])
function openLarge(a: Attachment, atts?: Attachment[]) {
  if (atts && atts.length) {
    lbItems.value = atts.filter((x) => x.has_file)
      .map((x) => ({ id: x.id, file_name: String(x.file_name || '') }))
  }
  lbStart.value = a.id
  lbOpen.value = true
}
const fileUrl = (id: number) => `/api/attachments/${id}/file`

function kb(n: number | null | undefined) {
  if (n == null) return '—'
  return n < 1024 ? `${n} B`
    : n < 1048576 ? `${(n / 1024).toFixed(1)} KB`
      : `${(n / 1048576).toFixed(1)} MB`
}

// ── 读图判类型（既有，AI 批量补判）─────────────────────────────────────────
// **真出网、真计费**，发出去的是真实发票与出差申请单。两段确认——第一下只是
// 「上膛」，把要调几次模型摆出来；第二下才真跑。
const classifyBusy = ref(false)
const classifyErr = ref('')
const classifyMsg = ref('')
/** 正在等第二次确认的目标：单条附件 id，或 'batch'（本单剩余全部）*/
const armed = ref<number | 'batch' | null>(null)
/** 最近一次分类结果，按 attachment_id 存，用来在表格里就地显示依据/备注 */
const lastResult = ref<Record<number, AttachmentClassifyItem>>({})

function untypedOf(atts: Attachment[]): Attachment[] {
  return atts.filter(a => a.source_kind === 'xlsx内嵌' && !a.attach_type)
}

async function classifyOne(id: number, reload: () => Promise<void>) {
  if (armed.value !== id) {
    armed.value = id            // 第一下只上膛
    return
  }
  armed.value = null
  classifyBusy.value = true
  classifyErr.value = ''
  try {
    const r = await api.attClassify({ attachment_ids: [id], limit: 1 })
    const it = r.items[0]
    if (it) lastResult.value = { ...lastResult.value, [id]: it }
    classifyMsg.value = it?.applied
      ? `判定为「${it.type_label}」（把握：${it.confidence}），已写入`
      : it?.ok
        ? `读图完成，但${it?.type_code ? `把握只有「${it.confidence}」` : '模型判不出'}，未写入——见下方依据`
        : `读图失败：${it?.message || '未知错误'}`
    await reload()
  } catch (e) {
    classifyErr.value = e instanceof Error ? e.message : String(e)
  } finally {
    classifyBusy.value = false
  }
}

async function classifyBatch(atts: Attachment[], reload: () => Promise<void>) {
  const ids = untypedOf(atts).map(a => a.id)
  if (!ids.length) return
  if (armed.value !== 'batch') {
    armed.value = 'batch'
    return
  }
  armed.value = null
  classifyBusy.value = true
  classifyErr.value = ''
  try {
    const r = await api.attClassify({ attachment_ids: ids, limit: ids.length })
    for (const it of r.items) lastResult.value = { ...lastResult.value, [it.attachment_id]: it }
    classifyMsg.value = `本单读图 ${r.tried} 张：写入 ${r.applied} 条，`
      + `${r.tried - r.applied} 条未写入（判不出或把握不够，见表格里的依据列）`
    await reload()
  } catch (e) {
    classifyErr.value = e instanceof Error ? e.message : String(e)
  } finally {
    classifyBusy.value = false
  }
}

// ── B-P0-2 ①③ 人工订正类型 / 未判人工定类 ────────────────────────────────
// 与 AI 批量补判并存不合并：AI 是花钱的推断，人工是免费的拍板，两个入口两个形态。
/** 订正权限：auditor + admin（后端同款双闸，前端只收敛入口） */
const canJudge = computed(() => {
  const r = session.current()?.role
  return r === 'auditor' || r === 'admin'
})
/** 决策表细类菜单（type_menu 实时取，不写死 15 类） */
const typeMenu = ref<{ code: string; label: string; en17: string; ocr_needed: boolean }[]>([])
api.attClassifyStatus().then((s) => { typeMenu.value = s.type_menu || [] })
  .catch(() => { /* 菜单取不到：订正按钮点了会报错，不静默造空菜单 */ })

/** 正在就地订正的附件 id */
const judgingId = ref<number | null>(null)
const judgePick = ref<string>('')
const judgeBusy = ref(false)
const judgeErr = ref('')

function startJudge(a: Attachment) {
  judgingId.value = a.id
  judgePick.value = a.type_code || ''
  judgeErr.value = ''
}

async function submitJudge(a: Attachment, reload: () => Promise<void>) {
  if (!judgePick.value) return
  judgeBusy.value = true
  judgeErr.value = ''
  try {
    const r = await api.attReclassify(a.id, judgePick.value)
    classifyMsg.value = r.unchanged
      ? `「${a.file_name}」已是人工指定的该类型，无需再改`
      : `已订正为「${r.type_label}」（原判定：${r.old_value || '未判定'}），留痕可查`
    judgingId.value = null
    await reload()
  } catch (e) {
    judgeErr.value = e instanceof Error ? e.message : String(e)
  } finally {
    judgeBusy.value = false
  }
}

// ── B-P0-2 ② 订正字段抽屉 ──────────────────────────────────────────────────
const fldOpen = ref(false)
const fldAtt = ref<Attachment | null>(null)
const fldData = ref<AttFields | null>(null)
const fldErr = ref('')
const fldBusy = ref(false)
/** 编辑中的值：field → string | null（null=清除人工值还原 AI 值） */
const fldEdits = ref<Record<string, string>>({})

async function openFields(a: Attachment, reload?: () => Promise<void>) {
  fldAtt.value = a
  fldOpen.value = true
  fldErr.value = ''
  fldEdits.value = {}
  fldData.value = null
  try {
    const d = await api.attFields(a.id)
    fldData.value = d
    // 预填：有人工值的带出人工值，没有的填 AI 值（改完 diff 出真正动过的键）
    for (const f of d.fields) {
      fldEdits.value[f.field] = f.manual_value ?? (f.ai_value != null ? String(f.ai_value) : '')
    }
  } catch (e) {
    fldErr.value = e instanceof Error ? e.message : String(e)
  }
  void reload
}

/** 提交字段订正：只提交「值真的变了」的键 */
async function submitFields(reload: () => Promise<void>) {
  if (!fldAtt.value || !fldData.value) return
  const updates: { field: string; value: string | null }[] = []
  for (const f of fldData.value.fields) {
    const orig = f.manual_value ?? (f.ai_value != null ? String(f.ai_value) : '')
    const now = fldEdits.value[f.field] ?? ''
    if (String(now).trim() !== String(orig).trim()) {
      updates.push({ field: f.field, value: String(now).trim() || null })
    }
  }
  if (!updates.length) {
    message.info('没有改动要提交——改过的字段会与 AI 原值并列保存')
    return
  }
  fldBusy.value = true
  fldErr.value = ''
  try {
    const r = await api.attFieldsUpdate(fldAtt.value.id, updates)
    message.success(`已订正 ${r.updated} 个字段并留痕（AI 原值不覆盖）`)
    fldOpen.value = false
    await reload()
  } catch (e) {
    fldErr.value = e instanceof Error ? e.message : String(e)
  } finally {
    fldBusy.value = false
  }
}

const CONCL: Record<string, { text: string; color: string }> = {
  pass: { text: '通过', color: 'success' },
  review: { text: '待复核', color: 'warning' },
  reject: { text: '不通过', color: 'error' },
}

const fmtAmt = (v: number | null | undefined) =>
  v != null ? Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '—'
const dt = (s: string | null | undefined) => (s || '').slice(0, 19).replace('T', ' ')

/** 行键（费用明细 / 审批记录无稳定业务键）：按记录对象发号。
 *  AntD4 弃用 rowKey(record, index) 双参（控制台警告），这里用单参 + WeakMap。 */
const rowSeq = new WeakMap<object, string>()
let rowSeqN = 0
const rowKeyOf = (r: unknown): string => {
  if (r && typeof r === 'object') {
    let k = rowSeq.get(r)
    if (!k) { k = 'r' + ++rowSeqN; rowSeq.set(r as object, k) }
    return k
  }
  return String(r)
}

const detailCols = [
  { title: '费用项目', dataIndex: 'expense_item', width: 260 },
  { title: '金额（元）', dataIndex: 'amount', width: 160, align: 'right' as const },
  { title: '分摊部门', dataIndex: 'alloc_dept', width: 200 },
]
const attCols = [
  { title: '序', dataIndex: 'seq', width: 44, align: 'center' as const },
  { title: '预览', key: 'thumb', width: 112, align: 'center' as const },
  { title: '文件名', dataIndex: 'file_name', ellipsis: true },
  { title: '附件类型', key: 'type', width: 170 },
  { title: '大小', key: 'size', width: 90, align: 'right' as const },
  { title: '来源', dataIndex: 'source_kind', width: 90 },
  { title: '操作', key: 'op', width: 250 },
]
const apprCols = [
  { title: '序', dataIndex: 'seq', width: 50, align: 'center' as const },
  { title: '审批环节', dataIndex: 'node', width: 180 },
  { title: '处理人', dataIndex: 'approver', width: 150 },
  { title: '意见', dataIndex: 'opinion' },
  { title: '时间', key: 'time', width: 170 },
]
const histCols = [
  { title: '审核号', dataIndex: 'run_id', width: 220 },
  { title: '结论', dataIndex: 'conclusion', width: 100 },
  { title: '命中', dataIndex: 'hit_count', width: 70, align: 'center' as const },
  { title: '待人工', dataIndex: 'pending_count', width: 80, align: 'center' as const },
  { title: '执行 / 候选', key: 'rules', width: 110, align: 'center' as const },
  { title: '规则集', dataIndex: 'rule_set_version', width: 120 },
  { title: '时间', key: 'time', width: 170 },
  { title: '与上一轮', key: 'diff', width: 110, align: 'center' as const },
]

/** 历次审核前后对照（P1-10）：items 按时间倒序，第 i 行与第 i+1 行（更早一轮）
 *  比对结论/命中/待人工的变化。纯展示层（billReviews 已带各轮计数），
 *  变了才显示——首轮显示「—」。 */
function histDiff(items: Record<string, any>[], i: number): string {
  const cur = items[i]
  const prev = items[i + 1]
  if (!prev) return ''
  const parts: string[] = []
  if (cur.conclusion !== prev.conclusion)
    parts.push(`结论 ${prev.conclusion === 'pass' ? '通过' : prev.conclusion === 'review' ? '待复核' : '不通过'}→${cur.conclusion === 'pass' ? '通过' : cur.conclusion === 'review' ? '待复核' : '不通过'}`)
  const dh = (cur.hit_count ?? 0) - (prev.hit_count ?? 0)
  const dp = (cur.pending_count ?? 0) - (prev.pending_count ?? 0)
  if (dh) parts.push(`命中${dh > 0 ? '+' : ''}${dh}`)
  if (dp) parts.push(`待人工${dp > 0 ? '+' : ''}${dp}`)
  return parts.join(' · ')
}
</script>

<template>
  <div class="bd">
  <Async :load="() => api.bill(no)" :deps="no">
    <template #default="{ data, reload }">
      <div class="bd-head">
        <h2 class="bd-title">
          {{ (data as any).bill_no }}
          <a-tag v-if="(data as any).is_demo" color="default">演示数据</a-tag>
        </h2>
        <div>
          <router-link to="/review/bills">← 回工作台</router-link>
          <span class="sep">·</span>
          <router-link :to="`/review/trace/${(data as any).bill_no}`">审计追溯</router-link>
        </div>
      </div>

      <!-- AI审核结论 Banner -->
      <template v-if="(data as any).latest_review">
        <a-alert class="banner" :type="(data as any).latest_review.conclusion === 'pass' ? 'success'
          : (data as any).latest_review.conclusion === 'review' ? 'warning' : 'error'" show-icon>
          <template #message>
            <span class="banner-line">
              <b>AI 审核：{{
                (data as any).latest_review.conclusion === 'reject' ? '不通过'
                : (data as any).latest_review.conclusion === 'review' ? '待复核' : '通过'
              }}</b>
              <span class="dim">执行 {{ (data as any).latest_review.rule_executed ?? '—' }} 条规则 ·
                命中 {{ (data as any).latest_review.hit_count ?? 0 }} 条 ·
                待人工 {{ (data as any).latest_review.pending_count ?? 0 }} 条</span>
              <router-link v-if="(data as any).latest_review.run_id"
                :to="`/review/report/${(data as any).latest_review.run_id}`"
                style="margin-left: auto">查看完整报告 →</router-link>
            </span>
          </template>
        </a-alert>

        <!-- 违规制度表 -->
        <a-card v-if="(data as any).latest_review.hits?.length" size="small" class="blk">
          <template #title>
            <span class="dot hit"></span>违规制度（{{ (data as any).latest_review.hits.length }} 条命中）
          </template>
          <a-table :data-source="(data as any).latest_review.hits" row-key="rule_id"
            size="small" :pagination="false"
            :columns="[
              { title: '规则', dataIndex: 'rule_id', width: 120 },
              { title: '制度名称', dataIndex: 'rule_name', width: 220, ellipsis: true },
              { title: '控制', dataIndex: 'control_type', width: 80, align: 'center' },
              { title: '违规说明', dataIndex: 'message' },
            ]">
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'rule_id'">
                <router-link :to="`/asset/rules/${record.rule_id}`">{{ record.rule_id }}</router-link>
              </template>
              <template v-else-if="column.dataIndex === 'control_type'">
                <a-tag :color="record.control_type === '禁止' ? 'error' : 'warning'">{{ record.control_type }}</a-tag>
              </template>
              <template v-else-if="column.dataIndex === 'message'">
                <span class="dim">{{ record.message }}</span>
              </template>
            </template>
          </a-table>
        </a-card>

        <!-- 待人工项 -->
        <a-card v-if="(data as any).latest_review.pending?.length" size="small" class="blk">
          <template #title><span class="dot pend"></span>待人工判断（{{ (data as any).latest_review.pending.length }} 条）</template>
          <a-table :data-source="(data as any).latest_review.pending" row-key="rule_id"
            size="small" :pagination="false"
            :columns="[
              { title: '规则', dataIndex: 'rule_id', width: 120 },
              { title: '制度名称', dataIndex: 'rule_name', width: 220, ellipsis: true },
              { title: '待判断原因', key: 'reason' },
            ]">
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'rule_id'">
                <router-link :to="`/asset/rules/${record.rule_id}`">{{ record.rule_id }}</router-link>
              </template>
              <template v-else-if="column.key === 'reason'">
                <span class="dim">{{ record.pending_reason || record.message }}</span>
              </template>
            </template>
          </a-table>
          <div class="note">「待人工」不等于「通过」，需审核岗人工判断后在报告页提交复核意见。</div>
        </a-card>
      </template>

      <a-card size="small" class="blk" title="单据">
        <div class="kvg">
          <div><span class="k">单据类型</span><span class="v">{{ (data as any).doc_type }}</span></div>
          <div><span class="k">单据日期</span><span class="v">{{ dt((data as any).doc_date) }}</span></div>
          <div><span class="k">业务类型</span><span class="v">{{ (data as any).biz_type }}</span></div>
          <div><span class="k">核算主体</span><span class="v">{{ (data as any).org_name || '—' }}</span></div>
          <div><span class="k">报账部门</span><span class="v">{{ (data as any).claim_dept || '—' }}</span></div>
          <div><span class="k">申请人职级</span><span class="v">{{ (data as any).applicant_rank || '—' }}</span></div>
          <div><span class="k">报账金额（元）</span><span class="v">{{ fmtAmt((data as any).amount) }}</span></div>
          <div><span class="k">摘要</span><span class="v">{{ (data as any).summary }}</span></div>
          <div><span class="k">补充说明</span>
            <span class="v"><template v-if="(data as any).remark">{{ (data as any).remark }}</template>
              <a-tag v-else color="default">空</a-tag></span>
          </div>
          <div><span class="k">收款方</span><span class="v">{{ (data as any).payee || '—' }}</span></div>
          <div><span class="k">收款账号</span><span class="v">{{ (data as any).payee_account || '—' }}</span></div>
        </div>
      </a-card>

      <a-card size="small" class="blk" :title="`费用明细（${(data as any).details.length} 行）`">
        <a-table :columns="detailCols" :data-source="(data as any).details"
          :row-key="rowKeyOf" size="small"
          :pagination="(data as any).details.length > 10 ? { pageSize: 10 } : false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'amount'">{{ fmtAmt(record.amount) }}</template>
            <template v-else-if="column.dataIndex === 'alloc_dept'">
              <span class="dim">{{ record.alloc_dept || '—' }}</span>
            </template>
          </template>
        </a-table>
      </a-card>

      <a-card v-if="(data as any).travelers?.length" size="small" class="blk"
        :title="`出行人员（${(data as any).travelers.length} 人）`">
        <a-table :data-source="(data as any).travelers" row-key="emp_no" size="small" :pagination="false"
          :columns="[
            { title: '工号', dataIndex: 'emp_no', width: 160 },
            { title: '姓名', dataIndex: 'name', width: 160 },
          ]" />
      </a-card>

      <a-card size="small" class="blk" :title="`附件（${(data as any).attachments.length} 个）`">
        <!-- 两个指标并列。**不要只显示一个**：哪个都不是「附件数」的唯一答案 -->
        <a-row :gutter="16" class="att-stats">
          <a-col :span="8"><a-statistic title="申报张数（审批意见 PDF）" :value="(data as any).attach_pages ?? '—'" /></a-col>
          <a-col :span="8"><a-statistic title="真实原图（xlsx 内嵌）" :value="(data as any).attach_count ?? 0" /></a-col>
          <a-col v-if="(data as any).attach_demo_count" :span="8">
            <a-statistic title="演示合成（示意图）" :value="(data as any).attach_demo_count" />
          </a-col>
        </a-row>
        <div v-if="(data as any)._attach_metrics_note" class="note">
          <MarkText :text="(data as any)._attach_metrics_note" />
        </div>

        <a-alert v-if="(data as any)._attachment_note"
          :type="(data as any).attachments.length ? 'info' : 'warning'" show-icon style="margin: 8px 0">
          <template #message><MarkText :text="(data as any)._attachment_note" /></template>
        </a-alert>

        <!-- 读图判类型（AI 批量）+ 人工订正口径条（B-P0-2） -->
        <div v-if="untypedOf((data as any).attachments as Attachment[]).length"
             class="cls-bar">
          <span class="dim">
            本单还有 {{ untypedOf((data as any).attachments as Attachment[]).length }} 张未判类型
          </span>
          <a-button :loading="classifyBusy && armed === null"
                    @click="classifyBatch((data as any).attachments as Attachment[], reload)">
            {{ armed === 'batch'
              ? `确认：对这 ${untypedOf((data as any).attachments as Attachment[]).length} 张调模型（出网计费）`
              : '批量判类型' }}
          </a-button>
        </div>
        <div v-if="canJudge" class="note">
          类型与识别字段支持人工订正（留痕）：表格行内「订正类型」，操作列「订正字段」。
          订正后 AI 原值仍并列保留，不覆盖历史；订正不自动重跑审核。
        </div>
        <a-alert v-if="classifyErr" type="error" show-icon :message="classifyErr" style="margin: 8px 0" />
        <div v-if="classifyMsg" class="note">{{ classifyMsg }}</div>

        <a-table v-if="(data as any).attachments.length"
          :columns="attCols"
          :data-source="(data as any).attachments as Attachment[]"
          row-key="id" size="small" :pagination="false">
          <template #bodyCell="{ column, record: a, index }">
            <template v-if="column.dataIndex === 'seq'">{{ a.seq ?? index + 1 }}</template>
            <template v-else-if="column.key === 'thumb'">
              <img v-if="a.id && a.has_file" class="thumb"
                   :src="fileUrl(a.id)" :alt="a.file_name"
                   title="点开放大（缩放/旋转/翻张）"
                   @click="openLarge(a, (data as any).attachments as Attachment[])" />
              <span v-else class="dim">无原图</span>
            </template>
            <template v-else-if="column.dataIndex === 'file_name'">
              <a v-if="a.id && a.has_file" @click.prevent="openLarge(a, (data as any).attachments as Attachment[])" style="cursor: pointer"
                 title="点开放大">{{ a.file_name }}</a>
              <span v-else>{{ a.file_name }}</span>
            </template>
            <template v-else-if="column.key === 'type'">
              <!-- 空 = 还没判，**不是「没有类型」**；人工订正的值并列显示，
                   AI 原值不覆盖（B-P0-2） -->
              <template v-if="judgingId === a.id && canJudge">
                <a-select v-model:value="judgePick" size="small" style="width: 150px"
                  :options="typeMenu.map((t) => ({ value: t.code, label: `${t.code} ${t.label}` }))"
                  placeholder="选择类型" />
                <a-button size="small" type="primary" style="margin-left: 4px"
                  :loading="judgeBusy" :disabled="!judgePick"
                  @click="submitJudge(a, reload)">确认</a-button>
                <a-button size="small" style="margin-left: 2px"
                  @click="judgingId = null">取消</a-button>
                <div v-if="judgeErr" class="err-tip">{{ judgeErr }}</div>
              </template>
              <template v-else>
                <a-tag v-if="a.attach_type" :color="a.type_source === '人工指定' ? 'processing' : 'success'">
                  {{ a.attach_type }}
                </a-tag>
                <a-tag v-if="a.type_source === '人工指定'" color="blue">人工订正</a-tag>
                <a-tag v-if="!a.attach_type" color="warning" title="需要读图才能判，见上方说明">未判定</a-tag>
                <a v-if="canJudge && a.source_kind === 'xlsx内嵌' && judgingId !== a.id"
                   class="judge-link" title="人工订正附件类型（留痕，AI 原值保留）"
                   @click="startJudge(a)">订正</a>
                <div v-if="a.type_source && a.type_source !== '人工指定'" class="dim" style="font-size: 11px">
                  {{ a.type_source }}{{ a.type_conf ? ` · 把握${a.type_conf}` : '' }}
                </div>
                <div v-if="a.type_source === '人工指定'" class="dim" style="font-size: 11px">
                  <a-tooltip :overlay-style="{ maxWidth: 380 }">
                    <template #title>
                      <span style="font-size: 12px; line-height: 1.7">
                        {{ a.judged_label || '—' }} 于 {{ dt(a.judged_at) || '—' }} 订正；
                        原判定：{{ a.judged_old || '未判定' }}。台账逐次留痕，订正不自动重跑审核。
                      </span>
                    </template>
                    {{ a.judged_label }} 订正 · 原判{{ a.judged_old || '未判定' }}
                  </a-tooltip>
                </div>
              </template>
            </template>
            <template v-else-if="column.key === 'size'">{{ kb(a.byte_size) }}</template>
            <template v-else-if="column.dataIndex === 'source_kind'">
              <a-tag :color="a.source_kind === 'xlsx内嵌' ? 'blue' : 'default'">{{ a.source_kind }}</a-tag>
            </template>
            <template v-else-if="column.key === 'op'">
              <template v-if="a.source_kind === 'xlsx内嵌' && !a.attach_type">
                <!-- 未判类型：AI 单条读图（计费）与人工定类（B-P0-2 ③）并排 -->
                <a-button size="small" :loading="classifyBusy"
                          :title="'真出网真计费：读一张图判类型'"
                          @click="classifyOne(a.id, reload)">
                  {{ armed === a.id ? '确认调模型（计费）' : '判类型' }}
                </a-button>
                <a-button v-if="canJudge && judgingId !== a.id" size="small" ghost type="primary"
                          style="margin-left: 4px"
                          title="人工直接定类型（不出网不计费，留痕）"
                          @click="startJudge(a)">人工定类</a-button>
              </template>
              <a-button v-else-if="canJudge && a.source_kind === 'xlsx内嵌' && a.type_code"
                        size="small" style="margin-right: 4px"
                        title="订正/补录识别字段：AI 值与人工值并列保存"
                        @click="openFields(a, reload)">订正字段</a-button>
              <a v-if="canJudge && a.source_kind === 'xlsx内嵌' && a.type_code && judgingId !== a.id"
                 class="judge-link" @click="startJudge(a)">订正类型</a>
              <span v-if="!canJudge || a.source_kind !== 'xlsx内嵌'" class="dim">—</span>
            </template>
          </template>
          <!-- 展开行：读图依据 + 大图（A-P1-2：点缩略图/文件名进大图层，此处保留直开） -->
          <template #expandedRowRender="{ record: a }">
            <div v-if="lastResult[a.id]" class="dim" style="font-size: 12px; margin-bottom: 8px">
              模型 {{ lastResult[a.id].model }} ·
              {{ lastResult[a.id].ok
                ? (lastResult[a.id].type_code
                    ? `判为 ${lastResult[a.id].type_label}（把握 ${lastResult[a.id].confidence}）`
                    : '判不出') + ' · 依据：' + (lastResult[a.id].reason || '—')
                : `失败：${lastResult[a.id].message}` }}
              <span v-if="lastResult[a.id].note && lastResult[a.id].note !== '无'">
                · 备注：{{ lastResult[a.id].note }}
              </span>
            </div>
            <img v-if="a.id && a.has_file" :src="fileUrl(a.id)" :alt="a.file_name" class="full"
                 style="cursor: zoom-in" title="点开放大" @click="openLarge(a, (data as any).attachments as Attachment[])" />
            <div class="dim" style="font-size: 12px; margin-top: 6px">
              {{ a.file_name }}（{{ kb(a.byte_size) }}）·
              <a :href="fileUrl(a.id)" target="_blank" rel="noopener">在新标签页打开</a>
            </div>
          </template>
          <template #expandColumnTitle>
            <span class="dim" style="font-size: 12px">详情</span>
          </template>
        </a-table>
      </a-card>

      <!-- B-P0-2 ② 订正字段抽屉：AI 值灰字 + 人工值正常色并列 -->
      <a-drawer v-model:open="fldOpen" width="560" title="订正识别字段"
        :body-style="{ padding: '16px 20px' }">
        <template v-if="fldAtt">
          <div class="dim" style="margin-bottom: 10px">
            {{ fldAtt.file_name }} ·
            <a-tag v-if="fldAtt.attach_type" color="success">{{ fldAtt.attach_type }}</a-tag>
            <a-tag v-else color="warning">未判定</a-tag>
          </div>
          <a-alert v-if="fldErr" type="error" show-icon :message="fldErr" style="margin-bottom: 10px" />
          <a-skeleton v-if="!fldData && !fldErr" active :paragraph="{ rows: 5 }" />
          <template v-if="fldData">
            <div v-if="!fldData.fields.length" class="note">
              该附件类型暂无可抽取字段（{{ fldData.type_code || '未判定' }}）。
              先订正类型为可抽取类（A01/A03/A04/A05）再补录字段。
            </div>
            <div class="dim" style="margin-bottom: 10px">
              <template v-if="fldData.ai_call">
                AI 抽取：{{ fldData.ai_call.model }} · 把握{{ fldData.ai_call.confidence || '—' }}
                · {{ fldData.ai_call.at || '—' }}。
              </template>
              <template v-else>这张附件还没跑过 AI 字段抽取（或抽取失败）——以下为人工补录。</template>
              人工值与 AI 值并列保存，AI 台账不动。
            </div>
            <div v-for="f in fldData.fields" :key="f.field" class="fld-row">
              <div class="fld-key">
                <span v-if="f.judged" class="dot-manual" title="该字段有人工订正值"></span>
                <b>{{ f.label }}</b>
                <span class="dim fld-code">{{ f.field }}</span>
              </div>
              <a-input v-model:value="fldEdits[f.field]" size="small"
                :placeholder="f.ai_value != null ? `留空=还原 AI 值` : '补录'"
                allow-clear />
              <div v-if="f.ai_value != null || f.judged" class="fld-vals">
                <span v-if="f.ai_value != null" class="dim">AI 原值：{{ f.ai_value }}</span>
                <span v-if="f.judged" class="manual-mark">人工值生效</span>
              </div>
            </div>
            <!-- 订正历史（留痕可见：谁/何时/哪个字段/前后值） -->
            <div v-if="fldData.history.length" class="fld-hist">
              <div class="dim" style="margin-bottom: 4px">订正历史（只增不改）</div>
              <div v-for="(h, i) in fldData.history.slice(0, 6)" :key="i" class="hist-row">
                <span class="dim">{{ h.at }}</span>
                <span>{{ h.label }}</span>
                <span class="dim">{{ h.old_value ?? '（空）' }} → {{ h.new_value ?? '（还原AI值）' }}</span>
                <span>{{ h.judged_label }}</span>
              </div>
            </div>
          </template>
        </template>
        <template #footer>
          <div style="display: flex; gap: 8px; justify-content: flex-end">
            <a-button :disabled="fldBusy" @click="fldOpen = false">取消</a-button>
            <a-button type="primary" :loading="fldBusy" :disabled="!fldData"
              @click="submitFields(reload)">提交订正</a-button>
          </div>
        </template>
      </a-drawer>

      <a-card size="small" class="blk" :title="`审批记录（${(data as any).approvals.length} 条）`">
        <a-table :columns="apprCols" :data-source="(data as any).approvals"
          :row-key="rowKeyOf" size="small"
          :pagination="(data as any).approvals.length > 10 ? { pageSize: 10 } : false">
          <template #bodyCell="{ column, record: a }">
            <template v-if="column.dataIndex === 'node'">
              {{ a.node }}
              <a-tag v-if="a.node === 'AI审核'" color="blue" style="margin-left: 4px">AI</a-tag>
            </template>
            <template v-else-if="column.dataIndex === 'opinion'">{{ a.opinion || '—' }}</template>
            <template v-else-if="column.key === 'time'">
              <span class="dim">{{ dt(a.approved_at) }}</span>
            </template>
          </template>
        </a-table>
      </a-card>

      <Async :load="() => api.billReviews(no)" :deps="no">
        <template #default="{ data: rv }">
          <a-card size="small" class="blk" title="历次审核">
            <a-table :columns="histCols" :data-source="(rv as any).items"
              :row-key="(r: unknown) => String((r as Record<string, any>).run_id)"
              size="small"
              :pagination="(rv as any).items.length > 10 ? { pageSize: 10 } : false">
              <template #bodyCell="{ column, record: r, index }">
                <template v-if="column.dataIndex === 'run_id'">
                  <router-link :to="`/review/report/${r.run_id}`">{{ r.run_id }}</router-link>
                </template>
                <template v-else-if="column.dataIndex === 'conclusion'">
                  <a-tag :color="CONCL[r.conclusion]?.color || 'default'"
                    >{{ CONCL[r.conclusion]?.text || r.conclusion }}</a-tag>
                </template>
                <template v-else-if="column.key === 'rules'">{{ r.rule_executed }} / {{ r.rule_candidates }}</template>
                <template v-else-if="column.key === 'diff'">
                  <span v-if="histDiff((rv as any).items, index)" class="diff-ch">{{ histDiff((rv as any).items, index) }}</span>
                  <span v-else class="dim">—</span>
                </template>
                <template v-else-if="column.key === 'time'">
                  <span class="dim">{{ dt(r.finished_at) }}</span>
                </template>
              </template>
              <template #emptyText>
                <a-empty :image-style="{ height: '40px' }" description="暂无审核记录" />
              </template>
            </a-table>
          </a-card>
        </template>
      </Async>

      <!-- A-P1-2 大图层（共享组件）：items 在挂载时按本单附件装配 -->
      <ImageViewer v-if="lbOpen && lbItems.length" :items="lbItems"
        :start-id="lbStart" @close="lbOpen = false" />
    </template>
  </Async>
  </div>
</template>


<style scoped>
.bd-head { display: flex; align-items: baseline; gap: 16px; margin-bottom: 12px; }
.bd-title { font-size: 18px; font-weight: 600; margin: 0; }
.sep { margin: 0 6px; color: rgba(0, 0, 0, .25); }
.banner { margin-bottom: 12px; }
.banner-line { display: flex; align-items: center; gap: 12px; }
.dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 8px; vertical-align: 2px; }
.dot.hit { background: #ff4d4f; }
.dot.pend { background: #98a2b3; }
.blk { margin-bottom: 16px; }
.dim { color: rgba(0, 0, 0, .45); font-size: 12px; }
.note { font-size: 12px; color: rgba(0, 0, 0, .55); margin: 8px 0; }
/* 历次审核「与上一轮」变化列（P1-10） */
.diff-ch { font-size: 12px; color: #d46b08; font-variant-numeric: tabular-nums; }

.kvg { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 0 32px; }
.kvg .k { display: inline-block; width: 110px; color: rgba(0, 0, 0, .45); font-size: 13px; padding: 6px 0; }
.kvg .v { font-size: 13px; }

.att-stats { margin-bottom: 4px; }
.att-stats :deep(.ant-statistic-title) { font-size: 13px; }
.att-stats :deep(.ant-statistic-content) { font-size: 22px; }
.cls-bar { display: flex; align-items: center; gap: 12px; margin: 8px 0; }

/* 人工订正入口（B-P0-2）：文字链样式，与按钮型动作区分 */
.judge-link { font-size: 12px; margin-left: 6px; cursor: pointer; color: #1677ff; }
.judge-link:hover { text-decoration: underline; }
.err-tip { font-size: 12px; color: #cf1322; margin-top: 4px; }

/* 订正字段抽屉（B-P0-2 ②）：AI 值灰字、人工值正常色并列 */
.fld-row { margin-bottom: 12px; }
.fld-key { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; font-size: 13px; }
.fld-code { font-size: 11px; background: #f5f5f5; padding: 0 4px; border-radius: 3px; }
.dot-manual { display: inline-block; width: 6px; height: 6px; border-radius: 50%;
  background: #1677ff; flex: none; }
.fld-vals { display: flex; gap: 10px; margin-top: 3px; font-size: 12px; }
.manual-mark { color: #1677ff; }
.fld-hist { margin-top: 14px; border-top: 1px dashed #f0f0f0; padding-top: 8px; }
.hist-row { display: flex; gap: 10px; font-size: 12px; padding: 2px 0; flex-wrap: wrap; }

/* 附件缩略图。扫描件在 72px 上只能看出「是张票还是张表」，
   看票面必须点开——所以缩略图给个明显的可点样式。 */
.thumb {
  width: 88px; height: 88px; object-fit: cover; cursor: zoom-in;
  border: 1px solid #e5e7eb; border-radius: 6px; background: #fafafa;
}
.thumb:hover { border-color: #4096ff; }
/* 大图不设高度上限：出差申请单那种是整表一张长图，截断了等于没给。
   宽度跟随容器，窄屏上也能看全。 */
.full { max-width: 100%; border: 1px solid #e5e7eb; border-radius: 6px; }
</style>
