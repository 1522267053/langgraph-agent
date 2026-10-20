import { get, post } from '@/api/index'
import type { ImportConflictPolicy, ImportConflictResult } from '@/types/flow'

export interface MarketplaceStatus {
  server_url: string
  connected: boolean
}

export const marketplaceApi = {
  getStatus() {
    return get<MarketplaceStatus>('/marketplace/status')
  },
  getConfig() {
    return get<MarketplaceStatus>('/marketplace/config')
  },
  saveConfig(server_url: string) {
    return post<{ connected: boolean }>('/marketplace/config', { server_url })
  },
  connect() {
    return post<{ connected: boolean }>('/marketplace/connect')
  },
  disconnect() {
    return post<unknown>('/marketplace/disconnect')
  },
  listResources(params: Record<string, unknown>) {
    return post('/marketplace/resources', params)
  },
  getResourceDetail(id: number) {
    return get(`/marketplace/resources/${id}`)
  },
  importResource(id: number, conflictPolicy?: ImportConflictPolicy) {
    return post('/marketplace/import/' + id, conflictPolicy ? { conflict_policy: conflictPolicy } : {})
  },
  previewImportConflicts(id: number) {
    return post<ImportConflictResult>('/marketplace/import/' + id + '/preview')
  },
  listCategories(resourceType: string = '') {
    return get('/marketplace/categories', { resource_type: resourceType })
  }
}
