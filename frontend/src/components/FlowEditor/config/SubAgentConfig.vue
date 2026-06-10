<script setup lang="ts">
import { inject, ref, watch, type Ref } from 'vue'
import type { SubAgentConfig } from './types'

const props = defineProps<{
  config: SubAgentConfig
  nodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: SubAgentConfig): void
  (e: 'update:label', value: string): void
}>()

const agents = inject<Ref<{ id: number; name: string; description?: string }[]>>(
  'agents',
  ref([])
)

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
            placeholder="选择已发布的Agent"
            style="width: 100%"
            filterable
            @change="updateConfig"
          >
            <el-option
              v-for="agent in agents"
              :key="agent.id"
              :label="agent.name"
              :value="agent.id"
            >
              <div class="agent-option">
                <span class="agent-name">{{ agent.name }}</span>
                <span v-if="agent.description" class="agent-desc">
                  {{ agent.description }}
                </span>
              </div>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          子Agent节点需通过工具连接到LLM节点使用。
          被引用的Agent必须已发布且填写了描述。
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

.agent-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.agent-name {
  font-size: 13px;
}

.agent-desc {
  font-size: 11px;
  color: #94a3b8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
