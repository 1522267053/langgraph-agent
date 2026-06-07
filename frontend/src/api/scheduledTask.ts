/**
 * 定时任务API
 */
import { get, post } from './index'
import type { PaginatedResponse, PaginationParams, ApiResponse } from '@/types/common'

export interface ScheduledTask {
  id?: number
  name?: string
  cron_expression?: string
  target_type?: string
  target_id?: number
  input_data?: Record<string, unknown>
  is_enabled?: number
  next_run_time?: string
  last_run_time?: string
  last_run_status?: number
  create_time?: string
  modify_time?: string
}

export interface ScheduledTaskLog {
  id?: number
  task_id?: number
  execution_id?: number
  session_id?: number
  agent_id?: number
  status?: number
  trigger_type?: number
  start_time?: string
  end_time?: string
  duration_ms?: number
  error_message?: string
  input_snapshot?: Record<string, unknown>
  create_time?: string
}

export const scheduledTaskApi = {
  page(params: PaginationParams<Partial<ScheduledTask>>) {
    return post<PaginatedResponse<ScheduledTask>>('/scheduled-task/page', params)
  },
  get(id: number) {
    return get<ScheduledTask>(`/scheduled-task/get/${id}`)
  },
  create(data: Partial<ScheduledTask>) {
    return post<ScheduledTask>('/scheduled-task/create', data)
  },
  update(data: Partial<ScheduledTask>) {
    return post<ApiResponse>('/scheduled-task/update', data)
  },
  delete(id: number) {
    return get<ApiResponse>(`/scheduled-task/delete/${id}`)
  },
  toggle(taskId: number) {
    return post<ScheduledTask>(`/scheduled-task/toggle/${taskId}`)
  },
  trigger(taskId: number) {
    return post<ScheduledTaskLog>(`/scheduled-task/trigger/${taskId}`)
  },
  logsPage(params: PaginationParams<{ task_id?: number }>) {
    return post<PaginatedResponse<ScheduledTaskLog>>('/scheduled-task/logs/page', params)
  }
}
