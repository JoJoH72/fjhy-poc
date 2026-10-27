<script setup lang="ts">
/**
 * 审核报告 + 人工复核闸门（2026-10-27 AntD 换装版）。
 *
 * 结构：常驻队列栏（清单层：搜索/筛选/排序/虚拟滚动）+ 队列状态条（翻张/进度）
 * → 复核闸门（事实行 + 按钮行）→ 单据信息（明细/审批）→ 规则判定明细
 * （四态合表 + 证据展开 + 制度依据）→ 附件（点开大图）→ 历次审核。
 * 键盘：←/→ 翻张；提交（通过/退回）后自动下一张。
 *
 * 三个诚实点必须保住（原版页面的立页之本，改版不许丢）：
 * 1. 覆盖面数字并列：候选 / 执行 / 命中 / 待人工 / 未执行——
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
import { message } from 'ant-design-vue'
import Async from '../components/Async.vue'
import * as session from '../data/session'
import { api, type Finding, type ReviewReport, type Bill, type ManualReview } from '../api'

const route = useRoute()
const router = useRouter()
// runId 可空：/review/report 从菜单直接进（demo 契约的四菜单之一）——
// 空时自动跳队列首张；没有待复核单据时给空态，不装默认数据
const runId = computed(() => (route.params.runId ? String(route.params.runId) : ''))

/* ── 复核队列：不通过 + 待复核 两种结论合并 ──
 * 2026-10-25 由横条胶片改为「状态条 + 队列抽屉」（清单-详情分层）：
 * 抽屉 = 清单层（搜索/筛选/排序/虚拟滚动/已处理打勾），本页 = 详情层（键盘翻张、
 * 提交后自动下一张）。常驻左栏（2026-10-25 用户定稿：不做开关，一直留着）。
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
    // 免参进入：优先回到上次处理的那张（还在队列=没被人审掉），否则首张。
    // 上次的单已不在队列 → 回落首张 + 提示（不打断，一句话说清去了哪）。
    if (!runId.value && queue.value.length) {
      let last: string | null = null
      try { last = localStorage.getItem(lastRunKey()) } catch { last = null }
      const lastQ = last ? queue.value.find((q) => q.run_id === last) : null
      if (lastQ) {
        pickQueue(lastQ)
        message.info(`已回到上次处理到的一张（${lastQ.no.replace(/^(FJPG-BX|FJHY-DEMO-)/, '')}）`)
      } else {
        if (last) message.info('上次处理到的那张已有复核结论（或被他人审掉），从队首继续')
        pickQueue(queue.value[0])
      }
    }
  } catch (e) {
    queueErr.value = e instanceof Error ? e.message : String(e)
  }
}
loadQueue()

/* ── 报告 + 单据详情：并行取（P0-6 提速）──
 * 旧实现串行 await（review 完才发 bill），review(213ms)+bill(464ms) 被串成
 * ~900ms 链（实测翻张 716~913ms）。bill 的单号依赖其实不成立：队列栏
 * （cur.no）早就知道这张单的单号——用它立刻并行发 bill，总耗时 = 最慢一路。
 * 队列外直达 URL（无 cur）时退化为先取报告再取单据，行为与旧版一致。
 * 另配邻张预取（见 watch(runId)），翻到第 N 张时后台先取 N+1 的报告。 */
const bill = ref<Record<string, any> | null>(null)
/** 防错挂令牌：并行取数下快速翻张时，上一张的 bill 响应可能后到——
 *  只许当前 run 的响应写 bill.value，晚到的旧响应丢弃。 */
let billToken = 0
async function loadAll(): Promise<ReviewReport & { _bill?: Record<string, any> }> {
  const token = ++billToken
  const noFromQueue = cur.value?.no
  const repP = api.review(runId.value)
  const billP = noFromQueue
    ? api.bill(noFromQueue)
    : repP.then((rep) => api.bill(rep.run.bill_no))
  billP.then((b) => { if (token === billToken) bill.value = b as Record<string, any> })
    .catch(() => { if (token === billToken) bill.value = null }) // 单据详情挂了不拦报告本身
  const rep = await repP
  return rep
}

const cur = computed<QueueBill | null>(() => queue.value.find((q) => q.run_id === runId.value) || null)
const qi = computed(() => (cur.value ? queue.value.findIndex((q) => q.no === cur.value!.no) : -1))
/** 队列构成（状态条分列显示）：review = AI 判不了待人工，reject = AI 命中不通过 */
const nReject = computed(() => queue.value.filter((q) => q.concl === 'reject').length)

/* ── 队列栏：搜索 / 筛选 / 排序 / 虚拟滚动 ──
 * 筛选与排序选择持久化（P1-7 顺带 2.11-A）：审核员日常用「仅真实」，刷新
 * 回到全量（风险排序下前 18 张全是演示单）是日常噪音。localStorage 按账号记。 */
const QPREF_KEY = 'fjhy:qpref:' + (session.current()?.account || '')
function readQPref(): { sort?: QSort; filter?: QFilter } {
  try { return JSON.parse(localStorage.getItem(QPREF_KEY) || '{}') } catch { return {} }
}
const qSearch = ref('')
type QSort = 'risk' | 'amt' | 'time'
const qSort = ref<QSort>(readQPref().sort || 'risk')
type QFilter = 'all' | 'hit3' | 'big' | 'real' | 'demo'
const qFilter = ref<QFilter>(readQPref().filter || 'all')
watch([qSort, qFilter], () => {
  try { localStorage.setItem(QPREF_KEY, JSON.stringify({ sort: qSort.value, filter: qFilter.value })) } catch { /* 平台可写性 */ }
})

