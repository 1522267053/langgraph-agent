import { get } from './index'
import type { ApiResponse } from '@/types/common'

export interface ConfigField {
  name: string
  type: string
  required: boolean
  description: string
  default?: unknown
  options?: string[]
  items?: {
    type: string
    properties?: Record<string, ConfigField>
  }
}

export interface NodeConfigSchema {
  label: string
  config_fields: ConfigField[]
}

export const nodeSchemaApi = {
  getAll(): Promise<{ data: ApiResponse<Record<string, NodeConfigSchema>> }> {
    return get('/ai/flow/config-schemas')
  }
}
