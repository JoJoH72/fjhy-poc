<script setup lang="ts">
/**
 * 系统 · 大模型配置。
 *
 * 2026-10-25 重排信息架构：**这一页服务的是「三类 AI 能力各用哪个模型」，
 * 不是一张模型参数表。**
 *
 * 原来的样子：三张用途卡 + 一块「API Key 的存储」+ 一张空的配置表 +
 * 一整页运维表单（Base URL / 并发 / 超时 / 深度思考）。业务人员看完只知道
 * 「这页有很多我不懂的字段」，不知道自己要做什么；而且用途卡里的下拉是空的、
 * 「设为默认」是灰的——连下一步该点哪都不知道。
 *
 * 现在的结构：
 *   ① 顶部一句话说清这页干什么，右侧给「3 项里几项就绪」
 *   ② 三张用途卡：这个能力负责什么、现在用哪个模型、测过没有、没配会怎样；
 *      按钮只有两个——「配置模型」和「连通测试」
 *   ③ 点「配置模型」开右侧抽屉：选厂商 → 填 Key → 拉型号 → 测通 → 保存并启用
 *   ④ 模型库（表格）留给需要横向对比的人
 *   ⑤ 平台口径（Key 怎么存、调用怎么脱敏、测试花不花钱）收进折叠区
 *
 * 下面这些行为是**刻意保留**的，不是风格问题：
 *   1. 用途 × 模型能力的交叉校验：读图配纯文本模型不会报错，模型会按文字线索
 *      编一段看起来合理的内容，而下游当成「已核验的票面信息」。判断在
 *      data/llmState.ts（可测），后端 sys_llm.modality_warning 同一套。
 *   2.「没测过」和「测了不通」是两个状态，不合并。
 *   3. 型号不写死，按「拉取型号」从厂商现取（型号半年就漂移）。
 *   4. 删除要点两次：会连带删掉测试历史，而历史是排查「哪天开始不通」的唯一依据。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import type { LlmConfig, LlmPreset, LlmTestResult, LlmTestRow } from '../api'
import { ERR_LABEL, modalityWarning, testTag } from '../data/llmState'
import { useCollapse } from '../data/collapse'

const cfgList = useCollapse()

const loading = ref(true)
const err = ref('')
const msg = ref('')
const warn = ref('')

const presets = ref<LlmPreset[]>([])
const presetNote = ref('')
const configs = ref<LlmConfig[]>([])
const purposes = ref<{ key: string; label: string; default: string | null }[]>([])
const missing = ref<string[]>([])
const policy = ref('')
const defaultsNote = ref('')
const keyStatus = ref<Awaited<ReturnType<typeof api.llmKeyStatus>> | null>(null)

/** 三类用途的人话说明。后端口径在 kinds/policy 里，这里是**给业务人员看的**那一层：
    只说「它负责什么、没配会怎样、选错会怎样」，不出现 base_url、token 这类词。 */
const PLAIN: Record<string, { what: string; req: string; none: string; icon: string }> = {
  vision_read: {
    icon: 'img',
    what: '看发票、行程单、审批单的照片，把票面信息读成结构化字段。',
    req: '必须选多模态模型；选成纯文本模型不会报错，它会照着文字线索编内容。',
    none: '没配：图片类票据读不出字段，相关规则全部落到待人工。',
  },
  rule_judge: {
    icon: 'scale',
    what: '规则判不了的单项，给一条参考意见，供审核岗复核时参考。',
    req: '长文本推理；温度建议 0，同一张单两次跑出不同结论没法向审核岗解释。',
    none: '没配：超出规则范围的单项直接标「待人工：未调用模型」，不会假装判过。',
  },
  extract: {
    icon: 'doc',
    what: '从制度文件里抽条款、起草规则八要素草稿。',
    req: '长上下文；产出的数值必须回原文核对，草稿不是发布。',
    none: '没配：制度文件只能人工读，规则靠手写，抽取流水线停在切条款这一步。',
  },
}

function blankForm() {
  return {
    config_id: '',
    name: '',
    preset_id: '',
    provider: 'openai_compatible',
    base_url: '',
    model_name: '',
    api_key: '',
    purpose: 'rule_judge',
    temperature: 0,
    max_tokens: null as number | null,
    enable_thinking: false,
    concurrency: 3,
    timeout_s: 60,
    enabled: true,
  }
}
const form = ref(blankForm())
const editing = ref(false)

/** 「拉取型号」拿回来的列表。空数组 ≠ 没拉过，用 modelsFetched 区分 */
const models = ref<string[]>([])
const modelsFetched = ref(false)
const busy = ref('')                         // 正在跑的动作名，用来禁按钮防重复点
const probe = ref<LlmTestResult | null>(null)
const testResult = ref<Record<string, LlmTestResult>>({})
const showKey = ref(false)                 // API Key 是否明文显示
const history = ref<{ cid: string; rows: LlmTestRow[] } | null>(null)
const pendingDelete = ref('')                // 删除的二次确认

const preset = computed(() => presets.value.find((p) => p.preset_id === form.value.preset_id))
const isHttp = computed(() => form.value.base_url.startsWith('http://'))
// 把型号名和「已经配成文本用途的型号」一起带进去：2026-10-19 收紧后，
// 预置选对了但型号填成文本模型，这里也要报。
const textModels = computed(() =>
  configs.value
    .filter((c) => c.purpose === 'rule_judge' || c.purpose === 'extract')
    .map((c) => c.model_name)
    .filter((m): m is string => !!m),
)
const modalityWarn = computed(() =>
  // 空白表单（没选厂商也没填型号）不报「能力不匹配」——那不是用户在犯错，
  // 是还没开始填。原来一打开抽屉就顶着一条红字，业务人员第一眼就被劝退。
  (!form.value.model_name && !form.value.preset_id)
    ? ''
    : modalityWarning(form.value.purpose, preset.value?.modality ?? null,
                      form.value.model_name, textModels.value,
                      preset.value?.suggest_model ?? null),
)
const legacyRows = computed(() => configs.value.filter((c) => c.key_format === 'legacy'))

