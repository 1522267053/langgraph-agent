export interface WsGatewayConfig {
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

export interface WsGatewayCreate {
  flow_id?: number
  name: string
  description?: string
  input_config?: Record<string, unknown>
  is_enabled: number
}

export interface WsGatewayUpdate {
  id: number
  flow_id?: number
  name?: string
  description?: string
  input_config?: Record<string, unknown>
  is_enabled?: number
}
