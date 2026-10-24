/**
 * 流式消息处理 Hook
 * @description 管理流式消息的接收、分段和显示
 */

import { ref, computed, triggerRef, type Ref } from 'vue'
import type { KnowledgeReference } from '@/types/knowledge'
import type { Segment, ToolCall, TodoItem, SegmentType } from '@/types/segment'
import {
  updateThinking,
  updateContent,
  addTool as addToolToSegments,
  updateTool as updateToolInSegments,
  attachKnowledgeCitations,
  genSegmentId
} from '@/composables/useSegmentBuilder'
import { SSE_FLUSH_INTERVAL } from '@/constants/timing'

export type { Segment, ToolCall, TodoItem, SegmentType }
export type MessageSegment = Segment
export type StreamingContentSegment = Segment

/** 附件文件信息 */
export interface MessageFile {
  id: number
  original_name: string
  mime_type: string
}

/** 流式消息 */
export interface StreamingMessage {
  id: string
  /** 关联的 DB 消息 id。id 含流式前缀时通过该字段定位 DB 行 */
  dbMsgId?: number
  role: 'human' | 'ai'
  displayType?: 'context-summary' | 'execution-error'
  removedCount?: number
  content: string
  thinking?: string
  tools?: ToolCall[]
  segments: Segment[]
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  files?: MessageFile[]
  /** 结束节点输出（该轮 AI 消息携带，前端按钮查看） */
  end_output?: Record<string, unknown>
  /** 本轮执行耗时毫秒（该轮最后一条 AI 行携带；流式中由 store 计时器逐步填充展示） */
  duration_ms?: number
  createdAt: Date
}

/** 流式消息状态 */
export interface StreamingState {
  currentSegmentType: Ref<SegmentType | null>
  thinkingContent: Ref<string>
  textContent: Ref<string>
  isStreaming: Ref<boolean>
}

/**
 * 流式消息处理 Hook
 * @description 管理流式消息的接收、累积和显示
 */
