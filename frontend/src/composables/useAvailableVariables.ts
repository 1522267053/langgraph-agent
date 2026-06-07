import { computed, type ComputedRef } from 'vue'
import type { CascaderOption } from 'element-plus'
import type { Node, Edge } from '@vue-flow/core'
import { useFlowStore } from '@/stores/flowStore'
import { useNodeSchema } from '@/composables/useNodeSchema'
import type { FieldType } from '@/types/flow'
import { VariablePrefix } from '@/constants/variable'

interface NodeOutputVariable {
  name: string
  type?: FieldType
  description?: string
}

const { getOutputVariables: getSchemaOutputVariables } = useNodeSchema()

function getOutputVariablesForNode(node: Node, allNodes?: Node[]): NodeOutputVariable[] {
  const nodeType = node.type
  if (!nodeType) return []

  if (nodeType === 'card') {
    const outputSchema = node.data?.config?.output_schema as
      | { fields?: Array<{ name: string; type?: FieldType; description?: string }> }
      | undefined
    const fields = outputSchema?.fields || []
    if (fields.length > 0) {
      return fields.map(f => ({
        name: f.name,
        type: f.type || 'string',
        description: f.description
      }))
    }
    return []
  }

  if (nodeType === 'loop') {
    const nodes = allNodes || []
    const prefix = `${node.id}__`
    const endNode = nodes.find(n => n.id.startsWith(prefix) && n.type === 'end')
    if (endNode) {
      const outputVars = (endNode.data?.config as Record<string, unknown>)?.output_variables as
        | Array<{ name: string; type?: string; source?: string }>
        | undefined
      if (outputVars && outputVars.length > 0) {
        return outputVars
          .filter(v => v.name)
          .map(v => ({
            name: v.name,
            type: 'array' as FieldType,
            description: v.source || ''
          }))
      }
    }
    return []
  }

  const schemaVars = getSchemaOutputVariables(nodeType)
  if (schemaVars.length > 0) {
    return schemaVars.map(v => ({
      name: v.name,
      type: (v.type as FieldType) || 'string',
      description: ''
    }))
  }

  return []
}

function formatVariableLabel(variable: NodeOutputVariable): string {
  if (variable.type) {
    return `${variable.name} (${variable.type})`
  }
  return variable.name
}

function getUpstreamNodeIds(
  nodeId: string,
  nodes: Node[],
  edges: Edge[],
  visited: Set<string> = new Set()
): string[] {
  if (visited.has(nodeId)) return []
  visited.add(nodeId)

  const incomingEdges = edges.filter(e => e.target === nodeId)
  const upstreamIds: string[] = []

  for (const edge of incomingEdges) {
    const sourceId = edge.source
    if (!upstreamIds.includes(sourceId)) {
      upstreamIds.push(sourceId)
      const furtherUpstream = getUpstreamNodeIds(sourceId, nodes, edges, visited)
      for (const id of furtherUpstream) {
        if (!upstreamIds.includes(id)) {
          upstreamIds.push(id)
        }
      }
    }
  }

  return upstreamIds
}

