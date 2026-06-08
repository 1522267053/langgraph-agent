<script setup lang="ts">
import { computed, type Component } from 'vue'
import { Handle, Position } from '@vue-flow/core'

export interface HandleConfig {
  type: 'target' | 'source'
  position: 'top' | 'bottom' | 'left' | 'right'
  id?: string
  label?: string
  color?: 'green' | 'blue' | 'orange' | 'red'
}

const props = withDefaults(
  defineProps<{
    id: string
    data: { label?: string; config?: Record<string, unknown> }
    selected?: boolean
    nodeType: string
    color: string
    icon?: Component
    defaultLabel?: string
    typeLabel?: string
    handles?: HandleConfig[]
  }>(),
  {
    selected: false,
    icon: undefined,
    defaultLabel: '',
    typeLabel: '',
    handles: () => []
  }
)

defineSlots<{
  default?: () => unknown
  content?: () => unknown
}>()

const label = computed(() => props.data?.label || props.defaultLabel)

const borderStyle = computed(() => {
  return {
    borderColor: props.color,
    '--node-color': props.color,
    ...(nodeMinHeight.value ? { minHeight: nodeMinHeight.value } : {})
  }
})

const positionMap = {
  top: Position.Top,
  bottom: Position.Bottom,
  left: Position.Left,
  right: Position.Right
}

const handleColorMap: Record<'green' | 'blue' | 'orange' | 'red', string> = {
  green: '#94a3b8',
  blue: '#94a3b8',
  orange: '#94a3b8',
  red: '#94a3b8'
}

const labelColorMap: Record<'green' | 'blue' | 'orange' | 'red', string> = {
  green: '#67c23a',
  blue: '#409eff',
  orange: '#e6a23c',
  red: '#f56c6c'
}

const HANDLE_GAP_MAX = 30
const SAFE_RANGE = 90
const MIN_PX_GAP = 22

interface HandleOffset {
  handle: HandleConfig
  top: string
}

const handlesWithOffsets = computed<HandleOffset[]>(() => {
  const sides: Record<string, { handle: HandleConfig; globalIndex: number }[]> = {
    left: [],
    right: []
  }
  for (let gi = 0; gi < props.handles.length; gi++) {
    const h = props.handles[gi]
    if (h.position === 'left' || h.position === 'right') {
      sides[h.position].push({ handle: h, globalIndex: gi })
    }
  }
  const offsetMap = new Map<number, string>()
  for (const side of ['left', 'right'] as const) {
    const group = sides[side]
    const total = group.length
    const gap = total > 1 ? Math.min(HANDLE_GAP_MAX, SAFE_RANGE / (total - 1)) : HANDLE_GAP_MAX
    for (let i = 0; i < total; i++) {
      offsetMap.set(
        group[i].globalIndex,
        `${50 - ((total - 1) * gap) / 2 + i * gap}%`
      )
    }
  }
  return props.handles.map((h, gi) => ({
    handle: h,
    top: offsetMap.get(gi) ?? '50%'
  }))
})

const nodeMinHeight = computed(() => {
  const sides: Record<string, number> = { left: 0, right: 0 }
  for (const h of props.handles) {
    if (h.position === 'left' || h.position === 'right') {
      sides[h.position]++
    }
  }
  const maxHandles = Math.max(sides.left, sides.right)
  if (maxHandles <= 1) return undefined
  const gap = Math.min(HANDLE_GAP_MAX, SAFE_RANGE / (maxHandles - 1))
  const minHeight = Math.ceil(MIN_PX_GAP * 100 / gap)
  return minHeight > 70 ? `${minHeight}px` : undefined
})

function getHandleStyle(
  color?: 'green' | 'blue' | 'orange' | 'red',
  top?: string
): Record<string, string> {
  const c = color || 'blue'
  return {
    background: '#fff',
    borderColor: handleColorMap[c],
    top: top ?? '50%'
  }
}

function getLabelStyle(
  color?: 'green' | 'blue' | 'orange' | 'red',
  top?: string,
  pos?: string
): Record<string, string> {
  const c = color || 'blue'
  const base: Record<string, string> = { background: labelColorMap[c] }
  if ((pos === 'left' || pos === 'right') && top) {
    base.top = top
  }
  return base
}

