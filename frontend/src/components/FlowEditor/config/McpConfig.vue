<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'
import { mcpServerApi } from '@/api/mcpServer'
import type { McpConfig } from './types'
import { fieldTypeOptions } from './types'
import ApprovalConfigSection, { type ApprovalConfig } from './ApprovalConfigSection.vue'
import { useInputVariables } from '@/composables/useInputVariables'
import VariableSelector from '../components/VariableSelector.vue'
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
    tool_args: { ...(c.tool_args ?? {}) },
    input_variables: (c.input_variables ?? []).map(v => ({ ...v })),
    output_variables: (c.output_variables ?? []).map(v => ({ ...v })),
    approval_required_tools: Array.isArray(c.approval_required_tools)
      ? [...c.approval_required_tools]
      : [],
    approval_required_patterns: Array.isArray(c.approval_required_patterns)
      ? [...c.approval_required_patterns]
      : []
  }
}

const localConfig = ref<McpConfig>(cloneConfig(props.config))
const { addInputVariable, removeInputVariable, handleSourceTypeChange } = useInputVariables(
  localConfig,
  updateConfig
)

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

/** 调后端 resolveConnectedTools 拿本节点实际暴露的 MCP 工具元数据（名称/描述/参数schema） */
const flowStore = useFlowStore()
interface McpToolMeta {
  label: string
  value: string
  parameters?: { name: string; type: string; description: string; required: boolean }[]
}
const mcpToolMetas = ref<McpToolMeta[]>([])
const mcpAvailableTools = computed(() =>
  mcpToolMetas.value.map(t => ({ label: t.label, value: t.value }))
)
let mcpToolRequestVersion = 0

async function fetchMcpAvailableTools(): Promise<void> {
  const version = ++mcpToolRequestVersion
  const flowId = flowStore.flowInfo?.id
  if (!flowId || !props.nodeId) {
    mcpToolMetas.value = []
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
      mcpToolMetas.value = tools.map(t => ({
        label: t.description ? `${t.name} - ${t.description.slice(0, 30)}` : t.name,
        value: t.name,
        parameters: t.parameters || []
      }))
    } else {
      mcpToolMetas.value = []
    }
  } catch {
    if (version === mcpToolRequestVersion) mcpToolMetas.value = []
  }
  syncSelectedToolParams()
}

// mcp_server_ids 变化时（连了不同 MCP server）必须重拉；
// 用序列化串做比较——cloneConfig 会整体替换 localConfig，直接 watch 数组
// 会因引用不等而频繁误触发（每次配置编辑都多发一次 resolve 请求）
watch(
  () => [props.nodeId, JSON.stringify(localConfig.value.mcp_server_ids || [])],
  () => fetchMcpAvailableTools(),
  { immediate: true }
)

function onApprovalUpdate(val: ApprovalConfig): void {
  localConfig.value.approval_required_tools = [...val.approval_required_tools]
  localConfig.value.approval_required_patterns = [...val.approval_required_patterns]
  updateConfig()
}

// ---- 直接执行模式（挂到主干流程：入→出，按固定工具+参数直接调用）----

/** 当前选中工具的参数 schema（来自 resolveConnectedTools 的 parameters 字段） */
interface ToolParam {
  name: string
  type: string
  description: string
  required: boolean
}
const selectedToolParams = ref<ToolParam[]>([])

/** 从 mcpAvailableTools 元数据提取选中工具的参数定义 */
function syncSelectedToolParams(): void {
  const toolName = localConfig.value.tool_name
  if (!toolName) {
    selectedToolParams.value = []
    return
  }
  // resolveConnectedTools 响应里 tools 带 name/description/parameters
  const found = mcpToolMetas.value.find(t => t.value === toolName)
  selectedToolParams.value = (found?.parameters as ToolParam[] | undefined) || []
}

/** 切换工具时重置参数绑定（保留同名参数的旧值） */
function onToolNameChange(): void {
  const oldArgs = { ...(localConfig.value.tool_args || {}) }
  const next: Record<string, string> = {}
  for (const p of selectedToolParams.value) {
    if (oldArgs[p.name] !== undefined) next[p.name] = oldArgs[p.name]
  }
  localConfig.value.tool_args = next
  updateConfig()
}

