<script setup lang="ts">
import { computed } from 'vue'
import { VideoPlay, Loading, CircleCheck, CircleClose, Remove } from '@element-plus/icons-vue'
import type {
  FlowExecution,
  NodeExecution,
  ExecutionStatus,
  NodeExecutionStatus,
  ExecutionStep
} from '@/types/execution'
import { EXECUTION_STATUS_TEXT, NODE_EXECUTION_STATUS_TEXT } from '@/types/execution'
import AIMessageContent from '@/components/common/AIMessageContent.vue'
import FilePreviewer from '@/components/common/FilePreviewer.vue'
import type { FileItem } from '@/components/common/FilePreviewer.vue'
import TodoList from '@/components/common/TodoList.vue'
import type { Segment, TodoItem } from '@/types/segment'
import { formatTokenCount, getNodeStatusType } from '@/utils/format'

export interface StreamingContentItem {
  segments: Segment[]
}

const props = withDefaults(
  defineProps<{
    execution: FlowExecution | null
    nodeExecutions: NodeExecution[]
    streamingContent?: Record<string, StreamingContentItem>
    isRunning?: boolean
    isStreamRunning?: boolean
    attachedFiles?: FileItem[]
    todos?: TodoItem[]
    showResumeButton?: boolean
    showEmptyState?: boolean
  }>(),
  {
    streamingContent: undefined,
    isRunning: false,
    isStreamRunning: false,
    attachedFiles: undefined,
    todos: undefined,
    showResumeButton: false,
    showEmptyState: false
  }
)

const emit = defineEmits<{
  (e: 'resumeExecution', exec: FlowExecution): void
}>()

function getStatusText(status: ExecutionStatus | undefined): string {
  if (status === undefined) return ''
  return EXECUTION_STATUS_TEXT[status]
}

function getNodeStatusText(status: NodeExecutionStatus | undefined): string {
  if (status === undefined) return ''
  return NODE_EXECUTION_STATUS_TEXT[status]
}

const statusTagType = computed(() => {
  if (!props.execution?.status) return 'info' as const
  const types: Record<number, 'primary' | 'success' | 'warning' | 'danger' | 'info'> = {
    0: 'info',
    1: 'warning',
    2: 'success',
    3: 'danger',
    4: 'info',
    5: 'warning'
  }
  return types[props.execution.status]
})

const statusIcon = computed(() => {
  if (!props.execution?.status) return Remove
  const icons: Record<number, typeof VideoPlay> = {
    0: Remove,
    1: Loading,
    2: CircleCheck,
    3: CircleClose,
    4: Remove,
    5: Remove
  }
  return icons[props.execution.status]
})

function handleResumeExecution(exec: FlowExecution, event: Event) {
  event.stopPropagation()
  emit('resumeExecution', exec)
}

function getExecutionSteps(node: NodeExecution): ExecutionStep[] {
  if (node.execution_steps && node.execution_steps.length > 0) {
    return node.execution_steps
  }
  return []
}

function executionStepsToSegments(steps: ExecutionStep[]): Segment[] {
  const segments: Segment[] = []
  const toolCallMap = new Map<string, number>()
  for (const step of steps) {
    if (step.role === 'human') {
      continue
    }
    if (step.role === 'tool') {
      const idx = toolCallMap.get(step.tool_call_id || '')
      if (idx !== undefined && segments[idx]?.tool) {
        segments[idx].tool.result = step.content || ''
      }
    } else {
      if (step.thinking) {
        segments.push({ type: 'thinking', thinking: step.thinking })
      }
      if (step.content) {
        segments.push({ type: 'content', content: step.content })
      }
      if (step.tool_calls) {
        for (const tool of step.tool_calls) {
          const segIdx = segments.length
          segments.push({
            type: 'tool',
            tool: {
              name: tool.name,
              args: tool.args || {},
              status: (tool.status || 'running') as 'running' | 'success' | 'error',
              result: tool.result
            }
          })
          if (tool.id) {
            toolCallMap.set(tool.id, segIdx)
          }
        }
      }
    }
  }
  return segments
}
</script>

