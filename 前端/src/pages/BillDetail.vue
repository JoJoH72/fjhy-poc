<script setup lang="ts">
/**
 * 单据与附件（2026-10-27 AntD 换装版）。
 *
 * 2026-10-21 附件这一块改了：原来它只显示「附件类型 / 文件名 / 页数」三列，
 * 而 40 张真实单一条附件记录都没有，于是整块永远只显示一条红色的
 * 「附件清单 100% 为空（G-29）」。
 *
 * 实际上附件一直在交付物里——**内嵌在 xlsx 里的 PNG**（219 张，覆盖全部 40 张单），
 * 只是我们的 xlsx 轻解析只读 XML 文本，看不见 xl/media/ 下的图片。
 * 现在它们抽出来了，所以这一块要能**看到原图**：审核岗复核「这张发票对不对」
 * 靠的是看票面，不是看文件名。
 *
 * 两条口径必须写在页面上：
 *
 * 1. **附件类型是空的，而空表示「还没判」，不是「没有类型」。**
 *    从一张 PNG 判它是出差审批单还是住宿费发票必须读图（必要附件决策表 11 种
 *    类型里 5 种标着 ocr_needed）。所以 TR-D6-01（必要附件完整性）**仍然开不了**。
 *    不写清楚的话，「有附件了」会被当成「附件规则能跑了」。
 *
 * 2. **「申报张数」与「实际附件数」是两个指标，并列显示，谁也不覆盖谁。**
 *    实测 40 张真实单里 34 张两者不等（申报多 32、申报少 2）。
 *    那**不等于「申报不实」**：「张数」可能按纸质页数算、一张图可能是两页合并、
 *    申报数也可能含未扫描的原件。所以没有做成规则——在口径搞清楚之前，
 *    那会是一条 32/40 命中的误报机。
 */
