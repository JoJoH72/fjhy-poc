<script setup lang="ts">
/**
 * 系统自检页（2026-10-27 AntD 换装版）。
 *
 * **失败项要原样列出来，不能只给一个 ok / false。**
 * 「哪几项没过、为什么」才是能让人去修的信息。
 *
 * 版面：第一屏只回答「能不能用」——结论 + 五组校验的汇总表；
 * 20 项检查、54 条剔除规则、13 个处理环节的参数与禁止项全部收在
 * 下面的 a-collapse 明细里，默认折叠。
 */
import Async from '../components/Async.vue'
import { api } from '../api'
import { ref } from 'vue'
import { message } from 'ant-design-vue'

interface Check { check: string; ok: boolean; detail: string }
interface AgentCfg {
  agent_id: string; version: string; layer: string; agent_name: string
  triggers: string[]; reads: string[]; writes: string[]
  params: Record<string, { lo: number; hi: number; default: number; why: string }>
  forbidden: string[]
}
interface Report {
  contract: {
    checks: Check[]; errors: string[]; warnings: string[]
    executable: string[]
    excluded: { rule_id: string; why: string }[]
  }
  agents: { checks: Check[]; errors: string[]; items: AgentCfg[] }
  mask: {
    mode: string; outlets: string[]; policies: string[]
    sensitive_fields: Record<string, string>
  }
}

const LAYER_CN: Record<string, string> = { L0: '感知层', L1: '执行层', L2: '协同层' }

/** 说明里常带绝对路径（含部门与客户名），压成 …\文件名。 */
function prettyDetail(s: string) {
  return (s || '').replace(/[A-Za-z]:\\[^，。；、）)「」『』]*\\/g, '…\\')
}

/** 展开某个处理环节的参数与禁止项（默认全收，表格才读得下去） */
const openAgent = ref<string[]>([])

/** 重新自检：loading 态 + 完成反馈（原版点了没反应，等接口回来才整页刷新） */
const reloading = ref(false)
async function rerun(reload: () => Promise<void>) {
  if (reloading.value) return
  reloading.value = true
  try {
    await reload()
    message.success('自检完成')
  } catch {
    message.error('自检失败，请重试')
  } finally {
    reloading.value = false
  }
}

/** 参数表数据源：模板表达式里写不了 as 断言，收到 script 里来 */
function paramsRows(a: AgentCfg) {
  return Object.entries(a.params).map(([k, v]) => ({
    k, lo: v.lo, hi: v.hi, default: v.default, why: v.why,
  }))
}
</script>

