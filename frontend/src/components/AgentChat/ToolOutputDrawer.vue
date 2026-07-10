<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { VideoPause } from '@element-plus/icons-vue'
import { useToolOutputStore } from '@/stores/toolOutput'
import { useIsMobile } from '@/composables/useIsMobile'

const store = useToolOutputStore()
const { isMobile } = useIsMobile()

const drawerSize = computed(() => (isMobile.value ? '100%' : '500px'))

const visible = computed({
  get: () => store.drawerVisible,
  set: (v: boolean) => {
    if (!v) {
      store.closeDrawer()
    } else {
      store.drawerVisible = true
    }
  }
})

const tools = computed(() => {
  void store._reactivityTrigger.value
  return store.toolList
})

const outputRefs = ref<Map<string, HTMLElement>>(new Map())

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

function setOutputRef(el: Element | unknown, taskId: string) {
  if (el && typeof el === 'object' && 'scrollTop' in el) {
    outputRefs.value.set(taskId, el as HTMLElement)
  }
}

async function scrollToBottom(taskId: string) {
  await nextTick()
  const el = outputRefs.value.get(taskId)
  if (el) el.scrollTop = el.scrollHeight
}

watch(
  () => store._reactivityTrigger.value,
  () => {
    for (const tool of tools.value) {
      if (tool.status === 'running') {
        scrollToBottom(tool.task_id)
      }
    }
  }
)

const cancellingIds = ref<Set<string>>(new Set())

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
  <el-drawer v-model="visible" direction="rtl" :size="drawerSize">
    <template #header>
      <div class="drawer-header">
        <span>后台任务</span>
        <span v-if="store.runningCount > 0" class="drawer-count">{{ store.runningCount }} 个运行中</span>
      </div>
    </template>
    <div class="drawer-content">
      <div v-if="tools.length === 0" class="empty-state">暂无后台任务</div>

      <div v-for="tool in tools" :key="tool.task_id" class="task-card">
        <div class="task-header">
          <div class="task-cmd" :title="tool.command">
            <span class="task-prompt">$</span>
            {{ truncateCmd(tool.command) }}
          </div>
          <span class="task-status" :style="{ color: statusColor(tool.status) }">
            {{ statusText(tool.status) }}
          </span>
        </div>

        <div class="task-meta">
          <span v-if="tool.status === 'running'" class="task-timer">
            {{ Math.floor((Date.now() - tool.startTime) / 1000) }}s
          </span>
          <span v-else-if="tool.elapsed_seconds" class="task-timer">
            {{ tool.elapsed_seconds }}s
          </span>
          <span v-if="tool.return_code !== null" class="task-rc">
            rc={{ tool.return_code }}
          </span>
        </div>

        <div
          :ref="(el: Element | unknown) => setOutputRef(el, tool.task_id)"
          class="task-output"
        >
          <pre>{{ formatOutput(tool.stdout, tool.stderr) }}</pre>
        </div>

        <div v-if="tool.status === 'running'" class="task-actions">
          <el-button
            type="danger"
            size="small"
            :icon="VideoPause"
            :loading="cancellingIds.has(tool.task_id)"
            @click="handleCancel(tool.task_id)"
          >
            停止
          </el-button>
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.drawer-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  overflow-y: auto;
}

.drawer-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.drawer-count {
  font-size: 13px;
  color: #f59e0b;
  font-weight: 600;
}

.empty-state {
  text-align: center;
  color: #94a3b8;
  padding: 40px 0;
  font-size: 14px;
}

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
