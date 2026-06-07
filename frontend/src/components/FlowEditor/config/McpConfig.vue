<script setup lang="ts">
import { inject, ref, watch, type Ref } from 'vue'
import type { McpConfig } from './types'

const props = defineProps<{
  config: McpConfig
  nodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: McpConfig): void
}>()

const mcpServers = inject<Ref<{ id: number; name: string; description?: string }[]>>(
  'mcpServers',
  ref([])
)

function cloneConfig(c: McpConfig): McpConfig {
  return { ...c, mcp_server_ids: [...(c.mcp_server_ids ?? [])] }
}

const localConfig = ref<McpConfig>(cloneConfig(props.config))

watch(
  () => props.config,
  newConfig => {
    localConfig.value = cloneConfig(newConfig)
  },
  { deep: true, immediate: true }
)

function updateConfig(): void {
  const names = mcpServers.value
    .filter(s => localConfig.value.mcp_server_ids.includes(s.id))
    .map(s => s.name)
  emit('update:config', {
    ...localConfig.value,
    mcp_server_ids: [...localConfig.value.mcp_server_ids],
    mcp_server_names: names
  })
}
</script>

<template>
  <div class="mcp-config">
    <div class="config-section">
      <div class="section-title">MCP服务器配置</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="服务器">
          <el-select
            v-model="localConfig.mcp_server_ids"
            placeholder="选择MCP服务器"
            style="width: 100%"
            multiple
            collapse-tags
            collapse-tags-tooltip
            @change="updateConfig"
          >
            <el-option
              v-for="server in mcpServers"
              :key="server.id"
              :label="server.name"
              :value="server.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          将此节点连接到LLM节点（使用"工具"连接点），LLM即可调用所选服务器的工具
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
  margin-top: 12px;
  padding: 8px;
  background: #fdf6ec;
  border-radius: 4px;
}
</style>
