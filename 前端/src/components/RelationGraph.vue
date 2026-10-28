<script setup lang="ts">
/**
 * 关系图谱（规则库第四页签，2026-10-27 P1-G 完整版）。
 *
 * 设计专章（打磨规划_规则管理员 §三）的四条硬口径，全部照做：
 *   一张图三层可切（全景/规则/概念/业务），不拆三图——17+82+71 节点拆三图反而碎；
 *   粒子承载信息不是装饰——呼吸光晕∝近30天命中、冲突边红虚线脉冲、连线粒子=数据流向；
 *   三级下钻——规则节点→八要素、概念节点→引用规则清单、边→对比裁决入口；
 *   可对比可溯源——ctrl+click 两条规则→路径高亮+八要素 diff；hover 出溯源三要素。
 *
 * 布局确定性（继承旧 SVG 圆周布局的优点）：force 布局结果按拓扑指纹缓存进
 * localStorage，**同数据二次打开不重排**——两次打开一模一样是排障对照的前提。
 * 指纹变了（加规则/加边）才重算。force 用固定随机种子 + 关闭初始动画入场
 * 逐帧跳动，收敛后 snapshot 存回。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { api } from '../api'
import type {
  ConceptRules, GraphConceptNode, GraphRuleEdge, GraphRuleNode, OntoGraphRich,
} from '../api'
import { echarts, type ECOption, type EChartsType } from '../lib/echarts'
import ConflictCompareDrawer from './ConflictCompareDrawer.vue'
import * as session from '../data/session'

const props = defineProps<{ open: boolean }>()
/** 裁决完成后父层刷新（关系边 needs_confirm 会变） */
const emit = defineEmits<{ reload: [] }>()

// ── 数据态 ──────────────────────────────────────────────────────────────────
const data = ref<OntoGraphRich | null>(null)
const err = ref('')
const loading = ref(false)

// ── 图层与视图控制 ──────────────────────────────────────────────────────────
type Layer = 'all' | 'rule' | 'concept' | 'biz'
const layer = ref<Layer>('all')
/** 图例筛选：节点类别多选（rule/concept/biz/entity_edge 类型） */
const legendSel = ref<string[]>(['rule', 'concept', 'biz', 'overrides', 'priority_over', 'biz-edge'])
/** 维度聚合：规则按 D1-D10 折叠成 combo 气泡 */
const grouped = ref(false)

// ── 选中与下钻 ──────────────────────────────────────────────────────────────
/** 侧栏详情：规则节点 / 概念节点 / 边 三形态 */
type Sel =
  | { kind: 'rule'; node: GraphRuleNode }
  | { kind: 'concept'; node: GraphConceptNode; detail: ConceptRules | null }
  | { kind: 'edge'; edge: GraphRuleEdge }
const sel = ref<Sel | null>(null)
const selLoading = ref('')

/** 对比模式：ctrl+click 依次选两个规则节点 */
const cmp = ref<GraphRuleNode[]>([])
const cmpDiff = ref<{ label: string; left: string; right: string; diff: boolean }[]>([])
const cmpPath = ref<string[]>([])

/** 冲突对比抽屉（复用 P0-1 组件：ruleId + other 定位串） */
const cmpDrawer = ref({ open: false, ruleId: '', other: '' })

// ── 布局缓存（确定性）───────────────────────────────────────────────────────
const LS_KEY = 'fjhy-graph-layout-v1'
interface CachedLayout { fp: string; pos: Record<string, [number, number]> }
function fingerprint(g: OntoGraphRich): string {
  const nodeIds = [
    ...g.entities.map((e) => e.entity_id),
    ...g.rule_nodes.map((r) => r.rule_id),
    ...g.concept_nodes.map((c) => c.concept_id),
  ].sort()
  const edges = [
    ...g.entity_edges.map((e) => `${e.from_entity}>${e.to_entity}`),
    ...g.rule_edges.map((e) => `${e.rule_id}>${e.other_rule}`),
    ...g.concept_edges.map((e) => `${e.rule_id}>${e.concept_id}`),
  ].sort()
  return nodeIds.length + '|' + nodeIds.join(',') + '|' + edges.join(';')
}
function loadCache(): CachedLayout | null {
  try {
    const raw = localStorage.getItem(LS_KEY)
    return raw ? (JSON.parse(raw) as CachedLayout) : null
  } catch {
    return null
  }
}
function saveCache(fp: string, pos: Record<string, [number, number]>) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({ fp, pos }))
  } catch {
    /* 私隐模式/满盘：缓存失败不影响画图 */
  }
}

// ── 颜色语义（与全站控制色一致：禁止=红 / 警告=橙）──────────────────────────
const C = {
  ruleBan: '#cf1322',      // 禁止类规则
  ruleWarn: '#d46b08',     // 警告类规则
  ruleCold: '#8c8c8c',     // 零命中灰（静态）
  concept: '#13c2c2',      // 概念蓝绿
  conceptGap: '#faad14',   // 可得性缺口概念（黄=吊在取不到的数据上）
  biz: '#2f6fb5',          // 业务实体
  bizIso: '#bfbfbf',       // 孤点实体
  ov: '#7a9ec4',           // overrides 边
  po: '#2e8b57',           // priority_over 边
  conflict: '#ff4d4f',     // 待裁决冲突
  conceptEdge: '#b8d4d4',  // 引用边（细淡）
  path: '#faad14',         // 对比路径高亮
}
const REL_CN: Record<string, string> = {
  overrides: '覆盖', priority_over: '优先于', conflicts_with: '冲突',
}

// ── hit 热度分档（呼吸强度∝命中次数，log 分档防爆闪）────────────────────────
const maxHit = computed(() =>
  Math.max(1, ...(data.value?.rule_nodes || []).map((r) => r.hit_count)))
function hitTier(n: number): number {
  if (!n) return 0
  return Math.min(3, Math.max(1, Math.ceil((Math.log(n + 1) / Math.log(maxHit.value + 1)) * 3)))
}
/** 呼吸动画的 shadowBlur 半径：0 命中不给光晕（灰静态），档位越高越亮 */
function haloSize(n: number): number {
  const t = hitTier(n)
  return t === 0 ? 0 : 8 + t * 7
}

