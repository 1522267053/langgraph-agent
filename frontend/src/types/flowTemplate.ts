export interface FlowTemplate {
  id: string
  name: string
  description: string
  flow_type: 'flow' | 'agent'
  node_count: number
}

export interface CreateFromTemplateRequest {
  template_id: string
  name: string
  description?: string
}
