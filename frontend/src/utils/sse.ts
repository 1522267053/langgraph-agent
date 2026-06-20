/**
 * SSE（Server-Sent Events）处理工具
 * @description 统一处理SSE流式数据解析，消除重复代码
 */

import type { SSEEvent, SSEEventHandler, FlowSSEHandlers } from '@/types/sse'

/** SSE连接配置 */
export interface SSEConnectionConfig<T extends string> {
  /** 请求URL */
  url: string
  /** 请求体 */
  body: unknown
  /** 事件处理器映射 */
  handlers: Record<string, SSEEventHandler<T> | undefined>
  /** 日志前缀（用于调试） */
  logPrefix?: string
  /** 断线超时毫秒数，超时无数据自动触发 error（默认 90000 = 90s） */
  disconnectTimeout?: number
}

/** SSE解析器状态 */
interface SSEParserState {
  /** 数据缓冲区 */
  buffer: string
  /** TextDecoder实例 */
  decoder: TextDecoder
}

const DEFAULT_DISCONNECT_TIMEOUT = 90_000

/**
 * 创建SSE解析器
 * @returns 解析器状态和处理函数
 */
function createSSEParser() {
  const state: SSEParserState = {
    buffer: '',
    decoder: new TextDecoder()
  }

  /**
   * 解析SSE事件字符串
   * @param eventStr 事件字符串
   * @returns 解析后的事件对象，注释/ping返回null
   */
  function parseEvent(eventStr: string): { type: string; data: string } | null {
    let eventType = ''
    let eventData = ''

    const lines = eventStr.replace(/\r\n/g, '\n').split('\n')
    for (const line of lines) {
      const trimmedLine = line.trim()
      // SSE 注释行（如 : ping），跳过
      if (trimmedLine.startsWith(':')) {
        return null
      }
      if (trimmedLine.startsWith('event:')) {
        eventType = trimmedLine.substring(6).trim()
      } else if (trimmedLine.startsWith('data:')) {
        eventData = trimmedLine.substring(5).trim()
      }
    }

    if (eventType && eventData) {
      return { type: eventType, data: eventData }
    }
    return null
  }

  /**
   * 处理数据块
   * @param value 数据块
   * @param onEvent 事件回调
   */
  function processChunk(value: Uint8Array, onEvent: (type: string, data: unknown) => void): void {
    state.buffer += state.decoder.decode(value, { stream: true })

    let events: string[]
    if (state.buffer.includes('\r\n\r\n')) {
      events = state.buffer.split('\r\n\r\n')
    } else {
      events = state.buffer.split('\n\n')
    }
    state.buffer = events.pop() || ''

    for (const eventStr of events) {
      if (!eventStr.trim()) continue

      const parsed = parseEvent(eventStr)
      if (parsed) {
        try {
          const data = JSON.parse(parsed.data)
          onEvent(parsed.type, data)
        } catch (parseError) {
          console.error('[SSE] Failed to parse event data:', eventStr, parseError)
        }
      }
    }
  }

  /**
   * 重置解析器状态
   */
  function reset(): void {
    state.buffer = ''
  }

  return { processChunk, reset }
}

/**
 * 创建SSE连接
 * @description 建立SSE连接并处理流式数据，返回中断函数
 * @param config 连接配置
 * @returns 中断连接的函数
 */
export function createSSEConnection<T extends string = string>(
  config: SSEConnectionConfig<T>
): () => void {
  const {
    url,
    body,
    handlers,
    logPrefix = '[SSE]',
    disconnectTimeout = DEFAULT_DISCONNECT_TIMEOUT
  } = config
  const controller = new AbortController()
  const parser = createSSEParser()
  let disconnectTimer: ReturnType<typeof setTimeout> | null = null

  function resetDisconnectTimer(): void {
    if (disconnectTimer) clearTimeout(disconnectTimer)
    disconnectTimer = setTimeout(() => {
      console.warn(`${logPrefix} 连接超时（${disconnectTimeout / 1000}s无数据），主动断开`)
      controller.abort()
      const errorHandler = handlers['error']
      if (errorHandler) {
        errorHandler({
          type: 'error' as T,
          data: { message: `SSE 连接超时（${disconnectTimeout / 1000}s 无数据）` }
        })
      }
    }, disconnectTimeout)
  }

  function clearDisconnectTimer(): void {
    if (disconnectTimer) {
      clearTimeout(disconnectTimer)
      disconnectTimer = null
    }
  }

  resetDisconnectTimer()

  fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream'
    },
    body: JSON.stringify(body),
    signal: controller.signal
  })
    .then(async response => {
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('No response body')
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          clearDisconnectTimer()
          break
        }

        resetDisconnectTimer()

        parser.processChunk(value, (type, data) => {
          console.log(`${logPrefix} Received event:`, type, data)

          const handler = handlers[type]
          if (handler) {
            const event = { type, data } as SSEEvent<T>
            handler(event)
          } else {
            console.warn(`${logPrefix} Unknown event type:`, type)
          }
        })
      }
    })
    .catch(error => {
      clearDisconnectTimer()
      if (error.name !== 'AbortError') {
        const errorHandler = handlers['error']
        if (errorHandler) {
          errorHandler({
            type: 'error' as T,
            data: { message: error.message }
          })
        }
      }
    })

  return () => {
    clearDisconnectTimer()
    controller.abort()
    parser.reset()
  }
}

/**
 * 创建流程执行SSE连接
 * @description 专为流程执行场景封装的SSE连接函数
 * @param url 请求URL
 * @param body 请求体
 * @param handlers 流程执行事件处理器
 * @param logPrefix 日志前缀
 * @returns 中断连接的函数
 */
export function createFlowSSEConnection(
  url: string,
  body: unknown,
  handlers: FlowSSEHandlers,
  logPrefix = '[Flow SSE]'
): () => void {
  const handlerMap: Record<string, SSEEventHandler | undefined> = {
    flow_start: handlers.onFlowStart,
    resume_start: handlers.onFlowStart,
    node_start: handlers.onNodeStart,
    node_thinking: handlers.onNodeThinking,
    node_content: handlers.onNodeContent,
    node_done: handlers.onNodeDone,
    tool_call_start: handlers.onToolCallStart,
    tool_call_end: handlers.onToolCallEnd,
    tool_call_limit: handlers.onToolCallLimit,
    token_usage: handlers.onTokenUsage,
    waiting_human: handlers.onWaitingHuman,
    tool_approval_required: handlers.onToolApproval,
    todo_update: handlers.onTodoUpdate,
    flow_done: handlers.onFlowDone,
    llm_retry: handlers.onLlmRetry,
    context_compressing: handlers.onContextCompressing,
    flow_preview: handlers.onFlowPreview,
    error: handlers.onError
  }

  return createSSEConnection({
    url,
    body,
    handlers: handlerMap,
    logPrefix
  })
}
