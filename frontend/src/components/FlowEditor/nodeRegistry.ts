/**
 * 节点注册表 — 节点元数据 + 组件 + 行为 hook 的唯一数据源
 * @description 自动发现 Node/Config 组件，统一管理节点配置面板逻辑。
 *   添加新节点只需：
 *   1. 创建 nodes/XxxNode.vue + config/XxxConfig.vue（自动发现）
 *   2. 在 registry 中添加一个 entry（元数据 + 默认配置 + 可选 hook）
 */

import { markRaw, type Component } from 'vue'
import {
  VideoPlay,
  CircleClose,
  Share,
  RefreshRight,
  Aim,
  ChatDotRound,
  Connection,
  Search,
  User,
  Link,
  MagicStick,
  Cpu,
  Monitor,
  Notebook,
  List,
  PictureFilled,
  Avatar,
  Calendar,
  Postcard
} from '@element-plus/icons-vue'
import type { NodeVariable, FlowIOField, FieldType } from '@/types/flow'

// ---- 自动发现组件（零配置注册） ----

const nodeModules = import.meta.glob('./nodes/*Node.vue', { eager: true })
const configModules = import.meta.glob('./config/*Config.vue', { eager: true })

/** PascalCase → snake_case（MediaGen → media_gen, IntentRouter → intent_router） */
function pascalToSnake(name: string): string {
  return name
    .replace(/([A-Z])/g, '_$1')
    .toLowerCase()
    .replace(/^_/, '')
}

/** 从 glob 路径提取节点类型（./nodes/LlmNode.vue → llm） */
function extractNodeType(path: string, suffix: string): string {
  const fileName = path.split('/').pop()!.replace('.vue', '')
  const typeName = fileName.replace(new RegExp(`${suffix}$`), '')
  return pascalToSnake(typeName)
}

// 构建 node component map（排除 BaseNode）
const nodeComponentMap: Record<string, Component> = {}
for (const [path, module] of Object.entries(nodeModules)) {
  if (path.includes('BaseNode')) continue
  const nodeType = extractNodeType(path, 'Node')
  nodeComponentMap[nodeType] = markRaw((module as { default: Component }).default)
}

// 构建 config component map
const configComponentMap: Record<string, Component> = {}
for (const [path, module] of Object.entries(configModules)) {
  const nodeType = extractNodeType(path, 'Config')
  configComponentMap[nodeType] = markRaw((module as { default: Component }).default)
}

// ---- Hook 上下文类型 ----

interface NodeConfigContext {
  selectedNodeId: string
  isInSubView: boolean
  isAgentMode: boolean
  flowInfo: {
    input_schema?: { fields: FlowIOField[] } | null
    output_schema?: {
      fields: { name: string; type: FieldType; description: string; required: boolean }[]
    } | null
  } | null
  getOutputVariables: (nodeType: string) => NodeVariable[]
  getInputVariables: (nodeType: string) => NodeVariable[]
  setNodeLabel: (label: string) => void
  updateNodeData: (id: string, data: Record<string, unknown>) => void
  updateInputSchema: (fields: FlowIOField[]) => void
  updateOutputSchema: (
    fields: { name: string; type: FieldType; description: string; required: boolean }[]
  ) => void
}

// ---- 注册表 entry 接口 ----

interface NodeRegistryEntry {
  label: string
  description: string
  category: 'basic' | 'llm' | 'tool' | 'io'
  icon: Component
  iconColor: string
  iconBgColor: string
  defaultConfig: () => Record<string, unknown>
  /** 从 raw 配置初始化（watch selectedNode 时调用），返回值会与 defaultConfig 合并 */
  initConfig?: (
    rawConfig: Record<string, unknown>,
    ctx: NodeConfigContext
  ) => Record<string, unknown>
  /** 初始化后副作用（如同步 store、更新 schema） */
  postInit?: (config: Record<string, unknown>, ctx: NodeConfigContext) => void
  /** 配置面板额外 props */
  getExtraProps?: (ctx: NodeConfigContext) => Record<string, unknown>
  /** 配置面板额外事件 */
  getExtraEvents?: (ctx: NodeConfigContext) => Record<string, (...args: unknown[]) => void>
  /** 是否渲染配置面板（默认 true） */
  shouldRenderConfig?: (ctx: NodeConfigContext) => boolean
}

