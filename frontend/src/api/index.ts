/**
 * HTTP请求封装
 * @description 基于axios封装的HTTP请求客户端，统一处理响应和错误
 */
import axios, { type AxiosInstance } from 'axios'
import type { ApiResponse } from '@/types/common'
import { ElMessage } from 'element-plus'

/** 请求配置 */
interface RequestOptions {
  /** 是否显示错误提示 */
  showError?: boolean
  /** 超时时间（毫秒） */
  timeout?: number
}

/** 创建axios实例 */
const request: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 30000
})

/** 响应拦截器 */
request.interceptors.response.use(
  response => {
    const res = response.data as ApiResponse
    if (res.code !== 1) {
      if ((response.config as Record<string, unknown>).showError !== false) {
        ElMessage.error(res.msg || '请求失败')
      }
      return Promise.reject(new Error(res.msg || '请求失败'))
    }
    return response
  },
  error => {
    if (error.response?.status === 401) {
      // 未认证，跳转登录页
      const currentPath = window.location.hash.slice(1) || '/'
      if (!currentPath.startsWith('/login')) {
        window.location.hash = '#/login?redirect=' + encodeURIComponent(currentPath)
      }
      return Promise.reject(error)
    }
    if ((error.config as Record<string, unknown>)?.showError !== false) {
      ElMessage.error(error.message || '网络错误')
    }
    return Promise.reject(error)
  }
)

/**
 * GET请求
 */
export function get<T>(
  url: string,
  params?: Record<string, unknown>,
  options?: RequestOptions
): Promise<{ data: ApiResponse<T> }> {
  return request.get(url, { params, ...options })
}

/**
 * POST请求
 */
export function post<T>(
  url: string,
  data?: unknown,
  options?: RequestOptions
): Promise<{ data: ApiResponse<T> }> {
  return request.post(url, data, options)
}

/**
 * PUT请求
 */
export function put<T>(
  url: string,
  data?: unknown,
  options?: RequestOptions
): Promise<{ data: ApiResponse<T> }> {
  return request.put(url, data, options)
}

/**
 * DELETE请求
 */
export function del<T>(url: string, options?: RequestOptions): Promise<{ data: ApiResponse<T> }> {
  return request.delete(url, options)
}

export default request
