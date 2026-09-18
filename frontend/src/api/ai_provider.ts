import { get, post } from '@/api/index'
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

/** models.dev reasoning_options 条目（ai_model.reasoning_options 同步产物） */
export interface ReasoningOption {
  type: 'effort' | 'toggle' | 'budget_tokens'
  values?: (string | null)[]
  min?: number
  max?: number
}

/** 归一化后的推理深度选择（对齐 opencode reasoningVariants 预设策略） */
export interface ReasoningMeta {
  mode: 'effort' | 'toggle'
  values: string[]
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
  reasoning_options: ReasoningOption[] | null
  knowledge: string | null
  release_date: string | null
  last_updated: string | null
  family: string | null
  status: string | null
}

/**
 * 归一化 reasoning_options 为 UI 可用的档位列表：
 * - effort → values 原样（null → "none"，对齐 opencode effortVariants）
 * - toggle → off/high 两档（UI 渲染为开关，开=high）
 * - budget_tokens → high/max 两预设（对齐 opencode budgetVariants：
 *   区间中点/钳制上限，不做数字输入框）
 * - 空/缺失/未知类型 → null（调用方回退静态三档）
 */
export function parseReasoningOptions(
  options: ReasoningOption[] | null | undefined
): ReasoningMeta | null {
  if (!options || options.length === 0) return null
  const effort = options.find(o => o.type === 'effort')
  if (effort) {
    const values = (effort.values || [])
      .map(v => (v === null || v === undefined ? 'none' : String(v)))
      .filter(Boolean)
    return values.length > 0 ? { mode: 'effort', values } : null
  }
  if (options.some(o => o.type === 'toggle')) {
    return { mode: 'toggle', values: ['off', 'high'] }
  }
  if (options.some(o => o.type === 'budget_tokens')) {
    return { mode: 'effort', values: ['high', 'max'] }
  }
  return null
}

export const aiProviderApi = {
  list(): Promise<{ data: ApiResponse<ProviderInfo[]> }> {
    return get<ProviderInfo[]>('/ai-provider/list')
  },

  getModels(providerId: string): Promise<{ data: ApiResponse<ModelInfo[]> }> {
    return get<ModelInfo[]>(`/ai-provider/models/${providerId}`)
  },

  sync(): Promise<{ data: ApiResponse }> {
    return post('/ai-provider/sync')
  }
}
