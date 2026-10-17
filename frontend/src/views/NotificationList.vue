<script setup lang="ts">
import { ref, reactive, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { notificationApi, type Notification } from '@/api/notification'
import type { PaginatedResponse } from '@/types/common'
// ExecutionStatus 是枚举：isActiveStatus 里当值用（.Running），必须值导入而非 import type
import { ExecutionStatus } from '@/types/execution'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Delete } from '@element-plus/icons-vue'
import { useNotificationStore } from '@/stores/notification'
import ExecutionResultContent from '@/components/common/ExecutionResultContent.vue'
import HumanInputDialog from '@/components/FlowEditor/HumanInputDialog.vue'
import { useFlowExecution } from '@/composables/useFlowExecution'

const router = useRouter()
const notificationStore = useNotificationStore()

const {
  currentExecution,
  nodeExecutions,
  streamingContent,
  isStreamRunning,
  hasExecution,
  attachedFiles,
  flowTodos,
  showHumanInputDialog,
  humanInputQuestion,
  humanInputContext,
  humanInputLoading,
  humanInputMessages,
  resumeFromHistory,
  loadExecutionDetail,
  submitHumanInput,
  stopExecution,
  resetState
} = useFlowExecution()

const loading = ref(false)
const tableData = ref<Notification[]>([])
const total = ref(0)

const queryParams = reactive({
  page: 1,
  page_size: 10,
  condition: {
    is_read: undefined as number | undefined
  }
})

const readOptions = [
  { label: '全部', value: 'all' },
  { label: '未读', value: 'unread' },
  { label: '已读', value: 'read' }
]
const readFilter = ref('all')

// 类型展示：徽标文案 + 配色（对齐 FlowList 的 type-badge 风格）
const typeMeta: Record<string, { label: string; cls: string }> = {
  execution_done: { label: '执行完成', cls: 'ntype-success' },
  execution_failed: { label: '执行失败', cls: 'ntype-danger' },
  execution_cancelled: { label: '执行取消', cls: 'ntype-muted' },
  human_input: { label: '等待输入', cls: 'ntype-warning' },
  agenda_reminder: { label: '日程提醒', cls: 'ntype-info' },
  reminder: { label: '提醒', cls: 'ntype-info' }
}

function getTypeMeta(type?: string) {
  return typeMeta[type || ''] || { label: '通知', cls: 'ntype-muted' }
}

async function loadData() {
  loading.value = true
  try {
    queryParams.condition.is_read =
      readFilter.value === 'unread' ? 0 : readFilter.value === 'read' ? 1 : undefined
    const res = await notificationApi.page(queryParams)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<Notification>
      tableData.value = data.items
      total.value = data.total
    }
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  queryParams.page = 1
  loadData()
}

function handleReset() {
  readFilter.value = 'all'
  handleSearch()
}

function handlePageChange(page: number) {
  queryParams.page = page
  loadData()
}

function handleSizeChange(size: number) {
  queryParams.page_size = size
  queryParams.page = 1
  loadData()
}

/** 点击行：标记已读 + 按类型分发（等待输入直接弹输入弹窗，其余跳转） */
async function handleClick(item: Notification) {
  if (!item.id) return
  if (!item.is_read) {
    try {
      await notificationApi.markRead(item.id)
      item.is_read = 1
      notificationStore.fetchUnreadCount()
    } catch {
      // 标记失败不阻断后续动作
    }
  }
  // Flow 等待人工输入：直接打开执行详情 + 输入弹窗（与 ExecutionList「继续」一致）
  if (item.type === 'human_input' && item.ref_type === 'execution' && item.ref_id) {
    await handleResumeExecution(item.ref_id)
    return
  }
  jumpToRef(item)
}

function jumpToRef(item: Notification) {
  switch (item.ref_type) {
    case 'session':
      if (item.ref_id) router.push(`/chat/${item.ref_id}`)
      break
    case 'execution':
      router.push('/execution')
      break
    case 'agenda':
      router.push('/agenda')
      break
    default:
      break
  }
}

// ---- 执行详情弹窗 + 人工输入（复用 ExecutionList 同款逻辑） ----

const showDetailDialog = ref(false)
const detailLoading = ref(false)