const viewQueue = computed(() => {
  let a = queue.value
  if (qFilter.value === 'hit3') a = a.filter((q) => q.hit >= 3)
  else if (qFilter.value === 'big') a = a.filter((q) => q.amt >= (bigAmt.value ?? Infinity))
  else if (qFilter.value === 'real') a = a.filter((q) => !q.is_demo)
  else if (qFilter.value === 'demo') a = a.filter((q) => q.is_demo)
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

/* ── 键盘流（P1-1）：←/→ 翻张；A 通过；X 退回（弹理由框）；E 展开证据；? 帮助 ──
 * 全部输入类控件内不劫持（理由框正在打字时按 X 必须是打出字母）。
 * A 是最高频动作（一天几十张全靠它）；X 不能一键退回——理由是回流起点，
 * 所以 X 只弹理由框。E 展开第一条 hit/pending 的证据。? 帮助浮层：
 * 没有帮助浮层的快捷键等于不存在。 */
const showHelp = ref(false)
function onKey(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null
  if (t && ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) return
  if (e.key === 'ArrowLeft') { navQueue(-1); return }
  if (e.key === 'ArrowRight') { navQueue(1); return }
  if (e.key === '?' || (e.key === '/' && e.shiftKey)) { showHelp.value = !showHelp.value; return }
  if (e.altKey || e.ctrlKey || e.metaKey) return
  const k = e.key.toLowerCase()
  if (k === 'a') { if (!doneEntry.value && !submitting.value) submit('agree'); return }
  if (k === 'x') { if (!doneEntry.value) showReturn.value = true; return }
  if (k === 'e') { toggleFirstEvidence(); return }
}
/** E 键：展开/收起当前单第一条 hit/pending 规则的证据（表格第一展开行） */
function toggleFirstEvidence() {
  const first = rows.value.find((r) => r.outcome === 'hit') || rows.value.find((r) => r.outcome === 'pending')
  if (first) openRule.value = openRule.value === first.id ? null : first.id
}
onMounted(() => document.addEventListener('keydown', onKey))
onUnmounted(() => document.removeEventListener('keydown', onKey))
/* 翻张跟随筛选视图：筛出一批逐张处理时，「下一张」应是筛选结果里的下一张，
 * 跳到筛外的单据会把人带丢。当前单不在筛选结果里（清筛选后按全队列进来的）
 * 才退回全队列顺序。进度「第 X / N 张」始终是全队列位次，口径不变。 */
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

/* ── 邻张预取（P0-6 提速后半）：翻到第 N 张时后台先取 N+1 的报告 ──
 * 真正翻过去时浏览器直接命中 HTTP 缓存外的内存 Promise，渲染只需本帧。
 * 只预取报告（bill 详情翻张时也会并行拉，量级小）；失败静默——预取是
 * 优化不是数据源，失败留给正式加载去报错。 */
const prefetched = new Map<string, Promise<unknown>>()
watch(runId, () => {
  const nxt = neighbor(1)
  if (nxt?.run_id && !prefetched.has(nxt.run_id)) {
    prefetched.set(nxt.run_id, api.review(nxt.run_id).catch(() => undefined))
    if (prefetched.size > 60) prefetched.clear() // 队列会动态变化，旧预取不值钱
  }
}, { immediate: true })

/* ── 闸门状态 ── */
/** 当前登录人的姓名（复核留痕的默认署名）。此前默认空、提交落「未署名」，
 *  而本页历史动作里又兜底写死「林嘉」——登录的是陈芳时，留痕和页面说的不是
 *  同一个人。署名必须跟着会话走，不许有第二个来源。 */
function sessName(): string {
  return session.current()?.user || ''
}
const reviewer = ref(sessName())
const note = ref('')
const reason = ref('')
const showReturn = ref(false)
const disagree = ref<string[]>([])
const submitting = ref(false)
const submitErr = ref('')
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

/* ── 大额阈值（P0-12：数字不写死）── 取后端 /api/review/config；取不到时
 *  队列「大额」筛选禁用而不是拿旧值硬判——口径必须与后端一致才有意义。 */
const bigAmt = ref<number | null>(null)
/* 退回原因分类（P1-4）：闭集合由后端下发（review/config），不写死前端。
 *  分类随留痕落库，偏差页按原因出分布。 */
const returnCats = ref<string[]>([])
api.reviewConfig().then((c) => {
  bigAmt.value = c.big_amount_threshold
  returnCats.value = c.return_categories || []
}).catch(() => { /* 筛选器按禁用处理 */ })
const returnCat = ref<string | undefined>(undefined)

/* ── 提交后回看条（P0-9）＋ 最近处理（P1-8）＋ 断点恢复 ── */
const doneBanner = ref<{ runId: string; no: string; text: string } | null>(null)
/** 本会话最近处理的单（runId 直达报告页，通道已实测可用）。sessionStorage：
 *  只服务「刚提交想回看」，跨会话无意义；20 条封顶防膨胀。 */
const RECENT_KEY = 'fjhy:recent-runs'
interface RecentRun { runId: string; no: string }
const recent = ref<RecentRun[]>(readRecent())
function readRecent(): RecentRun[] {
  try { return JSON.parse(sessionStorage.getItem(RECENT_KEY) || '[]') } catch { return [] }
}
function pushRecent(runId: string, no: string) {
  recent.value = [{ runId, no }, ...recent.value.filter((r) => r.runId !== runId)].slice(0, 20)
  try { sessionStorage.setItem(RECENT_KEY, JSON.stringify(recent.value)) } catch { /* 隐私模式等 */ }
}
/** 断点恢复（P1-8）：免参进入不再总是首张——上次处理的那张还在队列就回到它，
 *  已被别人审掉则回落首张并提示一句。key 按账号分（不同审核岗互不串）。 */
function lastRunKey(): string {
  return 'fjhy:last-run:' + (session.current()?.account || '')
}
function rememberLast(runId: string) {
  try { localStorage.setItem(lastRunKey(), runId) } catch { /* 可写性交给平台 */ }
}
async function submit(decision: 'agree' | 'return') {
  submitErr.value = ''
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
      return_category: decision === 'return' ? returnCat.value : undefined,
      disagree_with: disagree.value,
      verified: verified.value,
    })
    // 提交成功后当前单会移出队列，下一张要在刷新前记住
    const nextTarget = neighbor(1)
    const no = report.value?.run.bill_no
    const doneRunId = runId.value
    // 刷真状态：人工留痕 + 队列（manual=none 口径下，队列真少一张、已复核 +1）
    if (no) await loadManuals(no)
    await loadQueue()
    // 提交后回看条（P0-9）：队列口径下被提交的单从队列栏消失，想检查
    // 「刚才那张是不是点错了」没有入口。用 alert 条代替 600ms 即逝的 toast，
    // 8 秒内可点「查看刚才的单」直达（已复核单的报告页闸门锁定+留痕可见）。
    doneBanner.value = {
      runId: doneRunId, no: no || '',
      text: decision === 'agree' ? '已记录：复核通过' : '已退回，理由已记录',
    }
    setTimeout(() => { if (doneBanner.value?.runId === doneRunId) doneBanner.value = null }, 8000)
    // 最近处理（P1-8）：会话内最近 20 条，队列栏底部折叠直达
    pushRecent(doneRunId, no || '')
    // 意见与不认可勾选**当场清空**——它们属于刚提交的那张单，不清会原样
    // 带进下一张（意见错挂、不认可错勾到下一张的同号规则上）。
    note.value = ''
    reason.value = ''
    disagree.value = []
    verified.value = []
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

/* ── 分项核实（P1-5）：「已人工核实」勾选，与「不认可」对偶 ──
 * AI 判不了的项，人核过后必须留分项痕迹——否则审计时说不清单子里哪几项
 * 是人核过的。提交时随 verified: [...] 传后端，并入 agree_with 落库。 */
const verified = ref<string[]>([])
function toggleVerified(rid: string) {
  const i = verified.value.indexOf(rid)
  i >= 0 ? verified.value.splice(i, 1) : verified.value.push(rid)
}
/** 同一规则既「核实」又「不认可」是矛盾勾选：勾其一时把另一个清掉 */
function toggleDisagreeEx(rid: string) {
  const vi = verified.value.indexOf(rid)
  if (vi >= 0) verified.value.splice(vi, 1)
  toggleDisagree(rid)
}
function toggleVerifiedEx(rid: string) {
  const di = disagree.value.indexOf(rid)
  if (di >= 0) disagree.value.splice(di, 1)
  toggleVerified(rid)
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

const OUT: Record<string, { text: string; color: string }> = {
  hit: { text: '命中', color: 'error' },
  pending: { text: '待人工', color: 'warning' },
  pass: { text: '通过', color: 'success' },
  not_applicable: { text: '不适用', color: 'default' },
  excluded: { text: '未执行', color: 'default' },
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
/** 明细默认 10 条（命中/待人工排前），超出分页 */
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

function evidenceRows(f: Finding): [string, string][] {
  return Object.entries(f.evidence || {})
    .filter(([k]) => k !== 'not_applicable')
    .map(([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v)])
}

/* ── 附件大图（P1-3：滚轮缩放 + 拖拽平移 + 旋转 + ←/→ 翻本单附件）──
 * 审「发票抬头」要读 14px 的票面字，静态 img 拖不动放大镜是硬伤。
 * CSS transform 实现，不引库。大图层激活时 ←/→ 归附件翻张（Esc 关层归还
 * 队列键盘）——与队列翻张不冲突：层开着才劫持。 */
const lb = ref<{ name: string; url: string; idx: number } | null>(null)
const lbList = ref<Record<string, any>[]>([])
const lbScale = ref(1)
const lbRot = ref(0)
const lbPos = ref({ x: 0, y: 0 })
let lbDrag: { x: number; y: number; ox: number; oy: number } | null = null
function openAtt(a: Record<string, any>) {
  const list = ((bill.value?.attachments || []) as Record<string, any>[]).filter((x) => x.has_file)
  lbList.value = list
  const idx = Math.max(0, list.findIndex((x) => x.id === a.id))
  showLb(idx)
}
function showLb(idx: number) {
  const a = lbList.value[idx]
  if (!a) return
  lb.value = { name: a.file_name, url: `/api/attachments/${a.id}/file`, idx }
  lbScale.value = 1; lbRot.value = 0; lbPos.value = { x: 0, y: 0 }
}
function lbNav(d: number) {
  if (!lb.value) return
  const n = lbList.value.length
  if (n > 1) showLb((lb.value.idx + d + n) % n)
}
function lbZoom(d: number) {
  lbScale.value = Math.min(4, Math.max(0.5, lbScale.value * d))
}
function lbWheel(e: WheelEvent) {
  e.preventDefault()
  lbZoom(e.deltaY < 0 ? 1.2 : 1 / 1.2)
}
function lbDown(e: MouseEvent) {
  lbDrag = { x: e.clientX, y: e.clientY, ox: lbPos.value.x, oy: lbPos.value.y }
  e.preventDefault()
}
function lbMove(e: MouseEvent) {
  if (!lbDrag) return
  lbPos.value = { x: lbDrag.ox + e.clientX - lbDrag.x, y: lbDrag.oy + e.clientY - lbDrag.y }
}
function lbUp() { lbDrag = null }
function closeLb() { lb.value = null }
watch(lb, (v) => {
  const onKey = (e: KeyboardEvent) => {
    if (!lb.value) return
    if (e.key === 'Escape') closeLb()
    else if (e.key === 'ArrowLeft') lbNav(-1)  // 大图层内 ←/→ 翻附件（优先于队列翻张）
    else if (e.key === 'ArrowRight') lbNav(1)
  }
  if (v) document.addEventListener('keydown', onKey, true) // capture：先于队列 onKey
  else document.removeEventListener('keydown', onKey, true)
})

/* ── 历次审核：人工留痕（manual_review）+ AI 轮次 ── */
const reviews = ref<Record<string, any>[]>([])
const manuals = ref<ManualReview[]>([])
/** 同类单据（P1-9）：null=未加载/失败（折叠卡隐藏，不占版面） */
const peers = ref<{ items: any[]; window_days: number } | null>(null)
/** 重报检测（P1-10）：本单历史上有退回记录 → 显示黄色提示条 */
const returnedBefore = computed<{ at: string; reason: string } | null>(() => {
  const ret = [...manuals.value].filter((m) => m.decision === 'return')
    .sort((a, b) => (a.decided_at < b.decided_at ? 1 : -1))[0]
  return ret ? { at: (ret.decided_at || '').slice(0, 10), reason: ret.return_reason || '' } : null
})
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
    note: [m.return_reason,
           // 分项核实清单（P1-5）：核实过的规则在复核记录里留痕——
           // 「AI 判不了、人核过」从此说得清是哪几项
           (m.agree_with || []).length ? `人工核实 ${m.agree_with!.length} 项：${m.agree_with!.join('、')}` : '',
          ].filter(Boolean).join('；') || '—',
  }))
  const ai = reviews.value.map((rv: any, i: number) => ({
    key: 'r' + i, iso: String(rv.finished_at || rv.started_at || ''),
    tm: String(rv.finished_at || '').slice(5, 16).replace('T', ' '),
    who: '系统 · AI 预审',
    act: `${CONCL[rv.conclusion]?.text || rv.conclusion}（${rv.rule_executed ?? '—'} 条规则）`,
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
  verified.value = []
  returnCat.value = undefined
  submitErr.value = ''
  showReturn.value = false
  manuals.value = []
  // 断点记忆（P1-8）：当前正在看的一张存 localStorage，免参进入回到它
  if (runId.value) rememberLast(runId.value)
  if (!no) return
  if (!no) return
  api.billReviews(no).then((r) => { reviews.value = r.items }).catch(() => { reviews.value = [] })
  void loadManuals(no)
  // 同类单据（P1-9）：同主体近 7 日同金额/收款方/业务类型——「是不是重报」高频动作
  peers.value = null
  api.billPeers(no).then((p) => { peers.value = p }).catch(() => { peers.value = null })
}, { immediate: true })

const CONCL: Record<string, { text: string; color: string }> = {
  pass: { text: '通过', color: 'success' }, review: { text: '待复核', color: 'warning' }, reject: { text: '不通过', color: 'error' },
}
/** 人工结论标签（同类单据卡用，与 MyWork 同口径） */
const DECISION: Record<string, { text: string; color: string }> = {
  agree: { text: '复核通过', color: 'success' },
  agree_with_note: { text: '通过并备注', color: 'success' },
  return: { text: '退回', color: 'error' },
}

const fmtAmt = (v: number | null | undefined) =>
  v != null ? Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '—'

const ruleCols = [
  { title: '规则', dataIndex: 'id', width: 100 },
  { title: '制度依据', key: 'clause', width: 170 },
  { title: '结论', key: 'outcome', width: 86, align: 'center' as const },
  { title: '证据摘要', key: 'msg' },
  { title: '', key: 'exp', width: 56, align: 'center' as const },
]
const histCols = [
  { title: '时间', dataIndex: 'tm', width: 120 },
  { title: '操作人', dataIndex: 'who', width: 130 },
  { title: '动作', dataIndex: 'act', width: 190 },
  { title: '意见', dataIndex: 'note' },
]

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
  <div class="qwrap">
    <!-- 队列栏（清单层，常驻左栏）：搜索/筛选/排序/虚拟滚动，点行直达 -->
    <a-card size="small" class="qdrawer" title="待复核队列">
      <template #extra>
        <span class="dim">已人工复核 {{ doneCount }}</span>
      </template>
      <div class="qhead">
        <a-input-search v-model:value="qSearch" placeholder="搜索单号" allow-clear size="small" />
        <div class="qrow2">
          <a-radio-group v-model:value="qSort" size="small" button-style="solid">
            <a-radio-button value="risk">风险</a-radio-button>
            <a-radio-button value="amt">金额</a-radio-button>
            <a-radio-button value="time">时间</a-radio-button>
          </a-radio-group>
          <a-select v-model:value="qFilter" size="small" style="width: 104px"
            :options="[
              { value: 'all', label: '全部' },
              { value: 'hit3', label: '命中≥3' },
              { value: 'big', label: bigAmt != null ? `大额≥${bigAmt / 10000}万` : '大额', disabled: bigAmt == null },
              { value: 'real', label: '仅真实' },
              { value: 'demo', label: '仅演示' },
            ]" />
        </div>
      </div>
      <a-alert v-if="queueErr" type="error" show-icon :message="`队列取数失败：${queueErr}`" style="margin: 8px 0" />
      <div v-else-if="!viewQueue.length" class="qempty">
        <a-empty :image-style="{ height: '40px' }" description="没有符合筛选的单据" />
        <a-button size="small" @click="clearQFilter">清除筛选</a-button>
      </div>
      <div v-else class="qlist" ref="listEl" @scroll.passive="onListScroll">
        <div :style="{ height: vFirst * ROW_H + 'px' }"></div>
        <button v-for="q in viewQueue.slice(vFirst, vFirst + vRows)" :key="q.no" class="qrow"
                :class="{ cur: cur?.no === q.no, big: bigAmt != null && q.amt >= bigAmt }"
                :title="`${q.no}${bigAmt != null && q.amt >= bigAmt ? ' · 大额 ' + fmtAmt(q.amt) + ' 元' : ''}`"
                @click="pickQueue(q)">
          <span class="no">{{ q.no.replace(/^(FJPG-BX|FJHY-DEMO-)/, '') }}</span>
          <!-- 金额列（P1-7）：按风险排序时 250 万演示单与 847 元真实单长得一样。
               tabular-nums 右对齐，大额行首 3px 橙条 + 阈值由后端下发。 -->
          <span class="amtn" v-if="bigAmt != null && q.amt >= bigAmt">{{ fmtAmt(q.amt) }}</span>
          <a-tag v-if="bigAmt != null && q.amt >= bigAmt" color="orange" class="demo">大额</a-tag>
          <a-tag v-if="q.is_demo" color="default" class="demo">演示</a-tag>
          <span class="hitn" :class="q.concl">{{ q.hit }}</span>
        </button>
        <div :style="{ height: Math.max(0, viewQueue.length - vFirst - vRows) * ROW_H + 'px' }"></div>
      </div>
      <div class="qmeta">筛出 {{ viewQueue.length }} / {{ queue.length }} · 已人工复核 {{ doneCount }}</div>
      <!-- 最近处理（P1-8）：本会话已提交的单，提交后即从上面队列消失——
           这里是「刚才那张点错了想回看」的最短路径 -->
      <div v-if="recent.length" class="qrecent">
        <div class="qrt">最近处理（{{ recent.length }}）</div>
        <button v-for="r in recent.slice(0, 5)" :key="r.runId" class="qrow rrow"
                :title="r.no" @click="router.push(`/review/report/${encodeURIComponent(r.runId)}`)">
          <span class="no">{{ r.no.replace(/^(FJPG-BX|FJHY-DEMO-)/, '') }}</span>
          <span class="dim">已提交</span>
        </button>
      </div>
    </a-card>

    <div class="qmain">
    <!-- 队列状态条（详情层导航）：翻张/进度。
         队列 = 还没有人工结论的单（后端 manual=none 回流，2026-10-25 联动）：
         复核一张真少一张，持久在后端，刷新/换机器不回弹。AI 不通过与已复核数
         并排——三个数加起来才是完整口径，只摆一个看不出复核有没有用。 -->
    <a-card size="small" class="qbar">
      <div class="qbar-in">
        <a-tag color="warning" class="qtag">待复核 {{ queue.length }} 张</a-tag>
        <span class="dim">AI 不通过 {{ nReject }} · 已人工复核 {{ doneCount }}</span>
        <span class="qpos">{{ qi >= 0 ? `第 ${qi + 1} / ${queue.length} 张` : '当前单不在队列中' }}</span>
        <div class="grow"></div>
        <a-button size="small" :disabled="!canPrev" @click="navQueue(-1)">上一张</a-button>
        <a-button size="small" :disabled="!canNext" @click="navQueue(1)">下一张</a-button>
        <a-button size="small" title="重新拉取队列（他人复核、重跑审核后同步）" @click="loadQueue()">刷新队列</a-button>
        <a-button size="small" title="键盘快捷键" @click="showHelp = true">快捷键 ?</a-button>
      </div>
      <!-- 提交后回看条（P0-9）：替代即逝 toast——队列栏里被提交的单已消失，
           8 秒内可一键回看刚才那张的报告（闸门锁定+留痕可见）。 -->
      <a-alert v-if="doneBanner" type="success" show-icon closable style="margin-top: 8px"
        :message="doneBanner.text"
        :description="undefined">
        <template #action>
          <a-button size="small" @click="router.push(`/review/report/${encodeURIComponent(doneBanner.runId)}`)">
            查看刚才的单
          </a-button>
        </template>
      </a-alert>
    </a-card>

    <Async v-if="runId" :load="loadAll" :deps="runId" @loaded="(d) => (report = d)">
      <template #default="{ data }">
        <!-- 重报提示（P1-10）：本单历史上有退回记录 → 重报单。数据来自
             billManuals（按单查全历史），纯前端判断，无新接口。 -->
        <a-alert v-if="returnedBefore" type="warning" show-icon style="margin-bottom: 12px"
          :message="`本单曾于 ${returnedBefore.at} 被退回${returnedBefore.reason ? '（' + returnedBefore.reason + '）' : ''}，本次为重报`" />

        <!-- 复核闸门：上半只读事实（单号/结论/金额/部门/覆盖面），下半操作区。
             两键 = 行内费用审核的两态（2026-10-24 定）：行业惯例与客户系统均为
             同意/退回两态，「终审否决」是信贷场景动作，费用报销无此业务出口；
             若将来合规需要第四决策，后端加 decision 值后在此恢复即可。 -->
        <a-card size="small" class="gate">
          <div class="bill-line">
            <b class="billno">{{ data.run.bill_no }}</b>
            <a-tag v-if="bill?.is_demo" color="default">演示</a-tag>
            <a-tag :color="CONCL[data.run.conclusion]?.color || 'blue'">
              {{ CONCL[data.run.conclusion]?.text || data.run.conclusion }} · 待人工裁决
            </a-tag>
            <span class="amt"><span class="num">{{ fmtAmt(bill?.amount) }}</span><small> 元</small></span>
          </div>
          <div class="facts">
            <span><i>报账部门</i>{{ bill?.claim_dept || '—' }}</span>
            <span><i>核算主体</i>{{ bill?.org_name || '—' }}</span>
            <span><i>提单日期</i><span class="num">{{ (bill?.doc_date || '').slice(0, 10) || '—' }}</span></span>
          </div>
          <div class="ai-line">
            命中 <b style="color: #ff4d4f">{{ data.coverage.hit }}</b> 条 ·
            待人工 {{ data.coverage.pending }} 项 ·
            执行 {{ data.coverage.executed }}/{{ data.coverage.candidates }} 条
            <span v-if="data.coverage.excluded" class="dim">
              （{{ data.coverage.excluded }} 条未执行，原因见表内）
            </span>
            <span v-if="!doneEntry && data.coverage.pending" class="dim">
              · 已人工核实 {{ verified.length }}/{{ data.coverage.pending }} 项（引导，不拦提交）
            </span>
          </div>
          <div v-if="doneEntry" class="act done-row">
            <a-tag color="success">已复核：{{ doneLabel(doneEntry.d, !!doneEntry.note) }}</a-tag>
            <span class="dim">留痕已入库存档（见下方复核记录），同一轮不重复记录</span>
          </div>
          <div v-else class="act">
            <!-- 复核人只读（P0-4）：署名单一来源 = 会话。此前可编辑的输入框与
                 「署名跟会话走，不许有第二个来源」的既有纪律自相矛盾——改个名字
                 就造出第二个署名来源，且留痕按姓名归集，重名即串档。 -->
            <span class="rev-ro" title="复核人 = 当前登录账号，不可修改">
              <i>复核人</i><b>{{ reviewer || '—' }}</b>
            </span>
            <a-input v-model:value="note" class="note" placeholder="复核意见（选填，随通过记录）" aria-label="复核意见" />
            <a-button type="primary" class="pass" :loading="submitting" @click="submit('agree')">复核通过</a-button>
            <a-button danger class="rej" @click="showReturn = true">退回报账人</a-button>
          </div>
          <a-alert v-if="submitErr" type="error" show-icon :message="submitErr" style="margin-top: 10px" />
        </a-card>

        <!-- 单据信息：三卡各自独立（基础 KV / 费用明细 / 审批流程） -->
        <a-card size="small" class="blk" title="单据信息">
          <template #extra>
            <router-link :to="`/review/bills/${data.run.bill_no}`">完整单据详情</router-link>
          </template>
          <div class="kvg">
            <div><span class="k">业务类型</span><span class="v">{{ bill?.biz_type || '—' }}</span></div>
            <div><span class="k">报账部门</span><span class="v">{{ bill?.claim_dept || '—' }}</span></div>
            <div><span class="k">职级</span><span class="v">{{ bill?.applicant_rank || '—' }}</span></div>
            <div><span class="k">出行人员</span><span class="v">{{ (bill?.travelers || []).map((t: any) => t.name).join('、') || (bill?.payee || '—') }}</span></div>
          </div>
        </a-card>

        <a-card size="small" class="blk" title="费用明细">
          <a-table :data-source="bill?.details || []" :row-key="rowKeyOf"
            size="small" :pagination="false"
            :columns="[
              { title: '费用项目', dataIndex: 'expense_item', width: 260 },
              { title: '金额（元）', dataIndex: 'amount', width: 160, align: 'right' },
              { title: '分摊部门', dataIndex: 'alloc_dept', width: 200 },
            ]">
            <template #bodyCell="{ column, record: it }">
              <template v-if="column.dataIndex === 'amount'">{{ fmtAmt(it.amount) }}</template>
              <template v-else-if="column.dataIndex === 'alloc_dept'">
                <span class="dim">{{ it.alloc_dept || '—' }}</span>
              </template>
            </template>
            <template #summary>
              <a-table-summary-row class="sum-row">
                <a-table-summary-cell :index="0">合计</a-table-summary-cell>
                <a-table-summary-cell :index="1" align="right">{{ fmtAmt(bill?.amount) }}</a-table-summary-cell>
                <a-table-summary-cell :index="2"></a-table-summary-cell>
              </a-table-summary-row>
            </template>
          </a-table>
        </a-card>

        <a-card size="small" class="blk" title="审批流程">
          <a-table :data-source="bill?.approvals || []" :row-key="rowKeyOf"
            size="small" :pagination="false"
            :columns="[
              { title: '审批环节', dataIndex: 'node', width: 180 },
              { title: '处理人', dataIndex: 'approver', width: 140 },
              { title: '意见', dataIndex: 'opinion' },
              { title: '时间', key: 'time', width: 140 },
            ]">
            <template #bodyCell="{ column, record: a }">
              <template v-if="column.dataIndex === 'opinion'">{{ a.opinion || '—' }}</template>
              <template v-else-if="column.key === 'time'">
                <span class="dim">{{ (a.approved_at || '').slice(5, 16).replace('T', ' ') }}</span>
              </template>
            </template>
          </a-table>
        </a-card>

        <!-- 规则判定明细（四态+未执行合表，命中/待人工行展开证据与制度依据） -->
        <a-card size="small" class="blk" title="规则判定明细">
          <template #extra>
            <div class="chips">
              <a-radio-group v-model:value="filter" size="small">
                <a-radio-button v-for="[k, t] in FILTERS" :key="k" :value="k">
                  {{ t }} {{ countOf(k) }}
                </a-radio-button>
              </a-radio-group>
            </div>
          </template>
          <a-table :columns="ruleCols" :data-source="shownAll" row-key="id" size="small"
            :expanded-row-keys="openRule ? [openRule] : []"
            :pagination="shownAll.length > PREVIEW_N ? { pageSize: PREVIEW_N, showTotal: (t: number) => `共 ${t} 条` } : false">
            <template #bodyCell="{ column, record: r }">
              <template v-if="column.dataIndex === 'id'">
                <router-link :to="`/asset/rules/${r.id}`" @click.stop>{{ r.id }}</router-link>
              </template>
              <template v-else-if="column.key === 'clause'">
                <span class="dim" :title="r.f?.clause_ref ? `${r.f.clause_ref.file} ${r.f.clause_ref.clause}` : ''">
                  <template v-if="r.f?.clause_ref">{{ r.f.clause_ref.file }} {{ r.f.clause_ref.clause }}</template>
                  <template v-else>—</template>
                </span>
              </template>
              <template v-else-if="column.key === 'outcome'">
                <a-tag :color="OUT[r.outcome]?.color || 'default'">{{ OUT[r.outcome]?.text }}</a-tag>
              </template>
              <template v-else-if="column.key === 'msg'">
                <span class="dim" :title="r.f ? r.f.message : ''">
                  <!-- MarkText（P0-3）：后端 message 里有 **加粗** 标记（实测
                       「但本条要的是**单次连续**出差天数」裸露星号），同页 excluded
                       行早已用 MarkText，此处对齐。 -->
                  <template v-if="r.f"><MarkText :text="r.f.message" /></template>
                  <template v-else><MarkText :text="r.ex!.why" /></template>
                </span>
              </template>
              <template v-else-if="column.key === 'exp'">
                <span v-if="r.f && (r.outcome === 'hit' || r.outcome === 'pending')" class="dim exp"
                      @click.stop="openRule = openRule === r.id ? null : r.id">
                  {{ openRule === r.id ? '收起' : '展开' }}
                </span>
              </template>
            </template>
            <!-- 展开行：判定证据 + 制度依据（挨着证据，不搬去闸门） -->
            <template #expandedRowRender="{ record: r }">
              <div v-if="r.f" class="ev-grid">
                <div>
                  <!-- 对比视图（P1-2）：申报值 vs 制度规定值并排 + 差额徽章。
                       判定成本从「读一段话」降到「扫一眼红字」；数据由后端
                       从 evidence 键对提取（review.py _attach_compare）。 -->
                  <div v-if="r.f.compare && r.f.compare.length" class="ev-cmp">
                    <div class="cmp-h">
                      <span>项目</span><span>单据申报</span><span>制度规定</span><span>差异</span>
                    </div>
                    <div v-for="(c, ci) in r.f.compare" :key="ci" class="cmp-r">
                      <span>{{ c.label }}</span>
                      <span class="num">{{ c.actual.toLocaleString('zh-CN') }}</span>
                      <span class="num dim">{{ c.limit.toLocaleString('zh-CN') }} {{ c.unit }}</span>
                      <span class="num" :class="c.actual > c.limit ? 'over' : 'under'">
                        {{ c.actual > c.limit ? '+' : '' }}{{ (c.actual - c.limit).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}
                        <small v-if="c.limit">（{{ c.actual > c.limit ? '+' : '' }}{{ ((c.actual - c.limit) / c.limit * 100).toFixed(0) }}%）</small>
                      </span>
                    </div>
                  </div>
                  <div class="ev-t">判定证据</div>
                  <div class="ev-kv">
                    <template v-for="([k, v], i) in evidenceRows(r.f)" :key="i">
                      <span class="ek">{{ k }}</span><span>{{ v }}</span>
                    </template>
                  </div>
                  <div v-if="r.f.matched_text" class="ev-matched">
                    命中原文：<code>{{ r.f.matched_text }}</code>
                  </div>
                  <div v-if="r.f.llm_involved && r.f.llm_rationale" class="ev-llm">
                    <a-tag color="blue">模型辅助</a-tag>{{ r.f.llm_rationale }}
                  </div>
                  <label v-if="r.outcome === 'hit'" class="ev-dis" @click.stop>
                    <input type="checkbox" :checked="disagree.includes(r.id)" @change="toggleDisagreeEx(r.id)" />
                    我不认可这条命中（提交后逐条回流误报统计）
                  </label>
                  <!-- 分项核实（P1-5）：与「不认可」对偶的正向勾选。hit 与 pending
                       都可用——AI 判不了的项，人核过后留分项痕迹。 -->
                  <label v-if="r.outcome === 'hit' || r.outcome === 'pending'" class="ev-ver" @click.stop>
                    <input type="checkbox" :checked="verified.includes(r.id)" @change="toggleVerifiedEx(r.id)" />
                    已人工核实（{{ r.outcome === 'pending' ? '此判不了项我核过' : '此命中属实' }}，随复核留痕）
                  </label>
                  <!-- 人工核点（P1-6）：行动指向——「这条判不了，你去看什么」 -->
                  <div v-if="r.f.human_check_hint" class="ev-hint">
                    <a-tag color="blue" class="hint-tag">人工核点</a-tag>{{ r.f.human_check_hint }}
                  </div>
                </div>
                <div>
                  <div class="ev-t">制度依据</div>
                  <div v-if="r.f.clause_ref" class="ev-clause">
                    {{ r.f.clause_ref.file }} · {{ r.f.clause_ref.clause }}
                    <div class="ev-clause-text">{{ r.f.clause_ref.text }}</div>
                    <div v-if="r.f.clause_ref.confidence" class="ev-conf">来源可信度：{{ r.f.clause_ref.confidence }}</div>
                  </div>
                  <div v-else class="dim">软规则：由大模型辅助研判，结论仅供人工参考。</div>
                </div>
              </div>
            </template>
            <template #emptyText>
              <a-empty :image-style="{ height: '40px' }" description="没有该状态的规则" />
            </template>
          </a-table>
          <div v-if="disagree.length || verified.length" class="dis-note">
            <template v-if="verified.length">已人工核实 {{ verified.length }} 项（随复核留痕）</template>
            <template v-if="disagree.length"><template v-if="verified.length"> · </template>已勾 {{ disagree.length }} 条不认可 —— 提交后逐条回流误报统计，「偏差与回流」页按规则可见</template>
          </div>
        </a-card>

        <!-- 同类单据（P1-9）：同主体近 7 日同金额/收款方/业务类型。「这张是不是
             重报」是审核岗高频动作；金额相同是最强信号（前端标红）。默认折叠——
             有同类单才出现，空数据不占版面。 -->
        <a-card v-if="peers && peers.items.length" size="small" class="blk">
          <template #title>同类单据（{{ peers.items.length }}）</template>
          <template #extra><span class="dim">同主体 {{ peers.window_days }} 日内 · 同金额 / 同收款方 / 同业务类型</span></template>
          <a-table :data-source="peers.items" row-key="bill_no" size="small"
            :pagination="false"
            :columns="[
              { title: '单据编号', dataIndex: 'bill_no', width: 220 },
              { title: '金额（元）', dataIndex: 'amount', width: 130, align: 'right' },
              { title: '收款方', dataIndex: 'payee', ellipsis: true },
              { title: 'AI 结论', key: 'concl', width: 90, align: 'center' },
              { title: '人工结论', key: 'man', width: 90, align: 'center' },
              { title: '日期', key: 'dt', width: 110 },
            ]">
            <template #bodyCell="{ column, record: p }">
              <template v-if="column.dataIndex === 'bill_no'">
                <router-link v-if="p.run_id" :to="`/review/report/${p.run_id}`">{{ p.bill_no }}</router-link>
                <router-link v-else :to="`/review/bills/${p.bill_no}`">{{ p.bill_no }}</router-link>
                <a-tag v-if="p.same_amount" color="error" style="margin-left: 6px">同金额</a-tag>
              </template>
              <template v-else-if="column.dataIndex === 'amount'">
                <span :class="{ 'amt-same': p.same_amount }">{{ fmtAmt(p.amount) }}</span>
              </template>
              <template v-else-if="column.key === 'concl'">
                <a-tag v-if="p.conclusion" :color="CONCL[p.conclusion]?.color || 'default'">{{ CONCL[p.conclusion]?.text }}</a-tag>
                <a-tag v-else color="default">未审</a-tag>
              </template>
              <template v-else-if="column.key === 'man'">
                <a-tag v-if="p.manual_decision" :color="DECISION[p.manual_decision]?.color || 'default'">{{ DECISION[p.manual_decision]?.text || p.manual_decision }}</a-tag>
                <span v-else class="dim">—</span>
              </template>
              <template v-else-if="column.key === 'dt'">
                <span class="dim">{{ (p.doc_date || '').slice(0, 10) || '—' }}</span>
              </template>
            </template>
          </a-table>
        </a-card>

        <!-- 单据附件（1:1 缩略图，点开大图） -->
        <a-card size="small" class="blk" title="单据附件">
          <template #extra>
            <span class="dim">{{ bill?.attach_pages != null ? `申报 ${bill.attach_pages} 张 · ` : '' }}实际 {{ (bill?.attachments || []).length }} 张</span>
          </template>
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
            <div v-if="!(bill?.attachments || []).length" class="atts-empty">
              本单没有附件记录
            </div>
          </div>
        </a-card>

        <!-- 复核记录：人工留痕与 AI 审核轮次合并按时间倒序——留痕看得见才算留了 -->
        <a-card size="small" class="blk" title="复核记录">
          <a-table :columns="histCols" :data-source="histRows" row-key="key" size="small"
            :pagination="histRows.length > PREVIEW_N ? { pageSize: PREVIEW_N } : false">
            <template #bodyCell="{ column, record: h }">
              <template v-if="column.dataIndex === 'tm'"><span class="dim">{{ h.tm }}</span></template>
              <template v-else-if="column.dataIndex === 'note'"><span class="dim">{{ h.note }}</span></template>
            </template>
            <template #emptyText>
              <a-empty :image-style="{ height: '40px' }" description="本单尚无复核记录" />
            </template>
          </a-table>
        </a-card>
      </template>
    </Async>

    <a-card v-if="!runId" size="small">
      <a-empty style="padding: 40px 0"
        :description="queueErr ? `队列取数失败：${queueErr}` : '没有待复核的单据'" />
    </a-card>
    </div><!-- /qmain -->

    <!-- 退回理由弹窗。口径文案（P0-11）：旧文案承诺「原因将进入偏差统计」——
         现在 return 确实同步写 conclusion_vs_human 偏差候选（review.py 2.11-E），
         文案与链路对齐。 -->
    <a-modal v-model:open="showReturn" title="退回报账人" ok-text="确认退回" cancel-text="取消"
      :ok-button-props="{ disabled: !reason.trim() || submitting, loading: submitting }"
      :width="520" @ok="submit('return')">
      <div class="dim" style="margin-bottom: 8px">{{ report?.run.bill_no }}</div>
      <!-- 原因分类（P1-4）：结构化维度随留痕落库，偏差页按原因出分布。
           分类下拉在理由框之前——先归类再写细节，是表单的自然顺序。 -->
      <div class="ret-cat">
        <span class="dim">原因分类</span>
        <a-select v-model:value="returnCat" style="width: 140px" placeholder="选择分类"
          :options="returnCats.map((c) => ({ value: c, label: c }))" />
      </div>
      <a-textarea v-model:value="reason" :rows="4" style="margin-top: 8px"
        placeholder="退回原因（必填）：缺少的材料或需修正的内容，将通知报账人修改后重新提交" />
      <div class="dim" style="margin-top: 6px">填写原因后方可提交；原因将记入复核留痕，并作为漏报偏差候选回流统计</div>
    </a-modal>

    <!-- 键盘快捷键帮助（P1-1）：没有帮助浮层的快捷键等于不存在 -->
    <a-modal v-model:open="showHelp" title="键盘快捷键" :footer="null" :width="420">
      <div class="keys">
        <div class="krow"><kbd>←</kbd><kbd>→</kbd><span>上一张 / 下一张</span></div>
        <div class="krow"><kbd>A</kbd><span>复核通过</span></div>
        <div class="krow"><kbd>X</kbd><span>退回报账人（弹出理由框）</span></div>
        <div class="krow"><kbd>E</kbd><span>展开 / 收起首条证据</span></div>
        <div class="krow"><kbd>?</kbd><span>本帮助</span></div>
      </div>
      <div class="dim" style="margin-top: 10px">输入框内按键不生效（正常打字）</div>
    </a-modal>

    <!-- 附件大图查看器（P1-3）：缩放 / 拖拽 / 旋转 / 翻张 / 切换条 -->
    <div class="lb" :class="{ show: lb }" @click.self="closeLb">
      <div class="lb-card" v-if="lb">
        <div class="lb-img" @wheel="lbWheel" @mousedown="lbDown" @mousemove="lbMove" @mouseup="lbUp" @mouseleave="lbUp">
          <img :src="lb.url" :alt="lb.name" draggable="false"
            :style="{ transform: `translate(${lbPos.x}px, ${lbPos.y}px) scale(${lbScale}) rotate(${lbRot}deg)`, cursor: lbDrag ? 'grabbing' : 'grab' }">
        </div>
        <div class="lb-bar">
          <span class="n">{{ lb.name }}<span class="dim">（{{ lb.idx + 1 }}/{{ lbList.length }}）</span></span>
          <div class="lb-ops">
            <a-button size="small" @click="lbNav(-1)" :disabled="lbList.length <= 1">← 上一张</a-button>
            <a-button size="small" @click="lbNav(1)" :disabled="lbList.length <= 1">下一张 →</a-button>
            <a-button size="small" @click="lbZoom(1 / 1.2)">−</a-button>
            <span class="lb-zoom">{{ Math.round(lbScale * 100) }}%</span>
            <a-button size="small" @click="lbZoom(1.2)">＋</a-button>
            <a-button size="small" @click="lbRot = (lbRot + 90) % 360">旋转</a-button>
            <a-button size="small" @click="closeLb">关闭 (Esc)</a-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 满高等高双栏：父容器（外壳 content）已是视口高，两栏 height:100% 继承同一基准，
   各写 calc 魔法数迟早差一截（2026-10-26 实测教训）。右栏自己滚。 */
