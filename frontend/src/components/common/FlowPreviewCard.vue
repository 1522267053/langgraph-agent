<script setup lang="ts">
import { ref, watch, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { Edit, Close, Minus } from '@element-plus/icons-vue'
import { nodeTypes } from '@/components/FlowEditor/nodes'
import { backendNodeToVueFlow, backendEdgeToVueFlow } from '@/utils/flowTransform'
import type { FlowNode, FlowEdge } from '@/types/flow'
import type { Node, Edge } from '@vue-flow/core'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'

const props = defineProps<{
  flowId: number
  flowName?: string
  nodes?: Record<string, unknown>[]
  edges?: Record<string, unknown>[]
  deleted?: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const router = useRouter()

const instanceId = `flow-preview-${props.flowId}-${Date.now()}`
const { fitView } = useVueFlow(instanceId)

const localNodes = ref<Node[]>([])
const localEdges = ref<Edge[]>([])
const minimized = ref(false)

const nodeCount = computed(() => props.nodes?.length || 0)

function rebuildGraph(): void {
  localNodes.value = (props.nodes || []).map(n => backendNodeToVueFlow(n as unknown as FlowNode))
  localEdges.value = (props.edges || []).map(e => backendEdgeToVueFlow(e as unknown as FlowEdge))
  if (!minimized.value) {
    setTimeout(() => {
      try {
        fitView()
      } catch {
        // ignore
      }
    }, 50)
  }
}

watch(() => props.nodes, rebuildGraph, { immediate: false })
watch(() => props.edges, rebuildGraph, { immediate: false })

onMounted(() => {
  rebuildGraph()
})

function toggleMinimize(): void {
  minimized.value = !minimized.value
  if (!minimized.value) {
    setTimeout(() => {
      try {
        fitView()
      } catch {
        // ignore
      }
    }, 50)
  }
}

function openEditor(): void {
  router.push({ name: 'FlowEdit', params: { id: props.flowId } })
}
</script>

<template>
  <div class="flow-preview-card">
    <div class="preview-header">
      <span class="flow-name">{{ flowName || `流程 #${flowId}` }}</span>
      <span v-if="nodeCount" class="node-count">{{ nodeCount }} 个节点</span>
      <div class="header-spacer" />
      <el-button v-if="!deleted" size="small" :icon="Edit" @click="openEditor">编辑流程</el-button>
      <el-tag v-else type="info" size="small">已删除</el-tag>
      <el-button class="header-btn" :icon="Minus" link size="small" @click="toggleMinimize" />
      <el-button class="header-btn" :icon="Close" link size="small" @click="emit('close')" />
    </div>
    <div v-show="!minimized" class="preview-canvas">
      <VueFlow
        :id="instanceId"
        v-model:nodes="localNodes"
        v-model:edges="localEdges"
        :node-types="nodeTypes"
        :nodes-draggable="false"
        :nodes-connectable="false"
        :edges-updatable="false"
        :elements-selectable="false"
        :zoom-on-scroll="true"
        :pan-on-drag="true"
        :prevent-cycling="false"
        fit-view-on-init
      >
        <Background />
        <Controls />
      </VueFlow>
    </div>
  </div>
</template>

<style scoped>
.flow-preview-card {
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid #e2e8f0;
  box-shadow:
    0 2px 15px -3px rgba(0, 0, 0, 0.07),
    0 4px 6px -2px rgba(0, 0, 0, 0.05);
  max-width: 896px;
  margin: 0 auto;
  width: 100%;
}

.preview-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
}

.flow-name {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}

.node-count {
  font-size: 12px;
  color: #94a3b8;
}

.header-spacer {
  flex: 1;
}

.header-btn {
  color: #94a3b8;
  padding: 4px;
}

.header-btn:hover {
  color: #475569;
}

.preview-canvas {
  height: 200px;
  width: 100%;
}
</style>
