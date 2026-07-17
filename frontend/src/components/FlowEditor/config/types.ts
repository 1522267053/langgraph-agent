/** 条件规则接口 */
export interface ConditionRule {
  variable: string
  operator: string
  value: string
}

/** 输出变量接口（兼容旧引用） */
export type OutputVariable = NodeVariable

/** 节点变量（输入/输出通用，与后端 NodeVariable 对齐） */
export interface NodeVariable {
  name: string
  source: string
  type?: 'string' | 'number' | 'boolean' | 'object' | 'array'
}

/** LLM输入变量接口（兼容旧引用） */
export type LlmInputVariable = NodeVariable

/** 卡片输入映射接口 */
export interface CardInputMapping {
  card_field: string
  source: string
  type?: FieldType
}

/** 卡片输出映射接口 */
export interface CardOutputMapping {
  card_field: string
  target_variable: string
}

/** 模型多模态能力 */
export interface ModelCapabilities {
  image: boolean
  video: boolean
  audio: boolean
  pdf: boolean
  xlsx: boolean
}

/** LLM模型选项 */
export interface ModelOption {
  value: string
  label: string
  capabilities?: ModelCapabilities
  context_length?: number
  max_tokens?: number
}

/** LLM节点配置 */
export interface LlmConfig {
  provider: string
  model: string
  api_key: string
  base_url: string
  capabilities: ModelCapabilities
  input_variables: NodeVariable[]
  output_variables: NodeVariable[]
  system_prompt: string
  user_prompt: string
  temperature: number
  max_tool_iterations: number
  max_tokens: number
  history_mode: 'node' | 'flow' | 'none'
  max_history_turns: number
  require_tool_approval?: boolean
  extra_body?: Record<string, unknown>
  reasoning_effort?: string
  context_length?: number
  required_tools?: string[]
  tool_check_script?: string
  required_tools_max_retries?: number
  required_tools_hint?: string
}

/** 条件节点配置 */
export interface ConditionConfig {
  logic: string
  rules: ConditionRule[]
}

/** 意图规则（第一层） */
export interface IntentRule {
  keywords: string[]
  regex_patterns: string[]
}

/** 单个意图项 */
export interface IntentItem {
  key: string
  description: string
  examples: string[]
  rule: IntentRule
}

/** 意图路由节点配置 */
export interface IntentRouterConfig {
  enable_rule_layer: boolean
  enable_llm_layer: boolean
  case_sensitive: boolean
  provider?: string
  model?: string
  api_key?: string
  base_url?: string
  temperature: number
  max_tokens: number
  system_prompt: string
  confidence_threshold: number
  input_variable: string
  intents: IntentItem[]
  output_variables?: NodeVariable[]
}

/** 结束节点配置 */
export interface EndConfig {
  output_variables: NodeVariable[]
}

/** API节点单个文件上传字段 */
export interface ApiUploadField {
  field_name: string
  file_ids: number[]
}

/** API节点文件下载配置 */
export interface ApiFileDownloadConfig {
  enabled: boolean
}

/** API节点配置 */
export interface ApiConfig {
  api_url: string
  method: string
  headers: string
  body: string
  content_type: 'application/json' | 'multipart/form-data'
  form_fields: { key: string; value: string }[]
  input_variables: NodeVariable[]
  output_variables: NodeVariable[]
  file_config: {
    upload_fields: ApiUploadField[]
    download: ApiFileDownloadConfig
  }
  description?: string
  use_preset_for_tool?: boolean
}

/** MCP节点配置 */
export interface McpConfig {
  mcp_server_ids: number[]
  mcp_server_names?: string[]
}

/** Human节点配置 */
export interface HumanConfig {
  assist_prompt: string
  review_prompt: string
  input_variables: NodeVariable[]
  output_variables: NodeVariable[]
}

/** Skill节点配置 */
export interface SkillConfig {
  skill_ids: number[]
}

/** 知识库节点配置 */
export interface KnowledgeConfig {
  knowledge_base_id: number | null
  knowledge_base_name: string
  top_k: number
  input_variables: NodeVariable[]
  output_variables: NodeVariable[]
}

