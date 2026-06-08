/**
 * Agent会话API
 * @description Agent会话相关的API接口，包含会话管理和流式聊天
 */
import { get, post } from './index'
import type { ListResponse } from '@/types/common'
import type {
  AgentFlow,
  AgentSession,
  AgentMessage,
  AgentChatRequest,
  AgentResumeRequest
} from '@/types/agent'
import type { FlowSSEHandlers, SSEWaitData } from '@/types/sse'
import { createFlowSSEConnection } from '@/utils/sse'

/** Agent等待数据（兼容旧类型） */
export type AgentWaitData = SSEWaitData

/** Agent API */
export const agentApi = {
  /**
   * 获取Agent列表
   */
  list() {
    return get<ListResponse<AgentFlow>>('/agent/list')
  },

  /**
   * 获取Agent详情
   * @param id Agent ID
   */
  get(id: number) {
    return get<AgentFlow>(`/agent/get/${id}`)
  },

  /**
   * 获取会话列表（分页）
   * @param agentId Agent ID
   * @param page 页码
   * @param pageSize 每页数量
   */
  getSessions(agentId: number, page: number = 1, pageSize: number = 20) {
    return post<ListResponse<AgentSession>>(`/agent/${agentId}/sessions/page`, {
      page,
      page_size: pageSize
    })
  },

  /**
   * 创建会话
   * @param agentId Agent ID
   */
  createSession(agentId: number) {
    return post<AgentSession>(`/agent/${agentId}/sessions`)
  },

  /**
   * 删除会话
   * @param agentId Agent ID
   * @param sessionId 会话ID
   */
  deleteSession(agentId: number, sessionId: number) {
    return get<void>(`/agent/${agentId}/deleteSession/${sessionId}`)
  },

  /**
   * 删除指定消息及之后的所有消息
   * @param agentId Agent ID
   * @param sessionId 会话ID
   * @param messageId 起始消息ID
   */
  deleteMessagesFrom(agentId: number, sessionId: number, messageId: number) {
    return get<{ content: string }>(
      `/agent/${agentId}/sessions/${sessionId}/deleteMessages/${messageId}`
    )
  },

  /**
   * 获取消息列表（分页）
   * @param agentId Agent ID
   * @param sessionId 会话ID
   * @param beforeId 分页游标，获取此ID之前的消息
   * @param limit 每页数量
   */
  getMessages(agentId: number, sessionId: number, beforeId?: number, limit: number = 20) {
    const params: Record<string, number> = { limit }
    if (beforeId !== undefined) params.before_id = beforeId
    return post<ListResponse<AgentMessage>>(
      `/agent/${agentId}/sessions/${sessionId}/messages/page`,
      params
    )
  },

  /**
   * 发送消息（流式）
   * @param agentId Agent ID
   * @param sessionId 会话ID
   * @param chatRequest 聊天请求
   * @param handlers 事件处理器
   * @returns 中断连接函数
   */
  chat(
    agentId: number,
    sessionId: number,
    chatRequest: AgentChatRequest,
    handlers: FlowSSEHandlers
  ): () => void {
    const url = `/api/agent/${agentId}/sessions/${sessionId}/chat`
    return createFlowSSEConnection(url, chatRequest, handlers, '[Agent SSE]')
  },

  /**
   * 恢复会话（人工交互后继续）
   * @param agentId Agent ID
   * @param sessionId 会话ID
   * @param resumeRequest 恢复请求
   * @param handlers 事件处理器
   * @returns 中断连接函数
   */
  resume(
    agentId: number,
    sessionId: number,
    resumeRequest: AgentResumeRequest,
    handlers: FlowSSEHandlers
  ): () => void {
    const url = `/api/agent/${agentId}/sessions/${sessionId}/resume`
    return createFlowSSEConnection(url, resumeRequest, handlers, '[Agent SSE Resume]')
  },

  /**
   * 取消Agent会话执行
   * @param agentId Agent ID
   * @param sessionId 会话ID
   */
  cancel(agentId: number, sessionId: number) {
    return post<void>(`/agent/${agentId}/sessions/${sessionId}/cancel`)
  },

  /**
   * 工具确认（批准/拒绝）
   * @param agentId Agent ID
   * @param sessionId 会话ID
   * @param action "approved" 或 "rejected"
   */
  toolApproval(agentId: number, sessionId: number, action: 'approved' | 'rejected') {
    return post<void>(`/agent/${agentId}/sessions/${sessionId}/tool_approval`, { action })
  },

  /**
   * 压缩会话上下文
   * @param agentId Agent ID
   * @param sessionId 会话ID
   */
  compress(agentId: number, sessionId: number) {
    return post<{ summary: string | null; kept_count: number; removed_count: number }>(
      `/agent/${agentId}/sessions/${sessionId}/compress`
    )
  },

  /**
   * 查询会话是否正在压缩上下文
   * @param agentId Agent ID
   * @param sessionId 会话ID
   */
  compressStatus(agentId: number, sessionId: number) {
    return get<{ compressing: boolean }>(`/agent/${agentId}/sessions/${sessionId}/compressing`)
  },

  /**
   * 查询会话是否正在等待中断后的消息保存
   * @param agentId Agent ID
   * @param sessionId 会话ID
   */
  saveStatus(agentId: number, sessionId: number) {
    return get<{ saving: boolean }>(`/agent/${agentId}/sessions/${sessionId}/saving`)
  }
}

// 兼容旧类型导出
export type AgentStreamHandlers = FlowSSEHandlers
export type AgentStreamEvent = { type: string; data: Record<string, unknown> }
export type AgentStreamEventHandler = (event: AgentStreamEvent) => void
