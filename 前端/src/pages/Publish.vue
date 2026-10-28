<script setup lang="ts">
/**
 * 发布管理（2026-10-27 AntD 换装版）。四段：工作区 / 版本 / 消费 / 存储。
 *
 * 用户 2026-10-19 的原话：
 *   「发布管理，就是当前的 文件 → 规则 → 三元本体 以版本的形式资产化。
 *     并允许被其他业务消费。例如 API、本体等」
 *
 * 四条口径由用户定，页面按它排：
 *   ① **三元本体 = 业务 / 规则 / 流程**（不是 RDF 三元组）
 *   ② **快照冻结**，不是只记版本指针。v1.0 永远是 v1.0
 *   ③ **只读 API + 可下载资产包**
 *   ④ **装载只动工作区**，已发布版本与当前指针不受影响
 *
 * **发布必须有人签字。** 规则集一变，所有在跑的单据的判定口径就跟着变。
 * 库里有 CHECK 约束 ck_release_signed 钉着：reviewed_by 为空不允许进 published。
 * 而且**发布与生效是两件事**：签字只是入库，还要再切一次指针才生效。
 */
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import Async from '../components/Async.vue'
import { api } from '../api'
import type { ImpactPreview, PubIndex, RelAccess, RelDetail, RelRow, RelWorkspace, StorageRow } from '../api'

type Tab = 'ws' | 'rel' | 'consume' | 'store'
const tab = ref<Tab>('ws')

const ws = ref<RelWorkspace | null>(null)
const rows = ref<RelRow[]>([])
const policy = ref<Record<string, string>>({})
const detail = ref<RelDetail | null>(null)
const openId = ref('')
const pay = ref<{ layer: string; text: string; sha: string } | null>(null)
const idx = ref<PubIndex | null>(null)
const log = ref<RelAccess[]>([])
const store = ref<{ items: StorageRow[]; note: string } | null>(null)

const who = ref('')
const version = ref('')
const note = ref('')
const reason = ref('')
const rbTarget = ref('')
const busy = ref('')
const err = ref('')
const msg = ref('')

