<script setup lang="ts">
import { ref, watch } from 'vue'
import { mcpServerApi } from '@/api/mcpServer'
import type { McpConfig } from './types'
import ApprovalConfigSection, { type ApprovalConfig } from './ApprovalConfigSection.vue'
import { useFlowStore } from '@/stores/flowStore'
import { flowApi } from '@/api/flow'

const props = defineProps<{
  config: McpConfig
  nodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: McpConfig): void
}>()

const mcpServers = ref<{ id: number; name: string; description?: string }[]>([])
const loading = ref(false)

async function loadMcpServers(): Promise<void> {
  if (mcpServers.value.length || loading.value) return
  loading.value = true
  try {
    const res = await mcpServerApi.list()
    if (res.data.code === 1 && res.data.data) {
      mcpServers.value = res.data.data
    }
  } catch {
    mcpServers.value = []
  } finally {
    loading.value = false
  }
}
loadMcpServers()

function cloneConfig(c: McpConfig): McpConfig {
  return {
    ...c,
    mcp_server_ids: [...(c.mcp_server_ids ?? [])],
    approval_required_tools: Array.isArray(c.approval_required_tools)
      ? [...c.approval_required_tools]
      : [],
    approval_required_patterns: Array.isArray(c.approval_required_patterns)
      ? [...c.approval_required_patterns]
      : []
  }
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

// ---- 工具审批配置（approval 已下沉到本节点，复用 ApprovalConfigSection）----

/** 调后端 resolveConnectedTools 拿本节点实际暴露的 MCP 工具名（mcp__<server>__<tool>） */
const flowStore = useFlowStore()
const mcpAvailableTools = ref<{ label: string; value: string }[]>([])
let mcpToolRequestVersion = 0

async function fetchMcpAvailableTools(): Promise<void> {
  const version = ++mcpToolRequestVersion
  const flowId = flowStore.flowInfo?.id
  if (!flowId || !props.nodeId) {
    mcpAvailableTools.value = []
    return
  }
  try {
    const res = await flowApi.resolveConnectedTools(flowId, [
      {
        node_key: props.nodeId,
        node_type: 'mcp',
        node_name: '',
        base_config: localConfig.value
      }
    ])
    if (version !== mcpToolRequestVersion) return
    if (res.data.code === 1 && Array.isArray(res.data.data)) {
      const tools = res.data.data.flatMap(g => g.tools || [])
      mcpAvailableTools.value = tools.map(t => ({
        label: t.description ? `${t.name} - ${t.description.slice(0, 30)}` : t.name,
        value: t.name
      }))
    } else {
      mcpAvailableTools.value = []
    }
  } catch {
    if (version === mcpToolRequestVersion) mcpAvailableTools.value = []
  }
}

// mcp_server_ids 变化时（连了不同 MCP server）必须重拉
watch(
  () => [props.nodeId, [...(localConfig.value.mcp_server_ids || [])]],
  () => fetchMcpAvailableTools(),
  { immediate: true, deep: true }
)

function onApprovalUpdate(val: ApprovalConfig): void {
  localConfig.value.approval_required_tools = [...val.approval_required_tools]
  localConfig.value.approval_required_patterns = [...val.approval_required_patterns]
  updateConfig()
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
            :loading="loading"
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

    <ApprovalConfigSection
      :model-value="{
        approval_required_tools: localConfig.approval_required_tools,
        approval_required_patterns: localConfig.approval_required_patterns
      }"
      :available-tools="mcpAvailableTools"
      tools-placeholder="下拉为已连接 MCP 服务器实际加载的工具（mcp__<server>__<tool>）；可手动输入其他名"
      pattern-placeholder="正则表达式（Python re 语法，对 工具名 + 调用参数 拼接的字符串逐条 re.search(content, ...)）"
      hint="AI 调用本节点加载的任意 MCP 工具时：工具名精确匹配「需审批工具名」，或调用内容命中任一正则，则弹审批；仅 Agent 模式生效，留空时不过审批。"
      @update:model-value="onApprovalUpdate"
    />
  </div>
</template>

<style scoped>
@import './config-styles.css';

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
