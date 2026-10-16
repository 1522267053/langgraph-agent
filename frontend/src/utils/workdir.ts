/**
 * 工作路径前端偏好工具
 * @description 按 Agent 维度在 localStorage 持久化用户最近选择的目录，**仅用于
 *              目录选择弹窗（DirectoryPickerDialog）的初始定位**——让用户下次选
 *              路径时不必从根目录逐级点开。
 *
 *              严格禁止用于「自动设置会话工作路径」：localStorage 为全浏览器
 *              所有标签共享，一旦参与会话创建/回填，多标签场景下会出现
 *              「新建会话凭空带上工作路径」「旧会话被静默写库」等跨标签串扰。
 *              会话的 work_dir 只能来自用户显式选择（无会话阶段暂存于
 *              store.pendingWorkDir，纯内存、按标签隔离）。
 *
 *              localStorage 不可用时静默降级，不阻塞主流程。
 */

export const WORKDIR_RECENT_PREFIX = 'agent_workdir_recent:'

function workDirRecentKey(agentIdNum: number | null): string | null {
  return agentIdNum ? `${WORKDIR_RECENT_PREFIX}${agentIdNum}` : null
}

/** 读取 Agent 维度记住的目录（仅弹窗初始定位用）；agentId 为 null / localStorage 不可用时返回空串 */
export function loadWorkDirForAgent(agentIdNum: number | null): string {
  if (typeof localStorage === 'undefined') return ''
  const k = workDirRecentKey(agentIdNum)
  return k ? localStorage.getItem(k) || '' : ''
}

/** 写入 Agent 维度记住的目录（仅弹窗初始定位用）；空串表示清除（removeItem）；写入异常（配额/隐私模式）静默降级 */
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