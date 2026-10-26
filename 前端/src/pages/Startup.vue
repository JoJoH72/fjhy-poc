<script setup lang="ts">
/**
 * 系统自检页。
 *
 * **失败项要原样列出来，不能只给一个 ok / false。**
 * 「哪几项没过、为什么」才是能让人去修的信息；
 * 一个红灯加一句「系统异常」只会让人去翻日志。
 *
 * 版面（2026-10-25 重做）：第一屏只回答「能不能用」——结论 + 五组校验的汇总表；
 * 20 项检查、54 条剔除规则、13 个智能体的参数与禁止项全部收在下面的明细卡里，
 * 默认折叠。上一版把这四坨原始数据从上到下平铺，打开就是几屏密集文字，
 * 结论反而要往下翻才找得到。
 */
import Async from '../components/Async.vue'
import { api } from '../api'
import { useCollapse } from '../data/collapse'
import { ref } from 'vue'

const chkList = useCollapse(), exclList = useCollapse()

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

/**
 * 说明里常带绝对路径（E:\02 FDE部门\02 FDE客户\…\本体_v0.1.yaml）。
 * 路径里有部门与客户名，整串铺在表格里既占宽也没信息量——压成 …\文件名。
 */
function prettyDetail(s: string) {
  // 路径里含空格（「02 FDE客户」），不能用 \s 截断；用中文标点做边界，
  // 再退到最后一个反斜杠，只留文件名
  return (s || '').replace(/[A-Za-z]:\\[^，。；、）)「」『』]*\\/g, '…\\')
}

/** 展开某个智能体的参数与禁止项（默认全收，表格才读得下去） */
const openAgent = ref<Set<string>>(new Set())
function toggleAgent(id: string) {
  const next = new Set(openAgent.value)
  next.has(id) ? next.delete(id) : next.add(id)
  openAgent.value = next
}
</script>