/** Python节点配置 */
export interface PythonConfig {
  code: string
  timeout: number
  input_variables: NodeVariable[]
  output_variables: NodeVariable[]
  description?: string
  use_preset_for_tool?: boolean
}

/** Shell节点配置 */
export interface ShellConfig {
  command: string
  timeout: number
  input_variables: NodeVariable[]
  output_variables: NodeVariable[]
}

/** 记忆节点配置 */
export interface MemoryConfig {
  max_results: number
  default_importance: number
  default_category: string
  max_index_lines: number
  max_index_bytes: number
  auto_promote_threshold: number
  consolidate_threshold: number
  hot_decay_days: number
  warm_decay_days: number
  consolidate_interval_days: number
}

/** 任务计划节点配置 */
// eslint-disable-next-line @typescript-eslint/no-empty-object-type
export interface TodoConfig {}

/** 日程节点配置 */
// eslint-disable-next-line @typescript-eslint/no-empty-object-type
export interface AgendaConfig {}

/** 子Agent节点配置 */
export interface SubAgentConfig {
  agent_id: number | null
}

/** 卡片节点配置 */
export interface CardConfig {
  ref_flow_id: number
  input_schema: FlowIOSchema | null
  output_schema: FlowIOSchema | null
  input_mappings: CardInputMapping[]
  output_mappings: CardOutputMapping[]
}

/** 循环节点配置 */
export interface LoopConfig {
  loop_mode: 'count' | 'condition' | 'for_each'
  max_count: number
  condition_expression: string
  for_each_source: string
  for_each_item_type?: FieldType
  break_on_error: boolean
  concurrency: number
  input_mappings: CardInputMapping[]
  output_variables: NodeVariable[]
}

/** 开始节点配置 */
export interface StartConfig {
  input_variables: FlowIOField[]
}

/** 条件操作符列表 */
export const conditionOperators = [
  { value: '==', label: '等于' },
  { value: '!=', label: '不等于' },
  { value: '>', label: '大于' },
  { value: '>=', label: '大于等于' },
  { value: '<', label: '小于' },
  { value: '<=', label: '小于等于' },
  { value: 'contains', label: '包含' },
  { value: 'not_contains', label: '不包含' },
  { value: 'is_empty', label: '为空' },
  { value: 'is_not_empty', label: '不为空' },
  { value: 'starts_with', label: '开头是' },
  { value: 'ends_with', label: '结尾是' }
]

/** 上下文窗口预设选项（供 openai_compatible 等自定义模型使用） */
export const CONTEXT_LENGTH_PRESETS = [
  { label: '100K', value: 102400 },
  { label: '128K', value: 131072 },
  { label: '200K', value: 204800 },
  { label: '1M', value: 1048576 }
]

/** 将上下文窗口缩写转为数字（如 "32K" → 32000，"1M" → 1000000） */
export function parseContextLength(value: string | number | undefined): number | undefined {
  if (value === undefined || value === null || value === '') return undefined
  if (typeof value === 'number') return value
  const str = String(value).trim().toUpperCase()
  const match = str.match(/^(\d+(?:\.\d+)?)\s*(K|M)?$/)
  if (!match) return undefined
  const num = parseFloat(match[1])
  const unit = match[2]
  if (unit === 'K') return Math.round(num * 1000)
  if (unit === 'M') return Math.round(num * 1000000)
  return Math.round(num)
}

/** 字段类型选项 */
import type { FlowIOField, FieldType, FlowIOSchema } from '@/types/flow'

export const fieldTypeOptions: { value: FieldType; label: string }[] = [
  { value: 'string', label: '字符串' },
  { value: 'number', label: '数字' },
  { value: 'boolean', label: '布尔' },
  { value: 'object', label: '对象' },
  { value: 'array', label: '数组' },
  { value: 'file_list', label: '文件列表' },
  { value: 'python_result', label: 'Python结果' }
]

/** 变量格式提示文本 */
export function variableFormatHint(variableStr: string): string {
  return `{{ ${variableStr} }}`
}

/** 重导出类型 */
export type { FlowIOField, FieldType, FlowIOSchema }
