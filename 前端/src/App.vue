<script setup lang="ts">
/**
 * 应用外壳（2026-10-24 改版）：左侧品牌+角色菜单+用户区，顶栏页面标题+数据口径+主题切换。
 *
 * 改版契约：设计/审核端改版demo.html（已定稿）。类名走 design.css 的 app-* 体系。
 * 旧外壳（.topbar/.side 白卡片风）随本次替换；旧样式留在 style.css 里给
 * 未改版的页面继续用，不删——删了旧页面立刻裸奔。
 *
 * 角色过滤：菜单按 session 角色过滤（navTree 的 roles 字段）。
 * 这不是安全边界（后端无鉴权），是信息架构收敛——见 session.ts 文件头。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAV, type NavGroup, type NavItem } from './data/navTree'
import { api } from './api'
import { current as curDomain, label as domainLabel, load as loadDomain } from './data/domainState'
import * as iam from './data/iam'
import * as session from './data/session'

const route = useRoute()
const router = useRouter()
const sess = ref(session.current())
// 会话必须跟着路由走：登录页写完 localStorage 才跳转，挂载时读的那份是旧的。
// 不在这里重读，登录后侧栏菜单永远为空——「登录了但没进去」就是这么来的。
watch(() => route.path, () => { sess.value = session.current() })
const banner = ref<{ demo_bills: number; real_bills: number } | null>(null)
const navErr = ref('')
const dark = ref(localStorage.getItem('fjhy-theme') === 'dark')

/** 顶栏「真实单 / 演示单」口径。**要登录后才能取**（/api/* 现在都要会话），
    所以登录页不拉；登录成功、角色变化时再补一次。 */