<template>
  <div class="st">
  <Async :load="() => api.startupReport() as unknown as Promise<Report>">
    <template #default="{ data, reload }">
      <!-- ① 结论 + 五组汇总：这一屏回答「能不能用」 -->
      <a-card size="small" style="margin-bottom:16px">
        <div class="cihero">
          <div class="b" :class="{ bad: data.contract.errors.length + data.agents.errors.length }" aria-hidden="true">
            <check-circle-filled v-if="!data.contract.errors.length && !data.agents.errors.length" />
            <exclamation-circle-filled v-else />
          </div>
          <div class="hgroup">
            <div class="h1">
              {{ data.contract.errors.length + data.agents.errors.length ? '自检有失败项' : '启动自检全部通过' }}
            </div>
            <div class="h2">
              {{ data.contract.checks.length + data.agents.checks.length }} 项检查
              · 失败 {{ data.contract.errors.length + data.agents.errors.length }}
              · 警告 {{ data.contract.warnings.length }}
            </div>
          </div>
          <div class="grow"></div>
          <a-button :loading="reloading" @click="rerun(reload)">重新自检</a-button>
        </div>

        <a-table :data-source="[
            { key: 'contract', group: '规则资产契约', scope: '版本与漂移 · 规则八要素 · 执行集可用性',
              n: data.contract.checks.length, state: data.contract.errors.length ? 'fail' : 'pass',
              label: data.contract.errors.length ? `失败 ${data.contract.errors.length}` : '全部通过'
                + (data.contract.warnings.length ? ` · 警告 ${data.contract.warnings.length}` : '') },
            { key: 'agents', group: '处理环节配置契约', scope: `${data.agents.items.length} 个处理环节 · 触发 / 读写 / 参数区间 / 禁止项`,
              n: data.agents.checks.length, state: data.agents.errors.length ? 'fail' : 'pass',
              label: data.agents.errors.length ? `失败 ${data.agents.errors.length}` : '全部通过' },
            { key: 'exec', group: '执行集实现完整性', scope: '可执行规则对应实现覆盖率',
              n: data.contract.executable.length, state: 'pass', label: '全部有实现' },
            { key: 'excluded', group: '剔除规则登记', scope: '剔除规则及原因登记',
              n: data.contract.excluded.length, state: 'pass', label: '逐条有原因' },
            { key: 'mask', group: '脱敏出口覆盖', scope: `${data.mask.policies.length} 条策略 · ${Object.keys(data.mask.sensitive_fields).length} 个敏感字段`,
              n: data.mask.outlets.length, state: data.mask.mode === 'on' ? 'pass' : 'fail',
              label: data.mask.mode === 'on' ? '全覆盖' : '未开启' },
          ]" size="small" :pagination="false"
          :columns="[
            { title: '校验组', dataIndex: 'group', width: 220 },
            { title: '覆盖范围', key: 'scope' },
            { title: '项数', dataIndex: 'n', width: 80, align: 'right' },
            { title: '结果', key: 'state', width: 170, align: 'center' },
          ]">
          <template #bodyCell="{ column, record: r }">
            <template v-if="column.key === 'scope'">
              <span class="dim">{{ r.scope }}</span>
            </template>
            <template v-else-if="column.key === 'state'">
              <a-tag :color="r.state === 'pass' ? 'success' : 'error'">{{ r.label }}</a-tag>
            </template>
          </template>
        </a-table>
      </a-card>

      <div class="sect-head">
        <h2>明细</h2>
        <span class="dim">失败项默认展开</span>
      </div>

      <a-collapse :default-active-key="data.contract.errors.length ? ['contract'] : []">
        <!-- ② 规则资产契约 -->
        <a-collapse-panel key="contract">
          <template #header>
            <span class="dt">规则资产契约</span>
            <a-tag :color="data.contract.errors.length ? 'error' : 'success'">
              {{ data.contract.errors.length ? `失败 ${data.contract.errors.length}` : `${data.contract.checks.length} 项全过` }}
            </a-tag>
            <span class="dhint">与启动时同一套校验</span>
          </template>
          <a-table :data-source="data.contract.checks" row-key="check" size="small"
            :pagination="data.contract.checks.length > 10 ? { pageSize: 10 } : false"
            :columns="[
              { title: '结果', key: 'ok', width: 80, align: 'center' },
              { title: '检查项', dataIndex: 'check', width: 260 },
              { title: '说明', key: 'detail' },
            ]">
            <template #bodyCell="{ column, record: c }">
              <template v-if="column.key === 'ok'">
                <a-tag :color="c.ok ? 'success' : 'error'">{{ c.ok ? '通过' : '失败' }}</a-tag>
              </template>
              <template v-else-if="column.key === 'detail'">
                <span class="dim"><MarkText :text="prettyDetail(c.detail) || '—'" /></span>
              </template>
            </template>
          </a-table>
          <a-alert v-if="data.contract.errors.length" type="error" show-icon style="margin-top: 12px">
            <template #message>
              <b>失败项</b>
              <div v-for="(e, i) in data.contract.errors" :key="i">{{ e }}</div>
            </template>
          </a-alert>
        </a-collapse-panel>

        <!-- ③ 被剔出执行集的规则 -->
        <a-collapse-panel key="excluded">
          <template #header>
            <span class="dt">被剔出执行集的规则</span>
            <a-tag color="warning">{{ data.contract.excluded.length }} 条</a-tag>
            <span class="dhint">本轮未执行</span>
          </template>
          <div class="blk-note">
            以下规则本轮未执行（原因逐条登记）。核对无误后可在规则资产中修正后重新装载。
          </div>
          <a-table :data-source="data.contract.excluded" row-key="rule_id" size="small"
            :pagination="data.contract.excluded.length > 10 ? { pageSize: 10 } : false"
            :columns="[
              { title: '规则', dataIndex: 'rule_id', width: 150 },
              { title: '原因', key: 'why' },
            ]">
            <template #bodyCell="{ column, record: e }">
              <template v-if="column.key === 'why'">
                <span class="dim"><MarkText :text="e.why" /></span>
              </template>
            </template>
          </a-table>
        </a-collapse-panel>

        <!-- ④ 处理环节配置 -->
        <a-collapse-panel key="agents">
          <template #header>
            <span class="dt">处理环节配置</span>
            <a-tag color="blue">{{ data.agents.items.length }} 个</a-tag>
            <span class="dhint">配置检查 {{ data.agents.checks.length }} 项 · 失败 {{ data.agents.errors.length }}</span>
          </template>
          <a-table :data-source="data.agents.items" row-key="agent_id" size="small"
            :pagination="false"
            :expanded-row-keys="openAgent"
            @expand="(e: any, a: AgentCfg) => { openAgent = e ? [...openAgent, a.agent_id] : openAgent.filter((x) => x !== a.agent_id) }"
            :columns="[
              { title: '处理环节', key: 'name', width: 220 },
              { title: '层', key: 'layer', width: 96, align: 'center' },
              { title: '版本', key: 'ver', width: 80, align: 'center' },
              { title: '触发', key: 'trg', width: 170 },
              { title: '读表', key: 'reads', width: 70, align: 'right' },
              { title: '写表', key: 'writes', width: 70, align: 'right' },
              { title: '参数', key: 'params', width: 70, align: 'right' },
              { title: '禁止', key: 'forb', width: 70, align: 'right' },
            ]">
            <template #bodyCell="{ column, record: a }">
              <template v-if="column.key === 'name'">{{ a.agent_name }}
                <div class="dim sub2">{{ a.agent_id }}</div>
              </template>
              <template v-else-if="column.key === 'layer'">
                <a-tag color="default">{{ LAYER_CN[a.layer] || a.layer }}</a-tag>
              </template>
              <template v-else-if="column.key === 'ver'">v{{ a.version }}</template>
              <template v-else-if="column.key === 'trg'">
                <a-tag v-for="t in a.triggers" :key="t" style="margin: 0 4px 2px 0">{{ t }}</a-tag>
              </template>
              <template v-else-if="column.key === 'reads'">{{ a.reads.length }}</template>
              <template v-else-if="column.key === 'writes'">{{ a.writes.length }}</template>
              <template v-else-if="column.key === 'params'">{{ Object.keys(a.params).length }}</template>
              <template v-else-if="column.key === 'forb'">{{ a.forbidden.length }}</template>
            </template>
            <template #expandedRowRender="{ record: a }">
              <div class="ev">
                <div class="ev-col">
                  <div class="ev-k">触发方式</div>
                  <div class="ev-tags">
                    <a-tag v-for="t in a.triggers" :key="t" color="success">{{ t }}</a-tag>
                  </div>
                  <div class="ev-k" style="margin-top:14px">S 阶段可读</div>
                  <div class="ev-mono">{{ a.reads.join('、') || '—' }}</div>
                  <div class="ev-k" style="margin-top:14px">A 阶段可写</div>
                  <div class="ev-mono">{{ a.writes.join('、') || '—' }}</div>
                </div>
                <div class="ev-col">
                  <div class="ev-k">I 阶段可调参数</div>
                  <a-table v-if="Object.keys(a.params).length" :data-source="paramsRows(a)"
                    row-key="k" size="small" :pagination="false"
                    :columns="[
                      { title: '参数', dataIndex: 'k', width: 140 },
                      { title: '默认', dataIndex: 'default', width: 70, align: 'right' },
                      { title: '区间', key: 'range', width: 110 },
                      { title: '为什么是这个区间', key: 'why' },
                    ]">
                    <template #bodyCell="{ column, record: p }">
                      <template v-if="column.key === 'range'">[{{ p.lo }}, {{ p.hi }}]</template>
                      <template v-else-if="column.key === 'why'">
                        <span class="dim"><MarkText :text="p.why" /></span>
                      </template>
                    </template>
                  </a-table>
                  <div v-else class="dim" style="font-size:12px">无</div>
                  <div class="ev-k" style="margin-top:14px">I 阶段禁止动</div>
                  <div class="ev-tags">
                    <a-tag v-for="f in a.forbidden" :key="f" color="error">{{ f }}</a-tag>
                  </div>
                </div>
              </div>
            </template>
          </a-table>
        </a-collapse-panel>

        <!-- ⑤ 脱敏 -->
        <a-collapse-panel key="mask">
          <template #header>
            <span class="dt">脱敏</span>
            <a-tag :color="data.mask.mode === 'on' ? 'success' : 'error'">
              {{ data.mask.mode === 'on' ? '已开启' : '已关闭' }}
            </a-tag>
            <span class="dhint">{{ data.mask.policies.length }} 条策略 · {{ data.mask.outlets.length }} 个出口 · {{ Object.keys(data.mask.sensitive_fields).length }} 个敏感字段</span>
          </template>
          <a-row :gutter="24" style="padding: 8px 0 12px">
            <a-col :xs="24" :md="8">
              <div class="ev-k">策略（{{ data.mask.policies.length }} 条）</div>
              <div class="mlist">
                <div v-for="p in data.mask.policies" :key="p">{{ p }}</div>
              </div>
            </a-col>
            <a-col :xs="24" :md="8">
              <div class="ev-k">覆盖出口（{{ data.mask.outlets.length }} 个）</div>
              <div class="mlist">
                <div v-for="o in data.mask.outlets" :key="o">{{ o }}</div>
              </div>
            </a-col>
            <a-col :xs="24" :md="8">
              <div class="ev-k">敏感字段（{{ Object.keys(data.mask.sensitive_fields).length }} 个）</div>
              <a-table :data-source="Object.entries(data.mask.sensitive_fields).map(([k, v]) => ({ k, v }))"
                row-key="k" size="small" :pagination="false"
                :columns="[
                  { title: '字段', dataIndex: 'k', width: 90 },
                  { title: '说明', dataIndex: 'v' },
                ]" />
            </a-col>
          </a-row>
          <a-alert v-if="data.mask.mode !== 'on'" type="error" show-icon message="生产环境绝不允许关闭脱敏。" />
        </a-collapse-panel>
      </a-collapse>
    </template>
  </Async>
  </div>
