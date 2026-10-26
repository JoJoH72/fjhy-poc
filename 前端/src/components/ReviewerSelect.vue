<script setup lang="ts">
/**
 * 审定人选择（2026-10-25 演示待办④：取消手工输入姓名）。
 *
 * 原来是手输 input：可以填任何字串——「he工」「何 老师」都存得进去，
 * 事后对不上任何一个账号，署名就失去了审计意义。
 * 现在从系统账号里选（GET /api/extract/reviewers → sys_user，迁移 0040），
 * **默认落在当前登录人**：审自己审的东西署自己的名，绝大多数时候不用动它。
 *
 * 数据源刻意取后端而不是前端 localStorage 的 IAM 缓存：
 * 0040_iam 之后账号的真身在服务端，管理员刚加的人、刚停用的账号，
 * 只有后端清单是准的。选人必须选一个真实存在且启用的账号。
 */
import { onMounted, ref } from 'vue'
import { api, type ExReviewer } from '../api'

const props = defineProps<{ modelValue: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>()

const items = ref<ExReviewer[]>([])
const err = ref('')
const note = ref('')
const sel = ref<HTMLSelectElement | null>(null)

onMounted(async () => {
  try {
    const r = await api.exReviewers()
    items.value = r.items
    note.value = r.note
    if (!r.items.length && !err.value) err.value = r.note || '账号清单为空'
    if (!props.modelValue) {
      const me = await api.authMe().catch(() => null)
      const hit = me ? r.items.find((u) => u.account === me.account) : null
      if (hit) emit('update:modelValue', hit.user_label)
    }
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
  }
})

/** 给宿主页 focus 用：行内的提示点一下跳回这里。 */
function focus() {
  sel.value?.focus()
  sel.value?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}
defineExpose({ focus })
</script>

<template>
  <!-- 失败不静默：一个空着的下拉不说原因，看起来和「功能坏了」一模一样。 -->
  <select v-if="!err" ref="sel" :value="modelValue"
          :class="{ 'need-fill': !modelValue }"
          :title="note || '审定要留名：署名随审定记录留档，对不上账号的署名没有审计意义'"
          style="width: 170px"
          @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)">
    <option value="" disabled>审定人（必选）</option>
    <option v-for="u in items" :key="u.account" :value="u.user_label">
      {{ u.user_label }}（{{ u.dept }} · {{ u.role_name }}）
    </option>
  </select>
  <span v-else class="rv-err" :title="err">审定人清单不可用：{{ err }}</span>
</template>

<style scoped>
/* 必选但还没选。警示色不是错误色——「还没选」不是「选错了」。 */
.need-fill {
  border-color: var(--pend, #d48806);
}
.rv-err { color: var(--hit, #c0392b); font-size: 12px; }
</style>
