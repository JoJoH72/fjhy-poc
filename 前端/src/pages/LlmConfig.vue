<script setup lang="ts">
/**
 * 系统 · 模型配置（2026-10-27 第三版：单一结构，消除重复编辑入口）。
 *
 * 用户两轮拍板后的定式（对标阿里云百炼/Dify/OneAPI）：
 *   页头一行全局状态（X/3 可用 + 新增配置）
 *   主体一张表格，行=用途（不是行=配置）：
 *     用途 | 当前模型 | 状态 | 最近测试 | [编辑] [测试]
 *   「测试」轻量反馈：message 短提示 + 本行「最近测试」原地刷新。
 *   「编辑」开抽屉——**全页唯一编辑入口**：选模型/新建/停用/删除/设默认
 *   都在抽屉内完成。旧版「能力卡+模型库」双层是同一实体两个编辑面，
 *   已删。
 *
 * 保留的硬行为（不许丢）：
 *   用途×能力交叉校验（行内黄 tag）；没测过≠测了不通；接口挂了不退化空列表；
 *   legacy Key 横幅提示重填；型号现取。
 */
import { computed, createVNode, onMounted, onUnmounted, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  QuestionCircleOutlined, ExclamationCircleOutlined,
  FileImageOutlined, AuditOutlined, FileSearchOutlined,
} from '@ant-design/icons-vue'
import { api } from '../api'
import type { LlmConfig, LlmPreset, LlmTestResult, LlmTestRow } from '../api'
import { ERR_LABEL, modalityWarning } from '../data/llmState'

const loading = ref(true)
const err = ref('')
const warn = ref('')

const presets = ref<LlmPreset[]>([])
const configs = ref<LlmConfig[]>([])
const purposes = ref<{ key: string; label: string; default: string | null }[]>([])

/** 能力说明（「用途」列 tooltip） */
const PLAIN: Record<string, { what: string; req: string; none: string }> = {
  vision_read: {
    what: '读取票据图（发票、行程单、审批单照片），输出结构化票面字段。',
    req: '必须多模态型号；纯文本型号不报错，会按文字线索编造内容。',
    none: '未配置：图片票据读不出字段，相关规则转待人工。',
  },
  rule_judge: {
    what: '规则判不了的单项给一条参考意见，供审核岗复核。',
    req: '长文本推理；温度建议 0，保证同单两次结论一致。',
    none: '未配置：超规则范围的单项标「待人工：未调用模型」。',
  },
  extract: {
    what: '从制度文件抽取条款、起草规则八要素草稿。',
    req: '长上下文；产出数值须回原文核对，草稿不等于发布。',
    none: '未配置：抽取流水线停在切条款步骤。',
  },
}
const PURPOSE_ICON: Record<string, typeof FileImageOutlined> = {
  vision_read: FileImageOutlined,
  rule_judge: AuditOutlined,
  extract: FileSearchOutlined,
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

const models = ref<string[]>([])
const modelsFetched = ref(false)
const busy = ref('')
const busySec = ref(0)
/** 正在测试的用途 key（行内「测试中 Xs」） */
const testingKey = ref('')
let secTimer: number | undefined
const probe = ref<LlmTestResult | null>(null)
const history = ref<{ cid: string; rows: LlmTestRow[] } | null>(null)

const preset = computed(() => presets.value.find((p) => p.preset_id === form.value.preset_id))
const isHttp = computed(() => form.value.base_url.startsWith('http://'))
const textModels = computed(() =>
  configs.value
    .filter((c) => c.purpose === 'rule_judge' || c.purpose === 'extract')
    .map((c) => c.model_name)
    .filter((m): m is string => !!m),
)
const modalityWarn = computed(() =>
  (!form.value.model_name && !form.value.preset_id)
    ? ''
    : modalityWarning(form.value.purpose, preset.value?.modality ?? null,
                      form.value.model_name, textModels.value,
                      preset.value?.suggest_model ?? null),
)
const legacyRows = computed(() => configs.value.filter((c) => c.key_format === 'legacy'))

const cfgById = (cid: string | null) => configs.value.find((c) => c.config_id === cid) || null
/** 某用途的候选模型（抽屉内列表） */
function candidates(purposeKey: string) {
  return configs.value.filter((c) => c.purpose === purposeKey)
}

/** 用途行状态 */
function stateOf(p: { key: string; default: string | null }) {
  const c = cfgById(p.default)
  if (!c) return 'missing'
  if (!c.enabled) return 'off'
  if (c.last_test_ok === false) return 'fail'
  if (c.last_test_ok === null || c.last_test_ok === undefined) return 'untested'
  return 'ok'
}
const STATE_TEXT: Record<string, string> = {
  ok: '已就绪', untested: '未测试', missing: '未配置', off: '已停用', fail: '测试未通过',
}
const STATE_COLOR: Record<string, string> = {
  ok: 'success', untested: 'default', missing: 'error', off: 'default', fail: 'error',
}
const okCount = computed(() => purposes.value.filter((p) => stateOf(p) === 'ok').length)
const firstTodo = computed(() => purposes.value.find((p) => stateOf(p) !== 'ok') || null)

/* ── 抽屉（唯一编辑入口） ─────────────────────────────────────────────── */
const drawer = ref(false)
const drawerPurpose = computed(() => purposes.value.find((p) => p.key === form.value.purpose) || null)

function openDrawer(purposeKey?: string) {
  resetForm()
  const key = purposeKey || firstTodo.value?.key || 'rule_judge'
  form.value.purpose = key
  drawer.value = true
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
    const [p, c] = await Promise.all([
      api.llmPresets(),
      api.llmConfigs(),
    ])
    presets.value = p.items
    configs.value = c.items
    purposes.value = c.purposes
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}
onMounted(loadAll)
onUnmounted(stopSec)

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
  warn.value = ''
}

