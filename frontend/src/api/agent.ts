/**
 * Agent会话API
 * @description Agent会话相关的API接口，包含会话管理和流式聊天
 */
import { get, post, put } from './index'
import type { ApiResponse, ListResponse } from '@/types/common'
import type {
  AgentFlow,
  AgentSession,
  AgentMessage,
  AgentChatRequest,
  AgentResumeRequest,
  AgentDeleteMessagesResult,
  AgentRevertPreview,
  AgentFileChangeInfo,
  AgentFileChangeListItem
} from '@/types/agent'
import type { FlowSSEHandlers, SSEEvent, SSEWaitData } from '@/types/sse'
import { createFlowSSEConnection } from '@/utils/sse'

/** Agent等待数据（兼容旧类型） */
export type AgentWaitData = SSEWaitData

interface AgentRunStart {
  run_id: string
}

interface AgentRunStatus {
  running: boolean
  managed_running: boolean
  waiting_human: boolean
  run_id: string | null
  last_event_id: number
  terminal_event_type: string | null
  waiting_event: SSEEvent | null
}

type AgentRunAbort = (cancelRun?: boolean) => void | Promise<void>

const MAX_RECONNECT_DELAY = 5000

function cancelAgentRunRequest(agentId: number, sessionId: number): Promise<void> {
  return post<void>(`/agent/${agentId}/sessions/${sessionId}/cancel`, undefined, {
    showError: false
  }).then(() => undefined)
}

