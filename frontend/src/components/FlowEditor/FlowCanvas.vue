<script setup lang="ts">
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { VueFlow, useVueFlow, type Node, type Edge } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import { nodeTypes } from './nodes'
import { useFlowStore } from '@/stores/flowStore'
import type { AllNodeType } from '@/types/flow'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

const store = useFlowStore()
const {
  onConnect,
  onNodesChange,
  onEdgesChange,
  onNodeClick,
  onNodeDoubleClick,
  onEdgeClick,
  onPaneClick,
  onNodesInitialized,
  project,
  findNode,
  fitView
} = useVueFlow()

const flowContainer = ref<HTMLElement | null>(null)
let pendingFitView = false

onNodesInitialized(() => {
  if (pendingFitView) {
    pendingFitView = false
    fitView({ padding: 0.2 })
  }
})

const defaultEdgeOptions = {
  markerEnd: 'arrowclosed',
  style: {
    stroke: '#b1b3b8',
    strokeWidth: 2,
    strokeDasharray: '6,3'
  },
  class: 'flow-edge',
  animated: true
}

const emit = defineEmits<{
  (e: 'node-click', node: Node): void
  (e: 'edge-click', edge: Edge): void
  (e: 'pane-click'): void
}>()

const localNodes = ref<Node[]>([])
const localEdges = ref<Edge[]>([])
const isSyncing = ref(false)

watch(
  () => store.nodesVersion,
  () => {
    if (!isSyncing.value) {
      localNodes.value = store.visibleNodes.map(node => ({ ...node, data: { ...node.data } }))
      if (store.visibleNodes.length > 0) {
        pendingFitView = true
      }
    }
  },
  { immediate: true }
)

watch(
  () => store.edgesVersion,
  () => {
    if (!isSyncing.value) {
      localEdges.value = store.visibleEdges.map(edge => {
        const isToolEdge = edge.sourceHandle === 'tools'
        if (isToolEdge) {
          return {
            ...edge,
            style: {
              stroke: edge.source?.includes('human') ? '#67c23a' : '#e6a23c',
              strokeWidth: 2,
              strokeDasharray: '5,5'
            },
            class: 'tool-edge',
            animated: true
          }
        }
        return {
          ...edge,
          style: {
            stroke: '#b1b3b8',
            strokeWidth: 2,
            strokeDasharray: '6,3'
          },
          class: 'flow-edge',
          animated: true
        }
      })
    }
  },
  { immediate: true }
)

onConnect(params => {
  if (params.source === params.target) {
    ElMessage.warning('不允许自连接')
    return
  }
  const isSourceTool = params.sourceHandle === 'tools'
  const isTargetTool = params.targetHandle === 'tools'
  if (isSourceTool !== isTargetTool) {
    ElMessage.warning('工具连接只能与工具连接点相连')
    return
  }
  const isToolEdge = isSourceTool
  if (isToolEdge) {
    const targetNode = findNode(params.target)
    if (targetNode?.type !== 'llm') {
      ElMessage.warning('工具边只能连接到大模型调用节点')
      return
    }
  }
  localEdges.value.push({
    id: `edge_${Date.now()}`,
    source: params.source,
    target: params.target,
    sourceHandle: params.sourceHandle,
    targetHandle: params.targetHandle,
    ...(isToolEdge
      ? {
          style: {
            stroke: '#e6a23c',
            strokeWidth: 2,
            strokeDasharray: '5,5'
          },
          class: 'tool-edge',
          animated: true
        }
      : {
          style: {
            stroke: '#b1b3b8',
            strokeWidth: 2,
            strokeDasharray: '6,3'
          },
          class: 'flow-edge',
          animated: true
        })
  })
  syncEdgesToStore()
})

onNodesChange(() => {
  syncNodesToStore()
})

onEdgesChange(() => {
  syncEdgesToStore()
})

onNodeClick((event: { node: Node }) => {
  store.selectNode(event.node)
  emit('node-click', event.node)
})

onNodeDoubleClick(({ node }: { node: Node }) => {
  if (node.type === 'loop' && !store.isInSubView) {
    store.enterSubView(node.id)
  }
})

onEdgeClick((event: { edge: Edge }) => {
  store.selectEdge(event.edge)
  emit('edge-click', event.edge)
})

onPaneClick(() => {
  store.selectNode(null)
  store.selectEdge(null)
  emit('pane-click')
})

function syncNodesToStore() {
  for (const localNode of localNodes.value) {
    const storeNode = store.nodes.find(n => n.id === localNode.id)
    if (storeNode) {
      storeNode.position = localNode.position
      storeNode.selected = localNode.selected
    }
  }
}