function resetForm() {
  form.value = blankForm()
  editing.value = false
  models.value = []
  modelsFetched.value = false
  probe.value = null
}

function startSec() {
  busySec.value = 0
  secTimer = window.setInterval(() => { busySec.value++ }, 1000)
}
function stopSec() {
  if (secTimer !== undefined) {
    window.clearInterval(secTimer)
    secTimer = undefined
  }
}

async function run(tag: string, fn: () => Promise<void>) {
  busy.value = tag
  err.value = ''
  startSec()
  try {
    await fn()
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
    message.error(err.value)
  } finally {
    busy.value = ''
    testingKey.value = ''
    stopSec()
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

/** 保存；该用途没有默认时顺手设默认（配好即用的心智） */
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
        extra = '；设为默认失败，可在抽屉里手动设置'
      }
    }
    form.value.api_key = ''
    form.value.config_id = cid
    editing.value = true
    await loadAll()
    drawer.value = false
    if (w) message.warning(w)
    message.success(`已保存 ${cid}${extra}。${r.note || ''}`)
  })

/** 行内「测试」：message 短提示 + 本行「最近测试」原地刷新 */
const runTest = (cid: string, purposeKey?: string) =>
  run(cid + ':chat', async () => {
    if (purposeKey) testingKey.value = purposeKey
    const r = await api.testLlmConfig(cid, 'chat')
    if (r.ok) {
      message.success(`连通正常 · ${r.latency_ms ?? '—'}ms${r.reply_excerpt ? ` · ${r.reply_excerpt}` : ''}`)
    } else {
      message.error({ content: `测试失败：${ERR_LABEL[r.err_kind || ''] || r.message}`, duration: 6 })
    }
    await loadAll()
  })

const testRow = (p: { key: string; default: string | null }) => {
  if (p.default) runTest(p.default, p.key)
}

/** 抽屉内候选列表的「设为默认 / 停用 / 删除」 */
const makeDefault = (cid: string) =>
  run(cid + ':default', async () => {
    const r = await api.setLlmDefault(cid)
    message.success(`已设为「${r.purpose_label}」的默认模型`)
    if (r.modality_warning) message.warning(r.modality_warning)
    await loadAll()
  })

const toggleEnabled = (c: LlmConfig) =>
  run(c.config_id + ':enabled', async () => {
    const r = await api.setLlmEnabled(c.config_id, !c.enabled)
    message.success(`${c.config_id} 已${r.enabled ? '启用' : '停用'}`)
    if (r.warning) message.warning(r.warning)
    await loadAll()
  })

