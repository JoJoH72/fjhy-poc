<script setup lang="ts">
/**
 * 单条规则详情（2026-10-27 AntD 换装版）：八要素 + 概念绑定 + 可得性 + 最近命中。
 *
 * **概念的可得性要和规则并排显示。** 一条规则「为什么跑不了」的答案
 * 几乎总是「它引用的某个字段取不到」，而那个字段叫什么、属于哪个缺口，
 * 是让业务侧去补数据的唯一依据。
 *
 * 2026-10-27 P0-1：conflicts_with 关系行可点开「冲突对比与裁决」抽屉——
 * 客户当场投诉「冲突标注点不开，看不到两条规则的对比原文」。
 */
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import Async from '../components/Async.vue'
import Outcome from '../components/Outcome.vue'
import ConflictCompareDrawer from '../components/ConflictCompareDrawer.vue'
import { api } from '../api'

const route = useRoute()
const id = computed(() => String(route.params.id))

const AVAIL_OK = new Set(['样本可得', '样本可得(审批意见)', '已录入资产', '可推导'])

/** 冲突对比抽屉（P0-1） */
const cmpOpen = ref(false)
const cmpOther = ref('')
const cmpKey = ref(0)
const openCompare = (other: string) => {
  cmpOther.value = other
  cmpKey.value += 1
  cmpOpen.value = true
}

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

/** 裁决完成后重取详情：relations 的 needs_confirm 标签要当场变掉 */
const detailRef = ref<{ reload: () => void } | null>(null)
const reloadDetail = () => detailRef.value?.reload()
</script>

