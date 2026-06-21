import request, { get } from './index'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '@/types/common'
import type { WebhookConfig, WebhookCreate, WebhookUpdate, WebhookCallRecord, WebhookMessage } from '@/types/webhook'

export const webhookApi = {
  page(params: PaginationParams<WebhookConfig>) {
    return request.post<ApiResponse<PaginatedResponse<WebhookConfig>>>('/webhook/page', params)
  },

  create(data: WebhookCreate) {
    return request.post<ApiResponse<WebhookConfig>>('/webhook/create', data)
  },

  update(data: WebhookUpdate) {
    return request.post<ApiResponse<void>>('/webhook/update', data)
  },

  delete(id: number) {
    return get<void>(`/webhook/delete/${id}`)
  },

  getUrl(id: number) {
    return get<{ url: string; token: string }>(`/webhook/get/${id}/url`)
  },

  // ---- 免认证查询接口 ----

  queryCalls(token: string, page = 1, pageSize = 20) {
    return get<ApiResponse<{ total: number; list: WebhookCallRecord[] }>>(
      `/webhook/query/${token}/calls?page=${page}&page_size=${pageSize}`
    )
  },

  queryCallDetail(token: string, callId: number) {
    return get<ApiResponse<WebhookCallRecord>>(`/webhook/query/${token}/calls/${callId}`)
  },

  queryCallMessages(token: string, callId: number, beforeId?: number, limit = 20) {
    let url = `/webhook/query/${token}/calls/${callId}/messages?limit=${limit}`
    if (beforeId) {
      url += `&before_id=${beforeId}`
    }
    return get<ApiResponse<{ total: number; list: WebhookMessage[] }>>(url)
  }
}
