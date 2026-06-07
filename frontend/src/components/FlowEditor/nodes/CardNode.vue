<script setup lang="ts">
import { Postcard } from '@element-plus/icons-vue'
import BaseNode from './BaseNode.vue'

const props = defineProps<{
  id: string
  data: { label?: string }
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
    node-type="card"
    color="#10b981"
    :icon="Postcard"
    type-label="能力卡片"
    :handles="handles"
  />
</template>
