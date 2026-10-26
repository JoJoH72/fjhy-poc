<script setup lang="ts">
/**
 * 审核报告 + 人工复核闸门（2026-10-24 改版，契约：设计/审核端改版demo.html）。
 *
 * 结构：常驻队列栏（清单层：搜索/筛选/排序/虚拟滚动）+ 队列状态条（翻张/进度）
 * → 复核闸门（kv 瓷砖 + 按钮上下两行）→ 单据信息（明细/审批）→ 规则判定明细
 * （四态合表 + 证据展开 + 制度依据）→ 附件（点开大图）→ 历次审核。
 * 键盘：←/→ 翻张；提交（通过/退回）后自动下一张。
 *
 * 三个诚实点必须保住（原版页面的立页之本，改版不许丢）：
 * 1. 覆盖面数字并列：候选 82 / 执行 28 / 命中 / 待人工 / 未执行 54——
 *    「待人工」不是「通过」，未执行的规则要让审核岗看见。
 * 2. 命中原文回显（matched_text）与制度依据（clause_ref）挨着证据展示。
 * 3. AI 结论仅供参考；「我不认可这条命中」的勾选挨着证据，不搬去闸门。
 *
 * 后端边界（缺口已登记，勿静默绕过）：
 * - 人工决策只有 agree / agree_with_note / return 三种——demo 契约里的第四键
 *   「复核不通过」经行业口径确认属设计冗余（费用审核无终审否决出口），已删除。
 * - 两键配色 = 行内规矩（2026-10-24 用户定）：同意绿、退回红。
 * - 2026-10-25 起人工结论已回流后端（/api/bills manual=none/done 过滤 +
 *   GET /api/bills/{no}/manuals + 同轮 409 防重 + disagree 回流 feedback_log）：
 *   队列真减、留痕持久；review_run.conclusion 仍是 AI 结论，两者分开存。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Async from '../components/Async.vue'
import * as session from '../data/session'
import { api, type Finding, type ReviewReport, type Bill, type ManualReview, type Page } from '../api'

const route = useRoute()
const router = useRouter()
// runId 可空：/review/report 从菜单直接进（demo 契约的四菜单之一）——
// 空时自动跳队列首张；没有待复核单据时给空态，不装默认数据
const runId = computed(() => (route.params.runId ? String(route.params.runId) : ''))

/* ── 复核队列：不通过 + 待复核 两种结论合并 ──
 * 2026-10-25 由横条胶片改为「状态条 + 队列抽屉」（清单-详情分层）：横条在几千张
 * 队列下找不到（无搜索）、无分流（无筛选排序）、无进度感，客户量产规模必换。
 * 抽屉 = 清单层（搜索/筛选/排序/虚拟滚动/已处理打勾），本页 = 详情层（键盘翻张、
 * 提交后自动下一张）。可钉住成常驻左栏，也可临时弹出点完即走。
 *
 * 后端缺口（登记勿绕）：/api/bills 无服务端排序/筛选，队列只能按结论分页全量拉
 * （每页 200、上限 50 页封顶 1 万张），排序筛选全在前端做。上万张时需服务端
 * 排序分页接口，前端抽屉直连。 */
interface QueueBill { no: string; run_id: string | null; hit: number; concl: 'reject' | 'review'; is_demo: boolean; amt: number; at: string }
const queue = ref<QueueBill[]>([])
const queueErr = ref('')
/** 已人工复核的累计（review+reject 两类合计）。与队列并排显示：复核一张，
 *  队列减一、这里加一——两个数对得上，回流才算真的（2026-10-25 前后端联动）。 */
const doneCount = ref(0)
async function fetchConcl(concl: 'reject' | 'review'): Promise<Bill[]> {
  const out: Bill[] = []
  for (let page = 1; page <= 50; page++) {
    // manual=none：只拉还没有人工结论的。复核一张，队列真少一张——
    // 持久在后端，刷新页面、换台机器都不回弹（此前只会话内打勾，一刷新就弹回）。
    const p = await api.bills({ conclusion: concl, manual: 'none', size: 200, page })
    out.push(...p.items)
    if (p.items.length < 200 || out.length >= p.total) break
  }
  return out
}
function riskCmp(a: QueueBill, b: QueueBill) {
  return a.concl === b.concl ? b.hit - a.hit : a.concl === 'reject' ? -1 : 1
}
async function loadQueue() {
  queueErr.value = ''
  try {
    const [rejBills, revBills, rejDone, revDone] = await Promise.all([
      fetchConcl('reject'), fetchConcl('review'),
      api.bills({ conclusion: 'reject', manual: 'done', size: 1 }),
      api.bills({ conclusion: 'review', manual: 'done', size: 1 }),
    ])
    doneCount.value = rejDone.total + revDone.total
    const map = (b: Bill, c: 'reject' | 'review'): QueueBill =>
      ({ no: b.bill_no, run_id: b.run_id, hit: b.hit_count ?? 0, concl: c, is_demo: b.is_demo, amt: b.amount ?? 0, at: b.finished_at || '' })
    queue.value = [...rejBills.map((b) => map(b, 'reject')), ...revBills.map((b) => map(b, 'review'))].sort(riskCmp)
    // 免参进入：有队列就 replace 到首张（可回退），没队列停在空态
    if (!runId.value && queue.value.length) pickQueue(queue.value[0])
  } catch (e) {
    queueErr.value = e instanceof Error ? e.message : String(e)
  }
}
loadQueue()

