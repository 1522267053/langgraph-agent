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

// ---- 最近使用目录列表（弹窗快捷区，最多 8 条）----

export const WORKDIR_RECENTS_PREFIX = 'agent_workdir_recents:'
/** 快捷区最多展示的最近目录数 */
export const WORKDIR_RECENTS_LIMIT = 8

function workDirRecentsKey(agentIdNum: number | null): string | null {
  return agentIdNum ? `${WORKDIR_RECENTS_PREFIX}${agentIdNum}` : null
}

/** 读取 Agent 维度最近使用的目录列表（仅弹窗快捷区用），最近在前；无记录/localStorage 不可用时返回空数组 */
export function loadWorkDirRecents(agentIdNum: number | null): string[] {
  if (typeof localStorage === 'undefined') return []
  const k = workDirRecentsKey(agentIdNum)
  if (!k) return []
  try {
    const raw = localStorage.getItem(k)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((p): p is string => typeof p === 'string') : []
  } catch {
    return []
  }
}

/** 记录一次目录选择：去重置顶、截断到上限；空路径不记录；写入异常静默降级 */
export function recordWorkDirRecent(agentIdNum: number | null, path: string): void {
  const trimmed = path.trim()
  if (!trimmed) return
  const k = workDirRecentsKey(agentIdNum)
  if (!k) return
  try {
    const next = [trimmed, ...loadWorkDirRecents(agentIdNum).filter(p => p !== trimmed)].slice(
      0,
      WORKDIR_RECENTS_LIMIT
    )
    localStorage.setItem(k, JSON.stringify(next))
  } catch {
    // 配额满 / 隐私模式禁用：按浏览器原生行为降级
  }
}

/** 从最近列表移除失效目录（目录已不存在时调用）；写入异常静默降级 */
export function removeWorkDirRecent(agentIdNum: number | null, path: string): void {
  if (!path) return
  const k = workDirRecentsKey(agentIdNum)
  if (!k) return
  try {
    localStorage.setItem(
      k,
      JSON.stringify(loadWorkDirRecents(agentIdNum).filter(p => p !== path))
    )
  } catch {
    // 配额满 / 隐私模式禁用：按浏览器原生行为降级
  }
}