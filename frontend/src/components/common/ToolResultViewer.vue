<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { CopyDocument, Download, View } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { collapseHooks } from '@/components/AgentChat/collapseTransition'
import DiffViewer from '@/components/AgentChat/DiffViewer.vue'
import { detectFileLanguage } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    toolName: string
    result: unknown
    /** 仅展示富结果（文件读取/编辑/媒体预览下载），纯 JSON 回退不渲染 */
    hidePlainJson?: boolean
    /** 纯 JSON 块显隐是否带高度过渡（仅用户点击过的聊天工具行为 true，
     * 程序性翻转瞬时完成；Flow 执行面板等列表场景恒为 false） */
    animateJson?: boolean
  }>(),
  { hidePlainJson: false, animateJson: false }
)

let hljsModule: typeof import('highlight.js').default | null = null

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

// dry_run 预览结果：未写入文件，头部展示匹配统计而非"替换 N 处"
const isDryRun = computed(() => parsedResult.value?.dry_run === true)

const textEditorInfo = computed(() => {
  const r = parsedResult.value
  if (!r) return ''
  if (isDryRun.value) {
    const lines = Array.isArray(r.match_lines) ? `（第 ${r.match_lines.join('、')} 行）` : ''
    return `预览：匹配 ${r.match_count ?? 0} 处${lines}`
  }
  return `替换 ${r.replaced_count} 处`
})

const isSaveFile = computed(() => {
  return (
    !!parsedResult.value?.success &&
    !!(parsedResult.value?.preview_url || parsedResult.value?.download_url)
  )
})

/** 裸字符串结果（非 JSON 文本，如 file_write 成功消息、子Agent 回复、shell 输出）：始终展示 */
const isBareString = computed(() => typeof props.result === 'string' && parsedResult.value === null)

/** 回退容器内是否有可见内容（决定 border-top 显隐，折叠的纯 JSON 时为空容器） */
const hasVisibleFallback = computed(
  () => !props.hidePlainJson || isBareString.value || !!(isSaveFile.value && mediaInfo.value)
)

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
  return total
    ? `第 ${offset}-${actualEnd} 行 / 共 ${total} 行`
    : `${fileReadCodeLines.value.length} 行`
})

/**
 * 从后端 _diff_preview 的 -/+ 行还原新旧文本（格式：旧行 - 前缀在前、新行 + 前缀
 * 在后；-.../+... 为超限截断标记，按内容行处理），交由 DiffViewer 重新计算
 * 真正的行级对齐 diff（旧消息的存量数据同样适用）
 */
const editorDiffParts = computed(() => {
  const diff: string = parsedResult.value?.diff || ''
  const oldLines: string[] = []
  const newLines: string[] = []
  let isNew = false
  for (const line of diff.split('\n')) {
    if (line.startsWith('-')) {
      isNew = false
      oldLines.push(line.slice(1))
    } else if (line.startsWith('+')) {
      isNew = true
      newLines.push(line.slice(1))
    } else {
      ;(isNew ? newLines : oldLines).push(line)
    }
  }
  return { oldText: oldLines.join('\n'), newText: newLines.join('\n') }
})

const mediaInfo = computed(() => {
  const r = parsedResult.value
  if (!r?.preview_url && !r?.download_url) return null
  return {
    preview_url: (r.preview_url || '') as string,
    download_url: (r.download_url || '') as string,
    file_name: (r.file_name || '') as string,
    mime_type: (r.mime_type || '') as string,
    isVideo: ((r.mime_type || '') as string).startsWith('video/'),
    isImage: ((r.mime_type || '') as string).startsWith('image/')
  }
})

const copyText = computed(() => {
  if (isFileRead.value) {
    return fileReadCodeLines.value.map(l => l.text).join('\n')
  }
  if (isTextEditor.value) {
    return parsedResult.value?.diff || ''
  }
  return ''
})

async function handleCopy() {
  try {
    await navigator.clipboard.writeText(copyText.value)
    ElMessage.success({ message: '已复制', duration: 5000 })
  } catch {
    ElMessage.error({ message: '复制失败', duration: 5000 })
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
    ElMessage.success({ message: '已复制', duration: 5000 })
  } catch {
    ElMessage.error({ message: '复制失败', duration: 5000 })
  }
}

