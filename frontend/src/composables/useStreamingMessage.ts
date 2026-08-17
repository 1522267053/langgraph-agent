/**
 * 流式消息处理 Hook
 * @description 管理流式消息的接收、分段和显示
 */

import { ref, computed, triggerRef, type Ref } from 'vue'
import type { Segment, ToolCall, TodoItem, SegmentType } from '@/types/segment'
import {
  updateThinking,
  updateContent,
  addTool as addToolToSegments,
  updateTool as updateToolInSegments,
  addTodo as addTodoToSegments
} from '@/composables/useSegmentBuilder'

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
  role: 'human' | 'ai'
  content: string
  thinking?: string
  tools?: ToolCall[]
  segments: Segment[]
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  files?: MessageFile[]
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

  const messages = ref<StreamingMessage[]>([])
  const latestPromptTokens = ref(0)

  /** 流式 chunk 攒批间隔（ms）：缓冲期内合并 chunk，降低深响应写与 segments 重建频率 */
  const FLUSH_INTERVAL = 80
  /** 待 flush 的 content/thinking 缓冲（非响应式，同一时刻至多一种非空） */
  let pendingContent: string | null = null
  let pendingThinking: string | null = null
  let flushTimer: ReturnType<typeof setTimeout> | null = null

  const lastMessage = computed(() => {
    return messages.value.length > 0 ? messages.value[messages.value.length - 1] : null
  })

  /**
   * 将缓冲的 thinking/content 应用到当前流式消息（重建 segments，与原逐 chunk 逻辑一致）
   */
  function applyThinking(chunk: string): void {
    const isNewBlock = currentSegmentType.value !== 'thinking'
    if (isNewBlock) {
      currentSegmentType.value = 'thinking'
      thinkingContent.value = chunk
    } else {
      thinkingContent.value += chunk
    }
    const msg = getOrCreateStreamingMessage()
    if (isNewBlock) {
      msg.segments = [...msg.segments, { type: 'thinking', thinking: thinkingContent.value }]
    } else {
      msg.segments = updateThinking(msg.segments, thinkingContent.value)
    }
    msg.thinking = thinkingContent.value
  }

  /**
   * 将缓冲的 content 应用到当前流式消息
   */
  function applyContent(chunk: string): void {
    const isNewBlock = currentSegmentType.value !== 'content'
    if (isNewBlock) {
      currentSegmentType.value = 'content'
      textContent.value = chunk
    } else {
      textContent.value += chunk
    }
    const msg = getOrCreateStreamingMessage()
    if (isNewBlock) {
      msg.segments = [...msg.segments, { type: 'content', content: textContent.value }]
    } else {
      msg.segments = updateContent(msg.segments, textContent.value)
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
      pendingThinking = null
      applyThinking(chunk)
    }
    if (pendingContent !== null) {
      const chunk = pendingContent
      pendingContent = null
      applyContent(chunk)
    }
  }

  /**
   * 丢弃未 flush 的缓冲（清空消息场景使用）
   */
  function discardPending(): void {
    pendingContent = null
    pendingThinking = null
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
    }, FLUSH_INTERVAL)
  }

  /**
   * 获取或创建正在流式输出的消息
   */
  function getOrCreateStreamingMessage(): StreamingMessage {
    let lastMsg = messages.value[messages.value.length - 1]
    if (!lastMsg || lastMsg.role !== 'ai') {
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
  function startStreaming(): void {
    isStreaming.value = true
    currentSegmentType.value = null
    thinkingContent.value = ''
    textContent.value = ''
  }

  /**
   * 追加思考内容（缓冲攒批，FLUSH_INTERVAL 后统一应用）
   *
   * 新思考块的首个 chunk（currentSegmentType 从其他类型切换而来）在 flush 时追加新分段，
   * 避免 updateThinking 的前缀匹配命中上一轮的旧 thinking 分段（位于 tool 之前），
   * 导致新思考内容错误地替换旧位置、打乱 tool 与 thinking 的先后顺序。
   * 续传 chunk（同一 thinking 块的后续增量）走 updateThinking 原地更新。
   * 切换分段类型时（如 content → thinking）先 flush 保证分段顺序。
   */
  function appendThinking(chunk: string): void {
    if (pendingContent !== null) flushPending()
    if (pendingThinking === null) {
      pendingThinking = chunk
      scheduleFlush()
    } else {
      pendingThinking += chunk
    }
  }

  /**
   * 追加文本内容（缓冲攒批，FLUSH_INTERVAL 后统一应用）
   *
   * 新内容块的首个 chunk（currentSegmentType 从其他类型切换而来）在 flush 时追加新分段，
   * 避免 updateContent 的前缀匹配命中上一轮的旧 content 分段（位于 tool 之前）。
   * 切换分段类型时（如 thinking → content）先 flush 保证分段顺序。
   */
  function appendContent(chunk: string): void {
    if (pendingThinking !== null) flushPending()
    if (pendingContent === null) {
      pendingContent = chunk
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
    status: 'running' | 'success' | 'error' = 'running'
  ): void {
    // 工具分段必须排在已缓冲的 thinking/content 之后
    flushPending()
    currentSegmentType.value = 'tool'
    const msg = getOrCreateStreamingMessage()
    msg.segments = addToolToSegments(msg.segments, name, args, status)

    if (!msg.tools) {
      msg.tools = []
    }
    msg.tools.push({ name, args, status })
  }

  /**
   * 更新工具调用结果
   */
  function updateToolSegment(
    name: string,
    status: 'running' | 'success' | 'error',
    result?: unknown
  ): void {
    // 确保缓冲内容先落地，避免 tool 结果与 content 尾部显示不同步
    flushPending()
    const msg = messages.value[messages.value.length - 1]
    if (msg?.role !== 'ai' || !msg.segments) return

    msg.segments = updateToolInSegments(msg.segments, name, status, result)

    if (msg.tools) {
      const tool = [...msg.tools].reverse().find(t => t.name === name && t.status === 'running')
      if (tool) {
        tool.status = status
        if (result !== undefined) tool.result = result
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
   */
  function addTodoSegment(newTodos: TodoItem[]): void {
    // todo 分段必须排在已缓冲的 thinking/content 之后
    flushPending()
    currentSegmentType.value = 'todo'
    const msg = getOrCreateStreamingMessage()
    msg.segments = addTodoToSegments(msg.segments, newTodos)
  }

  function addTokenUsage(
    prompt_tokens: number,
    completion_tokens: number,
    total_tokens: number
  ): void {
    const msg = messages.value[messages.value.length - 1]
    if (msg?.role !== 'ai') return
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
    messages,
    lastMessage,
    latestPromptTokens,
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
    reset
  }
}
