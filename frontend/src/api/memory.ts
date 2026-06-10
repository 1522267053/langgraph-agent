import request, { get } from './index'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '@/types/common'

export interface Memory {
  id: number
  agent_id: number
  memory_type: string
  category: string
  title: string
  content: string
  keywords?: string
  source_session_id?: string
  importance: number
  access_count: number
  peak_tier?: string
  create_time?: string
  modify_time?: string
}

export interface MemoryQuery {
  agent_id?: number
  memory_type?: string
  category?: string
  title?: string
}

export interface MemoryExportItem {
  title: string
  content: string
  memory_type: string
  category: string
  importance: number
  keywords?: string
}

export interface MemoryExportResult {
  agent_id: number
  export_time: string
  total: number
  memories: MemoryExportItem[]
}

export interface MemoryImportResult {
  total: number
  imported: number
  failed: number
  errors: Array<{ index: number; title: string; error: string }>
}

export interface MemorySearchHit {
  memory: Memory
  score: number
}

export const memoryApi = {
  page(params: PaginationParams<MemoryQuery>) {
    return request.post<ApiResponse<PaginatedResponse<Memory>>>('/memory/page', params)
  },

  get(id: number) {
    return get<ApiResponse<Memory>>(`/memory/get/${id}`)
  },

  delete(id: number) {
    return get<ApiResponse<void>>(`/memory/delete/${id}`)
  },

  deleteBatch(ids: number[]) {
    return request.post<ApiResponse<void>>('/memory/delete-batch', ids)
  },

  revectorize(agentId: number, ids: number[]) {
    return request.post<
      ApiResponse<{ total: number; success: number; failed: number; failed_ids: number[] }>
    >('/memory/revectorize', { agent_id: agentId, ids })
  },

  exportMemory(params: { agent_id: number; ids?: number[]; tier?: string }) {
    return request.post<ApiResponse<MemoryExportResult>>('/memory/export', params)
  },

  importMemory(params: { agent_id: number; memories: MemoryExportItem[] }) {
    return request.post<ApiResponse<MemoryImportResult>>('/memory/import', params)
  },

  getStats(agentId: number) {
    return get<ApiResponse<{ hot: number; warm: number; cold: number }>>(
      `/memory/stats?agent_id=${agentId}`
    )
  },

  search(params: { agent_id: number; query: string; tier?: string; max_results?: number }) {
    return request.post<ApiResponse<{ items: MemorySearchHit[] }>>('/memory/search', params)
  }
}
