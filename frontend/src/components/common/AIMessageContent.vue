<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { CopyDocument, RefreshLeft, SetUp, View } from '@element-plus/icons-vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import TodoList from '@/components/common/TodoList.vue'
import type { Segment } from '@/types/segment'
import { formatToolArgs, formatToolArgsExpanded, hasStringifiedJson } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    segments: Segment[]
    showThinking?: boolean
    showToolCalls?: boolean
    isStreaming?: boolean
  }>(),
  { showThinking: true, showToolCalls: true, isStreaming: false }
)

const emit = defineEmits<{
  (e: 'revert', dbMsgId: number): void
}>()

const lastContentIdx = computed(() => {
  for (let i = props.segments.length - 1; i >= 0; i--) {
    if (props.segments[i].type === 'content') return i
  }
  return -1
})

function isLastSegment(idx: number): boolean {
  return idx === props.segments.length - 1
}

function isThinkingInProgress(idx: number): boolean {
  if (!props.isStreaming) return false
  for (let i = idx + 1; i < props.segments.length; i++) {
    if (props.segments[i]?.type === 'content') return false
  }
  return true
}

const expandedArgsSegments = ref(new Set<number>())

function toggleArgsFormat(idx: number): void {
  if (expandedArgsSegments.value.has(idx)) {
    expandedArgsSegments.value.delete(idx)
  } else {
    expandedArgsSegments.value.add(idx)
  }
}

function isArgsExpanded(idx: number): boolean {
  return expandedArgsSegments.value.has(idx)
}

function parseMediaResult(
  result: unknown
): { preview_url: string; file_name: string; mime_type: string } | null {
  try {
    const str = typeof result === 'string' ? result : JSON.stringify(result)
    const parsed = JSON.parse(str)
    if (parsed.success && parsed.preview_url) {
      return {
        preview_url: parsed.preview_url,
        file_name: parsed.file_name || '',
        mime_type: parsed.mime_type || ''
      }
    }
  } catch {
    return null
  }
  return null
}

function isMediaTool(result: unknown): boolean {
  return parseMediaResult(result) !== null
}

function mediaPreviewInfo(segment: Segment) {
  return getMediaPreviewInfo(segment)
}

function isMediaSegment(segment: Segment): boolean {
  return (
    segment.type === 'tool' &&
    !!segment.tool &&
    segment.tool.status === 'success' &&
    isMediaTool(segment.tool.result)
  )
}

function openMediaPreview(segment: Segment): void {
  const media = parseMediaResult(segment.tool?.result)
  if (!media) return
  window.open(media.preview_url, '_blank')
}

function getMediaPreviewInfo(
  segment: Segment
): { preview_url: string; isVideo: boolean; isImage: boolean } | null {
  const media = parseMediaResult(segment.tool?.result)
  if (!media) return null
  return {
    preview_url: media.preview_url,
    isVideo: (media.mime_type || '').startsWith('video/'),
    isImage: (media.mime_type || '').startsWith('image/'),
  }
}

function formatToolResult(result: unknown): string {
  if (result === undefined || result === null) return ''
  try {
    if (typeof result === 'string') {
      try {
        const parsed = JSON.parse(result)
        return JSON.stringify(parsed, null, 2)
      } catch {
        return result
      }
    }
    return JSON.stringify(result, null, 2)
  } catch {
    return String(result)
  }
}

async function handleCopy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}
</script>