// ── ECharts 实例与 option 组装 ─────────────────────────────────────────────
const el = ref<HTMLDivElement>()
let chart: EChartsType | null = null
let ro: ResizeObserver | null = null
let haloTimer: number | undefined
/** 呼吸相位：sin 波驱动 shadowBlur 循环（ECharts graph 无原生 halo 循环） */
let phase = 0
/** 当次渲染的节点集（BFS 路径高亮要对这个集合取交集） */
let renderedNodes: Record<string, { x: number; y: number }> = {}

interface GNode {
  id: string
  name: string
  x: number
  y: number
  symbolSize: number
  category: number
  itemStyle: Record<string, unknown>
  label?: Record<string, unknown>
}

const CATEGORIES = [
  { name: '规则·禁止', color: C.ruleBan },
  { name: '规则·警告', color: C.ruleWarn },
  { name: '概念', color: C.concept },
  { name: '概念·缺数据', color: C.conceptGap },
  { name: '业务实体', color: C.biz },
]

function buildOption(): ECOption {
  const g = data.value
  if (!g) return {}

  // 画布实际尺寸：节点坐标以中心 (CX,CY) 为原点的固定虚拟空间生成，
  // graph layout:'none' 直接按像素放置；resize 时重算 option 保持对齐。
  const W = el.value?.clientWidth || 840
  const H = el.value?.clientHeight || 540
  const CX = Math.round(W / 2)
  const CY = Math.round(H / 2)

  const showRules = layer.value === 'all' || layer.value === 'rule'
  const showConcepts = layer.value === 'all' || layer.value === 'concept'
  const showBiz = layer.value === 'all' || layer.value === 'biz'
  const selRuleIds = new Set(cmp.value.map((r) => r.rule_id))

  // 布局：有缓存先撒缓存坐标（layoutAnimation=false），没缓存才跑 force
  const cache = loadCache()
  const useCache = cache && cache.fp === fingerprint(g)
  const cachedPos = useCache ? cache!.pos : null

  const nodes: GNode[] = []
  const edges: Record<string, unknown>[] = []
  renderedNodes = {}

  const addNode = (n: GNode) => {
    nodes.push(n)
    renderedNodes[n.id] = { x: n.x, y: n.y }
  }

  // —— 规则层（grouped 时按维度折叠成 combo 代表点）——
  if (showRules) {
    if (grouped.value) {
      const byDim = new Map<string, GraphRuleNode[]>()
      for (const r of g.rule_nodes) {
        if (!byDim.has(r.dimension)) byDim.set(r.dimension, [])
        byDim.get(r.dimension)!.push(r)
      }
      let i = 0
      for (const [dim, rs] of byDim) {
        const hits = rs.reduce((s, r) => s + r.hit_count, 0)
        const ang = (i / byDim.size) * Math.PI * 2
        i++
        addNode({
          id: dim, name: `${dim} ${rs[0].dimension_name.slice(0, 5)}\n${rs.length} 条 · 命中 ${hits}`,
          x: CX + Math.cos(ang) * (W * 0.24), y: CY + Math.sin(ang) * (H * 0.26),
          symbolSize: 30 + Math.min(30, rs.length),
          category: hits > 0 ? 1 : 0,
          itemStyle: { color: hits > 0 ? C.ruleWarn : C.ruleCold, shadowBlur: haloSize(hits) },
          label: { show: true, position: 'inside', fontSize: 9, color: '#fff' },
        })
      }
    } else {
      for (const r of g.rule_nodes) {
        const base = r.control_type === '禁止' ? C.ruleBan : C.ruleWarn
        const color = r.hit_count === 0 ? C.ruleCold : base
        const size = 14 + Math.min(14, r.hit_count * 1.2) + (r.compiled ? 0 : -3)
        addNode({
          id: r.rule_id,
          name: r.rule_id.replace('TR-', ''),
          x: cachedPos?.[r.rule_id]?.[0] ?? CX + (hash01(r.rule_id) - 0.5) * W * 0.5,
          y: cachedPos?.[r.rule_id]?.[1] ?? CY + (hash01(r.rule_id + 'y') - 0.5) * H * 0.55,
          symbolSize: size,
          category: r.control_type === '禁止' ? 0 : 1,
          itemStyle: {
            color,
            shadowBlur: haloSize(r.hit_count),
            shadowColor: r.control_type === '禁止' ? C.ruleBan : C.ruleWarn,
            borderColor: r.compiled ? 'rgba(255,255,255,.85)' : '#faad14',
            borderWidth: r.compiled ? 1.5 : 2,
          },
          label: { show: hitTier(r.hit_count) >= 2, fontSize: 10,
            formatter: r.rule_id.replace('TR-', '') },
        })
      }
    }
  }

  // —— 概念层（被规则引用的 71 个；可得性非样本可得=黄）——
  if (showConcepts) {
    for (const c of g.concept_nodes) {
      const gap = c.availability !== '样本可得' && !c.availability.includes('可得')
      addNode({
        id: c.concept_id,
        name: c.concept_name,
        x: cachedPos?.[c.concept_id]?.[0] ?? CX + (hash01(c.concept_id) - 0.5) * W * 0.55,
        y: cachedPos?.[c.concept_id]?.[1] ?? CY + (hash01(c.concept_id + 'y') - 0.5) * H * 0.62,
        symbolSize: Math.min(26, 10 + c.n_rules * 0.9),
        category: gap ? 3 : 2,
        itemStyle: { color: gap ? C.conceptGap : C.concept, opacity: 0.92 },
        label: { show: c.n_rules >= 8, fontSize: 9 },
      })
    }
  }

  // —— 业务层（实体。孤点灰+虚线圈）——
  if (showBiz) {
    for (const e of g.entities) {
      addNode({
        id: e.entity_id,
        name: e.entity_name,
        x: cachedPos?.[e.entity_id]?.[0] ?? CX + (hash01(e.entity_id) - 0.5) * W * 0.5,
        y: cachedPos?.[e.entity_id]?.[1] ?? CY + (hash01(e.entity_id + 'y') - 0.5) * H * 0.6,
        symbolSize: Math.min(40, 20 + e.degree * 2.4),
        category: 4,
        itemStyle: {
          color: e.isolated ? C.bizIso : C.biz,
          borderType: e.isolated ? 'dashed' : 'solid',
        },
        label: { show: true, fontSize: 10 },
      })
    }
  }

  // —— 边 ——
  const nodeIds = new Set(nodes.map((n) => n.id))
  const inPath = (a: string, b: string) =>
    cmpPath.value.length > 0 &&
    cmpPath.value.includes(a) && cmpPath.value.includes(b) &&
    Math.abs(cmpPath.value.indexOf(a) - cmpPath.value.indexOf(b)) === 1

  if (showBiz && legendSel.value.includes('biz-edge')) {
    for (const e of g.entity_edges) {
      if (!nodeIds.has(e.from_entity) || !nodeIds.has(e.to_entity)) continue
      edges.push({
        source: e.from_entity, target: e.to_entity,
        lineStyle: {
          color: C.ov, width: 1.4, opacity: inPath(e.from_entity, e.to_entity) ? 1 : 0.55,
          curveness: 0.12,
        },
        value: `${e.rel_type}（${e.cardinality || '?'}）`,
        etype: 'entity',
      })
    }
  }
  if (showRules && !grouped.value) {
    for (const e of g.rule_edges) {
      // 分层互斥：规则层关掉时规则边也不画
      const target = e.other_rule
      const isOpen = e.conflict_open
      if (!nodeIds.has(e.rule_id)) continue
      if (isOpen) {
        // 待裁决冲突：另一头可能是「制度 第十条」非规则节点——补一个幽灵节点
        if (!nodeIds.has(target)) {
          addNode({
            id: target, name: target.length > 12 ? target.slice(0, 12) + '…' : target,
            x: cachedPos?.[target]?.[0] ?? CX + (hash01(target) - 0.5) * W * 0.5,
            y: cachedPos?.[target]?.[1] ?? CY + (hash01(target + 'y') - 0.5) * H * 0.55,
            symbolSize: 12, category: 0,
            itemStyle: { color: '#fff', borderColor: C.conflict,
              borderWidth: 2, borderType: 'dashed' },
            label: { show: true, fontSize: 9, color: C.conflict },
          })
          nodeIds.add(target)
        }
      } else if (!nodeIds.has(target)) continue
      const hl = inPath(e.rule_id, target)
      edges.push({
        source: e.rule_id, target,
        lineStyle: {
          color: isOpen ? C.conflict : e.rel_type === 'overrides' ? C.ov : C.po,
          width: isOpen ? 2.6 : hl ? 2.4 : 1.3,
          type: isOpen ? 'dashed' : 'solid',
          opacity: isOpen ? 1 : hl ? 1 : 0.6,
          curveness: 0.1,
        },
        emphasis: { lineStyle: { width: 3 } },
        value: isOpen ? `待裁决冲突${e.gap_id ? ' · ' + e.gap_id : ''}` : REL_CN[e.rel_type] || e.rel_type,
        etype: e.rel_type,
      })
    }
  }
  // 概念引用边（细淡，只在概念层开着时画；规则层关了边悬空不画）
  if (showConcepts && showRules && !grouped.value) {
    for (const e of g.concept_edges) {
      if (!nodeIds.has(e.rule_id) || !nodeIds.has(e.concept_id)) continue
      edges.push({
        source: e.rule_id, target: e.concept_id,
        lineStyle: { color: C.conceptEdge, width: 0.7, opacity: 0.35, curveness: 0.08 },
        value: `引用（${e.role === 'threshold' ? '阈值位' : e.role === 'exist' ? '存在性' : '读取'}）`,
        etype: 'concept-ref',
      })
    }
  }

  // 对比模式下其余节点降透明度
  if (cmp.value.length === 2) {
    const keep = new Set([...cmpPath.value, ...selRuleIds])
    for (const n of nodes) {
      if (!keep.has(n.id)) {
        n.itemStyle = { ...n.itemStyle, opacity: 0.15 }
        if (n.label) n.label = { ...n.label, show: false }
      }
    }
    for (const e of edges) {
      const ls = e.lineStyle as Record<string, unknown>
      if (ls) e.lineStyle = { ...ls, opacity: Math.min(0.12, Number(ls.opacity) || 0.5) }
    }
  }

  // lines-effect 粒子层的数据不在这里算——force 布局会挪动节点，预算坐标
  // 全是错的。render() 之后由 updateFlow() 从 chart 的真实节点位置回填。

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(255,255,255,.98)',
      borderColor: '#e5e7eb',
      textStyle: { color: 'rgba(0,0,0,.82)', fontSize: 12 },
      extraCssText: 'max-width: 340px; white-space: normal;',
      formatter: tooltipFmt,
    },
    animation: !useCache,                    // 缓存坐标时不重放入场动画（确定性）
    animationDuration: 700,
    grid: { left: 0, right: 0, top: 0, bottom: 0, containLabel: false },
    xAxis: { show: false, min: -CX, max: CX, type: 'value' },
    yAxis: { show: false, min: -CY, max: CY, type: 'value' },
    series: [
      {
        id: 'flow', type: 'lines', coordinateSystem: 'cartesian2d',
        z: 2, silent: true,
        polyline: false,
        effect: {
          show: false,                         // updateFlow() 布局稳定后再开
          symbol: 'circle', symbolSize: 3.2, period: 4,
          trailLength: 0.25, color: '#fff', constantSpeed: 0,
        },
        lineStyle: { width: 0, opacity: 0, curveness: 0.1 },
        data: [],
      },
      {
        id: 'graph', type: 'graph', layout: useCache ? 'none' : 'force',
        roam: true, draggable: true,
        z: 3,
        categories: CATEGORIES,
        force: {
          repulsion: 130, edgeLength: [46, 120], gravity: 0.08,
          friction: 0.25, layoutAnimation: true,
        },
        label: { position: 'right', distance: 3, color: 'rgba(0,0,0,.72)' },
        emphasis: { focus: 'adjacency', scale: 1.25,
          label: { show: true, fontSize: 10 } },
        nodes, links: edges,
        lineStyle: { curveness: 0.1 },
      },
    ],
  } as ECOption
}

