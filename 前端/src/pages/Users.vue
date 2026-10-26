<script setup lang="ts">
/**
 * 系统 · 用户与角色（IAM）。
 *
 * 2026-10-25 重做：上一版是「四张角色卡 + 一个账号表 + 一排开关」的看板，
 * 开关点得动、别的一个都改不了，离企业后台差得远。这一版按标准后台做：
 *
 *   用户账号：搜索（姓名/账号/手机/部门）、角色与状态筛选、分页、
 *             新增/编辑/重置密码/停用启用/删除、批量启用停用删除、
 *             删号与停用的三条护栏（不能删自己、不能停自己、至少留一个启用的管理员）。
 *   角色与权限：角色列表 + 角色信息（名称/说明/数据权限/成员）+
 *             按导航分组的菜单权限树（父项联动子项、半选态），保存后
 *             **真的改变该角色的侧栏与路由**——这是这一页唯一有价值的地方。
 *
 * **2026-10-25 起是真功能**：账号、角色、菜单权限都存在服务端三张表
 * （sys_role / sys_user / sys_session，迁移 0040），密码是 pbkdf2 摘要，
 * 会话是 HttpOnly Cookie。本页所有增删改都打接口，刷新浏览器不会回滚；
 * 权限改完立刻影响该角色的侧栏与路由，判断在服务端（非管理员访问 IAM 接口 403）。
 */
import { computed, reactive, ref, watch } from 'vue'
import { NAV, flatItems, type NavGroup, type NavItem } from '../data/navTree'
import * as iam from '../data/iam'

type Tab = 'users' | 'roles'
const tab = ref<Tab>('users')

/* ── 提示 ─────────────────────────────────────────────────────────────── */
const toast = ref('')
const toastBad = ref(false)
let toastTimer: number | undefined
function say(msg: string, bad = false) {
  toast.value = msg
  toastBad.value = bad
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { toast.value = '' }, 2600)
}

/* ══ 用户账号 ═══════════════════════════════════════════════════════════ */
const kw = ref('')
const fRole = ref('')
const fState = ref<'all' | 'on' | 'off'>('all')
const page = ref(1)
const PAGE = 10
const picked = ref<Set<string>>(new Set())

const allUsers = computed(() => iam.users())
const roleName = (k: string) => iam.roleByKey(k)?.name || k
const scopeCn = (k: string) => iam.SCOPE_CN[iam.scopeOf(k)]

const filtered = computed(() => {
  const q = kw.value.trim().toLowerCase()
  return allUsers.value.filter((u) => {
    if (fRole.value && u.roleKey !== fRole.value) return false
    if (fState.value === 'on' && !u.enabled) return false
    if (fState.value === 'off' && u.enabled) return false
    if (!q) return true
    return (
      u.name.toLowerCase().includes(q) ||
      u.account.toLowerCase().includes(q) ||
      u.dept.toLowerCase().includes(q) ||
      u.phone.includes(q)
    )
  })
})
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE)))
const rows = computed(() => filtered.value.slice((page.value - 1) * PAGE, page.value * PAGE))

watch([kw, fRole, fState], () => { page.value = 1; picked.value = new Set() })
watch(pageCount, (n) => { if (page.value > n) page.value = n })

const pageAllChecked = computed(() =>
  rows.value.length > 0 && rows.value.every((u) => picked.value.has(u.id)))
const pageSomeChecked = computed(() =>
  rows.value.some((u) => picked.value.has(u.id)) && !pageAllChecked.value)

function togglePage(on: boolean) {
  const next = new Set(picked.value)
  rows.value.forEach((u) => (on ? next.add(u.id) : next.delete(u.id)))
  picked.value = next
}
function toggleOne(id: string) {
  const next = new Set(picked.value)
  next.has(id) ? next.delete(id) : next.add(id)
  picked.value = next
}
function clearFilter() {
  kw.value = ''
  fRole.value = ''
  fState.value = 'all'
}

/* ── 弹窗 ─────────────────────────────────────────────────────────────── */
type Dlg = '' | 'user' | 'pwd' | 'del' | 'role' | 'delrole' | 'reset'
const dlg = ref<Dlg>('')
const dlgErr = ref('')

const uform = reactive({
  id: '', name: '', account: '', phone: '', dept: '',
  roleKey: 'auditor', enabled: true, password: '',
})
const editing = computed(() => !!uform.id)

function openUser(u?: iam.IamUser) {
  dlgErr.value = ''
  uform.id = u?.id || ''
  uform.name = u?.name || ''
  uform.account = u?.account || ''
  uform.phone = u?.phone || ''
  uform.dept = u?.dept || ''
  uform.roleKey = u?.roleKey || iam.roles()[0]?.key || 'auditor'
  uform.enabled = u?.enabled ?? true
  uform.password = ''
  dlg.value = 'user'
}

async function submitUser() {
  const r = await iam.saveUser({ ...uform })
  if (!r.ok) { dlgErr.value = r.msg; return }
  dlg.value = ''
  say(r.msg)
}

