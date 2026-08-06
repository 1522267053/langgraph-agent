/**
 * MCP 命令依赖映射
 * @description 常见 MCP 启动命令与所需运行环境的映射，用于命令缺失时的友好提示
 */

export interface McpCommandDependency {
  /** 依赖软件名称 */
  name: string
  /** 官方下载页面 */
  downloadUrl: string
}

/** 常见命令 → 依赖运行环境映射 */
export const MCP_COMMAND_DEPENDENCIES: Record<string, McpCommandDependency> = {
  npx: { name: 'Node.js', downloadUrl: 'https://nodejs.org/zh-cn/download' },
  node: { name: 'Node.js', downloadUrl: 'https://nodejs.org/zh-cn/download' },
  npm: { name: 'Node.js', downloadUrl: 'https://nodejs.org/zh-cn/download' },
  uvx: {
    name: 'uv',
    downloadUrl: 'https://docs.astral.sh/uv/getting-started/installation/'
  },
  uv: {
    name: 'uv',
    downloadUrl: 'https://docs.astral.sh/uv/getting-started/installation/'
  },
  python: { name: 'Python', downloadUrl: 'https://www.python.org/downloads/' },
  python3: { name: 'Python', downloadUrl: 'https://www.python.org/downloads/' }
}
