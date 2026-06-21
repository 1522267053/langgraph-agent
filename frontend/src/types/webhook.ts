export interface WebhookConfig {
  id?: number
  flow_id: number
  name: string
  token?: string
  description?: string
  input_config?: Record<string, unknown>
  callback_url?: string
  is_enabled: number
  call_count?: number
  last_call_time?: string
  create_time?: string
}

export interface WebhookCreate {
  flow_id?: number
  name: string
  description?: string
  input_config?: Record<string, unknown>
  callback_url?: string
  is_enabled: number
}

export interface WebhookUpdate {
  id: number
  flow_id?: number
  name?: string
  description?: string
  input_config?: Record<string, unknown>
  callback_url?: string
  is_enabled?: number
}

export interface WebhookCallRecord {
  id?: number
  webhook_id: number
  flow_id: number
  ref_type?: string
  ref_id?: number
  input_data?: Record<string, unknown>
  status: number
  output_data?: Record<string, unknown>
  error_message?: string
  callback_status?: string
  started_at?: string
  finished_at?: string
  message_count?: number
}

export interface WebhookMessage {
  id?: number
  role?: string
  content?: string
  thinking?: string
  tool_calls?: unknown[]
  tool_call_id?: string
  status?: string
  sequence?: number
  created_at?: string
}