export function useStreamingMessage() {
  const currentSegmentType = ref<SegmentType | null>(null)
  const thinkingContent = ref('')
  const textContent = ref('')
  const isStreaming = ref(false)
  const todos = ref<TodoItem[]>([])
  /**
   * 内容回放水位（SSE 事件 id）：≤ 水位的段来源事件必已落库（DB 行渲染）。
   * 数据源优先级：content_watermark 事件（实时）→ running_status 权威值
   * （重连初始化）→ undefined（旧 run 兜底，不启用回声拦截）。
   */
  const contentWatermarkEventId = ref<number | undefined>(undefined)

  const messages = ref<StreamingMessage[]>([])
  const latestPromptTokens = ref(0)

  /** 待 flush 的 content/thinking 缓冲（非响应式，同一时刻至多一种非空） */
  let pendingContent: string | null = null
  let pendingThinking: string | null = null
  /** 缓冲对应的首个 SSE 事件 id（水位互斥判定：段 srcEventId ≤ 水位 = 已落库） */
  let pendingContentEventId: number | null = null
  let pendingThinkingEventId: number | null = null
  let flushTimer: ReturnType<typeof setTimeout> | null = null

  const lastMessage = computed(() => {
    return messages.value.length > 0 ? messages.value[messages.value.length - 1] : null
  })

  /**
   * 将缓冲的 thinking/content 应用到当前流式消息（重建 segments，与原逐 chunk 逻辑一致）
   */
  function applyThinking(chunk: string, eventId?: number): void {
    if (!chunk) return
    // 空白 chunk（网关空占位/纯换行）不新开思考块，避免渲染出空「思考过程」框；
    // 已在 thinking 块中则照常累积，保留模型思考里的换行排版
    const isNewBlock = currentSegmentType.value !== 'thinking'
    if (isNewBlock && !chunk.trim()) return
    if (isNewBlock) {
      currentSegmentType.value = 'thinking'
      thinkingContent.value = chunk
    } else {
      thinkingContent.value += chunk
    }
    const msg = getOrCreateStreamingMessage()
    if (isNewBlock) {
      msg.segments = [
        ...msg.segments,
        {
          type: 'thinking',
          thinking: thinkingContent.value,
          srcEventId: eventId,
          id: genSegmentId()
        }
      ]
    } else {
      msg.segments = updateThinking(msg.segments, thinkingContent.value)
      // 原地更新的段补标来源事件 id（块首段已在创建时打标，此处兜底续传 chunk）
      const last = msg.segments[msg.segments.length - 1]
      if (last?.type === 'thinking' && last.srcEventId == null && eventId != null) {
        last.srcEventId = eventId
      }
    }
    msg.thinking = thinkingContent.value
  }

  /**
   * 将缓冲的 content 应用到当前流式消息
   */
  function applyContent(chunk: string, eventId?: number): void {
    const isNewBlock = currentSegmentType.value !== 'content'
    if (isNewBlock) {
      currentSegmentType.value = 'content'
      textContent.value = chunk
    } else {
      textContent.value += chunk
    }
    const msg = getOrCreateStreamingMessage()
    if (isNewBlock) {
      msg.segments = [
        ...msg.segments,
        {
          type: 'content',
          content: textContent.value,
          srcEventId: eventId,
          id: genSegmentId()
        }
      ]
    } else {
      msg.segments = updateContent(msg.segments, textContent.value)
      const last = msg.segments[msg.segments.length - 1]
      if (last?.type === 'content' && last.srcEventId == null && eventId != null) {
        last.srcEventId = eventId
      }
    }
    msg.content = textContent.value
  }

  /**
   * 立即 flush 缓冲的 chunk（取消定时器）
   */
  function flushPending(): void {
    if (flushTimer) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
    if (pendingThinking !== null) {
      const chunk = pendingThinking
      const eventId = pendingThinkingEventId
      pendingThinking = null
      pendingThinkingEventId = null
      applyThinking(chunk, eventId ?? undefined)
    }
    if (pendingContent !== null) {
      const chunk = pendingContent
      const eventId = pendingContentEventId
      pendingContent = null
      pendingContentEventId = null
      applyContent(chunk, eventId ?? undefined)
    }
  }

  /**
   * 丢弃未 flush 的缓冲（清空消息场景使用）
   */
  function discardPending(): void {
    pendingContent = null
    pendingThinking = null
    pendingContentEventId = null
    pendingThinkingEventId = null
    if (flushTimer) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
  }

  function scheduleFlush(): void {
    if (flushTimer) return
    flushTimer = setTimeout(() => {
      flushTimer = null
      flushPending()
    }, SSE_FLUSH_INTERVAL)
  }

  /**
   * 获取或创建正在流式输出的消息
   */
  function getOrCreateStreamingMessage(): StreamingMessage {
    let lastMsg = messages.value[messages.value.length - 1]
    if (!lastMsg || lastMsg.role !== 'ai' || lastMsg.displayType === 'context-summary') {
      lastMsg = {
        id: `streaming-${Date.now()}`,
        role: 'ai',
        content: '',
        segments: [],
        createdAt: new Date()
      }
      messages.value.push(lastMsg)
    }
    return lastMsg
  }

  /**
   * 添加用户消息
   */
  function addUserMessage(content: string, files?: MessageFile[]): void {
    messages.value.push({
      id: `user-${Date.now()}`,
      role: 'human',
      content,
      segments: [{ type: 'content', content }],
      files,
      createdAt: new Date()
    })
  }

  /**
   * 开始流式输出
   */
  function startStreaming(forceNewMessage = false): void {
    isStreaming.value = true
    currentSegmentType.value = null
    thinkingContent.value = ''
    textContent.value = ''
    if (forceNewMessage) {
      messages.value.push({
        id: `streaming-${Date.now()}`,
        role: 'ai',
        content: '',
        segments: [],
        createdAt: new Date()
      })
    }
  }

  /**
   * 追加思考内容（缓冲攒批，SSE_FLUSH_INTERVAL 后统一应用）
   *
   * 新思考块的首个 chunk（currentSegmentType 从其他类型切换而来）在 flush 时追加新分段，
   * 避免 updateThinking 的前缀匹配命中上一轮的旧 thinking 分段（位于 tool 之前），
   * 导致新思考内容错误地替换旧位置、打乱 tool 与 thinking 的先后顺序。
   * 续传 chunk（同一 thinking 块的后续增量）走 updateThinking 原地更新。
   * 切换分段类型时（如 content → thinking）先 flush 保证分段顺序。
   */
  function appendThinking(chunk: string, eventId?: number): void {
    if (pendingContent !== null) flushPending()
    if (pendingThinking === null) {
      pendingThinking = chunk
      pendingThinkingEventId = eventId ?? null
      scheduleFlush()
    } else {
      pendingThinking += chunk
    }
  }

  /**
   * 追加文本内容（缓冲攒批，SSE_FLUSH_INTERVAL 后统一应用）
   *
   * 新内容块的首个 chunk（currentSegmentType 从其他类型切换而来）在 flush 时追加新分段，
   * 避免 updateContent 的前缀匹配命中上一轮的旧 content 分段（位于 tool 之前）。
   * 切换分段类型时（如 thinking → content）先 flush 保证分段顺序。
   */
  function appendContent(chunk: string, eventId?: number): void {
    if (pendingThinking !== null) flushPending()
    if (pendingContent === null) {
      pendingContent = chunk
      pendingContentEventId = eventId ?? null
      scheduleFlush()
    } else {
      pendingContent += chunk
    }
  }

  /**
   * 添加工具调用分段
   */
  function addToolSegment(
    name: string,
    args?: Record<string, unknown>,
    status: 'running' | 'success' | 'error' = 'running',
    toolCallId?: string,
    eventId?: number
  ): void {
    // 工具分段必须排在已缓冲的 thinking/content 之后
    flushPending()
    currentSegmentType.value = 'tool'
    const msg = getOrCreateStreamingMessage()
    const segments = addToolToSegments(msg.segments, name, args, status, undefined, toolCallId)
    // 新增段打标来源事件 id（addTool 恒为追加，末段即新段）
    const last = segments[segments.length - 1]
    if (last && eventId != null) last.srcEventId = eventId
    msg.segments = segments

    if (!msg.tools) {
      msg.tools = []
    }
    msg.tools.push({ name, args, status, id: toolCallId })
  }

  /**
   * 更新工具调用结果
   */
  function updateToolSegment(
    name: string,
    status: 'running' | 'success' | 'error',
    result?: unknown,
    toolCallId?: string
  ): void {
    // 确保缓冲内容先落地，避免 tool 结果与 content 尾部显示不同步
    flushPending()
    const msg = messages.value[messages.value.length - 1]
    if (msg?.role !== 'ai' || !msg.segments) return

    msg.segments = updateToolInSegments(msg.segments, name, status, result, toolCallId)

    if (msg.tools) {
      // 优先按 ID 精确匹配（同名并行工具结果不错位），无 ID 回退名称匹配
      const tool = toolCallId
        ? [...msg.tools].reverse().find(t => t.id === toolCallId && t.status === 'running')
        : [...msg.tools].reverse().find(t => t.name === name && t.status === 'running')
      if (tool) {
        tool.status = status
        if (result !== undefined) tool.result = result
        delete tool.liveOutput
        delete tool.liveAgentName
        delete tool.liveTool
      }
    }

    triggerRef(messages)
  }

  /**
   * 更新子Agent实时输出快照（call_sub_agent_* 工具执行中嵌入展示）
   */
  function updateToolLiveOutput(name: string, content: string, agentName?: string): void {
    const msg = messages.value[messages.value.length - 1]
    if (msg?.role !== 'ai' || !msg.segments) return

    // 优先匹配带 ID 的同名并行工具段，无 ID 回退最后一个同名 running 分段
    const segment = [...msg.segments]
      .reverse()
      .find(s => s.type === 'tool' && s.tool?.name === name && s.tool?.status === 'running')
    if (!segment?.tool) return

    segment.tool.liveOutput = content
    if (agentName) segment.tool.liveAgentName = agentName
    triggerRef(messages)
  }

  /**
   * 更新子Agent正在调用的工具名快照（父Agent展示「正在调用xxx工具中」）；
   * 空串表示子Agent工具全部结束，清除状态
   */
  function updateToolLiveTool(name: string, toolName: string): void {
    const msg = messages.value[messages.value.length - 1]
    if (msg?.role !== 'ai' || !msg.segments) return

    const segment = [...msg.segments]
      .reverse()
      .find(s => s.type === 'tool' && s.tool?.name === name && s.tool?.status === 'running')
    if (!segment?.tool) return

    if (toolName) segment.tool.liveTool = toolName
    else delete segment.tool.liveTool
    triggerRef(messages)
  }

  /**
   * 将所有仍在 running 的工具分段标记为 error（手动停止场景）
   *
   * 停止后 tool_call_end SSE 不会再来，running 分段会永久转圈；
   * 终止文案与后端补写的 ToolMessage（{"success":false,"error":"执行被中断"}）保持一致，
   * 后续 save polling 合并/重发清理后由 DB 行接手，状态一致。
   */
  function failRunningToolSegments(message: string): void {
    for (const msg of messages.value) {
      if (msg.role !== 'ai' || !msg.segments) continue
      for (const seg of msg.segments) {
        if (seg.type !== 'tool' || seg.tool?.status !== 'running') continue
        seg.tool.status = 'error'
        seg.tool.result = { success: false, error: message }
        delete seg.tool.liveOutput
        delete seg.tool.liveAgentName
        delete seg.tool.liveTool
      }
      if (msg.tools) {
        for (const tool of msg.tools) {
          if (tool.status !== 'running') continue
          tool.status = 'error'
          tool.result = { success: false, error: message }
          delete tool.liveOutput
          delete tool.liveAgentName
          delete tool.liveTool
        }
      }
    }
    triggerRef(messages)
  }

  /**
   * 更新任务计划列表（供面板实时显示）
   */
  function updateTodos(newTodos: TodoItem[]): void {
    todos.value = newTodos
  }

  /**
   * 添加任务计划分段到当前消息
   *
   * 建卡条件与 DB 重建路径（buildChatMessagesFromDB）对齐：**仅当尾部工具段
   * 连续区（允许跳过并行组的其他工具段）内存在 todowrite/todoread 时**紧随其
   * 后建卡，否则只更新面板（todos ref 由调用方先行同步）。此前「收到事件无条件
   * 在行尾独立建卡」导致：重连回放时水位过滤拦掉了中间的工具事件、历史 todo
   * 事件却照发，卡片悬空挂在 browser 等无关工具后面，且与 DB 已渲染的同内容卡
   * 成对重复（实测同轮 13 项计划卡 ×2 相邻）。
   *
   * 回声拦截：段来源事件 id ≤ 内容水位（已落库，DB 行会渲染同内容卡）时跳过建卡
   * ——水位由 content_watermark 事件/running_status 权威值维护，无水位信息时
   * （旧 run 兜底）不拦截，宁多勿丢。
   */
  function addTodoSegment(
    newTodos: TodoItem[],
    eventId?: number,
    contentWatermarkEventId?: number
  ): void {
    // 回声拦截：来源事件已落库（≤ 水位）→ DB 行负责渲染，此处只同步面板
    // （判定必须先于 flushPending/currentSegmentType 变更：不建卡就绝不能动
    // 流式上下文，否则把进行中的 thinking 块截断——下一 chunk 会因
    // currentSegmentType 已是 'todo' 被误判新块，实测相邻双思考块）
    if (
      eventId != null &&
      contentWatermarkEventId != null &&
      eventId <= contentWatermarkEventId
    ) {
      return
    }
    const msg = getOrCreateStreamingMessage()
    // 建卡锚点：从尾向前找最近的 todowrite/todoread 工具段，仅跳过其间的其他
    // 工具段——并行工具组里 todowrite 先完成、其余工具后发起 tool_call_start
    // 时最后段不是 todowrite，旧「仅认最后一个 tool 段」失配直接丢卡（实测
    // todowrite 调用成功但任务计划卡不出现）。遇 content/thinking/todo 段停止：
    // 它们属于 todowrite 之后的输出，说明本轮已无待建卡锚点。
    let insertAt = -1
    for (let i = msg.segments.length - 1; i >= 0; i--) {
      const seg = msg.segments[i]
      if (seg.type !== 'tool') break
      const name = seg.tool?.name || ''
      if (name === 'todowrite' || name === 'todoread') {
        insertAt = i + 1
        break
      }
    }
    if (insertAt === -1) return
    // 判定全部通过、确认建卡：此刻才 flush（缓冲的 thinking/content 先落段）
    // 并切换段类型
    flushPending()
    currentSegmentType.value = 'todo'
    const seg: Segment = { type: 'todo', todo: [...newTodos], id: genSegmentId() }
    if (eventId != null) seg.srcEventId = eventId
    msg.segments = [...msg.segments.slice(0, insertAt), seg, ...msg.segments.slice(insertAt)]
  }

  /** Attach validated citations after all buffered answer text is visible. */
  function addKnowledgeCitations(citations: KnowledgeReference[]): void {
    if (citations.length === 0) return
    flushPending()
    const msg = getOrCreateStreamingMessage()
    msg.segments = attachKnowledgeCitations(msg.segments, citations)
    currentSegmentType.value = null
    triggerRef(messages)
  }

  function addTokenUsage(
    prompt_tokens: number,
    completion_tokens: number,
    total_tokens: number
  ): void {
    const msg = getOrCreateStreamingMessage()
    msg.prompt_tokens = prompt_tokens
    msg.completion_tokens = completion_tokens
    msg.total_tokens = total_tokens
    latestPromptTokens.value = msg.prompt_tokens
  }

  /**
   * 结束流式输出（先 flush 缓冲，确保尾部内容不丢失）
   */
  function stopStreaming(): void {
    flushPending()
    isStreaming.value = false
    currentSegmentType.value = null
  }

  /**
   * 清空所有消息（丢弃未 flush 的缓冲）
   */
  function clearMessages(): void {
    discardPending()
    messages.value = []
    thinkingContent.value = ''
    textContent.value = ''
    currentSegmentType.value = null
    isStreaming.value = false
    todos.value = []
    latestPromptTokens.value = 0
  }

  function reset(): void {
    clearMessages()
  }

  return {
    currentSegmentType,
    thinkingContent,
    textContent,
    isStreaming,
    todos,
    contentWatermarkEventId,
    messages,
    lastMessage,
    latestPromptTokens,
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
    reset
  }
}
