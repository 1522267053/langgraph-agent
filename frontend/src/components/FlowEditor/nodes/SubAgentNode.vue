<script setup lang="ts">
import { Avatar } from '@element-plus/icons-vue'
import { computed, inject, ref, type Ref } from 'vue'
import BaseNode from './BaseNode.vue'

const props = defineProps<{
  id: string
  data: { label?: string; config?: Record<string, unknown> }
  selected?: boolean
}>()

const agents = inject<Ref<{ id: number; name: string; description?: string }[]>>(
  'agents',
  ref([])
)

const agentName = computed(() => {
  const agentId = props.data?.config?.agent_id as number
  if (!agentId) return ''
  const agent = agents.value.find(a => a.id === agentId)
  return agent?.name || ''
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
    node-type="sub_agent"
    color="#3b82f6"
    :icon="Avatar"
    type-label="子Agent"
    :handles="handles"
  >
    <template #content>
      <div v-if="agentName" class="sub-agent-info">{{ agentName }}</div>
    </template>
  </BaseNode>
</template>

<style scoped>
.sub-agent-info {
  font-size: 10px;
  color: #64748b;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 120px;
}
</style>
