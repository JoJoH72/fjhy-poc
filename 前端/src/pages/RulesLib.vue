<script setup lang="ts">
/**
 * 规则库（2026-10-26 IA 重排新页）：规则管理员看生效规则全集。
 * 四页签：规则清单（规则本体表数据）｜报销标准（限额字典两段式）｜
 * 规则明细（条款→规则的加工台账，行展开并排比对）｜关系图谱。
 *
 * 2026-10-27 P0-2/P0-3（打磨规划断点 3/4）：明细页签原来取 point/state、
 * 报销标准取 table/row/value——全是接口没有的字段名，两页签静默空数据。
 * 现按接口真实字段（rule_name/review_state/origin；table_id/rows 两段式）重接，
 * 数据全部复用现有只读接口，不动后端。
 */
import { computed, ref } from 'vue'
import Async from '../components/Async.vue'
import { api } from '../api'
import type { ExDetail } from '../api'

const tab = ref('rules')
const kw = ref('')

async function load() {
  const r = await api.rules({ size: 500 })
  return r
}

const CTRL: Record<string, { text: string; color: string }> = {
  禁止: { text: '禁止', color: 'error' },
  警告: { text: '警告', color: 'warning' },
}
const TYPE: Record<string, { text: string; color: string }> = {
  hard: { text: '硬', color: 'red' },
  mixed: { text: '混合', color: 'orange' },
  soft: { text: '软', color: 'blue' },
}

// ── 规则明细页签（P0-2 修复：字段对齐 + 行展开并排卡）────────────────────
const dk = ref('')                      // 明细搜索
const dOrigin = ref('')                 // 来源筛选
const dState = ref('')                  // 状态筛选
const dOpen = ref<string[]>([])         // 展开行
const ORIGIN_COLOR: Record<string, string> = {
  AI草稿: 'warning', 列映射: 'success', 场景拆条: 'processing',
}
const STATE_COLOR: Record<string, string> = {
  待审: 'default', 采纳: 'success', 驳回: 'error',
}

const detailCols = [
  { title: '条款', dataIndex: 'clause_no', width: 110 },
  { title: '规则要点', key: 'name', ellipsis: true },
  { title: '控制', key: 'ctrl', width: 76, align: 'center' as const },
  { title: '来源', key: 'origin', width: 96, align: 'center' as const },
  { title: '状态', key: 'state', width: 84, align: 'center' as const },
  { title: '审定人', dataIndex: 'reviewer', width: 90 },
]

function detailFiltered(items: ExDetail[]): ExDetail[] {
  return items.filter((x) =>
    (!dk.value || (x.rule_name || '').includes(dk.value)
      || (x.clause_no || '').includes(dk.value))
    && (!dOrigin.value || x.origin === dOrigin.value)
    && (!dState.value || x.review_state === dState.value))
}

/** 条款原文里的数值与草稿阈值比对：对不上的红字（数值幻觉核对的呈现面） */
function suspectNums(d: ExDetail): Set<string> {
  const out = new Set<string>()
  for (const n of d.numbers || []) {
    if (!(d.clause_body || '').includes(n)) out.add(n)
  }
  return out
}

const EIGHT: { key: keyof ExDetail; label: string }[] = [
  { key: 'rule_name', label: '① 规则名称' },
  { key: 'dimension', label: '② 审核维度' },
  { key: 'risk_text', label: '③ 风险类别' },
  { key: 'scope_text', label: '⑤ 适用范围' },
  { key: 'logic_text', label: '⑥ 判定逻辑' },
  { key: 'exception_text', label: '⑦ 例外' },
]

// ── 报销标准页签（P0-3 修复：两段式——表清单 + 选中表行数据）──────────────
const selTable = ref('')
const ltCols = [
  { title: '标准表', key: 'tid', width: 170 },
  { title: '类型', dataIndex: 'kind', width: 110 },
  { title: '行数', dataIndex: 'row_count', width: 64, align: 'right' as const },
  { title: '来源文件', dataIndex: 'source_file', width: 200, ellipsis: true },
  { title: '未命中时', dataIndex: 'on_miss', width: 110 },
  { title: '说明', key: 'note' },
]
const KIND_CN: Record<string, string> = {
  limit_table: '限额表', decision_table: '决策表', allow_list: '名单表',
  keyword_list: '关键词表',
}

