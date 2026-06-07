<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { executionApi } from '@/api/execution'
import type { FlowExecution, ExecutionStatus } from '@/types/execution'
import { EXECUTION_STATUS_TEXT } from '@/types/execution'
import type { PaginatedResponse } from '@/types/common'
import { ElMessage, ElMessageBox } from 'element-plus'
import { View, VideoPlay, Close } from '@element-plus/icons-vue'
import ActionColumn from '@/components/common/ActionColumn.vue'
import ExecutionResultContent from '@/components/common/ExecutionResultContent.vue'
import HumanInputDialog from '@/components/FlowEditor/HumanInputDialog.vue'
import { useFlowExecution } from '@/composables/useFlowExecution'
import { useIsMobile } from '@/composables/useIsMobile'

const route = useRoute()
const { isMobile } = useIsMobile()

const {
  currentExecution,
  nodeExecutions,
  streamingContent,
  isStreamRunning,
  isRunning,
  hasExecution,
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
const tableData = ref<FlowExecution[]>([])
const total = ref(0)

const queryParams = reactive({
  page: 1,
  page_size: 10,
  condition: {
    flow_id: undefined as number | undefined,
    status: undefined as ExecutionStatus | undefined
  }
})

const statusOptions = [
  { label: '待执行', value: 0 },
  { label: '执行中', value: 1 },
  { label: '成功', value: 2 },
  { label: '失败', value: 3 },
  { label: '已取消', value: 4 },
  { label: '等待输入', value: 5 }
]

const showDetailDialog = ref(false)
const detailLoading = ref(false)

async function loadData() {
  loading.value = true
  try {
    const res = await executionApi.page(queryParams)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<FlowExecution>
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
  queryParams.condition = { flow_id: undefined, status: undefined }
  handleSearch()
}

function getRowActions(row: any) {
  return [
    { key: 'view', label: '查看', icon: View, btnClass: 'action-view' },
    {
      key: 'cancel',
      label: '取消',
      icon: Close,
      btnClass: 'action-cancel',
      visible: row.status === 1
    },
    {
      key: 'resume',
      label: '继续',
      icon: VideoPlay,
      btnClass: 'action-success',
      visible: row.status === 5
    }
  ]
}

function onRowAction(row: any, key: string) {
  switch (key) {
    case 'view':
      handleView(row)
      break
    case 'cancel':
      handleCancel(row)
      break
    case 'resume':
      handleResume(row)
      break
  }
}

async function handleCancel(row: FlowExecution) {
  ElMessageBox.confirm('确定要取消该执行吗？', '提示', { type: 'warning' })
    .then(async () => {
      await executionApi.cancel(row.id!)
      ElMessage.success('已取消')
      loadData()
    })
    .catch(() => {})
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

function getStatusText(status: ExecutionStatus | undefined): string {
  if (status === undefined) return '-'
  return EXECUTION_STATUS_TEXT[status]
}

function getStatusType(
  status: ExecutionStatus | undefined
): '' | 'success' | 'warning' | 'danger' | 'info' {
  if (status === undefined) return 'info'
  const types: Record<number, '' | 'success' | 'warning' | 'danger' | 'info'> = {
    0: 'info',
    1: 'warning',
    2: 'success',
    3: 'danger',
    4: 'info',
    5: 'warning'
  }
  return types[status] ?? 'info'
}

async function handleView(row: FlowExecution) {
  await handleViewById(row.id!)
}

async function handleViewById(id: number) {
  detailLoading.value = true
  showDetailDialog.value = true
  resetState()
  try {
    await loadExecutionDetail(id)
  } finally {
    detailLoading.value = false
  }
}

async function handleResume(row: FlowExecution) {
  if (!row.id) return
  detailLoading.value = true
  showDetailDialog.value = true
  resetState()
  try {
    currentExecution.value = row
    await resumeFromHistory(row)
  } finally {
    detailLoading.value = false
  }
}

function handleDetailClose() {
  showDetailDialog.value = false
  resetState()
}

onMounted(async () => {
  await loadData()
  const executionId = route.query.executionId as string
  if (executionId) {
    handleViewById(parseInt(executionId))
  }
})

onUnmounted(() => {
  resetState()
})
</script>

<template>
  <div class="execution-list-page page">
    <div class="page-header">
      <h1 class="page-title">执行记录</h1>
    </div>

    <div class="search-bar">
      <el-form inline>
        <el-form-item label="流程ID">
          <el-input-number
            v-model="queryParams.condition.flow_id"
            placeholder="请输入"
            :controls="false"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="queryParams.condition.status" placeholder="全部" clearable>
            <el-option
              v-for="item in statusOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button class="btn-search" @click="handleSearch">查询</el-button>
          <el-button class="btn-reset" @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="card-panel table-container">
      <el-table v-loading="loading" :data="tableData" stripe>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="flow_name" label="流程名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="flow_id" label="流程ID" width="100" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)" size="small">
              {{ getStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="error_message"
          label="错误信息"
          min-width="200"
          show-overflow-tooltip
        />
        <el-table-column prop="start_time" label="开始时间" width="180" />
        <el-table-column prop="end_time" label="结束时间" width="180" />
        <el-table-column label="操作" :width="isMobile ? 60 : 140" fixed="right">
          <template #default="{ row }">
            <ActionColumn :actions="getRowActions(row)" @action="onRowAction(row, $event)" />
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
          :is-running="isRunning"
          :is-stream-running="isStreamRunning"
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

<style scoped></style>
