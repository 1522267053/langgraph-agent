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
  role: 'user' | 'assistant'
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

  const lastMessage = computed(() => {
    return messages.value.length > 0 ? messages.value[messages.value.length - 1] : null
  })

  /**
   * 获取或创建正在流式输出的消息
   */
  function getOrCreateStreamingMessage(): StreamingMessage {
    let lastMsg = messages.value[messages.value.length - 1]
    if (!lastMsg || lastMsg.role !== 'assistant') {
      lastMsg = {
        id: `streaming-${Date.now()}`,
        role: 'assistant',
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
      role: 'user',
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
   * 追加思考内容
   *
   * 新思考块的首个 chunk（currentSegmentType 从其他类型切换而来）直接追加新分段，
   * 避免 updateThinking 的前缀匹配命中上一轮的旧 thinking 分段（位于 tool 之前），
   * 导致新思考内容错误地替换旧位置、打乱 tool 与 thinking 的先后顺序。
   * 续传 chunk（同一 thinking 块的后续增量）走 updateThinking 原地更新。
   */
  function appendThinking(chunk: string): void {
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
   * 追加文本内容
   *
   * 新内容块的首个 chunk（currentSegmentType 从其他类型切换而来）直接追加新分段，
   * 避免 updateContent 的前缀匹配命中上一轮的旧 content 分段（位于 tool 之前）。
   * 续传 chunk（同一 content 块的后续增量）走 updateContent 原地更新。
   */
  function appendContent(chunk: string): void {
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
   * 添加工具调用分段
   */
  function addToolSegment(
    name: string,
    args?: Record<string, unknown>,
    status: 'running' | 'success' | 'error' = 'running'
  ): void {
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
    const msg = messages.value[messages.value.length - 1]
    if (msg?.role !== 'assistant' || !msg.segments) return

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
    if (msg?.role !== 'assistant') return
    msg.prompt_tokens = (msg.prompt_tokens || 0) + prompt_tokens
    msg.completion_tokens = (msg.completion_tokens || 0) + completion_tokens
    msg.total_tokens = (msg.total_tokens || 0) + total_tokens
    latestPromptTokens.value = prompt_tokens
  }

  /**
   * 结束流式输出
   */
  function stopStreaming(): void {
    isStreaming.value = false
    currentSegmentType.value = null
  }

  /**
   * 清空所有消息
   */
  function clearMessages(): void {
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
