import { get, post } from '@/api/index'
import type { ApiResponse } from '@/types/common'

export interface BackgroundTask {
  task_id: string
  command: string
  status: 'running' | 'completed' | 'failed' | 'timeout' | 'cancelled'
  stdout: string
  stderr: string
  return_code: number | null
  elapsed_seconds: number | null
}

export const toolApi = {
  getRunning() {
    return get<ApiResponse<BackgroundTask[]>>('/agent/tools/running')
  },
  getStatus(taskId: string) {
    return get<ApiResponse<BackgroundTask>>(`/agent/tools/${taskId}/status`)
  },
  cancel(taskId: string) {
    // showError=false：任务已结束/不存在时由 store 静默落地终态，不弹错误提示
    return post<ApiResponse>(`/agent/tools/${taskId}/cancel`, undefined, { showError: false })
  }
}
