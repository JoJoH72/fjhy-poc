/**
 * 长列表统一折叠（2026-10-25 用户定）：任何 v-for 渲染的数据行，
 * 超过 10 条默认收起，点击“展开剩余 N 条”可展开并可收起。
 *
 * 用法（页面里三步）：
 *   const billList = useCollapse()            // 每张表一个实例
 *   <tr v-for="x in billList.view(rows)">    // v-for 换成 .view(源数组)
 *   <tr v-if="billList.more(rows)">…按钮行…  // 表尾放展开/收起行
 *
 * 不满 10 条时 view === 源数组、more 为 false，行为与原来完全一致。
 * 命名按张表起（billList / fileList …），不许全页共用一个实例——
 * 筛选条件变化时各自 reset。
 */
import { ref } from 'vue'

export const COLLAPSE_N = 10

export function useCollapse() {
  const expanded = ref(false)

  function view<T>(rows: T[]): T[] {
    if (expanded.value || rows.length <= COLLAPSE_N) return rows
    return rows.slice(0, COLLAPSE_N)
  }

  /** 展开行数据：[是否显示, 剩余条数或-1(收起态), 点击回调] */
  function more(rows: unknown[]): { show: boolean; rest: number; toggle: () => void } {
    return {
      show: rows.length > COLLAPSE_N,
      rest: expanded.value ? -1 : rows.length - COLLAPSE_N,
      toggle: () => { expanded.value = !expanded.value },
    }
  }

  function reset() { expanded.value = false }

  return { view, more, reset, expanded }
}
