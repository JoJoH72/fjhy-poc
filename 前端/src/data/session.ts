/**
 * 角色会话。
 *
 * 2026-10-25 起是**真会话**：账号密码由服务端校验（pbkdf2 摘要，迁移 0040），
 * 会话是 HttpOnly Cookie + `sys_session` 表，前端只存「我是谁」（角色 key 与账号）
 * 用于渲染，**不存 token、也不存任何能证明身份的东西**。
 *
 * 前端这些判断（菜单可见性、路由守卫）只负责「导航收敛」：
 * 真正的权限判断在服务端 `/api/*` 中间件与 `current_admin()`，
 * 改 localStorage 只能骗过界面，骗不过接口。
 */
import { NAV, flatItems, ITEM_BY_KEY } from './navTree'
import * as iam from './iam'
import { api, type Me } from '../api'

export type Role = string

export interface RoleDef {
  role: Role
  name: string
  user: string
  account: string
  home: string
}

/** 只读角色对哪些菜单只读（侧栏挂「只读」角标、页面隐藏写操作） */
export const READONLY: Record<string, string[]> = {
  auditor: [],
  engineer: ['/review/trace'],
  admin: [],
  leader: ['/review/bills', '/review/trace', '/review/deviation'],
}

/** 所有角色（IAM 缓存里的全量，登录页与侧栏都用它） */
export function allRoles(): RoleDef[] {
  return iam.roles().map((r) => {
    const primary = iam.usersOfRole(r.key).find((u) => u.enabled)
    return {
      role: r.key,
      name: r.name,
      // 账号清单只有管理员能拉，其他角色用 /api/iam/roles 带回来的示例账号
      user: primary?.name || r.sample_user || r.name,
      account: primary?.account || r.sample_account || '',
      home: r.home || firstHome(r),
    }
  })
}

/** 角色的落地页：配置的 home 若已失去权限，退到第一个有权限的菜单 */
function firstHome(r: iam.IamRole): string {
  for (const it of flatItems(NAV.flatMap((g) => g.items))) {
    if (iam.permits(r.key, it.key)) return it.path
  }
  return '/login'
}

const KEY = 'fjhy-session'

export function current(): RoleDef | null {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return null
    const r = JSON.parse(raw) as { role?: string; account?: string; user?: string }
    if (!r.role) return null
    const def = allRoles().find((x) => x.role === r.role) || null
    if (!def) return null
    // 登录的是「这个账号」，不是「这个角色的示例账号」。
    // allRoles 的 user/account 是角色的示例人（第一个启用账号）——同角色多人时
    // （审核岗 林嘉/陈芳），谁登录页首都显示同一个人，复核署名也会署错人。
    // 登录时把真实账号与姓名存进会话，这里优先用它们。
    if (r.user || r.account) {
      return { ...def, user: r.user || def.user, account: r.account || def.account }
    }
    return def
  } catch {
    return null
  }
}

/** 用 /api/auth/me 的结果恢复本地身份（刷新页面、换标签页时用） */
export function adopt(me: Me): RoleDef {
  localStorage.setItem(KEY, JSON.stringify({ role: me.roleKey, account: me.account, user: me.userName }))
  return {
    role: me.roleKey,
    name: me.roleName,
    user: me.userName,
    account: me.account,
    home: me.home || '/login',
  }
}

export function clearLocal(): void {
  localStorage.removeItem(KEY)
}

export async function login(account: string, password: string): Promise<{ ok: boolean; msg: string; def?: RoleDef }> {
  try {
    const me = await api.authLogin(account, password)
    const def = adopt(me)
    // 登录前拉的是「匿名可见」的角色清单，账号清单没拉；这里补一次，
    // 否则管理员进「用户与角色」会看到空表，直到做一次增删才刷出来。
    await iam.init()
    return { ok: true, msg: '', def }
  } catch (e) {
    return { ok: false, msg: e instanceof Error ? (e as { detail?: string }).detail || e.message : String(e) }
  }
}

export async function logout(): Promise<void> {
  try {
    await api.authLogout()
  } catch {
    // 登出接口失败也要清本地：网络断了不代表「我还想登着」
  }
  clearLocal()
}

export function isReadonly(role: Role, path: string): boolean {
  return (READONLY[role] || []).some((p) => path.startsWith(p))
}

/**
 * 路由权限：返回能访问该路径的角色 key 列表；null = 不拦（没有对应菜单的页面）。
 * 按菜单算而不是写死前缀表——权限可以在页面上改，写死会出现
 * 「侧栏没了但地址栏还能进」。平铺 IA 后无分组，直接按项匹配。
 */
export function allowedRoles(path: string): Role[] | null {
  const seg = '/' + path.split('/').filter(Boolean).slice(0, 3).join('/')
  const items = flatItems(NAV.flatMap((g) => g.items))
  const hit = items
    .filter((it) => seg === it.path || seg.startsWith(it.path + '/'))
    .sort((a, b) => b.path.length - a.path.length)[0]
  if (!hit) return null
  return allRoles().map((r) => r.role).filter((k) => iam.permits(k, hit.key))
}
