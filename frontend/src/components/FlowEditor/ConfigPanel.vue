<script setup lang="ts">
import { computed, provide, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { useFlowStore } from '@/stores/flowStore'
import { DArrowLeft, DArrowRight, CircleClose } from '@element-plus/icons-vue'
import { skillApi } from '@/api/skill'
import { knowledgeBaseApi } from '@/api/knowledge'
import { useNodeSchema } from '@/composables/useNodeSchema'
import type { Skill } from '@/types/skill'
import type { KnowledgeBase } from '@/types/knowledge'

import {
  LlmConfigComponent,
  ConditionConfigComponent,
  ApiConfigComponent,
  McpConfigComponent,
  HumanConfigComponent,
  SkillConfigComponent,
  KnowledgeConfigComponent,
  PythonConfigComponent,
  ShellConfigComponent,
  EndConfigComponent,
  CardConfigComponent,
  LoopConfigComponent,
  StartConfigComponent,
  MemoryConfigComponent,
  TodoConfigComponent,
  MediaGenConfigComponent,
  IntentRouterConfigComponent
} from './config'

import type {
  LlmConfig,
  ConditionConfig,
  ApiConfig,
  McpConfig,
  HumanConfig,
  SkillConfig,
  KnowledgeConfig,
  PythonConfig,
  ShellConfig,
  EndConfig,
  CardConfig,
  LoopConfig,
  StartConfig,
  MemoryConfig,
  TodoConfig,
  MediaGenNodeConfig,
  IntentRouterConfig,
  FlowIOField,
  FieldType,
  NodeVariable
} from './config'

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

const selectedNode = computed(() => store.selectedNode)
const selectedEdge = computed(() => store.selectedEdge)

const isStartNode = computed(() => selectedNode.value?.type === 'start')
const isSubViewStartNode = computed(() => isStartNode.value && store.isInSubView)
const isLlmNode = computed(() => selectedNode.value?.type === 'llm')
const isConditionNode = computed(() => selectedNode.value?.type === 'condition')

const parentLoopMappings = computed(() => {
  if (!store.subViewParentId) return []
  const loopNode = store.nodes.find(n => n.id === store.subViewParentId)
  return (loopNode?.data?.config?.input_mappings as CardConfig['input_mappings']) || []
})

const isEndNode = computed(() => selectedNode.value?.type === 'end')
const isSubViewEndNode = computed(() => isEndNode.value && store.isInSubView)
const isCardNode = computed(() => selectedNode.value?.type === 'card')
const isLoopNode = computed(() => selectedNode.value?.type === 'loop')
const isApiNode = computed(() => selectedNode.value?.type === 'api')
const isMcpNode = computed(() => selectedNode.value?.type === 'mcp')
const isHumanNode = computed(() => selectedNode.value?.type === 'human')
const isSkillNode = computed(() => selectedNode.value?.type === 'skill')
const isKnowledgeNode = computed(() => selectedNode.value?.type === 'knowledge')
const isPythonNode = computed(() => selectedNode.value?.type === 'python')
const isShellNode = computed(() => selectedNode.value?.type === 'shell')
const isMemoryNode = computed(() => selectedNode.value?.type === 'memory')
const isTodoNode = computed(() => selectedNode.value?.type === 'todo')
const isMediaGenNode = computed(() => selectedNode.value?.type === 'media_gen')
const isIntentRouterNode = computed(() => selectedNode.value?.type === 'intent_router')

const skills = ref<Skill[]>([])
const knowledgeBases = ref<KnowledgeBase[]>([])

provide('skills', skills)
provide('knowledgeBases', knowledgeBases)

const flowConfig = ref({
  name: '',
  description: ''
})

const llmConfig = ref<LlmConfig>({
  provider: '',
  model: '',
  api_key: '',
  base_url: '',
  capabilities: { image: false, video: false, audio: false, pdf: false, xlsx: false },
  input_variables: [],
  output_variables: [
    { name: 'result', source: '', type: undefined },
    { name: 'thinking', source: '', type: undefined }
  ],
  system_prompt: '',
  user_prompt: '',
  temperature: 0.7,
  max_tool_iterations: 5,
  max_tokens: 8192,
  history_mode: 'node',
  max_history_turns: 10
})

const conditionConfig = ref<ConditionConfig>({
  logic: 'and',
  rules: [{ variable: '', operator: '==', value: '' }]
})

const apiConfig = ref<ApiConfig>({
  api_url: '',
  method: 'GET',
  headers: '',
  body: '',
  content_type: 'application/json',
  form_fields: [],
  input_variables: [],
  output_variables: [
    { name: 'body', source: '', type: undefined },
    { name: 'status_code', source: '', type: 'number' },
    { name: 'headers', source: '', type: 'object' }
  ],
  file_config: { upload_fields: [], download: { enabled: false } }
})

const mcpConfig = ref<McpConfig>({ mcp_server_ids: [] })

const humanConfig = ref<HumanConfig>({
  assist_prompt: '',
  review_prompt: '',
  input_variables: [],
  output_variables: [{ name: 'feedback', source: '', type: undefined }]
})

const skillConfig = ref<SkillConfig>({ skill_ids: [] })

const knowledgeConfig = ref<KnowledgeConfig>({
  knowledge_base_id: null,
  knowledge_base_name: '',
  top_k: 5,
  input_variables: [],
  output_variables: [{ name: 'result', source: '', type: undefined }]
})

const pythonConfig = ref<PythonConfig>({
  code: '',
  timeout: 30,
  input_variables: [],
  output_variables: [{ name: 'result', source: '', type: undefined }]
})

const shellConfig = ref<ShellConfig>({
  command: '',
  timeout: 30,
  input_variables: [],
  output_variables: [
    { name: 'stdout', source: '', type: 'string' },
    { name: 'stderr', source: '', type: 'string' },
    { name: 'exit_code', source: '', type: 'number' }
  ]
})

const memoryConfig = ref<MemoryConfig>({
  max_results: 5,
  default_importance: 3,
  default_category: 'event',
  max_index_lines: 200,
  max_index_bytes: 25000,
  auto_promote_threshold: 5,
  consolidate_threshold: 50,
  hot_decay_days: 30,
  warm_decay_days: 60,
  consolidate_interval_days: 7
})

const todoConfig = ref<TodoConfig>({})

const mediaGenConfig = ref<MediaGenNodeConfig>({
  media_type: 'image',
  image: {
    enabled: true,
    provider: 'openai_compatible',
    model: 'dall-e-3',
    api_key: '',
    base_url: '',
    params: {}
  },
  audio: {
    enabled: false,
    provider: 'openai_compatible',
    model: 'tts-1',
    api_key: '',
    base_url: '',
    params: {}
  },
  video: {
    enabled: false,
    provider: 'minimax',
    model: 'video-01',
    api_key: '',
    base_url: '',
    params: {}
  },
  output_variables: [
    { name: 'url', source: '', type: 'string' },
    { name: 'media_type', source: '', type: 'string' }
  ],
  input_variables: []
})

const endConfig = ref<EndConfig>({ output_variables: [] })

const cardConfig = ref<CardConfig>({
  ref_flow_id: 0,
  input_schema: null,
  output_schema: null,
  input_mappings: [],
  output_mappings: []
})

const loopConfig = ref<LoopConfig>({
  loop_mode: 'count',
  max_count: 10,
  condition_expression: '',
  for_each_source: '',
  for_each_item_type: undefined,
  break_on_error: true,
  concurrency: 1,
  input_mappings: [],
  output_variables: []
})

const startConfig = ref<StartConfig>({ input_variables: [] })

const intentRouterConfig = ref<IntentRouterConfig>({
  enable_rule_layer: true,
  enable_llm_layer: true,
  case_sensitive: false,
  provider: '',
  model: '',
  api_key: '',
  base_url: '',
  temperature: 0.1,
  max_tokens: 200,
  system_prompt: '',
  confidence_threshold: 0.6,
  input_variable: 'input.question',
  intents: []
})

watch(
  () => store.flowInfo,
  info => {
    if (info) {
      flowConfig.value = {
        name: info.name || '',
        description: info.description || ''
      }
    }
  },
  { immediate: true }
)

async function loadSkills(): Promise<void> {
  try {
    const res = await skillApi.list()
    if (res.data.code === 1 && res.data.data) {
      skills.value = res.data.data
    }
  } catch {
    skills.value = []
  }
}
loadSkills()

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

function migrateOutputVariable(name: string, thinkingName?: string): NodeVariable[] {
  const vars: NodeVariable[] = [{ name, source: '', type: undefined }]
  if (thinkingName) {
    vars.push({ name: thinkingName, source: '', type: undefined })
  }
  return vars
}

function resolveOutputVars(config: Record<string, unknown>, nodeType: string): NodeVariable[] {
  if (config.output_variables) return config.output_variables as NodeVariable[]
  if (config.output_variable) return migrateOutputVariable(config.output_variable as string)
  return getOutputVariables(nodeType)
}

function resolveLlmOutputVars(config: Record<string, unknown>): NodeVariable[] {
  if (config.output_variables) return config.output_variables as NodeVariable[]
  if (config.output_variable) {
    const name = config.output_variable as string
    const thinkingName = (config.thinking_variable as string) || ''
    return migrateOutputVariable(name, thinkingName)
  }
  return getOutputVariables('llm')
}

function resolveInputVars(config: Record<string, unknown>, nodeType: string): NodeVariable[] {
  if (config.input_variables) return config.input_variables as NodeVariable[]
  return getInputVariables(nodeType)
}

watch(selectedNode, async node => {
  if (!isLoaded()) await loadSchemas()
  nodeLabel.value = node?.data?.label || ''
  const rawConfig = (node?.data?.config || {}) as Record<string, unknown>

  if (node?.type === 'start') {
    const nodeVars = rawConfig.input_variables as FlowIOField[] | undefined
    const schemaFallback = store.flowInfo?.input_schema?.fields || []
    const fields = nodeVars && nodeVars.length > 0 ? nodeVars : schemaFallback
    if (isAgentMode.value) {
      if (fields.length === 0 || fields[0].name !== 'message') {
        startConfig.value = {
          input_variables: [
            { name: 'message', type: 'string', description: '用户消息', required: true },
            ...fields
          ]
        }
      } else {
        startConfig.value = { input_variables: fields }
      }
    } else {
      startConfig.value = { input_variables: fields }
      if (startConfig.value.input_variables.length === 0) {
        startConfig.value.input_variables.push({
          name: 'message',
          type: 'string',
          description: '用户消息',
          required: true
        })
      }
    }
    if (startConfig.value.input_variables.length > 0) {
      store.updateNodeData(node.id, { config: { ...startConfig.value } })
      store.updateInputSchema({ fields: startConfig.value.input_variables })
    }
  }

  if (node?.type === 'llm') {
    llmConfig.value = rawConfig as unknown as LlmConfig
    llmConfig.value.input_variables = resolveInputVars(rawConfig, 'llm')
    llmConfig.value.output_variables = resolveLlmOutputVars(rawConfig)
    if (llmConfig.value.input_variables.length === 0) {
      llmConfig.value.input_variables.push({ name: '', source: '' })
    }
  }

  if (node?.type === 'condition') {
    conditionConfig.value = rawConfig as unknown as ConditionConfig
    if (!conditionConfig.value.rules) {
      conditionConfig.value.rules = [{ variable: '', operator: '==', value: '' }]
    } else if (conditionConfig.value.rules.length === 0) {
      conditionConfig.value.rules.push({ variable: '', operator: '==', value: '' })
    }
  }

  if (node?.type === 'api') {
    apiConfig.value = rawConfig as unknown as ApiConfig
    apiConfig.value.input_variables = resolveInputVars(rawConfig, 'api')
    apiConfig.value.output_variables = resolveOutputVars(rawConfig, 'api')
    if (!apiConfig.value.file_config) {
      apiConfig.value.file_config = { upload_fields: [], download: { enabled: false } }
    }
    if (!apiConfig.value.file_config.upload_fields) {
      apiConfig.value.file_config.upload_fields = []
    }
    if (!apiConfig.value.file_config.download) {
      apiConfig.value.file_config.download = { enabled: false }
    }
  }

  if (node?.type === 'end') {
    const existingVars = resolveOutputVars(rawConfig, 'end')
    if (!store.isInSubView && existingVars.length === 0) {
      const schema = store.flowInfo?.output_schema
      if (schema?.fields && schema.fields.length > 0) {
        endConfig.value = {
          ...rawConfig,
          output_variables: schema.fields.map(f => ({
            name: f.name,
            source: f.description || '',
            type: f.type
          }))
        } as EndConfig
      } else {
        endConfig.value = rawConfig as EndConfig
      }
    } else {
      endConfig.value = { ...rawConfig, output_variables: existingVars } as EndConfig
    }
    if (!endConfig.value.output_variables || endConfig.value.output_variables.length === 0) {
      endConfig.value.output_variables = [{ name: '', source: '', type: 'string' }]
    }
    store.updateNodeData(node.id, { config: { ...endConfig.value } })
  }

  if (node?.type === 'card') {
    cardConfig.value = rawConfig as unknown as CardConfig
  }

  if (node?.type === 'loop') {
    loopConfig.value = rawConfig as unknown as LoopConfig
    if (!loopConfig.value.input_mappings) {
      loopConfig.value.input_mappings = []
    }
    if (!loopConfig.value.output_variables) {
      loopConfig.value.output_variables = []
    }
  }

  if (node?.type === 'mcp') {
    mcpConfig.value = rawConfig as unknown as McpConfig
    if (!mcpConfig.value.mcp_server_ids) {
      mcpConfig.value.mcp_server_ids = []
    }
  }

  if (node?.type === 'human') {
    humanConfig.value = rawConfig as unknown as HumanConfig
    humanConfig.value.input_variables = resolveInputVars(rawConfig, 'human')
    humanConfig.value.output_variables = resolveOutputVars(rawConfig, 'human')
    if (humanConfig.value.input_variables.length === 0) {
      humanConfig.value.input_variables.push({ name: '', source: '' })
    }
  }

  if (node?.type === 'skill') {
    skillConfig.value = rawConfig as unknown as SkillConfig
  }

  if (node?.type === 'knowledge') {
    knowledgeConfig.value = rawConfig as unknown as KnowledgeConfig
    knowledgeConfig.value.input_variables = resolveInputVars(rawConfig, 'knowledge')
    knowledgeConfig.value.output_variables = resolveOutputVars(rawConfig, 'knowledge')
    if (knowledgeConfig.value.input_variables.length === 0) {
      knowledgeConfig.value.input_variables.push({ name: '', source: '' })
    }
  }

  if (node?.type === 'python') {
    pythonConfig.value = rawConfig as unknown as PythonConfig
    pythonConfig.value.input_variables = resolveInputVars(rawConfig, 'python')
    pythonConfig.value.output_variables = resolveOutputVars(rawConfig, 'python')
    if (pythonConfig.value.input_variables.length === 0) {
      pythonConfig.value.input_variables.push({ name: '', source: '' })
    }
  }

  if (node?.type === 'shell') {
    shellConfig.value = rawConfig as unknown as ShellConfig
    shellConfig.value.input_variables = resolveInputVars(rawConfig, 'shell')
    shellConfig.value.output_variables = resolveOutputVars(rawConfig, 'shell')
  }

  if (node?.type === 'memory') {
    memoryConfig.value = rawConfig as unknown as MemoryConfig
  }

  if (node?.type === 'todo') {
    todoConfig.value = {}
  }

  if (node?.type === 'media_gen' && node.data?.config) {
    mediaGenConfig.value = rawConfig as unknown as MediaGenNodeConfig
    mediaGenConfig.value.input_variables = resolveInputVars(rawConfig, 'media_gen')
    mediaGenConfig.value.output_variables = resolveOutputVars(rawConfig, 'media_gen')
  }

  if (node?.type === 'intent_router') {
    intentRouterConfig.value = {
      enable_rule_layer: true,
      enable_llm_layer: true,
      case_sensitive: false,
      temperature: 0.1,
      max_tokens: 200,
      confidence_threshold: 0.6,
      input_variable: 'input.question',
      system_prompt: '',
      intents: [],
      ...(rawConfig as unknown as Partial<IntentRouterConfig>)
    } as IntentRouterConfig
    if (!intentRouterConfig.value.intents) {
      intentRouterConfig.value.intents = []
    }
  }
})

watch(selectedEdge, edge => {
  edgeLabel.value = (edge?.label as string) || ''
})

function updateNodeLabel(): void {
  if (selectedNode.value) {
    store.updateNodeData(selectedNode.value.id, { label: nodeLabel.value })
  }
}

function updateLlmConfig(config: LlmConfig): void {
  if (selectedNode.value && isLlmNode.value) {
    llmConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateConditionConfig(config: ConditionConfig): void {
  if (selectedNode.value && isConditionNode.value) {
    conditionConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateApiConfig(config: ApiConfig): void {
  if (selectedNode.value && isApiNode.value) {
    apiConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateMcpConfig(config: McpConfig): void {
  if (selectedNode.value && isMcpNode.value) {
    mcpConfig.value = config
    store.updateNodeData(selectedNode.value.id, {
      config: { ...config }
    })
  }
}

function updateHumanConfig(config: HumanConfig): void {
  if (selectedNode.value && isHumanNode.value) {
    humanConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateSkillConfig(config: SkillConfig, label?: string): void {
  if (selectedNode.value && isSkillNode.value) {
    skillConfig.value = config
    if (label) {
      nodeLabel.value = label
    }
    store.updateNodeData(selectedNode.value.id, {
      config: { ...config },
      label: label || nodeLabel.value
    })
  }
}

function updateKnowledgeConfig(config: KnowledgeConfig, label?: string): void {
  if (selectedNode.value && isKnowledgeNode.value) {
    knowledgeConfig.value = config
    if (label) {
      nodeLabel.value = label
    }
    store.updateNodeData(selectedNode.value.id, {
      config: { ...config },
      label: label || nodeLabel.value
    })
  }
}

function updatePythonConfig(config: PythonConfig): void {
  if (selectedNode.value && isPythonNode.value) {
    pythonConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateShellConfig(config: ShellConfig): void {
  if (selectedNode.value && isShellNode.value) {
    shellConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateMemoryConfig(config: MemoryConfig): void {
  if (selectedNode.value && isMemoryNode.value) {
    memoryConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateTodoConfig(config: TodoConfig): void {
  if (selectedNode.value && isTodoNode.value) {
    todoConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateMediaGenConfig(config: MediaGenNodeConfig): void {
  if (selectedNode.value && isMediaGenNode.value) {
    mediaGenConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateIntentRouterConfig(config: IntentRouterConfig): void {
  if (selectedNode.value && isIntentRouterNode.value) {
    intentRouterConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateEndConfig(config: EndConfig): void {
  if (selectedNode.value && isEndNode.value) {
    endConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateEndOutputSchema(
  fields: { name: string; type: FieldType; description: string; required: boolean }[]
): void {
  if (!isSubViewEndNode.value) {
    store.updateOutputSchema({ fields })
  }
}

function updateCardConfig(config: CardConfig): void {
  if (selectedNode.value && isCardNode.value) {
    cardConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateLoopConfig(config: LoopConfig): void {
  if (selectedNode.value && isLoopNode.value) {
    loopConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateStartConfig(config: StartConfig): void {
  if (selectedNode.value && isStartNode.value) {
    startConfig.value = config
    store.updateNodeData(selectedNode.value.id, { config: { ...config } })
  }
}

function updateStartInputSchema(fields: FlowIOField[]): void {
  store.updateInputSchema({ fields })
}

function updateEdgeLabel(): void {
  if (selectedEdge.value) {
    selectedEdge.value.label = edgeLabel.value
  }
}

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
  }
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

        <LlmConfigComponent
          v-if="isLlmNode"
          :config="llmConfig"
          :current-node-id="selectedNode.id"
          :is-agent-mode="isAgentMode"
          @update:config="updateLlmConfig"
        />

        <ConditionConfigComponent
          v-if="isConditionNode"
          :config="conditionConfig"
          :current-node-id="selectedNode.id"
          @update:config="updateConditionConfig"
        />

        <ApiConfigComponent
          v-if="isApiNode"
          :config="apiConfig"
          :current-node-id="selectedNode.id"
          @update:config="updateApiConfig"
        />

        <McpConfigComponent
          v-if="isMcpNode"
          :config="mcpConfig"
          :node-id="selectedNode.id"
          @update:config="updateMcpConfig"
        />

        <HumanConfigComponent
          v-if="isHumanNode"
          :config="humanConfig"
          :current-node-id="selectedNode.id"
          @update:config="updateHumanConfig"
        />

        <SkillConfigComponent
          v-if="isSkillNode"
          :config="skillConfig"
          :node-id="selectedNode.id"
          @update:config="(config: SkillConfig) => updateSkillConfig(config)"
          @update:label="
            (label: string) => {
              nodeLabel = label
              store.updateNodeData(selectedNode!.id, { label })
            }
          "
        />

        <KnowledgeConfigComponent
          v-if="isKnowledgeNode"
          :config="knowledgeConfig"
          :node-id="selectedNode.id"
          :current-node-id="selectedNode.id"
          @update:config="updateKnowledgeConfig"
          @update:label="(label: string) => updateKnowledgeConfig(knowledgeConfig, label)"
        />

        <PythonConfigComponent
          v-if="isPythonNode"
          :config="pythonConfig"
          :current-node-id="selectedNode.id"
          @update:config="updatePythonConfig"
        />

        <ShellConfigComponent
          v-if="isShellNode"
          :config="shellConfig"
          :current-node-id="selectedNode.id"
          @update:config="updateShellConfig"
        />

        <MemoryConfigComponent
          v-if="isMemoryNode"
          :config="memoryConfig"
          :node-id="selectedNode.id"
          :current-node-id="selectedNode.id"
          @update:config="updateMemoryConfig"
        />

        <TodoConfigComponent
          v-if="isTodoNode"
          :config="todoConfig"
          @update:config="updateTodoConfig"
        />

        <MediaGenConfigComponent
          v-if="isMediaGenNode"
          :config="mediaGenConfig"
          :node-id="selectedNode.id"
          :current-node-id="selectedNode.id"
          @update:config="updateMediaGenConfig"
        />

        <IntentRouterConfigComponent
          v-if="isIntentRouterNode"
          :config="intentRouterConfig"
          :current-node-id="selectedNode.id"
          @update:config="updateIntentRouterConfig"
        />

        <EndConfigComponent
          v-if="isEndNode"
          :config="endConfig"
          :current-node-id="selectedNode.id"
          @update:config="updateEndConfig"
          @update:output-schema="updateEndOutputSchema"
        />

        <CardConfigComponent
          v-if="isCardNode"
          :config="cardConfig"
          :current-node-id="selectedNode.id"
          @update:config="updateCardConfig"
        />

        <LoopConfigComponent
          v-if="isLoopNode"
          :config="loopConfig"
          :current-node-id="selectedNode.id"
          @update:config="updateLoopConfig"
        />

        <StartConfigComponent
          v-if="isStartNode && !isSubViewStartNode"
          :config="startConfig"
          :is-agent-mode="isAgentMode"
          @update:config="updateStartConfig"
          @update:input-schema="updateStartInputSchema"
        />

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

        <div
          v-if="
            isStartNode &&
            !isSubViewStartNode &&
            isAgentMode &&
            startConfig.input_variables.some(f => f.type === 'file_list')
          "
          class="agent-file-hint"
        >
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
