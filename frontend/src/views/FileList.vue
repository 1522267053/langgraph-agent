<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { Upload, View, Delete, Download } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions, UploadUserFile } from 'element-plus'
import { fileApi } from '@/api/file'
import type { FileInfo, FileCondition } from '@/api/file'
import type { PaginatedResponse } from '@/types/common'
import { formatFileSize, formatDate, getFileTypeTag, isImage, isVideo } from '@/utils/format'
import ActionColumn from '@/components/common/ActionColumn.vue'
import { useIsMobile } from '@/composables/useIsMobile'

const { isMobile } = useIsMobile()

const loading = ref(false)
const tableData = ref<FileInfo[]>([])
const total = ref(0)
const selectedRows = ref<FileInfo[]>([])
const previewVisible = ref(false)
const previewUrl = ref('')
const previewType = ref<'image' | 'video'>('image')
const uploadVisible = ref(false)
const uploadFileList = ref<UploadUserFile[]>([])
const uploadRef = ref<InstanceType<(typeof import('element-plus'))['ElUpload']>>()
const uploading = ref(false)
const uploadPromises: Promise<void>[] = []

const queryParams = reactive({
  page: 1,
  page_size: 10,
  condition: {
    original_name: '',
    source_type: undefined as string | undefined
  } as FileCondition
})

async function loadFiles(): Promise<void> {
  loading.value = true
  try {
    const res = await fileApi.page(queryParams)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<FileInfo>
      tableData.value = data.items || []
      total.value = data.total
    }
  } finally {
    loading.value = false
  }
}

async function handleUpload(options: UploadRequestOptions): Promise<void> {
  const promise = (async () => {
    try {
      const res = await fileApi.upload(
        options.file,
        undefined,
        (event: { loaded: number; total: number }) => {
          options.onProgress(
            { percent: Math.round((event.loaded / event.total) * 100) },
            options.file
          )
        }
      )
      if (res.data.code === 1) {
        options.onSuccess(res.data.data)
      } else {
        options.onError(new Error(res.data.msg || '上传失败') as any)
      }
    } catch (err) {
      options.onError(err as any)
    }
  })()
  uploadPromises.push(promise)
  await promise
}

async function submitUpload(): Promise<void> {
  if (!uploadFileList.value.length) {
    ElMessage.warning('请先选择文件')
    return
  }
  uploading.value = true
  uploadPromises.length = 0
  try {
    uploadRef.value?.submit()
    await Promise.all(uploadPromises)
    const failed = uploadFileList.value.filter(f => f.status === 'fail')
    if (failed.length === 0) {
      ElMessage.success('全部上传成功')
      uploadVisible.value = false
    }
  } finally {
    uploading.value = false
  }
}

function openUploadDialog(): void {
  uploadFileList.value = []
  uploadPromises.length = 0
  uploadRef.value?.clearFiles()
  uploadVisible.value = true
}

function handleSearch(): void {
  queryParams.page = 1
  loadFiles()
}

function handleReset(): void {
  queryParams.condition = { original_name: '', source_type: undefined }
  queryParams.page = 1
  loadFiles()
}

function handlePageChange(page: number): void {
  queryParams.page = page
  loadFiles()
}

function handleSizeChange(size: number): void {
  queryParams.page_size = size
  queryParams.page = 1
  loadFiles()
}

function getSourceTypeLabel(sourceType: string | undefined): string {
  if (sourceType === 'flow') return '流程'
  if (sourceType === 'agent') return '智能体'
  if (sourceType === 'generation') return 'AI生成'
  return sourceType || '-'
}

function getRowActions(row: any) {
  return [
    {
      key: 'preview',
      label: '预览',
      icon: View,
      btnClass: 'action-view',
      visible: isImage(row.mime_type) || isVideo(row.mime_type)
    },
    { key: 'download', label: '下载', icon: Download, btnClass: 'action-download' },
    { key: 'delete', label: '删除', icon: Delete, btnClass: 'action-delete', danger: true }
  ]
}

function onRowAction(row: any, key: string) {
  switch (key) {
    case 'preview':
      handlePreview(row)
      break
    case 'download':
      handleDownload(row)
      break
    case 'delete':
      handleDelete(row)
      break
  }
}

function handleDownload(row: FileInfo): void {
  window.open(fileApi.download(row.id), '_blank')
}

function handlePreview(row: FileInfo): void {
  if (isImage(row.mime_type)) {
    previewUrl.value = row.preview_url || fileApi.download(row.id)
    previewType.value = 'image'
    previewVisible.value = true
  } else if (isVideo(row.mime_type)) {
    previewUrl.value = row.preview_url || fileApi.download(row.id)
    previewType.value = 'video'
    previewVisible.value = true
  } else {
    window.open(fileApi.download(row.id), '_blank')
  }
}

function handleSelectionChange(rows: FileInfo[]): void {
  selectedRows.value = rows
}

