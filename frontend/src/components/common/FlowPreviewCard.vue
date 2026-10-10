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
  flowType?: 'flow' | 'agent'
  nodes?: Record<string, unknown>[]
  edges?: Record<string, unknown>[]
  deleted?: boolean
  /** 折叠态（单行条，不渲染画布）：由父组件受控（flow_done 自动折叠/手动切换） */
  collapsed?: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'toggle'): void
}>()

const router = useRouter()

const instanceId = `flow-preview-${props.flowId}-${Date.now()}`
const { fitView } = useVueFlow(instanceId)

const localNodes = ref<Node[]>([])
const localEdges = ref<Edge[]>([])

const nodeCount = computed(() => props.nodes?.length || 0)

function rebuildGraph(): void {
  localNodes.value = (props.nodes || []).map(n => backendNodeToVueFlow(n as unknown as FlowNode))
  localEdges.value = (props.edges || []).map(e => backendEdgeToVueFlow(e as unknown as FlowEdge))
}

// 折叠→展开时重建并适配视口（折叠期间画布未挂载，nodes watch 不触发）
watch(
  () => props.collapsed,
  (val, old) => {
    if (old && !val) {
      rebuildGraph()
      setTimeout(() => {
        try {
          fitView()
        } catch {
          // ignore
        }
      }, 50)
    }
  }
)

watch(() => props.nodes, rebuildGraph, { immediate: false })
watch(() => props.edges, rebuildGraph, { immediate: false })

onMounted(() => {
  rebuildGraph()
})

function openEditor(): void {
  // 按真实数据 flow_type 选路由名：agent → /agent/edit/:id，否则 → /flow/edit/:id
  const targetName = props.flowType === 'agent' ? 'AgentEdit' : 'FlowEdit'
  router.push({ name: targetName, params: { id: props.flowId } })
}
</script>

<template>
  <!-- 折叠态：单行条，不渲染画布 -->
  <div v-if="collapsed" class="flow-preview-card flow-preview-collapsed" @click="emit('toggle')">
    <span class="flow-name">{{ flowName || `流程 #${flowId}` }}</span>
    <span v-if="nodeCount" class="node-count">{{ nodeCount }} 个节点</span>
    <div class="header-spacer" />
    <el-button
      v-if="!deleted"
      size="small"
      :icon="Edit"
      link
      @click.stop="openEditor"
    >编辑流程</el-button>
    <el-tag v-else type="info" size="small">已删除</el-tag>
    <el-button class="header-btn" :icon="Minus" link size="small" @click.stop="emit('toggle')" />
    <el-button class="header-btn" :icon="Close" link size="small" @click.stop="emit('close')" />
  </div>

  <!-- 展开态：完整画布卡片 -->
  <div v-else class="flow-preview-card">
    <div class="preview-header">
      <span class="flow-name">{{ flowName || `流程 #${flowId}` }}</span>
      <span v-if="nodeCount" class="node-count">{{ nodeCount }} 个节点</span>
      <div class="header-spacer" />
      <el-button v-if="!deleted" size="small" :icon="Edit" @click="openEditor">编辑流程</el-button>
      <el-tag v-else type="info" size="small">已删除</el-tag>
      <el-button class="header-btn" :icon="Minus" link size="small" @click="emit('toggle')" />
      <el-button class="header-btn" :icon="Close" link size="small" @click="emit('close')" />
    </div>
    <div class="preview-canvas">
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

/* 折叠态：单行条 */
.flow-preview-collapsed {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  cursor: pointer;
  background: #f8fafc;
  box-shadow: none;
}
.flow-preview-collapsed:hover {
  background: #f1f5f9;
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
  /* Vue Flow 要求父容器有确定宽高：外层 wrapper 的 max-height 裁剪时
     保证画布仍有可测量尺寸，避免 "parent container needs a width and a height" 警告 */
  min-height: 160px;
  width: 100%;
}
</style>
