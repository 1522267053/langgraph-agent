/**
 * 工具折叠行统计（工具行内嵌展示）
 * @description 从单个工具段的 args/result 中提取统计与文件路径：
 * - file_read → 读取行数（total_lines 优先，媒体注入显示「查看」）
 * - file_write → 写入行数（行数取 args.content，结果为字符串成功消息）
 * - text_editor → +A −R（从 result.diff 的 -/+ 行数统计）
 * 展示位置：工具行右侧统计 pill、工具名下方文件路径（AIMessageContent 工具行内嵌）。
 */

import type { ToolCall } from '@/types/segment'

export type FileOpKind = 'read' | 'write' | 'edit'

export interface FileOpStat {
  /** 文件绝对路径（展示层自行缩短） */
  path: string
  kind: FileOpKind
  /** read：读取行数；媒体注入时为 undefined */
  readLines?: number
  /** read 读取范围（1-based）：offset/limit 存在时携带 */
  readStart?: number
  readEnd?: number
  /** read 媒体注入（图片/PDF 等，无行数概念），值为 media_type（image/audio/pdf 等） */
  isMedia?: string
  /** write：写入行数 */
  writeLines?: number
  /** edit：diff 新增/删除行数 */
  added?: number
  removed?: number
}

function parseToolResult(result: unknown): Record<string, unknown> | null {
  if (result === null || result === undefined) return null
  if (typeof result === 'object') return result as Record<string, unknown>
  if (typeof result === 'string') {
    try {
      const parsed = JSON.parse(result)
      return typeof parsed === 'object' && parsed !== null
        ? (parsed as Record<string, unknown>)
        : null
    } catch {
      return null
    }
  }
  return null
}

function countLines(text: string): number {
  if (!text) return 0
  return text.split('\n').length
}

/** 从单个工具调用提取统计；非 file_ 类工具/失败/无有效数据返回 null */
export function statFromTool(tool: ToolCall): FileOpStat | null {
  const args = (tool.args || {}) as Record<string, unknown>

  if (tool.name === 'file_read') {
    const r = parseToolResult(tool.result)
    if (!r || r.success !== true || typeof r.file_path !== 'string') return null
    // 媒体注入（图片/PDF/音频）：无行数概念，pill 显示媒体类型
    if (typeof r.media_type === 'string') {
      return { path: r.file_path, kind: 'read', isMedia: r.media_type }
    }
    // docx/xlsx 文档转换分支：全文转 markdown 返回（content_type="document"），
    // content 是转换后的全文而非文件原始行——行数有误导性，pill 显示「文档」
    if (
      r.content_type === 'document' ||
      (typeof r.ext === 'string' && (r.ext === 'docx' || r.ext === 'xlsx'))
    ) {
      return {
        path: r.file_path,
        kind: 'read',
        isMedia: r.ext === 'xlsx' ? 'xlsx' : 'docx'
      }
    }
    // 读取范围（非总行数）：offset=1-based 起始行，limit=实际返回行数；
    // offset/limit 缺失（旧数据）回退 content 行数
    let readLines: number
    if (typeof r.offset === 'number' && typeof r.limit === 'number') {
      readLines = r.limit // pill 只显示数量；范围上下文由下方折叠摘要承载
    } else if (typeof r.content === 'string') {
      readLines = countLines(r.content)
    } else {
      return null
    }
    return {
      path: r.file_path,
      kind: 'read',
      readLines,
      readStart: typeof r.offset === 'number' ? r.offset : undefined,
      readEnd:
        typeof r.offset === 'number' && typeof r.limit === 'number'
          ? r.offset + r.limit - 1
          : undefined
    }
  }

  if (tool.name === 'file_write') {
    // 结果是字符串消息（"文件新建/覆盖成功: ...（行尾 XX）"），失败时为错误文案
    const path = typeof args.file_path === 'string' ? args.file_path : ''
    const ok = typeof tool.result === 'string' && tool.result.includes('成功')
    const content = typeof args.content === 'string' ? args.content : ''
    if (!path || !ok || !content) return null
    return { path, kind: 'write', writeLines: countLines(content) }
  }

  if (tool.name === 'text_editor') {
    const r = parseToolResult(tool.result)
    if (!r || r.success !== true || r.dry_run === true) return null
    const path =
      typeof r.file_path === 'string' ? r.file_path : String(args.file_path ?? '')
    const diff = typeof r.diff === 'string' ? r.diff : ''
    if (!path || !diff) return null
    // diff 为 -旧行/+新行 格式（_diff_preview 生成），行前缀即增删统计
    let added = 0
    let removed = 0
    for (const line of diff.split('\n')) {
      if (line.startsWith('+')) added++
      else if (line.startsWith('-')) removed++
    }
    return { path, kind: 'edit', added, removed }
  }

  return null
}

/** 路径缩短展示：取末两级目录 + 文件名（src/views/AgentChat.vue 风格） */
export function shortenPath(path: string): string {
  if (!path) return ''
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean)
  if (parts.length <= 3) return parts.join('/')
  return parts.slice(-3).join('/')
}

/** 媒体类型中文标签（file_read 媒体注入/文档转换的 media_type → 展示文案） */
export const MEDIA_TYPE_LABELS: Record<string, string> = {
  image: '图片',
  audio: '音频',
  video: '视频',
  pdf: 'PDF',
  docx: 'Word 文档',
  xlsx: 'Excel 表格'
}

export function mediaTypeLabel(mediaType: string | undefined): string {
  if (!mediaType) return '查看'
  return MEDIA_TYPE_LABELS[mediaType] ?? '查看'
}
