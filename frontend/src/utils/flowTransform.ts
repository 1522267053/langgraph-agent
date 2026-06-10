import type { Node, Edge } from '@vue-flow/core'
import type {
  FlowNode,
  FlowEdge,
  FlowNodeCreate,
  FlowNodeUpdate,
  FlowEdgeCreate,
  FlowEdgeUpdate,
  AllNodeType
} from '@/types/flow'
import { ALL_NODE_TYPES } from '@/constants/nodeTypes'

export function generateId(type?: string): string {
  const suffix = Math.random().toString(36).slice(2, 8)
  return type ? `${type}_${suffix}` : `node_${suffix}`
}

export function backendNodeToVueFlow(node: FlowNode): Node {
  const config = node.base_config || {}

  // 将 ref_flow_id 保存到 config（用于 CARD 节点）
  if (node.ref_flow_id) {
    config.ref_flow_id = node.ref_flow_id
  }

  return {
    id: node.node_key || generateId(),
    type: node.node_type || 'card',
    position: { x: node.position_x || 0, y: node.position_y || 0 },
    data: {
      label: node.node_name || '',
      config: config
    }
  }
}

export function vueFlowNodeToBackend(
  node: Node,
  flowId: number,
  existingId?: number
): FlowNodeCreate | FlowNodeUpdate {
  const config = node.data?.config || {}

  // 从 config 中提取 ref_flow_id，用于 CARD 节点
  const refFlowId = config.ref_flow_id as number | undefined

  const baseData = {
    flow_id: flowId,
    node_key: node.id,
    node_type: node.type as AllNodeType,
    node_name: node.data?.label || '',
    position_x: node.position.x,
    position_y: node.position.y,
    base_config: config,
    ref_flow_id: refFlowId ?? undefined
  }
  if (existingId) {
    return { ...baseData, id: existingId } as FlowNodeUpdate
  }
  return baseData as FlowNodeCreate
}

export function backendEdgeToVueFlow(edge: FlowEdge): Edge {
  return {
    id: `edge_${edge.id}`,
    source: edge.source_node_key || '',
    target: edge.target_node_key || '',
    sourceHandle: edge.source_handle || undefined,
    targetHandle: edge.target_handle || undefined,
    label: edge.label,
    data: edge.condition
  }
}

export function vueFlowEdgeToBackend(
  edge: Edge,
  flowId: number,
  existingId?: number
): FlowEdgeCreate | FlowEdgeUpdate {
  const baseData = {
    flow_id: flowId,
    source_node_key: edge.source,
    target_node_key: edge.target,
    source_handle: edge.sourceHandle ?? undefined,
    target_handle: edge.targetHandle ?? undefined,
    label: edge.label as string | undefined,
    condition: edge.data
  }
  if (existingId) {
    return { ...baseData, id: existingId } as FlowEdgeUpdate
  }
  return baseData as FlowEdgeCreate
}

export function vueFlowGraphToBackend(
  nodes: Node[],
  edges: Edge[],
  flowId: number,
  existingNodes: FlowNode[] = [],
  existingEdges: FlowEdge[] = []
): {
  nodesToCreate: FlowNodeCreate[]
  nodesToUpdate: FlowNodeUpdate[]
  nodesToDelete: number[]
  edgesToCreate: FlowEdgeCreate[]
  edgesToUpdate: FlowEdgeUpdate[]
  edgesToDelete: number[]
} {
  const existingNodeMap = new Map(existingNodes.map(n => [n.node_key, n]))
  const existingEdgeMap = new Map(
    existingEdges.map(e => [`${e.source_node_key}_${e.target_node_key}_${e.source_handle || ''}`, e])
  )

  const nodesToCreate: FlowNodeCreate[] = []
  const nodesToUpdate: FlowNodeUpdate[] = []
  const nodeKeys = new Set(nodes.map(n => n.id))

  nodes.forEach(node => {
    const existing = existingNodeMap.get(node.id)
    if (existing?.id) {
      nodesToUpdate.push(vueFlowNodeToBackend(node, flowId, existing.id) as FlowNodeUpdate)
    } else {
      nodesToCreate.push(vueFlowNodeToBackend(node, flowId) as FlowNodeCreate)
    }
  })

  const nodesToDelete = existingNodes
    .filter(n => n.node_key && !nodeKeys.has(n.node_key))
    .map(n => n.id!)
    .filter(Boolean)

  const edgesToCreate: FlowEdgeCreate[] = []
  const edgesToUpdate: FlowEdgeUpdate[] = []
  const edgeKeys = new Set(edges.map(e => `${e.source}_${e.target}_${e.sourceHandle || ''}`))

  edges.forEach(edge => {
    const key = `${edge.source}_${edge.target}_${edge.sourceHandle || ''}`
    const existing = existingEdgeMap.get(key)
    if (existing?.id) {
      edgesToUpdate.push(vueFlowEdgeToBackend(edge, flowId, existing.id) as FlowEdgeUpdate)
    } else {
      edgesToCreate.push(vueFlowEdgeToBackend(edge, flowId) as FlowEdgeCreate)
    }
  })

  const edgesToDelete = existingEdges
    .filter(e => {
      const key = `${e.source_node_key}_${e.target_node_key}_${e.source_handle || ''}`
      return !edgeKeys.has(key)
    })
    .map(e => e.id!)
    .filter(Boolean)

  return {
    nodesToCreate,
    nodesToUpdate,
    nodesToDelete,
    edgesToCreate,
    edgesToUpdate,
    edgesToDelete
  }
}

function getNextLabel(baseLabel: string, existingLabels: string[]): string {
  if (!existingLabels.includes(baseLabel)) {
    return baseLabel
  }
  let num = 2
  while (existingLabels.includes(`${baseLabel} ${num}`)) {
    num++
  }
  return `${baseLabel} ${num}`
}

export function createDefaultNode(
  type: AllNodeType,
  position: { x: number; y: number },
  existingNodes?: Node[],
  defaultConfig?: Record<string, unknown>
): Node {
  const nodeType = ALL_NODE_TYPES.find(n => n.value === type)
  const baseLabel = nodeType?.label || type

  const existingLabels = (existingNodes || [])
    .filter(n => n.type === type)
    .map(n => (n.data?.label as string) || '')
  const label = getNextLabel(baseLabel, existingLabels)

  return {
    id: generateId(type),
    type,
    position,
    data: {
      label,
      config: defaultConfig ? { ...defaultConfig } : {}
    }
  }
}
