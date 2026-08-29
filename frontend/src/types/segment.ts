/**
 * 消息分段相关类型定义
 * @description 统一的消息分段类型，供 AIMessageContent、Agent 聊天、Flow 执行共用
 */

import type { KnowledgeReference } from '@/types/knowledge'

/** 任务计划项 */
export interface TodoItem {
  id?: number
  content: string
  status: string
  priority: string
  position?: number
}

/** 工具调用信息 */
export interface ToolCall {
  id?: string
  name: string
  args?: Record<string, unknown>
  status: 'running' | 'success' | 'error'
  result?: unknown
  /** 子Agent实时输出快照（ask_* 工具执行中嵌入展示） */
  liveOutput?: string
  /** 子Agent名称 */
  liveAgentName?: string
}

/** 消息分段类型 */
export type SegmentType = 'thinking' | 'content' | 'tool' | 'todo'

/** 消息分段 */
export interface Segment {
  type: SegmentType
  /** 流式分段稳定标识，供列表渲染使用 */
  id?: string
  dbMsgId?: number
  thinking?: string
  content?: string
  tool?: ToolCall
  todo?: TodoItem[]
  knowledge_citations?: KnowledgeReference[]
}