const cfgById = (cid: string | null) => configs.value.find((c) => c.config_id === cid) || null
function candidates(purposeKey: string) {
  return configs.value.filter((c) => c.purpose === purposeKey && c.enabled)
}

/** 用途卡的状态：没配 / 停用 / 测过但不通 / 就绪 */
function stateOf(p: { key: string; default: string | null }) {
  const c = cfgById(p.default)
  if (!c) return 'missing'
  if (!c.enabled) return 'off'
  if (c.last_test_ok === false) return 'fail'
  // 配了但没测过 ≠ 通过。这条与 data/llmState.ts 的三态原则一致：
  // 合成绿色会让用户以为能用，跑批时才发现不通。
  if (c.last_test_ok === null || c.last_test_ok === undefined) return 'untested'
  return 'ok'
}
const SHORT: Record<string, string> = {
  vision_read: '读图', rule_judge: '判定', extract: '抽取',
}
const STATE_TEXT: Record<string, string> = {
  ok: '已就绪', untested: '已配置 · 未测试', missing: '未配置', off: '已停用', fail: '测试未通过',
}
const okCount = computed(() => purposes.value.filter((p) => stateOf(p) === 'ok').length)
/** 「未完成」= 没到 ok：未配置、未测试、测试不通、停用都算 */
const todoCount = computed(() => purposes.value.length - okCount.value)
const progress = computed(() =>
  purposes.value.length ? Math.round((okCount.value / purposes.value.length) * 100) : 0)
/** 顶部「去配置」跳到第一个没完成的用途 */
const firstTodo = computed(() => purposes.value.find((p) => stateOf(p) !== 'ok') || null)

/* ── 配置抽屉 ─────────────────────────────────────────────────────────── */
const drawer = ref('')                       // 值为用途 key
const drawerPurpose = computed(() => purposes.value.find((p) => p.key === drawer.value) || null)

function openDrawer(purposeKey?: string) {
  resetForm()
  const key = purposeKey || purposes.value.find((p) => !p.default)?.key || 'rule_judge'
  form.value.purpose = key
  drawer.value = key
  msg.value = ''
  warn.value = ''
}
function closeDrawer() {
  drawer.value = ''
  resetForm()
}

