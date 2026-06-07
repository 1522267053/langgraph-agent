/**
 * SSE流式连接管理 Hook
 * @description 封装SSE连接的创建、状态管理和清理逻辑
 */

import { ref, onUnmounted, type Ref } from 'vue'
import { createFlowSSEConnection } from '@/utils/sse'
import type { FlowSSEHandlers, SSEEvent } from '@/types/sse'

/** SSE连接状态 */
export interface SSEState {
  /** 是否正在连接中 */
  isConnecting: Ref<boolean>
  /** 是否已连接 */
  isConnected: Ref<boolean>
  /** 是否有错误 */
  hasError: Ref<boolean>
  /** 错误信息 */
  errorMessage: Ref<string>
}

/** SSE连接选项 */
export interface SSEOptions {
  /** 自动清理（组件卸载时自动断开） */
  autoCleanup?: boolean
  /** 日志前缀 */
  logPrefix?: string
}

/**
 * SSE流式连接 Hook
 * @description 提供SSE连接的创建、管理和清理功能
 * @param options 配置选项
 * @returns SSE状态和操作方法
 */
export function useSSE(options: SSEOptions = {}) {
  const { autoCleanup = true, logPrefix = '[SSE]' } = options

  // 状态
  const isConnecting = ref(false)
  const isConnected = ref(false)
  const hasError = ref(false)
  const errorMessage = ref('')

  // 中断函数引用
  let abortFn: (() => void) | null = null

  /**
   * 建立SSE连接
   * @param url 请求URL
   * @param body 请求体
   * @param handlers 事件处理器
   */
  function connect(url: string, body: unknown, handlers: FlowSSEHandlers): void {
    // 先断开现有连接
    disconnect()

    // 重置状态
    isConnecting.value = true
    isConnected.value = false
    hasError.value = false
    errorMessage.value = ''

    // 包装处理器，添加状态管理
    const wrappedHandlers: FlowSSEHandlers = {
      onFlowStart: event => {
        isConnecting.value = false
        isConnected.value = true
        handlers.onFlowStart?.(event)
      },
      onNodeStart: handlers.onNodeStart,
      onNodeThinking: handlers.onNodeThinking,
      onNodeContent: handlers.onNodeContent,
      onNodeDone: handlers.onNodeDone,
      onToolCallStart: handlers.onToolCallStart,
      onToolCallEnd: handlers.onToolCallEnd,
      onToolCallLimit: handlers.onToolCallLimit,
      onWaitingHuman: handlers.onWaitingHuman,
      onFlowDone: event => {
        isConnected.value = false
        handlers.onFlowDone?.(event)
      },
      onLlmRetry: handlers.onLlmRetry,
      onContextCompressing: handlers.onContextCompressing,
      onError: event => {
        isConnecting.value = false
        isConnected.value = false
        hasError.value = true
        errorMessage.value = event.data.message || '连接失败'
        handlers.onError?.(event)
      }
    }

    // 创建连接
    abortFn = createFlowSSEConnection(url, body, wrappedHandlers, logPrefix)
  }

  /**
   * 断开SSE连接
   */
  function disconnect(): void {
    if (abortFn) {
      abortFn()
      abortFn = null
    }
    isConnecting.value = false
    isConnected.value = false
  }

  /**
   * 重置状态
   */
  function reset(): void {
    disconnect()
    hasError.value = false
    errorMessage.value = ''
  }

  // 组件卸载时自动清理
  if (autoCleanup) {
    onUnmounted(() => {
      disconnect()
    })
  }

  return {
    // 状态
    isConnecting,
    isConnected,
    hasError,
    errorMessage,
    // 方法
    connect,
    disconnect,
    reset
  }
}

/**
 * 创建SSE事件处理器
 * @description 工厂函数，用于创建标准化的SSE事件处理器
 * @param callbacks 回调函数集合
 * @returns SSE事件处理器对象
 */
export function createSSEHandlers(callbacks: {
  onStart?: () => void
  onNodeStart?: (event: SSEEvent) => void
  onThinking?: (content: string) => void
  onContent?: (content: string) => void
  onNodeDone?: (event: SSEEvent) => void
  onToolStart?: (name: string, args: Record<string, unknown>) => void
  onToolEnd?: (name: string, result: unknown, error?: string) => void
  onToolCallLimit?: (nodeKey: string, maxIterations: number) => void
  onWaiting?: (data: SSEEvent['data']) => void
  onDone?: () => void
  onError?: (message: string) => void
}): FlowSSEHandlers {
  return {
    onFlowStart: () => callbacks.onStart?.(),
    onNodeStart: event => callbacks.onNodeStart?.(event),
    onNodeThinking: event => callbacks.onThinking?.(event.data.content || ''),
    onNodeContent: event => callbacks.onContent?.(event.data.content || ''),
    onNodeDone: event => callbacks.onNodeDone?.(event),
    onToolCallStart: event =>
      callbacks.onToolStart?.(event.data.tool_name || '', event.data.tool_args || {}),
    onToolCallEnd: event =>
      callbacks.onToolEnd?.(event.data.tool_name || '', event.data.result, event.data.error),
    onToolCallLimit: event =>
      callbacks.onToolCallLimit?.(event.data.node_key || '', event.data.max_iterations || 0),
    onWaitingHuman: event => callbacks.onWaiting?.(event.data),
    onFlowDone: () => callbacks.onDone?.(),
    onError: event => callbacks.onError?.(event.data.message || '未知错误')
  }
}
