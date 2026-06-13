export interface TokenStatisticsQuery {
  start_date?: string
  end_date?: string
  time_grain?: string
}

export interface TokenOverview {
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  llm_call_count: number
}

export interface TokenTrendItem {
  date: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface TokenByFlowItem {
  flow_id: number
  flow_name: string
  flow_type: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  call_count: number
}

export interface TokenByModelItem {
  model: string
  provider: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  call_count: number
  cache_read_tokens: number
  cache_write_tokens: number
  reasoning_tokens: number
}