const showHistory = (cid: string) =>
  run(cid + ':hist', async () => {
    history.value = {
      cid,
      rows: history.value?.cid === cid ? [] : (await api.llmTestHistory(cid)).items,
    }
  })

function confirmDelete(c: LlmConfig) {
  Modal.confirm({
    title: `删除配置 ${c.config_id}？`,
    icon: createVNode(ExclamationCircleOutlined),
    content: '该配置的全部测试历史将一并删除，且不可恢复。',
    okText: '删除', okType: 'danger', cancelText: '取消',
    onOk: () => remove(c.config_id),
  })
}

const remove = (cid: string) =>
  run(cid + ':del', async () => {
    const r = await api.deleteLlmConfig(cid)
    message.success(`已删除 ${cid}，连带删掉 ${(r as Record<string, unknown>).tests_removed} 条测试历史`)
    if (history.value?.cid === cid) history.value = null
    if (form.value.config_id === cid) resetForm()
    await loadAll()
  })

function fmtTime(s: string | null) {
  return s ? String(s).replace('T', ' ').slice(0, 19) : '—'
}
/** 行内「最近测试」文案 */
function lastTestAtOf(p: { key: string; default: string | null }): string {
  const c = cfgById(p.default)
  if (!c?.last_test_at) return ''
  const t = fmtTime(c.last_test_at).slice(5, 16)
  return c.last_test_latency_ms != null ? `${t} · ${c.last_test_latency_ms}ms` : t
}
/** 行内测试 tag（该用途默认配置的三态） */
function rowTestTag(p: { key: string; default: string | null }): { color: string; text: string } {
  const c = cfgById(p.default)
  if (testingKey.value === p.key) return { color: 'processing', text: `测试中 ${busySec}s` }
  if (!c) return { color: 'default', text: '—' }
  if (c.last_test_ok === true) return { color: 'success', text: '通过' }
  if (c.last_test_ok === false) return { color: 'error', text: '不通' }
  return { color: 'default', text: '未测试' }
}

/** 用途主名：括号补语（选型要求）收进 tooltip，不进列表列——列宽固定不换行 */
function purposeMain(label: string): string {
  const i = label.indexOf('（')
  return i > 0 ? label.slice(0, i) : label
}
</script>

