/**
 * Agent会话状态管理（优化版）
 * @description 使用composables重构的Agent Store，提供更好的代码复用
 */
import { defineStore } from 'pinia'
import { ref, computed, nextTick } from 'vue'
import type { AgentFlow, AgentSession, AgentMessage } from '@/types/agent'
import type { SSEWaitData, SSEEvent } from '@/types/sse'
import type {
  StreamingMessage,
  ToolCall,
  MessageFile,
  TodoItem
} from '@/composables/useStreamingMessage'
import { agentApi } from '@/api/agent'
import { createOnToolCallLimitHandler, createOnLlmRetryHandler } from '@/composables/useSSEHandlers'
import { useStreamingMessage } from '@/composables'
import { ElMessage } from 'element-plus'

export const useAgentStore = defineStore('agent', () => {
  // ========== 基础数据 ==========
  const agents = ref<AgentFlow[]>([])
  const currentAgent = ref<AgentFlow | null>(null)
  const sessions = ref<AgentSession[]>([])
  const currentSession = ref<AgentSession | null>(null)
  const messages = ref<AgentMessage[]>([])

  // ========== 消息分页状态 ==========
  const messageTotal = ref(0)
  const hasMoreMessages = computed(() => messages.value.length < messageTotal.value)
  const loadingMoreMessages = ref(false)

  // ========== 会话分页状态 ==========
  const sessionPage = ref(1)
  const sessionPageSize = ref(10)
  const sessionTotal = ref(0)

  // ========== 加载状态 ==========
  const loading = ref(false)
  const sessionsLoading = ref(false)
  const messagesLoading = ref(false)

  // ========== 流式消息处理（使用composable） ==========
  const {
    messages: chatMessages,
    isStreaming,
    thinkingContent,
    textContent,
    currentSegmentType,
    todos,
    addUserMessage,
    startStreaming,
    appendThinking,
    appendContent,
    addToolSegment,
    updateToolSegment,
    addTodoSegment,
    updateTodos,
    addTokenUsage,
    stopStreaming,
    clearMessages,
    latestPromptTokens
  } = useStreamingMessage()

  const totalSessionTokens = computed(() =>
    chatMessages.value.reduce((sum, m) => sum + (m.total_tokens || 0), 0)
  )

  // ========== SSE连接（使用composable） ==========

  // ========== 人工交互状态 ==========
  const currentWaitData = ref<SSEWaitData | null>(null)
  const isWaitingHuman = ref(false)

  // ========== 工具确认状态（仅Agent模式） ==========
  const isWaitingToolApproval = ref(false)
  const pendingToolCalls = ref<{ name: string; args: Record<string, unknown>; id?: string }[]>([])
  const pendingApprovalNeeded = ref<string[]>([])
  const approvalCountdown = ref(0)
  let approvalTimer: ReturnType<typeof setInterval> | null = null

  // ========== 子Agent工具审批状态 ==========
  const subAgentApproval = ref<{
    isSubAgent: boolean
    agentId: number
    sessionId: number
    agentName: string
  } | null>(null)

  // ========== 压缩上下文状态 ==========
  const isCompressing = ref(false)
  let compressPollTimer: ReturnType<typeof setInterval> | null = null

  // ========== 中断保存状态 ==========
  const isStopping = ref(false)
  let savePollTimer: ReturnType<typeof setTimeout> | null = null

  // ========== 中断函数引用 ==========
  let streamAbort: (() => void) | null = null
  let wasFirstMessage = false
  let isResume = false

  // ========== 数据加载方法 ==========

  /**
   * 加载Agent列表
   */
  async function loadAgents() {
    loading.value = true
    try {
      const res = await agentApi.list()
      if (res.data.code === 1) {
        agents.value = res.data.data?.list || []
      }
    } finally {
      loading.value = false
    }
  }

  /**
   * 加载单个Agent
   */
  async function loadAgent(id: number) {
    loading.value = true
    try {
      const res = await agentApi.get(id)
      if (res.data.code === 1) {
        currentAgent.value = res.data.data
      }
    } finally {
      loading.value = false
    }
  }

  /**
   * 加载会话列表（分页）
   */
  async function loadSessions(agentId: number, page: number = 1) {
    sessionsLoading.value = true
    sessionPage.value = page
    try {
      const res = await agentApi.getSessions(agentId, page, sessionPageSize.value)
      if (res.data.code === 1) {
        sessions.value = res.data.data?.list || []
        sessionTotal.value = res.data.data?.total || 0
      }
    } finally {
      sessionsLoading.value = false
    }
  }

  /**
   * 创建新会话
   */
  async function createSession(agentId: number): Promise<AgentSession | null> {
    try {
      const res = await agentApi.createSession(agentId)
      if (res.data.code === 1) {
        await loadSessions(agentId, 1)
        const session = res.data.data
        return session
      }
    } catch {
      // error handled by interceptor
    }
    return null
  }

  /**
   * 删除会话
   */
  async function deleteSession(agentId: number, sessionId: number) {
    try {
      await agentApi.deleteSession(agentId, sessionId)
      if (currentSession.value?.id === sessionId) {
        currentSession.value = null
        messages.value = []
        clearMessages()
      }
      await loadSessions(agentId, 1)
    } catch {
      // error handled by interceptor
    }
  }

  /**
   * 选择会话
   */
  async function selectSession(agentId: number, session: AgentSession) {
    messagesLoading.value = true
    clearMessages()
    cancelStream()

    currentSession.value = session
    try {
      const res = await agentApi.getMessages(agentId, session.id)
      if (res.data.code === 1) {
        messages.value = res.data.data?.list || []
        messageTotal.value = res.data.data?.total || 0
        rebuildChatMessages()
      }
    } finally {
      messagesLoading.value = false
    }

    // 检查会话是否正在压缩（页面刷新/重选场景）
    try {
      const statusRes = await agentApi.compressStatus(agentId, session.id)
      if (statusRes.data.code === 1 && statusRes.data.data?.compressing) {
        startCompressPolling(agentId, session.id)
      }
    } catch {
      // 状态检查失败不影响正常使用
    }
  }

  /**
   * 加载更多历史消息（向上翻页）
   */
  async function loadMoreMessages(agentId: number) {
    if (!currentSession.value || loadingMoreMessages.value || !hasMoreMessages.value) return 0
    const firstMsgId = messages.value[0]?.id
    if (!firstMsgId) return 0

    loadingMoreMessages.value = true
    try {
      const res = await agentApi.getMessages(agentId, currentSession.value.id, firstMsgId)
      if (res.data.code === 1) {
        const olderMessages = res.data.data?.list || []
        if (olderMessages.length > 0) {
          messageTotal.value = res.data.data?.total || messageTotal.value
          messages.value = [...olderMessages, ...messages.value]
          rebuildChatMessages()
          await nextTick()
          return olderMessages.length
        }
        // 返回空说明已到顶，修正 total 防止 hasMoreMessages 永远为 true
        messageTotal.value = messages.value.length
      }
    } catch (e) {
      console.error('[loadMoreMessages] error', e)
    } finally {
      loadingMoreMessages.value = false
    }
    return 0
  }

  // ========== 消息处理 ==========

  /**
   * 从 DB 消息列表构建 StreamingMessage[]（纯函数，不修改任何 ref）
   */
  function buildChatMessagesFromDB(dbMessages: AgentMessage[]): StreamingMessage[] {
    const result: StreamingMessage[] = []

    const toolResultMap = new Map<string, { content: string; status: 'success' | 'error' }>()
    for (const msg of dbMessages) {
      if (msg.role === 'tool' && msg.tool_call_id) {
        const status = msg.status === 'error' ? 'error' : 'success'
        toolResultMap.set(msg.tool_call_id, { content: msg.content, status })
      }
    }

    let currentAssistant: StreamingMessage | null = null

    for (const msg of dbMessages) {
      const role = msg.role === 'human' ? 'user' : msg.role === 'ai' ? 'assistant' : msg.role

      if (role === 'user') {
        if (currentAssistant) {
          result.push(currentAssistant)
          currentAssistant = null
        }
        result.push({
          id: `msg-${msg.id}`,
          role: 'user',
          content: msg.original_content || msg.content,
          segments: [{ type: 'content', content: msg.original_content || msg.content }],
          files: msg.files as MessageFile[] | undefined,
          createdAt: new Date(msg.created_at || Date.now())
        })
      } else if (role === 'assistant') {
        if (!currentAssistant) {
          currentAssistant = {
            id: `msg-${msg.id}`,
            role: 'assistant',
            content: '',
            segments: [],
            createdAt: new Date(msg.created_at || Date.now())
          }
        }

        if (msg.thinking) {
          currentAssistant.segments.push({
            type: 'thinking',
            thinking: msg.thinking,
            dbMsgId: msg.id
          })
          currentAssistant.thinking = msg.thinking
        }

        if (msg.content) {
          currentAssistant.segments.push({ type: 'content', content: msg.content, dbMsgId: msg.id })
          currentAssistant.content = msg.content
        }

        if (msg.tool_calls && Array.isArray(msg.tool_calls) && msg.tool_calls.length > 0) {
          if (!currentAssistant.tools) {
            currentAssistant.tools = []
          }
          for (const tc of msg.tool_calls) {
            const toolResult = toolResultMap.get(tc.id as string)
            let resultData: unknown = toolResult?.content
            try {
              resultData = JSON.parse(toolResult?.content || '')
            } catch {
              // keep raw content
            }
            const tool: ToolCall = {
              id: tc.id as string,
              name: tc.name as string,
              args: (tc.args as Record<string, unknown>) || {},
              status: toolResult?.status || 'success',
              result: resultData
            }
            currentAssistant.tools.push(tool)
            currentAssistant.segments.push({ type: 'tool', tool })

            const toolName = tc.name as string
            if (toolName === 'todowrite' && toolResult?.status === 'success') {
              try {
                const todosArgs = JSON.parse((tc.args as { todos?: string })?.todos || '[]')
                if (Array.isArray(todosArgs)) {
                  currentAssistant.segments.push({
                    type: 'todo',
                    todo: todosArgs.map(
                      (item: { content?: string; status?: string; priority?: string }) => ({
                        content: item.content || '',
                        status: item.status || 'pending',
                        priority: item.priority || 'medium'
                      })
                    )
                  })
                }
              } catch {
                // ignore parse errors
              }
            } else if (
              toolName === 'todoread' &&
              toolResult?.status === 'success' &&
              typeof resultData === 'object' &&
              resultData !== null
            ) {
              const parsed = resultData as { todos?: unknown }
              if (Array.isArray(parsed.todos)) {
                currentAssistant.segments.push({
                  type: 'todo',
                  todo: parsed.todos.map(
                    (item: { content?: string; status?: string; priority?: string }) => ({
                      content: item.content || '',
                      status: item.status || 'pending',
                      priority: item.priority || 'medium'
                    })
                  )
                })
              }
            }
          }
        }

        if (msg.prompt_tokens) {
          currentAssistant.prompt_tokens = (currentAssistant.prompt_tokens || 0) + msg.prompt_tokens
          currentAssistant.latest_prompt_tokens = msg.prompt_tokens
        }
        if (msg.completion_tokens) {
          currentAssistant.completion_tokens =
            (currentAssistant.completion_tokens || 0) + msg.completion_tokens
        }
        if (msg.total_tokens) {
          currentAssistant.total_tokens = (currentAssistant.total_tokens || 0) + msg.total_tokens
        }
      }
    }

    if (currentAssistant) {
      result.push(currentAssistant)
    }

    return result
  }

  /**
   * 从历史消息就地 diff 更新聊天消息列表（不 clearMessages，保留 Vue DOM 稳定性）
   * 用于 selectSession、onFlowDone、loadMoreMessages 等场景
   */
  function rebuildChatMessages() {
    const rebuilt = buildChatMessagesFromDB(messages.value)

    for (let i = 0; i < rebuilt.length; i++) {
      if (i < chatMessages.value.length) {
        Object.assign(chatMessages.value[i], rebuilt[i])
      } else {
        chatMessages.value.push(rebuilt[i])
      }
    }
    if (chatMessages.value.length > rebuilt.length) {
      chatMessages.value.splice(rebuilt.length)
    }

    thinkingContent.value = ''
    textContent.value = ''
    currentSegmentType.value = null
    isStreaming.value = false

    for (let i = messages.value.length - 1; i >= 0; i--) {
      const m = messages.value[i]
      if (m.role === 'ai') {
        latestPromptTokens.value = m.latest_prompt_tokens || m.prompt_tokens || 0
        break
      }
    }
  }

  /**
   * 创建SSE事件处理器
   */
  function createStreamHandlers() {
    return {
      onFlowStart: () => {
        startStreaming()
      },
      onNodeStart: (_event: SSEEvent) => {
        // 节点开始处理
      },
      onNodeThinking: (event: SSEEvent) => {
        appendThinking(event.data.content || '')
      },
      onNodeContent: (event: SSEEvent) => {
        appendContent(event.data.content || '')
      },
      onNodeDone: () => {
        // 节点完成处理
      },
      onToolCallStart: (event: SSEEvent) => {
        addToolSegment(event.data.tool_name || '', event.data.tool_args || {}, 'running')
      },
      onToolCallEnd: (event: SSEEvent) => {
        updateToolSegment(
          event.data.tool_name || '',
          event.data.status === 'error' ? 'error' : 'success',
          event.data.result
        )
      },
      onToolCallLimit: createOnToolCallLimitHandler(),
      onTokenUsage: (event: SSEEvent) => {
        addTokenUsage(
          event.data.prompt_tokens || 0,
          event.data.completion_tokens || 0,
          event.data.total_tokens || 0
        )
      },
      onTodoUpdate: (event: SSEEvent) => {
        const newTodos = (event.data.todos || []) as TodoItem[]
        updateTodos(newTodos)
        addTodoSegment(newTodos)
      },
      onWaitingHuman: (event: SSEEvent) => {
        stopStreaming()
        isWaitingHuman.value = true
        currentWaitData.value = event.data.wait_data || {
          type: 'human',
          node_key: event.data.node_key || '',
          question: event.data.question || '请提供输入',
          context: event.data.context
        }
      },
      onToolApproval: (event: SSEEvent) => {
        isWaitingToolApproval.value = true
        pendingToolCalls.value = event.data.tool_calls || []
        pendingApprovalNeeded.value = event.data.approval_needed || []
        // 检测子Agent审批
        if (event.data.is_sub_agent) {
          subAgentApproval.value = {
            isSubAgent: true,
            agentId: event.data.sub_agent_id,
            sessionId: event.data.sub_session_id,
            agentName: event.data.sub_agent_name || '子Agent'
          }
        } else {
          subAgentApproval.value = null
        }
        startApprovalCountdown(298)
      },
      onContextCompressing: (event: SSEEvent) => {
        const status = event.data.status as string
        if (status === 'compressing') {
          isCompressing.value = true
        } else {
          isCompressing.value = false
          if (status === 'done' && currentAgent.value && currentSession.value) {
            // 压缩完成后刷新消息列表
            try {
              agentApi.getMessages(currentAgent.value.id, currentSession.value.id).then(res => {
                if (res.data.code === 1) {
                  messages.value = res.data.data?.list || []
                  messageTotal.value = res.data.data?.total || 0
                  rebuildChatMessages()
                }
              })
            } catch (e) {
              console.error('[onContextCompressing] 刷新消息失败', e)
            }
          }
        }
      },
      onFlowDone: async () => {
        stopStreaming()
        isCompressing.value = false
        if (isWaitingToolApproval.value) {
          isWaitingToolApproval.value = false
          pendingToolCalls.value = []
          pendingApprovalNeeded.value = []
          stopApprovalCountdown()
          ElMessage.warning('工具确认超时，连接已断开')
        }
        if (isResume) {
          isResume = false
        }
        if (currentAgent.value && currentSession.value) {
          try {
            const res = await agentApi.getMessages(currentAgent.value.id, currentSession.value.id)
            if (res.data.code === 1) {
              messages.value = res.data.data?.list || []
              messageTotal.value = res.data.data?.total || 0
              rebuildChatMessages()
            }
          } catch (e) {
            console.error('[onFlowDone] 刷新消息失败', e)
          }
        }
        if (currentAgent.value && wasFirstMessage) {
          await loadSessions(currentAgent.value.id, sessionPage.value)
          wasFirstMessage = false
        }
      },
      onLlmRetry: createOnLlmRetryHandler(),
      onError: async (event: SSEEvent) => {
        stopStreaming()
        isCompressing.value = false
        if (isWaitingToolApproval.value) {
          isWaitingToolApproval.value = false
          pendingToolCalls.value = []
          pendingApprovalNeeded.value = []
          stopApprovalCountdown()
        }
        if (currentAgent.value && currentSession.value) {
          try {
            const res = await agentApi.getMessages(currentAgent.value.id, currentSession.value.id)
            if (res.data.code === 1) {
              messages.value = res.data.data?.list || []
              messageTotal.value = res.data.data?.total || 0
              rebuildChatMessages()
            }
          } catch (e) {
            console.error('[onError] 刷新消息失败', e)
          }
        }
        ElMessage.error(event.data.message || '发送失败')
      }
    }
  }

  // ========== 发送消息 ==========

  /**
   * 发送消息
   */
  function sendMessage(
    content: string,
    params: Record<string, unknown> = {},
    files?: MessageFile[]
  ) {
    if (!currentAgent.value || !currentSession.value) return

    wasFirstMessage = messages.value.length === 0

    addUserMessage(content, files)
    startStreaming()

    streamAbort = agentApi.chat(
      currentAgent.value.id,
      currentSession.value.id,
      { content, params },
      createStreamHandlers()
    )
  }

  /**
   * 恢复会话（人工输入后继续）
   */
  function resumeWithInput(humanInput: string) {
    if (!currentAgent.value || !currentSession.value) return

    isResume = true
    addUserMessage(humanInput)
    isWaitingHuman.value = false
    currentWaitData.value = null
    startStreaming()

    streamAbort = agentApi.resume(
      currentAgent.value.id,
      currentSession.value.id,
      { human_input: humanInput },
      createStreamHandlers()
    )
  }

  function startApprovalCountdown(seconds: number) {
    stopApprovalCountdown()
    approvalCountdown.value = seconds
    approvalTimer = setInterval(() => {
      if (approvalCountdown.value > 0) {
        approvalCountdown.value--
      } else {
        stopApprovalCountdown()
      }
    }, 1000)
  }

  function stopApprovalCountdown() {
    if (approvalTimer) {
      clearInterval(approvalTimer)
      approvalTimer = null
    }
    approvalCountdown.value = 0
  }

  /**
   * 批准工具执行（工具确认后继续）
   */
  async function approveToolCalls() {
    if (!currentAgent.value || !currentSession.value) return
    isWaitingToolApproval.value = false
    pendingToolCalls.value = []
    pendingApprovalNeeded.value = []
    stopApprovalCountdown()
    try {
      if (subAgentApproval.value?.isSubAgent) {
        await agentApi.toolApproval(
          subAgentApproval.value.agentId,
          subAgentApproval.value.sessionId,
          'approved'
        )
        subAgentApproval.value = null
      } else {
        await agentApi.toolApproval(currentAgent.value.id, currentSession.value.id, 'approved')
      }
    } catch {
      // error handled by interceptor
    }
  }

  async function rejectToolCalls() {
    if (!currentAgent.value || !currentSession.value) return
    isWaitingToolApproval.value = false
    pendingToolCalls.value = []
    pendingApprovalNeeded.value = []
    stopApprovalCountdown()
    try {
      if (subAgentApproval.value?.isSubAgent) {
        await agentApi.toolApproval(
          subAgentApproval.value.agentId,
          subAgentApproval.value.sessionId,
          'rejected'
        )
        subAgentApproval.value = null
      } else {
        await agentApi.toolApproval(currentAgent.value.id, currentSession.value.id, 'rejected')
      }
    } catch {
      // error handled by interceptor
    }
  }

  /**
   * 取消流式输出（仅断开SSE连接）
   * @param waitForSave 是否等待后端 save_to_db 完成后刷新消息（仅中断场景传 true）
   */
  function cancelStream(waitForSave = false) {
    if (streamAbort) {
      streamAbort()
      streamAbort = null
    }
    if (!waitForSave) {
      stopStreaming()
    }
    isWaitingHuman.value = false
    currentWaitData.value = null
    isWaitingToolApproval.value = false
    pendingToolCalls.value = []
    pendingApprovalNeeded.value = []
    stopApprovalCountdown()
    stopSavePolling()
    if (waitForSave && currentAgent.value && currentSession.value) {
      startSavePolling(currentAgent.value.id, currentSession.value.id)
    }
  }

  /**
   * 轮询等待后端中断后的消息保存完成，然后刷新消息
   */
  function startSavePolling(agentId: number, sessionId: number) {
    stopSavePolling()
    const startTime = Date.now()
    const timeout = 8000
    const onDone = () => {
      stopStreaming()
      isStopping.value = false
      ElMessage.success('停止成功')
    }
    const poll = async () => {
      if (!currentAgent.value || currentSession.value?.id !== sessionId) {
        stopSavePolling()
        stopStreaming()
        isStopping.value = false
        return
      }
      try {
        const res = await agentApi.saveStatus(agentId, sessionId)
        if (res.data.code === 1 && res.data.data?.saving) {
          if (Date.now() - startTime >= timeout) {
            stopSavePolling()
            refreshMessages(agentId, sessionId)
            onDone()
          } else {
            savePollTimer = setTimeout(poll, 1000)
          }
        } else {
          stopSavePolling()
          refreshMessages(agentId, sessionId)
          onDone()
        }
      } catch {
        stopSavePolling()
        refreshMessages(agentId, sessionId)
        onDone()
      }
    }
    poll()
  }

  function refreshMessages(agentId: number, sessionId: number) {
    if (currentSession.value?.id === sessionId) {
      agentApi
        .getMessages(agentId, sessionId)
        .then(res => {
          if (res.data.code === 1 && currentSession.value?.id === sessionId) {
            messages.value = res.data.data?.list || []
            messageTotal.value = res.data.data?.total || 0
            rebuildChatMessages()
          }
        })
        .catch(() => {})
    }
  }

  function stopSavePolling() {
    if (savePollTimer) {
      clearTimeout(savePollTimer)
      savePollTimer = null
    }
  }

  /**
   * 中断执行（通知后端停止并断开SSE）
   */
  async function interruptExecution() {
    if (isStopping.value) return
    isStopping.value = true
    if (currentAgent.value && currentSession.value) {
      await agentApi.cancel(currentAgent.value.id, currentSession.value.id)
    }
    cancelStream(true)
  }

  /**
   * 删除指定消息及之后的所有消息，   * @param messageId 要删除的消息ID
   * @returns 被删除的用户消息内容，   */
  async function deleteMessagesFrom(messageId: number): Promise<string | null> {
    if (!currentAgent.value || !currentSession.value) return null

    try {
      const res = await agentApi.deleteMessagesFrom(
        currentAgent.value.id,
        currentSession.value.id,
        messageId
      )
      if (res.data.code === 1) {
        const deletedContent = res.data.data?.content || null
        const beforeCount = messages.value.length
        messages.value = messages.value.filter(m => m.id < messageId)
        messageTotal.value = Math.max(0, messageTotal.value - (beforeCount - messages.value.length))
        rebuildChatMessages()
        return deletedContent
      }
      return null
    } catch {
      // error handled by interceptor
      return null
    }
  }

  /**
   * 重置状态
   */
  function resetState() {
    currentAgent.value = null
    sessions.value = []
    currentSession.value = null
    messages.value = []
    clearMessages()
    isWaitingHuman.value = false
    currentWaitData.value = null
    sessionPage.value = 1
    sessionTotal.value = 0
    cancelStream()
    stopCompressPolling()
  }

  /**
   * 压缩会话上下文
   */
  async function compressSession(agentId: number, sessionId: number): Promise<boolean> {
    isCompressing.value = true
    try {
      const res = await agentApi.compress(agentId, sessionId)
      if (res.data.code === 1) {
        startCompressPolling(agentId, sessionId)
        return true
      }
      isCompressing.value = false
      return false
    } catch {
      isCompressing.value = false
      return false
    }
  }

  /**
   * 轮询检查会话是否正在压缩上下文
   */
  function startCompressPolling(agentId: number, sessionId: number) {
    stopCompressPolling()
    const check = async () => {
      try {
        const res = await agentApi.compressStatus(agentId, sessionId)
        if (res.data.code === 1 && res.data.data?.compressing) {
          isCompressing.value = true
        } else if (isCompressing.value) {
          isCompressing.value = false
          stopCompressPolling()
          if (currentAgent.value && currentSession.value) {
            await selectSession(currentAgent.value.id, currentSession.value)
          }
        } else {
          stopCompressPolling()
        }
      } catch {
        stopCompressPolling()
      }
    }
    check()
    compressPollTimer = setInterval(check, 1000)
  }

  function stopCompressPolling() {
    if (compressPollTimer) {
      clearInterval(compressPollTimer)
      compressPollTimer = null
    }
  }

  return {
    // 数据
    agents,
    currentAgent,
    sessions,
    currentSession,
    messages,
    chatMessages,
    totalSessionTokens,
    latestPromptTokens,
    // 分页
    sessionPage,
    sessionPageSize,
    sessionTotal,
    // 状态
    loading,
    sessionsLoading,
    messagesLoading,
    isStreaming,
    thinkingContent,
    textContent,
    todos,
    isWaitingHuman,
    currentWaitData,
    isWaitingToolApproval,
    pendingToolCalls,
    pendingApprovalNeeded,
    approvalCountdown,
    subAgentApproval,
    isCompressing,
    isStopping,
    // 消息分页
    hasMoreMessages,
    loadingMoreMessages,
    // 方法
    loadAgents,
    loadAgent,
    loadSessions,
    createSession,
    deleteSession,
    selectSession,
    loadMoreMessages,
    sendMessage,
    resumeWithInput,
    approveToolCalls,
    rejectToolCalls,
    cancelStream,
    interruptExecution,
    resetState,
    deleteMessagesFrom,
    compressSession,
    startCompressPolling,
    stopCompressPolling,
    stopSavePolling
  }
})
