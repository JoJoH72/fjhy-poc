<script setup lang="ts">
/**
 * 审计追溯（2026-10-27 AntD 换装版）：S→D→A→F→I 竖向时间线。
 *
 * 立页之本：五张审计表**分开呈现**，不合成一条日志——
 * 「模型当时看到什么」（S）与「模型实际写了什么」（A）必须能分开追。
 * 时间线只是把这些表的行按阶段串起来读，数据一行没合并。
 *
 * 空阶段的语义不许含糊：F 无记录 = 尚无人工结论可比对；
 * I 无记录 = 该批次没有调参动作。都不是「没跑」。
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Async from '../components/Async.vue'
import { api } from '../api'

const route = useRoute()
const router = useRouter()
const billNo = ref(String(route.params.no || ''))
watch(() => route.params.no, (v) => { billNo.value = String(v || '') })
const key = computed(() => billNo.value)

function go() {
  if (billNo.value) router.push(`/review/trace/${billNo.value}`)
}

const traceData = ref<any>(null)
function exportPack() {
  if (!traceData.value || !key.value) return
  const blob = new Blob([JSON.stringify(traceData.value, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `审计包_${key.value}_${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(a.href)
}

const STAGES = ['S', 'D', 'A', 'F', 'I'] as const
type Stage = (typeof STAGES)[number]
const STAGE_NAME: Record<Stage, string> = {
  S: '感知 · 数据快照', D: '研判 · 判定周期', A: '落库 · 写入与信号',
  F: '比对 · 偏差回流', I: '改进 · 参数调整',
}
/* 2026-10-28 视觉评审 P0-5：五段五色收敛为蓝白基准——done 一律蓝系，
 * 仅 F（偏差）与 I（被拒调参）这两个「要人看」的状态位用状态色。 */
const STAGE_COLOR: Record<Stage, string> = {
  S: 'blue', D: 'blue', A: 'blue', F: 'blue', I: 'blue',
}

/** 对象值兜底格式化：后端 decision/expected/actual/bounded_by 是 JSON 对象，
 *  直接拼模板字符串会渲染成 [object Object]（P0-1）。 */
function objFmt(v: unknown): string {
  if (v == null) return '—'
  if (typeof v !== 'object') return String(v)
  const o = v as Record<string, unknown>
  // 常见语义键优先取值（conclusion / human / ai / target…），比裸 JSON 可读
  const prefer = ['conclusion', 'human', 'ai', 'label', 'name', 'text', 'value', 'target']
  for (const k of prefer) {
    if (o[k] != null) return String(o[k])
  }
  const kv = Object.entries(o).map(([k, x]) => `${k}=${typeof x === 'object' ? JSON.stringify(x) : String(x)}`)
  return kv.length ? kv.join(' ') : JSON.stringify(o)
}

/** ISO 时间戳统一 "MM-DD HH:mm"（P0-4：不再露 "10-24T16:19:47"） */
function tmFmt(t: unknown): string {
  const s = String(t || '')
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/)
  return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : (s.slice(5, 16).replace('T', ' ') || '—')
}

/** 把五张表的行摊成时间线节点。空阶段给一个 wait 节点 + 语义说明 */
function timeline(d: any): { stage: Stage; st: 'done' | 'wait'; title: string; desc: string; tags: string[]; ms?: string; tm: string }[] {
  const out: ReturnType<typeof timeline> = []
  const ms = (t: any) => (t == null ? '' : `${Math.round(t)} ms`)
  for (const s of d.S_snapshots || []) {
    out.push({ stage: 'S', st: 'done', title: STAGE_NAME.S,
      desc: `输入快照 ${String(s.snapshot_id).slice(0, 18)} · 脱敏${s.masked ? '通过' : '未通过（泄露）'} · 读取 ${Object.keys(s.row_counts || {}).length} 类表`,
      tags: ['data_pool_snapshot'], tm: tmFmt(s.taken_at) })
  }
  if (!(d.S_snapshots || []).length) out.push({ stage: 'S', st: 'wait', title: STAGE_NAME.S, desc: '无快照记录', tags: [], tm: '—' })

  for (const c of d.D_cycles || []) {
    const pt = c.phase_timings_ms || {}
    out.push({ stage: 'D', st: 'done', title: STAGE_NAME.D,
      desc: `${objFmt(c.trigger_kind)}触发 · 执行 ${objFmt(c.rule_executed)}/${objFmt(c.rule_candidates)} 条 · 结论 ${objFmt(c.decision)}`,
      tags: ['cycle_log'], ms: `S${ms(pt.S) || '—'} · D${ms(pt.D) || '—'} · A${ms(pt.A) || '—'} · F${ms(pt.F) || '—'}`,
      tm: tmFmt(c.started_at) })
  }
  if (!(d.D_cycles || []).length) out.push({ stage: 'D', st: 'wait', title: STAGE_NAME.D, desc: '无周期记录', tags: [], tm: '—' })

  for (const a of d.A_actions || []) {
    const wt = a.written_tables || {}
    out.push({ stage: 'A', st: 'done', title: STAGE_NAME.A,
      desc: `${objFmt(a.action)} ${objFmt(a.target)} · 写入 ${Object.entries(wt).map(([k, v]) => `${k}×${v}`).join(' ') || '无'}`,
      tags: ['agent_signal_log'], tm: tmFmt(a.emitted_at) })
  }
  if (!(d.A_actions || []).length) out.push({ stage: 'A', st: 'wait', title: STAGE_NAME.A, desc: '无动作记录', tags: [], tm: '—' })

  for (const f of d.F_feedback || []) {
    out.push({ stage: 'F', st: 'done', title: STAGE_NAME.F,
      desc: `${objFmt(f.metric)}：预期 ${objFmt(f.expected)} / 实际 ${objFmt(f.actual)} → ${f.deviation_kind ? objFmt(f.deviation_kind) : '无偏差'}`,
      tags: ['feedback_log'], tm: tmFmt(f.observed_at) })
  }
  if (!(d.F_feedback || []).length) out.push({ stage: 'F', st: 'wait', title: STAGE_NAME.F,
    desc: '尚无人工结论可比对（复核后回写）', tags: ['feedback_log'], tm: '待复核后' })

  for (const i of d.I_improvements || []) {
    out.push({ stage: 'I', st: i.rejected ? 'wait' : 'done', title: STAGE_NAME.I,
      desc: i.rejected
        ? `越界调参被拒绝：${objFmt(i.param)} → ${objFmt(i.new_value)}（${objFmt(i.rejected_why)}）`
        : `调整 ${objFmt(i.param)}：${objFmt(i.old_value)} → ${objFmt(i.new_value)}`,
      tags: ['improve_log'], tm: tmFmt(i.applied_at) })
  }
  if (!(d.I_improvements || []).length) out.push({ stage: 'I', st: 'wait', title: STAGE_NAME.I,
    desc: '本批次无调参动作（I 按批次调参，不按单）', tags: ['improve_log'], tm: '按批次' })
  return out
}
</script>