const ST: Record<string, { label: string; color: string }> = {
  draft: { label: '草稿', color: 'default' },
  reviewing: { label: '审阅中', color: 'processing' },
  published: { label: '已发布', color: 'success' },
  rolled_back: { label: '已回滚', color: 'error' },
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

const loadWs = () =>
  run('ws', async () => {
    ws.value = await api.relWorkspace()
    if (!version.value) version.value = ws.value.suggest_version
  })

const loadRel = () =>
  run('rel', async () => {
    const r = await api.relReleases()
    rows.value = r.items
    policy.value = r.policy
  })

function pickTab(t: Tab) {
  tab.value = t
  if (t === 'ws' && !ws.value) loadWs()
  if (t === 'rel' && !rows.value.length) loadRel()
  if (t === 'consume') {
    if (!idx.value) run('idx', async () => { idx.value = await api.pubIndex() })
    run('log', async () => { log.value = (await api.relAccessLog(50)).items })
    if (!rows.value.length) loadRel()
  }
  if (t === 'store' && !store.value)
    run('st', async () => { store.value = await api.relStorage() })
}

const canFreeze = computed(() => !!ws.value && ws.value.gates.every((g) => g.ok))
const published = computed(() => rows.value.filter((r) => r.status === 'published'))

// ── 试算差异（B-P1-F，2026-10-28）：改完规则未发布前，对历史单据只读回放 ──
// 「这版规则比现版会多命中/少命中哪些单」。发布前必看——没有试算的规则
// 修改等于盲签。结果抽屉：汇总条 + 按规则 ±命中 + 逐单差异（可跳报告页）。
const ipOpen = ref(false)
const ipBusy = ref(false)
const ipErr = ref('')
const ipScope = ref<'all' | 'real' | 'demo'>('all')
const ipData = ref<ImpactPreview | null>(null)

async function runImpact() {
  ipOpen.value = true
  ipBusy.value = true
  ipErr.value = ''
  ipData.value = null
  try {
    ipData.value = await api.relImpactPreview({ scope: ipScope.value })
  } catch (e) {
    ipErr.value = e instanceof Error ? e.message : String(e)
  } finally {
    ipBusy.value = false
  }
}

const IP_DIR: Record<string, { text: string; color: string }> = {
  stricter: { text: '变严', color: 'error' },
  looser: { text: '变宽', color: 'success' },
  flat: { text: '命中变化', color: 'warning' },
}
const CONCL_CN: Record<string, string> = { pass: '通过', review: '待复核', reject: '不通过' }

const doFreeze = () =>
  run('freeze', async () => {
    const r = await api.relFreeze({ version: version.value, by: who.value, note: note.value })
    message.success(`已冻结 ${r.release_id}（${r.rule_set_version}）：新增 ${r.diff.added} / 改 ${r.diff.modified} / 退 ${r.diff.retired}`)
    version.value = ''
    note.value = ''
    await Promise.all([loadWs(), loadRel()])
    tab.value = 'rel'
  })

const act = (label: string, fn: () => Promise<unknown>) =>
  run('act', async () => {
    const r = (await fn()) as { note?: string }
    message.success(label + (r?.note ? '：' + r.note : ''))
    await Promise.all([loadRel(), loadWs()])
    if (openId.value) detail.value = await api.relRelease(openId.value)
  })

const openDetail = (id: string) =>
  run('d', async () => {
    openId.value = openId.value === id ? '' : id
    detail.value = null
    pay.value = null
    detail.value = openId.value ? await api.relRelease(openId.value) : null
  })

const showPayload = (id: string, layer: string) =>
  run('p', async () => {
    const r = await api.relAsset(id, layer)
    const text = JSON.stringify(r.payload, null, 2)
    pay.value = {
      layer,
      sha: r.content_sha256,
      // 快照可以很大（规则层 82 条全字段）。只给前 20000 字，剩下的走接口或资产包。
      text: text.length > 20000 ? text.slice(0, 20000) + '\n…（截断，完整内容走接口或资产包）'
        : text,
    }
  })

const CHANGE: Record<string, string> = {
  added: '新增', modified: '修改', retired: '退役', unchanged: '未变',
}

/** 九层说明（P1-9）：note 是几十字的口径原文，列里压成一句名词短语——
 *  长解释整段进 tooltip，表列宽度留给层名与别名。 */
function noteBrief(raw: string | null | undefined): string {
  const s = String(raw || '').replace(/\*\*/g, '').replace(/\s+/g, ' ').trim()
  if (!s) return '—'
  const cut = s.replace(/[，。；:：].*$/, '')
  return cut.length >= 6 ? cut : s.slice(0, 18)
}

const wsCols = [
  { title: '#', dataIndex: 'ord', width: 40, align: 'center' as const },
  { title: '层', key: 'layer', width: 130 },
  { title: '条目', dataIndex: 'item_count', width: 64, align: 'center' as const },
  { title: '来源', dataIndex: 'source_kind', width: 80 },
  { title: '出处', dataIndex: 'source_ref', width: 220 },
  { title: '内容校验', key: 'sha', width: 120 },
  { title: '说明', key: 'note' },
]
const relCols = [
  { title: '发布号', key: 'rid', width: 110 },
  { title: '版本号', key: 'ver', width: 170 },
  { title: '状态', key: 'st', width: 84, align: 'center' as const },
  { title: '层', dataIndex: 'layers', width: 56, align: 'center' as const },
  { title: '规则', dataIndex: 'rule_count', width: 60, align: 'center' as const },
  { title: '可编译', dataIndex: 'compiled_count', width: 68, align: 'center' as const },
  { title: '签字人', key: 'by', width: 96 },
  { title: '被拉取', dataIndex: 'accesses', width: 68, align: 'center' as const },
  { title: '操作', key: 'op', width: 240 },
]
const epCols = [
  { title: '接口', dataIndex: 'path', width: 330 },
  { title: '返回内容', dataIndex: 'what' },
]
const layerCols = [
  { title: '#', dataIndex: 'ord', width: 40, align: 'center' as const },
  { title: '层名', key: 'layer', width: 120 },
  { title: 'URL 别名', key: 'alias', width: 160 },
  { title: '说明', key: 'note' },
]
const pubCols = [
  { title: '版本号', dataIndex: 'rule_set_version', width: 130 },
  { title: '当前', key: 'cur', width: 64, align: 'center' as const },
  { title: '包级校验', key: 'sha', width: 120 },
  { title: '签字人', dataIndex: 'reviewed_by', width: 96 },
  { title: '取法', key: 'how' },
]
const logCols = [
  { title: '时间', key: 'at', width: 150 },
  { title: '版本', dataIndex: 'rule_set_version', width: 130 },
  { title: '资源', dataIndex: 'resource', width: 180 },
  { title: '调用方自报', dataIndex: 'client', width: 150 },
  { title: '字节', dataIndex: 'bytes', width: 76, align: 'right' as const },
]
const storeCols = [
  { title: '表', dataIndex: 'table_name', width: 230 },
  { title: '行数', dataIndex: 'rows', width: 60, align: 'right' as const },
  { title: '存的是什么', dataIndex: 'what', width: 190 },
  { title: '写入方', dataIndex: 'written_by', width: 130 },
  { title: '重算时', dataIndex: 'on_recalc', width: 140 },
  { title: '存储原因', key: 'why' },
]
const dt = (s: unknown) => String(s || '—').slice(0, 19).replace('T', ' ')

onMounted(() => loadWs())
</script>

<template>
  <div class="pub">
  <a-card size="small">
    <a-tabs v-model:active-key="tab" @change="(k: any) => pickTab(k as Tab)">
      <template #rightExtra>
        <span v-if="ws?.current" class="dim" :title="ws.current.why">
          引擎在用 <code>{{ ws.current.rule_set_version }}</code>
          <a-tag :color="ws.current.is_pointer ? 'success' : 'processing'" style="margin-left:4px">
            {{ ws.current.is_pointer ? '已指定' : '自动' }}</a-tag>
        </span>
        <a-tag v-else color="warning">库里一版都没有</a-tag>
      </template>

      <a-tab-pane key="ws" tab="工作区">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <template v-if="ws">
          <h3 class="sec">工作区快照</h3>
          <div class="sub"><MarkText :text="ws.note" /></div>
          <a-table :columns="wsCols" :data-source="ws.layers" row-key="layer" size="small"
            :pagination="false" :row-class-name="(r: any) => r.is_triple ? 'row-tri' : ''">
            <template #bodyCell="{ column, record: l }">
              <template v-if="column.key === 'layer'">
                <b>{{ l.layer }}</b>
                <a-tag v-if="l.is_triple" color="success" style="margin-left: 4px">三元</a-tag>
              </template>
              <template v-else-if="column.key === 'sha'">
                <code class="mini-code">{{ l.content_sha256.slice(0, 12) }}…</code>
              </template>
              <template v-else-if="column.key === 'note'">
                <span class="dim"><MarkText :text="l.note" /></span>
              </template>
              <template v-else-if="column.dataIndex === 'source_ref'">
                <code class="mini-code">{{ l.source_ref }}</code>
              </template>
            </template>
          </a-table>
          <div class="kv" style="margin-top: 12px">
            <b>包级校验</b>
            <span><code class="mini-code">{{ ws.bundle_sha256 }}</code>
              <span class="dim">（各层校验值按层名排序后再算一次；<b>不是 zip 文件的哈希</b>）</span>
            </span>
            <b>与当前生效版本的差异</b>
            <span>
              <template v-if="ws.diff.base">
                规则 新增 <b>{{ ws.diff.added }}</b> / 修改 <b>{{ ws.diff.modified }}</b>
                / 退役 <b>{{ ws.diff.retired }}</b> / 未变 {{ ws.diff.unchanged }}
                <span v-if="ws.diff.layers_changed.length" class="dim">
                  · 变化的层：{{ ws.diff.layers_changed.join('、') }}
                </span>
                <span v-else class="dim">· 与生效版内容相同</span>
              </template>
              <template v-else>
                <a-tag color="warning">没有可比的已发布版本</a-tag>
                <span class="dim" style="margin-left: 6px"><MarkText :text="ws.diff.note" /></span>
              </template>
            </span>
          </div>

          <h3 class="sec">冻结为新版本</h3>
          <!-- 试算差异（B-P1-F）：发布前对历史单据只读回放——盲签的对面 -->
          <div class="row" style="margin-bottom: 10px">
            <a-select v-model:value="ipScope" size="small" style="width: 130px"
              :options="[
                { value: 'all', label: '全部单据' },
                { value: 'real', label: '仅真实单' },
                { value: 'demo', label: '仅演示单' },
              ]" />
            <a-button size="small" :loading="ipBusy" @click="runImpact">试算差异</a-button>
            <span class="dim">发布前对历史单据回放两个版本的判定差异（只读，不落库不出网）</span>
          </div>
          <a-table :data-source="ws.gates" row-key="gate" size="small" :pagination="false"
            :columns="[
              { title: '闸门', dataIndex: 'gate', width: 150 },
              { title: '结果', key: 'ok', width: 80, align: 'center' },
              { title: '口径', key: 'detail', width: 240 },
              { title: '计算方式', key: 'how' },
            ]">
            <template #bodyCell="{ column, record: g }">
              <template v-if="column.key === 'ok'">
                <a-tag :color="g.ok ? 'success' : 'error'">{{ g.ok ? '过' : '挡住' }}</a-tag>
              </template>
              <template v-else-if="column.key === 'detail'">
                <span class="dim"><MarkText :text="g.detail" /></span>
              </template>
              <template v-else-if="column.key === 'how'">
                <span :class="g.ok ? 'dim' : ''">{{ g.how }}</span>
              </template>
            </template>
          </a-table>
          <div class="row" style="margin-top: 12px">
            <a-input v-model:value="version" placeholder="版本号" style="width: 170px" />
            <a-input v-model:value="who" placeholder="冻结人署名（必填）" style="width: 160px" />
            <a-input v-model:value="note" placeholder="发布说明" style="flex: 1" />
            <a-button type="primary" :disabled="!!busy || !canFreeze || !version || !who" @click="doFreeze">
              冻结为新版本
            </a-button>
          </div>
        </template>
      </a-tab-pane>

      <a-tab-pane key="rel" tab="版本">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <!-- 「哪一版在生效」要写成一句话。只靠表格里的高亮，没设指针时
             看不出高亮是「有人指定的」还是「自动认的」。 -->
        <div v-if="ws?.current" class="dim" style="margin-bottom: 8px">
          规则引擎正在用 <code>{{ ws.current.rule_set_version }}</code>
          （{{ ws.current.release_id }}）—— {{ ws.current.why }}
        </div>
        <div class="row" style="margin-bottom: 8px">
          <a-input v-model:value="who" placeholder="操作人署名（选填）" style="width: 220px" />
        </div>
        <a-table :columns="relCols" :data-source="rows" row-key="release_id" size="small"
          :pagination="false"
          :row-class-name="(r: any) => r.is_effective ? 'row-cur' : ''">
          <template #bodyCell="{ column, record: r }">
            <template v-if="column.key === 'rid'">
              <a style="cursor: pointer" @click.prevent="openDetail(r.release_id)">{{ r.release_id }}</a>
            </template>
            <template v-else-if="column.key === 'ver'">
              {{ r.rule_set_version }}
              <a-tag v-if="r.is_current" color="success" style="margin-left:4px">生效中</a-tag>
              <a-tag v-else-if="r.is_effective" color="processing" style="margin-left:4px">生效中（自动）</a-tag>
            </template>
            <template v-else-if="column.key === 'st'">
              <a-tag :color="ST[r.status]?.color">{{ ST[r.status]?.label }}</a-tag>
            </template>
            <template v-else-if="column.dataIndex === 'layers'">
              <span :style="!r.layers ? 'color:#faad14' : ''">{{ r.layers || '—' }}</span>
            </template>
            <template v-else-if="column.key === 'by'">
              <span v-if="r.reviewed_by">{{ r.reviewed_by }}</span>
              <a-tag v-else color="warning">未签字</a-tag>
            </template>
            <template v-else-if="column.dataIndex === 'accesses'">{{ r.accesses || '—' }}</template>
            <template v-else-if="column.key === 'op'">
              <template v-if="r.status === 'draft'">
                <a-button v-if="r.layers" size="small" :disabled="!!busy || !who"
                  @click="act('已送审', () => api.relReview(r.release_id, who))">送审</a-button>
                <a-button v-if="r.layers" size="small" :disabled="!!busy || !who" style="margin-left: 4px"
                  @click="act('已签字发布', () => api.relPublish(r.release_id, who))">签字发布</a-button>
                <a-button size="small" :disabled="!!busy || !who" style="margin-left: 4px"
                  @click="act('已删草稿', () => api.relDiscard(r.release_id, who))">删草稿</a-button>
                <a-tooltip v-if="!r.layers" title="空草稿：未冻结内容">
                  <span class="dim">无内容</span>
                </a-tooltip>
              </template>
              <template v-else-if="r.status === 'reviewing'">
                <a-button size="small" :disabled="!!busy || !who"
                  @click="act('已签字发布', () => api.relPublish(r.release_id, who))">签字发布</a-button>
              </template>
              <template v-else-if="r.status === 'published'">
                <a-button v-if="!r.is_current" size="small" :disabled="!!busy"
                  @click="act('已切为当前', () => api.relSwitch(r.release_id, who))">切为当前</a-button>
                <a size="small" style="margin-left: 4px" :href="api.pubBundleUrl(r.rule_set_version)">下载资产包</a>
              </template>
              <span v-else class="dim"><MarkText :text="r.rollback_reason" /></span>
            </template>
          </template>
          <!-- 展开行：版本详情 + 回滚 -->
          <template #expandedRowRender="{ record: r }">
            <div v-if="openId === r.release_id">
              <div v-if="!detail" class="dim">读取中…</div>
              <template v-else>
                <div class="kv">
                  <b>冻结</b><span>{{ detail.created_by }} · {{ dt(detail.frozen_at) }}</span>
                  <b>签字</b><span>{{ detail.reviewed_by || '—' }} · {{ dt(detail.published_at) }}</span>
                  <b>包级校验</b><span><code class="mini-code">{{ detail.bundle_sha256 || '—' }}</code></span>
                  <b v-if="detail.note">说明</b><span v-if="detail.note"><MarkText :text="detail.note" /></span>
                </div>
                <a-table v-if="detail.assets.length" :data-source="detail.assets" row-key="layer"
                  size="small" :pagination="false" style="margin-top: 8px"
                  :columns="[
                    { title: '层', key: 'layer', width: 130 },
                    { title: '条目', dataIndex: 'item_count', width: 70, align: 'center' },
                    { title: '内容校验', key: 'sha', width: 130 },
                    { title: '出处', key: 'ref' },
                    { title: '', key: 'op', width: 90 },
                  ]">
                  <template #bodyCell="{ column, record: a }">
                    <template v-if="column.key === 'layer'">
                      <b>{{ a.layer }}</b>
                      <a-tag v-if="a.is_triple" color="success">三元</a-tag>
                    </template>
                    <template v-else-if="column.key === 'sha'">
                      <code class="mini-code">{{ a.content_sha256.slice(0, 12) }}…</code>
                    </template>
                    <template v-else-if="column.key === 'ref'">
                      <code class="mini-code">{{ a.source_ref }}</code>
                    </template>
                    <template v-else-if="column.key === 'op'">
                      <a-button size="small" :disabled="!!busy"
                        @click="showPayload(r.release_id, a.layer)">看快照</a-button>
                    </template>
                  </template>
                </a-table>
                <div v-else class="blk-note">
                  本版无快照内容（空壳草稿，仅记录规则条数）。
                  发版请到「工作区」点<b>冻结为新版本</b>。
                </div>
                <pre v-if="pay" class="snap">{{ pay.layer }}  sha {{ pay.sha }}
{{ pay.text }}</pre>
                <div v-if="detail.status === 'published'" class="row" style="margin-top: 8px">
                  <a-select v-model:value="rbTarget" style="width: 200px"
                    :options="[
                      { value: '', label: '回滚后：没有生效版本' },
                      ...published.filter((x) => x.release_id !== r.release_id)
                        .map((p) => ({ value: p.release_id, label: '指回 ' + p.rule_set_version })),
                    ]" />
                  <a-input v-model:value="reason" placeholder="回滚原因（必填）" style="flex: 1" />
                  <a-button size="small" danger :disabled="!!busy || !who || !reason"
                    @click="act('已回滚', () => api.relRollback(r.release_id,
                           { to: rbTarget, by: who, reason }))">回滚这一版</a-button>
                </div>
                <div v-if="detail.items.length" class="dim" style="font-size: 12px; margin-top: 8px">
                  逐条规则变更：
                  <template v-for="k in ['added', 'modified', 'retired', 'unchanged']" :key="k">
                    {{ CHANGE[k] }} {{ detail.items.filter((x) => x.change === k).length }} ·
                  </template>
                  版本内容见「规则」层快照
                </div>
              </template>
            </div>
            <div v-else class="dim">点发布号展开详情</div>
          </template>
        </a-table>
        <div class="blk-note" style="margin-top: 8px">
          <div v-for="(v, k) in policy" :key="k">· {{ v }}</div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="consume" tab="消费">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <template v-if="idx">
          <h3 class="sec">对外消费：{{ idx.service_name }}</h3>
          <a-table :columns="epCols" :data-source="idx.endpoints" row-key="path" size="small"
            :pagination="false">
            <template #bodyCell="{ column, record: e }">
              <template v-if="column.dataIndex === 'path'">
                <code class="mini-code">{{ e.path }}</code>
              </template>
              <template v-else-if="column.dataIndex === 'what'">
                <span class="dim">{{ e.what }}</span>
              </template>
            </template>
          </a-table>
          <div class="kv" style="margin-top: 12px">
            <template v-for="(v, k) in idx.rules_of_the_road" :key="k">
              <b>{{ k }}</b><span class="dim">{{ v }}</span>
            </template>
          </div>

          <h3 class="sec">九层的层名就是对外契约</h3>
          <a-table :columns="layerCols" :data-source="idx.layers" row-key="layer" size="small"
            :pagination="false"
            :row-class-name="(l: any) => idx?.triple_ontology?.includes(l.layer) ? 'row-tri' : ''">
            <template #bodyCell="{ column, record: l }">
              <template v-if="column.key === 'layer'">
                <b>{{ l.layer }}</b>
                <a-tag v-if="idx?.triple_ontology?.includes(l.layer)" color="success">三元</a-tag>
              </template>
              <template v-else-if="column.key === 'alias'">
                <code class="mini-code">{{ l.alias || '—' }}</code>
              </template>
              <template v-else-if="column.key === 'note'">
                <a-tooltip v-if="l.note" placement="topLeft" :overlay-style="{ maxWidth: '420px' }">
                  <template #title><span class="note-tt">{{ String(l.note).replace(/\*\*/g, '') }}</span></template>
                  <span class="dim">{{ noteBrief(l.note) }}</span>
                </a-tooltip>
                <span v-else class="dim">—</span>
              </template>
            </template>
          </a-table>
        </template>

        <h3 class="sec">可消费的版本</h3>
        <div v-if="!published.length" class="blk-note">
          暂无已签字发布的版本，对外接口无数据。发布路径：「工作区」冻结 → 「签字发布」。
        </div>
        <a-table v-else :columns="pubCols" :data-source="published" row-key="release_id"
          size="small" :pagination="published.length > 10 ? { pageSize: 10 } : false">
          <template #bodyCell="{ column, record: p }">
            <template v-if="column.key === 'cur'">
              <a-tag v-if="p.is_current" color="success">当前</a-tag>
            </template>
            <template v-else-if="column.key === 'sha'">
              <code class="mini-code">{{ (p.bundle_sha256 || '').slice(0, 12) }}…</code>
            </template>
            <template v-else-if="column.key === 'how'">
              <code class="mini-code">/api/pub/v1/releases/{{ p.rule_set_version }}</code>
              <a size="small" style="margin-left: 6px" :href="api.pubBundleUrl(p.rule_set_version)">下载资产包</a>
            </template>
          </template>
        </a-table>

        <h3 class="sec">消费台账（{{ log.length }}）</h3>
        <a-table v-if="log.length" :columns="logCols" :data-source="log" row-key="id"
          size="small" :pagination="log.length > 10 ? { pageSize: 10 } : false">
          <template #bodyCell="{ column, record: l }">
            <template v-if="column.key === 'at'"><code class="mini-code">{{ dt(l.at) }}</code></template>
            <template v-else-if="column.dataIndex === 'rule_set_version'">
              <code class="mini-code">{{ l.rule_set_version || '—' }}</code>
            </template>
            <template v-else-if="column.dataIndex === 'resource'">
              <code class="mini-code">{{ l.resource }}</code>
            </template>
            <template v-else-if="column.dataIndex === 'client'">
              <span class="dim">{{ l.client || '—' }}</span>
            </template>
            <template v-else-if="column.dataIndex === 'bytes'">{{ l.bytes ?? '—' }}</template>
          </template>
        </a-table>
        <div v-else class="blk-note">暂无拉取记录</div>
      </a-tab-pane>

      <a-tab-pane key="store" tab="存储">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <template v-if="store">
          <h3 class="sec">存储</h3>
          <a-table :columns="storeCols" :data-source="store.items" row-key="table_name"
            size="small" :pagination="store.items.length > 10 ? { pageSize: 10 } : false">
            <template #bodyCell="{ column, record: t }">
              <template v-if="column.dataIndex === 'table_name'">
                <code class="mini-code">{{ t.table_name }}</code>
              </template>
              <template v-else-if="column.dataIndex === 'rows'">{{ t.rows ?? '—' }}</template>
              <template v-else-if="column.dataIndex === 'written_by'">
                <span class="dim">{{ t.written_by }}</span>
              </template>
              <template v-else-if="column.key === 'why'">
                <span class="dim"><MarkText :text="t.why" /></span>
              </template>
            </template>
          </a-table>
          <div class="blk-note"><MarkText :text="store.note" /></div>
        </template>
      </a-tab-pane>
    </a-tabs>
  </a-card>

  <!-- 试算结果抽屉（B-P1-F）：汇总 + 按规则 ±命中 + 逐单差异（可跳报告页） -->
  <a-drawer v-model:open="ipOpen" width="880" title="试算差异（历史回放）"
    :body-style="{ padding: '0', display: 'flex', flexDirection: 'column', overflow: 'hidden' }">
    <div v-if="ipBusy" style="padding: 48px; text-align: center; color: rgba(0,0,0,.45)">
      正在回放历史单据（两个版本各跑一遍，通常 1~3 秒）…
    </div>
    <div v-else-if="ipErr" style="padding: 24px">
      <a-alert type="error" show-icon message="试算失败" :description="ipErr" />
    </div>
    <template v-else-if="ipData">
      <div class="ip-head">
        <a-alert v-if="ipData.same_set" type="info" show-icon
          message="工作区规则集与生效版口径一致，回放零差异"
          :description="ipData.note" />
        <template v-else>
          <div class="ip-sum">
            <div class="ip-cell"><b>{{ ipData.bills }}</b><span class="dim">回放单据</span></div>
            <div class="ip-cell"><b class="ip-changed">{{ ipData.summary.changed }}</b><span class="dim">结论变化</span></div>
            <div class="ip-cell"><b class="ip-stricter">{{ ipData.summary.stricter }}</b><span class="dim">变严</span></div>
            <div class="ip-cell"><b class="ip-looser">{{ ipData.summary.looser }}</b><span class="dim">变宽</span></div>
            <div class="ip-cell"><b>{{ ipData.summary.unchanged }}</b><span class="dim">不变</span></div>
            <div class="ip-cell"><span class="dim">耗时 {{ ipData.elapsed_ms }}ms</span></div>
          </div>
          <div class="dim ip-note">{{ ipData.note }}</div>
        </template>
      </div>
      <div class="ip-body">
        <template v-if="!ipData.same_set">
          <h3 class="sec">按规则命中变化</h3>
          <a-table v-if="ipData.by_rule.length" :data-source="ipData.by_rule" row-key="rule_id"
            size="small" :pagination="false"
            :columns="[
              { title: '规则', dataIndex: 'rule_id', width: 130 },
              { title: '规则名', dataIndex: 'rule_name', ellipsis: true },
              { title: '这版新增命中', key: 'nb', width: 110, align: 'center' },
              { title: '这版不再命中', key: 'na', width: 110, align: 'center' },
            ]">
            <template #bodyCell="{ column, record: r }">
              <template v-if="column.key === 'nb'">
                <b v-if="r.only_b" class="ip-stricter">+{{ r.only_b }}</b>
                <span v-else class="dim">—</span>
              </template>
              <template v-else-if="column.key === 'na'">
                <b v-if="r.only_a" class="ip-looser">−{{ r.only_a }}</b>
                <span v-else class="dim">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'rule_id'">
                <router-link :to="`/asset/rules/${encodeURIComponent(r.rule_id)}`">{{ r.rule_id }}</router-link>
              </template>
            </template>
          </a-table>
          <div v-else class="dim">没有规则级命中变化</div>

          <h3 class="sec">逐单差异（{{ ipData.flips.length }} 张，可跳报告页看现结论依据）</h3>
          <a-table v-if="ipData.flips.length" :data-source="ipData.flips" row-key="bill_no"
            size="small" :pagination="ipData.flips.length > 20 ? { pageSize: 20 } : false"
            :columns="[
              { title: '单据', key: 'bill', width: 200 },
              { title: '现结论', key: 'old', width: 90, align: 'center' },
              { title: '这版结论', key: 'new', width: 90, align: 'center' },
              { title: '方向', key: 'dir', width: 90, align: 'center' },
              { title: '命中变化', key: 'hits' },
            ]">
            <template #bodyCell="{ column, record: f }">
              <template v-if="column.key === 'bill'">
                <router-link v-if="f.run_id" :to="`/review/report/${f.run_id}`"
                  title="看这份报告的现行结论与证据">{{ f.bill_no }}</router-link>
                <span v-else>{{ f.bill_no }}</span>
              </template>
              <template v-else-if="column.key === 'old'">{{ CONCL_CN[f.old_conclusion] || f.old_conclusion }}</template>
              <template v-else-if="column.key === 'new'">
                <b>{{ CONCL_CN[f.new_conclusion] || f.new_conclusion }}</b>
              </template>
              <template v-else-if="column.key === 'dir'">
                <a-tag :color="IP_DIR[f.direction]?.color">{{ IP_DIR[f.direction]?.text }}</a-tag>
              </template>
              <template v-else-if="column.key === 'hits'">
                <div v-for="d in f.detail.slice(0, 3)" :key="d.rule_id + d.change" class="ip-hit-row">
                  <a-tag :color="d.change === 'new_hit' ? 'error' : 'default'"
                    :title="d.message">{{ d.change === 'new_hit' ? '新增命中' : '不再命中' }}</a-tag>
                  <router-link :to="`/asset/rules/${encodeURIComponent(d.rule_id)}`">{{ d.rule_id }}</router-link>
                </div>
                <div v-if="f.detail.length > 3" class="dim">…共 {{ f.detail.length }} 条</div>
              </template>
            </template>
          </a-table>
          <div v-else class="dim">逐单结论无变化（命中集也无变化）</div>
        </template>
      </div>
    </template>
  </a-drawer>
  </div>
