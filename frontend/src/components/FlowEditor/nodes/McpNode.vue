<script setup lang="ts">
import { computed, inject, ref, type Ref } from 'vue'
import { Connection } from '@element-plus/icons-vue'
import BaseNode from './BaseNode.vue'

const props = defineProps<{
  id: string
  data: { label?: string; config?: Record<string, unknown> }
  selected?: boolean
}>()

const mcpServers = inject<Ref<{ id: number; name: string; description?: string }[]>>(
  'mcpServers',
  ref([])
)

const handles = [
  {
    type: 'source' as const,
    position: 'right' as const,
    id: 'tools',
    label: '工具',
    color: 'blue' as const
  }
]

const mcpServerNames = computed(() => {
  const ids = (props.data?.config?.mcp_server_ids as number[]) || []
  return ids
    .map(id => mcpServers.value.find(s => s.id === id)?.name)
    .filter(Boolean)
    .join(', ')
})
</script>

<template>
  <BaseNode
    :id="props.id"
    :data="props.data"
    :selected="props.selected"
    node-type="mcp"
    color="#6366f1"
    :icon="Connection"
    type-label="MCP调用"
    :handles="handles"
  >
    <template #content>
      <el-icon size="18"><Connection /></el-icon>
      <div class="node-text">
        <span class="node-title">{{ props.data?.label || 'MCP调用' }}</span>
        <span v-if="mcpServerNames" class="node-subtitle">{{ mcpServerNames }}</span>
      </div>
    </template>
  </BaseNode>
</template>

<style scoped>
.node-text {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

.node-title {
  font-size: 14px;
}

.node-subtitle {
  font-size: 11px;
  opacity: 0.8;
}
</style>