async function updateHighlighting() {
  await loadHljs()
  if (!hljsModule) return

  if (isFileRead.value) {
    const lang = fileLanguage.value
    highlightedLines.value = fileReadCodeLines.value.map(line => {
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
        <span class="tool-result-path" :title="parsedResult?.file_path">
          {{ parsedResult?.file_path }}
        </span>
        <span class="tool-result-info">{{ textEditorInfo }}</span>
      </div>
      <div class="tool-result-actions">
        <el-button :icon="CopyDocument" link size="small" @click="handleCopy">复制</el-button>
      </div>
    </div>
    <DiffViewer
      class="tool-diff-viewer"
      :backup-content="editorDiffParts.oldText"
      :current-content="editorDiffParts.newText"
      :is-binary="false"
      :backup-missing="false"
      change-type="modify"
      default-view-mode="line-by-line"
      :show-toolbar="false"
    />
    <!-- dry_run 多匹配警告：实际执行会因多处匹配被拒 -->
    <div v-if="isDryRun && parsedResult?.warning" class="tool-edit-warning">
      {{ parsedResult.warning }}
    </div>
    <div v-if="parsedResult?.message" class="tool-edit-message">{{ parsedResult.message }}</div>
    <!-- 行尾容错匹配说明 -->
    <div v-if="parsedResult?.note" class="tool-edit-note">{{ parsedResult.note }}</div>
  </div>

  <!-- 容器始终渲染（v-else）：折叠/展开只翻转内部 JSON 块的 v-if，让
       ElCollapseTransition 的 enter/leave 正常触发（若容器随内容一起挂载，
       内部过渡会被视为初始渲染而吞掉）；无可见内容时隐藏 border 避免空线 -->
  <div v-else :class="['tool-fallback-result', { 'tool-fallback-hidden': !hasVisibleFallback }]">
    <!-- 纯 JSON 转储：折叠态隐藏；高度过渡仅在 animateJson（用户点击过）时启用 -->
    <Transition v-bind="animateJson ? collapseHooks : {}">
      <div v-if="!hidePlainJson || isBareString" class="tool-fallback-json">
        <pre class="tool-fallback-pre">{{ fallbackText }}</pre>
        <el-button
          :icon="CopyDocument"
          link
          size="small"
          class="tool-fallback-copy"
          @click="handleFallbackCopy"
        >
          复制
        </el-button>
      </div>
    </Transition>
    <!-- 文件结果：在返回数据下方追加预览/下载 -->
    <div v-if="isSaveFile && mediaInfo" class="tool-media-result">
      <div class="media-inline-preview">
        <video
          v-if="mediaInfo.isVideo"
          :src="mediaInfo.preview_url || mediaInfo.download_url"
          controls
          preload="none"
          class="media-video"
        />
        <img
          v-if="mediaInfo.isImage"
          :src="mediaInfo.preview_url || mediaInfo.download_url"
          class="media-image"
          @click="openMediaPreview"
        />
      </div>
      <div class="media-actions">
        <el-button
          v-if="mediaInfo.isVideo || mediaInfo.isImage"
          :icon="View"
          size="small"
          @click="openMediaPreview"
        >
          查看预览
        </el-button>
        <!-- 下载按钮：用 download_url（已登录带 cookie，FileResponse 返回原名） -->
        <el-button
          v-if="mediaInfo.download_url"
          :icon="Download"
          size="small"
          tag="a"
          :href="mediaInfo.download_url"
          :download="mediaInfo.file_name"
        >
          {{ mediaInfo.file_name }}下载
        </el-button>
      </div>
    </div>
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

/* 工具块内联 diff：限制高度内部滚动（DiffViewer 自带边框与配色） */
.tool-diff-viewer {
  max-height: 300px;
  overflow-y: auto;
}

.tool-edit-message {
  padding: 8px 16px;
  font-size: 12px;
  color: #059669;
  background: rgba(236, 253, 245, 0.6);
}

.tool-edit-warning {
  padding: 8px 16px;
  font-size: 12px;
  color: #b45309;
  background: rgba(254, 243, 199, 0.6);
}

.tool-edit-note {
  padding: 8px 16px;
  font-size: 12px;
  color: #1d4ed8;
  background: rgba(219, 234, 254, 0.5);
}

.tool-fallback-result {
  position: relative;
}

/* 折叠的纯 JSON 回退容器为空，隐藏 border-top 避免头部下出现空线 */
.tool-fallback-result.tool-fallback-hidden {
  border-top: none;
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
  max-height: 150px;
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
  padding: 12px 16px;
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

.media-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