/** 详情轮询定时器：仅弹窗打开且执行未到终态时运行 */
let detailPollTimer: ReturnType<typeof setInterval> | null = null

function isActiveStatus(status: number | undefined): boolean {
  return status === ExecutionStatus.Running || status === ExecutionStatus.WaitingInput
}

function stopDetailPoll(): void {
  if (detailPollTimer) {
    clearInterval(detailPollTimer)
    detailPollTimer = null
  }
}

function startDetailPoll(id: number): void {
  stopDetailPoll()
  if (!isActiveStatus(currentExecution.value?.status)) return
  detailPollTimer = setInterval(async () => {
    if (!showDetailDialog.value) {
      stopDetailPoll()
      return
    }
    await loadExecutionDetail(id)
    if (!isActiveStatus(currentExecution.value?.status)) {
      stopDetailPoll()
      loadData()
    }
  }, 2000)
}

/** 从通知打开执行详情：先拉完整执行记录，仍在等待输入时才弹输入框 */
async function handleResumeExecution(executionId: number) {
  detailLoading.value = true
  showDetailDialog.value = true
  resetState()
  try {
    // 拉取完整执行详情（input_data/output_data/status），避免占位对象导致弹窗空白
    await loadExecutionDetail(executionId)
    // 仅仍在等待人工输入时弹输入框并轮询；已完成的（人类已输入过）只展示详情
    if (currentExecution.value?.status === ExecutionStatus.WaitingInput) {
      await resumeFromHistory(currentExecution.value)
      startDetailPoll(executionId)
    }
  } finally {
    detailLoading.value = false
  }
}

function handleDetailClose() {
  stopDetailPoll()
  showDetailDialog.value = false
  resetState()
}

async function handleMarkAllRead() {
  try {
    await notificationApi.markAllRead()
    ElMessage.success({ message: '已全部标记已读', duration: 3000 })
    notificationStore.resetCount()
    loadData()
  } catch {
    // handled by interceptor
  }
}