async function loadAll() {
  loading.value = true
  err.value = ''
  try {
    const [p, c, k] = await Promise.all([
      api.llmPresets(),
      api.llmConfigs(),
      api.llmKeyStatus(),
    ])
    presets.value = p.items
    presetNote.value = p.note
    configs.value = c.items
    purposes.value = c.purposes
    missing.value = c.missing_defaults
    policy.value = c.policy
    defaultsNote.value = c.note
    keyStatus.value = k
  } catch (e) {
    // 取不到就说取不到。**不要退化成空列表**——
    // 空列表看着像「还没配过模型」，而实际是接口挂了，这两件事必须分得开。
    err.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}
onMounted(loadAll)

function note(m: string, w = '') {
  msg.value = m
  warn.value = w
}

function pickPreset(pid: string) {
  form.value.preset_id = pid
  const p = presets.value.find((x) => x.preset_id === pid)
  if (!p) return
  form.value.base_url = p.base_url
  form.value.provider = p.protocol
  form.value.model_name = p.suggest_model || ''
  if (!form.value.name) form.value.name = p.label
  models.value = []
  modelsFetched.value = false
  probe.value = null
}

/** 把一行载入表单编辑。Key 不回填（接口只回掩码），留空表示不改动。 */
function edit(c: LlmConfig) {
  form.value = {
    config_id: c.config_id,
    name: c.config_name,
    preset_id: c.preset_id || '',
    provider: c.provider,
    base_url: c.base_url,
    model_name: c.model_name,
    api_key: '',
    purpose: c.purpose || 'rule_judge',
    temperature: Number(c.temperature ?? 0),
    max_tokens: c.max_tokens,
    enable_thinking: c.enable_thinking,
    concurrency: c.concurrency,
    timeout_s: c.timeout_s,
    enabled: c.enabled,
  }
  editing.value = true
  drawer.value = form.value.purpose
  models.value = []
  modelsFetched.value = false
  probe.value = null
  msg.value = ''
  warn.value = ''
}

function resetForm() {
  form.value = blankForm()
  editing.value = false
  models.value = []
  modelsFetched.value = false
  probe.value = null
}

async function run(tag: string, fn: () => Promise<void>) {
  busy.value = tag
  err.value = ''
  try {
    await fn()
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = ''
  }
}

const fetchModels = () =>
  run('models', async () => {
    probe.value = null
    const r = await api.probeLlm({ ...form.value }, 'models')
    probe.value = r
    models.value = r.models || []
    modelsFetched.value = true
    if (r.ok && models.value.length && !models.value.includes(form.value.model_name)) {
      form.value.model_name = models.value[0]
    }
  })

const probeChat = () =>
  run('probe', async () => {
    probe.value = await api.probeLlm({ ...form.value }, 'chat')
  })

/** 保存；这个用途还没有默认模型时，顺手设为默认——
    业务人员的心智是「我配好了就能用」，不是「配完还要再去设一次默认」。 */
const save = () =>
  run('save', async () => {
    const purpose = form.value.purpose
    const r = await api.saveLlmConfig({ ...form.value })
    const cid = r.config_id
    let extra = ''
    let w = r.modality_warning || ''
    const p = purposes.value.find((x) => x.key === purpose)
    if (!p?.default) {
      try {
        const d = await api.setLlmDefault(cid)
        extra = `，并设为「${d.purpose_label}」的默认模型`
        if (d.modality_warning) w = d.modality_warning
      } catch {
        extra = '；设为默认失败，请在卡片里手动设置'
      }
    }
    form.value.api_key = ''
    form.value.config_id = cid
    editing.value = true
    await loadAll()
    drawer.value = ''
    note(`已保存 ${cid}${extra}。${r.note || ''}`, w)
  })

const runTest = (cid: string, kind: 'chat' | 'models') =>
  run(cid + ':' + kind, async () => {
    testResult.value = { ...testResult.value, [cid]: await api.testLlmConfig(cid, kind) }
    await loadAll()
  })

const testDefault = (p: { key: string; default: string | null }) => {
  if (p.default) runTest(p.default, 'chat')
}

const makeDefault = (cid: string) =>
  run(cid + ':default', async () => {
    const r = await api.setLlmDefault(cid)
    note(
      `已把 ${cid} 设为「${r.purpose_label}」的默认模型` +
        (r.replaced ? `，换掉了原来的 ${r.replaced.config_id}` : '') +
        (r.warning ? `。${r.warning}` : ''),
      r.modality_warning,
    )
    await loadAll()
  })

const toggleEnabled = (c: LlmConfig) =>
  run(c.config_id + ':enabled', async () => {
    const r = await api.setLlmEnabled(c.config_id, !c.enabled)
    note(`${c.config_id} 已${r.enabled ? '启用' : '停用'}`, r.warning)
    await loadAll()
  })

const showHistory = (cid: string) =>
  run(cid + ':hist', async () => {
    history.value = {
      cid,
      rows: history.value?.cid === cid ? [] : (await api.llmTestHistory(cid)).items,
    }
  })

const remove = (cid: string) =>
  run(cid + ':del', async () => {
    const r = await api.deleteLlmConfig(cid)
    note(
      `已删除 ${cid}，连带删掉 ${(r as Record<string, unknown>).tests_removed} 条测试历史。`,
      String((r as Record<string, unknown>).note || ''),
    )
    pendingDelete.value = ''
    if (history.value?.cid === cid) history.value = null
    if (form.value.config_id === cid) resetForm()
    await loadAll()
  })

function fmtTime(s: string | null) {
  return s ? String(s).replace('T', ' ').slice(0, 19) : '—'
}
const lastTestAt = (c: LlmConfig | null) => (c?.last_test_at ? fmtTime(c.last_test_at).slice(5, 16) : '')
const purposeLabel = (k: string) => purposes.value.find((p) => p.key === k)?.label || k
</script>

<template>
  <div class="v2">
    <div v-if="err" class="err">{{ err }}</div>
    <div v-if="msg" class="ok-note">{{ msg }}</div>
    <div v-if="warn" class="err">{{ warn }}</div>
    <div v-if="loading" class="muted">读取中…</div>

    <template v-else>
      <!-- ① 页头：白卡里只有四样东西——标题、就绪度、主操作、完成度。
           不放图标、不放眉标：图标只会跟下面的能力卡抢视觉，眉标与顶栏标题重复。
           动作按 Atlassian PageHeader 的约定放在标题行末端。 -->
      <header class="cmd">
        <div class="cmd-row">
          <h2 class="cmd-title">三类 AI 能力，各配一个模型</h2>
          <div class="cmd-right">
            <span class="cmd-chip" :class="okCount === 0 ? 'bad' : okCount === purposes.length ? 'ok' : 'mid'">
              <i></i>{{ okCount }} / {{ purposes.length }} 项就绪
            </span>
            <button v-if="firstTodo" class="btn primary" @click="openDrawer(firstTodo.key)">
              去配置「{{ SHORT[firstTodo.key] || firstTodo.label }}」
            </button>
          </div>
        </div>
        <p class="cmd-desc">
          系统有三处会调用大模型：读图、判定、抽取。这里决定每一处用哪个厂商、哪个型号，
          以及模型不通时会发生什么；保存即生效，不需要重启。
        </p>
        <div class="cmd-prog" role="img" :aria-label="`就绪 ${okCount}/${purposes.length}`">
          <i :class="{ full: okCount === purposes.length }" :style="{ width: progress + '%' }"></i>
        </div>
      </header>

      <!-- ② 三张能力卡（参照 Stripe / Vercel 的 integration card：
           头部标题+状态、当前配置、底部右对齐操作；不放虚框占位块） -->
      <div class="pgrid">
        <article v-for="p in purposes" :key="p.key" class="pcard" :class="stateOf(p)">
          <header class="pc-head">
            <div class="p-ic" aria-hidden="true">
              <svg v-if="PLAIN[p.key]?.icon === 'img'" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.6"/><path d="M21 16l-5-5L5 20"/></svg>
              <svg v-else-if="PLAIN[p.key]?.icon === 'scale'" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M12 3v18M7 6h10M4 12l3-6 3 6a3 3 0 0 1-6 0zM14 12l3-6 3 6a3 3 0 0 1-6 0z"/></svg>
              <svg v-else width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>
            </div>
            <h3 class="pc-title">{{ p.label }}</h3>
            <span class="p-st" :class="stateOf(p)"><i></i>{{ STATE_TEXT[stateOf(p)] }}</span>
          </header>

          <p class="pc-desc">{{ PLAIN[p.key]?.what }}</p>

          <div class="p-model" :class="stateOf(p)">
            <div class="pm-k">当前模型</div>
            <template v-if="cfgById(p.default)">
              <div class="pm-v">{{ cfgById(p.default)!.model_name }}</div>
              <div class="pm-sub">
                {{ cfgById(p.default)!.config_name }}
                <span v-if="cfgById(p.default)!.api_key_mask"> · {{ cfgById(p.default)!.api_key_mask }}</span>
              </div>
              <div class="pm-test">
                <span class="tag" :class="testTag(cfgById(p.default)!.last_test_ok).cls">
                  {{ testTag(cfgById(p.default)!.last_test_ok).text }}
                </span>
                <span class="pm-when">
                  {{ lastTestAt(cfgById(p.default)) ? '最近 ' + lastTestAt(cfgById(p.default)) : '还没测过' }}
                </span>
              </div>
            </template>
            <template v-else>
              <div class="pm-v none">未配置</div>
              <div class="pm-sub">{{ PLAIN[p.key]?.none }}</div>
            </template>
          </div>

          <p class="p-req" :title="PLAIN[p.key]?.req">{{ PLAIN[p.key]?.req }}</p>

          <footer class="pc-ops">
            <button class="btn ghost sm" :disabled="!p.default || !!busy" @click="testDefault(p)">
              {{ busy === (p.default || '') + ':chat' ? '测试中…' : '连通测试' }}
            </button>
            <button class="btn primary sm" :disabled="!!busy" @click="openDrawer(p.key)">
              {{ cfgById(p.default) ? '更换模型' : '配置模型' }}
            </button>
          </footer>
        </article>
      </div>

      <!-- ③ 模型库 -->
      <div class="card" style="margin-bottom: 16px">
        <div class="card-head">
          <h2>模型库</h2>
          <!-- 0 条用中性灰：这不是「待人工」那种业务结论，不该用金色 -->
          <span class="pill num" :class="{ brand: configs.length > 0 }">{{ configs.length }} 条</span>
          <div class="grow"></div>
          <button class="btn primary" @click="openDrawer()">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
            新增配置
          </button>
        </div>

        <div v-if="!configs.length" class="empty">
          <div class="empty-t">还没有任何模型配置</div>
          <div class="empty-d">
            先配最要紧的两档：<b>规则判定</b>决定判不了的单有没有参考意见，
            <b>读图</b>决定票据图能不能读。没配的用途会退回「待人工」，不会假装判过。
          </div>
          <button class="btn ghost" @click="openDrawer()">开始配置</button>
        </div>

        <table v-else class="tbl">
          <thead>
            <tr>
              <!-- 百分比：像素总和一旦大于容器（1080 宽时只有 1046），表格压不下去就出横向滚动条 -->
              <th style="width: 21%">配置</th>
              <th style="width: 16%">用途</th>
              <th style="width: 25%">型号 / 地址</th>
              <th style="width: 12%" class="c-key">Key</th>
              <th style="width: 12%">最近测试</th>
              <th style="width: 14%">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in cfgList.view(configs)" :key="c.config_id">
              <td>
                <div class="c-name">{{ c.config_name || c.config_id }}</div>
                <div class="mono c-id">{{ c.config_id }}</div>
                <span v-if="c.is_default" class="tag t-demo">该用途默认</span>
                <span v-if="!c.enabled" class="tag t-na">已停用</span>
                <!-- 「设为默认」与这个标签是同一件事，放在一起；操作列只留四个动作，窄屏才排得下 -->
                <button v-if="!c.is_default" class="lnk setdef" :disabled="!!busy || !c.enabled || !c.purpose"
                        @click="makeDefault(c.config_id)">设为默认</button>
              </td>
              <td>
                {{ purposeLabel(c.purpose || '') }}
                <!-- 能力不匹配的警告原来放在操作列，14% 宽里折成五行、把操作按钮挤没了；放回用途下面 -->
                <div v-if="c.modality_warning" class="pur-warn" :title="c.modality_warning">{{ c.modality_warning }}</div>
              </td>
              <td>
                <div class="mono" style="font-size: 12px">{{ c.model_name }}</div>
                <div class="mono muted c-url" :title="c.base_url">{{ c.base_url }}</div>
              </td>
              <td class="mono c-key">{{ c.api_key_mask || '—' }}</td>
              <td>
                <span class="tag" :class="testTag(c.last_test_ok).cls">{{ testTag(c.last_test_ok).text }}</span>
                <div v-if="c.last_test_at" class="muted" style="font-size: 11px">
                  {{ fmtTime(c.last_test_at).slice(5, 16) }}
                  <span v-if="c.last_test_latency_ms !== null">· {{ c.last_test_latency_ms }}ms</span>
                </div>
              </td>
              <td>
                <div class="ops">
                  <button class="lnk" @click="edit(c)">编辑</button>
                  <span class="sep"></span>
                  <button class="lnk" :disabled="!!busy" @click="runTest(c.config_id, 'chat')">
                    {{ busy === c.config_id + ':chat' ? '测试中…' : '测试' }}
                  </button>
                  <span class="sep"></span>
                  <button class="lnk" :disabled="!!busy" @click="toggleEnabled(c)">
                    {{ c.enabled ? '停用' : '启用' }}
                  </button>
                  <span class="sep"></span>
                  <button v-if="pendingDelete !== c.config_id" class="lnk bad" :disabled="!!busy"
                          @click="pendingDelete = c.config_id">删除</button>
                  <template v-else>
                    <button class="lnk bad" :disabled="!!busy" @click="remove(c.config_id)">确认删除</button>
                    <button class="lnk" @click="pendingDelete = ''">取消</button>
                  </template>
                </div>
                <div v-if="pendingDelete === c.config_id" class="del-hint">
                  会连带删掉这条的全部测试历史——那是排查「什么时候开始不通的」唯一依据。
                </div>
                <div v-if="testResult[c.config_id]" class="test-out">
                  <span class="tag" :class="testResult[c.config_id].ok ? 't-pass' : 't-hit'">
                    {{ testResult[c.config_id].ok ? '通了' : '失败' }}
                    <template v-if="testResult[c.config_id].latency_ms !== null">
                      · {{ testResult[c.config_id].latency_ms }}ms
                    </template>
                  </span>
                  <span v-if="testResult[c.config_id].reply_excerpt" class="muted">
                    模型回了：{{ testResult[c.config_id].reply_excerpt }}
                  </span>
                  <span v-else-if="testResult[c.config_id].message" class="muted">
                    {{ ERR_LABEL[testResult[c.config_id].err_kind || ''] || testResult[c.config_id].message }}
                  </span>
                </div>

              </td>
            </tr>
            <tr v-if="cfgList.more(configs).show">
              <td :colspan="6" class="morecell" @click="cfgList.more(configs).toggle()">
                {{ cfgList.more(configs).rest === -1 ? '收起' : `展开剩余 ${cfgList.more(configs).rest} 条` }}（共 {{ configs.length }} 条）
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="configs.length" class="footnote">
          「测试」会<b>真的发一次请求到配置的地址</b>，请求体里只有一句固定问候语，不含任何业务数据；
          「列型号」只调 <code>/models</code>，不消耗 token。每次点击都会在
          <code>llm_test_log</code> 留一条记录。没有任何自动或定时调用。
        </div>
      </div>

      <!-- ④ 平台口径（折叠） -->
      <details class="card llm-notes">
        <summary>
          <span class="nt">平台口径</span>
          <span class="nh">Key 怎么存、调用怎么脱敏、测试会不会花钱</span>
          <div class="grow"></div>
          <svg class="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>
        </summary>
        <div class="nbody">
          <div class="nrow">
            <div class="nk">Key 的存储</div>
            <div class="nv">
              <template v-if="keyStatus">
                加密来源：<code>{{ keyStatus.source || '不可用' }}</code>
                <span class="tag" :class="keyStatus.production_ready ? 't-pass' : 't-pend'">
                  {{ keyStatus.production_ready ? '可上生产' : '仅 POC' }}
                </span>
                <div v-if="keyStatus.note" class="nnote"><MarkText :text="keyStatus.note" /></div>
              </template>
              <template v-else>读取中…</template>
              <div v-if="legacyRows.length" class="nl bad">
                有 {{ legacyRows.length }} 条配置的 Key 是旧的单向摘要格式，取不回明文，测试必然失败：
                {{ legacyRows.map((c) => c.config_id).join('、') }}。请逐条重填一次 Key。
              </div>
            </div>
          </div>
          <div class="nrow">
            <div class="nk">调用口径</div>
            <div class="nv">
              <div><MarkText :text="policy" /></div>
              <div class="nnote">
                另外一条同等重要：<b>送进模型前就要脱敏，不能只脱回来的</b>。请求体一旦发出就离开了本系统的控制范围。
                <code>llm_call_log.prompt_masked</code> 为 FALSE 的行出现在生产，说明有一条调用路径绕过了脱敏。
              </div>
            </div>
          </div>
          <div v-if="defaultsNote" class="nrow">
            <div class="nk">默认模型</div>
            <div class="nv"><MarkText :text="defaultsNote" /></div>
          </div>
          <div v-if="presetNote" class="nrow">
            <div class="nk">预置厂商</div>
            <div class="nv">
              <div class="chips" style="margin-bottom: 8px">
                <span v-for="p in presets.filter((x) => x.verified_on).slice(0, 10)" :key="p.preset_id"
                      class="chip">{{ p.label }}</span>
              </div>
              <div class="nnote"><MarkText :text="presetNote" /></div>
            </div>
          </div>
        </div>
      </details>
    </template>

    <!-- 配置抽屉 -->
    <template v-if="drawer">
      <div class="dr-mask" @click.self="closeDrawer"></div>
      <aside class="dr" role="dialog" aria-modal="true" aria-label="模型配置">
        <div class="dr-head">
          <div>
            <div class="dr-t">{{ editing ? '编辑配置' : '配置模型' }}</div>
            <div class="dr-s">{{ drawerPurpose?.label || purposeLabel(form.purpose) }}</div>
          </div>
          <button class="dr-x" aria-label="关闭" @click="closeDrawer">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
          </button>
        </div>

        <div class="dr-body">
          <p class="dr-what">{{ PLAIN[form.purpose]?.what }}</p>

          <div v-if="candidates(form.purpose).length" class="dr-sec">
            <div class="dr-k">这个用途现有的模型</div>
            <div v-for="c in candidates(form.purpose)" :key="c.config_id" class="cfgrow"
                 :class="{ on: form.config_id === c.config_id }">
              <div class="cfg-main">
                <div class="cfg-model">{{ c.model_name }}</div>
                <div class="cfg-sub">{{ c.config_name }} · {{ c.api_key_mask || '无 Key' }}</div>
              </div>
              <span class="tag" :class="testTag(c.last_test_ok).cls">{{ testTag(c.last_test_ok).text }}</span>
              <button v-if="!c.is_default" class="lnk" :disabled="!!busy"
                      @click="makeDefault(c.config_id)">设为默认</button>
              <span v-else class="tag t-demo">默认</span>
              <button class="lnk" @click="edit(c)">编辑</button>
            </div>
          </div>

          <div class="dr-sec">
            <div class="dr-k">{{ editing ? '修改这条配置' : '新增一条配置' }}</div>

            <label class="fl">厂商</label>
            <select class="input w100" :value="form.preset_id"
                    @change="pickPreset(($event.target as HTMLSelectElement).value)">
              <option value="">— 手动填写 —</option>
              <option v-for="p in presets" :key="p.preset_id" :value="p.preset_id">
                {{ p.label }}{{ p.verified_on ? '' : '（地址未核对）' }}
              </option>
            </select>
            <div v-if="preset" class="phint">
              {{ preset.label }}
              <span class="tag" :class="preset.modality === 'text' ? 't-na' : 't-pass'">
                {{ preset.modality === 'text' ? '纯文本' : preset.modality === 'vision' ? '读图' : '文本+读图' }}
              </span>
              <template v-if="preset.verified_on">· 地址已对过官方文档（{{ preset.verified_on }}）</template>
              <template v-else>· 地址没对过官方文档，配不通先怀疑这里</template>
            </div>

            <label class="fl">Base URL</label>
            <input v-model="form.base_url" class="input w100" placeholder="https://…/v1">

            <label class="fl">API Key</label>
            <div class="keybox">
              <input v-model="form.api_key" :type="showKey ? 'text' : 'password'" class="input w100"
                     :placeholder="editing ? '留空表示不改动已存的 Key' : '粘贴厂商的 Key'"
                     autocomplete="new-password">
              <button type="button" class="keyeye" :aria-label="showKey ? '隐藏 Key' : '显示 Key'"
                      :title="showKey ? '隐藏' : '显示'" @click="showKey = !showKey">
                <svg v-if="!showKey" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>
                <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M2 12s3.5-7 10-7c2 0 3.7.7 5.2 1.7M22 12s-3.5 7-10 7c-2 0-3.7-.7-5.2-1.7"/><path d="M4 4l16 16"/></svg>
              </button>
            </div>

            <label class="fl">型号</label>
            <div class="row">
              <select v-if="models.length" v-model="form.model_name" class="input" style="flex: 1">
                <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
              </select>
              <input v-else v-model="form.model_name" class="input" style="flex: 1"
                     placeholder="点右侧「拉取型号」或手填">
              <button class="btn ghost" :disabled="!!busy || !form.base_url || preset?.model_list === false"
                      @click="fetchModels">
                {{ busy === 'models' ? '拉取中…' : '拉取型号' }}
              </button>
            </div>
            <div v-if="modelsFetched && !models.length" class="phint">
              这家没回型号列表。可能不支持 <code>/models</code>，也可能是 Key 不对——看下面的原文。
            </div>

            <details class="adv">
              <summary>高级参数（默认值通常不用改）</summary>
              <div class="adv-grid">
                <div><label class="fl">温度</label>
                  <input v-model.number="form.temperature" type="number" step="0.1" min="0" max="2" class="input">
                  <div class="phint">审核场景建议 0</div>
                </div>
                <div><label class="fl">最大输出</label>
                  <input v-model.number="form.max_tokens" type="number" class="input" placeholder="厂商默认">
                </div>
                <div><label class="fl">并发</label>
                  <input v-model.number="form.concurrency" type="number" min="1" class="input">
                </div>
                <div><label class="fl">超时（秒）</label>
                  <input v-model.number="form.timeout_s" type="number" min="1" class="input">
                </div>
                <div class="adv-full">
                  <label class="ckline"><input type="checkbox" v-model="form.enable_thinking"> 深度思考（只有支持的型号有效，更慢更贵）</label>
                </div>
                <div class="adv-full">
                  <label class="ckline"><input type="checkbox" v-model="form.enabled"> 启用这条配置</label>
                </div>
              </div>
            </details>

            <div v-if="modalityWarn" class="err" style="margin-top: 12px">
              <b>用途与模型能力不匹配</b>
              <div style="margin-top: 4px">{{ modalityWarn }}</div>
            </div>
            <div v-if="isHttp" class="note" style="margin-top: 12px">
              这是 <code>http://</code> 地址，<b>API Key 会明文过网</b>。内网不等于安全。
            </div>

            <div v-if="probe" :class="probe.ok ? 'ok-note' : 'err'" style="margin-top: 12px">
              <div>
                <b>{{ probe.ok ? '连通正常' : '连不通' }}</b>
                <span v-if="probe.err_kind"> · {{ ERR_LABEL[probe.err_kind] || probe.err_kind }}</span>
                <span v-if="probe.http_status"> · HTTP {{ probe.http_status }}</span>
                <span v-if="probe.latency_ms !== null"> · {{ probe.latency_ms }}ms</span>
              </div>
              <div v-if="probe.reply_excerpt" style="margin-top: 4px">
                模型回了：<b>{{ probe.reply_excerpt }}</b>
              </div>
              <div v-if="probe.message" class="mono" style="margin-top: 4px; font-size: 11px">
                {{ probe.message }}
              </div>
            </div>

            <div v-if="form.config_id" class="hist">
              <button class="lnk" :disabled="!!busy" @click="showHistory(form.config_id)">
                {{ history?.cid === form.config_id ? '收起测试历史' : '看测试历史' }}
              </button>
              <table v-if="history?.cid === form.config_id && history.rows.length" class="tbl mini">
                <thead><tr><th>时间</th><th>结果</th><th class="num">耗时</th></tr></thead>
                <tbody>
                  <tr v-for="t in history.rows.slice(0, 6)" :key="t.id">
                    <td class="mono">{{ fmtTime(t.tested_at).slice(5, 16) }}</td>
                    <td>
                      <span class="tag" :class="t.ok ? 't-pass' : 't-hit'">
                        {{ t.ok ? '通' : (ERR_LABEL[t.err_kind || ''] || '失败') }}
                      </span>
                    </td>
                    <td class="num">{{ t.latency_ms ?? '—' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div class="dr-foot">
          <button class="btn ghost" :disabled="!!busy || !form.base_url || !form.model_name"
                  @click="probeChat">
            {{ busy === 'probe' ? '测试中…' : '先测一下' }}
          </button>
          <button class="btn primary" :disabled="!!busy || !form.base_url || !form.model_name"
                  @click="save">
            {{ busy === 'save' ? '保存中…' : editing ? '保存修改' : '保存并启用' }}
          </button>
        </div>
      </aside>
    </template>
  </div>
</template>

<style scoped>
/* 类名带 llm-/p-/dr- 前缀：design.css 里有 .card/.panel 等通用类，
   短名会串样式（用户与角色那一页踩过）。 */
.muted { color: var(--muted); }
.mono { font-family: Consolas, 'Courier New', monospace; }

/* ① 页头：白卡 + 四样东西。留白靠 16/7/13 三段垂直节奏拉开层级，
   不靠图标和色块撑场——那些只会跟下面三张能力卡抢注意力。 */
.cmd {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-m);
  padding: 16px 20px 18px; margin-bottom: 16px;
}
.cmd-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.cmd-title {
  flex: 1; min-width: 0; margin: 0;
  font-size: 18px; font-weight: 600; color: var(--text); letter-spacing: -.2px; line-height: 1.4;
}
.cmd-right { display: flex; align-items: center; gap: 10px; flex: none; }
.cmd-right .btn { height: 30px; padding: 0 14px; font-size: 12.5px; }

.cmd-chip {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 11.5px; padding: 2px 9px; border-radius: 999px; white-space: nowrap;
  color: var(--muted); background: var(--panel-2); border: 1px solid var(--line);
}
.cmd-chip i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.cmd-chip.bad { color: #b42318; background: #fef3f2; border-color: #fecdca; }
.cmd-chip.ok { color: #067647; background: #ecfdf3; border-color: #abefc6; }
[data-theme='dark'] .cmd-chip.bad { color: #f97066; background: #2a1512; border-color: #5c221c; }
[data-theme='dark'] .cmd-chip.ok { color: #6ce9a6; background: #0e2a1e; border-color: #1d5a3c; }

.cmd-desc { font-size: 12.5px; color: var(--muted); line-height: 1.7; margin: 7px 0 0; max-width: 820px; }
.cmd-prog {
  /* 0% 时 --panel-2 的白条几乎看不见，会像一条多余的描边；用 --line 让空轨道可辨 */
  height: 3px; border-radius: 2px; background: var(--line);
  margin-top: 13px; overflow: hidden;
}
.cmd-prog i {
  display: block; height: 100%; border-radius: 2px;
  background: var(--brand); transition: width .3s ease;
}
.cmd-prog i.full { background: #32d583; }

/* ② 能力卡
   状态色：未配置 / 测试不通 = 淡红（#B42318 on #FEF3F2）；已就绪 = 淡绿（#067647 on #ECFDF3）；
   已停用 = 中性灰。**不用品牌金/棕**——金色在这套系统里表示「待人工」，是业务结论；
   「未配置」是配置缺失，两回事，用错了色会让人以为系统在说「这单要人工看」。
   也不上饱和红绿：只用 6px 圆点 + 极浅底 + 深一档的文字，避免整张卡在页面上发光。 */
.pgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; margin-bottom: 16px; }

.pcard {
  --st-fg: var(--weak);
  --st-bg: var(--panel-2);
  --st-line: var(--line);
  display: flex; flex-direction: column; min-width: 0;
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-m);
  padding: 16px 18px 14px;
  transition: border-color .15s, box-shadow .15s;
}
.pcard:hover { border-color: var(--line-strong); box-shadow: var(--shadow-1); }
.pcard.missing, .pcard.fail { --st-fg: #b42318; --st-bg: #fef3f2; --st-line: #fecdca; }
.pcard.ok { --st-fg: #067647; --st-bg: #ecfdf3; --st-line: #abefc6; }
.pcard.untested { --st-fg: #475467; --st-bg: var(--panel-2); --st-line: var(--line); }
[data-theme='dark'] .pcard.missing,
[data-theme='dark'] .pcard.fail { --st-fg: #f97066; --st-bg: #2a1512; --st-line: #5c221c; }
[data-theme='dark'] .pcard.ok { --st-fg: #6ce9a6; --st-bg: #0e2a1e; --st-line: #1d5a3c; }
[data-theme='dark'] .pcard.untested { --st-fg: #98a2b3; }

.pc-head { display: flex; align-items: center; gap: 10px; }
.p-ic {
  width: 30px; height: 30px; flex: none; border-radius: 7px;
  background: var(--brand-bg); color: var(--brand); display: grid; place-items: center;
}
.pc-title { flex: 1; min-width: 0; font-size: 14px; font-weight: 600; color: var(--text); margin: 0; line-height: 1.45; }
.p-st { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; color: var(--st-fg); white-space: nowrap; flex: none; }
.p-st i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; display: inline-block; }

.pc-desc { font-size: 12.5px; color: var(--muted); line-height: 1.6; margin: 10px 0 0; min-height: 40px; }

.p-model {
  margin-top: 12px; padding: 11px 13px; border-radius: var(--r-s);
  background: var(--panel-2); border: 1px solid var(--line);
}
.pcard.missing .p-model, .pcard.fail .p-model { background: var(--st-bg); border-color: var(--st-line); }
.pm-k { font-size: 11px; color: var(--weak); }
.pm-v { font-size: 15px; font-weight: 600; color: var(--text); margin-top: 2px; word-break: break-all; line-height: 1.35; }
.pm-v.none { color: var(--st-fg); font-size: 14px; }
.pm-sub { font-size: 11.5px; color: var(--weak); margin-top: 3px; line-height: 1.55; }
.pcard.missing .pm-sub, .pcard.fail .pm-sub { color: var(--muted); }
.pm-test { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.pm-when { font-size: 11px; color: var(--weak); }

.p-req {
  font-size: 11px; color: var(--weak); line-height: 1.55; margin: 10px 0 0; flex: 1;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

.pc-ops { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.pc-ops .btn { height: 30px; padding: 0 14px; font-size: 12.5px; }

/* ③ 模型库 */
.c-name { font-size: 13px; color: var(--text); }
.setdef { margin-top: 4px; font-size: 11.5px; }
.pur-warn {
  margin-top: 4px; font-size: 11px; line-height: 1.5; color: var(--bad);
  /* 最多两行，悬停看全文——整段摊开会把这一行撑成五行高 */
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.c-id { font-size: 11px; color: var(--weak); margin-top: 1px; }
.c-url { font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 240px; }
.ops { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.sep { width: 1px; height: 12px; background: var(--line); }
.lnk { border: 0; background: none; padding: 0; min-height: 0; height: auto; font-size: 12.5px; color: var(--brand); cursor: pointer; }
.lnk:hover { text-decoration: underline; background: none; border: 0; }
.lnk:disabled { color: var(--weak); cursor: not-allowed; text-decoration: none; }
.lnk.bad { color: var(--bad); }
.del-hint { margin-top: 6px; font-size: 11px; color: var(--bad); line-height: 1.55; }
.test-out { margin-top: 6px; display: flex; gap: 8px; align-items: baseline; font-size: 11.5px; flex-wrap: wrap; }
.morecell { text-align: center; color: var(--brand); cursor: pointer; font-size: 13px; }
.footnote { padding: 12px 20px 16px; font-size: 12px; color: var(--weak); line-height: 1.75; border-top: 1px dashed var(--panel-2); }
.empty { padding: 28px 20px 32px; text-align: center; }
.empty-t { font-size: 15px; font-weight: 600; color: var(--text); }
.empty-d { font-size: 12.5px; color: var(--muted); line-height: 1.75; max-width: 560px; margin: 8px auto 16px; }

/* ④ 平台口径 */
.llm-notes { overflow: hidden; }
.llm-notes > summary { list-style: none; display: flex; align-items: center; gap: 10px; padding: 14px 20px; cursor: pointer; }
.llm-notes > summary::-webkit-details-marker { display: none; }
.llm-notes[open] > summary { border-bottom: 1px solid var(--panel-2); }
.nt { font-size: 14px; font-weight: 600; color: var(--text); }
.nh { font-size: 12px; color: var(--weak); }
.chev { color: var(--weak); transition: transform .15s; flex: none; }
.llm-notes[open] .chev { transform: rotate(180deg); }
.nbody { padding: 6px 20px 16px; }
.nrow { display: grid; grid-template-columns: 96px 1fr; gap: 16px; padding: 10px 0; border-bottom: 1px solid var(--panel-2); font-size: 12.5px; }
.nrow:last-child { border-bottom: 0; }
.nk { color: var(--weak); }
.nv { color: var(--muted); line-height: 1.7; min-width: 0; }
.nnote { font-size: 11.5px; color: var(--weak); margin-top: 4px; line-height: 1.7; }
.nl.bad { color: var(--bad); margin-top: 6px; font-size: 11.5px; }

/* 抽屉 */
.dr-mask { position: fixed; inset: 0; background: rgba(10, 16, 24, .38); z-index: 70; }
.dr {
  position: fixed; top: 0; right: 0; bottom: 0; width: 480px; max-width: 94vw;
  background: var(--panel); border-left: 1px solid var(--line);
  box-shadow: var(--shadow-2); z-index: 71; display: flex; flex-direction: column;
  animation: drin .18s ease-out;
}
@keyframes drin { from { transform: translateX(18px); opacity: .5; } to { transform: none; opacity: 1; } }
.dr-head { display: flex; align-items: flex-start; gap: 12px; padding: 16px 20px 13px; border-bottom: 1px solid var(--panel-2); }
.dr-t { font-size: 15px; font-weight: 600; color: var(--text); }
.dr-s { font-size: 12px; color: var(--muted); margin-top: 2px; }
.dr-head .dr-x {
  margin-left: auto; padding: 0; width: 28px; min-height: 28px; height: 28px;
  border-radius: 6px; border: 0; background: none; color: var(--weak);
  display: grid; place-items: center; cursor: pointer;
}
.dr-head .dr-x:hover { background: var(--panel-2); color: var(--text); border-color: transparent; }
.dr-body { flex: 1; overflow: auto; padding: 14px 20px 8px; }
.dr-what { font-size: 12px; color: var(--muted); line-height: 1.7; margin: 0 0 14px; padding: 10px 12px; background: var(--panel-2); border-radius: var(--r-s); }
.dr-sec { margin-bottom: 16px; }
.dr-k { font-size: 12px; color: var(--weak); margin-bottom: 8px; }
.dr-foot { padding: 12px 20px; border-top: 1px solid var(--panel-2); display: flex; gap: 8px; justify-content: flex-end; }

.cfgrow {
  display: flex; align-items: center; gap: 10px; padding: 9px 10px;
  border: 1px solid var(--line); border-radius: var(--r-s); margin-bottom: 6px;
}
.cfgrow.on { border-color: var(--brand); background: var(--brand-bg); }
.cfg-main { min-width: 0; flex: 1; }
.cfg-model { font-size: 12.5px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cfg-sub { font-size: 11px; color: var(--weak); margin-top: 1px; }

.fl { display: block; font-size: 12px; color: var(--weak); margin: 12px 0 5px; }
/* Key 输入框：右侧一个「显示/隐藏」按钮。看不到自己粘了什么，粘错了只能重粘。 */
.keybox { position: relative; }
.keybox input { padding-right: 36px; }
.keyeye {
  position: absolute; right: 4px; top: 50%; transform: translateY(-50%);
  width: 28px; height: 26px; border: 0; border-radius: 6px; background: none;
  color: var(--weak); display: grid; place-items: center; cursor: pointer;
}
.keyeye:hover { background: var(--panel-2); color: var(--text); }
.w100 { width: 100%; }
.row { display: flex; gap: 8px; align-items: center; }
.phint { font-size: 11px; color: var(--weak); margin-top: 5px; line-height: 1.6; }
.adv { margin-top: 14px; border-top: 1px dashed var(--panel-2); padding-top: 10px; }
.adv > summary { list-style: none; font-size: 12px; color: var(--weak); cursor: pointer; }
.adv > summary::-webkit-details-marker { display: none; }
.adv > summary::before { content: '▸ '; }
.adv[open] > summary::before { content: '▾ '; }
.adv-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
.adv-full { grid-column: span 2; }
.ckline { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--muted); margin-top: 10px; }
.ckline input { width: 15px; height: 15px; accent-color: var(--brand); }
.hist { margin-top: 14px; }
.tbl.mini { margin-top: 8px; }
.tbl.mini th, .tbl.mini td { padding: 6px 8px; font-size: 12px; }
.ok-note {
  background: var(--ok-bg); border: 1px solid #abefc6; border-radius: 8px;
  padding: 8px 12px; margin-bottom: 12px; font-size: 12.5px; color: var(--ok);
}

@media (max-width: 1180px) {
  .c-key { display: none; }
  .pgrid { grid-template-columns: 1fr; }
}
</style>
