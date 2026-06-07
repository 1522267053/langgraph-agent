import { ElMessage } from 'element-plus'
import type { SSEEvent } from '@/types/sse'

export function createOnToolCallLimitHandler() {
  return (event: SSEEvent) => {
    ElMessage.warning(`工具调用次数已达上限（${event.data.max_iterations}次）`)
  }
}

export function createOnLlmRetryHandler() {
  return (event: SSEEvent) => {
    ElMessage.warning(event.data.message || 'LLM请求重试中')
  }
}
