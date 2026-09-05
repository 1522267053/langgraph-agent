<script setup lang="ts">
import { QuestionFilled } from '@element-plus/icons-vue'
import BaseNode from './BaseNode.vue'

const props = defineProps<{
  id: string
  data: { label?: string; config?: Record<string, unknown> }
  selected?: boolean
}>()

// 工具节点：仅暴露 tools 源边，连接到 LLM 节点的 tools 入边
const handles = [
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
    node-type="question"
    color="#a855f7"
    :icon="QuestionFilled"
    type-label="问题反问"
    :handles="handles"
  />
</template>
