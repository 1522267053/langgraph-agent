<script setup lang="ts">
/**
 * 统一文件查看弹窗：单一入口覆盖图片/视频/音频/xlsx/docx/pdf/txt/md。
 *
 * - 图片：el-image（可点击全屏放大，preview-teleported）
 * - 视频/音频：原生控件内嵌
 * - xlsx/docx/pdf：自动转发给 DocumentPreviewerDialog（本弹窗不打开）
 * - txt：等宽原文预览；md：MarkdownRenderer 渲染
 *   （fetch 同源带认证 cookie 拉取；超过 2MB 提示下载，防大文本卡页面）
 *
 * 用法（v-model:visible + file）：
 *   <FileViewerDialog v-model:visible="viewerVisible" :file="viewerFile" />
 */
import { ref, watch } from 'vue'
import { Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { fileApi } from '@/api/file'
import { isImage, isVideo, isAudio, isPreviewableDoc, isPlainText, isMarkdown } from '@/utils/format'
import DocumentPreviewerDialog from '@/components/common/DocumentPreviewerDialog.vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'

export interface FileViewerFile {
  id: number
  original_name: string
  mime_type: string
  file_size?: number
  preview_url?: string
}

const props = defineProps<{
  visible: boolean
  file: FileViewerFile | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
}>()

/** 纯文本/Markdown 预览的大小护栏：超出直接提示下载，避免一次性渲染大文件卡死页面 */
const TEXT_MAX_BYTES = 2 * 1024 * 1024

const textLoading = ref(false)
const textContent = ref('')
const textTooLarge = ref(false)

const docViewerVisible = ref(false)
const docViewerFile = ref<FileViewerFile | null>(null)

function isTextFile(file: FileViewerFile): boolean {
  return isPlainText(file.mime_type, file.original_name) || isMarkdown(file.mime_type, file.original_name)
}

function handleDownload(): void {
  if (props.file) window.open(fileApi.download(props.file.id), '_blank')
}

async function loadText(): Promise<void> {
  if (!props.file) return
  textLoading.value = true
  try {
    const res = await fetch(fileApi.download(props.file.id))
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    textContent.value = await res.text()
  } catch {
    ElMessage.error({ message: '预览加载失败', duration: 5000 })
    emit('update:visible', false)
  } finally {
    textLoading.value = false
  }
}

watch(
  () => [props.visible, props.file?.id] as const,
  ([visible]) => {
    if (!visible || !props.file) {
      textContent.value = ''
      textTooLarge.value = false
      textLoading.value = false
      return
    }
    if (isPreviewableDoc(props.file.mime_type, props.file.original_name)) {
      // xlsx/docx/pdf：本弹窗不打开，立即转发给文档预览器
      emit('update:visible', false)
      docViewerFile.value = props.file
      docViewerVisible.value = true
      return
    }
    if (isTextFile(props.file)) {
      textContent.value = ''
      textTooLarge.value = props.file.file_size !== undefined && props.file.file_size > TEXT_MAX_BYTES
      if (!textTooLarge.value) loadText()
    }
  }
)
</script>

<template>
  <!-- 图片/视频/音频/txt/md 内嵌预览弹窗（xlsx/docx/pdf 已在 watch 中转发，不会到达这里） -->
  <el-dialog
    :model-value="visible"
    :title="file?.original_name || '文件预览'"
    width="800px"
    append-to-body
    destroy-on-close
    @update:model-value="v => emit('update:visible', v)"
  >
    <!-- 图片：el-image 点击可全屏放大 -->
    <div v-if="file && isImage(file.mime_type)" class="viewer-image-wrapper">
      <el-image :src="file.preview_url || fileApi.download(file.id)" fit="contain" :preview-src-list="[file.preview_url || fileApi.download(file.id)]" preview-teleported />
    </div>

    <!-- 视频 -->
    <video
      v-else-if="file && isVideo(file.mime_type)"
      :src="file.preview_url || fileApi.download(file.id)"
      controls
      class="viewer-video"
    />

    <!-- 音频 -->
    <audio
      v-else-if="file && isAudio(file.mime_type)"
      :src="file.preview_url || fileApi.download(file.id)"
      controls
      class="viewer-audio"
    />

    <!-- txt / md 文本类 -->
    <div v-else-if="file && isTextFile(file)" v-loading="textLoading" class="viewer-text-body">
      <el-empty v-if="textTooLarge" description="文件超过 2MB，请下载后查看">
        <el-button type="primary" :icon="Download" @click="handleDownload">下载文件</el-button>
      </el-empty>
      <!-- MarkdownRenderer 为多根组件（主容器 + 全屏 Teleport），class 不继承，样式走容器选择器 -->
      <MarkdownRenderer v-else-if="isMarkdown(file.mime_type, file.original_name)" :content="textContent" />
      <pre v-else class="viewer-text-plain">{{ textContent }}</pre>
    </div>
  </el-dialog>

  <!-- xlsx/docx/pdf 文档预览器（由本组件转发打开） -->
  <DocumentPreviewerDialog v-model:visible="docViewerVisible" :file="docViewerFile" />
</template>

<style scoped>
.viewer-image-wrapper {
  display: flex;
  justify-content: center;
}

.viewer-image-wrapper .el-image {
  max-width: 100%;
  max-height: 65vh;
}

.viewer-video {
  display: block;
  width: 100%;
  max-height: 65vh;
}

.viewer-audio {
  display: block;
  width: 100%;
}

.viewer-text-body {
  min-height: 120px;
  max-height: 70vh;
  overflow: auto;
}

.viewer-text-plain {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.6;
}

/* MarkdownRenderer 根容器 .markdown-body 在弹窗内的边距 */
.viewer-text-body :deep(.markdown-body) {
  padding: 0;
}
</style>
