<script setup lang="ts">
/**
 * 登录页（2026-10-27 AntD 换装版，左图右卡布局为用户定稿）。
 *
 * 左侧：品牌深蓝区——产品名 + slogan + 三个业务版块（名词化，无实现层词）。
 * 右侧：AntD 表单——账号/密码 + 角色列表行（整行可选，杜绝文字溢出）。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { allRoles, login, type Role } from '../data/session'
import * as iam from '../data/iam'

const router = useRouter()
/** 角色来自 IAM（服务端 sys_role 表），新建的角色刷新就会出现在这里 */
const roleList = computed(() =>
  allRoles().map((r) => ({
    key: r.role,
    label: r.name,
    who: r.user,
    account: r.account,
    desc: iam.roleByKey(r.role)?.desc || `${r.name}工作台`,
  })),
)
const pick = ref<Role>(roleList.value[0]?.key || '')
const account = ref(roleList.value[0]?.account || '')
const password = ref('demo-1234')
const busy = ref(false)
const err = ref('')

function choose(r: Role) {
  pick.value = r
  // 点角色自动带出账号：演示时不用记 5 个账号名；账号仍可手改
  const hit = roleList.value.find((x) => x.key === r)
  if (hit?.account) account.value = hit.account
  err.value = ''
  // 测试期一键登录：开发构建里点角色行 = 直接进系统（账号已带出，密码统一）。
  // **只在 DEV 生效**——vite build 出来的交付版自动回落成标准输密码形态，
  // 交付前不需要手工清理这段代码。
  if (import.meta.env.DEV && hit?.account) {
    account.value = hit.account
    password.value = 'demo-1234'
    void submit()
  }
}

async function submit() {
  if (busy.value) return
  if (!account.value.trim() || !password.value) {
    err.value = '请输入账号和密码'
    return
  }
  busy.value = true
  err.value = ''
  const r = await login(account.value.trim(), password.value)
  busy.value = false
  if (!r.ok) {
    // 服务端统一回「账号或密码不正确」，这里原样显示，不做二次解释
    err.value = r.msg
    return
  }
  router.push(r.def?.home || '/login')
}
</script>

<template>
  <div class="login-split">
    <!-- 左侧品牌区 -->
    <div class="login-hero">
      <div class="hero-brand">
        <div class="hero-mark" aria-hidden="true">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M3 15c2 0 3-1.4 5-1.4S10.6 15 12 15s3-1.4 5-1.4 2.6 1.4 4 1.4"/><path d="M3 19c2 0 3-1.4 5-1.4S10.6 19 12 19s3-1.4 5-1.4 2.6 1.4 4 1.4"/><path d="M12 4v8"/><path d="M8 8l4-4 4 4"/></svg>
        </div>
        <div class="hero-name">福州港口集团</div>
      </div>
      <div class="hero-slogan">差旅费智能审核平台</div>
      <div class="hero-sub">AI 预审 · 人工闸门 · 全程可追溯</div>
      <div class="hero-layers">
        <div class="hero-line"><span class="ln">看板</span><span>结论分布 · 偏差回流 · 规则覆盖</span></div>
        <div class="hero-line"><span class="ln">审核</span><span>复核队列 · 证据展开 · 一键处置</span></div>
        <div class="hero-line"><span class="ln">规则</span><span>制度文件 · 规则审定 · 发布管理</span></div>
      </div>
      <div class="hero-foot">福建省港口集团 · 财务共享中心</div>
    </div>

    <!-- 右侧登录卡 -->
    <div class="login-side">
      <div class="login-card">
        <div class="card-title">登录平台</div>
        <div class="card-sub">选择角色进入对应工作台</div>

        <a-form layout="vertical" @submit.prevent>
          <a-form-item label="账号" for="loginUser">
            <a-input id="loginUser" v-model:value="account" size="large"
              autocomplete="username" placeholder="账号" />
          </a-form-item>
          <a-form-item label="密码" for="loginPwd">
            <a-input-password id="loginPwd" v-model:value="password" size="large"
              autocomplete="current-password" placeholder="密码" />
          </a-form-item>
        </a-form>

        <div class="role-label">登录角色（点击直接进入）</div>
        <div class="role-list">
          <button v-for="r in roleList" :key="r.key" class="role-row" :class="{ on: pick === r.key }"
                  @click="choose(r.key)" @dblclick="choose(r.key); submit()">
            <span class="rr-tag">{{ r.label }}</span>
            <span class="rr-desc">{{ r.desc }}</span>
            <span class="rr-who">{{ r.who }}</span>
          </button>
        </div>

        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-top: 12px" />

        <a-button type="primary" size="large" block style="margin-top: 18px"
                  :loading="busy" @click="submit">登录</a-button>
        <div class="login-note">演示账号的密码统一是 demo-1234</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-split { display: flex; height: 100vh; overflow: hidden; }
