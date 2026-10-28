<script setup lang="ts">
/**
 * 用户与角色（2026-10-26 AntD 重写版）：标准企业后台双栏——
 * 左角色列表（选中态），右两个页签：用户账号 / 角色详情（权限树）。
 * 数据层全部走 data/iam.ts（真表真接口），本页只做呈现与交互。
 */
import { computed, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { createVNode } from 'vue'
import { ExclamationCircleOutlined, PlusOutlined } from '@ant-design/icons-vue'
import { api } from '../api'
import * as iam from '../data/iam'
import { ITEM_BY_KEY, ROLE_ORDER } from '../data/navTree'

/* ── 角色选择 ── */
const curRoleKey = ref('')
const roles = iam.roles
const curRole = computed(() => iam.roleByKey(curRoleKey.value))
watch(() => roles().length, () => {
  if (!curRoleKey.value || !curRole.value) {
    curRoleKey.value = roles()[0]?.key || ''
  }
}, { immediate: true })

/* ── 用户页签 ── */
const tab = ref<'users' | 'role'>('users')
const kw = ref('')
const usersOf = (k: string) => iam.usersOfRole(k).filter((u) =>
  !kw.value || u.name.includes(kw.value) || u.account.includes(kw.value) || (u.dept || '').includes(kw.value))
const orgOptions = ref<{ id: string; name: string }[]>([])
api.iamOrgs().then((r) => { orgOptions.value = r.items })
  .catch(() => message.warning('单位清单拉取失败，单位范围暂不可改'))

/* 用户编辑弹窗 */
const uOpen = ref(false)
const uErr = ref('')
const uBusy = ref(false)
const uform = reactive({
  id: '', name: '', account: '', phone: '', dept: '',
  roleKey: '', enabled: true, password: '', orgIds: [] as string[],
})
const editing = computed(() => !!uform.id)
function openUser(u?: iam.IamUser) {
  uErr.value = ''
  uform.id = u?.id || ''
  uform.name = u?.name || ''
  uform.account = u?.account || ''
  uform.phone = u?.phone || ''
  uform.dept = u?.dept || ''
  uform.roleKey = u?.roleKey || curRoleKey.value || 'auditor'
  uform.enabled = u?.enabled ?? true
  uform.password = ''
  uform.orgIds = (u?.orgs || []).map((o) => o.id)
  uOpen.value = true
}
async function submitUser() {
  if (uBusy.value) return
  uBusy.value = true
  try {
    const r = await iam.saveUser({ ...uform, orgs: [...uform.orgIds] })
    if (!r.ok) { uErr.value = r.msg; return }
    uOpen.value = false
    message.success(r.msg)
  } finally {
    uBusy.value = false
  }
}
const noSelf = (u: iam.IamUser) => u.account === 'admin' // 删除护栏由服务端最终判定

async function toggleEnabled(u: iam.IamUser, on: boolean) {
  // a-switch 受控翻转：成功才落状态，失败回弹（iam.refresh 会重拉）
  const r = await iam.setUsersEnabled([u.id], on)
  message[r.ok ? 'success' : 'error'](r.msg)
  await iam.init()
}

/* 重置密码 / 删除 */
const pwdFor = ref('')
const pwd1 = ref('')
const pwdOpen = ref(false)
const pwdBusy = ref(false)
function openPwd(u: iam.IamUser) {
  pwdFor.value = u.id
  pwd1.value = ''
  pwdOpen.value = true
}
async function submitPwd() {
  if (pwdBusy.value) return
  pwdBusy.value = true
  try {
    const r = await iam.resetPassword(pwdFor.value, pwd1.value)
    if (!r.ok) { message.error(r.msg); return }
    pwdOpen.value = false
    message.success(r.msg)
  } finally {
    pwdBusy.value = false
  }
}
function confirmDel(u: iam.IamUser) {
  Modal.confirm({
    title: '删除账号？',
    icon: createVNode(ExclamationCircleOutlined),
    content: `${u.name}（${u.account}）将被删除，该账号的历史留痕保留。`,
    okText: '删除', okType: 'danger', cancelText: '取消',
    onOk: async () => {
      const r = await iam.deleteUsers([u.id])
      message[r.ok ? 'success' : 'error'](r.msg)
    },
  })
}

/* ── 角色页签 ── */
const rform = reactive({ name: '', desc: '', scope: 'all' as iam.DataScope, perms: new Set<string>() })
watch(curRole, (r) => {
  if (!r) return
  rform.name = r.name
  rform.desc = r.desc
  rform.scope = r.scope
  rform.perms = new Set(r.perms)
}, { immediate: true })

/** 权限树：按角色顺序分组展示（平铺 IA 后无父子，用角色序做树形分组便于全选） */
const treeData = computed(() => Object.entries(ROLE_ORDER).map(([rk, keys]) => ({
  title: { auditor: '审核', engineer: '规则', admin: '系统', leader: '领导' }[rk] || rk,
  key: '@role:' + rk,
  children: keys.map((k) => ({ title: ITEM_BY_KEY[k]?.label || k, key: k })),
})))
const checkedKeys = computed({
  get: () => [...rform.perms],
  set: (keys: any) => { rform.perms = new Set((keys.checked || keys).filter((k: string) => !k.startsWith('@role:'))) },
})
const roleBusy = ref(false)
async function saveRole() {
  if (roleBusy.value) return
  roleBusy.value = true
  try {
    const r = await iam.saveRole({
      key: curRoleKey.value, name: rform.name, desc: rform.desc,
      scope: rform.scope, perms: [...rform.perms],
    })
    message[r.ok ? 'success' : 'error'](r.msg)
  } finally {
    roleBusy.value = false
  }
}

/* 新建角色 */
const nOpen = ref(false)
const nErr = ref('')
const nBusy = ref(false)
const nform = reactive({ name: '', desc: '', scope: 'self' as iam.DataScope })
async function submitNewRole() {
  if (nBusy.value) return
  nBusy.value = true
  try {
    const r = await iam.saveRole({ ...nform, perms: [] })
    if (!r.ok) { nErr.value = r.msg; return }
    nOpen.value = false
    message.success(r.msg)
  } finally {
    nBusy.value = false
  }
}

/* 恢复默认 */
function confirmReset() {
  Modal.confirm({
    title: '恢复默认账号与角色？',
    icon: createVNode(ExclamationCircleOutlined),
    content: '自建账号与角色将丢失，密码统一恢复为 demo-1234。',
    okText: '恢复', okType: 'danger', cancelText: '取消',
    onOk: async () => {
      const r = await iam.resetAll()
      message[r.ok ? 'success' : 'error'](r.msg)
      curRoleKey.value = 'auditor'
    },
  })
}

const roleCols = [
  { title: '姓名', dataIndex: 'name', width: 90 },
  { title: '账号', dataIndex: 'account', width: 110 },
  { title: '部门', dataIndex: 'dept', width: 110, ellipsis: true },
  { title: '手机', dataIndex: 'phone', width: 130 },
  { title: '单位范围', key: 'orgs', ellipsis: true },
  { title: '启用', dataIndex: 'enabled', width: 70 },
  { title: '最近登录', dataIndex: 'lastLogin', width: 130 },
  { title: '操作', key: 'op', width: 130 },
]
</script>

<template>
  <div class="users-page">
    <div class="layout">
      <!-- 左：角色列表 -->
      <a-card size="small" class="role-card" title="角色">
        <template #extra>
          <a-button type="link" size="small" @click="nOpen = true"><plus-outlined /> 新建</a-button>
        </template>
        <div class="role-list">
          <div v-for="r in roles()" :key="r.key" class="role-item"
               :class="{ on: r.key === curRoleKey }" @click="curRoleKey = r.key">
            <div class="rname">{{ r.name }} <a-tag v-if="r.builtin" style="margin-left: 4px">内置</a-tag></div>
            <div class="rmeta">{{ r.members }} 人 · {{ r.perms.length }} 个菜单</div>
          </div>
        </div>
        <a-divider style="margin: 8px 0" />
        <a-button block size="small" @click="confirmReset">恢复默认</a-button>
      </a-card>

      <!-- 右：用户/角色详情 -->
      <a-card size="small" class="main-card">
        <a-tabs v-model:active-key="tab">
          <a-tab-pane key="users" :tab="`用户账号 (${iam.usersOfRole(curRoleKey).length})`">
            <div class="toolbar">
              <a-input-search v-model:value="kw" placeholder="姓名 / 账号 / 部门" allow-clear style="width: 220px" />
              <div style="flex: 1"></div>
              <a-button type="primary" @click="openUser()"><plus-outlined /> 新增账号</a-button>
            </div>
            <!-- P0-2：账号清单拉取失败必须说破，不许静默伪装成「该角色没有账号」 -->
            <a-alert v-if="iam.usersError()" type="error" show-icon style="margin-bottom: 12px"
              :message="`账号清单拉取失败：${iam.usersError()}`"
              description="左侧人数来自角色表；账号表数据未取到。请刷新重试。" />
            <a-table v-else :columns="roleCols" :data-source="usersOf(curRoleKey)"
              :row-key="(r: any) => r.account" size="small" :pagination="false"
              :scroll="{ x: 860 }">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'orgs'">
                  <a-tag v-for="o in (record.orgs || []).slice(0, 2)" :key="o.id" style="margin-bottom: 2px">{{ o.name }}</a-tag>
                  <a-tag v-if="(record.orgs || []).length > 2">+{{ record.orgs.length - 2 }}</a-tag>
                  <span v-if="!(record.orgs || []).length" class="dim">全部</span>
                </template>
                <template v-else-if="column.dataIndex === 'enabled'">
                  <a-switch :checked="record.enabled" size="small"
                    :disabled="record.account === 'admin'"
                    @change="(on: any) => toggleEnabled(record, !!on)" />
                </template>
                <template v-else-if="column.dataIndex === 'lastLogin'">
                  <span class="dim">{{ record.lastLogin || '从未登录' }}</span>
                </template>
                <template v-else-if="column.key === 'op'">
                  <a @click="openUser(record)">编辑</a>
                  <a-divider type="vertical" />
                  <a @click="openPwd(record)">重置密码</a>
                  <a-divider type="vertical" />
                  <a-popconfirm title="删除该账号？" ok-text="删除" cancel-text="取消" @confirm="confirmDel(record)">
                    <a style="color: #cf1322">删除</a>
                  </a-popconfirm>
                </template>
              </template>
              <template #emptyText>
                <a-empty :description="curRole && curRole.members > 0
                  ? `角色有 ${curRole.members} 名成员但账号表为空——账号清单未取到，请刷新`
                  : '该角色暂无账号，点右上「新增账号」创建'" />
              </template>
            </a-table>
          </a-tab-pane>

          <a-tab-pane key="role" tab="角色详情">
            <a-form layout="vertical" style="max-width: 560px">
              <a-form-item label="角色名称"><a-input v-model:value="rform.name" /></a-form-item>
              <a-form-item label="角色说明"><a-input v-model:value="rform.desc" /></a-form-item>
              <a-form-item label="数据权限">
                <a-radio-group v-model:value="rform.scope">
                  <a-radio value="all">全部数据</a-radio>
                  <a-radio value="dept">本部门</a-radio>
                  <a-radio value="self">仅本人</a-radio>
                </a-radio-group>
              </a-form-item>
              <a-form-item label="成员">
                <a-tag v-for="u in iam.usersOfRole(curRoleKey)" :key="u.account">{{ u.name }}</a-tag>
              </a-form-item>
            </a-form>
            <a-divider orientation="left" orientation-margin="0">菜单权限</a-divider>
            <p class="dim" style="margin: 4px 0 10px">勾选项即该角色的侧栏菜单与可访问页面。</p>
            <a-tree v-model:checked-keys="checkedKeys" :tree-data="treeData"
              checkable :default-expand-all="true" :selectable="false"
              style="max-width: 480px" />
            <a-divider />
            <a-space>
              <a-button type="primary" :loading="roleBusy" :disabled="curRole?.key === 'admin'" @click="saveRole">保存</a-button>
              <span v-if="curRole?.key === 'admin'" class="dim">系统管理员为内置超管，权限不可修改</span>
            </a-space>
          </a-tab-pane>
        </a-tabs>
      </a-card>
    </div>

    <!-- 新增/编辑用户 -->
    <a-modal v-model:open="uOpen" :title="editing ? '编辑账号' : '新增账号'"
      :ok-text="editing ? '保存' : '新增'" :confirm-loading="uBusy" cancel-text="取消" @ok="submitUser">
      <a-form layout="vertical">
        <a-row :gutter="12">
          <a-col :span="12"><a-form-item label="姓名" required><a-input v-model:value="uform.name" /></a-form-item></a-col>
          <a-col :span="12"><a-form-item label="账号" required>
            <a-input v-model:value="uform.account" :disabled="editing" placeholder="字母开头 3–20 位" />
          </a-form-item></a-col>
          <a-col :span="12"><a-form-item label="手机"><a-input v-model:value="uform.phone" /></a-form-item></a-col>
          <a-col :span="12"><a-form-item label="部门"><a-input v-model:value="uform.dept" /></a-form-item></a-col>
          <a-col :span="12"><a-form-item label="角色" required>
            <a-select v-model:value="uform.roleKey" :options="roles().map((r) => ({ value: r.key, label: r.name }))" />
          </a-form-item></a-col>
          <a-col :span="12"><a-form-item label="状态">
            <a-switch v-model:checked="uform.enabled" checked-children="启用" un-checked-children="停用" />
          </a-form-item></a-col>
          <a-col v-if="!editing" :span="12"><a-form-item label="初始密码" required>
            <a-input-password v-model:value="uform.password" placeholder="至少 6 位" />
          </a-form-item></a-col>
        </a-row>
        <a-form-item label="单位范围（不选=全部单位）">
          <a-select v-model:value="uform.orgIds" mode="multiple" allow-clear
            placeholder="不选即查看全部单位的单据"
            :options="orgOptions.map((o) => ({ value: o.id, label: o.name }))" />
        </a-form-item>
      </a-form>
      <a-alert v-if="uErr" type="error" :message="uErr" style="margin-top: 8px" />
    </a-modal>

    <!-- 重置密码 -->
    <a-modal v-model:open="pwdOpen" title="重置密码" ok-text="重置" :confirm-loading="pwdBusy" cancel-text="取消" @ok="submitPwd">
      <a-input-password v-model:value="pwd1" placeholder="至少 6 位（重置后该账号旧会话全部失效）" />
    </a-modal>

    <!-- 新建角色 -->
    <a-modal v-model:open="nOpen" title="新建角色" ok-text="创建" :confirm-loading="nBusy" cancel-text="取消" @ok="submitNewRole">
      <a-form layout="vertical">
        <a-form-item label="角色名称" required><a-input v-model:value="nform.name" /></a-form-item>
        <a-form-item label="说明"><a-input v-model:value="nform.desc" /></a-form-item>
        <a-form-item label="数据权限">
          <a-radio-group v-model:value="nform.scope">
            <a-radio value="all">全部数据</a-radio>
            <a-radio value="dept">本部门</a-radio>
            <a-radio value="self">仅本人</a-radio>
          </a-radio-group>
        </a-form-item>
      </a-form>
      <a-alert v-if="nErr" type="error" :message="nErr" style="margin-top: 8px" />
    </a-modal>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 16px; align-items: stretch; }
.role-card, .main-card { min-height: calc(100vh - 168px); }
.role-list { display: flex; flex-direction: column; gap: 4px; }
.role-item { padding: 8px 10px; border-radius: 6px; cursor: pointer; border: 1px solid transparent; }
.role-item:hover { background: #f5f5f5; }
.role-item.on { background: #e6f4ff; border-color: #91caff; }
.rname { font-size: 13.5px; font-weight: 500; }
.rmeta { font-size: 12px; color: rgba(0,0,0,.45); margin-top: 2px; }
.toolbar { display: flex; gap: 10px; margin-bottom: 12px; align-items: center; }
.dim { color: rgba(0,0,0,.45); font-size: 12.5px; }
</style>