// ---- I/O 变量解析辅助函数 ----

function resolveOutputVars(
  config: Record<string, unknown>,
  nodeType: string,
  ctx: NodeConfigContext
): NodeVariable[] {
  if (config.output_variables) return config.output_variables as NodeVariable[]
  if (config.output_variable) {
    return [{ name: config.output_variable as string, source: '', type: undefined }]
  }
  return ctx.getOutputVariables(nodeType)
}

function resolveInputVars(
  config: Record<string, unknown>,
  nodeType: string,
  ctx: NodeConfigContext
): NodeVariable[] {
  if (config.input_variables) return config.input_variables as NodeVariable[]
  return ctx.getInputVariables(nodeType)
}

function ensureInputVars(vars: NodeVariable[]): NodeVariable[] {
  if (vars.length === 0) vars.push({ name: '', source: '' })
  return vars
}

// ---- 默认配置常量 ----

const MEDIA_GEN_DEFAULTS = {
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
}

// ---- 注册表数据 ----

const registry: Record<string, NodeRegistryEntry> = {
  // ========== 基础节点 ==========

  start: {
    label: '开始',
    description: '流程的起始点，定义输入参数',
    category: 'basic',
    icon: VideoPlay,
    iconColor: '#10b981',
    iconBgColor: '#ecfdf5',
    defaultConfig: () => ({ input_variables: [] }),
    shouldRenderConfig: ctx => !ctx.isInSubView,
    initConfig: (_raw, ctx) => {
      const raw = _raw as Record<string, unknown>
      const nodeVars = raw.input_variables as FlowIOField[] | undefined
      const schemaFallback = ctx.flowInfo?.input_schema?.fields || []
      const fields = nodeVars && nodeVars.length > 0 ? nodeVars : schemaFallback

      if (ctx.isAgentMode) {
        if (fields.length === 0 || fields[0].name !== 'message') {
          return {
            input_variables: [
              { name: 'message', type: 'string', description: '用户消息', required: true },
              ...fields
            ]
          }
        }
        return { input_variables: fields }
      }

      const inputVariables =
        fields.length > 0
          ? fields
          : [{ name: 'message', type: 'string', description: '用户消息', required: true }]
      return { input_variables: inputVariables }
    },
    postInit: (config, ctx) => {
      const inputVars = config.input_variables as FlowIOField[]
      if (inputVars && inputVars.length > 0) {
        ctx.updateNodeData(ctx.selectedNodeId, { config: { ...config } })
        ctx.updateInputSchema(inputVars)
      }
    },
    getExtraProps: ctx => ({ isAgentMode: ctx.isAgentMode }),
    getExtraEvents: ctx => ({
      'update:input-schema': (fields: unknown) => ctx.updateInputSchema(fields as FlowIOField[])
    })
  },

  end: {
    label: '结束',
    description: '流程的结束点，定义输出结果',
    category: 'basic',
    icon: CircleClose,
    iconColor: '#ef4444',
    iconBgColor: '#fef2f2',
    defaultConfig: () => ({ output_variables: [] }),
    initConfig: (rawConfig, ctx) => {
      const existingVars = resolveOutputVars(rawConfig, 'end', ctx)
      if (!ctx.isInSubView && existingVars.length === 0) {
        const schema = ctx.flowInfo?.output_schema
        if (schema?.fields && schema.fields.length > 0) {
          return {
            output_variables: schema.fields.map(f => ({
              name: f.name,
              source: f.description || '',
              type: f.type
            }))
          }
        }
      }
      const outputVars =
        existingVars.length > 0 ? existingVars : [{ name: '', source: '', type: 'string' }]
      return { output_variables: outputVars }
    },
    postInit: (config, ctx) => {
      ctx.updateNodeData(ctx.selectedNodeId, { config: { ...config } })
    },
    getExtraEvents: ctx => ({
      'update:output-schema': (
        fields: { name: string; type: FieldType; description: string; required: boolean }[]
      ) => {
        if (!ctx.isInSubView) ctx.updateOutputSchema(fields)
      }
    })
  },

  condition: {
    label: '条件判断',
    description: '根据条件判断执行不同的分支',
    category: 'basic',
    icon: Share,
    iconColor: '#f59e0b',
    iconBgColor: '#fffbeb',
    defaultConfig: () => ({
      logic: 'and',
      rules: [{ variable: '', operator: '==', value: '' }]
    }),
    initConfig: rawConfig => {
      const rules = (rawConfig.rules || []) as unknown[]
      if (rules.length === 0) rules.push({ variable: '', operator: '==', value: '' })
      return { ...rawConfig, rules }
    }
  },

  loop: {
    label: '循环',
    description: '循环执行子流程，支持固定次数、条件和数组遍历',
    category: 'basic',
    icon: RefreshRight,
    iconColor: '#6366f1',
    iconBgColor: '#eef2ff',
    defaultConfig: () => ({
      loop_mode: 'count',
      max_count: 10,
      condition_expression: '',
      for_each_source: '',
      for_each_item_type: undefined,
      break_on_error: true,
      concurrency: 1,
      input_mappings: [],
      output_variables: []
    }),
    initConfig: rawConfig => ({
      ...rawConfig,
      input_mappings: rawConfig.input_mappings || [],
      output_variables: rawConfig.output_variables || []
    })
  },

  intent_router: {
    label: '意图路由',
    description: '使用规则（关键字/正则）或 LLM 把输入分类到不同分支',
    category: 'basic',
    icon: Aim,
    iconColor: '#9c27b0',
    iconBgColor: '#faf5ff',
    defaultConfig: () => ({
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
    }),
    initConfig: rawConfig => ({
      ...rawConfig,
      intents: rawConfig.intents || []
    })
  },

  // ========== LLM 节点 ==========

  llm: {
    label: '大模型调用 (LLM)',
    description: '调用大语言模型进行推理',
    category: 'llm',
    icon: ChatDotRound,
    iconColor: '#10b981',
    iconBgColor: '#ecfdf5',
    defaultConfig: () => ({
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
    }),
    initConfig: (rawConfig, ctx) => {
      const config = { ...rawConfig }
      config.input_variables = ensureInputVars(resolveInputVars(rawConfig, 'llm', ctx))
      if (config.output_variables) {
        // 已有 output_variables，直接用
      } else if (config.output_variable) {
        const name = config.output_variable as string
        const thinkingName = (config.thinking_variable as string) || ''
        const vars: NodeVariable[] = [{ name, source: '', type: undefined }]
        if (thinkingName) vars.push({ name: thinkingName, source: '', type: undefined })
        config.output_variables = vars
      } else {
        config.output_variables = ctx.getOutputVariables('llm')
      }
      return config
    },
    getExtraProps: ctx => ({ isAgentMode: ctx.isAgentMode })
  },

  // ========== 工具节点 ==========

  mcp: {
    label: 'MCP调用',
    description: '调用MCP服务器提供的工具',
    category: 'tool',
    icon: Connection,
    iconColor: '#6366f1',
    iconBgColor: '#eef2ff',
    defaultConfig: () => ({ mcp_server_ids: [] }),
    initConfig: rawConfig => ({
      ...rawConfig,
      mcp_server_ids: rawConfig.mcp_server_ids || []
    }),
    getExtraProps: ctx => ({ nodeId: ctx.selectedNodeId })
  },

  api: {
    label: 'API调用',
    description: '调用外部API接口',
    category: 'tool',
    icon: Link,
    iconColor: '#a855f7',
    iconBgColor: '#faf5ff',
    defaultConfig: () => ({
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
    }),
    initConfig: (rawConfig, ctx) => {
      const config = { ...rawConfig }
      config.input_variables = resolveInputVars(rawConfig, 'api', ctx)
      config.output_variables = resolveOutputVars(rawConfig, 'api', ctx)
      const fileConfig = (config.file_config || {}) as Record<string, unknown>
      if (!fileConfig.upload_fields) fileConfig.upload_fields = []
      if (!fileConfig.download) fileConfig.download = { enabled: false }
      config.file_config = fileConfig
      return config
    }
  },

  skill: {
    label: '技能调用',
    description: '调用预定义的技能',
    category: 'tool',
    icon: MagicStick,
    iconColor: '#a855f7',
    iconBgColor: '#faf5ff',
    defaultConfig: () => ({ skill_ids: [] }),
    getExtraProps: ctx => ({ nodeId: ctx.selectedNodeId }),
    getExtraEvents: ctx => ({
      'update:label': (label: unknown) => {
        const labelStr = label as string
        ctx.setNodeLabel(labelStr)
        ctx.updateNodeData(ctx.selectedNodeId, { label: labelStr })
      }
    })
  },

  knowledge: {
    label: '知识库检索',
    description: '从知识库检索相关信息',
    category: 'tool',
    icon: Search,
    iconColor: '#3b82f6',
    iconBgColor: '#eff6ff',
    defaultConfig: () => ({
      knowledge_base_id: null,
      knowledge_base_name: '',
      top_k: 5,
      input_variables: [],
      output_variables: [{ name: 'result', source: '', type: undefined }]
    }),
    initConfig: (rawConfig, ctx) => {
      const config = { ...rawConfig }
      config.input_variables = ensureInputVars(resolveInputVars(rawConfig, 'knowledge', ctx))
      config.output_variables = resolveOutputVars(rawConfig, 'knowledge', ctx)
      return config
    },
    getExtraProps: ctx => ({ nodeId: ctx.selectedNodeId }),
    getExtraEvents: ctx => ({
      'update:label': (label: unknown) => {
        const labelStr = label as string
        ctx.setNodeLabel(labelStr)
        ctx.updateNodeData(ctx.selectedNodeId, { label: labelStr })
      }
    })
  },

  python: {
    label: 'Python 代码',
    description: '在沙箱环境中执行Python代码',
    category: 'tool',
    icon: Cpu,
    iconColor: '#f59e0b',
    iconBgColor: '#fffbeb',
    defaultConfig: () => ({
      code: '',
      timeout: 30,
      input_variables: [],
      output_variables: [{ name: 'result', source: '', type: undefined }]
    }),
    initConfig: (rawConfig, ctx) => {
      const config = { ...rawConfig }
      config.input_variables = ensureInputVars(resolveInputVars(rawConfig, 'python', ctx))
      config.output_variables = resolveOutputVars(rawConfig, 'python', ctx)
      return config
    }
  },

  shell: {
    label: 'Shell 命令',
    description: '在受限环境中执行Shell命令',
    category: 'tool',
    icon: Monitor,
    iconColor: '#f59e0b',
    iconBgColor: '#fffbeb',
    defaultConfig: () => ({
      command: '',
      timeout: 30,
      input_variables: [],
      output_variables: [
        { name: 'stdout', source: '', type: 'string' },
        { name: 'stderr', source: '', type: 'string' },
        { name: 'exit_code', source: '', type: 'number' }
      ]
    }),
    initConfig: (rawConfig, ctx) => {
      const config = { ...rawConfig }
      config.input_variables = resolveInputVars(rawConfig, 'shell', ctx)
      config.output_variables = resolveOutputVars(rawConfig, 'shell', ctx)
      return config
    }
  },

  memory: {
    label: '记忆',
    description: '为Agent提供记忆保存与检索能力',
    category: 'tool',
    icon: Notebook,
    iconColor: '#3b82f6',
    iconBgColor: '#eff6ff',
    defaultConfig: () => ({
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
    }),
    getExtraProps: ctx => ({ nodeId: ctx.selectedNodeId })
  },

  todo: {
    label: '任务计划',
    description: '为Agent提供任务规划与进度跟踪能力',
    category: 'tool',
    icon: List,
    iconColor: '#64748b',
    iconBgColor: '#f8fafc',
    defaultConfig: () => ({}),
    initConfig: () => ({})
  },

  media_gen: {
    label: '媒体生成',
    description: '生成图片、音频、视频',
    category: 'tool',
    icon: PictureFilled,
    iconColor: '#a855f7',
    iconBgColor: '#faf5ff',
    defaultConfig: () => ({ ...MEDIA_GEN_DEFAULTS }),
    initConfig: (rawConfig, ctx) => {
      if (!rawConfig || Object.keys(rawConfig).length === 0) return { ...MEDIA_GEN_DEFAULTS }
      const config = { ...rawConfig }
      config.input_variables = resolveInputVars(rawConfig, 'media_gen', ctx)
      config.output_variables = resolveOutputVars(rawConfig, 'media_gen', ctx)
      return config
    },
    getExtraProps: ctx => ({ nodeId: ctx.selectedNodeId })
  },

  sub_agent: {
    label: '子Agent',
    description: '调用已发布的Agent作为子任务执行器',
    category: 'tool',
    icon: Avatar,
    iconColor: '#3b82f6',
    iconBgColor: '#eff6ff',
    defaultConfig: () => ({ agent_id: null }),
    getExtraProps: ctx => ({ nodeId: ctx.selectedNodeId }),
    getExtraEvents: ctx => ({
      'update:label': (label: unknown) => {
        const labelStr = label as string
        ctx.setNodeLabel(labelStr)
        ctx.updateNodeData(ctx.selectedNodeId, { label: labelStr })
      }
    })
  },

  agenda: {
    label: '日程',
    description: '为Agent提供日程管理能力（创建、查询、更新、删除日程）',
    category: 'tool',
    icon: Calendar,
    iconColor: '#0ea5e9',
    iconBgColor: '#f0f9ff',
    defaultConfig: () => ({}),
    initConfig: () => ({})
  },

  // ========== 交互节点 ==========

  human: {
    label: '人类回答',
    description: '等待人工输入或审核',
    category: 'io',
    icon: User,
    iconColor: '#ef4444',
    iconBgColor: '#fef2f2',
    defaultConfig: () => ({
      assist_prompt: '',
      review_prompt: '',
      input_variables: [],
      output_variables: [{ name: 'feedback', source: '', type: undefined }]
    }),
    initConfig: (rawConfig, ctx) => {
      const config = { ...rawConfig }
      config.input_variables = ensureInputVars(resolveInputVars(rawConfig, 'human', ctx))
      config.output_variables = resolveOutputVars(rawConfig, 'human', ctx)
      return config
    }
  },

  card: {
    label: '流程卡片',
    description: '引用其他流程作为子流程',
    category: 'io',
    icon: Postcard,
    iconColor: '#10b981',
    iconBgColor: '#ecfdf5',
    defaultConfig: () => ({
      ref_flow_id: 0,
      input_schema: null,
      output_schema: null,
      input_mappings: [],
      output_mappings: []
    })
  }
}

