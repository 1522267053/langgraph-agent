import { ElMessage } from 'element-plus'
import type { SSEEvent } from '@/types/sse'

export function createOnToolCallLimitHandler() {
  return (event: SSEEvent) => {
    ElMessage.warning({
      message: `工具调用次数已达上限（${event.data.max_iterations}次）`,
      duration: 5000
    })
  }
}

export function createOnLlmRetryHandler() {
  return (event: SSEEvent) => {
    ElMessage.warning({ message: event.data.message || 'LLM请求重试中', duration: 5000 })
  }
}
