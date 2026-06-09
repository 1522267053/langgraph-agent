import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Node, Edge } from '@vue-flow/core'
import type { FlowDetail, FlowNode, FlowEdge, AllNodeType, Flow, FlowIOSchema } from '@/types/flow'
import { flowApi, flowNodeApi, flowEdgeApi } from '@/api/flow'
import { configApi } from '@/api/config'
import { useNodeSchema } from '@/composables/useNodeSchema'
import {
  backendNodeToVueFlow,
  backendEdgeToVueFlow,
  vueFlowGraphToBackend,
  createDefaultNode,
  generateId
} from '@/utils/flowTransform'
import { ElMessage } from 'element-plus'

export const useFlowStore = defineStore('flow', () => {
  const { getDefaultConfig } = useNodeSchema()

  const flowInfo = ref<FlowDetail | null>(null)
  const nodes = ref<Node[]>([])
  const edges = ref<Edge[]>([])
  const selectedNode = ref<Node | null>(null)
  const selectedEdge = ref<Edge | null>(null)
  const flowCards = ref<Flow[]>([])
  const copiedNode = ref<Node | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const nodesVersion = ref(0)
  const edgesVersion = ref(0)
  const subViewParentId = ref<string | null>(null)

  const globalLlmDefaults = ref<Record<string, unknown> | null>(null)
  let _globalLlmLoaded = false

  async function loadGlobalLlmDefaults() {
    if (_globalLlmLoaded) return
    _globalLlmLoaded = true
    try {
      const res = await configApi.getConfig()
      if (res.data.code === 1 && res.data.data) {
        const c = res.data.data
        if (c.provider || c.model || c.base_url) {
          globalLlmDefaults.value = {
            provider: c.provider || '',
            model: c.model || '',
            base_url: c.base_url || ''
          }
        }
      }
    } catch {
      // 加载失败时使用空默认值
    }
  }

  function getLlmDefaultConfig(): Record<string, unknown> | undefined {
    return globalLlmDefaults.value || undefined
  }

  const isEditMode = computed(() => !!flowInfo.value?.id)

  const isInSubView = computed(() => !!subViewParentId.value)

  const subViewParentType = computed(() => {
    if (!subViewParentId.value) return null
    const parentNode = nodes.value.find(n => n.id === subViewParentId.value)
    return (parentNode?.type as string) || null
  })

  const subViewParentLabel = computed(() => {
    if (!subViewParentId.value) return ''
    const parentNode = nodes.value.find(n => n.id === subViewParentId.value)
    return parentNode?.data?.label || '循环体'
  })

  const visibleNodes = computed(() => {
    if (!subViewParentId.value) {
      return nodes.value.filter(n => !n.id.includes('__'))
    }
    const prefix = `${subViewParentId.value}__`
    return nodes.value.filter(n => n.id.startsWith(prefix))
  })

  const visibleEdges = computed(() => {
    if (!subViewParentId.value) {
      return edges.value.filter(e => !e.source.includes('__') && !e.target.includes('__'))
    }
    const prefix = `${subViewParentId.value}__`
    return edges.value.filter(e => e.source.startsWith(prefix) && e.target.startsWith(prefix))
  })

  function enterSubView(loopNodeId: string) {
    selectedNode.value = null
    selectedEdge.value = null
    subViewParentId.value = loopNodeId
    nodesVersion.value++
    edgesVersion.value++
  }

  function exitSubView() {
    selectedNode.value = null
    selectedEdge.value = null
    subViewParentId.value = null
    nodesVersion.value++
    edgesVersion.value++
  }

  function addSubViewNode(type: AllNodeType, position: { x: number; y: number }) {
    if (!subViewParentId.value) return
    const subId = `${subViewParentId.value}__${generateId(type)}`
    const schemaDefaults = getDefaultConfig(type)
    const globalCfg = type === 'llm' ? getLlmDefaultConfig() : {}
    const defaultCfg = { ...schemaDefaults, ...globalCfg }
    const node = createDefaultNode(type, position, nodes.value, defaultCfg)
    node.id = subId
    nodes.value.push(node)
    nodesVersion.value++
    return node
  }

  async function loadFlow(flowId: number) {
    loading.value = true
    try {
      const res = await flowApi.get(flowId)
      if (res.data.code === 1 && res.data.data) {
        flowInfo.value = res.data.data
        nodes.value = (res.data.data.nodes || []).map(backendNodeToVueFlow)
        edges.value = (res.data.data.edges || []).map(backendEdgeToVueFlow)
        nodesVersion.value++
        edgesVersion.value++
      }
    } finally {
      loading.value = false
    }
  }

  async function loadFlowCards(excludeId?: number) {
    try {
      const res = await flowApi.getFlowCards()
      if (res.data.code === 1) {
        flowCards.value = (res.data.data || []).filter(f => f.id !== excludeId)
      }
    } catch {
      // ignore
    }
  }

  function copySelectedNode(): boolean {
    if (!selectedNode.value) {
      return false
    }
    copiedNode.value = JSON.parse(JSON.stringify(selectedNode.value))
    return true
  }

  function pasteNode(position?: { x: number; y: number }): Node | null {
    if (!copiedNode.value) {
      return null
    }
    const newNode: Node = {
      ...JSON.parse(JSON.stringify(copiedNode.value)),
      id: generateId(copiedNode.value.type),
      position: position || {
        x: copiedNode.value.position.x + 50,
        y: copiedNode.value.position.y + 50
      }
    }
    delete (newNode as Record<string, unknown>).selected
    nodes.value.forEach(n => delete (n as Record<string, unknown>).selected)
    nodes.value.push(newNode)
    nodesVersion.value++
    return newNode
  }

  function duplicateNode(node: Node): Node {
    const newNode: Node = {
      ...JSON.parse(JSON.stringify(node)),
      id: generateId(node.type),
      position: {
        x: node.position.x + 50,
        y: node.position.y + 50
      }
    }
    nodes.value.push(newNode)
    nodesVersion.value++
    return newNode
  }

  function addFlowCardNode(
    flow: Flow,
    position: { x: number; y: number },
    parentId?: string | null
  ): Node {
    const inputFields = flow.input_schema?.fields || []
    const outputFields = flow.output_schema?.fields || []

    const input_mappings = inputFields.map(f => ({
      card_field: f.name,
      source: ''
    }))

    const output_mappings = outputFields.map(f => ({
      card_field: f.name,
      target_variable: ''
    }))

    const node: Node = {
      id: generateId('card'),
      type: 'card',
      position,
      data: {
        label: flow.name || '能力卡片',
        config: {
          ref_flow_id: flow.id,
          input_schema: flow.input_schema,
          output_schema: flow.output_schema,
          input_mappings,
          output_mappings
        }
      }
    }
    if (parentId) {
      node.id = `${parentId}__${node.id}`
    }
    nodes.value.push(node)
    nodesVersion.value++
    return node
  }

  function addNode(type: AllNodeType, position: { x: number; y: number }) {
    const schemaDefaults = getDefaultConfig(type)
    const globalCfg = type === 'llm' ? getLlmDefaultConfig() : {}
    const defaultCfg = { ...schemaDefaults, ...globalCfg }
    const node = createDefaultNode(type, position, nodes.value, defaultCfg)
    nodes.value.push(node)
    nodesVersion.value++
    return node
  }

  function removeNode(nodeId: string) {
    const prefix = `${nodeId}__`
    const isLoopNode = nodes.value.some(n => n.id === nodeId && n.type === 'loop')
    nodes.value = nodes.value.filter(n => {
      if (n.id === nodeId) return false
      if (isLoopNode && n.id.startsWith(prefix)) return false
      return true
    })
    edges.value = edges.value.filter(e => {
      if (e.source === nodeId || e.target === nodeId) return false
      if (isLoopNode && (e.source.startsWith(prefix) || e.target.startsWith(prefix))) return false
      return true
    })
    if (subViewParentId.value === nodeId) {
      subViewParentId.value = null
    }
    nodesVersion.value++
    edgesVersion.value++
    if (selectedNode.value?.id === nodeId) {
      selectedNode.value = null
    }
  }

  function updateNodeData(nodeId: string, data: Partial<Node['data']>) {
    const index = nodes.value.findIndex(n => n.id === nodeId)
    if (index !== -1) {
      nodes.value[index] = {
        ...nodes.value[index],
        data: { ...nodes.value[index].data, ...data }
      }
      nodesVersion.value++
    }
  }

  function selectNode(node: Node | null) {
    selectedNode.value = node
    selectedEdge.value = null
  }

  function selectEdge(edge: Edge | null) {
    selectedEdge.value = edge
    selectedNode.value = null
  }

  function removeEdge(edgeId: string) {
    edges.value = edges.value.filter(e => e.id !== edgeId)
    edgesVersion.value++
    if (selectedEdge.value?.id === edgeId) {
      selectedEdge.value = null
    }
  }

  function updateInputSchema(schema: FlowIOSchema) {
    if (flowInfo.value) {
      flowInfo.value.input_schema = schema
    }
  }

  function updateOutputSchema(schema: FlowIOSchema) {
    if (flowInfo.value) {
      flowInfo.value.output_schema = schema
    }
  }

  async function saveFlow(): Promise<boolean> {
    if (!flowInfo.value) return false

    saving.value = true
    try {
      await flowApi.update({
        id: flowInfo.value.id!,
        name: flowInfo.value.name!,
        description: flowInfo.value.description,
        input_schema: flowInfo.value.input_schema,
        output_schema: flowInfo.value.output_schema
      })

      const existingNodes: FlowNode[] = flowInfo.value.nodes || []
      const existingEdges: FlowEdge[] = flowInfo.value.edges || []

      const {
        nodesToCreate,
        nodesToUpdate,
        nodesToDelete,
        edgesToCreate,
        edgesToUpdate,
        edgesToDelete
      } = vueFlowGraphToBackend(
        nodes.value,
        edges.value,
        flowInfo.value.id!,
        existingNodes,
        existingEdges
      )

      await Promise.all([
        ...nodesToDelete.map((id: number) => flowNodeApi.delete(id)),
        ...edgesToDelete.map((id: number) => flowEdgeApi.delete(id))
      ])

      if (nodesToCreate.length > 0) {
        await flowNodeApi.batchCreate(nodesToCreate)
      }
      if (nodesToUpdate.length > 0) {
        await flowNodeApi.batchUpdate(nodesToUpdate)
      }
      if (edgesToCreate.length > 0 || edgesToUpdate.length > 0) {
        await flowEdgeApi.batchSave({
          create: edgesToCreate,
          update: edgesToUpdate
        })
      }

      ElMessage.success('保存成功')
      await loadFlow(flowInfo.value.id!)
      return true
    } catch {
      return false
    } finally {
      saving.value = false
    }
  }

  async function saveAsCard(): Promise<boolean> {
    if (!flowInfo.value?.id) return false
    if ((flowInfo.value as { flow_type?: string }).flow_type === 'agent') {
      ElMessage.warning('智能体类型的流程不能保存为能力卡片')
      return false
    }

    const saved = await saveFlow()
    if (!saved) return false

    try {
      const res = await flowApi.saveAsCard(flowInfo.value.id!)
      if (res.data.code === 1) {
        flowInfo.value.saved_as_card = 1
        ElMessage.success('已保存为能力卡片')
        return true
      }
      return false
    } catch {
      return false
    }
  }

  function resetState() {
    flowInfo.value = null
    nodes.value = []
    edges.value = []
    selectedNode.value = null
    selectedEdge.value = null
    subViewParentId.value = null
    nodesVersion.value++
    edgesVersion.value++
    _globalLlmLoaded = false
  }

  return {
    flowInfo,
    nodes,
    edges,
    selectedNode,
    selectedEdge,
    flowCards,
    copiedNode,
    loading,
    saving,
    isEditMode,
    isInSubView,
    subViewParentType,
    visibleNodes,
    visibleEdges,
    subViewParentId,
    subViewParentLabel,
    nodesVersion,
    edgesVersion,
    loadFlow,
    loadFlowCards,
    addNode,
    addSubViewNode,
    removeNode,
    updateNodeData,
    selectNode,
    selectEdge,
    removeEdge,
    copySelectedNode,
    pasteNode,
    duplicateNode,
    addFlowCardNode,
    updateInputSchema,
    updateOutputSchema,
    saveFlow,
    saveAsCard,
    enterSubView,
    exitSubView,
    resetState,
    loadGlobalLlmDefaults
  }
})