.login-hero {
  flex: 1.15; display: flex; flex-direction: column; justify-content: center;
  padding: 0 7%; color: #fff;
  background: linear-gradient(150deg, #014099 0%, #015293 55%, #013a7a 100%);
}
.hero-brand { display: flex; align-items: center; gap: 14px; }
.hero-mark {
  width: 46px; height: 46px; border-radius: 10px;
  background: rgba(255, 255, 255, .12); border: 1px solid rgba(255, 255, 255, .22);
  display: grid; place-items: center;
}
.hero-name { font-size: 26px; font-weight: 600; letter-spacing: 1px; }
.hero-slogan { font-size: 38px; font-weight: 700; margin: 30px 0 10px; letter-spacing: 2px; }
.hero-sub { font-size: 16px; opacity: .82; letter-spacing: 3px; }
.hero-layers { display: flex; flex-direction: column; gap: 14px; margin-top: 42px; }
.hero-line { display: flex; align-items: center; gap: 12px; font-size: 14px; opacity: .78; }
.ln {
  font-size: 12px; font-weight: 600; border: 1px solid rgba(255, 255, 255, .35);
  border-radius: 4px; padding: 1px 8px; flex: none;
}
.hero-foot { margin-top: 46px; font-size: 12px; opacity: .45; }

.login-side {
  flex: 1; display: flex; align-items: center; justify-content: center;
  background: #f5f6f8; padding: 24px;
}
.login-card {
  width: 400px; background: #fff; border: 1px solid #e5e7eb;
  border-radius: 12px; box-shadow: 0 10px 30px rgba(16, 24, 40, .1); padding: 34px 34px 26px;
}
.card-title { font-size: 20px; font-weight: 600; color: rgba(0, 0, 0, .88); }
.card-sub { color: rgba(0, 0, 0, .45); font-size: 13px; margin: 4px 0 12px; }

.role-label { font-size: 13px; color: rgba(0, 0, 0, .65); margin: 4px 0 6px; }
.role-list { display: flex; flex-direction: column; gap: 6px; max-height: 268px; overflow: auto; padding-right: 2px; }
.role-row {
  display: flex; align-items: center; gap: 10px; width: 100%;
  padding: 9px 12px; border: 1px solid #e5e7eb; border-radius: 8px;
  background: #fff; cursor: pointer; text-align: left; font-size: 13px; height: auto;
  transition: border-color .12s, background .12s;
}
.role-row:hover { background: #fafafa; border-color: #d9dce1; color: inherit; }
.role-row.on { border-color: #1677ff; background: #e6f4ff; }
.rr-tag { font-weight: 600; color: rgba(0, 0, 0, .88); flex: none; min-width: 72px; }
.role-row.on .rr-tag { color: #1677ff; }
.rr-desc { color: rgba(0, 0, 0, .45); font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rr-who { margin-left: auto; color: rgba(0, 0, 0, .35); font-size: 12px; flex: none; }

.login-note { margin-top: 10px; font-size: 11.5px; color: rgba(0, 0, 0, .35); text-align: center; }

@media (max-width: 900px) {
  .login-hero { display: none; }
  .login-split { background: #f5f6f8; }
}
</style>