/* 重置密码 */
const pwdId = ref('')
const pwd1 = ref('')
const pwd2 = ref('')
function openPwd(u: iam.IamUser) {
  pwdId.value = u.id
  pwd1.value = ''
  pwd2.value = ''
  dlgErr.value = ''
  dlg.value = 'pwd'
}
async function submitPwd() {
  if (pwd1.value.length < 6) { dlgErr.value = '密码至少 6 位'; return }
  if (pwd1.value !== pwd2.value) { dlgErr.value = '两次输入不一致'; return }
  const r = await iam.resetPassword(pwdId.value, pwd1.value)
  if (!r.ok) { dlgErr.value = r.msg; return }
  dlg.value = ''
  say(r.msg)
}

/* 删除 */
const delIds = ref<string[]>([])
const delNames = computed(() =>
  allUsers.value.filter((u) => delIds.value.includes(u.id)).map((u) => u.name))
function openDel(ids: string[]) {
  if (!ids.length) return
  delIds.value = ids
  dlgErr.value = ''
  dlg.value = 'del'
}
async function submitDel() {
  const r = await iam.deleteUsers(delIds.value)
  if (!r.ok) { dlgErr.value = r.msg; return }
  picked.value = new Set()
  dlg.value = ''
  say(r.msg)
}

async function batchEnable(on: boolean) {
  const r = await iam.setUsersEnabled([...picked.value], on)
  if (!r.ok) { say(r.msg, true); return }
  picked.value = new Set()
  say(r.msg)
}

/** 单行开关：先算好目标状态再改，改完按结果回执——不要在同一个表达式里既改又读 */
async function toggleUser(u: iam.IamUser) {
  const next = !u.enabled
  const r = await iam.setUsersEnabled([u.id], next)
  say(r.ok ? `${u.name} 已${next ? '启用' : '停用'}` : r.msg, !r.ok)
}

/* ══ 角色与权限 ═════════════════════════════════════════════════════════ */
const curRole = ref(iam.roles()[0]?.key || 'auditor')
const cur = computed(() => iam.roleByKey(curRole.value))
const isBuiltinAdmin = computed(() => curRole.value === 'admin')

const rform = reactive({
  name: '', desc: '', scope: 'all' as iam.DataScope, perms: new Set<string>(),
})
const rerr = ref('')

function loadRole(k: string) {
  const r = iam.roleByKey(k)
  if (!r) return
  rform.name = r.name
  rform.desc = r.desc
  rform.scope = r.scope
  rform.perms = new Set(r.perms)
  rerr.value = ''
}
watch(curRole, loadRole, { immediate: true })

const has = (k: string) => rform.perms.has(k)
function setPerm(k: string, on: boolean) {
  const next = new Set(rform.perms)
  on ? next.add(k) : next.delete(k)
  rform.perms = next
}
/** 父项带子项：勾父连子，取消父也连子——否则会出现「有工场没本体」这种半截权限 */
function toggleItem(it: NavItem, on: boolean) {
  const next = new Set(rform.perms)
  on ? next.add(it.key) : next.delete(it.key)
  for (const c of it.children || []) on ? next.add(c.key) : next.delete(c.key)
  rform.perms = next
}
const groupItems = (g: NavGroup) => flatItems(g.items)
const countIn = (g: NavGroup) => groupItems(g).filter((it) => has(it.key)).length
const groupAll = (g: NavGroup) => countIn(g) === groupItems(g).length
const groupPart = (g: NavGroup) => countIn(g) > 0 && !groupAll(g)
function toggleGroup(g: NavGroup, on: boolean) {
  const next = new Set(rform.perms)
  for (const it of groupItems(g)) on ? next.add(it.key) : next.delete(it.key)
  rform.perms = next
}

async function saveRoleForm() {
  const r = await iam.saveRole({
    key: curRole.value,
    name: rform.name,
    desc: rform.desc,
    scope: rform.scope,
    perms: [...rform.perms],
  })
  if (!r.ok) { rerr.value = r.msg; return }
  rerr.value = ''
  say(r.msg)
}

/* 新建角色 */
const nform = reactive({ name: '', desc: '', scope: 'self' as iam.DataScope })
function openRole() {
  nform.name = ''
  nform.desc = ''
  nform.scope = 'self'
  dlgErr.value = ''
  dlg.value = 'role'
}
async function submitRole() {
  const r = await iam.saveRole({ name: nform.name, desc: nform.desc, scope: nform.scope, perms: [] })
  if (!r.ok) { dlgErr.value = r.msg; return }
  // 按名字找回来：列表按创建时间排，新角色在最后，但不要依赖顺序
  const created = iam.roles().find((x) => x.name === nform.name.trim())
  dlg.value = ''
  if (created) curRole.value = created.key
  say(`${r.msg}，勾选菜单权限后保存生效`)
}

