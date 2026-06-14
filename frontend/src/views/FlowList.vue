<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  Plus,
  ChatDotRound,
  VideoPlay,
  Edit,
  Delete,
  Download,
  Upload,
  CopyDocument
} from '@element-plus/icons-vue'
import { flowApi } from '@/api/flow'
import type { Flow, FlowStatus, FlowType, FlowExportData } from '@/types/flow'
import type { PaginatedResponse } from '@/types/common'
import { ElMessage, ElMessageBox } from 'element-plus'
import FlowQuickExecute from '@/components/FlowEditor/FlowQuickExecute.vue'
import ActionColumn from '@/components/common/ActionColumn.vue'
import { useIsMobile } from '@/composables/useIsMobile'

const router = useRouter()
const { isMobile } = useIsMobile()

const loading = ref(false)
const tableData = ref<Flow[]>([])
const total = ref(0)
const selectedRows = ref<Flow[]>([])

const queryParams = reactive({
  page: 1,
  page_size: 10,
  condition: {
    name: '',
    status: undefined as FlowStatus | undefined,
    saved_as_card: undefined as number | undefined,
    flow_type: undefined as FlowType | undefined
  }
})

const flowTypeOptions = [
  { label: '流程 (Workflow)', value: 'flow' },
  { label: '智能体 (Agent)', value: 'agent' }
]

const statusOptions = [
  { label: '草稿', value: 0 },
  { label: '已发布', value: 1 }
]

const cardOptions = [
  { label: '是', value: 1 },
  { label: '否', value: 0 }
]

function getRowActions(row: Flow) {
  return [
    row.flow_type === 'agent'
      ? { key: 'chat', label: '对话', icon: ChatDotRound, btnClass: 'action-chat' }
      : { key: 'run', label: '执行', icon: VideoPlay, btnClass: 'action-run' },
    { key: 'duplicate', label: '复制', icon: CopyDocument, btnClass: 'action-duplicate' },
    { key: 'export', label: '导出', icon: Download, btnClass: 'action-export' },
    { key: 'edit', label: '编辑', icon: Edit, btnClass: 'action-edit' },
    { key: 'delete', label: '删除', icon: Delete, btnClass: 'action-delete', danger: true }
  ]
}

function onRowAction(row: Flow, key: string) {
  switch (key) {
    case 'chat':
      handleChat(row)
      break
    case 'run':
      handleRun(row)
      break
    case 'duplicate':
      handleDuplicate(row)
      break
    case 'export':
      handleExport([row.id!])
      break
    case 'edit':
      handleEdit(row)
      break
    case 'delete':
      handleDelete(row)
      break
  }
}

async function handleDuplicate(row: Flow) {
  try {
    const res = await flowApi.duplicate(row.id!)
    if (res.data.code === 1) {
      ElMessage.success('复制成功')
      await loadData()
    }
  } catch {
    // 错误由拦截器处理
  }
}

