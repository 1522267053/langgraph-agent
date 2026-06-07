<script setup lang="ts">
import { ref, reactive, onMounted, computed, nextTick } from 'vue'
import { Upload, View, Refresh, Delete, Edit, Open } from '@element-plus/icons-vue'
import axios from 'axios'
import { skillApi } from '@/api/skill'
import type { Skill, SkillQuery } from '@/types/skill'
import type { PaginatedResponse } from '@/types/common'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadFiles } from 'element-plus'
import ActionColumn from '@/components/common/ActionColumn.vue'
import { useIsMobile } from '@/composables/useIsMobile'

const { isMobile } = useIsMobile()

const loading = ref(false)
const tableData = ref<Skill[]>([])
const total = ref(0)
const uploadLoading = ref(false)
const uploadDialogVisible = ref(false)
const uploadRef = ref()
const uploadFileList = ref<UploadFiles>([])

const contentDialogVisible = ref(false)
const contentLoading = ref(false)
const contentTitle = ref('')
const contentText = ref('')

const selectedRows = ref<Skill[]>([])
const batchReloadLoading = ref(false)

const canReload = computed(() => selectedRows.value.some(row => !!row.skill_path))
const canBatchDelete = computed(() => selectedRows.value.some(row => row.is_system !== 1))

const queryParams = reactive({
  page: 1,
  page_size: 10,
  condition: {
    name: '',
    category: undefined as string | undefined,
    is_enabled: undefined as number | undefined
  } as SkillQuery
})

const dialogVisible = ref(false)
const dialogTitle = ref('编辑 Skill')
const formData = reactive({
  id: 0,
  name: '',
  description: '',
  skill_path: '',
  category: '',
  tags: '',
  icon: '',
  is_enabled: 1
})

const statusOptions = [
  { label: '启用', value: 1 },
  { label: '禁用', value: 0 }
]

function getRowActions(row: any) {
  return [
    { key: 'view', label: '查看', icon: View, btnClass: 'action-view' },
    {
      key: 'reload',
      label: '重载',
      icon: Refresh,
      btnClass: 'action-refresh',
      visible: !!row.skill_path
    },
    { key: 'edit', label: '编辑', icon: Edit, btnClass: 'action-edit' },
    {
      key: 'toggle',
      label: row.is_enabled === 1 ? '禁用' : '启用',
      icon: Open,
      btnClass: row.is_enabled === 1 ? 'action-warning' : 'action-success'
    },
    {
      key: 'delete',
      label: '删除',
      icon: Delete,
      btnClass: 'action-delete',
      danger: true,
      visible: row.is_system !== 1
    }
  ]
}

function onRowAction(row: any, key: string) {
  switch (key) {
    case 'view':
      handleViewContent(row)
      break
    case 'reload':
      handleReload(row)
      break
    case 'edit':
      handleEdit(row)
      break
    case 'toggle':
      handleToggleStatus(row)
      break
    case 'delete':
      handleDelete(row)
      break
  }
}

async function loadData() {
  loading.value = true
  try {
    const res = await skillApi.page(queryParams)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<Skill>
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
    category: undefined,
    is_enabled: undefined
  }
  handleSearch()
}

function handleEdit(row: Skill) {
  dialogTitle.value = '编辑 Skill'
  formData.id = row.id
  formData.name = row.name
  formData.description = row.description
  formData.skill_path = row.skill_path || ''
  formData.category = row.category || ''
  formData.tags = row.tags || ''
  formData.icon = row.icon || ''
  formData.is_enabled = row.is_enabled
  dialogVisible.value = true
}

async function handleSubmit() {
  try {
    await skillApi.update({
      id: formData.id,
      category: formData.category || undefined,
      tags: formData.tags || undefined,
      icon: formData.icon || undefined,
      is_enabled: formData.is_enabled
    })
    ElMessage.success('更新成功')
    dialogVisible.value = false
    loadData()
  } catch {
    // Error handled by interceptor
  }
}

function handleDelete(row: Skill) {
  if (row.is_system === 1) {
    ElMessage.warning('系统预置 Skill 不能删除')
    return
  }

  ElMessageBox.confirm(`确定要删除 Skill「${row.name}」吗？`, '提示', {
    type: 'warning'
  })
    .then(async () => {
      await skillApi.delete(row.id)
      ElMessage.success('删除成功')
      loadData()
    })
    .catch(() => {})
}