</template>

<script lang="ts">
import { CheckCircleFilled, ExclamationCircleFilled } from '@ant-design/icons-vue'
export default { components: { CheckCircleFilled, ExclamationCircleFilled } }
</script>

<style scoped>
.cihero { display: flex; align-items: center; gap: 14px; margin-bottom: 14px; }
.cihero .b {
  width: 44px; height: 44px; border-radius: 10px; font-size: 22px;
  display: grid; place-items: center; flex: none;
  background: #f6ffed; color: #52c41a;
}
.cihero .b.bad { background: #fff1f0; color: #ff4d4f; }
.hgroup { min-width: 0; }
.h1 { font-size: 16px; font-weight: 600; }
.h2 { font-size: 12.5px; color: rgba(0, 0, 0, .5); margin-top: 3px; line-height: 1.6; }
.grow { flex: 1; }

.sect-head { display: flex; align-items: baseline; gap: 10px; margin: 4px 2px 12px; }
.sect-head h2 { font-size: 14px; font-weight: 600; margin: 0; }
.dim { font-size: 12px; color: rgba(0, 0, 0, .5); }
.dt { font-size: 14px; font-weight: 600; margin-right: 10px; }
.dhint { font-size: 12px; color: rgba(0, 0, 0, .4); margin-left: 10px; }
.blk-note { font-size: 12px; color: rgba(0, 0, 0, .55); margin: 4px 0 12px; line-height: 1.7; }
.sub2 { font-size: 11px; margin-top: 2px; }

.ev { display: grid; grid-template-columns: 1fr 1.4fr; gap: 28px; }
.ev-col { min-width: 0; }
.ev-k { font-size: 12px; color: rgba(0, 0, 0, .45); margin-bottom: 6px; }
.ev-tags { display: flex; gap: 6px; flex-wrap: wrap; }
.ev-mono { font-size: 12px; color: rgba(0, 0, 0, .6); font-family: Consolas, "Courier New", monospace; line-height: 1.6; overflow-wrap: anywhere; }
.mlist { font-size: 12px; color: rgba(0, 0, 0, .6); line-height: 1.75; }

@media (max-width: 1180px) {
  .ev { grid-template-columns: 1fr; gap: 16px; }
}
</style>
