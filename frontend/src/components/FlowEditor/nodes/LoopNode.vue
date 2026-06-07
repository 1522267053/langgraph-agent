<script setup lang="ts">
import { RefreshRight } from '@element-plus/icons-vue'
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
  }
]
</script>

<template>
  <BaseNode
    :id="props.id"
    :data="props.data"
    :selected="props.selected"
    node-type="loop"
    color="#6366f1"
    :icon="RefreshRight"
    type-label="循环"
    :handles="handles"
  />
</template>