async function handleDelete(row: FileInfo): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定要删除文件「${row.original_name}」吗？`, '提示', {
      type: 'warning'
    })
    const res = await fileApi.delete(row.id)
    if (res.data.code === 1) {
      ElMessage.success('删除成功')
      loadFiles()
    }
  } catch {
    // cancel
  }
}

async function handleBatchDelete(): Promise<void> {
  if (selectedRows.value.length === 0) return
  try {
    await ElMessageBox.confirm(`确定要删除选中的 ${selectedRows.value.length} 个文件吗？`, '提示', {
      type: 'warning'
    })
    const ids = selectedRows.value.map(r => r.id)
    const res = await fileApi.batchDelete(ids)
    if (res.data.code === 1) {
      ElMessage.success('删除成功')
      selectedRows.value = []
      loadFiles()
    }
  } catch {
    // cancel
  }
}

onMounted(() => {
  loadFiles()
})
</script>

<template>
  <div class="file-list-page page">
    <div class="page-header">
      <h1 class="page-title">文件管理</h1>
      <div class="header-actions">
        <el-button type="primary" :icon="Upload" @click="openUploadDialog">上传文件</el-button>
        <el-button type="danger" :disabled="selectedRows.length === 0" @click="handleBatchDelete">
          批量删除
        </el-button>
      </div>
    </div>

    <div class="search-bar">
      <el-form inline>
        <el-form-item label="文件名">
          <el-input
            v-model="queryParams.condition.original_name"
            placeholder="搜索文件名..."
            clearable
            @keyup.enter="handleSearch"
          />
        </el-form-item>
        <el-form-item label="来源">
          <el-select v-model="queryParams.condition.source_type" placeholder="全部" clearable>
            <el-option label="流程" value="flow" />
            <el-option label="智能体" value="agent" />
            <el-option label="AI生成" value="generation" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button class="btn-search" @click="handleSearch">查询</el-button>
          <el-button class="btn-reset" @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div v-loading="loading" class="card-panel table-container">
      <el-table
        v-if="tableData.length > 0"
        :data="tableData"
        stripe
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="50" align="center" />
        <el-table-column label="预览" width="70" align="center">
          <template #default="{ row }">
            <img
              v-if="isImage(row.mime_type)"
              :src="row.preview_url || fileApi.download(row.id)"
              class="file-thumbnail"
              @click="handlePreview(row)"
            />
            <el-tag v-else size="small" :type="getFileTypeTag(row.file_type)">
              {{ (row.file_type || 'FILE').toUpperCase() }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="id" label="ID" width="70" align="center" />
        <el-table-column
          prop="original_name"
          label="文件名"
          min-width="200"
          show-overflow-tooltip
        />
        <el-table-column prop="file_type" label="类型" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="getFileTypeTag(row.file_type)">
              {{ (row.file_type || '-').toUpperCase() }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="120" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.source_type" size="small" type="info" effect="plain">
              {{ getSourceTypeLabel(row.source_type) }}
            </el-tag>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="100" align="right">
          <template #default="{ row }">
            {{ formatFileSize(row.file_size) }}
          </template>
        </el-table-column>
        <el-table-column label="上传时间" width="170">
          <template #default="{ row }">
            {{ formatDate(row.create_time) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" :width="isMobile ? 60 : 180" align="center" fixed="right">
          <template #default="{ row }">
            <ActionColumn :actions="getRowActions(row)" @action="onRowAction(row, $event)" />
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无文件，点击上方按钮上传" />

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
      v-model="uploadVisible"
      title="上传文件"
      width="520px"
      :close-on-click-modal="false"
      @closed="(uploadRef?.clearFiles(), (uploadFileList = []), loadFiles())"
    >
      <el-upload
        ref="uploadRef"
        v-model:file-list="uploadFileList"
        action=""
        drag
        multiple
        :auto-upload="false"
        :http-request="handleUpload"
      >
        <el-icon class="el-icon--upload"><Upload /></el-icon>
        <div class="el-upload__text">
          将文件拖到此处，或
          <em>点击选择</em>
        </div>
      </el-upload>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="uploading"
          :disabled="!uploadFileList.length"
          @click="submitUpload"
        >
          上传
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="previewVisible"
      :title="previewType === 'video' ? '视频预览' : '图片预览'"
      width="720px"
      destroy-on-close
    >
      <div v-if="previewType === 'image'" class="preview-image-wrapper">
        <el-image
          :src="previewUrl"
          fit="contain"
          :preview-src-list="[previewUrl]"
          preview-teleported
        />
      </div>
      <video v-else :src="previewUrl" controls autoplay class="preview-video" />
    </el-dialog>
  </div>
</template>

<style scoped>
.header-actions {
  display: flex;
  gap: 12px;
}

.file-thumbnail {
  width: 40px;
  height: 40px;
  object-fit: cover;
  border-radius: 4px;
}

.preview-video {
  max-width: 100%;
  max-height: 80vh;
  display: block;
  margin: 0 auto;
}
</style>