<template>
  <div class="llm">
    <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
    <a-alert v-if="legacyRows.length" type="warning" show-icon style="margin-bottom: 12px">
      <template #message>
        {{ legacyRows.length }} 条配置的 Key 为旧格式（测试必然失败），请在抽屉中重新填写：
        {{ legacyRows.map((c) => c.config_id).join('、') }}
      </template>
    </a-alert>
    <a-skeleton v-if="loading" active :paragraph="{ rows: 6 }" />

    <template v-else>
      <!-- 页头：全局状态 + 唯一新增入口 -->
      <div class="topbar-row">
        <h2 class="tb-title">模型配置</h2>
        <a-tag :color="okCount === 0 ? 'error' : okCount === purposes.length ? 'success' : 'processing'">
          {{ okCount }} / {{ purposes.length }} 可用
        </a-tag>
        <div class="grow"></div>
        <a-button type="primary" @click="openDrawer()">新增配置</a-button>
      </div>

      <!-- 主体唯一列表：行=用途。div 网格自绘（行高冻结；表格引擎的
           baseline 布局对行内混合 tag/button 产生 ±1px 累计差，钉不死） -->
      <a-card size="small">
        <div class="llmgrid">
          <div class="lg-head">
            <span>用途</span><span>当前模型</span><span>状态</span><span>最近测试</span><span></span>
          </div>
          <div v-for="p in purposes" :key="p.key" class="lg-row">
            <div class="lg-c lg-pu">
              <component :is="PURPOSE_ICON[p.key]" class="cap-ico" />
              <span class="pu-label">{{ purposeMain(p.label) }}</span>
              <a-tooltip placement="topLeft" :overlay-style="{ maxWidth: '360px' }">
                <template #title>
                  <div class="tt">
                    <p>{{ p.label }}：{{ PLAIN[p.key]?.what }}</p>
                    <p v-if="PLAIN[p.key]?.req">选型：{{ PLAIN[p.key]?.req }}</p>
                    <p v-if="PLAIN[p.key]?.none">{{ PLAIN[p.key]?.none }}</p>
                  </div>
                </template>
                <question-circle-outlined class="help" />
              </a-tooltip>
            </div>
            <div class="lg-c lg-model">
              <template v-if="cfgById(p.default)">
                <div class="cm-line">
                  <span class="cm-name">{{ cfgById(p.default)!.model_name }}</span>
                  <a-tag v-if="cfgById(p.default)!.modality_warning" color="warning">
                    <a-tooltip placement="top" :overlay-style="{ maxWidth: '360px' }">
                      <template #title>{{ cfgById(p.default)!.modality_warning }}</template>
                      能力不匹配
                    </a-tooltip>
                  </a-tag>
                </div>
                <div class="cm-sub">{{ cfgById(p.default)!.config_name }}</div>
              </template>
              <span v-else class="cm-none">未配置</span>
            </div>
            <div class="lg-c">
              <a-tag :color="STATE_COLOR[stateOf(p)]">{{ STATE_TEXT[stateOf(p)] }}</a-tag>
            </div>
            <div class="lg-c lg-test">
              <a-tag :color="rowTestTag(p).color">{{ rowTestTag(p).text }}</a-tag>
              <span v-if="lastTestAtOf(p)" class="cm-when">{{ lastTestAtOf(p) }}</span>
            </div>
            <div class="lg-c lg-ops">
              <a-button type="link" size="small" class="lnkbtn"
                        :disabled="!!busy && busy !== (p.default || '') + ':chat'"
                        @click="openDrawer(p.key)">编辑</a-button>
              <a-button type="link" size="small" class="lnkbtn"
                        :loading="busy === (p.default || '') + ':chat'"
                        :disabled="!p.default || (!!busy && busy !== (p.default || '') + ':chat')"
                        @click="testRow(p)">测试</a-button>
            </div>
          </div>
        </div>
      </a-card>
    </template>

    <!-- 抽屉：唯一编辑入口 -->
    <a-drawer v-model:open="drawer" width="520" placement="right"
      :title="editing ? '编辑配置' : '配置模型'">
      <template #extra>
        <span class="dim">{{ drawerPurpose?.label || '' }}</span>
      </template>

      <!-- 该用途现有模型：切换默认 / 编辑 / 停用 / 删除 -->
      <div class="dr-sec">
        <div class="dr-k">现有配置（{{ candidates(form.purpose).length }}）</div>
        <div v-if="!candidates(form.purpose).length" class="dim" style="font-size: 12px">该用途还没有配置</div>
        <div v-for="c in candidates(form.purpose)" :key="c.config_id" class="cfgrow"
             :class="{ on: form.config_id === c.config_id, off: !c.enabled }">
          <div class="cfg-main">
            <div class="cfg-model">
              {{ c.model_name }}
              <a-tag v-if="c.is_default" color="processing" style="margin-left: 6px">默认</a-tag>
              <a-tag v-if="!c.enabled" color="default" style="margin-left: 6px">已停用</a-tag>
            </div>
            <div class="cfg-sub">
              {{ c.config_name }}
              <span v-if="c.last_test_at"> · {{ fmtTime(c.last_test_at).slice(5, 16) }}
                {{ c.last_test_ok === true ? '通' : c.last_test_ok === false ? '不通' : '未测' }}</span>
            </div>
          </div>
          <a-button v-if="!c.is_default" type="link" size="small" class="lnkbtn"
                    :disabled="!!busy" @click="makeDefault(c.config_id)">设为默认</a-button>
          <a-button type="link" size="small" class="lnkbtn" :disabled="!!busy" @click="edit(c)">编辑</a-button>
          <a-button type="link" size="small" class="lnkbtn" :disabled="!!busy" @click="toggleEnabled(c)">
            {{ c.enabled ? '停用' : '启用' }}
          </a-button>
          <a-button type="link" size="small" class="lnkbtn lnk-bad" :disabled="!!busy"
                    @click="confirmDelete(c)">删除</a-button>
        </div>
      </div>

      <div class="dr-sec">
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
              <template v-else>· 地址未经官方文档核对</template>
            </div>
          </a-form-item>

          <a-form-item label="Base URL">
            <a-input v-model:value="form.base_url" placeholder="https://…/v1" />
          </a-form-item>

          <a-form-item>
            <template #label>
              API Key
              <a-tooltip title="Key 加密存储；测试请求仅一句固定问候语，不含业务数据">
                <question-circle-outlined class="help" style="margin-left: 4px" />
              </a-tooltip>
            </template>
            <a-input-password v-model:value="form.api_key" autocomplete="new-password"
              :placeholder="editing ? '留空表示不改动已存的 Key' : '粘贴厂商的 Key'" />
          </a-form-item>

          <a-form-item label="型号">
            <div class="row">
              <a-select v-if="models.length" v-model:value="form.model_name" style="flex: 1"
                :options="models.map((m) => ({ value: m, label: m }))" />
              <a-input v-else v-model:value="form.model_name" style="flex: 1"
                       placeholder="点右侧「拉取型号」或手填" />
              <a-button :loading="busy === 'models'"
                        :disabled="(!!busy && busy !== 'models') || !form.base_url || preset?.model_list === false"
                        @click="fetchModels">
                {{ busy === 'models' ? `拉取中 ${busySec}s` : '拉取型号' }}
              </a-button>
            </div>
            <div v-if="modelsFetched && !models.length" class="phint">
              未返回型号列表（可能不支持 <code>/models</code>，或 Key 无效）
            </div>
          </a-form-item>
        </a-form>

        <a-collapse ghost>
          <a-collapse-panel key="adv" header="高级参数">
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
                  深度思考（更慢更贵，仅部分型号支持）
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
          <template #message>这是 <code>http://</code> 地址，<b>API Key 会明文过网</b>。</template>
        </a-alert>

        <a-alert v-if="probe" :type="probe.ok ? 'success' : 'error'" show-icon style="margin-top: 12px">
          <template #message>
            <b>{{ probe.ok ? '连通正常' : '连不通' }}</b>
            <span v-if="probe.err_kind"> · {{ ERR_LABEL[probe.err_kind] || probe.err_kind }}</span>
            <span v-if="probe.http_status"> · HTTP {{ probe.http_status }}</span>
            <span v-if="probe.latency_ms !== null"> · {{ probe.latency_ms }}ms</span>
            <div v-if="probe.reply_excerpt" style="margin-top: 4px">
              模型回复：<b>{{ probe.reply_excerpt }}</b>
            </div>
          </template>
        </a-alert>

        <div v-if="form.config_id" class="hist">
          <a class="lnk" :class="{ off: !!busy }" @click="!busy && showHistory(form.config_id)">
            {{ history?.cid === form.config_id && history.rows.length ? '收起测试历史' : '测试历史' }}
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
        <a-button :loading="busy === 'probe'"
                  :disabled="(!!busy && busy !== 'probe') || !form.base_url || !form.model_name"
                  style="margin-right: 8px" @click="probeChat">
          {{ busy === 'probe' ? `测试中… ${busySec}s` : '先测一下' }}
        </a-button>
        <a-button type="primary" :loading="busy === 'save'"
                  :disabled="!!busy && busy !== 'save' || !form.base_url || !form.model_name"
                  @click="save">
          {{ editing ? '保存修改' : '保存并启用' }}
        </a-button>
      </template>
    </a-drawer>
  </div>
