<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { CopyDocument, View } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { detectFileLanguage } from '@/utils/format'

const props = defineProps<{
  toolName: string
  result: unknown
}>()

let hljsModule: (typeof import('highlight.js').default) | null = null

async function loadHljs() {
  if (!hljsModule) {
    hljsModule = (await import('highlight.js')).default
  }
}

function escapeHtml(text: string): string {
  const el = document.createElement('div')
  el.textContent = text
  return el.innerHTML
}

const parsedResult = computed(() => {
  if (props.result === undefined || props.result === null) return null
  if (typeof props.result === 'string') {
    try {
      return JSON.parse(props.result)
    } catch {
      return null
    }
  }
  if (typeof props.result === 'object') return props.result
  return null
})

const isFileRead = computed(() => {
  return (
    props.toolName === 'file_read' &&
    parsedResult.value?.success &&
    typeof parsedResult.value?.content === 'string' &&
    typeof parsedResult.value?.file_path === 'string'
  )
})

const isTextEditor = computed(() => {
  return (
    props.toolName === 'text_editor' &&
    parsedResult.value?.success &&
    typeof parsedResult.value?.diff === 'string'
  )
})

const isSaveFile = computed(() => {
  return (
    props.toolName === '__save_file__' &&
    parsedResult.value?.success &&
    parsedResult.value?.preview_url
  )
})

const filePath = computed(() => parsedResult.value?.file_path || '')

const fileLanguage = computed(() => detectFileLanguage(filePath.value) || 'plaintext')

interface CodeLine {
  lineNumber: number
  text: string
}

const fileReadCodeLines = computed<CodeLine[]>(() => {
  const content: string = parsedResult.value?.content || ''
  const lines = content.split('\n')
  const result: CodeLine[] = []
  for (const line of lines) {
    const match = line.match(/^(\d+):\s?(.*)/)
    if (match) {
      result.push({ lineNumber: parseInt(match[1]), text: match[2] })
    } else if (line) {
      result.push({ lineNumber: 0, text: line })
    }
  }
  return result
})

const highlightedLines = ref<string[]>([])

const fileReadMeta = computed(() => {
  const r = parsedResult.value
  if (!r) return ''
  const offset = r.offset || 1
  const limit = r.limit || fileReadCodeLines.value.length
  const lastLine = fileReadCodeLines.value[fileReadCodeLines.value.length - 1]
  const actualEnd = lastLine ? lastLine.lineNumber : offset + limit - 1
  const total = r.total_lines
  return total ? `第 ${offset}-${actualEnd} 行 / 共 ${total} 行` : `${fileReadCodeLines.value.length} 行`
})

interface DiffLine {
  type: 'remove' | 'add'
  text: string
}

const diffLines = computed<DiffLine[]>(() => {
  const diff: string = parsedResult.value?.diff || ''
  return diff.split('\n').map((line) => {
    if (line.startsWith('-')) return { type: 'remove', text: line.slice(1) }
    if (line.startsWith('+')) return { type: 'add', text: line.slice(1) }
    return { type: 'add', text: line }
  })
})

const mediaInfo = computed(() => {
  const r = parsedResult.value
  if (!r?.preview_url) return null
  return {
    preview_url: r.preview_url as string,
    file_name: (r.file_name || '') as string,
    mime_type: (r.mime_type || '') as string,
    isVideo: ((r.mime_type || '') as string).startsWith('video/'),
    isImage: ((r.mime_type || '') as string).startsWith('image/')
  }
})

const copyText = computed(() => {
  if (isFileRead.value) {
    return fileReadCodeLines.value.map((l) => l.text).join('\n')
  }
  if (isTextEditor.value) {
    return parsedResult.value?.diff || ''
  }
  return ''
})

