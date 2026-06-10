<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useFlowStore } from '@/stores/flowStore'
import { executionApi } from '@/api/execution'
import { ElMessage } from 'element-plus'
import { Plus, Setting } from '@element-plus/icons-vue'
import type { FlowExecution } from '@/types/execution'
import { useFlowExecution } from '@/composables/useFlowExecution'
import { useIsMobile } from '@/composables/useIsMobile'
import FlowCanvas from '@/components/FlowEditor/FlowCanvas.vue'
import NodePanel from '@/components/FlowEditor/NodePanel.vue'
import ConfigPanel from '@/components/FlowEditor/ConfigPanel.vue'
import Toolbar from '@/components/FlowEditor/Toolbar.vue'
import ExecutionPanel from '@/components/FlowEditor/ExecutionPanel.vue'
import CreateFlowDialog from '@/components/FlowEditor/CreateFlowDialog.vue'
import ExecuteFlowDialog from '@/components/FlowEditor/ExecuteFlowDialog.vue'
import HumanInputDialog from '@/components/FlowEditor/HumanInputDialog.vue'

const route = useRoute()
const router = useRouter()
const store = useFlowStore()

const {
  currentExecution,
  nodeExecutions,
  streamingContent,
  isStreamRunning,
  flowTodos,
  isRunning,
  attachedFiles,
  showHumanInputDialog,
  humanInputQuestion,
  humanInputContext,
  humanInputLoading,
  humanInputMessages,
  startStream,
  resumeFromHistory,
  loadExecutionDetail,
  submitHumanInput,
  stopExecution,
  cancelStream
} = useFlowExecution()

const flowId = ref<number | null>(null)
const showCreateDialog = ref(false)
const showExecuteDialog = ref(false)
const showExecutionPanel = ref(false)
const pollingTimer = ref<number | null>(null)

// 历史状态
const historyList = ref<FlowExecution[]>([])
const historyTotal = ref(0)
const historyLoading = ref(false)
const executionDetailLoading = ref(false)
const historyPage = ref(1)
const historyPageSize = ref(10)

// 面板折叠
const nodePanelCollapsed = ref(false)
const configPanelCollapsed = ref(false)

const { isMobile } = useIsMobile()
const mobileNodePanelOpen = ref(false)
const mobileConfigPanelOpen = ref(false)

function openMobileNodePanel() {
  mobileNodePanelOpen.value = true
}

function closeMobileNodePanel() {
  mobileNodePanelOpen.value = false
}

function openMobileConfigPanel() {
  mobileConfigPanelOpen.value = true
}

function closeMobileConfigPanel() {
  mobileConfigPanelOpen.value = false
}

watch(
  () => store.selectedNode,
  node => {
    if (isMobile.value) {
      if (node) {
        mobileConfigPanelOpen.value = true
      }
    }
  }
)

watch(
  () => store.selectedEdge,
  edge => {
    if (isMobile.value) {
      if (edge) {
        mobileConfigPanelOpen.value = true
      }
    }
  }
)

function handleMobileOverlayClick() {
  closeMobileNodePanel()
  closeMobileConfigPanel()
}

const inputFields = computed(() => store.flowInfo?.input_schema?.fields || [])

const isAgentMode = computed(() => route.path.startsWith('/agent'))

const nodeCount = computed(() => store.nodes.length)
const edgeCount = computed(() => store.edges.length)

const currentTime = ref('')
let timeTimer: number | null = null

function updateTime() {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  currentTime.value = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
}

// 监听路由参数变化，处理同一组件复用时跨流程导航的状态重置
watch(
  () => route.params.id,
  async (newId, oldId) => {
    if (!newId || newId === oldId) return
    stopPolling()
    cancelStream()
    currentExecution.value = null
    nodeExecutions.value = []
    streamingContent.value = {}
    historyList.value = []
    historyTotal.value = 0
    showExecutionPanel.value = false
    showHumanInputDialog.value = false
    flowTodos.value = []
    flowId.value = parseInt(newId as string)
    await store.loadFlow(flowId.value)
    const executionId = route.query.executionId as string
    if (executionId) {
      await loadExecutionDetailWithPanel(parseInt(executionId))
    }
  }
)

onMounted(async () => {
  updateTime()
  timeTimer = window.setInterval(updateTime, 1000)
  store.loadGlobalLlmDefaults()
  const id = route.params.id as string
  const executionId = route.query.executionId as string

  if (id) {
    flowId.value = parseInt(id)
    await store.loadFlow(flowId.value)

    if (executionId) {
      await loadExecutionDetailWithPanel(parseInt(executionId))
    }
  } else {
    showCreateDialog.value = true
  }
})

