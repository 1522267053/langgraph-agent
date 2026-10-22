/**
 * Agent会话状态管理（优化版）
 * @description 使用composables重构的Agent Store，提供更好的代码复用
 */
import { defineStore } from 'pinia'
import { ref, computed, nextTick, reactive } from 'vue'
import type {
  AgentFlow,
  AgentSession,
  AgentMessage,
  AgentDeleteMessagesResult,
  AgentFileChangeBase
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
import { ApiError } from '@/api/index'
import { USER_RESPONSE_COUNTDOWN_SECONDS } from '@/constants/timing'
import { createOnToolCallLimitHandler, createOnLlmRetryHandler } from '@/composables/useSSEHandlers'
import { useStreamingMessage } from '@/composables'
import { ElMessage } from 'element-plus'

const MESSAGE_REFRESH_LIMIT = 100
const CONTEXT_SUMMARY_MESSAGE_TYPE = 'context_summary'
/** view_media 注入的多模态内部消息（后端标记，前端不渲染为用户气泡） */
const MEDIA_INJECTED_MESSAGE_TYPE = 'media_injected'
/** 执行错误内部消息：仅前端展示错误卡片，后端 get_history 已过滤不进 LLM 上下文 */
const EXECUTION_ERROR_MESSAGE_TYPE = 'execution_error'

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
    // 新轮次事件 id 重新从 1 计数：上一轮水位对新轮事件是过期约束（会把新轮
    // 事件误判为已落库回声），必须清零，等新 run 的 content_watermark 重同步
    contentWatermarkEventId.value = undefined
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
      m => !(m.dbMsgId == null && (m.id.startsWith('user-') || m.id.startsWith('streaming-')))
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
    contentWatermarkEventId,
    addUserMessage,
    startStreaming,
    appendThinking,
    appendContent,
    addToolSegment,
    updateToolSegment,
    updateToolLiveOutput,
    updateToolLiveTool,
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

  // 会话累计 token：以服务端 token_usage 全量聚合为权威基线（含压缩调用，与消息分页无关），
  // 流式期间叠加本轮 SSE token_usage 事件增量；轮次结束重拉基线对齐后清零增量
  const serverTotalTokens = ref(0)
  const streamRunTokens = ref(0)
  const totalSessionTokens = computed(
    () => serverTotalTokens.value + streamRunTokens.value
  )

  /**
   * 拉取会话累计 token 基线（服务端 token_usage 全量聚合）
   * @param resetIncrement true 时同时清零本轮流式增量（轮次结束/切会话对齐场景）
   */
  async function fetchSessionTotalTokens(
    agentId: number,
    sessionId: number,
    resetIncrement = false
  ): Promise<void> {
    try {
      const res = await agentApi.getSessionTotalTokens(agentId, sessionId)
      if (
        res.data.code === 1 &&
        currentAgent.value?.id === agentId &&
        currentSession.value?.id === sessionId
      ) {
        serverTotalTokens.value = res.data.data?.total_tokens || 0
        if (resetIncrement) streamRunTokens.value = 0
      }
    } catch {
      // 拉取失败保留旧基线，不影响会话使用
    }
  }

  // ========== SSE连接（使用composable） ==========

  // ========== 本轮执行计时（流式中每秒跳动，flow_done 用后端权威值定格） ==========
  const streamElapsedMs = ref(0)
  let elapsedTimer: ReturnType<typeof setInterval> | null = null
  /** 本轮起点（本地发送时刻或刷新重连时本轮 human 行的 created_at） */
  let elapsedAnchorTs = 0

  /**
   * 起表：anchorTs 缺省 = 现在（发送/resume 场景）；重连场景传本轮 human 行
   * created_at 毫秒值，保证刷新后计时仍从轮次真实起点累计
   */
  function startElapsedTimer(anchorTs?: number): void {
    stopElapsedTimer()
    elapsedAnchorTs = anchorTs ?? Date.now()
    streamElapsedMs.value = 0
    elapsedTimer = setInterval(() => {
      streamElapsedMs.value = Math.max(0, Date.now() - elapsedAnchorTs)
    }, 1000)
  }

  function stopElapsedTimer(finalDurationMs?: number): void {
    if (elapsedTimer) {
      clearInterval(elapsedTimer)
      elapsedTimer = null
    }
    // 定格：优先后端权威值（flow_done 携带），无则保留当前跳动值
    if (finalDurationMs !== undefined) streamElapsedMs.value = finalDurationMs
  }

  // ========== 人工交互状态 ==========
  const currentWaitData = ref<SSEWaitData | null>(null)
  const isWaitingHuman = ref(false)

  // ========== 工具确认状态（仅Agent模式，队列化：同一 session 可同时多个审批批次） ==========
  interface PendingToolCall {
    name: string
    args: Record<string, unknown>
    id?: string
  }
  // approvalId → { toolCalls, approvalNeeded, subAgent?, deadline? }，不同 approvalId 同时入队
  // 用 reactive 包装 Map 实例（不是 ref(new Map())），确保 set/delete/clear 触发响应式
  // subAgent：子Agent审批路由信息（按批次记录——多批次并行时各批次可能来自不同子会话，
  // 提交时按当前 approvalId 精确路由，不能存全局单值，否则队列推进后路由错 session）
  // deadline：后端权威超时死线（Date.now()+expires_in，按批次记录——倒计时是全局
  // 单值时，批准当前批次 stop 后队列下一批无计时，会误显示「已过期」）
  const pendingApprovals = reactive(
    new Map<
      string,
      {
        toolCalls: PendingToolCall[]
        approvalNeeded: string[]
        subAgent?: { agentId: number; sessionId: number; agentName: string }
        deadline?: number
      }
    >()
  )
  // 当前展示的 approvalId（FIFO：取最早入队的）
  const currentApprovalId = ref<string | null>(null)
  // 派生：当前展示的工具确认数据（兼容旧 store.pendingToolCalls / pendingApprovalNeeded 引用）
  const pendingToolCalls = computed<PendingToolCall[]>(() => {
    const id = currentApprovalId.value
    if (!id) return []
    return pendingApprovals.get(id)?.toolCalls ?? []
  })
  const pendingApprovalNeeded = computed<string[]>(() => {
    const id = currentApprovalId.value
    if (!id) return []
    return pendingApprovals.get(id)?.approvalNeeded ?? []
  })
  const isWaitingToolApproval = computed(() => currentApprovalId.value !== null)
  // 已提交的 approvalId 集合（按 approvalId 幂等，防止同一批次重复提交）
  const resolvedApprovalIds = new Set<string>()
  // 队列推进：删除当前后从 Map 取最早入队的 key 顶上来
  function advanceApprovalQueue(): void {
    const nextId = pendingApprovals.keys().next().value ?? null
    currentApprovalId.value = nextId
    if (!nextId) {
      // 队列清空，重置进度
      approvalProgress.value = { current: 0, total: 0 }
    }
  }
  // 审批进度 N/M：方案B 批次合并后同轮工具合并为单批次一次弹窗，
  // 逐个排队的 N/M 指示器已无意义——保留字段兼容旧回放事件，仅整批
  // 场景（total>1 且批次数>1，如旧版后端）才展示，正常恒为隐藏
  const approvalProgress = ref<{ current: number; total: number }>({ current: 0, total: 0 })
  // 倒计时 tick：每秒自增触发 approvalCountdown 重算（deadline 按批次记录，
  // 计时器只负责驱动刷新，不再持有倒计时状态——批准/拒绝删批次后队列下一批
  // 自动显示自己的剩余时间，不再误显示「已过期」）
  const approvalTick = ref(0)
  let approvalTimer: ReturnType<typeof setInterval> | null = null

  // ========== 子Agent工具审批状态 ==========
  // 派生自当前审批批次的 subAgent 路由信息（与 currentApprovalId 一一对应）：
  // 多批次并行时各批次可能来自不同子会话，全局单值会在队列推进后路由错 session
  // （历史 Bug：第 1 批提交后清空单值，第 2/3 批误走父会话分支报「没有对应审批」）
  const subAgentApproval = computed<{
    isSubAgent: boolean
    agentId: number
    sessionId: number
    agentName: string
  } | null>(() => {
    const id = currentApprovalId.value
    if (!id) return null
    const sub = pendingApprovals.get(id)?.subAgent
    if (!sub) return null
    return { isSubAgent: true, ...sub }
  })

  // 倒计时（秒）：派生自当前批次的 deadline 死线（approvalTick 每秒自增驱动重算）。
  // 队列推进/批次删除后自动跟随下一批，无残留状态可失配
  const approvalCountdown = computed<number>(() => {
    void approvalTick.value // 依赖 tick：每秒重算
    const id = currentApprovalId.value
    if (!id) return 0
    const deadline = pendingApprovals.get(id)?.deadline
    if (!deadline) return 0
    return Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
  })

  // ========== 问题反问状态（ask_user_question 工具，队列化） ==========
  interface QuestionOption {
    label: string
    description?: string
    preview?: string
  }
  interface PendingQuestion {
    questionId: string
    nodeKey: string
    question: string
    header?: string | null
    options: QuestionOption[]
    multiple: boolean
    /** 后端权威回答剩余秒数（刷新重连回放时由服务端重算） */
    expiresIn?: number
    /** 子Agent反问路由信息（按条目记录，提交时按 questionId 精确路由到子会话） */
    subAgent?: { agentId: number; sessionId: number; agentName: string }
  }
  // questionId → PendingQuestion，不同 questionId 同时入队
  // 用 reactive 包装 Map 实例，确保 set/delete 触发响应式（队列推进时 <100ms 内更新下一个弹窗）
  const pendingQuestions = reactive(new Map<string, PendingQuestion>())
  // 当前展示的 questionId（FIFO）
  const currentQuestionId = ref<string | null>(null)
  // 派生：当前展示的问题（兼容旧 store.pendingQuestion 引用）
  const pendingQuestion = computed<PendingQuestion | null>(() => {
    const id = currentQuestionId.value
    if (!id) return null
    return pendingQuestions.get(id) ?? null
  })
  // 已提交的 questionId 集合（幂等）
  const resolvedQuestionIds = new Set<string>()
  // 子Agent反问路由信息：派生自当前问题条目（与 currentQuestionId 一一对应），
  // 同审批队列同模式——全局单值会在队列推进后路由错 session
  const subAgentQuestion = computed<{
    isSubAgent: boolean
    agentId: number
    sessionId: number
    agentName: string
  } | null>(() => {
    const id = currentQuestionId.value
    if (!id) return null
    const sub = pendingQuestions.get(id)?.subAgent
    if (!sub) return null
    return { isSubAgent: true, ...sub }
  })
  // 队列推进：删除当前后从 Map 取最早入队的 key 顶上来
  function advanceQuestionQueue(): void {
    const nextId = pendingQuestions.keys().next().value ?? null
    currentQuestionId.value = nextId
  }

  // ========== 文件变更（侧栏 Diff 面板） ==========
  // 列表缓存：与后端逐条记录口径一致（每次成功改动一条，同文件多次编辑多条共存）；
  // 已撤销条目仅本地标记（拉取时后端已过滤 is_reverted），badge 数量用 fileChangesCount
  const fileChanges = ref<AgentFileChangeBase[]>([])
  const fileChangesLoading = ref(false)
  // 请求序号守卫：会话快速切换时丢弃过期回包（最新请求胜出）
  let fileChangesReqSeq = 0
  // 当前打开的 diff 详情
  const activeFileChangeId = ref<number | null>(null)
  const activeFileChangeDiff = ref<{
    backup_content: string
    current_content: string
    is_binary: boolean
    backup_missing: boolean
    file_path: string
    change_type: string
  } | null>(null)
  const activeFileChangeDiffLoading = ref(false)
  // 单条撤销后的回执消息 key
  const lastRevertedChangeId = ref<number | null>(null)
  /** 未撤销变更数：右上角「文件」badge 口径，与后端拉取的 is_reverted=0 过滤一致 */
  const fileChangesCount = computed(() => fileChanges.value.filter(c => !c.is_reverted).length)

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

  // ========== 会话列表「对话中」图标轮询（批量接口，2s 自适应） ==========
  // 仅当列表存在 running 会话时轮询，全部结束自动停表；refresh 周期 2s
  let sessionStatusPollTimer: ReturnType<typeof setTimeout> | null = null
  let sessionStatusPollVersion = 0
  const SESSION_STATUS_POLL_INTERVAL = 2000

  // ========== 计划模式（只读探索，不执行修改），按会话独立存储 ==========
  // 有会话时以 currentSession.plan_mode（DB 字段）为权威；无会话时暂存到
  // pendingPlanMode（纯内存，本标签独享），首次创建会话时随 createSession 传入。
  // 禁止写入 localStorage：跨标签共享会导致「新建会话被别的标签自动切成计划模式」。
  const pendingPlanMode = ref(false)
  const planMode = computed(() =>
    currentSession.value ? !!currentSession.value.plan_mode : pendingPlanMode.value
  )
  /**
   * 无会话阶段暂存的工作路径（纯内存，本标签独享）。
   * 有会话时以 currentSession.work_dir 为准；首次创建会话时随 createSession 传入。
   * 与 pendingPlanMode 同语义：只承载用户在本标签的显式选择，不做跨标签继承。
   */
  const pendingWorkDir = ref('')
  async function togglePlanMode(): Promise<void> {
    const next = !planMode.value
    const agentId = currentAgent.value?.id
    const sessionId = currentSession.value?.id
    if (agentId && sessionId) {
      try {
        const res = await agentApi.updatePlanMode(agentId, sessionId, next)
        // await 期间用户可能已切换会话，只回写仍是目标会话的标志
        if (res.data.code === 1 && currentSession.value?.id === sessionId) {
          currentSession.value.plan_mode = next ? 1 : 0
        }
      } catch {
        // error handled by interceptor
        return
      }
    } else {
      pendingPlanMode.value = next
    }
  }

  /**
   * 更新会话级临时模型与推理深度（按会话独立存储，对标 togglePlanMode）
   * @param model 模型 id，空串表示清除（回退 LLM 节点默认，reasoning 同步清除）
   * @param provider 与 model 配套的供应商 ID
   * @param reasoning 推理深度（off/none/minimal/low/medium/high/xhigh/max），空=跟随节点配置
   * @returns 是否成功写入（无会话或接口失败返回 false）
   */
  async function updateSessionChatModel(
    model: string,
    provider?: string,
    reasoning?: string | null
  ): Promise<boolean> {
    const sessionId = currentSession.value?.id
    const agentId = currentAgent.value?.id
    if (!agentId || !sessionId) return false
    try {
      const res = await agentApi.updateChatModel(agentId, sessionId, model || null, provider, reasoning)
      // await 期间用户可能已切换会话，只回写仍是目标会话的标志
      if (res.data.code === 1 && currentSession.value?.id === sessionId) {
        currentSession.value.chat_model = model || null
        currentSession.value.chat_provider = model ? provider || null : null
        currentSession.value.chat_reasoning = model ? reasoning || null : null
      }
      return true
    } catch {
      // error handled by interceptor
      return false
    }
  }

  // ========== 流程预览（AI 创建/修改流程时推送，独立于消息分段） ==========
  const flowPreview = ref<{
    flow_id: number
    flow_name?: string
    flow_type?: 'flow' | 'agent'
    action?: string
    nodes?: Record<string, unknown>[]
    edges?: Record<string, unknown>[]
    deleted?: boolean
  } | null>(null)
  // 折叠态（单行条）：flow_done 后自动折叠，消除回合结束后画布悬空钉底；
  // 手动最小化/展开（卡片「—」按钮）同走此状态，单一状态源
  const flowPreviewCollapsed = ref(false)

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
        const list = res.data.data?.list || []
        // finish_unread（DB 持久化）→ justFinished（前端展示态）
        for (const s of list) {
          s.justFinished = (s as unknown as { finish_unread?: number }).finish_unread === 1
        }
        sessions.value = list
        sessionTotal.value = res.data.data?.total || 0
        // 列表含运行中会话 → 启动 2s 批量状态轮询（全结束自动停表）
        syncSessionStatusPolling(agentId)
      }
    } finally {
      sessionsLoading.value = false
    }
  }

  /**
   * 「对话中」图标轮询编排：列表有 running 会话则确保轮询存活，否则停表。
   * 轮询体走批量内存接口（runningStatusBatch）本地 mutate running，
   * 不重拉 page 接口、不置 sessionsLoading（避免 v-loading 闪烁）。
   */
  function syncSessionStatusPolling(agentId: number) {
    if (sessions.value.some(s => s.running)) {
      startSessionStatusPolling(agentId)
    } else {
      stopSessionStatusPolling()
    }
  }

  function startSessionStatusPolling(agentId: number) {
    stopSessionStatusPolling()
    const pollVersion = ++sessionStatusPollVersion
    sessionStatusPollTimer = setTimeout(poll, SESSION_STATUS_POLL_INTERVAL)

    async function poll() {
      if (pollVersion !== sessionStatusPollVersion) return
      // 当前 Agent 已切换或列表被清空（resetState）：本表作废
      if (currentAgent.value?.id !== agentId || sessions.value.length === 0) {
        stopSessionStatusPolling()
        return
      }
      try {
        const res = await agentApi.runningStatusBatch(
          agentId,
          sessions.value.map(s => s.id)
        )
        if (pollVersion !== sessionStatusPollVersion) return
        if (res.data.code === 1) {
          const runningIds = new Set(res.data.data?.running_ids || [])
          const waitingIds = new Set(res.data.data?.waiting_ids || [])
          const prevRunningIds = new Set(
            sessions.value.filter(s => s.running).map(s => s.id)
          )
          for (const s of sessions.value) {
            s.running = runningIds.has(s.id)
            s.waiting = waitingIds.has(s.id)
            // 执行完成亮点：非当前会话 running→false 翻转时点亮，点击会话后熄灭
            if (
              prevRunningIds.has(s.id) &&
              !s.running &&
              s.id !== currentSession.value?.id
            ) {
              s.justFinished = true
            }
          }
          if (runningIds.size > 0) {
            sessionStatusPollTimer = setTimeout(poll, SESSION_STATUS_POLL_INTERVAL)
            return
          }
        }
        // 请求失败或已无运行会话：停表（下次 loadSessions 重新评估）
      } catch {
        // 批量状态查询失败不影响列表使用；停表待下次 loadSessions 重启
      }
      stopSessionStatusPolling()
    }
  }

  function stopSessionStatusPolling() {
    sessionStatusPollVersion++
    if (sessionStatusPollTimer) {
      clearTimeout(sessionStatusPollTimer)
      sessionStatusPollTimer = null
    }
  }

  /** 本地立即设置某会话 running 并同步轮询起停（发消息/流结束的即时反馈） */
  function setSessionRunningLocal(
    agentId: number,
    sessionId: number,
    running: boolean
  ) {
    if (currentAgent.value?.id !== agentId) return
    const target = sessions.value.find(s => s.id === sessionId)
    if (target) {
      // 执行完成亮点：与批量轮询同一翻转语义（非当前会话 running→false 点亮）
      if (
        target.running &&
        !running &&
        sessionId !== currentSession.value?.id
      ) {
        target.justFinished = true
      }
      target.running = running
      // 点亮时跟随当前等待态（错误→等待路径 isWaitingHuman 已置真）；
      // 熄灭同步清 waiting。发消息/resume 前 isWaitingHuman 已复位为 false。
      target.waiting = running ? isWaitingHuman.value : false
    }
    syncSessionStatusPolling(agentId)
  }

  /**
   * 当前会话流结束：清除后端 finish_unread（用户正看着本会话，红点不应出现）。
   * 后台完成的会话（用户在别的会话/页面）不清除——红点保留提醒有新完成。
   * 与 selectSession 的清除对称：后端 mark 无条件置位，观看侧负责熄灭。
   */
  function clearFinishUnreadIfCurrent(agentId: number, sessionId: number) {
    if (
      currentAgent.value?.id === agentId &&
      currentSession.value?.id === sessionId
    ) {
      agentApi.clearFinishUnread(agentId, sessionId).catch(() => {
        // 清除失败不影响使用；下次进入会话仍会清除兜底
      })
    }
  }

  /**
   * 创建新会话
   * @param workDir 可选，会话级项目工作路径
   * @param planMode 可选，计划模式开关
   * @param chatModel 可选，会话级临时覆盖 LLM 模型 id
   * @param chatProvider 可选，与 chatModel 配套的供应商 ID
   */
  async function createSession(
    agentId: number,
    workDir?: string,
    planMode?: boolean,
    chatModel?: string,
    chatProvider?: string,
    chatReasoning?: string | null
  ): Promise<AgentSession | null> {
    try {
      const res = await agentApi.createSession(
        agentId,
        workDir,
        planMode,
        chatModel,
        chatProvider,
        chatReasoning
      )
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
      // 删除后停留在当前页；当前页被删空则回退一页（第 1 页除外）
      const page = sessionPage.value
      await loadSessions(agentId, page)
      if (page > 1 && sessions.value.length === 0) {
        await loadSessions(agentId, page - 1)
      }
    } catch {
      // error handled by interceptor
    }
  }

  /**
   * 批量删除会话
   */
  async function deleteSessionsBatch(agentId: number, sessionIds: number[]) {
    if (sessionIds.length === 0) return

    // 删除后停留在当前页；当前页被删空则回退一页（第 1 页除外）
    async function reloadList() {
      const page = sessionPage.value
      await loadSessions(agentId, page)
      if (page > 1 && sessions.value.length === 0) {
        await loadSessions(agentId, page - 1)
      }
    }

    try {
      await agentApi.deleteSessions(agentId, sessionIds)
      if (currentSession.value && sessionIds.includes(currentSession.value.id)) {
        currentSession.value = null
        messages.value = []
        clearMessages()
        streamBaseMsgId = 0
      }
      await reloadList()
    } catch (e) {
      // 含运行中会话时后端返回 code=0（运行中会话跳过不删，拦截器已弹提示），
      // data 携带明细 { deleted, skipped }，经拦截器 ApiError 透传到这里。
      // 刷新策略：真的删掉过（deleted > 0）就必须刷新，让已删会话从列表消失；
      // 全被跳过（deleted === 0）时列表未变，无需刷新。
      const deleted = e instanceof ApiError ? (e.data as { deleted?: number })?.deleted : 0
      if (deleted) await reloadList()
    }
  }

  /**
   * 上次会话（localStorage 持久化）：刷新/切页返回/重开浏览器均恢复。
   * 仅记 sessionId；页码由后端 /position 接口按 id 降序实时计算（分页漂移也能精确恢复）。
   * key 按 agentId 隔离，多 Agent 互不干扰。
   */
  function lastSessionPosKey(agentId: number): string {
    return `last_session_pos:${agentId}`
  }

  function saveLastSessionPos(agentId: number, sessionId: number): void {
    try {
      localStorage.setItem(lastSessionPosKey(agentId), String(sessionId))
    } catch {
      // 存储失败不影响功能
    }
  }

  function loadLastSessionPos(agentId: number): number | null {
    try {
      const raw = localStorage.getItem(lastSessionPosKey(agentId))
      if (!raw) return null
      const sessionId = Number(raw)
      return Number.isInteger(sessionId) && sessionId > 0 ? sessionId : null
    } catch {
      return null
    }
  }

  function clearLastSessionPos(agentId: number): void {
    try {
      localStorage.removeItem(lastSessionPosKey(agentId))
    } catch {
      // 存储失败不影响功能
    }
  }

  /**
   * 恢复上次会话：先加载第 1 页兜底（保证列表总有数据），localStorage 有记录
   * 则经后端 /position 算页码后跳页选中。任一步失败返回 false，由调用方回退
   * 默认逻辑（保持第 1 页选第一个）。
   */
  async function restoreLastSession(agentId: number): Promise<boolean> {
    await loadSessions(agentId, 1)
    const sessionId = loadLastSessionPos(agentId)
    if (!sessionId) return false
    try {
      const res = await agentApi.getSessionPosition(
        agentId,
        sessionId,
        sessionPageSize.value
      )
      if (res.data.code !== 1 || !res.data.data) {
        // 会话已被删除/失效：清除脏记录，避免每次进入都白发一次请求
        clearLastSessionPos(agentId)
        return false
      }
      const page = res.data.data.page
      if (page !== 1) await loadSessions(agentId, page)
      const target = sessions.value.find(s => s.id === sessionId)
      if (target) {
        await selectSession(agentId, target)
        return true
      }
      // 页码算出来了但列表里找不到（并发删除）：同样清理脏记录
      clearLastSessionPos(agentId)
    } catch {
      // 网络/接口异常保持第 1 页走回退（不清除记录，待下次重试）
    }
    return false
  }

  /**
   * 选择会话
   */
  async function selectSession(agentId: number, session: AgentSession) {
    const selectionVersion = ++sessionSelectionVersion
    messagesLoading.value = true
    clearMessages()
    streamBaseMsgId = 0
    stopElapsedTimer()
    cancelStream()
    stopCompressPolling()
    isCompressing.value = false
    // 用户点进该会话查看：本地立即熄灭红点，并异步清除 DB 未读标记（不阻塞加载）
    if (session.justFinished) {
      session.justFinished = false
      agentApi.clearFinishUnread(agentId, session.id).catch(() => {
        // 清除失败不影响会话使用；下次进入会重试
      })
    }
    const selectionGeneration = streamGeneration
    flowPreview.value = null
    flowPreviewCollapsed.value = false
    // 切换会话：清空 Diff 面板状态（避免显示上一会话的文件变更）
    fileChanges.value = []
    fileChangesReqSeq++
    activeFileChangeId.value = null
    activeFileChangeDiff.value = null
    lastRevertedChangeId.value = null

    currentSession.value = session
    // 记录上次会话 id，供刷新/切页返回后恢复（页码由后端 /position 实时计算）
    saveLastSessionPos(agentId, session.id)
    // 切会话：重拉累计 token 基线并对齐清零增量（守卫在 fetchSessionTotalTokens 内）
    void fetchSessionTotalTokens(agentId, session.id, true)
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
        // 重连对齐基线 = 「本轮起点之前的最新 DB 行 id」，供 rebuildChatMessages
        // 识别「本轮新增」行、把流式占位行与 DB 组对齐合并。
        //
        // 取本轮最后一条 human 行 id - 1（严格晚于它的行都属本轮）；
        // 窗口内无 human 行时（长 ReAct 的几十条 tool/ai 行挤满 20 条首屏窗口，
        // 本轮 human 行被挤出）退化为「窗口最小 id - 1」——该前提下 human 行
        // 必然早于窗口，窗口内全部行都属本轮，必须整体视为新增。
        // 若沿用「窗口最大 id」（本次修复前的写法）会把整轮判为旧行：
        // 本轮所有行 id ≤ 该值 → freshRows 恒空 → 占位行无候选 → DB 气泡与
        // SSE 占位气泡双份并存，且该值不随窗口内容变化，是结构性错误。
        const lastHumanRow = [...messages.value].reverse().find(m => m.role === 'human')
        streamBaseMsgId = lastHumanRow
          ? lastHumanRow.id - 1
          : Math.max((messages.value[0]?.id ?? 1) - 1, 0)
        rebuildChatMessages()
        // 预拉取文件变更列表，驱动右上角"文件"badge；SSE file_changed 后续增量维护
        void fetchFileChanges()
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
        // ---- 权威轮次基线：后端回填的本轮 human 行 id 优先 ----
        // 首屏窗口仅 20 条，长 ReAct 会把本轮 human 行挤出窗口，此前「窗口内找
        // human → 找不到退化为窗口最小 id-1」会把基线落在轮中间——后续 loadMore
        // 拉入同轮旧行时，跨基线拆分把同一轮拦腰切成两个气泡（实测交叉乱序）。
        // 后端在 user_prompt 落库后回填 round_start_message_id，此处用
        // 「权威值 - 1」覆盖窗口推断值，再进行下面的即时合并。
        const backendRoundStartId = Number(runRes.data.data?.round_start_message_id) || 0
        if (backendRoundStartId > 0) {
          streamBaseMsgId = backendRoundStartId - 1
        }
        // 内容回放水位初始化：重连回放前以 running_status 权威值为准，使回放区间
        // 内 ≤ 水位的段来源事件在前端即被回声拦截（后端回放水位过滤的双保险）
        const backendWatermark = Number(runRes.data.data?.content_watermark_event_id) || 0
        if (backendWatermark > (contentWatermarkEventId.value ?? 0)) {
          contentWatermarkEventId.value = backendWatermark
        }
        startStreaming(true)
        // 立即与 DB 已重建的 ai 组做一次对齐合并。
        //
        // startStreaming(true) 会强制 push 一个空占位行；刷新时 DB 窗口内往往已含
        // 本轮 ai 组（中途落库 / tool 行），于是渲染成 [ai组(DB)] [ai空占位] 两个
        // 同级气泡——这正是「上方 DB、下方 SSE」双气泡的直因。
        // 原实现只在 flow_done 才合并，整个流式期间保持双气泡；此处补一次即时对齐：
        // rebuildChatMessages 的反向对齐按 role=ai 命中 DB 组，通过 reattach 把 DB 段
        // 并入占位行（保留占位行 id/key，DOM 不重挂），后续 SSE 增量继续追加到同一行。
        // preserveStreaming=true：保持流式态（否则 rebuild 会把 isStreaming 翻 false）。
        rebuildChatMessages(true)
        const context: AgentStreamContext = {
          agentId,
          sessionId: session.id,
          generation: selectionGeneration,
          wasFirstMessage: false
        }
        const runId = runRes.data.data?.run_id
        if (runId && runRes.data.data?.managed_running) {
          // 计时锚点优先级：后端权威墙钟（round_started_at_ms，跨进程可靠）
          // → 本轮 human 行 created_at（窗口内可见时）
          // → 窗口最早行 created_at（本轮 human 行被长 ReAct 挤出 20 条首屏窗口，
          //   此时窗口内全属本轮，最早行时间即本轮可观测起点）
          // → now（兜底）。早先只做前两级，长轮次刷新会落到 now → 计时归零重算。
          const lastHuman = [...messages.value].reverse().find(m => m.role === 'human')
          const backendAnchor = Number(runRes.data.data?.round_started_at_ms) || 0
          const firstRowTs = messages.value[0]?.created_at
            ? new Date(messages.value[0].created_at).getTime()
            : NaN
          const anchorTs =
            backendAnchor > 0
              ? backendAnchor
              : lastHuman?.created_at
                ? new Date(lastHuman.created_at).getTime()
                : firstRowTs
          if (Number.isFinite(anchorTs)) startElapsedTimer(anchorTs)
          else startElapsedTimer()
          // 全量回放（after=0）：内容类事件的水位切分已在后端完成
          // （水位 = 最后一个 node_done，其之前的内容已落库、由上面的 DB 重建呈现，
          // 后端直接跳过不再下发）；进行中节点的已输出内容由此回放到空占位行上，
          // 刷新不再丢内容。等待响应类事件同样全量回放，弹窗得以恢复。
          streamAbort = agentApi.subscribeRun(
            agentId,
            session.id,
            runId,
            0,
            createStreamHandlers(context)
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
          // 与上方 running 分支同语义：全量回放 + 后端水位切分内容类事件。
          // 本分支原本即未做前端过滤，现与 running 分支口径完全一致
          streamAbort = agentApi.subscribeRun(
            agentId,
            session.id,
            runRes.data.data.run_id,
            0,
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
   *
   * 流式中也允许：用户上滚后 followPinned=false，rescue 循环与
   * guardedElementScroll 补偿写入均已让位，前插不会与贴底跟随打架；
   * onFlowDone 的 replace 全量刷新与增量 applyLatestMessages 均按 dbMsgId
   * 保留前插的历史行，不会丢失。前插必须以 preserveStreaming 保留流式状态，
   * 否则 rebuildChatMessages 会把 isStreaming 翻 false 断掉流式 UI。
   */
  async function loadMoreMessages(agentId: number) {
    if (
      !currentSession.value ||
      loadingMoreMessages.value ||
      !hasMoreMessages.value
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
          // 流式中前插：保留流式状态（rebuildChatMessages 默认会翻 isStreaming=false）
          // 基线重推：退化基线（窗口最小id-1）只在「窗口=本轮全部行」前提下自洽；
          // loadMore 拉入同轮更早的已落库行后该前提被打破——若沿用旧基线，
          // rebuild 的「跨基线并组拆分」会把同一轮内容在基线处拦腰切成
          // [历史气泡(基线前段)] + [活跃气泡(基线后段)] 两个 ai 气泡。
          // 重推 = 用扩展后的全量本地行重找本轮 human 行（流式中 human 行
          // id 必然 ≤ 旧基线前插行之外的所有新增行，重找结果只会更准）。
          if (isStreaming.value) {
            const lastHuman = [...messages.value].reverse().find(m => m.role === 'human')
            if (lastHuman && lastHuman.id - 1 < streamBaseMsgId) {
              streamBaseMsgId = lastHuman.id - 1
            }
          }
          rebuildChatMessages(isStreaming.value)
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
        // 防线 A：context-summary 仅采纳一次；任何后续重复（后端重复推送 / DB 残留 /
        // 用户在 human 输入里模仿"上下文摘要"格式）直接丢弃。摘要识别按 message_type
        // 判定，与文本前缀无关，human 输入里的"上下文摘要"四字天然不会误判。
        if (result.some(m => m.displayType === 'context-summary')) continue
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
      } else if (msg.message_type === EXECUTION_ERROR_MESSAGE_TYPE) {
        // 执行错误卡片：内部消息独立成行（同 context-summary 模式），
        // flush 当前 AI 组避免错误行被吸进前一条 AI 消息
        if (currentAssistant) {
          result.push(currentAssistant)
          currentAssistant = null
        }
        result.push({
          id: `msg-${msg.id}`,
          dbMsgId: msg.id,
          role: 'ai',
          displayType: 'execution-error',
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

        if (msg.thinking?.trim()) {
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
              // 段级 dbMsgId 必填：rebuildChatMessages 的「窗口外旧行」守卫靠它识别
              // 被合并组吸收的行（纯 tool_calls 消息整行吸收时，行级 dbMsgId 是组首，
              // 缺段级标记会导致旧行误判为窗口外而 unshift 到头部，出现重复+锚定失效）
              dbMsgId: msg.id,
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
                    dbMsgId: msg.id,
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
                  dbMsgId: msg.id,
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
        // 本轮执行耗时：记于该轮最后一条 AI 行，组内任一行携带即拾取
        if (msg.duration_ms && !currentAssistant.duration_ms) {
          currentAssistant.duration_ms = msg.duration_ms
        }
      }
    }

    if (currentAssistant) {
      result.push(currentAssistant)
    }

    // 防线 B（防御性兜底）：即便防线 A 已保证 result 最多一条 context-summary，
    // 若旧数据 / 迁移期残留导致出现多条，仅保留「第一条 human 之前」的那一条；
    // 从后往前遍历 splice 避免下标错位。
    // 合法位置是「在前」（i < firstHumanIdx）而非「紧邻」：摘要行与「[上下文已压缩]」
    // 通知 human 之间可隔任意压缩后历史——会话继续跑多轮后上翻回看，窗口切片正切在
    // 两者之间，紧邻假设会把合法摘要卡误判为残留裁掉（渲染成空白）。
    const firstHumanIdx = result.findIndex(
      m => m.role === 'human' && m.displayType !== 'context-summary'
    )
    // 仅当确实存在 ≥1 条 human 时裁剪「位置错」的摘要；无 human 场景保持防线 A 的
    // 天然去重结果（最多 1 条，位于头部——这是防线 A 留下的首条，按业务语义可接受；
    // 真出现此场景说明该会话仅含摘要而无对话，渲染层会按 0 human 正常处理）。
    if (firstHumanIdx !== -1) {
      for (let i = result.length - 1; i >= 0; i--) {
        if (result[i].displayType === 'context-summary' && i >= firstHumanIdx) {
          result.splice(i, 1)
        }
      }
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

    // [FIX] 前插历史跨基线并组拆分（流式中）：loadMore 前插的旧行与本轮流式行
    // 同为连续 ai 行，buildChatMessagesFromDB 会合并成一组（组首=前插行 id），
    // 使已回填 dbMsgId（原组首）的活跃占位行在 rebuilt 中失去对应组——随后被
    // 「窗口外旧行」守卫静默丢弃（段级 id 命中 keptDbIds），底部被 DB 快照整体
    // 重写（运行中工具/实时任务计划卡消失）。按基线拆分后：基线前的段留在历史
    // 行正常渲染，基线后的段独立成行与占位行对齐，恢复「历史 + 活跃流式行」结构。
    // token/耗时等轮级聚合无法按段拆分，经展开保留在新鲜行（活跃轮展示语义），
    // 历史行清零（同轮中途切片，非完整轮次）。
    // 拆分仅在流式中进行：连续 ai 行横跨基线只能由前插历史引入（窗口内有 human
    // 行时 human 会切断 ai 连续性；无 human 的长 ReAct 窗口整窗同轮），终态重建
    // 无此形态，保持 DB 全量快照语义。
    if (preserveStreaming && streamBaseMsgId > 0) {
      for (let i = rebuilt.length - 1; i >= 0; i--) {
        const row = rebuilt[i]
        if (row.role !== 'ai' || row.displayType) continue
        const preSegs = row.segments.filter(
          s => s.dbMsgId != null && s.dbMsgId <= streamBaseMsgId
        )
        const freshSegs = row.segments.filter(
          s => s.dbMsgId == null || s.dbMsgId > streamBaseMsgId
        )
        if (preSegs.length === 0 || freshSegs.length === 0) continue
        const freshHeadId = freshSegs.find(s => s.dbMsgId != null)?.dbMsgId
        const freshRow: StreamingMessage = {
          ...row,
          id: `msg-${freshHeadId ?? row.dbMsgId}`,
          dbMsgId: freshHeadId ?? row.dbMsgId,
          segments: freshSegs
        }
        row.segments = preSegs
        const rebase = (r: StreamingMessage) => {
          r.content = ''
          r.thinking = undefined
          r.tools = undefined
          for (const s of r.segments) {
            if (s.type === 'content') r.content = s.content
            else if (s.type === 'thinking') r.thinking = s.thinking
            else if (s.type === 'tool') (r.tools ??= []).push(s.tool)
          }
        }
        rebase(row)
        rebase(freshRow)
        // 轮级聚合经展开已在 freshRow，历史行清零
        row.prompt_tokens = undefined
        row.completion_tokens = undefined
        row.total_tokens = undefined
        row.latest_prompt_tokens = undefined
        row.duration_ms = undefined
        row.end_output = undefined
        rebuilt.splice(i, 1, row, freshRow)
      }
    }

    const local = chatMessages.value

    // ---- 占位行识别：本地尾部连续的流式临时行 ----
    // 占位行与 DB 组对齐（reattach）后会被回填 dbMsgId（组首 id）并保留
    // streaming- 前缀 id。本轮流式中（preserveStreaming）它仍是活跃行（SSE 增量
    // 持续写入），必须继续按占位行参与后续对齐——否则 loadMore 前插历史触发重建时，
    // 该行在 rebuilt 中无对应组（其 dbMsgId 组已与前插行合并、组首已变），
    // 落入「窗口外旧行」守卫被静默丢弃 → 运行中工具/实时段整体消失。
    // 放宽必须限定 dbMsgId > streamBaseMsgId（本轮起点）：此前轮次的 streaming-
    // 行在其 flow_done 合并时同样回填了 dbMsgId 但 id ≤ 基线，若一并放宽会被
    // 尾部扫描误认作占位行 → 对齐无候选 → 走 trailingAiPlaceholders 被推到列表
    // 末尾（整条历史 AI 回复跳到最新消息之后，实测回归）。
    // 终态重建（preserve=false）不放宽：结束路径的占位行必须被 DB 行替换。
    const isActivePlaceholder = (m: StreamingMessage): boolean =>
      (m.dbMsgId == null ||
        (preserveStreaming &&
          m.id.startsWith('streaming-') &&
          m.dbMsgId != null &&
          m.dbMsgId > streamBaseMsgId)) &&
      (m.id.startsWith('user-') || m.id.startsWith('streaming-'))
    const isPlaceholder = (m: StreamingMessage | undefined): boolean =>
      !!m && isActivePlaceholder(m)
    let placeholderStart = local.length
    while (placeholderStart > 0 && isPlaceholder(local[placeholderStart - 1])) placeholderStart--
    const placeholders = local.slice(placeholderStart)

    // ---- 占位行与本轮新增 DB 渲染行尾部反向对齐 ----
    // 占位行是会话最新消息，对应 DB 行也是最新的：从尾部向前按 role 匹配。
    // 顺序单调消费在中间缺行（如中断轮次的 ai 为空 chunk 未落库）时会让后续
    // 占位级联失配；反向对齐只影响末尾对应关系，缺行位置的行保持独立成行。
    // streamBaseMsgId=0（首轮/空会话）时 dbMsgId > 0 恒真，全部 rebuilt 行参与匹配
    const matchedPlaceholders: StreamingMessage[] = []
    /**
     * 「本轮新增」判定：渲染行自身或其任意分段的 dbMsgId 晚于基准即为新增。
     *
     * 连续 ai 行的行级 dbMsgId 取「组首」行 id（buildChatMessagesFromDB 合并语义），
     * 而 ReAct 每 N 轮中途落库会让落库边界切进 ai 组内：组首 id ≤ 基准、
     * 组内后段行 id > 基准。只看行级会把整组误判为旧行 → freshRows 为空 →
     * 流式占位行反向对齐游标立即出界（freshIdx = -1）→ 占位气泡不与 DB 组
     * 合并，出现「上方 DB 气泡 + 下方 SSE 气泡」双份渲染。
     * 段级 dbMsgId 在 content/thinking/tool/todo 段上均有（见 buildChatMessagesFromDB）。
     */
    const isFreshRow = (r: StreamingMessage): boolean =>
      (r.dbMsgId != null && r.dbMsgId > streamBaseMsgId) ||
      r.segments.some(s => s.dbMsgId != null && s.dbMsgId > streamBaseMsgId)
    // 排除 context-summary / execution-error：两者是内部标记消息（压缩摘要/错误卡片），
    // 参与对齐会被尾部流式占位行吸收（跳过内部行 + dbMsgId 过继给
    // 占位行），导致卡片卡在下次全量重建前不渲染；占位行只与真实对话行对齐
    // （提升到 if 外：未匹配占位行分流复用本轮新增行清单）
    const freshRows = rebuilt.filter(
      r =>
        isFreshRow(r) &&
        r.displayType !== 'context-summary' &&
        r.displayType !== 'execution-error'
    )
    if (placeholders.length > 0) {
      let freshIdx = freshRows.length - 1
      for (let pi = placeholders.length - 1; pi >= 0 && freshIdx >= 0; pi--) {
        const ph = placeholders[pi]
        const candidate = freshRows[freshIdx]
        // 角色不匹配时不消费游标：该行留给更早的占位行。中断停止可能只落库
        // human 行（ai 行未保存），此时 streaming(ai) 占位若吞掉游标会导致
        // user(human) 占位失配 → DB 行与占位行重复渲染同一用户气泡。
        // 占位行序列与 freshRows 时序天然同构（human 在前 ai 在后），无需跨行搜索
        if (candidate.role !== ph.role) continue
        freshIdx--
        // 仅回填元数据：保留原 id 与 segments（行 key 不变 → 虚拟行复用、内容不回退）
        ph.dbMsgId = candidate.dbMsgId
        ph.prompt_tokens = candidate.prompt_tokens
        ph.completion_tokens = candidate.completion_tokens
        ph.total_tokens = candidate.total_tokens
        if (candidate.latest_prompt_tokens) ph.latest_prompt_tokens = candidate.latest_prompt_tokens
        // ---- 区块拼接（事件 id 互斥，无任何段级匹配） ----
        //
        // 不变量：candidate 组的段全部带 dbMsgId（DB 权威，落库时序）；ph（占位行）
        // 的 live 段带 srcEventId（SSE 来源事件 id）。后端保证「事件 id ≤ 水位的内容
        // 必已落库」，故 ph 中 srcEventId ≤ 水位的段与 candidate 的 DB 段必然重叠
        // （回放回声），直接丢弃；> 水位或无 srcEventId 的是「进行中、未落库」增量，
        // 全部保留。两区自此互斥，拼接 = 一次数组连接，无配对/前缀匹配/签名判重
        // （此前四版段级匹配补丁——键判重、序数游标、身份制、前缀 fuzzy——全部删除，
        // 它们的连环缺陷：键永不命中整批复制、序数错位重复 key、顺序漂移乱序，
        // 根因都是试图在「两源重叠」上做内容级仲裁，而正确解是让两源不重叠）。
        //
        // 水位来源（useStreamingMessage.contentWatermarkEventId）：content_watermark
        // 事件实时同步 → running_status 权威值重连初始化 → undefined（旧 run 兜底，
        // 不丢弃任何段，宁多勿丢——最坏回到「多一张卡」，不会丢内容）。
        const watermark = contentWatermarkEventId.value
        const liveKept = ph.segments.filter(seg => {
          // 上次拼接进来的 DB 段（带 dbMsgId、无 srcEventId）：candidate 已包含
          // 全部 DB 段（同确定性段 id），不排除会双份 → Vue 重复 key
          // （实测流式中 loadMore：row-s-1863-t 等确定性段 id 重复告警）
          if (seg.dbMsgId != null) return false
          if (watermark == null) return true
          if (seg.srcEventId == null) return true
          return seg.srcEventId > watermark
        })
        // DB 段（落库时序）在前，live 增量段在后；空占位行拼完即纯 DB 快照
        ph.segments = [...candidate.segments, ...liveKept]
        matchedPlaceholders.push(ph)
      }
    }

    // ---- 已提交行按 dbMsgId 对齐：命中复用本地对象（保 key），未命中新建 ----
    // 占位对齐命中行走同一 Map：已提交的占位行按 rebuilt 原位出现（本地替身），
    // 只有未匹配占位行（真正未落库的流式占位）才堆尾部。旧实现把命中占位行
    // matchedFresh 跳过+堆尾，当 freshRows 里夹着内部行（如 execution-error：
    // 不参与对齐但仍在 rebuilt 尾部）时，[human占位堆尾, error行] 顺序颠倒——
    // 错误卡片被插到用户消息前面（实时态与刷新后 DB 顺序不一致）
    const localById = new Map<number, StreamingMessage>()
    for (let i = 0; i < placeholderStart; i++) {
      const m = local[i]
      if (m.dbMsgId != null) localById.set(m.dbMsgId, m)
    }
    const placeholderById = new Map<number, StreamingMessage>()
    for (const ph of matchedPlaceholders) placeholderById.set(ph.dbMsgId!, ph)
    const result: StreamingMessage[] = []
    // 执行错误卡延后到占位行之后：DB 里 error 紧跟 human，但异常终止时 AI 内容
    // 未落库——本地仍持有未匹配的 AI 流式占位行（含已收到的 thinking）。时序上
    // thinking 先于错误产生，错误卡作为本轮终态应保持在视图最下方
    const terminalRows: StreamingMessage[] = []
    for (const r of rebuilt) {
      if (r.displayType === 'execution-error') {
        terminalRows.push(r)
        continue
      }
      const placeholder = r.dbMsgId != null ? placeholderById.get(r.dbMsgId) : undefined
      if (placeholder) {
        result.push(placeholder)
        continue
      }
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

    // 未匹配占位行分流（修复「human 气泡显示在最新 AI 回复之后」）：
    //
    // 缺陷背景：尾部反向对齐按 role 逐位消费，本轮 AI 渲染组（ai+tool 合并组）
    // 夹在 human 与 AI 占位行之间时 human 占位必然失配；长轮次（>100 条落库行）
    // 增量窗口（limit=100）不含 human 行时同样失配。失配占位被 push 到列表
    // 最底部 → 用户消息显示在最新 AI 回复之后（刷新后按 DB 全量重建才恢复）。
    //
    // human 占位行：
    // - 本轮 DB 已有同内容行（长轮次窗口截断场景）→ 去重丢弃（DB 行已按正确
    //   时序渲染，占位行再追加只会造成重复+错位）
    // - 无同内容行 → 前插到本轮首个新增渲染行之前（human 语义先于本轮 AI 回复）
    // AI 占位行：保持尾部追加（未落库的流式思考内容属本轮终态）
    const matchedSet = new Set(matchedPlaceholders)
    const unmatchedPlaceholders = placeholders.filter(ph => !matchedSet.has(ph))
    const trailingAiPlaceholders: StreamingMessage[] = []
    if (unmatchedPlaceholders.length > 0) {
      const firstFreshIdx = result.findIndex(r => isFreshRow(r))
      let insertAt = firstFreshIdx === -1 ? result.length : firstFreshIdx
      for (const ph of unmatchedPlaceholders) {
        if (ph.role === 'human') {
          const dupInFresh = freshRows.some(
            r => r.role === 'human' && isFreshRow(r) && r.content === ph.content
          )
          if (!dupInFresh) {
            result.splice(insertAt, 0, ph)
            insertAt++
          }
        } else {
          trailingAiPlaceholders.push(ph)
        }
      }
    }
    // 错误卡（本轮终态）压轴：先于未匹配占位行（AI 流式思考）之上仍是其历史，
    // 与未匹配占位行同属"DB 未沉淀的尾部状态"
    result.push(...trailingAiPlaceholders, ...terminalRows)
    chatMessages.value = result

    // 流式上下文只在终态清空：流式中（preserveStreaming=true）的 rebuild 来自
    // loadMore 前插/重连即时合并，直播仍在继续——此处清 currentSegmentType 会让
    // 下一个 thinking/content chunk 被误判「新块」另建分段（实测每次上翻多一个
    // 空思考块：loadMore 前正文在 thinking 块中，rebuild 清态后 chunkLen 只剩
    // 尾巴的碎片块紧随其后）。终态（preserve=false）走 DB 全量重建，缓冲必须清。
    if (!preserveStreaming) {
      thinkingContent.value = ''
      textContent.value = ''
      currentSegmentType.value = null
      isStreaming.value = false
    }

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

  /**
   * 回合终态（flow_done / error）后的侧边栏标题对账：重拉会话列表。
   *
   * 后端在首轮开始时即把真实标题落库，而列表可能是「新建会话」时加载的
   * （此时标题还是「新对话」）。仅凭 wasFirstMessage（发送时刻快照）判断
   * 会漏掉重连场景：切会话回来/刷新页面后重连的流 wasFirstMessage 恒为
   * false，flow_done 后不再重拉，侧边栏将无限期停留在「新对话」。
   * 因此补充条件：列表条目缺失或仍是默认标题「新对话」时也重拉。
   */
  function reloadSidebarIfTitleStale(context: AgentStreamContext): void {
    if (!isCurrentStream(context)) return
    const listEntry = sessions.value.find(s => s.id === context.sessionId)
    if (context.wasFirstMessage || listEntry?.title === '新对话') {
      void loadSessions(context.agentId, sessionPage.value)
    }
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
      // 等待用户输入：停表（等待时间不计入本轮时长）；resume 后重新起表
      stopElapsedTimer()
      isWaitingHuman.value = true
      currentWaitData.value = event.data.wait_data || {
        type: 'human',
        node_key: event.data.node_key || '',
        question: event.data.question || '请提供输入',
        context: event.data.context
      }
      // 即时把列表图标切到等待态（呼吸灯），不等 2s 批量轮询
      const target = sessions.value.find(s => s.id === context.sessionId)
      if (target) target.waiting = true
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
        appendThinking(event.data.content || '', Number(event.id) || undefined)
      },
      onNodeContent: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        appendContent(event.data.content || '', Number(event.id) || undefined)
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
          event.data.tool_call_id,
          Number(event.id) || undefined
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
        // 会话累计 = 服务端基线 + 本轮 SSE 增量（轮次结束重拉基线后清零）
        streamRunTokens.value += event.data.total_tokens || 0
      },
      onTodoUpdate: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        const newTodos = (event.data.todos || []) as TodoItem[]
        const eventId = Number(event.id) || undefined
        updateTodos(newTodos)
        addTodoSegment(newTodos, eventId, contentWatermarkEventId.value)
      },
      onContentWatermark: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        // 内容回放水位实时同步：≤ 水位的段来源事件必已落库（DB 行渲染）
        const watermark = Number(event.data.watermark_event_id) || 0
        if (watermark > (contentWatermarkEventId.value ?? 0)) {
          contentWatermarkEventId.value = watermark
        }
      },
      onKnowledgeCitations: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        addKnowledgeCitations(event.data.citations || [])
      },
      onWaitingHuman: applyWaitingHuman,
      onToolApproval: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        const approvalId = String(event.data.approval_id || '')
        // total 字段保留兼容旧版后端回放；方案B 批次合并后 N/M 指示器不再展示
        // （同轮工具合并为单批次，无排队概念），仅记录不驱动 UI
        const totalFromEvent = Number(event.data.total) || 0
        if (totalFromEvent > 0 && approvalProgress.value.total === 0) {
          approvalProgress.value = { current: 1, total: totalFromEvent }
        }
        if (!approvalId) {
          // 后端未提供 approval_id（极老的事件回放），按空字符串兼容：直接替换当前批次
          currentApprovalId.value = '__legacy__'
          pendingApprovals.set('__legacy__', {
            toolCalls: (event.data.tool_calls || []) as PendingToolCall[],
            approvalNeeded: (event.data.approval_needed || []) as string[]
          })
        } else {
          // 同一 approval_id 重复到达（重连回放）原地更新；新 id 入队
          pendingApprovals.set(approvalId, {
            toolCalls: (event.data.tool_calls || []) as PendingToolCall[],
            approvalNeeded: (event.data.approval_needed || []) as string[]
          })
          if (!currentApprovalId.value) {
            currentApprovalId.value = approvalId
          }
        }
        // 子Agent审批路由信息挂到当前批次（提交时按 approvalId 精确路由到子会话）
        if (event.data.is_sub_agent && approvalId) {
          pendingApprovals.get(approvalId)!.subAgent = {
            agentId: event.data.sub_agent_id,
            sessionId: event.data.sub_session_id,
            agentName: event.data.sub_agent_name || '子Agent'
          }
        }
        // 后端权威剩余秒数（live 发布时=完整超时，刷新重连回放时由服务端按死线
        // 重算），缺失时回退常量——保证前端倒计时与后端超时死线不脱节。
        // 死线按批次记录（不能写 currentApprovalId 对应批次：并发到达时
        // current 始终指向第 1 批，后续批次的 deadline 会全部错挂到第 1 批）
        const expiresIn = Math.floor(Number(event.data.expires_in) || 0)
        const seconds = expiresIn > 0 ? expiresIn : USER_RESPONSE_COUNTDOWN_SECONDS
        const targetId = approvalId || '__legacy__'
        pendingApprovals.get(targetId)!.deadline = Date.now() + seconds * 1000
        startApprovalCountdown()
      },
      onQuestionRequest: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        const questionId = String(event.data.question_id || '')
        const expiresIn = Math.floor(Number(event.data.expires_in) || 0)
        const q: PendingQuestion = {
          questionId,
          nodeKey: event.data.node_key || '',
          question: event.data.question || '',
          header: event.data.header ?? null,
          options: (event.data.options || []) as QuestionOption[],
          multiple: Boolean(event.data.multiple),
          expiresIn: expiresIn > 0 ? expiresIn : undefined
        }
        // 入队：同一 question_id 重复到达原地更新；新 id 加入队列
        pendingQuestions.set(questionId, q)
        if (!currentQuestionId.value) {
          currentQuestionId.value = questionId
        }
        // 子Agent反问路由信息挂到当前条目（resolve 时按 questionId 精确路由到子会话）
        if (event.data.is_sub_agent && questionId) {
          pendingQuestions.get(questionId)!.subAgent = {
            agentId: event.data.sub_agent_id || 0,
            sessionId: event.data.sub_session_id || 0,
            agentName: event.data.sub_agent_name || '子Agent'
          }
        }
      },
      onFileChanged: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        onFileChanged(event)
      },
      onSubAgentProgress: (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        if (!event.data.node_key) return
        const toolKey = `call_sub_agent_${event.data.node_key}`
        // 工具调用状态（tool_name 字段由后端按需携带：非空=正在调用，空串=清除）；
        // 纯状态事件无 content，处理后即返回，不触碰 liveOutput 快照
        if (event.data.tool_name !== undefined) {
          updateToolLiveTool(toolKey, event.data.tool_name)
          if (!event.data.content) return
        }
        const content = event.data.content || ''
        if (!content) return
        // 写入对应 call_sub_agent_* 工具分段的实时输出区（快照替换，重连回放幂等）
        updateToolLiveOutput(
          toolKey,
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
          flow_type: (event.data.flow_type as 'flow' | 'agent' | undefined) || undefined,
          action: event.data.action,
          nodes: event.data.nodes as Record<string, unknown>[] | undefined,
          edges: event.data.edges as Record<string, unknown>[] | undefined,
          deleted: event.data.action === 'delete'
        }
        // 新预览推入时展开（若处于上一回合折叠态）
        flowPreviewCollapsed.value = false
      },
      onFlowDone: async (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        // 回合结束自动折叠流程预览卡：消除画布在输入框上方悬空钉底的观感；
        // 折叠为单行条保留编辑入口，用户点击或下轮 flow_preview 再展开
        if (flowPreview.value) {
          flowPreviewCollapsed.value = true
        }
        // [FLOW-END] 结束帧合并修复：日志实证 T2→T3 的网络往返（~100ms）期间
        // 「isStreaming=false 的 UI 中间态」已被浏览器绘制（waiting-dots 卸载/
        // 操作按钮出现/markdown 退出流式态 → 第一次重排「一下」），随后 rebuild
        // 行高修正触发第二次反向重排「一上」→ 抖动。
        // 修复：先发起 DB 拉取（DOM 保持流式末态不动），数据就绪后在同一微任务内
        // 同步执行 flush+翻转+rebuild，浏览器只绘制一次合并后的终态。
        //
        // replace=true 以 DB 最新一页重置原始行与分页状态；preserveStreaming=true
        // 走 rebuildChatMessages 就地 diff：占位气泡保留原 id 过继 dbMsgId，DOM 不重挂；
        // incrementalOnly=true 增量拉取本轮新增，避免全量重置把未加载的更早历史
        // 插入头部顶跑用户上滚查看的视口
        const afterId = messages.value.at(-1)?.id ?? undefined
        let fresh: { list: AgentMessage[]; total: number } | null = null
        try {
          const res = await agentApi.getMessages(
            context.agentId,
            context.sessionId,
            undefined,
            MESSAGE_REFRESH_LIMIT,
            afterId
          )
          if (res.data.code === 1 && isCurrentStream(context)) {
            fresh = { list: res.data.data?.list || [], total: res.data.data?.total || 0 }
          }
        } catch (e) {
          console.error('[onFlowDone] 刷新消息失败', e)
        }
        // ---- 数据就绪：以下全部同步执行，中间不让出主线程（关键） ----
        stopStreaming()
        // 定格计时：优先后端权威值（flow_done 携带，含取消轮），无则保留跳动值
        const finalDurationMs =
          typeof event.data.duration_ms === 'number' ? event.data.duration_ms : undefined
        stopElapsedTimer(finalDurationMs)
        // 权威值同步写入当前 AI 占位行：头部「耗时 X」立即显示，不等 DB 刷新回填
        if (finalDurationMs !== undefined) {
          for (let i = chatMessages.value.length - 1; i >= 0; i--) {
            const m = chatMessages.value[i]
            if (
              m.role === 'ai' &&
              m.displayType !== 'context-summary' &&
              m.displayType !== 'execution-error'
            ) {
              if (!m.duration_ms) m.duration_ms = finalDurationMs
              break
            }
          }
        }
        isCompressing.value = false
        if (isWaitingToolApproval.value) {
          pendingApprovals.clear()
          currentApprovalId.value = null
          approvalProgress.value = { current: 0, total: 0 }
          stopApprovalCountdown()
          ElMessage.warning({ message: '工具确认超时，连接已断开', duration: 5000 })
        }
        // 问题反问兜底清理：正常回答时 resolveQuestion 已清空；后端超时/异常结束时
        // 前端弹窗不会被动清除，避免留下无效弹窗（subAgentQuestion 为 computed，
        // 随队列清空自动派生 null，无需手动清理）
        pendingQuestions.clear()
        currentQuestionId.value = null
        if (isResume) {
          isResume = false
        }
        if (fresh) {
          applyLatestMessages(fresh.list, fresh.total, true, true)
          messageRefreshVersion.value++
        }
        // 轮次结束：重拉服务端累计 token 基线对齐落库终值，并清零本轮增量（防 SSE 丢事件漂移）
        void fetchSessionTotalTokens(context.agentId, context.sessionId, true)
        // 流正常结束：立即熄灭列表图标并同步轮询（有其他运行中会话则续表）
        setSessionRunningLocal(context.agentId, context.sessionId, false)
        // 用户正看着本会话：清除后端完成未读标记（否则刷新后红点误亮）
        clearFinishUnreadIfCurrent(context.agentId, context.sessionId)
        // 终态标题对账：首轮发送或列表条目仍是「新对话」（重连场景）时重拉列表
        reloadSidebarIfTitleStale(context)
      },
      onLlmRetry: createOnLlmRetryHandler(),
      onError: async (event: SSEEvent) => {
        if (!isCurrentStream(context)) return
        stopStreaming()
        stopElapsedTimer()
        isCompressing.value = false
        // 流异常结束：立即熄灭列表图标（waiting_human 除外——等待人工输入仍算对话中）
        setSessionRunningLocal(context.agentId, context.sessionId, false)
        // 错误终态同样清除观看中会话的完成未读（错误不算「新完成」红点）
        clearFinishUnreadIfCurrent(context.agentId, context.sessionId)
        if (isWaitingToolApproval.value) {
          pendingApprovals.clear()
          currentApprovalId.value = null
          approvalProgress.value = { current: 0, total: 0 }
          stopApprovalCountdown()
        }
        pendingQuestions.clear()
        currentQuestionId.value = null
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
            // 错误实为等待人工输入（interrupt）：会话仍在对话中，图标保持点亮
            setSessionRunningLocal(context.agentId, context.sessionId, true)
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

  // ========== 发送消息 ==========

  /**
   * 发送消息
   */
  function sendMessage(
    content: string,
    params: Record<string, unknown> = {},
    files?: MessageFile[],
    model?: string,
    provider?: string,
    reasoning?: string
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
    // 乐观移除旧错误卡片：重新发送即视为开启新一轮，不等后端软删生效
    if (messages.value.some(m => m.message_type === EXECUTION_ERROR_MESSAGE_TYPE)) {
      messages.value = messages.value.filter(
        m => m.message_type !== EXECUTION_ERROR_MESSAGE_TYPE
      )
      rebuildChatMessages(true)
    }
    addUserMessage(content, files)
    startStreaming()
    startElapsedTimer()
    // 新轮次开始：增量从零累计（基线 serverTotalTokens 保持服务端值）
    streamRunTokens.value = 0
    // 立即点亮列表图标并启动轮询（不等 loadSessions）
    setSessionRunningLocal(context.agentId, context.sessionId, true)

    streamAbort = agentApi.chat(
      context.agentId,
      context.sessionId,
      {
        content,
        params: { ...params, __plan_mode__: planMode.value },
        model,
        provider,
        reasoning
      },
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
    // 乐观移除旧错误卡片：恢复执行即视为开启新一轮，不等后端软删生效
    if (messages.value.some(m => m.message_type === EXECUTION_ERROR_MESSAGE_TYPE)) {
      messages.value = messages.value.filter(
        m => m.message_type !== EXECUTION_ERROR_MESSAGE_TYPE
      )
      rebuildChatMessages(true)
    }
    addUserMessage(humanInput)
    isWaitingHuman.value = false
    currentWaitData.value = null
    startStreaming()
    startElapsedTimer()
    // 新轮次开始：增量从零累计（基线 serverTotalTokens 保持服务端值）
    streamRunTokens.value = 0
    // 立即点亮列表图标并启动轮询（不等 loadSessions）
    setSessionRunningLocal(context.agentId, context.sessionId, true)

    streamAbort = agentApi.resume(
      context.agentId,
      context.sessionId,
      { human_input: humanInput },
      createStreamHandlers(context)
    )
  }

  /** 启动倒计时 tick：deadline 已由 onToolApproval 挂到各批次，这里只确保秒级 tick 在跑 */
  function startApprovalCountdown() {
    if (!approvalTimer) {
      approvalTimer = setInterval(() => {
        approvalTick.value += 1
        // 过期批次自动出队（与后端同步：后端超时即从等待队列移除并按超时继续，
        // 前端过期批次留着只会让用户点出「没有待确认的工具」）
        const now = Date.now()
        for (const [id, batch] of pendingApprovals) {
          if (batch.deadline && batch.deadline <= now) {
            pendingApprovals.delete(id)
            resolvedApprovalIds.add(id)
            if (currentApprovalId.value === id) {
              advanceApprovalQueue()
            }
          }
        }
      }, 1000)
    }
  }

  /** 停止秒级 tick（队列清空时调用；不清理批次 deadline，倒计时随批次派生） */
  function stopApprovalCountdown() {
    if (approvalTimer) {
      clearInterval(approvalTimer)
      approvalTimer = null
    }
  }

  /**
   * 批准工具执行（工具确认后继续）
   * @param rememberSession 批准并记住：同会话同工具后续调用自动放行
   * @param toolDecisions 逐工具决策（方案B）：{tool_call_id: approved|rejected}；
   *   提供时按卡片独立分发，action 仅表示「是否存在拒绝项」
   */
  async function approveToolCalls(rememberSession = false, toolDecisions?: Record<string, 'approved' | 'rejected'>) {
    if (!currentAgent.value || !currentSession.value) return
    const approvalId = currentApprovalId.value
    // 幂等保护：同一 approval_id 已提交过则直接跳过
    if (!approvalId || resolvedApprovalIds.has(approvalId)) return
    // 先从被批准批次本身捕获路由信息（不能读 subAgentApproval computed——
    // 它派生自 currentApprovalId，advance 之后已指向下一批/空，会路由错 session）
    const subAgent = pendingApprovals.get(approvalId)?.subAgent
    resolvedApprovalIds.add(approvalId)
    pendingApprovals.delete(approvalId)
    advanceApprovalQueue()
    // 队列清空才停 tick；还有后续批次时倒计时自动跟随下一批 deadline
    if (!currentApprovalId.value) stopApprovalCountdown()
    try {
      if (subAgent) {
        await agentApi.toolApproval(subAgent.agentId, subAgent.sessionId, approvalId, 'approved', rememberSession, toolDecisions)
      } else {
        await agentApi.toolApproval(
          currentAgent.value.id,
          currentSession.value.id,
          approvalId,
          'approved',
          rememberSession,
          toolDecisions
        )
      }
    } catch {
      // error handled by interceptor
    }
  }

  async function rejectToolCalls(toolDecisions?: Record<string, 'approved' | 'rejected'>) {
    if (!currentAgent.value || !currentSession.value) return
    const approvalId = currentApprovalId.value
    if (!approvalId || resolvedApprovalIds.has(approvalId)) return
    // 同 approveToolCalls：推进队列前先捕获本批次路由信息
    const subAgent = pendingApprovals.get(approvalId)?.subAgent
    resolvedApprovalIds.add(approvalId)
    pendingApprovals.delete(approvalId)
    advanceApprovalQueue()
    if (!currentApprovalId.value) stopApprovalCountdown()
    try {
      if (subAgent) {
        await agentApi.toolApproval(subAgent.agentId, subAgent.sessionId, approvalId, 'rejected', false, toolDecisions)
      } else {
        await agentApi.toolApproval(
          currentAgent.value.id,
          currentSession.value.id,
          approvalId,
          'rejected',
          false,
          toolDecisions
        )
      }
    } catch {
      // error handled by interceptor
    }
  }

  /** 提交问题反问的答案；answers 为空表示取消 */
  async function resolveQuestion(answers: string[]) {
    if (!currentAgent.value || !currentSession.value) return
    const questionId = currentQuestionId.value
    // 幂等保护：当前 questionId 已提交过则直接跳过
    if (!questionId || resolvedQuestionIds.has(questionId)) return
    if (!pendingQuestions.has(questionId)) return
    // 先从被回答条目本身捕获路由信息（不能读 subAgentQuestion computed——
    // advance 之后 currentQuestionId 已指向下一批/空，会路由错 session）
    const subAgent = pendingQuestions.get(questionId)?.subAgent
    resolvedQuestionIds.add(questionId)
    pendingQuestions.delete(questionId)
    advanceQuestionQueue()
    try {
      if (subAgent) {
        await agentApi.resolveQuestion(
          subAgent.agentId,
          subAgent.sessionId,
          questionId,
          answers
        )
      } else {
        await agentApi.resolveQuestion(
          currentAgent.value.id,
          currentSession.value.id,
          questionId,
          answers
        )
      }
    } catch {
      // 失败时恢复弹窗（让用户重试）
      pendingQuestions.set(questionId, {
        questionId,
        nodeKey: '',
        question: '',
        header: null,
        options: [],
        multiple: false
      })
      resolvedQuestionIds.delete(questionId)
      currentQuestionId.value = questionId
    }
  }

  /**
   * 问题倒计时归零：本地关闭当前问题弹窗（仅删除该 questionId），不调 resolve。
   * 后端超时后会自行向 LLM 返回过期错误并继续执行。
   * 队列中其他问题自动顶上。
   */
  function dismissExpiredQuestion() {
    const questionId = currentQuestionId.value
    if (!questionId) return
    pendingQuestions.delete(questionId)
    advanceQuestionQueue()
  }

  // ===== 文件变更（侧栏 Diff 面板） =====

  /**
   * 分页拉取当前会话的文件变更（后端 id 倒序，最新在前）
   */
  async function fetchFileChanges(limit = 50) {
    if (!currentSession.value) return
    const reqSeq = ++fileChangesReqSeq
    fileChangesLoading.value = true
    try {
      const res = await agentApi.pageFileChanges(currentSession.value.id, 1, limit)
      if (res.data.code === 1 && reqSeq === fileChangesReqSeq) {
        fileChanges.value = (res.data.data?.items || []) as AgentFileChangeBase[]
      }
    } catch {
      // ignore，错误条已由 interceptor 弹
    } finally {
      if (reqSeq === fileChangesReqSeq) fileChangesLoading.value = false
    }
  }

  /**
   * 打开某条变更的 diff 详情（拉取 backup + current 内容）
   */
  async function openFileChangeDiff(changeId: number) {
    if (!currentSession.value) return
    activeFileChangeId.value = changeId
    activeFileChangeDiff.value = null
    activeFileChangeDiffLoading.value = true
    try {
      const res = await agentApi.getFileChangeDiff(changeId)
      if (res.data.code === 1 && res.data.data) {
        const data = res.data.data
        activeFileChangeDiff.value = {
          backup_content: data.backup_content || '',
          current_content: data.current_content || '',
          is_binary: !!data.is_binary,
          backup_missing: !!data.backup_missing,
          file_path: data.file_path,
          change_type: data.change_type
        }
      } else {
        activeFileChangeId.value = null
      }
    } catch {
      activeFileChangeId.value = null
    } finally {
      activeFileChangeDiffLoading.value = false
    }
  }

  /** 关闭 diff 详情 */
  function closeFileChangeDiff() {
    activeFileChangeId.value = null
    activeFileChangeDiff.value = null
  }

  /**
   * 撤销单条文件变更（本地乐观更新 + 后端实际恢复）
   */
  async function revertFileChangeById(changeId: number) {
    try {
      await agentApi.revertFileChange(changeId)
      lastRevertedChangeId.value = changeId
      // 乐观更新：本地标 is_reverted
      fileChanges.value = fileChanges.value.map(c =>
        c.id === changeId ? { ...c, is_reverted: 1 } : c
      )
      if (activeFileChangeId.value === changeId) {
        closeFileChangeDiff()
      }
      ElMessage.success({ message: '已撤销该文件变更', duration: 3000 })
    } catch {
      // ignore
    }
  }

  /**
   * SSE file_changed 事件：实时增量更新列表（与后端「每次成功改动一条记录」口径一致）。
   * 仅按 change_id 幂等去重（SSE 重连会回放事件，重复投递需忽略）；不按 file_path
   * 替换——同一文件的多次编辑是可独立回退的多条记录，按文件聚合会让早期记录从
   * 面板消失，badge 数量也与后端拉取口径不一致
   */
  function onFileChanged(event: SSEEvent) {
    const data = event.data
    const changeId = (data.change_id as number) ?? 0
    const newItem: AgentFileChangeBase = {
      id: changeId,
      file_path: (data.file_path as string) || '',
      change_type: ((data.change_type as string) || 'modify') as AgentFileChangeBase['change_type'],
      tool_name: (data.tool_name as string) || '',
      create_time: (data.create_time as string) || new Date().toISOString(),
      is_reverted: 0,
      has_backup: data.change_type !== 'delete'
    }
    if (!newItem.file_path) return
    // 重连回放幂等：同 change_id 已存在则忽略
    if (changeId > 0 && fileChanges.value.some(c => c.id === changeId)) return
    fileChanges.value = [newItem, ...fileChanges.value]
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
    // 停止计时（手动中断轮次，时长以后端取消路径落库值为准）
    stopElapsedTimer()
    if (!waitForSave) {
      stopStreaming()
      isStopping.value = false
    }
    isWaitingHuman.value = false
    currentWaitData.value = null
    pendingApprovals.clear()
    currentApprovalId.value = null
    approvalProgress.value = { current: 0, total: 0 }
    pendingQuestions.clear()
    currentQuestionId.value = null
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
          // 后台会话结束：同步熄灭列表图标（有其他运行中会话则续表）
          setSessionRunningLocal(agentId, sessionId, false)
          // 用户已切回该会话观看：清除完成未读（否则刷新后红点误亮）
          clearFinishUnreadIfCurrent(agentId, sessionId)
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
   * @param restoreFiles 是否同步恢复该范围内追踪到的文件变更
   * @returns 被删除的用户消息 {content, files, input_data, reverted_files}，用于回退恢复
   */
  async function deleteMessagesFrom(
    messageId: number,
    restoreFiles: boolean = true
  ): Promise<AgentDeleteMessagesResult | null> {
    if (!currentAgent.value || !currentSession.value) return null

    try {
      const res = await agentApi.deleteMessagesFrom(
        currentAgent.value.id,
        currentSession.value.id,
        messageId,
        restoreFiles
      )
      if (res.data.code === 1) {
        const deleted = res.data.data
        const beforeCount = messages.value.length
        messages.value = messages.value.filter(m => m.id < messageId)
        // 撤回时流式必然已结束（上层 isStreaming 守卫），此刻所有 dbMsgId==null 的
        // user-/streaming- 占位行都是中断轮次的孤儿（对应 DB 行已被删或从未落库），
        // 保留会在尾部渲染幽灵 thinking/内容段（与 clearOrphanPlaceholders 同口径，
        // 但清理时机提前到撤回——下次 sendMessage 才清来不及）
        chatMessages.value = chatMessages.value.filter(
          m => !(m.dbMsgId == null && (m.id.startsWith('user-') || m.id.startsWith('streaming-')))
        )
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
    stopElapsedTimer()
    // 计划模式 / 工作路径的会话暂存值一并复位（有会话时标志随 session 字段走）
    pendingPlanMode.value = false
    pendingWorkDir.value = ''
    messages.value = []
    clearMessages()
    streamBaseMsgId = 0
    serverTotalTokens.value = 0
    streamRunTokens.value = 0
    flowPreview.value = null
    flowPreviewCollapsed.value = false
    isWaitingHuman.value = false
    currentWaitData.value = null
    sessionPage.value = 1
    sessionTotal.value = 0
    cancelStream()
    stopCompressPolling()
    stopRunningPolling()
    stopSessionStatusPolling()
    // 文件变更 Diff：彻底清空（序号自增使在途回包失效）
    fileChanges.value = []
    fileChangesReqSeq++
    fileChangesLoading.value = false
    activeFileChangeId.value = null
    activeFileChangeDiff.value = null
    activeFileChangeDiffLoading.value = false
    lastRevertedChangeId.value = null
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
    fetchSessionTotalTokens,
    latestPromptTokens,
    // 本轮执行计时
    streamElapsedMs,
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
    pendingApprovals,
    currentApprovalId,
    approvalProgress,
    approvalCountdown,
    subAgentApproval,
    subAgentQuestion,
    pendingQuestion,
    pendingQuestions,
    currentQuestionId,
    // 文件变更 Diff
    fileChanges,
    fileChangesCount,
    fileChangesLoading,
    activeFileChangeId,
    activeFileChangeDiff,
    activeFileChangeDiffLoading,
    lastRevertedChangeId,
    isCompressing,
    isStopping,
    flowPreview,
    flowPreviewCollapsed,
    planMode,
    pendingPlanMode,
    pendingWorkDir,
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
    deleteSessionsBatch,
    selectSession,
    restoreLastSession,
    loadMoreMessages,
    sendMessage,
    resumeWithInput,
    togglePlanMode,
    updateSessionChatModel,
    approveToolCalls,
    rejectToolCalls,
    resolveQuestion,
    dismissExpiredQuestion,
    // 文件变更
    fetchFileChanges,
    openFileChangeDiff,
    closeFileChangeDiff,
    revertFileChangeById,
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