// ---- 导出 helper 函数 ----

/** 获取节点注册 entry */
export function getNodeEntry(nodeType: string): NodeRegistryEntry | undefined {
  return registry[nodeType]
}

/** 获取配置面板组件 */
export function getConfigComponent(nodeType: string): Component | undefined {
  return configComponentMap[nodeType]
}

/** Vue Flow nodeTypes map */
export const nodeTypes: Record<string, Component> = nodeComponentMap

/** 获取所有注册的节点类型列表 */
export function getAllRegisteredTypes(): string[] {
  return Object.keys(registry)
}

/** 获取节点标签 */
export function getNodeLabel(nodeType: string): string {
  return registry[nodeType]?.label || nodeType
}

/** 获取节点面板基础节点列表 */
export function getBasicPanelNodes(): Array<{
  type: string
  label: string
  icon: Component
  iconColor: string
  iconBgColor: string
}> {
  return Object.entries(registry)
    .filter(([, e]) => e.category === 'basic')
    .map(([type, e]) => ({
      type,
      label: e.label,
      icon: e.icon,
      iconColor: e.iconColor,
      iconBgColor: e.iconBgColor
    }))
}

/** 获取节点面板能力卡片列表（排除 card 类型，card 有独立入口） */
export function getCardPanelNodes(): Array<{
  type: string
  label: string
  icon: Component
  iconColor: string
  iconBgColor: string
}> {
  return Object.entries(registry)
    .filter(([, e]) => e.category !== 'basic')
    .filter(([type]) => type !== 'card')
    .map(([type, e]) => ({
      type,
      label: e.label,
      icon: e.icon,
      iconColor: e.iconColor,
      iconBgColor: e.iconBgColor
    }))
}

/** 导出类型 */
export type { NodeConfigContext, NodeRegistryEntry }