onUnmounted(() => {
  store.resetState()
  stopPolling()
  cancelStream()
  if (timeTimer) {
    clearInterval(timeTimer)
    timeTimer = null
  }
})

async function loadExecutionDetailWithPanel(executionId: number) {
  try {
    await loadExecutionDetail(executionId)
    showExecutionPanel.value = true
    if (currentExecution.value?.status === 1) {
      startPolling()
    }
  } catch {
    // ignore
  }
}

async function handleFlowCreated(id: number) {
  flowId.value = id
  router.replace(isAgentMode.value ? `/agent/edit/${id}` : `/flow/edit/${id}`)
  await store.loadFlow(id)
}

async function handleSave() {
  if (!flowId.value) {
    ElMessage.warning('请先创建流程')
    return
  }
  await store.saveFlow()
}

async function handleExecute() {
  if (!flowId.value) {
    ElMessage.warning(isAgentMode.value ? '请先创建Agent' : '请先创建流程')
    return
  }
  await store.saveFlow()
  if (isAgentMode.value) {
    router.push(`/chat/${flowId.value}`)
  } else {
    showExecuteDialog.value = true
  }
}

function handleConfirmExecute(
  input: Record<string, unknown>,
  files: Array<{ id: number; original_name: string; mime_type: string }>
) {
  if (!flowId.value) return
  showExecuteDialog.value = false
  showExecutionPanel.value = true
  startStream(flowId.value, input, files)
}

function stopExecutionWithPolling() {
  stopPolling()
  stopExecution()
}

function startPolling() {
  stopPolling()
  pollingTimer.value = window.setInterval(async () => {
    if (!currentExecution.value?.id) return
    try {
      const [execRes, nodesRes] = await Promise.all([
        executionApi.get(currentExecution.value.id),
        executionApi.getNodes(currentExecution.value.id)
      ])
      if (execRes.data.code === 1) {
        currentExecution.value = execRes.data.data
      }
      if (nodesRes.data.code === 1 && !isStreamRunning.value) {
        nodeExecutions.value = nodesRes.data.data
      }
      if (!isRunning.value) {
        stopPolling()
      }
    } catch {
      stopPolling()
    }
  }, 1000)
}

function stopPolling() {
  if (pollingTimer.value) {
    clearInterval(pollingTimer.value)
    pollingTimer.value = null
  }
}

async function loadHistory(page: number, pageSize: number) {
  if (!flowId.value) return
  historyLoading.value = true
  try {
    const res = await executionApi.page({
      condition: { flow_id: flowId.value } as Partial<FlowExecution>,
      page,
      page_size: pageSize,
      order_by: 'id',
      is_asc: false
    })
    if (res.data.code === 1) {
      historyList.value = res.data.data.items || []
      historyTotal.value = res.data.data.total || 0
    }
  } finally {
    historyLoading.value = false
  }
}

async function viewExecution(exec: FlowExecution) {
  streamingContent.value = {}
  executionDetailLoading.value = true
  try {
    if (exec.id) {
      await loadExecutionDetail(exec.id)
    }
  } finally {
    executionDetailLoading.value = false
  }
}

async function handleResumeExecution(exec: FlowExecution) {
  currentExecution.value = exec
  showExecutionPanel.value = true
  await resumeFromHistory(exec)
}

function closeExecutionPanel() {
  showExecutionPanel.value = false
  stopPolling()
  cancelStream()
}

function showHistoryFromDialog() {
  showExecuteDialog.value = false
  showExecutionPanel.value = true
  loadHistory(historyPage.value, historyPageSize.value)
}

function handleShowHistory() {
  showExecutionPanel.value = true
  loadHistory(historyPage.value, historyPageSize.value)
}
</script>

