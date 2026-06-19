import { get, post } from './index'
import type { ApiResponse } from '@/types/common'
import type { FlowTemplate, CreateFromTemplateRequest } from '@/types/flowTemplate'

export const flowTemplateApi = {
  list(flowType?: string) {
    const params: Record<string, string> = {}
    if (flowType) params.flow_type = flowType
    return get<ApiResponse<FlowTemplate[]>>('/flow/templates', params)
  },

  createFromTemplate(data: CreateFromTemplateRequest) {
    return post<ApiResponse<{ id: number }>>('/flow/create-from-template', data)
  }
}
