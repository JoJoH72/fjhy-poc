<script setup lang="ts">
/**
 * 知识结构（2026-10-26 IA 重排新页，仅系统管理员）：本体三元结构的技术视图。
 * 业务本体/规则本体/流程本体/本体工场四页合并为只读页签——
 * 业务角色不再看到这些（退出了业务界面），但实施与排障仍需要看。
 *
 * 2026-10-28 P0-3：三个页签全部重接真实字段——
 * 旧版取 data.concepts / rule_schema / name+desc，三个字段接口都不回
 * （/api/asset/ontology 只有 stats/entities/availability/enums），
 * 整页静默 No data。现按接口真实结构接线：
 *   业务概念 ← /api/onto/graph/rich 的 concept_nodes（被规则引用的概念，
 *              带 entity_name/data_type/availability/gap_id/n_rules）
 *   规则要素 ← /api/onto/source/section?name=rule_schema 的八要素定义
 *              （YAML 原文的 elements 段，field/name/required/note）
 *   流程节点 ← /api/asset/process 的 nodes（node_id/node_name，note 收 tooltip）
 * 后端接口全部现成，不动。
 */
import { ref } from 'vue'
import Async from '../components/Async.vue'
import { api } from '../api'

const tab = ref('biz')

/** 可得性标签色：绿=拿得到；黄=吊在缺口上；灰=不可得 */
const AVAIL_COLOR: Record<string, string> = {
  样本可得: 'success', '样本可得(审批意见)': 'success', 已录入资产: 'success',
  可推导: 'success', 仅单人可得: 'warning', 需票据OCR: 'warning',
  不可得: 'default',
}

/** rule_schema YAML 里的 elements 行：`- {field: rule_id, name: 规则标识, required: true, …}` */
interface SchemaEl { field: string; name: string; required: string; note: string }
function parseSchema(text: string): SchemaEl[] {
  const out: SchemaEl[] = []
  for (const raw of text.split('\n')) {
    // 一条要素是单行 flow mapping（- {field: …}）；note 可能折到续行，续行以空格开头且不含 "field:"
    const m = raw.match(/^\s*-\s*\{?\s*field:\s*([\w]+)/)
    if (!m) continue
    const name = (raw.match(/name:\s*([^,}]+)/) || [])[1]?.trim() || ''
    const required = /required:\s*true/.test(raw) ? '必填' : '选填'
    const note = (raw.match(/note:\s*"([^"]*)"/) || raw.match(/note:\s*([^,}]+)/) || [])[1]?.trim() || ''
    out.push({ field: m[1], name, required, note })
  }
  return out
}
</script>

<template>
  <div>
    <a-alert type="info" show-icon style="margin-bottom: 16px"
      message="只读视图 · 供实施与排障使用" />
    <a-card size="small">
      <a-tabs v-model:active-key="tab">
        <a-tab-pane key="biz" tab="业务概念">
          <Async :load="() => api.ontoGraphRich()">
            <template #default="{ data }">
              <div class="dim" style="margin-bottom: 10px">
                被规则引用的 {{ (data.concept_nodes || []).length }} 个概念（本体共
                {{ (data.entities || []).reduce((s: number, e: any) => s + (e.concepts || 0), 0) }}
                个）；可得性非绿的概念吊在数据缺口上
              </div>
              <a-table :data-source="(data.concept_nodes || [])"
                :row-key="(r: any) => r.concept_id"
                size="small" :pagination="{ pageSize: 15, showTotal: (t: number) => `共 ${t} 条` }"
                :columns="[
                  { title: '概念ID', dataIndex: 'concept_id', width: 100 },
                  { title: '概念名', dataIndex: 'concept_name', width: 150 },
                  { title: '所属实体', dataIndex: 'entity_name', width: 130 },
                  { title: '数据类型', dataIndex: 'data_type', width: 100 },
                  { title: '可得性', key: 'avail', width: 150 },
                  { title: '缺口', dataIndex: 'gap_id', width: 90 },
                  { title: '引用规则数', dataIndex: 'n_rules', width: 96, align: 'right' as const },
                ]">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'avail'">
                    <a-tag :color="AVAIL_COLOR[record.availability] || 'default'">{{ record.availability }}</a-tag>
                  </template>
                  <template v-else-if="column.dataIndex === 'gap_id'">
                    <span class="dim">{{ record.gap_id || '—' }}</span>
                  </template>
                </template>
              </a-table>
            </template>
          </Async>
        </a-tab-pane>

        <a-tab-pane key="rule" tab="规则要素">
          <Async :load="() => api.ontoSourceSection('rule_schema')">
            <template #default="{ data }">
              <a-table :data-source="parseSchema(data.text || '')" row-key="field"
                size="small" :pagination="false"
                :columns="[
                  { title: '字段', dataIndex: 'field', width: 160 },
                  { title: '要素名', dataIndex: 'name', width: 140 },
                  { title: '必填', key: 'req', width: 80, align: 'center' as const },
                  { title: '说明', key: 'note' },
                ]">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'req'">
                    <a-tag v-if="record.required === '必填'" color="processing">必填</a-tag>
                    <a-tag v-else>选填</a-tag>
                  </template>
                  <template v-else-if="column.key === 'note'">
                    <span class="dim">{{ record.note || '—' }}</span>
                  </template>
                </template>
              </a-table>
            </template>
          </Async>
        </a-tab-pane>

        <a-tab-pane key="proc" tab="流程节点">
          <Async :load="() => api.process()">
            <template #default="{ data }">
              <a-table :data-source="(data.nodes || [])" :row-key="(r: any) => r.node_id"
                size="small" :pagination="false"
                :columns="[
                  { title: '节点', dataIndex: 'node_id', width: 100 },
                  { title: '名称', dataIndex: 'node_name', width: 200 },
                  { title: '旁路', key: 'bypass', width: 80, align: 'center' as const },
                  { title: '出现次数', dataIndex: 'occurrences', width: 100, align: 'right' as const },
                  { title: '说明', key: 'note' },
                ]">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'bypass'">
                    <a-tag v-if="record.is_bypass" color="warning">旁路</a-tag>
                    <span v-else class="dim">—</span>
                  </template>
                  <template v-else-if="column.dataIndex === 'occurrences'">
                    <span class="dim">{{ record.occurrences ?? '—' }}</span>
                  </template>
                  <template v-else-if="column.key === 'note'">
                    <a-tooltip v-if="record.note" placement="topLeft" :overlay-style="{ maxWidth: '420px' }">
                      <template #title><span class="tt">{{ String(record.note).replace(/\*\*/g, '') }}</span></template>
                      <span class="dim note-clip">{{ String(record.note).replace(/\*\*/g, '') }}</span>
                    </a-tooltip>
                    <span v-else class="dim">—</span>
                  </template>
                </template>
              </a-table>
            </template>
          </Async>
        </a-tab-pane>
      </a-tabs>
    </a-card>
  </div>
</template>

<style scoped>
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.note-clip { display: inline-block; max-width: 100%; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom; }
.tt { font-size: 12px; line-height: 1.7; color: rgba(255, 255, 255, .85); }
</style>
