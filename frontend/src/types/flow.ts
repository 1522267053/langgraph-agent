/**
 * 流程相关类型定义
 * @description 定义流程、节点、边等核心实体的类型
 */

import type { BaseEntity } from './common'

/** 节点类型 */
export type NodeType = 'start' | 'end' | 'condition' | 'card' | 'loop' | 'intent_router'

/** 卡片节点类型 */
export type CardNodeType =
  | 'llm'
  | 'mcp'
  | 'knowledge'
  | 'human'
  | 'api'
  | 'skill'
  | 'python'
  | 'shell'
  | 'memory'
  | 'todo'
  | 'sub_agent'
  | 'agenda'

/** 所有节点类型 */
export type AllNodeType = NodeType | CardNodeType

/** 流程状态 */
export type FlowStatus = 0 | 1

/** 流程类型 */
export type FlowType = 'flow' | 'agent'

/** 字段类型 */
export type FieldType =
  | 'string'
  | 'number'
  | 'boolean'
  | 'object'
  | 'array'
  | 'file_list'
  | 'python_result'

/** 流程输入输出字段定义 */
export interface FlowIOField {
  /** 字段名称 */
  name: string
  /** 字段类型 */
  type: FieldType
  /** 字段描述 */
  description?: string
  /** 输入框占位提示文本 */
  placeholder?: string
  /** 是否必填 */
  required?: boolean
  /** 允许的文件类型，如 image/*,.pdf,.docx */
  accept?: string
  /** 是否允许多文件 */
  multiple?: boolean
  /** 最大文件大小(MB) */
  max_size?: number
}

/** 流程输入输出Schema */
export interface FlowIOSchema {
  /** 字段列表 */
  fields: FlowIOField[]
}

/** 流程节点实体 */
export interface FlowNode extends BaseEntity {
  /** 所属流程ID */
  flow_id?: number
  /** 节点类型 */
  node_type?: AllNodeType
  /** 节点唯一标识 */
  node_key?: string
  /** 节点名称 */
  node_name?: string
  /** X坐标 */
  position_x?: number
  /** Y坐标 */
  position_y?: number
  /** 基础配置 */
  base_config?: Record<string, unknown>
  /** 引用的流程ID（卡片节点） */
  ref_flow_id?: number
}

/** 流程边实体 */
export interface FlowEdge extends BaseEntity {
  /** 所属流程ID */
  flow_id?: number
  /** 源节点Key */
  source_node_key?: string
  /** 目标节点Key */
  target_node_key?: string
  /** 源节点连接点 */
  source_handle?: string
  /** 目标节点连接点 */
  target_handle?: string
  /** 条件配置 */
  condition?: Record<string, unknown>
  /** 边标签 */
  label?: string
}

/** 流程实体 */
export interface Flow extends BaseEntity {
  /** 流程名称 */
  name?: string
  /** 流程描述 */
  description?: string
  /** 流程类型 */
  flow_type?: FlowType
  /** 流程状态 */
  status?: FlowStatus
  /** 是否保存为卡片 */
  saved_as_card?: number
  /** 输入Schema */
  input_schema?: FlowIOSchema
  /** 输出Schema */
  output_schema?: FlowIOSchema
  /** 建议提示词列表 */
  suggested_prompts?: string[]
}

/** 流程详情（包含节点和边） */
export interface FlowDetail extends Flow {
  /** 节点列表 */
  nodes?: FlowNode[]
  /** 边列表 */
  edges?: FlowEdge[]
}

/** Vue Flow节点数据 */
export interface VueFlowNodeData {
  /** 节点标签 */
  label?: string
  /** 节点配置 */
  config?: Record<string, unknown>
}

/** Vue Flow节点 */
export interface VueFlowNode {
  /** 节点ID */
  id: string
  /** 节点类型 */
  type?: string
  /** 节点位置 */
  position: { x: number; y: number }
  /** 节点数据 */
  data: VueFlowNodeData
}

/** Vue Flow边 */
export interface VueFlowEdge {
  /** 边ID */
  id: string
  /** 源节点ID */
  source: string
  /** 目标节点ID */
  target: string
  /** 边标签 */
  label?: string
  /** 边数据 */
  data?: Record<string, unknown>
}

/** Vue Flow图结构 */
export interface VueFlowGraph {
  /** 节点列表 */
  nodes: VueFlowNode[]
  /** 边列表 */
  edges: VueFlowEdge[]
}

