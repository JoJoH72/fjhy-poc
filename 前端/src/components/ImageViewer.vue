<script setup lang="ts">
/**
 * 附件大图查看器（A-P1-2，2026-10-28 从 ReviewReport 抽出的共享组件）。
 *
 * 交互与 ReviewReport（P1-3）完全同款——审核岗「同一动作同一模式」纪律：
 *   滚轮缩放（0.5×~4×）/ 按钮缩放 / 拖拽平移 / 旋转 90° / ←→ 翻本单附件
 *   Esc 关层。层开着才劫持键盘（capture 阶段，先于页面级 onKey），
 *   关层即归还——与队列翻张不冲突。
 *
 * 两处挂载：ReviewReport（原版改为引本组件）与 BillDetail（A-P1-2 接线：
 * 缩略图/文件名点击进入，替代原先的静态大图）。
 * CSS transform 实现，不引库。
 */
import { onMounted, onUnmounted, ref, watch } from 'vue'

export interface ViewerItem {
  id: number
  file_name: string
}

const props = defineProps<{
  /** 本单可看的附件清单（仅 has_file 的，调用方过滤好） */
  items: ViewerItem[]
  /** 初始定位的附件 id */
  startId: number | null
}>()
const emit = defineEmits<{ close: [] }>()

const cur = ref<{ name: string; url: string; idx: number } | null>(null)
const scale = ref(1)
const rot = ref(0)
const pos = ref({ x: 0, y: 0 })
let drag: { x: number; y: number; ox: number; oy: number } | null = null

function showById(id: number | null) {
  const idx = props.items.findIndex((x) => x.id === id)
  showAt(idx >= 0 ? idx : 0)
}
function showAt(idx: number) {
  const a = props.items[idx]
  if (!a) return
  cur.value = { name: a.file_name, url: `/api/attachments/${a.id}/file`, idx }
  scale.value = 1
  rot.value = 0
  pos.value = { x: 0, y: 0 }
}
function nav(d: number) {
  if (!cur.value) return
  const n = props.items.length
  if (n > 1) showAt((cur.value.idx + d + n) % n)
}
function zoom(d: number) {
  scale.value = Math.min(4, Math.max(0.5, scale.value * d))
}
function onWheel(e: WheelEvent) {
  e.preventDefault()
  zoom(e.deltaY < 0 ? 1.2 : 1 / 1.2)
}
function onDown(e: MouseEvent) {
  drag = { x: e.clientX, y: e.clientY, ox: pos.value.x, oy: pos.value.y }
  e.preventDefault()
}
function onMove(e: MouseEvent) {
  if (!drag) return
  pos.value = { x: drag.ox + e.clientX - drag.x, y: drag.oy + e.clientY - drag.y }
}
function onUp() { drag = null }
function close() {
  cur.value = null
  emit('close')
}
function onKey(e: KeyboardEvent) {
  if (!cur.value) return
  if (e.key === 'Escape') close()
  else if (e.key === 'ArrowLeft') nav(-1)
  else if (e.key === 'ArrowRight') nav(1)
}
onMounted(() => document.addEventListener('keydown', onKey, true))
onUnmounted(() => document.removeEventListener('keydown', onKey, true))

// 打开即定位到 startId；items 变化（换单）时若当前项还在清单里保持位次
watch(() => [props.startId, props.items.length] as const, ([id]) => {
  if (cur.value) return
  if (id != null) showById(id)
}, { immediate: true })

defineExpose({ showById })
</script>

<template>
  <div class="iv" :class="{ show: !!cur }" @click.self="close">
    <div v-if="cur" class="iv-card">
      <div class="iv-img" @wheel="onWheel" @mousedown="onDown" @mousemove="onMove"
        @mouseup="onUp" @mouseleave="onUp">
        <img :src="cur.url" :alt="cur.name" draggable="false"
          :style="{ transform: `translate(${pos.x}px, ${pos.y}px) scale(${scale}) rotate(${rot}deg)`, cursor: drag ? 'grabbing' : 'grab' }">
      </div>
      <div class="iv-bar">
        <span class="n">{{ cur.name }}<span class="dim">（{{ cur.idx + 1 }}/{{ items.length }}）</span></span>
        <div class="iv-ops">
          <a-button size="small" :disabled="items.length <= 1" @click="nav(-1)">← 上一张</a-button>
          <a-button size="small" :disabled="items.length <= 1" @click="nav(1)">下一张 →</a-button>
          <a-button size="small" @click="zoom(1 / 1.2)">−</a-button>
          <span class="iv-zoom">{{ Math.round(scale * 100) }}%</span>
          <a-button size="small" @click="zoom(1.2)">＋</a-button>
          <a-button size="small" @click="rot = (rot + 90) % 360">旋转</a-button>
          <a-button size="small" @click="close">关闭 (Esc)</a-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 与 ReviewReport 大图层同款样式（类名改 iv- 避免两处同页冲突） */
.iv { position: fixed; inset: 0; z-index: 1000; display: none;
  background: rgba(0, 0, 0, .62); align-items: center; justify-content: center; }
.iv.show { display: flex; }
.iv-card { background: #fff; border-radius: 8px; overflow: hidden;
  display: flex; flex-direction: column; max-width: 92vw; max-height: 92vh; }
.iv-img { overflow: hidden; display: flex; align-items: center; justify-content: center;
  min-width: 60vw; min-height: 50vh; max-height: calc(92vh - 52px); background: #f5f5f5; }
.iv-img img { max-width: 88vw; max-height: 84vh; user-select: none; }
.iv-bar { display: flex; align-items: center; justify-content: space-between;
  gap: 12px; padding: 8px 14px; border-top: 1px solid #f0f0f0; }
.iv-bar .n { font-size: 12.5px; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; max-width: 40%; }
.dim { color: rgba(0, 0, 0, .45); }
.iv-ops { display: flex; align-items: center; gap: 6px; }
.iv-zoom { font-size: 12px; color: rgba(0, 0, 0, .55); min-width: 40px;
  text-align: center; font-variant-numeric: tabular-nums; }
</style>
