import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import { api } from './api'
import * as iamStore from './data/iam'
import MarkText from './components/MarkText.vue'
import './style.css'
import './design.css'
import * as session from './data/session'

// 用 hash 路由而不是 history 路由：
// POC 要能直接双击 dist/index.html 打开看，history 模式下那样打开会白屏
// （所有子路由都 404），而「本地打开就白屏」会让人以为构建坏了。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    // 登录页不进外壳（App.vue 的侧栏在无会话时是空的，登录页自己全屏）
    { path: '/login', component: () => import('./pages/Login.vue') },
    // 领导总览看板（2026-10-24 新增，角色：领导查询）
    { path: '/dash', component: () => import('./pages/Dashboard.vue') },
    // 领域知识资产的落地页：选领域/实例。**放在 /asset/* 之前无所谓**，
    // vue-router 按最具体的匹配，不是按先后。
    { path: '/asset', component: () => import('./pages/DomainHome.vue') },
    { path: '/asset/docs', component: () => import('./pages/DocLib.vue') },
    { path: '/asset/extract', component: () => import('./pages/Extract.vue') },
    { path: '/asset/workshop', component: () => import('./pages/Workshop.vue') },
    { path: '/asset/biz', component: () => import('./pages/BizOnto.vue') },
    { path: '/asset/rules', component: () => import('./pages/RuleOnto.vue') },
    { path: '/asset/rules/:id', component: () => import('./pages/RuleDetail.vue') },
    { path: '/asset/process', component: () => import('./pages/ProcessOnto.vue') },
    { path: '/asset/releases', component: () => import('./pages/Publish.vue') },
    { path: '/review/ai-review', component: () => import('./pages/AIReview.vue') },
    { path: '/review/bills', component: () => import('./pages/Workbench.vue') },
    { path: '/review/bills/:no', component: () => import('./pages/BillDetail.vue') },
    { path: '/review/report', component: () => import('./pages/ReviewReport.vue') },
    { path: '/review/report/:runId', component: () => import('./pages/ReviewReport.vue') },
    { path: '/review/trace', component: () => import('./pages/AuditTrace.vue') },
    { path: '/review/trace/:no', component: () => import('./pages/AuditTrace.vue') },
    { path: '/review/deviation', component: () => import('./pages/Deviation.vue') },
    { path: '/sys/agents', component: () => import('./pages/AgentHub.vue') },
    { path: '/sys/startup', component: () => import('./pages/Startup.vue') },
    { path: '/sys/llm', component: () => import('./pages/LlmConfig.vue') },
    // 用户与角色（2026-10-24 新增，角色：系统管理员；后端无用户体系，前端演示配置）
    { path: '/sys/users', component: () => import('./pages/Users.vue') },
    // 大模型配置 2026-10-18 从领域知识资产移到系统组。老路径保留，
    // 否则已经发出去的链接会白屏——白屏会被当成构建坏了，而不是「菜单挪了」。
    { path: '/asset/llm', redirect: '/sys/llm' },
    // 根路径：有会话进角色落地页，没会话进登录
    { path: '/', redirect: () => {
      const s = session.current()
      return s ? s.home : '/login'
    } },
    // 未匹配的路径同根路径处理，避免白屏
    { path: '/:rest(.*)', redirect: () => {
      const s = session.current()
      return s ? s.home : '/login'
    } },
  ],
})

/**
 * 角色路由守卫（一期：前端演示级）。
 *
 * 两条规则：
 * 1. 没登录 → 一律去 /login。
 * 2. 登录了但访问的角色不可见路由 → 回自己的落地页。
 *    这不是安全边界（接口不设防，见 session.ts 文件头），是导航兜底——
 *    手敲 URL 不该看到「别的角色的空壳页」。
 */
router.beforeEach((to) => {
  const s = session.current()
  if (to.path === '/login') {
    // 角色被改成「一个菜单都没有」时 home 会退成 /login，此时不能再跳回去，否则死循环
    if (s && s.home !== '/login') return s.home
    return true
  }
  if (!s) return '/login'
  if (s.home === '/login') return '/login'
  const allowed = session.allowedRoles(to.path)
  if (allowed && !allowed.includes(s.role)) return s.home
  return true
})

// **全局注册 MarkText**：
// 18 个页面都要渲染后端文案里的 **加粗**，全局注册省去每个文件各自 import，
// 且类型声明在 global-components.d.ts 里统一维护。
const app = createApp(App)
app.component('MarkText', MarkText)

/**
 * 先确认身份、再拉权限，然后才挂载。
 *
 * **顺序不能反**：菜单与路由守卫都是同步读权限缓存的，缓存空的时候挂载，
 * 第一屏会按「一个菜单都没有」渲染，随后数据到了再跳一次——用户看到的是闪一下。
 * 后端不可用时 iam.init() 只记录错误不抛，页面照常起来（会显示空菜单与错误条）。
 */
async function boot() {
  try {
    session.adopt(await api.authMe())
  } catch {
    session.clearLocal()      // 没会话 / 会话过期：清掉本地身份，路由守卫会送去登录页
  }
  await iamStore.init()
  // **router 必须在权限缓存就绪后再装**：Vue Router 在 use() 时就会跑首次导航，
  // 而守卫要读角色清单；清单还空时 current() 找不到角色，会把刚登录的人踢回登录页。
  app.use(router)
  app.mount('#app')
}
void boot()