/** row_key（JSON 对象）按 key_fields 顺序展开成显示串 */
function keyText(rowKey: Record<string, unknown>, keyFields: string[] | null): string {
  const keys = (keyFields && keyFields.length ? keyFields : Object.keys(rowKey))
    .filter((k) => rowKey[k] !== undefined && rowKey[k] !== null)
  if (!keys.length) return JSON.stringify(rowKey)
  return keys.map((k) => `${k}=${rowKey[k]}`).join(' · ')
}

/** row_value 里挑人话主字段（不同 kind 结构不同） */
function valueText(rowValue: Record<string, unknown>): string {
  if (!rowValue) return '—'
  const prefer = ['rule', 'amount', 'rate', 'condition', 'unit', 'city']
  const parts: string[] = []
  for (const k of prefer) {
    const v = rowValue[k]
    if (v !== undefined && v !== null && v !== '') parts.push(`${k}: ${v}`)
  }
  return parts.length ? parts.join(' · ') : JSON.stringify(rowValue)
}
</script>

<template>
  <div>
    <Async :load="load">
      <template #default="{ data }">
        <a-card size="small">
          <a-tabs v-model:active-key="tab">
            <a-tab-pane key="rules" :tab="`规则清单 (${(data.items || []).length})`">
              <div class="toolbar">
                <a-input-search v-model:value="kw" placeholder="规则编号 / 名称" allow-clear style="width: 240px" />
              </div>
              <a-table
                :data-source="(data.items || []).filter((x: any) =>
                  !kw || (x.rule_id || '').includes(kw) || (x.rule_name || '').includes(kw))"
                :row-key="(r: any) => r.rule_id"
                size="small"
                :pagination="{ pageSize: 15, showTotal: (t: number) => `共 ${t} 条` }"
                :columns="[
                  { title: '规则编号', dataIndex: 'rule_id', width: 130 },
                  { title: '规则名称', dataIndex: 'rule_name', ellipsis: true },
                  { title: '维度', dataIndex: 'dimension', width: 130, ellipsis: true },
                  { title: '类型', dataIndex: 'rule_class', width: 80 },
                  { title: '控制', dataIndex: 'control_type', width: 80 },
                  { title: '状态', dataIndex: 'compiled', width: 90 },
                ]">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.dataIndex === 'rule_class'">
                    <a-tag :color="TYPE[record.rule_class]?.color || 'default'">
                      {{ TYPE[record.rule_class]?.text || record.rule_class }}
                    </a-tag>
                  </template>
                  <template v-else-if="column.dataIndex === 'control_type'">
                    <a-tag :color="CTRL[record.control_type]?.color || 'default'">
                      {{ record.control_type || '—' }}
                    </a-tag>
                  </template>
                  <template v-else-if="column.dataIndex === 'compiled'">
                    <a-tag :color="record.compiled ? 'success' : 'default'">
                      {{ record.compiled ? '可执行' : '待定' }}
                    </a-tag>
                  </template>
                  <template v-else-if="column.dataIndex === 'rule_name'">
                    <router-link :to="`/asset/rules/${encodeURIComponent(record.rule_id)}`">
                      {{ record.rule_name }}
                    </router-link>
                  </template>
                </template>
              </a-table>
            </a-tab-pane>

            <!-- P0-3：两段式。上段=标准表清单（行可选中），下段=选中表的行数据。
                 原版取 table/row/value 三个不存在的字段，整页签静默空数据。 -->
            <a-tab-pane key="std" tab="报销标准">
              <a-alert type="info" show-icon style="margin-bottom: 12px"
                message="住宿/伙食/市内交通限额与职级、城市对照。数据来自业务字典（原业务本体），每行带可信度与制度出处。" />
              <Async :load="() => api.lookupTables()">
                <template #default="{ data: std }">
                  <a-table :data-source="std.items || []"
                    :row-key="(r: any) => r.table_id"
                    :custom-row="(r: any) => ({ style: { cursor: 'pointer' }, onClick: () => { selTable = selTable === r.table_id ? '' : r.table_id } })"
                    :row-class-name="(r: any) => (selTable === r.table_id ? 'row-sel' : '')"
                    size="small" :pagination="false" :columns="ltCols">
                    <template #bodyCell="{ column, record: t }">
                      <template v-if="column.key === 'tid'">
                        <b>{{ t.table_id }}</b>
                      </template>
                      <template v-else-if="column.dataIndex === 'kind'">
                        <a-tag>{{ KIND_CN[t.kind] || t.kind }}</a-tag>
                      </template>
                      <template v-else-if="column.key === 'note'">
                        <span class="dim">{{ t.note || '—' }}</span>
                      </template>
                    </template>
                  </a-table>

                  <template v-if="selTable">
                    <h3 class="sec">{{ selTable }} · 行数据</h3>
                    <Async :load="() => api.lookupTable(selTable)" :deps="selTable"
                      :key="selTable">
                      <template #default="{ data: one }">
                        <a-alert v-if="one.reconciliation" type="success" show-icon
                          style="margin-bottom: 10px"
                          :message="`对账通过：源表 ${one.reconciliation.sum_source_rows} 行已归并为 ${one.rows.length} 场景（与装载时声明一致）`" />
                        <a-table :data-source="one.rows" :row-key="(r: any) => r.id" size="small"
                          :pagination="one.rows.length > 10 ? { pageSize: 10 } : false"
                          :columns="[
                            { title: '条目键', key: 'rk', width: 230 },
                            { title: '内容', key: 'rv' },
                            { title: '可信度', dataIndex: 'confidence', width: 96 },
                            { title: '启用', key: 'on', width: 60, align: 'center' },
                          ]">
                          <template #bodyCell="{ column, record: r }">
                            <template v-if="column.key === 'rk'">
                              <code class="kcode">{{ keyText(r.row_key, one.key_fields) }}</code>
                            </template>
                            <template v-else-if="column.key === 'rv'">
                              <span class="dim">{{ valueText(r.row_value) }}</span>
                              <div v-if="r.note" class="dim" style="font-size: 11.5px; margin-top: 2px">
                                <MarkText :text="r.note" />
                              </div>
                            </template>
                            <template v-else-if="column.key === 'on'">
                              <a-tag v-if="r.enabled" color="success">是</a-tag>
                              <a-tag v-else color="default">停</a-tag>
                            </template>
                          </template>
                        </a-table>
                      </template>
                    </Async>
                  </template>
                  <div v-else class="dim" style="margin-top: 8px">点上方任一标准表查看行数据</div>
                </template>
              </Async>
            </a-tab-pane>

            <!-- P0-2：字段对齐（rule_name/review_state/origin）+ 行展开「条款原文 vs 八要素」并排卡 -->
            <a-tab-pane key="detail" tab="规则明细">
              <Async :load="() => api.exDetails()">
                <template #default="{ data: det }">
                  <div class="toolbar">
                    <a-input-search v-model:value="dk" placeholder="条款号 / 规则要点" allow-clear
                      style="width: 220px" />
                    <a-select v-model:value="dOrigin" style="width: 130px" allow-clear
                      placeholder="全部来源"
                      :options="[
                        { value: 'AI草稿', label: 'AI草稿' },
                        { value: '列映射', label: '列映射' },
                        { value: '场景拆条', label: '场景拆条' },
                      ]" />
                    <a-select v-model:value="dState" style="width: 110px" allow-clear
                      placeholder="全部状态"
                      :options="[
                        { value: '待审', label: '待审' },
                        { value: '采纳', label: '采纳' },
                        { value: '驳回', label: '驳回' },
                      ]" />
                    <span class="dim">共 {{ detailFiltered(det.items || []).length }} 条</span>
                  </div>
                  <a-table :data-source="detailFiltered(det.items || [])"
                    :row-key="(r: any) => r.detail_id" size="small"
                    :pagination="{ pageSize: 15, showTotal: (t: number) => `共 ${t} 条` }"
                    :columns="detailCols"
                    :expanded-row-keys="dOpen"
                    @expand="(_e: any, r: any) => {
                      const i = dOpen.indexOf(r.detail_id)
                      if (i >= 0) dOpen.splice(i, 1); else dOpen.push(r.detail_id)
                    }">
                    <template #bodyCell="{ column, record: d }">
                      <template v-if="column.key === 'name'">{{ d.rule_name }}</template>
                      <template v-else-if="column.key === 'ctrl'">
                        <a-tag v-if="d.control_type" :color="d.control_type === '禁止' ? 'error' : 'warning'">
                          {{ d.control_type }}
                        </a-tag>
                        <span v-else class="dim">—</span>
                      </template>
                      <template v-else-if="column.key === 'origin'">
                        <a-tooltip :title="d.origin === 'AI草稿'
                          ? '模型起草，数值需人工逐条核' : '确定性映射（不出网不计费）'">
                          <a-tag :color="ORIGIN_COLOR[d.origin] || 'default'">
                            {{ d.origin === 'AI草稿' ? 'AI草稿' : d.origin }}
                          </a-tag>
                        </a-tooltip>
                      </template>
                      <template v-else-if="column.key === 'state'">
                        <a-tag :color="STATE_COLOR[d.review_state] || 'default'">{{ d.review_state }}</a-tag>
                      </template>
                      <template v-else-if="column.dataIndex === 'reviewer'">
                        <span class="dim">{{ d.reviewer || '—' }}</span>
                      </template>
                    </template>
                    <template #expandedRowRender="{ record: d }">
                      <div class="dcard">
                        <div class="dpane">
                          <div class="dhead">
                            <b>条款原文</b>
                            <a-tag :color="d.confidence === 'OCR' ? 'warning' : 'success'">
                              {{ d.confidence }}
                            </a-tag>
                          </div>
                          <div class="dfile dim">{{ d.file_name }} · {{ d.clause_no }}</div>
                          <pre class="dbody">{{ d.clause_body || '（原文未取到）' }}</pre>
                        </div>
                        <div class="dpane">
                          <div class="dhead"><b>规则要点（八要素投影）</b></div>
                          <div class="kv8">
                            <template v-for="f in EIGHT" :key="f.key">
                              <b>{{ f.label }}</b>
                              <span :class="{ miss: !d[f.key] }">{{ d[f.key] || '—' }}</span>
                            </template>
                            <b>⑧ 数值</b>
                            <span>
                              <template v-if="(d.numbers || []).length">
                                <a-tag v-for="n in d.numbers" :key="n"
                                  :color="suspectNums(d).has(n) ? 'error' : 'default'"
                                  :title="suspectNums(d).has(n)
                                    ? '原文里找不到这个数值——模型可能编的，须人工核对' : '原文里有此数值'">
                                  {{ n }}{{ suspectNums(d).has(n) ? ' ⚠' : '' }}
                                </a-tag>
                              </template>
                              <span v-else class="dim">无数值</span>
                            </span>
                          </div>
                          <div v-if="d.note" class="dim dnote"><MarkText :text="d.note" /></div>
                        </div>
                      </div>
                    </template>
                  </a-table>
                </template>
              </Async>
            </a-tab-pane>
          </a-tabs>
        </a-card>
      </template>
    </Async>
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.sec { font-size: 14px; font-weight: 600; margin: 16px 0 8px; }
:deep(.row-sel) td { background: #e6f4ff; }
.kcode { font-size: 11px; background: #fafafa; padding: 1px 5px; border-radius: 4px;
  word-break: break-all; }

/* 明细行展开：条款原文 vs 八要素并排 */
.dcard { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.dpane { min-width: 0; border: 1px solid #f0f0f0; border-radius: 6px; padding: 8px 10px; }
.dhead { display: flex; align-items: center; justify-content: space-between;
  font-size: 12.5px; margin-bottom: 4px; }
.dfile { font-size: 11.5px; margin-bottom: 6px; }
.dbody { margin: 0; font-size: 12px; line-height: 1.7; white-space: pre-wrap;
  word-break: break-all; background: #fafafa; border-radius: 4px; padding: 8px;
  max-height: 300px; overflow-y: auto; }
.kv8 { display: grid; grid-template-columns: 84px 1fr; gap: 4px 10px; font-size: 12.5px; }
.kv8 b { color: rgba(0, 0, 0, .45); font-weight: 500; }
.kv8 .miss { color: rgba(0, 0, 0, .3); }
.dnote { margin-top: 8px; line-height: 1.6; }
</style>
