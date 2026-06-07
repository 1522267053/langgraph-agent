/**
 * 流程执行API
 * @description 流程执行相关的API接口，包含同步和流式执行
 */
import { get, post } from './index'
import type { PaginatedResponse, PaginationParams } from '@/types/common'
import type {
  FlowExecution,
  NodeExecution,
  ExecutionInput,
  WaitStatusData,
  ConversationHistoryData
} from '@/types/execution'
import type { FlowSSEHandlers } from '@/types/sse'
import { createFlowSSEConnection } from '@/utils/sse'

/** 流程执行API */
export const executionApi = {
  /**
   * 同步启动流程执行
   * @param flowId 流程ID
   * @param input 输入数据
   */
  start(flowId: number, input?: ExecutionInput) {
    return post<FlowExecution>(`/execution/start/${flowId}`, input)
  },

  /**
   * 获取执行记录
   * @param id 执行记录ID
   */
  get(id: number) {
    return get<FlowExecution>(`/execution/get/${id}`)
  },

  /**
   * 获取执行节点列表
   * @param executionId 执行记录ID
   */
  getNodes(executionId: number) {
    return get<NodeExecution[]>(`/execution/nodes/${executionId}`)
  },

  /**
   * 取消执行
   * @param id 执行记录ID
   */
  cancel(id: number) {
    return post<FlowExecution>(`/execution/cancel/${id}`)
  },

  /**
   * 分页查询执行记录
   * @param params 分页参数
   */
  page(params: PaginationParams<FlowExecution>) {
    return post<PaginatedResponse<FlowExecution>>('/execution/page', params)
  },

  /**
   * 流式执行流程（SSE）
   * @param flowId 流程ID
   * @param input 输入数据
   * @param handlers 事件处理器
   * @returns 中断连接函数
   */
  stream(flowId: number, input: ExecutionInput | undefined, handlers: FlowSSEHandlers): () => void {
    const url = `/api/execution/stream/${flowId}`
    return createFlowSSEConnection(url, input || {}, handlers, '[Flow SSE]')
  },

  /**
   * 流式恢复执行（多轮人工交互）
   * @param executionId 执行记录ID
   * @param input 用户输入
   * @param handlers 事件处理器
   * @returns 中断连接函数
   */
  resumeStream(executionId: number, input: string, handlers: FlowSSEHandlers): () => void {
    const url = `/api/execution/human-input-stream/${executionId}`
    return createFlowSSEConnection(url, { input }, handlers, '[Flow SSE Resume]')
  },

  /**
   * 获取等待状态
   * @param executionId 执行记录ID
   */
  getWaitStatus(executionId: number) {
    return get<WaitStatusData>(`/execution/wait-status/${executionId}`)
  },

  /**
   * 获取对话历史
   * @param executionId 执行记录ID
   * @param nodeKey 可选，节点Key
   */
  getConversationHistory(executionId: number, nodeKey?: string) {
    const params = nodeKey ? { node_key: nodeKey } : {}
    return get<ConversationHistoryData>(`/execution/conversation-history/${executionId}`, params)
  }
}

// 兼容旧导出
export type { FlowSSEHandlers as StreamHandlers }