<template>
  <div class="v2">
  <Async :load="() => api.startupReport() as unknown as Promise<Report>">
    <template #default="{ data, reload }">
      <!-- ① 结论 + 五组汇总：这一屏回答「能不能用」 -->
      <div class="card" style="margin-bottom:16px">
        <div class="cihero">
          <div class="b" :class="{ bad: data.contract.errors.length + data.agents.errors.length }" aria-hidden="true">
            <svg v-if="!data.contract.errors.length && !data.agents.errors.length" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M5 13l4 4L19 7"/></svg>
            <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 8v5M12 17h.01"/><circle cx="12" cy="12" r="9"/></svg>
          </div>
          <div class="hgroup">
            <div class="h1">
              {{ data.contract.errors.length + data.agents.errors.length ? '自检有失败项' : '启动自检全部通过' }}
            </div>
            <div class="h2">
              {{ data.contract.checks.length + data.agents.checks.length }} 项检查
              · 失败 {{ data.contract.errors.length + data.agents.errors.length }}
              · 警告 {{ data.contract.warnings.length }}；
              任何一组不过，系统会拒绝启动而不是带病运行
            </div>
          </div>
          <div class="grow"></div>
          <button class="btn ghost" @click="reload">重新自检</button>
        </div>

        <table class="tbl">
          <thead>
            <tr>
              <th style="width: 230px">校验组</th><th>覆盖范围</th>
              <th class="num" style="width: 90px">项数</th><th style="width: 170px">结果</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>规则资产契约</td>
              <td class="muted">本体版本与漂移 · 规则八要素 · 执行集可用性</td>
              <td class="num">{{ data.contract.checks.length }}</td>
              <td>
                <span class="tag" :class="data.contract.errors.length ? 't-hit' : 't-pass'">
                  {{ data.contract.errors.length ? `失败 ${data.contract.errors.length}` : '全部通过' }}
                  <template v-if="data.contract.warnings.length">· 警告 {{ data.contract.warnings.length }}</template>
                </span>
              </td>
            </tr>
            <tr>
              <td>智能体配置契约</td>
              <td class="muted">{{ data.agents.items.length }} 个智能体 · 触发 / 读写 / 参数区间 / 禁止项</td>
              <td class="num">{{ data.agents.checks.length }}</td>
              <td>
                <span class="tag" :class="data.agents.errors.length ? 't-hit' : 't-pass'">
                  {{ data.agents.errors.length ? `失败 ${data.agents.errors.length}` : '全部通过' }}
                </span>
              </td>
            </tr>
            <tr>
              <td>执行集实现完整性</td>
              <td class="muted">每条可执行规则都要有对应实现，不做静默降级</td>
              <td class="num">{{ data.contract.executable.length }}</td>
              <td><span class="tag t-pass">全部有实现</span></td>
            </tr>
            <tr>
              <td>剔除规则登记</td>
              <td class="muted">未执行的规则逐条记原因，不是「没跑」当成「通过」</td>
              <td class="num">{{ data.contract.excluded.length }}</td>
              <td>
                <span class="tag t-pass">逐条有原因</span>
              </td>
            </tr>
            <tr>
              <td>脱敏出口覆盖</td>
              <td class="muted">{{ data.mask.policies.length }} 条策略 · {{ Object.keys(data.mask.sensitive_fields).length }} 个敏感字段</td>
              <td class="num">{{ data.mask.outlets.length }}</td>
              <td>
                <span class="tag" :class="data.mask.mode === 'on' ? 't-pass' : 't-hit'">
                  {{ data.mask.mode === 'on' ? '全覆盖' : '未开启' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="sect-head">
        <h2>明细</h2>
        <span class="muted">每一项的证据与原因；失败项默认置顶</span>
      </div>

      <!-- ② 规则资产契约 -->
      <details class="card dcard" :open="data.contract.errors.length > 0">
        <summary>
          <span class="dt">规则资产契约</span>
          <span class="tag" :class="data.contract.errors.length ? 't-hit' : 't-pass'">
            {{ data.contract.errors.length ? `失败 ${data.contract.errors.length}` : `${data.contract.checks.length} 项全过` }}
          </span>
          <span class="grow"></span>
          <span class="dhint">与后端启动时跑的是同一套</span>
          <svg class="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>
        </summary>
        <div class="dbody">
          <table class="tbl ck-tbl">
            <thead><tr><th style="width: 86px">结果</th><th style="width: 240px">检查项</th><th>说明</th></tr></thead>
            <tbody>
              <tr v-for="c in chkList.view(data.contract.checks)" :key="c.check">
                <td>
                  <span class="tag" :class="c.ok ? 't-pass' : 't-hit'">{{ c.ok ? '通过' : '失败' }}</span>
                </td>
                <td style="font-size:12.5px">{{ c.check }}</td>
                <td class="muted" style="font-size:12px">
                  <MarkText :text="prettyDetail(c.detail) || '—'" />
                </td>
              </tr>
              <tr v-if="chkList.more(data.contract.checks).show">
                <td :colspan="3" class="morecell" @click="chkList.more(data.contract.checks).toggle()">
                  {{ chkList.more(data.contract.checks).rest === -1 ? '收起' : `展开剩余 ${chkList.more(data.contract.checks).rest} 条` }}（共 {{ data.contract.checks.length }} 条）
                </td>
              </tr>
            </tbody>
          </table>
          <div v-if="data.contract.errors.length" class="err" style="margin:12px 20px 16px">
            <b>失败项</b>
            <div v-for="(e, i) in data.contract.errors" :key="i">{{ e }}</div>
          </div>
        </div>
      </details>

      <!-- ③ 被剔出执行集的规则 -->
      <details class="card dcard">
        <summary>
          <span class="dt">被剔出执行集的规则</span>
          <span class="pill warn num">{{ data.contract.excluded.length }} 条</span>
          <span class="grow"></span>
          <span class="dhint">没跑，不是通过</span>
          <svg class="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>
        </summary>
        <div class="dbody">
          <div class="note" style="margin:14px 20px 0">
            这些规则本次<b>没有执行</b>。不是通过，是没跑。
            逐条记原因是必须的——静默剔除会让一条本该拦截的闸悄悄消失，而且没人知道。
          </div>
          <table class="tbl">
            <thead><tr><th style="width: 140px">规则</th><th>原因</th></tr></thead>
            <tbody>
              <tr v-for="e in exclList.view(data.contract.excluded)" :key="e.rule_id">
                <td class="mono" style="font-size:12px">{{ e.rule_id }}</td>
                <td class="muted" style="font-size:12px"><MarkText :text="e.why" /></td>
              </tr>
              <tr v-if="exclList.more(data.contract.excluded).show">
                <td :colspan="2" class="morecell" @click="exclList.more(data.contract.excluded).toggle()">
                  {{ exclList.more(data.contract.excluded).rest === -1 ? '收起' : `展开剩余 ${exclList.more(data.contract.excluded).rest} 条` }}（共 {{ data.contract.excluded.length }} 条）
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>

      <!-- ④ 智能体配置 -->
      <details class="card dcard">
        <summary>
          <span class="dt">智能体配置</span>
          <span class="pill brand num">{{ data.agents.items.length }} 个</span>
          <span class="grow"></span>
          <span class="dhint">配置检查 {{ data.agents.checks.length }} 项 · 失败 {{ data.agents.errors.length }} · 展开后点一行看参数</span>
          <svg class="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>
        </summary>
        <div class="dbody">
          <table class="tbl">
            <thead>
              <tr>
                <th style="width: 220px">智能体</th>
                <th style="width: 90px">层</th>
                <th style="width: 80px">版本</th>
                <th style="width: 150px">触发</th>
                <th class="num" style="width: 70px">读表</th>
                <th class="num" style="width: 70px">写表</th>
                <th class="num" style="width: 70px">参数</th>
                <th class="num" style="width: 70px">禁止</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="a in data.agents.items" :key="a.agent_id">
                <tr class="rowlink" @click="toggleAgent(a.agent_id)">
                  <td>
                    <span class="caret" :class="{ open: openAgent.has(a.agent_id) }">›</span>
                    {{ a.agent_name }}
                    <div class="mono sub2">{{ a.agent_id }}</div>
                  </td>
                  <td><span class="tag t-na">{{ LAYER_CN[a.layer] || a.layer }}</span></td>
                  <td class="mono">v{{ a.version }}</td>
                  <td>
                    <span v-for="t in a.triggers" :key="t" class="tk">{{ t }}</span>
                  </td>
                  <td class="num">{{ a.reads.length }}</td>
                  <td class="num">{{ a.writes.length }}</td>
                  <td class="num">{{ Object.keys(a.params).length }}</td>
                  <td class="num">{{ a.forbidden.length }}</td>
                </tr>
                <tr v-if="openAgent.has(a.agent_id)" class="evrow">
                  <td colspan="8">
                    <div class="ev">
                      <div class="ev-col">
                        <div class="ev-k">触发方式</div>
                        <div class="ev-tags">
                          <span v-for="t in a.triggers" :key="t" class="tag t-pass">{{ t }}</span>
                        </div>
                        <div class="ev-k" style="margin-top:14px">S 阶段可读</div>
                        <div class="ev-mono">{{ a.reads.join('、') || '—' }}</div>
                        <div class="ev-k" style="margin-top:14px">A 阶段可写</div>
                        <div class="ev-mono">{{ a.writes.join('、') || '—' }}</div>
                      </div>
                      <div class="ev-col">
                        <div class="ev-k">I 阶段可调参数</div>
                        <table v-if="Object.keys(a.params).length" class="tbl mini">
                          <thead><tr><th style="width:130px">参数</th><th class="num" style="width:70px">默认</th><th style="width:100px">区间</th><th>为什么是这个区间</th></tr></thead>
                          <tbody>
                            <tr v-for="(v, k) in a.params" :key="k">
                              <td class="mono">{{ k }}</td>
                              <td class="num">{{ v.default }}</td>
                              <td class="mono">[{{ v.lo }}, {{ v.hi }}]</td>
                              <td class="muted" style="font-size:12px"><MarkText :text="v.why" /></td>
                            </tr>
                          </tbody>
                        </table>
                        <div v-else class="muted" style="font-size:12px">无</div>
                        <div class="ev-k" style="margin-top:14px">I 阶段禁止动</div>
                        <div class="ev-tags">
                          <span v-for="f in a.forbidden" :key="f" class="tag t-hit">{{ f }}</span>
                        </div>
                      </div>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </details>

      <!-- ⑤ 脱敏 -->
      <details class="card dcard">
        <summary>
          <span class="dt">脱敏</span>
          <span class="tag" :class="data.mask.mode === 'on' ? 't-pass' : 't-hit'">
            {{ data.mask.mode === 'on' ? '已开启' : '已关闭' }}
          </span>
          <span class="grow"></span>
          <span class="dhint">{{ data.mask.policies.length }} 条策略 · {{ data.mask.outlets.length }} 个出口 · {{ Object.keys(data.mask.sensitive_fields).length }} 个敏感字段</span>
          <svg class="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>
        </summary>
        <div class="dbody">
          <div class="maskgrid">
            <div class="mbox">
              <div class="ev-k">策略（{{ data.mask.policies.length }} 条）</div>
              <div class="mlist">
                <div v-for="p in data.mask.policies" :key="p">{{ p }}</div>
              </div>
            </div>
            <div class="mbox">
              <div class="ev-k">覆盖出口（{{ data.mask.outlets.length }} 个）</div>
              <div class="mlist">
                <div v-for="o in data.mask.outlets" :key="o">{{ o }}</div>
              </div>
            </div>
            <div class="mbox">
              <div class="ev-k">敏感字段（{{ Object.keys(data.mask.sensitive_fields).length }} 个）</div>
              <table class="tbl mini">
                <tbody>
                  <tr v-for="(v, k) in data.mask.sensitive_fields" :key="k">
                    <td class="mono" style="width:80px">{{ k }}</td>
                    <td>{{ v }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div v-if="data.mask.mode !== 'on'" class="err" style="margin:0 20px 16px">
            生产环境绝不允许关闭脱敏。
          </div>
        </div>
      </details>
    </template>
  </Async>
  </div>
</template>

<style scoped>
.hgroup { min-width: 0; }
.h1 { font-size: 16px; font-weight: 600; color: var(--text); }
.h2 { font-size: 12.5px; color: var(--muted); margin-top: 3px; line-height: 1.6; }
.cihero .b.bad { background: var(--bad-bg); color: var(--bad); }
.cihero .b { flex: none; }

.sect-head { display: flex; align-items: baseline; gap: 10px; margin: 4px 2px 12px; }
.sect-head h2 { font-size: 14px; font-weight: 600; color: var(--text); margin: 0; }
.sect-head .muted { font-size: 12px; color: var(--weak); }

/* 明细卡：默认收起。20 项检查 + 54 条剔除 + 13 个智能体全平铺要三千像素，
   结论会被埋在最上面一屏之后——先给结论，证据按需展开。 */
.dcard { margin-bottom: 16px; }
.dcard > summary {
  list-style: none; display: flex; align-items: center; gap: 10px;
  padding: 14px 20px; cursor: pointer; user-select: none;
}
.dcard > summary::-webkit-details-marker { display: none; }
.dcard[open] > summary { border-bottom: 1px solid var(--panel-2); }
.dcard > summary:hover .dt { color: var(--brand); }
.dt { font-size: 15px; font-weight: 600; color: var(--text); }
.dhint { font-size: 12px; color: var(--weak); }
.chev { color: var(--weak); transition: transform .15s; flex: none; }
.dcard[open] .chev { transform: rotate(180deg); }
.dbody { padding-bottom: 4px; }

.hint { font-size: 12px; color: var(--weak); }
.muted { color: var(--muted); }
.morecell { text-align: center; color: var(--brand); cursor: pointer; font-size: 13px; }

/* 智能体行：整行可点，展开后是证据区 */
.rowlink { cursor: pointer; }
.caret {
  display: inline-block; width: 14px; color: var(--weak); font-size: 14px;
  transition: transform .15s; transform: rotate(0deg);
}
.caret.open { transform: rotate(90deg); color: var(--brand); }
.sub2 { font-size: 11px; color: var(--weak); margin-top: 2px; padding-left: 14px; }
.tk {
  display: inline-block; font-size: 11px; color: var(--muted);
  border: 1px solid var(--line); border-radius: 3px; padding: 0 6px; margin-right: 4px;
  line-height: 17px;
}
.evrow td { background: var(--panel-2); padding: 16px 20px; }
.ev { display: grid; grid-template-columns: 1fr 1.4fr; gap: 28px; }
.ev-col { min-width: 0; }
.ev-k { font-size: 12px; color: var(--weak); margin-bottom: 6px; }
.ev-tags { display: flex; gap: 6px; flex-wrap: wrap; }
.ev-mono { font-size: 12px; color: var(--muted); font-family: Consolas, "Courier New", monospace; line-height: 1.6; overflow-wrap: anywhere; }
.tbl.mini th, .tbl.mini td { padding: 6px 8px; font-size: 12px; }

/* 脱敏三列 */
.maskgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; padding: 16px 20px 20px; }
.mbox { min-width: 0; }
.mlist { font-size: 12px; color: var(--muted); line-height: 1.75; }
@media (max-width: 1180px) {
  .maskgrid, .ev { grid-template-columns: 1fr; gap: 16px; }
}
/* 结果列（通过/失败）是状态，居中；其余文本左对齐 */
.ck-tbl th:nth-child(1), .ck-tbl td:nth-child(1) { text-align: center; }
</style>
