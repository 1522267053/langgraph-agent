/**
 * 格式化工具函数
 * @description 提供常用的数据格式化功能
 */

/**
 * 格式化日期时间
 * @param date 日期字符串或Date对象
 * @param format 格式类型
 * @returns 格式化后的字符串
 */
export function formatDate(
  date: string | Date | undefined,
  format: 'full' | 'date' | 'time' | 'datetime' = 'datetime'
): string {
  if (!date) return '-'

  const d = typeof date === 'string' ? new Date(date) : date
  if (isNaN(d.getTime())) return '-'

  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hours = String(d.getHours()).padStart(2, '0')
  const minutes = String(d.getMinutes()).padStart(2, '0')
  const seconds = String(d.getSeconds()).padStart(2, '0')

  switch (format) {
    case 'date':
      return `${year}-${month}-${day}`
    case 'time':
      return `${hours}:${minutes}:${seconds}`
    case 'full':
      return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`
    case 'datetime':
    default:
      return `${year}-${month}-${day} ${hours}:${minutes}`
  }
}

/**
 * 格式化相对时间
 * @param date 日期字符串或Date对象
 * @returns 相对时间描述（如：刚刚、5分钟前、昨天等）
 */
export function formatRelativeTime(date: string | Date | undefined): string {
  if (!date) return '-'

  const d = typeof date === 'string' ? new Date(date) : date
  if (isNaN(d.getTime())) return '-'

  const now = new Date()
  const diff = now.getTime() - d.getTime()
  const seconds = Math.floor(diff / 1000)
  const minutes = Math.floor(seconds / 60)
  const hours = Math.floor(minutes / 60)
  const days = Math.floor(hours / 24)

  if (seconds < 60) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  if (hours < 24) return `${hours}小时前`
  if (days < 7) return `${days}天前`
  if (days < 30) return `${Math.floor(days / 7)}周前`
  if (days < 365) return `${Math.floor(days / 30)}个月前`
  return `${Math.floor(days / 365)}年前`
}

/**
 * 格式化工具调用参数
 * @description 将工具参数对象格式化为可读的字符串
 * @param args 工具参数对象
 * @param maxLength 最大显示长度
 * @returns 格式化后的字符串
 */
export function formatToolArgs(
  args: Record<string, unknown> | undefined,
  maxLength = Infinity
): string {
  if (!args || Object.keys(args).length === 0) return '无参数'

  try {
    const str = JSON.stringify(args, null, 2)
    if (maxLength !== Infinity && str.length > maxLength) {
      return str.substring(0, maxLength) + '...'
    }
    return str
  } catch {
    return '参数解析失败'
  }
}

export function formatToolArgsExpanded(
  args: Record<string, unknown> | undefined,
  maxLength = Infinity
): string {
  if (!args || Object.keys(args).length === 0) return '无参数'

  try {
    const formatted = tryParseStringValues(args)
    const str = JSON.stringify(formatted, null, 2)
    if (maxLength !== Infinity && str.length > maxLength) {
      return str.substring(0, maxLength) + '...'
    }
    return str
  } catch {
    return '参数解析失败'
  }
}

export function hasStringifiedJson(args: Record<string, unknown>): boolean {
  for (const value of Object.values(args)) {
    if (typeof value === 'string') {
      try {
        const parsed = JSON.parse(value)
        if (typeof parsed === 'object' && parsed !== null) return true
      } catch {
        // ignore
      }
    }
  }
  return false
}

function tryParseStringValues(obj: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(obj)) {
    if (typeof value === 'string') {
      try {
        const parsed = JSON.parse(value)
        if (typeof parsed === 'object' && parsed !== null) {
          result[key] = parsed
        } else {
          result[key] = value
        }
      } catch {
        result[key] = value
      }
    } else {
      result[key] = value
    }
  }
  return result
}

/**
 * 格式化JSON数据
 * @param data JSON数据
 * @param indent 缩进空格数
 * @returns 格式化后的字符串
 */
export function formatJson(data: unknown, indent = 2): string {
  try {
    return JSON.stringify(data, null, indent)
  } catch {
    return '数据解析失败'
  }
}

/**
 * 截断文本
 * @param text 原始文本
 * @param maxLength 最大长度
 * @param suffix 截断后缀
 * @returns 截断后的文本
 */
export function truncateText(text: string, maxLength: number, suffix = '...'): string {
  if (!text || text.length <= maxLength) return text
  return text.substring(0, maxLength - suffix.length) + suffix
}

/**
 * 格式化文件大小
 * @param bytes 字节数
 * @returns 格式化后的大小字符串
 */
export function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B'

  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const k = 1024
  const i = Math.floor(Math.log(bytes) / Math.log(k))

  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${units[i]}`
}

/**
 * 判断MIME类型是否为图片
 */
export function isImage(mimeType: string): boolean {
  return mimeType.startsWith('image/')
}

export function isVideo(mimeType: string): boolean {
  return mimeType.startsWith('video/')
}

export function isAudio(mimeType: string): boolean {
  return mimeType.startsWith('audio/')
}

/**
 * 根据文件扩展名获取Element Plus Tag类型
 */
export function getFileTypeTag(type: string): '' | 'success' | 'warning' | 'danger' | 'info' {
  const imageTypes = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg']
  const videoTypes = ['mp4', 'avi', 'mov', 'wmv', 'mkv', 'flv']
  const audioTypes = ['mp3', 'wav', 'ogg', 'flac', 'aac']
  const docTypes = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx']
  if (imageTypes.includes(type)) return 'success'
  if (videoTypes.includes(type)) return 'danger'
  if (audioTypes.includes(type)) return 'warning'
  if (docTypes.includes(type)) return ''
  return 'info'
}

/**
 * 格式化数字（添加千分位）
 * @param num 数字
 * @returns 格式化后的字符串
 */
export function formatNumber(num: number): string {
  return num.toLocaleString('zh-CN')
}

/**
 * 格式化百分比
 * @param value 数值（0-1之间）
 * @param decimals 小数位数
 * @returns 格式化后的百分比字符串
 */
export function formatPercent(value: number, decimals = 0): string {
  return `${(value * 100).toFixed(decimals)}%`
}

/**
 * 格式化持续时间
 * @param ms 毫秒数
 * @returns 格式化后的时间字符串
 */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  if (ms < 3600000) {
    const minutes = Math.floor(ms / 60000)
    const seconds = Math.floor((ms % 60000) / 1000)
    return `${minutes}m ${seconds}s`
  }
  const hours = Math.floor(ms / 3600000)
  const minutes = Math.floor((ms % 3600000) / 60000)
  return `${hours}h ${minutes}m`
}

