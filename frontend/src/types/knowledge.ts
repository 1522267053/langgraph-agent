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

export interface KnowledgeDocumentUpdate extends KnowledgeDocumentCreate {
  id: number
}

export interface SegmentSearchResult {
  document_id: number
  document_title: string
  title_id?: number
  title_text: string
  segment_id: number
  content: string
  score: number
}