/** 创建流程参数 */
export interface FlowCreate {
  /** 流程名称 */
  name: string
  /** 流程描述 */
  description?: string
  /** 流程状态 */
  status?: FlowStatus
  /** 输入Schema */
  input_schema?: FlowIOSchema
  /** 输出Schema */
  output_schema?: FlowIOSchema
  /** 建议提示词列表 */
  suggested_prompts?: string[]
}

/** 更新流程参数 */
export interface FlowUpdate extends FlowCreate {
  /** 流程ID */
  id: number
}

/** 创建节点参数 */
export interface FlowNodeCreate {
  /** 所属流程ID */
  flow_id: number
  /** 节点类型 */
  node_type: AllNodeType
  /** 节点唯一标识 */
  node_key: string
  /** 节点名称 */
  node_name?: string
  /** X坐标 */
  position_x?: number
  /** Y坐标 */
  position_y?: number
  /** 基础配置 */
  base_config?: Record<string, unknown>
  /** 引用的流程ID */
  ref_flow_id?: number
}

/** 更新节点参数 */
export interface FlowNodeUpdate extends FlowNodeCreate {
  /** 节点ID */
  id: number
}

/** 创建边参数 */
export interface FlowEdgeCreate {
  /** 所属流程ID */
  flow_id: number
  /** 源节点Key */
  source_node_key: string
  /** 目标节点Key */
  target_node_key: string
  /** 源节点连接点 */
  source_handle?: string
  /** 目标节点连接点 */
  target_handle?: string
  /** 条件配置 */
  condition?: Record<string, unknown>
  /** 边标签 */
  label?: string
}

/** 更新边参数 */
export interface FlowEdgeUpdate extends FlowEdgeCreate {
  /** 边ID */
  id: number
}

// ---- 导入导出类型 ----

/** 导出的节点（使用名称引用代替ID） */
export interface FlowExportNode {
  node_type: string
  node_key: string
  node_name?: string
  position_x: number
  position_y: number
  base_config?: Record<string, unknown>
  ref_flow_name?: string
}

/** 导出的边 */
export interface FlowExportEdge {
  source_node_key: string
  target_node_key: string
  source_handle?: string
  target_handle?: string
  condition?: Record<string, unknown>
  label?: string
}

/** 导出的单个流程 */
export interface FlowExportFlow {
  name: string
  description?: string
  flow_type: FlowType
  saved_as_card?: number
  input_schema?: FlowIOSchema
  output_schema?: FlowIOSchema
  nodes: FlowExportNode[]
  edges: FlowExportEdge[]
}

/** 导出的记忆条目 */
export interface FlowExportMemoryItem {
  memory_type: string
  category: string
  title: string
  content: string
  keywords?: string
  importance: number
  peak_tier: string
}

/** 导出的记忆分组 */
export interface FlowExportMemory {
  flow_name: string
  memories: FlowExportMemoryItem[]
}

/** 导出的 MCP 服务器工具缓存 */
export interface FlowExportMcpToolCache {
  tool_name: string
  description?: string
  tool_schema?: Record<string, unknown>
  is_enabled: number
}

/** 导出的 MCP 服务器 */
export interface FlowExportMcpServer {
  name: string
  description?: string
  transport: string
  is_enabled: number
  keep_alive: number
  configs: Record<string, unknown>
  tools_cache: FlowExportMcpToolCache[]
}

/** 导出的知识库 */
export interface FlowExportKnowledgeBase {
  name: string
  description?: string
  status: number
}

/** 导出的技能 */
export interface FlowExportSkill {
  name: string
  description: string
  category?: string
  tags?: string
  icon?: string
  is_enabled: number
  skill_content?: string
}

/** 导出文件的完整数据结构 */
export interface FlowExportData {
  version: string
  export_time: string
  flows: FlowExportFlow[]
  memories: FlowExportMemory[]
  mcp_servers: FlowExportMcpServer[]
  knowledge_bases: FlowExportKnowledgeBase[]
  skills: FlowExportSkill[]
}

/** 导入结果 */
export interface FlowImportResult {
  created: Array<{ id: number; name: string; flow_type: string }>
  warnings: string[]
}

/** 流程版本快照 */
export interface FlowSnapshot {
  id?: number
  flow_id: number
  snapshot_name: string
  snapshot_description?: string
  snapshot_data?: Record<string, unknown>
  snapshot_type: 'auto' | 'manual'
  is_pinned: number
  create_time?: string
}
