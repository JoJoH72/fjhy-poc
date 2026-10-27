<script setup lang="ts">
/**
 * 栏目一 · 制度文件库（2026-10-27 AntD 换装版）。规则的唯一上游。
 *
 * 五个页签对应五件不同的事，**刻意不合并**：
 *   文件来源   上传制度文件；或纳入服务器上已有的目录；扫描
 *   文件清单   库里有哪些文件、每份能不能识别、识别不了的原因、删托管文件
 *   上传台账   谁什么时候传了什么、**哪些被拒了以及为什么**、托管区占了多少盘
 *   变化台账   历次扫描的增删改历史（第二次扫描的价值全在这里）
 *   读图       OCR 丢结构的那几页，用多模态模型再读一遍并互相印证
 *
 * 2026-10-21 文件进库改成**以客户端上传为主**（迁移 0034，用户决策「双模式并存」）：
 *   托管目录   客户端上传 → 服务器 var/doclib/{领域id}/{实例id}/，可写可删
 *   引用目录   服务器上已有的目录 → 原地只读扫描，一个字节都不写
 *
 * 两条口径必须在界面上写死，否则一定被读错：
 *
 * 1. **「只读」是这一栏的地基。** 本系统绝不向被扫描的目录写入任何东西，
 *    「移出文件库」只删登记。客户的资料目录是证据，不是工作区。
 *
 * 2. **「需OCR」不等于「不能识别」。** 前者是 PDF 没有文字层、下一步办得到；
 *    后者（「未解析」）是格式本身取不到、要换路子。合成一个状态，
 *    没人知道该去跑 OCR 还是该去转格式。
 */
import { computed, onMounted, ref } from 'vue'
import Async from '../components/Async.vue'
import { pro } from '../data/prose'
import FolderPicker from '../components/FolderPicker.vue'
import Uploader from '../components/Uploader.vue'
import ExtractReview from '../components/ExtractReview.vue'
import { api } from '../api'
import type {
  DlChange, DlFile, DlRoot, DlScanResult, DlStats, DlStorage, DlUploadItem,
  DlUploadResult, VisionCross, VisionPage, VisionRead,
} from '../api'

type Tab = 'roots' | 'files' | 'uploads' | 'changes' | 'vision' | 'extract'
const tab = ref<Tab>('roots')

const roots = ref<DlRoot[]>([])
const stats = ref<DlStats | null>(null)
const files = ref<DlFile[]>([])
const changes = ref<DlChange[]>([])
const changeScan = ref<Record<string, unknown> | null>(null)
const lastScan = ref<DlScanResult | null>(null)
const opened = ref<(DlFile & { preview: string; truncated: boolean }) | null>(null)

// ── 读图 ────────────────────────────────────────────────────────────────
const vpages = ref<VisionPage[]>([])
const vreads = ref<VisionRead[]>([])
const vcross = ref<VisionCross | null>(null)
const vopen = ref('')
const vsum = ref<{ totals: Record<string, number>; note: string } | null>(null)
const vwho = ref('')
const vmsg = ref('')

// ── 上传与托管区 ────────────────────────────────────────────────────────
const storage = ref<DlStorage | null>(null)
const ledger = ref<(DlUploadItem & { batch_id: string; uploaded_at: string
                                     uploaded_by: string | null
                                     parse_state: string | null
                                     text_chars: number | null
                                     missing: boolean | null })[]>([])
const ledgerBy = ref<{ result: string; n: number }[]>([])
const onlyRejected = ref(false)
const scopeNote = ref('')

const picking = ref(false)
const busy = ref('')
const err = ref('')
const msg = ref('')
const pendingRemove = ref('')
const pendingDel = ref('')

const fq = ref('')
const fext = ref('')
const fstate = ref('')
const fkind = ref('')
const forigin = ref('')