</template>

<style scoped>
.sec { font-size: 14px; font-weight: 600; margin: 18px 0 10px; }
.sec:first-of-type { margin-top: 0; }
.sub { font-size: 12.5px; color: rgba(0, 0, 0, .55); margin-bottom: 10px; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 6px 16px; font-size: 13px; }
.kv b { color: rgba(0, 0, 0, .45); font-weight: 500; align-self: baseline; }
.mini-code { font-size: 11px; background: #fafafa; padding: 1px 5px; border-radius: 4px; }
.note-tt { font-size: 12px; line-height: 1.7; color: rgba(255, 255, 255, .85); }
.blk-note { font-size: 12px; color: rgba(0, 0, 0, .55); margin-top: 8px; line-height: 1.7; }
.snap {
  max-height: 300px; overflow: auto; background: #fafafa; border: 1px solid #f0f0f0;
  border-radius: 6px; padding: 8px; font-size: 11px; line-height: 1.5; white-space: pre;
}
:deep(.row-tri) td { background: #f6ffed; }
:deep(.row-cur) td { background: #fffbe6; }

/* 试算抽屉（B-P1-F） */
.ip-head { padding: 14px 20px 10px; border-bottom: 1px solid #f0f0f0; }
.ip-sum { display: flex; gap: 24px; align-items: baseline; flex-wrap: wrap; }
.ip-cell { display: flex; flex-direction: column; gap: 2px; font-size: 20px; }
.ip-cell .dim { font-size: 11px; }
.ip-changed { color: #d46b08; }
.ip-stricter { color: #cf1322; }
.ip-looser { color: #389e0d; }
.ip-note { margin-top: 8px; max-width: 780px; }
.ip-body { padding: 12px 20px; overflow-y: auto; flex: 1; }
.ip-hit-row { display: flex; gap: 6px; align-items: center; margin-bottom: 2px; }
</style>