/* ── 报告 + 单据详情：并行取 ── */
const bill = ref<Record<string, any> | null>(null)
async function loadAll(): Promise<ReviewReport & { _bill?: Record<string, any> }> {
  const rep = await api.review(runId.value)
  try {
    bill.value = await api.bill(rep.run.bill_no)
  } catch { bill.value = null } // 单据详情挂了不拦报告本身
  return rep
}

const cur = computed<QueueBill | null>(() => queue.value.find((q) => q.run_id === runId.value) || null)
const qi = computed(() => (cur.value ? queue.value.findIndex((q) => q.no === cur.value!.no) : -1))
/** 队列构成（状态条分列显示）：review = AI 判不了待人工，reject = AI 命中不通过 */
const nReject = computed(() => queue.value.filter((q) => q.concl === 'reject').length)
const QUEUE_NOTE =
  '队列 = AI 判待复核或 AI 不通过、且还没有人工结论的单（后端按 manual=none 过滤）。' +
  '复核通过/退回后该单移出队列并持久保存——刷新页面、换台机器都不回弹。' +
  '「已人工复核」是该口径下的累计复核数，与队列的增减互为印证。'

/* ── 队列栏：搜索 / 筛选 / 排序 / 虚拟滚动 ──
 * 2026-10-25 用户定稿：常驻左栏——不做开关、不做钉住，一直留着；
 * 翻张/跳转时当前单自动滚进视野，筛选排序时不跟滚（手在列表上）。 */
const qSearch = ref('')
type QSort = 'risk' | 'amt' | 'time'
const qSort = ref<QSort>('risk')
type QFilter = 'all' | 'hit3' | 'big' | 'real'
const qFilter = ref<QFilter>('all')

const viewQueue = computed(() => {
  let a = queue.value
  if (qFilter.value === 'hit3') a = a.filter((q) => q.hit >= 3)
  else if (qFilter.value === 'big') a = a.filter((q) => q.amt >= 10000)
  else if (qFilter.value === 'real') a = a.filter((q) => !q.is_demo)
  const s = qSearch.value.trim().toLowerCase()
  if (s) a = a.filter((q) => q.no.toLowerCase().includes(s))
  const cmp: Record<QSort, (x: QueueBill, y: QueueBill) => number> = {
    risk: riskCmp,
    amt: (x, y) => y.amt - x.amt,
    time: (x, y) => (x.at < y.at ? 1 : x.at > y.at ? -1 : 0),
  }
  return [...a].sort(cmp[qSort.value])
})

/* 虚拟滚动：行高固定 44px，只渲染视口 ±6 行，几千行也不卡 */
const listEl = ref<HTMLElement | null>(null)
const sTop = ref(0)
const listH = ref(600)
const ROW_H = 44
const OVER = 6
const vFirst = computed(() => Math.max(0, Math.floor(sTop.value / ROW_H) - OVER))
const vRows = computed(() => Math.ceil(listH.value / ROW_H) + OVER * 2)
function onListScroll(e: Event) { sTop.value = (e.target as HTMLElement).scrollTop }
watch([qSearch, qSort, qFilter], () => { sTop.value = 0 })
/* 当前单变化（翻张/跳转/队列首次加载）时滚进视野；已在视野内不动，避免列表抖动 */
watch(cur, async () => {
  await nextTick()
  const el = listEl.value
  listH.value = el?.clientHeight || 600
  const i = viewQueue.value.findIndex((q) => cur.value && q.no === cur.value.no)
  if (i < 0 || !el) return
  const top = i * ROW_H
  if (top < el.scrollTop || top + ROW_H > el.scrollTop + listH.value) {
    el.scrollTop = Math.max(0, top - listH.value / 2)
  }
}, { immediate: true })
function clearQFilter() { qSearch.value = ''; qFilter.value = 'all' }

/* ── 键盘：←/→ 翻张（输入框内不劫持） ── */
function onKey(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null
  if (t && ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) return
  if (e.key === 'ArrowLeft') navQueue(-1)
  else if (e.key === 'ArrowRight') navQueue(1)
}
onMounted(() => document.addEventListener('keydown', onKey))
onUnmounted(() => document.removeEventListener('keydown', onKey))
/* 翻张跟随筛选视图：筛出一批逐张处理时，「下一张」应是筛选结果里的下一张，
 * 跳到筛外的单据会把人带丢。当前单不在筛选结果里（清筛选后按全队列进来的）
 * 才退回全队列顺序。进度「第 X / 63 张」始终是全队列位次，口径不变。 */
const canPrev = computed(() => {
  const vi = viewQueue.value.findIndex((q) => cur.value && q.no === cur.value.no)
  return vi >= 0 ? vi > 0 : qi.value > 0
})
const canNext = computed(() => {
  const vi = viewQueue.value.findIndex((q) => cur.value && q.no === cur.value.no)
  return vi >= 0 ? vi < viewQueue.value.length - 1 : qi.value < queue.value.length - 1
})
function navQueue(delta: number) {
  const t = neighbor(delta)
  if (t) pickQueue(t)
}
/** 队列里当前单的邻张。给翻张按钮和「提交后自动下一张」共用——
 *  提交后当前单会移出队列，下一张必须在刷新前记住。 */
