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
    return post<ApiResponse>(`/agent/tools/${taskId}/cancel`)
  }
}