async function loadData() {
  loading.value = true
  try {
    const res = await flowApi.page(queryParams)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<Flow>
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
  queryParams.condition = {
    name: '',
    status: undefined,
    saved_as_card: undefined,
    flow_type: undefined
  }
  handleSearch()
}

function handleCreate(type: FlowType) {
  if (type === 'agent') {
    router.push('/agent/create')
  } else {
    router.push('/flow/create')
  }
}

function handleEdit(row: Flow) {
  if (row.flow_type === 'agent') {
    router.push(`/agent/edit/${row.id}`)
  } else {
    router.push(`/flow/edit/${row.id}`)
  }
}

function handleChat(row: Flow) {
  router.push(`/chat/${row.id}`)
}

function handleDelete(row: Flow) {
  const typeName = row.flow_type === 'agent' ? '智能体' : '流程'
  ElMessageBox.confirm(`确定要删除${typeName}「${row.name}」吗？`, '提示', {
    type: 'warning'
  })
    .then(async () => {
      await flowApi.delete(row.id!)
      ElMessage.success('删除成功')
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

function getStatusText(status: number | undefined): string {
  return status === 1 ? '已发布' : '草稿'
}

function getStatusClass(status: number | undefined): string {
  return status === 1 ? 'status-published' : 'status-draft'
}

const showQuickExecute = ref(false)
const executeFlowId = ref<number | null>(null)

function handleRun(row: Flow) {
  executeFlowId.value = row.id!
  showQuickExecute.value = true
}

function handleSelectionChange(rows: Flow[]) {
  selectedRows.value = rows
}

// ---- 导出 ----

const exportLoading = ref(false)

async function handleExport(ids: number[]) {
  exportLoading.value = true
  try {
    const res = await flowApi.exportFlows(ids)
    if (res.data.code === 1) {
      const data = res.data.data as FlowExportData
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const timestamp = new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-')
      const flow = data.flows[0]
      a.download = `${flow.name}_${flow.flow_type || 'flow'}_${timestamp}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      ElMessage.success('导出成功')
    }
  } catch {
    ElMessage.error('导出失败')
  } finally {
    exportLoading.value = false
  }
}

// ---- 导入 ----

const importDialogVisible = ref(false)
const importLoading = ref(false)
const importFileData = ref<FlowExportData | null>(null)
const importFileName = ref('')

function handleOpenImport() {
  importFileData.value = null
  importFileName.value = ''
  importDialogVisible.value = true
}

function handleImportFileChange(file: File) {
  importFileName.value = file.name
  const reader = new FileReader()
  reader.onload = e => {
    try {
      const data = JSON.parse(e.target?.result as string) as FlowExportData
      if (!data.version || !data.flows || !Array.isArray(data.flows)) {
        ElMessage.error('无效的导入文件格式')
        return
      }
      importFileData.value = data
    } catch {
      ElMessage.error('文件解析失败，请检查文件格式')
    }
  }
  reader.readAsText(file)
}

async function handleConfirmImport() {
  if (!importFileData.value) return
  importLoading.value = true
  try {
    const res = await flowApi.importFlows(importFileData.value)
    if (res.data.code === 1) {
      const result = res.data.data
      const warnings: string[] = result.warnings || []
      if (warnings.length > 0) {
        ElMessage.warning({
          message: `导入 ${result.created.length} 个流程，${warnings.length} 条警告`,
          duration: 5000
        })
      } else {
        ElMessage.success(`成功导入 ${result.created.length} 个流程`)
      }
      importDialogVisible.value = false
      loadData()
    }
  } catch {
    ElMessage.error('导入失败')
  } finally {
    importLoading.value = false
  }
}

onMounted(() => {
  loadData()
})
</script>

<template>
  <div class="flow-list-page page">
    <div class="page-title-bar">
      <div class="title-section">
        <div class="title-row">
          <h1 class="page-title">流程和智能体管理</h1>
          <p class="page-desc">
            在这里构建、部署和监控您的智能业务流。当前共有
            <strong>{{ total }}</strong>
            个实例。
          </p>
        </div>
      </div>
      <div class="action-buttons">
        <el-button class="btn-import" :icon="Upload" @click="handleOpenImport">导入</el-button>
        <el-button class="btn-flow" :icon="Plus" @click="handleCreate('flow')">新建流程</el-button>
        <el-button class="btn-agent" :icon="Plus" @click="handleCreate('agent')">
          新建智能体
        </el-button>
      </div>
    </div>

    <el-form :inline="true" class="search-bar" @submit.prevent="handleSearch">
      <el-form-item label="实例名称">
        <el-input
          v-model="queryParams.condition.name"
          placeholder="搜索资源名称..."
          clearable
          @keyup.enter="handleSearch"
        />
      </el-form-item>
      <el-form-item label="类型">
        <el-select v-model="queryParams.condition.flow_type" placeholder="全部类型" clearable>
          <el-option
            v-for="item in flowTypeOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="当前状态">
        <el-select v-model="queryParams.condition.status" placeholder="全部状态" clearable>
          <el-option
            v-for="item in statusOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="能力卡片">
        <el-select v-model="queryParams.condition.saved_as_card" placeholder="全部能力" clearable>
          <el-option
            v-for="item in cardOptions"
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

    <div v-loading="loading" class="card-panel table-container">
      <el-table :data="tableData" style="width: 100%" @selection-change="handleSelectionChange">
        <el-table-column type="selection" width="55" />
        <el-table-column label="ID" width="80">
          <template #default="{ row }">
            <span class="id-text">#{{ String(row.id ?? 0) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="名称" min-width="200">
          <template #default="{ row }">
            <div class="name-cell">
              <div class="name-text">
                <div class="name-primary">{{ row.name }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="100" align="center">
          <template #default="{ row }">
            <span
              class="type-badge"
              :class="row.flow_type === 'agent' ? 'type-agent' : 'type-flow'"
            >
              {{ row.flow_type === 'agent' ? '智能体' : '流程' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="描述" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.description" class="desc-text">{{ row.description }}</span>
            <span v-else class="desc-empty">暂无描述内容</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <span class="status-badge" :class="getStatusClass(row.status)">
              <span class="status-dot" />
              {{ getStatusText(row.status) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="能力卡片" width="90" align="center">
          <template #default="{ row }">
            <span v-if="row.saved_as_card === 1" class="card-yes">是</span>
            <span v-else class="card-no">-</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">
            <span class="time-text">{{ row.create_time }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" :width="isMobile ? 60 : 230" fixed="right">
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

    <FlowQuickExecute
      v-model:visible="showQuickExecute"
      :flow-id="executeFlowId"
      @done="loadData"
    />

    <!-- 导入对话框 -->
    <el-dialog
      v-model="importDialogVisible"
      title="导入流程/智能体"
      width="720px"
      :close-on-click-modal="false"
    >
      <!-- 文件上传区域 -->
      <div v-if="!importFileData" class="import-upload-area">
        <el-upload
          drag
          accept=".json"
          :auto-upload="false"
          :show-file-list="false"
          :on-change="(f: any) => handleImportFileChange(f.raw)"
        >
          <div class="upload-content">
            <el-icon class="upload-icon"><Upload /></el-icon>
            <div class="upload-text">
              将 JSON 文件拖拽到此处，或
              <em>点击选择文件</em>
            </div>
            <div class="upload-tip">仅支持由导出功能生成的 .json 文件</div>
          </div>
        </el-upload>
      </div>

      <!-- 预览区域 -->
      <div v-else class="import-preview">
        <el-alert type="warning" :closable="false" show-icon class="import-tip">
          导入时如果有相同名称的内容，会创建新副本
        </el-alert>

        <div class="preview-section">
          <h4>流程列表 ({{ importFileData.flows.length }} 个)</h4>
          <el-table :data="importFileData.flows" size="small" max-height="250">
            <el-table-column prop="name" label="名称" min-width="160" />
            <el-table-column prop="flow_type" label="类型" width="100" align="center">
              <template #default="{ row }">
                {{ row.flow_type === 'agent' ? '智能体' : '流程' }}
              </template>
            </el-table-column>
            <el-table-column label="节点数" width="80" align="center">
              <template #default="{ row }">
                {{ row.nodes?.length || 0 }}
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div class="preview-stats">
          <span v-if="importFileData.mcp_servers?.length">
            MCP 服务器:
            <strong>{{ importFileData.mcp_servers.length }}</strong>
          </span>
          <span v-if="importFileData.knowledge_bases?.length">
            知识库:
            <strong>{{ importFileData.knowledge_bases.length }}</strong>
          </span>
          <span v-if="importFileData.skills?.length">
            技能:
            <strong>{{ importFileData.skills.length }}</strong>
          </span>
          <span v-if="importFileData.memories?.length">
            记忆分组:
            <strong>{{ importFileData.memories.length }}</strong>
          </span>
        </div>
      </div>

      <template #footer>
        <el-button @click="importDialogVisible = false">取消</el-button>
        <el-button
          v-if="importFileData"
          type="primary"
          :loading="importLoading"
          @click="handleConfirmImport"
        >
          确认导入
        </el-button>
      </template>
    </el-dialog>
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

.table-container {
  position: relative;
}

.table-container :deep(.el-table__body tr) {
  margin-bottom: 4px;
}

.table-container :deep(.el-table__row) {
  position: relative;
}

.name-cell {
  display: flex;
  align-items: center;
  gap: 12px;
}

.name-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.icon-flow {
  background: #eff6ff;
  color: #2563eb;
}

.icon-agent {
  background: #ecfdf5;
  color: #059669;
}

.name-text {
  min-width: 0;
}

.name-primary {
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.name-secondary {
  font-size: 10px;
  color: #94a3b8;
}

.id-text {
  font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
  font-size: 12px;
  color: #94a3b8;
}

.desc-text {
  font-size: 14px;
  color: #64748b;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.desc-empty {
  font-size: 14px;
  color: #94a3b8;
  font-style: italic;
}

.card-yes {
  color: #059669;
  font-size: 14px;
  font-weight: 600;
}

.card-no {
  color: #cbd5e1;
  font-size: 14px;
}

.time-text {
  font-size: 12px;
  color: #64748b;
  font-weight: 500;
}

.flow-list-page :deep(.el-button.btn-flow) {
  --el-button-bg-color: #fff;
  --el-button-border-color: #dbeafe;
  --el-button-text-color: #2563eb;
  --el-button-hover-bg-color: #fff;
  --el-button-hover-border-color: #bfdbfe;
  --el-button-hover-text-color: #2563eb;
  --el-button-active-bg-color: #eff6ff;
  --el-button-active-border-color: #93c5fd;
  --el-button-active-text-color: #2563eb;
  --el-button-disabled-bg-color: #f8fafc;
  --el-button-disabled-border-color: #e2e8f0;
  --el-button-disabled-text-color: #94a3b8;
  border-radius: 12px;
  font-weight: 600;
  box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
  transition: all 0.2s;
}

.flow-list-page :deep(.el-button.btn-flow:hover) {
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

.flow-list-page :deep(.el-button.btn-agent) {
  --el-button-bg-color: #10b981;
  --el-button-border-color: transparent;
  --el-button-text-color: #fff;
  --el-button-hover-bg-color: #059669;
  --el-button-hover-border-color: transparent;
  --el-button-hover-text-color: #fff;
  --el-button-active-bg-color: #047857;
  --el-button-active-border-color: transparent;
  --el-button-active-text-color: #fff;
  background: linear-gradient(to right, #10b981, #14b8a6, #059669, #0d9488) !important;
  background-size: 200% 100% !important;
  background-position: 0% !important;
  border-color: transparent !important;
  color: #fff !important;
  border-radius: 12px;
  font-weight: 600;
  box-shadow: 0 4px 12px rgba(16, 185, 129, 0.3);
  transition:
    background-position 0.6s cubic-bezier(0.4, 0, 0.2, 1),
    box-shadow 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}

.flow-list-page :deep(.el-button.btn-agent:hover) {
  background-position: 100% !important;
  box-shadow: 0 6px 16px rgba(16, 185, 129, 0.4);
}

.flow-list-page :deep(.el-button.btn-export) {
  border-radius: 12px;
  font-weight: 600;
}

.flow-list-page :deep(.el-button.btn-import) {
  border-radius: 12px;
  font-weight: 600;
}

/* 导入对话框 */
.import-upload-area {
  margin-bottom: 20px;
}

.import-upload-area :deep(.el-upload-dragger) {
  border-radius: 12px;
  padding: 40px 20px;
}

.upload-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.upload-icon {
  font-size: 48px;
  color: #94a3b8;
}

.upload-text {
  font-size: 14px;
  color: #64748b;
}

.upload-text em {
  color: #2563eb;
  font-style: normal;
}

.upload-tip {
  font-size: 12px;
  color: #94a3b8;
}

.import-preview {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.import-tip {
  border-radius: 8px;
}

.preview-section h4 {
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
  margin: 0 0 8px 0;
}

.preview-stats {
  display: flex;
  gap: 20px;
  font-size: 13px;
  color: #64748b;
}

.preview-stats strong {
  color: #0f172a;
}
</style>