function neighbor(delta: number): QueueBill | null {
  const vi = viewQueue.value.findIndex((q) => cur.value && q.no === cur.value.no)
  const list = vi >= 0 ? viewQueue.value : queue.value
  return list[(vi >= 0 ? vi : qi.value) + delta] || null
}
function pickQueue(q: QueueBill) {
  if (q.run_id) router.push(`/review/report/${encodeURIComponent(q.run_id)}`)
  else router.push(`/review/bills/${encodeURIComponent(q.no)}`)
}

/* ── 闸门状态 ── */
/** 当前登录人的姓名（复核留痕的默认署名）。此前默认空、提交落「未署名」，
 * 而本页历史动作里又兜底写死「林嘉」——登录的是陈芳时，留痕和页面说的不是
 * 同一个人。署名必须跟着会话走，不许有第二个来源。 */
function sessName(): string {
  return session.current()?.user || ''
}
const reviewer = ref(sessName())
const note = ref('')
const reason = ref('')
const showReturn = ref(false)
const disagree = ref<string[]>([])
const submitting = ref(false)
const submitMsg = ref('')
const submitErr = ref('')
const toast = ref('')
let toastTimer = 0
function say(m: string) {
  toast.value = m
  clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => (toast.value = ''), 2400)
}
/** 该轮的人工结论（后端持久状态，2026-10-25 联动后取代会话级标记）。
 *  有值则闸门锁定、防重复落库——后端另有同 run 唯一留痕（409）双保险。 */
const curManual = computed<ManualReview | null>(() =>
  manuals.value.find((m) => m.run_id === runId.value) || manuals.value[0] || null)
const doneEntry = computed<{ d: 'agree' | 'return'; note: string } | undefined>(() => {
  const m = curManual.value
  return m ? { d: m.decision === 'return' ? 'return' : 'agree', note: m.return_reason || '' } : undefined
})
const doneLabel = (d: 'agree' | 'return', hasNote = false) =>
  d === 'agree' ? (hasNote ? '同意并备注' : '复核通过') : '退回报账人'

async function submit(decision: 'agree' | 'return') {
  submitErr.value = ''
  submitMsg.value = ''
  if (doneEntry.value) {
    submitErr.value = '本单已有复核留痕（后端已记录），不重复提交'
    return
  }
  // 先把文本取出来再动任何状态——上一版先清空 reason 再读它，
  // 退回理由在本页历史里永远是「—」。
  const reasonText = reason.value.trim()
  const noteText = note.value.trim()
  if (decision === 'return' && !reasonText) {
    submitErr.value = '退回必须给理由——理由是误报/漏报回流的起点'
    return
  }
  submitting.value = true
  try {
    // 意见走 agree_with_note 决策 + return_reason 文本字段（后端无独立备注字段，
    // 原版页面就是这么传的）。agree_with 字段是「明确认可的命中列表」，不许塞文字。
    const withNote = decision === 'agree' && noteText
    await api.submitManual(runId.value, {
      decision: withNote ? 'agree_with_note' : decision,
      reviewer: reviewer.value.trim() || sessName() || '未署名',
      return_reason: decision === 'return' ? reasonText
                   : withNote ? noteText : undefined,
      disagree_with: disagree.value,
    })
    // 提交成功后当前单会移出队列，下一张要在刷新前记住
    const nextTarget = neighbor(1)
    const no = report.value?.run.bill_no
    // 刷真状态：人工留痕 + 队列（manual=none 口径下，队列真少一张、已复核 +1）
    if (no) await loadManuals(no)
    await loadQueue()
    submitMsg.value = decision === 'agree' ? '已记录：复核通过' : '已记录：退回报账人'
    say(decision === 'agree' ? '已记录：复核通过' : '已退回，理由已记录')
    // 意见与不认可勾选**当场清空**——它们属于刚提交的那张单，不清会原样
    // 带进下一张（意见错挂、不认可错勾到下一张的同号规则上）。
    note.value = ''
    reason.value = ''
    disagree.value = []
    showReturn.value = false
    // 提交即「本单已处理」：通过/退回都自动下一张（600ms 让人看清结果）
    setTimeout(() => { if (nextTarget) pickQueue(nextTarget) }, 600)
  } catch (e) {
    submitErr.value = e instanceof Error ? e.message : String(e)
    // 409=这轮已在别处复核过：同步一次真状态，闸门据此锁定
    const no = report.value?.run.bill_no
    if (no) { void loadManuals(no); void loadQueue() }
  } finally {
    submitting.value = false
  }
}
function toggleDisagree(rid: string) {
  const i = disagree.value.indexOf(rid)
  i >= 0 ? disagree.value.splice(i, 1) : disagree.value.push(rid)
}

/* ── 规则合表（四态 + 未执行） ── */
type Filter = 'all' | 'hit' | 'pending' | 'passed' | 'na' | 'excluded'
const filter = ref<Filter>('all')
const openRule = ref<string | null>(null)

