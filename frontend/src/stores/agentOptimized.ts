/**
 * Agent会话状态管理（优化版）
 * @description 使用composables重构的Agent Store，提供更好的代码复用
 */
import { defineStore } from 'pinia'
import { ref, computed, nextTick } from 'vue'
import type {
  AgentFlow,
  AgentSession,
  AgentMessage,
  AgentDeleteMessagesResult
} from '@/types/agent'
import type { FlowSSEHandlers, SSEWaitData, SSEEvent, SSEEventHandler } from '@/types/sse'
import type {
  StreamingMessage,
  ToolCall,
  MessageFile,
  MessageSegment,
  TodoItem
} from '@/composables/useStreamingMessage'
import { agentApi } from '@/api/agent'
import { createOnToolCallLimitHandler, createOnLlmRetryHandler } from '@/composables/useSSEHandlers'
import { useStreamingMessage } from '@/composables'
import { ElMessage } from 'element-plus'

const MESSAGE_REFRESH_LIMIT = 100
const CONTEXT_SUMMARY_MESSAGE_TYPE = 'context_summary'
/** view_media 注入的多模态内部消息（后端标记，前端不渲染为用户气泡） */
const MEDIA_INJECTED_MESSAGE_TYPE = 'media_injected'

interface AgentStreamContext {
  agentId: number
  sessionId: number
  generation: number
  wasFirstMessage: boolean
}