async function handleClearRead() {
  try {
    await ElMessageBox.confirm('确定清空所有已读通知吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await notificationApi.clearRead()
    ElMessage.success({ message: '已清空已读通知', duration: 3000 })
    loadData()
  } catch {
    // cancelled
  }
}

async function handleDelete(item: Notification) {
  if (!item.id) return
  try {
    await notificationApi.delete(item.id)
    if (!item.is_read) notificationStore.fetchUnreadCount()
    loadData()
  } catch {
    // handled by interceptor
  }
}

onMounted(loadData)

// WS 推送到达时实时刷新列表（lastNotification 每次推送都会更新 _ts 触发 watch）
watch(
  () => notificationStore.lastNotification,
  (val, old) => {
    if (val && val !== old) loadData()
  }
)

onUnmounted(() => {
  stopDetailPoll()
  resetState()
})
</script>

<template>
  <div class="notification-list-page page">
    <div class="page-title-bar">
      <div class="title-section">
        <div class="title-row">
          <h1 class="page-title">通知中心</h1>
          <p class="page-desc">
            所有系统通知统一在此查看，当前共有
            <strong>{{ total }}</strong>
            条通知。
          </p>
        </div>
      </div>
      <div class="action-buttons">
        <el-button class="btn-flow" :icon="Check" :disabled="notificationStore.unreadCount <= 0" @click="handleMarkAllRead">
          全部已读
        </el-button>
        <el-button class="btn-reset" :icon="Delete" @click="handleClearRead">清空已读</el-button>
      </div>
    </div>

    <el-form :inline="true" class="search-bar" @submit.prevent="handleSearch">
      <el-form-item label="已读状态">
        <el-select v-model="readFilter" placeholder="全部" @change="handleSearch">
          <el-option v-for="item in readOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button class="btn-search" @click="handleSearch">查询</el-button>
        <el-button class="btn-reset" @click="handleReset">重置</el-button>
      </el-form-item>
    </el-form>

    <div v-loading="loading" class="card-panel table-container">
      <el-table :data="tableData" style="width: 100%" :row-class-name="row => (row.is_read ? '' : 'row-unread')" @row-click="handleClick">
        <el-table-column label="ID" width="70">
          <template #default="{ row }">
            <span class="id-text">#{{ String(row.id ?? 0) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="110" align="center">
          <template #default="{ row }">
            <span class="type-badge ntype-badge" :class="getTypeMeta(row.type).cls">
              {{ getTypeMeta(row.type).label }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="标题" min-width="220">
          <template #default="{ row }">
            <div class="title-cell">
              <span class="title-text">{{ row.title }}</span>
              <span v-if="!row.is_read" class="unread-dot" />
            </div>
          </template>
        </el-table-column>
        <el-table-column label="内容" min-width="260" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.content" class="desc-text">{{ row.content }}</span>
            <span v-else class="desc-empty">-</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <span class="read-badge" :class="row.is_read ? 'read-yes' : 'read-no'">
              {{ row.is_read ? '已读' : '未读' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">
            <span class="time-text">{{ row.create_time }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center">
          <template #default="{ row }">
            <el-button link type="danger" size="small" @click.stop="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="queryParams.page"
          v-model:page-size="queryParams.page_size"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </div>

    <!-- 执行详情弹窗（等待输入通知点击后展示，与 ExecutionList 一致） -->
    <el-dialog
      v-model="showDetailDialog"
      title="执行详情"
      width="800px"
      destroy-on-close
      :before-close="handleDetailClose"
    >
      <div v-loading="detailLoading" class="detail-dialog-content">
        <ExecutionResultContent
          v-if="hasExecution"
          :execution="currentExecution"
          :node-executions="nodeExecutions"
          :streaming-content="streamingContent"
          :is-running="isStreamRunning"
          :is-stream-running="isStreamRunning"
          :attached-files="attachedFiles"
          :todos="flowTodos"
        />
      </div>
      <template #footer>
        <div style="display: flex; justify-content: space-between; width: 100%">
          <el-button
            v-if="isStreamRunning || currentExecution?.status === 5"
            type="danger"
            @click="stopExecution"
          >
            停止
          </el-button>
          <span />
          <el-button @click="handleDetailClose">关闭</el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 人工输入弹窗（等待输入通知直达） -->
    <HumanInputDialog
      v-model:visible="showHumanInputDialog"
      :question="humanInputQuestion"
      :context="humanInputContext"
      :messages="humanInputMessages"
      :loading="humanInputLoading"
      @submit="submitHumanInput"
      @cancel="stopExecution"
    />
  </div>
</template>

<style scoped>
.page-desc {
  color: #64748b;
  font-size: 14px;
}

.page-desc strong {
  color: #0f172a;
}

.id-text {
  font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
  font-size: 12px;
  color: #94a3b8;
}

.title-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.title-text {
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.unread-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #e34d59;
  flex-shrink: 0;
}

.desc-text {
  font-size: 14px;
  color: #64748b;
}

.desc-empty {
  font-size: 14px;
  color: #cbd5e1;
}

.time-text {
  font-size: 12px;
  color: #64748b;
  font-weight: 500;
}

/* 类型徽标（复用全局 .type-badge 基础，配色对齐 FlowList 风格） */
.ntype-badge {
  cursor: default;
}

.ntype-success {
  background: rgba(5, 150, 105, 0.1);
  color: #059669;
}

.ntype-danger {
  background: rgba(220, 38, 38, 0.1);
  color: #dc2626;
}

.ntype-warning {
  background: rgba(217, 119, 6, 0.12);
  color: #d97706;
}

.ntype-info {
  background: rgba(37, 99, 235, 0.1);
  color: #2563eb;
}

.ntype-muted {
  background: rgba(100, 116, 139, 0.12);
  color: #64748b;
}

/* 已读状态徽标（对齐全局 status-badge 胶囊风格） */
.read-badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  font-size: 10px;
  font-weight: 700;
  border-radius: 9999px;
}

.read-no {
  background: #fff7f5;
  color: #e34d59;
  border: 1px solid #ffe8e3;
}

.read-yes {
  background: #f8fafc;
  color: #94a3b8;
  border: 1px solid #e2e8f0;
}

/* 未读行高亮 */
:deep(.el-table__row.row-unread) {
  background: #fffdf9;
  cursor: pointer;
}

:deep(.el-table__row.row-unread:hover td.el-table__cell) {
  background: #fff7ec !important;
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
