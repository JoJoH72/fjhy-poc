<script setup lang="ts">
/**
 * 知识结构（2026-10-26 IA 重排新页，仅系统管理员）：本体三元结构的技术视图。
 * 业务本体/规则本体/流程本体/本体工场四页合并为只读页签——
 * 业务角色不再看到这些（退出了业务界面），但实施与排障仍需要看。
 * 数据接口全部复用现有只读端点，不动后端。
 */
import { ref } from 'vue'
import Async from '../components/Async.vue'
import { api } from '../api'

const tab = ref('biz')
</script>

<template>
  <div>
    <a-alert type="info" show-icon style="margin-bottom: 16px"
      message="只读视图 · 供实施与排障使用" />
    <a-card size="small">
      <a-tabs v-model:active-key="tab">
        <a-tab-pane key="biz" tab="业务概念">
          <Async :load="() => api.ontology()">
            <template #default="{ data }">
              <a-table :data-source="(data.concepts || [])" :row-key="(r: any) => r.concept_id"
                size="small" :pagination="{ pageSize: 15, showTotal: (t: number) => `共 ${t} 条` }"
                :columns="[
                  { title: '概念ID', dataIndex: 'concept_id', width: 100 },
                  { title: '概念名', dataIndex: 'name', width: 160 },
                  { title: '类型', dataIndex: 'kind', width: 100 },
                  { title: '说明', dataIndex: 'desc', ellipsis: true },
                ]" />
            </template>
          </Async>
        </a-tab-pane>

        <a-tab-pane key="rule" tab="规则要素">
          <Async :load="() => api.ontology()">
            <template #default="{ data }">
              <a-table :data-source="(data.rule_schema || data.rule_onto || [])" :row-key="(r: any) => r.key || r.name"
                size="small" :pagination="{ pageSize: 15 }"
                :columns="[
                  { title: '要素', dataIndex: 'name', width: 160 },
                  { title: '取值', dataIndex: 'value', ellipsis: true },
                ]" />
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
                  { title: '名称', dataIndex: 'name', width: 200 },
                  { title: '说明', dataIndex: 'desc', ellipsis: true },
                ]" />
            </template>
          </Async>
        </a-tab-pane>
      </a-tabs>
    </a-card>
  </div>
</template>
