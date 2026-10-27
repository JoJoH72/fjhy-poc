<script setup lang="ts">
/**
 * 系统 · 大模型配置（2026-10-27 AntD 换装版）。
 *
 * 信息架构（2026-10-25 定稿）：**这一页服务的是「三类 AI 能力各用哪个模型」，
 * 不是一张模型参数表。**
 *
 *   ① 顶部一句话说清这页干什么，右侧给「3 项里几项就绪」
 *   ② 三张用途卡：这个能力负责什么、现在用哪个模型、测过没有、没配会怎样；
 *      按钮只有两个——「配置模型」和「连通测试」
 *   ③ 点「配置模型」开右侧抽屉：选厂商 → 填 Key → 拉型号 → 测通 → 保存并启用
 *   ④ 模型库（表格）留给需要横向对比的人
 *   ⑤ 平台口径（Key 怎么存、调用怎么脱敏、测试花不花钱）收进折叠区
 *
 * 下面这些行为是**刻意保留**的，不是风格问题：
 *   1. 用途 × 模型能力的交叉校验：读图配纯文本模型不会报错，模型会按文字线索
 *      编一段看起来合理的内容，而下游当成「已核验的票面信息」。
 *   2.「没测过」和「测了不通」是两个状态，不合并。
 *   3. 型号不写死，按「拉取型号」从厂商现取（型号半年就漂移）。
 *   4. 删除要点两次：会连带删掉测试历史，而历史是排查「哪天开始不通」的唯一依据。
 */
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { api } from '../api'
import type { LlmConfig, LlmPreset, LlmTestResult, LlmTestRow } from '../api'
import { ERR_LABEL, modalityWarning, testTag } from '../data/llmState'

/** testTag 的旧 token 色（t-na/t-pass/t-hit）→ AntD 色名 */
function antTag(lastTestOk: boolean | null | undefined): { color: string; text: string } {
  const t = testTag(lastTestOk)
  return {
    color: t.cls === 't-pass' ? 'success' : t.cls === 't-hit' ? 'error' : 'default',
    text: t.text,
  }
}

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

