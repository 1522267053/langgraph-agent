<script setup lang="ts">
import { Key } from '@element-plus/icons-vue'
import BaseNode from './BaseNode.vue'

const props = defineProps<{
  id: string
  data: { label?: string; config?: Record<string, unknown> }
  selected?: boolean
}>()

// 纯工具提供者（同 MCP）：仅 tools 工具输出口，无普通入图连线
const handles = [
  {
    type: 'source' as const,
    position: 'right' as const,
    id: 'tools',
    label: '工具',
    color: 'orange' as const
  }
]
</script>

<template>
  <BaseNode
    :id="props.id"
    :data="props.data"
    :selected="props.selected"
    node-type="ssh"
    color="#14b8a6"
    :icon="Key"
    type-label="SSH命令"
    :handles="handles"
  />
</template>
