import type { BaseEntity } from './common'

export type KnowledgeBaseStatus = 0 | 1
export type DocumentType = 'txt' | 'md' | 'docx' | 'pdf' | 'xlsx'

export interface KnowledgeBase extends BaseEntity {
  name?: string
  description?: string
  status?: KnowledgeBaseStatus
}

export interface KnowledgeDocument extends BaseEntity {
  knowledge_base_id?: number
  title?: string
  content?: string
  file_type?: DocumentType
  file_path?: string
  word_count?: number
  segment_count?: number
  processing_status?: number
  error_message?: string
}

export interface KnowledgeDocumentSegment extends BaseEntity {
  document_id?: number
  segment_index?: number
  title?: string
  content?: string
  word_count?: number
}

export interface KnowledgeInsight extends BaseEntity {
  knowledge_base_id?: number
  question?: string
  answer?: string
  keywords?: string
  source_segment_ids?: number[]
}

export interface KnowledgeDocumentUploadResult {
  id: number
  title: string
  file_type: string
  processing_status: number
}

export interface KnowledgeBaseCreate {
  name: string
  description?: string
  status?: KnowledgeBaseStatus
}

export interface KnowledgeBaseUpdate extends KnowledgeBaseCreate {
  id: number
}

export interface KnowledgeDocumentCreate {
  knowledge_base_id: number
  title: string
  content?: string
  file_type?: DocumentType
}

export interface KnowledgeDocumentUpdate {
  id: number
  title?: string
}

export interface SegmentSearchResult {
  knowledge_base_id?: number
  document_id: number
  document_title: string
  file_type?: string
  title_id?: number
  title_text: string
  segment_id: number
  segment_index?: number
  content: string
  score?: number
  retrieval_method?: string
}

export interface KnowledgeReference {
  reference_id: string
  citation_marker: string
  knowledge_base_id: number
  document_id: number
  document_title: string
  file_type?: string
  title_id?: number
  title_text?: string
  segment_id: number
  segment_index?: number
  excerpt?: string
  score?: number
  retrieval_method?: string
}

export interface KnowledgeSegmentContextDocument {
  id: number
  knowledge_base_id: number
  title: string
  file_type: string
}

export interface KnowledgeSegmentContextItem {
  id: number
  document_id: number
  segment_index: number
  title?: string
  title_id?: number
  content: string
  word_count: number
}

export interface KnowledgeSegmentContextResult {
  document: KnowledgeSegmentContextDocument
  current: KnowledgeSegmentContextItem
  prev?: KnowledgeSegmentContextItem
  next?: KnowledgeSegmentContextItem
}

export interface KnowledgeExportTitle {
  title_index: number
  level: number
  title: string
  start_segment_index: number
  end_segment_index: number
}

export interface KnowledgeExportSegment {
  segment_index: number
  title?: string
  content: string
  word_count: number
  /** 所属标题的 title_index 锚点，-1 表示无标题 */
  title_index: number
}

export interface KnowledgeExportDocument {
  title: string
  file_type?: string
  word_count: number
  segment_count: number
  file_path?: string | null
  /** 以下为解析产物快照（新包字段，已完成解析的文档才有） */
  content?: string
  titles?: KnowledgeExportTitle[]
  segments?: KnowledgeExportSegment[]
}

export interface KnowledgeExportInsight {
  question: string
  answer: string
  keywords?: string
  /** 来源引用锚点：文档在导出数组中的下标 */
  source_doc_indexes?: number[]
  /** 来源引用锚点：文档内段落序号 */
  source_segment_indexes?: number[]
}

export interface KnowledgeExportItem {
  name: string
  description?: string
  status?: KnowledgeBaseStatus
  documents: KnowledgeExportDocument[]
  insights?: KnowledgeExportInsight[]
}

/** 知识库 .lga 导出包的 manifest 结构 */
export interface KnowledgeExportData {
  version: string
  export_time?: string
  knowledge_bases: KnowledgeExportItem[]
}

export interface KnowledgeImportResult {
  created: Array<{ id: number; name: string }>
  warnings: string[]
}
