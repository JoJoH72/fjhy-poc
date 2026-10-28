<script setup lang="ts">
/**
 * 单条规则详情（2026-10-27 AntD 换装版）：八要素 + 概念绑定 + 可得性 + 最近命中。
 *
 * **概念的可得性要和规则并排显示。** 一条规则「为什么跑不了」的答案
 * 几乎总是「它引用的某个字段取不到」，而那个字段叫什么、属于哪个缺口，
 * 是让业务侧去补数据的唯一依据。
 *
 * 2026-10-27 P0-1：conflicts_with 关系行可点开「冲突对比与裁决」抽屉——
 * 客户当场投诉「冲突标注点不开，看不到两条规则的对比原文」。
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import Async from '../components/Async.vue'
import Outcome from '../components/Outcome.vue'
import ConflictCompareDrawer from '../components/ConflictCompareDrawer.vue'
import { api } from '../api'
import type { RuleVersions } from '../api'
import * as session from '../data/session'

const route = useRoute()
const router = useRouter()
const id = computed(() => String(route.params.id))

// ── 版本历史 + 修订（B-P1-E，2026-10-28）────────────────────────────────────
// 版本历史卡：全版本行（谁/何时/为什么改了什么）+ 各版随哪个 release 发布。
// 修订表单：修订原因必填 + 旧值新值对照（旧值只读灰字，只提交真改了的字段）
// + compare 声明编辑区（A-P1-1 契约维护面：logic.compare 四字段）。
// 异议收件箱「确认为规则问题」经 ?revise=1&reason=… 跳进来（理由预填）。
const versions = ref<RuleVersions | null>(null)
const verErr = ref('')
async function loadVersions() {
  verErr.value = ''
  try {
    versions.value = await api.ruleVersions(id.value)
  } catch (e) {
    verErr.value = e instanceof Error ? e.message : String(e)
  }
}
watch(id, loadVersions, { immediate: true })

/** 修订权限：engineer + admin（后端 403 双闸，前端收敛入口） */
const canRevise = computed(() => {
  const r = session.current()?.role
  return r === 'engineer' || r === 'admin'
})

/** 修订抽屉 */
const rvOpen = ref(false)
const rvBusy = ref(false)
const rvErr = ref('')
const rvReason = ref('')
/** 参与修订的八要素字段（string 形态，提交时 diff） */
const rvForm = ref<{ name: string; exception: string; owner: string; priority: string
                     controlType: string; scopeJson: string; logicJson: string } | null>(null)
/** 旧值快照（对照展示，改过的字段亮出旧→新） */
const rvOld = ref<{ name: string; exception: string; owner: string; priority: string
                    controlType: string; scopeJson: string; logicJson: string } | null>(null)
/** compare 声明编辑（A-P1-1：label/actual_key/limit_key/unit 四字段） */
const rvCompare = ref<{ label: string; actual_key: string; limit_key: string; unit: string }[]>([])

/** 打开修订表单：预填当前 version=1 值 + ?reason= 的异议预填 */
async function openRevise(detail: Record<string, any>) {
  rvOpen.value = true
  rvErr.value = ''
  const qReason = String(route.query.reason || '')
  rvReason.value = qReason || ''
  const logic = detail.logic || {}
  const cmp = Array.isArray(logic.compare) ? logic.compare : []
  rvForm.value = {
    name: String(detail.rule_name || ''),
    exception: String(detail.exception || ''),
    owner: String(detail.owner || ''),
    priority: String(detail.priority ?? ''),
    controlType: String(detail.control_type || ''),
    scopeJson: JSON.stringify(detail.scope || {}, null, 1),
    logicJson: (() => {
      const copy = { ...logic }
      delete copy.compare
      return JSON.stringify(copy, null, 1)
    })(),
  }
  rvOld.value = { ...rvForm.value }
  rvCompare.value = cmp.map((c: any) => ({
    label: String(c.label || ''), actual_key: String(c.actual_key || ''),
    limit_key: String(c.limit_key || ''), unit: String(c.unit || ''),
  }))
}