/**
 * 首字母大写
 * @param str 字符串
 * @returns 首字母大写后的字符串
 */
export function capitalize(str: string): string {
  if (!str) return ''
  return str.charAt(0).toUpperCase() + str.slice(1)
}

/**
 * 驼峰转短横线
 * @param str 驼峰字符串
 * @returns 短横线字符串
 */
export function camelToKebab(str: string): string {
  return str.replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase()
}

/**
 * 短横线转驼峰
 * @param str 短横线字符串
 * @returns 驼峰字符串
 */
export function kebabToCamel(str: string): string {
  return str.replace(/-([a-z])/g, (_, char) => char.toUpperCase())
}

/**
 * 格式化聊天时间（中文格式：2024年01月01日  12时00分）
 */
export function formatChatTime(date: Date | string): string {
  const d = typeof date === 'string' ? new Date(date) : date
  if (isNaN(d.getTime())) return '-'
  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hour = String(d.getHours()).padStart(2, '0')
  const minute = String(d.getMinutes()).padStart(2, '0')
  return `${year}年${month}月${day}日  ${hour}时${minute}分`
}

export function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) return (tokens / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M'
  if (tokens >= 1_000) return (tokens / 1_000).toFixed(1).replace(/\.0$/, '') + 'K'
  return tokens.toLocaleString()
}

export function getNodeStatusType(status?: number): '' | 'success' | 'warning' | 'danger' | 'info' {
  if (status === undefined) return 'info'
  const types: Record<number, string> = {
    0: 'info',
    1: 'warning',
    2: 'success',
    3: 'danger',
    4: 'info',
    5: 'info'
  }
  return (types[status] || 'info') as '' | 'success' | 'warning' | 'danger' | 'info'
}
