import { get, post, put, del } from '@/api/index'
import type { ApiResponse, PaginatedResponse } from '@/types/common'
import type { ModelInfo } from '@/api/ai_provider'

/** AI 供应商连接（api_key 响应中已脱敏） */
export interface AIProviderConnectionInfo {
  id: number
  provider_id: string
  provider_name: string
  api_key: string | null
  base_url: string | null
  default_model: string | null
  context_length: number | null
  is_default: number
  is_enabled: number
  remark: string | null
  create_time?: string
  modify_time?: string
}

/** 供应商连接创建/编辑参数 */
export interface AIProviderConnectionPayload {
  id?: number
  provider_id: string
  api_key?: string
  base_url?: string | null
  default_model?: string | null
  context_length?: number | null
  is_enabled?: number
  remark?: string | null
}

/** 跨供应商模型分组（AgentChat 模型下拉数据源） */
export interface ModelGroup {
  provider_id: string
  provider_label: string
  models: ModelInfo[]
}

export interface ConnectionQueryParams {
  page: number
  page_size: number
  condition?: {
    provider_id?: string
    is_default?: number
    is_enabled?: number
  }
}

export const providerConnectionApi = {
  page(params: ConnectionQueryParams): Promise<{
    data: ApiResponse<PaginatedResponse<AIProviderConnectionInfo>>
  }> {
    return post<PaginatedResponse<AIProviderConnectionInfo>>('/ai-provider-connection/page', params)
  },

  create(payload: AIProviderConnectionPayload): Promise<{
    data: ApiResponse<AIProviderConnectionInfo>
  }> {
    return post<AIProviderConnectionInfo>('/ai-provider-connection/create', payload)
  },

  update(payload: AIProviderConnectionPayload): Promise<{ data: ApiResponse }> {
    return post('/ai-provider-connection/update', payload)
  },

  delete(id: number): Promise<{ data: ApiResponse }> {
    return del(`/ai-provider-connection/delete/${id}`)
  },

  setDefault(id: number): Promise<{ data: ApiResponse }> {
    return put(`/ai-provider-connection/set-default/${id}`)
  },

  /** 聚合启用连接的供应商模型分组（无模型元数据的供应商不返回） */
  modelGroups(): Promise<{ data: ApiResponse<ModelGroup[]> }> {
    return get<ModelGroup[]>('/ai-provider-connection/model-groups')
  }
}