<template>
  <template v-for="(segment, idx) in segments" :key="idx">
    <div v-if="segment.type === 'thinking'" class="thinking-block">
      <div class="code-block-header">
        <div class="code-block-dots">
          <span class="dot-red"></span>
          <span class="dot-amber"></span>
          <span class="dot-green"></span>
        </div>
        <span class="code-block-label thinking-label">思考过程</span>
        <div class="code-block-actions">
          <span v-if="!showThinking && isThinkingInProgress(idx)" class="thinking-loading">
            思考中...
          </span>
          <el-tooltip
            v-if="!isStreaming && segment.dbMsgId && !isLastSegment(idx)"
            content="删除此条及之后的内容"
            placement="top"
          >
            <el-button
              :icon="RefreshLeft"
              link
              size="small"
              class="revert-btn"
              @click="emit('revert', segment.dbMsgId!)"
            />
          </el-tooltip>
        </div>
      </div>
      <pre v-if="showThinking" class="thinking-content">{{ segment.thinking }}</pre>
    </div>

    <div v-else-if="segment.type === 'tool' && segment.tool" class="tool-block">
      <div :class="['code-block-header', 'tool-header-' + segment.tool.status]">
        <el-icon class="tool-header-icon"><SetUp /></el-icon>
        <span class="tool-header-name">{{ segment.tool.name }}</span>
        <span :class="['tool-status-badge', segment.tool.status]">
          <span v-if="segment.tool.status === 'running'" class="status-spinner"></span>
          {{
            segment.tool.status === 'running'
              ? '执行中'
              : segment.tool.status === 'error'
                ? '失败'
                : '完成'
          }}
        </span>
      </div>
      <template v-if="showToolCalls">
        <div
          v-if="segment.tool.args && Object.keys(segment.tool.args).length > 0"
          class="tool-content-args-wrapper"
        >
          <pre class="tool-content tool-content-args">{{
            isArgsExpanded(idx)
              ? formatToolArgsExpanded(segment.tool.args)
              : formatToolArgs(segment.tool.args)
          }}</pre>
          <el-button
            v-if="hasStringifiedJson(segment.tool.args)"
            link
            size="small"
            class="args-toggle-btn"
            @click="toggleArgsFormat(idx)"
          >
            {{ isArgsExpanded(idx) ? '显示原始' : '显示格式化' }}
          </el-button>
        </div>
        <pre
          v-if="segment.tool.status !== 'error' && segment.tool.result !== undefined"
          class="tool-content tool-content-result"
          >{{ formatToolResult(segment.tool.result) }}</pre
        >
        <pre v-if="segment.tool.status === 'error'" class="tool-content tool-content-error">{{
          segment.tool.result !== undefined ? formatToolResult(segment.tool.result) : '执行失败'
        }}</pre>
        <div
          v-show="isMediaSegment(segment)"
          class="tool-media-preview"
        >
          <div v-show="mediaPreviewInfo(segment)?.isVideo" class="media-inline-preview">
            <video
              :src="mediaPreviewInfo(segment)?.preview_url || ''"
              controls
              preload="metadata"
              class="media-video"
            />
          </div>
          <div v-show="mediaPreviewInfo(segment)?.isImage" class="media-inline-preview">
            <img
              :src="mediaPreviewInfo(segment)?.preview_url || ''"
              class="media-image"
            />
          </div>
          <el-button :icon="View" size="small" @click="openMediaPreview(segment)">
            查看预览
          </el-button>
        </div>
      </template>
      <div v-else-if="segment.tool.status === 'running'" class="tool-content tool-loading-text">
        执行中...
      </div>
    </div>

    <div v-else-if="segment.type === 'content'" class="message-content">
      <div class="content-actions">
        <el-tooltip v-if="!isStreaming && segment.content" content="复制源文本" placement="top">
          <el-button
            :icon="CopyDocument"
            link
            size="small"
            class="copy-btn"
            @click="handleCopy(segment.content || '')"
          />
        </el-tooltip>
        <el-tooltip
          v-if="
            !isStreaming &&
            segment.dbMsgId &&
            idx !== lastContentIdx
          "
          content="删除此条及之后的内容"
          placement="top"
        >
          <el-button
            :icon="RefreshLeft"
            link
            size="small"
            class="content-revert-btn"
            @click="emit('revert', segment.dbMsgId!)"
          />
        </el-tooltip>
      </div>
      <MarkdownRenderer :content="segment.content || ''" />
    </div>

    <div v-else-if="segment.type === 'todo' && segment.todo" class="todo-block">
      <div class="todo-header">
        <span class="todo-badge">任务计划</span>
        <span class="todo-count">{{ segment.todo.length }} 项</span>
      </div>
      <TodoList :items="segment.todo" />
    </div>
  </template>
</template>

<style scoped>
.thinking-block,
.tool-block {
  border-radius: 12px;
  overflow: hidden;
  margin-bottom: 12px;
  border: 1px solid #e2e8f0;
  box-shadow:
    0 2px 15px -3px rgba(0, 0, 0, 0.07),
    0 4px 6px -2px rgba(0, 0, 0, 0.05);
}