/** 解析状态 → 标签颜色。六态各有各的颜色，合并就等于合并了处置动作。 */
const STATE_COLOR: Record<string, string> = {
  已解析: 'success', 需OCR: 'warning', 未解析: 'default',
  空文件: 'default', 解析失败: 'error', 未扫描: 'default',
}
/** 文件类别。单据和范本不是制度——混在一张清单里看不出来。 */
const KIND_COLOR: Record<string, string> = {
  制度: 'success', 范本: 'default', 单据: 'processing', 其他: 'default',
}
const CHANGE_COLOR: Record<string, string> = {
  新增: 'success', 修改: 'warning', 消失: 'error',
}
/** 根的类型。**托管可写、引用只读**，这是只读纪律的边界，必须一眼可辨。 */
const KIND_ROOT_COLOR: Record<string, string> = { 托管: 'success', 引用: 'default' }
/** 上传结果七态。「同名另存」与「同内容已存在」是相反的两件事，颜色也要分开。 */
const RESULT_COLOR: Record<string, string> = {
  已入库: 'success', 同名另存: 'processing', 同内容已存在: 'default',
  扩展名不支持: 'error', 超过大小上限: 'error',
  文件名不合法: 'error', 写盘失败: 'error',
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

const loadRoots = () =>
  run('roots', async () => {
    const [r, s, st] = await Promise.all([api.dlRoots(), api.dlStats(), api.dlStorage()])
    roots.value = r.items
    stats.value = s
    storage.value = st
    scopeNote.value = s.scope_note || ''
  })

const loadFiles = () =>
  run('files', async () => {
    files.value = (await api.dlFiles({
      // 按「能否识别」筛，不按 parse_state：后者是磁盘事实，OCR 跑完也还是「需OCR」，
      // 让人对着一个写着「需OCR」的下拉去找已经 OCR 过的文件，是界面在骗人。
      q: fq.value, ext: fext.value, recognize: fstate.value, kind: fkind.value,
      origin: forigin.value, limit: 1000,
    })).items
  })

const loadUploads = () =>
  run('uploads', async () => {
    const [r, st] = await Promise.all([
      api.dlUploads({ limit: 500, only_rejected: onlyRejected.value || undefined }),
      api.dlStorage(),
    ])
    ledger.value = r.items
    ledgerBy.value = r.by_result
    storage.value = st
  })

/** 上传完刷新三处：根（文件数变了）、清单、台账。只刷一处会让另外两处显示旧数字。
 *  2026-10-26：上传的扫描件（需OCR 的 pdf）自动走视觉转文字。 */
const afterUpload = (r: DlUploadResult) =>
  run('after-upload', async () => {
    msg.value = `上传完成：进库 ${r.ok_count} 份`
      + (r.rejected.length ? `，未入库 ${r.rejected.length} 份（见「上传台账」）` : '')
    await loadRoots()
    await loadFiles()
    if (tab.value === 'uploads') await loadUploads()
    const scans = files.value.filter(
      (f) => f.parse_state === '需OCR' && f.ext === '.pdf'
        && (r.items || []).some((i) => i.file_id === f.file_id || i.rel_path === f.rel_path))
    if (scans.length) await visionOcr(scans.map((f) => f.file_id))
  })

/** 视觉转文字：页间并行、计费播报。上传后自动触发，也可单独补跑。 */
const visionOcr = (fileIds: string[]) =>
  run('voc', async () => {
    msg.value = `对 ${fileIds.length} 份扫描件调视觉模型逐页转写（真出网、真计费，页间并行）…`
    const r = await api.dlVisionOcr({ file_ids: fileIds })
    const ok = r.items.filter((i) => i.ok)
    const bad = r.items.filter((i) => !i.ok)
    msg.value = `视觉转写：${ok.length} 份完成`
      + (ok.length ? `（${ok.reduce((s, i) => s + (i.chars || 0), 0)} 字 · ${r.elapsed_s}s · ${r.engine}）` : '')
      + (bad.length ? `，${bad.length} 份失败（见「上传台账」原因）` : '')
      + '。转写文本可信度比原生低一档，数字需人工复核——已可进入规则抽取。'
    await loadRoots()
    await loadFiles()
  })

/** 删一份托管文件。**引用目录的删不了**，后端回 400；
 *  已产出条款/规则明细的回 409——若确认是传错的文件，升级为
 *  「连产出一并作废删除」。 */
const delFile = (f: DlFile) =>
  run(f.file_id + ':del', async () => {
    let d: Awaited<ReturnType<typeof api.dlDeleteFile>>
    try {
      d = await api.dlDeleteFile(f.file_id, 'ui')
    } catch (e) {
      const detail = (e as { status?: number; detail?: string })
      if (detail.status === 409) {
        // 传错文件的出路：确认后连产出一起作废删除（后端一个事务级联清）
        const ok = window.confirm(detail.detail
          + '\n\n若是传错的文件，可选择「连产出一并作废删除」——'
          + '上面列出的条款/草稿/明细/读图会随文件一起清掉（它们出自误传文件，本就不该存在）。\n确认连产出一起删除？')
        if (!ok) { msg.value = '已取消。要按正规流程处理，去「抽取与审定」页签把产出作废后再删。'; return }
        d = await api.dlDeleteFile(f.file_id, 'ui', true)
      } else {
        throw e
      }
    }
    pendingDel.value = ''
    const purgedTxt = d.purged && Object.keys(d.purged).length
      ? '已一并作废：' + Object.entries(d.purged).map(([k, v]) => `${v} 条${k}`).join('、') + '。'
      : ''
    msg.value = `已删除「${d.file_name}」。`
      + (d.removed_from_disk ? '磁盘文件已删，' : '磁盘上本来就没有这个文件，')
      + '登记行按「消失」保留。' + purgedTxt
    await loadRoots()
    await loadFiles()
  })

const loadChanges = () =>
  run('changes', async () => {
    const r = await api.dlChanges({ limit: 1000 })
    changes.value = r.items
    changeScan.value = r.scan || null
  })

const addRoots = (paths: string[]) =>
  run('add', async () => {
    const r = await api.dlAddRoots(paths)
    picking.value = false
    msg.value = `加入 ${r.ok_count} 个文件夹`
      + (r.failed.length ? `，${r.failed.length} 个未加入：${r.failed[0].reason}` : '')
    await loadRoots()
    if (r.ok_count) await scan(false)
  })

const toggleRoot = (r: DlRoot) =>
  run(r.root_id + ':toggle', async () => {
    await api.dlSetRoot(r.root_id, { enabled: !r.enabled })
    await loadRoots()
  })

const removeRoot = (r: DlRoot) =>
  run(r.root_id + ':del', async () => {
    const d = await api.dlRemoveRoot(r.root_id)
    pendingRemove.value = ''
    msg.value = `已移出文件库（删登记 ${d.files_unregistered} 条，磁盘文件未做任何改动）`
    await loadRoots()
    await loadFiles()
  })

const scan = (force: boolean) =>
  run(force ? 'rescan' : 'scan', async () => {
    const r = await api.dlScan({ force })
    lastScan.value = r
    msg.value = `扫描完成：新增 ${r.added}、修改 ${r.updated}、未变 ${r.unchanged}、消失 ${r.missing}`
    await loadRoots()
    await loadFiles()
    await loadChanges()
  })

const open = (f: DlFile) =>
  run(f.file_id + ':open', async () => {
    opened.value = await api.dlFile(f.file_id)
  })

function pickTab(t: Tab) {
  tab.value = t
  if (t === 'files' && !files.value.length) loadFiles()
  if (t === 'uploads' && !ledger.value.length) loadUploads()
  if (t === 'changes' && !changes.value.length) loadChanges()
  if (t === 'vision' && !vpages.value.length) loadVision()
}

const loadVision = () =>
  run('v', async () => {
    const [p, s2] = await Promise.all([api.visionPages(60), api.visionSummary()])
    vpages.value = p.items
    vsum.value = { totals: s2.totals as unknown as Record<string, number>, note: s2.note }
  })

const openPage = (p: VisionPage) =>
  run('vp', async () => {
    const key = p.file_id + ':' + p.page_no
    vopen.value = vopen.value === key ? '' : key
    vreads.value = []
    vcross.value = null
    if (!vopen.value) return
    const d = await api.visionPage(p.file_id, p.page_no)
    vreads.value = d.reads
    vcross.value = d.cross_check
  })

/** **这一下会出网、会计费。** 按钮文案里必须写清楚。 */
const readPage = (p: VisionPage) =>
  run('read', async () => {
    const r = await api.visionRead({
      file_id: p.file_id, page_no: p.page_no,
      kind: p.has_table ? 'table' : 'text',
    })
    vmsg.value = r.ok
      ? `${r.model_name} 读完：${r.rows ?? 0} 行、看不清 ${r.unreadable ?? 0} 格、`
        + `${r.latency_ms}ms、token ${r.tokens.prompt_tokens}/${r.tokens.completion_tokens}`
      : `**失败** ${r.err_kind}：${(r.message || '').slice(0, 120)}`
    await loadVision()
    if (vopen.value === p.file_id + ':' + p.page_no) {
      const d = await api.visionPage(p.file_id, p.page_no)
      vreads.value = d.reads
      vcross.value = d.cross_check
    }
  })

const unreadable = computed(() =>
  (stats.value?.by_state || []).filter((s) => s.state !== '已解析')
    .reduce((a, b) => a + b.n, 0))
const needOcr = computed(() =>
  (stats.value?.by_state || []).find((s) => s.state === '需OCR')?.n || 0)

const byOrigin = computed(() => {
  const m: Record<string, number> = { 上传: 0, 扫描: 0 }
  for (const o of stats.value?.by_origin || []) m[o.origin] = o.n
  return m
})
/** 托管根与引用根分开列：**两者的权限与可靠性都不一样**。 */
const managedRoots = computed(() => roots.value.filter((r) => r.kind === '托管'))
const refRoots = computed(() => roots.value.filter((r) => r.kind === '引用'))

function kb(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1048576).toFixed(1)} MB`
}
function t(s: string | null | undefined) {
  return s ? String(s).replace('T', ' ').slice(0, 19) : '—'
}

const rootCols = [
  { title: '启用', key: 'on', width: 56, align: 'center' as const },
  { title: '名称', key: 'label', width: 220 },
  { title: '服务器路径', key: 'path', width: 300 },
  { title: '文件数', dataIndex: 'files', width: 80, align: 'right' as const },
  { title: '占用', key: 'size', width: 96, align: 'right' as const },
  { title: '上次扫描', key: 'scan', width: 160 },
]
const fileCols = [
  { title: '文件', key: 'name' },
  { title: '类别', key: 'kind', width: 80, align: 'center' as const },
  { title: '来源', key: 'origin', width: 90, align: 'center' as const },
  { title: '文号 / 标题', key: 'docno', width: 170 },
  { title: '格式', dataIndex: 'ext', width: 56, align: 'center' as const },
  { title: '大小', key: 'size', width: 84, align: 'right' as const },
  { title: '能否识别', key: 'rec', width: 130 },
  { title: '备注', key: 'note' },
  { title: '操作', key: 'op', width: 180 },
]
const ledgerCols = [
  { title: '时间', key: 'at', width: 150 },
  { title: '文件', key: 'name' },
  { title: '结果', key: 'result', width: 110 },
  { title: '大小', key: 'size', width: 84, align: 'right' as const },
  { title: '上传人', key: 'by', width: 96 },
  { title: '说明', key: 'reason' },
]
const changeCols = [
  { title: '变化', key: 'change', width: 76, align: 'center' as const },
  { title: '文件', key: 'name' },
  { title: '说明', key: 'detail' },
  { title: '记录时间', key: 'at', width: 150 },
]
const visionCols = [
  { title: '文件', dataIndex: 'file_name' },
  { title: '页', dataIndex: 'page_no', width: 50, align: 'center' as const },
  { title: 'OCR 字', key: 'chars', width: 76, align: 'right' as const },
  { title: 'OCR 置信', dataIndex: 'mean_conf', width: 84, align: 'right' as const },
  { title: '表格', key: 'table', width: 96, align: 'center' as const },
  { title: '已读', key: 'reads', width: 60, align: 'right' as const },
  { title: '操作', key: 'op', width: 200 },
]

onMounted(loadRoots)

/** 行键：数据源无稳定业务键时按记录对象发号（单参，AntD4 弃用双参 rowKey） */
const rowSeq = new WeakMap<object, string>()
let rowSeqN = 0
const rowKeyOf = (r: unknown): string => {
  if (r && typeof r === 'object') {
    let k = rowSeq.get(r)
    if (!k) { k = 'r' + ++rowSeqN; rowSeq.set(r as object, k) }
    return k
  }
  return String(r)
}
</script>

<template>
  <div class="dl">
  <a-card v-if="stats" size="small" class="statbar">
    <div class="stat-oneline">
      <div><b>{{ stats.files }}</b><span>在库文件</span></div>
      <div><b>{{ byOrigin['上传'] }}</b><span>客户端上传</span></div>
      <div><b>{{ byOrigin['扫描'] }}</b><span>引用目录扫描</span></div>
      <div><b>{{ stats.readable }}</b><span>已取到正文</span></div>
      <div><b>{{ needOcr }}</b><span>需 OCR</span></div>
      <div><b>{{ unreadable - needOcr }}</b><span>取不到正文</span></div>
      <div><b>{{ kb(stats.bytes) }}</b><span>总体积</span></div>
      <div><b>{{ stats.missing }}</b><span>已消失</span></div>
      <div v-if="stats.instance_label" class="scope">
        {{ stats.domain_label }} / {{ stats.instance_label }}
      </div>
    </div>
  </a-card>

  <a-card size="small" style="margin-top: 16px">
    <a-tabs v-model:active-key="tab" @change="(k: any) => pickTab(k as Tab)">
      <template #rightExtra>
        <span v-if="busy" class="dim">{{ busy }} 处理中…</span>
      </template>

      <a-tab-pane key="roots" tab="文件来源">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <a-alert v-if="msg" type="success" show-icon closable :message="msg" style="margin-bottom: 12px" />

        <!-- 上传放在最前：它是默认入口。 -->
        <h3 class="sec">上传制度文件</h3>
        <Uploader :storage="storage" @done="afterUpload" />

        <template v-if="managedRoots.length">
          <h3 class="sec">托管目录（上传落在这里，可写可删）</h3>
          <a-table :columns="rootCols" :data-source="managedRoots" row-key="root_id"
            size="small" :pagination="false">
            <template #bodyCell="{ column, record: r }">
              <template v-if="column.key === 'on'">
                <a-checkbox :checked="r.enabled" @change="toggleRoot(r)" />
              </template>
              <template v-else-if="column.key === 'label'">
                {{ r.label }}
                <a-tag :color="KIND_ROOT_COLOR[r.kind]" style="margin-left: 5px">{{ r.kind }}</a-tag>
              </template>
              <template v-else-if="column.key === 'path'">
                <code class="pathcode">{{ r.abs_path }}</code>
                <div v-if="!r.exists"><a-tag color="error" style="margin-top: 3px">目录已不存在（下一次上传会自动重建）</a-tag></div>
              </template>
              <template v-else-if="column.key === 'size'">
                {{ (storage?.dirs || []).find(d => d.root_id === r.root_id)?.size_text || '—' }}
              </template>
              <template v-else-if="column.key === 'scan'">
                <span class="dim">{{ t(r.last_scan_at) }}</span>
              </template>
            </template>
          </a-table>
          <div class="blk-note">
            托管目录是<b>本系统自己的工作区</b>，所以允许写入与删除；
            而下面的引用目录是<b>客户的资料目录</b>，本系统对它只读，一个字节都不写。
            托管目录由系统按当前实例自动创建，<b>不提供整体移出</b>——
            整体移出会把条款、OCR、读图结果、规则明细一起级联删掉，
            而磁盘上的文件会留下来变成没人认领的孤儿。要清空请在「文件清单」里逐份删。
          </div>
        </template>

        <h3 class="sec">纳入服务器上已有的目录（引用，原地只读扫描）· 已纳入 {{ refRoots.length }} 个</h3>
        <div class="blk-note">
          <b>这不是上传。</b>它要求路径在<b>服务器</b>上存在，适合「资料已经在服务器上了」
          的情况。如果浏览器和服务不在同一台机器上，这里选不到你本机的文件，请用上面的上传。
          纳入只登记路径，<b>不复制、不移动、不修改磁盘上的任何文件</b>。
        </div>
        <FolderPicker v-if="picking" @add="addRoots" @close="picking = false" />
        <a-table v-if="refRoots.length" :columns="rootCols.filter(c => c.key !== 'size')"
          :data-source="refRoots" row-key="root_id" size="small" :pagination="false">
          <template #bodyCell="{ column, record: r }">
            <template v-if="column.key === 'on'">
              <a-checkbox :checked="r.enabled" @change="toggleRoot(r)" />
            </template>
            <template v-else-if="column.key === 'label'">
              {{ r.label }}
              <a-tag :color="KIND_ROOT_COLOR[r.kind]" style="margin-left: 5px">{{ r.kind }}</a-tag>
            </template>
            <template v-else-if="column.key === 'path'">
              <code class="pathcode">{{ r.abs_path }}</code>
              <div v-if="!r.exists"><a-tag color="error" style="margin-top: 3px">文件夹已不存在</a-tag></div>
              <div v-if="r.note" class="dim" style="font-size: 11px"><MarkText :text="r.note" /></div>
            </template>
            <template v-else-if="column.key === 'scan'">
              <span class="dim">{{ t(r.last_scan_at) || '未扫描' }}</span>
            </template>
          </template>
        </a-table>
        <div v-else class="dim" style="margin: 8px 0">尚未纳入任何服务器目录</div>
        <div class="row" style="margin: 12px 0 20px">
          <a-button @click="picking = !picking">
            {{ picking ? '收起选择器' : '＋ 浏览服务器目录' }}
          </a-button>
        </div>

        <h3 class="sec">扫描</h3>
        <div class="blk-note">
          上传之后会<b>自动扫描一次</b>，不用手点。这里的按钮给两种情况：
          引用目录里的文件被客户改过了；或者改过解析器要全量重跑（强制重扫）。
          不指定范围时<b>只扫当前实例的根</b>。
        </div>
        <div class="row">
          <a-button :loading="busy === 'scan'" :disabled="!!busy || !roots.length" @click="scan(false)">扫描</a-button>
          <a-button :loading="busy === 'rescan'" :disabled="!!busy || !roots.length" @click="scan(true)">强制重扫</a-button>
        </div>

        <div v-if="lastScan" class="blk-note">
          <b>本次扫描 {{ t(lastScan.scanned_at) }}（{{ lastScan.mode }}）：</b>
          新增 <b>{{ lastScan.added }}</b>、修改 <b>{{ lastScan.updated }}</b>、
          未变 {{ lastScan.unchanged }}、消失 <b>{{ lastScan.missing }}</b>
          <div v-for="w in lastScan.warnings" :key="w" class="warn-text">{{ w }}</div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="files" tab="文件清单">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <a-alert v-if="msg" type="success" show-icon closable :message="msg" style="margin-bottom: 12px" />
        <div class="row" style="margin-bottom: 12px">
          <a-input v-model:value="fq" placeholder="按文件名筛选" style="flex: 1; min-width: 160px"
                   @keyup.enter="loadFiles" />
          <a-select v-model:value="fext" style="width: 140px" @change="loadFiles"
            :options="[{ value: '', label: '全部类型' },
              ...(stats?.by_ext || []).map((e: any) => ({ value: e.ext, label: `${e.ext}（${e.n}）` }))]" />
          <a-select v-model:value="fstate" style="width: 150px" @change="loadFiles"
            :options="[{ value: '', label: '全部识别状态' },
              ...(stats?.by_recognize || []).map((s: any) => ({ value: s.label, label: `${s.label}（${s.n}）` }))]" />
          <a-select v-model:value="fkind" style="width: 130px" @change="loadFiles"
            :options="[{ value: '', label: '全部类别' },
              ...(stats?.by_kind || []).map((k: any) => ({ value: k.kind, label: `${k.kind}（${k.n}）` }))]" />
          <a-select v-model:value="forigin" style="width: 130px" @change="loadFiles"
            :options="[{ value: '', label: '全部来源' },
              ...(stats?.by_origin || []).map((o: any) => ({ value: o.origin, label: `${o.origin}（${o.n}）` }))]" />
          <a-button type="primary" @click="loadFiles">筛选</a-button>
        </div>

        <a-table v-if="files.length" :columns="fileCols" :data-source="files" row-key="file_id"
          size="small" :pagination="files.length > 10 ? { pageSize: 10, showTotal: (n: number) => `共 ${n} 份` } : false">
          <template #bodyCell="{ column, record: f, index }">
            <template v-if="column.key === 'name'">
              <a style="cursor: pointer" @click.prevent="open(f)">{{ f.file_name }}</a>
              <div class="dim pathcode">{{ f.rel_path }}</div>
              <!-- 同名的另一个版本：不标出来，使用者会以为新版本替换了旧的。 -->
              <a-tag v-if="f.name_copies > 1" color="processing" style="margin-top: 3px">
                同名另有 {{ f.name_copies - 1 }} 个版本
              </a-tag>
            </template>
            <template v-else-if="column.key === 'kind'">
              <a-tag :color="KIND_COLOR[f.doc_kind || '其他'] || 'default'">{{ f.doc_kind || '其他' }}</a-tag>
            </template>
            <template v-else-if="column.key === 'origin'">
              <a-tag :color="f.origin === '上传' ? 'success' : 'default'">{{ f.origin }}</a-tag>
              <div v-if="f.uploaded_by" class="dim" style="font-size: 11px">{{ f.uploaded_by }}</div>
            </template>
            <template v-else-if="column.key === 'docno'">
              <div v-if="f.doc_number" class="dim">{{ f.doc_number }}</div>
              <div v-if="f.doc_title" style="font-size: 11px">《{{ f.doc_title }}》</div>
              <span v-if="!f.doc_number && !f.doc_title" class="dim">—</span>
            </template>
            <template v-else-if="column.key === 'size'">{{ kb(f.file_size) }}</template>
            <template v-else-if="column.key === 'rec'">
              <a-tag :color="STATE_COLOR[f.parse_state] || 'default'"
                >{{ f.recognize?.label || f.parse_state }}</a-tag>
              <div class="dim" style="font-size: 11px; margin-top: 2px">{{ f.recognize?.how }}</div>
            </template>
            <template v-else-if="column.key === 'note'">
              <span class="dim"><MarkText :text="f.recognize?.note || f.parse_reason || ''" /></span>
            </template>
            <template v-else-if="column.key === 'op'">
              <!-- 需OCR 的扫描件：视觉转文字一键进抽取链（真出网计费）。
                   上传后已自动跑过一次；这里是补跑/手动入口（预置扫描件用）。 -->
              <a-button v-if="f.parse_state === '需OCR' && f.ext === '.pdf'" size="small"
                        style="margin-bottom: 4px"
                        title="把每一页发给多模态模型逐页转写（真出网、真计费，页间并行）"
                        @click="visionOcr([f.file_id])">视觉转文字</a-button>
              <!-- 删除只对托管文件开放。引用目录里的文件是客户的资料，
                   本系统对它只读——按钮直接不给，而不是点了再报错。 -->
              <template v-if="f.deletable">
                <a-popconfirm v-if="pendingDel !== f.file_id" title="确认删除这份托管文件？"
                  @confirm="delFile(f)" @cancel="pendingDel = ''"
                  :open="pendingDel === f.file_id"
                  ok-text="确认删除" cancel-text="取消"
                  @show="() => {}">
                  <a-button danger size="small" :disabled="!!busy"
                            @click="pendingDel = f.file_id">删除</a-button>
                </a-popconfirm>
                <template v-else>
                  <a-button danger size="small" :disabled="!!busy" @click="delFile(f)">确认删除</a-button>
                  <a-button size="small" :disabled="!!busy" style="margin-left: 4px"
                            @click="pendingDel = ''">取消</a-button>
                </template>
              </template>
              <a-tooltip v-else title="引用目录是客户的资料目录，本系统对它只读">
                <span class="dim">引用 · 只读</span>
              </a-tooltip>
            </template>
          </template>
          <template #emptyText>
            <a-empty :image-style="{ height: '40px' }" description="没有符合条件的文件" />
          </template>
        </a-table>
        <a-empty v-else :image-style="{ height: '40px' }" description="没有符合条件的文件" />

        <div class="blk-note">
          <b>删除</b>只对托管目录里的文件开放，而且<b>已产出条款、规则明细或已签字读图的会被拒</b>——
          删掉原文之后那些条款会指向一份不存在的文件，而页面上它们看着仍然有出处，
          这种断链基本查不出来。删除成功时<b>磁盘文件删掉、登记行按「消失」保留</b>：
          删登记行会连带级联清空 OCR、读图、条款、规则明细，
          而「这份资料曾经在库里」是溯源的一部分，只能作废不能蒸发。
        </div>

        <template v-if="opened">
          <h3 class="sec">{{ opened.file_name }}</h3>
          <div class="kv">
            <b>路径</b><span class="pathcode">{{ opened.abs_path }}</span>
            <b>解析状态</b>
            <span>
              <a-tag :color="STATE_COLOR[opened.parse_state] || 'default'">{{ opened.parse_state }}</a-tag>
              <span class="dim" style="margin-left: 8px"><MarkText :text="opened.parse_reason" /></span>
            </span>
            <b>正文字数</b><span>{{ opened.text_chars }}{{ opened.truncated ? '（预览已截断）' : '' }}</span>
          </div>
          <!-- 正文预览：完成要求是「页面内即可看到清单及文件，不依赖打开本地文件夹」。
               需OCR 的扫描件没有正文，如实说明，不装作有。 -->
          <pre v-if="opened.preview" class="doc-preview">{{ opened.preview }}</pre>
          <div v-else class="dim" style="margin-top: 8px; font-size: 12.5px">
            这份文件没有可预览的正文：{{ opened.parse_reason || '解析状态为 ' + opened.parse_state }}
            <template v-if="opened.parse_state === '需OCR'">——扫描件可在清单里点「视觉转文字」（真出网、真计费）</template>
          </div>
        </template>
      </a-tab-pane>

      <!-- 抽取与审定（2026-10-27 P0-4）：四步工作流的②跑AI+③审定。
           旧 Extract.vue 退役后这步在界面上断了；此页签把它挂回来，
           兑现 navTree 注释宣称的「制度文件页=文件库+抽取合并」（客户待办#8）。 -->
      <a-tab-pane key="extract" tab="抽取与审定">
        <ExtractReview />
      </a-tab-pane>

      <!-- 上传台账：客户传了 20 份而库里只多 17 份时，差的 3 份去哪了必须答得上来。 -->
      <a-tab-pane key="uploads" tab="上传台账">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <a-alert v-if="msg" type="success" show-icon closable :message="msg" style="margin-bottom: 12px" />

        <template v-if="storage">
          <h3 class="sec">托管区占用</h3>
          <a-row :gutter="16">
            <a-col :span="6"><a-statistic title="托管文件" :value="storage.total_files" /></a-col>
            <a-col :span="6"><a-statistic title="占用磁盘" :value="kb(storage.total_bytes)" /></a-col>
            <a-col :span="6"><a-statistic title="单份上限" :value="storage.limits.max_upload_bytes_text" /></a-col>
            <a-col :span="6"><a-statistic title="单次份数上限" :value="storage.limits.max_upload_files" /></a-col>
          </a-row>
          <div class="kv" style="margin-top: 12px">
            <b>托管区根目录</b><span class="pathcode">{{ storage.root_dir }}</span>
            <b>允许的扩展名</b><span class="pathcode">{{ storage.limits.allowed_ext.join(' ') }}</span>
          </div>
          <a-table v-if="storage.dirs.length" :data-source="storage.dirs" row-key="root_id"
            size="small" :pagination="false" style="margin-top: 12px"
            :columns="[
              { title: '归属', key: 'own', width: 220 },
              { title: '目录', key: 'dir' },
              { title: '文件', dataIndex: 'files', width: 70, align: 'right' },
              { title: '占用', key: 'size', width: 90, align: 'right' },
            ]">
            <template #bodyCell="{ column, record: d }">
              <template v-if="column.key === 'own'">{{ d.domain_label }} / {{ d.instance_label }}</template>
              <template v-else-if="column.key === 'dir'">
                <code class="pathcode">{{ d.abs_path }}</code>
                <div v-if="!d.exists"><a-tag color="error" style="margin-top: 3px">目录已不存在</a-tag></div>
              </template>
              <template v-else-if="column.key === 'size'">{{ d.size_text }}</template>
            </template>
          </a-table>
          <!-- 暂存区本该是空的。非空说明有一次上传崩在中途，必须报出来。 -->
          <a-alert v-if="storage.staging_stale.length" type="error" show-icon style="margin-top: 12px"
            :message="`暂存区有 ${storage.staging_stale.length} 个残留文件（${storage.staging_dir}）：每份上传要么移入托管目录、要么当场删掉，残留说明有一次上传崩在中途。这些文件没有任何登记指向它们，可以直接删。`" />
          <div v-else class="blk-note"><MarkText :text="storage.note" /></div>
        </template>

        <h3 class="sec">上传台账（{{ ledger.length }} 行）</h3>
        <div class="row" style="margin-bottom: 12px">
          <a-checkbox v-model:checked="onlyRejected" @change="loadUploads">只看未入库的</a-checkbox>
          <a-button :disabled="!!busy" @click="loadUploads">刷新</a-button>
          <a-tag v-for="b in ledgerBy" :key="b.result" :color="RESULT_COLOR[b.result] || 'default'">
            {{ b.result }} {{ b.n }}
          </a-tag>
        </div>
        <a-table v-if="ledger.length" :columns="ledgerCols" :data-source="ledger"
          :row-key="rowKeyOf" size="small"
          :pagination="ledger.length > 10 ? { pageSize: 10, showTotal: (n: number) => `共 ${n} 条` } : false">
          <template #bodyCell="{ column, record: u }">
            <template v-if="column.key === 'at'"><span class="dim">{{ t(u.uploaded_at) }}</span></template>
            <template v-else-if="column.key === 'name'">
              <div class="pathcode">{{ u.rel_path || u.file_name }}</div>
              <div v-if="u.stored_name && u.stored_name !== u.file_name" class="dim" style="font-size: 11px">
                落盘名 → <b>{{ u.stored_name }}</b>
              </div>
            </template>
            <template v-else-if="column.key === 'result'">
              <a-tag :color="RESULT_COLOR[u.result] || 'default'">{{ u.result }}</a-tag>
              <div v-if="u.missing" class="dim" style="font-size: 11px">已删除</div>
            </template>
            <template v-else-if="column.key === 'size'">
              {{ u.file_size == null ? '—' : kb(u.file_size) }}
            </template>
            <template v-else-if="column.key === 'by'">{{ u.uploaded_by || '—' }}</template>
            <template v-else-if="column.key === 'reason'">
              <span class="dim"><MarkText :text="u.reason || '—'" /></span>
            </template>
          </template>
        </a-table>
        <a-empty v-else :image-style="{ height: '40px' }" description="还没有上传过文件" />
        <div class="blk-note">
          <b>「同名另存」和「同内容已存在」是相反的两件事。</b>
          前者=文件名一样但内容不同，已按新版本落盘（制度换了一版，旧的没被覆盖）；
          后者=字节完全相同，什么都没落盘（重复点了上传）。
          合成一个「重复」，使用者就不知道自己刚传的新版本到底进没进库。
        </div>
      </a-tab-pane>

      <a-tab-pane key="changes" tab="变化台账">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <div v-if="changeScan" class="blk-note">
          最近一次扫描 <code class="pathcode">{{ changeScan.scan_id }}</code>
          ｜{{ t(changeScan.started_at as string) }}｜{{ changeScan.mode }}
          ｜新增 {{ changeScan.added }}·修改 {{ changeScan.updated }}·消失 {{ changeScan.missing }}
        </div>
        <a-table v-if="changes.length" :columns="changeCols" :data-source="changes" row-key="id"
          size="small" :pagination="changes.length > 10 ? { pageSize: 10 } : false">
          <template #bodyCell="{ column, record: c }">
            <template v-if="column.key === 'change'">
              <a-tag :color="CHANGE_COLOR[c.change]">{{ c.change }}</a-tag>
            </template>
            <template v-else-if="column.key === 'name'">
              {{ c.file_name }}
              <div class="dim pathcode" style="font-size: 11px">{{ c.rel_path }}</div>
            </template>
            <template v-else-if="column.key === 'detail'">
              <span class="dim"><MarkText :text="c.detail" /></span>
            </template>
            <template v-else-if="column.key === 'at'">
              <span class="dim">{{ t(c.noted_at) }}</span>
            </template>
          </template>
          <template #emptyText>
            <a-empty :image-style="{ height: '40px' }" description="最近一次扫描没有变化" />
          </template>
        </a-table>
        <a-empty v-else :image-style="{ height: '40px' }" description="最近一次扫描没有变化" />
      </a-tab-pane>

      <a-tab-pane key="vision" tab="读图">
        <a-alert v-if="err" type="error" show-icon :message="err" style="margin-bottom: 12px" />
        <h3 class="sec">多模态读图</h3>
        <div v-if="vsum" class="kv">
          <b>已调用</b>
          <span>
            {{ vsum.totals.calls }} 次（失败 {{ vsum.totals.failed }}）·
            {{ vsum.totals.models }} 个型号 ·
            入 {{ vsum.totals.pt }} token / 出 {{ vsum.totals.ct }} token
          </span>
          <b>口径</b><span class="dim"><MarkText :text="vsum.note" /></span>
        </div>
        <div class="row" style="margin: 8px 0">
          <a-input v-model:value="vwho" placeholder="签字人署名" style="width: 150px" />
        </div>
        <a-alert v-if="vmsg" type="info" show-icon :message="vmsg" style="margin-bottom: 12px" />
        <a-table :columns="visionCols" :data-source="vpages"
          :row-key="(p: any) => p.file_id + ':' + p.page_no" size="small"
          :pagination="vpages.length > 10 ? { pageSize: 10 } : false"
          :expanded-row-keys="vopen ? [vopen] : []"
          @expand="(_e: any, p: any) => openPage(p)">
          <template #bodyCell="{ column, record: p }">
            <template v-if="column.key === 'chars'">
              <span :style="!p.chars ? 'color:#faad14' : ''">{{ p.chars }}</span>
            </template>
            <template v-else-if="column.key === 'table'">
              <a-tag v-if="p.has_table" color="warning">表格</a-tag>
              <a-tag v-else color="error">没认出字</a-tag>
            </template>
            <template v-else-if="column.key === 'reads'">
              <span :class="{ dim: !p.read_times }">{{ p.read_times || '—' }}</span>
            </template>
            <template v-else-if="column.key === 'op'">
              <a-button size="small" :disabled="!!busy" @click="readPage(p)">读一页（出网）</a-button>
              <a-button size="small" :disabled="!!busy" style="margin-left: 4px" @click="openPage(p)">
                {{ vopen === p.file_id + ':' + p.page_no ? '收起' : '看结果' }}
              </a-button>
            </template>
          </template>
          <template #expandedRowRender="{ record: p }">
            <div v-if="vcross" class="kv">
              <b>互相印证</b>
              <span>
                <a-tag :color="vcross.agree === true ? 'success' : vcross.agree === false ? 'error' : 'default'">
                  {{ vcross.verdict }}</a-tag>
                <span v-if="vcross.a" class="dim" style="margin-left: 6px">
                  {{ vcross.a.model_name }}（{{ vcross.a.numbers }} 个数字 /
                  {{ vcross.a.rows }} 行 / 看不清 {{ vcross.a.unreadable }}）
                  vs {{ vcross.b?.model_name }}（{{ vcross.b?.numbers }} /
                  {{ vcross.b?.rows }} / {{ vcross.b?.unreadable }}）
                </span>
              </span>
              <b>怎么用</b><span class="dim"><MarkText :text="vcross.note" /></span>
              <template v-if="vcross.agree === false">
                <b>只有前者有</b>
                <span class="pathcode">{{ (vcross.only_a || []).join('、') || '—' }}</span>
                <b>只有后者有</b>
                <span class="pathcode">{{ (vcross.only_b || []).join('、') || '—' }}</span>
              </template>
            </div>
            <div v-for="r in vreads" :key="r.model_name" class="readblk">
              <div class="row" style="margin: 0 0 4px">
                <b>{{ r.model_name }}</b>
                <span class="dim">
                  第 {{ r.attempt }} 次 · {{ r.rows_parsed }} 行 ·
                  看不清 {{ r.unreadable }} 格 · {{ r.latency_ms }}ms ·
                  token {{ r.prompt_tokens }}/{{ r.completion_tokens }}
                </span>
              </div>
              <pre class="vtext">{{ r.content }}</pre>
            </div>
            <div v-if="!vreads.length" class="dim">还没读过这一页。</div>
          </template>
          <template #emptyText>
            <a-empty :image-style="{ height: '40px' }" description="暂无待读图页面" />
          </template>
        </a-table>

        <Async :load="() => api.visionStorage()">
          <template #default="{ data }">
            <h3 class="sec">读图 → 印证 → <b>人工签字</b> → 才谈得上录入</h3>
            <a-table :data-source="(data as any).items" row-key="table_name" size="small"
              :pagination="false"
              :columns="[
                { title: '表', dataIndex: 'table_name', width: 170 },
                { title: '存的是什么', key: 'what', width: 240 },
                { title: '谁写的', key: 'by', width: 170 },
                { title: '重算时', dataIndex: 'on_recalc', width: 120 },
                { title: '为什么这么存', key: 'why' },
              ]">
              <template #bodyCell="{ column, record: x }">
                <template v-if="column.key === 'what'"><span class="dim">{{ pro(x.what) }}</span></template>
                <template v-else-if="column.key === 'by'"><span class="dim">{{ pro(x.written_by) }}</span></template>
                <template v-else-if="column.key === 'why'"><span class="dim"><MarkText :text="pro(x.why)" /></span></template>
              </template>
            </a-table>
            <div class="blk-note"><MarkText :text="(data as any).note" /></div>
          </template>
        </Async>
      </a-tab-pane>
    </a-tabs>
  </a-card>
  </div>
</template>

<style scoped>
.statbar :deep(.ant-card-body) { padding: 12px 16px; }
/* 统计条一行排：九个指标并排（2026-10-24 用户要求），窄屏横向滚动 */
.stat-oneline { display: flex; flex-wrap: nowrap; gap: 32px; overflow-x: auto; align-items: baseline; }
.stat-oneline > div { min-width: 0; white-space: nowrap; }
.stat-oneline b { font-size: 18px; font-weight: 600; display: block; font-variant-numeric: tabular-nums; }
.stat-oneline span { font-size: 11.5px; color: rgba(0, 0, 0, .45); }
.stat-oneline .scope { margin-left: auto; align-self: center; font-size: 11px; color: rgba(0, 0, 0, .4); }

.sec { font-size: 14px; font-weight: 600; margin: 18px 0 10px; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 6px 16px; font-size: 13px; }
.kv b { color: rgba(0, 0, 0, .45); font-weight: 500; align-self: baseline; }
.blk-note { font-size: 12px; color: rgba(0, 0, 0, .55); margin: 8px 0 12px; line-height: 1.7; }
.warn-text { color: #ff4d4f; margin-top: 4px; font-size: 12px; }
/* 反斜杠路径不是 CSS 断行点，break-all 按字符断，列宽才被真正用满 */
.pathcode { font-size: 11px; word-break: break-all; line-height: 1.5; background: #fafafa; padding: 1px 4px; border-radius: 3px; }

.readblk { border-left: 3px solid #e5e7eb; padding: 4px 0 6px 9px; margin: 8px 0; }
.vtext {
  max-height: 320px; overflow: auto; background: #fafafa; border: 1px solid #f0f0f0;
  border-radius: 6px; padding: 8px; font-size: 11px; line-height: 1.55;
  white-space: pre-wrap; margin: 0;
}
/* 文件正文预览：等宽 + 限高滚动，长文档（截断到 4000 字）也能在面板内读完 */
.doc-preview {
  margin-top: 10px; padding: 12px 14px; background: #fafafa;
  border: 1px solid #f0f0f0; border-radius: 6px;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px; line-height: 1.7; white-space: pre-wrap; word-break: break-all;
  max-height: 340px; overflow: auto;
}
</style>