/** 提交修订：字段 diff（只传真改了的）+ compare 全量（声明是编辑面整体语义） */
async function submitRevise() {
  const f = rvForm.value
  if (!f) return
  if (!rvReason.value.trim()) {
    rvErr.value = '修订必须写理由——三个月后看这条规则的人只看得到这段话'
    return
  }
  const fields: Record<string, unknown> = {}
  if (f.name.trim() !== rvOld.value!.name) fields.name = f.name.trim()
  if (f.exception !== rvOld.value!.exception) fields.exception = f.exception || undefined
  if (f.owner !== rvOld.value!.owner) fields.owner = f.owner || undefined
  if (f.priority !== rvOld.value!.priority) fields.priority = f.priority
  if (f.controlType !== rvOld.value!.controlType) fields.controlType = f.controlType
  let scopeObj: Record<string, unknown> | null = null
  let logicObj: Record<string, unknown> | null = null
  if (f.scopeJson !== rvOld.value!.scopeJson) {
    try { scopeObj = JSON.parse(f.scopeJson) } catch {
      rvErr.value = '适用范围不是合法 JSON——对象形态，如 {"biz_type":["*"]}'
      return
    }
    fields.scope = scopeObj
  }
  if (f.logicJson !== rvOld.value!.logicJson) {
    try { logicObj = JSON.parse(f.logicJson) } catch {
      rvErr.value = '判定逻辑不是合法 JSON——对象形态（不含 compare，声明走下方编辑区）'
      return
    }
    fields.logic = logicObj
  }
  const hasCompare = rvCompare.value.length > 0
  if (!Object.keys(fields).length && !hasCompare) {
    rvErr.value = '没有要修订的字段——改过哪个要素，哪个要素就会进新版本'
    return
  }
  rvBusy.value = true
  rvErr.value = ''
  try {
    const r = await api.ruleRevise(id.value, {
      reason: rvReason.value.trim(),
      fields,
      ...(hasCompare ? { compare: rvCompare.value.filter((c) => c.label && c.actual_key && c.limit_key) } : {}),
    })
    rvOpen.value = false
    detailRef.value?.reload()
    void loadVersions()
    // 完成后引导（五环第 5 环）：修订 → 试算看影响面 → 再去发布
    Modal.confirm({
      title: `修订已落 v${r.version}（旧行保留）`,
      content: r.note,
      okText: '去试算',
      cancelText: '稍后',
      onOk: () => { router.push('/asset/releases') },
    })
  } catch (e) {
    rvErr.value = e instanceof Error ? e.message : String(e)
  } finally {
    rvBusy.value = false
  }
}

/** 修订表单的旧→新对照行（改过的才列） */
const rvDiffRows = computed(() => {
  const f = rvForm.value, o = rvOld.value
  if (!f || !o) return []
  const rows: { label: string; old: string; nw: string }[] = []
  const push = (label: string, old: string, nw: string) => {
    if (old !== nw) rows.push({ label, old: old || '（空）', nw: nw || '（空）' })
  }
  push('① 规则名称', o.name, f.name)
  push('控制类型', o.controlType, f.controlType)
  push('⑦ 例外', o.exception, f.exception)
  push('责任部门', o.owner, f.owner)
  push('⑧ 优先级', o.priority, f.priority)
  push('⑤ 适用范围', o.scopeJson, f.scopeJson)
  push('⑥ 判定逻辑', o.logicJson, f.logicJson)
  return rows
})

/** ?revise=1（异议收件箱跳转）自动开修订表单：详情数据到位后触发（Async @loaded） */
const lastDetail = ref<Record<string, any> | null>(null)
function onDetailLoaded(d: unknown) {
  lastDetail.value = d as Record<string, any>
  if (route.query.revise && !rvOpen.value) {
    void openRevise(lastDetail.value)
  }
}

/** 版本历史行的时间口径 */
const dt = (s: string | null | undefined) => (s || '').slice(0, 19).replace('T', ' ')

const AVAIL_OK = new Set(['样本可得', '样本可得(审批意见)', '已录入资产', '可推导'])

/** 维度名（P1-12 与规则库同口径）：D 编号旁补中文名，映射取 graph_rich */
const DIM_NAMES = ref<Record<string, string>>({})
api.ontoGraphRich().then((g) => {
  const m: Record<string, string> = {}
  for (const r of g.rule_nodes || []) {
    if (r.dimension && r.dimension_name && !m[r.dimension]) m[r.dimension] = r.dimension_name
  }
  DIM_NAMES.value = m
}).catch(() => { /* 取不到：只显示编号 */ })
function dimNameOf(code: unknown): string {
  const k = String(code || '')
  return DIM_NAMES.value[k] ? `· ${DIM_NAMES.value[k]}` : ''
}