.code-block-header {
  background: #0f172a;
  padding: 8px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.tool-header-success {
  background: #059669;
}

.tool-header-error {
  background: #dc2626;
}

.tool-header-running {
  background: #0f172a;
}

.tool-header-icon {
  font-size: 20px;
  color: #fff;
  opacity: 0.9;
}

.code-block-dots {
  display: flex;
  gap: 5px;
}

.code-block-dots .dot-red,
.code-block-dots .dot-amber,
.code-block-dots .dot-green {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.code-block-dots .dot-red {
  background: rgba(239, 68, 68, 0.8);
}

.code-block-dots .dot-amber {
  background: rgba(245, 158, 11, 0.8);
}

.code-block-dots .dot-green {
  background: rgba(16, 185, 129, 0.8);
}

.tool-header-name {
  font-size: 15px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 0.01em;
}

.thinking-label {
  font-size: 14px;
  font-weight: 600;
  color: #cbd5e1;
  letter-spacing: 0.01em;
  text-transform: none;
}

.code-block-label {
  font-size: 10px;
  font-family: 'Courier New', monospace;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.code-block-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
}

.thinking-loading {
  color: #fbbf24;
  font-size: 12px;
}

.tool-status-badge {
  margin-left: auto;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 4px;
}

.tool-status-badge.running {
  background: rgba(245, 158, 11, 0.15);
  color: #fbbf24;
}

.tool-status-badge.success {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
}

.tool-status-badge.error {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
}

.status-spinner {
  display: inline-block;
  width: 10px;
  height: 10px;
  border: 2px solid rgba(251, 191, 36, 0.3);
  border-top-color: #fbbf24;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.thinking-content {
  margin: 0;
  padding: 14px 16px;
  background: rgba(248, 250, 252, 0.8);
  font-family: 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  color: #334155;
  white-space: pre-wrap;
  word-break: break-word;
}

.tool-content {
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

.tool-content-args {
  border-top: 1px solid #e2e8f0;
}

.tool-content-args-wrapper {
  position: relative;
}

.args-toggle-btn {
  position: absolute;
  top: 6px;
  right: 8px;
  font-size: 11px;
  color: #64748b;
  z-index: 1;
}

.args-toggle-btn:hover {
  color: #409eff;
}

.tool-content-result {
  border-top: 1px solid #e2e8f0;
  background: rgba(236, 253, 245, 0.4);
}

.tool-content-error {
  border-top: 1px solid #fecaca;
  background: rgba(254, 242, 242, 0.6);
  color: #dc2626;
}

.tool-loading-text {
  padding: 12px 16px;
  color: #94a3b8;
  font-size: 13px;
}

.revert-btn {
  color: #64748b;
  font-size: 14px;
  transition: color 0.2s;
}

.revert-btn:hover {
  color: #f87171;
}

.message-content {
  word-break: break-word;
  line-height: 1.7;
  background: #fff;
  padding: 20px 22px;
  border-radius: 4px 16px 16px 16px;
  border: 1px solid #e2e8f0;
  box-shadow:
    0 2px 15px -3px rgba(0, 0, 0, 0.07),
    0 4px 6px -2px rgba(0, 0, 0, 0.05);
  margin-bottom: 10px;
  position: relative;
  font-size: 15px;
}

.content-actions {
  position: absolute;
  top: 2px;
  right: 14px;
  display: flex;
  align-items: center;
  gap: 2px;
}

.copy-btn {
  color: #c0c4cc;
  font-size: 14px;
  transition: color 0.2s;
}

.copy-btn:hover {
  color: #409eff;
}

.content-revert-btn {
  color: #c0c4cc;
  font-size: 14px;
  transition: color 0.2s;
}

.content-revert-btn:hover {
  color: #f56c6c;
}

.todo-block {
  background: #f9fafb;
  padding: 20px;
  margin-bottom: 12px;
  border-radius: 16px;
  border: 1px solid rgba(37, 99, 235, 0.08);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.todo-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.todo-badge {
  padding: 2px 8px;
  background: #2563eb;
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.todo-count {
  font-size: 14px;
  font-weight: 700;
  color: #1e293b;
}

.tool-media-preview {
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
