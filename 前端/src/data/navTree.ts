/**
 * 导航树（2026-10-26 IA 重排版，设计/产品企业级化总体规划_v0.1.md §3）。
 *
 * 原则：
 * 1. **每个角色 ≤6 项，一律平铺**——菜单按角色过滤后直接渲染，不再有分组层
 *    （App.vue 侧栏吃 items，不看 groups）。
 * 2. 菜单名 = 角色的工作动词/名词，不是实现层词汇：本体三页退出业务界面
 *    （归系统管理员"知识结构"只读）；本体工场=实施工具同归；智能体→运行监控。
 * 3. key 是权限键（sys_role.perms），改名不改 key；path 改了必须留 redirect。
 *
 * `built` 标记是否已接真实后端；菜单项不带说明文字（侧栏是导航不是文档）。
 */
export interface NavItem {
  key: string
  label: string
  path: string
  built: boolean
  /** 未接时的说明。built=false 必须写，否则不知道缺什么 */
  todo?: string
  /** 可见角色（种子口径；运行态以 sys_role.perms 为准） */
  roles?: string[]
}

/** 把带子项的菜单压平成一串（遍历所有菜单项的地方都要用它） */
export function flatItems(items: NavItem[]): NavItem[] {
  return items
}

export interface NavGroup {
  key: string
  label: string
  items: NavItem[]
  path?: string
  roles?: string[]
}

/** 全部菜单项（唯一事实来源；分组只服务于「按角色过滤」的中间结构） */
const ALL: NavItem[] = [
  // ── 审核岗（auditor）──
  { key: 'mywork', label: '个人工作台', path: '/review/my', built: true, roles: ['auditor'] },
  { key: 'ai-review', label: '智能审核', path: '/review/ai-review', built: true, roles: ['auditor'] },
  { key: 'workbench', label: '审核工作台', path: '/review/bills', built: true, roles: ['auditor', 'leader'] },
  { key: 'report', label: '审核报告', path: '/review/report', built: true, roles: ['auditor'] },
  { key: 'trace', label: '审计追溯', path: '/review/trace', built: true, roles: ['auditor', 'leader', 'engineer'] },
  { key: 'deviation', label: '偏差与回流', path: '/review/deviation', built: true, roles: ['leader'] },

  // ── 规则管理员（engineer）── 四项=四步工作流：传文件→跑AI→审定→发布
  // 制度文件页=文件库+抽取合并（客户待办#8「上传后直接同步抽取」）
  { key: 'doc-lib', label: '制度文件', path: '/asset/docs', built: true, roles: ['engineer'] },
  { key: 'rules', label: '规则库', path: '/asset/rules-lib', built: true, roles: ['engineer'] },
  { key: 'publish', label: '发布管理', path: '/asset/releases', built: true, roles: ['engineer'] },

  // ── 系统管理员（admin）──
  { key: 'users', label: '用户与角色', path: '/sys/users', built: true, roles: ['admin'] },
  { key: 'llm', label: '模型配置', path: '/sys/llm', built: true, roles: ['admin'] },
  { key: 'ops', label: '运行监控', path: '/sys/agents', built: true, roles: ['admin'] },
  { key: 'startup', label: '系统自检', path: '/sys/startup', built: true, roles: ['admin'] },
  // 知识结构=本体三元的技术视图（只读）：本体退出业务界面后的去处，
  // 实施排障要看，不给业务角色
  { key: 'knowstruct', label: '知识结构', path: '/sys/knowstruct', built: true, roles: ['admin'] },
  // 操作日志=M2 管理员新功能：登录/用户角色/发布的动作留痕（只读台账）
  { key: 'oplog', label: '操作日志', path: '/sys/oplog', built: true, roles: ['admin'] },

  // ── 领导（leader）──
  { key: 'dash', label: '总览看板', path: '/dash', built: true, roles: ['leader'] },
]

/** NAV 兼容旧结构（App.vue/groups 遍历用）：单组包全部，不再有分组层 */
export const NAV: NavGroup[] = [
  { key: 'all', label: '功能菜单', items: ALL },
]

/** 角色种子顺序（菜单渲染顺序以此为准） */
export const ROLE_ORDER: Record<string, string[]> = {
  auditor: ['mywork', 'ai-review', 'workbench', 'report', 'trace'],
  engineer: ['doc-lib', 'rules', 'publish', 'trace'],
  admin: ['users', 'llm', 'ops', 'startup', 'knowstruct', 'oplog'],
  leader: ['dash', 'workbench', 'trace', 'deviation'],
}

export const ITEM_BY_KEY: Record<string, NavItem> = Object.fromEntries(
  ALL.map((it) => [it.key, it]),
)
