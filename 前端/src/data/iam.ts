/**
 * 身份与权限（IAM）——**真表，不再是 localStorage 演示数据**。
 *
 * 数据源：服务端 `sys_role / sys_user / sys_session` 三张表（迁移 0040），
 * 接口在 `app/api/iam.py`。本文件只做两件事：
 *   ① 把接口数据缓存成一个 reactive 对象，供侧栏、路由守卫同步读取；
 *   ② 把增删改包成 {ok, msg}，页面直接弹 msg，不做第二套文案。
 *
 * 页面为什么用同步读 + 异步写：
 *   菜单过滤、`permits()`、登录页角色列表都在渲染期被调用，
 *   不能是 Promise；所以启动时 `init()` 拉一次全量，之后本地改、后台写。
 *   写失败就把服务端的 detail 原样返回，**不吞异常**。
 *
 * 安全边界（这一版终于有了）：
 *   会话是 HttpOnly Cookie，前端拿不到 token；所有 /api/* 由服务端中间件校验，
 *   未登录直接 401（前端统一回登录页）。前端这些判断只是「导航收敛」，
 *   真正的权限判断在服务端 `current_admin()`。
 */
import { reactive } from 'vue'
import { api, ApiError } from '../api'
import { NAV, flatItems } from './navTree'

export type DataScope = 'all' | 'dept' | 'self'
export const SCOPE_CN: Record<DataScope, string> = {
  all: '全部数据', dept: '本部门', self: '仅本人',
}

export interface IamUser {
  id: string
  name: string
  account: string
  /** 展示用掩码（138****6021）；提交时若仍是掩码，服务端保留原值 */
  phone: string
  dept: string
  roleKey: string
  enabled: boolean
  createdAt: string
  lastLogin: string | null
  /** 单位绑定（审核专业组）。空=不限单位看全池 */
  orgs?: { id: string; name: string }[]
}

export interface IamRole {
  key: string
  name: string
  desc: string
  builtin: boolean
  perms: string[]
  scope: DataScope
  home: string
  members: number
  /** 该角色下第一个启用账号（登录页点角色时自动填账号） */
  sample_account?: string
  sample_user?: string
}

const store = reactive<{
  roles: IamRole[]
  users: IamUser[]
  ready: boolean
  error: string
}>({ roles: [], users: [], ready: false, error: '' })

export const roles = () => store.roles
export const users = () => store.users
export const isReady = () => store.ready
export const loadError = () => store.error
export const roleByKey = (k: string) => store.roles.find((r) => r.key === k) || null
export const usersOfRole = (k: string) => store.users.filter((u) => u.roleKey === k)

function msgOf(e: unknown): string {
  if (e instanceof ApiError) return e.detail || `HTTP ${e.status}`
  return e instanceof Error ? e.message : String(e)
}

/** 当前会话是不是管理员（决定启动时要不要拉账号清单；非管理员拉会 403） */
function adminSession(): boolean {
  try {
    const raw = localStorage.getItem('fjhy-session')
    return raw ? (JSON.parse(raw) as { role?: string }).role === 'admin' : false
  } catch {
    return false
  }
}

/**
 * 启动时拉一次全量。**失败不抛**：把错误挂在 store 上，由页面显示，
 * 而不是让整个应用起不来（后端挂了时至少还能看到登录页）。
 */
export async function init(): Promise<void> {
  try {
    const [r, u] = await Promise.all([
      api.iamRoles(),
      adminSession()
        ? api.iamUsers().catch(() => ({ items: [] }))
        : Promise.resolve({ items: [] }),
    ])
    store.roles = r.items.map((x) => ({ ...x }))
    store.users = u.items.map((x) => ({ ...x }))
    store.ready = true
    store.error = ''
  } catch (e) {
    store.error = msgOf(e)
    store.ready = false
  }
}

const refresh = () => init()

/**
 * 某个角色能不能看某个菜单。
 * 系统管理员的「用户与角色」「系统自检」硬放行——否则一次误操作把这两个菜单
 * 关掉，就再没人进得来改回去。服务端还有一层：非 admin 改 IAM 接口直接 403。
 */
export function permits(roleKey: string, menuKey: string): boolean {
  const r = roleByKey(roleKey)
  if (!r) return false
  if (roleKey === 'admin' && (menuKey === 'users' || menuKey === 'startup')) return true
  return r.perms.includes(menuKey)
}

export function scopeOf(roleKey: string): DataScope {
  return roleByKey(roleKey)?.scope || 'self'
}

export function maskPhone(p: string): string {
  const t = (p || '').replace(/\s/g, '')
  if (!t || t.includes('*')) return t || '—'
  if (t.length < 7) return t
  return t.slice(0, 3) + '****' + t.slice(-4)
}

export function menuCount(roleKey: string): number {
  return roleByKey(roleKey)?.perms.length || 0
}

/** 菜单项全集（角色编辑器用；与 navTree 同源） */
export function allMenuKeys(): string[] {
  return NAV.flatMap((g) => flatItems(g.items)).map((it) => it.key)
}