interface Row { id: string; outcome: string; name: string; msg: string; f?: Finding; ex?: { rule_id: string; rule_name: string; control_type: string; why: string } }
const rows = computed<Row[]>(() => {
  const d = report.value
  if (!d) return []
  const mk = (f: Finding, name: string): Row => ({ id: f.rule_id, outcome: f.outcome, name, msg: f.message, f })
  return [
    ...d.findings.hit.map((f) => mk(f, '')),
    ...d.findings.pending.map((f) => mk(f, '')),
    ...d.findings.passed.map((f) => mk(f, '')),
    ...d.findings.not_applicable.map((f) => mk(f, '')),
    ...d.excluded_rules.map((e) => ({ id: e.rule_id, outcome: 'excluded', name: e.rule_name, msg: '', ex: e })),
  ]
})
const report = ref<(ReviewReport & { _bill?: Record<string, any> }) | null>(null)

const OUT: Record<string, { label: string; cls: string }> = {
  hit: { label: '命中', cls: 'bad' },
  pending: { label: '待人工', cls: 'warn' },
  pass: { label: '通过', cls: 'ok' },
  not_applicable: { label: '不适用', cls: 'brand' },
  excluded: { label: '未执行', cls: 'brand' },
}
const FILTERS: [Filter, string][] = [['all', '全部'], ['hit', '命中'], ['pending', '待人工'], ['passed', '通过'], ['na', '不适用'], ['excluded', '未执行']]
function countOf(k: Filter): number {
  if (k === 'all') return rows.value.length
  if (k === 'excluded') return report.value?.excluded_rules.length ?? 0
  if (k === 'hit') return report.value?.findings.hit.length ?? 0
  if (k === 'pending') return report.value?.findings.pending.length ?? 0
  if (k === 'passed') return report.value?.findings.passed.length ?? 0
  return report.value?.findings.not_applicable.length ?? 0
}
/** 明细默认 10 条（命中/待人工排前），超出折叠展开/收起 */
const expanded = ref(false)
const PREVIEW_N = 10
const shownAll = computed(() => {
  if (filter.value === 'all') return rows.value
  const map: Record<Filter, (r: Row) => boolean> = {
    all: () => true,
    hit: (r) => r.outcome === 'hit',
    pending: (r) => r.outcome === 'pending',
    passed: (r) => r.outcome === 'pass',
    na: (r) => r.outcome === 'not_applicable',
    excluded: (r) => r.outcome === 'excluded',
  }
  return rows.value.filter(map[filter.value])
})
const shown = computed(() =>
  expanded.value || shownAll.value.length <= PREVIEW_N ? shownAll.value : shownAll.value.slice(0, PREVIEW_N))

function evidenceRows(f: Finding): [string, string][] {
  return Object.entries(f.evidence || {})
    .filter(([k]) => k !== 'not_applicable')
    .map(([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v)])
}

/* ── 附件大图 ── */
const lb = ref<{ name: string; url: string } | null>(null)
function openAtt(a: Record<string, any>) {
  lb.value = { name: a.file_name, url: `/api/attachments/${a.id}/file` }
}
watch(lb, (v) => {
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') lb.value = null }
  if (v) document.addEventListener('keydown', onKey)
  else document.removeEventListener('keydown', onKey)
})

/* ── 历次审核：人工留痕（manual_review，2026-10-25 起有读取接口）+ AI 轮次 ── */
const reviews = ref<Record<string, any>[]>([])
const manuals = ref<ManualReview[]>([])
async function loadManuals(no: string) {
  try { manuals.value = (await api.billManuals(no)).items } catch { manuals.value = [] }
}
/** 复核记录 = 人工留痕 + AI 审核轮次，按时间倒序合表。人工行带署名与理由——
 *  留痕看不见等于没留：此前只有会话内动作，刷新即失忆。 */
const histRows = computed(() => {
  const man = manuals.value.map((m) => ({
    key: 'm' + m.id, iso: m.decided_at || '',
    tm: (m.decided_at || '').slice(5, 16).replace('T', ' '),
    who: m.user_label,
    act: m.decision === 'return' ? '退回报账人' : doneLabel('agree', m.decision === 'agree_with_note'),
    note: m.return_reason || '—',
  }))
  const ai = reviews.value.map((rv: any, i: number) => ({
    key: 'r' + i, iso: String(rv.finished_at || rv.started_at || ''),
    tm: String(rv.finished_at || '').slice(5, 16).replace('T', ' '),
    who: '系统 · AI 预审',
    act: `${CONCL[rv.conclusion]?.label || rv.conclusion}（${rv.rule_executed ?? '—'} 条规则）`,
    note: `命中 ${rv.hit_count ?? 0} · 待人工 ${rv.pending_count ?? 0}`,
  }))
  return [...man, ...ai].sort((a, b) => (a.iso < b.iso ? 1 : a.iso > b.iso ? -1 : 0))
})
/** 换单即清账：意见、退回理由、不认可勾选、提交提示都**只属于当前单**——
 *  上一版 hist 是页面级累积，翻到第 30 张时复核记录里摆的是前面 29 张的动作。 */
watch(() => report.value?.run.bill_no, (no) => {
  note.value = ''
  reason.value = ''
  disagree.value = []
  submitMsg.value = ''
  submitErr.value = ''
  showReturn.value = false
  manuals.value = []
  if (!no) return
  api.billReviews(no).then((r) => { reviews.value = r.items }).catch(() => { reviews.value = [] })
  void loadManuals(no)
}, { immediate: true })

