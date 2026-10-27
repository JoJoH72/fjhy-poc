<script setup lang="ts">
/**
 * 个人工作台（2026-10-26 AntD 换装版）：待办任务 / 待认领 / 已办记录。
 * 企业后台语言：名词化标题、表格列、状态标签；说明性句子收进 tooltip 与副标题。
 */
import { createVNode, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ExclamationCircleOutlined } from '@ant-design/icons-vue'
import Async from '../components/Async.vue'
import { api, type MyWork, type MyWorkItem, type MyWorkHistory } from '../api'

const router = useRouter()
const reload = ref(0)
const busy = ref('')
const tab = ref('todo')

const CONCL: Record<string, { text: string; color: string }> = {
  pass: { text: '通过', color: 'success' },
  review: { text: '待复核', color: 'warning' },
  reject: { text: '不通过', color: 'error' },
}
const DECISION: Record<string, { text: string; color: string }> = {
  agree: { text: '复核通过', color: 'success' },
  agree_with_note: { text: '通过并备注', color: 'success' },
  return: { text: '退回', color: 'error' },
}

async function load(): Promise<MyWork> {
  return api.myWork()
}

async function claim(no: string) {
  busy.value = no
  try {
    await api.billClaim(no)
    message.success(`已认领 ${no}`)
    reload.value++
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally { busy.value = '' }
}

async function release(no: string) {
  busy.value = no
  try {
    // 释放会放弃该单的办理权，二次确认防误触（企业后台惯例）
    const { Modal } = await import('ant-design-vue')
    Modal.confirm({
      title: '释放该工单？',
      icon: createVNode(ExclamationCircleOutlined),
      content: `${no} 将退回待认领队列，其他审核岗可认领。`,
      okText: '释放',
      cancelText: '取消',
      onOk: async () => {
        await api.billRelease(no)
        message.success(`已释放 ${no}`)
        reload.value++
      },
    })
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally { busy.value = '' }
}

function goReport(row: MyWorkItem) {
  if (row.run_id) router.push(`/review/report/${encodeURIComponent(row.run_id)}`)
  else router.push(`/review/bills/${encodeURIComponent(row.bill_no)}`)
}

const fmtAmt = (v: number | null) =>
  v != null ? v.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '—'

const todoCols = [
  { title: '单据编号', dataIndex: 'bill_no', width: 240 },
  { title: '核算主体', dataIndex: 'org_name', width: 180, ellipsis: true },
  { title: '金额（元）', dataIndex: 'amount', width: 120, align: 'right' as const },
  { title: 'AI 结论', dataIndex: 'conclusion', width: 96 },
  { title: '认领时间', dataIndex: 'claimed_at', width: 140 },
  { title: '操作', key: 'op', width: 170 },
]
const poolCols = [
  { title: '单据编号', dataIndex: 'bill_no', width: 240 },
  { title: '核算主体', dataIndex: 'org_name', width: 180, ellipsis: true },
  { title: '金额（元）', dataIndex: 'amount', width: 120, align: 'right' as const },
  { title: 'AI 结论', dataIndex: 'conclusion', width: 96 },
  { title: '命中/待人工', key: 'hits', width: 110, align: 'center' as const },
  { title: '操作', key: 'op', width: 100 },
]
const histCols = [
  { title: '单据编号', dataIndex: 'bill_no', width: 240 },
  { title: '核算主体', dataIndex: 'org_name', width: 180, ellipsis: true },
  { title: '金额（元）', dataIndex: 'amount', width: 120, align: 'right' as const },
  { title: '复核结论', dataIndex: 'decision', width: 110 },
  { title: '退回理由', dataIndex: 'return_reason', ellipsis: true },
  { title: '复核时间', dataIndex: 'decided_at', width: 150 },
]
const dt = (s: string | null) => (s || '').slice(0, 16).replace('T', ' ')
</script>

<template>
  <div class="mywork">
    <Async :load="load" :deps="String(reload)">
      <template #default="{ data }">
        <!-- 四卡结构一致（title + value + .sub 副行）：长文案不进 suffix——
             suffix 挤在 24px 大数字旁会折成两行小字，与大数字卡格格不入 -->
        <a-row :gutter="[16, 16]">
          <a-col :span="6"><a-card size="small">
            <a-statistic title="待办任务" :value="data.todo.total" />
            <div class="sub">认领后办理</div>
          </a-card></a-col>
          <a-col :span="6"><a-card size="small">
            <a-statistic title="待认领" :value="data.pool.total" />
            <div class="sub">全审核岗共享池</div>
          </a-card></a-col>
          <a-col :span="6"><a-card size="small">
            <a-statistic title="今日已办" :value="data.stats.today" />
            <div class="sub">本月 {{ data.stats.month }}</div>
          </a-card></a-col>
          <a-col :span="6"><a-card size="small">
            <a-statistic title="累计复核" :value="data.stats.total" />
            <div class="sub">通过 {{ data.stats.agree }} · 退回 {{ data.stats.ret }}</div>
          </a-card></a-col>
        </a-row>

        <a-alert v-if="data.me.orgs.length" type="info" show-icon style="margin-top: 16px"
          :message="`数据范围：已按单位过滤（${data.me.orgs.length} 个核算主体）`" />

        <a-card size="small" style="margin-top: 16px">
          <a-tabs v-model:active-key="tab">
            <a-tab-pane key="todo" :tab="`待办任务 (${data.todo.total})`">
          <a-table :columns="todoCols" :data-source="data.todo.items" row-key="bill_no"
            size="small" :pagination="false">
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'bill_no'">
                <router-link :to="`/review/bills/${record.bill_no}`">{{ record.bill_no }}</router-link>
                <a-tag v-if="record.is_demo" color="default" style="margin-left: 6px">演示</a-tag>
              </template>
              <template v-else-if="column.dataIndex === 'amount'">{{ fmtAmt(record.amount) }}</template>
              <template v-else-if="column.dataIndex === 'conclusion'">
                <a-tag :color="CONCL[record.conclusion || '']?.color || 'default'">
                  {{ CONCL[record.conclusion || '']?.text || '未审' }}
                </a-tag>
              </template>
              <template v-else-if="column.dataIndex === 'claimed_at'">{{ dt(record.claimed_at) }}</template>
              <template v-else-if="column.key === 'op'">
                <a-button type="primary" size="small" @click="goReport(record)">办理</a-button>
                <a-button size="small" style="margin-left: 8px"
                  :disabled="busy === record.bill_no" @click="release(record.bill_no)">释放</a-button>
              </template>
            </template>
            <template #emptyText>
              <a-empty :image-style="{ height: '40px' }" description="暂无待办任务" />
            </template>
          </a-table>
            </a-tab-pane>

            <a-tab-pane key="pool" :tab="`待认领 (${data.pool.total})`">
          <a-table :columns="poolCols" :data-source="data.pool.items" row-key="bill_no"
            size="small" :pagination="{ pageSize: 10, showTotal: (t: number) => `共 ${t} 条` }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'bill_no'">
                <router-link :to="`/review/bills/${record.bill_no}`">{{ record.bill_no }}</router-link>
                <a-tag v-if="record.is_demo" color="default" style="margin-left: 6px">演示</a-tag>
              </template>
              <template v-else-if="column.dataIndex === 'amount'">{{ fmtAmt(record.amount) }}</template>
              <template v-else-if="column.dataIndex === 'conclusion'">
                <a-tag :color="CONCL[record.conclusion || '']?.color || 'default'">
                  {{ CONCL[record.conclusion || '']?.text || '未审' }}
                </a-tag>
              </template>
              <template v-else-if="column.key === 'hits'">{{ record.hit_count ?? 0 }} / {{ record.pending_count ?? 0 }}</template>
              <template v-else-if="column.key === 'op'">
                <a-button type="primary" ghost size="small"
                  :disabled="busy === record.bill_no" @click="claim(record.bill_no)">认领</a-button>
              </template>
            </template>
            <template #emptyText>
              <a-empty :image-style="{ height: '40px' }" description="队列为空" />
            </template>
          </a-table>
            </a-tab-pane>

            <a-tab-pane key="hist" :tab="`已办记录 (${data.history.total})`">
          <a-table :columns="histCols" :data-source="data.history.items"
            :row-key="(r: MyWorkHistory) => r.bill_no + r.decided_at"
            size="small" :pagination="{ pageSize: 10, showTotal: (t: number) => `共 ${t} 条` }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'bill_no'">
                <router-link :to="`/review/bills/${record.bill_no}`">{{ record.bill_no }}</router-link>
                <a-tag v-if="record.is_demo" color="default" style="margin-left: 6px">演示</a-tag>
              </template>
              <template v-else-if="column.dataIndex === 'amount'">{{ fmtAmt(record.amount) }}</template>
              <template v-else-if="column.dataIndex === 'decision'">
                <a-tag :color="DECISION[record.decision]?.color || 'default'">
                  {{ DECISION[record.decision]?.text || record.decision }}
                </a-tag>
              </template>
              <template v-else-if="column.dataIndex === 'decided_at'">{{ dt(record.decided_at) }}</template>
            </template>
            <template #emptyText>
              <a-empty :image-style="{ height: '40px' }" description="暂无复核记录" />
            </template>
          </a-table>
            </a-tab-pane>
          </a-tabs>
        </a-card>
      </template>
    </Async>
  </div>
</template>

<style scoped>
.dim { color: rgba(0, 0, 0, 0.45); font-size: 12px; font-weight: normal; }
/* 与 Dashboard.vue 同款副行：卡片底部 12px 灰字 */
.sub { font-size: 12px; color: rgba(0, 0, 0, 0.45); margin-top: 2px; }
</style>
