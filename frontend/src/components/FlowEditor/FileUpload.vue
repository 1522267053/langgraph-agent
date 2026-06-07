<script setup lang="ts">
import { ElMessage } from 'element-plus'
import type { UploadRequestOptions, UploadRawFile } from 'element-plus'
import type { FileInfo } from '@/api/file'
import { fileApi } from '@/api/file'
import { formatFileSize, isImage } from '@/utils/format'

const props = defineProps<{
  sourceType?: string
  accept?: string
  multiple?: boolean
  maxSize?: number
  modelValue: FileInfo[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: FileInfo[]): void
}>()

function emitUpdate(files: FileInfo[]): void {
  emit('update:modelValue', files)
}

function beforeUpload(rawFile: UploadRawFile): boolean {
  if (props.maxSize && rawFile.size > props.maxSize * 1024 * 1024) {
    ElMessage.error(`文件大小不能超过 ${props.maxSize}MB`)
    return false
  }
  return true
}

async function handleUpload(options: UploadRequestOptions): Promise<void> {
  try {
    const res = await fileApi.upload(options.file, props.sourceType)
    if (res.data.code === 1) {
      const newFiles = [...props.modelValue, res.data.data]
      emitUpdate(newFiles)
      options.onSuccess(res.data.data)
    } else {
      options.onError(new Error(res.data.msg || '上传失败') as any)
    }
  } catch (err) {
    options.onError(err as any)
  }
}

async function handleRemove(fileInfo: FileInfo): Promise<void> {
  try {
    const res = await fileApi.delete(fileInfo.id)
    if (res.data.code === 1) {
      const newFiles = props.modelValue.filter(f => f.id !== fileInfo.id)
      emitUpdate(newFiles)
      ElMessage.success('删除成功')
    }
  } catch {
    // error handled by interceptor
  }
}

function handlePreview(fileInfo: FileInfo): void {
  if (fileInfo.download_url) {
    window.open(fileInfo.download_url, '_blank')
  }
}
</script>

<template>
  <div class="flow-file-upload">
    <el-upload
      action=""
      :show-file-list="false"
      :accept="accept"
      :multiple="multiple"
      :auto-upload="true"
      :http-request="handleUpload"
      :before-upload="beforeUpload"
      class="upload-trigger"
    >
      <el-button size="small" type="primary" plain>选择文件</el-button>
    </el-upload>

    <div v-if="modelValue.length > 0" class="file-list">
      <div v-for="file in modelValue" :key="file.id" class="file-item">
        <div class="file-preview" @click="handlePreview(file)">
          <img
            v-if="isImage(file.mime_type)"
            :src="file.preview_url || fileApi.download(file.id)"
            class="file-thumbnail"
          />
          <div v-else class="file-icon">
            <span>{{ file.file_type.toUpperCase() || 'FILE' }}</span>
          </div>
        </div>
        <div class="file-info">
          <span class="file-name" :title="file.original_name">{{ file.original_name }}</span>
          <span class="file-size">{{ formatFileSize(file.file_size) }}</span>
        </div>
        <el-button type="danger" size="small" link @click="handleRemove(file)">删除</el-button>
      </div>
    </div>

    <div v-if="!modelValue.length" class="upload-hint">
      <el-text size="small" type="info">支持上传图片、文档、xlsx、docx、视频、音频等文件</el-text>
    </div>
  </div>
</template>

<style scoped>
.flow-file-upload {
  width: 100%;
}

.upload-trigger {
  margin-bottom: 8px;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
  max-height: 200px;
  overflow-y: auto;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  background: #f5f7fa;
  border-radius: 4px;
}

.file-preview {
  flex-shrink: 0;
  cursor: pointer;
}

.file-thumbnail {
  width: 40px;
  height: 40px;
  object-fit: cover;
  border-radius: 4px;
}

.file-icon {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e6e8eb;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  color: #606266;
}

.file-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.file-name {
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-size {
  font-size: 12px;
  color: #909399;
}

.upload-hint {
  margin-top: 4px;
}
</style>