/* ── 用户 ─────────────────────────────────────────────────────────────── */

const ACC_RE = /^[a-zA-Z][a-zA-Z0-9_.-]{2,19}$/

export interface UserInput {
  id?: string
  name: string
  account: string
  phone: string
  dept: string
  roleKey: string
  enabled: boolean
  password?: string
  /** 单位绑定 id 列表（审核专业组）。undefined=本次不改；[]=清空回全池 */
  orgs?: string[]
}

type Ret = { ok: boolean; msg: string }

export async function saveUser(input: UserInput): Promise<Ret> {
  // 前端先做一遍，是为了在弹窗里当场给错误提示；真正的校验在服务端。
  const name = input.name.trim()
  const account = input.account.trim()
  if (!name) return { ok: false, msg: '请填写姓名' }
  if (!input.id && !ACC_RE.test(account)) {
    return { ok: false, msg: '账号需以字母开头，3–20 位字母/数字/._-' }
  }
  try {
    if (input.id) {
      await api.iamUpdateUser(input.id, {
        name, phone: input.phone, dept: input.dept.trim(),
        roleKey: input.roleKey, enabled: input.enabled,
        ...(input.orgs !== undefined ? { orgs: input.orgs } : {}),
      })
      await refresh()
      return { ok: true, msg: `已保存 ${name}` }
    }
    await api.iamCreateUser({
      name, account, phone: input.phone.replace(/\s/g, ''), dept: input.dept.trim(),
      roleKey: input.roleKey, enabled: input.enabled, password: input.password,
      ...(input.orgs !== undefined ? { orgs: input.orgs } : {}),
    })
    await refresh()
    return { ok: true, msg: `已新增 ${name}` }
  } catch (e) {
    return { ok: false, msg: msgOf(e) }
  }
}

export async function deleteUsers(ids: string[]): Promise<Ret> {
  const list = store.users.filter((u) => ids.includes(u.id))
  if (!list.length) return { ok: false, msg: '没有可删除的用户' }
  try {
    for (const u of list) await api.iamDeleteUser(u.account)
    await refresh()
    return { ok: true, msg: `已删除 ${list.length} 个账号` }
  } catch (e) {
    await refresh()
    return { ok: false, msg: msgOf(e) }
  }
}

export async function setUsersEnabled(ids: string[], on: boolean): Promise<Ret> {
  const list = store.users.filter((u) => ids.includes(u.id))
  if (!list.length) return { ok: false, msg: '没有选中用户' }
  try {
    for (const u of list) await api.iamUpdateUser(u.account, { enabled: on })
    await refresh()
    return { ok: true, msg: on ? `已启用 ${list.length} 个账号` : `已停用 ${list.length} 个账号` }
  } catch (e) {
    await refresh()
    return { ok: false, msg: msgOf(e) }
  }
}

export async function resetPassword(id: string, pwd: string): Promise<Ret> {
  const u = store.users.find((x) => x.id === id)
  if (!u) return { ok: false, msg: '用户不存在' }
  if (pwd.length < 6) return { ok: false, msg: '密码至少 6 位' }
  try {
    await api.iamResetPwd(u.account, pwd)
    return { ok: true, msg: `已重置 ${u.name} 的密码，该账号的旧会话已全部失效` }
  } catch (e) {
    return { ok: false, msg: msgOf(e) }
  }
}

/* ── 角色 ─────────────────────────────────────────────────────────────── */

export interface RoleInput {
  key?: string
  name: string
  desc: string
  scope: DataScope
  perms: string[]
}

export async function saveRole(input: RoleInput): Promise<Ret> {
  if (!input.name.trim()) return { ok: false, msg: '请填写角色名称' }
  try {
    if (input.key) {
      await api.iamUpdateRole(input.key, {
        name: input.name, desc: input.desc, scope: input.scope, perms: input.perms,
      })
    } else {
      await api.iamCreateRole({
        name: input.name, desc: input.desc, scope: input.scope, perms: input.perms,
      })
    }
    await refresh()
    return { ok: true, msg: `已保存角色「${input.name}」` }
  } catch (e) {
    return { ok: false, msg: msgOf(e) }
  }
}

export async function deleteRole(key: string): Promise<Ret> {
  const r = roleByKey(key)
  if (!r) return { ok: false, msg: '角色不存在' }
  try {
    await api.iamDeleteRole(key)
    await refresh()
    return { ok: true, msg: `已删除角色「${r.name}」` }
  } catch (e) {
    return { ok: false, msg: msgOf(e) }
  }
}

/** 恢复默认（演示复位）：自建账号与角色会丢，密码统一回到 demo-1234 */
export async function resetAll(): Promise<Ret> {
  try {
    await api.iamReset()
    await refresh()
    return { ok: true, msg: '已恢复默认账号与角色（演示密码 demo-1234）' }
  } catch (e) {
    return { ok: false, msg: msgOf(e) }
  }
}
