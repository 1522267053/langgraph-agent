/**
 * SSE（Server-Sent Events）相关类型定义
 * @description 定义SSE流式传输的事件类型和处理接口
 */

import type { TodoItem } from '@/types/segment'

export type { TodoItem }

/** SSE事件基础数据结构 */
export interface SSEEventData {
  /** 流程ID */
  flow_id?: number
  /** 执行ID */
  execution_id?: number
  /** 会话ID */
  session_id?: number
  /** 节点Key */
  node_key?: string
  /** 节点类型 */
  node_type?: string
  /** 节点名称 */
  node_name?: string
  /** 内容（文本、思考过程等） */
  content?: string
  /** 消息 */
  message?: string
  /** 状态 */
  status?: string
  /** 输入数据 */
  input_data?: Record<string, unknown>
  /** 输出数据 */
  output_data?: Record<string, unknown>
  /** 错误信息 */
  error?: string
  /** 问题（人工交互时的问题） */
  question?: string
  /** 上下文信息 */
  context?: string
  /** 工具名称 */
  tool_name?: string
  /** 工具参数 */
  tool_args?: Record<string, unknown>
  /** 工具调用结果 */
  result?: unknown
  /** 等待数据（人工交互） */
  wait_data?: SSEWaitData
  /** 最大工具调用迭代次数 */
  max_iterations?: number
  /** 输入token数 */
  prompt_tokens?: number
  /** 输出token数 */
  completion_tokens?: number
  /** 总token数 */
  total_tokens?: number
  /** 最大重试次数 */
  max_retries?: number
  /** 等待秒数 */
  wait_seconds?: number
  /** 当前重试次数 */
  retry_count?: number
  /** 任务计划列表 */
  todos?: TodoItem[]
  /** 媒体生成文件ID */
  file_id?: number
  /** 媒体文件名 */
  file_name?: string
  /** 媒体MIME类型 */
  mime_type?: string
  /** 媒体下载URL */
  download_url?: string
}

/** 人工等待数据 */
export interface SSEWaitData {
  /** 等待类型 */
  type: string
  /** 节点Key */
  node_key: string
  /** 问题内容 */
  question: string
  /** 上下文信息 */
  context?: string
  /** 工具调用ID */
  tool_call_id?: string
  /** LLM状态数据 */
  llm_state?: Record<string, unknown>
}

/** SSE事件类型（流程执行） */
export type FlowSSEEventType =
  | 'flow_start'
  | 'resume_start'
  | 'node_start'
  | 'node_thinking'
  | 'node_content'
  | 'node_done'
  | 'tool_call_start'
  | 'tool_call_end'
  | 'tool_call_limit'
  | 'token_usage'
  | 'waiting_human'
  | 'tool_approval_required'
  | 'todo_update'
  | 'flow_done'
  | 'error'
  | 'llm_retry'
  | 'context_compressing'

/** SSE事件类型（Agent会话） */
export type AgentSSEEventType = FlowSSEEventType

/** SSE事件结构 */
export interface SSEEvent<T extends string = FlowSSEEventType> {
  /** 事件类型 */
  type: T
  /** 事件数据 */
  data: SSEEventData
}

/** SSE事件处理器 */
export type SSEEventHandler<T extends string = FlowSSEEventType> = (event: SSEEvent<T>) => void

/** 流程执行SSE处理器接口 */
export interface FlowSSEHandlers {
  /** 流程开始 */
  onFlowStart?: SSEEventHandler
  /** 节点开始 */
  onNodeStart?: SSEEventHandler
  /** 节点思考中 */
  onNodeThinking?: SSEEventHandler
  /** 节点内容输出 */
  onNodeContent?: SSEEventHandler
  /** 节点完成 */
  onNodeDone?: SSEEventHandler
  /** 工具调用开始 */
  onToolCallStart?: SSEEventHandler
  /** 工具调用结束 */
  onToolCallEnd?: SSEEventHandler
  /** 工具调用超过最大迭代次数 */
  onToolCallLimit?: SSEEventHandler
  /** Token用量 */
  onTokenUsage?: SSEEventHandler
  /** 等待人工输入 */
  onWaitingHuman?: SSEEventHandler
  /** 工具确认（批准/拒绝） */
  onToolApproval?: SSEEventHandler
  /** 流程完成 */
  onFlowDone?: SSEEventHandler
  /** 任务计划更新 */
  onTodoUpdate?: SSEEventHandler
  /** 错误处理 */
  onError?: SSEEventHandler
  /** LLM重试 */
  onLlmRetry?: SSEEventHandler
  /** 上下文压缩状态 */
  onContextCompressing?: SSEEventHandler
}

/** Agent会话SSE处理器接口 */
export type AgentSSEHandlers = FlowSSEHandlers
