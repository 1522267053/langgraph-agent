<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { VideoPause } from '@element-plus/icons-vue'
import type { RunningTool } from '@/stores/toolOutput'
import { useToolOutputStore } from '@/stores/toolOutput'
import { useAutoScroll } from '@/composables/useAutoScroll'

const props = defineProps<{ task: RunningTool }>()

const store = useToolOutputStore()

const outputRef = ref<HTMLElement | null>(null)
const { handleScroll } = useAutoScroll(outputRef, [
  () => props.task.stdout,
  () => props.task.stderr
])

const cancellingIds = ref<Set<string>>(new Set())

function statusText(status: string): string {
  const map: Record<string, string> = {
    running: '执行中',
    completed: '完成',
    failed: '失败',
    timeout: '超时',
    cancelled: '已取消'
  }
  return map[status] || status
}

function statusColor(status: string): string {
  if (status === 'running') return '#f59e0b'
  if (status === 'completed') return '#10b981'
  return '#ef4444'
}

function truncateCmd(cmd: string, max = 60): string {
  return cmd.length > max ? cmd.slice(0, max) + '...' : cmd
}

function formatOutput(stdout: string, stderr: string): string {
  const parts: string[] = []
  if (stdout) parts.push(stdout)
  if (stderr) parts.push('\n[stderr]\n' + stderr)
  return parts.join('\n').trim() || '(暂无输出)'
}

async function handleCancel(taskId: string) {
  cancellingIds.value.add(taskId)
  try {
    await store.cancelTask(taskId)
    ElMessage.success('已取消')
  } finally {
    cancellingIds.value.delete(taskId)
  }
}
</script>

<template>
  <div class="task-card">
    <div class="task-header">
      <div class="task-cmd" :title="task.command">
        <span class="task-prompt">$</span>
        {{ truncateCmd(task.command) }}
      </div>
      <span class="task-status" :style="{ color: statusColor(task.status) }">
        {{ statusText(task.status) }}
      </span>
    </div>

    <div class="task-meta">
      <span v-if="task.status === 'running'" class="task-timer">
        {{ Math.floor((Date.now() - task.startTime) / 1000) }}s
      </span>
      <span v-else-if="task.elapsed_seconds" class="task-timer">
        {{ task.elapsed_seconds }}s
      </span>
      <span v-if="task.return_code !== null" class="task-rc">rc={{ task.return_code }}</span>
    </div>

    <div ref="outputRef" class="task-output" @scroll="handleScroll">
      <pre>{{ formatOutput(task.stdout, task.stderr) }}</pre>
    </div>

    <div v-if="task.status === 'running'" class="task-actions">
      <el-button
        type="danger"
        size="small"
        :icon="VideoPause"
        :loading="cancellingIds.has(task.task_id)"
        @click="handleCancel(task.task_id)"
      >
        停止
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.task-card {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  overflow: hidden;
}

.task-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 14px;
  background: #f8fafc;
}

.task-cmd {
  font-family: 'Courier New', monospace;
  font-size: 13px;
  color: #334155;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.task-prompt {
  color: #10b981;
  font-weight: 700;
  margin-right: 4px;
}

.task-status {
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.task-meta {
  display: flex;
  gap: 12px;
  padding: 4px 14px;
  font-size: 12px;
  color: #64748b;
}

.task-output {
  background: #1a1a2e;
  max-height: 280px;
  overflow-y: auto;
}

.task-output pre {
  margin: 0;
  padding: 12px 14px;
  font-family: 'Courier New', monospace;
  font-size: 12.5px;
  line-height: 1.5;
  color: #e0e0e0;
  white-space: pre-wrap;
  word-break: break-all;
}

.task-actions {
  display: flex;
  gap: 8px;
  padding: 8px 14px;
  background: #f8fafc;
}
</style>