async function handleToggleStatus(row: Skill) {
  const newStatus = row.is_enabled === 1 ? 0 : 1
  try {
    await skillApi.update({
      id: row.id,
      is_enabled: newStatus
    })
    ElMessage.success(newStatus === 1 ? '已启用' : '已禁用')
    loadData()
  } catch {
    // Error handled by interceptor
  }
}

async function handleViewContent(row: Skill) {
  if (!row.skill_path) {
    ElMessage.warning('该 Skill 没有文件路径')
    return
  }
  contentTitle.value = row.name
  contentDialogVisible.value = true
  contentLoading.value = true
  contentText.value = ''
  try {
    const res = await skillApi.getContent(row.id)
    if (res.data.code === 1) {
      contentText.value = res.data.data
    }
  } catch {
    // error handled by interceptor
  } finally {
    contentLoading.value = false
  }
}

function handleSelectionChange(rows: Skill[]): void {
  selectedRows.value = rows
}

async function handleReload(row: Skill) {
  if (!row.skill_path) {
    ElMessage.warning('该 Skill 没有文件路径')
    return
  }
  try {
    await skillApi.reload(row.id)
    ElMessage.success('重新加载成功')
    loadData()
  } catch {
    // Error handled by interceptor
  }
}

async function handleBatchReload(): Promise<void> {
  if (selectedRows.value.length === 0) return
  const ids = selectedRows.value.filter(row => !!row.skill_path).map(row => row.id)
  if (ids.length === 0) {
    ElMessage.warning('选中的 Skill 均无文件路径')
    return
  }
  batchReloadLoading.value = true
  try {
    const res = await skillApi.reloadBatch(ids)
    const data = res.data.data
    if (data.failed_count === 0) {
      ElMessage.success(`已成功重新加载 ${data.success_count} 个 Skill`)
    } else {
      const details = data.failed_items
        .map((item: { id: number; reason: string }) => `ID ${item.id}（${item.reason}）`)
        .join('；')
      ElMessage.warning(`成功 ${data.success_count} 个，失败 ${data.failed_count} 个：${details}`)
    }
    selectedRows.value = []
    loadData()
  } catch {
    // Error handled by interceptor
  } finally {
    batchReloadLoading.value = false
  }
}

async function handleBatchDelete(): Promise<void> {
  if (selectedRows.value.length === 0) return
  const deletableRows = selectedRows.value.filter(row => row.is_system !== 1)
  if (deletableRows.length === 0) {
    ElMessage.warning('选中的 Skill 均为系统预置，不可删除')
    return
  }
  try {
    await ElMessageBox.confirm(`确定要删除选中的 ${deletableRows.length} 个 Skill 吗？`, '提示', {
      type: 'warning'
    })
    const ids = deletableRows.map(row => row.id)
    await skillApi.deleteBatch(ids)
    ElMessage.success('删除成功')
    selectedRows.value = []
    loadData()
  } catch {
    // cancel
  }
}

function openUploadDialog() {
  uploadFileList.value = []
  uploadDialogVisible.value = true
  nextTick(() => {
    uploadRef.value?.clearFiles()
  })
}

function handleFileChange(_uploadFile: UploadFile, uploadFiles: UploadFiles) {
  uploadFileList.value = uploadFiles
}

function handleFileRemove(_uploadFile: UploadFile, uploadFiles: UploadFiles) {
  uploadFileList.value = uploadFiles
}

