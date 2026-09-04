/**
 * 工作路径前端偏好工具
 * @description 按 Agent 维度在 localStorage 持久化用户最近选择的工作目录，
 *              供所有可能触发「新建会话」的入口复用（App.vue 侧栏/抽屉、SessionSidebar、AgentChat）。
 *              localStorage 不可用时静默降级，不阻塞主流程。
 */

export const WORKDIR_RECENT_PREFIX = 'agent_workdir_recent:'

function workDirRecentKey(agentIdNum: number | null): string | null {
  return agentIdNum ? `${WORKDIR_RECENT_PREFIX}${agentIdNum}` : null
}

/** 读取 Agent 维度记住的工作路径；agentId 为 null / localStorage 不可用时返回空串 */
export function loadWorkDirForAgent(agentIdNum: number | null): string {
  if (typeof localStorage === 'undefined') return ''
  const k = workDirRecentKey(agentIdNum)
  return k ? localStorage.getItem(k) || '' : ''
}

/** 写入 Agent 维度记住的工作路径；空串表示清除（removeItem）；写入异常（配额/隐私模式）静默降级 */
export function saveWorkDirForAgent(agentIdNum: number | null, path: string): void {
  if (typeof localStorage === 'undefined') return
  const k = workDirRecentKey(agentIdNum)
  if (!k) return
  try {
    if (path) localStorage.setItem(k, path)
    else localStorage.removeItem(k)
  } catch {
    // 配额满 / 隐私模式禁用：按浏览器原生行为降级
  }
}
