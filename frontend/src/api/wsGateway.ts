import request, { get } from './index'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '@/types/common'
import type { WsGatewayConfig, WsGatewayCreate, WsGatewayUpdate } from '@/types/wsGateway'

export const wsGatewayApi = {
  page(params: PaginationParams<WsGatewayConfig>) {
    return request.post<ApiResponse<PaginatedResponse<WsGatewayConfig>>>('/ws-gateway/page', params)
  },

  create(data: WsGatewayCreate) {
    return request.post<ApiResponse<WsGatewayConfig>>('/ws-gateway/create', data)
  },

  update(data: WsGatewayUpdate) {
    return request.post<ApiResponse<void>>('/ws-gateway/update', data)
  },

  delete(id: number) {
    return get<void>(`/ws-gateway/delete/${id}`)
  },

  getUrl(id: number) {
    return get<{ url: string; token: string }>(`/ws-gateway/get/${id}/url`)
  }
}