const CONCL: Record<string, { label: string; cls: string }> = {
  pass: { label: '通过', cls: 'ok' }, review: { label: '待复核', cls: 'warn' }, reject: { label: '不通过', cls: 'bad' },
}
</script>

<template>
  <div class="v2 qwrap">
    <!-- 队列栏（清单层，常驻左栏）：搜索/筛选/排序/虚拟滚动，点行直达 -->
    <aside class="card qdrawer" aria-label="待复核队列清单">
      <div class="qhead">
        <input class="input" v-model="qSearch" placeholder="搜索单号" aria-label="搜索单号">
        <div class="seg" aria-label="排序">
          <button :class="{ on: qSort === 'risk' }" @click="qSort = 'risk'">风险</button>
          <button :class="{ on: qSort === 'amt' }" @click="qSort = 'amt'">金额</button>
          <button :class="{ on: qSort === 'time' }" @click="qSort = 'time'">时间</button>
        </div>
        <div class="seg" aria-label="筛选">
          <button :class="{ on: qFilter === 'all' }" @click="qFilter = 'all'">全部</button>
          <button :class="{ on: qFilter === 'hit3' }" @click="qFilter = 'hit3'">命中≥3</button>
          <button :class="{ on: qFilter === 'big' }" title="金额 ≥ 1 万元" @click="qFilter = 'big'">大额</button>
          <button :class="{ on: qFilter === 'real' }" @click="qFilter = 'real'">仅真实</button>
        </div>
      </div>
      <div v-if="queueErr" class="qerr">队列取数失败：{{ queueErr }}</div>
      <div v-else-if="!viewQueue.length" class="qempty">
        没有符合筛选的单据
        <button class="btn ghost" @click="clearQFilter">清除筛选</button>
      </div>
      <div v-else class="qlist" ref="listEl" @scroll.passive="onListScroll">
        <div :style="{ height: vFirst * ROW_H + 'px' }"></div>
        <button v-for="q in viewQueue.slice(vFirst, vFirst + vRows)" :key="q.no" class="qrow"
                :class="{ cur: cur?.no === q.no }" :title="q.no" @click="pickQueue(q)">
          <span class="no">{{ q.no.replace(/^(FJPG-BX|FJHY-DEMO-)/, '') }}</span>
          <span v-if="q.is_demo" class="tag-demo">演示</span>
          <span class="hitn num" :class="q.concl">{{ q.hit }}</span>
        </button>
        <div :style="{ height: Math.max(0, viewQueue.length - vFirst - vRows) * ROW_H + 'px' }"></div>
      </div>
      <div class="qmeta">筛出 {{ viewQueue.length }} / {{ queue.length }} · 已人工复核 {{ doneCount }}</div>
    </aside>

    <div class="qmain">
    <!-- 队列状态条（详情层导航）：翻张/进度。
         队列 = 还没有人工结论的单（后端 manual=none 回流，2026-10-25 联动）：
         复核一张真少一张，持久在后端，刷新/换机器不回弹。AI 不通过与已复核数
         并排——三个数加起来才是完整口径，只摆一个看不出复核有没有用。 -->
    <div class="card qbar">
      <span class="pill warn num" :title="QUEUE_NOTE">待复核 {{ queue.length }} 张</span>
      <span class="qsub" style="font-size: 12px; color: var(--weak)">AI 不通过 {{ nReject }} · 已人工复核 {{ doneCount }}</span>
      <span class="qpos">{{ qi >= 0 ? `第 ${qi + 1} / ${queue.length} 张` : '当前单不在队列中' }}</span>
      <div class="grow"></div>
      <!-- 三个按钮成组：窄屏时整组换到第二行并右对齐，
           而不是各自被压成两行字（「刷新队列」被挤成「刷新队/列」那次就是这么来的） -->
      <div class="qacts">
        <button class="btn ghost" :disabled="!canPrev" @click="navQueue(-1)">上一张</button>
        <button class="btn ghost" :disabled="!canNext" @click="navQueue(1)">下一张</button>
        <button class="btn ghost" title="重新拉取队列（他人复核、重跑审核后同步）" @click="loadQueue()">刷新队列</button>
      </div>
    </div>

    <Async v-if="runId" :load="loadAll" :deps="runId" @loaded="(d) => (report = d)">
      <template #default="{ data }">
        <!-- 复核闸门（2026-10-25 UI 走查重构）：上半只读展示（纯文本，不套框），
             分割线下半操作区（等高输入 + 标准尺寸按钮右置）。
             两键 = 行内费用审核的两态（2026-10-24 定）：行业惯例与客户系统均为
             同意/退回两态，「终审否决」是信贷场景动作，费用报销无此业务出口；
             若将来合规需要第四决策，后端加 decision 值后在此恢复即可。 -->
        <div class="gate">
          <div class="bill-line">
            <b>{{ data.run.bill_no }}</b>
            <span v-if="bill?.is_demo" class="tag-demo">演示</span>
            <span class="pill" :class="CONCL[data.run.conclusion]?.cls || 'brand'">
              {{ CONCL[data.run.conclusion]?.label || data.run.conclusion }} · 待人工裁决
            </span>
            <span class="amt"><span class="num">{{ bill?.amount != null ? bill.amount.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '—' }}</span><small>元</small></span>
          </div>
          <div class="facts">
            <span><i>报账部门</i>{{ bill?.claim_dept || '—' }}</span>
            <span><i>核算主体</i>{{ bill?.org_name || '—' }}</span>
            <span><i>提单日期</i><span class="num">{{ (bill?.doc_date || '').slice(0, 10) || '—' }}</span></span>
          </div>
          <div class="ai-line">
            命中 <b style="color: var(--bad)">{{ data.coverage.hit }}</b> 条 ·
            待人工 {{ data.coverage.pending }} 项 ·
            执行 {{ data.coverage.executed }}/{{ data.coverage.candidates }} 条
            <span v-if="data.coverage.excluded" style="color: var(--weak); font-size: 12px">
              （{{ data.coverage.excluded }} 条未执行，原因见表内）
            </span>
          </div>
          <div v-if="doneEntry" class="act" style="gap: 10px">
            <span class="pill ok">已复核：{{ doneLabel(doneEntry.d, !!doneEntry.note) }}</span>
            <span style="font-size: 12px; color: var(--weak)">留痕已入库存档（见下方复核记录），同一轮不重复记录</span>
          </div>
          <div v-else class="act">
            <input class="input rev" v-model="reviewer" placeholder="复核人" aria-label="复核人">
            <input class="input note" v-model="note" placeholder="复核意见（选填，随通过记录）" aria-label="复核意见">
            <button class="btn pass" :disabled="submitting" @click="submit('agree')">{{ submitting ? '提交中…' : '复核通过' }}</button>
            <button class="btn reject" @click="showReturn = true">退回报账人</button>
          </div>
          <div v-if="submitErr" style="color: var(--bad); font-size: 13px; margin-top: 8px">{{ submitErr }}</div>
          <div v-if="submitMsg" style="color: var(--ok); font-size: 13px; margin-top: 8px">{{ submitMsg }}</div>
        </div>

        <!-- 单据信息：三卡各自独立（基础 KV / 费用明细 / 审批流程） -->
        <div class="card" style="margin-bottom: 16px">
          <div class="card-head">
            <h2>单据信息</h2>
            <div class="grow"></div>
            <router-link :to="`/review/bills/${data.run.bill_no}`" style="font-size: 13px">完整单据详情</router-link>
          </div>
          <div class="kvg">
            <div><span class="k">业务类型</span><span class="v">{{ bill?.biz_type || '—' }}</span></div>
            <div><span class="k">报账部门</span><span class="v">{{ bill?.claim_dept || '—' }}</span></div>
            <div><span class="k">职级</span><span class="v">{{ bill?.applicant_rank || '—' }}</span></div>
            <div><span class="k">出行人员</span><span class="v">{{ (bill?.travelers || []).map((t: any) => t.name).join('、') || (bill?.payee || '—') }}</span></div>
          </div>
        </div>

        <div class="card" style="margin-bottom: 16px">
          <div class="card-head"><h2>费用明细</h2></div>
          <!-- 列宽按内容算：费用项目最长「市内交通费补贴」约 91px、金额最长「1,374.80」约 65px、
               分摊部门最长「运营管理部」约 65px（12–13px 字号实测）。40/30/30 是把宽度均分，
               结果三列都宽得离谱、内容却挤在相邻的列边界上。现在费用项目自适应，另两列给 2–3 倍内容宽。 -->
          <table class="tbl money">
            <thead><tr><th>费用项目</th><th class="amt" style="width: 160px">金额（元）</th><th style="width: 200px">分摊部门</th></tr></thead>
            <tbody>
              <tr v-for="(it, i) in bill?.details || []" :key="i">
                <td>{{ it.expense_item }}</td>
                <td class="amt num">{{ it.amount != null ? it.amount.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '—' }}</td>
                <td style="color: var(--weak)">{{ it.alloc_dept || '—' }}</td>
              </tr>
              <tr style="background: var(--panel-2); font-weight: 600">
                <td>合计</td>
                <td class="amt num">{{ bill?.amount != null ? bill.amount.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '—' }}</td>
                <td></td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="card" style="margin-bottom: 16px">
          <div class="card-head"><h2>审批流程</h2></div>
          <table class="tbl">
            <thead><tr>
              <!-- 百分比均分 = 每列都空：审批环节 22%、处理人 26%、意见 32%。
                   实测「处理人」只占 58px（三个字），「意见」常有空值。改成：
                   环节/处理人/时间按内容定宽，意见吃剩余（它才是可变长的那列）。 -->
              <th style="width: 180px">审批环节</th><th style="width: 140px">处理人</th>
              <th>意见</th><th class="num" style="width: 170px">时间</th>
            </tr></thead>
            <tbody>
              <tr v-for="(a, i) in bill?.approvals || []" :key="i">
                <td>{{ a.node }}</td>
                <td>{{ a.approver }}</td>
                <td>{{ a.opinion || '—' }}</td>
                <td class="num" style="color: var(--weak)">{{ (a.approved_at || '').slice(5, 16).replace('T', ' ') }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 规则判定明细（四态+未执行合表，默认10条折叠，点命中/待人工行展开证据与制度依据） -->
        <div class="card" style="margin-bottom: 16px">
          <div class="card-head">
            <h2>规则判定明细</h2>
            <div class="grow"></div>
            <div class="chips">
              <button v-for="[k, t] in FILTERS" :key="k" class="chip" :class="{ on: filter === k }"
                      @click="filter = k; expanded = false">
                {{ t }} <span class="num">{{ countOf(k) }}</span>
              </button>
            </div>
          </div>
          <div style="overflow: auto">
            <table class="tbl hit-tbl">
              <thead>
                <tr><th style="width: 96px">规则</th><th style="width: 150px">制度依据</th><th class="c" style="width: 76px">结论</th><th>证据摘要</th><th class="c" style="width: 52px"></th></tr>
              </thead>
              <tbody>
                <template v-for="r in shown" :key="r.id">
                  <tr :style="{ cursor: r.f && (r.outcome === 'hit' || r.outcome === 'pending') ? 'pointer' : 'default' }"
                      @click="r.f && (r.outcome === 'hit' || r.outcome === 'pending') && (openRule = openRule === r.id ? null : r.id)">
                    <td class="num" style="text-align: left"><router-link :to="`/asset/rules/${r.id}`" @click.stop>{{ r.id }}</router-link></td>
                    <td class="t-cut" style="font-size: 12px; color: var(--weak)" :title="r.f?.clause_ref ? `${r.f.clause_ref.file} ${r.f.clause_ref.clause}` : ''">
                      <template v-if="r.f?.clause_ref">{{ r.f.clause_ref.file }} {{ r.f.clause_ref.clause }}</template>
                      <template v-else>—</template>
                    </td>
                    <td><span class="pill" :class="OUT[r.outcome]?.cls">{{ OUT[r.outcome]?.label }}</span></td>
                    <td class="t-cut" style="font-size: 12px; color: var(--muted)" :title="r.f ? r.f.message : ''">
                      <template v-if="r.f">{{ r.f.message }}</template>
                      <template v-else><MarkText :text="r.ex!.why" /></template>
                    </td>
                    <td>
                      <span v-if="r.f && (r.outcome === 'hit' || r.outcome === 'pending')" style="color: var(--weak); font-size: 12px">
                        {{ openRule === r.id ? '收起' : '展开' }}
                      </span>
                    </td>
                  </tr>
                  <tr v-if="r.f && openRule === r.id && (r.outcome === 'hit' || r.outcome === 'pending')">
                    <td :colspan="5" style="background: var(--panel-2); padding: 16px 20px 20px">
                      <div style="display: grid; grid-template-columns: 1.4fr 1fr; gap: 24px">
                        <div>
                          <div style="font-size: 12px; color: var(--weak); margin-bottom: 8px">判定证据</div>
                          <div style="display: grid; grid-template-columns: 130px 1fr; gap: 6px 12px; font-size: 13px">
                            <template v-for="([k, v], i) in evidenceRows(r.f)" :key="i">
                              <span style="color: var(--weak)">{{ k }}</span><span>{{ v }}</span>
                            </template>
                          </div>
                          <div v-if="r.f.matched_text" style="margin-top: 10px; font-size: 12.5px; color: var(--muted)">
                            命中原文：<code>{{ r.f.matched_text }}</code>
                          </div>
                          <div v-if="r.f.llm_involved && r.f.llm_rationale" style="margin-top: 10px; font-size: 12.5px">
                            <span class="pill brand" style="margin-right: 6px">模型辅助</span>{{ r.f.llm_rationale }}
                          </div>
                          <label v-if="r.outcome === 'hit'" style="display: block; margin-top: 12px; font-size: 12.5px">
                            <input type="checkbox" :checked="disagree.includes(r.id)" @change="toggleDisagree(r.id)" />
                            我不认可这条命中（提交后逐条回流误报统计）
                          </label>
                        </div>
                        <div>
                          <div style="font-size: 12px; color: var(--weak); margin-bottom: 8px">制度依据</div>
                          <div v-if="r.f.clause_ref" style="font-size: 12.5px; color: var(--muted); border: 1px solid var(--line); border-radius: 5px; padding: 10px 12px">
                            {{ r.f.clause_ref.file }} · {{ r.f.clause_ref.clause }}
                            <div style="margin-top: 6px">{{ r.f.clause_ref.text }}</div>
                            <div v-if="r.f.clause_ref.confidence" style="margin-top: 6px; font-size: 11px; color: var(--weak)">来源可信度：{{ r.f.clause_ref.confidence }}</div>
                          </div>
                          <div v-else style="font-size: 12.5px; color: var(--weak)">软规则：由大模型辅助研判，结论仅供人工参考。</div>
                        </div>
                      </div>
                    </td>
                  </tr>
                </template>
                <tr v-if="!shown.length" class="empty-row"><td :colspan="5">没有该状态的规则</td></tr>
                <tr v-if="shownAll.length > PREVIEW_N">
                  <td :colspan="5" style="text-align: center; color: var(--brand); cursor: pointer; font-size: 13px" @click="expanded = !expanded">
                    {{ expanded ? '收起' : `展开剩余 ${shownAll.length - PREVIEW_N} 条` }}（共 {{ shownAll.length }} 条）
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="disagree.length" style="padding: 10px 20px; font-size: 12.5px; color: var(--warn)">
            已勾 {{ disagree.length }} 条不认可 —— 提交后逐条回流误报统计（feedback_log），
            「偏差与回流」页按规则可见
          </div>
        </div>

        <!-- 单据附件（1:1 缩略图，点开大图） -->
        <div class="card" style="margin-bottom: 16px">
          <div class="card-head">
            <h2>单据附件</h2>
            <div class="grow"></div>
            <span style="font-size: 12px; color: var(--weak)">{{ bill?.attach_pages != null ? `申报 ${bill.attach_pages} 张 · ` : '' }}实际 {{ (bill?.attachments || []).length }} 张</span>
          </div>
          <div class="atts">
            <button v-for="a in bill?.attachments || []" :key="a.id" class="att" @click="a.has_file && openAtt(a)">
              <div class="thumb">
                <img v-if="a.has_file" :src="`/api/attachments/${a.id}/file`" :alt="a.file_name" loading="lazy">
                <svg v-else width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M3 17l5-4 4 3 4-4 5 5"/></svg>
              </div>
              <div class="meta">
                <span class="n">{{ a.file_name }}</span>
                <span class="t">{{ a.attach_type || '未判类型' }}</span>
              </div>
            </button>
            <div v-if="!(bill?.attachments || []).length" style="grid-column: 1 / -1; padding: 24px; text-align: center; color: var(--weak); font-size: 13px">
              本单没有附件记录
            </div>
          </div>
        </div>

        <!-- 复核记录（demo 契约：时间/操作人/动作/意见）。
             人工留痕（后端 manual_review，2026-10-25 起有读取接口）与 AI 审核
             轮次合并按时间倒序——留痕看得见才算留了 -->
        <div class="card">
          <div class="card-head">
            <h2>复核记录</h2>
            <div class="grow"></div>
          </div>
          <table class="tbl">
            <thead><tr><th class="num" style="width: 120px">时间</th><th style="width: 110px">操作人</th><th style="width: 170px">动作</th><th>意见</th></tr></thead>
            <tbody>
              <tr v-for="h in histRows" :key="h.key">
                <td class="num" style="color: var(--weak); white-space: nowrap">{{ h.tm }}</td>
                <td>{{ h.who }}</td>
                <td>{{ h.act }}</td>
                <td style="color: var(--muted)">{{ h.note }}</td>
              </tr>
              <tr v-if="!histRows.length" class="empty-row"><td :colspan="4">本单尚无复核记录</td></tr>
            </tbody>
          </table>
        </div>
      </template>
    </Async>

    <div v-if="!runId" class="card">
      <div style="padding: 40px; text-align: center; color: var(--weak)">
        {{ queueErr ? `队列取数失败：${queueErr}` : '没有待复核的单据' }}
      </div>
    </div>
    </div><!-- /qmain -->

    <!-- 退回理由弹窗 -->
    <div class="mask" :class="{ show: showReturn }" @click.self="showReturn = false">
      <div class="modal" role="dialog" aria-modal="true" aria-label="退回报账人">
        <h3>退回报账人</h3>
        <div class="sub">{{ report?.run.bill_no }}</div>
        <textarea v-model="reason" placeholder="退回原因（必填）：缺少的材料或需修正的内容，将通知报账人修改后重新提交"></textarea>
        <div class="hint">填写原因后方可提交；原因将进入偏差统计</div>
        <div class="ops">
          <button class="btn ghost" @click="showReturn = false">取消</button>
          <button class="btn primary" :disabled="!reason.trim() || submitting" @click="submit('return')">{{ submitting ? '提交中…' : '确认退回' }}</button>
        </div>
      </div>
    </div>

    <!-- 附件大图 -->
    <div class="lb" :class="{ show: lb }" @click.self="lb = null">
      <div class="lb-card" v-if="lb">
        <div class="lb-img"><img :src="lb.url" :alt="lb.name" style="max-width: 100%; max-height: 100%; object-fit: contain"></div>
        <div class="lb-bar">
          <span class="n">{{ lb.name }}</span>
          <button class="btn ghost" @click="lb = null">关闭</button>
        </div>
      </div>
    </div>

    <div class="toast" :class="{ show: toast }" role="status" aria-live="polite">{{ toast }}</div>
  </div>
</template>

<style scoped>
/* 队列状态条右侧的三个按钮成组：窄屏换到第二行时整组贴右，
   而不是被 flex 压成「刷新队/列」两行字。 */
/* 三列金额表不撑满卡片：撑满时「费用项目」会独占三四百像素、内容只有 90px，
   中间一大片空。定宽靠左，空白留在表格外面，看着才像一张表。 */
.money { width: auto; }
.money th:first-child { width: 260px; }

/* 金额列右端贴着下一列，数字看着像挤在边界上：两侧各留 32px 气口。 */
.money th:nth-child(2), .money td:nth-child(2) { padding-right: 32px; }
.money th:nth-child(3), .money td:nth-child(3) { padding-left: 32px; }

.qacts { display: flex; gap: 8px; margin-left: auto; flex: none; }
.qacts .btn { flex: none; white-space: nowrap; }
/* 命中规则表：结论与操作两列居中，规则/依据/证据摘要左对齐 */
.hit-tbl th:nth-child(3), .hit-tbl td:nth-child(3),
.hit-tbl th:nth-child(5), .hit-tbl td:nth-child(5) { text-align: center; }
</style>