</template>

<style scoped>
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.code { font-family: Consolas, 'Courier New', monospace; font-size: 12px; }

/* 页头状态行 */
.topbar-row { display: flex; align-items: center; gap: 10px; margin: 0 0 12px; }
.tb-title { font-size: 14px; font-weight: 600; margin: 0; }
.tb-note { font-size: 12px; }
.grow { flex: 1; }

/* 主列表：div 网格，行高冻结 56px（测试中/通过/不通/未配置任意组合等高） */
.llmgrid { width: 100%; }
.lg-head, .lg-row {
  display: grid;
  /* 列对齐语义（大厂表格惯例）：用途/当前模型左对齐（长文本起点一致）；
     状态/最近测试居中（短标签+短度量）；操作固定右贴边 */
  grid-template-columns: 210px minmax(0, 1fr) 120px 240px 170px;
  gap: 12px; align-items: center;
}
.lg-head {
  height: 40px; padding: 0 16px; border-bottom: 1px solid #f0f0f0;
  font-size: 13px; font-weight: 600; color: rgba(0, 0, 0, .85); background: #fafafa;
}
.lg-head > span:nth-child(3), .lg-head > span:nth-child(4) { text-align: center; }
.lg-head > span:last-child { text-align: right; }
.lg-row { height: 56px; padding: 0 16px; }
.lg-row + .lg-row { border-top: 1px solid #f0f0f0; }
.lg-c { min-width: 0; overflow: hidden; }
.lg-pu { display: flex; align-items: center; gap: 7px; }
.pu-label { font-size: 13.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* 状态列：居中 */
.lg-row .lg-c:nth-child(3) { display: flex; justify-content: center; }
/* 最近测试列：居中 */
.lg-test { display: flex; align-items: center; justify-content: center; gap: 6px; white-space: nowrap; }
/* 操作列：右贴边，[编辑] [测试] 顺序 */
.lg-ops { display: flex; justify-content: flex-end; }
.cap-ico { font-size: 15px; color: rgba(0, 0, 0, .55); }
.help { font-size: 12px; color: rgba(0, 0, 0, .35); cursor: help; }
.help:hover { color: rgba(0, 0, 0, .65); }
.tt p { margin: 0 0 4px; font-size: 12px; line-height: 1.6; }
.tt p:last-child { margin-bottom: 0; }
.cap-ico { font-size: 15px; color: rgba(0, 0, 0, .55); }
.help { font-size: 12px; color: rgba(0, 0, 0, .35); cursor: help; }
.help:hover { color: rgba(0, 0, 0, .65); }
.tt p { margin: 0 0 4px; font-size: 12px; line-height: 1.6; }
.tt p:last-child { margin-bottom: 0; }
.cm-line { display: flex; align-items: center; gap: 6px; min-height: 22px; }
.cm-name { font-size: 13px; font-weight: 500; }
.cm-sub { font-size: 12px; color: rgba(0, 0, 0, .45); margin-top: 1px; min-height: 18px; }
.cm-none { color: rgba(0, 0, 0, .3); }
/* 最近测试列：tag+时间同行固定高，测试中/通过/不通切换不撑行 */
.test-cell { display: flex; align-items: center; gap: 6px; min-height: 24px; white-space: nowrap; }
.cm-when { font-size: 12px; color: rgba(0, 0, 0, .4); }
.lnkbtn { padding: 0 4px; height: auto; font-size: 12.5px; }
.lnk-bad { color: #ff4d4f; }

/* 抽屉 */
.dr-sec { margin-bottom: 16px; }
.dr-k { font-size: 12px; color: rgba(0, 0, 0, .45); margin-bottom: 8px; }
.cfgrow {
  display: flex; align-items: center; gap: 4px; padding: 9px 10px;
  border: 1px solid #f0f0f0; border-radius: 6px; margin-bottom: 6px;
  flex-wrap: wrap;
}
.cfgrow.on { border-color: #91caff; background: #e6f4ff; }
.cfgrow.off { opacity: .6; }
.cfg-main { min-width: 0; flex: 1; }
.cfg-model { font-size: 12.5px; }
.cfg-sub { font-size: 11px; color: rgba(0, 0, 0, .45); margin-top: 1px; }
.fl { font-size: 12px; color: rgba(0, 0, 0, .45); margin: 8px 0 4px; }
.row { display: flex; gap: 8px; align-items: center; }
.phint { font-size: 11px; color: rgba(0, 0, 0, .45); margin-top: 5px; line-height: 1.6; }
.hist { margin-top: 14px; }
.lnk { font-size: 12.5px; color: #1677ff; cursor: pointer; }
.lnk:hover { text-decoration: underline; }
.lnk.off { color: rgba(0, 0, 0, .3); cursor: not-allowed; text-decoration: none; }
.mini-tbl { width: 100%; border-collapse: collapse; margin-top: 8px; }
.mini-tbl th, .mini-tbl td { padding: 6px 8px; font-size: 12px; text-align: left; border-bottom: 1px solid #f0f0f0; }
.mini-tbl .num { text-align: right; }
</style>