export const useAgentStore = defineStore('agent', () => {
  // ========== 基础数据 ==========
  const agents = ref<AgentFlow[]>([])
  const currentAgent = ref<AgentFlow | null>(null)
  // 上次使用的 Agent ID（内存态，刷新页面后重置，resetState 不清除以支持标签页切换记忆）
  const lastUsedAgentId = ref<number | null>(null)
  const sessions = ref<AgentSession[]>([])
  const currentSession = ref<AgentSession | null>(null)
  const messages = ref<AgentMessage[]>([])
  // SSE 结束后从接口回载消息完成的版本号，供聊天页面在渲染后同步滚动。
  const messageRefreshVersion = ref(0)
  // 流式开始时本地已提交的最大 DB 消息 id：SSE 结束合并时用于识别「本轮新增」DB 行，
  // 与尾部流式占位行（user-/streaming-）按 role 匹配后仅回填元数据，避免占位气泡重挂
  let streamBaseMsgId = 0

  function markStreamBaseMsgId(): void {
    streamBaseMsgId = 0
    for (const m of messages.value) {
      if (m.id > streamBaseMsgId) streamBaseMsgId = m.id
    }
  }

  /**
   * 清理上一轮中断残留的占位行（手动停止后重新发送等场景）
   *
   * 非流式状态下仍无 dbMsgId 的占位行只可能是中断残留：正常结束会被 onFlowDone
   * 合并回填，等待人工输入的占位属于 resume 语义不在此清理。残留占位不清会导致
   * 重发后列表保留半截内容，且与新轮占位一起参与 role 匹配造成错配/重复；残留
   * 轮次已落库的内容由后续合并以 DB 行形态自然补回，不丢数据。
   * 活跃流式（isStreaming 且非停止中）时不清理，防止误删正在流式的合法占位。
   */
  function clearOrphanPlaceholders(): void {
    if (isStreaming.value && !isStopping.value) return
    const filtered = chatMessages.value.filter(
      m =>
        !(
          m.dbMsgId == null &&
          (m.id.startsWith('user-') || m.id.startsWith('streaming-'))
        )
    )
    if (filtered.length !== chatMessages.value.length) {
      chatMessages.value = filtered
    }
  }

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
    updateToolLiveOutput,
    failRunningToolSegments,
    addTodoSegment,
    addKnowledgeCitations,
    updateTodos,
    addTokenUsage,
    flushPending,
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
  let compressPollTimer: ReturnType<typeof setTimeout> | null = null
  let compressPollVersion = 0

  // ========== 中断保存状态 ==========
  const isStopping = ref(false)
  let savePollTimer: ReturnType<typeof setTimeout> | null = null
  let savePollVersion = 0

  // ========== 后台运行检测（刷新后检测会话是否仍在跑） ==========
  let runningPollTimer: ReturnType<typeof setTimeout> | null = null
  let runningPollVersion = 0

  // ========== 计划模式（只读探索，不执行修改），localStorage 持久化 ==========
  const planMode = ref(localStorage.getItem('agent_plan_mode') === '1')
  function togglePlanMode(): void {
    planMode.value = !planMode.value
    localStorage.setItem('agent_plan_mode', planMode.value ? '1' : '0')
  }

  // ========== 流程预览（AI 创建/修改流程时推送，独立于消息分段） ==========
  const flowPreview = ref<{
    flow_id: number
    flow_name?: string
    action?: string
    nodes?: Record<string, unknown>[]
    edges?: Record<string, unknown>[]
    deleted?: boolean
  } | null>(null)

  // ========== 中断函数引用 ==========
  let streamAbort: ((cancelRun?: boolean) => void | Promise<void>) | null = null
  let streamGeneration = 0
  let sessionSelectionVersion = 0
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
   * @param workDir 可选，会话级项目工作路径
   */
  async function createSession(agentId: number, workDir?: string): Promise<AgentSession | null> {
    try {
      const res = await agentApi.createSession(agentId, workDir)
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
        streamBaseMsgId = 0
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
    const selectionVersion = ++sessionSelectionVersion
    messagesLoading.value = true
    clearMessages()
    streamBaseMsgId = 0
    cancelStream()
    stopCompressPolling()
    isCompressing.value = false
    const selectionGeneration = streamGeneration
    flowPreview.value = null

    currentSession.value = session
    try {
      const res = await agentApi.getMessages(agentId, session.id)
      if (
        res.data.code === 1 &&
        selectionVersion === sessionSelectionVersion &&
        selectionGeneration === streamGeneration &&
        currentAgent.value?.id === agentId &&
        currentSession.value?.id === session.id
      ) {
        messages.value = res.data.data?.list || []
        messageTotal.value = res.data.data?.total || 0
        rebuildChatMessages()
      }
    } finally {
      if (selectionVersion === sessionSelectionVersion) messagesLoading.value = false
    }

    // 检查会话是否正在压缩（页面刷新/重选场景）
    try {
      const statusRes = await agentApi.compressStatus(agentId, session.id)
      if (
        selectionVersion === sessionSelectionVersion &&
        selectionGeneration === streamGeneration &&
        statusRes.data.code === 1 &&
        statusRes.data.data?.status === 'compressing'
      ) {
        startCompressPolling(agentId, session.id)
      }
    } catch {
      // 状态检查失败不影响正常使用
    }

    // 检测会话是否正在后台执行（刷新/重选场景），点亮停止按钮
    try {
      const runRes = await agentApi.runningStatus(agentId, session.id)
      if (
        selectionVersion === sessionSelectionVersion &&
        selectionGeneration === streamGeneration &&
        runRes.data.code === 1 &&
        runRes.data.data?.running
      ) {
        // 新事件写入独立气泡；完成后仍以数据库消息为准重建。
        startStreaming(true)
        const context: AgentStreamContext = {
          agentId,
          sessionId: session.id,
          generation: selectionGeneration,
          wasFirstMessage: false
        }
        const runId = runRes.data.data?.run_id
        if (runId && runRes.data.data?.managed_running) {
          const lastEventId = runRes.data.data?.last_event_id || 0
          streamAbort = agentApi.subscribeRun(
            agentId,
            session.id,
            runId,
            Math.max(0, lastEventId - 1),
            createReattachHandlers(context, lastEventId)
          )
        } else {
          startRunningPolling(agentId, session.id)
        }
      } else if (
        selectionVersion === sessionSelectionVersion &&
        selectionGeneration === streamGeneration &&
        runRes.data.code === 1 &&
        runRes.data.data?.waiting_human
      ) {
        const context: AgentStreamContext = {
          agentId,
          sessionId: session.id,
          generation: selectionGeneration,
          wasFirstMessage: false
        }
        const handlers = createStreamHandlers(context)
        if (runRes.data.data.waiting_event) {
          handlers.onWaitingHuman?.(runRes.data.data.waiting_event)
        } else if (runRes.data.data.run_id) {
          streamAbort = agentApi.subscribeRun(
            agentId,
            session.id,
            runRes.data.data.run_id,
            Math.max(0, runRes.data.data.last_event_id - 1),
            handlers
          )
        }
      }
    } catch {
      // 检测失败不影响正常使用
    }
  }

  /**
   * 加载更多历史消息（向上翻页）
   */
  async function loadMoreMessages(agentId: number) {
    if (
      !currentSession.value ||
      loadingMoreMessages.value ||
      !hasMoreMessages.value ||
      isStreaming.value
    )
      return 0
    const sessionId = currentSession.value.id
    const selectionVersion = sessionSelectionVersion
    const generation = streamGeneration
    const firstMsgId = messages.value[0]?.id
    if (!firstMsgId) return 0

    loadingMoreMessages.value = true
    try {
      const res = await agentApi.getMessages(agentId, sessionId, firstMsgId)
      if (
        res.data.code === 1 &&
        currentAgent.value?.id === agentId &&
        currentSession.value?.id === sessionId &&
        selectionVersion === sessionSelectionVersion &&
        generation === streamGeneration
      ) {
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
      const role = msg.role

      if (msg.message_type === CONTEXT_SUMMARY_MESSAGE_TYPE) {
        if (currentAssistant) {
          result.push(currentAssistant)
          currentAssistant = null
        }
        const removedCount = Number(msg.input_data?.removed_count || 0)
        result.push({
          id: `msg-${msg.id}`,
          dbMsgId: msg.id,
          role: 'ai',
          displayType: 'context-summary',
          removedCount: Number.isFinite(removedCount) ? removedCount : 0,
          content: msg.content,
          segments: [],
          prompt_tokens: msg.prompt_tokens,
          completion_tokens: msg.completion_tokens,
          total_tokens: msg.total_tokens,
          createdAt: new Date(msg.created_at || Date.now())
        })
      } else if (role === 'human') {
        // view_media 注入的内部消息：不渲染为用户气泡（LLM 历史中仍保留标注文本）
        if (msg.message_type === MEDIA_INJECTED_MESSAGE_TYPE) {
          continue
        }
        if (currentAssistant) {
          result.push(currentAssistant)
          currentAssistant = null
        }
        result.push({
          id: `msg-${msg.id}`,
          dbMsgId: msg.id,
          role: 'human',
          content: msg.original_content || msg.content,
          segments: [{ type: 'content', content: msg.original_content || msg.content }],
          files: msg.files as MessageFile[] | undefined,
          createdAt: new Date(msg.created_at || Date.now())
        })
      } else if (role === 'ai') {
        if (!currentAssistant) {
          currentAssistant = {
            id: `msg-${msg.id}`,
            dbMsgId: msg.id,
            role: 'ai',
            content: '',
            segments: [],
            createdAt: new Date(msg.created_at || Date.now())
          }
        }

        if (msg.thinking) {
          currentAssistant.segments.push({
            type: 'thinking',
            thinking: msg.thinking,
            dbMsgId: msg.id,
            // 确定性段 id（按 DB 行派生）：供虚拟行 key 使用，前插历史被吸收合并时
            // 旧段 key 不随下标漂移
            id: `s-${msg.id}-t`
          })
          currentAssistant.thinking = msg.thinking
        }

        if (msg.content || msg.knowledge_citations?.length) {
          currentAssistant.segments.push({
            type: 'content',
            content: msg.content,
            dbMsgId: msg.id,
            knowledge_citations: msg.knowledge_citations,
            id: `s-${msg.id}-c`
          })
          currentAssistant.content = msg.content
        }

        if (msg.tool_calls && Array.isArray(msg.tool_calls) && msg.tool_calls.length > 0) {
          if (!currentAssistant.tools) {
            currentAssistant.tools = []
          }
          for (const [tcIdx, tc] of msg.tool_calls.entries()) {
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
            currentAssistant.segments.push({
              type: 'tool',
              tool,
              id: `s-${msg.id}-tc${tcIdx}`
            })

            const toolName = tc.name as string
            if (toolName === 'todowrite' && toolResult?.status === 'success') {
              try {
                // args.todos 当前为数组（TodoWriteInput: list[TodoItem]）；
                // 兼容旧数据中的 JSON 字符串形态
                const rawTodos = (tc.args as { todos?: unknown })?.todos
                const todosArgs =
                  typeof rawTodos === 'string' ? JSON.parse(rawTodos || '[]') : rawTodos
                if (Array.isArray(todosArgs)) {
                  currentAssistant.segments.push({
                    type: 'todo',
                    id: `s-${msg.id}-todo${tcIdx}`,
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
                  id: `s-${msg.id}-todo${tcIdx}`,
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

        // 结束节点输出（该轮 AI 消息携带，前端按钮查看）
        if (msg.end_output) {
          currentAssistant.end_output = msg.end_output
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
   * 浅比较两个分段是否等价（避免引用比较导致的无谓重渲）
   */
  function isSameSegment(a: MessageSegment, b: MessageSegment): boolean {
    if (a.type !== b.type) return false
    if (a.type === 'content' && b.type === 'content') {
      return (
        a.content === b.content &&
        a.dbMsgId === b.dbMsgId &&
        JSON.stringify(a.knowledge_citations) === JSON.stringify(b.knowledge_citations)
      )
    }
    if (a.type === 'thinking' && b.type === 'thinking') {
      return a.thinking === b.thinking && a.dbMsgId === b.dbMsgId
    }
    if (a.type === 'tool' && b.type === 'tool') {
      const ta = a.tool
      const tb = b.tool
      if (ta === tb) return true
      if (!ta || !tb) return false
      return (
        ta.id === tb.id &&
        ta.name === tb.name &&
        ta.status === tb.status &&
        ta.args === tb.args &&
        ta.result === tb.result
      )
    }
    if (a.type === 'todo' && b.type === 'todo') {
      return JSON.stringify(a.todo) === JSON.stringify(b.todo)
    }
    return true
  }

  /**
   * 比较两条聊天消息是否等价：等价则跳过 Object.assign，
   * 保留旧 segments 引用使 Vue 跳过该消息的重渲染（避免回合结束全量 markdown 重渲尖峰）
   */
  function logicalMessageId(m: StreamingMessage): string {
    return m.dbMsgId != null ? `msg-${m.dbMsgId}` : m.id
  }

  function isSameMessage(a: StreamingMessage, b: StreamingMessage): boolean {
    if (logicalMessageId(a) !== logicalMessageId(b)) return false
    if (
      a.role !== b.role ||
      a.displayType !== b.displayType ||
      a.removedCount !== b.removedCount ||
      a.content !== b.content ||
      a.thinking !== b.thinking ||
      a.prompt_tokens !== b.prompt_tokens ||
      a.completion_tokens !== b.completion_tokens ||
      a.total_tokens !== b.total_tokens
    ) {
      return false
    }
    if (a.createdAt.getTime() !== b.createdAt.getTime()) return false
    if (JSON.stringify(a.files) !== JSON.stringify(b.files)) return false
    if (a.segments.length !== b.segments.length) return false
    for (let i = 0; i < a.segments.length; i++) {
      if (!isSameSegment(a.segments[i], b.segments[i])) return false
    }
    return true
  }

  /**
   * 从历史消息合并更新聊天消息列表（按 dbMsgId 对齐，非按位置）
   * 用于 selectSession、onFlowDone、loadMoreMessages 等场景
   *
   * 刷新拉取范围（最新一页）可能大于本地已加载范围（首屏/向上翻页），按下标逐位
   * 对齐会整体错位导致气泡全量 REPLACE 重挂；此处以 dbMsgId 为键复用本地行对象，
   * 虚拟滚动的行 key（m-<msg.id>/row-<segment.id>）稳定，DOM 与实测高度缓存均复用。
   *
   * 流式占位行（user-/streaming- 前缀）不按 DB 重建：DB 视图可能缺 thinking/tool 段
   * （重建会导致内容回退 + 段 key 变化重挂），仅按 role 与本轮新增 DB 行
   * （id > streamBaseMsgId）匹配后回填 dbMsgId/token 元数据。
   */
  function rebuildChatMessages(preserveStreaming = false) {
    const rebuilt = buildChatMessagesFromDB(messages.value)
    const local = chatMessages.value

    // ---- 占位行识别：本地尾部连续的流式临时行 ----
    const isPlaceholder = (m: StreamingMessage | undefined): boolean =>
      !!m && m.dbMsgId == null && (m.id.startsWith('user-') || m.id.startsWith('streaming-'))
    let placeholderStart = local.length
    while (placeholderStart > 0 && isPlaceholder(local[placeholderStart - 1])) placeholderStart--
    const placeholders = local.slice(placeholderStart)

    // ---- 占位行与本轮新增 DB 渲染行尾部反向对齐 ----
    // 占位行是会话最新消息，对应 DB 行也是最新的：从尾部向前按 role 匹配。
    // 顺序单调消费在中间缺行（如中断轮次的 ai 为空 chunk 未落库）时会让后续
    // 占位级联失配；反向对齐只影响末尾对应关系，缺行位置的行保持独立成行。
    // streamBaseMsgId=0（首轮/空会话）时 dbMsgId > 0 恒真，全部 rebuilt 行参与匹配
    const matchedFresh = new Set<StreamingMessage>()
    if (placeholders.length > 0) {
      const freshRows = rebuilt.filter(r => r.dbMsgId != null && r.dbMsgId > streamBaseMsgId)
      let freshIdx = freshRows.length - 1
      for (let pi = placeholders.length - 1; pi >= 0 && freshIdx >= 0; pi--) {
        const ph = placeholders[pi]
        while (freshIdx >= 0) {
          const candidate = freshRows[freshIdx]
          freshIdx--
          if (candidate.role !== ph.role) continue
          // 仅回填元数据：保留原 id 与 segments（行 key 不变 → 虚拟行复用、内容不回退）
          ph.dbMsgId = candidate.dbMsgId
          ph.prompt_tokens = candidate.prompt_tokens
          ph.completion_tokens = candidate.completion_tokens
          ph.total_tokens = candidate.total_tokens
          if (candidate.latest_prompt_tokens) ph.latest_prompt_tokens = candidate.latest_prompt_tokens
          matchedFresh.add(candidate)
          break
        }
      }
    }

    // ---- 已提交行按 dbMsgId 对齐：命中复用本地对象（保 key），未命中新建 ----
    const localById = new Map<number, StreamingMessage>()
    for (let i = 0; i < placeholderStart; i++) {
      const m = local[i]
      if (m.dbMsgId != null) localById.set(m.dbMsgId, m)
    }
    const result: StreamingMessage[] = []
    for (const r of rebuilt) {
      if (matchedFresh.has(r)) continue
      const existing = r.dbMsgId != null ? localById.get(r.dbMsgId) : undefined
      if (existing) {
        if (!isSameMessage(existing, r)) Object.assign(existing, r)
        result.push(existing)
      } else {
        result.push(r)
      }
    }

    // ---- 窗口外的本地旧行：以 messages（权威列表，已处理保留/压缩裁剪）为准保留在头部 ----
    // 覆盖集合除渲染行自身的 dbMsgId 外，还纳入各段 dbMsgId：连续 ai 行会跨越 tool
    // 结果行合并为一条渲染消息（dbMsgId 取组首），向上翻页前插更早的 ai 行会改变
    // 分组、把旧行的 dbMsgId 吸收进新组首（如 30091 并入 30089）。若仅按渲染行
    // dbMsgId 判定，被吸收的旧行会被误判为窗口外而 unshift 到头部，造成重复+错位
    const rebuiltIds = new Set<number>()
    for (const r of rebuilt) {
      if (r.dbMsgId != null) rebuiltIds.add(r.dbMsgId)
      for (const seg of r.segments) {
        if (seg.dbMsgId != null) rebuiltIds.add(seg.dbMsgId)
      }
    }
    const keptDbIds = new Set(messages.value.map(m => m.id))
    const olderKept: StreamingMessage[] = []
    for (let i = 0; i < placeholderStart; i++) {
      const m = local[i]
      if (m.dbMsgId == null || rebuiltIds.has(m.dbMsgId) || !keptDbIds.has(m.dbMsgId)) continue
      olderKept.push(m)
    }
    if (olderKept.length > 0) {
      result.unshift(...olderKept)
    }

    // 未匹配占位行（DB 尚无对应行）保持尾部
    result.push(...placeholders)
    chatMessages.value = result

    thinkingContent.value = ''
    textContent.value = ''
    currentSegmentType.value = null
    if (!preserveStreaming) isStreaming.value = false

    for (let i = messages.value.length - 1; i >= 0; i--) {
      const m = messages.value[i]
      if (m.role === 'ai' || m.message_type === CONTEXT_SUMMARY_MESSAGE_TYPE) {
        latestPromptTokens.value = m.latest_prompt_tokens || m.prompt_tokens || 0
        break
      }
    }
  }

  function isCurrentStream(context: AgentStreamContext): boolean {
    return (
      context.generation === streamGeneration &&
      currentAgent.value?.id === context.agentId &&
      currentSession.value?.id === context.sessionId
    )
  }

  function applyLatestMessages(
    latestMessages: AgentMessage[],
    total: number,
    replace = false,
    preserveStreaming = false
  ): void {
    if (replace) {
      // 以 DB 最新一页为权威重置原始行；窗口外的更早历史保留（向上翻页成果不丢）。
      // 压缩场景（窗口内出现 context_summary）：其之前的旧行已软删，需一并裁剪
      const windowMinId = latestMessages[0]?.id ?? Number.MAX_SAFE_INTEGER
      const summaryMsg = latestMessages.find(m => m.message_type === CONTEXT_SUMMARY_MESSAGE_TYPE)
      const olderKept = messages.value.filter(
        m => m.id < windowMinId && (!summaryMsg || m.id > summaryMsg.id)
      )
      messages.value = olderKept.length > 0 ? [...olderKept, ...latestMessages] : latestMessages
    } else if (latestMessages.length > 0) {
      const oldestLatestId = latestMessages[0].id
      const loadedOlderMessages = messages.value.filter(message => message.id < oldestLatestId)
      messages.value = [...loadedOlderMessages, ...latestMessages]
      if (messages.value.length > total) {
        messages.value = messages.value.slice(messages.value.length - total)
      }
    } else if (total === 0) {
      messages.value = []
    }
    messageTotal.value = total
    rebuildChatMessages(preserveStreaming)
  }

  async function refreshStreamMessages(
    context: AgentStreamContext,
    replace = false,
    preserveStreaming = false,
    incrementalOnly = false
  ): Promise<void> {
    // 增量刷新：只拉本地已知最大 DB id 之后的新增消息。结束刷新若全量拉取，
    // 会把本地未上翻加载的更早历史插入列表头部（applyLatestMessages 的 replace
    // 重置），头部骤增导致像素锚定失效、用户上滚查看的视口"跑到上面"。
    // 压缩刷新（incrementalOnly=false）保持全量：裁剪已软删旧行依赖全量对账
    const afterId = incrementalOnly ? (messages.value.at(-1)?.id ?? undefined) : undefined
    const res = await agentApi.getMessages(
      context.agentId,
      context.sessionId,
      undefined,
      MESSAGE_REFRESH_LIMIT,
      afterId
    )
    if (res.data.code !== 1 || !isCurrentStream(context)) return
    applyLatestMessages(
      res.data.data?.list || [],
      res.data.data?.total || 0,
      replace,
      preserveStreaming
    )
    messageRefreshVersion.value++
  }

  /**
   * 创建SSE事件处理器
   */
  function createStreamHandlers(context: AgentStreamContext) {
    // SSE 解析器不会等待异步 handler，刷新压缩历史期间需串行化后续事件，避免新内容被覆盖。
    let compressionRefreshBarrier: Promise<void> | null = null

    const trackCompressionRefresh = (task: Promise<void>) => {
      const barrier = task.catch(error => {
        console.error('[context_compressing] 刷新消息失败', error)
      })
      compressionRefreshBarrier = barrier
      void barrier.finally(() => {
        if (compressionRefreshBarrier === barrier) compressionRefreshBarrier = null
      })
    }

    const runAfterCompressionRefresh = (action: () => void | Promise<void>) => {
      const previous = compressionRefreshBarrier
      if (!previous) {
        try {
          const result = action()
          if (result) {
            void result.catch(error => {
              console.error('[Agent SSE] 处理事件失败', error)
            })
          }
        } catch (error) {
          console.error('[Agent SSE] 处理事件失败', error)
        }
        return
      }

      const queued = previous
        .then(async () => {
          if (!isCurrentStream(context)) return
          await action()
        })
        .catch(error => {
          console.error('[Agent SSE] 处理压缩后事件失败', error)
        })
      compressionRefreshBarrier = queued
      void queued.finally(() => {
        if (compressionRefreshBarrier === queued) compressionRefreshBarrier = null
      })
    }

    const deferDuringCompressionRefresh = (
      handler: SSEEventHandler | undefined
    ): SSEEventHandler | undefined => {
      if (!handler) return undefined
      return event => runAfterCompressionRefresh(() => handler(event))
    }

    const applyWaitingHuman = (event: SSEEvent) => {
      if (!isCurrentStream(context)) return
      stopStreaming()
      isWaitingHuman.value = true
      currentWaitData.value = event.data.wait_data || {
        type: 'human',
        node_key: event.data.node_key || '',
        question: event.data.question || '请提供输入',
        context: event.data.context
      }
    }

    const handlers: FlowSSEHandlers = {
      onFlowStart: () => {
        if (!isCurrentStream(context)) return
        flowPreview.value = null
        startStreaming()
      },
      onNodeStart: (_event: SSEEvent) => {
        // 节点开始处理
      },
      onNodeThinking: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        appendThinking(event.data.content || '')
      },
      onNodeContent: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        appendContent(event.data.content || '')
      },
      onNodeDone: () => {
        // 节点完成处理
      },
      onToolCallStart: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        addToolSegment(
          event.data.tool_name || '',
          event.data.tool_args || {},
          'running',
          event.data.tool_call_id
        )
      },
      onToolCallEnd: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        updateToolSegment(
          event.data.tool_name || '',
          event.data.status === 'error' ? 'error' : 'success',
          event.data.result,
          event.data.tool_call_id
        )
      },
      onToolCallLimit: createOnToolCallLimitHandler(),
      onTokenUsage: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        addTokenUsage(
          event.data.prompt_tokens || 0,
          event.data.completion_tokens || 0,
          event.data.total_tokens || 0
        )
      },
      onTodoUpdate: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        const newTodos = (event.data.todos || []) as TodoItem[]
        updateTodos(newTodos)
        addTodoSegment(newTodos)
      },
      onKnowledgeCitations: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        addKnowledgeCitations(event.data.citations || [])
      },
      onWaitingHuman: applyWaitingHuman,
      onToolApproval: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
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
      onSubAgentProgress: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        const content = event.data.content || ''
        if (!content || !event.data.node_key) return
        // 写入对应 call_sub_agent_* 工具分段的实时输出区（快照替换，重连回放幂等）
        updateToolLiveOutput(
          `call_sub_agent_${event.data.node_key}`,
          content,
          event.data.sub_agent_name || '子Agent'
        )
      },
      onContextCompressing: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        const status = event.data.status as string
        if (status === 'compressing') {
          isCompressing.value = true
        } else {
          isCompressing.value = false
          if (status === 'done') {
            // 压缩事务已提交；清空 chunk 缓冲后以 DB 为准替换已软删除的旧历史。
            flushPending()
            trackCompressionRefresh(refreshStreamMessages(context, true, true))
          }
        }
      },
      onFlowPreview: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        flowPreview.value = {
          flow_id: event.data.flow_id || 0,
          flow_name: event.data.flow_name,
          action: event.data.action,
          nodes: event.data.nodes as Record<string, unknown>[] | undefined,
          edges: event.data.edges as Record<string, unknown>[] | undefined,
          deleted: event.data.action === 'delete'
        }
      },
      onFlowDone: async () => {
        if (!isCurrentStream(context)) return
        stopStreaming()
        isCompressing.value = false
        if (isWaitingToolApproval.value) {
          isWaitingToolApproval.value = false
          pendingToolCalls.value = []
          pendingApprovalNeeded.value = []
          stopApprovalCountdown()
          ElMessage.warning({ message: '工具确认超时，连接已断开', duration: 5000 })
        }
        if (isResume) {
          isResume = false
        }
        try {
          // replace=true 以 DB 最新一页重置原始行与分页状态；preserveStreaming=true
          // 走 rebuildChatMessages 就地 diff：占位气泡保留原 id 过继 dbMsgId，DOM 不重挂；
          // incrementalOnly=true 增量拉取本轮新增，避免全量重置把未加载的更早历史
          // 插入头部顶跑用户上滚查看的视口
          await refreshStreamMessages(context, true, true, true)
        } catch (e) {
          console.error('[onFlowDone] 刷新消息失败', e)
        }
        if (context.wasFirstMessage && isCurrentStream(context)) {
          await loadSessions(context.agentId, sessionPage.value)
        }
      },
      onLlmRetry: createOnLlmRetryHandler(),
      onError: async (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        stopStreaming()
        isCompressing.value = false
        if (isWaitingToolApproval.value) {
          isWaitingToolApproval.value = false
          pendingToolCalls.value = []
          pendingApprovalNeeded.value = []
          stopApprovalCountdown()
        }
        try {
          await refreshStreamMessages(context, false, false, true)
        } catch (e) {
          console.error('[onError] 刷新消息失败', e)
        }
        try {
          const status = await agentApi.runningStatus(context.agentId, context.sessionId)
          if (
            status.data.code === 1 &&
            status.data.data?.waiting_human &&
            isCurrentStream(context)
          ) {
            applyWaitingHuman(
              status.data.data.waiting_event || {
                type: 'waiting_human',
                data: {
                  wait_data: {
                    type: 'human',
                    node_key: '',
                    question: '请重新提供输入'
                  }
                }
              }
            )
          }
        } catch {
          // 错误提示仍使用原始执行错误。
        }
        ElMessage.error({ message: event.data.message || '发送失败', duration: 5000 })
      }
    }

    return {
      ...handlers,
      onNodeThinking: deferDuringCompressionRefresh(handlers.onNodeThinking),
      onNodeContent: deferDuringCompressionRefresh(handlers.onNodeContent),
      onToolCallStart: deferDuringCompressionRefresh(handlers.onToolCallStart),
      onToolCallEnd: deferDuringCompressionRefresh(handlers.onToolCallEnd),
      onToolCallLimit: deferDuringCompressionRefresh(handlers.onToolCallLimit),
      onTokenUsage: deferDuringCompressionRefresh(handlers.onTokenUsage),
      onWaitingHuman: deferDuringCompressionRefresh(handlers.onWaitingHuman),
      onToolApproval: deferDuringCompressionRefresh(handlers.onToolApproval),
      onSubAgentProgress: deferDuringCompressionRefresh(handlers.onSubAgentProgress),
      onTodoUpdate: deferDuringCompressionRefresh(handlers.onTodoUpdate),
      onFlowDone: deferDuringCompressionRefresh(handlers.onFlowDone),
      onLlmRetry: deferDuringCompressionRefresh(handlers.onLlmRetry),
      onContextCompressing: deferDuringCompressionRefresh(handlers.onContextCompressing),
      onFlowPreview: deferDuringCompressionRefresh(handlers.onFlowPreview),
      onKnowledgeCitations: deferDuringCompressionRefresh(handlers.onKnowledgeCitations),
      onError: deferDuringCompressionRefresh(handlers.onError)
    }
  }

  function createReattachHandlers(
    context: AgentStreamContext,
    snapshotEventId: number
  ): FlowSSEHandlers {
    const handlers = createStreamHandlers(context)
    const afterSnapshot = (handler: ((event: SSEEvent) => void) | undefined) => {
      return (event: SSEEvent) => {
        const eventId = Number(event.id)
        if (!Number.isInteger(eventId) || eventId > snapshotEventId) {
          handler?.(event)
        }
      }
    }
    return {
      ...handlers,
      onNodeThinking: afterSnapshot(handlers.onNodeThinking),
      onNodeContent: afterSnapshot(handlers.onNodeContent),
      onToolCallStart: afterSnapshot(handlers.onToolCallStart),
      onToolCallEnd: afterSnapshot(handlers.onToolCallEnd),
      onToolCallLimit: afterSnapshot(handlers.onToolCallLimit),
      onSubAgentProgress: afterSnapshot(handlers.onSubAgentProgress),
      onTokenUsage: afterSnapshot(handlers.onTokenUsage),
      onTodoUpdate: afterSnapshot(handlers.onTodoUpdate),
      onKnowledgeCitations: afterSnapshot(handlers.onKnowledgeCitations),
      onLlmRetry: afterSnapshot(handlers.onLlmRetry)
    }
  }

  // ========== 发送消息 ==========

  /**
   * 发送消息
   */
  function sendMessage(
    content: string,
    params: Record<string, unknown> = {},
    files?: MessageFile[],
    model?: string
  ) {
    if (!currentAgent.value || !currentSession.value) return

    const context: AgentStreamContext = {
      agentId: currentAgent.value.id,
      sessionId: currentSession.value.id,
      generation: ++streamGeneration,
      wasFirstMessage: messages.value.length === 0
    }

    markStreamBaseMsgId()
    clearOrphanPlaceholders()
    addUserMessage(content, files)
    startStreaming()

    streamAbort = agentApi.chat(
      context.agentId,
      context.sessionId,
      { content, params: { ...params, __plan_mode__: planMode.value }, model },
      createStreamHandlers(context)
    )
  }

  /**
   * 恢复会话（人工输入后继续）
   */
  function resumeWithInput(humanInput: string) {
    if (!currentAgent.value || !currentSession.value) return

    const context: AgentStreamContext = {
      agentId: currentAgent.value.id,
      sessionId: currentSession.value.id,
      generation: ++streamGeneration,
      wasFirstMessage: false
    }
    isResume = true
    markStreamBaseMsgId()
    addUserMessage(humanInput)
    isWaitingHuman.value = false
    currentWaitData.value = null
    startStreaming()

    streamAbort = agentApi.resume(
      context.agentId,
      context.sessionId,
      { human_input: humanInput },
      createStreamHandlers(context)
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
   * 取消流式输出；中断场景会在启动请求完成后取消后台执行
   * @param waitForSave 是否等待后端 save_to_db 完成后刷新消息（仅中断场景传 true）
   */
  function cancelStream(waitForSave = false) {
    streamGeneration++
    const cancelGeneration = streamGeneration
    const agentId = currentAgent.value?.id
    const sessionId = currentSession.value?.id
    let cancelResult: void | Promise<void> = undefined
    if (streamAbort) {
      cancelResult = streamAbort(waitForSave)
      streamAbort = null
    } else if (waitForSave && agentId && sessionId) {
      cancelResult = agentApi.cancel(agentId, sessionId).then(() => undefined)
    }
    // 取消后 tool_call_end SSE 不会再来，终止仍在转圈的 running 工具分段，
    // 文案与后端补写的 ToolMessage 保持一致
    failRunningToolSegments('执行被中断')
    if (!waitForSave) {
      stopStreaming()
      isStopping.value = false
    }
    isWaitingHuman.value = false
    currentWaitData.value = null
    isWaitingToolApproval.value = false
    pendingToolCalls.value = []
    pendingApprovalNeeded.value = []
    stopApprovalCountdown()
    stopSavePolling()
    stopRunningPolling()
    if (waitForSave && agentId && sessionId) {
      void Promise.resolve(cancelResult)
        .then(() => {
          if (
            cancelGeneration === streamGeneration &&
            currentAgent.value?.id === agentId &&
            currentSession.value?.id === sessionId
          ) {
            startSavePolling(agentId, sessionId)
          }
        })
        .catch(() => {
          if (
            cancelGeneration === streamGeneration &&
            currentAgent.value?.id === agentId &&
            currentSession.value?.id === sessionId
          ) {
            isStopping.value = false
            startRunningPolling(agentId, sessionId)
            ElMessage.error({ message: '停止请求失败，请稍后重试', duration: 5000 })
          }
        })
    }
  }

  /**
   * 轮询等待后端中断后的消息保存完成，然后刷新消息
   */
  function startSavePolling(agentId: number, sessionId: number) {
    stopSavePolling()
    const pollVersion = savePollVersion
    const expectedGeneration = streamGeneration
    const startTime = Date.now()
    const timeout = 8000
    const isCurrentPoll = () =>
      pollVersion === savePollVersion &&
      expectedGeneration === streamGeneration &&
      currentAgent.value?.id === agentId &&
      currentSession.value?.id === sessionId
    const onDone = () => {
      if (!isCurrentPoll()) return
      stopStreaming()
      isStopping.value = false
      ElMessage.success({ message: '停止成功', duration: 5000 })
      // 停止路径不走 onFlowDone 的会话列表刷新，此处同步侧边栏标题等状态
      void loadSessions(agentId, sessionPage.value)
    }
    const poll = async () => {
      if (!isCurrentPoll()) return
      try {
        const res = await agentApi.saveStatus(agentId, sessionId)
        if (!isCurrentPoll()) return
        if (res.data.code === 1 && res.data.data?.saving) {
          if (Date.now() - startTime >= timeout) {
            refreshMessages(agentId, sessionId, expectedGeneration)
            onDone()
            stopSavePolling()
          } else {
            savePollTimer = setTimeout(poll, 1000)
          }
        } else {
          refreshMessages(agentId, sessionId, expectedGeneration)
          onDone()
          stopSavePolling()
        }
      } catch {
        if (!isCurrentPoll()) return
        refreshMessages(agentId, sessionId, expectedGeneration)
        onDone()
        stopSavePolling()
      }
    }
    poll()
  }

  function refreshMessages(agentId: number, sessionId: number, expectedGeneration: number) {
    if (currentSession.value?.id === sessionId) {
      // 增量拉取：游标为本地已知最大 DB id，只补缺失的新增消息（拉回最终回复语义不变），
      // 避免全量重置把本地未加载的更早历史插入头部顶跑用户视口
      const afterId = messages.value.at(-1)?.id
      agentApi
        .getMessages(agentId, sessionId, undefined, MESSAGE_REFRESH_LIMIT, afterId)
        .then(res => {
          if (
            res.data.code === 1 &&
            streamGeneration === expectedGeneration &&
            currentAgent.value?.id === agentId &&
            currentSession.value?.id === sessionId
          ) {
            applyLatestMessages(res.data.data?.list || [], res.data.data?.total || 0)
            messageRefreshVersion.value++
          }
        })
        .catch(() => {})
    }
  }

  function stopSavePolling() {
    savePollVersion++
    if (savePollTimer) {
      clearTimeout(savePollTimer)
      savePollTimer = null
    }
  }

  /**
   * 停止后台运行检测轮询
   */
  function stopRunningPolling() {
    runningPollVersion++
    if (runningPollTimer) {
      clearTimeout(runningPollTimer)
      runningPollTimer = null
    }
  }

  /**
   * 轮询检测会话是否仍在后台执行
   *
   * 刷新后若检测到会话在跑，点亮停止按钮（复用 isStreaming）。
   * agent 自然结束时（running=false）自动复位按钮并拉回最终回复；
   * 不设超时上限（agent 可长时间运行）。
   */
  function startRunningPolling(agentId: number, sessionId: number) {
    stopRunningPolling()
    const pollVersion = runningPollVersion
    const expectedGeneration = streamGeneration
    const isCurrentPoll = () =>
      pollVersion === runningPollVersion &&
      expectedGeneration === streamGeneration &&
      currentAgent.value?.id === agentId &&
      currentSession.value?.id === sessionId
    const poll = async () => {
      if (!isCurrentPoll()) return
      try {
        const res = await agentApi.runningStatus(agentId, sessionId)
        if (!isCurrentPoll()) return
        if (res.data.code === 1 && res.data.data?.running) {
          runningPollTimer = setTimeout(poll, 1000)
        } else {
          stopRunningPolling()
          isStreaming.value = false
          refreshMessages(agentId, sessionId, expectedGeneration)
        }
      } catch {
        if (!isCurrentPoll()) return
        stopRunningPolling()
        isStreaming.value = false
      }
    }
    poll()
  }

  /**
   * 中断执行（通知后端停止并断开SSE）
   */
  function interruptExecution() {
    if (isStopping.value) return
    if (!currentAgent.value || !currentSession.value) return
    isStopping.value = true
    cancelStream(true)
  }

  /**
   * 删除指定消息及之后的所有消息
   * @param messageId 要删除的消息ID
   * @returns 被删除的用户消息 {content, files, input_data}，用于回退恢复
   */
  async function deleteMessagesFrom(messageId: number): Promise<AgentDeleteMessagesResult | null> {
    if (!currentAgent.value || !currentSession.value) return null

    try {
      const res = await agentApi.deleteMessagesFrom(
        currentAgent.value.id,
        currentSession.value.id,
        messageId
      )
      if (res.data.code === 1) {
        const deleted = res.data.data
        const beforeCount = messages.value.length
        messages.value = messages.value.filter(m => m.id < messageId)
        messageTotal.value = Math.max(0, messageTotal.value - (beforeCount - messages.value.length))
        rebuildChatMessages()
        return deleted
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
    streamBaseMsgId = 0
    flowPreview.value = null
    isWaitingHuman.value = false
    currentWaitData.value = null
    sessionPage.value = 1
    sessionTotal.value = 0
    cancelStream()
    stopCompressPolling()
    stopRunningPolling()
  }

  /**
   * 压缩会话上下文
   * @param prompt 自定义压缩提示词，追加到默认提示词后，空则仅使用默认
   */
  async function compressSession(
    agentId: number,
    sessionId: number,
    prompt?: string
  ): Promise<boolean> {
    if (currentAgent.value?.id !== agentId || currentSession.value?.id !== sessionId) return false
    const selectionVersion = sessionSelectionVersion
    isCompressing.value = true
    try {
      const res = await agentApi.compress(agentId, sessionId, prompt)
      if (
        selectionVersion !== sessionSelectionVersion ||
        currentAgent.value?.id !== agentId ||
        currentSession.value?.id !== sessionId
      )
        return false
      if (res.data.code === 1) {
        startCompressPolling(agentId, sessionId)
        return true
      }
      isCompressing.value = false
      return false
    } catch {
      if (
        selectionVersion === sessionSelectionVersion &&
        currentAgent.value?.id === agentId &&
        currentSession.value?.id === sessionId
      ) {
        isCompressing.value = false
      }
      return false
    }
  }

  /**
   * 轮询检查会话是否正在压缩上下文
   */
  function startCompressPolling(agentId: number, sessionId: number) {
    stopCompressPolling()
    const pollVersion = compressPollVersion
    const selectionVersion = sessionSelectionVersion
    const isCurrentPoll = () =>
      pollVersion === compressPollVersion &&
      selectionVersion === sessionSelectionVersion &&
      currentAgent.value?.id === agentId &&
      currentSession.value?.id === sessionId
    const check = async () => {
      if (!isCurrentPoll()) return
      try {
        const res = await agentApi.compressStatus(agentId, sessionId)
        if (!isCurrentPoll()) return
        if (res.data.code === 1 && res.data.data?.status === 'compressing') {
          isCompressing.value = true
          compressPollTimer = setTimeout(check, 1000)
        } else if (isCompressing.value) {
          const data = res.data.code === 1 ? res.data.data : null
          isCompressing.value = false
          stopCompressPolling()
          if (data?.status === 'failed') {
            ElMessage.error({ message: data?.error || '上下文压缩失败', duration: 5000 })
          } else if (currentSession.value) {
            await selectSession(agentId, currentSession.value)
          }
        } else {
          stopCompressPolling()
        }
      } catch {
        if (!isCurrentPoll()) return
        stopCompressPolling()
      }
    }
    void check()
  }

  function stopCompressPolling() {
    compressPollVersion++
    if (compressPollTimer) {
      clearTimeout(compressPollTimer)
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
    flowPreview,
    planMode,
    // 消息分页
    hasMoreMessages,
    loadingMoreMessages,
    messageRefreshVersion,
    // 方法
    loadAgents,
    loadAgent,
    lastUsedAgentId,
    loadSessions,
    createSession,
    deleteSession,
    selectSession,
    loadMoreMessages,
    sendMessage,
    resumeWithInput,
    togglePlanMode,
    approveToolCalls,
    rejectToolCalls,
    cancelStream,
    interruptExecution,
    resetState,
    deleteMessagesFrom,
    compressSession,
    startCompressPolling,
    stopCompressPolling,
    stopSavePolling,
    stopRunningPolling
  }
})
