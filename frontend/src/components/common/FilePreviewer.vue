<script setup lang="ts">
import { computed } from 'vue'
import { fileApi } from '@/api/file'
import { isImage, isVideo, isAudio } from '@/utils/format'

export interface FileItem {
  id: number
  original_name: string
  mime_type: string
  preview_url?: string
}

export interface ImagePreviewData {
  url: string
  urls: string[]
  index: number
}

const props = defineProps<{
  files: FileItem[]
}>()

const emit = defineEmits<{
  (e: 'preview', data: ImagePreviewData): void
}>()

const imageUrls = computed(() =>
  props.files.filter(f => isImage(f.mime_type)).map(f => getFileUrl(f))
)

const imageIndexMap = computed(() => {
  const map: Record<number, number> = {}
  let idx = 0
  for (const f of props.files) {
    if (isImage(f.mime_type)) {
      map[f.id] = idx++
    }
  }
  return map
})

function getFileUrl(file: FileItem): string {
  return file.preview_url || fileApi.download(file.id)
}

function handleImageClick(file: FileItem) {
  emit('preview', {
    url: getFileUrl(file),
    urls: imageUrls.value,
    index: imageIndexMap.value[file.id]
  })
}
</script>

<template>
  <div class="file-previewer">
    <div v-for="file in files" :key="file.id" class="file-previewer-item">
      <img
        v-if="isImage(file.mime_type)"
        :src="getFileUrl(file)"
        class="file-thumbnail"
        @click="handleImageClick(file)"
      />
      <div v-else-if="isVideo(file.mime_type)" class="file-media-wrapper">
        <video :src="getFileUrl(file)" controls preload="none" class="file-video" />
      </div>
      <div v-else-if="isAudio(file.mime_type)" class="file-media-wrapper">
        <audio :src="getFileUrl(file)" controls class="file-audio" />
      </div>
      <a v-else :href="getFileUrl(file)" target="_blank" class="file-link">
        {{ file.original_name }}
      </a>
    </div>
  </div>
</template>

<style scoped>
.file-previewer {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.file-previewer-item {
  display: inline-flex;
  flex-direction: column;
}

.file-thumbnail {
  max-width: 200px;
  max-height: 200px;
  border-radius: 8px;
  cursor: pointer;
  object-fit: cover;
  border: 1px solid #ebeef5;
}

.file-media-wrapper {
  display: flex;
  align-items: center;
}

.file-video {
  max-width: 320px;
  max-height: 200px;
  border-radius: 8px;
  border: 1px solid #ebeef5;
}

.file-audio {
  max-width: 300px;
  height: 36px;
}

.file-link {
  display: inline-flex;
  align-items: center;
  padding: 6px 12px;
  background: #f5f7fa;
  border-radius: 6px;
  color: #409eff;
  text-decoration: none;
  font-size: 13px;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border: 1px solid #ebeef5;
}

.file-link:hover {
  background: #ecf5ff;
  border-color: #d9ecff;
}
</style>
