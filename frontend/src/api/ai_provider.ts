import { get } from '@/api/index'
import type { ApiResponse } from '@/types/common'

/** AI 供应商信息 */
export interface ProviderInfo {
  name: string
  label: string
  default_base_url: string
}

export const aiProviderApi = {
  list(): Promise<{ data: ApiResponse<ProviderInfo[]> }> {
    return get<ProviderInfo[]>('/ai-provider/list')
  }
}
