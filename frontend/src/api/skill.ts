import request, { get } from './index'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '@/types/common'
import type { Skill, SkillUpdate, SkillQuery, SkillBatchResult } from '@/types/skill'

export const skillApi = {
  page(params: PaginationParams<SkillQuery>) {
    return request.post<ApiResponse<PaginatedResponse<Skill>>>('/skill/page', params)
  },

  list() {
    return request.get<ApiResponse<Skill[]>>('/skill/list')
  },

  get(id: number) {
    return request.get<ApiResponse<Skill>>(`/skill/get/${id}`)
  },

  update(data: SkillUpdate) {
    return request.post<ApiResponse<void>>('/skill/update', data)
  },

  delete(id: number) {
    return get<void>(`/skill/delete/${id}`)
  },

  deleteBatch(ids: number[]) {
    return request.post<ApiResponse<void>>('/skill/deleteBatch', ids)
  },

  search(keyword: string, limit: number = 10) {
    return request.get<ApiResponse<Skill[]>>('/skill/search', { params: { keyword, limit } })
  },

  getByCategory(category: string) {
    return request.get<ApiResponse<Skill[]>>(`/skill/category/${category}`)
  },

  upload(formData: FormData) {
    return request.post<ApiResponse<Skill>>('/skill/upload', formData)
  },

  getContent(id: number) {
    return request.get<ApiResponse<string>>(`/skill/${id}/content`)
  },

  reload(id: number) {
    return request.post<ApiResponse<Skill>>(`/skill/${id}/reload`)
  },

  reloadBatch(ids: number[]) {
    return request.post<ApiResponse<SkillBatchResult>>('/skill/reloadBatch', ids)
  }
}
