<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { isAxiosError } from 'axios'
import { Document, Download } from '@element-plus/icons-vue'
import { knowledgeDocumentApi } from '@/api/knowledge'
import { useIsMobile } from '@/composables/useIsMobile'
import type { KnowledgeReference } from '@/types/knowledge'
import { downloadKnowledgeDocument } from '@/utils/knowledgeDownload'

interface ContextSegment {
  id: number
  segmentIndex?: number
  title?: string
  content: string
  isCurrent: boolean
}

type ViewMode = 'context' | 'document'
type LoadError = 'missing' | 'generic' | null

const props = defineProps<{
  visible: boolean
  reference: KnowledgeReference | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
}>()

const { isMobile } = useIsMobile()
const drawerSize = computed(() => (isMobile.value ? '100%' : '560px'))
const drawerVisible = computed({
  get: () => props.visible,
  set: value => emit('update:visible', value)
})

const activeView = ref<ViewMode>('context')
const contextSegments = ref<ContextSegment[]>([])
const contextLoading = ref(false)
const contextError = ref<LoadError>(null)
const documentContent = ref('')
const documentLoading = ref(false)
const documentLoaded = ref(false)
const documentError = ref<LoadError>(null)
const downloading = ref(false)
let contextRequestVersion = 0
let documentRequestVersion = 0

const currentContextIndex = computed(() =>
  contextSegments.value.findIndex(segment => segment.isCurrent)
)

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function toContextSegment(
  value: unknown,
  currentSegmentId: number,
  forceCurrent = false
): ContextSegment | null {
  if (!isRecord(value)) return null
  const rawId = value.id
  if (typeof rawId !== 'number' || typeof value.content !== 'string') return null

  const rawIndex = value.segment_index
  const rawTitle = value.title
  return {
    id: rawId,
    segmentIndex: typeof rawIndex === 'number' ? rawIndex : undefined,
    title: typeof rawTitle === 'string' ? rawTitle : undefined,
    content: value.content,
    isCurrent: forceCurrent || rawId === currentSegmentId
  }
}

function normalizeContext(payload: unknown, currentSegmentId: number): ContextSegment[] {
  if (!isRecord(payload)) return []
  const candidates = [
    { value: payload.prev },
    { value: payload.current, forceCurrent: true },
    { value: payload.next }
  ]

  return candidates
    .map(item => toContextSegment(item.value, currentSegmentId, item.forceCurrent))
    .filter((item): item is ContextSegment => item !== null)
}

function classifyError(error: unknown): Exclude<LoadError, null> {
  if (isAxiosError(error) && error.response?.status === 404) return 'missing'
  const message = error instanceof Error ? error.message : ''
  return /404|不存在|已删除/.test(message) ? 'missing' : 'generic'
}

async function loadContext(): Promise<void> {
  const reference = props.reference
  if (!reference) return

  const requestVersion = ++contextRequestVersion
  contextLoading.value = true
  contextError.value = null
  contextSegments.value = []

  try {
    const response = await knowledgeDocumentApi.getSegmentContext(reference.segment_id)
    if (requestVersion !== contextRequestVersion) return
    const segments = normalizeContext(response.data.data, reference.segment_id)
    if (!segments.some(segment => segment.isCurrent)) {
      contextError.value = 'missing'
      return
    }
    contextSegments.value = segments
  } catch (error) {
    if (requestVersion === contextRequestVersion) contextError.value = classifyError(error)
  } finally {
    if (requestVersion === contextRequestVersion) contextLoading.value = false
  }
}

async function loadDocumentContent(): Promise<void> {
  const reference = props.reference
  if (!reference || documentLoaded.value || documentLoading.value) return

  const requestVersion = ++documentRequestVersion
  documentLoading.value = true
  documentError.value = null

  try {
    const response = await knowledgeDocumentApi.getContent(reference.document_id)
    if (requestVersion !== documentRequestVersion) return
    if (!response.data.data) {
      documentError.value = 'missing'
      return
    }
    documentContent.value = response.data.data.content || ''
    documentLoaded.value = true
  } catch (error) {
    if (requestVersion === documentRequestVersion) documentError.value = classifyError(error)
  } finally {
    if (requestVersion === documentRequestVersion) documentLoading.value = false
  }
}

