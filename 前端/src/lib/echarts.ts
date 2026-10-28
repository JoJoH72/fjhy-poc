/**
 * ECharts 5 按需引入（关系图谱专用，2026-10-27 P1-G）。
 *
 * 选型结论（设计专章 §3.3）：ECharts 5 按需 + Canvas 渲染器。
 * **一票否决 echarts-gl**——POC 服务器经 RDP 访问时 WebGL 常走软件渲染掉帧，
 * Canvas 2D 零风险。
 *
 * 体积实测（esbuild metafile 口径，2026-10-27）：
 *   全量 import 'echarts'          337KB gzip
 *   按需 graph+lines+tooltip+grid  ~175KB gzip（打包进 RelationGraph 异步 chunk，
 *                                  只在第一次点开「关系图谱」页签时下载）
 *   其中 echarts/core+CanvasRenderer 底价 97KB gzip（zrender 渲染层），
 *   设计稿「+90KB gzip」的估算低于这个底价——已通过 defineAsyncComponent
 *   把整包隔离成懒加载 chunk，规则清单首屏不背它。
 *
 * 这里是全项目唯一的 echarts import 点——别的页面要用图表，也从这里拿，
 * 防止第二处 import 'echarts' 把按需纪律击穿。
 */
import * as echarts from 'echarts/core'
import { GraphChart, LinesChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { ComposeOption } from 'echarts/core'
import type { GraphSeriesOption, LinesSeriesOption } from 'echarts/charts'
import type { GridComponentOption, TooltipComponentOption } from 'echarts/components'

// GridComponent：lines 系列（粒子层）必须挂在 cartesian2d 上——它默认找 geo。
// 用 0 边距 + min/max 钉满画布的隐轴，坐标空间与 graph 的 x/y 像素直通。
// LegendComponent 不装：图例走组件自绘工具条（要带开关语义与分组行为）。
echarts.use([GraphChart, LinesChart, GridComponent, TooltipComponent, CanvasRenderer])

export type ECOption = ComposeOption<
  | GraphSeriesOption
  | LinesSeriesOption
  | GridComponentOption
  | TooltipComponentOption
>

export { echarts }
export type EChartsType = ReturnType<typeof echarts.init>