/** 稳定伪随机（同 id 永远同一个值——布局初始位置确定性的一部分） */
function hash01(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return ((h >>> 0) % 10000) / 10000
}

/** tooltip：节点=名称+热度+溯源三要素；边=关系语义+依据 */
function tooltipFmt(p: Record<string, any>): string {
  const g = data.value
  if (!g) return ''
  if (p.dataType === 'edge') {
    const v = String(p.data?.value || '')
    const src = p.data?.source as string, tgt = p.data?.target as string
    const re = g.rule_edges.find(
      (e) => (e.rule_id === src && e.other_rule === tgt)
        || (e.rule_id === tgt && e.other_rule === src))
    let out = `<b>${v}</b><br/><span style="color:#888">${src} → ${tgt}</span>`
    if (re?.conflict_open) {
      out += `<br/><span style="color:#cf1322">待裁决${re.gap_id ? ' · 缺口 ' + re.gap_id : ''}——点边开对比裁决</span>`
      const why = String((re.note || {}).why || '')
      if (why) out += `<br/><span style="color:#666">${why.slice(0, 120)}</span>`
    } else if (re) {
      const why = String((re.note || {}).why || '')
      if (why) out += `<br/><span style="color:#666">${why.slice(0, 100)}</span>`
    }
    return out
  }
  const id = String(p.data?.id || '')
  const r = g.rule_nodes.find((x) => x.rule_id === id)
  if (r) {
    const src = r.source
      ? `来源：${r.source.file || '？'} ${r.source.clause || ''}（${r.source.confidence || '—'}）` : '来源未写明'
    return `<b>${r.rule_id}</b> ${r.rule_name}<br/>`
      + `${r.dimension} ${r.dimension_name} · ${r.control_type || '—'} · ${r.compiled ? '可执行' : '待定'}<br/>`
      + `<span style="color:${r.hit_count ? '#d46b08' : '#888'}">近 30 天命中 ${r.hit_count} 次</span><br/>`
      + `<span style="color:#666">${src}</span>`
  }
  const c = g.concept_nodes.find((x) => x.concept_id === id)
  if (c) {
    return `<b>${c.concept_name}</b>（${c.concept_id}）<br/>`
      + `出自实体：${c.entity_name} · ${c.data_type}<br/>`
      + `可得性：${c.availability}${c.gap_id ? ' · 缺口 ' + c.gap_id : ''}<br/>`
      + `<span style="color:#666">被 ${c.n_rules} 条规则引用——点节点看清单</span>`
  }
  const e = g.entities.find((x) => x.entity_id === id)
  if (e) {
    return `<b>${e.entity_name}</b>（${e.entity_id}）<br/>`
      + `概念 ${e.concepts} 个 · 关系 ${e.degree} 条${e.isolated ? '<br/><span style="color:#888">孤点——本体关系没建到它</span>' : ''}`
  }
  return String(p.data?.name || '')
}

