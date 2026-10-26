<script setup lang="ts">
/**
 * 登录页（2026-10-24 二版，参照 Ki-Brain 左图右卡布局，用户指定）。
 *
 * 左侧：品牌深蓝区——产品名 + slogan + 三层能力行 + 底注（纯展示）。
 * 右侧：白卡——账号/密码 + 角色列表行（整行可选，杜绝文字溢出）。
 *
 * 诚实边界不变：后端没有鉴权接口，密码不校验——选角色进入对应菜单，
 * 真鉴权是二期后端工作（缺口 #1）。
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
const showPwd = ref(false)
const busy = ref(false)
const err = ref('')

function choose(r: Role) {
  pick.value = r
  // 点角色自动带出账号：演示时不用记 5 个账号名；账号仍可手改
  const hit = roleList.value.find((x) => x.key === r)
  if (hit?.account) account.value = hit.account
  err.value = ''
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
  <div class="v2 login-split">
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
        <div class="hero-line"><span class="ln">L2</span><span>管理看板 —— 结论分布 · 偏差回流 · 规则覆盖</span></div>
        <div class="hero-line"><span class="ln">L1</span><span>审核工作台 —— 复核队列 · 证据展开 · 一键处置</span></div>
        <div class="hero-line"><span class="ln">L0</span><span>知识资产 —— 制度文件 · 规则抽取 · 本体工场</span></div>
      </div>
      <div class="hero-foot">福建省港口集团 · 财务共享中心</div>
    </div>

    <!-- 右侧登录卡 -->
    <div class="login-side">
      <div class="login-card2">
        <div class="card-title">登录平台</div>
        <div class="card-sub">选择角色进入对应工作台</div>

        <label for="loginUser">账号</label>
        <input id="loginUser" v-model="account" class="input" style="width: 100%; height: 38px"
               autocomplete="username" placeholder="账号">

        <label for="loginPwd">密码</label>
        <div style="position: relative">
          <input id="loginPwd" v-model="password" class="input" style="width: 100%; height: 38px; padding-right: 40px"
                 :type="showPwd ? 'text' : 'password'" autocomplete="current-password" placeholder="密码">
          <button type="button" @click="showPwd = !showPwd"
                  :aria-label="showPwd ? '隐藏密码' : '显示密码'"
                  style="position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
                         width: 28px; height: 28px; border-radius: 4px; display: grid; place-items: center;
                         color: var(--weak); border: 0; background: none; cursor: pointer">
            <svg v-if="!showPwd" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>
            <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M2 12s3.5-7 10-7c2 0 3.7.7 5.2 1.7M22 12s-3.5 7-10 7c-2 0-3.7-.7-5.2-1.7"/><path d="M4 4l16 16"/></svg>
          </button>
        </div>

        <label>登录角色（点击行切换，双击直接进入）</label>
        <div class="role-list">
          <button v-for="r in roleList" :key="r.key" class="role-row" :class="{ on: pick === r.key }"
                  @click="choose(r.key)" @dblclick="choose(r.key); submit()">
            <span class="rr-tag">{{ r.label }}</span>
            <span class="rr-desc">{{ r.desc }}</span>
            <span class="rr-who">{{ r.who }}</span>
          </button>
        </div>

        <div v-if="err" class="login-err">{{ err }}</div>

        <button class="btn primary" style="width: 100%; height: 40px; margin-top: 18px; font-size: 14px"
                :disabled="busy" @click="submit">{{ busy ? '登录中…' : '登 录' }}</button>
        <div class="login-note">演示账号的密码统一是 demo-1234；账号密码由服务端校验，不是前端假的</div>
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
  font-size: 11px; font-weight: 600; border: 1px solid rgba(255, 255, 255, .35);
  border-radius: 4px; padding: 1px 6px; font-variant-numeric: tabular-nums; flex: none;
}
.hero-foot { margin-top: 46px; font-size: 12px; opacity: .45; }

.login-side {
  flex: 1; display: flex; align-items: center; justify-content: center;
  background: var(--bg); padding: 24px;
}
.login-card2 {
  width: 400px; background: var(--panel); border: 1px solid var(--line);
  border-radius: 14px; box-shadow: var(--shadow-2); padding: 34px 34px 26px;
}
.card-title { font-size: 20px; font-weight: 600; color: var(--text); }
.card-sub { color: var(--weak); font-size: 13px; margin: 4px 0 20px; }
.login-card2 label { display: block; font-size: 13px; color: var(--muted); margin: 12px 0 6px; }

.role-list { display: flex; flex-direction: column; gap: 6px; max-height: 268px; overflow: auto; padding-right: 2px; }
.role-row {
  display: flex; align-items: center; gap: 10px; width: 100%;
  padding: 9px 12px; border: 1px solid var(--line); border-radius: 8px;
  background: var(--panel); cursor: pointer; text-align: left; font-size: 13px; height: auto;
  transition: border-color .12s, background .12s;
}
.role-row:hover { background: var(--panel-2); border-color: var(--line); color: inherit; }
.role-row.on { border-color: var(--brand); background: var(--brand-bg); }
.rr-tag { font-weight: 600; color: var(--text); flex: none; min-width: 72px; }
.role-row.on .rr-tag { color: var(--brand); }
.rr-desc { color: var(--weak); font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rr-who { margin-left: auto; color: var(--muted); font-size: 12px; flex: none; }

.login-err {
  margin-top: 12px; padding: 8px 12px; border-radius: var(--r-s);
  background: var(--bad-bg); color: var(--bad); font-size: 12.5px;
}
.login-note { margin-top: 10px; font-size: 11.5px; color: var(--weak); text-align: center; }

@media (max-width: 900px) {
  .login-hero { display: none; }
  .login-split { background: var(--bg); }
}
</style>