export function useAvailableVariables(
  currentNodeId: string,
  options?: { allNodes?: boolean }
): {
  variableOptions: ComputedRef<CascaderOption[]>
  upstreamNodes: ComputedRef<Node[]>
} {
  const flowStore = useFlowStore()
  const showAllNodes = options?.allNodes ?? false

  // 子视图模式：使用可见的节点/边（过滤后的），主视图模式使用全量
  const effectiveNodes = computed(() => {
    if (flowStore.isInSubView && !showAllNodes) {
      return flowStore.visibleNodes
    }
    return flowStore.nodes
  })

  const effectiveEdges = computed(() => {
    if (flowStore.isInSubView && !showAllNodes) {
      return flowStore.visibleEdges
    }
    return flowStore.edges
  })

  const upstreamNodeIds = computed(() => {
    if (!currentNodeId) return []
    return getUpstreamNodeIds(currentNodeId, effectiveNodes.value, effectiveEdges.value)
  })

  const upstreamNodes = computed(() => {
    if (showAllNodes) return flowStore.nodes
    return effectiveNodes.value.filter(n => upstreamNodeIds.value.includes(n.id))
  })

  function resolveMappingType(source: string): FieldType | undefined {
    const parts = source.split('.')
    if (parts[0] === VariablePrefix.INPUT && parts.length >= 2) {
      const field = flowStore.flowInfo?.input_schema?.fields?.find(f => f.name === parts[1])
      return field?.type
    } else if (parts[0] === VariablePrefix.NODES && parts.length >= 3) {
      const sourceNode = flowStore.nodes.find(n => n.id === parts[1])
      if (sourceNode) {
        const outputVars = getOutputVariablesForNode(sourceNode, flowStore.nodes)
        const match = outputVars.find(v => v.name === parts[2])
        return match?.type
      }
    } else if (parts[0] === VariablePrefix.VARIABLES && parts.length >= 2) {
      if (parts[1] === 'loop_index' || parts[1] === 'loop_count') return 'number'
      if (parts[1] === 'loop_item') {
        const parentNode = flowStore.nodes.find(n => n.id === flowStore.subViewParentId)
        if (parentNode?.data?.config) {
          return (parentNode.data.config as Record<string, unknown>).for_each_item_type as
            | FieldType
            | undefined
        }
      }
    }
    return undefined
  }

  // 子视图选项：input 分组（2 级）+ 循环输入+子图节点输出在 nodes 分组（3 级）+ 循环内置变量在 variables 分组（2 级）
  const subViewOptions = computed<CascaderOption[]>(() => {
    if (!flowStore.isInSubView || !flowStore.subViewParentId) return []

    const parentNode = flowStore.nodes.find(n => n.id === flowStore.subViewParentId)
    if (!parentNode?.data?.config) return []

    const loopConfig = parentNode.data.config as Record<string, unknown>
    const inputMappings = (
      (loopConfig?.input_mappings || []) as Array<{
        card_field: string
        source: string
        type?: string
      }>
    ).filter(Boolean)
    const nodeKey = (parentNode?.data?.node_key as string) || flowStore.subViewParentId

    const options: CascaderOption[] = []

    // input 分组：循环输入映射（2 级：input → field，VariableSelector 会自动转换为 nodes.<loopKey>.input_<field>）
    const inputChildren: CascaderOption[] = inputMappings
      .filter(m => m.card_field)
      .map(m => {
        const resolvedType = m.type || resolveMappingType(m.source)
        return {
          value: m.card_field,
          label: resolvedType ? `${m.card_field} (${resolvedType})` : m.card_field,
          disabled: false
        }
      })
    if (inputChildren.length > 0) {
      options.push({
        value: 'input',
        label: '循环输入',
        children: inputChildren
      })
    }

    // nodes 分组：循环输入 + 子图节点输出（3 级：nodes → key → field）
    const nodesChildren: CascaderOption[] = []

    const mappingItems: CascaderOption[] = inputMappings
      .filter(m => m.card_field)
      .map(m => {
        const resolvedType = m.type || resolveMappingType(m.source)
        return {
          value: `input_${m.card_field}`,
          label: resolvedType ? `${m.card_field} (${resolvedType})` : m.card_field,
          disabled: false
        }
      })
    if (mappingItems.length > 0) {
      nodesChildren.push({
        value: nodeKey,
        label: '循环输入',
        children: mappingItems
      })
    }

    for (const node of upstreamNodes.value) {
      if (['start', 'condition', 'end'].includes(node.type || '')) continue
      const outputVars = getOutputVariablesForNode(node, effectiveNodes.value)
      if (outputVars.length === 0) continue
      nodesChildren.push({
        value: node.id,
        label: (node.data?.label as string) || node.type || '未知节点',
        children: outputVars.map(v => ({
          value: v.name,
          label: formatVariableLabel(v),
          disabled: false
        }))
      })
    }

    if (nodesChildren.length > 0) {
      options.push({
        value: 'nodes',
        label: '数据源',
        children: nodesChildren
      })
    }

    // variables 分组：循环内置变量（2 级：variables → name）
    const loopMode = (loopConfig?.loop_mode as string) || 'count'
    const builtinItems: CascaderOption[] = [
      { value: 'loop_index', label: 'loop_index (number)', disabled: false },
      { value: 'loop_count', label: 'loop_count (number)', disabled: false }
    ]
    if (loopMode === 'for_each') {
      const itemType = loopConfig?.for_each_item_type as FieldType | undefined
      builtinItems.push({
        value: 'loop_item',
        label: itemType ? `loop_item (${itemType})` : 'loop_item',
        disabled: false
      })
    }
    if (builtinItems.length > 0) {
      options.push({
        value: 'variables',
        label: '循环变量',
        children: builtinItems
      })
    }

    return options
  })

  // 主视图输入选项
  const mainInputOptions = computed<CascaderOption[]>(() => {
    if (flowStore.isInSubView) return []

    const inputSchema = flowStore.flowInfo?.input_schema
    const fields = inputSchema?.fields || []
    const flowType = flowStore.flowInfo?.flow_type

    const opts: CascaderOption[] = []

    for (const field of fields) {
      if (!field.name || (field.name === 'message' && flowType === 'agent')) continue
      opts.push({
        value: field.name,
        label: field.type ? `${field.name} (${field.type})` : field.name,
        disabled: false
      })
    }

    if (flowType === 'agent') {
      opts.unshift({ value: 'message', label: 'message (string)', disabled: false })
    }

    return opts
  })

  const nodeOutputOptions = computed<CascaderOption[]>(() => {
    return upstreamNodes.value
      .filter(node => !['start', 'condition', 'end'].includes(node.type || ''))
      .map(node => {
        const outputVars = getOutputVariablesForNode(node, effectiveNodes.value)
        return {
          value: node.id,
          label: (node.data?.label as string) || node.type || '未知节点',
          children: outputVars.map(v => ({
            value: v.name,
            label: formatVariableLabel(v),
            disabled: false
          }))
        }
      })
      .filter(node => node.children && node.children.length > 0)
  })

  const variableOptions = computed<CascaderOption[]>(() => {
    const options: CascaderOption[] = []

    if (flowStore.isInSubView) {
      options.push(...subViewOptions.value)
    } else {
      if (mainInputOptions.value.length > 0) {
        options.push({
          value: 'input',
          label: '流程输入',
          children: mainInputOptions.value
        })
      }

      if (nodeOutputOptions.value.length > 0) {
        options.push({
          value: 'nodes',
          label: '节点输出',
          children: nodeOutputOptions.value
        })
      }
    }

    return options
  })

  return {
    variableOptions,
    upstreamNodes
  }
}

