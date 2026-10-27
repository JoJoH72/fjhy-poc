<script setup lang="ts">
/**
 * 应用外壳（2026-10-26 全站 Ant Design Vue 换装）：
 * a-layout 深色侧栏 + 顶栏 + 内容区。菜单/权限/会话逻辑与旧壳一致，
 * 只是呈现层从手写卡片（design.css 的 app-* 体系）换成业内标准后台语言。
 *
 * 角色过滤走 IAM（动态 perms），这是导航收敛；权限边界在服务端。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { FundOutlined } from '@ant-design/icons-vue'
import { ITEM_BY_KEY, ROLE_ORDER } from './data/navTree'
import { api } from './api'
import { current as curDomain, label as domainLabel, load as loadDomain } from './data/domainState'
import * as iam from './data/iam'
import * as session from './data/session'

const route = useRoute()
const router = useRouter()
const sess = ref(session.current())
watch(() => route.path, () => { sess.value = session.current() })
const banner = ref<{ demo_bills: number; real_bills: number } | null>(null)
const navErr = ref('')

async function loadBanner() {
  if (!sess.value) return
  try {
    const d = await api.assetNav()
    banner.value = d.banner
    navErr.value = ''
  } catch (e) {
    navErr.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(() => {
  if (sess.value) loadDomain()
  void loadBanner()
})
watch(() => sess.value?.role, (r) => {
  if (!r) return
  void loadBanner()
  loadDomain()
})

const role = computed(() => sess.value?.role)

/** 当前角色的菜单：按 ROLE_ORDER 顺序平铺（IA 重排后无分组无子级） */
const menuItems = computed(() => {
  if (!role.value) return []
  const order = ROLE_ORDER[role.value] || []
  return order
    .map((k) => ITEM_BY_KEY[k])
    .filter(Boolean)
    .filter((it) => iam.permits(role.value!, it.key))
})

const pageTitle = computed(() => {
  const hit = menuItems.value.find((it) => route.path.startsWith(it.path))
  return hit ? hit.label : (sess.value ? `${sess.value.name}工作台` : '')
})

/** a-menu 选中键 = 当前命中的菜单 path（平铺后无 openKeys 语义，留空数组） */
const selectedKeys = computed<string[]>(() => {
  const hit = menuItems.value.find((it) => route.path.startsWith(it.path))
  return hit ? [hit.path] : []
})

function onMenuClick({ key }: { key: string }) {
  router.push(String(key))
}

/** 只读角色角标（领导/规则管理员对部分菜单只读） */
function readonlyOf(path: string): boolean {
  return role.value ? session.isReadonly(role.value, path) : false
}

async function logout() {
  await session.logout()
  router.push('/login')
}
</script>

<template>
  <router-view v-if="route.path === '/login'" />

  <a-layout v-else class="shell">
    <!-- 固定侧栏（AntD 标准 fixed-sider 模式）：内容再长菜单也不动，侧栏自己滚 -->
    <a-layout-sider theme="dark" :width="216" class="sider-fixed">
      <div class="brand">
        <fund-outlined class="brand-icon" />
        <div>
          <div class="brand-name">福州港口集团</div>
          <div class="brand-sub">差旅费智能审核</div>
        </div>
      </div>
      <!-- 菜单平铺（2026-10-26 IA 重排定稿）：当前角色的菜单 ≤6 项直接渲染，
           无分组无子级。不传 items —— AntD4 里空 items 会吞掉插槽菜单。 -->
      <a-menu mode="inline" theme="dark" :selected-keys="selectedKeys" @click="onMenuClick">
        <a-menu-item v-for="it in menuItems" :key="it.path">
          {{ it.label }}
          <span v-if="readonlyOf(it.path)" class="ro">只读</span>
        </a-menu-item>
      </a-menu>
      <div v-if="sess" class="userbox">
        <a-avatar :size="28" style="background: #1677ff">{{ sess.user[0] }}</a-avatar>
        <div class="who">
          <div class="name">{{ sess.user }}</div>
          <div class="role">{{ sess.name }}</div>
        </div>
        <a-button type="text" size="small" class="out" @click="logout">退出</a-button>
      </div>
    </a-layout-sider>

    <a-layout class="main-fixed">
      <a-layout-header class="topbar">
        <span class="title">{{ pageTitle }}</span>
        <div class="grow"></div>
        <a-tag v-if="navErr" color="error">数据接口异常</a-tag>
        <a-tag v-else-if="banner" color="blue">真实单据 {{ banner.real_bills }} · 演示 {{ banner.demo_bills }}</a-tag>
      </a-layout-header>
      <div v-if="route.path.startsWith('/asset')" class="scopebar">
        <span>{{ domainLabel(curDomain) }}</span>
        <router-link v-if="route.path !== '/asset'" to="/asset">切换领域/实例</router-link>
      </div>
      <a-layout-content class="content">
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<style scoped>
.shell { min-height: 100vh; }
/* AntD 标准 fixed-sider：侧栏钉在左侧自己滚，主区让出 216px，内容滚动不带走菜单 */
.sider-fixed {
  position: fixed; inset: 0 auto 0 0;
  overflow-y: auto; overflow-x: hidden;
  padding-bottom: 64px; /* 给底部用户区留出 */
}
.main-fixed { margin-left: 216px; }
.brand {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 16px 12px; color: #fff;
}
.brand-icon { font-size: 22px; color: #1677ff; }
.brand-name { font-size: 15px; font-weight: 600; letter-spacing: 0.5px; }
.brand-sub { font-size: 11px; opacity: 0.55; margin-top: 1px; }
.userbox {
  position: absolute; bottom: 0; left: 0; right: 0;
  display: flex; align-items: center; gap: 10px;
  padding: 12px 14px; background: rgba(0, 0, 0, 0.25);
}
.userbox .who { flex: 1; min-width: 0; color: rgba(255, 255, 255, 0.85); }
.userbox .name { font-size: 13px; }
.userbox .role { font-size: 11px; opacity: 0.55; }
.userbox .out { color: rgba(255, 255, 255, 0.65); }
.ro {
  margin-left: 6px; font-size: 11px; padding: 0 4px;
  border: 1px solid rgba(255, 255, 255, 0.35); border-radius: 3px; opacity: 0.7;
}
.topbar {
  display: flex; align-items: center; gap: 12px;
  background: #fff; border-bottom: 1px solid #f0f0f0;
  padding: 0 24px; height: 52px; line-height: 52px;
}
.topbar .title { font-size: 16px; font-weight: 600; }
.grow { flex: 1; }
.scopebar {
  display: flex; justify-content: space-between; align-items: center;
  padding: 6px 24px; background: #e6f4ff; font-size: 13px; color: #0958d9;
}
.content {
  padding: 20px 24px 24px;
  /* 满高标准（2026-10-26 统一）：内容区高度=视口-顶栏，页面级滚动只发生在
     .content 内部。所有页面在固定视口内完成，不把 body 拉长——
     「左右等高」「底边贴屏」都由这一条保证 */
  height: calc(100vh - 52px);
  overflow-y: auto;
  overscroll-behavior: contain;
}
</style>