async function submitDelRole() {
  const r = await iam.deleteRole(curRole.value)
  if (!r.ok) { dlgErr.value = r.msg; return }
  dlg.value = ''
  const first = iam.roles()[0].key
  curRole.value = first
  loadRole(first)
  say(r.msg)
}

async function submitReset() {
  const r = await iam.resetAll()
  dlg.value = ''
  curRole.value = 'auditor'
  loadRole('auditor')
  say(r.msg)
}

function closeDlg() {
  dlg.value = ''
  dlgErr.value = ''
}
</script>

<template>
  <div class="v2">
    <div class="iam-bar">
      <div class="tabs" role="tablist">
        <button :class="{ on: tab === 'users' }" @click="tab = 'users'">用户账号</button>
        <button :class="{ on: tab === 'roles' }" @click="tab = 'roles'">角色与权限</button>
      </div>
      <div class="grow"></div>
      <span class="iam-note">账号与角色存于服务端数据库（sys_user / sys_role），会话 12 小时过期</span>
    </div>

    <!-- ══ 用户账号 ══════════════════════════════════════════════════════ -->
    <div v-if="tab === 'users'" class="card">
      <div class="tb">
        <div class="search">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.6-3.6"/></svg>
          <input v-model="kw" placeholder="搜索姓名 / 账号 / 手机 / 部门" aria-label="搜索用户">
        </div>
        <select v-model="fRole" class="sel" aria-label="按角色筛选">
          <option value="">全部角色</option>
          <option v-for="r in iam.roles()" :key="r.key" :value="r.key">{{ r.name }}</option>
        </select>
        <select v-model="fState" class="sel" aria-label="按状态筛选">
          <option value="all">全部状态</option>
          <option value="on">已启用</option>
          <option value="off">已停用</option>
        </select>
        <button class="btn ghost" @click="clearFilter">重置</button>
        <div class="grow"></div>
        <button class="btn primary" @click="openUser()">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
          新增用户
        </button>
      </div>

      <!-- 表格外包一层横向滚动：万一列宽还是超了，滚动发生在卡片内，
           不会把整页顶出横向滚动条（1181–1360 那一档就是这么漏的） -->
      <div class="tblwrap">
      <table class="tbl iam-tbl">
        <thead>
          <tr>
            <th class="ckc">
              <input type="checkbox" :checked="pageAllChecked" :indeterminate="pageSomeChecked"
                     aria-label="全选本页" @change="togglePage(($event.target as HTMLInputElement).checked)">
            </th>
            <th style="width: 215px">用户</th>
            <th style="width: 120px" class="c-dept">部门</th>
            <th style="width: 120px" class="c-phone">手机</th>
            <th style="width: 110px">角色</th>
            <th style="width: 100px" class="c-scope">数据权限</th>
            <th style="width: 80px">状态</th>
            <th style="width: 135px" class="c-login">最后登录</th>
            <th class="sp"></th>
            <th style="width: 170px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in rows" :key="u.id" :class="{ off: !u.enabled, sel: picked.has(u.id) }">
            <td class="ckc">
              <input type="checkbox" :checked="picked.has(u.id)" :aria-label="`选择 ${u.name}`"
                     @change="toggleOne(u.id)">
            </td>
            <td>
              <div class="ucell">
                <span class="uav" :class="{ dim: !u.enabled }">{{ u.name.slice(0, 1) }}</span>
                <div class="umeta">
                  <div class="un">{{ u.name }}</div>
                  <div class="ua mono">{{ u.account }}</div>
                </div>
              </div>
            </td>
            <td class="c-dept">{{ u.dept || '—' }}</td>
            <td class="c-phone mono">{{ iam.maskPhone(u.phone) }}</td>
            <td><span class="rchip">{{ roleName(u.roleKey) }}</span></td>
            <td class="c-scope muted">{{ scopeCn(u.roleKey) }}</td>
            <td>
              <button class="switch sm" :class="{ on: u.enabled }" role="switch"
                      :aria-checked="u.enabled" :aria-label="`${u.name} 启用状态`"
                      @click="toggleUser(u)">
                <i></i>
              </button>
            </td>
            <td class="c-login mono muted">{{ u.lastLogin || '从未登录' }}</td>
            <td class="sp"></td>
            <td>
              <div class="ops">
                <button class="lnk" @click="openUser(u)">编辑</button>
                <span class="sep"></span>
                <button class="lnk" @click="openPwd(u)">重置密码</button>
                <span class="sep"></span>
                <button class="lnk bad" @click="openDel([u.id])">删除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      </div>

      <div v-if="!rows.length" class="iam-empty">
        <div class="ie-t">没有匹配的账号</div>
        <div class="ie-d">换个关键词，或清掉筛选条件；也可以直接新增一个。</div>
        <button class="btn ghost" @click="clearFilter">清除筛选</button>
      </div>

      <div class="foot">
        <span class="muted">共 <b class="num">{{ filtered.length }}</b> 个账号，已选 <b class="num">{{ picked.size }}</b></span>
        <div class="grow"></div>
        <button class="pg" :disabled="page <= 1" @click="page--">上一页</button>
        <span class="pgc num">{{ page }} / {{ pageCount }}</span>
        <button class="pg" :disabled="page >= pageCount" @click="page++">下一页</button>
      </div>
    </div>

    <!-- ══ 角色与权限 ═══════════════════════════════════════════════════ -->
    <div v-else class="role-wrap">
      <div class="card rlist">
        <div class="rl-head">
          <span>角色</span>
          <span class="pill num">{{ iam.roles().length }}</span>
          <div class="grow"></div>
          <button class="btn ghost sm" @click="openRole">+ 新建</button>
        </div>
        <div class="rl-body">
          <button v-for="r in iam.roles()" :key="r.key" class="rl-item"
                  :class="{ on: curRole === r.key }" @click="curRole = r.key">
            <div class="rl-name">
              {{ r.name }}
              <span v-if="r.builtin" class="rl-tag">内置</span>
            </div>
            <div class="rl-meta">
              <span class="num">{{ iam.usersOfRole(r.key).length }}</span> 人 ·
              <span class="num">{{ r.perms.length }}</span> 个菜单
            </div>
            <!-- 数据库里每个角色都存着 description；之前只在编辑弹窗里能看到，
                 角色列表上不放，用户看着四个角色名不知道谁该用哪个 -->
            <div class="rl-desc">{{ r.desc }}</div>
          </button>
        </div>
        <div class="rl-foot">
          <button class="lnk" @click="dlg = 'reset'">恢复默认账号与角色</button>
        </div>
      </div>

      <div class="card rmain">
        <div class="card-head">
          <h2>{{ rform.name || '角色' }}</h2>
          <span v-if="isBuiltinAdmin" class="tag t-na">内置超管 · 权限只读</span>
          <span v-else-if="rform.perms.size === 0" class="tag t-hit">未配置菜单</span>
          <div class="grow"></div>
          <button v-if="!cur?.builtin" class="btn ghost" @click="dlg = 'delrole'">删除角色</button>
          <button class="btn primary" :disabled="isBuiltinAdmin" @click="saveRoleForm">保存</button>
        </div>

        <div class="rform">
          <div class="fr">
            <label>角色名称</label>
            <input v-model="rform.name" :disabled="isBuiltinAdmin" placeholder="如：财务主管">
          </div>
          <div class="fr">
            <label>角色说明</label>
            <input v-model="rform.desc" :disabled="isBuiltinAdmin" placeholder="一句话说明负责什么">
          </div>
          <div class="fr">
            <label>数据权限</label>
            <select v-model="rform.scope" :disabled="isBuiltinAdmin" class="sel">
              <option value="all">全部数据</option>
              <option value="dept">本部门</option>
              <option value="self">仅本人</option>
            </select>
            <span class="fhint">该角色下所有账号统一按此范围取数</span>
          </div>
          <div class="fr">
            <label>成员</label>
            <div class="members">
              <span v-for="u in iam.usersOfRole(curRole)" :key="u.id" class="mchip">{{ u.name }}</span>
              <span v-if="!iam.usersOfRole(curRole).length" class="muted">暂无账号使用该角色</span>
            </div>
          </div>
        </div>

        <div class="perm-head">
          <h3>菜单权限</h3>
          <span class="phint">勾选即生效：该角色的侧栏菜单与直达链接同时受控；取消父项会连带取消子项</span>
        </div>

        <div class="perm">
          <div v-for="g in NAV" :key="g.key" class="pgroup">
            <label class="pgh">
              <input type="checkbox" :checked="groupAll(g)" :indeterminate="groupPart(g)"
                     :disabled="isBuiltinAdmin"
                     @change="toggleGroup(g, ($event.target as HTMLInputElement).checked)">
              <span class="pgn">{{ g.label }}</span>
              <span class="pgc num">{{ countIn(g) }}/{{ groupItems(g).length }}</span>
            </label>
            <div class="pitems">
              <div v-for="it in g.items" :key="it.key" class="pw">
                <label class="pitem">
                  <input type="checkbox" :checked="has(it.key)" :disabled="isBuiltinAdmin"
                         @change="toggleItem(it, ($event.target as HTMLInputElement).checked)">
                  {{ it.label }}
                </label>
                <div v-if="(it.children || []).length" class="pchildren">
                  <label v-for="c in it.children || []" :key="c.key" class="pitem sub">
                    <input type="checkbox" :checked="has(c.key)" :disabled="isBuiltinAdmin"
                           @change="setPerm(c.key, ($event.target as HTMLInputElement).checked)">
                    {{ c.label }}
                  </label>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="rerr" class="err" style="margin: 0 20px 16px">{{ rerr }}</div>
        <div v-if="isBuiltinAdmin" class="note" style="margin: 0 20px 16px">
          系统管理员是内置超级管理员，权限不可修改——否则一次误操作就能把「用户与角色」
          自己关掉，从此没人改得回来。需要受限的管理账号，请新建一个角色再分配菜单。
        </div>

        <!-- 权限树很长，滚到底还要回到卡头保存太远：底部再放一条操作条 -->
        <div class="rmain-foot">
          <span class="muted">已勾选 <b class="num">{{ rform.perms.size }}</b> 个菜单</span>
          <div class="grow"></div>
          <button v-if="!cur?.builtin" class="btn ghost" @click="dlg = 'delrole'">删除角色</button>
          <button class="btn primary" :disabled="isBuiltinAdmin" @click="saveRoleForm">保存</button>
        </div>
      </div>
    </div>

    <!-- 批量条 -->
    <div class="batch" :class="{ show: picked.size > 0 && tab === 'users' }">
      <span>已选 <b class="num">{{ picked.size }}</b> 个账号</span>
      <button class="btn" @click="batchEnable(true)">启用</button>
      <button class="btn" @click="batchEnable(false)">停用</button>
      <button class="btn danger" @click="openDel([...picked])">删除</button>
      <button class="btn" @click="picked = new Set()">取消</button>
    </div>

    <!-- ══ 弹窗 ═════════════════════════════════════════════════════════ -->
    <div v-if="dlg" class="mask show" @click.self="closeDlg">
      <div class="modal iam-modal" role="dialog" aria-modal="true">
        <!-- 新增 / 编辑用户 -->
        <template v-if="dlg === 'user'">
          <h3>{{ editing ? '编辑用户' : '新增用户' }}</h3>
          <div class="msub">{{ editing ? '账号创建后不可修改，其余信息可随时调整' : '账号用于登录，创建后不可修改' }}</div>
          <div class="mgrid">
            <div class="mf"><label>姓名 <i>*</i></label><input v-model="uform.name" placeholder="如：张三"></div>
            <div class="mf"><label>账号 <i>*</i></label>
              <input v-model="uform.account" :disabled="editing" placeholder="字母开头，3–20 位" class="mono">
            </div>
            <div class="mf"><label>手机</label><input v-model="uform.phone" placeholder="选填"></div>
            <div class="mf"><label>部门</label><input v-model="uform.dept" placeholder="如：单据审核部"></div>
            <div class="mf"><label>角色 <i>*</i></label>
              <select v-model="uform.roleKey" class="sel">
                <option v-for="r in iam.roles()" :key="r.key" :value="r.key">{{ r.name }}</option>
              </select>
            </div>
            <div class="mf"><label>状态</label>
              <label class="ckline"><input type="checkbox" v-model="uform.enabled"> 启用该账号</label>
            </div>
            <div v-if="!editing" class="mf span2"><label>初始密码 <i>*</i></label>
              <input v-model="uform.password" type="password" placeholder="至少 6 位" autocomplete="new-password">
            </div>
          </div>
          <div v-if="dlgErr" class="merr">{{ dlgErr }}</div>
          <div class="mops">
            <button class="btn ghost" @click="closeDlg">取消</button>
            <button class="btn primary" @click="submitUser">保存</button>
          </div>
        </template>

        <!-- 重置密码 -->
        <template v-else-if="dlg === 'pwd'">
          <h3>重置密码</h3>
          <div class="msub">演示环境不落库，接后端后这里会走 bcrypt 摘要写入。</div>
          <div class="mgrid">
            <div class="mf span2"><label>新密码 <i>*</i></label>
              <input v-model="pwd1" type="password" placeholder="至少 6 位" autocomplete="new-password">
            </div>
            <div class="mf span2"><label>确认新密码 <i>*</i></label>
              <input v-model="pwd2" type="password" autocomplete="new-password">
            </div>
          </div>
          <div v-if="dlgErr" class="merr">{{ dlgErr }}</div>
          <div class="mops">
            <button class="btn ghost" @click="closeDlg">取消</button>
            <button class="btn primary" @click="submitPwd">确认重置</button>
          </div>
        </template>

        <!-- 删除用户 -->
        <template v-else-if="dlg === 'del'">
          <h3>删除账号</h3>
          <div class="msub">
            将删除 <b>{{ delNames.length }}</b> 个账号：{{ delNames.join('、') }}。
            账号删除后不可恢复；如果只是暂时停用，请改用「停用」。
          </div>
          <div v-if="dlgErr" class="merr">{{ dlgErr }}</div>
          <div class="mops">
            <button class="btn ghost" @click="closeDlg">取消</button>
            <button class="btn reject" @click="submitDel">确认删除</button>
          </div>
        </template>

        <!-- 新建角色 -->
        <template v-else-if="dlg === 'role'">
          <h3>新建角色</h3>
          <div class="msub">先建角色，再在右侧勾菜单权限；账号在「新增用户」里选它。</div>
          <div class="mgrid">
            <div class="mf span2"><label>角色名称 <i>*</i></label>
              <input v-model="nform.name" placeholder="如：财务主管">
            </div>
            <div class="mf span2"><label>角色说明</label>
              <input v-model="nform.desc" placeholder="一句话说明负责什么">
            </div>
            <div class="mf span2"><label>数据权限</label>
              <select v-model="nform.scope" class="sel">
                <option value="all">全部数据</option>
                <option value="dept">本部门</option>
                <option value="self">仅本人</option>
              </select>
            </div>
          </div>
          <div v-if="dlgErr" class="merr">{{ dlgErr }}</div>
          <div class="mops">
            <button class="btn ghost" @click="closeDlg">取消</button>
            <button class="btn primary" @click="submitRole">创建</button>
          </div>
        </template>

        <!-- 删除角色 -->
        <template v-else-if="dlg === 'delrole'">
          <h3>删除角色「{{ rform.name }}」</h3>
          <div class="msub">只有没有成员的自定义角色才能删除。成员先改到别的角色。</div>
          <div v-if="dlgErr" class="merr">{{ dlgErr }}</div>
          <div class="mops">
            <button class="btn ghost" @click="closeDlg">取消</button>
            <button class="btn reject" @click="submitDelRole">确认删除</button>
          </div>
        </template>

        <!-- 恢复默认 -->
        <template v-else-if="dlg === 'reset'">
          <h3>恢复默认配置</h3>
          <div class="msub">
            会把账号、角色与菜单权限全部恢复成出厂状态，你新增的账号和角色会丢失。
          </div>
          <div class="mops">
            <button class="btn ghost" @click="closeDlg">取消</button>
            <button class="btn reject" @click="submitReset">恢复默认</button>
          </div>
        </template>
      </div>
    </div>

    <div class="iam-toast" :class="{ show: !!toast, bad: toastBad }">{{ toast }}</div>
  </div>
