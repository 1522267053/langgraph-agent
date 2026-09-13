/**
 * 调试 API 封装
 * @description 提供节点/工具脚本的独立试运行能力，与流程中真实执行路径共享沙箱核心逻辑
 */
import { post } from './index'
import type { ApiResponse } from '@/types/common'

/** Python 试运行请求体 */
export interface PythonDebugRequest {
  code: string
  timeout?: number
  input_data?: Record<string, unknown>
  debug_session_id: string
}

/** Python 试运行响应 */
export interface PythonDebugResult {
  stdout: string
  stderr: string
  result: unknown
  success: boolean
}

/**
 * 生成并获取调试会话 ID
 * @description 同一浏览器会话复用同一 ID，调试产物（图片等）会按 session_id 归类到
 * workspace/temp/debug_uploads/<session_id>/ 目录，7 天后由 scheduler 清理。
 * 注意：与业务流程的"会话"无关，仅用于前端试运行面板的临时文件归类。
 */
function getOrCreateDebugSessionId(): string {
  const STORAGE_KEY = 'debugSessionId'
  try {
    let sid = localStorage.getItem(STORAGE_KEY)
    if (!sid) {
      // 简易 UUID v4 生成（不依赖 crypto.randomUUID 以兼容老浏览器）
      sid = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = (Math.random() * 16) | 0
        const v = c === 'x' ? r : (r & 0x3) | 0x8
        return v.toString(16)
      })
      localStorage.setItem(STORAGE_KEY, sid)
    }
    return sid
  } catch {
    // localStorage 不可用（如 SSR 或隐私模式）时使用本次会话临时 ID
    return `tmp-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  }
}

export const debugApi = {
  /**
   * Python 脚本试运行
   * - 复用 RestrictedPython 沙箱核心（与流程中执行 100% 一致）
   * - __save_file__ 产物落盘到 workspace/temp/debug_uploads/<session_id>/，不写 File DB
   * - 不调 record_tool_file_change，避免污染文件追踪与会话回退
   */
  runPython(req: Omit<PythonDebugRequest, 'debug_session_id'>): Promise<ApiResponse<PythonDebugResult>> {
    return post<ApiResponse<PythonDebugResult>>('/debug/python', {
      ...req,
      debug_session_id: getOrCreateDebugSessionId()
    })
  }
}