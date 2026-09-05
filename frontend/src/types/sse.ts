/**
 * SSE（Server-Sent Events）相关类型定义
 * @description 定义SSE流式传输的事件类型和处理接口
 */

import type { KnowledgeReference } from '@/types/knowledge'
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
  /** 工具调用ID（同名并行调用时用于精确匹配结果） */
  tool_call_id?: string
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
  /** 流程名称（flow_preview 事件） */
  flow_name?: string
  /** 流程类型（flow_preview 事件：flow / agent，前端编辑跳转路由时按此选 AgentEdit/FlowEdit） */
  flow_type?: 'flow' | 'agent'
  /** 变更类型（flow_preview 事件） */
  action?: string
  /** 节点列表（flow_preview 事件） */
  nodes?: Record<string, unknown>[]
  /** 边列表（flow_preview 事件） */
  edges?: Record<string, unknown>[]
  /** LLM 回答中经过校验的知识库引用 */
  citations?: KnowledgeReference[]
  /** 子Agent ID（sub_agent_progress 事件） */
  sub_agent_id?: number
  /** 子Agent会话ID（sub_agent_progress 事件） */
  sub_session_id?: number
  /** 子Agent名称（sub_agent_progress 事件） */
  sub_agent_name?: string
  /** 子Agent执行状态（sub_agent_progress 事件） */
  sub_status?: string
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

/** SSE事件类型（流程执行） — 由下方 EVENT_HANDLER_MAP 自动派生，禁止手写 */

/** SSE事件结构 */
export interface SSEEvent<T extends string = FlowSSEEventType> {
  /** 服务端事件游标 */
  id?: string
  /** 事件类型 */
  type: T
  /** 事件数据 */
  data: SSEEventData
}

/** SSE事件处理器 */
export type SSEEventHandler<T extends string = FlowSSEEventType> = (event: SSEEvent<T>) => void

/**
 * 流程执行SSE处理器接口（手写字段清单，作为 EVENT_HANDLER_MAP 的校验目标）
 *
 * 单一事实源是 EVENT_HANDLER_MAP，新增事件类型只需在那里添加一行；
 * 此 interface 是 value 的合法命名空间 — 编译期确保 map 中的 handlerKey 必须存在
 */
export interface FlowSSEHandlers {
  /** 流程开始 / 恢复（两种事件复用） */
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
  /** 子Agent执行进度（实时输出预览） */
  onSubAgentProgress?: SSEEventHandler
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
  /** 流程预览（AI 创建/修改流程时推送） */
  onFlowPreview?: SSEEventHandler
  /** 知识库引用 */
  onKnowledgeCitations?: SSEEventHandler
  /** 问题反问（ask_user_question 工具触发） */
  onQuestionRequest?: SSEEventHandler
  /** 文件变更（侧栏实时刷新） */
  onFileChanged?: SSEEventHandler
}

/** 所有合法 handler 字段名（自动从 interface 派生） */
export type FlowSSEHandlerKey = keyof FlowSSEHandlers

/**
 * SSE 事件类型 → handler 字段名的单一事实源
 *
 * 新增事件只需在此表添加一行：eventType 必须是字符串字面量，
 * handlerKey 必须存在于 FlowSSEHandlers（satisfies 编译期校验，杜绝拼写错误）。
 * 同一 handler 可被多个事件类型共用（alias，如 flow_start / resume_start → onFlowStart）。
 */
export const EVENT_HANDLER_MAP = {
  flow_start: 'onFlowStart',
  resume_start: 'onFlowStart',
  node_start: 'onNodeStart',
  node_thinking: 'onNodeThinking',
  node_content: 'onNodeContent',
  node_done: 'onNodeDone',
  tool_call_start: 'onToolCallStart',
  tool_call_end: 'onToolCallEnd',
  tool_call_limit: 'onToolCallLimit',
  token_usage: 'onTokenUsage',
  waiting_human: 'onWaitingHuman',
  tool_approval_required: 'onToolApproval',
  sub_agent_progress: 'onSubAgentProgress',
  todo_update: 'onTodoUpdate',
  flow_done: 'onFlowDone',
  llm_retry: 'onLlmRetry',
  context_compressing: 'onContextCompressing',
  flow_preview: 'onFlowPreview',
  knowledge_citations: 'onKnowledgeCitations',
  question_request: 'onQuestionRequest',
  file_changed: 'onFileChanged',
  error: 'onError'
} as const satisfies Record<string, FlowSSEHandlerKey>

/** 所有已注册的 SSE 事件类型（自动派生，新增/删除 EVENT_HANDLER_MAP 时同步） */
export type FlowSSEEventType = keyof typeof EVENT_HANDLER_MAP

/** Agent会话SSE处理器接口（与流程执行共享同一接口） */
export type AgentSSEHandlers = FlowSSEHandlers