</template>

<style scoped>
/* 类名统一 iam-/r-/m- 前缀：design.css 的 .v2 .qbar/.ck 等短名会串样式 */
.iam-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.iam-bar .tabs { margin-bottom: 0; }
.iam-note { font-size: 11.5px; color: var(--weak); }
.muted { color: var(--muted); }
.mono { font-family: Consolas, 'Courier New', monospace; }

/* ── 工具条 ── */
.tb { display: flex; align-items: center; gap: 10px; padding: 14px 20px; border-bottom: 1px solid var(--panel-2); flex-wrap: wrap; }
.search {
  display: flex; align-items: center; gap: 8px; height: 32px; padding: 0 10px;
  border: 1px solid var(--line); border-radius: var(--r-s); background: var(--panel);
  color: var(--weak); min-width: 260px; flex: 0 1 300px;
}
.search:focus-within { border-color: var(--brand); box-shadow: var(--ring); }
.search input { flex: 1; min-width: 0; border: 0; outline: none; background: none; color: var(--text); font-size: 13px; height: 30px; }
.sel { height: 32px; padding: 0 10px; border: 1px solid var(--line); border-radius: var(--r-s); background: var(--panel); color: var(--text); font-size: 13px; }

/* ── 表格 ── */
.tblwrap { overflow-x: auto; }
.iam-tbl { table-layout: fixed; width: 100%; }
.iam-tbl th.ckc, .iam-tbl td.ckc { width: 44px; padding-left: 16px; padding-right: 0; }
/* 弹性空列：窄屏隐藏了手机/部门/登录列之后，多出来的宽度全给它，
   右侧「操作」列才能贴住表格右边，而不是跟状态列一起被拉开 */
