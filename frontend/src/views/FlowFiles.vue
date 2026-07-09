<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Upload, View, Download, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions, UploadRawFile } from 'element-plus'
import { fileApi } from '@/api/file'
import type { FileInfo } from '@/api/file'
import type { PaginatedResponse } from '@/types/common'
import { flowApi } from '@/api/flow'
import { formatFileSize, formatDate, getFileTypeTag, isImage, isVideo } from '@/utils/format'
import ActionColumn from '@/components/common/ActionColumn.vue'
import { useIsMobile } from '@/composables/useIsMobile'

const { isMobile } = useIsMobile()
const route = useRoute()
const router = useRouter()
const flowId = ref<number>(parseInt(route.params.id as string))
const flowName = ref('')
const loading = ref(false)
const fileList = ref<FileInfo[]>([])
const total = ref(0)
const previewVisible = ref(false)
const previewUrl = ref('')
const previewType = ref<'image' | 'video'>('image')

const queryParams = reactive({
  page: 1,
  page_size: 10,
  condition: {
    flow_id: flowId.value,
    original_name: '',
    source_type: undefined as string | undefined
  }
})

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

async function loadFlowName(): Promise<void> {
  if (!flowId.value) return
  try {
    const res = await flowApi.get(flowId.value)
    if (res.data.code === 1) {
      flowName.value = res.data.data.name || ''
    }
  } catch {
    // ignore
  }
}

function handleSearch(): void {
  queryParams.page = 1
  loadFiles()
}

function handleReset(): void {
  queryParams.condition.original_name = ''
  queryParams.condition.source_type = undefined
  queryParams.page = 1
  loadFiles()
}

async function loadFiles(): Promise<void> {
  loading.value = true
  try {
    const res = await fileApi.page(queryParams)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<FileInfo>
      fileList.value = data.items || []
      total.value = data.total
    }
  } finally {
    loading.value = false
  }
}

function beforeUpload(rawFile: UploadRawFile): boolean {
  const maxSize = 100
  if (rawFile.size > maxSize * 1024 * 1024) {
    ElMessage.error(`文件大小不能超过 ${maxSize}MB`)
    return false
  }
  return true
}

async function handleUpload(options: UploadRequestOptions): Promise<void> {
  try {
    const res = await fileApi.upload(options.file, 'flow')
    if (res.data.code === 1) {
      ElMessage.success('上传成功')
      options.onSuccess(res.data.data)
      loadFiles()
    } else {
      options.onError(new Error(res.data.msg || '上传失败') as any)
    }
  } catch (err) {
    options.onError(err as any)
  }
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

function handleDownload(row: FileInfo): void {
  window.open(fileApi.download(row.id), '_blank')
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

function handlePageChange(page: number): void {
  queryParams.page = page
  loadFiles()
}

function handleSizeChange(size: number): void {
  queryParams.page_size = size
  queryParams.page = 1
  loadFiles()
}

function handleBack(): void {
  const flowType = route.path.startsWith('/agent') ? 'agent' : 'flow'
  router.push(`/${flowType}/edit/${flowId.value}`)
}

onMounted(() => {
  loadFlowName()
  loadFiles()
})
</script>

<template>
  <div class="flow-files-page page">
    <div class="page-header">
      <div class="header-left">
        <el-button :icon="ArrowLeft" text @click="handleBack">返回编辑</el-button>
        <el-divider direction="vertical" />
        <span class="page-title">{{ flowName || '流程' }} - 文件管理</span>
        <el-tag size="small" type="info" style="margin-left: 8px">{{ total }} 个文件</el-tag>
      </div>
      <div class="header-right">
        <el-upload
          action=""
          :show-file-list="false"
          :auto-upload="true"
          :http-request="handleUpload"
          :before-upload="beforeUpload"
          multiple
        >
          <el-button type="primary" :icon="Upload">上传文件</el-button>
        </el-upload>
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
      <el-table v-if="fileList.length > 0" :data="fileList" stripe>
        <el-table-column prop="id" label="ID" width="70" align="center" />
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

      <div v-if="total > 0" class="pagination">
        <el-pagination
          v-model:current-page="queryParams.page"
          v-model:page-size="queryParams.page_size"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </div>

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
.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.page-title {
  font-size: 18px;
  font-weight: 500;
  color: #303133;
}

.file-thumbnail {
  width: 40px;
  height: 40px;
  object-fit: cover;
  border-radius: 4px;
  cursor: pointer;
}

.preview-video {
  max-width: 100%;
  max-height: 80vh;
  display: block;
  margin: 0 auto;
}
</style>
