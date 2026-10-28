<script setup lang="ts">
/**
 * 对话式起草（B-P1-A，客户 #6，2026-10-28 Wave 4）。
 *
 * 对话是**草稿的第五来源**：用户用自然语言描述业务要求 → 模型追问要素 →
 * 产八要素草稿 → 用户确认 → 转待审进既有专家审定链（ExtractReview 同款
 * 页签）。审定纪律不破：这里没有任何「直接入库」的通道——转待审是唯一
 * 落库动作，落的是待审草稿，采纳/驳回/发布全在既有链上。
 *
 * 交互形态（业内对话起草定式）：
 *   消息流（气泡，用户右 / 助手左）+ 底部输入框（回车发送）
 *   → ready 后草稿卡嵌在对话流里（八要素就地可编辑，数值类字段挂
 *     「模型生成需核对」黄条——对话起草没有制度原文佐证）
 *   → 追问继续增量修订同一张卡（不是每轮新卡）
 *   → 「转待审」→ toast 给落点（去制度文件 → 抽取与审定）
 *
 * 五环：发送中输入区 loading+禁发（防重复）；失败人话原因+同屏重试；
 * 成功草稿卡就地更新；完成后落点链接引导。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { api } from '../api'
import * as session from '../data/session'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'submitted'): void; (e: 'update:open', v: boolean): void }>()

interface Msg { role: 'user' | 'assistant'; content: string; err?: boolean }

const msgs = ref<Msg[]>([])
const input = ref('')
const sending = ref(false)
/** 就地编辑的八要素草稿（ready 后才有；每轮模型回稿覆盖，用户改过后
 *  下一轮模型回稿不再覆盖用户改过的字段——以用户为准） */
const draft = ref<Record<string, any> | null>(null)
const userEdited = ref<Set<string>>(new Set())
const submitting = ref(false)
const done = ref<{ draft_id: string } | null>(null)
const listEl = ref<HTMLElement | null>(null)

const who = computed(() => session.current()?.user || '')

/** 八要素呈现顺序：①②③⑤⑥⑦ + 处置动作/规则类型（与 ExtractReview 对齐） */
const FIELDS: { key: string; label: string; editable: boolean; textarea?: boolean }[] = [
  { key: '规则名称', label: '① 规则名称', editable: true },
  { key: '审核维度', label: '② 审核维度', editable: true },
  { key: '风险等级', label: '③ 风险类别', editable: true },
  { key: '适用范围', label: '⑤ 适用范围', editable: true },
  { key: '判定逻辑', label: '⑥ 判定逻辑', editable: true, textarea: true },
  { key: '例外情形', label: '⑦ 例外', editable: true, textarea: true },
  { key: '处置动作', label: '处置动作', editable: true },
  { key: '规则类型', label: '规则类型', editable: true },
]

const canSubmit = computed(() =>
  !!draft.value && !!(draft.value['规则名称'] || '').toString().trim()
  && !!(draft.value['判定逻辑'] || '').toString().trim() && !submitting.value)

function reset() {
  msgs.value = [{ role: 'assistant', content:
    '用一句话描述要起草的规则，例如：住宿费超出标准 200 元以上的单据要拦截报销。'
    + '要素不全我会追问；确认后转「抽取与审定」页签走专家审定。' }]
  input.value = ''
  draft.value = null
  userEdited.value = new Set()
  done.value = null
  sending.value = false
  submitting.value = false
}

async function scrollBottom() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return
  msgs.value.push({ role: 'user', content: text })
  input.value = ''
  sending.value = true
  await scrollBottom()
  try {
    const r = await api.exChat({
      messages: msgs.value.filter((m) => !m.err).map((m) => ({ role: m.role, content: m.content })),
      session_hint: (draft.value?.['规则名称'] as string) || text.slice(0, 30),
    })
    msgs.value.push({ role: 'assistant', content: r.reply, err: !r.ok })
    if (r.ok && r.ready && r.draft) {
      // 增量更新同一张卡：用户改过的字段不被模型回稿覆盖
      if (!draft.value) draft.value = {}
      for (const [k, v] of Object.entries(r.draft)) {
        if (!userEdited.value.has(k)) draft.value[k] = v
      }
    }
  } catch (e) {
    msgs.value.push({ role: 'assistant', err: true,
      content: humanErr(e) + '（可点击重试或修改后重发上一句）' })
  } finally {
    sending.value = false
    await scrollBottom()
  }
}