.qwrap { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 16px; height: 100%; min-height: 0; }
.qdrawer { display: flex; flex-direction: column; overflow: hidden; }
.qdrawer :deep(.ant-card-body) { flex: 1; display: flex; flex-direction: column; min-height: 0; overflow: hidden; padding: 12px; }
.qhead { display: flex; flex-direction: column; gap: 8px; margin-bottom: 8px; }
.qrow2 { display: flex; gap: 8px; }
.qempty { text-align: center; padding: 24px 0; display: flex; flex-direction: column; gap: 8px; align-items: center; }
.qlist { flex: 1; overflow-y: auto; min-height: 0; }
.qrow {
  display: flex; align-items: center; gap: 6px; width: 100%; height: 44px;
  padding: 0 8px; border: 0; border-radius: 6px; background: none;
  cursor: pointer; text-align: left; font-size: 13px;
}
.qrow:hover { background: #f5f5f5; }
.qrow.cur { background: #e6f4ff; }
.qrow .no { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: rgba(0, 0, 0, .8); }
.qrow.cur .no { color: #1677ff; font-weight: 600; }
/* 大额分级（P1-7）：行首 3px 橙条 + 金额列。阈值从 /api/review/config 取，
   不写死（P0-12 红线）。 */
.qrow.big { box-shadow: inset 3px 0 0 #fa8c16; }
.amtn { font-size: 11px; color: rgba(0, 0, 0, .55); font-variant-numeric: tabular-nums; white-space: nowrap; }
.demo { transform: scale(.85); transform-origin: left center; }
.hitn { font-variant-numeric: tabular-nums; font-size: 12px; min-width: 20px; text-align: center; border-radius: 4px; padding: 0 4px; }
.hitn.reject { color: #ff4d4f; background: #fff1f0; }
.hitn.review { color: #d46b08; background: #fff7e6; }
.qmeta { font-size: 12px; color: rgba(0, 0, 0, .45); padding-top: 8px; }

/* 最近处理折叠区（队列栏底部） */
.qrecent { border-top: 1px solid #f0f0f0; margin-top: 8px; padding-top: 8px; }
.qrt { font-size: 12px; color: rgba(0, 0, 0, .45); margin-bottom: 4px; }
.qrow.rrow { height: 32px; }
.qrow.rrow .no { font-size: 12px; }

/* 复核人只读（P0-4） */
.rev-ro { display: inline-flex; align-items: center; gap: 6px; padding: 0 10px;
  height: 32px; border: 1px dashed #d9d9d9; border-radius: 6px; background: #fafafa;
  flex: none; }
.rev-ro i { font-style: normal; font-size: 12px; color: rgba(0, 0, 0, .45); }
.rev-ro b { font-size: 13px; }

/* 快捷键帮助 */
.keys { display: flex; flex-direction: column; gap: 8px; }
.krow { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.krow kbd { min-width: 26px; text-align: center; padding: 2px 7px; border: 1px solid #d9d9d9;
  border-bottom-width: 2px; border-radius: 4px; background: #fafafa; font-size: 12px;
  font-family: inherit; }

.qmain { overflow-y: auto; min-height: 0; padding-right: 2px; }
.qbar { margin-bottom: 16px; }
.qbar-in { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.qtag { font-size: 13px; padding: 2px 10px; }
.grow { flex: 1; }

/* 复核闸门 */
.bill-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.billno { font-size: 16px; }
.amt { margin-left: auto; font-size: 14px; }
.amt .num { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.amt small { color: rgba(0, 0, 0, .45); }
.facts { display: flex; gap: 24px; margin-top: 8px; flex-wrap: wrap; }
.facts span { font-size: 13px; }
.facts i { font-style: normal; color: rgba(0, 0, 0, .45); margin-right: 6px; }
.ai-line { font-size: 13px; color: rgba(0, 0, 0, .65); margin-top: 10px; padding-top: 10px; border-top: 1px solid #f0f0f0; }
.act { display: flex; gap: 8px; margin-top: 12px; align-items: center; }
.act .rev { width: 120px; flex: none; }
.act .note { flex: 1; min-width: 160px; }
.done-row { gap: 10px; }
.ret-cat { display: flex; align-items: center; gap: 10px; }

.blk { margin-bottom: 16px; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.kvg { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 0 32px; }
.kvg .k { display: inline-block; width: 90px; color: rgba(0, 0, 0, .45); font-size: 13px; padding: 6px 0; }
.kvg .v { font-size: 13px; }
.sum-row { font-weight: 600; }

/* 证据展开 */
.exp { cursor: pointer; }
/* 对比视图（P1-2）：四列 grid，超标红字 */
.ev-cmp { border: 1px solid #ffe0e0; background: #fffafa; border-radius: 6px;
  padding: 8px 12px; margin-bottom: 12px; font-size: 12.5px; }
.cmp-h, .cmp-r { display: grid; grid-template-columns: 1.2fr 1fr 1.2fr 1.6fr; gap: 8px; padding: 3px 0; }
.cmp-h { color: rgba(0, 0, 0, .45); font-size: 12px; border-bottom: 1px solid #f0f0f0; padding-bottom: 5px; }
.cmp-r .num { font-variant-numeric: tabular-nums; text-align: right; }
.cmp-r .num small { color: rgba(0, 0, 0, .45); font-size: 11px; }
.cmp-r .over { color: #cf1322; font-weight: 600; }
.cmp-r .under { color: rgba(0, 0, 0, .65); }
.exp:hover { color: #1677ff; }
.ev-grid { display: grid; grid-template-columns: 1.4fr 1fr; gap: 24px; }
.ev-t { font-size: 12px; color: rgba(0, 0, 0, .45); margin-bottom: 8px; }
.ev-kv { display: grid; grid-template-columns: 130px 1fr; gap: 6px 12px; font-size: 13px; }
.ek { color: rgba(0, 0, 0, .45); }
.ev-matched { margin-top: 10px; font-size: 12.5px; color: rgba(0, 0, 0, .65); }
.ev-matched code { background: #fafafa; padding: 1px 6px; border-radius: 4px; }
.ev-llm { margin-top: 10px; font-size: 12.5px; }
.ev-dis { display: flex; align-items: center; gap: 6px; margin-top: 12px; font-size: 12.5px; cursor: pointer; }
/* 分项核实（P1-5）与人工核点（P1-6） */
.ev-ver { display: flex; align-items: center; gap: 6px; margin-top: 6px; font-size: 12.5px; cursor: pointer; }
.ev-hint { display: flex; align-items: flex-start; gap: 6px; margin-top: 10px; padding: 6px 10px;
  background: #f0f7ff; border-radius: 6px; font-size: 12.5px; color: rgba(0, 0, 0, .75); }
.ev-hint .hint-tag { flex: none; transform: scale(.9); transform-origin: left center; }
.amt-same { color: #ff4d4f; font-weight: 600; }
.ev-clause { font-size: 12.5px; color: rgba(0, 0, 0, .65); border: 1px solid #f0f0f0; border-radius: 6px; padding: 10px 12px; }
.ev-clause-text { margin-top: 6px; }
.ev-conf { margin-top: 6px; font-size: 11px; color: rgba(0, 0, 0, .45); }
.dis-note { padding: 10px 0 0; font-size: 12.5px; color: #d46b08; }

/* 附件卡 */
.atts { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
.att { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 10px;
  border: 1px solid #f0f0f0; border-radius: 8px; background: #fff; cursor: pointer; }
.att:hover { border-color: #91caff; }
.thumb { width: 110px; height: 110px; border-radius: 6px; overflow: hidden; display: grid; place-items: center; background: #fafafa; color: rgba(0, 0, 0, .25); }
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.meta { display: flex; flex-direction: column; gap: 2px; text-align: center; min-width: 0; width: 100%; }
.meta .n { font-size: 12px; color: rgba(0, 0, 0, .75); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta .t { font-size: 11px; color: rgba(0, 0, 0, .45); }
.atts-empty { grid-column: 1 / -1; padding: 24px; text-align: center; color: rgba(0, 0, 0, .45); font-size: 13px; }

/* 附件大图查看器（P1-3） */
.lb { position: fixed; inset: 0; z-index: 1000; background: rgba(0, 0, 0, .55);
  display: none; align-items: center; justify-content: center; }
.lb.show { display: flex; }
.lb-card { background: #fff; border-radius: 10px; padding: 16px; max-width: min(1100px, 92vw); }
.lb-img { max-height: 72vh; overflow: hidden; display: grid; place-items: center;
  user-select: none; touch-action: none; background: #fafafa; }
.lb-img img { max-width: 100%; max-height: 72vh; object-fit: contain; transition: transform .05s linear; }
.lb-bar { display: flex; align-items: center; justify-content: space-between; margin-top: 10px; gap: 12px; flex-wrap: wrap; }
.lb-bar .n { font-size: 13px; color: rgba(0, 0, 0, .65); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 300px; }
.lb-ops { display: flex; align-items: center; gap: 6px; }
.lb-zoom { font-size: 12px; color: rgba(0, 0, 0, .55); min-width: 40px; text-align: center; font-variant-numeric: tabular-nums; }

@media (max-width: 1100px) {
  .qwrap { grid-template-columns: 260px minmax(0, 1fr); }
}
</style>
