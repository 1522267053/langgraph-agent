<script setup lang="ts">
import { Monitor } from '@element-plus/icons-vue'
import BaseNode from './BaseNode.vue'

const props = defineProps<{
  id: string
  data: { label?: string; config?: Record<string, unknown> }
  selected?: boolean
}>()

const handles = [
  {
    type: 'target' as const,
    position: 'left' as const,
    id: 'default',
    label: '入',
    color: 'green' as const
  },
  {
    type: 'source' as const,
    position: 'right' as const,
    id: 'default',
    label: '出',
    color: 'blue' as const
  },
  {
    type: 'source' as const,
    position: 'right' as const,
    id: 'tools',
    label: '工具',
    color: 'green' as const
  }
]
</script>

<template>
  <BaseNode
    :id="props.id"
    :data="props.data"
    :selected="props.selected"
    node-type="shell"
    color="#f59e0b"
    :icon="Monitor"
    type-label="Shell命令"
    :handles="handles"
  />
</template>