function connectAgentRun(
  agentId: number,
  sessionId: number,
  runId: string,
  afterEventId: number,
  handlers: FlowSSEHandlers
): () => void {
  let stopped = false
  let terminal = false
  let lastEventId = afterEventId
  let reconnectAttempts = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let abortCurrent: (() => void) | null = null

  const clearReconnectTimer = () => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  const markTerminal = () => {
    terminal = true
    clearReconnectTimer()
  }

  const streamHandlers: FlowSSEHandlers = {
    ...handlers,
    onWaitingHuman: event => {
      markTerminal()
      handlers.onWaitingHuman?.(event)
    },
    onFlowDone: event => {
      markTerminal()
      handlers.onFlowDone?.(event)
    },
    onError: event => {
      markTerminal()
      handlers.onError?.(event)
    }
  }

  function scheduleReconnect(error?: Error): void {
    abortCurrent = null
    if (stopped || terminal || reconnectTimer) return
    const status = Number(error?.message.match(/status: (\d+)/)?.[1])
    if (error?.name === 'SSEProtocolError' || (status >= 400 && status < 500)) {
      markTerminal()
      handlers.onError?.({
        type: 'error',
        data: {
          message:
            error?.name === 'SSEProtocolError'
              ? error.message
              : `Agent 事件订阅失败（HTTP ${status}）`
        }
      })
      return
    }
    const delay = Math.min(500 * 2 ** Math.min(reconnectAttempts, 4), MAX_RECONNECT_DELAY)
    reconnectAttempts++
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  function connect(): void {
    if (stopped || terminal) return
    abortCurrent = createFlowSSEConnection(
      `/api/agent/${agentId}/sessions/${sessionId}/events`,
      { run_id: runId, after_event_id: lastEventId },
      streamHandlers,
      '[Agent SSE]',
      {
        onEventId: eventId => {
          const parsedId = Number(eventId)
          if (Number.isInteger(parsedId) && parsedId > lastEventId) {
            lastEventId = parsedId
          }
          reconnectAttempts = 0
        },
        onDisconnect: scheduleReconnect
      }
    )
  }

  connect()
  return () => {
    stopped = true
    clearReconnectTimer()
    abortCurrent?.()
    abortCurrent = null
  }
}

function startAgentRun(
  startRequest: Promise<{ data: ApiResponse<AgentRunStart> }>,
  agentId: number,
  sessionId: number,
  handlers: FlowSSEHandlers
): AgentRunAbort {
  let stopped = false
  let abortSubscription: (() => void) | null = null
  let cancelPromise: Promise<void> | null = null

  const runPromise = startRequest.then(response => {
    const runId = response.data.data?.run_id
    if (!runId) throw new Error('后端未返回 Agent 执行 ID')
    return runId
  })

  void runPromise
    .then(runId => {
      if (stopped) return
      abortSubscription = connectAgentRun(agentId, sessionId, runId, 0, handlers)
    })
    .catch(async (error: unknown) => {
      if (stopped) return
      try {
        const response = await get<AgentRunStatus>(
          `/agent/${agentId}/sessions/${sessionId}/running`,
          undefined,
          { showError: false }
        )
        const status = response.data.data
        if (!stopped && status?.managed_running && status.run_id) {
          abortSubscription = connectAgentRun(agentId, sessionId, status.run_id, 0, handlers)
          return
        }
      } catch {
        // 启动响应丢失时尽力恢复；状态查询失败则显示原始错误。
      }
      if (stopped) return
      const event: SSEEvent = {
        type: 'error',
        data: { message: error instanceof Error ? error.message : '发送失败' }
      }
      handlers.onError?.(event)
    })

  return (cancelRun = false) => {
    stopped = true
    abortSubscription?.()
    abortSubscription = null
    if (!cancelRun) return

    if (!cancelPromise) {
      cancelPromise = runPromise
        .then(() => cancelAgentRunRequest(agentId, sessionId))
        .catch(() => cancelAgentRunRequest(agentId, sessionId))
    }
    return cancelPromise
  }
}

/** Agent API */
export const agentApi = {
  /**
   * 获取Agent列表
   * @param excludeId 排除的Agent ID（防止选择自身）
   */
  list(excludeId?: number) {
    const params: Record<string, number> = {}
    if (excludeId !== undefined) params.exclude_id = excludeId
    return get<ListResponse<AgentFlow>>('/agent/list', params)
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
   * @param workDir 可选，项目工作路径
   */
  createSession(agentId: number, workDir?: string) {
    return post<AgentSession>(`/agent/${agentId}/sessions`, {
      work_dir: workDir || null
    })
  },

  /**
   * 切换会话工作路径
   * @param agentId Agent ID
   * @param sessionId 会话ID
   * @param workDir 新工作路径，空串/null 表示清除（回退默认目录）
   */
  updateWorkDir(agentId: number, sessionId: number, workDir: string | null) {
    return put<AgentSession>(`/agent/${agentId}/sessions/${sessionId}/workdir`, {
      work_dir: workDir
    })
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
   * 搜索会话和消息内容
   * @param agentId Agent ID
   * @param keyword 搜索关键词
   */
  search(agentId: number, keyword: string) {
    return post<{
      sessions: Array<{ id: number; title: string; create_time: string }>
      messages: Array<{
        id: number
        session_id: number
        session_title: string
        role: string
        content_preview: string
        create_time: string
      }>
    }>(`/agent/${agentId}/search`, { keyword })
  },

  /**
   * 删除指定消息及之后的所有消息
   * @param agentId Agent ID
   * @param sessionId 会话ID
   * @param messageId 起始消息ID
   * @param restoreFiles 是否同步恢复该范围内追踪到的文件变更
   */
  deleteMessagesFrom(
    agentId: number,
    sessionId: number,
    messageId: number,
    restoreFiles: boolean = true
  ) {
    return get<AgentDeleteMessagesResult>(
      `/agent/${agentId}/sessions/${sessionId}/deleteMessages/${messageId}`,
      { restore_files: restoreFiles }
    )
  },

  /**
   * 预览回退到指定消息时将恢复的文件清单
   * @param agentId Agent ID
   * @param sessionId 会话ID
   * @param messageId 回退锚点消息ID
   */
  revertPreview(agentId: number, sessionId: number, messageId: number) {
    return get<AgentRevertPreview>(
      `/agent/${agentId}/sessions/${sessionId}/revertPreview/${messageId}`
    )
  },

  /**
   * 仅恢复文件变更（保留对话消息不动）
   * @param agentId Agent ID
   * @param sessionId 会话ID
   * @param messageId 回退锚点消息ID
   */
  restoreFilesOnly(agentId: number, sessionId: number, messageId: number) {
    return post<{ reverted_files: AgentFileChangeInfo[]; ok_count: number }>(
      `/agent/${agentId}/sessions/${sessionId}/restoreFiles/${messageId}`
    )
  },

  /**
   * 提交问题反问的答案
   * @param agentId Agent ID
   * @param sessionId 会话 ID
   * @param answers 用户所选标签列表（空 = 取消）
   */
  resolveQuestion(agentId: number, sessionId: number, answers: string[]) {
    return post<ApiResponse>(
      `/agent/${agentId}/sessions/${sessionId}/question/resolve`,
      { answers }
    )
  },

  /**
   * 获取会话的文件变更列表（按 message_id / 时间倒序）
   */
  listFileChanges(agentId: number, sessionId: number, limit = 50) {
    return get<{ list: AgentFileChangeListItem[]; total: number }>(
      `/agent/${agentId}/sessions/${sessionId}/file_changes`,
      { limit }
    )
  },

  /**
   * 获取单条文件变更的 diff 文本（backup → current）
   */
  getFileChangeDiff(
    agentId: number,
    sessionId: number,
    changeId: number | string
  ): Promise<{
    change_id: number | string
    file_path: string
    change_type: string
    tool_name: string
    backup_content: string | null
    current_content: string | null
    is_binary: boolean
    backup_missing: boolean
    backup_size: number
    current_size: number
  }> {
    return get(
      `/agent/${agentId}/sessions/${sessionId}/file_changes/${changeId}/diff`
    )
  },

  /**
   * 撤销单条文件变更（restore backup over current）
   */
  revertFileChange(
    agentId: number,
    sessionId: number,
    changeId: number | string
  ) {
    return post<ApiResponse<{ reverted: boolean; path: string }>>(
      `/agent/${agentId}/sessions/${sessionId}/file_changes/${changeId}/revert`,
      {}
    )
  },

  /**
   * 获取消息列表（分页）
   * @param agentId Agent ID
   * @param sessionId 会话ID
   * @param beforeId 分页游标，获取此ID之前的消息
   * @param limit 每页数量
   * @param afterId 增量游标，获取此ID之后的消息
   */
  getMessages(
    agentId: number,
    sessionId: number,
    beforeId?: number,
    limit: number = 20,
    afterId?: number
  ) {
    const params: Record<string, number> = { limit }
    if (beforeId !== undefined) params.before_id = beforeId
    if (afterId !== undefined) params.after_id = afterId
    return post<ListResponse<AgentMessage>>(
      `/agent/${agentId}/sessions/${sessionId}/messages/page`,
      params
    )
  },

  /**
   * 启动后台对话并订阅可重连事件流
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
  ): AgentRunAbort {
    return startAgentRun(
      post<AgentRunStart>(`/agent/${agentId}/sessions/${sessionId}/chat`, chatRequest, {
        showError: false
      }),
      agentId,
      sessionId,
      handlers
    )
  },

  /**
   * 启动后台恢复并订阅可重连事件流
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
  ): AgentRunAbort {
    return startAgentRun(
      post<AgentRunStart>(`/agent/${agentId}/sessions/${sessionId}/resume`, resumeRequest, {
        showError: false
      }),
      agentId,
      sessionId,
      handlers
    )
  },

  /** 重新订阅仍在后台运行的 Agent 执行 */
  subscribeRun(
    agentId: number,
    sessionId: number,
    runId: string,
    afterEventId: number,
    handlers: FlowSSEHandlers
  ): AgentRunAbort {
    const abortSubscription = connectAgentRun(agentId, sessionId, runId, afterEventId, handlers)
    let cancelPromise: Promise<void> | null = null
    return (cancelRun = false) => {
      abortSubscription()
      if (!cancelRun) return
      cancelPromise ||= cancelAgentRunRequest(agentId, sessionId)
      return cancelPromise
    }
  },

  /**
   * 取消Agent会话执行
   * @param agentId Agent ID
   * @param sessionId 会话ID
   */
  cancel(agentId: number, sessionId: number) {
    return post<void>(`/agent/${agentId}/sessions/${sessionId}/cancel`, undefined, {
      showError: false
    })
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
   * @param prompt 自定义压缩提示词，追加到默认提示词后，空则仅使用默认
   */
  compress(agentId: number, sessionId: number, prompt?: string) {
    return post<{ summary: string | null; kept_count: number; removed_count: number }>(
      `/agent/${agentId}/sessions/${sessionId}/compress`,
      { prompt: prompt || '' }
    )
  },

  /**
   * 查询会话是否正在压缩上下文
   * @param agentId Agent ID
   * @param sessionId 会话ID
   */
  compressStatus(agentId: number, sessionId: number) {
    return get<{
      status: 'compressing' | 'done' | 'failed'
      error?: string
      removed_count?: number
    }>(`/agent/${agentId}/sessions/${sessionId}/compressing`)
  },

  /**
   * 查询会话是否正在等待中断后的消息保存
   * @param agentId Agent ID
   * @param sessionId 会话ID
   */
  saveStatus(agentId: number, sessionId: number) {
    return get<{ saving: boolean }>(`/agent/${agentId}/sessions/${sessionId}/saving`)
  },

  /**
   * 查询会话是否正在后台执行（页面刷新后检测，据此显示停止按钮）
   * @param agentId Agent ID
   * @param sessionId 会话ID
   */
  runningStatus(agentId: number, sessionId: number) {
    return get<AgentRunStatus>(`/agent/${agentId}/sessions/${sessionId}/running`, undefined, {
      showError: false
    })
  },

  /**
   * 恢复内置 Agent 出厂设置
   */
  resetBuiltin() {
    return post<{ id: number }>('/agent/reset-builtin')
  }
}

// 兼容旧类型导出
export type AgentStreamHandlers = FlowSSEHandlers
export type AgentStreamEvent = { type: string; data: Record<string, unknown> }
export type AgentStreamEventHandler = (event: AgentStreamEvent) => void
