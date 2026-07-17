import { get } from '@/api/index'
import type { ApiResponse } from '@/types/common'

export interface ProviderInfo {
  provider_id: string
  name: string
  label: string
  default_base_url: string
  api_url: string
  adapter_type: string
  env_vars: string[] | null
}

export interface ModelInfo {
  model_id: string
  name: string
  description: string | null
  provider_id: string
  provider_name: string | null
  modalities: { input: string[]; output: string[] } | null
  limits: { context: number; input: number; output: number } | null
  cost: Record<string, number> | null
  reasoning: number
  tool_call: number
  temperature: number
  attachment: number
  open_weights: number
  is_experimental: number
  structured_output: number
  knowledge: string | null
  release_date: string | null
  last_updated: string | null
  family: string | null
  status: string | null
}

export const aiProviderApi = {
  list(): Promise<{ data: ApiResponse<ProviderInfo[]> }> {
    return get<ProviderInfo[]>('/ai-provider/list')
  },

  getModels(providerId: string): Promise<{ data: ApiResponse<ModelInfo[]> }> {
    return get<ModelInfo[]>(`/ai-provider/models/${providerId}`)
  }
}