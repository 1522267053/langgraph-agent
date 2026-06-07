<script setup lang="ts">
import { Share } from '@element-plus/icons-vue'
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
    id: 'true',
    label: '是',
    color: 'blue' as const
  },
  {
    type: 'source' as const,
    position: 'right' as const,
    id: 'false',
    label: '否',
    color: 'red' as const
  }
]
</script>

<template>
  <BaseNode
    :id="props.id"
    :data="props.data"
    :selected="props.selected"
    node-type="condition"
    color="#f59e0b"
    :icon="Share"
    type-label="条件判断"
    :handles="handles"
  />
</template>