function retryLast() {
  // 重试 = 把最后一条用户发言之后的内容撤掉重发
  const lastUser = [...msgs.value].reverse().find((m) => m.role === 'user')
  if (!lastUser) return
  const idx = msgs.value.lastIndexOf(lastUser)
  msgs.value.splice(idx)          // 撤掉那一句及其后所有回复
  input.value = lastUser.content
  send()
}

function humanErr(e: unknown): string {
  const s = e instanceof Error ? e.message : String(e)
  if (s.includes('Failed to fetch')) return '网络不通或服务未启动'
  return s
}

/** 数值类字段的红标核对在转待审时由后端 number_check 对用户原话核定；
 *  卡内统一挂「模型生成需核对」黄条（对话没有制度原文，所有数值都须人工确认），
 *  不在卡内重复做一遍前端正则——两处判定迟早漂移。 */

function onEdit(key: string) {
  userEdited.value.add(key)
}

async function submitForReview() {
  if (!canSubmit.value || !draft.value) return
  submitting.value = true
  try {
    const r = await api.exDraftFromChat({
      draft: draft.value,
      messages: msgs.value.filter((m) => !m.err).map((m) => ({ role: m.role, content: m.content })),
      session_hint: (draft.value['规则名称'] as string) || '',
    })
    done.value = { draft_id: r.draft_id }
    const suspects = r.number_check?.suspect || []
    message.success(suspects.length
      ? `已转待审（数值待核 ${suspects.length} 处已打标）。去「制度文件 → 抽取与审定」页签审定`
      : '已转待审。去「制度文件 → 抽取与审定」页签审定')
    emit('submitted')
  } catch (e) {
    message.error(humanErr(e))
  } finally {
    submitting.value = false
  }
}

/* 首次打开初始化欢迎语；每次打开滚到底（重置只在首次或完成后点「再起草一条」）。
 * immediate：抽屉可能在挂载时就是开着的（入口先 set open 再渲染） */
watch(() => props.open, (v) => {
  if (v) {
    if (!msgs.value.length) reset()
    nextTick(scrollBottom)
  }
}, { immediate: true })

const inputDisabled = computed(() => sending.value || !!done.value)
</script>

<template>
  <a-drawer :open="props.open" width="640" placement="right"
    :body-style="{ padding: '0', display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
    @close="emit('update:open', false)">
    <template #title>
      <span>对话起草</span>
      <span class="cd-sub">自然语言 → 八要素草稿 → 专家审定</span>
    </template>

    <!-- 消息流 -->
    <div ref="listEl" class="cd-list">
      <div v-for="(m, i) in msgs" :key="i" class="cd-msg" :class="m.role === 'user' ? 'me' : 'bot'">
        <div class="cd-bubble" :class="{ err: m.err }">
          <template v-if="m.role === 'user'">{{ m.content }}</template>
          <template v-else>
            {{ m.content }}
            <!-- 失败且后面没有新的用户发言时给重试（重试=撤回重发最后一句） -->
            <a v-if="m.err && !msgs.slice(i + 1).some((x) => x.role === 'user')"
              class="cd-retry" @click="retryLast">重试</a>
          </template>
        </div>
      </div>

      <!-- 生成中 -->
      <div v-if="sending" class="cd-msg bot">
        <div class="cd-bubble cd-typing">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          <span class="cd-typing-t">模型思考中…（每轮真调模型、真计费）</span>
        </div>
      </div>

      <!-- 草稿卡：ready 后嵌在对话流里，就地可编辑 -->
      <div v-if="draft && !done" class="cd-draft">
        <div class="cd-draft-head">
          <b>八要素草稿</b>
          <a-tag color="warning">模型生成 · 数值需核对</a-tag>
        </div>
        <a-alert type="warning" show-icon class="cd-warn"
          message="对话起草没有制度原文佐证——每个数值采纳前必须人工确认。" />
        <div class="cd-kv">
          <template v-for="f in FIELDS" :key="f.key">
            <label :class="{ req: f.key === '规则名称' || f.key === '判定逻辑' }">{{ f.label }}</label>
            <a-textarea v-if="f.textarea" v-model:value="draft[f.key]" :rows="2"
              :placeholder="f.key === '例外情形' ? '无则留空' : ''" size="small"
              class="cd-input" @change="onEdit(f.key)" />
            <a-input v-else v-model:value="draft[f.key]" size="small" class="cd-input"
              @change="onEdit(f.key)" />
          </template>
          <label>⑧ 缺口</label>
          <div class="cd-gap dim">{{ draft['缺口'] || '—' }}</div>
        </div>
        <div class="cd-draft-foot">
          <span class="dim">转待审后与制度抽取草稿走同一条审定链（采纳/驳回/发布）</span>
          <a-button type="primary" size="small" :loading="submitting"
            :disabled="!canSubmit" @click="submitForReview">转待审</a-button>
        </div>
      </div>

      <!-- 完成态：落点引导（五环第 5 环） -->
      <div v-if="done" class="cd-done">
        <a-alert type="success" show-icon
          message="草稿已转待审（留痕：操作日志 · 规则类）"
          :description="`去「制度文件 → 抽取与审定」页签审定这批草稿；审定人=${who || '会话登录人'}。`" />
        <div class="cd-done-acts">
          <a-button size="small" @click="reset">再起草一条</a-button>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div v-if="!done" class="cd-inputbar">
      <a-input v-model:value="input" placeholder="描述业务要求，回车发送"
        :disabled="inputDisabled" :maxlength="600"
        @keyup.enter="send" />
      <a-button type="primary" :loading="sending"
        :disabled="inputDisabled || !input.trim()" @click="send">发送</a-button>
    </div>
  </a-drawer>