// ── 渲染与生命周期 ──────────────────────────────────────────────────────────
function render() {
  if (!chart || !data.value) return
  chart.setOption(buildOption(), { notMerge: true })
  startHalo()
  // force 布局要时间收敛；layout:'none'（缓存）也要一帧进 zrender。
  // 粒子坐标必须等布局稳定后从真实节点位置回填，先收敛后 1.2s 再挂粒子。
  window.clearTimeout(flowTimer)
  window.clearTimeout(fitTimer)
  // force 收敛后把全图 fit 进画布（172 节点的 force 会散到 ±600px，不 fit 一半在屏外）
  fitTimer = window.setTimeout(fitView, 1600)
  const rulesShown = layer.value === 'all' || layer.value === 'rule'
  if (rulesShown && !grouped.value && legendSel.value.includes('rule-edge')) {
    flowTimer = window.setTimeout(updateFlow, 2100)
  }
}
let flowTimer: number | undefined
let fitTimer: number | undefined

/** 把 force 布局散出去的节点拉回画布：读 layout 范围，等比缩放+平移后
 *  **写回节点坐标**（写回而不是用 series.zoom——zoom 只作用于 graph 系列，
 *  lines 粒子层跟不上，坐标会脱节）。写回后粒子与节点永远同空间。 */
function fitView() {
  const g = data.value
  if (!chart || !g) return
  const pos = readLayout()
  if (!Object.keys(pos).length) return
  const xs = Object.values(pos).map((p) => p[0])
  const ys = Object.values(pos).map((p) => p[1])
  const minX = Math.min(...xs), maxX = Math.max(...xs)
  const minY = Math.min(...ys), maxY = Math.max(...ys)
  const W = el.value?.clientWidth || 840
  const H = el.value?.clientHeight || 540
  const spanX = Math.max(1, maxX - minX), spanY = Math.max(1, maxY - minY)
  const k = Math.min((W * 0.86) / spanX, (H * 0.86) / spanY)
  if (k >= 1) return                            // 本来就在画布里，不动
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2
  // 全部节点（172 个，全量数组——**不许给部分数组**，setOption 数据是整体替换）
  const nodes: { id: string; x: number; y: number }[] = []
  for (const [id, p] of Object.entries(pos)) {
    nodes.push({ id, x: (p[0] - cx) * k + W / 2, y: (p[1] - cy) * k + H / 2 })
  }
  // **layout 换成 none**：force 还在跑的话写回的坐标下一帧就被模拟器覆盖
  //（实测踩坑：写回后元素位置纹丝不动）。冻结布局=坐标语义钉死。
  chart.setOption({ series: [{ id: 'graph', layout: 'none', nodes }] })
  // fit 后立刻存缓存（坐标已是画布内空间）
  saveCache(fingerprint(g), Object.fromEntries(nodes.map((n) => [n.id, [n.x, n.y]])))
}

/** 粒子回填：从图模型读布局后的真实节点坐标（graph model layout），
 *  换算进 lines 隐轴空间（数据坐标 = 像素坐标 - 画布中心）。 */