function onArgChange(): void {
  updateConfig()
}

</script>

<template>
  <div class="mcp-config">
    <div class="config-section">
      <div class="section-title">
        <span>输入变量</span>
        <el-button type="primary" size="small" link @click="addInputVariable">+ 添加变量</el-button>
      </div>
      <div class="input-variables">
        <div
          v-for="(variable, index) in localConfig.input_variables"
          :key="index"
          class="input-variable"
        >
          <div class="variable-header">
            <span class="variable-index">变量 {{ index + 1 }}</span>
            <el-button type="danger" size="small" link @click="removeInputVariable(index)">
              删除
            </el-button>
          </div>
          <el-form label-width="60px" size="small">
            <el-form-item label="名称">
              <el-input
                v-model="variable.name"
                placeholder="变量名（可在参数值中用双大括号引用）"
                @blur="updateConfig"
              />
            </el-form-item>
            <el-form-item label="类型">
              <el-select
                v-model="variable.type"
                placeholder="选择类型"
                style="width: 100%"
                @change="updateConfig"
              >
                <el-option
                  v-for="item in fieldTypeOptions"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="来源">
              <VariableSelector
                v-model="variable.source"
                :current-node-id="nodeId"
                placeholder="选择变量来源"
                @update:model-value="updateConfig"
                @update:type="t => handleSourceTypeChange(index, t)"
              />
            </el-form-item>
          </el-form>
        </div>
      </div>
      <div class="config-hint">
        <el-text size="small" type="info">
          直接执行参数值中用双大括号包裹变量名即可引用（如 target_url）
        </el-text>
      </div>
    </div>
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
          工具连接：连到LLM节点（"工具"把手）后由 AI 自主调用
          <br />
          直接执行：连接到主干流程（"入/出"把手），按下方固定配置直接执行
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">直接执行配置（挂到主干流程时生效）</div>
      <el-form label-width="100px" size="small">
        <el-form-item label="执行工具">
          <el-select
            v-model="localConfig.tool_name"
            placeholder="选择要直接执行的工具（留空=仅作为工具提供者）"
            style="width: 100%"
            clearable
            filterable
            :loading="loading"
            @change="onToolNameChange"
          >
            <el-option
              v-for="tool in mcpAvailableTools"
              :key="tool.value"
              :label="tool.label"
              :value="tool.value"
            />
            <template #empty>
              <div class="select-empty-hint">
                <el-text size="small" type="info">无可用工具</el-text>
                <br />
                <el-text size="small" type="info">检查 MCP 服务器是否在线；后端刚重启时首次加载会自动建立连接，稍后重试</el-text>
              </div>
            </template>
          </el-select>
        </el-form-item>
        <el-form-item
          v-for="param in selectedToolParams"
          :key="param.name"
          :required="param.required"
        >
          <template #label>
            {{ param.name }}
            <el-tooltip
              v-if="param.description"
              :content="`[${param.type}] ${param.description}`"
              placement="top"
            >
              <el-icon class="param-tip-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <el-input
            v-model="localConfig.tool_args[param.name]"
            :placeholder="param.type"
            @change="onArgChange"
          />
        </el-form-item>
        <el-form-item v-if="selectedToolParams.length" label="">
          <el-text size="small" type="info">
            参数值支持变量插值：双大括号包裹变量路径（语法同 API 节点，如输入变量的 message）
          </el-text>
        </el-form-item>
      </el-form>
    </div>

    <div class="config-section">
      <div class="section-title">输出变量</div>
      <div class="output-variables-info">
        <div v-for="ov in localConfig.output_variables" :key="ov.name" class="output-var-tag">
          <el-tag size="small" type="info">{{ ov.name }}</el-tag>
          <span class="output-var-type">{{ ov.type || '' }}</span>
        </div>
        <el-text size="small" type="info">工具执行结果写入（下游用 nodes.节点key.result 引用）</el-text>
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

.select-empty-hint {
  padding: 8px 12px;
  line-height: 1.6;
}

.param-tip-icon {
  margin-left: 4px;
  cursor: help;
  color: #909399;
}
.output-variables-info {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.output-var-tag {
  display: flex;
  align-items: center;
  gap: 4px;
}
.output-var-type {
  font-size: 12px;
  color: #909399;
}
</style>