<template>
  <div class="rd">
  <Async ref="detailRef" :load="() => api.rule(id)" :deps="id">
    <template #default="{ data }">
      <div class="rd-head">
        <h2 class="rd-title">{{ (data as any).rule_id }} {{ (data as any).rule_name }}</h2>
        <router-link to="/asset/rules-lib">← 回规则库</router-link>
      </div>

      <a-card size="small" class="blk" title="八要素">
        <div class="kv">
          <b>① 规则名称</b><span>{{ (data as any).rule_name }}</span>
          <b>② 审核维度</b><span>{{ (data as any).dimension }}</span>
          <b>③ 风险类别</b><span>{{ (data as any).risk_class }}</span>
          <b>④ 来源条款</b>
          <span>
            <template v-if="(data as any).source_clause">
              <code class="clause">{{ (data as any).source_clause.file }}</code>
              {{ (data as any).source_clause.clause }}
              <a-tag :color="(data as any).source_clause.confidence === 'OCR' ? 'warning' : 'success'"
                style="margin-left: 4px">{{ (data as any).source_clause.confidence }}</a-tag>
              <div class="dim" style="margin-top: 3px">「{{ (data as any).source_clause.text }}」</div>
            </template>
          </span>
          <b>⑤ 适用范围</b>
          <span><pre class="json">{{ JSON.stringify((data as any).scope, null, 1) }}</pre></span>
          <b>⑥ 判定逻辑</b>
          <span><pre class="json">{{ JSON.stringify((data as any).logic, null, 1) }}</pre></span>
          <b>⑦ 例外</b><span>{{ (data as any).exception || '无' }}</span>
          <b>⑧ 优先级</b><span>{{ (data as any).priority }}</span>
          <b>责任部门</b><span>{{ (data as any).owner || '—' }}</span>
          <b>生效期</b>
          <span>{{ (data as any).effective_from || '—' }} ~ {{ (data as any).effective_to || '长期' }}</span>
        </div>
      </a-card>

      <a-card size="small" class="blk" title="对齐客户配置器">
        <div class="kv">
          <b>算法编码</b><span>{{ (data as any).algo_code || '—' }}</span>
          <b>算法类型</b><span>{{ (data as any).algo_type || '—' }}</span>
          <b>控制类型</b>
          <span>
            <a-tag :color="(data as any).control_type === '禁止' ? 'error' : 'warning'">
              {{ (data as any).control_type }}
            </a-tag>
          </span>
          <b>配置器状态</b><span>{{ (data as any).configurator_status || '—' }}</span>
          <b>客户自评可实现性</b><span>{{ (data as any).feasibility || '未评估' }}</span>
          <b>实现途径（原文）</b><span>{{ (data as any).impl_path || '—' }}</span>
        </div>
      </a-card>

      <a-card size="small" class="blk" title="可执行性">
        <div class="kv">
          <b>规则资产层面</b>
          <span>
            <a-tag :color="(data as any).compiled ? 'success' : 'default'">
              {{ (data as any).compiled ? '已编译' : '未编译' }}
            </a-tag>
            <span v-if="(data as any).compiled_reason" class="dim" style="margin-left: 6px">
              <MarkText :text="(data as any).compiled_reason" />
            </span>
          </span>
          <b>当前数据能否跑</b>
          <span>
            <a-tag :color="(data as any).demo_executable ? 'success' : 'warning'">
              {{ (data as any).demo_executable ? '在执行集' : '不在执行集' }}
            </a-tag>
            <span v-if="(data as any).demo_scope" class="dim" style="margin-left: 6px">
              {{ (data as any).demo_scope }}
            </span>
          </span>
          <template v-if="(data as any).demo_blocker">
            <b>阻塞原因</b>
            <span class="blk-note">{{ (data as any).demo_blocker }}</span>
          </template>
          <template v-if="(data as any).logic?.degraded?.cannot_hit_without">
            <b>结构性无法命中</b>
            <span class="blk-err">
              缺 <code>{{ (data as any).logic.degraded.cannot_hit_without.join(', ') }}</code> 时本条永远不会命中。
              <div style="margin-top: 4px">
                现在实际退化成：<b>{{ (data as any).logic.degraded.degenerates_to }}</b>
              </div>
              <div class="dim" style="margin-top: 4px">
                <MarkText :text="(data as any).logic.degraded.cannot_hit_why" />
              </div>
            </span>
          </template>
          <template v-if="(data as any).logic?.rule_effect === 'exempt'">
            <b>规则作用</b>
            <span class="blk-note">
              <b>豁免型</b>：命中 = 豁免生效，按设计不产生违规告警。
              <div class="dim"><MarkText :text="(data as any).logic.rule_effect_why" /></div>
            </span>
          </template>
        </div>
      </a-card>

      <a-card size="small" class="blk" title="引用的本体概念">
        <a-table :data-source="(data as any).concepts" row-key="concept_id" size="small"
          :pagination="(data as any).concepts?.length > 10 ? { pageSize: 10 } : false"
          :columns="[
            { title: '概念', dataIndex: 'concept_id', width: 130 },
            { title: '名称', dataIndex: 'concept_name', width: 180 },
            { title: '所属实体', dataIndex: 'entity_id', width: 120 },
            { title: '可得性', key: 'avail', width: 160 },
            { title: '缺口', dataIndex: 'gap_id', width: 110 },
          ]">
          <template #bodyCell="{ column, record: c }">
            <template v-if="column.key === 'avail'">
              <a-tag :color="AVAIL_OK.has(c.availability) ? 'success' : 'warning'">{{ c.availability }}</a-tag>
            </template>
            <template v-else-if="column.dataIndex === 'gap_id'">{{ c.gap_id || '—' }}</template>
          </template>
          <template #emptyText>
            <a-empty :image-style="{ height: '40px' }" description="未引用概念" />
          </template>
        </a-table>
        <div v-if="(data as any).entities?.length" style="margin-top: 12px">
          <b>存在性判断引用的实体</b>
          <div class="dim">判断「附件/对象是否存在」，区别于上表「字段取值」类概念引用</div>
          <a-table :data-source="(data as any).entities" row-key="entity_id" size="small"
            :pagination="false" style="margin-top: 6px"
            :columns="[
              { title: '实体', dataIndex: 'entity_id', width: 90 },
              { title: '语义', dataIndex: 'semantics' },
            ]" />
        </div>
      </a-card>

      <a-card v-if="(data as any).relations?.length" size="small" class="blk" title="与其他规则的关系">
        <a-table :data-source="(data as any).relations" :row-key="rowKeyOf"
          size="small" :pagination="false"
          :custom-row="(r: any) => r.rel_type === 'conflicts_with'
            ? { style: { cursor: 'pointer' }, onClick: () => openCompare(r.other_rule) } : {}"
          :row-class-name="(r: any) => r.rel_type === 'conflicts_with' ? 'row-conflict' : ''"
          :columns="[
            { title: '关系', dataIndex: 'rel_type', width: 130 },
            { title: '对方', key: 'other', width: 130 },
            { title: '需裁决', key: 'confirm', width: 130, align: 'center' },
            { title: '缺口', dataIndex: 'gap_id', width: 110 },
            { title: '说明', key: 'note' },
          ]">
          <template #bodyCell="{ column, record: r }">
            <template v-if="column.dataIndex === 'rel_type'">
              <a-tag v-if="r.rel_type === 'conflicts_with'" color="error">冲突</a-tag>
              <span v-else>{{ r.rel_type }}</span>
            </template>
            <template v-else-if="column.key === 'other'">
              <a v-if="r.rel_type === 'conflicts_with'" @click.stop="openCompare(r.other_rule)"
                title="点开左右对比两条口径的原文">{{ r.other_rule }}</a>
              <span v-else>{{ r.other_rule }}</span>
            </template>
            <template v-else-if="column.key === 'confirm'">
              <a-tag v-if="r.needs_confirm" color="warning"
                @click="r.rel_type === 'conflicts_with' && openCompare(r.other_rule)">
                需业务侧裁决{{ r.rel_type === 'conflicts_with' ? ' ·' : '' }}
              </a-tag>
              <span v-else class="dim">—</span>
            </template>
            <template v-else-if="column.dataIndex === 'gap_id'">{{ r.gap_id || '—' }}</template>
            <template v-else-if="column.key === 'note'">
              <span class="dim"><MarkText :text="(r.note || '').slice(0, 200)" /></span>
            </template>
          </template>
        </a-table>
        <div class="dim" style="margin-top: 6px">
          「冲突」行可点开对比：原文并排、差异高亮，裁决后留痕
        </div>
      </a-card>

      <a-card size="small" class="blk" title="最近的判定">
        <a-empty v-if="!(data as any).recent_findings?.length"
          :image-style="{ height: '40px' }" description="暂无判定记录" />
        <a-table v-else :data-source="(data as any).recent_findings"
          :row-key="rowKeyOf" size="small"
          :pagination="(data as any).recent_findings.length > 10 ? { pageSize: 10 } : false"
          :columns="[
            { title: '单据', key: 'bill', width: 220 },
            { title: '结果', key: 'outcome', width: 100, align: 'center' },
            { title: '说明', dataIndex: 'message' },
          ]">
          <template #bodyCell="{ column, record: f }">
            <template v-if="column.key === 'bill'">
              <router-link :to="`/review/report/${f.run_id}`">{{ f.bill_no }}</router-link>
            </template>
            <template v-else-if="column.key === 'outcome'">
              <Outcome :outcome="f.outcome" />
            </template>
          </template>
        </a-table>
      </a-card>

      <a-card v-if="(data as any).note" size="small" class="blk" title="维护备注">
        <div style="white-space: pre-wrap">{{ (data as any).note }}</div>
      </a-card>
    </template>
  </Async>

  <ConflictCompareDrawer :key="cmpKey" v-model:open="cmpOpen" :rule-id="id" :other="cmpOther"
    @resolved="reloadDetail" />
  </div>
</template>

<style scoped>
.rd-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 12px; }
.rd-title { font-size: 18px; font-weight: 600; margin: 0; }
.blk { margin-bottom: 16px; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .45); }
.clause { background: #fafafa; padding: 1px 6px; border-radius: 4px; }
.blk-note { font-size: 12.5px; color: rgba(0, 0, 0, .65); }
.blk-err { font-size: 12.5px; color: #cf1322; }
.json {
  margin: 0; font-size: 12px; background: #fafafa; border-radius: 6px;
  padding: 8px 10px; overflow-x: auto; max-width: 640px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
}
.kv { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 0 32px; }
.kv b { display: inline-block; width: 130px; color: rgba(0, 0, 0, .45); font-weight: 500; font-size: 13px; padding: 7px 0; vertical-align: top; }
.kv > span { font-size: 13px; padding: 7px 0; display: inline-block; min-width: 0; }
:deep(.row-conflict) td { background: #fff1f0; }
:deep(.row-conflict:hover) td { background: #ffccc7; }
</style>