<template>
  <div class="at">
    <a-card size="small" class="searchbar">
      <div class="sb-in">
        <a-input v-model:value="billNo" placeholder="单据编号，如 FJPG-BX2610132121"
                 style="width: 300px" @keyup.enter="go" />
        <a-button type="primary" @click="go">追溯</a-button>
        <div class="grow"></div>
        <a-button :disabled="!traceData" @click="exportPack">导出审计包</a-button>
      </div>
    </a-card>

    <Async v-if="key" :load="() => api.trace(key)" :deps="key">
      <template #default="{ data }">
        {{ void (traceData = data) }}
        <a-card size="small" style="margin-top: 16px">
          <a-timeline class="tl">
            <a-timeline-item v-for="(n, i) in timeline(data)" :key="i"
              :color="n.st === 'done' ? STAGE_COLOR[n.stage] : 'gray'">
              <div class="tl-item">
                <div class="l1">
                  <a-tag :color="n.st === 'done' ? STAGE_COLOR[n.stage] : 'default'">{{ n.stage }} · {{ n.title }}</a-tag>
                  <span v-if="n.ms" class="dim">{{ n.ms }}</span>
                  <span class="tm">{{ n.tm }}</span>
                </div>
                <div class="desc" :class="{ empty: n.st === 'wait' }">{{ n.desc }}</div>
              </div>
            </a-timeline-item>
          </a-timeline>
        </a-card>
        <!-- 2026-10-28 P0-4：整段说明书（含 per_cycle=false 内联代码）收进 tooltip，
             卡片只留名词短语——实现层标识不上界面 -->
        <a-card v-if="(data as any).I_scope_note" size="small" style="margin-top: 16px">
          <a-tooltip placement="topLeft" :overlay-style="{ maxWidth: '460px' }">
            <template #title>
              <div class="scope-note">{{ String((data as any).I_scope_note).replace(/\*\*/g, '') }}</div>
            </template>
            <span class="scope-line">I 阶段口径：按批次调参，不按单</span>
          </a-tooltip>
        </a-card>
      </template>
    </Async>
    <a-card v-else size="small" style="margin-top: 16px">
      <a-empty style="padding: 40px 0" description="输入单据编号开始追溯" />
    </a-card>
  </div>
</template>

<style scoped>
.sb-in { display: flex; align-items: center; gap: 8px; }
.grow { flex: 1; }
.tl { margin-top: 8px; padding-left: 4px; }
.l1 { display: flex; align-items: center; gap: 10px; }
.tm { font-size: 12px; color: rgba(0, 0, 0, .45); font-variant-numeric: tabular-nums; }
.desc { font-size: 13px; color: rgba(0, 0, 0, .75); margin-top: 2px; }
.desc.empty { color: rgba(0, 0, 0, .4); }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); font-variant-numeric: tabular-nums; }
.scope-line { font-size: 12.5px; color: rgba(0, 0, 0, .65); cursor: help;
  border-bottom: 1px dashed rgba(0, 0, 0, .25); }
.scope-note { font-size: 12px; color: rgba(255, 255, 255, .85); line-height: 1.7; }
</style>
