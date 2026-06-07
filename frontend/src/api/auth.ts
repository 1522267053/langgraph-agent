import { get, post } from '@/api/index'
import type { ApiResponse } from '@/types/common'

export interface AuthCheckData {
  need_login: boolean
  authenticated: boolean
  has_username: boolean
  username?: string | null
}

export const authApi = {
  check(): Promise<{ data: ApiResponse<AuthCheckData> }> {
    return get<AuthCheckData>('/auth/check')
  },
  async login(
    username: string,
    password: string,
    options?: { showError?: boolean }
  ): Promise<{ data: ApiResponse<null> }> {
    return post<null>('/auth/login', { username, password }, options)
  },
  logout(): Promise<{ data: ApiResponse<null> }> {
    return post<null>('/auth/logout')
  }
}
