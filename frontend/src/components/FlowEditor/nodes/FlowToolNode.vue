<script setup lang="ts">
import { Connection } from '@element-plus/icons-vue'
import { computed, inject, ref, type Ref } from 'vue'
import BaseNode from './BaseNode.vue'

const props = defineProps<{
  id: string
  data: { label?: string; config?: Record<string, unknown> }
  selected?: boolean
}>()

// 注入可用的 Flow 列表（与 SubAgentNode 一致约定，由 ConfigPanel 提供）
const flows = inject<Ref<{ id: number; name: string; status?: number }[]>>(
  'flows',
  ref([])
)

const flowName = computed(() => {
  const flowId = props.data?.config?.flow_id as number
  if (!flowId) return ''
  const flow = flows.value.find(f => f.id === flowId)
  return flow?.name || ''
})

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
    node-type="flow_tool"
    color="#8b5cf6"
    :icon="Connection"
    type-label="Flow工具"
    :handles="handles"
  >
    <template #content>
      <div v-if="flowName" class="flow-tool-info">{{ flowName }}</div>
    </template>
  </BaseNode>
</template>

<style scoped>
.flow-tool-info {
  font-size: 10px;
  color: #64748b;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 120px;
}
</style>
