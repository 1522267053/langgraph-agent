/**
 * 流程执行相关类型定义
 * @description 定义流程执行记录、节点执行记录等类型
 */

import type { BaseEntity } from './common'

/** 执行状态枚举 */
export enum ExecutionStatus {
  /** 待执行 */
  Pending = 0,
  /** 执行中 */
  Running = 1,
  /** 成功 */
  Success = 2,
  /** 失败 */
  Failed = 3,
  /** 已取消 */
  Cancelled = 4,
  /** 等待输入 */
  WaitingInput = 5
}

/** 节点执行状态枚举 */
export enum NodeExecutionStatus {
  /** 待执行 */
  Pending = 0,
  /** 执行中 */
  Running = 1,
  /** 成功 */
  Success = 2,
  /** 失败 */
  Failed = 3,
  /** 跳过 */
  Skipped = 4,
  /** 已取消 */
  Cancelled = 5
}

/** 执行状态文本映射 */
export const EXECUTION_STATUS_TEXT: Record<ExecutionStatus, string> = {
  [ExecutionStatus.Pending]: '待执行',
  [ExecutionStatus.Running]: '执行中',
  [ExecutionStatus.Success]: '成功',
  [ExecutionStatus.Failed]: '失败',
  [ExecutionStatus.Cancelled]: '已取消',
  [ExecutionStatus.WaitingInput]: '等待输入'
}

/** 节点执行状态文本映射 */
export const NODE_EXECUTION_STATUS_TEXT: Record<NodeExecutionStatus, string> = {
  [NodeExecutionStatus.Pending]: '待执行',
  [NodeExecutionStatus.Running]: '执行中',
  [NodeExecutionStatus.Success]: '成功',
  [NodeExecutionStatus.Failed]: '失败',
  [NodeExecutionStatus.Skipped]: '跳过',
  [NodeExecutionStatus.Cancelled]: '已取消'
}

/** 人工等待数据 */
export interface ExecutionWaitData {
  /** 等待类型 */
  type?: string
  /** 问题内容 */
  question?: string
  /** 上下文信息 */
  context?: string
  /** 工具调用ID */
  toolCallId?: string
}

/** 流程执行记录实体 */
export interface FlowExecution extends BaseEntity {
  /** 关联的流程ID */
  flow_id?: number
  /** 流程名称 */
  flow_name?: string
  /** 执行状态 */
  status?: ExecutionStatus
  /** 输入数据 */
  input_data?: Record<string, unknown>
  /** 输出数据 */
  output_data?: Record<string, unknown>
  /** 错误信息 */
  error_message?: string
  /** 开始时间 */
  start_time?: string
  /** 结束时间 */
  end_time?: string
  /** 等待数据（人工交互） */
  wait_data?: ExecutionWaitData
  /** 附件文件信息 */
  files?: Array<{ id: number; original_name: string; mime_type: string }>
}

/** 工具调用记录 */
export interface ToolCallRecord {
  /** 工具名称 */
  name: string
  /** 工具参数 */
  args: Record<string, unknown>
  /** 工具调用 ID */
  id?: string
  /** 执行状态 */
  status?: 'running' | 'success' | 'error'
  /** 返回结果 */
  result?: unknown
  /** 错误信息 */
  error?: string
  /** 开始时间 */
  start_time?: string
  /** 结束时间 */
  end_time?: string
}

/** 执行步骤记录 */
export interface ExecutionStep {
  /** 步骤序号 */
  step: number
  /** 消息角色：human / ai / tool */
  role?: string
  /** 思考内容 */
  thinking?: string
  /** 响应内容 */
  content?: string
  /** 工具调用列表 */
  tool_calls?: ToolCallRecord[]
  /** 工具调用 ID（role=tool 时） */
  tool_call_id?: string
  /** 工具名称（role=tool 时） */
  tool_name?: string
}

/** 节点执行记录实体 */
export interface NodeExecution extends BaseEntity {
  /** 关联的流程执行ID */
  flow_execution_id?: number
  /** 节点Key */
  node_key?: string
  /** 节点类型 */
  node_type?: string
  /** 节点名称 */
  node_name?: string
  /** 执行状态 */
  status?: NodeExecutionStatus
  /** 输入数据 */
  input_data?: Record<string, unknown>
  /** 输出数据 */
  output_data?: Record<string, unknown>
  /** 错误信息 */
  error_message?: string
  /** 开始时间 */
  start_time?: string
  /** 结束时间 */
  end_time?: string
  /** 执行步骤记录 */
  execution_steps?: ExecutionStep[]
  /** 输入token数 */
  prompt_tokens?: number
  /** 输出token数 */
  completion_tokens?: number
  /** 总token数 */
  total_tokens?: number
}

/** 执行输入参数 */
export interface ExecutionInput {
  /** 输入数据 */
  input_data?: Record<string, unknown>
  /** 附件文件信息 */
  files?: Array<{ id: number; original_name: string; mime_type: string }>
}

/** 等待状态数据 */
export interface WaitStatusData {
  /** 是否正在等待 */
  waiting: boolean
  /** 执行ID */
  execution_id?: number
  /** 执行状态 */
  status?: number
  /** 提示信息 */
  prompt?: string
  /** 上下文信息 */
  context?: string
  /** 输出变量名 */
  output_variable?: string
  /** 超时时间 */
  timeout?: number
}

/** 对话历史消息 */
export interface ConversationMessage {
  /** 消息角色 */
  role: string
  /** 消息内容 */
  content: string
  /** 消息名称（tool消息使用） */
  name?: string
  /** 工具调用信息 */
  tool_calls?: Array<{
    /** 工具名称 */
    name: string
    /** 工具参数 */
    args: Record<string, unknown>
    /** 调用ID */
    id?: string
    /** 执行状态 */
    status?: 'running' | 'success' | 'error'
  }>
}

/** 对话历史数据 */
export interface ConversationHistoryData {
  /** 消息列表 */
  messages: ConversationMessage[]
  /** 错误信息 */
  error?: string
}