/** 三类用途的人话说明：只说「它负责什么、没配会怎样、选错会怎样」 */
const PLAIN: Record<string, { what: string; req: string; none: string }> = {
  vision_read: {
    what: '看发票、行程单、审批单的照片，把票面信息读成结构化字段。',
    req: '必须选多模态模型；选成纯文本模型不会报错，它会照着文字线索编内容。',
    none: '没配：图片类票据读不出字段，相关规则全部落到待人工。',
  },
  rule_judge: {
    what: '规则判不了的单项，给一条参考意见，供审核岗复核时参考。',
    req: '长文本推理；温度建议 0，同一张单两次跑出不同结论没法向审核岗解释。',
    none: '没配：超出规则范围的单项直接标「待人工：未调用模型」，不会假装判过。',
  },
  extract: {
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
const busy = ref('')
const probe = ref<LlmTestResult | null>(null)
const testResult = ref<Record<string, LlmTestResult>>({})
const history = ref<{ cid: string; rows: LlmTestRow[] } | null>(null)
const pendingDelete = ref('')

const preset = computed(() => presets.value.find((p) => p.preset_id === form.value.preset_id))
const isHttp = computed(() => form.value.base_url.startsWith('http://'))
// 把型号名和「已经配成文本用途的型号」一起带进去：预置选对了但型号填成文本模型也要报。
const textModels = computed(() =>
  configs.value
    .filter((c) => c.purpose === 'rule_judge' || c.purpose === 'extract')
    .map((c) => c.model_name)
    .filter((m): m is string => !!m),
)
const modalityWarn = computed(() =>
  // 空白表单不报「能力不匹配」——还没开始填不是犯错。
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
  // 配了但没测过 ≠ 通过：合成绿色会让用户以为能用，跑批时才发现不通。
  if (c.last_test_ok === null || c.last_test_ok === undefined) return 'untested'
  return 'ok'
}
const SHORT: Record<string, string> = {
  vision_read: '读图', rule_judge: '判定', extract: '抽取',
}
const STATE_TEXT: Record<string, string> = {
  ok: '已就绪', untested: '已配置 · 未测试', missing: '未配置', off: '已停用', fail: '测试未通过',
}
const STATE_COLOR: Record<string, string> = {
  ok: 'success', untested: 'default', missing: 'error', off: 'default', fail: 'error',
}
const okCount = computed(() => purposes.value.filter((p) => stateOf(p) === 'ok').length)
const todoCount = computed(() => purposes.value.length - okCount.value)
const progress = computed(() =>
  purposes.value.length ? Math.round((okCount.value / purposes.value.length) * 100) : 0)
const firstTodo = computed(() => purposes.value.find((p) => stateOf(p) !== 'ok') || null)

/* ── 配置抽屉 ─────────────────────────────────────────────────────────── */
const drawer = ref(false)
const drawerPurpose = computed(() => purposes.value.find((p) => p.key === form.value.purpose) || null)

function openDrawer(purposeKey?: string) {
  resetForm()
  const key = purposeKey || purposes.value.find((p) => !p.default)?.key || 'rule_judge'
  form.value.purpose = key
  drawer.value = true
  msg.value = ''
  warn.value = ''
}
function closeDrawer() {
  drawer.value = false
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
  if (m) message.success(m)
  if (w) message.warning(w)
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
  drawer.value = true
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
    message.error(err.value)
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
    drawer.value = false
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

const cfgCols = [
  { title: '配置', key: 'name', width: 210 },
  { title: '用途', key: 'purpose', width: 150 },
  { title: '型号 / 地址', key: 'model', width: 250 },
  { title: '最近测试', key: 'test', width: 150 },
  { title: '操作', key: 'op', width: 260 },
]
</script>

<template>
  <div class="llm">
    <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
    <a-alert v-if="warn && !msg" type="warning" show-icon :message="warn" style="margin-bottom: 12px" />
    <div v-if="loading" class="dim">读取中…</div>

    <template v-else>
      <!-- ① 页头 -->
      <a-card size="small" class="blk">
        <div class="cmd-row">
          <h2 class="cmd-title">三类 AI 能力，各配一个模型</h2>
          <a-tag :color="okCount === 0 ? 'error' : okCount === purposes.length ? 'success' : 'processing'">
            {{ okCount }} / {{ purposes.length }} 项就绪
          </a-tag>
          <a-button v-if="firstTodo" type="primary" @click="openDrawer(firstTodo.key)">
            去配置「{{ SHORT[firstTodo.key] || firstTodo.label }}」
          </a-button>
        </div>
        <p class="cmd-desc">
          系统有三处会调用大模型：读图、判定、抽取。这里决定每一处用哪个厂商、哪个型号，
          以及模型不通时会发生什么；保存即生效，不需要重启。
        </p>
        <a-progress :percent="progress" :show-info="false" size="small"
          :stroke-color="progress === 100 ? '#52c41a' : '#1677ff'" style="margin-top: 8px" />
      </a-card>

      <!-- ② 三张能力卡（自绘结构：状态语义 AntD 卡片表达不了） -->
      <a-row :gutter="[16, 16]" class="blk">
        <a-col v-for="p in purposes" :key="p.key" :xs="24" :md="12" :xl="8">
          <a-card size="small" class="pcard" :class="stateOf(p)">
            <div class="pc-head">
              <h3 class="pc-title">{{ p.label }}</h3>
              <a-tag :color="STATE_COLOR[stateOf(p)]">{{ STATE_TEXT[stateOf(p)] }}</a-tag>
            </div>
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
                  <a-tag :color="antTag(cfgById(p.default)!.last_test_ok).color">
                    {{ antTag(cfgById(p.default)!.last_test_ok).text }}
                  </a-tag>
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
            <div class="pc-ops">
              <a-button size="small" :disabled="!p.default || !!busy" @click="testDefault(p)">
                {{ busy === (p.default || '') + ':chat' ? '测试中…' : '连通测试' }}
              </a-button>
              <a-button type="primary" size="small" :disabled="!!busy" @click="openDrawer(p.key)">
                {{ cfgById(p.default) ? '更换模型' : '配置模型' }}
              </a-button>
            </div>
          </a-card>
        </a-col>
      </a-row>

      <!-- ③ 模型库 -->
      <a-card size="small" class="blk" title="模型库">
        <template #extra>
          <a-tag :color="configs.length > 0 ? 'blue' : 'default'">{{ configs.length }} 条</a-tag>
          <a-button type="primary" size="small" style="margin-left: 8px" @click="openDrawer()">新增配置</a-button>
        </template>

        <a-empty v-if="!configs.length" style="padding: 24px 0"
          description="还没有任何模型配置——先配最要紧的两档：规则判定决定判不了的单有没有参考意见，读图决定票据图能不能读。没配的用途会退回「待人工」，不会假装判过。">
          <a-button @click="openDrawer()">开始配置</a-button>
        </a-empty>

        <a-table v-else :columns="cfgCols" :data-source="configs" row-key="config_id"
          size="small" :pagination="configs.length > 10 ? { pageSize: 10, showTotal: (n: number) => `共 ${n} 条` } : false">
          <template #bodyCell="{ column, record: c }">
            <template v-if="column.key === 'name'">
              <div class="c-name">{{ c.config_name || c.config_id }}</div>
              <div class="dim c-id">{{ c.config_id }}</div>
              <a-tag v-if="c.is_default" color="processing">该用途默认</a-tag>
              <a-tag v-if="!c.enabled" color="default">已停用</a-tag>
              <a v-if="!c.is_default" class="lnk" :class="{ off: !!busy || !c.enabled || !c.purpose }"
                 @click="!(busy || !c.enabled || !c.purpose) && makeDefault(c.config_id)">设为默认</a>
            </template>
            <template v-else-if="column.key === 'purpose'">
              {{ purposeLabel(c.purpose || '') }}
              <div v-if="c.modality_warning" class="pur-warn" :title="c.modality_warning">{{ c.modality_warning }}</div>
            </template>
            <template v-else-if="column.key === 'model'">
              <div class="code">{{ c.model_name }}</div>
              <div class="dim code c-url" :title="c.base_url">{{ c.base_url }}</div>
            </template>
            <template v-else-if="column.key === 'test'">
              <a-tag :color="antTag(c.last_test_ok).color">{{ antTag(c.last_test_ok).text }}</a-tag>
              <div v-if="c.last_test_at" class="dim" style="font-size: 11px">
                {{ fmtTime(c.last_test_at).slice(5, 16) }}
                <span v-if="c.last_test_latency_ms !== null">· {{ c.last_test_latency_ms }}ms</span>
              </div>
            </template>
            <template v-else-if="column.key === 'op'">
              <a class="lnk" @click="edit(c)">编辑</a>
              <a-divider type="vertical" />
              <a class="lnk off-when(busy)" :class="{ off: !!busy }" @click="!busy && runTest(c.config_id, 'chat')">
                {{ busy === c.config_id + ':chat' ? '测试中…' : '测试' }}
              </a>
              <a-divider type="vertical" />
              <a class="lnk" :class="{ off: !!busy }" @click="!busy && toggleEnabled(c)">
                {{ c.enabled ? '停用' : '启用' }}
              </a>
              <a-divider type="vertical" />
              <template v-if="pendingDelete !== c.config_id">
                <a class="lnk bad" :class="{ off: !!busy }" @click="!busy && (pendingDelete = c.config_id)">删除</a>
              </template>
              <template v-else>
                <a class="lnk bad" :class="{ off: !!busy }" @click="!busy && remove(c.config_id)">确认删除</a>
                <a class="lnk" @click="pendingDelete = ''">取消</a>
              </template>
              <div v-if="pendingDelete === c.config_id" class="del-hint">
                会连带删掉这条的全部测试历史——那是排查「什么时候开始不通的」唯一依据。
              </div>
              <div v-if="testResult[c.config_id]" class="test-out">
                <a-tag :color="testResult[c.config_id].ok ? 'success' : 'error'">
                  {{ testResult[c.config_id].ok ? '通了' : '失败' }}
                  <template v-if="testResult[c.config_id].latency_ms !== null">
                    · {{ testResult[c.config_id].latency_ms }}ms
                  </template>
                </a-tag>
                <span v-if="testResult[c.config_id].reply_excerpt" class="dim">
                  模型回了：{{ testResult[c.config_id].reply_excerpt }}
                </span>
                <span v-else-if="testResult[c.config_id].message" class="dim">
                  {{ ERR_LABEL[testResult[c.config_id].err_kind || ''] || testResult[c.config_id].message }}
                </span>
              </div>
            </template>
          </template>
        </a-table>

        <div v-if="configs.length" class="footnote">
          「测试」会<b>真的发一次请求到配置的地址</b>，请求体里只有一句固定问候语，不含任何业务数据；
          「列型号」只调 <code>/models</code>，不消耗 token。每次点击都会留一条测试记录。
          没有任何自动或定时调用。
        </div>
      </a-card>

      <!-- ④ 平台口径（折叠） -->
      <a-collapse>
        <a-collapse-panel key="notes">
          <template #header>
            <b>平台口径</b>
            <span class="dim" style="margin-left: 10px">Key 怎么存、调用怎么脱敏、测试会不会花钱</span>
          </template>
          <div class="nrow">
            <div class="nk">Key 的存储</div>
            <div class="nv">
              <template v-if="keyStatus">
                加密来源：<code>{{ keyStatus.source || '不可用' }}</code>
                <a-tag :color="keyStatus.production_ready ? 'success' : 'warning'">
                  {{ keyStatus.production_ready ? '可上生产' : '仅 POC' }}
                </a-tag>
                <div v-if="keyStatus.note" class="nnote"><MarkText :text="keyStatus.note" /></div>
              </template>
              <template v-else>读取中…</template>
              <div v-if="legacyRows.length" class="nl-bad">
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
              <div style="margin-bottom: 8px">
                <a-tag v-for="p in presets.filter((x) => x.verified_on).slice(0, 10)"
                       :key="p.preset_id">{{ p.label }}</a-tag>
              </div>
              <div class="nnote"><MarkText :text="presetNote" /></div>
            </div>
          </div>
        </a-collapse-panel>
      </a-collapse>
    </template>

    <!-- 配置抽屉 -->
    <a-drawer v-model:open="drawer" width="500" placement="right"
      :title="editing ? '编辑配置' : '配置模型'">
      <template #extra>
        <span class="dim">{{ drawerPurpose?.label || purposeLabel(form.purpose) }}</span>
      </template>

      <div class="dr-what">{{ PLAIN[form.purpose]?.what }}</div>

      <div v-if="candidates(form.purpose).length" class="dr-sec">
        <div class="dr-k">这个用途现有的模型</div>
        <div v-for="c in candidates(form.purpose)" :key="c.config_id" class="cfgrow"
             :class="{ on: form.config_id === c.config_id }">
          <div class="cfg-main">
            <div class="cfg-model">{{ c.model_name }}</div>
            <div class="cfg-sub">{{ c.config_name }} · {{ c.api_key_mask || '无 Key' }}</div>
          </div>
          <a-tag :color="antTag(c.last_test_ok).color">{{ antTag(c.last_test_ok).text }}</a-tag>
          <a v-if="!c.is_default" class="lnk" :class="{ off: !!busy }"
             @click="!busy && makeDefault(c.config_id)">设为默认</a>
          <a-tag v-else color="processing">默认</a-tag>
          <a class="lnk" @click="edit(c)">编辑</a>
        </div>
      </div>

      <div class="dr-sec">
        <div class="dr-k">{{ editing ? '修改这条配置' : '新增一条配置' }}</div>

        <a-form layout="vertical">
          <a-form-item label="厂商">
            <a-select :value="form.preset_id" style="width: 100%"
              :options="[
                { value: '', label: '— 手动填写 —' },
                ...presets.map((p) => ({ value: p.preset_id,
                  label: p.label + (p.verified_on ? '' : '（地址未核对）') })),
              ]"
              @change="(v: any) => pickPreset(String(v))" />
            <div v-if="preset" class="phint">
              {{ preset.label }}
              <a-tag :color="preset.modality === 'text' ? 'default' : 'success'">
                {{ preset.modality === 'text' ? '纯文本' : preset.modality === 'vision' ? '读图' : '文本+读图' }}
              </a-tag>
              <template v-if="preset.verified_on">· 地址已对过官方文档（{{ preset.verified_on }}）</template>
              <template v-else>· 地址没对过官方文档，配不通先怀疑这里</template>
            </div>
          </a-form-item>

          <a-form-item label="Base URL">
            <a-input v-model:value="form.base_url" placeholder="https://…/v1" />
          </a-form-item>

          <a-form-item label="API Key">
            <a-input-password v-model:value="form.api_key" autocomplete="new-password"
              :placeholder="editing ? '留空表示不改动已存的 Key' : '粘贴厂商的 Key'" />
          </a-form-item>

          <a-form-item label="型号">
            <div class="row">
              <a-select v-if="models.length" v-model:value="form.model_name" style="flex: 1"
                :options="models.map((m) => ({ value: m, label: m }))" />
              <a-input v-else v-model:value="form.model_name" style="flex: 1"
                       placeholder="点右侧「拉取型号」或手填" />
              <a-button :disabled="!!busy || !form.base_url || preset?.model_list === false"
                        @click="fetchModels">
                {{ busy === 'models' ? '拉取中…' : '拉取型号' }}
              </a-button>
            </div>
            <div v-if="modelsFetched && !models.length" class="phint">
              这家没回型号列表。可能不支持 <code>/models</code>，也可能是 Key 不对——看下面的原文。
            </div>
          </a-form-item>
        </a-form>

        <a-collapse ghost>
          <a-collapse-panel key="adv" header="高级参数（默认值通常不用改）">
            <a-row :gutter="12">
              <a-col :span="12">
                <div class="fl">温度</div>
                <a-input-number v-model:value="form.temperature" :min="0" :max="2" :step="0.1" style="width: 100%" />
                <div class="phint">审核场景建议 0</div>
              </a-col>
              <a-col :span="12">
                <div class="fl">最大输出</div>
                <a-input-number v-model:value="form.max_tokens" style="width: 100%" placeholder="厂商默认" />
              </a-col>
              <a-col :span="12">
                <div class="fl">并发</div>
                <a-input-number v-model:value="form.concurrency" :min="1" style="width: 100%" />
              </a-col>
              <a-col :span="12">
                <div class="fl">超时（秒）</div>
                <a-input-number v-model:value="form.timeout_s" :min="1" style="width: 100%" />
              </a-col>
              <a-col :span="24">
                <a-checkbox v-model:checked="form.enable_thinking" style="margin-top: 10px">
                  深度思考（只有支持的型号有效，更慢更贵）
                </a-checkbox>
                <a-checkbox v-model:checked="form.enabled" style="margin-top: 6px">
                  启用这条配置
                </a-checkbox>
              </a-col>
            </a-row>
          </a-collapse-panel>
        </a-collapse>

        <a-alert v-if="modalityWarn" type="error" show-icon style="margin-top: 12px">
          <template #message>
            <b>用途与模型能力不匹配</b>
            <div style="margin-top: 4px">{{ modalityWarn }}</div>
          </template>
        </a-alert>
        <a-alert v-if="isHttp" type="warning" show-icon style="margin-top: 12px">
          <template #message>这是 <code>http://</code> 地址，<b>API Key 会明文过网</b>。内网不等于安全。</template>
        </a-alert>

        <a-alert v-if="probe" :type="probe.ok ? 'success' : 'error'" show-icon style="margin-top: 12px">
          <template #message>
            <b>{{ probe.ok ? '连通正常' : '连不通' }}</b>
            <span v-if="probe.err_kind"> · {{ ERR_LABEL[probe.err_kind] || probe.err_kind }}</span>
            <span v-if="probe.http_status"> · HTTP {{ probe.http_status }}</span>
            <span v-if="probe.latency_ms !== null"> · {{ probe.latency_ms }}ms</span>
            <div v-if="probe.reply_excerpt" style="margin-top: 4px">
              模型回了：<b>{{ probe.reply_excerpt }}</b>
            </div>
            <div v-if="probe.message" class="dim" style="margin-top: 4px; font-size: 11px">
              {{ probe.message }}
            </div>
          </template>
        </a-alert>

        <div v-if="form.config_id" class="hist">
          <a class="lnk" :class="{ off: !!busy }" @click="!busy && showHistory(form.config_id)">
            {{ history?.cid === form.config_id && history.rows.length ? '收起测试历史' : '看测试历史' }}
          </a>
          <table v-if="history?.cid === form.config_id && history.rows.length" class="mini-tbl">
            <thead><tr><th>时间</th><th>结果</th><th class="num">耗时</th></tr></thead>
            <tbody>
              <tr v-for="t in history.rows.slice(0, 6)" :key="t.id">
                <td class="code">{{ fmtTime(t.tested_at).slice(5, 16) }}</td>
                <td>
                  <a-tag :color="t.ok ? 'success' : 'error'">
                    {{ t.ok ? '通' : (ERR_LABEL[t.err_kind || ''] || '失败') }}
                  </a-tag>
                </td>
                <td class="num">{{ t.latency_ms ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <template #footer>
        <a-button :disabled="!!busy || !form.base_url || !form.model_name"
                  style="margin-right: 8px" @click="probeChat">
          {{ busy === 'probe' ? '测试中…' : '先测一下' }}
        </a-button>
        <a-button type="primary" :disabled="!!busy || !form.base_url || !form.model_name"
                  :loading="busy === 'save'" @click="save">
          {{ editing ? '保存修改' : '保存并启用' }}
        </a-button>
      </template>
    </a-drawer>
  </div>
</template>

<style scoped>
.blk { margin-bottom: 16px; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.code { font-family: Consolas, 'Courier New', monospace; font-size: 12px; }

/* ① 页头 */
.cmd-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.cmd-title { flex: 1; min-width: 0; margin: 0; font-size: 17px; font-weight: 600; }
.cmd-desc { font-size: 12.5px; color: rgba(0, 0, 0, .55); line-height: 1.7; margin: 8px 0 0; max-width: 820px; }

/* ② 能力卡：状态语义自绘（AntD 卡片表达不了五态底色） */
.pcard { height: 100%; }
.pc-head { display: flex; align-items: center; gap: 10px; }
.pc-title { flex: 1; min-width: 0; font-size: 14px; font-weight: 600; margin: 0; }
.pc-desc { font-size: 12.5px; color: rgba(0, 0, 0, .55); line-height: 1.6; margin: 10px 0 0; min-height: 40px; }
.p-model {
  margin-top: 12px; padding: 11px 13px; border-radius: 6px;
  background: #fafafa; border: 1px solid #f0f0f0;
}
.pcard.missing .p-model, .pcard.fail .p-model { background: #fff1f0; border-color: #ffccc7; }
.pm-k { font-size: 11px; color: rgba(0, 0, 0, .4); }
.pm-v { font-size: 15px; font-weight: 600; margin-top: 2px; word-break: break-all; line-height: 1.35; }
.pm-v.none { color: #cf1322; font-size: 14px; }
.pm-sub { font-size: 11.5px; color: rgba(0, 0, 0, .45); margin-top: 3px; line-height: 1.55; }
.pm-test { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.pm-when { font-size: 11px; color: rgba(0, 0, 0, .4); }
.p-req {
  font-size: 11px; color: rgba(0, 0, 0, .4); line-height: 1.55; margin: 10px 0 0; flex: 1;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.pc-ops { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }

/* ③ 模型库 */
.c-name { font-size: 13px; }
.c-id { font-size: 11px; margin-top: 1px; }
.c-url { font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 230px; }
.pur-warn {
  margin-top: 4px; font-size: 11px; line-height: 1.5; color: #cf1322;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.lnk { font-size: 12.5px; color: #1677ff; cursor: pointer; }
.lnk:hover { text-decoration: underline; }
.lnk.off { color: rgba(0, 0, 0, .3); cursor: not-allowed; text-decoration: none; }
.lnk.bad { color: #ff4d4f; }
.del-hint { margin-top: 6px; font-size: 11px; color: #ff4d4f; line-height: 1.55; }
.test-out { margin-top: 6px; display: flex; gap: 8px; align-items: baseline; font-size: 11.5px; flex-wrap: wrap; }
.footnote { padding: 12px 0 4px; font-size: 12px; color: rgba(0, 0, 0, .45); line-height: 1.75; }

/* ④ 平台口径 */
.nrow { display: grid; grid-template-columns: 96px 1fr; gap: 16px; padding: 10px 0; border-bottom: 1px solid #f0f0f0; font-size: 12.5px; }
.nrow:last-child { border-bottom: 0; }
.nk { color: rgba(0, 0, 0, .45); }
.nv { color: rgba(0, 0, 0, .6); line-height: 1.7; min-width: 0; }
.nnote { font-size: 11.5px; color: rgba(0, 0, 0, .45); margin-top: 4px; line-height: 1.7; }
.nl-bad { color: #cf1322; margin-top: 6px; font-size: 11.5px; }

/* 抽屉 */
.dr-what { font-size: 12px; color: rgba(0, 0, 0, .6); line-height: 1.7; margin: 0 0 14px;
  padding: 10px 12px; background: #fafafa; border-radius: 6px; }
.dr-sec { margin-bottom: 16px; }
.dr-k { font-size: 12px; color: rgba(0, 0, 0, .45); margin-bottom: 8px; }
.cfgrow {
  display: flex; align-items: center; gap: 10px; padding: 9px 10px;
  border: 1px solid #f0f0f0; border-radius: 6px; margin-bottom: 6px;
}
.cfgrow.on { border-color: #91caff; background: #e6f4ff; }
.cfg-main { min-width: 0; flex: 1; }
.cfg-model { font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cfg-sub { font-size: 11px; color: rgba(0, 0, 0, .45); margin-top: 1px; }
.fl { font-size: 12px; color: rgba(0, 0, 0, .45); margin: 8px 0 4px; }
.row { display: flex; gap: 8px; align-items: center; }
.phint { font-size: 11px; color: rgba(0, 0, 0, .45); margin-top: 5px; line-height: 1.6; }
.hist { margin-top: 14px; }
.mini-tbl { width: 100%; border-collapse: collapse; margin-top: 8px; }
.mini-tbl th, .mini-tbl td { padding: 6px 8px; font-size: 12px; text-align: left; border-bottom: 1px solid #f0f0f0; }
.mini-tbl .num { text-align: right; }
</style>
