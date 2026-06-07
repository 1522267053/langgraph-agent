import { get } from '@/api/index'
import type { ApiResponse } from '@/types/common'

/** AI 供应商信息 */
export interface ProviderInfo {
  name: string
  label: string
  default_base_url: string
  supports_image: boolean
  supports_audio: boolean
  supports_video: boolean
  media_fields?: Record<string, MediaGenFieldDef[]>
}

/** 媒体生成参数字段定义 */
export interface MediaGenFieldDef {
  name: string
  label: string
  field_type: 'text' | 'number' | 'select' | 'switch' | 'textarea'
  default: unknown
  required: boolean
  options: string[]
  min_val: number | null
  max_val: number | null
  step: number | null
  placeholder: string
  description: string
}

export const aiProviderApi = {
  list(): Promise<{ data: ApiResponse<ProviderInfo[]> }> {
    return get<ProviderInfo[]>('/ai-provider/list')
  },
  getMediaFields(
    provider: string,
    mediaType: string
  ): Promise<{ data: ApiResponse<MediaGenFieldDef[]> }> {
    return get<MediaGenFieldDef[]>(
      `/ai-provider/media-fields?provider=${provider}&media_type=${mediaType}`
    )
  }
}
