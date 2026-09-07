/**
 * 计划模式前端偏好工具
 * @description 按 Agent 维度在 localStorage 持久化计划模式开关，供所有可能
 *              触发「新建会话」的入口复用（App.vue 侧栏/抽屉、SessionSidebar、
 *              AgentChat 首条消息），让新建会话继承最近一次的计划模式偏好。
 *              会话创建后以会话字段（AgentSession.plan_mode）为权威。
 *              localStorage 不可用时静默降级，不阻塞主流程。
 */

export const PLAN_MODE_RECENT_PREFIX = 'agent_plan_mode_recent:'

function planModeRecentKey(agentIdNum: number | null): string | null {
  return agentIdNum ? `${PLAN_MODE_RECENT_PREFIX}${agentIdNum}` : null
}

/** 读取 Agent 维度记住的计划模式；agentId 为 null / localStorage 不可用时返回 false */
export function loadPlanModeForAgent(agentIdNum: number | null): boolean {
  if (typeof localStorage === 'undefined') return false
  const k = planModeRecentKey(agentIdNum)
  return k ? localStorage.getItem(k) === '1' : false
}

/** 写入 Agent 维度记住的计划模式；写入异常（配额/隐私模式）静默降级 */
export function savePlanModeForAgent(agentIdNum: number | null, enabled: boolean): void {
  if (typeof localStorage === 'undefined') return
  const k = planModeRecentKey(agentIdNum)
  if (!k) return
  try {
    localStorage.setItem(k, enabled ? '1' : '0')
  } catch {
    // 配额满 / 隐私模式禁用：按浏览器原生行为降级
  }
}
