import request, { get, put } from './index'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '@/types/common'
import type {
  McpServer,
  McpServerConfig,
  McpServerCreate,
  McpServerUpdate,
  McpServerTestResult
} from '@/types/mcpServer'

export type {
  McpServer,
  McpServerConfig,
  McpServerCreate,
  McpServerUpdate,
  McpToolInfo,
  McpServerTestResult,
  McpTransportType
} from '@/types/mcpServer'

export const mcpServerApi = {
  page(params: PaginationParams<Partial<McpServer>>) {
    return request.post<ApiResponse<PaginatedResponse<McpServer>>>('/mcp-server/page', params)
  },

  list() {
    return request.get<ApiResponse<McpServer[]>>('/mcp-server/list')
  },

  get(id: number) {
    return request.get<ApiResponse<McpServer & { config?: McpServerConfig }>>(
      `/mcp-server/get/${id}`
    )
  },

  create(data: McpServerCreate) {
    return request.post<ApiResponse<McpServer>>('/mcp-server/create', data)
  },

  update(data: McpServerUpdate) {
    return request.post<ApiResponse<McpServer>>('/mcp-server/update', data)
  },

  delete(id: number) {
    return get<void>(`/mcp-server/delete/${id}`)
  },

  test(id: number) {
    return request.post<ApiResponse<McpServerTestResult>>(`/mcp-server/test/${id}`)
  },

  refresh(id: number) {
    return request.post<ApiResponse<McpServerTestResult>>(`/mcp-server/refresh/${id}`)
  },

  updateToolStatus(serverId: number, toolName: string, isEnabled: number) {
    return put<ApiResponse>('/mcp-server/tools/status', {
      server_id: serverId,
      tool_name: toolName,
      is_enabled: isEnabled
    })
  }
}
