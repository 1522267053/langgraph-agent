<script setup lang="ts">
import { ref, watch, onUnmounted } from 'vue'
import { flowApi } from '@/api/flow'
import type { FlowIOField } from '@/types/flow'
import { Bottom } from '@element-plus/icons-vue'
import FlowInputForm from '@/components/common/FlowInputForm.vue'
import ExecutionResultContent from '@/components/common/ExecutionResultContent.vue'
import HumanInputDialog from '@/components/FlowEditor/HumanInputDialog.vue'
import { useFlowExecution } from '@/composables/useFlowExecution'
import { useAutoScroll } from '@/composables/useAutoScroll'

const props = defineProps<{
  visible: boolean
  flowId: number | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'done'): void
}>()

const {
  currentExecution,
  nodeExecutions,
  streamingContent,
  isStreamRunning,
  flowTodos,
  isRunning,
  hasExecution,
  showHumanInputDialog,
  humanInputQuestion,
  humanInputContext,
  humanInputLoading,
  humanInputMessages,
  startStream,
  submitHumanInput,
  stopExecution,
  resetState
} = useFlowExecution({
  onFlowDone: () => emit('done'),
  onError: () => emit('done')
})

// ---- 输入参数 ----
const inputFields = ref<FlowIOField[]>([])
const loadingDetail = ref(false)
const executeFormData = ref<Record<string, unknown>>({})
const inputFormRef = ref<InstanceType<typeof FlowInputForm>>()

// ---- 滚动控制 ----
const resultPanelRef = ref<HTMLElement | null>(null)
const { isAtBottom, scrollToBottom, handleScroll } = useAutoScroll(resultPanelRef, [
  nodeExecutions,
  streamingContent
])

watch(
  () => props.visible,
  async visible => {
    if (!visible || !props.flowId) return
    loadingDetail.value = true
    try {
      const res = await flowApi.get(props.flowId)
      if (res.data.code === 1) {
        inputFields.value = res.data.data.input_schema?.fields || []
        executeFormData.value = {}
      } else {
        close()
      }
    } catch {
      close()
    } finally {
      loadingDetail.value = false
    }
  }
)

// ---- 表单提交 ----
function confirmExecute(): void {
  if (!props.flowId || !inputFormRef.value) return
  try {
    const { input, attachedFiles } = inputFormRef.value.collect()
    startStream(props.flowId, input, attachedFiles)
  } catch {
    // JSON解析错误已由FlowInputForm内部处理
  }
}

function handleDialogClose(): void {
  if (isStreamRunning.value) {
    stopExecution()
  }
  resetState()
  close()
}

function close(): void {
  emit('update:visible', false)
}

onUnmounted(() => {
  resetState()
})
</script>

<template>
  <el-dialog
    :model-value="visible"
    title="执行流程"
    width="1100px"
    :close-on-click-modal="false"
    :close-on-press-escape="!isStreamRunning"
    :before-close="handleDialogClose"
    destroy-on-close
    class="quick-execute-dialog"
  >
    <div v-loading="loadingDetail" class="split-layout">
      <!-- 左侧：输入参数 -->
      <div class="left-panel">
        <div class="panel-title">输入参数</div>
        <div v-if="inputFields.length === 0" class="empty-params">
          <el-empty description="该流程无需输入参数" :image-size="60" />
        </div>
        <FlowInputForm
          v-else
          ref="inputFormRef"
          v-model="executeFormData"
          :fields="inputFields"
          source-type="flow"
          show-tooltip
        />
        <div class="left-actions">
          <el-button
            type="primary"
            :loading="isStreamRunning"
            :disabled="isStreamRunning"
            @click="confirmExecute"
          >
            {{ isStreamRunning ? '执行中...' : '执行' }}
          </el-button>
          <el-button v-if="isStreamRunning" type="danger" @click="stopExecution">停止</el-button>
        </div>
      </div>

      <!-- 右侧：执行结果 -->
      <div class="right-panel">
        <div class="panel-title">执行结果</div>
        <div ref="resultPanelRef" class="result-scroll" @scroll="handleScroll">
          <ExecutionResultContent
            v-if="hasExecution"
            :execution="currentExecution"
            :node-executions="nodeExecutions"
            :streaming-content="streamingContent"
            :is-stream-running="isStreamRunning"
            :is-running="isRunning"
            :todos="flowTodos"
          />
          <div v-else class="result-placeholder">
            <el-empty description="点击执行按钮开始" :image-size="60" />
          </div>
          <div
            v-if="hasExecution"
            :class="['scroll-to-bottom', { hidden: isAtBottom }]"
            @click="scrollToBottom"
          >
            <el-icon :size="16"><Bottom /></el-icon>
          </div>
        </div>
      </div>
    </div>

    <HumanInputDialog
      v-model:visible="showHumanInputDialog"
      :question="humanInputQuestion"
      :context="humanInputContext"
      :messages="humanInputMessages"
      :loading="humanInputLoading"
      @submit="submitHumanInput"
      @cancel="stopExecution"
    />
  </el-dialog>
</template>

<style scoped>
.split-layout {
  display: flex;
  gap: 20px;
  height: 65vh;
}

.left-panel {
  width: 360px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #ebeef5;
  padding-right: 20px;
}

.right-panel {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 12px;
  flex-shrink: 0;
}

.empty-params {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.input-form {
  flex: 1;
  overflow-y: auto;
}

.input-form :deep(.el-form-item__label) {
  white-space: normal;
  word-break: break-all;
  line-height: 1.4;
}

.input-form .field-label-text {
  white-space: normal;
  word-break: break-all;
}

.left-actions {
  flex-shrink: 0;
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
  display: flex;
  gap: 8px;
}

.result-scroll {
  flex: 1;
  overflow-y: auto;
  position: relative;
}

.result-placeholder {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.scroll-to-bottom {
  position: sticky;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  margin: 0 auto;
}
</style>

<style>
.quick-execute-dialog .el-dialog__body {
  padding: 16px 20px;
}
</style>
