<script setup lang="ts">
/**
 * 规则库（2026-10-26 IA 重排新页）：规则管理员看生效规则全集。
 * 三页签：规则清单（规则本体表数据）｜报销标准（业务本体的限额字典换形态）｜
 * 规则明细（doclib 条款映射）。数据全部复用现有只读接口，不动后端。
 */
import { computed, ref } from 'vue'
import Async from '../components/Async.vue'
import { api } from '../api'

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

            <a-tab-pane key="std" tab="报销标准">
              <a-alert type="info" show-icon style="margin-bottom: 12px"
                message="住宿/伙食/市内交通限额与职级、城市对照。数据来自业务字典（原业务本体）。" />
              <Async :load="() => api.lookupTables()">
                <template #default="{ data: std }">
                  <a-table :data-source="std.items || []" :row-key="(r: any) => r.table + r.row"
                    size="small" :pagination="false"
                    :columns="[
                      { title: '标准表', dataIndex: 'table', width: 140 },
                      { title: '条目', dataIndex: 'row', ellipsis: true },
                      { title: '数值', dataIndex: 'value', width: 160 },
                    ]" />
                </template>
              </Async>
            </a-tab-pane>

            <a-tab-pane key="detail" tab="规则明细">
              <Async :load="() => api.exDetails()">
                <template #default="{ data: det }">
                  <a-table :data-source="(det.items || []).slice(0, 200)" :row-key="(r: any) => r.id"
                    size="small" :pagination="{ pageSize: 15 }"
                    :columns="[
                      { title: '条款', dataIndex: 'clause_no', width: 90 },
                      { title: '规则要点', dataIndex: 'point', ellipsis: true },
                      { title: '状态', dataIndex: 'state', width: 100 },
                    ]" />
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
.toolbar { display: flex; margin-bottom: 12px; }
</style>