async function handleCopy() {
  try {
    await navigator.clipboard.writeText(copyText.value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}

function openMediaPreview() {
  if (mediaInfo.value?.preview_url) {
    window.open(mediaInfo.value.preview_url, '_blank')
  }
}

const fallbackText = computed(() => {
  if (props.result === undefined || props.result === null) return ''
  try {
    if (typeof props.result === 'string') {
      try {
        const parsed = JSON.parse(props.result)
        return JSON.stringify(parsed, null, 2)
      } catch {
        return props.result
      }
    }
    return JSON.stringify(props.result, null, 2)
  } catch {
    return String(props.result)
  }
})

async function handleFallbackCopy() {
  try {
    await navigator.clipboard.writeText(fallbackText.value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}

async function updateHighlighting() {
  await loadHljs()
  if (!hljsModule) return

  if (isFileRead.value) {
    const lang = fileLanguage.value
    highlightedLines.value = fileReadCodeLines.value.map((line) => {
      try {
        const result = hljsModule!.highlight(line.text, { language: lang })
        return result.value
      } catch {
        return escapeHtml(line.text)
      }
    })
  }
}

watch(
  () => [props.toolName, props.result],
  () => {
    nextTick(() => updateHighlighting())
  },
  { immediate: true }
)
</script>

<template>
  <div v-if="isFileRead" class="tool-read-result">
    <div class="tool-result-header">
      <div class="tool-result-meta">
        <span class="tool-result-path" :title="filePath">{{ filePath }}</span>
        <span class="tool-result-info">{{ fileReadMeta }}</span>
      </div>
      <div class="tool-result-actions">
        <span v-if="parsedResult?._truncated" class="truncated-badge">内容已截断</span>
        <el-button :icon="CopyDocument" link size="small" @click="handleCopy">复制</el-button>
      </div>
    </div>
    <div class="tool-code-viewer">
      <table class="code-table">
        <tr v-for="(line, i) in fileReadCodeLines" :key="i" class="code-row">
          <td class="line-number">{{ line.lineNumber }}</td>
          <td class="line-code">
            <span v-if="highlightedLines[i]" v-html="highlightedLines[i]"></span>
            <span v-else>{{ line.text }}</span>
          </td>
        </tr>
      </table>
    </div>
  </div>

  <div v-else-if="isTextEditor" class="tool-edit-result">
    <div class="tool-result-header">
      <div class="tool-result-meta">
        <span class="tool-result-path" :title="parsedResult?.file_path">{{
          parsedResult?.file_path
        }}</span>
        <span class="tool-result-info">替换 {{ parsedResult?.replaced_count }} 处</span>
      </div>
      <div class="tool-result-actions">
        <el-button :icon="CopyDocument" link size="small" @click="handleCopy">复制</el-button>
      </div>
    </div>
    <div class="tool-diff-viewer">
      <div v-for="(line, i) in diffLines" :key="i" :class="['diff-line', line.type]">
        <span class="diff-prefix">{{ line.type === 'remove' ? '-' : '+' }}</span>
        <span class="diff-text">{{ line.text }}</span>
      </div>
    </div>
    <div v-if="parsedResult?.message" class="tool-edit-message">{{ parsedResult.message }}</div>
  </div>

  <div v-else-if="isSaveFile && mediaInfo" class="tool-media-result">
    <div class="media-inline-preview">
      <video
        v-if="mediaInfo.isVideo"
        :src="mediaInfo.preview_url"
        controls
        preload="none"
        class="media-video"
      />
      <img
        v-if="mediaInfo.isImage"
        :src="mediaInfo.preview_url"
        class="media-image"
        @click="openMediaPreview"
      />
    </div>
    <el-button :icon="View" size="small" @click="openMediaPreview"> 查看预览 </el-button>
  </div>

  <div v-else class="tool-fallback-result">
    <pre class="tool-fallback-pre">{{ fallbackText }}</pre>
    <el-button
      :icon="CopyDocument"
      link
      size="small"
      class="tool-fallback-copy"
      @click="handleFallbackCopy"
      >复制</el-button
    >
  </div>
</template>

<style scoped>
.tool-read-result,
.tool-edit-result,
.tool-media-result,
.tool-fallback-result {
  border-top: 1px solid #e2e8f0;
}

.tool-result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
}

.tool-result-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.tool-result-path {
  font-size: 12px;
  font-weight: 600;
  color: #334155;
  font-family: 'Fira Code', 'Consolas', monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 360px;
}

.tool-result-info {
  font-size: 11px;
  color: #94a3b8;
  white-space: nowrap;
}

.tool-result-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.truncated-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  white-space: nowrap;
  background: #fce7f3;
  color: #9d174d;
}

.tool-code-viewer {
  max-height: 400px;
  overflow: auto;
  background: #1e1e1e;
}

.code-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
}

.code-row:hover {
  background: rgba(255, 255, 255, 0.04);
}

.line-number {
  width: 1%;
  min-width: 48px;
  padding: 1px 12px 1px 16px;
  text-align: right;
  vertical-align: top;
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #6e7681;
  user-select: none;
  border-right: 1px solid #30363d;
  white-space: nowrap;
}

.line-code {
  padding: 1px 16px;
  vertical-align: top;
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #d4d4d4;
  white-space: pre;
}

.tool-diff-viewer {
  max-height: 400px;
  overflow-y: auto;
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.6;
}

.diff-line {
  display: flex;
  padding: 1px 16px;
}

.diff-line.remove {
  background: rgba(248, 113, 113, 0.12);
}

.diff-line.add {
  background: rgba(74, 222, 128, 0.1);
}

.diff-prefix {
  width: 20px;
  flex-shrink: 0;
  user-select: none;
}

.diff-line.remove .diff-prefix {
  color: #f87171;
}

.diff-line.add .diff-prefix {
  color: #4ade80;
}

.diff-text {
  white-space: pre-wrap;
  word-break: break-all;
  color: #334155;
}

.tool-edit-message {
  padding: 8px 16px;
  font-size: 12px;
  color: #059669;
  background: rgba(236, 253, 245, 0.6);
}

.tool-fallback-result {
  position: relative;
}

.tool-fallback-pre {
  margin: 0;
  padding: 12px 16px;
  background: rgba(248, 250, 252, 0.8);
  font-family: 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #64748b;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 300px;
  overflow-y: auto;
}

.tool-fallback-copy {
  position: absolute;
  top: 6px;
  right: 8px;
  font-size: 11px;
  color: #94a3b8;
}

.tool-fallback-copy:hover {
  color: #409eff;
}

.tool-media-result {
  padding: 8px 12px;
}

.media-inline-preview {
  margin-bottom: 8px;
}

.media-video {
  max-width: 100%;
  max-height: 360px;
  border-radius: 6px;
}

.media-image {
  max-width: 100%;
  max-height: 360px;
  border-radius: 6px;
  cursor: pointer;
}
</style>