export function formatVariablePath(path: string[]): string {
  if (path.length === 0) return ''
  return path.join('.')
}

export function parseVariablePath(path: string): string[] {
  if (!path) return []
  return path.split('.')
}

export function getVariableTypeByPath(
  path: string,
  currentNodeId: string,
  nodes: Node[],
  edges: Edge[],
  inputSchema: { fields?: Array<{ name: string; type?: FieldType }> } | null,
  _flowType?: string
): FieldType | undefined {
  if (!path) return undefined

  const parts = parseVariablePath(path)
  if (parts.length === 0) return undefined

  if (parts[0] === 'input') {
    if (parts[1] === 'message') {
      return 'string'
    }
    const field = inputSchema?.fields?.find(f => f.name === parts[1])
    return field?.type
  }

  if (parts[0] === 'nodes' && parts.length >= 3) {
    const nodeId = parts[1]
    const varName = parts[2]
    const node = nodes.find(n => n.id === nodeId)
    if (node) {
      const outputVars = getOutputVariablesForNode(node, nodes)
      const outputVar = outputVars.find(v => v.name === varName)
      return outputVar?.type
    }
  }

  if (parts[0] === 'variables' && parts.length >= 2) {
    if (parts[1] === 'loop_index' || parts[1] === 'loop_count') return 'number'
    if (parts[1] === 'loop_item') {
      const parentNode = nodes.find(n => currentNodeId.startsWith(`${n.id}__`) && n.type === 'loop')
      if (parentNode?.data?.config) {
        return (parentNode.data.config as Record<string, unknown>).for_each_item_type as
          | FieldType
          | undefined
      }
    }
  }

  return undefined
}