import { computed, createVNode, ref } from 'vue'
import { useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import { ExclamationCircleOutlined } from '@ant-design/icons-vue'
import Async from '../components/Async.vue'
import { api, type Attachment, type AttachmentClassifyItem } from '../api'

const route = useRoute()
const no = computed(() => String(route.params.no))

/** 展开看大图的那一张。附件是扫描件，缩略图上根本看不清票面。 */
const openAtt = ref<number | null>(null)
function toggle(id: number) {
  openAtt.value = openAtt.value === id ? null : id
}
const fileUrl = (id: number) => `/api/attachments/${id}/file`

function kb(n: number | null | undefined) {
  if (n == null) return '—'
  return n < 1024 ? `${n} B`
    : n < 1048576 ? `${(n / 1024).toFixed(1)} KB`
      : `${(n / 1048576).toFixed(1)} MB`
}

// ── 读图判类型 ────────────────────────────────────────────────────────────
// **真出网、真计费**，发出去的是真实发票与出差申请单。所以这里走
// 两段确认——第一下只是「上膛」，把要调几次模型摆出来；第二下才真跑。
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
  { title: '附件类型', key: 'type', width: 150 },
  { title: '大小', key: 'size', width: 90, align: 'right' as const },
  { title: '来源', dataIndex: 'source_kind', width: 90 },
  { title: '操作', key: 'op', width: 170 },
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
]
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
          <!-- 演示合成单独一格，只在有的时候出现。
               原来只摆一个「实际附件数」，演示单于是头部写「附件（2 个）」、
               指标卡写「0」，同一个页面两个数对不上。 -->
          <a-col v-if="(data as any).attach_demo_count" :span="8">
            <a-statistic title="演示合成（示意图）" :value="(data as any).attach_demo_count" />
          </a-col>
        </a-row>
        <div v-if="(data as any)._attach_metrics_note" class="note">
          <MarkText :text="(data as any)._attach_metrics_note" />
        </div>

        <!-- 没有附件时这条是红的（要去查）；有附件时它说的是「类型还没判」，
             那是说明不是故障，用 note 不用 err。 -->
        <a-alert v-if="(data as any)._attachment_note"
          :type="(data as any).attachments.length ? 'info' : 'warning'" show-icon style="margin: 8px 0">
          <template #message><MarkText :text="(data as any)._attachment_note" /></template>
        </a-alert>

        <!-- 读图判类型。**真出网、真计费**——两段确认，第一下只是把要调几次
             模型摆出来。批量按钮只对**这一单**的未判附件生效，不是全库 219 张。 -->
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
                   @click="toggle(a.id)" />
              <span v-else class="dim">无原图</span>
            </template>
            <template v-else-if="column.dataIndex === 'file_name'">
              <a v-if="a.id && a.has_file" @click.prevent="toggle(a.id)" style="cursor: pointer">{{ a.file_name }}</a>
              <span v-else>{{ a.file_name }}</span>
            </template>
            <template v-else-if="column.key === 'type'">
              <!-- 空 = 还没判，**不是「没有类型」**。显示「未判定」而不是「—」：
                   「—」会被读成「这份附件没有类型」。 -->
              <a-tag v-if="a.attach_type" color="success">{{ a.attach_type }}</a-tag>
              <a-tag v-else color="warning" title="需要读图才能判，见上方说明">未判定</a-tag>
              <div v-if="a.type_source" class="dim" style="font-size: 11px">
                {{ a.type_source }}{{ a.type_conf ? ` · 把握${a.type_conf}` : '' }}
              </div>
            </template>
            <template v-else-if="column.key === 'size'">{{ kb(a.byte_size) }}</template>
            <template v-else-if="column.dataIndex === 'source_kind'">
              <a-tag :color="a.source_kind === 'xlsx内嵌' ? 'blue' : 'default'">{{ a.source_kind }}</a-tag>
            </template>
            <template v-else-if="column.key === 'op'">
              <a-button v-if="a.source_kind === 'xlsx内嵌' && !a.attach_type"
                        size="small" :loading="classifyBusy"
                        @click="classifyOne(a.id, reload)">
                {{ armed === a.id ? '确认调模型（计费）' : '判类型' }}
              </a-button>
              <span v-else class="dim">—</span>
            </template>
          </template>
          <!-- 展开行：读图依据 + 大图。附件是扫描件，缩略图上看不清票面 -->
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
            <img v-if="a.id && a.has_file" :src="fileUrl(a.id)" :alt="a.file_name" class="full" />
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
              row-key="run_id" size="small"
              :pagination="(rv as any).items.length > 10 ? { pageSize: 10 } : false">
              <template #bodyCell="{ column, record: r }">
                <template v-if="column.dataIndex === 'run_id'">
                  <router-link :to="`/review/report/${r.run_id}`">{{ r.run_id }}</router-link>
                </template>
                <template v-else-if="column.dataIndex === 'conclusion'">
                  <a-tag :color="CONCL[r.conclusion]?.color || 'default'"
                    >{{ CONCL[r.conclusion]?.text || r.conclusion }}</a-tag>
                </template>
                <template v-else-if="column.key === 'rules'">{{ r.rule_executed }} / {{ r.rule_candidates }}</template>
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

.kvg { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 0 32px; }
.kvg .k { display: inline-block; width: 110px; color: rgba(0, 0, 0, .45); font-size: 13px; padding: 6px 0; }
.kvg .v { font-size: 13px; }

.att-stats { margin-bottom: 4px; }
.att-stats :deep(.ant-statistic-title) { font-size: 13px; }
.att-stats :deep(.ant-statistic-content) { font-size: 22px; }
.cls-bar { display: flex; align-items: center; gap: 12px; margin: 8px 0; }

/* 附件缩略图。扫描件在 72px 上只能看出「是张票还是张表」，
   看票面必须点开——所以缩略图给个明显的可点样式。 */
.thumb {
  width: 88px; height: 88px; object-fit: cover; cursor: pointer;
  border: 1px solid #e5e7eb; border-radius: 6px; background: #fafafa;
}
.thumb:hover { border-color: #4096ff; }
/* 大图不设高度上限：出差申请单那种是整表一张长图，截断了等于没给。
   宽度跟随容器，窄屏上也能看全。 */
.full { max-width: 100%; border: 1px solid #e5e7eb; border-radius: 6px; }
</style>