</template>

<style scoped>
.cd-sub { font-size: 12px; color: rgba(0, 0, 0, .45); font-weight: normal; margin-left: 8px; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }

.cd-list { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex;
  flex-direction: column; gap: 10px; }
.cd-msg { display: flex; }
.cd-msg.me { justify-content: flex-end; }
.cd-bubble { max-width: 78%; padding: 8px 12px; border-radius: 10px;
  font-size: 13px; line-height: 1.7; word-break: break-word; }
.cd-msg.bot .cd-bubble { background: #f5f5f5; border-top-left-radius: 2px; }
.cd-msg.me .cd-bubble { background: #e6f4ff; border-top-right-radius: 2px; }
.cd-bubble.err { background: #fff1f0; color: #cf1322; }
.cd-retry { margin-left: 8px; color: #1677ff; cursor: pointer; }

.cd-typing { display: flex; align-items: center; gap: 4px; }
.cd-typing .dot { width: 6px; height: 6px; border-radius: 50%; background: #bfbfbf;
  animation: cd-blink 1.2s infinite; }
.cd-typing .dot:nth-child(2) { animation-delay: .2s; }
.cd-typing .dot:nth-child(3) { animation-delay: .4s; }
@keyframes cd-blink { 0%, 80%, 100% { opacity: .3; } 40% { opacity: 1; } }
.cd-typing-t { font-size: 12px; color: rgba(0, 0, 0, .45); margin-left: 8px; }

.cd-draft { border: 1px solid #ffe58f; background: #fffbe6;
  border-radius: 8px; padding: 12px 14px; }
.cd-draft-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.cd-warn { margin-bottom: 10px; }
.cd-kv { display: grid; grid-template-columns: 92px 1fr; gap: 6px 10px;
  align-items: center; font-size: 12.5px; background: #fff; border-radius: 6px;
  padding: 10px 12px; }
.cd-kv label { color: rgba(0, 0, 0, .55); }
.cd-kv label.req::after { content: ' *'; color: #ff4d4f; }
.cd-gap { background: #fafafa; border-radius: 4px; padding: 4px 8px;
  line-height: 1.7; word-break: break-all; }
.cd-draft-foot { display: flex; align-items: center; justify-content: space-between;
  margin-top: 10px; gap: 8px; flex-wrap: wrap; }

.cd-done { margin-top: 4px; }
.cd-done-acts { margin-top: 10px; display: flex; justify-content: center; }

.cd-inputbar { display: flex; gap: 8px; padding: 12px 20px;
  border-top: 1px solid #f0f0f0; background: #fff; }
</style>