function getLabelClass(pos: string): string {
  const classMap: Record<string, string> = {
    top: 'label-top',
    bottom: 'label-bottom',
    left: 'label-left',
    right: 'label-right'
  }
  return classMap[pos] || ''
}
</script>

<template>
  <div
    class="base-node node-shadow"
    :class="[`node-${nodeType}`, { selected }]"
    :style="borderStyle"
  >
    <template v-for="item in handlesWithOffsets" :key="`${item.handle.position}_${item.handle.id}`">
      <Handle
        :id="item.handle.id"
        :type="item.handle.type"
        :position="positionMap[item.handle.position]"
        :class="['flow-handle', { 'tool-handle': item.handle.id === 'tools' }]"
        :style="getHandleStyle(item.handle.color, item.top)"
      />
      <span
        v-if="item.handle.label"
        class="handle-label"
        :class="[getLabelClass(item.handle.position), { 'tool-label': item.handle.id === 'tools' }]"
        :style="getLabelStyle(item.handle.color, item.top, item.handle.position)"
      >
        {{ item.handle.label }}
      </span>
    </template>

    <div class="node-header">
      <el-icon v-if="icon" size="18" class="node-type-icon" :style="{ color: color }">
        <component :is="icon" />
      </el-icon>
      <span v-if="typeLabel" class="node-type-label">{{ typeLabel }}</span>
    </div>
    <div class="node-body">
      <slot name="content">
        <span class="node-label">{{ label }}</span>
      </slot>
    </div>

    <slot />
  </div>
</template>

<style scoped>
@import './node-styles.css';

.base-node {
  background: #fff;
  padding: 12px 32px;
  border-radius: 16px;
  border: 2px solid #e2e8f0;
  min-width: 140px;
  max-width: 220px;
  position: relative;
  cursor: pointer;
  transition: all 0.2s;
}

.base-node:hover {
  transform: translateY(-2px);
  box-shadow:
    0 10px 15px -3px rgba(0, 0, 0, 0.1),
    0 4px 6px -2px rgba(0, 0, 0, 0.05);
}

.base-node.selected {
  border-color: var(--node-color, #3b82f6);
  box-shadow:
    0 0 0 4px color-mix(in srgb, var(--node-color, #3b82f6) 15%, transparent),
    0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

.node-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.node-type-icon {
  flex-shrink: 0;
}

.node-type-label {
  font-size: 10px;
  font-weight: 700;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.node-body {
  display: flex;
  align-items: center;
  gap: 6px;
}

.node-label {
  font-size: 14px;
  font-weight: 700;
  color: #1e293b;
}

.node-content {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.flow-handle {
  width: 8px !important;
  height: 8px !important;
  border: 2px solid #94a3b8 !important;
  background: #fff !important;
  border-radius: 9999px !important;
  z-index: 10;
  transition:
    width 0.15s,
    height 0.15s,
    background 0.15s,
    border-color 0.15s,
    box-shadow 0.15s;
}

.flow-handle:hover {
  width: 12px !important;
  height: 12px !important;
  background: #3b82f6 !important;
  border-color: #3b82f6 !important;
}

.tool-handle {
  border: 2px solid #e6a23c !important;
  background: #fffbe6 !important;
}

.flow-handle.tool-handle:hover {
  width: 12px !important;
  height: 12px !important;
  background: #e6a23c !important;
  border-color: #e6a23c !important;
  box-shadow: 0 0 0 4px rgba(230, 162, 60, 0.2);
}

.handle-label {
  position: absolute;
  font-size: 10px;
  padding: 1px 4px;
  border-radius: 3px;
  color: white;
  pointer-events: none;
  z-index: 1;
}

.label-left {
  left: 2px;
  transform: translateY(-50%);
}

.label-right {
  right: 2px;
  transform: translateY(-50%);
}

.label-top {
  top: 2px;
  left: 50%;
  transform: translateX(-50%);
}

.label-bottom {
  bottom: 2px;
  left: 50%;
  transform: translateX(-50%);
}

.tool-label {
  background: #e6a23c !important;
}
</style>
