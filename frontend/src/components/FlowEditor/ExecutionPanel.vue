<script setup lang="ts">
import { ref, watch, toRef } from 'vue'
import { Close, VideoPause, Bottom } from '@element-plus/icons-vue'
import type { FlowExecution, NodeExecution, ExecutionStatus } from '@/types/execution'
import { EXECUTION_STATUS_TEXT } from '@/types/execution'
import { getNodeStatusType } from '@/utils/format'
import { useAutoScroll } from '@/composables/useAutoScroll'
import ExecutionResultContent from '@/components/common/ExecutionResultContent.vue'
import type {
  StreamingContentItem,
  TodoDisplayItem
} from '@/components/common/ExecutionResultContent.vue'

const props = defineProps<{
  visible: boolean
  execution: FlowExecution | null
  nodeExecutions: NodeExecution[]
  streamingContent?: Record<string, StreamingContentItem>
  isStreamRunning?: boolean
  attachedFiles?: Array<{ id: number; original_name: string; mime_type: string }>
  historyList: FlowExecution[]
  historyTotal: number
  historyLoading: boolean
  historyPage: number
  historyPageSize: number
  isRunning: boolean
  executionDetailLoading?: boolean
  todos?: TodoDisplayItem[]
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'stop'): void
  (e: 'loadHistory', page: number, pageSize: number): void
  (e: 'viewExecution', exec: FlowExecution): void
  (e: 'resumeExecution', exec: FlowExecution): void
  (e: 'update:historyPage', page: number): void
  (e: 'update:historyPageSize', size: number): void
}>()

const activeTab = ref<'result' | 'history'>('result')
const panelContentRef = ref<HTMLElement | null>(null)

const { isAtBottom, scrollToBottom, handleScroll } = useAutoScroll(panelContentRef, [
  toRef(props, 'nodeExecutions'),
  toRef(props, 'streamingContent')
])

watch(
  () => props.visible,
  val => {
    if (val && !props.execution) {
      activeTab.value = 'history'
    } else if (val && props.execution) {
      activeTab.value = 'result'
    }
  }
)

function getStatusText(status: ExecutionStatus | undefined): string {
  if (status === undefined) return ''
  return EXECUTION_STATUS_TEXT[status]
}

function handleLoadHistory() {
  emit('loadHistory', props.historyPage, props.historyPageSize)
}

function clickHistory() {
  activeTab.value = 'history'
  handleLoadHistory()
}

function handlePageChange(page: number) {
  emit('update:historyPage', page)
  emit('loadHistory', page, props.historyPageSize)
}

function handleSizeChange(size: number) {
  emit('update:historyPageSize', size)
  emit('loadHistory', 1, size)
}

function handleViewExecution(exec: FlowExecution) {
  emit('viewExecution', exec)
  activeTab.value = 'result'
}

function handleResumeExecution(exec: FlowExecution, event: Event) {
  event.stopPropagation()
  emit('resumeExecution', exec)
}
</script>

<template>
  <div v-if="visible" class="execution-panel">
    <div class="panel-header">
      <span class="panel-title">执行结果</span>
      <div class="header-actions">
        <el-button type="info" link @click="$emit('close')">
          <el-icon>
            <Close />
          </el-icon>
        </el-button>
      </div>
    </div>

    <div class="panel-tabs">
      <div
        class="tab-item"
        :class="{ active: activeTab === 'result' }"
        @click="activeTab = 'result'"
      >
        执行结果
      </div>
      <div class="tab-item" :class="{ active: activeTab === 'history' }" @click="clickHistory()">
        历史记录
      </div>
    </div>

    <div ref="panelContentRef" class="panel-content" @scroll="handleScroll">
      <template v-if="activeTab === 'result'">
        <div v-if="isStreamRunning || isRunning" class="stop-bar">
          <span class="stop-label">执行中</span>
          <el-button type="danger" size="small" :icon="VideoPause" @click="emit('stop')">
            终止
          </el-button>
        </div>
        <div v-loading="executionDetailLoading" class="result-wrapper">
          <ExecutionResultContent
            :execution="execution"
            :node-executions="nodeExecutions"
            :streaming-content="streamingContent"
            :is-running="isRunning"
            :is-stream-running="isStreamRunning"
            :attached-files="attachedFiles"
            :todos="todos"
            show-resume-button
            show-empty-state
            @resume-execution="emit('resumeExecution', $event)"
          />
          <div :class="['scroll-to-bottom', { hidden: isAtBottom }]" @click="scrollToBottom">
            <el-icon :size="16"><Bottom /></el-icon>
          </div>
        </div>
      </template>

      <template v-else>
        <div v-loading="historyLoading" class="history-content">
          <div v-if="historyList.length === 0 && !historyLoading" class="empty-state">
            <el-empty description="暂无执行记录" :image-size="80" />
          </div>
          <template v-else>
            <div class="history-list">
              <div
                v-for="item in historyList"
                :key="item.id"
                class="history-item"
                @click="handleViewExecution(item)"
              >
                <div class="history-header">
                  <span class="history-id">#{{ item.id }}</span>
                  <el-tag :type="getNodeStatusType(item.status)" size="small">
                    {{ getStatusText(item.status) }}
                  </el-tag>
                </div>
                <div class="history-time">{{ item.start_time }}</div>
                <div v-if="item.status === 5" class="history-actions">
                  <el-button
                    type="primary"
                    size="small"
                    @click="handleResumeExecution(item, $event)"
                  >
                    继续输入
                  </el-button>
                </div>
                <div v-if="item.error_message" class="history-error">{{ item.error_message }}</div>
              </div>
            </div>
            <div class="pagination">
              <el-pagination
                :current-page="historyPage"
                :page-size="historyPageSize"
                :total="historyTotal"
                :page-sizes="[5, 10, 20]"
                layout="total, sizes, prev, pager, next"
                size="small"
                @current-change="handlePageChange"
                @size-change="handleSizeChange"
              />
            </div>
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.execution-panel {
  width: 400px;
  background: #fff;
  border-left: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #ebeef5;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.panel-tabs {
  display: flex;
  border-bottom: 1px solid #ebeef5;
}

.tab-item {
  flex: 1;
  text-align: center;
  padding: 10px 0;
  font-size: 13px;
  color: #606266;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.2s;
}

.tab-item:hover {
  color: #409eff;
}

.tab-item.active {
  color: #409eff;
  border-bottom-color: #409eff;
}

.panel-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  position: relative;
}

.scroll-to-bottom {
  position: sticky;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  margin: 0 auto;
}

.history-content {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.history-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.history-item {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 10px;
  cursor: pointer;
  transition: background 0.2s;
}

.history-item:hover {
  background: #eef1f6;
}

.history-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.history-id {
  font-weight: 500;
  font-size: 13px;
}

.history-time {
  font-size: 12px;
  color: #909399;
}

.history-actions {
  margin-top: 8px;
}

.history-error {
  margin-top: 6px;
  font-size: 12px;
  color: #f56c6c;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pagination {
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
  margin-top: 12px;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 200px;
}

.result-wrapper {
  min-height: 100px;
}

.stop-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  margin-bottom: 12px;
  background: #fef0f0;
  border-radius: 6px;
  border: 1px solid #fde2e2;
}

.stop-label {
  font-size: 13px;
  color: #f56c6c;
  font-weight: 500;
}

@media (max-width: 768px) {
  .execution-panel {
    position: fixed;
    inset: 0;
    width: 100vw;
    z-index: 200;
    border-left: none;
  }
}
</style>