function updateFlow() {
  const g = data.value
  if (!chart || !g) return
  const pos = readLayout()
  if (!Object.keys(pos).length) return
  const W = el.value?.clientWidth || 840
  const H = el.value?.clientHeight || 540
  const CX = W / 2, CY = H / 2
  const flow: { coords: [number, number][]; lineStyle: Record<string, unknown> }[] = []
  for (const e of g.rule_edges) {
    if (e.conflict_open) continue            // 冲突边用脉冲语义，不用流粒子
    const a = pos[e.rule_id]
    const b = pos[e.other_rule]
    if (!a || !b) continue
    flow.push({
      coords: [[a[0] - CX, a[1] - CY], [b[0] - CX, b[1] - CY]],
      lineStyle: { color: e.rel_type === 'overrides' ? C.ov : C.po, opacity: 0 },
    })
  }
  chart.setOption({
    xAxis: { min: -CX, max: CX },
    yAxis: { min: -CY, max: CY },
    series: [{
      id: 'flow',
      effect: { show: flow.length > 0 },
      data: flow,
    }],
  })
}

/** 呼吸动画：graph 无原生 halo 循环，用 sin 波改 shadowBlur。
 *  **直接改 zrender 元素**而不是 setOption——实测 setOption 局部更新会
 *  把 172 个节点截断成传入数组的长度（数据数组整体替换语义），
 *  图越跑节点越少就是它干的。zrender 元素层改属性 + refresh 零副作用。 */
function startHalo() {
  stopHalo()
  const g = data.value
  if (!g || !chart) return
  const hot = g.rule_nodes.filter((r) => r.hit_count > 0)
  if (!hot.length) return
  haloTimer = window.setInterval(() => {
    if (!chart) return
    phase += 0.35
    const k = 0.65 + 0.35 * Math.sin(phase)
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const sm = (chart as any).getModel().getSeriesByIndex(1)
      if (!sm || !sm.getGraph) return
      let touched = 0
      sm.getGraph().eachNode((n: {
        id: string; getGraphicEl: () => { shadowBlur?: number } | undefined
      }) => {
        const r = g.rule_nodes.find((x) => x.rule_id === n.id)
        if (!r || !r.hit_count) return
        const el = n.getGraphicEl()
        if (el) {
          el.shadowBlur = Math.round(haloSize(r.hit_count) * k)
          touched++
        }
      })
      if (touched) chart.getZr().refresh()
    } catch {
      /* 布局重建间隙取不到元素就跳过这一拍 */
    }
  }, 500)
}
function stopHalo() {
  if (haloTimer !== undefined) {
    window.clearInterval(haloTimer)
    haloTimer = undefined
  }
}

function resetView() {
  chart?.dispatchAction({ type: 'restore' })
  cmp.value = []
  cmpPath.value = []
  cmpDiff.value = []
  sel.value = null
  render()
}

/** 图例筛选开关：同组内多类别共用一个开关键（规则两类一起开关） */
function toggleLegend(key: string) {
  const i = legendSel.value.indexOf(key)
  if (i >= 0) legendSel.value.splice(i, 1)
  else legendSel.value.push(key)
}

// ── 事件：点击（下钻/对比）、边点击（冲突→抽屉）─────────────────────────────
function onClick(p: Record<string, any>) {
  const g = data.value
  if (!g) return
  const id = String(p.data?.id || '')
  const src = String(p.data?.source || '')
  const tgt = String(p.data?.target || '')
  const dataType = String(p.dataType || '')

  if (p.dataType === 'edge') {
    const re = g.rule_edges.find(
      (e) => (e.rule_id === src && e.other_rule === tgt)
        || (e.rule_id === tgt && e.other_rule === src))
    if (re?.conflict_open) {
      // 待裁决冲突边：直开对比抽屉（复用 P0-1 ConflictCompareDrawer）
      cmpDrawer.value = { open: true, ruleId: re.rule_id, other: re.other_rule }
    } else if (re) {
      sel.value = { kind: 'edge', edge: re }
    } else {
      // 实体边/概念引用边：详情面板给关系语义
      sel.value = null
    }
    return
  }

  // 节点
  const r = g.rule_nodes.find((x) => x.rule_id === id)
  if (r && !grouped.value) {
    if (p.event?.event?.ctrlKey || p.event?.event?.metaKey) {
      onCmpPick(r)
      return
    }
    sel.value = { kind: 'rule', node: r }
    return
  }
  if (grouped.value && /^D\d+$/.test(id)) {
    // combo 点开=切到规则层并取消聚合（双击展开语义，单击触发）
    grouped.value = false
    layer.value = 'rule'
    return
  }
  const c = g.concept_nodes.find((x) => x.concept_id === id)
  if (c) {
    sel.value = { kind: 'concept', node: c, detail: null }
    loadConcept(c.concept_id)
  }
}

async function loadConcept(cid: string) {
  selLoading.value = cid
  try {
    const d = await api.ontoConceptRules(cid)
    if (sel.value?.kind === 'concept' && sel.value.node.concept_id === cid) {
      sel.value = { kind: 'concept', node: sel.value.node, detail: d }
    }
  } catch (e) {
    message.error('取概念引用清单失败：' + (e instanceof Error ? e.message : String(e)))
  } finally {
    selLoading.value = ''
  }
}

// ── 对比模式 ────────────────────────────────────────────────────────────────
function onCmpPick(r: GraphRuleNode) {
  const i = cmp.value.findIndex((x) => x.rule_id === r.rule_id)
  if (i >= 0) {
    cmp.value.splice(i, 1)
  } else {
    cmp.value.push(r)
    if (cmp.value.length > 2) cmp.value.shift()
  }
  recomputeCmp()
}

function recomputeCmp() {
  cmpDiff.value = []
  cmpPath.value = []
  if (cmp.value.length !== 2) {
    render()
    return
  }
  const [a, b] = cmp.value
  // BFS 最短路（图仅百余节点，前端算）
  cmpPath.value = bfs(a.rule_id, b.rule_id)
  // 八要素 diff（值取自 graph 出口已有字段 + 详情页同源字段的轻量投影）
  cmpDiff.value = diffRows(a, b)
  render()
}

