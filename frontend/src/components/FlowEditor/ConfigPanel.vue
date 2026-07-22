<script setup lang="ts">
import { computed, provide, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { useFlowStore } from '@/stores/flowStore'
import { DArrowLeft, DArrowRight, CircleClose, Delete, Plus } from '@element-plus/icons-vue'
import { knowledgeBaseApi } from '@/api/knowledge'
import { useNodeSchema } from '@/composables/useNodeSchema'
import type { KnowledgeBase } from '@/types/knowledge'
import type { FlowIOField, FieldType } from '@/types/flow'
import type { CardConfig } from './config/types'
import ToolEdgeCondition from './components/ToolEdgeCondition.vue'
import { getNodeEntry, getConfigComponent, type NodeConfigContext } from './nodeRegistry'

const store = useFlowStore()
const {
  load: loadSchemas,
  getOutputVariables,
  getInputVariables,
  isLoaded,
  loading
} = useNodeSchema()

const schemaLoading = computed(() => !isLoaded() && loading.value)

const props = defineProps<{
  collapsed?: boolean
  isAgentMode?: boolean
  mobileOpen?: boolean
}>()

const isAgentMode = computed(() => props.isAgentMode)

const emit = defineEmits<{
  (e: 'toggle'): void
  (e: 'closeMobile'): void
}>()

const nodeLabel = ref('')
const edgeLabel = ref('')
const currentConfig = ref<Record<string, unknown>>({})

const selectedNode = computed(() => store.selectedNode)
const selectedEdge = computed(() => store.selectedEdge)

const isSubViewStartNode = computed(() => selectedNode.value?.type === 'start' && store.isInSubView)

const parentLoopMappings = computed(() => {
  if (!store.subViewParentId) return []
  const loopNode = store.nodes.find(n => n.id === store.subViewParentId)
  return (loopNode?.data?.config?.input_mappings as CardConfig['input_mappings']) || []
})

// ---- 注册表驱动 ----

const currentEntry = computed(() =>
  selectedNode.value?.type ? getNodeEntry(selectedNode.value.type) : undefined
)

const configComponent = computed(() =>
  selectedNode.value?.type ? getConfigComponent(selectedNode.value.type) : undefined
)

/** 构建 hook 上下文 */
function buildContext(): NodeConfigContext {
  return {
    selectedNodeId: selectedNode.value!.id,
    isInSubView: store.isInSubView,
    isAgentMode: isAgentMode.value,
    flowInfo: store.flowInfo as NodeConfigContext['flowInfo'],
    getOutputVariables,
    getInputVariables,
    setNodeLabel: (label: string) => {
      nodeLabel.value = label
    },
    updateNodeData: (id: string, data: Record<string, unknown>) => {
      store.updateNodeData(id, data)
    },
    updateInputSchema: (fields: FlowIOField[]) => {
      store.updateInputSchema({ fields })
    },
    updateOutputSchema: (
      fields: { name: string; type: FieldType; description: string; required: boolean }[]
    ) => {
      store.updateOutputSchema({ fields })
    }
  }
}

/** 是否渲染配置面板 */
const showConfig = computed(() => {
  if (!selectedNode.value?.type || !configComponent.value || !currentEntry.value) return false
  if (currentEntry.value.shouldRenderConfig) {
    return currentEntry.value.shouldRenderConfig(buildContext())
  }
  return true
})

/** 配置面板额外 props */
const configExtraProps = computed(() => {
  if (!currentEntry.value?.getExtraProps) return {}
  return currentEntry.value.getExtraProps(buildContext())
})

/** 配置面板事件（标准 + 额外） */
const configEvents = computed(() => {
  const events: Record<string, (...args: unknown[]) => void> = {
    'update:config': (config: unknown) => updateConfig(config as Record<string, unknown>)
  }
  if (currentEntry.value?.getExtraEvents) {
    Object.assign(events, currentEntry.value.getExtraEvents(buildContext()))
  }
  return events
})

/** Agent 模式文件上传提示 */
const showAgentFileHint = computed(() => {
  if (selectedNode.value?.type !== 'start' || store.isInSubView || !isAgentMode.value) return false
  const inputVars = currentConfig.value.input_variables as FlowIOField[] | undefined
  return inputVars?.some(f => f.type === 'file_list') ?? false
})

const knowledgeBases = ref<KnowledgeBase[]>([])

provide('knowledgeBases', knowledgeBases)

const flowConfig = ref({
  name: '',
  description: '',
  suggested_prompts: [] as string[]
})

watch(
  () => store.flowInfo,
  info => {
    if (info) {
      flowConfig.value = {
        name: info.name || '',
        description: info.description || '',
        suggested_prompts: info.suggested_prompts || []
      }
    }
  },
  { immediate: true }
)

async function loadKnowledgeBases(): Promise<void> {
  try {
    const res = await knowledgeBaseApi.page({ page: 1, page_size: 100, condition: { status: 1 } })
    if (res.data.code === 1 && res.data.data) {
      knowledgeBases.value = res.data.data.items
    }
  } catch {
    knowledgeBases.value = []
  }
}
loadKnowledgeBases()

// ---- 选中节点变化时初始化配置 ----

watch(selectedNode, async node => {
  if (!isLoaded()) await loadSchemas()
  nodeLabel.value = node?.data?.label || ''

  if (!node?.type) {
    currentConfig.value = {}
    return
  }

  const entry = getNodeEntry(node.type)
  if (!entry) {
    currentConfig.value = {}
    return
  }

  const rawConfig = (node.data?.config || {}) as Record<string, unknown>
  const ctx = buildContext()

  // defaults + initConfig 结果合并，确保所有字段有值
  const defaults = entry.defaultConfig()
  const initialized = entry.initConfig ? entry.initConfig(rawConfig, ctx) : rawConfig
  currentConfig.value = { ...defaults, ...initialized }

  // 初始化后副作用（如 start/end 同步 schema 到 store）
  if (entry.postInit) {
    entry.postInit(currentConfig.value, ctx)
  }
})

watch(selectedEdge, edge => {
  edgeLabel.value = (edge?.label as string) || ''
})

// ---- 更新函数 ----

function updateConfig(config: Record<string, unknown>): void {
  if (!selectedNode.value) return
  currentConfig.value = config
  store.updateNodeData(selectedNode.value.id, { config: { ...config } })
}

function updateNodeLabel(): void {
  if (selectedNode.value) {
    store.updateNodeData(selectedNode.value.id, { label: nodeLabel.value })
  }
}

function updateEdgeLabel(): void {
  if (!selectedEdge.value) return
  const storeEdge = store.edges.find(e => e.id === selectedEdge.value!.id)
  if (storeEdge) {
    storeEdge.label = edgeLabel.value
  }
  selectedEdge.value.label = edgeLabel.value
}

// ---- 工具边条件双向绑定 ----

const edgeCondition = computed(() => {
  const edgeId = store.selectedEdge?.id
  if (!edgeId) return null
  const storeEdge = store.edges.find(e => e.id === edgeId)
  const data = storeEdge?.data
  if (data && typeof data === 'object') {
    return data as Record<string, unknown>
  }
  return null
})

function updateEdgeCondition(val: Record<string, unknown> | null): void {
  const edgeId = store.selectedEdge?.id
  if (!edgeId) return
  const storeEdge = store.edges.find(e => e.id === edgeId)
  if (storeEdge) {
    storeEdge.data = val as Record<string, unknown> | undefined
  }
  if (store.selectedEdge) {
    store.selectedEdge.data = val as Record<string, unknown> | undefined
  }
}

// ---- 删除/保存 ----

async function deleteNode(): Promise<void> {
  if (!selectedNode.value) return
  try {
    await ElMessageBox.confirm('确定要删除该节点吗？', '删除确认', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    store.removeNode(selectedNode.value.id)
  } catch {
    // 用户取消
  }
}

function deleteEdge(): void {
  if (selectedEdge.value) {
    store.removeEdge(selectedEdge.value.id)
  }
}

function updateFlowConfig(): void {
  if (store.flowInfo) {
    store.flowInfo.name = flowConfig.value.name
    store.flowInfo.description = flowConfig.value.description
    store.flowInfo.suggested_prompts = flowConfig.value.suggested_prompts.filter(p => p.trim())
  }
}

function addPrompt(): void {
  flowConfig.value.suggested_prompts.push('')
}

function removePrompt(index: number): void {
  flowConfig.value.suggested_prompts.splice(index, 1)
  updateFlowConfig()
}

async function saveFlowConfig(): Promise<void> {
  updateFlowConfig()
  await store.saveFlow()
}

async function handleToggleCard(val: boolean | string): Promise<void> {
  if (val) {
    await store.saveAsCard()
  }
}
</script>

<template>
  <div class="config-panel" :class="{ collapsed, 'mobile-open': mobileOpen }">
    <div v-if="mobileOpen" class="mobile-panel-header">
      <h2 class="mobile-panel-title">节点配置</h2>
      <button class="mobile-close-btn" @click="emit('closeMobile')">
        <el-icon size="18"><CircleClose /></el-icon>
      </button>
    </div>

    <div v-if="!mobileOpen" class="toggle-btn" @click="emit('toggle')">
      <el-icon>
        <DArrowLeft v-if="collapsed" />
        <DArrowRight v-else />
      </el-icon>
    </div>

    <template v-if="!collapsed || mobileOpen">
      <div class="panel-header">
        <div class="panel-header-row">
          <h2 class="panel-title">节点配置</h2>
          <el-button v-if="selectedNode" type="danger" size="small" @click="deleteNode">
            删除节点
          </el-button>
          <el-button v-else-if="selectedEdge" type="danger" size="small" @click="deleteEdge">
            删除连线
          </el-button>
          <el-button
            v-else
            type="primary"
            size="small"
            :loading="store.saving"
            @click="saveFlowConfig"
          >
            保存
          </el-button>
        </div>
        <p class="panel-subtitle">Properties</p>
      </div>

      <div v-if="selectedNode" v-loading="schemaLoading" class="config-content">
        <div class="config-section">
          <div class="section-title">基本信息</div>
          <el-form label-width="70px" size="small">
            <el-form-item label="ID">
              <el-input :value="selectedNode.id" disabled />
            </el-form-item>
            <el-form-item label="类型">
              <el-input :value="selectedNode.type" disabled />
            </el-form-item>
            <el-form-item label="名称">
              <el-input v-model="nodeLabel" @blur="updateNodeLabel" />
            </el-form-item>
          </el-form>
        </div>

        <!-- 动态配置组件（注册表驱动） -->
        <component
          :is="configComponent"
          v-if="showConfig"
          :config="currentConfig"
          :current-node-id="selectedNode.id"
          v-bind="configExtraProps"
          v-on="configEvents"
        />

        <!-- 子视图 start 节点：显示来自循环节点的输入映射 -->
        <div v-if="isSubViewStartNode" class="config-section">
          <div class="section-title">输入映射（来自循环节点）</div>
          <div v-if="parentLoopMappings.length" class="sub-view-mappings">
            <div v-for="(m, i) in parentLoopMappings" :key="i" class="sub-view-mapping">
              <el-tag size="small" type="info">{{ m.type || 'string' }}</el-tag>
              <el-tag size="small" type="warning">{{ m.card_field }}</el-tag>
              <span class="mapping-arrow">&larr;</span>
              <el-tag size="small" type="info">{{ m.source }}</el-tag>
            </div>
          </div>
          <el-text v-else size="small" type="info">未配置输入映射，请在循环节点中设置</el-text>
          <div class="config-hint" style="margin-top: 8px">
            <el-text size="small" type="info">
              子图内通过 input.变量名 或 nodes.循环节点.字段名 访问映射输入，同时可使用
              variables.loop_index、variables.loop_count、variables.loop_item
            </el-text>
          </div>
        </div>

        <!-- Agent 文件上传提示 -->
        <div v-if="showAgentFileHint" class="agent-file-hint">
          <el-text size="small" type="info">
            定义的文件上传字段将在 Agent 对话界面中显示为附件上传入口，用户上传的文件路径会通过
            <code>input.files</code>
            传递给流程。
          </el-text>
        </div>
      </div>

      <div v-else-if="selectedEdge" class="config-content">
        <div class="config-section">
          <div class="section-title">边配置</div>
          <el-form label-width="60px" size="small">
            <el-form-item label="源节点">
              <el-input :value="selectedEdge.source" disabled />
            </el-form-item>
            <el-form-item label="目标">
              <el-input :value="selectedEdge.target" disabled />
            </el-form-item>
            <el-form-item label="标签">
              <el-input v-model="edgeLabel" @blur="updateEdgeLabel" />
            </el-form-item>
          </el-form>
          <!-- 工具边条件配置 -->
          <ToolEdgeCondition
            :model-value="edgeCondition"
            @update:model-value="updateEdgeCondition"
          />
        </div>
      </div>

      <div v-else class="config-content">
        <div class="config-section">
          <div class="section-title">基本信息</div>
          <el-form label-width="80px" size="small">
            <el-form-item label="名称">
              <el-input
                v-model="flowConfig.name"
                placeholder="请输入名称"
                @blur="updateFlowConfig"
              />
            </el-form-item>
            <el-form-item label="类型">
              <el-tag :type="isAgentMode ? 'success' : 'primary'">
                {{ isAgentMode ? '智能体' : '流程' }}
              </el-tag>
            </el-form-item>
            <el-form-item label="能力卡片">
              <div class="switch-row">
                <el-switch
                  :model-value="!!store.flowInfo?.saved_as_card"
                  :disabled="isAgentMode"
                  @change="handleToggleCard"
                />
                <span class="switch-label">
                  {{ store.flowInfo?.saved_as_card ? '是' : '否' }}
                </span>
              </div>
            </el-form-item>
            <el-form-item label="描述">
              <el-input
                v-model="flowConfig.description"
                type="textarea"
                :rows="3"
                placeholder="描述这个流程或智能体的用途"
                @blur="updateFlowConfig"
              />
            </el-form-item>
            <el-form-item label="建议提问">
              <div class="prompts-editor">
                <div
                  v-for="(p, i) in flowConfig.suggested_prompts"
                  :key="i"
                  class="prompt-row"
                >
                  <el-input
                    v-model="flowConfig.suggested_prompts[i]"
                    placeholder="输入建议提问内容，发送后即保存"
                    size="small"
                    @blur="updateFlowConfig"
                    @keydown.enter="updateFlowConfig"
                  />
                  <el-button
                    :icon="Delete"
                    circle
                    size="small"
                    style="flex-shrink: 0; margin-left: 4px"
                    @click="removePrompt(i)"
                  />
                </div>
                <el-button :icon="Plus" size="small" @click="addPrompt">添加</el-button>
              </div>
            </el-form-item>
          </el-form>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.config-panel {
  width: 320px;
  background: #fff;
  border-left: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  position: relative;
  height: 100%;
  min-height: 0;
}

.config-panel.collapsed {
  width: 24px;
}

.toggle-btn {
  position: absolute;
  left: -12px;
  top: 50%;
  transform: translateY(-50%);
  width: 24px;
  height: 48px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px 0 0 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 100;
  transition: background 0.2s;
}

.toggle-btn:hover {
  background: #f8fafc;
}

.toggle-btn .el-icon {
  font-size: 12px;
  color: #94a3b8;
}

.panel-header {
  padding: 20px 24px;
  border-bottom: 1px solid #f1f5f9;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.panel-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.panel-title {
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
  margin: 0;
}

.panel-subtitle {
  font-size: 10px;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-weight: 600;
  margin: 0;
}

.config-content {
  flex: 1;
  padding: 24px;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.config-section {
  margin-bottom: 24px;
}

.section-title {
  font-size: 12px;
  font-weight: 700;
  color: #334155;
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.config-section .el-form-item__label {
  font-size: 10px;
  font-weight: 700;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.config-actions {
  padding-top: 16px;
  border-top: 1px solid #f1f5f9;
  margin-top: auto;
}

.config-actions .el-button {
  border-radius: 12px;
  font-weight: 700;
}

.switch-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.switch-label {
  font-size: 12px;
  color: #64748b;
  font-weight: 500;
}

.agent-file-hint {
  margin-top: 12px;
  padding: 12px;
  background: #eff6ff;
  border: 1px solid #dbeafe;
  border-radius: 12px;
}

.agent-file-hint code {
  background: #ecfdf5;
  padding: 2px 6px;
  border-radius: 6px;
  font-size: 12px;
  color: #059669;
}

.sub-view-mappings {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sub-view-mapping {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mapping-arrow {
  color: #94a3b8;
  font-size: 14px;
}

.mobile-panel-header {
  display: none;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #e2e8f0;
}

.mobile-panel-title {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  margin: 0;
}

.mobile-close-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: #f1f5f9;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #64748b;
}

.prompts-editor {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}

.prompt-row {
  display: flex;
  align-items: center;
  width: 100%;
}

@media (max-width: 768px) {
  .config-panel {
    position: fixed;
    right: 0;
    top: 0;
    bottom: 0;
    width: 90vw;
    max-width: 400px;
    z-index: 200;
    transform: translateX(100%);
    transition: transform 0.3s ease;
    box-shadow: none;
  }

  .config-panel.mobile-open {
    transform: translateX(0);
    box-shadow: -4px 0 24px rgba(0, 0, 0, 0.15);
  }

  .config-panel.collapsed {
    width: 90vw;
    max-width: 400px;
  }

  .toggle-btn {
    display: none;
  }

  .mobile-panel-header {
    display: flex;
  }

  .panel-header {
    padding: 16px 20px;
  }
}
</style>
