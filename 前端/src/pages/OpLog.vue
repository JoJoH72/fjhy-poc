<script setup lang="ts">
/**
 * 系统管理员 · 操作日志（2026-10-27 M2）。
 *
 * 谁、何时、对什么做了什么——动作必留痕的查询面。
 * 台账只增不减（服务端只写 INSERT），这里只读 + 服务端分页：
 * 列表页一页 20 条在屏内，翻页解决剩余，不整表拉到前端。
 *
 * 工具栏三件：分类（接口带计数）/ 关键字（回车触发）/ 刷新。
 */
import { ref } from 'vue'
import Async from '../components/Async.vue'
import { api } from '../api'
import type { OpLogPage } from '../api'
import * as iam from '../data/iam'

/** 提交用的查询状态（回车/点查询才变） */
const category = ref('')
const kw = ref('')
const committedKw = ref('')
const page = ref(1)
const PAGE_SIZE = 20

/** 分类中文（接口 categories 也带 label，这里用于表格列内联显示） */
const CAT_COLOR: Record<string, string> = {
  login: 'blue', iam: 'purple', publish: 'gold', review: 'cyan', rule: 'volcano',
}

/** 角色中文（P0-4）：后端落的是 role_key（admin/engineer），界面不许露英文键——
 *  从 IAM 缓存取角色名；缓存没有的（角色被删）原样显示留排障线索 */
function roleCn(key: string): string {
  if (!key) return ''
  return iam.roleByKey(key)?.name || key
}

function load(): Promise<OpLogPage> {
  return api.opLog({
    category: category.value || undefined,
    q: committedKw.value || undefined,
    page: page.value,
    size: PAGE_SIZE,
  })
}

function search() {
  committedKw.value = kw.value.trim()
  page.value = 1
}

function reset() {
  category.value = ''
  kw.value = ''
  committedKw.value = ''
  page.value = 1
}
</script>

<template>
  <div class="oplog">
    <Async :load="load" :deps="[category, committedKw, page]" v-slot="{ data, reload }">
      <a-card size="small">
        <!-- 工具栏：分类（带计数）+ 关键字 + 刷新 -->
        <div class="toolbar">
          <a-select v-model:value="category" style="width: 190px"
            placeholder="全部分类" allow-clear @change="page = 1">
            <a-select-option v-for="c in data.categories" :key="c.key" :value="c.key">
              {{ c.label }}（{{ c.n }}）
            </a-select-option>
          </a-select>
          <a-input-search v-model:value="kw" style="width: 260px"
            placeholder="账号 / 动作 / 对象 / 说明" allow-clear
            @search="search" @keyup.enter="search" />
          <a-button @click="reload">刷新</a-button>
          <span class="grow"></span>
          <span class="dim">共 {{ data.total }} 条</span>
        </div>

        <a-table v-if="data.items.length"
          :data-source="data.items" row-key="id" size="small"
          :pagination="{
            current: page,
            pageSize: PAGE_SIZE,
            total: data.total,
            showSizeChanger: false,
            onChange: (p: number) => { page = p },
          }"
          :scroll="{ x: 1100 }"
          :columns="[
            { title: '时间', dataIndex: 'at', width: 160 },
            { title: '操作人', key: 'actor', width: 130 },
            { title: '角色', key: 'role', width: 110 },
            { title: '动作', dataIndex: 'action', width: 110 },
            { title: '对象', dataIndex: 'target', width: 190, ellipsis: true },
            { title: '结果', key: 'ok', width: 80, align: 'center' },
            { title: '说明', key: 'detail', ellipsis: true },
          ]">
          <template #bodyCell="{ column, record: r }">
            <template v-if="column.key === 'actor'">
              {{ r.actor_name }}
              <div class="dim sub2">{{ r.actor }}</div>
            </template>
            <template v-else-if="column.key === 'role'">
              <a-tooltip v-if="r.role" :title="r.role">
                <a-tag :color="CAT_COLOR[r.category] || 'default'">{{ roleCn(r.role) }}</a-tag>
              </a-tooltip>
              <span v-else class="dim">—</span>
            </template>
            <template v-else-if="column.key === 'ok'">
              <a-tag :color="r.ok ? 'success' : 'error'">{{ r.ok ? '成功' : '失败' }}</a-tag>
            </template>
            <template v-else-if="column.key === 'detail'">
              <span class="dim">{{ r.detail || '—' }}</span>
            </template>
            <template v-else-if="column.dataIndex === 'target'">
              <span v-if="r.target">{{ r.target }}</span>
              <span v-else class="dim">—</span>
            </template>
          </template>
        </a-table>
        <a-empty v-else :image-style="{ height: '40px' }" description="暂无操作记录" />
      </a-card>
    </Async>
  </div>
</template>

<script lang="ts">
export default { name: 'OpLog' }
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.grow { flex: 1; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .5); }
.sub2 { font-size: 11px; margin-top: 2px; }
</style>