.iam-tbl th.sp, .iam-tbl td.sp { width: auto; padding: 0; }
.iam-tbl tbody tr.off td { color: var(--weak); }
.iam-tbl tbody tr.off .uav { opacity: .55; }
/* 选中行给底色：只靠勾选框，滚到下面一行就看不出这行已经被选了 */
.iam-tbl tbody tr.sel td { background: var(--brand-bg); }
.iam-tbl tbody tr.sel:hover td { background: var(--brand-bg); }
.ucell { display: flex; align-items: center; gap: 10px; min-width: 0; }
.uav {
  width: 28px; height: 28px; border-radius: 50%; flex: none;
  background: var(--brand-bg); color: var(--brand);
  display: grid; place-items: center; font-size: 12px; font-weight: 600;
}
.umeta { min-width: 0; }
.un { font-size: 13px; color: var(--text); line-height: 1.3; }
.ua { font-size: 11.5px; color: var(--weak); line-height: 1.4; }
.rchip { font-size: 12px; color: var(--brand); background: var(--brand-bg); border-radius: 4px; padding: 1px 8px; white-space: nowrap; }

/* 开关：36×20，比全局 .switch 小一档，表格里不抢视线 */
.switch.sm { width: 36px; min-height: 20px; height: 20px; border-radius: 10px; border: 0; padding: 0; background: var(--line-strong); position: relative; cursor: pointer; }
.switch.sm i { position: absolute; left: 2px; top: 2px; width: 16px; height: 16px; border-radius: 50%; background: #fff; transition: transform .15s; box-shadow: 0 1px 2px rgba(0,0,0,.25); }
.switch.sm.on { background: var(--ok); }
.switch.sm.on i { transform: translateX(16px); }

.ops { display: flex; align-items: center; gap: 6px; }
.sep { width: 1px; height: 12px; background: var(--line); }
.lnk {
  border: 0; background: none; padding: 0; min-height: 0; height: auto;
  font-size: 12.5px; color: var(--brand); cursor: pointer;
}
.lnk:hover { color: var(--brand-light, var(--brand)); text-decoration: underline; background: none; border: 0; }
.lnk.bad { color: var(--bad); }
.lnk.bad:hover { color: var(--bad); }

.iam-empty { padding: 40px 20px 44px; text-align: center; }
.ie-t { font-size: 14px; font-weight: 600; color: var(--text); }
.ie-d { font-size: 12.5px; color: var(--weak); margin: 6px 0 14px; }

.foot { display: flex; align-items: center; gap: 10px; padding: 12px 20px; border-top: 1px solid var(--panel-2); font-size: 12.5px; }
.foot b { color: var(--text); font-weight: 600; }
.pg { height: 28px; padding: 0 12px; font-size: 12.5px; }
.pgc { font-size: 12.5px; color: var(--muted); }

/* ── 角色与权限 ── */
.role-wrap { display: grid; grid-template-columns: 268px minmax(0, 1fr); gap: 16px; align-items: start; }
.rlist { overflow: hidden; }
.rl-head { display: flex; align-items: center; gap: 8px; padding: 14px 16px; border-bottom: 1px solid var(--panel-2); font-size: 13px; font-weight: 600; color: var(--text); }
.rl-body { padding: 6px; }
.rl-item {
  display: block; width: 100%; text-align: left; padding: 10px 12px; border-radius: var(--r-s);
  border: 1px solid transparent; background: none; cursor: pointer; position: relative;
}
.rl-item:hover { background: var(--panel-2); }
.rl-item.on { background: var(--brand-bg); border-color: var(--brand-line); }
.rl-item.on::before { content: ''; position: absolute; left: 0; top: 10px; bottom: 10px; width: 3px; border-radius: 2px; background: var(--brand); }
.rl-name { font-size: 13px; color: var(--text); display: flex; align-items: center; gap: 6px; }
.rl-item.on .rl-name { color: var(--brand); font-weight: 600; }
.rl-tag { font-size: 10px; color: var(--weak); border: 1px solid var(--line); border-radius: 3px; padding: 0 4px; line-height: 14px; }
.rl-meta { font-size: 11.5px; color: var(--weak); margin-top: 3px; }
.rl-desc { font-size: 11.5px; color: var(--muted); margin-top: 4px; line-height: 1.5; }
.rl-foot { padding: 10px 16px 14px; border-top: 1px solid var(--panel-2); }

.rform { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 20px; padding: 16px 20px 6px; }
.fr { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.fr label { font-size: 12px; color: var(--weak); }
.fr input, .fr select { height: 32px; padding: 0 10px; border: 1px solid var(--line); border-radius: var(--r-s); background: var(--panel); color: var(--text); font-size: 13px; width: 100%; }
.fr input:disabled { color: var(--weak); background: var(--panel-2); }
.fhint { font-size: 11px; color: var(--weak); }
.members { display: flex; gap: 6px; flex-wrap: wrap; min-height: 32px; align-items: center; }
.mchip { font-size: 12px; padding: 2px 8px; border-radius: 999px; background: var(--panel-2); color: var(--muted); }

.perm-head { display: flex; align-items: baseline; gap: 10px; padding: 10px 20px 8px; border-top: 1px solid var(--panel-2); margin-top: 12px; }
.perm-head h3 { font-size: 13px; font-weight: 600; color: var(--text); margin: 0; }
.phint { font-size: 11.5px; color: var(--weak); }
.rmain-foot { display: flex; align-items: center; gap: 10px; padding: 12px 20px; border-top: 1px solid var(--panel-2); font-size: 12.5px; }
.rmain-foot b { color: var(--text); font-weight: 600; }
/* 权限树单列排：两列时「本体工场」这一块高，右边的项会被挤到下一行行首，
   看起来像是挂在隔壁组下面。单列一眼从上到下，组与组的边界也清楚。 */
.perm { padding: 0 20px 20px; display: grid; grid-template-columns: 1fr; gap: 10px; align-items: start; }
/* 每组一张小卡：两列并排时没有边框，组头和别组的项会连成一片，读起来像同一个组 */
.pgroup { padding: 10px 12px 8px; border: 1px solid var(--line); border-radius: var(--r-s); background: var(--panel-2); min-width: 0; }
.pgh { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text); font-weight: 600; padding: 2px 0 6px; cursor: pointer; }
.pgh input, .pitem input { width: 14px; height: 14px; accent-color: var(--brand); flex: none; }
.pgc { margin-left: auto; font-size: 11px; color: var(--weak); font-weight: 400; }
/* 父项与它的子项是一个竖排单元：子项紧贴父项下方并加一条虚线，避免和旁边的一级菜单混成一行 */
.pitems { display: flex; flex-wrap: wrap; gap: 4px 20px; padding-left: 22px; align-items: flex-start; }
.pw { min-width: 0; }
.pchildren { display: flex; flex-direction: column; gap: 1px; margin: 2px 0 0 6px; padding-left: 10px; border-left: 1px dashed var(--line); }
.pitem { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--muted); cursor: pointer; padding: 2px 0; white-space: nowrap; }
.pitem.sub { color: var(--weak); }