function bfs(from: string, to: string): string[] {
  const g = data.value
  if (!g) return []
  const adj = new Map<string, string[]>()
  const push = (a: string, b: string) => {
    if (!adj.has(a)) adj.set(a, [])
    adj.get(a)!.push(b)
    if (!adj.has(b)) adj.set(b, [])
    adj.get(b)!.push(a)
  }
  for (const e of g.rule_edges) {
    const other = /^TR-/.test(e.other_rule) ? e.other_rule : ''
    if (other) push(e.rule_id, other)
  }
  const prev = new Map<string, string>([[from, '']])
  const q = [from]
  while (q.length) {
    const cur = q.shift()!
    if (cur === to) break
    for (const nxt of adj.get(cur) || []) {
      if (!prev.has(nxt)) {
        prev.set(nxt, cur)
        q.push(nxt)
      }
    }
  }
  if (!prev.has(to)) return [from, to]
  const path: string[] = []
  for (let cur = to; cur; cur = prev.get(cur)!) path.unshift(cur)
  return path
}

/** 八要素对照行（与冲突抽屉 diffRows 同构：差异行高亮） */
function diffRows(a: GraphRuleNode, b: GraphRuleNode) {
  const fmt = (v: unknown) =>
    v === null || v === undefined || v === '' ? '—' : String(v)
  const src = (r: GraphRuleNode) =>
    r.source ? `${r.source.file} ${r.source.clause || ''}` : '—'
  const rows: { label: string; left: string; right: string; diff: boolean }[] = []
  const push = (label: string, l: string, r: string) =>
    rows.push({ label, left: l, right: r, diff: l !== r })
  push('① 规则名称', fmt(a.rule_name), fmt(b.rule_name))
  push('② 审核维度', fmt(a.dimension_name), fmt(b.dimension_name))
  push('③ 风险类别', fmt(a.risk_class), fmt(b.risk_class))
  push('控制类型', fmt(a.control_type), fmt(b.control_type))
  push('类型', fmt(a.rule_class), fmt(b.rule_class))
  push('⑧ 优先级', fmt(a.priority), fmt(b.priority))
  push('可执行', a.compiled ? '可执行' : '待定', b.compiled ? '可执行' : '待定')
  push('命中(30天)', String(a.hit_count), String(b.hit_count))
  push('来源', src(a), src(b))
  return rows
}

/** 对比转裁决：两条规则确实口径冲突时，生成 conflicts_with 裁决入口。
 *  不新造旁路——转成一个对比抽屉会话（走既有 /relations/resolve 留痕）；
 *  若两规则间本来就有冲突边直接用它，没有则提示用「任意对比」结论走修订。 */
function cmpToAdjudicate() {
  const g = data.value
  if (!g || cmp.value.length !== 2) return
  const [a, b] = cmp.value
  const rel = g.rule_edges.find(
    (e) => (e.rule_id === a.rule_id && e.other_rule === b.rule_id)
      || (e.rule_id === b.rule_id && e.other_rule === a.rule_id))
  if (rel) {
    cmpDrawer.value = { open: true, ruleId: rel.rule_id, other: rel.other_rule }
  } else {
    message.info('这两条规则之间没有冲突关系记录。对比结论请走「修订规则」随版本发布生效（裁决流只受理已登记的冲突）')
  }
}

function onResolved() {
  emit('reload')
  void reload()
}

// ── 加载 ────────────────────────────────────────────────────────────────────
/** 从 ECharts 图模型读布局后的真实节点位置（getOption 只回输入数据，
 *  force 挪过的位置只存在 graph model 的 layout 里——实测踩坑）。 */
function readLayout(): Record<string, [number, number]> {
  if (!chart) return {}
  const pos: Record<string, [number, number]> = {}
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const sm = (chart as any).getModel().getSeriesByIndex(1)
    if (!sm || !sm.getGraph) return {}
    sm.getGraph().eachNode((n: { id: string; getLayout: () => number[] }) => {
      const lay = n.getLayout()
      if (lay && lay.length >= 2 && n.id) pos[n.id] = [lay[0], lay[1]]
    })
  } catch {
    /* model API 变动时退回空——粒子只是不显示，不该把页面打挂 */
  }
  return pos
}

async function reload() {
  loading.value = true
  err.value = ''
  try {
    data.value = await api.ontoGraphRich()
    await nextTick()
    render()
    // 布局快照由 render 里的 fitTimer（1.6s）做——fit 后坐标才是画布内空间
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

// ── 挂载 ────────────────────────────────────────────────────────────────────
onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value)
  chart.on('click', onClick)
  // 调试/验证钩子：无头断言从容器元素直接取实例（避免从页面外 import
  // echarts 得到另一个模块实例、instance 注册表对不上的坑）
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(el.value as any).__chart = chart
  ro = new ResizeObserver(() => chart?.resize())
  ro.observe(el.value)
  void reload()
})
onUnmounted(() => {
  stopHalo()
  window.clearTimeout(flowTimer)
  window.clearTimeout(fitTimer)
  ro?.disconnect()
  chart?.dispose()
  chart = null
})

// open 由父层 tab 驱动：页签不在前台时 ECharts 容器 display:none 拿不到尺寸，
// 切回来必须 resize（否则画布 0×0 图不显示——ECharts 最常见的翻车点）
watch(() => props.open, (v) => {
  if (v) {
    void nextTick(() => {
      chart?.resize()
      if (!data.value && !loading.value) void reload()
    })
  }
})
watch([layer, grouped], () => {
  sel.value = null
  render()
})
watch(legendSel, () => render(), { deep: true })
</script>