function syncEdgesToStore() {
  isSyncing.value = true
  const localIds = new Set(localEdges.value.map(e => e.id))
  const isSubView = store.isInSubView
  const prefix = isSubView ? `${store.subViewParentId}__` : ''

  function isCurrentViewEdge(edge: Edge): boolean {
    if (isSubView) {
      return edge.source.startsWith(prefix) && edge.target.startsWith(prefix)
    }
    return !edge.source.includes('__') && !edge.target.includes('__')
  }

  for (const localEdge of localEdges.value) {
    const storeEdge = store.edges.find(e => e.id === localEdge.id)
    if (storeEdge) {
      Object.assign(storeEdge, localEdge)
    } else {
      store.edges.push({ ...localEdge })
    }
  }
  store.edges = store.edges.filter(e => {
    if (localIds.has(e.id)) return true
    return !isCurrentViewEdge(e)
  })
  store.edgesVersion++
  nextTick(() => {
    isSyncing.value = false
  })
}

function onDragOver(event: DragEvent) {
  event.preventDefault()
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = 'move'
  }
}

function onDrop(event: DragEvent) {
  const type = event.dataTransfer?.getData('application/vueflow') as AllNodeType
  if (!type || !flowContainer.value) return

  const bounds = flowContainer.value.getBoundingClientRect()
  const position = project({
    x: event.clientX - bounds.left,
    y: event.clientY - bounds.top
  })

  if (store.isInSubView) {
    store.addSubViewNode(type, position)
  } else {
    store.addNode(type, position)
  }
}

function handleKeyDown(event: KeyboardEvent) {
  if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) {
    return
  }

  if (event.key === 'Delete' || event.key === 'Backspace') {
    if (store.selectedNode) {
      ElMessageBox.confirm('确定要删除该节点吗？', '删除确认', {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      })
        .then(() => {
          store.removeNode(store.selectedNode!.id)
        })
        .catch(() => {})
    } else if (store.selectedEdge) {
      store.removeEdge(store.selectedEdge.id)
    }
    return
  }

  if (event.ctrlKey || event.metaKey) {
    if (event.key === 'c' || event.key === 'C') {
      if (store.selectedNode) {
        store.copySelectedNode()
      }
      return
    }
    if (event.key === 'v' || event.key === 'V') {
      const newNode = store.pasteNode()
      if (newNode) {
        nextTick(() => {
          localNodes.value.forEach(n => {
            n.selected = n.id === newNode.id
          })
          const localNode = localNodes.value.find(n => n.id === newNode.id)
          store.selectNode(localNode || newNode)
        })
      }
      return
    }
  }
}

onMounted(() => {
  localNodes.value = store.visibleNodes.map(node => ({ ...node, data: { ...node.data } }))
  localEdges.value = store.visibleEdges.map(edge => {
    const isToolEdge = edge.sourceHandle === 'tools'
    if (isToolEdge) {
      return {
        ...edge,
        style: {
          stroke: edge.source?.includes('human') ? '#67c23a' : '#e6a23c',
          strokeWidth: 2,
          strokeDasharray: '5,5'
        },
        class: 'tool-edge',
        animated: true
      }
    }
    return {
      ...edge,
      style: {
        stroke: '#b1b3b8',
        strokeWidth: 2,
        strokeDasharray: '6,3'
      },
      class: 'flow-edge',
      animated: true
    }
  })
  window.addEventListener('keydown', handleKeyDown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeyDown)
})
</script>

<template>
  <div ref="flowContainer" class="flow-canvas" :class="{ 'sub-view-mode': store.isInSubView }">
    <VueFlow
      v-model:nodes="localNodes"
      v-model:edges="localEdges"
      :node-types="nodeTypes as any"
      :default-edge-options="defaultEdgeOptions"
      fit-view-on-init
      @dragover.prevent="onDragOver"
      @drop.prevent="onDrop"
    >
      <Background />
      <Controls />
      <MiniMap />
    </VueFlow>
  </div>
</template>

<style scoped>
.flow-canvas {
  width: 100%;
  height: 100%;
  position: relative;
  background: #f8fafc;
}

.flow-canvas.sub-view-mode {
  background: #fefce8;
}
</style>

<style>
.tool-edge {
  stroke-dasharray: 5, 5;
}

.tool-edge:hover {
  stroke-width: 3px !important;
}

.flow-edge {
  stroke-dasharray: 6, 3;
}

.flow-edge:hover {
  stroke-width: 3px !important;
  stroke: #909399 !important;
}
</style>
