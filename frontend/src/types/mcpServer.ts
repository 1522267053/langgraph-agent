/**
 * MCP服务器相关类型定义
 * @description 定义MCP服务器的配置、状态和操作相关类型
 */

/** MCP服务器传输类型 */
export type McpTransportType = 'stdio' | 'sse' | 'streamable-http'

/** MCP服务器实体 */
export interface McpServer {
  /** 主键ID */
  id: number
  /** 服务器名称 */
  name: string
  /** 服务器描述 */
  description?: string
  /** 传输类型 */
  transport: McpTransportType
  /** 是否启用 */
  is_enabled: number
  /** 保持连接：0=调用后释放，1=保持连接 */
  keep_alive: number
  /** 最后连接时间 */
  last_connected_at?: string
  /** 最后错误信息 */
  last_error?: string
  /** 创建时间 */
  create_time?: string
}

/** MCP服务器配置（用于stdio传输） */
export interface McpServerStdioConfig {
  /** 执行命令 */
  command?: string
  /** 命令参数 */
  args?: string[]
  /** 环境变量 */
  env?: Record<string, string>
}

/** MCP服务器配置（用于网络传输） */
export interface McpServerNetworkConfig {
  /** 服务器URL */
  url?: string
  /** 请求头 */
  headers?: Record<string, string>
  /** 工具调用超时时间（秒），1-600 */
  timeout?: number
}

/** MCP服务器完整配置 */
export type McpServerConfig = McpServerStdioConfig & McpServerNetworkConfig

/** 创建MCP服务器参数 */
export interface McpServerCreate {
  /** 服务器名称 */
  name: string
  /** 服务器描述 */
  description?: string
  /** 传输类型 */
  transport: McpTransportType
  /** 是否启用 */
  is_enabled?: number
  /** 保持连接：0=调用后释放，1=保持连接 */
  keep_alive?: number
  /** 服务器配置 */
  config?: McpServerConfig
}

/** 更新MCP服务器参数 */
export interface McpServerUpdate {
  /** 主键ID */
  id: number
  /** 服务器名称 */
  name?: string
  /** 服务器描述 */
  description?: string
  /** 传输类型 */
  transport?: McpTransportType
  /** 是否启用 */
  is_enabled?: number
  /** 保持连接：0=调用后释放，1=保持连接 */
  keep_alive?: number
  /** 服务器配置 */
  config?: McpServerConfig
}

/** MCP工具信息 */
export interface McpToolInfo {
  /** 工具名称 */
  name: string
  /** 工具描述 */
  description?: string
  /** 输入参数Schema */
  input_schema?: Record<string, unknown>
  /** 是否启用 */
  is_enabled?: number
}

/** MCP服务器测试结果 */
export interface McpServerTestResult {
  /** 是否成功 */
  success: boolean
  /** 可用工具列表 */
  tools: McpToolInfo[]
  /** 错误信息 */
  error?: string
}
