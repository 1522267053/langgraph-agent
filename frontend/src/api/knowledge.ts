import request, { get } from './index'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '@/types/common'
import type {
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeDocumentSegment,
  KnowledgeBaseCreate,
  KnowledgeBaseUpdate,
  KnowledgeDocumentCreate,
  KnowledgeDocumentUpdate,
  KnowledgeDocumentUploadResult,
  SegmentSearchResult
} from '@/types/knowledge'

export const knowledgeBaseApi = {
  page(params: PaginationParams<KnowledgeBase>) {
    return request.post<ApiResponse<PaginatedResponse<KnowledgeBase>>>(
      '/knowledge/base/page',
      params
    )
  },

  get(id: number) {
    return request.get<ApiResponse<KnowledgeBase>>(`/knowledge/base/get/${id}`)
  },

  create(data: KnowledgeBaseCreate) {
    return request.post<ApiResponse<KnowledgeBase>>('/knowledge/base/create', data)
  },

  update(data: KnowledgeBaseUpdate) {
    return request.post<ApiResponse<void>>('/knowledge/base/update', data)
  },

  delete(id: number) {
    return get<void>(`/knowledge/base/delete/${id}`)
  },

  deleteBatch(ids: number[]) {
    return request.post<ApiResponse<void>>('/knowledge/base/deleteBatch', ids)
  }
}

export const knowledgeDocumentApi = {
  page(params: PaginationParams<KnowledgeDocument>) {
    return request.post<ApiResponse<PaginatedResponse<KnowledgeDocument>>>(
      '/knowledge/document/page',
      params
    )
  },

  get(id: number) {
    return request.get<ApiResponse<KnowledgeDocument>>(`/knowledge/document/get/${id}`)
  },

  create(data: KnowledgeDocumentCreate) {
    return request.post<ApiResponse<KnowledgeDocument>>('/knowledge/document/create', data)
  },

  update(data: KnowledgeDocumentUpdate) {
    return request.post<ApiResponse<void>>('/knowledge/document/update', data)
  },

  delete(id: number) {
    return get<void>(`/knowledge/document/delete/${id}`)
  },

  deleteBatch(ids: number[]) {
    return request.post<ApiResponse<void>>('/knowledge/document/deleteBatch', ids)
  },

  upload(file: File, knowledgeBaseId: number) {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('knowledge_base_id', String(knowledgeBaseId))
    return request.post<ApiResponse<KnowledgeDocumentUploadResult>>(
      '/knowledge/document/upload',
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      }
    )
  },

  getSegments(documentId: number) {
    return request.get<ApiResponse<KnowledgeDocumentSegment[]>>(
      `/knowledge/document/segments/${documentId}`
    )
  },

  getDownloadUrl(documentId: number) {
    return `/api/knowledge/document/download/${documentId}`
  },

  getContent(documentId: number) {
    return request.get<
      ApiResponse<{
        id: number
        title: string
        content: string
        word_count: number
        file_type: string
      }>
    >(`/knowledge/document/content/${documentId}`)
  },

  vectorizeDocument(documentId: number, force: boolean = false) {
    return request.post<
      ApiResponse<{
        document_id: number
        vectorized_segments: number
        total_segments: number
      }>
    >(`/knowledge/document/vectorize/document/${documentId}?force=${force}`)
  },

  reprocess(documentId: number) {
    return request.post<ApiResponse<void>>(`/knowledge/document/reprocess/${documentId}`)
  },

  searchSegments(data: { knowledge_base_id: number; query: string; top_k?: number }) {
    return request.post<ApiResponse<SegmentSearchResult[]>>(
      '/knowledge/document/search-segments',
      data
    )
  }
}