/** 冲突对比抽屉（P0-1） */
const cmpOpen = ref(false)
const cmpOther = ref('')
const cmpKey = ref(0)
const openCompare = (other: string) => {
  cmpOther.value = other
  cmpKey.value += 1
  cmpOpen.value = true
}

/** 行键：数据源无稳定业务键时按记录对象发号（单参，AntD4 弃用双参 rowKey） */
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

/**
 * 维护备注的呈现层整理（P1-9）：后端 note 里有一条核对口径原文
 * 「…是两回事，不要混」——命令式句子不上界面。已知句式替换成
 * 名词化口径（「系统已有 ≠ 配置器已配」），原文整段收进 tooltip；
 * 其余 note 原样显示。
 */
function noteShown(raw: unknown): { brief: string; full: string } {
  const full = String(raw || '').trim()
  if (!full) return { brief: '', full: '' }
  if (full.includes('不要混')) {
    return { brief: '口径：系统已有 ≠ 配置器已配', full }
  }
  return { brief: full, full }
}

/** 裁决完成后重取详情：relations 的 needs_confirm 标签要当场变掉 */
const detailRef = ref<{ reload: () => void } | null>(null)
const reloadDetail = () => detailRef.value?.reload()
</script>

<template>
  <div class="rd">
  <Async ref="detailRef" :load="() => api.rule(id)" :deps="id" @loaded="onDetailLoaded">
    <template #default="{ data }">
      <div class="rd-head">
        <h2 class="rd-title">{{ (data as any).rule_id }} {{ (data as any).rule_name }}</h2>
        <div>
          <a-button v-if="canRevise" size="small" type="primary"
            @click="openRevise(data as any)">发起修订</a-button>
          <router-link to="/asset/rules-lib" style="margin-left: 12px">← 回规则库</router-link>
        </div>
      </div>

      <a-card size="small" class="blk" title="八要素">
        <div class="kv">
          <b>① 规则名称</b><span>{{ (data as any).rule_name }}</span>
          <b>② 审核维度</b><span>{{ (data as any).dimension }} {{ dimNameOf((data as any).dimension) }}</span>
          <b>③ 风险类别</b><span>{{ (data as any).risk_class }}</span>
          <b>④ 来源条款</b>
          <span>
            <template v-if="(data as any).source_clause">
              <code class="clause">{{ (data as any).source_clause.file }}</code>
              {{ (data as any).source_clause.clause }}
              <a-tag :color="(data as any).source_clause.confidence === 'OCR' ? 'warning' : 'success'"
                style="margin-left: 4px">{{ (data as any).source_clause.confidence }}</a-tag>
              <div class="dim" style="margin-top: 3px">「{{ (data as any).source_clause.text }}」</div>
            </template>
          </span>
          <b>⑤ 适用范围</b>
          <span><pre class="json">{{ JSON.stringify((data as any).scope, null, 1) }}</pre></span>
          <b>⑥ 判定逻辑</b>
          <span><pre class="json">{{ JSON.stringify((data as any).logic, null, 1) }}</pre></span>
          <b>⑦ 例外</b><span>{{ (data as any).exception || '无' }}</span>
          <b>⑧ 优先级</b><span>{{ (data as any).priority }}</span>
          <b>责任部门</b><span>{{ (data as any).owner || '—' }}</span>
          <b>生效期</b>
          <span>{{ (data as any).effective_from || '—' }} ~ {{ (data as any).effective_to || '长期' }}</span>
        </div>
      </a-card>

      <a-card size="small" class="blk" title="对齐客户配置器">
        <div class="kv">
          <b>算法编码</b><span>{{ (data as any).algo_code || '—' }}</span>
          <b>算法类型</b><span>{{ (data as any).algo_type || '—' }}</span>
          <b>控制类型</b>
          <span>
            <a-tag :color="(data as any).control_type === '禁止' ? 'error' : 'warning'">
              {{ (data as any).control_type }}
            </a-tag>
          </span>
          <b>配置器状态</b><span>{{ (data as any).configurator_status || '—' }}</span>
          <b>客户自评可实现性</b><span>{{ (data as any).feasibility || '未评估' }}</span>
          <b>实现途径（原文）</b><span>{{ (data as any).impl_path || '—' }}</span>
        </div>
      </a-card>

      <a-card size="small" class="blk" title="可执行性">
        <div class="kv">
          <b>规则资产层面</b>
          <span>
            <a-tag :color="(data as any).compiled ? 'success' : 'default'">
              {{ (data as any).compiled ? '已编译' : '未编译' }}
            </a-tag>
            <span v-if="(data as any).compiled_reason" class="dim" style="margin-left: 6px">
              <MarkText :text="(data as any).compiled_reason" />
            </span>
          </span>
          <b>当前数据能否跑</b>
          <span>
            <a-tag :color="(data as any).demo_executable ? 'success' : 'warning'">
              {{ (data as any).demo_executable ? '在执行集' : '不在执行集' }}
            </a-tag>
            <span v-if="(data as any).demo_scope" class="dim" style="margin-left: 6px">
              {{ (data as any).demo_scope }}
            </span>
          </span>
          <template v-if="(data as any).demo_blocker">
            <b>阻塞原因</b>
            <span class="blk-note">{{ (data as any).demo_blocker }}</span>
          </template>
          <template v-if="(data as any).logic?.degraded?.cannot_hit_without">
            <b>结构性无法命中</b>
            <span class="blk-err">
              缺 <code>{{ (data as any).logic.degraded.cannot_hit_without.join(', ') }}</code> 时本条永远不会命中。
              <div style="margin-top: 4px">
                现在实际退化成：<b>{{ (data as any).logic.degraded.degenerates_to }}</b>
              </div>
              <div class="dim" style="margin-top: 4px">
                <MarkText :text="(data as any).logic.degraded.cannot_hit_why" />
              </div>
            </span>
          </template>
          <template v-if="(data as any).logic?.rule_effect === 'exempt'">
            <b>规则作用</b>
            <span class="blk-note">
              <b>豁免型</b>：命中 = 豁免生效，按设计不产生违规告警。
              <div class="dim"><MarkText :text="(data as any).logic.rule_effect_why" /></div>
            </span>
          </template>
        </div>
      </a-card>

      <a-card size="small" class="blk" title="引用的本体概念">
        <a-table :data-source="(data as any).concepts" row-key="concept_id" size="small"
          :pagination="(data as any).concepts?.length > 10 ? { pageSize: 10 } : false"
          :columns="[
            { title: '概念', dataIndex: 'concept_id', width: 130 },
            { title: '名称', dataIndex: 'concept_name', width: 180 },
            { title: '所属实体', dataIndex: 'entity_id', width: 120 },
            { title: '可得性', key: 'avail', width: 160 },
            { title: '缺口', dataIndex: 'gap_id', width: 110 },
          ]">
          <template #bodyCell="{ column, record: c }">
            <template v-if="column.key === 'avail'">
              <a-tag :color="AVAIL_OK.has(c.availability) ? 'success' : 'warning'">{{ c.availability }}</a-tag>
            </template>
            <template v-else-if="column.dataIndex === 'gap_id'">{{ c.gap_id || '—' }}</template>
          </template>
          <template #emptyText>
            <a-empty :image-style="{ height: '40px' }" description="未引用概念" />
          </template>
        </a-table>
        <div v-if="(data as any).entities?.length" style="margin-top: 12px">
          <b>存在性判断引用的实体</b>
          <div class="dim">判断「附件/对象是否存在」，区别于上表「字段取值」类概念引用</div>
          <a-table :data-source="(data as any).entities" row-key="entity_id" size="small"
            :pagination="false" style="margin-top: 6px"
            :columns="[
              { title: '实体', dataIndex: 'entity_id', width: 90 },
              { title: '语义', dataIndex: 'semantics' },
            ]" />
        </div>
      </a-card>

      <a-card v-if="(data as any).relations?.length" size="small" class="blk" title="与其他规则的关系">
        <a-table :data-source="(data as any).relations" :row-key="rowKeyOf"
          size="small" :pagination="false"
          :custom-row="(r: any) => r.rel_type === 'conflicts_with'
            ? { style: { cursor: 'pointer' }, onClick: () => openCompare(r.other_rule) } : {}"
          :row-class-name="(r: any) => r.rel_type === 'conflicts_with' ? 'row-conflict' : ''"
          :columns="[
            { title: '关系', dataIndex: 'rel_type', width: 130 },
            { title: '对方', key: 'other', width: 130 },
            { title: '需裁决', key: 'confirm', width: 130, align: 'center' },
            { title: '缺口', dataIndex: 'gap_id', width: 110 },
            { title: '说明', key: 'note' },
          ]">
          <template #bodyCell="{ column, record: r }">
            <template v-if="column.dataIndex === 'rel_type'">
              <a-tag v-if="r.rel_type === 'conflicts_with'" color="error">冲突</a-tag>
              <span v-else>{{ r.rel_type }}</span>
            </template>
            <template v-else-if="column.key === 'other'">
              <a v-if="r.rel_type === 'conflicts_with'" @click.stop="openCompare(r.other_rule)"
                title="点开左右对比两条口径的原文">{{ r.other_rule }}</a>
              <span v-else>{{ r.other_rule }}</span>
            </template>
            <template v-else-if="column.key === 'confirm'">
              <a-tag v-if="r.needs_confirm" color="warning"
                @click="r.rel_type === 'conflicts_with' && openCompare(r.other_rule)">
                需业务侧裁决{{ r.rel_type === 'conflicts_with' ? ' ·' : '' }}
              </a-tag>
              <span v-else class="dim">—</span>
            </template>
            <template v-else-if="column.dataIndex === 'gap_id'">{{ r.gap_id || '—' }}</template>
            <template v-else-if="column.key === 'note'">
              <span class="dim"><MarkText :text="(r.note || '').slice(0, 200)" /></span>
            </template>
          </template>
        </a-table>
        <div class="dim" style="margin-top: 6px">
          「冲突」行可点开对比：原文并排、差异高亮，裁决后留痕
        </div>
      </a-card>

      <!-- 版本历史（B-P1-E）：谁、何时、为什么改了什么；各版随哪个 release 发布 -->
      <a-card size="small" class="blk" title="修订历史">
        <a-alert v-if="verErr" type="error" show-icon :message="`版本历史加载失败：${verErr}`
          " style="margin-bottom: 8px" />
        <template v-if="versions">
          <a-table v-if="versions.items.length" :data-source="versions.items"
            row-key="version" size="small" :pagination="false"
            :columns="[
              { title: '版本', dataIndex: 'version', width: 70, align: 'center' },
              { title: '修订人', key: 'who', width: 150 },
              { title: '时间', key: 'at', width: 150 },
              { title: '修订理由', key: 'reason' },
              { title: '发布', key: 'rel', width: 150 },
            ]">
            <template #bodyCell="{ column, record: v }">
              <template v-if="column.dataIndex === 'version'">
                <a-tag :color="v.version === 1 ? 'default' : 'processing'">v{{ v.version }}</a-tag>
              </template>
              <template v-else-if="column.key === 'who'">
                <template v-if="v.revised_by">
                  <b>{{ v.revised_label || v.revised_by }}</b>
                  <div class="dim">自 v{{ v.revised_from }} 修订</div>
                </template>
                <span v-else class="dim">{{ v.created_by || '装载基线' }}</span>
              </template>
              <template v-else-if="column.key === 'at'">
                <span class="dim">{{ dt(v.created_at) }}</span>
              </template>
              <template v-else-if="column.key === 'reason'">
                <span v-if="v.revised_reason">{{ v.revised_reason }}</span>
                <span v-else class="dim">{{ v.version === 1 ? '初始装载（无人工修订记录）' : '—' }}</span>
              </template>
              <template v-else-if="column.key === 'rel'">
                <a-tag v-if="v.released_in" color="success">
                  随 {{ v.released_in.rule_set_version }}
                </a-tag>
                <a-tag v-else-if="v.version > 1" color="warning">待发布</a-tag>
                <span v-else class="dim">—</span>
              </template>
            </template>
          </a-table>
          <div v-else class="dim">暂无版本历史</div>
          <div class="dim" style="margin-top: 6px">{{ versions.note }}</div>
        </template>
        <a-skeleton v-else active :paragraph="{ rows: 2 }" />
      </a-card>

      <a-card size="small" class="blk" title="最近的判定">
        <a-empty v-if="!(data as any).recent_findings?.length"
          :image-style="{ height: '40px' }" description="暂无判定记录" />
        <a-table v-else :data-source="(data as any).recent_findings"
          :row-key="rowKeyOf" size="small"
          :pagination="(data as any).recent_findings.length > 10 ? { pageSize: 10 } : false"
          :columns="[
            { title: '单据', key: 'bill', width: 220 },
            { title: '结果', key: 'outcome', width: 100, align: 'center' },
            { title: '说明', dataIndex: 'message' },
          ]">
          <template #bodyCell="{ column, record: f }">
            <template v-if="column.key === 'bill'">
              <router-link :to="`/review/report/${f.run_id}`">{{ f.bill_no }}</router-link>
            </template>
            <template v-else-if="column.key === 'outcome'">
              <Outcome :outcome="f.outcome" />
            </template>
          </template>
        </a-table>
      </a-card>

      <a-card v-if="(data as any).note" size="small" class="blk" title="维护备注">
        <a-tooltip v-if="noteShown((data as any).note).brief !== noteShown((data as any).note).full"
          placement="topLeft" :overlay-style="{ maxWidth: '460px' }">
          <template #title>
            <span style="font-size: 12px; line-height: 1.7; color: rgba(255,255,255,.85)">
              {{ noteShown((data as any).note).full }}
            </span>
          </template>
          <div style="white-space: pre-wrap; cursor: help; display: inline;
            border-bottom: 1px dashed rgba(0,0,0,.25)">
            {{ noteShown((data as any).note).brief }}
          </div>
        </a-tooltip>
        <div v-else style="white-space: pre-wrap">{{ noteShown((data as any).note).brief }}</div>
      </a-card>
    </template>
  </Async>

  <ConflictCompareDrawer :key="cmpKey" v-model:open="cmpOpen" :rule-id="id" :other="cmpOther"
    @resolved="reloadDetail" />

  <!-- 修订表单（B-P1-E）：理由必填 + 旧值新值对照 + compare 声明编辑（A-P1-1） -->
  <a-drawer v-model:open="rvOpen" width="720" title="发起规则修订"
    :body-style="{ padding: '16px 20px', overflowY: 'auto' }">
    <a-alert type="info" show-icon style="margin-bottom: 12px"
      message="修订会创建新版本（旧行原样保留、可回看），需随发布版本生效后才会改变审核口径。" />
    <a-alert v-if="rvErr" type="error" show-icon :message="rvErr" style="margin-bottom: 12px" />
    <template v-if="rvForm">
      <div class="rv-sec">
        <b>修订理由（必填）</b>
        <a-textarea v-model:value="rvReason" :rows="2"
          placeholder="为什么改——三个月后看这条规则的人只看得到这段话（异议处置跳转时已预填，可改）" />
      </div>
      <div class="rv-sec">
        <b>八要素变更</b>
        <div class="dim" style="margin-bottom: 8px">只提交真改了的字段；旧值新值对照见表单底部</div>
        <div class="rv-grid">
          <div class="rv-k">① 规则名称</div>
          <a-input v-model:value="rvForm.name" size="small" />
          <div class="rv-k">控制类型</div>
          <a-select v-model:value="rvForm.controlType" size="small"
            :options="[{ value: '禁止' }, { value: '警告' }]" />
          <div class="rv-k">⑧ 优先级</div>
          <a-input v-model:value="rvForm.priority" size="small" />
          <div class="rv-k">责任部门</div>
          <a-input v-model:value="rvForm.owner" size="small" />
        </div>
        <div class="rv-k" style="margin-top: 8px">⑦ 例外</div>
        <a-textarea v-model:value="rvForm.exception" :rows="2" size="small" />
        <div class="rv-k" style="margin-top: 8px">⑤ 适用范围（JSON）</div>
        <a-textarea v-model:value="rvForm.scopeJson" :rows="3" size="small"
          class="rv-json" />
        <div class="rv-k" style="margin-top: 8px">⑥ 判定逻辑（JSON，不含 compare）</div>
        <a-textarea v-model:value="rvForm.logicJson" :rows="4" size="small"
          class="rv-json" />
      </div>
      <div class="rv-sec">
        <b>对比声明（compare）</b>
        <div class="dim" style="margin-bottom: 8px">
          声明该规则的哪些证据键参与「申报值 vs 制度规定值」对比展示（审核报告的对比块）。
          label=显示名、实际值键 / 限额键=证据 JSON 的键名、单位可空。
        </div>
        <div v-for="(c, i) in rvCompare" :key="i" class="rv-cmp-row">
          <a-input v-model:value="c.label" size="small" placeholder="显示名（如 住宿费日均）"
            style="width: 130px" />
          <a-input v-model:value="c.actual_key" size="small" placeholder="实际值键（如 日均）"
            style="flex: 1" />
          <span class="dim">vs</span>
          <a-input v-model:value="c.limit_key" size="small" placeholder="限额键（如 区间上界）"
            style="flex: 1" />
          <a-input v-model:value="c.unit" size="small" placeholder="单位" style="width: 70px" />
          <a-button size="small" danger @click="rvCompare.splice(i, 1)">删</a-button>
        </div>
        <a-button size="small" dashed block
          @click="rvCompare.push({ label: '', actual_key: '', limit_key: '', unit: '' })">
          + 加一条声明
        </a-button>
      </div>
      <div class="rv-sec">
        <b>旧值 → 新值对照</b>
        <table v-if="rvDiffRows.length" class="rv-diff">
          <tbody>
            <tr v-for="r in rvDiffRows" :key="r.label">
              <td class="rv-dl">{{ r.label }}</td>
              <td class="rv-old">{{ r.old }}</td>
              <td class="rv-new">{{ r.nw }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="dim">尚未改动任何字段</div>
      </div>
    </template>
    <template #footer>
      <div style="display: flex; gap: 8px; justify-content: flex-end">
        <a-button :disabled="rvBusy" @click="rvOpen = false">取消</a-button>
        <a-button type="primary" :loading="rvBusy" @click="submitRevise">提交修订</a-button>
      </div>
    </template>
  </a-drawer>
  </div>
</template>

<style scoped>
.rd-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 12px; }
.rd-title { font-size: 18px; font-weight: 600; margin: 0; }
.blk { margin-bottom: 16px; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.clause { background: #fafafa; padding: 1px 6px; border-radius: 4px; }
.blk-note { font-size: 12.5px; color: rgba(0, 0, 0, .65); }
.blk-err { font-size: 12.5px; color: #cf1322; }
.json {
  margin: 0; font-size: 12px; background: #fafafa; border-radius: 6px;
  padding: 8px 10px; overflow-x: auto; max-width: 640px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
}
.kv { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 0 32px; }
.kv b { display: inline-block; width: 130px; color: rgba(0, 0, 0, .45); font-weight: 500; font-size: 13px; padding: 7px 0; vertical-align: top; }
.kv > span { font-size: 13px; padding: 7px 0; display: inline-block; min-width: 0; }
:deep(.row-conflict) td { background: #fff1f0; }
:deep(.row-conflict:hover) td { background: #ffccc7; }

/* 修订表单（B-P1-E） */
.rv-sec { margin-bottom: 16px; display: flex; flex-direction: column; gap: 6px; }
.rv-grid { display: grid; grid-template-columns: 84px 1fr 84px 1fr; gap: 6px 8px; align-items: center; }
.rv-k { font-size: 12.5px; color: rgba(0, 0, 0, .55); }
.rv-json :deep(textarea) { font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace; font-size: 12px; }
.rv-cmp-row { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.rv-diff { width: 100%; border-collapse: collapse; font-size: 12px; }
.rv-diff td { border: 1px solid #f0f0f0; padding: 5px 8px; vertical-align: top; word-break: break-all; }
.rv-diff .rv-dl { width: 90px; color: rgba(0, 0, 0, .45); background: #fafafa; }
.rv-diff .rv-old { color: rgba(0, 0, 0, .45); text-decoration: line-through; background: #fafafa; }
.rv-diff .rv-new { background: #f6ffed; }
</style>
