<script setup lang="ts">
import { ref, watch } from 'vue'
import { Search, Upload } from '@element-plus/icons-vue'
import type { FileInfo, FileCondition } from '@/api/file'
import { fileApi } from '@/api/file'
import { formatFileSize, isImage, getFileTypeTag } from '@/utils/format'
import { ElMessage } from 'element-plus'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    selectedIds?: number[]
    multiple?: boolean
    maxSelect?: number
    accept?: string
    maxSize?: number
  }>(),
  {
    selectedIds: () => [],
    multiple: true,
    maxSelect: 0,
    accept: '',
    maxSize: 0
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'update:selectedIds', value: number[]): void
  (e: 'confirm', files: FileInfo[]): void
}>()

const visible = ref(false)
const keyword = ref('')
const fileList = ref<FileInfo[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const uploading = ref(false)
const localSelected = ref(new Set<number>())

watch(
  () => props.modelValue,
  val => {
    visible.value = val
    if (val) {
      localSelected.value = new Set(props.selectedIds)
      currentPage.value = 1
      keyword.value = ''
      loadFiles()
    }
  }
)

watch(visible, val => {
  emit('update:modelValue', val)
})

async function loadFiles(): Promise<void> {
  loading.value = true
  try {
    const condition: FileCondition = {}
    if (keyword.value.trim()) {
      condition.original_name = keyword.value.trim()
    }
    if (props.accept) {
      condition.mime_type = props.accept
    }
    const res = await fileApi.page({
      condition,
      page: currentPage.value,
      page_size: pageSize.value
    })
    const data = res.data.data
    fileList.value = data?.items || data?.list || data?.rows || []
    total.value = data?.total || 0
  } catch {
    fileList.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function handleSearch(): void {
  currentPage.value = 1
  loadFiles()
}

function handlePageChange(page: number): void {
  currentPage.value = page
  loadFiles()
}

function handleSizeChange(size: number): void {
  pageSize.value = size
  currentPage.value = 1
  loadFiles()
}

function toggleSelect(id: number): void {
  if (!props.multiple) {
    localSelected.value.clear()
    localSelected.value.add(id)
    return
  }
  if (
    props.maxSelect > 0 &&
    !localSelected.value.has(id) &&
    localSelected.value.size >= props.maxSelect
  ) {
    ElMessage.warning(`最多选择 ${props.maxSelect} 个文件`)
    return
  }
  if (props.maxSize > 0 && !localSelected.value.has(id)) {
    const file = fileList.value.find(f => f.id === id)
    if (file && file.file_size > props.maxSize * 1024 * 1024) {
      ElMessage.warning(`文件 "${file.original_name}" 超过 ${props.maxSize}MB 限制`)
      return
    }
  }
  if (localSelected.value.has(id)) {
    localSelected.value.delete(id)
  } else {
    localSelected.value.add(id)
  }
}

function isSelected(id: number): boolean {
  return localSelected.value.has(id)
}

async function handleUpload(options: {
  file: File
  onSuccess: () => void
  onError: (err: Error) => void
}): Promise<void> {
  if (props.maxSize > 0 && options.file.size > props.maxSize * 1024 * 1024) {
    ElMessage.warning(`文件大小不能超过 ${props.maxSize}MB`)
    options.onError(new Error('文件过大'))
    return
  }
  uploading.value = true
  try {
    const res = await fileApi.upload(options.file, '')
    if (res.data.code === 1) {
      options.onSuccess()
      await loadFiles()
    } else {
      options.onError(new Error(res.data.msg || '上传失败'))
    }
  } catch {
    options.onError(new Error('上传失败'))
  } finally {
    uploading.value = false
  }
}

function handleConfirm(): void {
  const ids = [...localSelected.value]
  const files = fileList.value.filter(f => localSelected.value.has(f.id))
  emit('update:selectedIds', ids)
  emit('confirm', files)
  visible.value = false
}

function handleCancel(): void {
  visible.value = false
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="选择文件"
    width="680px"
    :close-on-click-modal="false"
    destroy-on-close
    @closed="handleCancel"
  >
    <div class="file-picker-toolbar">
      <el-input
        v-model="keyword"
        placeholder="搜索文件名"
        clearable
        :prefix-icon="Search"
        size="small"
        style="flex: 1"
        @keyup.enter="handleSearch"
        @clear="handleSearch"
      />
      <el-upload
        action=""
        :show-file-list="false"
        :http-request="handleUpload"
        style="margin-left: 8px"
      >
        <el-button :icon="Upload" size="small" type="primary" plain :loading="uploading">
          上传
        </el-button>
      </el-upload>
    </div>

    <div v-loading="loading" class="file-picker-list">
      <div
        v-for="file in fileList"
        :key="file.id"
        class="file-picker-item"
        :class="{ selected: isSelected(file.id) }"
        @click="toggleSelect(file.id)"
      >
        <el-checkbox
          :model-value="isSelected(file.id)"
          @click.stop
          @change="toggleSelect(file.id)"
        />
        <div class="file-picker-preview">
          <img
            v-if="isImage(file.mime_type)"
            :src="file.preview_url || fileApi.download(file.id)"
            class="file-picker-thumbnail"
          />
          <el-tag v-else :type="getFileTypeTag(file.file_type)" size="small" effect="plain">
            {{ file.file_type?.toUpperCase() || 'FILE' }}
          </el-tag>
        </div>
        <div class="file-picker-info">
          <span class="file-picker-name" :title="file.original_name">{{ file.original_name }}</span>
          <span class="file-picker-size">{{ formatFileSize(file.file_size) }}</span>
        </div>
      </div>
      <div v-if="!loading && fileList.length === 0" class="file-picker-empty">
        <el-text type="info">暂无文件</el-text>
      </div>
    </div>

    <div class="file-picker-footer">
      <el-pagination
        v-if="total > 0"
        size="small"
        layout="total, sizes, prev, pager, next"
        :total="total"
        :current-page="currentPage"
        :page-size="pageSize"
        :page-sizes="[10, 20, 50]"
        @current-change="handlePageChange"
        @size-change="handleSizeChange"
      />
      <div class="file-picker-actions">
        <el-button size="small" @click="handleCancel">取消</el-button>
        <el-button size="small" type="primary" @click="handleConfirm">
          确认选择{{ localSelected.size > 0 ? `(${localSelected.size})` : '' }}
        </el-button>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
.file-picker-toolbar {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}

.file-picker-list {
  max-height: 400px;
  overflow-y: auto;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.file-picker-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  cursor: pointer;
  border-bottom: 1px solid #f1f5f9;
  transition: background 0.15s;
}

.file-picker-item:last-child {
  border-bottom: none;
}

.file-picker-item:hover {
  background: #f8fafc;
}

.file-picker-item.selected {
  background: #eff6ff;
}

.file-picker-preview {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.file-picker-thumbnail {
  width: 40px;
  height: 40px;
  object-fit: cover;
  border-radius: 4px;
}

.file-picker-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.file-picker-name {
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-picker-size {
  font-size: 12px;
  color: #909399;
}

.file-picker-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 0;
}

.file-picker-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
}

.file-picker-actions {
  display: flex;
  gap: 8px;
}
</style>