async function handleBatchUpload() {
  if (uploadFileList.value.length === 0) {
    ElMessage.warning('请选择要上传的文件')
    return
  }

  uploadLoading.value = true
  const formDataObj = new FormData()
  for (const file of uploadFileList.value) {
    if (file.raw) formDataObj.append('files', file.raw)
  }
  try {
    const res = await axios.post('/api/skill/upload_batch', formDataObj)
    const data = res.data
    if (data.code === 1) {
      const result = data.data
      if (result.failed_count > 0) {
        const details = result.failed_items
          .map((item: { filename: string; reason: string }) => `${item.filename}（${item.reason}）`)
          .join('；')
        ElMessage.warning(
          `成功 ${result.success_count} 个，失败 ${result.failed_count} 个：${details}`
        )
      } else {
        ElMessage.success(`已成功上传 ${result.success_count} 个 Skill`)
      }
      uploadDialogVisible.value = false
      loadData()
    } else {
      ElMessage.error(data.msg || '上传失败')
    }
  } catch {
    ElMessage.error('上传失败')
  } finally {
    uploadLoading.value = false
  }
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

onMounted(() => {
  loadData()
})
</script>

<template>
  <div class="skill-list-page page">
    <div class="page-header">
      <h1 class="page-title">Skill 管理</h1>
      <div class="header-actions">
        <el-button type="primary" :icon="Upload" @click="openUploadDialog">上传 Skill</el-button>
        <el-button type="primary" :disabled="!canReload" @click="handleBatchReload">
          批量重载
        </el-button>
        <el-button type="danger" :disabled="!canBatchDelete" @click="handleBatchDelete">
          批量删除
        </el-button>
      </div>
    </div>

    <div class="search-bar">
      <el-form inline>
        <el-form-item label="名称">
          <el-input v-model="queryParams.condition.name" placeholder="请输入" clearable />
        </el-form-item>
        <el-form-item label="分类">
          <el-input v-model="queryParams.condition.category" placeholder="请输入" clearable />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="queryParams.condition.is_enabled" placeholder="全部" clearable>
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
      <el-table
        v-loading="loading"
        :data="tableData"
        stripe
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="50" align="center" />
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" label="名称" min-width="100" />
        <el-table-column prop="description" label="描述" min-width="100" show-overflow-tooltip />
        <el-table-column prop="skill_path" label="文件路径" min-width="100" show-overflow-tooltip />
        <el-table-column prop="category" label="分类" width="100" />
        <el-table-column prop="is_enabled" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_enabled === 1 ? 'success' : 'info'" size="small">
              {{ row.is_enabled === 1 ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="create_time" label="创建时间" width="180" />
        <el-table-column label="操作" :width="isMobile ? 60 : 250" fixed="right">
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

    <el-dialog v-model="uploadDialogVisible" title="上传 Skill" width="500px">
      <el-upload
        ref="uploadRef"
        drag
        multiple
        :auto-upload="false"
        accept=".zip"
        :on-change="handleFileChange"
        :on-remove="handleFileRemove"
      >
        <el-icon class="el-icon--upload"><Upload /></el-icon>
        <div class="el-upload__text">
          拖拽 ZIP 文件到此处或
          <em>点击上传</em>
        </div>
        <template #tip>
          <div class="el-upload__tip">仅支持 ZIP 格式，单文件不超过 10MB，可多选</div>
        </template>
      </el-upload>
      <template #footer>
        <el-button @click="uploadDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploadLoading" @click="handleBatchUpload">
          上传
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="600px" destroy-on-close>
      <el-form :model="formData" label-width="100px">
        <el-form-item label="名称">
          <el-input v-model="formData.name" disabled />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="formData.description" type="textarea" :rows="2" disabled />
        </el-form-item>
        <el-form-item v-if="formData.skill_path" label="文件路径">
          <el-input v-model="formData.skill_path" disabled />
        </el-form-item>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="分类">
              <el-input v-model="formData.category" placeholder="分类" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="状态">
              <el-radio-group v-model="formData.is_enabled">
                <el-radio :value="1">启用</el-radio>
                <el-radio :value="0">禁用</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="标签">
              <el-input v-model="formData.tags" placeholder="标签（逗号分隔）" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="图标">
              <el-input v-model="formData.icon" placeholder="图标名称" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="contentDialogVisible"
      :title="`${contentTitle} - SKILL.md`"
      width="800px"
      destroy-on-close
    >
      <div v-loading="contentLoading" class="skill-content">
        <pre v-if="contentText" class="skill-content-pre">{{ contentText }}</pre>
        <el-empty v-else-if="!contentLoading" description="暂无内容" />
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.header-actions {
  display: flex;
  gap: 12px;
}

.skill-content {
  max-height: 60vh;
  overflow-y: auto;
}

.skill-content-pre {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
