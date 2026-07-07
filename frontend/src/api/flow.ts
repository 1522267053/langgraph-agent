import request, { get } from './index'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '@/types/common'
import type {
  Flow,
  FlowDetail,
  FlowCreate,
  FlowUpdate,
  VueFlowGraph,
  FlowNodeCreate,
  FlowNodeUpdate,
  FlowEdgeCreate,
  FlowEdgeUpdate,
  FlowExportData,
  FlowImportResult,
  FlowSnapshot
} from '@/types/flow'

export interface ConnectedToolInfo {
  node_key: string
  node_type: string
  node_label: string
  tools: Array<{ name: string; description: string }>
}

export const flowApi = {
  page(params: PaginationParams<Flow>) {
    return request.post<ApiResponse<PaginatedResponse<Flow>>>('/flow/page', params)
  },

  get(id: number) {
    return request.get<ApiResponse<FlowDetail>>(`/flow/get/${id}`)
  },

  getVueFlow(id: number) {
    return request.get<ApiResponse<VueFlowGraph>>(`/flow/get/${id}/vue-flow`)
  },

  create(data: FlowCreate) {
    return request.post<ApiResponse<Flow>>('/flow/create', data)
  },

  update(data: FlowUpdate) {
    return request.post<ApiResponse<void>>('/flow/update', data)
  },

  delete(id: number) {
    return get<void>(`/flow/delete/${id}`)
  },

  deleteBatch(ids: number[]) {
    return request.post<ApiResponse<void>>('/flow/delete-batch', ids)
  },

  saveAsCard(id: number) {
    return request.post<ApiResponse<void>>(`/flow/save-as-card/${id}`)
  },

  getFlowCards() {
    return request.get<ApiResponse<Flow[]>>('/flow/list/cards')
  },

  getAgentFlows() {
    return request.get<ApiResponse<Flow[]>>('/flow/list/agents')
  },

  createAgent(data: FlowCreate) {
    return request.post<ApiResponse<Flow>>('/flow/create-agent', data)
  },

  exportFlows(ids: number[]) {
    return request.post<ApiResponse<FlowExportData>>('/flow/export', { ids })
  },

  importFlows(data: FlowExportData) {
    return request.post<ApiResponse<FlowImportResult>>('/flow/import', data)
  },

  duplicate(id: number) {
    return request.post<ApiResponse<Flow>>(`/flow/duplicate/${id}`)
  },

  getConnectedTools(flowId: number, nodeKey: string) {
    return request.get<ApiResponse<ConnectedToolInfo[]>>(
      `/flow/${flowId}/node/${nodeKey}/connected-tools`
    )
  },

  // ---- 版本快照 ----
  autoSnapshot(flowId: number) {
    return request.post<ApiResponse<void>>(`/flow-snapshot/auto/${flowId}`)
  },

  createSnapshot(flowId: number, data: { name: string; description?: string }) {
    return request.post<ApiResponse<void>>(`/flow-snapshot/create/${flowId}`, data)
  },

  listSnapshots(flowId: number) {
    return request.get<ApiResponse<FlowSnapshot[]>>(`/flow-snapshot/list/${flowId}`)
  },

  restoreSnapshot(snapshotId: number) {
    return request.post<ApiResponse<void>>(`/flow-snapshot/restore/${snapshotId}`)
  },

  deleteSnapshot(id: number) {
    return get<void>(`/flow-snapshot/delete/${id}`)
  },

  pinSnapshot(id: number) {
    return request.post<ApiResponse<void>>(`/flow-snapshot/pin/${id}`)
  }
}

export const flowNodeApi = {
  create(data: FlowNodeCreate) {
    return request.post<ApiResponse<FlowNodeCreate>>('/flow-node/create', data)
  },

  update(data: FlowNodeUpdate) {
    return request.post<ApiResponse<void>>('/flow-node/update', data)
  },

  delete(id: number) {
    return get<void>(`/flow-node/delete/${id}`)
  },

  batchCreate(nodes: FlowNodeCreate[]) {
    return request.post<ApiResponse<void>>('/flow-node/batch-create', nodes)
  },

  batchUpdate(nodes: FlowNodeUpdate[]) {
    return request.post<ApiResponse<void>>('/flow-node/batch-update', nodes)
  }
}

export const flowEdgeApi = {
  create(data: FlowEdgeCreate) {
    return request.post<ApiResponse<FlowEdgeCreate>>('/flow-edge/create', data)
  },

  update(data: FlowEdgeUpdate) {
    return request.post<ApiResponse<void>>('/flow-edge/update', data)
  },

  delete(id: number) {
    return get<void>(`/flow-edge/delete/${id}`)
  },

  batchCreate(edges: FlowEdgeCreate[]) {
    return request.post<ApiResponse<void>>('/flow-edge/batch-create', edges)
  },

  batchUpdate(edges: FlowEdgeUpdate[]) {
    return request.post<ApiResponse<void>>('/flow-edge/batch-update', edges)
  }
}
