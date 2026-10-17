/**
 * 统一通知中心 API
 */
import { get, post } from './index'
import type { PaginatedResponse, PaginationParams, ApiResponse } from '@/types/common'

export interface Notification {
  id?: number
  type?: string
  title?: string
  content?: string
  ref_type?: string
  ref_id?: number
  /** 辅助关联ID：session 通知存 agent flow_id（跳转 /chat/{ref_sub_id}?sessionId= 用） */
  ref_sub_id?: number
  is_read?: number
  read_time?: string
  create_time?: string
}

export interface NotificationCondition {
  type?: string
  is_read?: number
  title?: string
}

export const notificationApi = {
  page(params: PaginationParams<NotificationCondition>) {
    return post<PaginatedResponse<Notification>>('/notification/page', params)
  },
  get(id: number) {
    return get<Notification>(`/notification/get/${id}`)
  },
  getUnreadCount() {
    return get<{ count: number }>('/notification/unread-count')
  },
  markRead(id: number) {
    return post<ApiResponse>(`/notification/mark-read/${id}`)
  },
  markAllRead() {
    return post<ApiResponse<{ count: number }>>('/notification/mark-all-read')
  },
  clearRead() {
    return post<ApiResponse<{ count: number }>>('/notification/clear-read')
  },
  delete(id: number) {
    return get<ApiResponse>(`/notification/delete/${id}`)
  },
  batchDelete(ids: number[]) {
    return post<ApiResponse>('/notification/delete-batch', ids)
  }
}