function resetReferenceState(): void {
  contextRequestVersion++
  documentRequestVersion++
  activeView.value = 'context'
  contextSegments.value = []
  contextLoading.value = false
  contextError.value = null
  documentContent.value = ''
  documentLoading.value = false
  documentLoaded.value = false
  documentError.value = null
}

async function downloadOriginal(): Promise<void> {
  const reference = props.reference
  if (!reference || downloading.value) return

  downloading.value = true
  try {
    await downloadKnowledgeDocument(reference.document_id, reference.document_title)
  } finally {
    downloading.value = false
  }
}

function segmentPosition(segment: ContextSegment, index: number): string {
  if (segment.isCurrent) return '当前引用'
  return index < currentContextIndex.value ? '上一个分片' : '下一个分片'
}

function segmentNumber(segment: ContextSegment): string {
  return segment.segmentIndex === undefined
    ? `分片 ${segment.id}`
    : `第 ${segment.segmentIndex + 1} 段`
}

function segmentTitle(segment: ContextSegment): string | undefined {
  return segment.title || (segment.isCurrent ? props.reference?.title_text : undefined)
}

function formatScore(score: number): string {
  return `${(score * 100).toFixed(1)}%`
}

watch(
  [() => props.visible, () => props.reference],
  ([visible, reference]) => {
    if (!visible || !reference) {
      contextRequestVersion++
      documentRequestVersion++
      return
    }
    resetReferenceState()
    void loadContext()
  },
  { immediate: true }
)

watch(activeView, view => {
  if (view === 'document' && props.visible) void loadDocumentContent()
})
</script>

<template>
  <el-drawer
    v-model="drawerVisible"
    class="knowledge-reference-drawer"
    direction="rtl"
    :size="drawerSize"
    :close-on-click-modal="true"
  >
    <template #header>
      <div class="drawer-title">
        <span>引用来源</span>
        <span v-if="reference" class="drawer-document-title" :title="reference.document_title">
          {{ reference.document_title }}
        </span>
      </div>
    </template>

    <div v-if="reference" class="drawer-content">
      <div class="reference-summary">
        <div class="reference-file-icon">
          <el-icon><Document /></el-icon>
        </div>
        <div class="reference-info">
          <div class="reference-title-row">
            <span class="reference-title">{{ reference.document_title }}</span>
            <el-tag v-if="reference.file_type" size="small" effect="plain">
              {{ reference.file_type.toUpperCase() }}
            </el-tag>
          </div>
          <div class="reference-meta">
            <span v-if="reference.title_text">{{ reference.title_text }}</span>
            <span v-if="reference.segment_index !== undefined">
              第 {{ reference.segment_index + 1 }} 段
            </span>
            <span v-if="typeof reference.score === 'number'">
              相似度 {{ formatScore(reference.score) }}
            </span>
            <span v-if="reference.retrieval_method">{{ reference.retrieval_method }}</span>
          </div>
        </div>
        <el-button :icon="Download" :loading="downloading" size="small" @click="downloadOriginal">
          下载原文件
        </el-button>
      </div>

      <div class="view-switch" role="tablist" aria-label="引用内容视图">
        <button
          type="button"
          :class="['view-switch-button', { active: activeView === 'context' }]"
          role="tab"
          :aria-selected="activeView === 'context'"
          @click="activeView = 'context'"
        >
          引用上下文
        </button>
        <button
          type="button"
          :class="['view-switch-button', { active: activeView === 'document' }]"
          role="tab"
          :aria-selected="activeView === 'document'"
          @click="activeView = 'document'"
        >
          解析全文
        </button>
      </div>

      <div v-if="activeView === 'context'" class="view-content">
        <div v-if="contextLoading" class="loading-state">
          <el-skeleton :rows="7" animated />
        </div>
        <el-result
          v-else-if="contextError"
          :icon="contextError === 'missing' ? 'warning' : 'error'"
          :title="contextError === 'missing' ? '引用内容不可用' : '上下文加载失败'"
          :sub-title="
            contextError === 'missing' ? '该分片或所属文档可能已被删除' : '请检查网络后重试'
          "
        >
          <template v-if="contextError === 'generic'" #extra>
            <el-button type="primary" @click="loadContext">重新加载</el-button>
          </template>
        </el-result>
        <div v-else class="context-list">
          <article
            v-for="(segment, index) in contextSegments"
            :key="segment.id"
            :class="['context-segment', { current: segment.isCurrent }]"
          >
            <header class="context-segment-header">
              <el-tag :type="segment.isCurrent ? 'primary' : 'info'" size="small" effect="light">
                {{ segmentPosition(segment, index) }}
              </el-tag>
              <span class="context-segment-number">{{ segmentNumber(segment) }}</span>
              <span v-if="segmentTitle(segment)" class="context-segment-title">
                {{ segmentTitle(segment) }}
              </span>
            </header>
            <pre class="context-segment-content">{{ segment.content }}</pre>
          </article>
        </div>
      </div>

      <div v-else class="view-content document-view">
        <div v-if="documentLoading" class="loading-state">
          <el-skeleton :rows="10" animated />
        </div>
        <el-result
          v-else-if="documentError"
          :icon="documentError === 'missing' ? 'warning' : 'error'"
          :title="documentError === 'missing' ? '文档不可用' : '全文加载失败'"
          :sub-title="documentError === 'missing' ? '文档可能已被删除' : '请检查网络后重试'"
        >
          <template v-if="documentError === 'generic'" #extra>
            <el-button type="primary" @click="loadDocumentContent">重新加载</el-button>
          </template>
        </el-result>
        <el-empty
          v-else-if="documentLoaded && !documentContent"
          description="该文档暂无解析内容"
          :image-size="80"
        />
        <pre v-else-if="documentLoaded" class="document-content">{{ documentContent }}</pre>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.drawer-title {
  display: flex;
  align-items: baseline;
  min-width: 0;
  gap: 10px;
  color: #1f2937;
  font-size: 16px;
  font-weight: 600;
}