<template>
  <div v-loading="store.loading" class="flow-edit-page">
    <Toolbar
      :is-agent="isAgentMode"
      @save="handleSave"
      @execute="handleExecute"
      @show-history="handleShowHistory"
    />

    <div class="editor-container">
      <NodePanel
        :collapsed="isMobile ? false : nodePanelCollapsed"
        :is-agent="isAgentMode"
        :mobile-open="mobileNodePanelOpen"
        @toggle="nodePanelCollapsed = !nodePanelCollapsed"
        @close-mobile="closeMobileNodePanel"
      />
      <FlowCanvas />
      <div v-if="store.isInSubView" class="sub-view-breadcrumb">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item @click="store.exitSubView()">
            <a class="breadcrumb-link">主流程</a>
          </el-breadcrumb-item>
          <el-breadcrumb-item>
            {{ store.subViewParentLabel || '循环体' }}
          </el-breadcrumb-item>
        </el-breadcrumb>
      </div>
      <ConfigPanel
        :collapsed="isMobile ? false : configPanelCollapsed"
        :is-agent-mode="isAgentMode"
        :mobile-open="mobileConfigPanelOpen"
        @toggle="configPanelCollapsed = !configPanelCollapsed"
        @close-mobile="closeMobileConfigPanel"
      />
      <ExecutionPanel
        :visible="showExecutionPanel"
        :execution="currentExecution"
        :node-executions="nodeExecutions"
        :streaming-content="streamingContent"
        :is-stream-running="isStreamRunning"
        :attached-files="attachedFiles"
        :history-list="historyList"
        :history-total="historyTotal"
        :history-loading="historyLoading"
        :history-page="historyPage"
        :history-page-size="historyPageSize"
        :is-running="isRunning"
        :execution-detail-loading="executionDetailLoading"
        :todos="flowTodos"
        @close="closeExecutionPanel"
        @stop="stopExecutionWithPolling"
        @load-history="loadHistory"
        @view-execution="viewExecution"
        @resume-execution="handleResumeExecution"
        @update:history-page="historyPage = $event"
        @update:history-page-size="historyPageSize = $event"
      />

      <div v-if="isMobile" class="mobile-floating-btns">
        <button class="mobile-fab" title="添加节点" @click="openMobileNodePanel">
          <el-icon size="20"><Plus /></el-icon>
        </button>
        <button class="mobile-fab" title="配置" @click="openMobileConfigPanel">
          <el-icon size="20"><Setting /></el-icon>
        </button>
      </div>

      <div
        v-if="isMobile && (mobileNodePanelOpen || mobileConfigPanelOpen)"
        class="mobile-overlay"
        @click="handleMobileOverlayClick"
      ></div>
    </div>

    <CreateFlowDialog
      v-model:visible="showCreateDialog"
      :is-agent-mode="isAgentMode"
      @created="handleFlowCreated"
    />

    <ExecuteFlowDialog
      v-model:visible="showExecuteDialog"
      :input-fields="inputFields"
      :is-agent-mode="isAgentMode"
      @execute="handleConfirmExecute"
      @show-history="showHistoryFromDialog"
    />

    <HumanInputDialog
      v-model:visible="showHumanInputDialog"
      :question="humanInputQuestion"
      :context="humanInputContext"
      :messages="humanInputMessages"
      :loading="humanInputLoading"
      @submit="submitHumanInput"
      @cancel="stopExecutionWithPolling"
    />

    <footer class="status-bar">
      <div class="status-left">
        <div class="status-indicator">
          <span class="status-dot-glow"></span>
          <span>引擎就绪</span>
        </div>
        <div class="status-divider"></div>
        <div class="status-stats">Nodes: {{ nodeCount }} | Edges: {{ edgeCount }}</div>
      </div>
      <div class="status-right">
        <span class="status-time">{{ currentTime }}</span>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.flow-edit-page {
  height: 100vh;
  display: grid;
  grid-template-rows: auto 1fr auto;
  background: #f8fafc;
  overflow: hidden;
}

.editor-container {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  overflow: hidden;
  position: relative;
  min-height: 0;
}

.sub-view-breadcrumb {
  position: absolute;
  top: 8px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 100;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(8px);
  padding: 8px 16px;
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  border: 1px solid #e2e8f0;
}

.breadcrumb-link {
  cursor: pointer;
  color: #2563eb;
  font-weight: 500;
}

.status-bar {
  height: 32px;
  background: #0f172a;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  position: relative;
  z-index: 100;
}

.status-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-dot-glow {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 8px #10b981;
}

.status-indicator span:last-child {
  font-size: 10px;
  color: #fff;
  font-family: 'Courier New', monospace;
  text-transform: uppercase;
  letter-spacing: -0.025em;
}

.status-divider {
  width: 1px;
  height: 12px;
  background: #334155;
}

.status-stats {
  font-size: 10px;
  color: #64748b;
  font-family: 'Courier New', monospace;
}

.status-right {
  display: flex;
  align-items: center;
}

.status-time {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.6);
  font-weight: 700;
  font-family: 'Courier New', monospace;
}

.mobile-floating-btns {
  position: absolute;
  right: 16px;
  bottom: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  z-index: 50;
}

.mobile-fab {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: #2563eb;
  color: #fff;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(37, 99, 235, 0.4);
  transition:
    transform 0.2s,
    box-shadow 0.2s;
}

.mobile-fab:active {
  transform: scale(0.92);
}

.mobile-fab:last-child {
  background: #fff;
  color: #334155;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
}

.mobile-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 60;
}

@media (max-width: 768px) {
  .editor-container {
    grid-template-columns: 1fr;
  }

  .status-bar {
    height: 28px;
    padding: 0 12px;
  }

  .status-stats {
    display: none;
  }

  .status-divider {
    display: none;
  }
}
</style>
