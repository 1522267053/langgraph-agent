import request, { get, post } from './index'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '@/types/common'

export interface FileInfo {
  id: number
  source_type?: string
  original_name: string
  file_path: string
  file_type: string
  file_size: number
  mime_type: string
  download_url?: string
  preview_url?: string
}

export interface FileCondition {
  original_name?: string
  source_type?: string
  mime_type?: string
}

export const fileApi = {
  upload(
    file: File,
    sourceType?: string,
    onUploadProgress?: (event: { loaded: number; total: number }) => void
  ) {
    const formData = new FormData()
    formData.append('file', file)
    if (sourceType) formData.append('source_type', sourceType)
    return request.post<ApiResponse<FileInfo>>('/file/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress
    })
  },

  page(params: PaginationParams<FileCondition>) {
    return post<ApiResponse<PaginatedResponse<FileInfo>>>('/file/page', params)
  },

  download(fileId: number) {
    return `/api/file/download/${fileId}`
  },

  delete(fileId: number) {
    return get<void>(`/file/delete/${fileId}`)
  },

  batchDelete(ids: number[]) {
    return post<ApiResponse>('/file/deleteBatch', ids)
  }
}