.drawer-document-title {
  overflow: hidden;
  color: #94a3b8;
  font-size: 12px;
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.drawer-content {
  min-height: 100%;
}

.reference-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #f8fafc;
}

.reference-file-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 36px;
  height: 36px;
  border-radius: 9px;
  background: #dbeafe;
  color: #2563eb;
  font-size: 18px;
}

.reference-info {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.reference-title-row {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 7px;
}

.reference-title {
  overflow: hidden;
  color: #1e293b;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reference-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 3px 10px;
  color: #64748b;
  font-size: 11px;
}

.view-switch {
  display: inline-flex;
  margin: 18px 0 14px;
  padding: 3px;
  border-radius: 8px;
  background: #f1f5f9;
}

.view-switch-button {
  padding: 6px 14px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #64748b;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.view-switch-button.active {
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.12);
  color: #2563eb;
  font-weight: 600;
}

.view-content {
  min-height: 280px;
}

.loading-state {
  padding: 12px 4px;
}

.context-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.context-segment {
  padding: 12px 14px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
}

.context-segment.current {
  border-color: #93c5fd;
  background: #eff6ff;
  box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.08);
}

.context-segment-header {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 8px;
  margin-bottom: 9px;
}

.context-segment-number {
  flex: 0 0 auto;
  color: #64748b;
  font-size: 12px;
}

.context-segment-title {
  overflow: hidden;
  color: #334155;
  font-size: 12px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.context-segment-content,
.document-content {
  margin: 0;
  color: #334155;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}

.document-content {
  padding: 16px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #f8fafc;
}

@media (max-width: 767px) {
  .reference-summary {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .reference-summary > .el-button {
    margin-left: 46px;
  }

  .view-switch {
    display: flex;
  }

  .view-switch-button {
    flex: 1;
  }

  .context-segment-header {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .context-segment-title {
    flex-basis: 100%;
  }
}
</style>
