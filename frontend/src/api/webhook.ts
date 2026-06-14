import request, { get } from './index'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '@/types/common'
import type { WebhookConfig, WebhookCreate, WebhookUpdate } from '@/types/webhook'

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
  }
}
