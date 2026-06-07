/**
 * 消息分段相关类型定义
 * @description 统一的消息分段类型，供 AIMessageContent、Agent 聊天、Flow 执行共用
 */

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
}

/** 消息分段类型 */
export type SegmentType = 'thinking' | 'content' | 'tool' | 'todo'

/** 消息分段 */
export interface Segment {
  type: SegmentType
  dbMsgId?: number
  thinking?: string
  content?: string
  tool?: ToolCall
  todo?: TodoItem[]
}
