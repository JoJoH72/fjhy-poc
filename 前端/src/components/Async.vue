<script setup lang="ts" generic="T">
/**
 * 异步数据容器（2026-10-27 交互基线升级）。
 *
 * 三态 + 刷新语义（SWR 模式，业界标准）：
 *   首次加载  a-skeleton 骨架屏（不是空白/一行小字）
 *   刷新      保留旧数据继续渲染，不卸载插槽——否则按钮 loading spinner
 *             会被整段卸载吃掉，「点完闪一下」就是这么来的
 *   刷新失败  旧数据上方横幅报错，数据不丢
 *   首次失败   标准错误态 + 重试按钮；**不退化成空列表**——
 *             空列表看着像「没有数据」，实际是接口挂了，这两件事必须分得开。
 */
import { onMounted, ref, watch } from 'vue'

const props = defineProps<{
  /** 取数函数。变化时会重新取 */
  load: () => Promise<T>
  /** 依赖项变化时重新取数 */
  deps?: unknown
}>()

const data = ref<T | null>(null)
const error = ref('')
const loading = ref(true)

async function run() {
  // 首次（无数据）才显示骨架屏；刷新保留旧数据（stale-while-revalidate）
  if (data.value === null) loading.value = true
  error.value = ''
  try {
    data.value = await props.load()
    emit('loaded', data.value)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(run)
watch(() => props.deps, run)
defineExpose({ reload: run })

const emit = defineEmits<{ loaded: [value: T] }>()
</script>

<template>
  <a-skeleton v-if="loading && data === null" active :paragraph="{ rows: 4 }" />
  <div v-else-if="error && data === null" class="async-err">
    <a-alert type="error" show-icon>
      <template #message>加载失败：{{ error }}</template>
      <template #description>
        <a-button size="small" type="primary" @click="run">重试</a-button>
      </template>
    </a-alert>
  </div>
  <template v-else>
    <a-alert v-if="error" type="error" show-icon closable style="margin-bottom: 12px">
      <template #message>刷新失败：{{ error }}</template>
    </a-alert>
    <slot :data="data as T" :reload="run" />
  </template>
</template>
