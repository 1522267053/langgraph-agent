/**
 * MCP 命令检查工具
 * @description 根据命令名推断所需运行环境，用于命令缺失时的友好提示。
 *   前端为告知性提示（无法真实检测系统是否安装），由后端 test_connection 兜底真实检测。
 */

import { MCP_COMMAND_DEPENDENCIES, type McpCommandDependency } from '@/constants/mcp'

export interface McpCommandCheckResult {
  /** 是否匹配到已知依赖 */
  matched: boolean
  /** 原始命令名 */
  command: string
  /** 依赖软件名称 */
  dependencyName?: string
  /** 官方下载页面 */
  downloadUrl?: string
  /** 提示文案 */
  message: string
}

/** 检查单个命令是否对应已知依赖环境 */
export function checkMcpCommand(command: string): McpCommandCheckResult {
  const cmd = (command || '').trim()
  const dep: McpCommandDependency | undefined = MCP_COMMAND_DEPENDENCIES[cmd]
  if (dep) {
    return {
      matched: true,
      command: cmd,
      dependencyName: dep.name,
      downloadUrl: dep.downloadUrl,
      message: `命令「${cmd}」依赖 ${dep.name} 运行环境，如未安装请先下载安装`
    }
  }
  return { matched: false, command: cmd, message: '' }
}

/**
 * 从 MCP 服务器配置列表中收集已知命令依赖提示（按命令去重）
 * @param servers 含 configs.command 的服务器配置数组（流程导入预览用）
 */
export function collectMcpCommandWarnings(
  servers: Array<{ configs?: { command?: unknown } }>
): McpCommandCheckResult[] {
  const results: McpCommandCheckResult[] = []
  const seen = new Set<string>()
  for (const server of servers) {
    const command = server.configs?.command
    if (typeof command !== 'string' || !command.trim()) continue
    const check = checkMcpCommand(command)
    if (check.matched && !seen.has(check.command)) {
      seen.add(check.command)
      results.push(check)
    }
  }
  return results
}
