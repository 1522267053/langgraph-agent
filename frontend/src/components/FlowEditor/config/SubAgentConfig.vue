<script setup lang="ts">
import { ref, watch } from 'vue'
import { agentApi } from '@/api/agent'
import { useFlowStore } from '@/stores/flowStore'
import type { SubAgentConfig } from './types'

const props = defineProps<{
  config: SubAgentConfig
  nodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: SubAgentConfig): void
  (e: 'update:label', value: string): void
}>()

const store = useFlowStore()
const agents = ref<{ id: number; name: string; description?: string }[]>([])
const loaded = ref(false)
const loading = ref(false)

const localConfig = ref<SubAgentConfig>({ agent_id: null })

watch(
  () => props.config,
  newConfig => {
    localConfig.value = {
      agent_id: newConfig.agent_id || null
    }
  },
  { deep: true, immediate: true }
)

async function loadAgents(): Promise<void> {
  if (loaded.value || loading.value) return
  loading.value = true
  try {
    const currentId = store.flowInfo.value?.id
    const res = await agentApi.list(currentId)
    if (res.data.code === 1 && res.data.data) {
      agents.value = res.data.data.list || []
    }
    loaded.value = true
  } catch {
    agents.value = []
  } finally {
    loading.value = false
  }
}
loadAgents()

function updateConfig(): void {
  const selectedAgent = agents.value.find(a => a.id === localConfig.value.agent_id)
  if (selectedAgent) {
    emit('update:label', selectedAgent.name)
  }
  emit('update:config', { ...localConfig.value })
}
</script>

<template>
  <div class="sub-agent-config">
    <div class="config-section">
      <div class="section-title">子Agent配置</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="选择Agent">
          <el-select
            v-model="localConfig.agent_id"
            placeholder="选择Agent"
            style="width: 100%"
            filterable
            :loading="loading"
            @change="updateConfig"
          >
            <el-option
              v-for="agent in agents"
              :key="agent.id"
              :label="agent.name"
              :value="agent.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          子Agent节点需通过工具连接到LLM节点使用。 被引用的Agent必须填写了描述。
        </el-text>
      </div>
    </div>
  </div>
</template>

<style scoped>
.config-section {
  margin-bottom: 16px;
}

.section-title {
  font-size: 13px;
  color: #606266;
  margin-bottom: 12px;
  font-weight: 500;
}

.config-hint {
  margin-top: 8px;
  line-height: 1.5;
}
</style>