<template>
  <div class="rg-wrap">
    <!-- 顶部工具条：分层开关 / 图例筛选 / 聚合 / 重置 -->
    <div class="rg-toolbar">
      <a-segmented v-model:value="layer" :options="[
        { value: 'all', label: '全景' },
        { value: 'rule', label: '规则层' },
        { value: 'concept', label: '概念层' },
        { value: 'biz', label: '业务层' },
      ]" size="small" />
      <span class="rg-sep" />
      <span class="rg-legend">
        <span class="lg on" :style="{ '--c': C.ruleBan }"
          @click="toggleLegend('rule')">禁止规则</span>
        <span class="lg on" :style="{ '--c': C.ruleWarn }"
          @click="toggleLegend('rule')">警告规则</span>
        <span class="lg" :class="{ on: legendSel.includes('concept') }"
          :style="{ '--c': C.concept }" @click="toggleLegend('concept')">概念</span>
        <span class="lg" :class="{ on: legendSel.includes('biz') }"
          :style="{ '--c': C.biz }" @click="toggleLegend('biz')">业务实体</span>
        <span class="lg" :class="{ on: legendSel.includes('rule-edge') }"
          :style="{ '--c': C.po }" @click="toggleLegend('rule-edge')">关系粒子</span>
      </span>
      <span class="rg-sep" />
      <a-switch v-model:checked="grouped" size="small" />
      <span class="dim" style="margin-left: -6px">按维度聚合</span>
      <span class="rg-sep" />
      <a-button size="small" @click="resetView">重置视图</a-button>
      <div style="flex: 1" />
      <span class="dim">光晕 = 近 30 天命中 · <span class="rg-conflict-legend">红虚线</span> = 待裁决冲突 · Ctrl+点两个规则做对比</span>
    </div>

    <!-- 主体：图 + 侧栏 -->
    <div class="rg-main">
      <div class="rg-canvas-col">
        <div v-if="loading" class="rg-state">正在取图数据…</div>
        <div v-else-if="err" class="rg-state">
          <a-alert type="error" show-icon message="图谱数据加载失败" :description="err" />
          <a-button size="small" style="margin-top: 8px" @click="reload()">重试</a-button>
        </div>
        <div ref="el" class="rg-canvas" />
        <!-- 对比模式侧栏（浮在画布右上） -->
        <div v-if="cmp.length" class="rg-cmp">
          <div class="rg-cmp-head">
            <b>规则对比</b>
            <a @click="cmp = []; recomputeCmp()">清除</a>
          </div>
          <div class="rg-cmp-pair">
            <code v-for="r in cmp" :key="r.rule_id">{{ r.rule_id }}</code>
            <span v-if="cmp.length < 2" class="dim">再 Ctrl+点一个</span>
          </div>
          <template v-if="cmp.length === 2">
            <div class="dim" style="margin: 4px 0">
              路径 {{ cmpPath.length ? cmpPath.join(' → ') : '（两规则间无关系链）' }}
            </div>
            <table class="rg-diff-table">
              <tbody>
                <tr v-for="row in cmpDiff" :key="row.label" :class="{ diffr: row.diff }">
                  <td class="dl">{{ row.label }}</td>
                  <td>{{ row.left }}</td>
                  <td>{{ row.right }}</td>
                </tr>
              </tbody>
            </table>
            <a-button size="small" type="primary" block style="margin-top: 8px"
              @click="cmpToAdjudicate">转为裁决</a-button>
          </template>
        </div>
      </div>

      <!-- 选中详情面板（替代弹窗轰炸） -->
      <div class="rg-side">
        <template v-if="sel?.kind === 'rule'">
          <div class="rg-side-head">
            <b>{{ sel.node.rule_id }}</b>
            <a-tag :color="sel.node.control_type === '禁止' ? 'error' : 'warning'">
              {{ sel.node.control_type }}
            </a-tag>
          </div>
          <div class="rg-side-title">{{ sel.node.rule_name }}</div>
          <div class="rg-meta">
            <a-tag>{{ sel.node.dimension }} {{ sel.node.dimension_name }}</a-tag>
            <a-tag :color="sel.node.compiled ? 'success' : 'default'">
              {{ sel.node.compiled ? '可执行' : '待定' }}
            </a-tag>
            <a-tag v-if="sel.node.hit_count" color="orange">命中 {{ sel.node.hit_count }}</a-tag>
            <a-tag v-else>零命中</a-tag>
          </div>
          <div v-if="sel.node.source" class="rg-quote">
            <div class="dim">来源（溯源）</div>
            <div>{{ sel.node.source.file }} · {{ sel.node.source.clause || '条款号未写明' }}</div>
            <a-tag v-if="sel.node.source.confidence" size="small"
              :color="sel.node.source.confidence === 'OCR' ? 'warning' : 'success'">
              {{ sel.node.source.confidence }}
            </a-tag>
          </div>
          <div class="rg-kv">
            <b>风险类别</b><span>{{ sel.node.risk_class || '—' }}</span>
            <b>优先级</b><span>{{ sel.node.priority ?? '—' }}</span>
            <b>可执行性</b><span>{{ sel.node.feasibility || '—' }}</span>
          </div>
          <router-link class="rg-side-link"
            :to="`/asset/rules/${encodeURIComponent(sel.node.rule_id)}`">
            查看完整详情（八要素 / 版本 / 执行记录）→
          </router-link>
        </template>

        <template v-else-if="sel?.kind === 'concept'">
          <div class="rg-side-head">
            <b>{{ sel.node.concept_id }}</b>
            <a-tag :color="sel.node.availability === '样本可得' ? 'cyan' : 'gold'">
              {{ sel.node.availability }}
            </a-tag>
          </div>
          <div class="rg-side-title">{{ sel.node.concept_name }}</div>
          <div class="rg-meta">
            <a-tag>{{ sel.node.entity_name }}</a-tag>
            <a-tag>{{ sel.node.data_type }}</a-tag>
            <a-tag v-if="sel.node.sensitive" color="red">敏感</a-tag>
          </div>
          <div v-if="sel.node.gap_id" class="rg-quote">
            <div class="dim">数据缺口</div>
            <div>{{ sel.node.gap_id }}</div>
          </div>
          <div class="rg-side-sec">
            引用它的规则（{{ sel.detail?.rules.length ?? '…' }} 条）
          </div>
          <div v-if="selLoading === sel.node.concept_id" class="dim">正在取引用清单…</div>
          <div v-else-if="sel.detail" class="rg-rule-list">
            <div v-for="r in sel.detail.rules" :key="r.rule_id" class="rg-rule-item">
              <router-link :to="`/asset/rules/${encodeURIComponent(r.rule_id)}`">
                {{ r.rule_id }}
              </router-link>
              <span class="rg-rule-name">{{ r.rule_name }}</span>
              <a-tag v-if="r.role === 'threshold'" color="orange" style="margin-left: auto">
                阈值位
              </a-tag>
              <a-tag v-else-if="r.role === 'exist'" style="margin-left: auto">存在性</a-tag>
            </div>
          </div>
        </template>

        <template v-else-if="sel?.kind === 'edge'">
          <div class="rg-side-head">
            <b>规则关系</b>
            <a-tag :color="sel.edge.rel_type === 'conflicts_with' ? 'error' : 'blue'">
              {{ REL_CN[sel.edge.rel_type] || sel.edge.rel_type }}
            </a-tag>
          </div>
          <div class="rg-cmp-pair">
            <code>{{ sel.edge.rule_id }}</code>
            <span class="dim">→</span>
            <code>{{ sel.edge.other_rule }}</code>
          </div>
          <div v-if="sel.edge.gap_id" class="rg-quote">
            <div class="dim">缺口</div><div>{{ sel.edge.gap_id }}</div>
          </div>
          <div v-if="sel.edge.note" class="rg-quote">
            <div class="dim">装载时依据</div>
            <div class="rg-note"><MarkText :text="String(sel.edge.note.why || '')" /></div>
          </div>
          <a-button v-if="sel.edge.conflict_open" type="primary" size="small" block
            @click="cmpDrawer = { open: true, ruleId: sel.edge.rule_id, other: sel.edge.other_rule }">
            对比裁决
          </a-button>
        </template>

        <div v-else class="rg-side-empty">
          <div class="dim">点节点看详情</div>
          <ul class="rg-hints dim">
            <li>规则节点：八要素摘要 + 溯源条款</li>
            <li>概念节点：可得性 + 引用它的规则清单</li>
            <li>红虚线边：待裁决冲突，点开即对比</li>
            <li>Ctrl+点两个规则节点：路径与要素差异</li>
          </ul>
        </div>
      </div>
    </div>

    <!-- 冲突对比抽屉（复用 P0-1） -->
    <ConflictCompareDrawer :key="cmpDrawer.ruleId + '|' + cmpDrawer.other"
      v-model:open="cmpDrawer.open" :rule-id="cmpDrawer.ruleId" :other="cmpDrawer.other"
      @resolved="onResolved" />
  </div>