/* ── 批量条 ── */
.batch {
  position: fixed; left: 50%; bottom: 24px; transform: translate(-50%, 80px); opacity: 0;
  display: flex; align-items: center; gap: 10px; padding: 8px 10px 8px 18px;
  background: #1b2532; color: #fff; border-radius: var(--r-m); box-shadow: var(--shadow-2);
  transition: transform .2s, opacity .2s; z-index: 30; pointer-events: none;
}
.batch.show { transform: translate(-50%, 0); opacity: 1; pointer-events: auto; }
.batch span { font-size: 12.5px; }
.batch b { color: var(--gold); margin: 0 2px; }
.batch .btn { height: 28px; padding: 0 12px; background: transparent; color: #fff; border-color: rgba(255,255,255,.28); font-size: 12.5px; }
.batch .btn:hover { border-color: #fff; color: #fff; background: transparent; }
.batch .btn.danger { color: #ffb4a4; border-color: rgba(255,180,164,.4); }

/* ── 弹窗 ── */
.iam-modal { width: 560px; max-width: 92vw; }
.iam-modal h3 { font-size: 16px; margin: 0 0 4px; }
.msub { font-size: 12.5px; color: var(--muted); line-height: 1.65; margin-bottom: 16px; }
.mgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.mf { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.mf.span2 { grid-column: span 2; }
.mf label { font-size: 12px; color: var(--weak); }
.mf label i { color: var(--bad); font-style: normal; }
.mf input, .mf select { height: 32px; padding: 0 10px; border: 1px solid var(--line); border-radius: var(--r-s); background: var(--panel); color: var(--text); font-size: 13px; width: 100%; }
.mf input:disabled { color: var(--weak); background: var(--panel-2); }
.mf input:focus, .mf select:focus { outline: none; border-color: var(--brand); box-shadow: var(--ring); }
.ckline { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); height: 32px; }
.ckline input { width: 15px; height: 15px; accent-color: var(--brand); }
.merr { margin-top: 12px; padding: 8px 12px; border-radius: var(--r-s); background: var(--bad-bg); color: var(--bad); font-size: 12.5px; }
.mops { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }

/* 深色下把「主色文字」提亮一档：--brand 是被契约测试钉死的深蓝 (#015293)，
   深蓝落在 #151c26 的卡片上等于没有颜色——链接、角色标签、头像字全会糊掉。
   只在本页覆盖，不动全局变量。 */
[data-theme='dark'] .rchip,
[data-theme='dark'] .uav,
[data-theme='dark'] .lnk,
[data-theme='dark'] .rl-item.on .rl-name,
[data-theme='dark'] .tabs button.on { color: #7cb2f0; }
[data-theme='dark'] .lnk.bad { color: #ef8a76; }
[data-theme='dark'] .iam-note,
[data-theme='dark'] .phint { color: var(--weak); }

.iam-toast {
  position: fixed; top: 64px; left: 50%; transform: translate(-50%, -12px); opacity: 0;
  background: #1b2532; color: #fff; font-size: 13px; padding: 8px 20px; border-radius: 999px;
  pointer-events: none; transition: opacity .2s, transform .2s; z-index: 60; box-shadow: var(--shadow-2);
}
.iam-toast.show { opacity: 1; transform: translate(-50%, 0); }
.iam-toast.bad { background: var(--bad); }

/* 列宽总和必须小于内容区，否则整页出横向滚动条（1181–1360 那一档漏过一次）。
   收列的优先级：数据权限 > 最后登录 > 手机；部门一直留到最后。
   最后一列的 135px 是「2026-10-24 09:12」实测宽度 + 内边距，再窄就会截成省略号，
   所以到 1400 就整列收掉，而不是留着半截。 */
@media (max-width: 1400px) {
  .c-scope, .c-login { display: none; }
}
@media (max-width: 1180px) {
  .c-phone { display: none; }
  .role-wrap { grid-template-columns: 1fr; }
  /* 角色列表横过来两列：竖排四个角色会把表单挤到首屏之外 */
  .rl-body { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 6px; }
  .rl-item { border-color: var(--line); }
  .perm { grid-template-columns: 1fr; }
  .rform { grid-template-columns: 1fr; }
}
/* 状态（第 7 列）与操作（第 10 列）居中；其余左对齐 */
.iam-tbl th:nth-child(7), .iam-tbl td:nth-child(7),
.iam-tbl th:nth-child(10), .iam-tbl td:nth-child(10) { text-align: center; }
</style>