async function loadBanner() {
  if (!sess.value) return
  try {
    const d = await api.assetNav()
    banner.value = d.banner
    navErr.value = ''
  } catch (e) {
    // 取不到要可见，不能静默成「没有数据」的假象
    navErr.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(() => {
  // 有会话才拉：登录页上拉 /api/domain/state 只会得到 401，
  // 把领域徽标钉在「接口异常」上，登录成功后也没人补它。
  if (sess.value) loadDomain()
  applyTheme()
  void loadBanner()
})

// 登录完成（无会话 → 有会话）时补拉一次：否则顶栏会一直挂着
// 「口径接口异常：HTTP 401」，而那是登录前那一次请求留下的。
// 领域徽标同一个坑：loadDomain 只在挂载时拉，登录前的 401 会让它一直
// 显示「未选领域/接口异常」，直到手动刷新——这里一起补。
watch(() => sess.value?.role, (r) => {
  if (!r) return
  void loadBanner()
  loadDomain() // 失败过时 current 为空，会真正重试；成功过则幂等跳过
})

function applyTheme() {
  document.documentElement.dataset.theme = dark.value ? 'dark' : 'light'
}
function toggleTheme() {
  dark.value = !dark.value
  localStorage.setItem('fjhy-theme', dark.value ? 'dark' : 'light')
  applyTheme()
}

const role = computed(() => sess.value?.role)

/**
 * 窄屏导航（2026-10-25）：≤1080 时左侧栏改成抽屉，顶栏出现汉堡按钮。
 *
 * 为什么不是把侧栏做成图标栏：菜单没有图标，64px 的空栏比抽屉更难用。
 * 收起侧栏能给内容区腾出 216px——1024 宽的屏幕上（1280 笔记本 125% 缩放就是这个宽度）
 * 内容区从 760 变成 992，表格才放得下。路由一跳转就自动收起，
 * 否则点完菜单抽屉还挡在上面。
 */
const navOpen = ref(false)
watch(() => route.path, () => { navOpen.value = false })

/* 分组菜单：组头可点击展开/收起（参照主流管理后台的分组导航）。
   纯标题占一行的旧做法看不出可交互——组头带箭头、点了收起，才像菜单。 */
const openGroups = ref<Set<string>>(new Set(NAV.map((g) => g.key)))
function toggleGroup(key: string) {
  const next = new Set(openGroups.value)
  next.has(key) ? next.delete(key) : next.add(key)
  openGroups.value = next
}
const isGroupOpen = (key: string) => openGroups.value.has(key)
// 路由进了某个组，该组必须展开（收着当前页的组让人迷路）
watch(() => route.path, () => {
  for (const g of groups.value) {
    const hit = g.visible.some((it) => route.path.startsWith(it.path)
      || (it.children || []).some((c) => route.path.startsWith(c.path)))
    if (hit && !openGroups.value.has(g.key)) {
      const next = new Set(openGroups.value); next.add(g.key); openGroups.value = next
    }
  }
})
/**
 * 菜单可见性走 IAM（2026-10-25）：角色权限可在「用户与角色」里改，改完这里立刻生效。
 * **不再看 navTree 的静态 roles**——那份现在只是种子数据；两套判断同时存在，
 * 就会出现「权限里勾了、侧栏还是不显示」这种查半天的问题。
 */
const canSee = (key: string) => (role.value ? iam.permits(role.value, key) : false)
/** 组里只要有一项可见就显示组；子项单独过滤（父项有权限不代表子项也有） */
const groups = computed<(NavGroup & { visible: NavGroup['items'] })[]>(() => {
  if (!role.value) return []
  return NAV
    .map((g) => ({
      ...g,
      visible: g.items.filter(
        (it) => canSee(it.key) || (it.children || []).some((c) => canSee(c.key)),
      ),
    }))
    .filter((g) => g.visible.length > 0)
})
const kids = (it: NavItem) => (it.children || []).filter((c) => canSee(c.key))

const pageTitle = computed(() => {
  for (const g of groups.value) {
    for (const it of g.visible) {
      if (route.path.startsWith(it.path)) return it.label
      for (const c of it.children || []) {
        if (route.path.startsWith(c.path)) return `${it.label} · ${c.label}`
      }
    }
  }
  return sess.value ? `${sess.value.name}工作台` : ''
})

/** 只读角色在对应菜单挂角标 */
function readonlyOf(path: string): boolean {
  return role.value ? session.isReadonly(role.value, path) : false
}

async function logout() {
  // 先等服务端把会话与 Cookie 清掉再跳：反过来会让路由守卫读到「还登录着」，
  // 于是刚点退出又被弹回工作台。
  await session.logout()
  router.push('/login')
}
</script>

<template>
  <!-- 登录页不套外壳：全屏左右分栏自己撑满，顶栏数据口径与空侧栏不出现在登录页 -->
  <div v-if="route.path === '/login'" class="login-fullscreen">
    <router-view />
  </div>

  <div v-else class="app-shell">
    <aside class="app-side" :class="{ open: navOpen }">
      <div class="app-brand">
        <div class="app-mark" aria-hidden="true">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M3 15c2 0 3-1.4 5-1.4S10.6 15 12 15s3-1.4 5-1.4 2.6 1.4 4 1.4"/><path d="M3 19c2 0 3-1.4 5-1.4S10.6 19 12 19s3-1.4 5-1.4 2.6 1.4 4 1.4"/><path d="M12 4v8"/><path d="M8 8l4-4 4 4"/></svg>
        </div>
        <div>
          <div class="nm">福州港口集团</div>
          <div class="sb">差旅费智能审核</div>
        </div>
        <button class="side-x" aria-label="关闭菜单" @click="navOpen = false">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
        </button>
      </div>

      <nav class="app-nav" aria-label="主导航">
        <template v-for="g in groups" :key="g.key">
          <button class="app-nav-group" :aria-expanded="isGroupOpen(g.key)" @click="toggleGroup(g.key)">
            {{ g.label }}
            <svg class="chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" :style="{ transform: isGroupOpen(g.key) ? 'none' : 'rotate(-90deg)' }"><path d="M6 9l6 6 6-6"/></svg>
          </button>
          <template v-if="isGroupOpen(g.key)" v-for="it in g.visible" :key="it.key">
            <router-link :to="it.path" class="app-nav-item"
                         :class="{ on: route.path.startsWith(it.path) }">
              {{ it.label }}
              <span v-if="readonlyOf(it.path)" class="ro">只读</span>
            </router-link>
            <router-link v-for="c in kids(it)" :key="c.key" :to="c.path"
                         class="app-nav-item" style="padding-left: 32px"
                         :class="{ on: route.path.startsWith(c.path) }">
              {{ c.label }}
            </router-link>
          </template>
        </template>
      </nav>

      <div class="app-user" v-if="sess">
        <div class="avatar" aria-hidden="true">{{ sess.user[0] }}</div>
        <div>
          <div class="who">{{ sess.user }}</div>
          <div class="role">{{ sess.name }}</div>
        </div>
        <button class="out" @click="logout">退出</button>
      </div>
    </aside>

    <div v-if="navOpen" class="nav-mask" @click="navOpen = false"></div>

    <div class="app-main">
      <header class="app-topbar">
        <button class="nav-toggle" aria-label="打开菜单" @click="navOpen = true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
        </button>
        <h1>{{ pageTitle }}</h1>
        <div class="grow"></div>
        <span v-if="navErr" class="data-pill" style="color: var(--bad)">
          <i style="background: var(--bad)"></i>口径接口异常：{{ navErr }}
        </span>
        <span v-else-if="banner" class="data-pill">
          <i aria-hidden="true"></i>真实单据 {{ banner.real_bills }} · 演示 {{ banner.demo_bills }}
        </span>
        <button class="icon-btn" @click="toggleTheme" :aria-label="dark ? '切换到浅色' : '切换到深色'">
          <svg v-if="!dark" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M20 14A8 8 0 0 1 10 4a8 8 0 1 0 10 10z"/></svg>
          <svg v-else width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
        </button>
      </header>

      <!-- 领域/实例徽标：领域知识资产组页面专用（沿用原口径） -->
      <div v-if="route.path.startsWith('/asset')" class="app-scopebar">
        <span>{{ domainLabel(curDomain) }}</span>
        <router-link v-if="route.path !== '/asset'" to="/asset" class="scopelink">切换</router-link>
      </div>

      <main class="app-content">
        <router-view />
      </main>
    </div>
  </div>
</template>