</template>

<style scoped>
.rg-wrap { display: flex; flex-direction: column; gap: 0; }
.rg-toolbar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 0 0 10px; border-bottom: 1px solid #f0f0f0; margin-bottom: 10px;
}
.rg-sep { width: 1px; height: 18px; background: #f0f0f0; }
.rg-legend { display: flex; align-items: center; gap: 4px; }
.lg {
  font-size: 12px; color: rgba(0, 0, 0, .45); cursor: pointer;
  padding: 1px 8px; border-radius: 10px; border: 1px solid #f0f0f0;
  transition: all .15s;
}
.lg.on { color: rgba(0, 0, 0, .8); border-color: var(--c); }
.lg.on::before {
  content: ''; display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: var(--c); margin-right: 5px;
}
.lg:not(.on) { text-decoration: line-through; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.rg-conflict-legend { color: #ff4d4f; }

.rg-main {
  display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 12px;
  align-items: stretch;
}
.rg-canvas-col { position: relative; min-height: 520px; }
.rg-canvas { width: 100%; height: 540px; }
.rg-state { position: absolute; inset: 0; z-index: 5; display: flex;
  flex-direction: column; align-items: center; justify-content: center;
  background: rgba(255, 255, 255, .92); }

/* 对比侧栏（浮右上） */
.rg-cmp {
  position: absolute; top: 8px; right: 8px; width: 320px; max-height: 480px;
  overflow-y: auto; z-index: 6; background: #fff; border: 1px solid #e5e7eb;
  border-radius: 8px; padding: 10px 12px; box-shadow: 0 4px 14px rgba(0, 0, 0, .08);
}
.rg-cmp-head { display: flex; align-items: center; justify-content: space-between;
  font-size: 13px; margin-bottom: 6px; }
.rg-cmp-pair { display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  font-size: 12px; }
.rg-cmp-pair code { background: #f5f5f5; padding: 1px 6px; border-radius: 4px; }
.rg-diff-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.rg-diff-table td { border: 1px solid #f0f0f0; padding: 4px 8px; vertical-align: top;
  word-break: break-all; }
.rg-diff-table .dl { width: 76px; color: rgba(0, 0, 0, .45); background: #fafafa; }
.rg-diff-table tr.diffr td { background: #fffbe6; }

/* 侧栏详情 */
.rg-side {
  border: 1px solid #f0f0f0; border-radius: 8px; padding: 12px 14px;
  overflow-y: auto; min-height: 520px; max-height: 540px;
}
.rg-side-head { display: flex; align-items: center; gap: 8px; }
.rg-side-title { font-size: 14px; font-weight: 600; margin: 6px 0 8px; }
.rg-meta { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.rg-quote { background: #fafafa; border: 1px solid #f0f0f0; border-radius: 6px;
  padding: 6px 10px; margin-bottom: 10px; font-size: 12.5px; line-height: 1.7; }
.rg-note { max-height: 180px; overflow-y: auto; }
.rg-kv { display: grid; grid-template-columns: 72px 1fr; gap: 4px 8px;
  font-size: 12.5px; margin-bottom: 10px; }
.rg-kv b { color: rgba(0, 0, 0, .45); font-weight: 500; }
.rg-side-link { font-size: 12.5px; }
.rg-side-sec { font-size: 13px; font-weight: 600; margin: 10px 0 6px; }
.rg-rule-list { display: flex; flex-direction: column; gap: 4px; }
.rg-rule-item { display: flex; align-items: center; gap: 8px; font-size: 12px;
  padding: 3px 6px; border-radius: 4px; }
.rg-rule-item:hover { background: #fafafa; }
.rg-rule-item:nth-child(n+13) { display: none; }
.rg-rule-list.expanded .rg-rule-item:nth-child(n+13) { display: flex; }
.rg-rule-name { color: rgba(0, 0, 0, .65); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.rg-side-empty { padding-top: 24px; text-align: center; }
.rg-hints { list-style: none; padding: 0; margin: 10px 0 0; text-align: left;
  font-size: 12px; line-height: 2.1; }
</style>
