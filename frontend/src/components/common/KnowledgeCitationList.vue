<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowRight, Document, Download } from '@element-plus/icons-vue'
import type { KnowledgeReference } from '@/types/knowledge'
import { downloadKnowledgeDocument } from '@/utils/knowledgeDownload'

interface NumberedCitation {
  number: number
  reference: KnowledgeReference
}

interface CitationGroup {
  documentId: number
  documentTitle: string
  fileType?: string
  citations: NumberedCitation[]
}

const props = defineProps<{
  citations: KnowledgeReference[]
}>()

const emit = defineEmits<{
  (e: 'select', reference: KnowledgeReference): void
}>()

const expandedDocumentIds = ref(new Set<number>())

const citationGroups = computed<CitationGroup[]>(() => {
  const groups = new Map<number, CitationGroup>()

  props.citations.forEach((reference, index) => {
    let group = groups.get(reference.document_id)
    if (!group) {
      group = {
        documentId: reference.document_id,
        documentTitle: reference.document_title || '未命名文档',
        fileType: reference.file_type,
        citations: []
      }
      groups.set(reference.document_id, group)
    }
    group.citations.push({ number: index + 1, reference })
  })

  return [...groups.values()]
})

function isDocumentExpanded(documentId: number): boolean {
  return expandedDocumentIds.value.has(documentId)
}

function toggleDocument(documentId: number): void {
  const next = new Set(expandedDocumentIds.value)
  if (next.has(documentId)) {
    next.delete(documentId)
  } else {
    next.add(documentId)
  }
  expandedDocumentIds.value = next
}

function segmentLabel(reference: KnowledgeReference): string {
  return reference.segment_index === undefined
    ? `分片 ${reference.segment_id}`
    : `第 ${reference.segment_index + 1} 段`
}

function formatScore(score: number): string {
  return `${(score * 100).toFixed(1)}%`
}

function handleDownload(group: CitationGroup): void {
  void downloadKnowledgeDocument(group.documentId, group.documentTitle)
}
</script>

<template>
  <section v-if="citationGroups.length" class="knowledge-citations" aria-label="参考来源">
    <div class="citation-list-heading">
      <span>参考来源</span>
    </div>

    <article v-for="group in citationGroups" :key="group.documentId" class="citation-group">
      <header
        :class="['citation-document-header', { expanded: isDocumentExpanded(group.documentId) }]"
      >
        <button
          type="button"
          class="citation-document-info"
          :aria-expanded="isDocumentExpanded(group.documentId)"
          @click="toggleDocument(group.documentId)"
        >
          <span class="file-icon">
            <el-icon><Document /></el-icon>
          </span>
          <span class="document-title-wrap">
            <span class="document-title" :title="group.documentTitle">
              {{ group.documentTitle }}
            </span>
            <span v-if="isDocumentExpanded(group.documentId)" class="document-meta">
              <span v-if="group.fileType" class="file-type">
                {{ group.fileType.toUpperCase() }}
              </span>
              {{ group.citations.length }} 个引用分片
            </span>
          </span>
          <el-icon :class="['expand-icon', { expanded: isDocumentExpanded(group.documentId) }]">
            <ArrowRight />
          </el-icon>
        </button>
        <div class="document-actions">
          <el-button link size="small" :icon="Download" @click="handleDownload(group)">
            下载
          </el-button>
        </div>
      </header>

      <div v-if="isDocumentExpanded(group.documentId)" class="citation-fragments">
        <button
          v-for="item in group.citations"
          :key="item.reference.reference_id"
          type="button"
          class="citation-fragment"
          @click="emit('select', item.reference)"
        >
          <span class="citation-number">[{{ item.number }}]</span>
          <span class="fragment-content">
            <span class="fragment-meta-row">
              <span v-if="item.reference.title_text" class="fragment-title">
                {{ item.reference.title_text }}
              </span>
              <span class="segment-label">{{ segmentLabel(item.reference) }}</span>
              <span v-if="typeof item.reference.score === 'number'" class="score-label">
                相似度 {{ formatScore(item.reference.score) }}
              </span>
            </span>
            <span v-if="item.reference.excerpt" class="fragment-excerpt">
              {{ item.reference.excerpt }}
            </span>
          </span>
        </button>
      </div>
    </article>
  </section>
</template>

<style scoped>
.knowledge-citations {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid #edf1f5;
}

.citation-list-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  color: #475569;
  font-size: 13px;
  font-weight: 600;
}

.citation-group {
  overflow: hidden;
  margin-top: 8px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
}

.citation-document-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  background: #f8fafc;
}

.citation-document-header.expanded {
  border-bottom: 1px solid #eef2f7;
}

.citation-document-info {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
  gap: 9px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.file-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: #e0ecff;
  color: #2563eb;
  font-size: 16px;
}

.document-title {
  overflow: hidden;
  min-width: 0;
  color: #1e293b;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color 0.2s ease;
}

.citation-document-info:hover .document-title {
  color: #2563eb;
}

.document-title-wrap {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.document-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #94a3b8;
  font-size: 11px;
}

.file-type {
  color: #64748b;
  font-weight: 600;
}

.expand-icon {
  flex: 0 0 auto;
  color: #64748b;
  transition: transform 0.2s ease;
}

.expand-icon.expanded {
  transform: rotate(90deg);
}

.document-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
}

.citation-fragments {
  padding: 4px 10px;
}

.citation-fragment {
  display: flex;
  align-items: flex-start;
  width: 100%;
  gap: 8px;
  padding: 9px 4px;
  border: 0;
  border-bottom: 1px dashed #e5eaf0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.citation-fragment:last-child {
  border-bottom: 0;
}

.citation-fragment:hover .fragment-title,
.citation-fragment:hover .segment-label {
  color: #2563eb;
}

.citation-number {
  flex: 0 0 auto;
  color: #2563eb;
  font-size: 12px;
  font-weight: 700;
  line-height: 20px;
}

.fragment-content {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.fragment-meta-row {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 7px;
  color: #64748b;
  font-size: 12px;
  line-height: 20px;
}

.fragment-title {
  overflow: hidden;
  color: #334155;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color 0.2s;
}

.segment-label {
  flex: 0 0 auto;
  transition: color 0.2s;
}

.score-label {
  flex: 0 0 auto;
  margin-left: auto;
  color: #0f766e;
  font-size: 11px;
}

.fragment-excerpt {
  display: -webkit-box;
  overflow: hidden;
  color: #64748b;
  font-size: 12px;
  line-height: 1.55;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

@media (max-width: 767px) {
  .citation-document-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .document-actions {
    align-self: flex-end;
  }

  .citation-document-info {
    width: 100%;
  }

  .fragment-meta-row {
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 2px 7px;
  }

  .fragment-title {
    flex-basis: 100%;
  }

  .score-label {
    margin-left: 0;
  }
}
</style>
