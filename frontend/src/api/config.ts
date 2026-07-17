import { get, post } from '@/api/index'
import type { ApiResponse } from '@/types/common'
import { sha256 } from '@/utils/crypto'

export interface ProviderInfo {
  name: string
  label: string
  default_base_url: string
  api_url: string
  adapter_type: string
  env_vars: string[] | null
}

export interface GlobalConfigData {
  provider?: string
  model?: string
  api_key_masked?: string
  base_url?: string
  context_length?: number
  embedding_model?: string
  embedding_api_key_masked?: string
  embedding_base_url?: string
  has_password?: boolean
  has_username?: boolean
  username?: string | null
  execution_notification_enabled?: boolean
}

export interface InitConfigRequest {
  provider: string
  api_key: string
  model: string
  base_url?: string
  context_length?: number
  embedding_api_key?: string
  embedding_base_url?: string
  embedding_model?: string
  login_password?: string
  login_username?: string
}

export interface UpdateConfigRequest {
  provider?: string
  api_key?: string
  model?: string
  base_url?: string
  context_length?: number
  embedding_api_key?: string
  embedding_base_url?: string
  embedding_model?: string
  login_password?: string
  login_username?: string
  current_password?: string
  execution_notification_enabled?: boolean
}

export const configApi = {
  checkInitialized(): Promise<{ data: ApiResponse<{ initialized: boolean }> }> {
    return get<{ initialized: boolean }>('/config/check')
  },
  initConfig(data: InitConfigRequest): Promise<{ data: ApiResponse<null> }> {
    return post<null>('/config/init', data)
  },
  getProviders(): Promise<{ data: ApiResponse<ProviderInfo[]> }> {
    return get<ProviderInfo[]>('/config/providers')
  },
  getConfig(): Promise<{ data: ApiResponse<GlobalConfigData> }> {
    return get<GlobalConfigData>('/config/')
  },
  updateConfig(data: UpdateConfigRequest): Promise<{ data: ApiResponse }> {
    return post('/config/update', data)
  },
  checkUpdate(refresh?: boolean): Promise<{ data: ApiResponse<UpdateCheckResult> }> {
    return get<UpdateCheckResult>(`/config/check-update${refresh ? '?refresh=true' : ''}`)
  }
}

export interface UpdateCheckResult {
  has_update: boolean
  current_version: string
  latest_version: string
  release_notes: string
  download_url: string
  published_at: string
  force_upgrade: boolean
}

export async function hashPassword(password: string): Promise<string> {
  return sha256(password)
}
