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
   * 流式恢复执行（多轮人工交互 / 工具审批决策）
   * @param executionId 执行记录ID
   * @param input 用户输入（审批决策时忽略）
   * @param handlers 事件处理器
   * @param approval 审批决策；提供时本次 resume 为审批提交（input 忽略）
   * @returns 中断连接函数
   */
  resumeStream(
    executionId: number,
    input: string,
    handlers: FlowSSEHandlers,
    approval?: { action: 'approved' | 'rejected'; rememberSession: boolean }
  ): () => void {
    const url = `/api/execution/human-input-stream/${executionId}`
    const payload = approval
      ? { input, approval_action: approval.action, remember_session: approval.rememberSession }
      : { input }
    return createFlowSSEConnection(url, payload, handlers, '[Flow SSE Resume]')
  },

  /**
   * 提交工具审批决策（Flow future 路线；resolve 后端 future 唤醒执行流）
   * @param executionId 执行记录ID（审批队列 key）
   * @param approvalId 审批批次ID
   * @param action approved / rejected（整批粒度；提供 toolDecisions 时表示「是否存在拒绝项」）
   * @param rememberSession 批准时是否记住本执行免审
   * @param toolDecisions 逐工具决策（方案B）：{tool_call_id: approved|rejected}，缺省整批
   */
  submitToolApproval(
    executionId: number,
    approvalId: string,
    action: 'approved' | 'rejected',
    rememberSession: boolean,
    toolDecisions?: Record<string, 'approved' | 'rejected'>
  ): Promise<unknown> {
    return post(`/execution/tool-approval/${executionId}`, {
      approval_id: approvalId,
      action,
      remember_session: rememberSession,
      ...(toolDecisions ? { tool_decisions: toolDecisions } : {})
    })
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