<template>
  <div v-if="execution" class="result-content">
    <div class="status-header">
      <el-tag :type="statusTagType" size="large">
        <el-icon class="status-icon" :class="{ 'is-loading': isRunning || isStreamRunning }">
          <component :is="statusIcon" />
        </el-icon>
        {{ getStatusText(execution.status) }}
      </el-tag>
      <span class="exec-id">ID: {{ execution.id }}</span>
      <el-button
        v-if="showResumeButton && execution.status === 5"
        type="primary"
        size="small"
        @click="handleResumeExecution(execution, $event)"
      >
        继续输入
      </el-button>
    </div>

    <div class="section-title">输入数据</div>
    <pre class="json-content">{{ JSON.stringify(execution.input_data, null, 2) }}</pre>

    <div v-if="attachedFiles && attachedFiles.length > 0" class="section-title">附件</div>
    <FilePreviewer v-if="attachedFiles && attachedFiles.length > 0" :files="attachedFiles" />

    <div class="section-title">输出数据</div>
    <pre class="json-content">{{
      execution.output_data ? JSON.stringify(execution.output_data, null, 2) : '无'
    }}</pre>

    <template v-if="todos && todos.length > 0">
      <div class="section-title">任务计划 ({{ todos.length }})</div>
      <TodoList :items="todos" />
    </template>

    <template v-if="execution.error_message">
      <div class="section-title">错误信息</div>
      <pre class="json-content error">{{ execution.error_message }}</pre>
    </template>

    <div class="section-title">节点执行记录 ({{ nodeExecutions.length }})</div>
    <div class="node-list">
      <div v-for="(node, index) in nodeExecutions" :key="node.node_key || index" class="node-item">
        <div class="node-header">
          <span class="node-name">{{ node.node_name || node.node_key }}</span>
          <el-tag :type="getNodeStatusType(node.status)" size="small">
            {{ getNodeStatusText(node.status) }}
          </el-tag>
        </div>

        <div v-if="node.total_tokens" class="node-token-info">
          <span>输入: {{ formatTokenCount(node.prompt_tokens) }} token</span>
          <span>输出: {{ formatTokenCount(node.completion_tokens) }} token</span>
          <span>总计: {{ formatTokenCount(node.total_tokens) }} token</span>
        </div>

        <!-- 实时流式内容（LLM 节点） -->
        <AIMessageContent
          v-if="
            streamingContent &&
            node.node_key &&
            streamingContent[node.node_key]?.segments &&
            streamingContent[node.node_key].segments.length > 0
          "
          :segments="streamingContent[node.node_key].segments"
        />

        <!-- 历史执行步骤（LLM 节点） -->
        <AIMessageContent
          v-else-if="getExecutionSteps(node).length > 0"
          :segments="executionStepsToSegments(getExecutionSteps(node))"
        />

        <!-- 节点输入数据 -->
        <div v-if="node.input_data && Object.keys(node.input_data).length > 0" class="node-input">
          <span class="label">输入:</span>
          <pre>{{ JSON.stringify(node.input_data, null, 2) }}</pre>
        </div>

        <!-- 节点完成后的输出 -->
        <div
          v-if="node.output_data && Object.keys(node.output_data).length > 0"
          class="node-output"
        >
          <span class="label">输出:</span>
          <pre>{{ JSON.stringify(node.output_data, null, 2) }}</pre>
        </div>
        <div v-if="node.error_message" class="node-error">
          <span class="label">错误:</span>
          <pre>{{ node.error_message }}</pre>
        </div>
      </div>
      <div v-if="nodeExecutions.length === 0" class="empty-text">暂无节点执行记录</div>
    </div>
  </div>
  <div v-else-if="showEmptyState" class="empty-state">
    <el-empty description="暂无执行结果" :image-size="80" />
  </div>
</template>

<style scoped>
.result-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.status-header {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-icon {
  margin-right: 4px;
}

.status-icon.is-loading {
  animation: rotating 2s linear infinite;
}

@keyframes rotating {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

.exec-id {
  color: #909399;
  font-size: 12px;
}

.section-title {
  font-size: 13px;
  color: #606266;
  font-weight: 500;
  margin-bottom: 8px;
}

.json-content {
  background: #f5f7fa;
  padding: 10px;
  border-radius: 4px;
  font-size: 12px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}

.json-content.error {
  color: #f56c6c;
  background: #fef0f0;
}

.node-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.node-item {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 10px;
}

.node-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.node-name {
  font-weight: 500;
  font-size: 13px;
}

.node-token-info {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: #c0c4cc;
  margin: 10px 2px;
}

.node-input,
.node-output,
.node-error {
  margin-top: 6px;
}

.node-input .label,
.node-output .label,
.node-error .label,
.node-thinking .label,
.node-streaming .label {
  color: #909399;
  font-size: 12px;
}

.node-input pre,
.node-output pre,
.node-error pre,
.thinking-content,
.streaming-content {
  margin: 4px 0 0;
  font-size: 11px;
  background: #fff;
  padding: 6px;
  border-radius: 4px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.node-input pre {
  background: #ecf5ff;
  border-left: 3px solid #409eff;
}

.empty-state {
  color: #f56c6c;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 200px;
}

.empty-text {
  text-align: center;
  color: #909399;
  padding: 20px 0;
  font-size: 13px;
}
</style>
