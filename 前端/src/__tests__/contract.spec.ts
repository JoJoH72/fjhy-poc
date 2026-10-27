/**
 * 前端契约测试。只测那些**坏了不会报错**的东西。
 *
 * 不测「按钮点了有没有反应」这类——那种坏了一眼就能看出来。
 * 这里守的是四件会悄悄出错的事：
 *   1. 领域知识资产各栏目的顺序（顺序本身是信息，乱了就看不出规则加工链）
 *   2. 错误被吞成空数据（页面显示「无数据」而实际是接口挂了）
 *   3.「待人工」长得像「通过」（审核岗会以为查过了没问题）
 *   4. 未接通的栏目被藏起来（会让人以为功能不存在，而不是「还没做」）
 */
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { ApiError } from '../api'
import { NAV, ROLE_ORDER, ITEM_BY_KEY, flatItems } from '../data/navTree'
import { isComplete, label as domainLabel } from '../data/domainState'
import {
  ERR_KINDS,
  ERR_LABEL,
  KEY_FORMAT,
  errLabel,
  looksVision,
  modalityWarning,
  testState,
  testTag,
} from '../data/llmState'
import Outcome from '../components/Outcome.vue'
import Async from '../components/Async.vue'

describe('导航树', () => {
  // 2026-10-26 IA 重排后的新契约：平铺、按角色组织、本体退出业务界面。
  it('四角色菜单各自 ≤6 项且顺序钉死', () => {
    expect(ROLE_ORDER.auditor).toEqual(['mywork', 'ai-review', 'workbench', 'report', 'trace'])
    expect(ROLE_ORDER.engineer).toEqual(['doc-lib', 'rules', 'publish', 'trace'])
    expect(ROLE_ORDER.admin).toEqual(['users', 'llm', 'ops', 'startup', 'knowstruct', 'oplog'])
    expect(ROLE_ORDER.leader).toEqual(['dash', 'workbench', 'trace', 'deviation'])
    for (const keys of Object.values(ROLE_ORDER)) {
      expect(keys.length).toBeLessThanOrEqual(6)
    }
  })

  it('本体退出业务界面：engineer 的菜单里没有本体与工场', () => {
    const gone = ['workshop', 'biz-onto', 'onto', 'proc', 'extract']
    for (const k of gone) {
      expect(ROLE_ORDER.engineer, `engineer 不该有 ${k}`).not.toContain(k)
    }
    // 知识结构（本体的新去处）只归系统管理员
    expect(ROLE_ORDER.admin).toContain('knowstruct')
    expect(ROLE_ORDER.engineer).not.toContain('knowstruct')
  })

  it('每个菜单项都有路径且接通', () => {
    for (const it of flatItems(NAV.flatMap((g) => g.items))) {
      expect(it.path, `${it.key} 缺 path`).toMatch(/^\//)
      if (!it.built) {
        expect(it.todo, `${it.key} 标了 built=false 但没写 todo`).toBeTruthy()
      }
    }
    // ROLE_ORDER 引用的 key 必须都能解析成菜单项
    for (const keys of Object.values(ROLE_ORDER)) {
      for (const k of keys) {
        expect(ITEM_BY_KEY[k], `${k} 在 ROLE_ORDER 里但 navTree 没有这项`).toBeTruthy()
      }
    }
  })
})

describe('接口错误不许被吞', () => {
  it('ApiError 带状态码与后端 detail', () => {
    const e = new ApiError(404, '报账单 X 不存在')
    expect(e.status).toBe(404)
    expect(e.detail).toBe('报账单 X 不存在')
    expect(e.message).toContain('404')
  })

  it('Async 组件在出错时显示错误，而不是退化成空列表', async () => {
    // 这是前端最常见的隐藏故障方式：catch 之后 return []。
    // 页面上看着是「没有数据」，实际是接口挂了——
    // 审核岗据此以为「这批单没问题」，而真相是什么都没查。
    // 2026-10-27 Async 换 AntD 三态：错误态用 a-alert message 传文案（prop），
    // DOM 文本断言改查渲染后的 alert 内容。
    const w = mount(Async, {
      props: { load: () => Promise.reject(new ApiError(500, '数据库连不上')) },
      slots: { default: '<div class="ok-slot">有数据</div>' },
      global: { plugins: [Antd] },
    })
    await new Promise((r) => setTimeout(r, 0))
    expect(w.text()).toContain('加载失败')
    expect(w.text()).toContain('数据库连不上')
    // AntD 按钮两字间渲染空格（「重 试」），用正则匹配
    expect(w.text()).toMatch(/重\s*试/)
    expect(w.find('.ok-slot').exists()).toBe(false)
  })

  it('Async 组件成功时才渲染插槽', async () => {
    const w = mount(Async, {
      props: { load: () => Promise.resolve({ n: 1 }) },
      slots: { default: '<div class="ok-slot">有数据</div>' },
    })
    await new Promise((r) => setTimeout(r, 0))
    expect(w.find('.ok-slot').exists()).toBe(true)
    expect(w.text()).not.toContain('取数失败')
  })
})

describe('判定结果标签', () => {
  const cls = (outcome: string, extra = {}) =>
    mount(Outcome, { props: { outcome, ...extra } }).find('.tag').classes()

  it('待人工不能长得像通过', () => {
    // 把 pending 显示成绿色或干脆不显示，审核岗就会以为这条查过了而且没问题，
    // 而实际是「判不了」。那等于让系统声称复核了没复核的东西。
    expect(cls('pending')).toContain('t-pend')
    expect(cls('pending')).not.toContain('t-pass')
    expect(cls('pass')).toContain('t-pass')
    expect(cls('hit')).toContain('t-hit')
  })

  it('四态的样式两两不同', () => {
    const got = ['hit', 'pass', 'pending', 'excluded'].map((o) =>
      cls(o).filter((c) => c.startsWith('t-')).join(),
    )
    expect(new Set(got).size).toBe(4)
  })

  it('不适用与通过要分开显示', () => {
    // 「不适用」说明这条规则跟本单无关；「合规」说明查过了而且没问题。
    // 混在一起会让覆盖面看起来比实际大。
    const na = mount(Outcome, { props: { outcome: 'pass', notApplicable: true } })
    expect(na.text()).toContain('不适用')
    const pass = mount(Outcome, { props: { outcome: 'pass' } })
    expect(pass.text()).toContain('通过')
  })

  it('命中时要显示控制类型，禁止与警告不同色', () => {
    const block = mount(Outcome, { props: { outcome: 'hit', controlType: '禁止' } })
    const warn = mount(Outcome, { props: { outcome: 'hit', controlType: '警告' } })
    expect(block.html()).toContain('t-block')
    expect(warn.html()).toContain('t-warn')
    // 不命中时不显示控制类型——一条通过的规则标着「禁止」会让人误读
    const pass = mount(Outcome, { props: { outcome: 'pass', controlType: '禁止' } })
    expect(pass.html()).not.toContain('t-block')
  })
})

describe('判定四态的颜色（功能色，不是装饰）', () => {
  // 上面那几条测的是 class 名。class 名对了而四个 class 恰好被调成同一个颜色，
  // 测试照样全绿——改配色时这正是最容易发生的事：
  // 为了跟官网协调，把四个颜色一起往蓝灰里压，压到肉眼分不开。
  // 所以这里直接读 style.css 量颜色距离。
  // vitest 的 import.meta.url 不是 file: 协议（是 Vite 的模块 URL），
  // 拿它喂 readFileSync 会报 'The URL must be of scheme file'。走 cwd。
  const cssPath = resolve(process.cwd(), 'src/style.css')
  expect(existsSync(cssPath), 'style.css 找不到：' + cssPath).toBe(true)
  const css = readFileSync(cssPath, 'utf8')

  /** 取 :root 里的变量定义 */
  const vars = Object.fromEntries(
    [...css.matchAll(/--([a-z-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]),
  )

  /** 解一层 var(--x)，再转 RGB */
  const rgb = (raw: string): [number, number, number] => {
    const v = raw.replace(/var\(--([a-z-]+)\)/, (_, n) => vars[n] ?? '')
    const h = v.trim().replace('#', '')
    const f = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
    expect(f, `色值解不出来：${raw}`).toMatch(/^[0-9a-fA-F]{6}$/)
    return [0, 2, 4].map((i) => parseInt(f.slice(i, i + 2), 16)) as [number, number, number]
  }

  const dist = (a: string, b: string) =>
    Math.hypot(...rgb(a).map((x, i) => x - rgb(b)[i]))

  it('四个变量都有定义', () => {
    for (const k of ['hit', 'pend', 'pass', 'na']) {
      expect(vars[k], `--${k} 没定义`).toBeTruthy()
    }
  })

  it('四态颜色两两拉得开', () => {
    const keys = ['hit', 'pend', 'pass', 'na']
    for (let i = 0; i < keys.length; i++) {
      for (let j = i + 1; j < keys.length; j++) {
        const d = dist(vars[keys[i]], vars[keys[j]])
        // 60 是经验阈值：低于这个数，两个色块并排放在表格里一眼分不出。
        expect(d, `--${keys[i]} 与 --${keys[j]} 太像了（距离 ${d.toFixed(0)}）`,
        ).toBeGreaterThan(60)
      }
    }
  })

  it('待人工与通过的标签文字色拉得开', () => {
    // 标签是浅底 + 深色文字 + 同色边框，**文字色才是主信号**。
    // 浅底两者接近是可以的，文字色不行。
    // 不用正则取：模板字符串里的 \\s 很容易在编辑过程中退化成 s，
    // 那样匹配不到规则，测试会以「规则找不到」的形式失败——
    // 看着像 CSS 少了一条，实际是测试自己的转义坏了。纯字符串切片没这个风险。
    const pick = (cls: string) => {
      const at = css.indexOf('.' + cls + ' ')
      expect(at, `.${cls} 规则找不到`).toBeGreaterThan(-1)
      const body = css.slice(css.indexOf('{', at) + 1, css.indexOf('}', at))
      const decl = body.split(';').map((x) => x.trim())
        .find((x) => x.startsWith('color:'))
      expect(decl, `.${cls} 没写 color`).toBeTruthy()
      return decl!.slice('color:'.length).trim()
    }
    const d = dist(pick('t-pend'), pick('t-pass'))
    expect(d, `t-pend 与 t-pass 文字色太像（距离 ${d.toFixed(0)}）`).toBeGreaterThan(60)
  })

  it('官网主色与金色点缀没被改掉', () => {
    // 这几个值是从 www.fjpg.cn 的 CSS 里取的。
    // 不是"差不多的蓝"——换掉就不是参照官网了，所以钉住。
    expect(vars['brand']).toBe('#015293')
    expect(vars['brand-light']).toBe('#1f93f9')
    expect(vars['gold']).toBe('#c19c41')
  })
})

describe('当前领域/实例', () => {
  it('没选实例时说「未选实例」，不编一个出来', () => {
    // 「审核 / 」或者退到上一个实例名，都会让人以为选中了某个实例。
    expect(domainLabel({ domain_id: 'report', domain_label: '报表',
                         instance_id: null, instance_label: null, mismatch: false }))
      .toBe('报表 / 未选实例')
  })

  it('领域与实例不匹配时要显示出来，不许装作正常', () => {
    const c = { domain_id: 'report', domain_label: '报表', instance_id: null,
                instance_label: null, mismatch: true }
    expect(domainLabel(c)).toContain('不匹配')
    expect(isComplete(c)).toBe(false)
  })

  it('领域和实例都齐了才算选全', () => {
    expect(isComplete({ domain_id: 'audit', domain_label: '审核',
                        instance_id: 'fjhy', instance_label: '福建海运集团',
                        mismatch: false })).toBe(true)
    expect(isComplete(null)).toBe(false)
  })
})

describe('大模型配置的状态显示', () => {
  it('「没测过」「通了」「不通」是三态，不许合成两态', () => {
    // 合成两态的后果很具体：
    //   并成「通过」→ 用户以为能用，跑批时全军覆没
    //   并成「不通」→ 用户去查一个根本没问题的配置
    expect(testState(null)).toBe('untested')
    expect(testState(undefined)).toBe('untested')
    expect(testState(true)).toBe('ok')
    expect(testState(false)).toBe('failed')
    const styles = [null, true, false].map((v) => testTag(v).cls)
    expect(new Set(styles).size).toBe(3)
  })

  it('未测试不许长得像通过，也不许长得像不通', () => {
    expect(testTag(null).cls).not.toBe(testTag(true).cls)
    expect(testTag(null).cls).not.toBe(testTag(false).cls)
    expect(testTag(null).text).toContain('未测')
  })

  it('每种失败原因都要有中文文案，而且文案里带「下一步干什么」', () => {
    // 后端 app/llm/client.py 加了新的 err_kind 而这里没加，
    // 界面会把英文原样显示给用户看。那种退化不报错、没人会注意到。
    for (const k of ERR_KINDS) {
      expect(ERR_LABEL[k], `err_kind ${k} 没有中文文案`).toBeTruthy()
    }
    // 「调用失败」这种文案等于没说。至少这几类要指出去哪儿解决。
    for (const k of ['auth', 'quota']) {
      expect(ERR_LABEL[k]).toContain('厂商')
    }
    expect(ERR_LABEL['model_not_found']).toContain('型号')
  })

  it('认不出来的原因原样显示，不能变成空白', () => {
    // 空白会被当成「没有错误」——那是把失败显示成了正常。
    expect(errLabel('something_new')).toBe('something_new')
    expect(errLabel(null)).toBe('')
  })
})

describe('用途与模型能力的交叉校验', () => {
  it('读票据图配纯文本模型必须警告', () => {
    // 这是整套系统里最难发现的错误：纯文本模型拿到图不报错，
    // 它会按 prompt 里的文字线索编一段看起来合理的内容——
    // HTTP 200、有回复、格式正确、读起来像真的，没有任何技术信号。
    const w = modalityWarning('vision_read', 'text')
    expect(w).toBeTruthy()
    expect(w).toContain('编')          // 文案必须点明「会编」这个后果
  })

  it('读图配多模态不该报警告', () => {
    expect(modalityWarning('vision_read', 'vision')).toBe('')
    expect(modalityWarning('vision_read', 'both')).toBe('')
  })

  it('判不出能力时也要提醒，不能当成没问题', () => {
    // 手动填地址时 modality 是 null。静默放过等于把「不知道」当成「没问题」。
    expect(modalityWarning('vision_read', null)).toBeTruthy()
    expect(modalityWarning('vision_read', undefined)).toBeTruthy()
  })

  // ── 2026-10-19 收紧：预置选对了不等于型号填对了 ────────────────────────
  // 实测躺过一个配置：preset=aliyun_bailian_vision（标注 vision），
  // 而 model_name=qwen3.5-122b-a10b —— 和两个文本用途的配置一模一样。
  // 原来只看预置，于是这条检查一次都没报过。
  it('预置能读图但型号被当成文本用途在用，要报警告', () => {
    const w = modalityWarning('vision_read', 'vision', 'qwen3.5-122b-a10b',
                              ['qwen3.5-122b-a10b'], 'qwen-vl-max')
    expect(w).toBeTruthy()
    expect(w).toContain('qwen3.5-122b-a10b')   // 要指名道姓，不能只说「有问题」
    expect(w).toContain('qwen-vl-max')         // 要给出该填什么
    expect(w).toContain('编')                  // 后果必须写出来
  })

  it('型号名带 vl 标记且没被当文本用，不报警告（不许误伤）', () => {
    expect(modalityWarning('vision_read', 'vision', 'qwen-vl-max', [], 'qwen-vl-max'))
      .toBe('')
    expect(modalityWarning('vision_read', 'both', 'glm-4v-plus', [])).toBe('')
  })

  it('型号名没有多模态标记时只说「判不出」，不说「不行」', () => {
    // gpt-4o / kimi-k3 这类多模态型号名里也没有 vl/vision，
    // 所以缺标记只能降级成提醒。把「不知道」说成「有问题」也是错的。
    const w = modalityWarning('vision_read', 'vision', 'my-gateway-model-7b', [])
    expect(w).toBeTruthy()
    expect(w).toContain('判不出')
    expect(w).not.toContain('不能用于')
  })

  it('纯文本预置的硬结论不受影响（原有逻辑不许被改坏）', () => {
    const w = modalityWarning('vision_read', 'text', 'qwen-vl-max', [])
    expect(w).toContain('不能用于')
  })

  it('没填型号名时不乱报', () => {
    expect(modalityWarning('vision_read', 'vision', '', [])).toBe('')
    expect(modalityWarning('vision_read', 'vision', null, [])).toBe('')
  })

  it('looksVision 只用来判「有」，不用来判「没有」', () => {
    expect(looksVision('qwen-vl-max')).toBe(true)
    expect(looksVision('gpt-4o')).toBe(true)
    expect(looksVision('glm-4v-plus')).toBe(true)
    expect(looksVision('qwen3.5-122b-a10b')).toBe(false)
  })

  it('其它用途不受这条校验影响', () => {
    expect(modalityWarning('rule_judge', 'text')).toBe('')
    expect(modalityWarning('extract', 'text')).toBe('')
  })
})

describe('Key 存储格式', () => {
  it('三种格式两两不同，且旧格式要明说必须重填', () => {
    const cls = ['ok', 'none', 'legacy'].map((k) => KEY_FORMAT[k].cls)
    expect(new Set(cls).size).toBe(3)
    expect(KEY_FORMAT.legacy.hint).toContain('重新填')
    // 「未填」不能长得像「已加密」——那会让人以为配好了
    expect(KEY_FORMAT.none.cls).not.toBe(KEY_FORMAT.ok.cls)
  })
})

// 2026-10-27 死代码清理：RuleOnto / Extract / OntoLoadSteps 随五页退役一并删除，
// 原先钉在它们身上的两个 describe（规则本体·统一口径、本体工场·装载两步的闸）
// 同步移除——被测文件已不存在。规则管理员的新落点是 RulesLib / DocLib，
// 后续若在那些页面重立口径契约，应按新页面重新钉。